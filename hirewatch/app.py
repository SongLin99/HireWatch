import csv
import io
import os
import json
import re
import hashlib
import threading
from html import escape
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .collectors import SOURCES
from .engine import Engine
from .models import matches, now, subscription_state
from .notify import validate_secret
from .storage import Store
from .ai import AIService, AIError, ROLE_TYPES, MODEL, DEFAULT_MODELS, validate_ai_secret
from .paths import RESOURCE_ROOT, default_db_path

ROOT = RESOURCE_ROOT


class Rule(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=40)
    exclude: list[str] = Field(default_factory=list, max_length=40)
    companies: list[str] = Field(default_factory=list, max_length=20)
    cities: list[str] = Field(default_factory=list, max_length=20)
    mode: Literal['any','all'] = 'any'
    job_types: list[str] = Field(default_factory=list,max_length=20)
    internship_types: list[str] = Field(default_factory=list,max_length=20)
    departments: list[str] = Field(default_factory=list,max_length=40)
    degrees: list[Literal['专科','本科','硕士','博士','不限','未说明']] = Field(default_factory=list,max_length=6)
    tags: list[str] = Field(default_factory=list,max_length=40)
    exclude_tags: list[str] = Field(default_factory=list,max_length=40)
    tag_mode: Literal['any','all'] = 'any'
    available_days: int = Field(default=0,ge=0,le=7)
    available_months: float = Field(default=0,ge=0,le=60)
    unknown_policy: Literal['include','exclude'] = 'include'


class Notifications(BaseModel):
    enabled: bool = False
    provider: str = "serverchan"
    cooldown_minutes: int = Field(default=60, ge=1, le=1440)
    daily_limit: int = Field(default=4, ge=1, le=100)
    notify_updates: bool = True
    sendkey: str | None = Field(default=None, max_length=300)
    webhook: str | None = Field(default=None, max_length=1000)


class Settings(BaseModel):
    interval_minutes: int = Field(default=15, ge=5, le=1440)
    enabled_sources: list[str] = Field(default_factory=lambda: list(SOURCES))
    rule: Rule
    notifications: Notifications
    ai: "AISettings" = Field(default_factory=lambda: AISettings())


class AISettings(BaseModel):
    enabled: bool = True
    daily_budget_usd: float = Field(default=5, ge=0, le=100)
    batch_size: int = Field(default=8, ge=1, le=12)
    provider: Literal['openrouter','deepseek'] = 'openrouter'
    model: str = Field(default=MODEL,max_length=200)
    openrouter_api_key: str | None = Field(default=None,max_length=300,repr=False)
    deepseek_api_key: str | None = Field(default=None,max_length=300,repr=False)
    input_price_usd_per_million: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)
    output_price_usd_per_million: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)


Settings.model_rebuild()


class Saved(BaseModel):
    saved: bool


class Labels(BaseModel):
    labels: list[str] = Field(max_length=12)


class MatchRequest(BaseModel):
    prompt: str = Field(min_length=5, max_length=6000)
    companies: list[str] = Field(default_factory=list,max_length=20)
    cities: list[str] = Field(default_factory=list,max_length=15)
    candidates: int = Field(default=80,ge=20,le=200)


def create_app(db_path=None, scheduler=True):
    store = Store(db_path or os.getenv('HIREWATCH_DB',str(default_db_path())))
    engine = Engine(store)
    ai = AIService(store)
    facets_cache={'revision':-1,'payload':None}
    facets_lock=threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if scheduler:
            engine.start()
            ai.start()
        yield
        engine.stop()
        ai.stop()

    app = FastAPI(title="招聘雷达", lifespan=lifespan)
    app.state.store = store
    app.state.engine = engine
    app.state.ai = ai
    instance=hashlib.sha256(str(store.path.resolve()).casefold().encode()).hexdigest()[:16]

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI normally echoes invalid values. Never echo malformed secrets.
        errors=[]
        for error in exc.errors():
            item={k:v for k,v in error.items() if k not in ('input','ctx')}
            errors.append(item)
        return JSONResponse(status_code=422,content={'detail':errors})

    @app.get('/api/health')
    def health():
        return {'app':'hirewatch','protocol':1,'process_id':os.getpid(),'instance':instance}

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        # Local single-user dashboard. Prevent cross-site writes and DNS rebinding.
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return Response("只允许本机访问", status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return Response("不允许跨站操作", status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return Response("不允许跨站操作", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == '/' or request.url.path.startswith('/assets/'):
            # A cached older script must never initialize a newer settings form.
            response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
        return response

    @app.get("/")
    def index():
        markup=(ROOT/'web'/'index.html').read_text(encoding='utf-8')
        choices={
            'job-types':{t:t for t in ROLE_TYPES},
            'internship-types':{'日常':'日常实习','暑期':'暑期实习','研究':'研究型实习','ByteIntern':'ByteIntern','应届实习':'应届实习','转正':'转正实习','兼招全职':'实习 / 全职兼招'},
            'degrees':{d:d for d in ('专科','本科','硕士','博士','不限','未说明')},
        }
        for name,options in choices.items():
            controls=''.join(f'<label class="check"><input type="checkbox" value="{escape(value,quote=True)}">{escape(label)}</label>' for value,label in options.items())
            markup=markup.replace('<!-- subscription-'+name+' -->',controls)
        def version_asset(match):
            asset=ROOT/'web'/match[1]
            version=hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
            return f'/assets/{match[1]}?v={version}'
        markup=re.sub(r'/assets/([A-Za-z0-9_.-]+\.(?:css|js))(?=["\'])',version_asset,markup)
        return HTMLResponse(markup)

    @app.get("/api/settings")
    def get_settings():
        return store.settings(public=True)

    @app.get('/api/ai/models')
    def ai_models(provider:Literal['openrouter','deepseek']='openrouter'):
        try:return ai.client.public_catalog(provider)
        except AIError as exc:raise HTTPException(400,str(exc)) from None

    @app.put("/api/settings")
    def put_settings(data: Settings):
        if set(data.enabled_sources) - SOURCES.keys() or set(data.rule.companies) - SOURCES.keys():
            raise HTTPException(400, "未知的公司来源")
        if set(data.rule.job_types)-set(ROLE_TYPES):
            raise HTTPException(400,'未知的岗位类型')
        if data.notifications.provider not in {"serverchan", "wecom"}:
            raise HTTPException(400, "筛选方式或通知渠道无效")
        result = data.model_dump()
        for key in ('keywords','exclude','cities','job_types','internship_types','departments','degrees','tags','exclude_tags'):
            result["rule"][key] = list(dict.fromkeys(k.strip()[:100] for k in result["rule"][key] if k.strip()))
        old_settings=store.settings()
        old = old_settings['notifications']
        for key, provider in (("sendkey", "serverchan"), ("webhook", "wecom")):
            if result["notifications"][key] is None:
                result["notifications"][key] = old.get(key, "")
            try:
                validate_secret(provider, result["notifications"][key])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        a=result['ai']
        a['model']=a['model'].strip() or DEFAULT_MODELS[a['provider']]
        if not re.fullmatch(r'[~A-Za-z0-9][A-Za-z0-9_./:~+-]{0,199}',a['model']):
            raise HTTPException(400,'模型 ID 只能包含字母、数字和常用分隔符')
        for provider in ('openrouter','deepseek'):
            key=provider+'_api_key'
            if a[key] is None:a[key]=old_settings['ai'].get(key)
            elif isinstance(a[key],str):a[key]=a[key].strip()
            try:validate_ai_secret(provider,a[key])
            except AIError as exc:raise HTTPException(400,str(exc)) from None
        if (a['input_price_usd_per_million'] is None)!=(a['output_price_usd_per_million'] is None):
            raise HTTPException(400,'输入和输出单价需一起填写；都留空则使用自动单价')
        store.set_settings(result)
        if result['ai']!=old_settings['ai']:ai.client.invalidate()
        return store.settings(public=True)

    @app.post('/api/subscription/preview')
    def preview_subscription(rule: Rule):
        if set(rule.companies)-SOURCES.keys() or set(rule.job_types)-set(ROLE_TYPES):
            raise HTTPException(400,'未知的公司或岗位类型')
        values=rule.model_dump()
        for key,value in values.items():
            if isinstance(value,list):values[key]=list(dict.fromkeys(v.strip()[:100] for v in value if v.strip()))
        rows=[r for r in store.feed_snapshot()[1] if r['status']=='active' and r['employment']!='非实习']
        matched=[];waiting=0
        for row in rows:
            state=subscription_state(row,values)
            if state=='matched':matched.append(row)
            elif state=='pending':waiting+=1
        return {'total':len(rows),'matched':len(matched),'waiting_for_analysis':waiting,
                'items':[{k:r[k] for k in ('id','company','title','city','job_type','degree','internship_type','url')} for r in matched[:5]]}

    @app.post("/api/scan", status_code=202)
    def scan():
        if not engine.trigger():
            raise HTTPException(409, "已有采集任务正在运行")
        return {"message": "已开始检查招聘网站"}

    @app.get("/api/status")
    def status():
        sources = {row["id"]: row for row in store.rows("SELECT * FROM sources")}
        for source in sources.values():
            source['coverage']=json.loads(source.get('coverage') or '{}')
        settings = store.settings()
        job_stats=store.rows("SELECT COUNT(*) AS total,COALESCE(SUM(status='active'),0) AS active,COALESCE(SUM(saved),0) AS saved FROM jobs")[0]
        job_stats['pending']=store.rows("SELECT COUNT(*) AS n FROM events WHERE delivery='pending'")[0]['n']
        return {"process_id": os.getpid(), "engine": engine.state(), "sources": [dict(info, id=id, enabled=id in settings["enabled_sources"], **{k:v for k,v in sources.get(id,{}).items() if k!='id'}) for id, info in SOURCES.items()],
                "stats":job_stats,
                "deliveries": store.rows("SELECT * FROM deliveries ORDER BY id DESC LIMIT 10"), "ai":ai.state(),
                "jobs_revision":int(store.rows("SELECT value FROM meta WHERE key='job_feed_revision'")[0]['value'])}

    def filtered(q="", company="", city="", status="", saved=False, matching=False, kind="",
                 job_type="",degree="",tag="",department="",ai_status="",days=0,months=0,recent=0,sort="changed"):
        rows = store.feed_snapshot()[1]
        rule = store.settings()["rule"]
        words = q.replace("，", " ").replace(",", " ").split()
        companies=set(company.split(',')) if company else set()
        city=city.casefold();department=department.casefold();tag=tag.casefold()
        cutoff=(datetime.fromisoformat(now())-timedelta(days=recent)).isoformat(timespec='seconds') if recent else ''
        result = []
        for job in rows:
            if job['employment']=='非实习':continue
            if companies and job["source"] not in companies or city and city not in job["city"].casefold():
                continue
            if status and job["status"] != status or saved and not job["saved"]:
                continue
            if kind and kind not in job["internship_type"]:
                continue
            if job_type and job_type!=job['job_type']:continue
            if degree and degree!=job['degree']:continue
            if department and department not in job['department'].casefold():continue
            if ai_status and ai_status!=job['ai_status']:continue
            if tag and tag not in [t.casefold() for t in job['tags']+job['skills']+job['labels']]:continue
            if days and job['min_days'] and job['min_days']>days:continue
            if months and job['min_months'] and job['min_months']>months:continue
            if cutoff and job['first_seen']<cutoff:continue
            if words and not matches(job, {"keywords": words, "mode": "all"}):
                continue
            if matching and subscription_state(job,rule)!='matched':
                continue
            result.append(job)
        if sort=='first_seen':result.sort(key=lambda j:j['first_seen'],reverse=True)
        elif sort=='company':result.sort(key=lambda j:(j['company'],j['title']))
        elif sort=='published':result.sort(key=lambda j:j['published_at'] or j['first_seen'],reverse=True)
        return result

    @app.get("/api/jobs")
    def jobs(q: str = "", company: str = "", city: str = "", status: str = "", saved: bool = False,
             matching: bool = False, kind: str = "", page: int = 1, page_size: int = 20,
             job_type: str="", degree: str="", tag: str="", department: str="", ai_status: str="",
             days: int=0, months: float=0, recent: int=0, sort: str="changed"):
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        rows = filtered(q, company, city, status, saved, matching, kind,job_type,degree,tag,department,ai_status,days,months,recent,sort)
        rule=store.settings()['rule']
        items=[]
        for row in rows[(page-1)*page_size:page*page_size]:
            item=store.copy_job(row)
            item['subscription_state']=subscription_state(row,rule)
            item['matches_rule']=item['subscription_state']=='matched'
            items.append(item)
        return {"items":items, "total": len(rows), "page": page, "page_size": page_size,
                "companies": [{"id": k, "name": v["name"]} for k, v in SOURCES.items()]}

    @app.get("/api/export")
    def export(q: str = "", company: str = "", city: str = "", status: str = "", saved: bool = False,
               matching: bool = False, kind: str = "", job_type: str="", degree: str="", tag: str="", department: str="", ai_status: str="",
               days: int=0, months: float=0, recent: int=0, sort: str="changed"):
        rows = filtered(q, company, city, status, saved, matching, kind,job_type,degree,tag,department,ai_status,days,months,recent,sort)
        stream = io.StringIO()
        writer = csv.writer(stream)
        keys = ["company", "title", "city", "department", "internship_type", "job_type", "degree", "skills", "tags", "labels", "min_days", "min_months", "ai_status", "published_at", "first_seen", "last_seen", "status", "url"]
        writer.writerow(["公司", "岗位", "城市", "部门", "实习类型", "岗位方向", "学历", "技能", "AI标签", "自定义标签", "每周最低天数", "最少月数", "AI解析状态", "官网发布日期", "首次发现", "最近发现", "采集状态", "投递链接"])
        for row in rows:
            # Avoid spreadsheet formula execution from externally supplied job titles.
            values=[' / '.join(row[k]) if isinstance(row[k],list) else '' if row[k] is None else str(row[k]) for k in keys]
            writer.writerow(["'"+v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
        return Response(content=stream.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=hirewatch.csv"})

    @app.patch("/api/jobs/{job_id}/saved")
    def save(job_id: str, data: Saved):
        if not store.save_job(job_id, data.saved):
            raise HTTPException(404, "岗位不存在")
        return {"saved": data.saved,"saved_count":store.rows('SELECT COALESCE(SUM(saved),0) AS n FROM jobs')[0]['n']}

    @app.get('/api/facets')
    def facets():
        revision,rows=store.feed_snapshot()
        with facets_lock:
            if facets_cache['revision']!=revision:
                active=[r for r in rows if r['status']=='active' and r['employment']!='非实习']
                def counts(key):
                    result={}
                    for r in active:
                        values=r[key] if isinstance(r[key],list) else [r[key]]
                        for value in values:
                            if value:result[value]=result.get(value,0)+1
                    return [{'name':k,'count':v} for k,v in sorted(result.items(),key=lambda item:(-item[1],item[0]))]
                facets_cache['payload']={'companies':[dict(id=k,**v) for k,v in SOURCES.items()], 'role_types':ROLE_TYPES,
                        'cities':counts('cities'),'tags':counts('tags')[:60],'labels':counts('labels'),
                        'departments':counts('department')[:100], 'degrees':counts('degree')}
                facets_cache['revision']=revision
            return facets_cache['payload']

    @app.get('/api/jobs/{job_id}')
    def detail(job_id:str):
        rows=store.job_feed([job_id])
        if not rows:raise HTTPException(404,'岗位不存在')
        return rows[0]

    @app.patch('/api/jobs/{job_id}/labels')
    def labels(job_id:str,data:Labels):
        values=list(dict.fromkeys(v.strip() for v in data.labels if v.strip()))
        if any(len(v)>30 for v in values):raise HTTPException(400,'每个标签最多30个字符')
        if not store.labels(job_id,values):raise HTTPException(404,'岗位不存在')
        return {'labels':values}

    @app.get('/api/ai/status')
    def ai_status():return ai.state()

    @app.post('/api/ai/retry')
    def ai_retry():
        ai.retry_failed()
        return {'message':'已重置失败解析，后台将继续处理'}

    @app.post('/api/matches',status_code=202)
    def match(data:MatchRequest):
        if set(data.companies)-SOURCES.keys():raise HTTPException(400,'未知的公司来源')
        try:run_id=ai.create_match(data.prompt,data.companies,data.cities,data.candidates)
        except AIError as exc:raise HTTPException(400,str(exc)) from None
        return {'id':run_id,'state':'pending'}

    @app.get('/api/matches')
    def match_history():
        return store.rows('SELECT id,prompt,state,progress,created_at,updated_at,error FROM match_runs ORDER BY created_at DESC LIMIT 12')

    @app.post('/api/matches/{run_id}/retry')
    def retry_match(run_id:str):
        try:ai.retry_match(run_id)
        except AIError as exc:raise HTTPException(400,str(exc)) from None
        return {'id':run_id,'state':'running'}

    @app.get('/api/matches/{run_id}')
    def match_result(run_id:str):
        rows=store.rows('SELECT * FROM match_runs WHERE id=?',(run_id,))
        if not rows:raise HTTPException(404,'匹配记录不存在')
        run=rows[0];run['result']=json.loads(run['result'])
        entries=run['result'].get('results',[])+run['result'].get('unscored',[])
        if entries:
            fresh={r['id']:r for r in store.job_feed(r['id'] for r in entries)}
            for result in entries:
                row=fresh.get(result['id'])
                if row:
                    result['stale']=row['fingerprint']!=result['job']['fingerprint'] or row['status']!='active'
                    result['job']=row
                else:result['stale']=True
        return run

    @app.post("/api/notifications/test")
    def test_notification():
        try:
            engine.notifier.test()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"message": "推送服务已接受测试消息，请在微信中查看"}

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    return app
