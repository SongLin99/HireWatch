import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hirewatch.app import create_app
from hirewatch.models import matches, subscription_state
from hirewatch.notify import Notifier
from hirewatch.storage import Store
from test_ai import make_job, parsed


def analyze(store,job,**values):
    with store.connection() as db:
        db.execute("""INSERT OR REPLACE INTO job_analysis(job_id,fingerprint,prompt_version,payload,state)
            VALUES(?,?,?,?,'complete')""",(job.id,job.fingerprint,'test',json.dumps(parsed(id=job.id,**values))))


def enabled_store(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3')
    settings=store.settings()
    settings['notifications'].update(enabled=True,sendkey='SCTTEST123',cooldown_minutes=1)
    settings['rule'].update(job_types=['大模型/Agent'],degrees=['本科'],tags=['RAG'],available_days=4,available_months=3)
    store.set_settings(settings)
    return store


def test_structured_filters_combine_groups_and_tag_modes():
    row=dict(source='deepseek',city='杭州 / 北京',job_type='大模型/Agent',department='模型数据策略',internship_type='实习（兼招全职）',degree='本科',min_days=4,min_months=3,skills=['Python','RAG'],tags=['Agent'],labels=['优先投递'],ai_status='complete')
    rule=dict(companies=['deepseek'],cities=['北京','上海'],job_types=['算法/研究','大模型/Agent'],internship_types=['兼招全职'],departments=['数据策略'],degrees=['本科'],tags=['Python','优先投递'],tag_mode='all',available_days=4,available_months=3)
    assert subscription_state(row,rule)=='matched'
    for field,value in [('job_types',['后端开发']),('departments',['财务']),('degrees',['博士']),('tags',['Python','CUDA']),('exclude_tags',['RAG']),('available_days',3),('available_months',2)]:
        assert not matches(row,{**rule,field:value}),field
    assert matches(row,{**rule,'tags':['CUDA','Python'],'tag_mode':'any'})


def test_unknown_qualification_policy_is_explicit_and_does_not_relax_type():
    row=dict(degree='未说明',requirements='',job_type='',ai_status='complete')
    rule=dict(degrees=['本科'],available_days=4,available_months=3,unknown_policy='include')
    assert matches(row,rule)
    assert not matches(row,{**rule,'unknown_policy':'exclude'})
    assert not matches(row,{**rule,'job_types':['大模型/Agent']})
    assert matches({**row,'degree':'本科','requirements':'每周3天，实习至少2个月'}, {**rule,'unknown_policy':'exclude'})


def test_raw_new_event_waits_for_analysis_and_then_sends_matching_job(tmp_path,monkeypatch):
    store=enabled_store(tmp_path)
    store.snapshot('baidu',[make_job('baseline')])
    new=make_job('new')
    store.snapshot('baidu',[make_job('baseline'),new])
    calls=[]
    monkeypatch.setattr('hirewatch.notify.send_message',lambda *a:calls.append(a))
    notifier=Notifier(store)
    notifier.dispatch()
    assert not calls
    assert store.rows("SELECT delivery FROM events WHERE job_id=?",(new.id,))[0]['delivery']=='pending'
    analyze(store,new)
    notifier.dispatch()
    assert len(calls)==1 and new.title in calls[0][2]
    assert store.rows("SELECT delivery FROM events WHERE job_id=?",(new.id,))[0]['delivery']=='sent'
    notifier.dispatch()
    assert len(calls)==1


def test_unmatched_analysis_and_removed_jobs_are_not_sent(tmp_path,monkeypatch):
    store=enabled_store(tmp_path)
    store.snapshot('baidu',[make_job('baseline')])
    wrong=make_job('wrong');removed=make_job('removed')
    store.snapshot('baidu',[make_job('baseline'),wrong,removed])
    analyze(store,wrong,job_type='运营/市场')
    analyze(store,removed)
    with store.connection() as db:db.execute("UPDATE jobs SET status='unseen' WHERE id=?",(removed.id,))
    monkeypatch.setattr('hirewatch.notify.send_message',lambda *a:pytest.fail('Excluded jobs must not send'))
    Notifier(store).dispatch()
    assert not store.rows("SELECT * FROM events WHERE delivery='pending'")


def test_changed_source_does_not_use_stale_ai_fields_or_event_payload(tmp_path,monkeypatch):
    store=enabled_store(tmp_path)
    store.snapshot('baidu',[make_job('baseline')])
    old=make_job('new');store.snapshot('baidu',[make_job('baseline'),old]);analyze(store,old)
    changed=replace(old,title='更新后的 Agent 实习生',requirements='本科，每周4天，实习3个月')
    store.snapshot('baidu',[make_job('baseline'),changed])
    calls=[];monkeypatch.setattr('hirewatch.notify.send_message',lambda *a:calls.append(a))
    notifier=Notifier(store);notifier.dispatch()
    assert not calls and len(store.rows("SELECT * FROM events WHERE delivery='pending'"))==2
    analyze(store,changed,job_type='运营/市场')
    notifier.dispatch()
    assert not calls and not store.rows("SELECT * FROM events WHERE delivery='pending'")


def test_settings_compatibility_validation_and_preview_are_read_only(tmp_path):
    app=create_app(tmp_path/'app.sqlite3',scheduler=False);store=app.state.store
    job=make_job();store.snapshot('baidu',[job,make_job('pending')]);analyze(store,job)
    settings=store.settings();settings['rule']={'keywords':[],'exclude':[],'companies':[],'cities':[],'mode':'any'}
    settings['ai']['daily_budget_usd']=5
    settings['notifications']['sendkey']='SCTKEEPME'
    store.set_settings(settings)
    with TestClient(app) as client:
        current=client.get('/api/settings').json()
        assert current['rule']['job_types']==[] and current['rule']['unknown_policy']=='include'
        current['rule'].update(job_types=['大模型/Agent'],tags=['RAG'],tag_mode='all',degrees=['本科'],departments=['ACG'],available_days=4,available_months=3,unknown_policy='exclude')
        saved=client.put('/api/settings',json=current)
        assert saved.status_code==200
        assert saved.json()['rule']==current['rule']
        assert store.settings()['ai']['daily_budget_usd']==5
        assert store.settings()['notifications']['sendkey']=='SCTKEEPME'
        before=store.settings();events=store.rows('SELECT * FROM events')
        preview=client.post('/api/subscription/preview',json=current['rule'])
        assert preview.status_code==200
        assert preview.json()['matched']==1 and preview.json()['waiting_for_analysis']==1
        assert preview.json()['items'][0]['id']==job.id
        assert client.get('/api/jobs?matching=true').json()['total']==preview.json()['matched']
        assert before==store.settings() and events==store.rows('SELECT * FROM events')
        assert not store.rows('SELECT * FROM ai_calls') and not store.rows('SELECT * FROM deliveries')
        assert client.post('/api/subscription/preview',json={'job_types':['不存在']}).status_code==400
        for bad in ({'available_days':8},{'available_months':-1},{'tag_mode':'bad'},{'unknown_policy':'bad'}):
            assert client.put('/api/settings',json={**current,'rule':{**current['rule'],**bad}}).status_code==422
        assert client.put('/api/settings',json={**current,'rule':{**current['rule'],'job_types':['不存在']}}).status_code==400
        assert store.settings()==before


def test_default_lists_do_not_leak_across_settings_calls(tmp_path):
    store=Store(tmp_path/'legacy.sqlite3')
    settings=store.settings();settings['rule']={};store.set_settings(settings)
    first=store.settings();first['rule']['tags'].append('local edit')
    assert store.settings()['rule']['tags']==[]
