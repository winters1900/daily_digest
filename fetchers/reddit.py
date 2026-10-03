import time
from html.parser import HTMLParser

import feedparser

from models import Article

# Reddit's OAuth API requires username+password for "script" apps.
# RSS feeds are publicly accessible and need no auth — we use those instead.


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


class RedditFetcher:
    _RSS = "https://www.reddit.com/r/{sub}/top.rss"

    def __init__(self, config: dict):
        cfg = config.get("filters", {}).get("reddit", {})
        self._time_filter = cfg.get("time_filter", "week")
        self._max_per_sub = cfg.get("max_per_subreddit", 3)
        user_agent = (
            config.get("api_keys", {}).get("reddit_user_agent")
            or "python:DailyDigest:v1.0 (personal use)"
        )
        # feedparser respects the User-Agent set via the request_headers arg
        self._ua = user_agent

    def fetch(self, subreddits: list) -> list[Article]:
        if not subreddits:
            return []

        articles: list[Article] = []

        for sub_name in subreddits:
            url = self._RSS.format(sub=sub_name)
            try:
                parsed = feedparser.parse(
                    url,
                    request_headers={"User-Agent": self._ua},
                    agent=self._ua,
                )
                if parsed.get("status", 200) >= 400:
                    print(f"  [Reddit] r/{sub_name}: HTTP {parsed.get('status')}")
                    continue

                count = 0
                for entry in parsed.entries:
                    # Reddit RSS entries use <content:encoded> for the post body
                    raw = (
                        entry.get("content", [{}])[0].get("value", "")
                        or entry.get("summary", "")
                    )
                    snippet = " ".join(_strip_html(raw).split())[:200]

                    link = entry.get("link", "")
                    title = entry.get("title", "")

                    articles.append(
                        Article(
                            title=title,
                            url=link,
                            score=0,
                            snippet=snippet,
                            source="reddit",
                            score_label="🔥",
                            extra=f"r/{sub_name}",
                        )
                    )
                    count += 1
                    if count >= self._max_per_sub:
                        break

            except Exception as e:
                print(f"  [Reddit] r/{sub_name}: {e}")
            time.sleep(1.5)

        return articles
