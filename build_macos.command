#!/bin/bash
# Run locally on a Mac. This helper never uploads the project or installs Homebrew.
set -euo pipefail
cd -- "$(dirname -- "$0")"

failed() {
  printf '\n构建未完成。请保留上方错误信息，查看 Mac构建说明.md 后重试。\n'
  if [[ -t 0 ]]; then read -r -p '按回车关闭窗口…' _answer; fi
}
trap failed ERR

if [[ "$(uname -s)" != Darwin ]]; then
  printf '请在 Mac 上运行此文件；Windows 不能生成可运行的 macOS App。\n'
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf '请先从 python.org 安装适合这台 Mac 的 Python 3.12，再重新打开本文件。\n'
  exit 1
fi
python3 -c 'import platform,sys; assert sys.version_info >= (3,10), "需要 Python 3.10 或更新版本，建议 3.12"; assert int(platform.mac_ver()[0].split(".")[0]) >= 14, "需要 macOS 14 或更新版本"'
if ! /usr/bin/xcrun --find clang >/dev/null 2>&1; then
  printf '请先安装 Apple Command Line Tools（终端运行 xcode-select --install），安装后再重新打开本文件。\n'
  exit 1
fi
printf '正在为这台 Mac 构建招聘雷达。首次需要联网下载构建依赖和内置采集浏览器；不会调用付费模型或发送推送。\n'
python3 -m venv .mac-build-venv
.mac-build-venv/bin/python -m pip install --upgrade pip
.mac-build-venv/bin/python -m pip install -r requirements-build-macos.txt
.mac-build-venv/bin/python -m pytest -q
.mac-build-venv/bin/python scripts/build_macos.py
.mac-build-venv/bin/python scripts/verify_macos_package.py
printf '\n构建及本机检查完成。请分享 release 中与本机芯片对应的 macOS ZIP，不要分享个人数据目录。\n'
/usr/bin/open release
if [[ -t 0 ]]; then read -r -p '按回车关闭窗口…' _answer; fi
