from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from course_robot.models import TAIPEI, Course, parse_datetime


# 課時規則：來源的「時段」比實際授課時間多 5 分鐘緩衝。
#   30 分鐘的時段 → 顯示 25 分鐘
#   60 分鐘的時段 → 顯示 50 分鐘
# 這個換算只取決於該堂課自己的來源長度，與其他課程無關，
# 所以同一堂課永遠得到同一個結果（收斂的必要條件）。
SLOT_TO_LESSON_MINUTES = {30: 25, 60: 50}
BREAK_MINUTES = 5
ELEMENTARY_PREFIX = "小"
SLOT_MINUTES = 30
# 連續授課時，下一節與前一節之間留 1 分鐘，避免日曆上兩筆事件的邊界
# 完全貼齊而顯示成重疊的形狀。


@dataclass(frozen=True)
class DisplayTiming:
    """事件在日曆上實際顯示的起訖時間。"""

    start_at: str
    end_at: str
    note: str = ""

    def as_event_times(self) -> dict[str, dict[str, str]]:
        return {
            "start": {"dateTime": self.start_at, "timeZone": "Asia/Taipei"},
            "end": {"dateTime": self.end_at, "timeZone": "Asia/Taipei"},
        }


def is_elementary(grade: str) -> bool:
    return grade.strip().startswith(ELEMENTARY_PREFIX)


def plan_display(courses: list[Course] | tuple[Course, ...]) -> dict[str, DisplayTiming]:
    """為「每一筆要寫入日曆的事件」決定顯示時間。

    回傳的 key 是每一群的代表（連續授課時為第一堂）；被合併掉的課程
    不會出現在結果裡。

    規則：先依來源時段換算課時（30→25、60→50），
    連續授課（同一學生、同一天、時間相接）再合併成一節，
    長度為各堂課時加總。
    """
    timings: dict[str, DisplayTiming] = {}
    for run in group_lessons(courses):
        first = run[0]
        if not first.end_at:
            continue
        start = parse_datetime(first.start_at)
        total = sum(lesson_minutes_for(slot_minutes_of(c)) for c in run)
        timings[first.source_id] = DisplayTiming(
            start_at=start.isoformat(timespec="seconds"),
            end_at=(start + timedelta(minutes=total)).isoformat(timespec="seconds"),
        )
    return timings


def group_lessons(courses) -> list[list[Course]]:
    """把課程分成「一筆事件一群」：連續授課合成一群，其餘各自一群。"""
    materialised = list(courses)
    runs = {run[0].source_id: run for run in consecutive_runs(materialised)}
    grouped: list[list[Course]] = []
    used: set[str] = set()
    ordered = sorted(materialised, key=lambda c: c.start_at)
    for course in ordered:
        if course.source_id in used or not course.end_at:
            continue
        for run in runs.values():
            if run[0].source_id == course.source_id:
                grouped.append(list(run))
                used.update(c.source_id for c in run)
                break
        else:
            grouped.append([course])
            used.add(course.source_id)
    return grouped


def consecutive_runs(courses) -> list[list[Course]]:
    """同一學生、同一天、時間相接的連續課程分組（長度 >= 2 才回傳）。"""
    groups: dict[tuple[str, str], list[Course]] = {}
    for course in courses:
        if not course.end_at:
            continue
        groups.setdefault((course.student_name, course.start_at[:10]), []).append(course)

    runs: list[list[Course]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item.start_at)
        run: list[Course] = []
        for course in ordered:
            if run and run[-1].end_at != course.start_at:
                if len(run) > 1:
                    runs.append(run)
                run = []
            run.append(course)
        if len(run) > 1:
            runs.append(run)
    return runs


def lesson_minutes_for(slot_minutes: int) -> int:
    """來源時段長度換算成實際授課分鐘數。

    30 → 25、60 → 50；其他長度扣掉 5 分鐘緩衝，但不小於 1 分鐘。
    """
    mapped = SLOT_TO_LESSON_MINUTES.get(slot_minutes)
    if mapped is not None:
        return mapped
    return max(slot_minutes - BREAK_MINUTES, 1)


def slot_minutes_of(course: Course) -> int:
    if not course.end_at:
        return 0
    return int(
        (parse_datetime(course.end_at) - parse_datetime(course.start_at)).total_seconds()
        // 60
    )


def display_timing(course: Course) -> DisplayTiming | None:
    """這堂課在日曆上顯示的起訖時間；缺少結束時間時回傳 None。"""
    if not course.end_at:
        return None
    start = parse_datetime(course.start_at)
    end = start + timedelta(minutes=lesson_minutes_for(slot_minutes_of(course)))
    return DisplayTiming(
        start_at=start.isoformat(timespec="seconds"),
        end_at=end.isoformat(timespec="seconds"),
    )


def consecutive_pairs(courses):
    """找出「同一位國小學生、同一天、時間相接」的兩堂課。"""
    groups: dict[tuple[str, str], list[Course]] = {}
    for course in courses:
        if not course.end_at:
            continue
        groups.setdefault((course.student_name, course.start_at[:10]), []).append(course)

    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: item.start_at)
        for first, second in zip(ordered, ordered[1:]):
            if not is_elementary(first.grade):
                continue
            # 來源時間必須真的相接，才是連堂。
            if first.end_at != second.start_at:
                continue
            yield first, second
