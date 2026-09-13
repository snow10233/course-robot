from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from course_robot.cli import main
from course_robot.config import Settings
from course_robot.google_calendar import authorize_google_calendar


class MigrationTests(unittest.TestCase):
    def test_windows_bom_and_crlf_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_bytes(
                b"\xef\xbb\xbfPEAK1_USERNAME=teacher\r\nPEAK1_PASSWORD=secret\r\n"
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.load(root)
            self.assertEqual(settings.username, "teacher")
            self.assertEqual(settings.password, "secret")

    def test_cli_passes_no_browser_to_authorization(self) -> None:
        with (
            patch("course_robot.cli._settings") as settings,
            patch("course_robot.cli.authorize_google_calendar") as authorize,
        ):
            self.assertEqual(main(["calendar-auth", "--no-browser"]), 0)
            authorize.assert_called_once_with(
                settings.return_value.google_oauth_client,
                settings.return_value.google_oauth_token,
                open_browser=False,
            )

    def test_manual_oauth_still_saves_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "client.json"
            client.write_text("{}", encoding="utf-8")
            token = root / "data" / "token.json"
            flow = Mock()
            flow.run_local_server.return_value.to_json.return_value = '{"token":"test"}'
            with patch(
                "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                return_value=flow,
            ):
                authorize_google_calendar(client, token, open_browser=False)
            flow.run_local_server.assert_called_once_with(port=0, open_browser=False)
            self.assertEqual(token.read_text(encoding="utf-8"), '{"token":"test"}')
