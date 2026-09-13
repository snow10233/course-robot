from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    base_url: str
    database: Path
    report_html: Path
    report_csv: Path
    reports_enabled: bool
    google_calendar_enabled: bool
    google_calendar_id: str
    google_oauth_client: Path
    google_oauth_token: Path

    @classmethod
    def load(cls, project_dir: Path | None = None) -> "Settings":
        root = project_dir or Path.cwd()
        load_dotenv(root / ".env", encoding="utf-8-sig")
        return cls(
            username=os.getenv("PEAK1_USERNAME", "").strip(),
            password=os.getenv("PEAK1_PASSWORD", ""),
            base_url=os.getenv("PEAK1_BASE_URL", "https://tutor.peak1.com.tw").rstrip("/"),
            database=Path(os.getenv("PEAK1_DATABASE", "data/course-robot.db")),
            report_html=Path(os.getenv("PEAK1_REPORT_HTML", "output/schedule.html")),
            report_csv=Path(os.getenv("PEAK1_REPORT_CSV", "output/schedule.csv")),
            reports_enabled=os.getenv(
                "COURSE_ROBOT_REPORTS_ENABLED", "true"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            google_calendar_enabled=os.getenv(
                "GOOGLE_CALENDAR_ENABLED", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary").strip(),
            google_oauth_client=Path(
                os.getenv("GOOGLE_OAUTH_CLIENT", "credentials.json")
            ),
            google_oauth_token=Path(
                os.getenv("GOOGLE_OAUTH_TOKEN", "data/google-token.json")
            ),
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
