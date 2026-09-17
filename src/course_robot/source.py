from __future__ import annotations

import logging
from dataclasses import dataclass

from course_robot.api import Peak1ApiError, Peak1Client
from course_robot.config import Settings
from course_robot.models import Course


logger = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """來源（老師站）無法提供可信的完整資料。"""


@dataclass(frozen=True)
class Snapshot:
    """一次成功抓取的完整結果。"""

    courses: tuple[Course, ...]
    fetched_at: str
    window_start: str
    window_end: str
    pages: int

    def public_payload(self) -> list[dict[str, str]]:
        return [course.public() for course in self.courses]


def fetch_snapshot(settings: Settings, *, max_pages: int = 100) -> Snapshot:
    """登入老師站並取得完整課表。

    任何一筆課程無法解析，就整批失敗——寧可這一輪不更新，
    也不要讓殘缺的資料流向日曆。
    """
    settings.require_credentials()
    with Peak1Client(
        settings.base_url,
        settings.username,
        settings.password,
        max_pages=max_pages,
    ) as client:
        client.login()
        fetched = client.fetch_all_schedule()

    courses: list[Course] = []
    for index, item in enumerate(fetched.records):
        try:
            courses.append(Course.from_api(item))
        except ValueError as exc:
            raise SourceError(f"第 {index + 1} 筆課程無法解析：{exc}") from exc

    if len(courses) != fetched.reported_total:
        raise SourceError(
            f"課程筆數不符：宣稱 {fetched.reported_total} 筆，解析後得到 {len(courses)} 筆"
        )

    starts = sorted(course.start_at for course in courses)
    return Snapshot(
        courses=tuple(courses),
        fetched_at=fetched.fetched_at,
        window_start=starts[0] if starts else "",
        window_end=starts[-1] if starts else "",
        pages=fetched.pages,
    )


