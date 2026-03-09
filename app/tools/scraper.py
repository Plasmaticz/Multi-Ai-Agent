from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup


class PageFetcher:
    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds

    def fetch_page(self, url: str, max_chars: int = 4000) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = " ".join(soup.stripped_strings)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
