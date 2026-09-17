from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """執行期設定。

    `.env` 是唯一來源：`override=True` 讓檔案內容勝過行程環境變數。
    這是刻意的——否則容器或 shell 裡的殘留變數會靜默蓋掉 `.env`，
    造成「改了設定卻沒生效」的錯誤。
    """

    username: str
    password: str
    base_url: str

    google_calendar_enabled: bool
    google_calendar_id: str
    google_oauth_client: Path
    google_oauth_token: Path

    calendar_sync_at: str

    @classmethod
    def load(cls, project_dir: Path | None = None) -> "Settings":
        root = project_dir or Path.cwd()
        load_dotenv(root / ".env", encoding="utf-8-sig", override=True)
        return cls(
            username=os.getenv("PEAK1_USERNAME", "").strip(),
            password=os.getenv("PEAK1_PASSWORD", ""),
            base_url=os.getenv("PEAK1_BASE_URL", "https://tutor.peak1.com.tw").rstrip("/"),
            google_calendar_enabled=_as_bool("GOOGLE_CALENDAR_ENABLED", False),
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "").strip(),
            google_oauth_client=Path(
                os.getenv("GOOGLE_OAUTH_CLIENT", "credentials.json")
            ),
            google_oauth_token=Path(
                os.getenv("GOOGLE_OAUTH_TOKEN", "data/google-token.json")
            ),
            calendar_sync_at=os.getenv("CALENDAR_SYNC_AT", "00:00").strip(),
        )

    def require_credentials(self) -> None:
        missing = []
        if not self.username:
            missing.append("PEAK1_USERNAME")
        if not self.password:
            missing.append("PEAK1_PASSWORD")
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"缺少登入設定：{names}。請複製 .env.example 為 .env 後填入。")

    def require_google_calendar(self) -> None:
        if not self.google_calendar_id:
            raise ValueError("GOOGLE_CALENDAR_ID 不可為空")


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必須是整數，目前是 {raw!r}") from exc
