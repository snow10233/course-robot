from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")

# 後端 API 與前端共用的公開欄位白名單。
# student_id 刻意不在其中：那是內部識別碼，Calendar 事件與網頁都不需要。
PUBLIC_COURSE_FIELDS = (
    "source_id",
    "start_at",
    "end_at",
    "student_name",
    "grade",
    "chapter",
    "version_name",
    "class_type",
    "room_id",
    "class_url",
    "preview_pdf_name",
    "preview_pdf_url",
    "answer_pdf_name",
    "answer_pdf_url",
)


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("課程時間為空")
    parsed = datetime.fromisoformat(text.replace("/", "-"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


@dataclass(frozen=True)
class Course:
    source_id: str
    room_id: str
    start_at: str
    end_at: str
    class_type: str
    class_url: str
    student_name: str
    student_id: str
    subject: str
    grade: str
    chapter: str
    version_name: str
    tag: str
    preview_pdf_name: str = ""
    preview_pdf_url: str = ""
    answer_pdf_name: str = ""
    answer_pdf_url: str = ""

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Course":
        if not isinstance(item, dict):
            raise ValueError("課程資料不是物件")

        source_id = str(item.get("class_id") or "").strip()
        if not source_id:
            raise ValueError("API 課程缺少 class_id")

        start = parse_datetime(item.get("class_time"))
        end_value = item.get("end_time")
        end = parse_datetime(end_value) if end_value else None

        students = _dict_list(item.get("students"), "students", source_id)
        knowledge_files = _dict_list(
            item.get("knowledge_files"), "knowledge_files", source_id
        )

        preview_file = _pick_file(knowledge_files, "review")
        answer_file = _pick_file(knowledge_files, "material_ans")

        names = [_text(student.get("student_name")) for student in students]
        ids = [_text(student.get("student_id")) for student in students]
        chapter = _text(item.get("knowledge_point_title"))

        return cls(
            source_id=source_id,
            room_id=_text(item.get("class_room_no")),
            start_at=start.isoformat(timespec="seconds"),
            end_at=end.isoformat(timespec="seconds") if end else "",
            class_type=_text(item.get("class_type")),
            class_url=_text(item.get("class_url")),
            student_name="、".join(name for name in names if name),
            student_id=",".join(sid for sid in ids if sid),
            subject=_text(item.get("subject")),
            grade=_text(item.get("grade") or item.get("level")),
            chapter=chapter,
            version_name=_text(item.get("version_name")),
            tag=_text(item.get("tag")),
            preview_pdf_name=_text(preview_file.get("name")),
            preview_pdf_url=_text(preview_file.get("url")),
            answer_pdf_name=_text(answer_file.get("name")),
            answer_pdf_url=_text(answer_file.get("url")),
        )

    def payload(self) -> dict[str, str]:
        return asdict(self)

    def public(self) -> dict[str, str]:
        """只含對外公開欄位（不含 student_id）。"""
        everything = self.payload()
        return {field: everything.get(field, "") for field in PUBLIC_COURSE_FIELDS}

    def content_hash(self) -> str:
        """事件內容雜湊，用來判斷日曆上既有的事件是否需要更新。"""
        encoded = json.dumps(
            self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_list(value: Any, field: str, source_id: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"課程 {source_id} 的 {field} 不是陣列")
    for element in value:
        if not isinstance(element, dict):
            raise ValueError(f"課程 {source_id} 的 {field} 含有非物件元素")
    return value


def _pick_file(files: list[dict[str, Any]], class_type: str) -> dict[str, Any]:
    return next(
        (
            file
            for file in files
            if file.get("main") == "Y"
            and file.get("class_type") == class_type
            and file.get("url")
        ),
        {},
    )
