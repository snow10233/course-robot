from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from course_robot.database import active_courses, current_state
from course_robot.models import TAIPEI


PUBLIC_COURSE_FIELDS = (
    "source_id",
    "start_at",
    "end_at",
    "student_name",
    "grade",
    "chapter",
    "preview_pdf_name",
    "preview_pdf_url",
    "answer_pdf_name",
    "answer_pdf_url",
    "class_type",
    "room_id",
    "class_url",
)


def _public_course(course: dict[str, str]) -> dict[str, str]:
    return {field: course.get(field, "") for field in PUBLIC_COURSE_FIELDS}


def write_csv(path: Path, courses: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=PUBLIC_COURSE_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(_public_course(course) for course in courses)


def _state_payload(state: sqlite3.Row | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "last_success_at": state["last_success_at"],
        "fetched_count": state["fetched_count"],
    }


def write_json(
    path: Path,
    courses: list[dict[str, str]],
    state: sqlite3.Row | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "state": _state_payload(state),
        "courses": [_public_course(course) for course in courses],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_web_assets(html_path: Path) -> None:
    """Copy the data-free frontend shell next to the generated JSON report."""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    web_assets = files("course_robot").joinpath("web")
    destinations = {
        "schedule.html": html_path,
        "schedule.js": html_path.with_suffix(".js"),
        "schedule.css": html_path.with_suffix(".css"),
    }
    for source_name, destination in destinations.items():
        with web_assets.joinpath(source_name).open("rb") as source:
            with destination.open("wb") as target:
                shutil.copyfileobj(source, target)


def write_reports(
    connection: sqlite3.Connection,
    html_path: Path,
    csv_path: Path,
    *,
    days: int = 35,
) -> int:
    courses = active_courses(connection, days=days)
    write_csv(csv_path, courses)
    write_json(html_path.with_suffix(".json"), courses, current_state(connection))
    write_web_assets(html_path)
    return len(courses)
