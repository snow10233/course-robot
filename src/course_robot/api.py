from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class Peak1ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    reported_total: int
    pages: int


class Peak1Client:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        max_pages: int = 100,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.max_pages = max_pages
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

    def _decode(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
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

    def login(self) -> None:
        response = self.client.post(
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
        if not self.token:
            raise Peak1ApiError("尚未登入")

        records: list[dict[str, Any]] = []
        reported_total = 0
        for page in range(1, self.max_pages + 1):
            response = self.client.get(
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
            page_records = result.get("data") or []
            meta = result.get("meta") or {}
            if not isinstance(page_records, list):
                raise Peak1ApiError(f"課表第 {page} 頁的 data 不是陣列")
            if any(not isinstance(item, dict) for item in page_records):
                raise Peak1ApiError(f"課表第 {page} 頁含有非物件資料")

            try:
                reported_total = int(meta.get("total") or reported_total or 0)
            except (TypeError, ValueError) as exc:
                raise Peak1ApiError("課表 meta.total 不是整數") from exc
            records.extend(page_records)

            if reported_total and len(records) >= reported_total:
                return FetchResult(records[:reported_total], reported_total, page)
            if not page_records:
                return FetchResult(records, reported_total or len(records), page)

        raise Peak1ApiError(
            f"課表超過安全上限 {self.max_pages} 頁，為避免把不完整快照寫入資料庫，已停止同步"
        )

