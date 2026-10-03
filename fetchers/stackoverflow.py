import html
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

from models import Article


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


class StackOverflowFetcher:
    _BASE = "https://api.stackexchange.com/2.3/questions"

    def __init__(self, config: dict):
        keys = config.get("api_keys", {})
        self._key = keys.get("stackoverflow_key", "")
        cfg = config.get("filters", {}).get("stackoverflow", {})
        self._min_votes = cfg.get("min_votes", 10)
        self._days_back = cfg.get("days_back", 7)
        self._max_per_tag = cfg.get("max_per_tag", 3)
        self._session = requests.Session()

    def fetch(self, tags: list) -> list[Article]:
        if not tags:
            return []

        seen_urls: set = set()
        articles: list[Article] = []

        for tag in tags[:4]:
            # Fetch recently active questions of any age, then filter by score
            # in Python. New questions rarely accumulate enough votes in a week
            # to pass a score threshold, so date-gating the API gives nothing.
            params = {
                "site": "stackoverflow",
                "tagged": tag,
                "sort": "activity",
                "order": "desc",
                "filter": "withbody",
                "pagesize": 20,
            }
            if self._key:
                params["key"] = self._key

            try:
                r = self._session.get(self._BASE, params=params, timeout=12)
                r.raise_for_status()
                data = r.json()

                if "backoff" in data:
                    time.sleep(data["backoff"])

                count = 0
                for item in data.get("items", []):
                    if item["score"] < self._min_votes:
                        continue
                    url = item["link"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    body = _strip_html(item.get("body", ""))
                    snippet = " ".join(body.split())[:200]

                    articles.append(
                        Article(
                            title=html.unescape(item["title"]),
                            url=url,
                            score=item["score"],
                            snippet=snippet,
                            source="stackoverflow",
                            score_label=f"▲{item['score']}",
                            tags=item.get("tags", [])[:4],
                            extra="已解答" if item.get("is_answered") else "",
                        )
                    )
                    count += 1
                    if count >= self._max_per_tag:
                        break
                time.sleep(0.4)

            except Exception as e:
                print(f"  [Stack Overflow] tag={tag}: {e}")

        return articles
