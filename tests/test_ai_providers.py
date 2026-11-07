import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from hirewatch.ai import AIError, AIService, MODEL, RouterClient, read_key
from hirewatch.app import create_app
from hirewatch.storage import Store


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-environment-for-test')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    return Store(tmp_path/'providers.sqlite3')


def configure(store, **changes):
    settings=store.settings()
    settings['ai'].update(changes)
    store.set_settings(settings)
    return settings


def completion(model, usage=None):
    return Response({'model':model,'choices':[{'message':{'content':'{"ok":true}'}}],
                     'usage':usage or {'prompt_tokens':10,'completion_tokens':5}})


def test_legacy_settings_merge_new_defaults_without_overwriting_budget(store):
    old=store.settings()
    old['ai']={'enabled':False,'daily_budget_usd':5,'batch_size':6}
    store.set_settings(old)
    merged=store.settings()['ai']
    assert merged['provider']=='openrouter' and merged['model']==MODEL
    assert merged['daily_budget_usd']==5 and not merged['enabled'] and merged['batch_size']==6


def test_public_build_never_reads_adjacent_key_and_clear_stops_fallback(tmp_path,monkeypatch,store):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    (tmp_path/'openrouter.txt').write_text('sk-or-private-project-only',encoding='utf8')
    monkeypatch.setattr('hirewatch.ai.paths.RESOURCE_ROOT',tmp_path)
    monkeypatch.setattr('hirewatch.ai.paths.IS_FROZEN',False)
    assert read_key(store)==''
    configure(store,openrouter_api_key='')
    assert read_key(store)==''
    configure(store,openrouter_api_key=None)
    monkeypatch.setattr('hirewatch.ai.paths.IS_FROZEN',True)
    assert read_key(store)==''


def test_settings_redact_preserve_switch_and_explicitly_clear_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-env-unit-only')
    app=create_app(tmp_path/'app.sqlite3',scheduler=False)
    with TestClient(app) as client:
        data=client.get('/api/settings').json()
        assert data['ai']['has_openrouter_api_key']
        data['ai'].update(provider='deepseek',model='deepseek-flash',openrouter_api_key='sk-or-local-unit-only',deepseek_api_key='sk-deepseek-unit-only')
        result=client.put('/api/settings',json=data)
        assert result.status_code==200 and 'sk-deepseek' not in result.text
        public=result.json()
        assert public['ai']['configured'] and public['ai']['has_deepseek_api_key']
        assert 'openrouter_api_key' not in public['ai'] and 'deepseek_api_key' not in public['ai']
        assert client.put('/api/settings',json=public).status_code==200
        assert app.state.store.settings()['ai']['deepseek_api_key']=='sk-deepseek-unit-only'
        public['ai'].update(openrouter_api_key='',deepseek_api_key='')
        cleared=client.put('/api/settings',json=public).json()['ai']
        assert not cleared['configured'] and not cleared['has_openrouter_api_key'] and not cleared['has_deepseek_api_key']
        assert 'sk-or-local' not in client.get('/api/status').text


def test_malformed_secret_validation_never_echoes_secret(tmp_path):
    app=create_app(tmp_path/'validation.sqlite3',scheduler=False)
    with TestClient(app) as client:
        data=client.get('/api/settings').json()
        for value in ('invalid-sensitive-secret','sk-'+('sensitive-secret-'*40)):
            data['ai']['deepseek_api_key']=value
            response=client.put('/api/settings',json=data)
            assert response.status_code in (400,422)
            assert 'sensitive-secret' not in response.text
        data['ai'].pop('deepseek_api_key')
        data['ai']['input_price_usd_per_million']=1
        assert client.put('/api/settings',json=data).status_code==400


def test_health_identity_needs_no_database_reads_and_has_no_paths(tmp_path,monkeypatch):
    app=create_app(tmp_path/'health.sqlite3',scheduler=False)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.store,'connection',lambda:pytest.fail('health must not read DB'))
        response=client.get('/api/health')
        assert response.status_code==200
        data=response.json()
        assert data['app']=='hirewatch' and data['protocol']==1
        assert data['instance']==hashlib.sha256(str(app.state.store.path.resolve()).casefold().encode()).hexdigest()[:16]
        assert str(tmp_path) not in response.text


def test_deepseek_uses_only_its_credential_and_json_object_with_schema_example(store,monkeypatch):
    configure(store,provider='deepseek',model='deepseek-flash',deepseek_api_key='sk-deepseek-own-key',openrouter_api_key='sk-or-other-provider')
    seen=[]
    def get(url,**kw):
        assert url=='https://api.deepseek.com/models'
        assert kw['headers']['Authorization']=='Bearer sk-deepseek-own-key'
        return Response({'data':[{'id':'deepseek-flash'}]})
    def post(url,**kw):
        seen.append((url,kw))
        return completion('deepseek-flash')
    monkeypatch.setattr('hirewatch.ai.requests.get',get)
    monkeypatch.setattr('hirewatch.ai.requests.post',post)
    result,model=RouterClient(store).complete('test','请解析',{}, {'type':'object','properties':{'ok':{'type':'boolean'}}},max_tokens=10)
    assert result=={'ok':True} and model=='deepseek-flash'
    url,kw=seen[0]
    assert url=='https://api.deepseek.com/chat/completions'
    body=kw['json']
    assert body['response_format']=={'type':'json_object'} and body['thinking']=={'type':'disabled'}
    assert 'reasoning' not in body and 'X-OpenRouter-Title' not in kw['headers']
    assert 'JSON Schema' in body['messages'][0]['content'] and '"ok": false' in body['messages'][0]['content']
    cost=store.rows('SELECT cost,status FROM ai_calls')[0]
    assert cost['status']=='complete' and cost['cost']==pytest.approx((10*.3+5*1.2)/1e6)


def test_catalog_is_free_caches_and_provider_or_model_changes_resolve_again(store,monkeypatch):
    configure(store,openrouter_api_key='sk-or-test-catalog',model='vendor/first')
    calls=[]
    def get(url,**kw):
        calls.append(url)
        if 'deepseek.com' in url:return Response({'data':[{'id':'deepseek-flash'}]})
        assert 'headers' not in kw
        return Response({'data':[{'id':id,'pricing':{'prompt':'.000002','completion':'.000004'},
                                  'supported_parameters':['response_format']} for id in ('vendor/first','vendor/second')]})
    monkeypatch.setattr('hirewatch.ai.requests.get',get)
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:pytest.fail('catalog must never generate tokens'))
    client=RouterClient(store)
    assert client.resolve()[0]=='vendor/first'
    assert client.public_catalog('openrouter')['models'][0]['input_price_usd_per_million']==2
    assert len(calls)==1 and store.rows('SELECT * FROM ai_calls')==[]
    configure(store,model='vendor/second')
    assert client.resolve()[0]=='vendor/second'
    configure(store,provider='deepseek',model='deepseek-flash',deepseek_api_key='sk-deepseek-catalog')
    assert client.resolve()[0]=='deepseek-flash'
    assert len(calls)==2 and client.pricing['prompt']==.3/1e6


def test_catalog_endpoint_has_no_paid_calls_and_deepseek_help_without_key(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    app=create_app(tmp_path/'catalog.sqlite3',scheduler=False)
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:pytest.fail('must not generate'))
    with TestClient(app) as client:
        data=client.get('/api/ai/models?provider=deepseek').json()
        assert data['default_model']=='deepseek-flash'
        assert data['models'][0]['input_price_usd_per_million']==.3
        assert client.get('/api/ai/models?provider=other').status_code==422
    assert app.state.store.rows('SELECT * FROM ai_calls')==[]


def test_unknown_deepseek_model_requires_prices_and_missing_usage_keeps_reservation(store,monkeypatch):
    configure(store,provider='deepseek',model='deepseek-future',deepseek_api_key='sk-deepseek-own-key')
    monkeypatch.setattr('hirewatch.ai.requests.get',lambda *a,**kw:Response({'data':[{'id':'deepseek-future'}]}))
    posted=[]
    def post(*a,**kw):
        posted.append(kw)
        return Response({'model':'deepseek-future','choices':[{'message':{'content':'{"ok":true}'}}]})
    monkeypatch.setattr('hirewatch.ai.requests.post',post)
    client=RouterClient(store)
    with pytest.raises(AIError,match='单价'):client.complete('test','extract',{}, {'type':'object'},max_tokens=10)
    assert not posted
    configure(store,input_price_usd_per_million=2,output_price_usd_per_million=4)
    client.complete('test','extract',{}, {'type':'object'},max_tokens=10)
    reserved=len(json.dumps(posted[0]['json'],ensure_ascii=False).encode())*2/1e6+10*4/1e6
    assert store.rows('SELECT cost FROM ai_calls')[0]['cost']==pytest.approx(reserved)


def test_inflight_settings_change_does_not_change_request_accounting(store,monkeypatch):
    configure(store,provider='deepseek',model='deepseek-flash',deepseek_api_key='sk-deepseek-own-key')
    monkeypatch.setattr('hirewatch.ai.requests.get',lambda *a,**kw:Response({'data':[{'id':'deepseek-flash'}]}))
    client=RouterClient(store)
    def post(*a,**kw):
        configure(store,provider='openrouter',model='vendor/next',openrouter_api_key='sk-or-future-key')
        client.invalidate()
        return completion('deepseek-flash')
    monkeypatch.setattr('hirewatch.ai.requests.post',post)
    assert client.complete('test','extract',{}, {'type':'object'},max_tokens=10)[1]=='deepseek-flash'
    row=store.rows('SELECT model,cost FROM ai_calls')[0]
    assert row['model']=='deepseek-flash' and row['cost']==pytest.approx((10*.3+5*1.2)/1e6)


def test_deepseek_budget_blocks_post_and_errors_hide_response_credentials(store,monkeypatch):
    configure(store,provider='deepseek',model='deepseek-flash',deepseek_api_key='sk-deepseek-secret-only',daily_budget_usd=0)
    monkeypatch.setattr('hirewatch.ai.requests.get',lambda *a,**kw:Response({'data':[{'id':'deepseek-flash'}]}))
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:pytest.fail('budget must block'))
    client=RouterClient(store)
    with pytest.raises(AIError,match='预算'):client.complete('test','extract',{}, {'type':'object'})
    configure(store,daily_budget_usd=5)
    response=Response({'error':'sk-deepseek-secret-only'});response.status_code=401
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:response)
    with pytest.raises(AIError) as error:client.complete('test','extract',{}, {'type':'object'})
    assert 'secret' not in str(error.value) and store.rows('SELECT cost FROM ai_calls')[0]['cost']==0


def test_unavailable_model_never_falls_back(store,monkeypatch):
    configure(store,model='vendor/missing',openrouter_api_key='sk-or-unit-model')
    monkeypatch.setattr('hirewatch.ai.requests.get',lambda *a,**kw:Response({'data':[{'id':'vendor/other','pricing':{'prompt':'0','completion':'0'}}]}))
    monkeypatch.setattr('hirewatch.ai.requests.post',lambda *a,**kw:pytest.fail('must not silently substitute'))
    with pytest.raises(AIError,match='未自动切换'):RouterClient(store).complete('test','extract',{}, {'type':'object'})
