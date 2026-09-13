from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from course_robot.database import compare_snapshot, connect, google_event_ids, replace_snapshot
from course_robot.google_calendar import (
    apply_calendar_plan,
    build_calendar_plan,
    event_body,
    google_event_id,
)
from course_robot.models import Course, TAIPEI


def make_course(
    source_id: str = "123",
    chapter: str = "2-2 根式的運算_2B",
) -> Course:
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
        preview_pdf_name="教材",
        preview_pdf_url="https://files.example.test/material.pdf",
        answer_pdf_name="解答",
        answer_pdf_url="https://files.example.test/answer.pdf",
    )


class FakeTransport:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, dict[str, Any]]] = []
        self.patched: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def insert_event(self, event_id: str, body: dict[str, Any]) -> str:
        self.inserted.append((event_id, body))
        return event_id

    def patch_event(self, event_id: str, body: dict[str, Any]) -> str:
        self.patched.append((event_id, body))
        return event_id

    def delete_event(self, event_id: str) -> None:
        self.deleted.append(event_id)


class GoogleCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection: sqlite3.Connection = connect(
            Path(self.tempdir.name) / "calendar.db"
        )
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=TAIPEI)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_event_body_contains_clickable_urls_and_private_source_id(self) -> None:
        course = make_course()
        body = event_body(course)

        self.assertEqual(body["summary"], "王惟晞｜2-2 根式的運算_2B")
        self.assertIn("教材：https://files.example.test/material.pdf", body["description"])
        self.assertIn("解答：https://files.example.test/answer.pdf", body["description"])
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(
            body["extendedProperties"]["private"]["course_robot_source_id"],
            "123",
        )
        self.assertRegex(google_event_id("123"), re.compile(r"^[0-9a-v]{5,1024}$"))

    def test_existing_snapshot_without_mappings_is_bootstrapped(self) -> None:
        course = make_course()
        replace_snapshot(self.connection, [course], observed_at=self.now)
        diff = compare_snapshot(self.connection, [course])
        plan = build_calendar_plan([course], diff, google_event_ids(self.connection))

        self.assertEqual(plan.create, (course,))
        self.assertEqual(plan.update, ())
        self.assertEqual(plan.remove, ())
        self.assertEqual(plan.unchanged, 0)

        transport = FakeTransport()
        result = apply_calendar_plan(transport, plan, {})
        self.assertEqual(len(transport.inserted), 1)
        self.assertEqual(result.event_ids["123"], google_event_id("123"))

    def test_changed_and_removed_courses_use_saved_event_ids(self) -> None:
        changed_old = make_course(source_id="123", chapter="舊章節")
        removed = make_course(source_id="456")
        replace_snapshot(
            self.connection,
            [changed_old, removed],
            observed_at=self.now,
            event_ids={"123": "saved123", "456": "saved456"},
        )
        changed_new = make_course(source_id="123", chapter="新章節")
        diff = compare_snapshot(self.connection, [changed_new])
        mappings = google_event_ids(self.connection)
        plan = build_calendar_plan([changed_new], diff, mappings)

        transport = FakeTransport()
        result = apply_calendar_plan(transport, plan, mappings)

        self.assertEqual(transport.deleted, ["saved456"])
        self.assertEqual(transport.patched[0][0], "saved123")
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.removed, 1)
        self.assertNotIn("456", result.event_ids)


if __name__ == "__main__":
    unittest.main()
