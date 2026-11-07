import re

from fastapi.testclient import TestClient

from hirewatch.app import create_app


def test_subscription_options_are_in_initial_html_and_assets_are_versioned(tmp_path):
    app=create_app(tmp_path/'jobs.sqlite3',scheduler=False)
    with TestClient(app) as client:
        response=client.get('/')
        assert response.status_code==200
        assert 'no-cache' in response.headers['cache-control']
        html=response.text
        for name,count in [('job-types',13),('internship-types',7),('degrees',6)]:
            group=re.search(r'id="rule-'+name+r'"[^>]*>(.*?)</div>',html,re.S)[1]
            assert group.count('type="checkbox"')==count
        assets=re.findall(r'(?:href|src)="(/assets/[^\"]+\.(?:js|css)\?v=[a-f0-9]{16})"',html)
        assert len(assets)==5
        assert not re.search(r'(?:href|src)="/assets/[^\"]+\.(?:css|js)"',html)
        for asset in assets:
            resource=client.get(asset)
            assert resource.status_code==200
            assert 'no-cache' in resource.headers['cache-control']
            unchanged=client.get(asset,headers={'If-None-Match':resource.headers['etag']})
            assert unchanged.status_code==304
            assert 'no-cache' in unchanged.headers['cache-control']


def test_asset_content_change_changes_the_url_without_restarting_server(tmp_path,monkeypatch):
    web=tmp_path/'web';web.mkdir()
    (web/'index.html').write_text('<script src="/assets/app.js"></script><link href="/assets/style.css">',encoding='utf-8')
    script=web/'app.js';script.write_text('console.log("first")',encoding='utf-8')
    (web/'style.css').write_text('body{color:black}',encoding='utf-8')
    monkeypatch.setattr('hirewatch.app.ROOT',tmp_path)
    app=create_app(tmp_path/'jobs.sqlite3',scheduler=False)
    with TestClient(app) as client:
        before=client.get('/').text
        script.write_text('console.log("second")',encoding='utf-8')
        after=client.get('/').text
        assert re.search(r'app.js\?v=([a-f0-9]+)',before)[1]!=re.search(r'app.js\?v=([a-f0-9]+)',after)[1]
        assert re.search(r'style.css\?v=([a-f0-9]+)',before)[1]==re.search(r'style.css\?v=([a-f0-9]+)',after)[1]
