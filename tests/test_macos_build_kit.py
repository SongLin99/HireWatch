import importlib.util
import stat
import zipfile
from pathlib import Path

import pytest


def kit_module():
    source=Path(__file__).resolve().parent.parent/'scripts/prepare_macos_kit.py'
    spec=importlib.util.spec_from_file_location('prepare_macos_kit',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_mac_kit_explicitly_excludes_developer_credentials_and_user_history(tmp_path):
    kit=kit_module()
    for name in kit.FIXED_FILES:
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('source',encoding='utf-8')
    for name in ('data/hirewatch.sqlite3','data/resume.txt','openrouter.txt','.env','private-notes.md','release/app.zip','web/__pycache__/secret.pyc'):
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('PRIVATE',encoding='utf-8')
    for name in ('hirewatch/ai.py','web/app.js','tests/test_ai.py'):
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('source',encoding='utf-8')
    selected=kit.kit_files(tmp_path);kit.audit_kit_files(selected,tmp_path)
    assert all('PRIVATE' not in file.read_text(encoding='utf-8') for file in selected)
    assert len(selected)==len(kit.FIXED_FILES)+3
    (tmp_path/'desktop.pyw').unlink()
    with pytest.raises(RuntimeError,match='Missing build kit inputs'):kit.kit_files(tmp_path)


def test_mac_command_archive_has_unix_permissions_and_line_endings(tmp_path):
    kit=kit_module();script=tmp_path/'build.command'
    script.write_bytes(b'\xef\xbb\xbf#!/bin/bash\r\necho ready\r\n')
    archive=tmp_path/'test.zip'
    with zipfile.ZipFile(archive,'w') as output:kit.archive_write(output,'build.command',kit.source_payload(script),True)
    with zipfile.ZipFile(archive) as packaged:
        info=packaged.getinfo('build.command')
        assert info.create_system==3 and stat.S_IMODE(info.external_attr>>16)==0o755
        assert packaged.read('build.command')==b'#!/bin/bash\necho ready\n'


def test_mac_kit_refuses_runtime_files_and_literal_credential(tmp_path):
    kit=kit_module();file=tmp_path/'ai.py'
    file.write_text('key="sk-or-v1-'+('a'*32)+'"')
    with pytest.raises(RuntimeError,match='Credential pattern'):kit.audit_kit_files([file],tmp_path)
    forbidden=tmp_path/'openrouter.txt';forbidden.write_text('not-a-real-key')
    with pytest.raises(RuntimeError,match='Private/runtime'):kit.audit_kit_files([forbidden],tmp_path)
