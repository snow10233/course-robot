#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
course_robot="${project_root}/.venv/bin/course-robot"
log_directory="${project_root}/logs"
log_file="${log_directory}/course-robot.log"

cd "${project_root}"
export PYTHONUTF8=1

if [[ ! -x "${course_robot}" ]]; then
  printf '找不到 %s，請先執行 uv sync --frozen\n' "${course_robot}" >&2
  exit 1
fi

mkdir -p "${log_directory}"
started_at="$(date '+%Y-%m-%d %H:%M:%S')"
printf '[%s] sync start\n' "${started_at}" >"${log_file}"

set +e
"${course_robot}" sync "$@" >>"${log_file}" 2>&1
exit_code=$?
set -e

finished_at="$(date '+%Y-%m-%d %H:%M:%S')"
printf '[%s] sync exit=%s\n' "${finished_at}" "${exit_code}" >>"${log_file}"
exit "${exit_code}"
