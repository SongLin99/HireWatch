import json

from fastapi.testclient import TestClient

from hirewatch.app import create_app
from test_match_repair import INTENT,score,seed,run_match


def test_board_scored_and_failed_candidates_share_persistent_favorites(tmp_path,monkeypatch):
    app=create_app(tmp_path/'jobs.db',scheduler=False);store=app.state.store;ai=app.state.ai;seed(store,2)
    monkeypatch.setattr(ai.client,'complete',lambda task,*a,**k:(INTENT if task=='match_intent' else {'results':[score('baidu:0')]},'test'))
    run_match(store,ai)
    original=store.rows("SELECT result FROM match_runs WHERE id='run'")[0]['result']
    with TestClient(app) as client:
        for id,field,count in [('baidu:0','results',1),('baidu:1','unscored',2)]:
            response=client.patch('/api/jobs/'+id+'/saved',json={'saved':True})
            assert response.json()=={'saved':True,'saved_count':count}
            assert client.patch('/api/jobs/'+id+'/saved',json={'saved':True}).json()['saved_count']==count
            result=client.get('/api/matches/run').json()['result']
            assert next(r for r in result[field] if r['id']==id)['job']['saved']==1
            assert client.get('/api/jobs/'+id).json()['saved']==1
        assert client.get('/api/jobs?saved=true').json()['total']==2
        # Collection status and historical model snapshots do not create separate favorites.
        with store.connection() as db:db.execute("UPDATE jobs SET status='unseen' WHERE id='baidu:1'")
        assert client.get('/api/jobs?saved=true').json()['total']==2
        assert client.patch('/api/jobs/baidu:0/saved',json={'saved':False}).json()['saved_count']==1
        result=client.get('/api/matches/run').json()['result']
        assert result['results'][0]['job']['saved']==0
        assert result['unscored'][0]['job']['saved']==1
        assert store.rows("SELECT result FROM match_runs WHERE id='run'")[0]['result']==original
        assert json.loads(original)['results'][0]['job']['saved']==0
        assert client.patch('/api/jobs/missing/saved',json={'saved':True}).status_code==404
        assert not store.rows('SELECT * FROM ai_calls') and not store.rows('SELECT * FROM deliveries')
