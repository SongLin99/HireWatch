import json
from dataclasses import replace

from fastapi.testclient import TestClient

from hirewatch.app import create_app
from hirewatch.storage import Store
from test_ai import make_job, parsed
from test_subscriptions import analyze


def instrument(store,monkeypatch):
    calls=[];original=store._normalize_job
    def normalize(row):calls.append(row['id']);return original(row)
    monkeypatch.setattr(store,'_normalize_job',normalize)
    return calls


def test_cache_refreshes_only_changed_content_and_preserves_metadata(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.sqlite3');jobs=[make_job(str(i)) for i in range(8)]
    store.snapshot('baidu',jobs);calls=instrument(store,monkeypatch)
    assert len(store.job_feed())==8 and len(calls)==8
    calls.clear();store.job_feed();assert not calls
    # Collection timestamp and favourite updates must be visible without re-parsing JDs.
    store.snapshot('baidu',jobs);store.save_job(jobs[0].id,True)
    assert next(r for r in store.job_feed() if r['id']==jobs[0].id)['saved']==1
    assert not calls
    changed=replace(jobs[1],requirements='本科，每周5天，实习6个月')
    store.snapshot('baidu',[jobs[0],changed,*jobs[2:]])
    row=next(r for r in store.job_feed() if r['id']==changed.id)
    assert row['min_days']==5 and row['min_months']==6
    assert calls==[changed.id]


def test_other_writers_analysis_labels_and_direct_sql_are_visible(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3');job=make_job();store.snapshot('baidu',[job]);store.job_feed()
    other=Store(store.path);analyze(other,job,job_type='产品',tags=['新标签'])
    other.labels(job.id,['已投递'])
    row=store.job_feed()[0]
    assert row['job_type']=='产品' and row['tags']==['新标签'] and row['labels']==['已投递']
    with other.connection() as db:db.execute('UPDATE jobs SET city=? WHERE id=?',('上海',job.id))
    assert store.job_feed()[0]['official_city']=='上海'
    with other.connection() as db:db.execute('DELETE FROM job_analysis WHERE job_id=?',(job.id,))
    assert store.job_feed()[0]['analysis'] is None
    with other.connection() as db:db.execute('DELETE FROM jobs WHERE id=?',(job.id,))
    assert store.job_feed()==[]


def test_caller_cannot_mutate_cached_lists_analysis_or_search_results(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3');job=make_job();store.snapshot('baidu',[job]);analyze(store,job)
    first=store.job_feed()[0]
    first['city']='别处';first['skills'].append('injected');first['analysis']['tags'].append('injected');first['labels'].append('injected')
    fresh=store.job_feed()[0]
    assert fresh['city']=='北京' and 'injected' not in str(fresh)
    assert not any(k.startswith('_') for k in fresh)


def test_irrelevant_writes_do_not_invalidate_job_data(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.sqlite3');store.snapshot('baidu',[make_job()])
    revision=store.feed_snapshot()[0];calls=instrument(store,monkeypatch)
    settings=store.settings();settings['ai']['daily_budget_usd']=5;store.set_settings(settings)
    store.source_error('baidu','暂时失败')
    with store.connection() as db:db.execute("INSERT INTO ai_calls(task,model,created_at,status) VALUES('test','test','2026-10-15','complete')")
    assert store.feed_snapshot()[0]==revision and not calls


def test_cache_revision_and_content_come_from_same_read_snapshot(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.sqlite3');job=make_job();store.snapshot('baidu',[job])
    other=Store(store.path);original=store._load_job_feed;written=False
    def load(db,ids):
        nonlocal written
        if not written:
            written=True
            with other.connection() as writer:writer.execute('UPDATE jobs SET city=? WHERE id=?',('上海',job.id))
        return original(db,ids)
    monkeypatch.setattr(store,'_load_job_feed',load)
    assert store.job_feed()[0]['official_city']=='北京'
    assert store.job_feed()[0]['official_city']=='上海'


def test_single_job_and_history_load_only_referenced_jobs(tmp_path,monkeypatch):
    app=create_app(tmp_path/'app.sqlite3',scheduler=False);store=app.state.store
    jobs=[make_job(str(i)) for i in range(50)];store.snapshot('baidu',jobs)
    payload={'intent':{'summary':'测试'},'results':[{'id':jobs[0].id,'score':90,'job':dict(jobs[0].__dict__,fingerprint=jobs[0].fingerprint)}],'total_pool':50,'eligible_pool':50,'evaluated':1}
    with store.connection() as db:db.execute("INSERT INTO match_runs VALUES('run','测试','complete','完成','2026-10-15','2026-10-15',?,'')",(json.dumps(payload),))
    calls=instrument(store,monkeypatch)
    with TestClient(app) as client:
        assert client.get('/api/jobs/'+jobs[0].id).status_code==200
        assert calls==[jobs[0].id]
        calls.clear()
        result=client.get('/api/matches/run').json()
        assert not calls and not result['result']['results'][0]['stale']
        store.save_job(jobs[0].id,True)
        assert client.get('/api/matches/run').json()['result']['results'][0]['job']['saved']==1
        assert not calls


def test_filter_facets_and_subscriptions_stay_fresh_without_mutating_cached_rows(tmp_path):
    app=create_app(tmp_path/'app.sqlite3',scheduler=False);store=app.state.store
    job=make_job();store.snapshot('baidu',[job]);analyze(store,job)
    with TestClient(app) as client:
        assert client.get('/api/jobs?q=Agent&city=北京').json()['total']==1
        assert client.get('/api/facets').json()['tags'][0]['name']
        store.labels(job.id,['新标签'])
        assert client.get('/api/jobs?tag=新标签').json()['total']==1
        assert client.get('/api/facets').json()['labels']==[{'name':'新标签','count':1}]
        settings=store.settings();settings['rule']['exclude']=['Agent'];store.set_settings(settings)
        assert client.get('/api/jobs?matching=true').json()['total']==0
        row=client.get('/api/jobs').json()['items'][0]
        assert not row['matches_rule'] and not any(k.startswith('_') for k in row)
        assert 'matches_rule' not in store.feed_snapshot()[1][0]
