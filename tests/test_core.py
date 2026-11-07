import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hirewatch.app import create_app
from hirewatch.models import Job, matches
from hirewatch.notify import Notifier, validate_secret
from hirewatch.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


def job(id="1", **kwargs):
    data = dict(source="baidu", external_id=id, company="百度", title="Python 后端实习生", city="北京 / 上海", category="研发", department="ACG", internship_type="日常实习", description="开发 Agent 服务", requirements="每周 4 天，持续 3 个月", url="https://talent.baidu.com/jobs/detail/"+id)
    data.update(kwargs)
    return Job(**data)


def enable(store):
    settings = store.settings()
    settings["notifications"].update(enabled=True, sendkey="SCTTEST123", cooldown_minutes=1)
    settings["rule"]["keywords"] = ["Python"]
    store.set_settings(settings)


def test_first_scan_is_baseline_then_new_and_changed_are_queued(store):
    enable(store)
    result = store.snapshot("baidu", [job()])
    assert result["baseline"]
    assert store.rows("SELECT delivery FROM events")[0]["delivery"] == "baseline"
    store.snapshot("baidu", [job()])
    assert len(store.rows("SELECT * FROM events")) == 1
    store.snapshot("baidu", [job(), job("2")])
    store.snapshot("baidu", [job(requirements="每周 5 天"), job("2")])
    assert [(e["kind"],e["delivery"]) for e in store.rows("SELECT * FROM events ORDER BY id")]==[("new","baseline"),("new","pending"),("updated","pending")]
    assert Store(store.path).rows("SELECT COUNT(*) AS n FROM jobs")[0]["n"] == 2


def test_timestamp_refresh_does_not_generate_content_change(store):
    store.snapshot("baidu", [job(published_at="2026-10-01")])
    store.snapshot("baidu", [job(published_at="2026-10-02", source_updated_at="2026-10-13")])
    assert len(store.rows("SELECT * FROM events")) == 1


def test_failure_and_empty_source_never_mark_jobs_missing(store):
    store.snapshot("baidu", [job(), job("2")])
    with pytest.raises(ValueError):
        store.snapshot("baidu", [])
    store.source_error("baidu", "网络失败")
    assert all(r["status"] == "active" for r in store.rows("SELECT * FROM jobs"))
    assert store.rows("SELECT last_success FROM sources")[0]["last_success"]
    store.snapshot("baidu", [job("2")])
    assert store.rows("SELECT status FROM jobs WHERE id='baidu:1'")[0]["status"] == "active"
    store.snapshot("baidu", [job("2")])
    assert store.rows("SELECT status FROM jobs WHERE id='baidu:1'")[0]["status"] == "unseen"
    store.snapshot("baidu", [job(), job("2")])
    assert store.rows("SELECT status FROM jobs WHERE id='baidu:1'")[0]["status"] == "active"


def test_keyword_rules():
    assert matches(job(), {"keywords":["python","后端"],"mode":"all","cities":["上海"],"companies":["baidu"]})
    assert not matches(job(), {"keywords":["Python"],"exclude":["Agent"]})
    assert not matches(job(), {"companies":["tencent"]})
    assert matches(job(), {"keywords":["Rust","Python"],"mode":"any"})
    assert not matches(job(), {"keywords":["Rust","Python"],"mode":"all"})


def test_notifications_retry_and_sent_dedup(store, monkeypatch):
    enable(store)
    store.snapshot("baidu", [job()])
    store.snapshot("baidu", [job(), job("2")])
    notifier = Notifier(store)
    def fail(*args):
        raise ValueError("服务临时失败")
    monkeypatch.setattr("hirewatch.notify.send_message", fail)
    notifier.dispatch()
    pending = store.rows("SELECT * FROM events WHERE delivery='pending'")
    assert len(pending)==1 and pending[0]["attempts"]==1
    with store.connection() as db:
        db.execute("UPDATE deliveries SET created_at='2026-01-01T00:00:00+08:00'")
    calls=[]
    monkeypatch.setattr("hirewatch.notify.send_message", lambda *args: calls.append(args))
    notifier.dispatch()
    notifier.dispatch()
    assert len(calls)==1
    assert not store.rows("SELECT * FROM events WHERE delivery='pending'")


def test_new_rule_is_rechecked_before_dispatch(store, monkeypatch):
    enable(store)
    store.snapshot("baidu", [job()])
    store.snapshot("baidu", [job(), job("2")])
    s=store.settings();s["rule"]["exclude"]=["Python"];store.set_settings(s)
    calls=[]
    monkeypatch.setattr("hirewatch.notify.send_message", lambda *args: calls.append(args))
    Notifier(store).dispatch()
    assert not calls
    assert store.rows("SELECT delivery FROM events ORDER BY id DESC LIMIT 1")[0]["delivery"]=="ignored"


def test_wecom_message_respects_utf8_limit_and_keeps_overflow(store, monkeypatch):
    enable(store)
    store.snapshot("baidu", [job()])
    store.snapshot("baidu", [job(str(i),title="很长的中文岗位名称"*15+"Python") for i in range(1,20)])
    calls=[]
    monkeypatch.setattr("hirewatch.notify.send_message",lambda *args:calls.append(args))
    Notifier(store).dispatch()
    assert len(calls)==1 and len(calls[0][2].encode())<4096
    assert store.rows("SELECT * FROM events WHERE delivery='pending'")


def test_secret_validation_rejects_foreign_webhook():
    with pytest.raises(ValueError):
        validate_secret("wecom", "http://127.0.0.1/admin")
    validate_secret("wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=123")


def test_http_filters_favorites_export_and_secret_redaction(tmp_path):
    app=create_app(tmp_path/"app.sqlite3",scheduler=False)
    store=app.state.store
    store.snapshot("baidu",[job(),job("2",city="深圳",title="=敏感公式测试")])
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/jobs?q=python&city=上海').json()['total']==1
        assert client.get('/api/jobs?q=python%20Agent').json()['total']==1
        assert client.patch('/api/jobs/baidu:1/saved',json={'saved':True}).status_code==200
        assert client.get('/api/jobs?saved=true').json()['total']==1
        assert client.get('/api/status').json()['stats']['saved']==1
        exported=client.get('/api/export').content.decode('utf-8-sig')
        assert "'=敏感公式测试" in exported
        settings=client.get('/api/settings').json()
        settings['notifications']['sendkey']='SCTSECRET123'
        response=client.put('/api/settings',json=settings)
        assert response.status_code==200
        assert 'SCTSECRET123' not in response.text
        assert response.json()['notifications']['has_sendkey']
        assert 'sendkey' not in client.get('/api/settings').json()['notifications']
        settings=client.get('/api/settings').json()
        settings['notifications']['enabled']=True
        assert client.put('/api/settings',json=settings).status_code==200
        assert store.settings()['notifications']['sendkey']=='SCTSECRET123'
        assert client.put('/api/settings',json=settings,headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403


def test_invalid_settings_do_not_replace_working_configuration(tmp_path):
    app=create_app(tmp_path/'app.sqlite3',scheduler=False)
    with TestClient(app) as client:
        settings=client.get('/api/settings').json()
        settings['interval_minutes']=1
        assert client.put('/api/settings',json=settings).status_code==422
        assert client.get('/api/settings').json()['interval_minutes']==15
