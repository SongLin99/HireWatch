import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hirewatch.app import create_app
from hirewatch.ai import read_key

ROOT = Path(__file__).resolve().parent.parent


def load_script(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT/relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_public_profile_needs_no_secret_and_does_not_call_external_services(tmp_path, monkeypatch):
    for name in ('OPENROUTER_API_KEY', 'DEEPSEEK_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    app = create_app(tmp_path/'fresh.sqlite3', scheduler=False)
    with TestClient(app) as client:
        settings = client.get('/api/settings').json()
        assert not settings['ai']['enabled'] and not settings['ai']['configured']
        assert not settings['ai']['has_openrouter_api_key'] and not settings['ai']['has_deepseek_api_key']
        assert not settings['notifications']['enabled']
        assert not settings['notifications']['has_sendkey'] and not settings['notifications']['has_webhook']
        assert client.get('/api/jobs').json()['total'] == 0
        assert client.get('/api/matches').json() == []
        assert client.get('/api/status').json()['stats']['saved'] == 0
        assert client.get('/').status_code == 200
        assert read_key(app.state.store) == ''


def test_public_audit_rejects_realistic_keys_without_echoing_them(tmp_path):
    audit = load_script('scripts/audit_public_source.py')
    source = tmp_path/'module.py'
    for secret in ('sk-or-v1-'+('a'*40), 'sk-'+('a'*32), 'ghp_'+('a'*36)):
        source.write_text('credential = '+repr(secret), encoding='utf-8')
        with pytest.raises(ValueError) as error:
            audit.audit_paths([source], tmp_path)
        assert secret not in str(error.value)
    source.write_text('print("public")', encoding='utf-8')
    assert audit.audit_paths([source], tmp_path) == 1
    private = tmp_path/'data/settings.json'
    private.parent.mkdir()
    private.write_text('{}')
    with pytest.raises(ValueError):
        audit.audit_paths([private], tmp_path)


def test_bootstrap_selects_native_python_and_reuses_successful_setup(tmp_path, monkeypatch):
    launcher = load_script('launcher.py')
    assert launcher.environment_python(tmp_path, 'win32') == tmp_path/'.venv/Scripts/python.exe'
    assert launcher.environment_python(tmp_path, 'darwin') == tmp_path/'.venv/bin/python'
    for name in ('requirements.txt', 'requirements-desktop.txt'):
        (tmp_path/name).write_text('public dependency')
    executable = launcher.environment_python(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_text('test executable')
    calls = []
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *args, **kwargs: calls.append(args))
    assert launcher.prepare(tmp_path) == executable
    assert len(calls) == 2
    assert '--only-shell' in calls[1][0]
    launcher.prepare(tmp_path)
    assert len(calls) == 2
    (tmp_path/'requirements.txt').write_text('changed dependency')
    launcher.prepare(tmp_path)
    assert len(calls) == 4
