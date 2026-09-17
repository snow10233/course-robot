from __future__ import annotations

import unittest
from datetime import datetime

from course_robot.calendar import (
    CourseWindow,
    listing_start,
    next_run_at,
    sync_once,
)
from course_robot.models import TAIPEI
from course_robot.source import SourceError


class ListingWindowTests(unittest.TestCase):
    def test_listing_starts_at_today_midnight(self) -> None:
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        self.assertEqual(listing_start(now), "2026-09-16T00:00:00+08:00")

    def test_listing_window_is_wider_than_deletion_window(self) -> None:
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        self.assertLess(listing_start(now), now.isoformat(timespec="seconds"))


class SchedulerTests(unittest.TestCase):
    def test_next_run_is_today_when_still_ahead(self) -> None:
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        self.assertEqual(
            next_run_at(now, "23:00").isoformat(timespec="seconds"),
            "2026-09-16T23:00:00+08:00",
        )

    def test_next_run_rolls_to_tomorrow(self) -> None:
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        self.assertEqual(
            next_run_at(now, "00:00").isoformat(timespec="seconds"),
            "2026-09-17T00:00:00+08:00",
        )

    def test_invalid_time_is_rejected(self) -> None:
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        for value in ("25:00", "abc", "12"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    next_run_at(now, value)


class SyncOnceTests(unittest.TestCase):
    def test_rebuilds_and_verifies(self) -> None:
        from tests.test_google_calendar import FakeTransport, make_course

        transport = FakeTransport()
        window = CourseWindow(
            courses=(make_course(),), fetched_at="", window_start="", window_end=""
        )
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        result = sync_once(transport, window, now=now)

        self.assertEqual(result.created, 1)
        self.assertTrue(result.verified)
        self.assertEqual(result.attempts, 1)

    def test_retries_when_calendar_does_not_match(self) -> None:
        """審查不通過時會重試，逾次數後回報未通過。"""
        from tests.test_google_calendar import FakeTransport, make_course

        class DropTransport(FakeTransport):
            def insert_event(self, body):
                raise __import__(
                    "course_robot.google_calendar", fromlist=["GoogleCalendarError"]
                ).GoogleCalendarError("模擬失敗")

        window = CourseWindow(
            courses=(make_course(),), fetched_at="", window_start="", window_end=""
        )
        transport = DropTransport()
        now = datetime(2026, 9, 16, 13, 5, tzinfo=TAIPEI)
        result = sync_once(transport, window, now=now, max_attempts=2)

        self.assertFalse(result.verified)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.errors)


class SourceErrorTests(unittest.TestCase):
    def test_source_error_is_a_runtime_error(self) -> None:
        self.assertIsInstance(SourceError("x"), RuntimeError)


if __name__ == "__main__":
    unittest.main()
