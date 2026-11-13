"""Exercise the actual unpacked EXE using fresh data, without model/source calls."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from build_windows import audit_distribution


def main():
    archive=ROOT/'release/HireWatch-1.0.2-Windows-x64.zip'
    env=os.environ.copy()
    for key in ('OPENROUTER_API_KEY','DEEPSEEK_API_KEY','HIREWATCH_DB','HIREWATCH_DATA_DIR','HIREWATCH_DETAIL_CACHE'):
        env.pop(key,None)
    with tempfile.TemporaryDirectory(prefix='exe-smoke-',dir=ROOT/'build') as temporary:
        root=Path(temporary);appdir=root/'unpacked';appdir.mkdir()
        with zipfile.ZipFile(archive) as packaged:packaged.extractall(appdir)
        folder=appdir/'招聘雷达';exe=folder/'招聘雷达.exe';data=root/'clean-user-data'
        env['PLAYWRIGHT_BROWSERS_PATH']=str(folder/'_internal/browser-runtime')
        os.environ['PLAYWRIGHT_BROWSERS_PATH']=env['PLAYWRIGHT_BROWSERS_PATH']
        audit_distribution(folder)
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        result=subprocess.run([str(exe),'--self-check','--no-browser','--data-dir',str(data)],env=env,cwd=root,timeout=60,creationflags=flags)
        report=json.loads((data/'self-check.json').read_text(encoding='utf-8'))
        assert result.returncode==0 and report['frozen'] and report['browser_launch']
        command=[str(exe),'--data-dir',str(data),'--port','8794','--no-browser','--no-tray','--no-scheduler']
        launcher=subprocess.run(command,env=env,cwd=root,timeout=60,creationflags=flags)
        assert launcher.returncode==0
        state=json.loads((data/'desktop-runtime.json').read_text(encoding='utf-8'))
        base=f"http://127.0.0.1:{state['port']}";session=requests.Session();session.trust_env=False
        try:
            health=session.get(base+'/api/health',timeout=10).json()
            assert health['app']=='hirewatch' and health['instance']==state['instance']
            settings=session.get(base+'/api/settings',timeout=10).json()
            assert not settings['ai']['configured'] and not settings['ai']['has_openrouter_api_key'] and not settings['ai']['has_deepseek_api_key']
            assert not settings['notifications']['enabled'] and not settings['notifications']['has_sendkey'] and not settings['notifications']['has_webhook']
            assert session.get(base+'/api/jobs',timeout=10).json()['total']==0
            assert session.get(base+'/api/matches',timeout=10).json()==[]
            status=session.get(base+'/api/status',timeout=10).json()
            assert not status['ai']['running'] and not status['ai']['matching'] and status['ai']['calls_today']==0
            assert not status['engine']['running'] and status['stats']['saved']==0
            assert 'tutorial-dialog' in session.get(base+'/',timeout=10).text
            with sync_playwright() as automation:
                browser=automation.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(base,wait_until='networkidle')
                    expect(page.locator('#tutorial-dialog')).to_be_visible()
                    out=ROOT/'build/verification/packaged-first-run.png';out.parent.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(out),full_page=False)
                    for _ in range(2):page.locator('#tutorial-next').click()
                    page.locator('[data-tutorial-settings="ai"]').click()
                    expect(page.locator('#openrouter-api-key')).to_have_value('')
                    expect(page.locator('#ai-budget')).to_have_value('5')
                    assert not errors,errors
                finally:browser.close()
            # A second launch must re-use this database's server, not spawn another.
            assert subprocess.run(command,env=env,cwd=root,timeout=30,creationflags=flags).returncode==0
            assert session.get(base+'/api/health',timeout=10).json()['process_id']==health['process_id']
        finally:
            subprocess.run([str(exe),'--data-dir',str(data),'--stop'],env=env,cwd=root,timeout=30,creationflags=flags,check=True)
            for _ in range(50):
                try:session.get(base+'/api/health',timeout=.2)
                except requests.RequestException:break
                time.sleep(.1)
            else:raise AssertionError('Owned EXE background service did not stop')
        # A test run writes only into its explicitly chosen data directory.
        audit_distribution(folder)
        # Exercise the real packaged notification-area loop without opening UI
        # windows or polling external sources. Terminate only our Popen handle.
        tray_command=[str(exe),'--data-dir',str(data),'--port','8794','--no-browser','--no-scheduler']
        tray_process=subprocess.Popen(tray_command,env=env,cwd=root,creationflags=flags)
        try:
            for _ in range(300):
                if tray_process.poll() is not None:raise AssertionError('Tray process exited unexpectedly')
                try:
                    state=json.loads((data/'desktop-runtime.json').read_text(encoding='utf-8'))
                    candidate=f"http://127.0.0.1:{state['port']}"
                    current=session.get(candidate+'/api/health',timeout=.2).json()
                    if (current.get('app')=='hirewatch' and current.get('instance')==health['instance']
                            and current.get('process_id')!=health['process_id']):
                        base=candidate
                        break
                except (requests.RequestException,OSError,ValueError,KeyError):pass
                time.sleep(.1)
            else:raise AssertionError('Tray launcher did not start service')
            time.sleep(.5);assert tray_process.poll() is None
            assert subprocess.run(tray_command,env=env,cwd=root,timeout=30,creationflags=flags).returncode==0
            assert session.get(base+'/api/health',timeout=5).json()['process_id']==current['process_id']
        finally:
            subprocess.run([str(exe),'--data-dir',str(data),'--stop'],env=env,cwd=root,timeout=30,creationflags=flags,check=True)
            if tray_process.poll() is None:tray_process.terminate()
            tray_process.wait(timeout=10)
            time.sleep(.3)
        report={'result':'passed','archive':archive.name,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                          'unpacked_exe_runs_without_python':True,'bundled_browser_driver_runs':True,
                          'fresh_credentials_empty':True,'fresh_jobs_favorites_matches_empty':True,
                          'double_launch_reuses_service':True,'owned_service_stops':True,'first_run_tutorial_visible':True,
                          'native_tray_loop_runs':True,'no_model_calls':True}
        (ROOT/'release/windows-smoke-x64.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))


if __name__=='__main__':main()
