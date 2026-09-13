#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_root}"

if ! command -v uv >/dev/null 2>&1; then
  printf '找不到 Linux 版 uv；請先安裝 uv，再重新執行此腳本。\n' >&2
  exit 1
fi

# Windows virtual environments cannot be reused on Linux. Preserve the old one.
if [[ -d .venv/Scripts ]]; then
  backup=".venv.windows.$(date +%Y%m%d%H%M%S).$$"
  mv -- .venv "${backup}"
  printf '原 Windows 虛擬環境已保留在 %s\n' "${backup}"
fi

uv sync --frozen
if [[ ! -e .env ]]; then
  (umask 077; cp .env.example .env)
fi
mkdir -p data output logs
chmod +x scripts/*.sh
printf 'WSL 開發環境已就緒。請確認 .env 設定，執行 ./scripts/dev.sh test 驗證。\n'
