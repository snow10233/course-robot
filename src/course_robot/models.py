from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("課程時間為空")
    normalized = text.replace("/", "-").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
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
        source_id = str(item.get("class_id") or "").strip()
        if not source_id:
            raise ValueError("API 課程缺少 class_id")

        start = parse_datetime(item.get("class_time"))
        end_value = item.get("end_time")
        end = parse_datetime(end_value) if end_value else None
        students = item.get("students") or []
        if not isinstance(students, list):
            raise ValueError(f"課程 {source_id} 的 students 不是陣列")

        knowledge_files = item.get("knowledge_files") or []
        if not isinstance(knowledge_files, list):
            raise ValueError(f"課程 {source_id} 的 knowledge_files 不是陣列")
        preview_file = next(
            (
                file
                for file in knowledge_files
                if isinstance(file, dict)
                and file.get("main") == "Y"
                and file.get("class_type") == "review"
                and file.get("url")
            ),
            {},
        )
        answer_file = next(
            (
                file
                for file in knowledge_files
                if isinstance(file, dict)
                and file.get("main") == "Y"
                and file.get("class_type") == "material_ans"
                and file.get("url")
            ),
            {},
        )

        names = [str(student.get("student_name") or "").strip() for student in students]
        ids = [str(student.get("student_id") or "").strip() for student in students]
        return cls(
            source_id=source_id,
            room_id=str(item.get("class_room_no") or "").strip(),
            start_at=start.isoformat(timespec="seconds"),
            end_at=end.isoformat(timespec="seconds") if end else "",
            class_type=str(item.get("class_type") or "").strip(),
            class_url=str(item.get("class_url") or "").strip(),
            student_name="、".join(name for name in names if name),
            student_id=",".join(student_id for student_id in ids if student_id),
            subject=str(item.get("subject") or "").strip(),
            grade=str(item.get("grade") or item.get("level") or "").strip(),
            chapter=str(item.get("knowledge_point_title") or "-").strip(),
            version_name=str(item.get("version_name") or "").strip(),
            tag=str(item.get("tag") or "").strip(),
            preview_pdf_name=str(preview_file.get("name") or "").strip(),
            preview_pdf_url=str(preview_file.get("url") or "").strip(),
            answer_pdf_name=str(answer_file.get("name") or "").strip(),
            answer_pdf_url=str(answer_file.get("url") or "").strip(),
        )

    def payload(self) -> dict[str, str]:
        return asdict(self)

    def content_hash(self) -> str:
        encoded = json.dumps(
            self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CourseDelta:
    source_id: str
    old: Course | None
    new: Course | None
    google_event_id: str = ""


@dataclass(frozen=True)
class SnapshotDiff:
    added: tuple[CourseDelta, ...]
    updated: tuple[CourseDelta, ...]
    removed: tuple[CourseDelta, ...]
    unchanged: int


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    added: int
    updated: int
    removed: int
    unchanged: int
