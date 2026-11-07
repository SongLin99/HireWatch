import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hirewatch.ai import AIService, RouterClient, AIError, Intent, PROMPT_VERSION, shortlist, city_supported
from hirewatch.app import create_app
from hirewatch.models import Job
from hirewatch.models import explicit_availability
from hirewatch.storage import Store
from hirewatch.extra_sources import intern_evidence, feishu_job, moka_job


def make_job(id='1',source='baidu',**values):
    args=dict(source=source,external_id=id,company='百度' if source=='baidu' else '上海 AI Lab',title='Python 大模型实习生',city='北京',category='研发',department='ACG',internship_type='日常实习',description='研发 Agent 与 RAG 服务',requirements='本科及以上，每周4天，至少3个月。',url='https://example.com/job/'+id)
    args.update(values)
    return Job(**args)


def parsed(id='baidu:1',**kwargs):
    p=dict(id=id,cities=['北京'],employment='实习',internship_type='日常实习',job_type='大模型/Agent',department='ACG',degree='本科',skills=['Python','RAG'],tags=['Agent','RAG'],min_days=4,min_months=3,summary='研发 Agent 服务。',confidence=.95,evidence='研发 Agent 与 RAG 服务')
    return {**p,**kwargs}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'ai.sqlite3')


def test_classification_cache_is_invalidated_only_on_content_change(store,monkeypatch):
    job=make_job();store.snapshot('baidu',[job]);ai=AIService(store);calls=[]
    def response(*args,**kwargs):calls.append(args);return {'jobs':[parsed()]},'deepseek/flash-test'
    monkeypatch.setattr(ai.client,'complete',response)
    assert ai.parse_batch()==1
    assert ai.parse_batch()==0 and len(calls)==1
    row=store.job_feed()[0]
    assert row['skills']==['Python','RAG'] and row['min_days']==4
    store.snapshot('baidu',[replace(job,requirements='本科及以上，每周5天，至少3个月。')])
    assert store.job_feed()[0]['analysis'] is None
    assert ai.parse_batch()==1 and len(calls)==2
    # The old extracted number is not in the updated source, so it must not be retained.
    assert store.job_feed()[0]['analysis']['min_days'] is None
    assert store.job_feed()[0]['min_days']==5


def test_model_cannot_assign_foreign_ids_or_guess_missing_fields(store,monkeypatch):
    store.snapshot('baidu',[make_job()]);ai=AIService(store)
    monkeypatch.setattr(ai.client,'complete',lambda *a,**kw:({'jobs':[parsed(id='invented:999')]},'test'))
    assert ai.parse_batch()==0 and ai.state()['failed']==1
    assert store.job_feed()[0]['analysis'] is None
    ai.retry_failed()
    monkeypatch.setattr(ai.client,'complete',lambda *a,**kw:({'jobs':[parsed(cities=['上海'],department='不存在的团队',min_months=6,evidence='编造原文')]},'test'))
    assert ai.parse_batch()==1
    p=store.job_feed()[0]['analysis']
    assert p['cities']==[] and p['department'] is None and p['min_months'] is None and not p['evidence']
    assert city_supported('上海','Shanghai office')


def test_analysis_result_is_discarded_if_source_changed_during_call(store,monkeypatch):
    original=make_job();store.snapshot('baidu',[original]);ai=AIService(store)
    def response(*args,**kwargs):
        store.snapshot('baidu',[replace(original,title='更新后的岗位')])
        return {'jobs':[parsed()]},'test'
    monkeypatch.setattr(ai.client,'complete',response)
    ai.parse_batch()
    assert not store.rows('SELECT * FROM job_analysis')


def test_hard_constraints_and_company_aliases_keep_only_eligible_internships(store):
    store.snapshot('baidu',[make_job(),make_job('2',city='深圳')])
    store.snapshot('shailab',[make_job('3','shailab',city='上海')])
    intent=Intent(cities=['上海'],cities_are_required=True,companies=['上海人工智能实验室'],companies_are_required=True,terms=['Agent','RAG'],exclude_terms=['销售'],role_types=['大模型/Agent'],available_days=4,available_months=3,summary='上海研究实习')
    rows,count=shortlist(store.job_feed(),intent,[],[],20)
    assert count==1 and rows[0]['id']=='shailab:3'


def test_internship_admission_does_not_confuse_past_experience():
    assert intern_evidence('Agent Harness 研究员（实习/全职）')
    assert intern_evidence('Marketing Intern')
    assert intern_evidence('算法研发',requirements='欢迎实习生投递')
    assert not intern_evidence('2027届校招全职算法研发','全职',requirements='欢迎在校生投递')
    assert not intern_evidence('高级工程师','全职','有实习经历优先，负责指导实习生')
    assert not intern_evidence('算法工程师',requirements=None)
    assert feishu_job({'title':'高级工程师','recruit_type':{'name':'全职'},'requirement':'有实习经历优先'},'zhipu','https://example.com','index') is None


def test_verified_empty_source_is_success_without_erasing_history(store):
    store.snapshot('deepseek',[make_job('1','deepseek')])
    store.snapshot('deepseek',[],allow_empty=True)
    assert store.rows("SELECT count FROM sources WHERE id='deepseek'")[0]['count']==0
    assert len(store.rows('SELECT * FROM jobs'))==1


def test_budget_prevents_api_calls_and_credentials_never_enter_response(store,monkeypatch):
    s=store.settings();s['ai']['daily_budget_usd']=0;store.set_settings(s)
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-test-secret-only')
    client=RouterClient(store)
    client.pricing={'prompt':.0001,'completion':.0001};client.last_catalog=10**12
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:pytest.fail('Budget must block HTTP'))
    with pytest.raises(AIError,match='预算'):client.complete('test','extract',{}, {'type':'object'})
    assert not store.rows('SELECT * FROM ai_calls')


def test_routing_error_is_sanitized_and_unbilled_reservation_is_released(store,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-test-secret-only')
    client=RouterClient(store);client.pricing={'prompt':1e-8,'completion':1e-8};client.last_catalog=10**12
    class Rejected:
        status_code=401
        text='credentials should not be copied from this response'
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:Rejected())
    with pytest.raises(AIError) as error:client.complete('test','extract',{}, {'type':'object'})
    assert 'secret' not in str(error.value) and 'credentials' not in str(error.value)
    assert store.rows('SELECT cost,status FROM ai_calls')[0]=={'cost':0,'status':'failed'}


def test_filters_labels_export_and_intern_only_scope(tmp_path,monkeypatch):
    app=create_app(tmp_path/'app.sqlite3',scheduler=False);store=app.state.store
    store.snapshot('baidu',[make_job()]);store.snapshot('shailab',[make_job('2','shailab',city='上海',title='全职算法工程师',internship_type='全职')])
    for id,p in [('baidu:1',parsed()),('shailab:2',parsed(id='shailab:2',cities=['上海'],min_days=5,employment='非实习'))]:
        fingerprint=store.rows('SELECT fingerprint FROM jobs WHERE id=?',(id,))[0]['fingerprint']
        with store.connection() as db:db.execute("INSERT INTO job_analysis(job_id,fingerprint,prompt_version,payload,state) VALUES(?,?,?,?,'complete')",(id,fingerprint,PROMPT_VERSION,json.dumps(p,ensure_ascii=False)))
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-test-secret-only')
    with TestClient(app) as client:
        assert client.get('/api/jobs').json()['total']==1
        assert client.get('/api/jobs?company=baidu,shailab&job_type=大模型/Agent&degree=本科&days=3').json()['total']==0
        assert client.patch('/api/jobs/baidu:1/labels',json={'labels':['优先投递','优先投递','已投递']}).json()['labels']==['优先投递','已投递']
        assert client.get('/api/jobs?tag=已投递').json()['total']==1
        assert client.get('/api/jobs?q=优先投递').json()['total']==1
        assert client.get('/api/facets').json()['labels'][0]['count']==1
        assert 'RAG' in client.get('/api/export').content.decode('utf8')
        assert 'sk-or-test' not in client.get('/api/status').text
        assert client.get('/assets/openrouter.txt').status_code==404
        assert client.patch('/api/jobs/baidu:1/labels',json={'labels':['x'*40]}).status_code==400


def test_explicit_availability_does_not_use_graduation_months():
    assert explicit_availability('每周至少4-5天，可连续实习6个月。2028届毕业。')==(4,6)
    assert explicit_availability('一周三天，实习半年。')==(3,6)
    assert explicit_availability('2028年6月毕业，有4年研发经验。')==(None,None)


def test_matching_rejects_invented_results_and_saves_real_scores(store,monkeypatch):
    store.snapshot('baidu',[make_job()]);ai=AIService(store)
    intent=dict(cities=['北京'],cities_are_required=True,companies=[],companies_are_required=False,terms=['Agent'],exclude_terms=[],role_types=[],available_days=4,available_months=3,summary='北京Agent实习')
    for run_id,returned_id in [('good','baidu:1'),('bad','invented:999')]:
        with store.connection() as db:db.execute("INSERT INTO match_runs(id,prompt,state,progress,created_at,updated_at) VALUES(?,?,'pending','','2026-10-13','2026-10-13')",(run_id,'北京Agent实习'))
        def completion(task,*args,**kwargs):
            if task=='match_intent':return intent,'test'
            return {'results':[{'id':returned_id,'score':85,'reason':'Agent与Python相关','strengths':['研发Agent服务'],'gaps':['论文经历未提供'],'evidence':'研发 Agent 与 RAG 服务'}]},'test'
        monkeypatch.setattr(ai.client,'complete',completion)
        ai.match_lock.acquire();ai._match(run_id,'北京Agent实习',[],[],20)
    good=store.rows("SELECT * FROM match_runs WHERE id='good'")[0]
    bad=store.rows("SELECT * FROM match_runs WHERE id='bad'")[0]
    assert good['state']=='complete' and json.loads(good['result'])['evaluated']==1
    assert bad['state']=='failed'
    failed=json.loads(bad['result'])
    assert failed['evaluated']==0 and failed['missing_ids']==['baidu:1']
    assert failed['unscored'][0]['id']=='baidu:1' and failed['results']==[]


def test_labels_and_favorites_survive_source_refresh(store):
    job=make_job();store.snapshot('baidu',[job]);store.save_job(job.id,True);store.labels(job.id,['已投递'])
    store.snapshot('baidu',[replace(job,requirements='每周5天，至少6个月')])
    row=store.job_feed()[0]
    assert row['saved']==1 and row['labels']==['已投递']
