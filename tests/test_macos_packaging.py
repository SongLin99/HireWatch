import stat
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'scripts'))
import build_macos
from verify_macos_package import clean_environment, validate_zip_members, verify_manifest


def test_architectures_are_explicit_and_separate():
    assert build_macos.native_architecture('aarch64') == 'arm64'
    assert build_macos.native_architecture('x86_64') == 'x86_64'
    assert build_macos.archive_name('arm64').endswith('macOS-AppleSilicon.zip')
    assert build_macos.archive_name('x86_64').endswith('macOS-Intel.zip')
    with pytest.raises(ValueError):
        build_macos.archive_name('../../not-an-architecture')
    with pytest.raises(ValueError):
        build_macos.native_architecture('i386')


def test_browser_is_copied_as_opaque_resources_without_host_cache_metadata(tmp_path):
    source=tmp_path/'cache';source.mkdir()
    browser=source/'chromium_headless_shell-1243';browser.mkdir()
    binary=browser/'chrome-headless-shell';binary.write_bytes(b'upstream Mach-O bytes')
    metadata=source/'.links';metadata.mkdir();(metadata/'private-build-path').write_text('/private/build')
    target=tmp_path/'app/Contents/Resources/browser-runtime'
    build_macos.copy_browser_runtime(source,target)
    assert (target/browser.name/binary.name).read_bytes()==binary.read_bytes()
    assert not (target/'.links').exists()


def test_pyinstaller_collects_only_public_resources_and_driver(tmp_path):
    args = build_macos.pyinstaller_arguments('arm64', tmp_path/'build', tmp_path/'dist', tmp_path/'icon.icns')
    resources = [args[index+1] for index, value in enumerate(args) if value == '--add-data']
    assert len(resources) == 3
    assert resources[0] == str(ROOT/'web')+':web'
    assert all('data:' not in value and 'openrouter' not in value for value in resources)
    assert str(ROOT)+':' not in resources
    assert args[args.index('--collect-all')+1] == 'playwright'
    assert '--codesign-identity' not in args
    assert '--windowed' in args and 'pystray._darwin' in args
    assert args[-1] == str(ROOT/'desktop.pyw')


def test_macos_audit_rejects_nested_private_state(tmp_path):
    folder = tmp_path/'招聘雷达.app/Contents/Resources/data'
    folder.mkdir(parents=True)
    (folder/'settings.json').write_text('{}')
    with pytest.raises(RuntimeError, match='private/runtime'):
        build_macos.audit_macos_distribution(tmp_path)


def test_manifest_stays_outside_signed_app_and_detects_modified_files(tmp_path):
    app = tmp_path/'招聘雷达.app'
    app.mkdir()
    (app/'program').write_bytes(b'public program')
    payload = build_macos.write_manifest(tmp_path, 'arm64', {'playwright': 'test'})
    assert not (app/'distribution-manifest.json').exists()
    assert payload['credentials_included'] is False and payload['personal_data_included'] is False
    assert verify_manifest(tmp_path)['architecture'] == 'arm64'
    (app/'program').write_bytes(b'modified')
    with pytest.raises(AssertionError):
        verify_manifest(tmp_path)


def test_archive_preflight_accepts_internal_symlinks_rejects_escape(tmp_path):
    archive = tmp_path/'safe.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('招聘雷达/App/Contents/Frameworks/shared', b'code')
        link = zipfile.ZipInfo('招聘雷达/App/Contents/Resources/shared')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, '../Frameworks/shared')
    validate_zip_members(archive)
    for name in ('../outside', '/absolute', 'nested/../outside'):
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr(name, 'bad')
        with pytest.raises(RuntimeError, match='Unsafe archive member'):
            validate_zip_members(archive)
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(link, '../../../../../outside')
    with pytest.raises(RuntimeError, match='Unsafe archive symlink'):
        validate_zip_members(archive)


def test_smoke_environment_has_no_host_credentials_or_private_paths(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'synthetic-unit-key')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-unit-key')
    monkeypatch.setenv('HIREWATCH_DB', 'private-database')
    monkeypatch.setenv('SERVERCHAN_SENDKEY', 'synthetic-unit-key')
    env = clean_environment()
    assert not any(key.startswith(('OPENROUTER_', 'DEEPSEEK_', 'HIREWATCH_')) for key in env)
    assert 'SERVERCHAN_SENDKEY' not in env
    assert env['PLAYWRIGHT_BROWSERS_PATH'] == '0'


def test_windows_host_cannot_claim_native_app_build(monkeypatch):
    monkeypatch.setattr(build_macos.sys, 'platform', 'win32')
    with pytest.raises(SystemExit, match='cannot cross-compile'):
        build_macos.main([])


def test_macos_workflow_is_manual_native_and_does_not_publish():
    text = (ROOT/'.github/workflows/build-macos.yml').read_text(encoding='utf-8')
    assert 'workflow_dispatch:' in text and 'contents: read' in text
    assert 'macos-15-intel' in text and 'runner: macos-15\n' in text
    assert 'verify_macos_package.py' in text and 'upload-artifact@' in text
    assert 'push:' not in text and 'contents: write' not in text and 'gh release' not in text
    command = (ROOT/'build_macos.command').read_text(encoding='utf-8')
    assert 'scripts/verify_macos_package.py' in command and 'scripts/build_macos.py' in command
    assert 'xattr -' not in command and 'spctl --master-disable' not in command
