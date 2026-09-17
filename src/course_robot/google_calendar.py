from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import quote

import httpx

from course_robot.models import TAIPEI, Course, parse_datetime
from course_robot.timing import group_lessons, plan_display


CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

MANAGED_PROPERTY = "course_robot_managed"


RETRYABLE_STATUS = {429, 500, 502, 503, 504}

logger = logging.getLogger(__name__)


class GoogleCalendarError(RuntimeError):
    retryable = False

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class CalendarTransport(Protocol):
    def list_events(self, time_min: str) -> list[dict[str, Any]]: ...

    def insert_event(self, body: dict[str, Any]) -> dict[str, Any]: ...


    def get_event(self, event_id: str) -> dict[str, Any] | None: ...

    def delete_event(self, event_id: str) -> None: ...


# --------------------------------------------------------------------------
# 事件 ID 與內容
# --------------------------------------------------------------------------


def build_event_body(
    courses: Course | list[Course],
    timing: DisplayTiming | None = None,
    *,
    note: str = "",
) -> dict[str, Any]:
    """組出事件內容。

    接受一群課程：連續授課時會合併成一筆事件，
    標題取第一堂的章節，教材與解答則兩堂都附上。
    呼叫端必須先確認結束時間存在——缺少時我們不猜測長度。
    """
    return _compose_body(courses, timing, note=note)


def _compose_body(
    courses: Course | list[Course],
    timing: DisplayTiming | None = None,
    *,
    note: str = "",
) -> dict[str, Any]:
    """組出事件內容。

    一群課程（連續授課）會被合成一筆事件：
    - 標題：學生｜第一堂的章節
    - 說明：年級、以及**每一堂各自的教材與解答**
    """
    group = [courses] if isinstance(courses, Course) else list(courses)
    if not group:
        raise ValueError("沒有課程可以建立事件")

    first = group[0]
    for course in group:
        if not course.end_at:
            raise ValueError(f"課程 {course.source_id} 缺少結束時間，無法建立事件")

    if timing is None:
        raise ValueError(f"課程 {first.source_id} 沒有可用的顯示時間")
    display = timing
    summary_parts = [part for part in (first.student_name, first.chapter) if part]

    description_lines: list[str] = []
    grade = next((c.grade for c in group if c.grade), "")
    if grade:
        description_lines.append(f"年級：{grade}")
    for course in group:
        if course.preview_pdf_url:
            description_lines.append(f"教材：{course.preview_pdf_url}")
    for course in group:
        if course.answer_pdf_url:
            description_lines.append(f"解答：{course.answer_pdf_url}")

    body: dict[str, Any] = {
        "summary": "｜".join(summary_parts) or f"支點課程 {first.source_id}",
        "description": "\n".join(description_lines),
        **display.as_event_times(),
        "visibility": "private",
        "extendedProperties": {"private": {MANAGED_PROPERTY: "true"}},
    }
    return body


@dataclass(frozen=True)
class EventSpec:
    """一筆預計寫入日曆的事件。

    刻意不指定事件 ID：讓 Google 自行產生。
    指定固定 ID 的話，每天重建時刪掉的 ID 會被永久保留，
    隔天再建立同一個 ID 就會 409，於是每天都要往下找新的後綴。
    既然每天整批換掉，就不需要事件身分。
    """

    body: dict[str, Any]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class SyncPlan:
    """每日重建計畫：先把今天之後的清掉，再放上目前的課表。"""

    create: tuple[EventSpec, ...] = ()
    remove: tuple[dict[str, Any], ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    deletions_suppressed: bool = False

    @property
    def writes(self) -> int:
        return len(self.create) + len(self.remove)


@dataclass(frozen=True)
class SyncResult:
    created: int
    removed: int
    skipped: tuple[tuple[str, str], ...]
    deletions_suppressed: bool
    errors: tuple[str, ...] = ()
    verified: bool = False
    attempts: int = 1


def build_plan(
    courses: Iterable[Course],
    existing_events: Iterable[dict[str, Any]],
    *,
    now: datetime,
) -> SyncPlan:
    """產生「今天之後全部重建」的計畫。

    不做增量比對、也不算內容雜湊——每天一次，直接重放最單純，
    而且沒有「雜湊不一致導致每輪重寫」這類問題。

    安全規則：
    1. 沒有管理標記的事件一律不碰（可能是手動建立的行程）。
    2. 只刪除「今天 00:00 之後」的事件，過去的紀錄一定保留。
    3. 來源一筆都沒有時，不執行任何刪除（避免把日曆清空）。
    """
    course_list = [course for course in courses if course.end_at]
    skipped = tuple(
        (course.source_id, "缺少結束時間")
        for course in courses
        if not course.end_at
    )

    timings = plan_display(course_list)
    groups = group_lessons(course_list)

    create: list[EventSpec] = []
    for run in groups:
        first = run[0]
        timing = timings.get(first.source_id)
        if timing is None:
            skipped = skipped + ((first.source_id, "無法計算顯示時間"),)
            continue
        try:
            body = build_event_body(run, timing)
        except ValueError as exc:
            skipped = skipped + ((first.source_id, str(exc)),)
            continue
        create.append(
            EventSpec(
                body=body,
                source_ids=tuple(course.source_id for course in run),
            )
        )

    boundary = _day_start(now)
    removable = [
        event
        for event in existing_events
        if _is_managed(event)
        and (start := _event_start(event)) is not None
        and start >= boundary
    ]

    suppressed = not course_list and bool(removable)
    return SyncPlan(
        create=tuple(create),
        remove=() if suppressed else tuple(removable),
        skipped=skipped,
        deletions_suppressed=suppressed,
    )


def _day_start(now: datetime) -> datetime:
    local = now.astimezone(TAIPEI)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def apply_plan(
    transport: CalendarTransport,
    plan: SyncPlan,
    *,
    on_error: Callable[[str], None] | None = None,
) -> SyncResult:
    """套用計畫：先刪除今天之後的事件，再重新建立。

    刪除只針對帶管理標記的事件，所以手動建立的事件不受影響。
    """
    removed = 0
    created = 0
    errors: list[str] = []

    for event in plan.remove:
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        try:
            transport.delete_event(event_id)
            removed += 1
        except GoogleCalendarError as exc:
            errors.append(f"刪除 {event_id} 失敗：{exc}")
            _notify(on_error, errors[-1])

    for spec in plan.create:
        try:
            transport.insert_event(spec.body)
            created += 1
        except GoogleCalendarError as exc:
            errors.append(f"新增 {spec.body.get('summary', '')} 失敗：{exc}")
            _notify(on_error, errors[-1])

    return SyncResult(
        created=created,
        removed=removed,
        skipped=plan.skipped,
        deletions_suppressed=plan.deletions_suppressed,
        errors=tuple(errors),
    )


def verify_rebuild(
    transport: CalendarTransport, plan: SyncPlan
) -> tuple[bool, str]:
    """審查重建結果：今天之後應該只剩我們剛建立的事件。

    不指定事件 ID，所以這裡比對「筆數」而不是逐一認 ID。
    用 `events.list` 檢查；它可能有數分鐘的索引延遲，因此只在
    筆數不足時才判定失敗，並由呼叫端整輪重試。

    回傳（是否通過、說明）。
    """
    expected = len(plan.create)
    actual = [
        event
        for event in transport.list_events(_listing_boundary(plan))
        if _is_managed(event)
    ]
    if len(actual) < expected:
        return False, f"事件數不足：預期 {expected}、清單看到 {len(actual)}"
    if len(actual) > expected:
        return False, f"事件數過多（可能有殘留）：預期 {expected}、清單看到 {len(actual)}"
    return True, ""


def _listing_boundary(plan: "SyncPlan") -> str:
    """審查時的列舉下界：計畫中最早的刪除時間，沒有就用今天 00:00。"""
    starts = [
        start for event in plan.remove if (start := _event_start(event)) is not None
    ]
    if starts:
        return min(starts).isoformat(timespec="seconds")
    return (
        datetime.now(TAIPEI)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _is_managed(event: dict[str, Any]) -> bool:
    return _private_property(event, MANAGED_PROPERTY) == "true"


def _private_property(event: dict[str, Any], name: str) -> str:
    properties = event.get("extendedProperties") or {}
    private = properties.get("private") or {}
    return str(private.get(name) or "")


def _event_start(event: dict[str, Any]) -> datetime | None:
    start = event.get("start") or {}
    raw = start.get("dateTime") or start.get("date")
    if not raw:
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        return None


def authorize_google_calendar(
    client_file: Path, token_file: Path, *, open_browser: bool = True
) -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleCalendarError("缺少 Google OAuth 套件，請先執行 uv sync") from exc

    if not client_file.exists():
        raise GoogleCalendarError(f"找不到 Google OAuth 憑證：{client_file}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file), [CALENDAR_EVENTS_SCOPE]
    )
    credentials = flow.run_local_server(port=0, open_browser=open_browser)
    write_token(token_file, credentials.to_json())


def write_token(token_file: Path, payload: str) -> None:
    """原子寫入 token：先寫暫存檔再 os.replace，避免中斷時毀掉唯一的 refresh token。"""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        dir=str(token_file.parent), prefix=".token-", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, token_file)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def load_google_credentials(token_file: Path) -> Any:
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise GoogleCalendarError("缺少 Google OAuth 套件，請先執行 uv sync") from exc

    if not token_file.exists():
        raise GoogleCalendarError(
            f"找不到 Google token：{token_file}；請先執行 course-robot calendar-auth"
        )
    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_file), [CALENDAR_EVENTS_SCOPE]
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            write_token(token_file, credentials.to_json())
    except (RefreshError, ValueError) as exc:
        raise GoogleCalendarError(
            "Google token 無法更新，請重新執行 calendar-auth"
        ) from exc
    if not credentials.valid:
        raise GoogleCalendarError("Google 授權已失效，請重新執行 calendar-auth")
    return credentials


# --------------------------------------------------------------------------
# HTTP transport
# --------------------------------------------------------------------------


class GoogleCalendarTransport:
    def __init__(
        self,
        calendar_id: str,
        token_file: Path,
        *,
        timeout: float = 30.0,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.calendar_id = calendar_id
        self.token_file = token_file
        self.credentials = load_google_credentials(token_file)
        self.attempts = max(1, attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self._sleep = sleep
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "GoogleCalendarTransport":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- 請求 -----------------------------------------------------------

    def _events_url(self, event_id: str = "") -> str:
        calendar = quote(self.calendar_id, safe="")
        base = f"{CALENDAR_API_BASE}/calendars/{calendar}/events"
        return f"{base}/{quote(event_id, safe='')}" if event_id else base

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accepted: tuple[int, ...] = (200,),
        tolerated: tuple[int, ...] = (),
    ) -> httpx.Response:
        last_error: GoogleCalendarError | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self.credentials.token}"},
                    json=json_body,
                    params=params,
                )
            except httpx.TransportError as exc:
                last_error = GoogleCalendarError(
                    f"連線 Google Calendar 失敗：{exc}", retryable=True
                )
            else:
                if response.status_code in accepted or response.status_code in tolerated:
                    return response
                if response.status_code in RETRYABLE_STATUS:
                    last_error = GoogleCalendarError(
                        f"Google Calendar 暫時無法處理（HTTP {response.status_code}）",
                        retryable=True,
                    )
                else:
                    raise self._error_from(method, response)
            if attempt < self.attempts:
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        assert last_error is not None
        raise GoogleCalendarError(
            f"{last_error}（已重試 {self.attempts} 次）", retryable=True
        )

    @staticmethod
    def _error_from(method: str, response: httpx.Response) -> GoogleCalendarError:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except (json.JSONDecodeError, AttributeError, ValueError):
            detail = response.text[:300]
        hint = ""
        if response.status_code == 401:
            hint = "；token 可能已失效，請重新執行 calendar-auth"
        elif response.status_code == 403:
            hint = "；請確認 Calendar API 已啟用，且 token 對該日曆有寫入權限"
        elif response.status_code == 404:
            hint = "；請確認 GOOGLE_CALENDAR_ID 正確且該日曆存在"
        return GoogleCalendarError(
            f"Google Calendar API {method} 失敗（HTTP {response.status_code}）："
            f"{detail}{hint}"
        )

    # -- 操作 -----------------------------------------------------------

    def list_events(self, time_min: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "timeMin": time_min,
                "maxResults": 2500,
                "singleEvents": "true",
                "showDeleted": "false",
                "privateExtendedProperty": f"{MANAGED_PROPERTY}=true",
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._request("GET", self._events_url(), params=params)
            payload = response.json()
            events.extend([item for item in payload.get("items", []) if _is_managed(item)])
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return events

    def verify_access(self) -> int:
        """唯讀檢查：算出這個日曆上一共有多少筆本工具管理的事件。"""
        counted = 0
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "maxResults": 2500,
                "showDeleted": "false",
                "privateExtendedProperty": f"{MANAGED_PROPERTY}=true",
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._request("GET", self._events_url(), params=params)
            payload = response.json()
            counted += len(payload.get("items", []))
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return counted

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        response = self._request("GET", self._events_url(event_id), tolerated=(404, 410))
        if response.status_code in (404, 410):
            return None
        return response.json()

    def insert_event(self, body: dict[str, Any]) -> dict[str, Any]:
        """建立事件。

        刻意不指定事件 ID，讓 Google 自行產生——指定固定 ID 的話，
        每天重建時刪掉的 ID 會被永久保留，隔天再建立同一個 ID 就會 409。
        隨機 ID 沒有這個問題。
        """
        response = self._request("POST", self._events_url(), json_body=body)
        return response.json()

    def _insert_once(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST", self._events_url(), json_body={**body, "id": event_id}
        )
        return response.json()

    def _patch_once(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request("PATCH", self._events_url(event_id), json_body=body)
        return response.json()

    def delete_event(self, event_id: str) -> None:
        # 404／410 代表「已經不在」，對同步而言就是成功。
        self._request(
            "DELETE", self._events_url(event_id), accepted=(204,), tolerated=(404, 410)
        )
