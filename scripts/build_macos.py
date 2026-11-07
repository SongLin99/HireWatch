"""Native macOS .app build with bundled Chromium and an allowlisted distribution.

This script intentionally refuses cross-compilation. It performs ad-hoc signing
for local testing only; it does not submit files to Apple or bypass Gatekeeper.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT/'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT/'scripts'))
from build_windows import audit_distribution

VERSION = '1.0.1'
NAME = '招聘雷达'
ARCHITECTURES = ('arm64', 'x86_64')
MINIMUM_MACOS = '14.0'


def native_architecture(machine=None):
    value = (machine or platform.machine()).lower()
    aliases = {'aarch64': 'arm64', 'amd64': 'x86_64'}
    value = aliases.get(value, value)
    if value not in ARCHITECTURES:
        raise ValueError('Unsupported Mac architecture: '+value)
    return value


def archive_name(architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError('Invalid Mac architecture')
    label = 'AppleSilicon' if architecture == 'arm64' else 'Intel'
    return f'HireWatch-{VERSION}-macOS-{label}.zip'


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def audit_macos_distribution(directory):
    directory = Path(directory).resolve()
    for path in directory.rglob('*'):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(directory)
            except (ValueError, OSError, RuntimeError) as exc:
                raise RuntimeError('Distribution contains an unsafe symlink: '+path.relative_to(directory).as_posix()) from exc
        relative = path.relative_to(directory)
        if any(part.casefold() in {'data', '.env', '.git', '.venv', '.mac-build-venv'} for part in relative.parts):
            raise RuntimeError('Distribution contains private/runtime files: '+relative.as_posix())
    return audit_distribution(directory)


def pyinstaller_arguments(architecture, build, dist, icon):
    if architecture not in ARCHITECTURES:
        raise ValueError('Invalid Mac architecture')
    return [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
            '--windowed', '--noupx', '--name', NAME, '--target-arch', architecture,
            # Leaving identity unset selects PyInstaller's normal ad-hoc mode.
            # Explicit '-' would incorrectly turn on hardened-runtime validation.
            '--osx-bundle-identifier', 'com.hirewatch.desktop',
            '--distpath', str(dist), '--workpath', str(build/'work'), '--specpath', str(build),
            '--icon', str(icon), '--add-data', str(ROOT/'web')+':web',
            '--add-data', str(ROOT/'assets/hirewatch.ico')+':assets',
            '--add-data', str(icon)+':assets', '--collect-all', 'playwright',
            '--collect-submodules', 'uvicorn', '--collect-submodules', 'pystray',
            '--hidden-import', 'pystray._darwin', '--hidden-import', 'AppKit',
            '--hidden-import', 'Foundation', '--hidden-import', 'Quartz',
            '--exclude-module', 'tkinter', '--exclude-module', 'pytest',
            '--exclude-module', 'httpx', str(ROOT/'desktop.pyw')]


def make_icon(target):
    from PIL import Image
    with Image.open(ROOT/'assets/hirewatch.ico') as original:
        image = original.convert('RGBA').resize((1024, 1024), Image.Resampling.LANCZOS)
        image.save(target, format='ICNS')


def copy_browser_runtime(source,target):
    """Keep Chromium's original Mach-O load commands and signatures intact.

    Chromium libraries have no spare Mach-O header space for PyInstaller's
    install_name_tool rewrites. Copy their self-contained upstream layout only
    after the Python app is assembled, before signing the outer app bundle.
    """
    source=Path(source);target=Path(target)
    packages=[path for path in source.iterdir() if path.is_dir() and path.name.startswith(('chromium_headless_shell-','ffmpeg-'))]
    if not any(path.name.startswith('chromium_headless_shell-') for path in packages):
        raise RuntimeError('The pinned Chromium headless runtime was not downloaded')
    target.mkdir(parents=True,exist_ok=True)
    for package in packages:shutil.copytree(package,target/package.name,symlinks=True)


def copy_licenses(target):
    licenses = target/'licenses'
    licenses.mkdir()
    versions = {}
    names = {'fastapi', 'starlette', 'uvicorn', 'requests', 'playwright', 'pydantic',
             'pystray', 'pillow', 'pyinstaller', 'macholib'}
    names.update(package.metadata['Name'] for package in importlib.metadata.distributions()
                 if package.metadata.get('Name', '').lower().startswith('pyobjc-'))
    for name in sorted(names):
        package = importlib.metadata.distribution(name)
        versions[name] = package.version
        for relative in package.files or []:
            if relative.name.casefold().startswith(('license', 'copying', 'notice')):
                original = Path(package.locate_file(relative))
                if original.is_file():
                    shutil.copyfile(original, licenses/(name+'-'+relative.name))
    return versions


def write_manifest(target, architecture, versions):
    # Keep this outside the signed .app: a later metadata write must not invalidate it.
    files = audit_macos_distribution(target)
    payload = {'application': 'HireWatch', 'version': VERSION, 'platform': 'macOS',
               'architecture': architecture, 'minimum_macos': MINIMUM_MACOS,
               'signing': 'ad-hoc; not Apple notarized', 'bundled_browser': 'Chromium headless shell',
               'credentials_included': False, 'personal_data_included': False, 'dependencies': versions,
               'files': [{'path': path.relative_to(target).as_posix(), 'bytes': path.stat().st_size,
                          'sha256': file_hash(path),
                          **({'symlink': os.readlink(path)} if path.is_symlink() else {})}
                         for path in sorted(files)]}
    (target/'distribution-manifest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


def readme(architecture):
    chip = 'Apple 芯片（M1 / M2 / M3 / M4 等）' if architecture == 'arm64' else 'Intel 芯片'
    return f'''招聘雷达 macOS {VERSION} · {chip}

适用系统：macOS 14 或更新版本；本包仅支持以上芯片类型。
将“招聘雷达.app”拖到“应用程序”，双击打开。无需安装 Python、Chrome 或 Edge。
采集用的 Chromium 无界面浏览器已内置；看板使用系统默认浏览器，也支持 Safari。
首次启动会显示新手教程，可在看板“使用教程”入口或菜单栏重新打开。
菜单栏图标可以打开看板、教程、数据目录或退出监控。关闭浏览器不会停止监控。

分享包没有 API Key、微信推送凭据、岗位数据、收藏、匹配历史或开发者日志。
在“订阅与推送”的设置中填写自己的 OpenRouter / DeepSeek 密钥和自选模型。
如需微信推送，填写自己的 Server酱 SendKey 或企业微信机器人地址，再启用。
不填密钥可先采集、筛选和收藏；使用 AI 解析和匹配才会按服务商规则产生费用。
个人数据保存在 ~/Library/Application Support/HireWatch/data，与程序文件分开。
升级可替换 App；分享请使用原始 ZIP，勿加入自己的数据目录。

本包仅作 ad-hoc 本机签名，尚未使用 Apple Developer ID 签名或公证。
macOS 可能阻止从网上收到的 App；请按系统“隐私与安全性”的提示核验并决定是否允许打开。
不要关闭 Gatekeeper，也不要批量移除系统隔离标记。公开分发前应由开发者完成 Developer ID 签名、公证和真实 Mac 验证。
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build a native macOS HireWatch app (no remote uploads)')
    parser.add_argument('--arch', choices=ARCHITECTURES)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        raise SystemExit('Mac builds must run on macOS. Windows cannot cross-compile this app.')
    architecture = native_architecture()
    if args.arch and args.arch != architecture:
        raise SystemExit('Use a native '+args.arch+' Mac/Python to build that architecture; cross-architecture builds are disabled.')
    if int(platform.mac_ver()[0].split('.')[0]) < 14:
        raise SystemExit('macOS 14 or newer is required by the bundled browser.')
    build = ROOT/'build/macos'/architecture
    dist = ROOT/'dist/macos'/architecture
    build.mkdir(parents=True, exist_ok=True)
    icon = build/'hirewatch.icns'
    make_icon(icon)
    env = os.environ.copy()
    browser_download=build/'browser-download'
    env['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_download.resolve())
    # A dedicated build cache is copied as opaque Resources after PyInstaller;
    # its browser binaries must not be rewritten as Python dependency libraries.
    import playwright
    old_hermetic=Path(playwright.__file__).parent/'driver/package/.local-browsers'
    if old_hermetic.exists():
        raise SystemExit('Use a clean build virtualenv: old hermetic Playwright browsers would be collected by PyInstaller.')
    subprocess.run([sys.executable, '-m', 'playwright', 'install', '--only-shell', 'chromium'], cwd=ROOT, env=env, check=True)
    subprocess.run(pyinstaller_arguments(architecture, build, dist, icon), cwd=ROOT, env=env, check=True)
    app = dist/(NAME+'.app')
    copy_browser_runtime(browser_download,app/'Contents/Resources/browser-runtime')
    info_path = app/'Contents/Info.plist'
    with info_path.open('rb') as source:
        info = plistlib.load(source)
    info.update(CFBundleDisplayName=NAME, CFBundleName=NAME, CFBundleShortVersionString=VERSION,
                CFBundleVersion=VERSION, LSMinimumSystemVersion=MINIMUM_MACOS, LSUIElement=True,
                NSHighResolutionCapable=True)
    with info_path.open('wb') as output:
        plistlib.dump(info, output)
    # The nested executable signatures were created by PyInstaller. Re-sign the
    # outer bundle after updating its metadata, then demand a strict validation.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    staging = Path(tempfile.mkdtemp(prefix='package-', dir=build))
    target = staging/NAME
    target.mkdir()
    try:
        shutil.copytree(app, target/app.name, symlinks=True)
        (target/'使用说明.txt').write_text(readme(architecture), encoding='utf-8')
        shutil.copyfile(ROOT/'LICENSE',target/'LICENSE.txt')
        versions = copy_licenses(target)
        write_manifest(target, architecture, versions)
        release = ROOT/'release'
        release.mkdir(exist_ok=True)
        archive = release/archive_name(architecture)
        # Apple's ditto preserves application symlinks, permissions and bundle metadata.
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(target), str(archive)], check=True)
        checksum = file_hash(archive)
        archive.with_suffix('.sha256.txt').write_text(checksum+'  '+archive.name+'\n', encoding='ascii')
        print(json.dumps({'app': str(app), 'archive': str(archive), 'architecture': architecture,
                          'bytes': archive.stat().st_size, 'sha256': checksum,
                          'signing': 'ad-hoc; not notarized', 'private_files_included': False}, ensure_ascii=True))
    finally:
        shutil.rmtree(staging)


if __name__ == '__main__':
    main()
