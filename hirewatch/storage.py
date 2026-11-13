import json
import re
import sqlite3
import threading
import copy
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .models import now, explicit_availability, internship_evidence, internship_kind, RULE_DEFAULTS

DEFAULT_SETTINGS = {
    "interval_minutes": 15,
    "enabled_sources": ["baidu", "meituan", "tencent", "bytedance", "alibaba"],
    "rule": RULE_DEFAULTS,
    "notifications": {"enabled": False, "provider": "serverchan", "cooldown_minutes": 60,
                      "daily_limit": 4, "notify_updates": True, "sendkey": "", "webhook": ""},
    "ai": {"enabled": False, "daily_budget_usd": 5.0, "batch_size": 8,
           "provider": "openrouter", "model": "deepseek/deepseek-v3.2",
           "openrouter_api_key": None, "deepseek_api_key": None,
           "input_price_usd_per_million": None, "output_price_usd_per_million": None},
}
NEW_SOURCES = ["zhipu", "mihoyo", "minimax", "deepseek", "shailab", "kimi", "stepfun"]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._feed_lock=threading.RLock()
        self._feed_revision=-1
        self._feed_rows={}
        self._feed_signatures={}
        self._feed_ordered=()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
                    company TEXT NOT NULL, title TEXT NOT NULL, city TEXT NOT NULL,
                    category TEXT NOT NULL, department TEXT NOT NULL, internship_type TEXT NOT NULL,
                    description TEXT NOT NULL, requirements TEXT NOT NULL, url TEXT NOT NULL,
                    published_at TEXT NOT NULL, source_updated_at TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, changed_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', missed_scans INTEGER NOT NULL DEFAULT 0,
                    saved INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS jobs_source ON jobs(source);
                CREATE INDEX IF NOT EXISTS jobs_changed ON jobs(changed_at DESC);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
                    last_error TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 0,
                    added INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, kind TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL, delivery TEXT NOT NULL,
                    sent_at TEXT, error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, created_at TEXT NOT NULL,
                    success INTEGER NOT NULL, message TEXT NOT NULL, count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_analysis (
                    job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}', model TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', updated_at TEXT, retry_at TEXT
                );
                CREATE TABLE IF NOT EXISTS job_labels (
                    job_id TEXT NOT NULL, label TEXT NOT NULL,
                    PRIMARY KEY(job_id,label)
                );
                CREATE TABLE IF NOT EXISTS ai_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, model TEXT NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS match_runs (
                    id TEXT PRIMARY KEY, prompt TEXT NOT NULL, state TEXT NOT NULL,
                    progress TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT ''
                );
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (json.dumps(DEFAULT_SETTINGS),))
            db.execute("INSERT OR IGNORE INTO meta VALUES('job_feed_revision','0')")
            db.execute('CREATE TABLE IF NOT EXISTS job_feed_changes (job_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS job_feed_changes_revision ON job_feed_changes(revision)')
            db.execute('CREATE INDEX IF NOT EXISTS events_delivery ON events(delivery,id)')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_status_saved ON jobs(status,saved)')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_analysis_lookup ON jobs(status,id,fingerprint)')
            db.execute('CREATE INDEX IF NOT EXISTS analysis_feed_counts ON job_analysis(job_id,state,fingerprint,prompt_version)')
            # Track all writers, including the AI worker and other Store instances.
            # Model call accounting and scheduler status do not invalidate the feed.
            for table,key in (('jobs','id'),('job_analysis','job_id'),('job_labels','job_id')):
                for operation,record in (('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS feed_{table}_{operation.lower()}
                        AFTER {operation} ON {table} BEGIN
                        UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='job_feed_revision';
                        INSERT INTO job_feed_changes(job_id,revision)
                        VALUES({record}.{key},(SELECT CAST(value AS INTEGER) FROM meta WHERE key='job_feed_revision'))
                        ON CONFLICT(job_id) DO UPDATE SET revision=excluded.revision;
                        END""")
            if 'coverage' not in {r['name'] for r in db.execute('PRAGMA table_info(sources)')}:
                db.execute("ALTER TABLE sources ADD COLUMN coverage TEXT NOT NULL DEFAULT '{}'")
            if not db.execute("SELECT 1 FROM meta WHERE key='v2_sources'").fetchone():
                settings = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
                settings['enabled_sources'] = list(dict.fromkeys(settings['enabled_sources'] + NEW_SOURCES))
                settings.setdefault('ai', DEFAULT_SETTINGS['ai'].copy())
                db.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(settings,ensure_ascii=False),))
                db.execute("INSERT INTO meta VALUES('v2_sources','1')")

            # Run once on upgrade; later user opt-outs must remain respected.
            if not db.execute("SELECT 1 FROM meta WHERE key='sources_20261113'").fetchone():
                settings = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
                settings['enabled_sources'] = list(dict.fromkeys(settings['enabled_sources'] + ['baichuan','modelbest','baai','shengshu']))
                db.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(settings,ensure_ascii=False),))
                db.execute("INSERT INTO meta VALUES('sources_20261113','1')")


    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self, public=False):
        with self.connection() as db:
            result = json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])
        result['rule'] = {**{k:list(v) if isinstance(v,list) else v for k,v in RULE_DEFAULTS.items()}, **result.get('rule',{})}
        result['ai'] = {**DEFAULT_SETTINGS['ai'], **result.get('ai',{})}
        if public:
            n = result["notifications"]
            n["has_sendkey"] = bool(n.pop("sendkey", ""))
            n["has_webhook"] = bool(n.pop("webhook", ""))
            a = result['ai']
            # Public callers never receive credentials, even when using Store directly.
            from .ai import read_key
            for provider in ('openrouter','deepseek'):
                a['has_'+provider+'_api_key'] = bool(read_key(settings=a,provider=provider))
                a.pop(provider+'_api_key',None)
            a['configured'] = a['has_'+a['provider']+'_api_key']
        return result

    def set_settings(self, settings):
        with self.connection() as db:
            db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(settings, ensure_ascii=False),))

    def snapshot(self, source, jobs, allow_empty=False, coverage=None):
        # A source must finish all pages successfully before this transaction is called.
        if not jobs and not allow_empty:
            raise ValueError("列表为空，暂不覆盖历史记录，请核对官网或采集接口")
        stamp = now()
        settings = self.settings()
        jobs = list({job.id: job for job in jobs}.values())
        added = updated = 0
        with self.connection() as db:
            previous = db.execute("SELECT last_success,count,coverage FROM sources WHERE id=?", (source,)).fetchone()
            baseline = not previous or not previous[0]
            db.execute("UPDATE jobs SET missed_scans=missed_scans+1 WHERE source=?", (source,))
            for job in jobs:
                fields = job.__dict__
                old = db.execute("SELECT fingerprint FROM jobs WHERE id=?", (job.id,)).fetchone()
                kind = "new" if not old else "updated" if old[0] != job.fingerprint else None
                columns = list(fields)
                db.execute(f"""INSERT INTO jobs
                    (id,{','.join(columns)},fingerprint,first_seen,last_seen,changed_at)
                    VALUES ({','.join('?' for _ in range(len(columns)+5))})
                    ON CONFLICT(id) DO UPDATE SET
                    {','.join(f'{k}=excluded.{k}' for k in columns)},fingerprint=excluded.fingerprint,
                    last_seen=excluded.last_seen,
                    changed_at=CASE WHEN jobs.fingerprint!=excluded.fingerprint THEN excluded.changed_at ELSE jobs.changed_at END,
                    status='active',missed_scans=0""",
                    (job.id, *fields.values(), job.fingerprint, stamp, stamp, stamp))
                if kind:
                    added += kind == "new"
                    updated += kind == "updated"
                    notify = settings["notifications"]
                    # Structured subscription fields arrive after collection. Keep the
                    # event until delivery can evaluate the latest original + analysis.
                    pending = (not baseline and notify["enabled"]
                               and (kind == "new" or notify["notify_updates"]))
                    db.execute("INSERT INTO events(job_id,kind,created_at,payload,delivery) VALUES(?,?,?,?,?)",
                               (job.id, kind, stamp, json.dumps(fields, ensure_ascii=False),
                                "pending" if pending else "baseline" if baseline else "ignored"))
            db.execute("UPDATE jobs SET status='unseen' WHERE source=? AND missed_scans>=2", (source,))
            db.execute("""INSERT INTO sources(id,last_attempt,last_success,count,added,updated)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_attempt=excluded.last_attempt,
                last_success=excluded.last_success,last_error='',count=excluded.count,
                added=excluded.added,updated=excluded.updated""", (source, stamp, stamp, len(jobs), added, updated))
            summary=coverage or {'checked':len(jobs),'included':len(jobs),'scope':'官方实习招聘项目','complete':True}
            summary=dict(summary)
            warnings=[]
            if not jobs:warnings.append('本轮未发现明确实习岗位，请复核官网入口及筛选条件')
            if previous and previous['count']>=5 and len(jobs)<previous['count']/2:
                warnings.append(f"收录数较上次成功采集减少超过一半（{previous['count']} → {len(jobs)}），需复核")
            def entry_urls(coverage):
                return {urlsplit(e['url'])._replace(query='',fragment='').geturl() for e in coverage.get('entries',[]) if e.get('url')}
            if previous and coverage:
                old_coverage=json.loads(previous['coverage'] or '{}')
                if entry_urls(old_coverage)-entry_urls(summary):
                    warnings.append('本轮检查的官方入口少于上次，可能遗漏独立校招或社招入口，请复核')
            summary['warnings']=warnings
            db.execute('UPDATE sources SET coverage=? WHERE id=?',(json.dumps(summary,ensure_ascii=False),source))
        return {"added": added, "updated": updated, "baseline": baseline, "count": len(jobs)}

    def source_error(self, source, message):
        with self.connection() as db:
            db.execute("""INSERT INTO sources(id,last_attempt,last_error) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET last_attempt=excluded.last_attempt,last_error=excluded.last_error""",
                (source, now(), message[:500]))

    def rows(self, query, params=()):
        with self.connection() as db:
            return [dict(row) for row in db.execute(query, params)]

    def save_job(self, job_id, saved):
        with self.connection() as db:
            cursor = db.execute("UPDATE jobs SET saved=? WHERE id=?", (int(saved), job_id))
            return cursor.rowcount > 0

    def labels(self, job_id, labels):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM jobs WHERE id=?',(job_id,)).fetchone():
                return False
            db.execute('DELETE FROM job_labels WHERE job_id=?',(job_id,))
            db.executemany('INSERT INTO job_labels VALUES(?,?)',[(job_id,label) for label in labels])
        return True

    @staticmethod
    def copy_job(row):
        return copy.deepcopy({k:v for k,v in row.items() if not k.startswith('_')})

    def feed_snapshot(self):
        """Internal read-only view. Refresh changed IDs in one consistent transaction."""
        with self._feed_lock, self.connection() as db:
            db.execute('BEGIN')
            revision=int(db.execute("SELECT value FROM meta WHERE key='job_feed_revision'").fetchone()[0])
            if revision!=self._feed_revision:
                ids=None if self._feed_revision<0 else [r[0] for r in db.execute('SELECT job_id FROM job_feed_changes WHERE revision>?',(self._feed_revision,))]
                if ids is None:
                    rows=self._load_job_feed(db,None)
                    self._feed_rows={r['id']:r for r in rows}
                else:
                    for job_id in ids:self._feed_rows.pop(job_id,None)
                    # Keep the old content cache while refreshing metadata-only changes.
                    for start in range(0,len(ids),800):
                        for row in self._load_job_feed(db,ids[start:start+800]):self._feed_rows[row['id']]=row
                    for job_id in ids:
                        if job_id not in self._feed_rows:self._feed_signatures.pop(job_id,None)
                self._feed_revision=revision
                self._feed_ordered=tuple(sorted(self._feed_rows.values(),key=lambda r:r['id']))
                self._feed_ordered=tuple(sorted(self._feed_ordered,key=lambda r:r['changed_at'],reverse=True))
            return revision,self._feed_ordered

    def job_feed(self, job_ids=None):
        if job_ids is None:
            return [self.copy_job(row) for row in self.feed_snapshot()[1]]
        job_ids=list(dict.fromkeys(job_ids))
        if not job_ids:return []
        with self._feed_lock, self.connection() as db:
            db.execute('BEGIN')
            return [self.copy_job(row) for start in range(0,len(job_ids),800) for row in self._load_job_feed(db,job_ids[start:start+800])]

    def _load_job_feed(self, db, job_ids):
        where = 'WHERE j.id IN ('+','.join('?' for _ in job_ids)+')' if job_ids is not None else ''
        rows = [dict(r) for r in db.execute("""SELECT j.*, a.payload AS analysis_json, a.state AS analysis_state,
            a.fingerprint AS analysis_fingerprint, a.model AS analysis_model,
            a.updated_at AS analyzed_at, a.error AS analysis_error FROM jobs j
            LEFT JOIN job_analysis a ON a.job_id=j.id """+where+" ORDER BY j.changed_at DESC,j.id",job_ids or ())]
        labels = {}
        label_where=' WHERE job_id IN ('+','.join('?' for _ in job_ids)+')' if job_ids is not None else ''
        for row in db.execute('SELECT * FROM job_labels'+label_where+' ORDER BY label',job_ids or ()):
            labels.setdefault(row['job_id'],[]).append(row['label'])
        result=[]
        metadata=('first_seen','last_seen','changed_at','status','missed_scans','saved','published_at','source_updated_at')
        for row in rows:
            row['labels'] = labels.get(row['id'],[])
            signature=tuple((k,tuple(v) if isinstance(v,list) else v) for k,v in row.items() if k not in metadata)
            previous=self._feed_signatures.get(row['id'])
            if previous and previous[0]==signature:
                current={**previous[1],**{k:row[k] for k in metadata}}
            else:
                current=self._normalize_job(row)
                current['_search_text']='\n'.join(str(current.get(k) or '') for k in ('title','company','city','category','department','internship_type','description','requirements','skills','tags','labels')).casefold()
                self._feed_signatures[row['id']]=(signature,current)
            result.append(current)
        return result

    def _normalize_job(self,row):
        row['ai_status'] = row.pop('analysis_state') or 'pending'
        same = row.pop('analysis_fingerprint') == row['fingerprint']
        parsed = json.loads(row.pop('analysis_json') or '{}')
        if not same:
            if row['ai_status'] == 'complete':row['ai_status']='pending'
            parsed={}
        row['analysis'] = parsed if row['ai_status']=='complete' else None
        row['official_city'], row['official_category'], row['official_department'] = row['city'],row['category'],row['department']
        row['cities'] = parsed.get('cities') or [c.strip().removesuffix('市') for c in re.split(r'[/、,，\s]+',row['city']) if c.strip()]
        row['job_type'] = parsed.get('job_type') or ''
        row['skills'] = parsed.get('skills') or []
        row['tags'] = parsed.get('tags') or []
        row['degree'] = parsed.get('degree') or '未说明'
        raw_days,raw_months=explicit_availability(row['requirements'])
        row['min_days'],row['min_months'] = parsed.get('min_days') or raw_days,parsed.get('min_months') or raw_months
        row['employment'] = parsed.get('employment') or '实习'
        evidence=internship_evidence(row['title'], '', row['description'],row['requirements']) or internship_evidence(row['title'],row['internship_type'],row['description'],row['requirements'])
        row['internship_evidence']=evidence
        if evidence:
            row['employment']='实习'
            if internship_kind(evidence)=='实习（兼招全职）':
                row['internship_type']='实习（兼招全职）'
            if row['analysis']:
                row['analysis']={**parsed,'employment':'实习','internship_type':row['internship_type']}
        if parsed.get('cities'):row['city']=' / '.join(parsed['cities'])
        if parsed.get('department'):row['department']=parsed['department']
        if parsed.get('job_type'):row['category']=parsed['job_type']
        return row
