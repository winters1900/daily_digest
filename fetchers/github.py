from datetime import datetime, timedelta, timezone
import os

import requests

from models import Article


class GitHubFetcher:
    _SEARCH = "https://api.github.com/search/repositories"

    def __init__(self, config: dict):
        token = os.environ.get("DAILY_DIGEST_GITHUB_TOKEN", "")
        cfg = config.get("filters", {}).get("github", {})
        self._min_stars = cfg.get("min_stars", 100)
        self._days_back = cfg.get("days_back", 7)
        self._max_per_topic = cfg.get("max_per_topic", 4)

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._session = requests.Session()
        self._session.headers.update(headers)

    def fetch(self, topics: list) -> list[Article]:
        if not topics:
            return []

        since = (
            datetime.now(timezone.utc) - timedelta(days=self._days_back)
        ).strftime("%Y-%m-%d")

        seen_urls: set = set()
        articles: list[Article] = []

        for topic in topics[:3]:
            query = (
                f"topic:{topic} pushed:>{since} stars:>={self._min_stars}"
            )
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": self._max_per_topic,
            }
            try:
                r = self._session.get(self._SEARCH, params=params, timeout=12)
                r.raise_for_status()
                for repo in r.json().get("items", []):
                    url = repo["html_url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    stars = repo["stargazers_count"]
                    stars_str = f"{stars / 1000:.1f}k" if stars >= 1000 else str(stars)
                    lang = repo.get("language") or ""

                    articles.append(
                        Article(
                            title=repo["full_name"],
                            url=url,
                            score=stars,
                            snippet=(repo.get("description") or "")[:200],
                            source="github",
                            score_label=f"⭐ {stars_str}",
                            tags=[lang] if lang else [],
                            extra=lang,
                        )
                    )
            except Exception as e:
                print(f"  [GitHub] topic={topic}: {e}")

        # Sort by stars, deduplicate already done; keep top N overall
        articles.sort(key=lambda a: a.score, reverse=True)
        return articles[: self._max_per_topic * 2]
