from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from course_robot.api import Peak1ApiError
from course_robot.calendar import listing_start, next_run_at, sync_once, window_from_source
from course_robot.source import SourceError
from course_robot.config import Settings
from course_robot.google_calendar import (
    GoogleCalendarError,
    GoogleCalendarTransport,
    authorize_google_calendar,
    build_plan,
)
from course_robot.models import TAIPEI


logger = logging.getLogger("course_robot")

# 這些例外代表「環境或設定問題」，用訊息回報即可，不需要 traceback。
EXPECTED_ERRORS = (
    GoogleCalendarError,
    Peak1ApiError,
    SourceError,
    ValueError,
    OSError,
    RuntimeError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="支點教育課表同步工具")
    parser.add_argument("--verbose", action="store_true", help="輸出除錯訊息")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calendar_auth = subparsers.add_parser(
        "calendar-auth", help="在本機瀏覽器完成 Google Calendar OAuth 授權"
    )
    calendar_auth.add_argument(
        "--no-browser",
        action="store_true",
        help="顯示授權網址，手動在瀏覽器開啟（適用 WSL）",
    )

    subparsers.add_parser(
        "calendar-check", help="唯讀確認 token 對目標日曆具有事件存取權"
    )

    plan = subparsers.add_parser(
        "calendar-plan", help="預覽本輪會對日曆做的異動，不寫入任何資料"
    )
    plan.add_argument(
        "--max-events", type=int, default=20, help="最多列出幾筆（0 表示全部）"
    )

    sync = subparsers.add_parser(
        "calendar-sync", help="抓取老師站課表並重建日曆（無狀態）"
    )
    sync.add_argument(
        "--dry-run", action="store_true", help="只顯示計畫，不寫入日曆"
    )
    sync.add_argument(
        "--no-delete",
        action="store_true",
        help="保留既有事件，只新增與修改（第一次上線建議先跑一次）",
    )
    sync.add_argument(
        "--allow-primary",
        action="store_true",
        help="允許在 primary 日曆上執行刪除（預設拒絕，避免誤刪主日曆）",
    )

    daemon = subparsers.add_parser(
        "calendar-daemon",
        help="常駐並每天在 CALENDAR_SYNC_AT 執行一次同步",
    )
    daemon.add_argument(
        "--run-now",
        action="store_true",
        help="啟動後先跑一次，再進入每日排程",
    )

    return parser


def _settings() -> Settings:
    return Settings.load(Path.cwd())


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# --------------------------------------------------------------------------
# 唯讀檢查
# --------------------------------------------------------------------------


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
        managed = transport.verify_access()
    print(
        f"Google Calendar 權限確認成功；{_calendar_label(settings)} 共有 {managed} 筆"
        "由本工具管理的事件；未修改任何事件"
    )
    return 0


# --------------------------------------------------------------------------
# 計畫與同步
# --------------------------------------------------------------------------


def _build_plan(settings: Settings, *, allow_primary: bool):
    """取得老師站課表、列舉日曆，產生計畫。

    回傳 (transport, plan, now, window, existing_count)。只列舉一次日曆，
    後續同步直接沿用同一批既有事件，避免多打一次 API。
    """
    moment = datetime.now(TAIPEI)
    window = window_from_source(settings)
    transport = GoogleCalendarTransport(
        settings.google_calendar_id, settings.google_oauth_token
    )
    existing = transport.list_events(listing_start(moment))
    plan = build_plan(window.courses, existing, now=moment)
    if plan.remove and _is_primary(settings) and not allow_primary:
        transport.close()
        raise ValueError(
            "GOOGLE_CALENDAR_ID 是 primary，且本輪有 "
            f"{len(plan.remove)} 筆預計刪除；為避免誤刪主日曆已中止。"
            "請改指向專用日曆，或加上 --allow-primary 明確同意。"
        )
    return transport, plan, moment, window, len(existing)


def _is_primary(settings: Settings) -> bool:
    return settings.google_calendar_id.strip().lower() == "primary"


def _calendar_label(settings: Settings) -> str:
    calendar_id = settings.google_calendar_id
    if len(calendar_id) > 28:
        return f"日曆 {calendar_id[:12]}…{calendar_id[-12:]}"
    return f"日曆 {calendar_id}"


def _describe_plan(settings: Settings, plan, max_events: int) -> None:
    print(
        f"Calendar 重建計畫（{_calendar_label(settings)}）："
        f"刪除 {len(plan.remove)}、新增 {len(plan.create)}"
    )
    if plan.skipped:
        print(f"  跳過 {len(plan.skipped)} 筆：")
        for source_id, reason in plan.skipped[:5]:
            print(f"    跳過｜{source_id}｜{reason}")
    if plan.deletions_suppressed:
        print("  ⚠ 來源筆數相對於日曆既有事件異常偏少，本輪不執行任何刪除")

    lines = [
        *(
            f"新增｜{spec.body['start']['dateTime']}｜{spec.body['summary']}"
            for spec in plan.create
        ),
        *(
            f"刪除｜{_event_start_text(event)}｜{event.get('summary', '')}"
            for event in plan.remove
        ),
    ]
    shown = lines if max_events <= 0 else lines[:max_events]
    for line in shown:
        print(f"  {line}")
    if len(lines) > len(shown):
        print(f"  ……另有 {len(lines) - len(shown)} 筆")


def _event_start_text(event: dict) -> str:
    start = event.get("start") or {}
    return str(start.get("dateTime") or start.get("date") or "")


def run_calendar_plan(settings: Settings, max_events: int) -> int:
    settings.require_google_calendar()
    transport, plan, _moment, window, _existing = _build_plan(settings, allow_primary=False)
    try:
        print(f"老師站課表：{len(window.courses)} 筆課程（抓取於 {window.fetched_at}）")
        _describe_plan(settings, plan, max_events)
        print("這只是預覽；日曆與任何資料都沒有被修改。")
    finally:
        transport.close()
    return 0


def run_calendar_sync(
    settings: Settings,
    *,
    dry_run: bool,
    no_delete: bool,
    allow_primary: bool,
    on_error=None,
) -> int:
    settings.require_google_calendar()
    transport, plan, moment, window, existing_count = _build_plan(
        settings, allow_primary=allow_primary
    )
    try:
        if no_delete and plan.remove:
            plan = type(plan)(
                create=plan.create,
                remove=(),
                skipped=plan.skipped,
                deletions_suppressed=True,
            )
        if dry_run:
            print(f"老師站課表：{len(window.courses)} 筆課程（抓取於 {window.fetched_at}）")
            _describe_plan(settings, plan, 20)
            print("--dry-run：日曆沒有被修改。")
            return 0

        result = sync_once(transport, window, now=moment, on_error=on_error)
        print(
            f"重建完成（{_calendar_label(settings)}）："
            f"刪除 {result.removed}、新增 {result.created}"
            + (f"；第 {result.attempts} 次通過審查" if result.verified else "；⚠ 未通過審查")
            + (f"；跳過 {len(result.skipped)}" if result.skipped else "")
            + ("；已抑制刪除" if result.deletions_suppressed else "")
        )
        for source_id, reason in result.skipped[:5]:
            print(f"  跳過｜{source_id}｜{reason}")
        if result.errors:
            print(f"  本輪有 {len(result.errors)} 筆失敗：")
            for message in result.errors[:10]:
                print(f"    {message}")
            return 1
    finally:
        transport.close()
    return 0


def run_calendar_daemon(
    settings: Settings, *, run_now: bool, on_error=None
) -> int:
    settings.require_google_calendar()
    print(
        f"Calendar 服務啟動：每天 {settings.calendar_sync_at}（Asia/Taipei）執行一次"
    )
    if run_now:
        _attempt_sync(settings, on_error=on_error)
    while True:
        target = next_run_at(datetime.now(TAIPEI), settings.calendar_sync_at)
        seconds = max((target - datetime.now(TAIPEI)).total_seconds(), 1.0)
        print(f"下一次執行：{target.isoformat(timespec='seconds')}（{int(seconds)} 秒後）")
        time.sleep(seconds)
        _attempt_sync(settings, on_error=on_error)


def _attempt_sync(settings: Settings, *, on_error=None) -> None:
    """執行一次同步；失敗只記錄，不讓常駐服務結束。"""
    try:
        code = run_calendar_sync(
            settings,
            dry_run=False,
            no_delete=False,
            allow_primary=False,
            on_error=on_error,
        )
    except EXPECTED_ERRORS as exc:
        message = f"Calendar 同步失敗：{exc}"
        logger.error(message)
        print(message, file=sys.stderr)
        if on_error is not None:
            on_error(message)
        return
    if code != 0:
        logger.warning("Calendar 同步完成但有部分失敗")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    settings = _settings()
    try:
        if args.command == "calendar-auth":
            return run_calendar_auth(settings, open_browser=not args.no_browser)
        if args.command == "calendar-check":
            return run_calendar_check(settings)
        if args.command == "calendar-plan":
            return run_calendar_plan(settings, args.max_events)
        if args.command == "calendar-sync":
            return run_calendar_sync(
                settings,
                dry_run=args.dry_run,
                no_delete=args.no_delete,
                allow_primary=args.allow_primary,
                on_error=lambda message: print(message, file=sys.stderr),
            )
        if args.command == "calendar-daemon":
            return run_calendar_daemon(
                settings,
                run_now=args.run_now,
                on_error=lambda message: print(message, file=sys.stderr),
            )
    except EXPECTED_ERRORS as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中止", file=sys.stderr)
        return 130
    return 2
