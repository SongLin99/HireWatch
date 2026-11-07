"""Desktop entry, background service and Windows/macOS notification-area controls."""
import argparse
import ctypes
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


def instance_id(db_path):
    return hashlib.sha256(str(Path(db_path).resolve()).casefold().encode()).hexdigest()[:16]


def health(port):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
            result=json.load(response)
        if not isinstance(result,dict):return None
        if result.get('app')!='hirewatch' or type(result.get('protocol')) is not int or result['protocol']!=1:return None
        if type(result.get('process_id')) is not int or result['process_id']<=0:return None
        identity=result.get('instance')
        if not isinstance(identity,str) or len(identity)!=16 or any(c not in '0123456789abcdef' for c in identity):return None
        return result
    except (OSError,ValueError,urllib.error.URLError):return None


def acquire_guard(name,directory=None):
    if sys.platform!='win32':
        # macOS Finder can launch another .app while the first starts. An advisory
        # lock remains held for the lifetime of the tray process and is released
        # by the operating system even if the process exits unexpectedly.
        import fcntl
        directory=Path(directory) if directory is not None else Path.home()/'.hirewatch'
        directory.mkdir(parents=True,exist_ok=True)
        handle=(directory/('.desktop-'+name+'.lock')).open('a+b')
        try:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES,errno.EAGAIN}:return None,True
            raise RuntimeError('无法创建启动保护，请检查数据目录的访问权限。') from exc
        return handle,False
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,'Local\\HireWatchDesktop-'+name)
    if not handle:raise RuntimeError('无法创建启动保护，请稍后重试。')
    return handle,ctypes.get_last_error()==183


def release_guard(handle):
    if handle:
        if hasattr(handle,'fileno'):
            import fcntl
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            finally:handle.close()
            return
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)


def choose_port(preferred):
    for port in range(preferred,min(preferred+30,65536)):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port));return port
            except OSError:pass
    raise RuntimeError('没有可用的本机端口，请关闭重复运行的程序后重试。')


def read_runtime(directory):
    try:
        state=json.loads((directory/'desktop-runtime.json').read_text(encoding='utf-8'))
        if not isinstance(state,dict) or type(state.get('port')) not in {int,str}:return None
        state['port']=int(state['port'])
        if not 1024<=state['port']<=65535:return None
        if 'process_id' in state and (type(state['process_id']) is not int or state['process_id']<=0):return None
        return state
    except (OSError,ValueError,TypeError,KeyError):return None


def ensure_service(args,directory,db_path):
    expected=instance_id(db_path);state=read_runtime(directory)
    for port in dict.fromkeys([state['port'] if state else args.port,args.port]):
        existing=health(port)
        if existing and existing['instance']==expected:
            (directory/'desktop-runtime.json').write_text(json.dumps({'port':port,'process_id':existing['process_id'],'instance':expected}),encoding='utf-8')
            return port,int(existing['process_id']),None
    port=choose_port(args.port)
    if getattr(sys,'frozen',False):command=[sys.executable]
    else:command=[sys.executable,str(Path(__file__).resolve())]
    command+=['--serve','--port',str(port),'--data-dir',str(directory)]
    if args.no_scheduler:command.append('--no-scheduler')
    env=os.environ.copy();env['HIREWATCH_DATA_DIR']=str(directory);env['HIREWATCH_DB']=str(db_path)
    # The distributed app never needs to write alongside its program files.
    with open(directory/'server.log','ab',buffering=0) as stdout,open(directory/'server-error.log','ab',buffering=0) as stderr:
        child=subprocess.Popen(command,cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    for _ in range(120):
        current=health(port)
        if current and current['instance']==expected:
            info={'port':port,'process_id':current['process_id'],'instance':expected}
            (directory/'desktop-runtime.json').write_text(json.dumps(info),encoding='utf-8')
            return port,int(current['process_id']),child
        if child.poll() is not None:break
        time.sleep(.25)
    raise RuntimeError('看板未能启动。请查看数据目录中的 server-error.log。')


def stop_owned_service(port,process_id,expected):
    if type(process_id) is not int or process_id<=0:return False
    current=health(port)
    if not current or current.get('process_id')!=process_id or current.get('instance')!=expected:return False
    try:os.kill(process_id,signal.SIGTERM)
    except ProcessLookupError:return False
    return True


def open_page(port,tutorial=False):
    webbrowser.open(f'http://127.0.0.1:{port}/'+('?tutorial=1' if tutorial else ''),new=2)


def open_data_directory(directory):
    if sys.platform=='win32':
        os.startfile(str(directory))
        return
    command=['/usr/bin/open' if sys.platform=='darwin' else 'xdg-open',str(directory)]
    subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def configure_browser_environment():
    if getattr(sys,'frozen',False) and sys.platform in {'darwin','win32'}:
        # Chromium is an opaque resource, not a Python binary dependency. This
        # preserves its upstream Mach-O layout and avoids rewriting its dylibs.
        if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or os.environ['PLAYWRIGHT_BROWSERS_PATH']=='0':
            runtime=(Path(sys.executable).parent.parent/'Resources/browser-runtime' if sys.platform=='darwin'
                     else Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent))/'browser-runtime')
            os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(runtime)


def show_startup_error(message):
    if sys.platform=='win32':
        ctypes.windll.user32.MessageBoxW(None,message,'招聘雷达',0x10)
    elif sys.platform=='darwin':
        # Pass text as an argument; no shell interpolation or credentials in UI.
        script='on run argv\ndisplay alert "招聘雷达" message (item 1 of argv) as critical\nend run'
        try:subprocess.run(['/usr/bin/osascript','-e',script,message],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=False)
        except (OSError,subprocess.TimeoutExpired):pass


def tray(port,process_id,db_path,directory):
    import pystray
    from PIL import Image
    from hirewatch.paths import RESOURCE_ROOT
    def quit_app(icon,item):
        stop_owned_service(port,process_id,instance_id(db_path));icon.stop()
    icon=pystray.Icon('HireWatch',Image.open(RESOURCE_ROOT/'assets'/'hirewatch.ico'),'招聘雷达 · 监控运行中',
                     pystray.Menu(pystray.MenuItem('打开岗位看板',lambda icon,item:open_page(port),default=True),
                                  pystray.MenuItem('新手教程',lambda icon,item:open_page(port,True)),
                                  pystray.MenuItem('打开数据目录',lambda icon,item:open_data_directory(directory)),
                                  pystray.Menu.SEPARATOR,
                                  pystray.MenuItem('退出招聘雷达（停止监控）',quit_app)))
    icon.run()


def main():
    parser=argparse.ArgumentParser(description='招聘雷达桌面版')
    parser.add_argument('--serve',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--data-dir',type=Path,help='单独的数据目录（不会写入程序目录）')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--no-tray',action='store_true',help='仅用于自动验证启动')
    parser.add_argument('--no-scheduler',action='store_true',help='仅查看已有数据，不自动访问招聘源或模型')
    parser.add_argument('--stop',action='store_true',help='停止此数据目录对应的服务')
    parser.add_argument('--self-check',action='store_true',help='检查可分发文件内的依赖，不访问网络')
    args=parser.parse_args()
    configure_browser_environment()
    if args.data_dir:
        os.environ['HIREWATCH_DATA_DIR']=str(args.data_dir.resolve())
        os.environ['HIREWATCH_DB']=str(args.data_dir.resolve()/'hirewatch.sqlite3')
    from hirewatch.paths import DATA_DIR,RESOURCE_ROOT,default_db_path
    directory=DATA_DIR;db_path=default_db_path()
    directory.mkdir(parents=True,exist_ok=True)
    # Avoid windowed-mode libraries trying to write to missing console streams.
    if sys.stdout is None:sys.stdout=open(directory/'desktop.log','a',encoding='utf-8')
    if sys.stderr is None:sys.stderr=open(directory/'desktop-error.log','a',encoding='utf-8')
    guard=None
    try:
        if args.self_check:
            import playwright.sync_api,pystray,uvicorn,fastapi,pydantic,requests
            from PIL import Image
            from hirewatch.app import create_app
            assert (RESOURCE_ROOT/'web/index.html').is_file()
            assert (RESOURCE_ROOT/'assets/hirewatch.ico').is_file()
            driver=RESOURCE_ROOT/'playwright/driver'/('node.exe' if sys.platform=='win32' else 'node')
            assert driver.is_file() or not getattr(sys,'frozen',False)
            bundled_desktop=getattr(sys,'frozen',False) and sys.platform in {'darwin','win32'}
            if bundled_desktop:
                bundled_browsers=Path(os.environ['PLAYWRIGHT_BROWSERS_PATH'])
                assert any(p.is_dir() for p in bundled_browsers.glob('chromium_headless_shell-*')), 'Bundled Chromium headless shell is missing'
                os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(bundled_browsers)
            with playwright.sync_api.sync_playwright() as automation:
                from hirewatch.collectors import browser_instance
                browser=automation.chromium.launch(headless=True) if bundled_desktop else browser_instance(automation)
                try:
                    page=browser.new_page();page.set_content('<h1>HireWatch</h1>')
                    assert page.locator('h1').inner_text()=='HireWatch'
                finally:browser.close()
            (directory/'self-check.json').write_text(json.dumps({'ok':True,'frozen':bool(getattr(sys,'frozen',False)),'web':True,'browser_driver':driver.is_file(),'browser_launch':True}),encoding='utf-8')
            return 0
        if args.serve:
            import uvicorn
            from hirewatch.app import create_app
            uvicorn.run(create_app(db_path=db_path,scheduler=not args.no_scheduler),host='127.0.0.1',port=args.port,log_level='warning',log_config=None)
            return 0
        if args.stop:
            state=read_runtime(directory)
            if not state:return 0
            stop_owned_service(int(state['port']),int(state['process_id']),instance_id(db_path))
            return 0
        guard,already_running=acquire_guard(instance_id(db_path),directory)
        if already_running:
            for _ in range(120):
                state=read_runtime(directory)
                current=health(state['port']) if state else None
                if current and current.get('instance')==instance_id(db_path):
                    if not args.no_browser:open_page(state['port'])
                    return 0
                time.sleep(.25)
            raise RuntimeError('另一个启动窗口正在初始化，请稍后再试。')
        first_use=not db_path.exists()
        port,pid,child=ensure_service(args,directory,db_path)
        if not args.no_browser:open_page(port,first_use)
        if not args.no_tray:tray(port,pid,db_path,directory)
        return 0
    except Exception as exc:
        logging.exception('Desktop startup failed')
        message=str(exc) if isinstance(exc,RuntimeError) else '启动失败，请查看数据目录中的 desktop-error.log。'
        if not args.no_browser:show_startup_error(message)
        return 1
    finally:release_guard(guard)


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
