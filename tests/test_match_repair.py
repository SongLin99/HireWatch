import json
import threading
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from hirewatch.ai import AIService, AIError, AIResponseError
from hirewatch.app import create_app
from hirewatch.models import now
from hirewatch.storage import Store
from test_ai import make_job


INTENT=dict(cities=[],cities_are_required=False,companies=[],companies_are_required=False,
            terms=['Agent'],exclude_terms=[],role_types=[],available_days=None,
            available_months=None,summary='Agent 实习')


def score(id,**changes):
    return dict(id=id,score=85,reason='Agent 研发方向相符',strengths=['研发经验'],
                gaps=['学历需确认'],evidence='研发 Agent 与 RAG 服务',**changes)


def seed(store,count):
    store.snapshot('baidu',[make_job(str(n)) for n in range(count)])


def run_match(store,ai,id='run',saved=None):
    if not saved:
        with store.connection() as db:
            db.execute("INSERT INTO match_runs(id,prompt,state,progress,created_at,updated_at) VALUES(?,?,'pending','',?,?)",(id,'Agent 实习',now(),now()))
    ai.match_lock.acquire();ai._match(id,'Agent 实习',[],[],200,saved)
    row=store.rows('SELECT * FROM match_runs WHERE id=?',(id,))[0]
    return row,json.loads(row['result'])


def test_repairs_only_missing_duplicate_and_invalid_items(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.db');seed(store,3);ai=AIService(store);calls=[]
    def complete(task,system,payload,*args,**kwargs):
        if task=='match_intent':return INTENT,'test'
        ids=[j['id'] for j in payload['jobs']];calls.append(ids)
        if len(calls)==1:
            return {'results':[score('baidu:0'),score('baidu:1'),score('baidu:1'),
                               {'id':'baidu:2','score':999},score('foreign:9')]},'test'
        saved=json.loads(store.rows("SELECT result FROM match_runs WHERE id='run'")[0]['result'])
        assert saved['evaluated']==1 and saved['results'][0]['id']=='baidu:0'
        assert set(saved['missing_ids'])=={'baidu:1','baidu:2'}
        return {'results':[score(id) for id in ids]},'test'
    monkeypatch.setattr(ai.client,'complete',complete)
    row,result=run_match(store,ai)
    assert row['state']=='complete' and result['coverage_complete']
    assert calls==[['baidu:0','baidu:1','baidu:2'],['baidu:1','baidu:2']]
    assert len(result['results'])==3 and not result['unscored']


def test_budget_failure_keeps_all_candidates_and_repair_keeps_all_scores(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.db');seed(store,45);ai=AIService(store);calls=[]
    def fail(task,system,payload,*args,**kwargs):
        if task=='match_intent':return INTENT,'test'
        ids=[j['id'] for j in payload['jobs']];calls.append(ids)
        if len(calls)==2:raise AIError('已达到本机设置的每日 AI 预算')
        return {'results':[score(id) for id in ids]},'test'
    monkeypatch.setattr(ai.client,'complete',fail)
    row,partial=run_match(store,ai)
    assert row['state']=='partial' and '预算' in row['error']
    assert partial['evaluated']==20 and partial['candidate_count']==45
    assert len(partial['unscored'])==25 and len(calls)==2
    original={r['id']:r for r in partial['results']};repair_calls=[]
    def repair(task,system,payload,*args,**kwargs):
        assert task=='rank_jobs','Repair must not call intent extraction again'
        ids=[j['id'] for j in payload['jobs']];repair_calls.extend(ids)
        assert not set(ids)&set(original)
        return {'results':[score(id) for id in ids]},'test'
    monkeypatch.setattr(ai.client,'complete',repair)
    row,result=run_match(store,ai,saved=partial)
    assert row['state']=='complete' and not row['error']
    assert result['evaluated']==45 and len(result['results'])==45
    assert set(repair_calls)==set(partial['missing_ids'])
    assert all(r==original[r['id']] for r in result['results'] if r['id'] in original)


def test_automatic_repairs_are_bounded_and_use_small_batches(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.db');seed(store,8);ai=AIService(store);calls=[]
    def complete(task,system,payload,*args,**kwargs):
        if task=='match_intent':return INTENT,'test'
        ids=[j['id'] for j in payload['jobs']];calls.append(ids)
        return {'results':[score(ids[0])] if len(calls)==1 else []},'test'
    monkeypatch.setattr(ai.client,'complete',complete)
    row,result=run_match(store,ai)
    assert row['state']=='partial' and result['evaluated']==1
    assert len(result['unscored'])==7 and not result['coverage_complete']
    assert [len(c) for c in calls]==[8,5,2,5,2]
    counts=Counter(id for ids in calls for id in ids)
    assert counts['baidu:0']==1 and max(counts.values())==3


def test_malformed_response_is_repaired_without_losing_other_batches(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.db');seed(store,2);ai=AIService(store);calls=[]
    def complete(task,system,payload,*args,**kwargs):
        if task=='match_intent':return INTENT,'test'
        calls.append(payload['jobs'])
        if len(calls)==1:raise AIResponseError('截断的 JSON')
        return {'results':[score(j['id']) for j in payload['jobs']]},'test'
    monkeypatch.setattr(ai.client,'complete',complete)
    row,result=run_match(store,ai)
    assert row['state']=='complete' and result['evaluated']==2 and len(calls)==2


@pytest.mark.parametrize('scored',[0,1])
def test_restart_preserves_candidates_and_checkpoints(tmp_path,monkeypatch,scored):
    store=Store(tmp_path/'jobs.db');seed(store,2);ai=AIService(store)
    monkeypatch.setattr(ai.client,'complete',lambda task,*a,**k:(INTENT if task=='match_intent' else {'results':[score('baidu:0')] if scored else []},'test'))
    _,before=run_match(store,ai)
    with store.connection() as db:db.execute("UPDATE match_runs SET state='running' WHERE id='run'")
    AIService(store)
    row=store.rows("SELECT * FROM match_runs WHERE id='run'")[0]
    assert row['state']==('partial' if scored else 'failed')
    assert json.loads(row['result'])==before and '重启' in row['error']


def test_failed_first_request_can_be_repaired_from_api_in_background(tmp_path,monkeypatch):
    app=create_app(tmp_path/'jobs.db',scheduler=False);store=app.state.store;ai=app.state.ai;seed(store,2)
    def fail(task,*args,**kwargs):
        if task=='match_intent':return INTENT,'test'
        raise AIError('OpenRouter 网络请求失败')
    monkeypatch.setattr(ai.client,'complete',fail)
    row,result=run_match(store,ai)
    assert row['state']=='failed' and len(result['unscored'])==2
    entered=threading.Event();release=threading.Event();calls=[]
    def repair(task,system,payload,*args,**kwargs):
        assert task=='rank_jobs';calls.append(payload['jobs']);entered.set()
        assert release.wait(5)
        return {'results':[score(j['id']) for j in payload['jobs']]},'test'
    monkeypatch.setattr(ai.client,'complete',repair);monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-test-only')
    with TestClient(app) as client:
        assert client.post('/api/matches/run/retry').status_code==200
        try:
            assert entered.wait(3)
            pending=client.get('/api/matches/run').json()
            assert pending['state']=='running' and pending['result']['evaluated']==0
            assert len(pending['result']['unscored'])==2
            assert client.post('/api/matches/run/retry').status_code==400
        finally:release.set()
        assert ai.match_lock.acquire(timeout=5);ai.match_lock.release()
        complete=client.get('/api/matches/run').json()
        assert complete['state']=='complete' and complete['result']['evaluated']==2
        assert len(calls)==1
        assert client.post('/api/matches/run/retry').status_code==400
        assert not ai.match_lock.locked()
        assert client.post('/api/matches/missing/retry').status_code==400
        assert not ai.match_lock.locked()
        store.labels('baidu:0',['收藏方向'])
        with store.connection() as db:db.execute("UPDATE jobs SET status='removed' WHERE id='baidu:1'")
        refreshed=client.get('/api/matches/run').json()['result']['results']
        assert next(r for r in refreshed if r['id']=='baidu:0')['job']['labels']==['收藏方向']
        assert next(r for r in refreshed if r['id']=='baidu:1')['stale']


def test_no_candidates_completes_without_scoring(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.db');ai=AIService(store)
    def complete(task,*args,**kwargs):
        assert task=='match_intent';return INTENT,'test'
    monkeypatch.setattr(ai.client,'complete',complete)
    row,result=run_match(store,ai)
    assert row['state']=='complete' and result['candidate_count']==0 and not result['unscored']
