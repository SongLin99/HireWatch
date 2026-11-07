#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
failed() {
  printf '\n启动未完成，请查看上方提示和 docs/快速上手.md。\n'
  if [[ -t 0 ]]; then read -r -p '按回车关闭窗口…' _answer; fi
}
trap failed ERR
if ! command -v python3 >/dev/null 2>&1; then
  printf '请先从 https://www.python.org/downloads/ 安装 Python 3.12，再打开本文件。\n'
  exit 1
fi
python3 launcher.py "$@"
