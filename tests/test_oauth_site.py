from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = PROJECT_ROOT / "deploy" / "oauth-site"


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.scripts = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(str(attributes["href"]))
        if tag == "link" and attributes.get("href"):
            self.links.append(str(attributes["href"]))
        if tag == "script":
            self.scripts += 1


class OAuthSiteTests(unittest.TestCase):
    def test_required_pages_and_local_links_exist(self) -> None:
        pages = [
            SITE_ROOT / "index.html",
            SITE_ROOT / "privacy" / "index.html",
            SITE_ROOT / "terms" / "index.html",
        ]

        for page in pages:
            self.assertTrue(page.is_file(), page)
            parser = LinkCollector()
            parser.feed(page.read_text(encoding="utf-8"))
            self.assertEqual(parser.scripts, 0, page)

            for link in parser.links:
                if not link.startswith("/"):
                    continue
                target = SITE_ROOT / link.lstrip("/")
                if link.endswith("/"):
                    target /= "index.html"
                self.assertTrue(target.is_file(), f"{page}: {link}")

    def test_privacy_page_describes_google_data_use(self) -> None:
        privacy = (SITE_ROOT / "privacy" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Google Calendar", privacy)
        self.assertIn("OAuth", privacy)
        self.assertIn("Google API Services User Data Policy", privacy)
        self.assertIn("Limited Use", privacy)

    def test_domain_is_consistent(self) -> None:
        expected_domain = "course.zhidian.snowbox.dev"
        worker_config = (PROJECT_ROOT / "deploy" / "wrangler.jsonc").read_text(
            encoding="utf-8"
        )
        self.assertIn(expected_domain, worker_config)

        for page in SITE_ROOT.rglob("*.html"):
            self.assertIn(expected_domain, page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
