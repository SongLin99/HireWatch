import json
from dataclasses import replace

import pytest

from hirewatch.models import internship_evidence, internship_kind
from hirewatch.extra_sources import feishu_job, moka_job, MokaDetailCache
from hirewatch.storage import Store
from test_ai import make_job, parsed
from hirewatch.ai import AIService


@pytest.mark.parametrize('text', [
    '【岗位类别】：实习/全职',
    '<p><strong>【岗位类别】：实习/全职</strong></p>',
    '**岗位类别**：\n实习 / 全职',
    '招聘性质：全职或实习',
    'Employment type: Internship / Full-time',
    '欢迎实习生投递',
    '可以以实习形式入职',
])
def test_explicit_body_evidence_admits_jobs_without_intern_title(text):
    assert internship_evidence('算法研发','全职',text)


@pytest.mark.parametrize('text', [
    '岗位类别：全职。有实习经历优先。',
    '负责管理和指导实习生。',
    '本岗位不接受实习生。',
    '暂不招收实习生，提供全职岗位。',
    '工作类型：非实习',
    '欢迎具有实习经历的全职候选人投递。',
])
def test_past_experience_and_negation_do_not_admit_full_time(text):
    assert not internship_evidence('算法研发','全职',text)


def test_mixed_body_has_priority_over_list_title_and_type():
    assert internship_kind('算法实习生','全职','【岗位类别】：实习/全职')=='实习（兼招全职）'
    card=dict(title='情感智能数据产品经理',info='模型数据策略\n杭州 北京\n全职',text='【岗位类别】：实习/全职\n【岗位要求】\n熟悉模型训练原理',url='https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/b8e62d9e-5cfb-4f24-bb6b-549bf45e1ee0',published='')
    job=moka_job(card,'deepseek')
    assert job and job.internship_type=='实习（兼招全职）'
    assert '模型训练' in job.requirements
    assert '岗位类别' in job.description
    row=dict(id='1',title='算法研发',recruit_type={'name':'全职'},description='【岗位类别】：实习/全职',requirement='Python',job_subject={'name':'校招'})
    assert feishu_job(row,'zhipu','https://example.com','index').internship_type=='实习（兼招全职）'


def test_model_cannot_hide_source_confirmed_internship(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3')
    job=make_job('1','deepseek',title='情感智能数据产品经理',internship_type='实习（兼招全职）',description='【岗位类别】：实习/全职')
    store.snapshot('deepseek',[job])
    with store.connection() as db:
        db.execute("INSERT INTO job_analysis(job_id,fingerprint,prompt_version,payload,state) VALUES(?,?,?,?,'complete')",(job.id,job.fingerprint,'test',json.dumps(parsed(id=job.id,employment='非实习',internship_type='全职'))))
    row=store.job_feed()[0]
    assert row['employment']=='实习'
    assert row['analysis']['employment']=='实习'
    assert row['analysis']['internship_type']=='实习（兼招全职）'
    assert row['internship_evidence']=='【岗位类别】：实习/全职'


def test_new_model_results_preserve_explicit_mixed_hiring(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs.sqlite3')
    job=make_job('1','deepseek',title='研发工程师',description='【岗位类别】：实习/全职',internship_type='实习（兼招全职）')
    store.snapshot('deepseek',[job])
    ai=AIService(store)
    monkeypatch.setattr(ai.client,'complete',lambda *a,**kw:({'jobs':[parsed(id=job.id,employment='非实习',internship_type='全职')]},'test'))
    assert ai.parse_batch()==1
    payload=json.loads(store.rows('SELECT payload FROM job_analysis')[0]['payload'])
    assert payload['employment']=='实习' and payload['internship_type']=='实习（兼招全职）'


def test_detail_cache_rechecks_changes_and_expiry_without_llm_calls(tmp_path,monkeypatch):
    cache=MokaDetailCache(tmp_path/'cache.sqlite3')
    card=dict(url='https://example.com/#/job/1',title='研发',info='北京',text='列表摘要')
    monkeypatch.setattr('hirewatch.extra_sources.time.time',lambda:100)
    assert cache.get(card) is None
    cache.put(card,'岗位类别：全职')
    assert cache.get(card)=='岗位类别：全职'
    assert cache.get({**card,'text':'更新摘要'}) is None
    monkeypatch.setattr('hirewatch.extra_sources.time.time',lambda:100+cache.TTL)
    assert cache.get(card) is None
    with pytest.raises(ValueError):cache.put(card,'')


def test_count_anomalies_are_visible_and_failures_preserve_last_coverage(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3')
    store.snapshot('deepseek',[make_job(str(i),'deepseek') for i in range(6)],coverage={'reported':10,'checked':10,'included':6,'complete':True})
    store.snapshot('deepseek',[],allow_empty=True,coverage={'reported':10,'checked':10,'included':0,'complete':True})
    before=store.rows("SELECT coverage FROM sources WHERE id='deepseek'")[0]['coverage']
    assert len(json.loads(before)['warnings'])==2
    store.source_error('deepseek','官网详情无法读取')
    assert store.rows("SELECT coverage FROM sources WHERE id='deepseek'")[0]['coverage']==before


def test_disappearing_official_portal_is_reported(tmp_path):
    store=Store(tmp_path/'jobs.sqlite3')
    job=make_job('1','deepseek')
    store.snapshot('deepseek',[job],coverage={'entries':[{'url':'https://example.com/social?page=2'},{'url':'https://example.com/campus'}],'checked':10,'included':1,'complete':True})
    store.snapshot('deepseek',[job],coverage={'entries':[{'url':'https://example.com/social?page=3'}],'checked':10,'included':1,'complete':True})
    coverage=json.loads(store.rows("SELECT coverage FROM sources WHERE id='deepseek'")[0]['coverage'])
    assert len(coverage['warnings'])==1 and '入口' in coverage['warnings'][0]
