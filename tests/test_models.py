from __future__ import annotations

import unittest

from course_robot.models import Course


class CourseModelTests(unittest.TestCase):
    def test_normalizes_daily_schedule_record(self) -> None:
        course = Course.from_api(
            {
                "class_id": 123,
                "class_room_no": "ROOM-9",
                "class_time": "2026-09-26 17:00:00",
                "end_time": "2026-09-26 17:50:00",
                "class_type": "1v1",
                "class_url": "https://example.test/class/123",
                "grade": "國二上",
                "subject": "數學",
                "knowledge_point_title": "2-2 根式的運算_2B",
                "version_name": "版本 A",
                "students": [{"student_id": 7, "student_name": "王惟晞"}],
                "knowledge_files": [
                    {
                        "name": "國二上 2-2 根式的運算_2B（學習教材）",
                        "url": "https://files.example.test/material.pdf",
                        "class_type": "review",
                        "main": "Y",
                    },
                    {
                        "name": "國二上 2-2 根式的運算_2B（學習教材解答）",
                        "url": "https://files.example.test/answer.pdf",
                        "class_type": "material_ans",
                        "main": "Y",
                    },
                ],
            }
        )
        self.assertEqual(course.source_id, "123")
        self.assertEqual(course.student_name, "王惟晞")
        self.assertEqual(course.start_at, "2026-09-26T17:00:00+08:00")
        self.assertEqual(course.chapter, "2-2 根式的運算_2B")
        self.assertEqual(course.preview_pdf_url, "https://files.example.test/material.pdf")
        self.assertEqual(course.answer_pdf_url, "https://files.example.test/answer.pdf")

    def test_missing_preview_file_is_allowed(self) -> None:
        course = Course.from_api(
            {
                "class_id": 456,
                "class_time": "2026-09-27 17:00:00",
                "students": [],
                "knowledge_files": [],
            }
        )
        self.assertEqual(course.preview_pdf_url, "")
        self.assertEqual(course.answer_pdf_url, "")


if __name__ == "__main__":
    unittest.main()
