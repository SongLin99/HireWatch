"""Create a drag-to-Applications disk image from the verified native app."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from build_macos import VERSION,NAME,ARCHITECTURES,native_architecture,file_hash,copy_licenses


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--arch',choices=ARCHITECTURES)
    args=parser.parse_args()
    if sys.platform!='darwin':raise SystemExit('Build and verify a DMG on macOS.')
    architecture=native_architecture()
    assert not args.arch or args.arch==architecture
    label='AppleSilicon' if architecture=='arm64' else 'Intel'
    app=ROOT/'dist/macos'/architecture/(NAME+'.app')
    assert app.is_dir()
    output=ROOT/'release'/f'HireWatch-{VERSION}-macOS-{label}.dmg'
    with tempfile.TemporaryDirectory(prefix='dmg-',dir=ROOT/'build') as temporary:
        root=Path(temporary);staging=root/'content';staging.mkdir()
        shutil.copytree(app,staging/app.name,symlinks=True)
        (staging/'Applications').symlink_to('/Applications',target_is_directory=True)
        (staging/'使用说明.txt').write_text('将“招聘雷达.app”拖到 Applications 文件夹，再从“应用程序”打开。\n无需安装 Python 或采集浏览器，不填 Key 也可使用。\n首次使用有教程；AI 与微信提醒均可跳过。\n本应用尚未完成 Apple 开发者公证，首次打开请按系统提示确认来源。\n',encoding='utf-8')
        shutil.copyfile(ROOT/'LICENSE',staging/'LICENSE.txt')
        copy_licenses(staging)
        subprocess.run(['/usr/bin/hdiutil','create','-ov','-format','UDZO','-fs','HFS+',
                        '-volname',NAME,'-srcfolder',str(staging),str(output)],check=True)
        mount=root/'mounted';mount.mkdir()
        subprocess.run(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-mountpoint',str(mount),str(output)],check=True)
        try:
            assert (mount/'Applications').is_symlink() and os.readlink(mount/'Applications')=='/Applications'
            mounted=mount/app.name
            subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(mounted)],check=True)
            # Simulate the user's drag-copy before launching, keeping the actual
            # /Applications directory and existing user data untouched.
            installed=root/'Applications'/app.name
            installed.parent.mkdir()
            subprocess.run(['/usr/bin/ditto',str(mounted),str(installed)],check=True)
        finally:
            subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True)
        env=os.environ.copy()
        for key in list(env):
            if key.startswith(('HIREWATCH_','OPENROUTER_','DEEPSEEK_')) or key=='PLAYWRIGHT_BROWSERS_PATH':env.pop(key,None)
        subprocess.run([str(installed/'Contents/MacOS'/NAME),'--self-check','--no-browser','--data-dir',str(root/'empty-data')],
                       env=env,cwd=root,timeout=90,check=True)
        assert json.loads((root/'empty-data/self-check.json').read_text())['browser_launch']
    digest=file_hash(output)
    output.with_suffix('.dmg.sha256.txt').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    report={'result':'passed','dmg':output.name,'sha256':digest,'architecture':architecture,
            'drag_copy_runs_with_bundled_browser':True,'applications_link_verified':True,
            'strict_signature_verified':True,'no_model_calls':True,'signing':'ad-hoc; not notarized'}
    (ROOT/'release'/f'macos-dmg-smoke-{architecture}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
