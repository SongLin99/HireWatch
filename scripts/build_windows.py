"""Reproducible allowlist-only Windows EXE build and shareable archive.

Run using .build-venv/Scripts/python.exe after installing requirements-build.txt.
No project root directory, database, logs or credential files are ever collected.
"""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
VERSION='1.0.2'
NAME='招聘雷达'
FORBIDDEN_NAMES={'openrouter','openrouter.txt','.env','hirewatch.sqlite3','moka_details.sqlite3','server.pid','desktop-runtime.json'}


def audit_distribution(directory):
    files=[p for p in directory.rglob('*') if p.is_file()]
    forbidden=[p.relative_to(directory).as_posix() for p in files if p.name.casefold() in FORBIDDEN_NAMES or p.suffix.casefold() in {'.sqlite3','.db','.log','.lnk'} or p.relative_to(directory).parts[0]=='data']
    if forbidden:raise RuntimeError('Distribution contains private/runtime files: '+', '.join(forbidden))
    # Secrets are generated only at runtime. Scan textual artifacts for literal
    # bearer keys; never load or print the developer's actual credential files.
    import re
    pattern=re.compile(rb'sk-or-v1-[a-zA-Z0-9]{24,}|SCT[0-9]{3,}[a-zA-Z0-9]{12,}|qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[a-zA-Z0-9-]{12,}')
    for path in files:
        if path.suffix.casefold() in {'.json','.txt','.md','.html','.js','.css','.toml','.ini','.py'} and pattern.search(path.read_bytes()):
            raise RuntimeError('Distribution contains a credential pattern in '+path.name)
    return files


def main():
    if sys.platform!='win32':raise SystemExit('Build this Windows distribution on Windows.')
    build=ROOT/'build/windows';dist=ROOT/'dist';build.mkdir(parents=True,exist_ok=True)
    version_file=build/'version-info.txt'
    version_file.write_text("""VSVersionInfo(ffi=FixedFileInfo(filevers=(1,0,2,0),prodvers=(1,0,2,0),mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('080404b0',[StringStruct('CompanyName','HireWatch'),StringStruct('FileDescription','招聘雷达 · 官方实习机会'),StringStruct('FileVersion','1.0.2'),StringStruct('InternalName','HireWatch'),StringStruct('OriginalFilename','招聘雷达.exe'),StringStruct('ProductName','招聘雷达 HireWatch'),StringStruct('ProductVersion','1.0.2')])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])""",encoding='utf-8')
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--noupx',
          '--name',NAME,'--distpath',str(dist),'--workpath',str(build/'work'),'--specpath',str(build),
          '--icon',str(ROOT/'assets/hirewatch.ico'),'--add-data',str(ROOT/'web')+';web',
          '--version-file',str(version_file),
          '--add-data',str(ROOT/'assets/hirewatch.ico')+';assets',
          '--collect-all','playwright','--collect-submodules','uvicorn','--collect-submodules','pystray',
          '--exclude-module','tkinter','--exclude-module','pytest','--exclude-module','httpx',str(ROOT/'desktop.pyw')]
    env=os.environ.copy()
    browser_download=build/'browser-download'
    env['PLAYWRIGHT_BROWSERS_PATH']=str(browser_download.resolve())
    subprocess.run([sys.executable,'-m','playwright','install','--only-shell','chromium'],cwd=ROOT,env=env,check=True)
    subprocess.run(args,cwd=ROOT,env=env,check=True)
    target=dist/NAME
    runtime=target/'_internal/browser-runtime'
    for package in browser_download.iterdir():
        if package.is_dir() and package.name.startswith(('chromium_headless_shell-','ffmpeg-')):
            shutil.copytree(package,runtime/package.name,dirs_exist_ok=True)
    if not any(runtime.glob('chromium_headless_shell-*')):
        raise RuntimeError('Bundled Windows Chromium is missing')
    text='''招聘雷达 Windows 免安装版 1.0.2

使用方法
1. 将整个“招聘雷达”文件夹解压到电脑中的任意位置。
2. 双击“招聘雷达.exe”，程序会在默认浏览器打开本机看板，并出现在右下角托盘。
3. 首次启动会显示新手教程；以后可从看板右上角或托盘“新手教程”再次打开。
4. 到“订阅与推送”的 AI 设置选择 OpenRouter 或 DeepSeek，填写自己的 API Key 和模型。
5. 如需微信推送，填写自己的 Server酱 SendKey 或企业微信机器人地址，再启用推送。
6. 右键托盘图标 → “退出招聘雷达（停止监控）”可以退出后台。

不需要安装 Python、Node.js 或采集浏览器，运行环境与 Chromium 采集浏览器均已内置。
浏览器看板地址只监听本机。关闭浏览器不会停止监控；退出托盘程序或关机会停止。
第一次采集多个官网可能需要几分钟；无需等待 AI 解析完成就可以搜索、收藏和查看官网。
AI 解析/匹配需要你自己的模型服务账户，按服务商计费，预算可在设置中调整；不填 Key 可先使用采集、筛选和收藏。

个人数据
本分享包不包含任何 API Key、推送凭据、岗位数据库、收藏、匹配记录或开发者日志。
你的数据默认保存在 %LOCALAPPDATA%\\HireWatch\\data，与程序文件分开。
API Key 和推送凭据保存在本机配置数据库中，普通页面响应只显示是否已设置。不要分享自己的数据目录。
升级时可替换程序文件夹，原来的个人数据会继续保留。
分享给别人时，请分享原始 ZIP；不要把个人数据目录、密钥或日志添加进去。

程序为 Windows x64 便携版。整个程序文件夹需要一同保留，不要只复制 EXE。
'''
    (target/'使用说明.txt').write_text(text,encoding='utf-8-sig')
    shutil.copyfile(ROOT/'LICENSE',target/'LICENSE.txt')
    # Dependency licenses are included for recipients; use package metadata only.
    licenses=target/'licenses';licenses.mkdir(exist_ok=True)
    packages=['fastapi','starlette','uvicorn','requests','playwright','pydantic','pystray','Pillow','pyinstaller']
    versions={}
    for name in packages:
        package=importlib.metadata.distribution(name);versions[name]=package.version
        for relative in package.files or []:
            if relative.name.casefold().startswith(('license','copying','notice')):
                original=Path(package.locate_file(relative))
                if original.is_file():shutil.copyfile(original,licenses/(name+'-'+relative.name))
    audit_distribution(target)
    manifest={'application':'HireWatch','version':VERSION,'platform':'Windows x64','dependencies':versions,
              'credentials_included':False,'personal_data_included':False,
              'files':[{'path':p.relative_to(target).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(target.rglob('*')) if p.is_file()]}
    (target/'distribution-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    release=ROOT/'release';release.mkdir(exist_ok=True)
    archive=release/f'HireWatch-{VERSION}-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for path in audit_distribution(target):output.write(path,(Path(NAME)/path.relative_to(target)).as_posix())
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.sha256.txt').write_text(checksum+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'exe':str(target/(NAME+'.exe')),'archive':str(archive),'bytes':archive.stat().st_size,'sha256':checksum,'private_files_included':False},ensure_ascii=True))


if __name__=='__main__':main()
