"""Smoke-test the actual native Mac archive without source, AI or push requests.

Run this on the target Mac after build_macos.py. It uses a temporary, empty user
directory, verifies the .app signature and browser, then removes only its own data.
It intentionally does not alter quarantine or Gatekeeper settings.
"""
import argparse
import json
import os
import posixpath
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'scripts'))
from build_macos import (ARCHITECTURES, NAME, archive_name, audit_macos_distribution,
                         file_hash, native_architecture)


def validate_zip_members(archive):
    """Check paths before ditto extraction, including symlink destinations."""
    with zipfile.ZipFile(archive) as packaged:
        for member in packaged.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in member.filename:
                raise RuntimeError('Unsafe archive member: '+member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                target = packaged.read(member).decode('utf-8')
                resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
                if target.startswith('/') or resolved == '..' or resolved.startswith('../') or '\\' in target:
                    raise RuntimeError('Unsafe archive symlink: '+member.filename)


def clean_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('HIREWATCH_', 'OPENROUTER_', 'DEEPSEEK_')) or key in {'SENDKEY', 'SERVERCHAN_SENDKEY', 'WECOM_WEBHOOK'}:
            env.pop(key, None)
    env['PLAYWRIGHT_BROWSERS_PATH'] = '0'
    return env


def verify_manifest(folder):
    payload = json.loads((folder/'distribution-manifest.json').read_text(encoding='utf-8'))
    assert payload['credentials_included'] is False and payload['personal_data_included'] is False
    assert payload['signing'] == 'ad-hoc; not Apple notarized'
    for item in payload['files']:
        path = folder/item['path']
        path.resolve(strict=True).relative_to(folder.resolve())
        assert path.is_file() and file_hash(path) == item['sha256'], item['path']
        if 'symlink' in item:
            assert path.is_symlink() and os.readlink(path) == item['symlink'], item['path']
    return payload


def wait_stopped(session, base):
    for _ in range(80):
        try:
            session.get(base+'/api/health', timeout=.2)
        except requests.RequestException:
            return
        time.sleep(.1)
    raise AssertionError('The smoke test service did not stop')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify an actual native Mac HireWatch archive')
    parser.add_argument('--arch', choices=ARCHITECTURES)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        raise SystemExit('Actual Mac .app smoke verification requires macOS.')
    architecture = native_architecture()
    if args.arch and args.arch != architecture:
        raise SystemExit('Verify on the matching native Mac architecture.')
    archive = args.archive or ROOT/'release'/archive_name(architecture)
    validate_zip_members(archive)
    env = clean_environment()
    # Both drivers use the browser bundled inside the unpacked app below.
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'
    build = ROOT/'build'
    build.mkdir(exist_ok=True)
    result = {'result': 'passed', 'architecture': architecture,
              'archive': archive.name, 'archive_sha256': file_hash(archive)}
    with tempfile.TemporaryDirectory(prefix='mac-app-smoke-', dir=build) as temporary:
        root = Path(temporary)
        unpacked = root/'unpacked'
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive.resolve()), str(unpacked)], check=True)
        folder = unpacked/NAME
        app = folder/(NAME+'.app')
        executable = app/'Contents/MacOS'/NAME
        env['PLAYWRIGHT_BROWSERS_PATH']=str((app/'Contents/Resources/browser-runtime').resolve())
        os.environ['PLAYWRIGHT_BROWSERS_PATH']=env['PLAYWRIGHT_BROWSERS_PATH']
        assert executable.is_file() and os.access(executable, os.X_OK)
        assert any(path.is_symlink() for path in app.rglob('*')), 'App symlinks were not preserved'
        audit_macos_distribution(folder)
        manifest = verify_manifest(folder)
        assert manifest['architecture'] == architecture
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        binary_archs = subprocess.check_output(['/usr/bin/lipo', '-archs', str(executable)], text=True).split()
        assert binary_archs == [architecture], binary_archs
        data = root/'clean-user-data'
        session = requests.Session()
        session.trust_env = False
        command = [str(executable), '--data-dir', str(data), '--port', '8794', '--no-browser', '--no-tray', '--no-scheduler']
        stop = [str(executable), '--data-dir', str(data), '--stop', '--no-browser']
        tray_process = None
        base = None
        try:
            subprocess.run([str(executable), '--self-check', '--no-browser', '--data-dir', str(data)],
                           env=env, cwd=root, timeout=90, check=True)
            report = json.loads((data/'self-check.json').read_text(encoding='utf-8'))
            assert report['ok'] and report['frozen'] and report['browser_driver'] and report['browser_launch']
            subprocess.run(command, env=env, cwd=root, timeout=60, check=True)
            state = json.loads((data/'desktop-runtime.json').read_text(encoding='utf-8'))
            base = f"http://127.0.0.1:{state['port']}"
            health = session.get(base+'/api/health', timeout=10).json()
            assert health['app'] == 'hirewatch' and health['instance'] == state['instance']
            settings = session.get(base+'/api/settings', timeout=10).json()
            assert not settings['ai']['configured'] and not settings['ai']['has_openrouter_api_key'] and not settings['ai']['has_deepseek_api_key']
            assert not settings['notifications']['enabled'] and not settings['notifications']['has_sendkey'] and not settings['notifications']['has_webhook']
            assert session.get(base+'/api/jobs', timeout=10).json()['total'] == 0
            assert session.get(base+'/api/matches', timeout=10).json() == []
            status = session.get(base+'/api/status', timeout=10).json()
            assert not status['ai']['running'] and not status['ai']['matching'] and status['ai']['calls_today'] == 0
            assert not status['engine']['running'] and status['stats']['saved'] == 0
            with sync_playwright() as automation:
                browser = automation.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    # UI verification must remain local even if future assets add remote URLs.
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                    page.goto(base, wait_until='networkidle')
                    expect(page.locator('#tutorial-dialog')).to_be_visible()
                    for _ in range(2):
                        page.locator('#tutorial-next').click()
                    page.locator('[data-tutorial-settings="ai"]').click()
                    expect(page.locator('#openrouter-api-key')).to_have_value('')
                    expect(page.locator('#ai-budget')).to_have_value('5')
                    assert not errors, errors
                finally:
                    browser.close()
            subprocess.run(command, env=env, cwd=root, timeout=30, check=True)
            assert session.get(base+'/api/health', timeout=10).json()['process_id'] == health['process_id']
            subprocess.run(stop, env=env, cwd=root, timeout=30, check=True)
            wait_stopped(session, base)
            # Validate the real Cocoa menu loop, not only the hidden server process.
            tray_command = [argument for argument in command if argument != '--no-tray']
            tray_process = subprocess.Popen(tray_command, env=env, cwd=root)
            for _ in range(300):
                if tray_process.poll() is not None:
                    raise AssertionError('Mac menu-bar app exited unexpectedly')
                try:
                    # A just-stopped server can leave its port in TIME_WAIT.
                    # Follow the new launcher's runtime record instead of
                    # assuming that choose_port() will reuse the old port.
                    state = json.loads((data/'desktop-runtime.json').read_text(encoding='utf-8'))
                    candidate = f"http://127.0.0.1:{state['port']}"
                    current = session.get(candidate+'/api/health', timeout=.2).json()
                    if (current.get('app') == 'hirewatch' and
                            current.get('instance') == health['instance'] and
                            current.get('process_id') != health['process_id']):
                        base = candidate
                        break
                except (requests.RequestException, OSError, ValueError, KeyError):
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Mac menu-bar app did not start its service')
            time.sleep(.5)
            assert tray_process.poll() is None
            subprocess.run(tray_command, env=env, cwd=root, timeout=30, check=True)
            assert session.get(base+'/api/health', timeout=10).json()['process_id'] == current['process_id']
            result.update(unpacked_app_runs_without_python=True, app_symlinks_preserved=True,
                          strict_signature_verified=True, bundled_browser_launches=True,
                          fresh_credentials_empty=True, fresh_jobs_favorites_matches_empty=True,
                          double_launch_reuses_service=True, first_run_tutorial_visible=True,
                          native_menu_bar_loop_runs=True, no_model_calls=True,
                          no_source_or_push_requests=True, gatekeeper_settings_unchanged=True)
        except Exception:
            # This directory was created empty above and never receives keys.
            # Keep native launcher diagnostics in CI even after temp cleanup.
            for name in ('desktop-runtime.json', 'desktop-error.log', 'server-error.log'):
                log = data/name
                if log.is_file():
                    print(name+': '+log.read_text(encoding='utf-8', errors='replace')[-8000:], flush=True)
            raise
        finally:
            subprocess.run(stop, env=env, cwd=root, timeout=30, check=True)
            if tray_process is not None:
                if tray_process.poll() is None:
                    tray_process.terminate()
                tray_process.wait(timeout=15)
            if base:
                wait_stopped(session, base)
            session.close()
        # Nothing written into the unpacked program directory during startup.
        audit_macos_distribution(folder)
        verify_manifest(folder)
    output = ROOT/'release'/f'macos-smoke-{architecture}.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
