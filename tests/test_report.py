from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from course_robot.report import write_csv, write_json, write_web_assets


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.course = {
            "source_id": "123",
            "start_at": "2026-09-26T17:00:00+08:00",
            "end_at": "2026-09-26T17:50:00+08:00",
            "student_name": "王惟晞",
            "subject": "數學",
            "grade": "國二上",
            "chapter": "2-2 根式的運算_2B",
            "preview_pdf_name": "學習教材",
            "preview_pdf_url": "https://files.example.test/material.pdf",
            "answer_pdf_name": "學習教材解答",
            "answer_pdf_url": "https://files.example.test/answer.pdf",
            "class_type": "1v1",
            "room_id": "room-1",
            "class_url": "https://class.example.test/room-1",
        }

    def test_json_contains_data_but_html_is_a_data_free_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            html_path = output / "schedule.html"
            json_path = output / "schedule.json"
            write_web_assets(html_path)
            write_json(json_path, [self.course], None)

            document = html_path.read_text(encoding="utf-8")
            script = (output / "schedule.js").read_text(encoding="utf-8")
            data = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertNotIn("王惟晞", document)
            self.assertNotIn("根式的運算", document)
            self.assertNotIn("material.pdf", document)
            self.assertIn('id="schedule-body"', document)
            self.assertIn('src="schedule.js"', document)
            self.assertIn("fetch(DATA_URL", script)
            self.assertIn('document.createElement("tr")', script)
            self.assertIn('makeLink("解"', script)
            self.assertEqual(data["courses"][0]["chapter"], "2-2 根式的運算_2B")
            self.assertEqual(
                data["courses"][0]["preview_pdf_url"],
                "https://files.example.test/material.pdf",
            )
            self.assertEqual(
                data["courses"][0]["answer_pdf_url"],
                "https://files.example.test/answer.pdf",
            )
            self.assertNotIn("subject", data["courses"][0])

    def test_csv_hides_subject_and_keeps_both_document_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "schedule.csv"
            write_csv(csv_path, [self.course])

            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle))
            self.assertNotIn("subject", header)
            self.assertIn("preview_pdf_url", header)
            self.assertIn("answer_pdf_url", header)
