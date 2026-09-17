from __future__ import annotations

import unittest

from course_robot.google_calendar import build_event_body
from course_robot.models import Course, parse_datetime
from course_robot.timing import group_lessons, lesson_minutes_for, plan_display


def course(
    source_id: str,
    start: str,
    end: str,
    *,
    student: str = "郭尚儒",
    grade: str = "小四上",
) -> Course:
    return Course(
        source_id=source_id,
        room_id="R1",
        start_at=start,
        end_at=end,
        class_type="1v1",
        class_url="",
        student_name=student,
        student_id="1",
        subject="數學",
        grade=grade,
        chapter="乘法_1B",
        version_name="南版",
        tag="",
    )


class LessonLengthTests(unittest.TestCase):
    def test_single_event_body_has_only_grade_materials_answers(self) -> None:
        c = course("7", "2026-09-18T14:00:00+08:00", "2026-09-18T14:30:00+08:00")
        body = build_event_body([c], plan_display([c])["7"])
        lines = body["description"].splitlines()
        self.assertEqual(lines[0], "年級：小四上")
        self.assertEqual([l.split("：")[0] for l in lines], ["年級"])  # 此範例沒有教材網址
        self.assertNotIn("課時", body["description"])
        self.assertNotIn("教室：", body["description"])
        self.assertNotIn("支點課程 ID", body["description"])

    def test_30_minute_slot_becomes_25(self) -> None:
        c = course("1", "2026-09-18T14:00:00+08:00", "2026-09-18T14:30:00+08:00")
        timing = plan_display([c])["1"]
        self.assertEqual(timing.start_at, "2026-09-18T14:00:00+08:00")
        self.assertEqual(timing.end_at, "2026-09-18T14:25:00+08:00")
        self.assertEqual(lesson_minutes_for(30), 25)

    def test_60_minute_slot_becomes_50(self) -> None:
        """60 分鐘的時段是 50 分鐘授課，不是 25 分鐘。"""
        c = course("2", "2026-09-19T16:00:00+08:00", "2026-09-19T17:00:00+08:00")
        timing = plan_display([c])["2"]
        self.assertEqual(timing.start_at, "2026-09-19T16:00:00+08:00")
        self.assertEqual(timing.end_at, "2026-09-19T16:50:00+08:00")
        self.assertEqual(lesson_minutes_for(60), 50)

    def test_60_minute_slot_makes_a_50_minute_event(self) -> None:
        c = course("3", "2026-09-19T16:00:00+08:00", "2026-09-19T17:00:00+08:00")
        body = build_event_body([c], plan_display([c])["3"])
        self.assertEqual(body["start"]["dateTime"], "2026-09-19T16:00:00+08:00")
        self.assertEqual(body["end"]["dateTime"], "2026-09-19T16:50:00+08:00")

    def test_single_slot_body_ends_25_minutes_later(self) -> None:
        c = course("9", "2026-09-18T14:00:00+08:00", "2026-09-18T14:30:00+08:00")
        body = build_event_body([c], plan_display([c])["9"])
        self.assertEqual(body["end"]["dateTime"], "2026-09-18T14:25:00+08:00")


class ConsecutiveLessonTests(unittest.TestCase):
    """國小連續兩節：合併成「一筆」50 分鐘的事件。"""

    def setUp(self) -> None:
        self.first = course("10", "2026-09-18T20:00:00+08:00", "2026-09-18T20:30:00+08:00")
        self.second = course("11", "2026-09-18T20:30:00+08:00", "2026-09-18T21:00:00+08:00")

    def test_pair_merges_into_one_50_minute_event(self) -> None:
        timings = plan_display([self.first, self.second])
        # 只回傳群組第一堂的 timing，另一堂已被合併
        self.assertIn("10", timings)
        self.assertNotIn("11", timings)
        self.assertEqual(timings["10"].start_at, "2026-09-18T20:00:00+08:00")
        self.assertEqual(timings["10"].end_at, "2026-09-18T20:50:00+08:00")

    def test_group_lessons_pairs_them(self) -> None:
        groups = group_lessons([self.first, self.second])
        self.assertEqual(len(groups), 1)
        self.assertEqual([c.source_id for c in groups[0]], ["10", "11"])

    def test_merged_event_keeps_first_title_and_both_materials(self) -> None:
        timed = []
        for c in (self.first, self.second):
            timed.append(
                Course(**{**c.payload(), "preview_pdf_url": f"https://x/{c.source_id}m.pdf",
                          "answer_pdf_url": f"https://x/{c.source_id}a.pdf"})
            )
        groups = group_lessons(timed)
        timing = plan_display(timed)["10"]
        body = build_event_body(groups[0], timing)
        self.assertEqual(body["summary"], "郭尚儒｜乘法_1B")  # 第一堂的章節
        self.assertEqual(body["description"].count("教材："), 2)
        self.assertEqual(body["description"].count("解答："), 2)
        # 年級只出現一次
        self.assertEqual(body["description"].count("年級："), 1)
        self.assertEqual(body["extendedProperties"]["private"]["course_robot_managed"], "true")

    def test_three_consecutive_lessons_merge_into_one(self) -> None:
        third = course("12", "2026-09-18T21:00:00+08:00", "2026-09-18T21:30:00+08:00")
        timings = plan_display([self.first, self.second, third])
        self.assertEqual(len(timings), 1)
        self.assertEqual(timings["10"].end_at, "2026-09-18T21:15:00+08:00")

    def test_different_students_are_not_merged(self) -> None:
        other = course(
            "31", "2026-09-18T20:30:00+08:00", "2026-09-18T21:00:00+08:00", student="其他人"
        )
        timings = plan_display([self.first, other])
        self.assertIn("10", timings)
        self.assertIn("31", timings)

    def test_non_adjacent_lessons_are_not_merged(self) -> None:
        later = course("41", "2026-09-18T21:00:00+08:00", "2026-09-18T21:30:00+08:00")
        timings = plan_display([self.first, later])
        self.assertIn("10", timings)
        self.assertIn("41", timings)

    def test_different_days_are_not_merged(self) -> None:
        next_day = course("51", "2026-09-19T20:30:00+08:00", "2026-09-19T21:00:00+08:00")
        timings = plan_display([self.first, next_day])
        self.assertIn("10", timings)
        self.assertIn("51", timings)

    def test_merged_60_minute_pair(self) -> None:
        a = course("61", "2026-09-18T16:00:00+08:00", "2026-09-18T17:00:00+08:00")
        b = course("62", "2026-09-18T17:00:00+08:00", "2026-09-18T18:00:00+08:00")
        # 60 分時段各自 50 分，但時間相接時視為連續 → 合併成 100 分鐘
        timings = plan_display([a, b])
        self.assertEqual(len(timings), 1)
        self.assertEqual(timings["61"].start_at, "2026-09-18T16:00:00+08:00")
        self.assertEqual(timings["61"].end_at, "2026-09-18T17:40:00+08:00")


if __name__ == "__main__":
    unittest.main()
