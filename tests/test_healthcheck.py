from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from course_robot.database import connect, replace_snapshot
from course_robot.healthcheck import database_health
from course_robot.models import TAIPEI


class HealthcheckTests(unittest.TestCase):
    def test_recent_sync_is_healthy_and_stale_sync_is_not(self) -> None:
        now = datetime(2026, 8, 28, 12, 0, tzinfo=TAIPEI)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "course-robot.db"
            connection = connect(database)
            try:
                replace_snapshot(connection, [], observed_at=now)
            finally:
                connection.close()

            healthy, message = database_health(database, 3600, now=now)
            self.assertTrue(healthy)
            self.assertIn("課程 0 筆", message)

            stale, stale_message = database_health(
                database,
                3600,
                now=now + timedelta(seconds=3601),
            )
            self.assertFalse(stale)
            self.assertIn("上限 3600 秒", stale_message)

    def test_missing_database_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            healthy, message = database_health(
                Path(directory) / "missing.db",
                3600,
            )
        self.assertFalse(healthy)
        self.assertIn("找不到 SQLite", message)
