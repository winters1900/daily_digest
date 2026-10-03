from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import feedparser

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


class SubstackFetcher:
    def __init__(self, config: dict):
        cfg = config.get("filters", {}).get("substack", {})
        self._days_back = cfg.get("days_back", 14)
        self._max_per_feed = cfg.get("max_per_feed", 2)

    def fetch(self, feeds: list) -> list[Article]:
        if not feeds:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._days_back)
        articles: list[Article] = []

        for feed_cfg in feeds:
            url = feed_cfg.get("url", "")
            name = feed_cfg.get("name", url)
            if not url:
                continue

            try:
                parsed = feedparser.parse(url)
                count = 0
                for entry in parsed.entries:
                    # feedparser gives published_parsed as UTC time.struct_time
                    pub = None
                    if getattr(entry, "published_parsed", None):
                        pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                    if pub and pub < cutoff:
                        continue

                    raw_summary = entry.get("summary", "")
                    snippet = " ".join(_strip_html(raw_summary).split())[:200]

                    articles.append(
                        Article(
                            title=entry.get("title", "Untitled"),
                            url=entry.get("link", ""),
                            score=0,
                            snippet=snippet,
                            source="substack",
                            score_label="📰",
                            extra=name,
                            published=pub,
                        )
                    )
                    count += 1
                    if count >= self._max_per_feed:
                        break

            except Exception as e:
                print(f"  [Substack] {name}: {e}")

        return articles
