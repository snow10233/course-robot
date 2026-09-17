"""Calendar 服務：無狀態地把課表重建到 Google Calendar。

每輪流程：
1. 呼叫後端 API 取得目前的課程窗口。
2. 列舉日曆上「今天之後、帶管理標記」的事件。
3. 比對後新增／修改；刪除只作用在還沒開始的事件。

沒有資料庫：事件 ID 由 `class_id` 決定性產生，事件本身帶管理標記，
所以「哪個事件對應哪堂課」隨時可以重算。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from course_robot.google_calendar import (
    CalendarTransport,
    GoogleCalendarError,
    SyncPlan,
    SyncResult,
    apply_plan,
    build_plan,
    verify_rebuild,
)
from course_robot.models import TAIPEI, Course, parse_datetime
from course_robot.source import SourceError, fetch_snapshot
from course_robot.timing import plan_display


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CourseWindow:
    """要寫入日曆的課程範圍（由來源層取得）。"""

    courses: tuple[Course, ...]
    fetched_at: str
    window_start: str
    window_end: str


def listing_start(now: datetime) -> str:
    """列舉下界：今天 00:00。

    比刪除下界（now）寬，才能看到「今天稍早才被取消」的事件；
    真正能不能刪由 build_plan 的 `start <= now` 規則決定。
    """
    local = now.astimezone(TAIPEI)
    return datetime.combine(local.date(), time.min, tzinfo=TAIPEI).isoformat(
        timespec="seconds"
    )


def sync_once(
    transport: CalendarTransport,
    window: CourseWindow,
    *,
    now: datetime | None = None,
    max_attempts: int = 3,
    on_error=None,
) -> SyncResult:
    """重建日曆，並審查結果；不通過就整輪重試。

    流程（每天 00:00 執行一次）：
    1. 列出今天 00:00 之後、帶管理標記的事件
    2. 全部刪除
    3. 依目前課表重新建立
    4. 重新列舉並比對：預期的都在、沒有殘留
       → 不通過就重試，最多 max_attempts 次
    """
    moment = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    last_result: SyncResult | None = None
    last_reason = ""

    for attempt in range(1, max_attempts + 1):
        existing = transport.list_events(listing_start(moment))
        plan = build_plan(window.courses, existing, now=moment)
        result = apply_plan(transport, plan, on_error=on_error)
        last_result = result

        ok, reason = verify_rebuild(transport, plan)
        if ok and not result.errors:
            return SyncResult(
                created=result.created,
                removed=result.removed,
                skipped=result.skipped,
                deletions_suppressed=result.deletions_suppressed,
                errors=result.errors,
                verified=True,
                attempts=attempt,
            )
        last_reason = reason or "；".join(result.errors[:1]) or "審查未通過"
        logger.warning("第 %d 次重建未通過審查：%s", attempt, last_reason)
        if on_error is not None:
            on_error(f"第 {attempt} 次重建未通過：{last_reason}")

    assert last_result is not None
    return SyncResult(
        created=last_result.created,
        removed=last_result.removed,
        skipped=last_result.skipped,
        deletions_suppressed=last_result.deletions_suppressed,
        errors=last_result.errors + (f"重試 {max_attempts} 次仍未通過審查：{last_reason}",),
        verified=False,
        attempts=max_attempts,
    )


def next_run_at(now: datetime, at: str) -> datetime:
    """算出下一次執行的時間（每天固定的 HH:MM，Asia/Taipei）。"""
    hour, minute = _parse_at(at)
    local = now.astimezone(TAIPEI)
    candidate = datetime.combine(local.date(), time(hour, minute), tzinfo=TAIPEI)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def _parse_at(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"CALENDAR_SYNC_AT 必須是 HH:MM，目前是 {value!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"CALENDAR_SYNC_AT 必須是 HH:MM，目前是 {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"CALENDAR_SYNC_AT 超出範圍：{value!r}")
    return hour, minute


def window_from_source(settings) -> CourseWindow:
    """從老師站取得要同步的課程範圍。"""
    snapshot = fetch_snapshot(settings)
    return CourseWindow(
        courses=snapshot.courses,
        fetched_at=snapshot.fetched_at,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
    )


__all__ = [
    "SourceError",
    "CourseWindow",
    "GoogleCalendarError",
    "CourseWindow",
    "SourceError",
    "window_from_source",
    "listing_start",
    "next_run_at",
    "run_once",
    "sync_once",
    "parse_datetime",
]
