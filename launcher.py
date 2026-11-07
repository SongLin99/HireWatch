"""First-run setup and cross-platform desktop entry, without embedded secrets."""
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def environment_python(root=ROOT, platform=None):
    platform = sys.platform if platform is None else platform
    return root/'.venv'/('Scripts/python.exe' if platform == 'win32' else 'bin/python')


def dependency_fingerprint(root=ROOT):
    return hashlib.sha256(b'\0'.join((root/name).read_bytes()
                         for name in ('requirements.txt', 'requirements-desktop.txt'))).hexdigest()


def prepare(root=ROOT):
    python = environment_python(root)
    if not python.is_file():
        print('首次使用：正在创建本地运行环境……', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(root/'.venv')], check=True)
    marker = root/'.venv/hirewatch-setup.sha256'
    expected = dependency_fingerprint(root)
    if not marker.is_file() or marker.read_text(encoding='ascii').strip() != expected:
        print('正在安装依赖与采集浏览器；首次需要联网，不会调用 AI 或发送微信。', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root/'requirements-desktop.txt')], cwd=root, check=True)
        subprocess.run([str(python), '-m', 'playwright', 'install', '--only-shell', 'chromium'], cwd=root, check=True)
        marker.write_text(expected+'\n', encoding='ascii')
    return python


def main(argv=None):
    if sys.version_info < (3, 10):
        raise SystemExit('需要 Python 3.10 或更新版本，推荐 Python 3.12。')
    arguments = list(sys.argv[1:] if argv is None else argv)
    setup_only = '--setup-only' in arguments
    if setup_only:
        arguments.remove('--setup-only')
    try:
        python = prepare()
        if setup_only:
            print('环境已准备好。再次打开启动入口即可使用。', flush=True)
            return 0
        print('正在打开招聘雷达。AI 与微信提醒均可跳过。', flush=True)
        return subprocess.run([str(python), str(ROOT/'desktop.pyw'), *arguments], cwd=ROOT).returncode
    except subprocess.CalledProcessError as exc:
        print('启动未完成。请检查网络和上方错误，参阅 docs/快速上手.md。', file=sys.stderr)
        return exc.returncode or 1


if __name__ == '__main__':
    raise SystemExit(main())
