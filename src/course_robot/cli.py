from __future__ import annotations

import argparse
import sys
from pathlib import Path

from course_robot.api import Peak1ApiError, Peak1Client
from course_robot.config import Settings
from course_robot.database import (
    apply_snapshot,
    compare_snapshot,
    connect,
    google_event_ids,
    replace_snapshot,
)
from course_robot.google_calendar import (
    GoogleCalendarError,
    GoogleCalendarTransport,
    CalendarPlan,
    apply_calendar_plan,
    authorize_google_calendar,
    build_calendar_plan,
)
from course_robot.models import Course, SnapshotDiff, SyncResult
from course_robot.report import write_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="支點教育課表同步工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="初始化 SQLite 及空白報表")
    init.add_argument("--days", type=int, default=35, help="報表顯示未來幾天")

    sync = subparsers.add_parser("sync", help="登入老師站並同步完整課表")
    sync.add_argument("--days", type=int, default=35, help="報表顯示未來幾天")
    sync.add_argument("--max-pages", type=int, default=100, help="API 最大分頁安全上限")
    sync.add_argument(
        "--allow-empty",
        action="store_true",
        help="即使 API 回傳零筆也允許標記既有未來課程為移除",
    )
    calendar_mode = sync.add_mutually_exclusive_group()
    calendar_mode.add_argument(
        "--calendar",
        dest="calendar",
        action="store_true",
        help="本次同步同時套用 Google Calendar 異動",
    )
    calendar_mode.add_argument(
        "--no-calendar",
        dest="calendar",
        action="store_false",
        help="本次同步不呼叫 Google Calendar",
    )
    sync.set_defaults(calendar=None)

    report = subparsers.add_parser("report", help="從 SQLite 重新產生報表")
    report.add_argument("--days", type=int, default=35, help="報表顯示未來幾天")

    calendar_auth = subparsers.add_parser(
        "calendar-auth",
        help="在本機瀏覽器完成 Google Calendar OAuth 授權",
    )
    calendar_auth.add_argument(
        "--no-browser",
        action="store_true",
        help="顯示授權網址，手動在 Windows 瀏覽器開啟（適用 WSL）",
    )
    subparsers.add_parser(
        "calendar-check",
        help="唯讀確認 token 對目標 Calendar ID 具有事件存取權",
    )

    calendar_plan = subparsers.add_parser(
        "calendar-plan",
        help="預覽下一次同步會對 Google Calendar 做哪些異動，不寫入任何資料",
    )
    calendar_plan.add_argument(
        "--max-pages", type=int, default=100, help="API 最大分頁安全上限"
    )
    calendar_plan.add_argument(
        "--allow-empty",
        action="store_true",
        help="允許把 API 的空快照視為刪除全部既有課程",
    )
    calendar_plan.add_argument(
        "--verbose",
        action="store_true",
        help="列出全部預計新增、修改與刪除的課程",
    )
    return parser


def _settings() -> Settings:
    return Settings.load(Path.cwd())


def _write_reports(settings: Settings, days: int) -> int:
    with connect(settings.database) as connection:
        return write_reports(
            connection,
            settings.report_html,
            settings.report_csv,
            days=days,
        )


def run_init(settings: Settings, days: int) -> int:
    count = _write_reports(settings, days)
    print(f"已初始化：{settings.database}；報表有 {count} 堂未來課程")
    return 0


def _fetch_courses(settings: Settings, max_pages: int) -> tuple[list[Course], int]:
    settings.require_credentials()
    with Peak1Client(
        settings.base_url,
        settings.username,
        settings.password,
        max_pages=max_pages,
    ) as client:
        client.login()
        fetched = client.fetch_all_schedule()
    return [Course.from_api(item) for item in fetched.records], fetched.pages


def _sync_result(courses: list[Course], diff: SnapshotDiff) -> SyncResult:
    return SyncResult(
        fetched=len(courses),
        added=len(diff.added),
        updated=len(diff.updated),
        removed=len(diff.removed),
        unchanged=diff.unchanged,
    )


def run_sync(
    settings: Settings,
    days: int,
    max_pages: int,
    allow_empty: bool,
    calendar_override: bool | None,
) -> int:
    courses, pages = _fetch_courses(settings, max_pages)
    calendar_enabled = (
        settings.google_calendar_enabled
        if calendar_override is None
        else calendar_override
    )
    calendar_text = ""
    with connect(settings.database) as connection:
        if calendar_enabled:
            settings.require_google_calendar()
            diff = compare_snapshot(connection, courses, allow_empty=allow_empty)
            mappings = google_event_ids(connection)
            plan = build_calendar_plan(courses, diff, mappings)
            with GoogleCalendarTransport(
                settings.google_calendar_id,
                settings.google_oauth_token,
            ) as transport:
                calendar_result = apply_calendar_plan(transport, plan, mappings)
            replace_snapshot(
                connection,
                courses,
                event_ids=calendar_result.event_ids,
            )
            result = _sync_result(courses, diff)
            calendar_text = (
                f"；Calendar 新增 {calendar_result.created}、"
                f"修改 {calendar_result.updated}、刪除 {calendar_result.removed}、"
                f"未變 {calendar_result.unchanged}"
            )
        else:
            result = apply_snapshot(connection, courses, allow_empty=allow_empty)
        visible = None
        if settings.reports_enabled:
            visible = write_reports(
                connection,
                settings.report_html,
                settings.report_csv,
                days=days,
            )

    print(
        f"同步完成：API {pages} 頁／{result.fetched} 筆；"
        f"新增 {result.added}、修改 {result.updated}、刪除 {result.removed}、"
        f"未變 {result.unchanged}；已保存最新快照{calendar_text}"
        + (f"；報表顯示 {visible} 堂" if visible is not None else "；報表已停用")
    )
    return 0


def run_calendar_auth(settings: Settings, *, open_browser: bool = True) -> int:
    authorize_google_calendar(
        settings.google_oauth_client,
        settings.google_oauth_token,
        open_browser=open_browser,
    )
    print(f"Google Calendar 授權完成；token 已保存至 {settings.google_oauth_token}")
    return 0


def run_calendar_check(settings: Settings) -> int:
    settings.require_google_calendar()
    with GoogleCalendarTransport(
        settings.google_calendar_id,
        settings.google_oauth_token,
    ) as transport:
        managed_events = transport.verify_access()
    print(
        f"Google Calendar 權限確認成功；course-robot 管理 {managed_events} 筆事件；"
        "未修改任何事件"
    )
    return 0


def _calendar_plan_lines(plan: CalendarPlan) -> list[str]:
    lines = [
        *(f"新增｜{course.start_at}｜{course.student_name}｜{course.chapter}" for course in plan.create),
        *(f"修改｜{course.start_at}｜{course.student_name}｜{course.chapter}" for course in plan.update),
        *(f"刪除｜{course.start_at}｜{course.student_name}｜{course.chapter}" for course, _ in plan.remove),
    ]
    return lines


def run_calendar_plan(
    settings: Settings,
    max_pages: int,
    allow_empty: bool,
    verbose: bool,
) -> int:
    courses, pages = _fetch_courses(settings, max_pages)
    with connect(settings.database) as connection:
        diff = compare_snapshot(connection, courses, allow_empty=allow_empty)
        plan = build_calendar_plan(courses, diff, google_event_ids(connection))

    print(
        f"Calendar 預覽：API {pages} 頁／{len(courses)} 筆；"
        f"預計新增 {len(plan.create)}、修改 {len(plan.update)}、"
        f"刪除 {len(plan.remove)}、未變 {plan.unchanged}"
    )
    lines = _calendar_plan_lines(plan)
    shown = lines if verbose else lines[:10]
    for line in shown:
        print(f"  {line}")
    if len(lines) > len(shown):
        print(f"  ……另有 {len(lines) - len(shown)} 筆；加上 --verbose 可全部列出")
    print("這只是預覽；SQLite 與 Google Calendar 都沒有被修改。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _settings()
    try:
        if args.command == "init":
            return run_init(settings, args.days)
        if args.command == "sync":
            return run_sync(
                settings,
                args.days,
                args.max_pages,
                args.allow_empty,
                args.calendar,
            )
        if args.command == "report":
            visible = _write_reports(settings, args.days)
            print(f"報表已更新：{visible} 堂未來課程")
            return 0
        if args.command == "calendar-auth":
            return run_calendar_auth(settings, open_browser=not args.no_browser)
        if args.command == "calendar-check":
            return run_calendar_check(settings)
        if args.command == "calendar-plan":
            return run_calendar_plan(
                settings,
                args.max_pages,
                args.allow_empty,
                args.verbose,
            )
    except (GoogleCalendarError, Peak1ApiError, ValueError, OSError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    return 2
