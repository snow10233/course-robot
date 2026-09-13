#!/bin/sh
set -eu

interval="${SYNC_INTERVAL_SECONDS:-900}"
case "${interval}" in
  ''|*[!0-9]*)
    echo "SYNC_INTERVAL_SECONDS 必須是正整數" >&2
    exit 64
    ;;
esac
if [ "${interval}" -lt 60 ]; then
  echo "SYNC_INTERVAL_SECONDS 不可小於 60 秒" >&2
  exit 64
fi

child_pid=""
terminate() {
  if [ -n "${child_pid}" ]; then
    kill "${child_pid}" 2>/dev/null || true
  fi
  exit 0
}
trap terminate INT TERM

echo "course-robot worker 啟動；同步間隔 ${interval} 秒"
while true; do
  echo "[$(date -Iseconds)] Calendar sync start"
  if course-robot sync --calendar; then
    echo "[$(date -Iseconds)] Calendar sync success"
  else
    exit_code=$?
    echo "[$(date -Iseconds)] Calendar sync failed；exit=${exit_code}" >&2
  fi

  sleep "${interval}" &
  child_pid=$!
  wait "${child_pid}" || true
  child_pid=""
done
