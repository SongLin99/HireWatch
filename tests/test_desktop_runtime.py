import importlib.machinery
import importlib.util
import errno
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from hirewatch import paths


def desktop_module():
    source=Path(__file__).resolve().parent.parent/'desktop.pyw'
    loader=importlib.machinery.SourceFileLoader('desktop_entry',str(source))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


def test_frozen_data_does_not_write_to_program_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(paths,'IS_FROZEN',True)
    monkeypatch.setattr(paths.sys,'platform','win32')
    monkeypatch.delenv('HIREWATCH_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    assert paths.data_directory()==tmp_path/'local/HireWatch/data'
    monkeypatch.setenv('HIREWATCH_DATA_DIR',str(tmp_path/'chosen'))
    assert paths.data_directory()==tmp_path/'chosen'


def test_macos_and_linux_use_per_user_data_directories(tmp_path,monkeypatch):
    monkeypatch.setattr(paths,'IS_FROZEN',True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    monkeypatch.delenv('HIREWATCH_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'windows-only'))
    monkeypatch.setattr(paths.sys,'platform','darwin')
    assert paths.data_directory()==tmp_path/'Library/Application Support/HireWatch/data'
    monkeypatch.setenv('HIREWATCH_DATA_DIR',str(tmp_path/'chosen'))
    assert paths.data_directory()==tmp_path/'chosen'
    monkeypatch.delenv('HIREWATCH_DATA_DIR')
    monkeypatch.setattr(paths.sys,'platform','linux')
    monkeypatch.delenv('XDG_DATA_HOME',raising=False)
    assert paths.data_directory()==tmp_path/'.local/share/HireWatch/data'
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'xdg'))
    assert paths.data_directory()==tmp_path/'xdg/HireWatch/data'
    monkeypatch.setenv('XDG_DATA_HOME','relative-location')
    assert paths.data_directory()==tmp_path/'.local/share/HireWatch/data'
    monkeypatch.setattr(paths,'IS_FROZEN',False)
    assert paths.data_directory()==paths.RESOURCE_ROOT/'data'


def test_runtime_state_validation_and_database_identity(tmp_path):
    desktop=desktop_module()
    assert desktop.instance_id(tmp_path/'jobs.db')!=desktop.instance_id(tmp_path/'other.db')
    assert desktop.read_runtime(tmp_path) is None
    path=tmp_path/'desktop-runtime.json'
    path.write_text('invalid',encoding='utf-8');assert desktop.read_runtime(tmp_path) is None
    path.write_text(json.dumps({'port':1}),encoding='utf-8');assert desktop.read_runtime(tmp_path) is None
    path.write_text(json.dumps({'port':8765}),encoding='utf-8');assert desktop.read_runtime(tmp_path)['port']==8765
    for invalid in [[],None,{'port':True},{'port':8765.2},{'port':8765,'process_id':-1}]:
        path.write_text(json.dumps(invalid),encoding='utf-8')
        assert desktop.read_runtime(tmp_path) is None


@pytest.mark.parametrize('payload',[
    [],None,{'app':'hirewatch','protocol':1},
    {'app':'hirewatch','protocol':True,'process_id':123,'instance':'1'*16},
    {'app':'hirewatch','protocol':1,'process_id':-1,'instance':'1'*16},
    {'app':'hirewatch','protocol':1,'process_id':123,'instance':'wrong'},
])
def test_health_rejects_incomplete_or_foreign_responses(payload,monkeypatch):
    desktop=desktop_module()
    opener=SimpleNamespace(open=lambda *args,**kwargs:io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(desktop.urllib.request,'build_opener',lambda *args:opener)
    assert desktop.health(8765) is None


def test_health_accepts_valid_service_identity(monkeypatch):
    desktop=desktop_module()
    payload={'app':'hirewatch','protocol':1,'process_id':123,'instance':'a'*16}
    opener=SimpleNamespace(open=lambda *args,**kwargs:io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(desktop.urllib.request,'build_opener',lambda *args:opener)
    assert desktop.health(8765)==payload


def test_posix_guard_is_exclusive_and_released(tmp_path,monkeypatch):
    desktop=desktop_module();held=[]
    def flock(descriptor,operation):
        if operation==8:
            held.remove(descriptor)
        elif held:
            raise BlockingIOError(errno.EAGAIN,'already locked')
        else:held.append(descriptor)
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(flock=flock,LOCK_EX=2,LOCK_NB=4,LOCK_UN=8))
    monkeypatch.setattr(desktop.sys,'platform','darwin')
    handle,existing=desktop.acquire_guard('identity',tmp_path)
    assert not existing and not handle.closed
    assert desktop.acquire_guard('identity',tmp_path)==(None,True)
    desktop.release_guard(handle)
    assert handle.closed and not held
    replacement,existing=desktop.acquire_guard('identity',tmp_path)
    assert not existing
    desktop.release_guard(replacement)


def test_posix_guard_reports_permission_error_and_closes_file(tmp_path,monkeypatch):
    desktop=desktop_module()
    def flock(*args):raise OSError(errno.EIO,'filesystem error')
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(flock=flock,LOCK_EX=2,LOCK_NB=4,LOCK_UN=8))
    monkeypatch.setattr(desktop.sys,'platform','darwin')
    with pytest.raises(RuntimeError,match='访问权限'):desktop.acquire_guard('identity',tmp_path)
    (tmp_path/'.desktop-identity.lock').unlink()


@pytest.mark.skipif(sys.platform=='win32',reason='Native flock is available on macOS/Linux')
def test_native_posix_guard_prevents_second_instance(tmp_path):
    desktop=desktop_module()
    first,already_running=desktop.acquire_guard('native',tmp_path)
    assert not already_running
    try:assert desktop.acquire_guard('native',tmp_path)==(None,True)
    finally:desktop.release_guard(first)
    second,already_running=desktop.acquire_guard('native',tmp_path)
    try:assert not already_running
    finally:desktop.release_guard(second)


def test_macos_directory_and_error_ui_use_native_commands(tmp_path,monkeypatch):
    desktop=desktop_module();calls=[]
    monkeypatch.setattr(desktop.sys,'platform','darwin')
    monkeypatch.setattr(desktop.subprocess,'Popen',lambda command,**kwargs:calls.append(command))
    monkeypatch.setattr(desktop.subprocess,'run',lambda command,**kwargs:calls.append(command))
    desktop.open_data_directory(tmp_path/'含 空格')
    assert calls[0]==['/usr/bin/open',str(tmp_path/'含 空格')]
    message='启动失败，含有 "引号" 的说明'
    desktop.show_startup_error(message)
    assert calls[1][0]=='/usr/bin/osascript' and calls[1][-1]==message


def test_only_frozen_macos_defaults_to_bundled_browser(monkeypatch):
    desktop=desktop_module()
    monkeypatch.setattr(desktop.sys,'frozen',True,raising=False)
    monkeypatch.setattr(desktop.sys,'platform','darwin')
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH',raising=False)
    desktop.configure_browser_environment()
    assert desktop.os.environ['PLAYWRIGHT_BROWSERS_PATH']==str(Path(sys.executable).parent.parent/'Resources/browser-runtime')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH','chosen-browser-location')
    desktop.configure_browser_environment()
    assert desktop.os.environ['PLAYWRIGHT_BROWSERS_PATH']=='chosen-browser-location'
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH')
    monkeypatch.setattr(desktop.sys,'platform','win32')
    desktop.configure_browser_environment()
    assert 'PLAYWRIGHT_BROWSERS_PATH' not in desktop.os.environ


def test_shutdown_only_terminates_matching_service(tmp_path,monkeypatch):
    desktop=desktop_module();killed=[]
    monkeypatch.setattr(desktop.os,'kill',lambda pid,signal:killed.append(pid))
    monkeypatch.setattr(desktop,'health',lambda port:{'process_id':123,'instance':'other'})
    assert not desktop.stop_owned_service(8765,123,'mine') and not killed
    monkeypatch.setattr(desktop,'health',lambda port:{'process_id':124,'instance':'mine'})
    assert not desktop.stop_owned_service(8765,123,'mine') and not killed
    monkeypatch.setattr(desktop,'health',lambda port:{'process_id':123,'instance':'mine'})
    assert desktop.stop_owned_service(8765,123,'mine') and killed==[123]
    monkeypatch.setattr(desktop,'health',lambda port:{'process_id':-1,'instance':'mine'})
    assert not desktop.stop_owned_service(8765,-1,'mine') and killed==[123]


def test_distribution_audit_rejects_credentials_and_runtime_data(tmp_path):
    source=Path(__file__).resolve().parent.parent/'scripts/build_windows.py'
    spec=importlib.util.spec_from_file_location('build_windows',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import pytest
    (tmp_path/'app.exe').write_bytes(b'program')
    assert len(module.audit_distribution(tmp_path))==1
    (tmp_path/'openrouter.txt').write_text('not-even-a-real-key')
    with pytest.raises(RuntimeError,match='private/runtime'):module.audit_distribution(tmp_path)
    (tmp_path/'openrouter.txt').unlink()
    (tmp_path/'config.json').write_text('{"key":"sk-or-v1-'+('f'*32)+'"}')
    with pytest.raises(RuntimeError,match='credential pattern'):module.audit_distribution(tmp_path)
