from __future__ import annotations

import json
import unittest

import httpx

from course_robot.api import Peak1Client


class ApiTests(unittest.TestCase):
    def test_login_and_paginated_schedule(self) -> None:
        seen_authorization = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/teacher/login":
                return httpx.Response(
                    200,
                    json={
                        "code": 1,
                        "result": {
                            "access_token": "test-token",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        },
                    },
                )
            if request.url.path == "/api/teacher/daily-schedule":
                seen_authorization.append(request.headers.get("authorization"))
                page = int(request.url.params["page"])
                record = {
                    "class_id": page,
                    "class_time": f"2026-09-2{page} 17:00:00",
                    "students": [],
                }
                return httpx.Response(
                    200,
                    content=json.dumps(
                        {"code": 1, "result": {"data": [record], "meta": {"total": 2}}}
                    ).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(
            transport=transport,
            base_url="https://tutor.peak1.com.tw",
        )
        client = Peak1Client(
            "https://tutor.peak1.com.tw",
            "teacher",
            "secret",
            client=http_client,
        )
        client.login()
        result = client.fetch_all_schedule()

        self.assertEqual(result.reported_total, 2)
        self.assertEqual(result.pages, 2)
        self.assertEqual([record["class_id"] for record in result.records], [1, 2])
        self.assertEqual(seen_authorization, ["Bearer test-token", "Bearer test-token"])


if __name__ == "__main__":
    unittest.main()

