from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import httpx

from course_robot.models import TAIPEI


class Peak1ApiError(RuntimeError):
    """老師站 API 的錯誤。

    分成兩類：
    - retryable：暫時性問題（網路、429、5xx），已由客戶端退避重試
    - 其餘：設定或資料問題，重試不會成功
    """

    retryable = False

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Peak1RetryableError(Peak1ApiError):
    retryable = True


@dataclass(frozen=True)
class FetchResult:
    records: tuple[dict[str, Any], ...]
    reported_total: int
    pages: int
    fetched_at: str


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class Peak1Client:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        max_pages: int = 100,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.max_pages = max_pages
        self.attempts = max(1, attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self._sleep = sleep
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
        )
        self.token = ""

    def __enter__(self) -> "Peak1Client":
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_client:
            self.client.close()

    # -- 請求層 ---------------------------------------------------------

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """送出請求，對暫時性失敗做有限指數退避。"""
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = Peak1RetryableError(f"連線老師站失敗：{exc}")
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                last_error = Peak1RetryableError(
                    f"老師站暫時無法處理（HTTP {response.status_code}）"
                )
            if attempt < self.attempts:
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        assert last_error is not None
        raise Peak1ApiError(
            f"{last_error}（已重試 {self.attempts} 次）", retryable=True
        )

    def _decode(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Peak1ApiError(f"{operation} HTTP 失敗：{exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise Peak1ApiError(f"{operation} 未回傳 JSON，可能是登入頁或網站改版") from exc
        if not isinstance(payload, dict):
            raise Peak1ApiError(f"{operation} 回傳格式不是物件")
        if payload.get("code") not in (1, True, "1"):
            message = payload.get("message") or payload.get("msg") or "未知錯誤"
            raise Peak1ApiError(f"{operation} 失敗：{message}")
        return payload

    # -- 對外操作 -------------------------------------------------------

    def login(self) -> None:
        response = self._send(
            "POST",
            "/api/teacher/login",
            files={
                "username": (None, self.username),
                "password": (None, self.password),
            },
            headers={"locale": "zh-TW"},
        )
        payload = self._decode(response, "登入")
        result = payload.get("result") or {}
        token = str(result.get("access_token") or "").strip()
        token_type = str(result.get("token_type") or "Bearer").strip()
        if not token:
            raise Peak1ApiError("登入成功回應中沒有 access_token")
        self.token = f"{token_type} {token}"

    def fetch_all_schedule(self) -> FetchResult:
        """抓取完整課表。

        分頁驗證是這個方法的關鍵責任：只要有任何跡象顯示回應不完整
        （total 前後不一致、筆數不足、頁數超限），就整批放棄並拋錯。
        寧可這一輪不更新，也不要把缺頁當成「課程被取消」。
        """
        if not self.token:
            raise Peak1ApiError("尚未登入")

        records: list[dict[str, Any]] = []
        reported_total: int | None = None
        pages = 0

        for page in range(1, self.max_pages + 1):
            response = self._send(
                "GET",
                "/api/teacher/daily-schedule",
                params={"page": page},
                headers={
                    "Authorization": self.token,
                    "user-account": self.username,
                    "locale": "zh-TW",
                },
            )
            payload = self._decode(response, f"讀取課表第 {page} 頁")
            result = payload.get("result") or {}
            page_records = result.get("data")
            meta = result.get("meta") or {}
            pages = page
            if not isinstance(page_records, list):
                raise Peak1ApiError(f"課表第 {page} 頁的 data 不是陣列")
            if any(not isinstance(item, dict) for item in page_records):
                raise Peak1ApiError(f"課表第 {page} 頁含有非物件資料")

            total = self._page_total(meta, page)
            if reported_total is None:
                reported_total = total
            elif total != reported_total:
                raise Peak1ApiError(
                    f"課表分頁總數不一致：第 1 頁為 {reported_total}，"
                    f"第 {page} 頁為 {total}；已中止本次同步"
                )

            records.extend(page_records)

            if len(records) >= reported_total:
                break
            if not page_records:
                raise Peak1ApiError(
                    f"課表回應不完整：宣稱 {reported_total} 筆，"
                    f"但第 {page} 頁為空且只收到 {len(records)} 筆；已中止本次同步"
                )
        else:
            raise Peak1ApiError(
                f"課表超過安全上限 {self.max_pages} 頁，為避免把不完整快照寫入，已停止同步"
            )

        if reported_total is None or reported_total <= 0:
            raise Peak1ApiError("課表回應沒有有效的 meta.total；已中止本次同步")
        if len(records) != reported_total:
            raise Peak1ApiError(
                f"課表筆數不符：宣稱 {reported_total} 筆，實際收到 {len(records)} 筆"
            )

        return FetchResult(
            records=tuple(records),
            reported_total=reported_total,
            pages=pages,
            fetched_at=datetime.now(TAIPEI).isoformat(timespec="seconds"),
        )

    @staticmethod
    def _page_total(meta: Any, page: int) -> int:
        if not isinstance(meta, dict):
            raise Peak1ApiError(f"課表第 {page} 頁的 meta 不是物件")
        raw = meta.get("total")
        if raw is None:
            raise Peak1ApiError(f"課表第 {page} 頁缺少 meta.total")
        try:
            total = int(raw)
        except (TypeError, ValueError) as exc:
            raise Peak1ApiError(f"課表第 {page} 頁的 meta.total 不是整數") from exc
        if total < 0:
            raise Peak1ApiError(f"課表第 {page} 頁的 meta.total 為負數")
        return total
