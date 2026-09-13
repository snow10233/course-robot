from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping

from course_robot.models import (
    Course,
    CourseDelta,
    SnapshotDiff,
    SyncResult,
    TAIPEI,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    source_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    start_at TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    google_event_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS courses_start_idx ON courses(start_at);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_success_at TEXT NOT NULL,
    fetched_count INTEGER NOT NULL
);

DROP TABLE IF EXISTS course_changes;
DROP TABLE IF EXISTS sync_runs;
"""


def _migrate_legacy_database(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(courses)").fetchall()
    }
    if "status" not in columns:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            DROP TABLE IF EXISTS course_changes;
            DROP TABLE IF EXISTS sync_runs;
            DROP TABLE IF EXISTS courses_latest;
            CREATE TABLE courses_latest (
                source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                start_at TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                google_event_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO courses_latest(
                source_id, payload_json, content_hash, start_at, synced_at, google_event_id
            )
            SELECT source_id, payload_json, content_hash, start_at, last_seen_at, ''
            FROM courses
            WHERE status = 'active';
            DROP TABLE courses;
            ALTER TABLE courses_latest RENAME TO courses;
            COMMIT;
            """
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _migrate_legacy_database(connection)
    connection.executescript(SCHEMA)
    return connection


def _json(course: Course) -> str:
    return json.dumps(course.payload(), ensure_ascii=False, sort_keys=True)


def _stored_course(row: sqlite3.Row) -> Course:
    return Course(**json.loads(row["payload_json"]))


def compare_snapshot(
    connection: sqlite3.Connection,
    courses: list[Course],
    *,
    allow_empty: bool = False,
) -> SnapshotDiff:
    incoming = {course.source_id: course for course in courses}
    if len(incoming) != len(courses):
        raise ValueError("本次 API 快照含有重複 class_id")

    existing = {
        row["source_id"]: row
        for row in connection.execute("SELECT * FROM courses").fetchall()
    }
    if not courses and existing and not allow_empty:
        raise ValueError("API 回傳零筆，但目前仍有課程；已拒絕用空快照覆蓋現有課表")

    added: list[CourseDelta] = []
    updated: list[CourseDelta] = []
    removed: list[CourseDelta] = []
    unchanged = 0

    for source_id, course in incoming.items():
        old_row = existing.get(source_id)
        if old_row is None:
            added.append(CourseDelta(source_id=source_id, old=None, new=course))
            continue
        if old_row["content_hash"] != course.content_hash():
            updated.append(
                CourseDelta(
                    source_id=source_id,
                    old=_stored_course(old_row),
                    new=course,
                    google_event_id=old_row["google_event_id"],
                )
            )
        else:
            unchanged += 1

    for source_id, old_row in existing.items():
        if source_id not in incoming:
            removed.append(
                CourseDelta(
                    source_id=source_id,
                    old=_stored_course(old_row),
                    new=None,
                    google_event_id=old_row["google_event_id"],
                )
            )

    return SnapshotDiff(
        added=tuple(added),
        updated=tuple(updated),
        removed=tuple(removed),
        unchanged=unchanged,
    )


def replace_snapshot(
    connection: sqlite3.Connection,
    courses: list[Course],
    *,
    observed_at: datetime | None = None,
    event_ids: Mapping[str, str] | None = None,
) -> None:
    now = (observed_at or datetime.now(TAIPEI)).astimezone(TAIPEI)
    now_text = now.isoformat(timespec="seconds")
    existing_event_ids = {
        row["source_id"]: row["google_event_id"]
        for row in connection.execute(
            "SELECT source_id, google_event_id FROM courses"
        ).fetchall()
    }
    resolved_event_ids = {**existing_event_ids, **(event_ids or {})}

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM courses")
        connection.executemany(
            """
            INSERT INTO courses(
                source_id, payload_json, content_hash, start_at, synced_at, google_event_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    course.source_id,
                    _json(course),
                    course.content_hash(),
                    course.start_at,
                    now_text,
                    resolved_event_ids.get(course.source_id, ""),
                )
                for course in courses
            ],
        )
        connection.execute(
            """
            INSERT INTO sync_state(id, last_success_at, fetched_count)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_success_at = excluded.last_success_at,
                fetched_count = excluded.fetched_count
            """,
            (now_text, len(courses)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_snapshot(
    connection: sqlite3.Connection,
    courses: list[Course],
    *,
    observed_at: datetime | None = None,
    allow_empty: bool = False,
) -> SyncResult:
    diff = compare_snapshot(connection, courses, allow_empty=allow_empty)
    replace_snapshot(connection, courses, observed_at=observed_at)
    return SyncResult(
        fetched=len(courses),
        added=len(diff.added),
        updated=len(diff.updated),
        removed=len(diff.removed),
        unchanged=diff.unchanged,
    )


def active_courses(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    days: int = 35,
) -> list[dict[str, str]]:
    start = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    end = start + timedelta(days=days)
    rows = connection.execute(
        """
        SELECT payload_json FROM courses
        WHERE start_at >= ? AND start_at < ?
        ORDER BY start_at
        """,
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def current_state(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()


def google_event_ids(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row["source_id"]: row["google_event_id"]
        for row in connection.execute(
            "SELECT source_id, google_event_id FROM courses"
        ).fetchall()
        if row["google_event_id"]
    }
