"""Assemble an explicit source allowlist for native Mac builds; never an .app."""
import hashlib
import json
import stat
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
KIT_NAME='HireWatch-1.0.1-macOS-Build-Kit'
FIXED_FILES=(
    'LICENSE','desktop.pyw','requirements.txt','requirements-dev.txt','requirements-build.txt',
    'requirements-build-macos.txt','build_macos.command','Mac构建说明.md','README.md',
    'assets/hirewatch.ico','scripts/build_windows.py','scripts/build_macos.py',
    'scripts/verify_macos_package.py','scripts/prepare_macos_kit.py','.github/workflows/build-macos.yml',
    'scripts/build_windows_installer.py','scripts/verify_windows_installer.py',
    'scripts/build_macos_dmg.py','packaging/windows.iss',
    'launcher.py','requirements-desktop.txt','scripts/audit_public_source.py',
)
SOURCE_GROUPS={'hirewatch':{'.py'},'web':{'.html','.css','.js','.svg'},'tests':{'.py'}}


def kit_files(root=ROOT):
    paths=[root/name for name in FIXED_FILES]
    for folder,suffixes in SOURCE_GROUPS.items():
        paths.extend(path for path in (root/folder).rglob('*') if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts)
    missing=[path.relative_to(root).as_posix() for path in paths if not path.is_file()]
    if missing:raise RuntimeError('Missing build kit inputs: '+', '.join(missing))
    return sorted(set(paths),key=lambda path:path.relative_to(root).as_posix())


def audit_kit_files(files,root=ROOT):
    # Stage no files: apply the same credential-pattern checks directly to the
    # chosen textual source, never to the developer's real credential files.
    import re
    pattern=re.compile(rb'sk-or-v1-[A-Za-z0-9]{24,}|SCT[0-9]{3,}[A-Za-z0-9]{12,}|qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9-]{12,}')
    forbidden_parts={'data','.venv','.build-venv','build','dist','release','__pycache__','.git'}
    for path in files:
        relative=path.relative_to(root)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or any(part.casefold() in forbidden_parts for part in relative.parts):
            raise RuntimeError('Private/runtime path in source kit: '+relative.as_posix())
        if path.name.casefold() in {'openrouter','openrouter.txt','.env'} or path.suffix.casefold() in {'.sqlite3','.db','.log','.lnk','.exe'}:
            raise RuntimeError('Private/runtime file in source kit: '+relative.as_posix())
        if path.suffix.casefold() in {'.py','.pyw','.js','.html','.md','.txt','.yml','.command'} and pattern.search(path.read_bytes()):
            raise RuntimeError('Credential pattern in source kit: '+relative.as_posix())


def source_payload(path):
    payload=path.read_bytes()
    if path.suffix in {'.py','.pyw','.js','.css','.html','.svg','.md','.txt','.yml','.command'}:
        payload=payload.replace(b'\r\n',b'\n')
        if path.suffix=='.command':payload=payload.removeprefix(b'\xef\xbb\xbf')
    return payload


def archive_write(output,name,payload,executable=False):
    info=zipfile.ZipInfo(name)
    info.create_system=3
    info.external_attr=(stat.S_IFREG|(0o755 if executable else 0o644))<<16
    info.compress_type=zipfile.ZIP_DEFLATED
    output.writestr(info,payload)


def main():
    files=kit_files();audit_kit_files(files)
    release=ROOT/'release';release.mkdir(exist_ok=True)
    archive=release/(KIT_NAME+'.zip')
    manifest={'application':'HireWatch','version':'1.0.1','kind':'macOS-source-build-kit',
              'contains_native_app':False,'native_macos_build_required':True,
              'personal_data_included':False,'credentials_included':False,
              'files':[{'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(source_payload(p)).hexdigest(),'bytes':len(source_payload(p))} for p in files]}
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for path in files:
            relative=path.relative_to(ROOT).as_posix()
            archive_write(output,KIT_NAME+'/'+relative,source_payload(path),relative.endswith('.command'))
        archive_write(output,KIT_NAME+'/build-kit-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.sha256.txt').write_text(checksum+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(files),
                      'contains_native_app':False,'private_files_included':False,'sha256':checksum},ensure_ascii=True))


if __name__=='__main__':main()
