import os
from datetime import date

from models import Article

_SOURCE_HEADERS = {
    "stackoverflow": "### Stack Overflow 精选",
    "github": "### GitHub 热门",
    "reddit": "### Reddit 热帖",
    "substack": "### Substack 精读",
}

_SOURCE_ORDER = ["stackoverflow", "github", "reddit", "substack"]


class Reporter:
    def __init__(self, config: dict):
        self._config = config
        self._out_dir = config.get("output", {}).get("directory", "./reports")

    def write(self, today: date, all_articles: dict[str, list[Article]]) -> str:
        os.makedirs(self._out_dir, exist_ok=True)
        filepath = os.path.join(self._out_dir, f"{today.strftime('%Y-%m-%d')}.md")

        total = sum(len(v) for v in all_articles.values())
        lines: list[str] = [
            f"# 技术日报 · {today.strftime('%Y-%m-%d')}",
            "",
        ]

        topics = self._config.get("topics", {})
        for topic_key, topic_cfg in topics.items():
            articles = all_articles.get(topic_key, [])
            if not articles:
                continue

            label = topic_cfg.get("label", topic_key)
            lines += [f"## {label}", ""]

            by_source: dict[str, list[Article]] = {}
            for a in articles:
                by_source.setdefault(a.source, []).append(a)

            for source in _SOURCE_ORDER:
                items = by_source.get(source, [])
                if not items:
                    continue
                lines.append(_SOURCE_HEADERS[source])
                lines.append("")
                for a in items:
                    _render_article(lines, a)
                lines.append("")

        lines += [
            "---",
            f"*生成于 {today.strftime('%Y-%m-%d')} | 共抓取 {total} 条*",
        ]

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath


def _render_article(lines: list[str], a: Article) -> None:
    score_part = f"{a.score_label} " if a.score_label else ""
    extra_part = f" — {a.extra}" if a.extra else ""
    tags_part = " ".join(f"`{t}`" for t in a.tags[:3]) if a.tags else ""

    lines.append(f"- {score_part}**[{a.title}]({a.url})**{extra_part}")
    if tags_part:
        lines.append(f"  {tags_part}")
    if a.snippet:
        lines.append(f"  > {a.snippet}…")
