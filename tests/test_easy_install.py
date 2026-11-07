from pathlib import Path
import importlib.util

import pytest

ROOT=Path(__file__).resolve().parent.parent


def test_installer_cannot_delete_personal_data_or_request_admin():
    text=(ROOT/'packaging/windows.iss').read_text(encoding='utf-8-sig')
    assert 'PrivilegesRequired=lowest' in text
    assert 'DefaultDirName={localappdata}\\Programs\\HireWatch' in text
    assert '[UninstallDelete]' not in text and '[InstallDelete]' not in text
    assert '{localappdata}\\HireWatch\\data' not in text
    assert 'Flags: postinstall nowait skipifsilent' in text
    assert '{userdesktop}' in text and '{userprograms}' in text


def test_windows_build_downloads_and_copies_own_browser():
    text=(ROOT/'scripts/build_windows.py').read_text(encoding='utf-8')
    assert "env['PLAYWRIGHT_BROWSERS_PATH']=str(browser_download.resolve())" in text
    assert "'--only-shell','chromium'" in text
    assert "target/'_internal/browser-runtime'" in text


def test_installer_tools_refuse_cross_platform_execution(monkeypatch):
    for name,platform in [('build_windows_installer','darwin'),('build_macos_dmg','win32')]:
        spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        monkeypatch.setattr(module.sys,'platform',platform)
        monkeypatch.setattr(module.sys,'argv',[name])
        with pytest.raises(SystemExit):module.main()
