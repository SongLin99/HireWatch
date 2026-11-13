import copy

from fastapi.testclient import TestClient
from hirewatch.app import create_app
from hirewatch.collectors import SOURCES
from hirewatch.storage import Store


ADDED={'baichuan','modelbest','baai','shengshu'}


def test_upgrade_adds_sources_once_without_resetting_preferences(tmp_path):
    path=tmp_path/'jobs.sqlite3'
    store=Store(path)
    settings=store.settings()
    settings['enabled_sources']=['zhipu']
    settings['interval_minutes']=45
    settings['rule']['cities']=['北京']
    before=copy.deepcopy(settings)
    store.set_settings(settings)
    with store.connection() as db:
        db.execute("DELETE FROM meta WHERE key='sources_20261113'")
    upgraded=Store(path).settings()
    assert set(upgraded['enabled_sources'])=={'zhipu'}|ADDED
    assert upgraded['rule']==before['rule']
    assert upgraded['ai']==before['ai']
    assert upgraded['notifications']==before['notifications']
    assert upgraded['interval_minutes']==45
    upgraded['enabled_sources']=['zhipu']
    store.set_settings(upgraded)
    assert Store(path).settings()['enabled_sources']==['zhipu']


def test_new_sources_available_in_settings_status_and_filters(tmp_path):
    with TestClient(create_app(db_path=tmp_path/'jobs.sqlite3',scheduler=False)) as client:
        settings=client.get('/api/settings').json()
        assert ADDED <= set(settings['enabled_sources'])
        assert ADDED <= {s['id'] for s in client.get('/api/status').json()['sources']}
        assert ADDED <= {s['id'] for s in client.get('/api/facets').json()['companies']}
        assert len(SOURCES)==16
        for source in ADDED:
            response=client.get('/api/jobs',params={'company':source})
            assert response.status_code==200
