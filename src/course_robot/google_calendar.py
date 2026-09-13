from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from course_robot.models import Course, SnapshotDiff, parse_datetime


CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarError(RuntimeError):
    pass


class CalendarTransport(Protocol):
    def insert_event(self, event_id: str, body: dict[str, Any]) -> str: ...

    def patch_event(self, event_id: str, body: dict[str, Any]) -> str: ...

    def delete_event(self, event_id: str) -> None: ...


@dataclass(frozen=True)
class CalendarPlan:
    create: tuple[Course, ...]
    update: tuple[Course, ...]
    remove: tuple[tuple[Course, str], ...]
    unchanged: int

    @property
    def writes(self) -> int:
        return len(self.create) + len(self.update) + len(self.remove)


@dataclass(frozen=True)
class CalendarSyncResult:
    created: int
    updated: int
    removed: int
    unchanged: int
    event_ids: dict[str, str]


def google_event_id(source_id: str) -> str:
    """Return a stable ID using only Calendar's accepted base32hex characters."""
    digest = hashlib.sha256(f"course-robot:{source_id}".encode()).hexdigest()
    return f"peak1{digest[:32]}"


def event_body(course: Course) -> dict[str, Any]:
    if not course.end_at:
        end_at = (parse_datetime(course.start_at) + timedelta(minutes=50)).isoformat(
            timespec="seconds"
        )
    else:
        end_at = course.end_at

    summary_parts = [part for part in (course.student_name, course.chapter) if part]
    description = [
        f"年級：{course.grade}" if course.grade else "",
        f"教材：{course.preview_pdf_url}" if course.preview_pdf_url else "",
        f"解答：{course.answer_pdf_url}" if course.answer_pdf_url else "",
        f"進入教室：{course.class_url}" if course.class_url else "",
        f"支點課程 ID：{course.source_id}",
    ]
    body: dict[str, Any] = {
        "summary": "｜".join(summary_parts) or f"支點課程 {course.source_id}",
        "description": "\n".join(line for line in description if line),
        "start": {"dateTime": course.start_at, "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end_at, "timeZone": "Asia/Taipei"},
        "visibility": "private",
        "extendedProperties": {
            "private": {
                "course_robot_managed": "true",
                "course_robot_source_id": course.source_id,
            }
        },
    }
    if course.class_url:
        body["source"] = {"title": "支點線上教室", "url": course.class_url}
    return body


def build_calendar_plan(
    courses: list[Course],
    diff: SnapshotDiff,
    existing_event_ids: dict[str, str],
) -> CalendarPlan:
    added_ids = {delta.source_id for delta in diff.added}
    updated_ids = {delta.source_id for delta in diff.updated}

    create = [delta.new for delta in diff.added if delta.new is not None]
    create.extend(
        course
        for course in courses
        if course.source_id not in added_ids
        and course.source_id not in updated_ids
        and not existing_event_ids.get(course.source_id)
    )
    update = [delta.new for delta in diff.updated if delta.new is not None]
    remove = [
        (delta.old, delta.google_event_id or google_event_id(delta.source_id))
        for delta in diff.removed
        if delta.old is not None
    ]
    unchanged = len(courses) - len(create) - len(update)
    return CalendarPlan(
        create=tuple(create),
        update=tuple(update),
        remove=tuple(remove),
        unchanged=max(unchanged, 0),
    )


def apply_calendar_plan(
    transport: CalendarTransport,
    plan: CalendarPlan,
    existing_event_ids: dict[str, str],
) -> CalendarSyncResult:
    event_ids = dict(existing_event_ids)

    for old_course, event_id in plan.remove:
        transport.delete_event(event_id)
        event_ids.pop(old_course.source_id, None)

    for course in plan.create:
        preferred_id = existing_event_ids.get(course.source_id) or google_event_id(
            course.source_id
        )
        event_ids[course.source_id] = transport.insert_event(
            preferred_id, event_body(course)
        )

    for course in plan.update:
        preferred_id = existing_event_ids.get(course.source_id) or google_event_id(
            course.source_id
        )
        event_ids[course.source_id] = transport.patch_event(
            preferred_id, event_body(course)
        )

    return CalendarSyncResult(
        created=len(plan.create),
        updated=len(plan.update),
        removed=len(plan.remove),
        unchanged=plan.unchanged,
        event_ids=event_ids,
    )


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
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")


def load_google_credentials(token_file: Path) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
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
            token_file.write_text(credentials.to_json(), encoding="utf-8")
    except (RefreshError, ValueError) as exc:
        raise GoogleCalendarError(
            "Google token 無法更新，請重新執行 calendar-auth"
        ) from exc
    if not credentials.valid:
        raise GoogleCalendarError("Google 授權已失效，請重新執行 calendar-auth")
    return credentials


class GoogleCalendarTransport:
    def __init__(
        self,
        calendar_id: str,
        token_file: Path,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.calendar_id = calendar_id
        self.token_file = token_file
        self.credentials = load_google_credentials(token_file)
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "GoogleCalendarTransport":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _events_url(self, event_id: str = "") -> str:
        calendar = quote(self.calendar_id, safe="")
        base = f"{CALENDAR_API_BASE}/calendars/{calendar}/events"
        return f"{base}/{quote(event_id, safe='')}" if event_id else base

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        response = self.client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {self.credentials.token}"},
            json=body,
        )
        if response.status_code not in accepted:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except (json.JSONDecodeError, AttributeError):
                detail = response.text[:300]
            raise GoogleCalendarError(
                f"Google Calendar API {method} 失敗（HTTP {response.status_code}）：{detail}"
            )
        return response

    def verify_access(self) -> int:
        managed_events = 0
        page_token = ""
        while True:
            params = {
                "maxResults": 2500,
                "privateExtendedProperty": "course_robot_managed=true",
                "showDeleted": "false",
            }
            if page_token:
                params["pageToken"] = page_token
            response = self.client.get(
                self._events_url(),
                headers={"Authorization": f"Bearer {self.credentials.token}"},
                params=params,
            )
            if response.status_code != 200:
                self._raise_response("GET", response)
            payload = response.json()
            managed_events += len(payload.get("items", []))
            page_token = str(payload.get("nextPageToken", ""))
            if not page_token:
                return managed_events

    def insert_event(self, event_id: str, body: dict[str, Any]) -> str:
        insert_body = {**body, "id": event_id}
        response = self.client.post(
            self._events_url(),
            headers={"Authorization": f"Bearer {self.credentials.token}"},
            json=insert_body,
        )
        if response.status_code == 409:
            return self.patch_event(event_id, body)
        if response.status_code != 200:
            self._raise_response("POST", response)
        return str(response.json()["id"])

    def patch_event(self, event_id: str, body: dict[str, Any]) -> str:
        response = self.client.patch(
            self._events_url(event_id),
            headers={"Authorization": f"Bearer {self.credentials.token}"},
            json=body,
        )
        if response.status_code in (404, 410):
            source_id = body["extendedProperties"]["private"][
                "course_robot_source_id"
            ]
            return self.insert_event(google_event_id(source_id), body)
        if response.status_code != 200:
            self._raise_response("PATCH", response)
        return str(response.json()["id"])

    def delete_event(self, event_id: str) -> None:
        response = self.client.delete(
            self._events_url(event_id),
            headers={"Authorization": f"Bearer {self.credentials.token}"},
        )
        if response.status_code not in (204, 404, 410):
            self._raise_response("DELETE", response)

    @staticmethod
    def _raise_response(method: str, response: httpx.Response) -> None:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            detail = response.text[:300]
        raise GoogleCalendarError(
            f"Google Calendar API {method} 失敗（HTTP {response.status_code}）：{detail}"
        )
