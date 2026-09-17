from __future__ import annotations

import unittest

from course_robot.models import PUBLIC_COURSE_FIELDS, Course


def record(**overrides: object) -> dict:
    base = {
        "class_id": 123,
        "class_room_no": "R2026091800298",
        "class_time": "2026-09-18 14:00:00",
        "end_time": "2026-09-18 14:30:00",
        "class_type": "1v1",
        "class_url": "https://example.test/class/123",
        "grade": "小四上",
        "subject": "數學",
        "knowledge_point_title": "乘法_1B",
        "version_name": "南版AB*",
        "students": [{"student_id": 18639, "student_name": "郭尚儒"}],
        "knowledge_files": [
            {
                "name": "小四上_第二章_乘法_1B(學習教材)",
                "url": "https://files.example.test/material.pdf",
                "class_type": "review",
                "main": "Y",
            },
            {
                "name": "小四上_第二章_乘法_1B(學習教材解答)",
                "url": "https://files.example.test/answer.pdf",
                "class_type": "material_ans",
                "main": "Y",
            },
        ],
    }
    base.update(overrides)
    return base


class CourseModelTests(unittest.TestCase):
    def test_normalizes_daily_schedule_record(self) -> None:
        course = Course.from_api(record())
        self.assertEqual(course.source_id, "123")
        self.assertEqual(course.room_id, "R2026091800298")
        self.assertEqual(course.student_name, "郭尚儒")
        self.assertEqual(course.start_at, "2026-09-18T14:00:00+08:00")
        self.assertEqual(course.end_at, "2026-09-18T14:30:00+08:00")
        self.assertEqual(course.chapter, "乘法_1B")
        self.assertEqual(course.preview_pdf_url, "https://files.example.test/material.pdf")
        self.assertEqual(course.answer_pdf_url, "https://files.example.test/answer.pdf")

    def test_missing_preview_file_is_allowed(self) -> None:
        course = Course.from_api(
            record(knowledge_files=[], students=[], knowledge_point_title=None)
        )
        self.assertEqual(course.preview_pdf_url, "")
        self.assertEqual(course.answer_pdf_url, "")

    def test_missing_chapter_stays_empty_instead_of_dash(self) -> None:
        """舊版會把缺章節填成 '-'，導致事件標題變成「學生｜-」。"""
        course = Course.from_api(record(knowledge_point_title=None))
        self.assertEqual(course.chapter, "")

    def test_missing_end_time_is_preserved_as_empty(self) -> None:
        """缺 end_time 時不可臆測長度；由上層決定跳過。"""
        course = Course.from_api(record(end_time=None))
        self.assertEqual(course.end_at, "")

    def test_non_dict_student_raises_value_error(self) -> None:
        """非物件元素要給明確錯誤，而不是 AttributeError 讓服務崩潰。"""
        with self.assertRaisesRegex(ValueError, "students"):
            Course.from_api(record(students=["王惟晞"]))

    def test_non_dict_knowledge_file_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "knowledge_files"):
            Course.from_api(record(knowledge_files=["教材.pdf"]))

    def test_non_list_students_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "students"):
            Course.from_api(record(students={"student_name": "王惟晞"}))

    def test_missing_class_id_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "class_id"):
            Course.from_api(record(class_id=None))

    def test_public_payload_excludes_student_id(self) -> None:
        course = Course.from_api(record())
        public = course.public()
        self.assertEqual(set(public), set(PUBLIC_COURSE_FIELDS))
        self.assertNotIn("student_id", public)
        self.assertNotIn("subject", public)
        self.assertNotIn("tag", public)
        self.assertEqual(public["student_name"], "郭尚儒")

    def test_content_hash_changes_with_content(self) -> None:
        first = Course.from_api(record())
        same = Course.from_api(record())
        changed = Course.from_api(record(knowledge_point_title="乘法_2B"))
        self.assertEqual(first.content_hash(), same.content_hash())
        self.assertNotEqual(first.content_hash(), changed.content_hash())

    def test_content_hash_changes_when_material_url_changes(self) -> None:
        """教材以雲端為準：URL 變更必須觸發事件更新。"""
        first = Course.from_api(record())
        changed = Course.from_api(
            record(
                knowledge_files=[
                    {
                        "name": "新版教材",
                        "url": "https://files.example.test/material-v2.pdf",
                        "class_type": "review",
                        "main": "Y",
                    }
                ]
            )
        )
        self.assertNotEqual(first.content_hash(), changed.content_hash())


if __name__ == "__main__":
    unittest.main()
