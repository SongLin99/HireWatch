"""Check publishable files without ever printing matched secret values."""
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {'.git', '.venv', '.build-venv', '.mac-build-venv', '__pycache__', '.pytest_cache', 'build', 'dist', 'release'}
FORBIDDEN = {'data', 'openrouter', 'openrouter.txt', '.env', 'desktop-runtime.json'}
PATTERNS = [
    rb'sk-or-v1-[A-Za-z0-9_-]{24,}',
    rb'(?<![A-Za-z0-9])sk-[a-fA-F0-9]{32,}',
    rb'(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{30,})',
    rb'SCT[0-9]{3,}[A-Za-z0-9]{12,}',
    rb'webhook/send\?key=[A-Za-z0-9-]{12,}',
    rb'AKIA[0-9A-Z]{16}',
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    rb'(?i)[A-Z]:[/\\]Users[/\\](?!Public\b|Default\b)[^/\\\s]+[/\\]',
    rb'(?i)[A-Z]:[/\\]Code[/\\][^/\\\s]+',
]


def public_files(root=ROOT):
    if (root/'.git').exists() and shutil.which('git'):
        output = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root)
        return [root/name.decode('utf-8') for name in output.split(b'\0') if name]
    return [path for path in root.rglob('*') if path.is_file() and
            not any(part in EXCLUDED for part in path.relative_to(root).parts)]


def audit_paths(files, root=ROOT):
    failures = []
    for path in files:
        relative = path.relative_to(root)
        label = relative.as_posix()
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or
                any(part.casefold() in FORBIDDEN for part in relative.parts) or
                any(part in EXCLUDED for part in relative.parts) or
                path.suffix.casefold() in {'.db', '.sqlite', '.sqlite3', '.log', '.lnk', '.pem', '.key', '.pfx', '.p12'} or
                path.name.startswith('.env') and path.name != '.env.example'):
            failures.append(label+': private/runtime path')
            continue
        if path.suffix.casefold() not in {'.png', '.jpg', '.jpeg', '.ico'}:
            payload = path.read_bytes()
            if any(re.search(pattern, payload) for pattern in PATTERNS):
                failures.append(label+': credential/private-path pattern')
    if failures:
        raise ValueError('\n'.join(failures))
    return len(files)


if __name__ == '__main__':
    try:
        count = audit_paths(public_files())
    except ValueError as exc:
        raise SystemExit('Public source audit failed (values redacted):\n'+str(exc))
    print(f'Public source audit passed: {count} files; no detected credentials or private state.')
