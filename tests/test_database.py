from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from course_robot.database import (
    active_courses,
    apply_snapshot,
    compare_snapshot,
    connect,
    replace_snapshot,
)
from course_robot.models import Course, TAIPEI


def make_course(chapter: str = "2-2 根式", source_id: str = "123") -> Course:
    return Course(
        source_id=source_id,
        room_id="ROOM-9",
        start_at="2026-09-26T17:00:00+08:00",
        end_at="2026-09-26T17:50:00+08:00",
        class_type="1v1",
        class_url="https://example.test/class/123",
        student_name="王惟晞",
        student_id="7",
        subject="數學",
        grade="國二上",
        chapter=chapter,
        version_name="版本 A",
        tag="",
    )


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "test.db"
        self.connection = connect(self.path)
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=TAIPEI)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_each_successful_sync_replaces_the_previous_snapshot(self) -> None:
        first = apply_snapshot(
            self.connection,
            [make_course(), make_course(source_id="456")],
            observed_at=self.now,
        )
        self.assertEqual(first.fetched, 2)

        second = apply_snapshot(
            self.connection,
            [make_course(chapter="2-3 最新章節")],
            observed_at=self.now,
        )
        self.assertEqual(second.fetched, 1)
        self.assertEqual(second.added, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(second.removed, 1)
        self.assertEqual(second.unchanged, 0)
        current = active_courses(self.connection, now=self.now)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["chapter"], "2-3 最新章節")

        third = apply_snapshot(
            self.connection,
            [make_course(chapter="2-3 最新章節")],
            observed_at=self.now,
        )
        self.assertEqual(third.unchanged, 1)
        self.assertEqual(third.added + third.updated + third.removed, 0)

        names = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertNotIn("course_changes", names)
        self.assertNotIn("sync_runs", names)

    def test_diff_keeps_google_event_mapping_for_future_delta_updates(self) -> None:
        course = make_course()
        replace_snapshot(
            self.connection,
            [course],
            observed_at=self.now,
            event_ids={course.source_id: "google-event-123"},
        )
        changed = make_course(chapter="新章節")
        diff = compare_snapshot(self.connection, [changed])
        self.assertEqual(len(diff.updated), 1)
        self.assertEqual(diff.updated[0].google_event_id, "google-event-123")

        replace_snapshot(self.connection, [changed], observed_at=self.now)
        event_id = self.connection.execute(
            "SELECT google_event_id FROM courses WHERE source_id = '123'"
        ).fetchone()[0]
        self.assertEqual(event_id, "google-event-123")

    def test_empty_snapshot_is_rejected_unless_explicitly_allowed(self) -> None:
        apply_snapshot(self.connection, [make_course()], observed_at=self.now)
        with self.assertRaisesRegex(ValueError, "拒絕"):
            apply_snapshot(self.connection, [], observed_at=self.now)
        self.assertEqual(len(active_courses(self.connection, now=self.now)), 1)

        result = apply_snapshot(
            self.connection,
            [],
            observed_at=self.now,
            allow_empty=True,
        )
        self.assertEqual(result.fetched, 0)
        self.assertEqual(active_courses(self.connection, now=self.now), [])

    def test_migrates_legacy_database_without_history(self) -> None:
        self.connection.close()
        self.path.unlink()
        legacy = sqlite3.connect(self.path)
        legacy.executescript(
            """
            CREATE TABLE courses (
                source_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL, start_at TEXT NOT NULL,
                status TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                removed_at TEXT
            );
            CREATE TABLE sync_runs (id INTEGER PRIMARY KEY);
            CREATE TABLE course_changes (id INTEGER PRIMARY KEY);
            """
        )
        payload = json.dumps(make_course().payload(), ensure_ascii=False)
        legacy.execute(
            "INSERT INTO courses VALUES (?, ?, ?, ?, 'active', ?, ?, ?, NULL)",
            ("123", payload, "hash", make_course().start_at, "t", "t", "t"),
        )
        legacy.commit()
        legacy.close()

        self.connection = connect(self.path)
        names = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 1)
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(courses)")
        }
        self.assertIn("google_event_id", columns)
        self.assertNotIn("course_changes", names)
        self.assertNotIn("sync_runs", names)


if __name__ == "__main__":
    unittest.main()
