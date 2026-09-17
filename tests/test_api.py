from __future__ import annotations

import json
import unittest

import httpx

from course_robot.api import Peak1ApiError, Peak1Client


def make_client(handler, **kwargs) -> Peak1Client:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://tutor.peak1.com.tw",
    )
    return Peak1Client(
        "https://tutor.peak1.com.tw",
        "teacher",
        "secret",
        client=http_client,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def login_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": 1,
            "result": {"access_token": "test-token", "token_type": "Bearer"},
        },
    )


def schedule_page(records: list, total: int) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(
            {"code": 1, "result": {"data": records, "meta": {"total": total}}}
        ).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def record(class_id: int) -> dict:
    return {
        "class_id": class_id,
        "class_time": f"2026-09-{(class_id % 27) + 1:02d} 17:00:00",
        "students": [],
    }


class ApiTests(unittest.TestCase):
    def test_login_and_paginated_schedule(self) -> None:
        seen_authorization: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            seen_authorization.append(request.headers.get("authorization"))
            page = int(request.url.params["page"])
            return schedule_page([record(page)], total=2)

        client = make_client(handler)
        client.login()
        result = client.fetch_all_schedule()

        self.assertEqual(result.reported_total, 2)
        self.assertEqual(result.pages, 2)
        self.assertEqual([item["class_id"] for item in result.records], [1, 2])
        self.assertEqual(seen_authorization, ["Bearer test-token", "Bearer test-token"])
        self.assertTrue(result.fetched_at)

    def test_incomplete_pagination_is_rejected(self) -> None:
        """宣稱 2 筆但第 2 頁是空的 → 必須整批拒絕，不能回傳 1 筆。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            page = int(request.url.params["page"])
            return schedule_page([record(1)] if page == 1 else [], total=2)

        client = make_client(handler)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "不完整"):
            client.fetch_all_schedule()

    def test_total_changing_between_pages_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            page = int(request.url.params["page"])
            total = 5 if page == 1 else 9
            return schedule_page([record(page)], total=total)

        client = make_client(handler)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "分頁總數不一致"):
            client.fetch_all_schedule()

    def test_extra_records_beyond_total_are_rejected(self) -> None:
        """回傳筆數多於 meta.total 也要視為異常，不可靜默截斷。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            return schedule_page([record(1), record(2)], total=1)

        client = make_client(handler)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "筆數不符"):
            client.fetch_all_schedule()

    def test_missing_total_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            return httpx.Response(200, json={"code": 1, "result": {"data": []}})

        client = make_client(handler)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "meta.total"):
            client.fetch_all_schedule()

    def test_non_object_record_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            return schedule_page(["壞資料"], total=1)

        client = make_client(handler)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "非物件"):
            client.fetch_all_schedule()

    def test_page_limit_is_enforced(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            page = int(request.url.params["page"])
            return schedule_page([record(page)], total=999)

        client = make_client(handler, max_pages=3)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "安全上限"):
            client.fetch_all_schedule()

    def test_transient_failure_is_retried(self) -> None:
        calls = {"schedule": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            calls["schedule"] += 1
            if calls["schedule"] == 1:
                return httpx.Response(503, text="backend error")
            return schedule_page([record(1)], total=1)

        client = make_client(handler, attempts=3)
        client.login()
        result = client.fetch_all_schedule()

        self.assertEqual(calls["schedule"], 2)
        self.assertEqual(len(result.records), 1)

    def test_retries_are_bounded(self) -> None:
        calls = {"schedule": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            calls["schedule"] += 1
            return httpx.Response(429, text="rate limited")

        client = make_client(handler, attempts=3)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "已重試 3 次"):
            client.fetch_all_schedule()
        self.assertEqual(calls["schedule"], 3)

    def test_network_error_is_retried_and_reported(self) -> None:
        calls = {"schedule": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            calls["schedule"] += 1
            raise httpx.ConnectError("connection refused", request=request)

        client = make_client(handler, attempts=2)
        client.login()
        with self.assertRaisesRegex(Peak1ApiError, "連線老師站失敗"):
            client.fetch_all_schedule()
        self.assertEqual(calls["schedule"], 2)

    def test_non_retryable_status_fails_immediately(self) -> None:
        calls = {"schedule": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return login_response()
            calls["schedule"] += 1
            return httpx.Response(404, text="not found")

        client = make_client(handler, attempts=3)
        client.login()
        with self.assertRaises(Peak1ApiError):
            client.fetch_all_schedule()
        self.assertEqual(calls["schedule"], 1)

    def test_login_failure_message_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "message": "帳號或密碼錯誤"})

        client = make_client(handler)
        with self.assertRaisesRegex(Peak1ApiError, "帳號或密碼錯誤"):
            client.login()

    def test_fetch_requires_login(self) -> None:
        client = make_client(lambda request: login_response())
        with self.assertRaisesRegex(Peak1ApiError, "尚未登入"):
            client.fetch_all_schedule()


if __name__ == "__main__":
    unittest.main()
