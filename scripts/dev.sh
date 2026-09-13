#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_root}"
export PYTHONUTF8=1

if [[ ! -x .venv/bin/python || ! -x .venv/bin/course-robot ]]; then
  printf '找不到 WSL 虛擬環境，請先執行 bash scripts/setup-wsl.sh\n' >&2
  exit 1
fi

case "${1:---help}" in
  test)
    shift
    exec .venv/bin/python -m unittest discover -s tests -v "$@"
    ;;
  serve)
    shift
    exec .venv/bin/python -m http.server --bind 127.0.0.1 --directory output "$@"
    ;;
  *)
    exec .venv/bin/course-robot "$@"
    ;;
esac
