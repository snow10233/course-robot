from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from course_robot.google_calendar import (
    MANAGED_PROPERTY,
    GoogleCalendarError,
    GoogleCalendarTransport,
    apply_plan,
    build_event_body,
    build_plan,
    verify_rebuild,
    write_token,
)
from course_robot.models import TAIPEI, Course, parse_datetime
from course_robot.timing import plan_display


NOW = datetime(2026, 9, 16, 0, 0, tzinfo=TAIPEI)


def make_course(
    source_id: str = "558798",
    *,
    chapter: str = "乘法_1B",
    start_at: str = "2026-09-18T14:00:00+08:00",
    end_at: str = "2026-09-18T14:30:00+08:00",
) -> Course:
    return Course(
        source_id=source_id,
        room_id="R2026091800298",
        start_at=start_at,
        end_at=end_at,
        class_type="1v1",
        class_url="",
        student_name="郭尚儒",
        student_id="18639",
        subject="數學",
        grade="小四上",
        chapter=chapter,
        version_name="南版AB*",
        tag="",
        preview_pdf_name="乘法_1B(學習教材)",
        preview_pdf_url="https://files.example.test/m.pdf",
        answer_pdf_name="乘法_1B(學習教材解答)",
        answer_pdf_url="https://files.example.test/a.pdf",
    )


def make_event(course: Course, *, event_id: str | None = None) -> dict[str, Any]:
    body = build_event_body([course], plan_display([course])[course.source_id])
    return {
        "id": event_id or f"random-{course.source_id}",
        "status": "confirmed",
        "summary": body["summary"],
        "start": body["start"],
        "end": body["end"],
        "extendedProperties": body["extendedProperties"],
    }


class FakeTransport:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = {event["id"]: event for event in (events or [])}
        self.inserted: list[tuple[str, dict[str, Any]]] = []
        self.patched: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.listed_time_min = ""

    def list_events(self, time_min: str) -> list[dict[str, Any]]:
        self.listed_time_min = time_min
        boundary = parse_datetime(time_min)
        result = []
        for event in self.events.values():
            start = (event.get("start") or {}).get("dateTime")
            if start and parse_datetime(start) < boundary:
                continue
            result.append(event)
        return result

    def insert_event(self, body: dict[str, Any]) -> dict[str, Any]:
        event_id = f"random{len(self.inserted) + 1}"
        self.inserted.append((event_id, body))
        event = {"id": event_id, "status": "confirmed", **body}
        self.events[event_id] = event
        return event

    def patch_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.patched.append((event_id, body))
        event = {"id": event_id, **body}
        self.events[event_id] = event
        return event

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return self.events.get(event_id)

    def delete_event(self, event_id: str) -> None:
        self.deleted.append(event_id)
        self.events.pop(event_id, None)


class EventBodyTests(unittest.TestCase):
    def test_body_has_only_grade_materials_and_answers(self) -> None:
        c = make_course()
        body = build_event_body([c], plan_display([c])[c.source_id])
        self.assertEqual(body["summary"], "郭尚儒｜乘法_1B")
        kinds = [line.split("：")[0] for line in body["description"].splitlines()]
        self.assertEqual(kinds, ["年級", "教材", "解答"])
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(body["start"]["timeZone"], "Asia/Taipei")
        private = body["extendedProperties"]["private"]
        self.assertEqual(private[MANAGED_PROPERTY], "true")

    def test_missing_end_time_is_rejected(self) -> None:
        """長度不可臆測：沒有結束時間就不建立事件。"""
        with self.assertRaisesRegex(ValueError, "結束時間"):
            build_event_body([make_course(end_at="")], None)

    def test_missing_chapter_does_not_produce_dash(self) -> None:
        c = make_course(chapter="")
        body = build_event_body([c], plan_display([c])[c.source_id])
        self.assertEqual(body["summary"], "郭尚儒")


class RebuildPlanTests(unittest.TestCase):
    """每日重建：刪掉今天之後的，再重新放一次。沒有比對、沒有 ID、沒有雜湊。"""

    def test_plan_deletes_today_onwards_and_recreates(self) -> None:
        course = make_course()
        plan = build_plan([course], [make_event(course)], now=NOW)
        self.assertEqual(len(plan.remove), 1)
        self.assertEqual(len(plan.create), 1)
        self.assertNotIn("id", plan.create[0].body)

    def test_past_events_are_never_removed(self) -> None:
        past = make_course(
            source_id="559999",
            start_at="2026-09-15T14:00:00+08:00",
            end_at="2026-09-15T14:30:00+08:00",
        )
        plan = build_plan([], [make_event(past)], now=NOW)
        self.assertEqual(plan.remove, ())

    def test_unmanaged_events_are_never_removed(self) -> None:
        foreign = {
            "id": "manual-event",
            "summary": "私人行程",
            "start": {"dateTime": "2026-09-20T10:00:00+08:00"},
            "end": {"dateTime": "2026-09-20T11:00:00+08:00"},
        }
        plan = build_plan([make_course()], [foreign], now=NOW)
        self.assertEqual(plan.remove, ())

    def test_empty_source_suppresses_deletion(self) -> None:
        """來源一筆都沒有時不刪除，避免把日曆清空。"""
        plan = build_plan([], [make_event(make_course())], now=NOW)
        self.assertTrue(plan.deletions_suppressed)
        self.assertEqual(plan.remove, ())

    def test_consecutive_lessons_produce_one_event(self) -> None:
        first = make_course(
            source_id="10", start_at="2026-09-18T20:00:00+08:00",
            end_at="2026-09-18T20:30:00+08:00", chapter="乘法_1B",
        )
        second = make_course(
            source_id="11", start_at="2026-09-18T20:30:00+08:00",
            end_at="2026-09-18T21:00:00+08:00", chapter="乘法_2B",
        )
        plan = build_plan([first, second], [], now=NOW)
        self.assertEqual(len(plan.create), 1)
        spec = plan.create[0]
        self.assertEqual(spec.body["end"]["dateTime"], "2026-09-18T20:50:00+08:00")
        self.assertEqual(spec.body["summary"], "郭尚儒｜乘法_1B")
        self.assertEqual(spec.source_ids, ("10", "11"))

    def test_event_body_has_no_identity_fields(self) -> None:
        """說明與 extendedProperties 不需要辨識用的欄位。"""
        plan = build_plan([make_course()], [], now=NOW)
        body = plan.create[0].body
        self.assertNotIn("支點課程 ID", body["description"])
        self.assertEqual(
            body["extendedProperties"]["private"], {MANAGED_PROPERTY: "true"}
        )

    def test_course_without_end_time_is_skipped(self) -> None:
        plan = build_plan([make_course(end_at="")], [], now=NOW)
        self.assertEqual(plan.create, ())
        self.assertEqual(len(plan.skipped), 1)


class RebuildApplyTests(unittest.TestCase):
    def test_apply_removes_then_creates(self) -> None:
        course = make_course()
        transport = FakeTransport([make_event(course)])
        plan = build_plan([course], list(transport.events.values()), now=NOW)
        result = apply_plan(transport, plan)
        self.assertEqual(result.removed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(len(transport.deleted), 1)

    def test_single_failure_does_not_stop_the_rest(self) -> None:
        class FlakyTransport(FakeTransport):
            def insert_event(self, body):
                if "乘法_1B" in body.get("summary", ""):
                    raise GoogleCalendarError("模擬失敗")
                return super().insert_event(body)

        courses = [
            make_course(source_id="558798"),
            make_course(
                source_id="560002",
                start_at="2026-09-22T14:00:00+08:00",
                end_at="2026-09-22T14:30:00+08:00",
                chapter="別的章節",
            ),
        ]
        transport = FlakyTransport()
        result = apply_plan(transport, build_plan(courses, [], now=NOW))
        self.assertEqual(result.created, 1)
        self.assertEqual(len(result.errors), 1)

    def test_verify_passes_when_counts_match(self) -> None:
        course = make_course()
        transport = FakeTransport()
        plan = build_plan([course], [], now=NOW)
        apply_plan(transport, plan)
        ok, reason = verify_rebuild(transport, plan)
        self.assertTrue(ok, reason)

    def test_verify_detects_missing_event(self) -> None:
        plan = build_plan([make_course()], [], now=NOW)
        ok, reason = verify_rebuild(FakeTransport(), plan)
        self.assertFalse(ok)
        self.assertIn("不足", reason)

    def test_verify_detects_leftover_event(self) -> None:
        course = make_course()
        transport = FakeTransport()
        plan = build_plan([course], [], now=NOW)
        apply_plan(transport, plan)
        stale = make_event(
            make_course(
                source_id="999999",
                start_at="2026-09-25T10:00:00+08:00",
                end_at="2026-09-25T10:30:00+08:00",
            )
        )
        transport.events[stale["id"]] = stale
        ok, reason = verify_rebuild(transport, plan)
        self.assertFalse(ok)
        self.assertIn("過多", reason)

    def test_verify_ignores_unmanaged_events(self) -> None:
        """手動建立的事件不該讓審查失敗。"""
        course = make_course()
        transport = FakeTransport()
        plan = build_plan([course], [], now=NOW)
        apply_plan(transport, plan)
        transport.events["manual"] = {
            "id": "manual",
            "summary": "私人行程",
            "start": {"dateTime": "2026-09-25T10:00:00+08:00"},
        }
        ok, reason = verify_rebuild(transport, plan)
        self.assertTrue(ok, reason)


class TokenWriteTests(unittest.TestCase):
    def test_token_is_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "data" / "token.json"
            write_token(token, '{"token": "value"}')
            self.assertEqual(token.read_text(encoding="utf-8"), '{"token": "value"}')
            leftovers = [p for p in token.parent.iterdir() if p.name != token.name]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
