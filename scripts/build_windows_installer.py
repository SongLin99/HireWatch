"""Wrap the audited portable app in a per-user, offline Inno Setup installer."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from build_windows import VERSION,NAME,audit_distribution


def find_compiler():
    executable=shutil.which('ISCC.exe')
    if executable:return Path(executable)
    for key in ('ProgramFiles(x86)','ProgramFiles'):
        base=Path(os.environ.get(key,'C:/Program Files'))
        for path in sorted(base.glob('Inno Setup */ISCC.exe'),reverse=True):
            if path.is_file():return path
    raise RuntimeError('Install Inno Setup before building the Windows installer.')


def main():
    if sys.platform!='win32':raise SystemExit('Build the installer on Windows.')
    application=ROOT/'dist'/NAME
    audit_distribution(application)
    assert (application/(NAME+'.exe')).is_file()
    compiler=find_compiler()
    release=ROOT/'release';release.mkdir(exist_ok=True)
    subprocess.run([str(compiler),'/DAppVersion='+VERSION,'/DSourceDir='+str(application),
                    '/DReleaseDir='+str(release),'/DProjectDir='+str(ROOT),
                    str(ROOT/'packaging/windows.iss')],check=True)
    target=release/f'HireWatch-{VERSION}-Windows-x64-Setup.exe'
    assert target.is_file()
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256.txt').write_text(digest+'  '+target.name+'\n',encoding='ascii')
    print('Offline per-user installer built: '+target.name)


if __name__=='__main__':main()
