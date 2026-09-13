from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from course_robot.config import Settings
from course_robot.models import TAIPEI, parse_datetime


DEFAULT_MAX_AGE_SECONDS = 3600


def database_health(
    path: Path,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if max_age_seconds <= 0:
        return False, "HEALTHCHECK_MAX_AGE_SECONDS 必須大於零"
    if not path.is_file():
        return False, f"找不到 SQLite：{path}"

    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute(
                "SELECT last_success_at, fetched_count FROM sync_state WHERE id = 1"
            ).fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return False, f"SQLite 無法讀取：{exc}"

    if row is None:
        return False, "SQLite 尚無成功同步紀錄"
    try:
        last_success = parse_datetime(row[0])
    except ValueError as exc:
        return False, f"同步時間無法解析：{exc}"

    current = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    age_seconds = max((current - last_success).total_seconds(), 0)
    if age_seconds > max_age_seconds:
        return (
            False,
            f"最後成功同步已過 {int(age_seconds)} 秒；上限 {max_age_seconds} 秒",
        )
    return True, f"最後同步距今 {int(age_seconds)} 秒；課程 {row[1]} 筆"


def main() -> int:
    settings = Settings.load(Path.cwd())
    try:
        max_age = int(
            os.getenv("HEALTHCHECK_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS))
        )
    except ValueError:
        print("HEALTHCHECK_MAX_AGE_SECONDS 必須是整數")
        return 1

    healthy, message = database_health(settings.database, max_age)
    print(message)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
