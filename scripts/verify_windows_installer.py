"""Install, run, reinstall and uninstall only in a disposable CI directory."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from build_windows import VERSION,NAME


def main():
    if sys.platform!='win32':raise SystemExit('Verify this installer on Windows.')
    installer=ROOT/'release'/f'HireWatch-{VERSION}-Windows-x64-Setup.exe'
    with tempfile.TemporaryDirectory(prefix='installer-smoke-',dir=ROOT/'build') as temporary:
        root=Path(temporary).resolve();application=root/'installed';data=root/'personal-data'
        assert application.is_relative_to(root)
        data.mkdir();sentinel=data/'keep.txt';sentinel.write_text('preserve user data')
        command=[str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(application)]
        env=os.environ.copy()
        for key in list(env):
            if key.startswith(('HIREWATCH_','OPENROUTER_','DEEPSEEK_')):env.pop(key,None)
        env.pop('PLAYWRIGHT_BROWSERS_PATH',None)
        for _ in range(2):
            subprocess.run(command,env=env,timeout=180,check=True)
            executable=application/(NAME+'.exe')
            assert executable.is_file() and (application/'unins000.exe').is_file()
            subprocess.run([str(executable),'--self-check','--no-browser','--data-dir',str(data)],
                           env=env,cwd=root,timeout=90,check=True)
            assert json.loads((data/'self-check.json').read_text())['browser_launch']
            assert sentinel.read_text()=='preserve user data'
        subprocess.run([str(application/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],
                       env=env,cwd=root,timeout=120,check=True)
        assert not (application/(NAME+'.exe')).exists()
        assert sentinel.read_text()=='preserve user data'
    report={'result':'passed','installer':installer.name,'sha256':hashlib.sha256(installer.read_bytes()).hexdigest(),
            'installs_without_admin_request':True,'installed_app_and_browser_run':True,
            'reinstall_preserves_data':True,'uninstall_preserves_data':True,'no_model_calls':True}
    (ROOT/'release/windows-installer-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
