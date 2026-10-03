import sys
from datetime import date

import yaml

from fetchers.github import GitHubFetcher
from fetchers.reddit import RedditFetcher
from fetchers.stackoverflow import StackOverflowFetcher
from fetchers.substack import SubstackFetcher
from reporter import Reporter


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()

    so = StackOverflowFetcher(config)
    gh = GitHubFetcher(config)
    rd = RedditFetcher(config)
    ss = SubstackFetcher(config)

    all_articles: dict = {}
    topics: dict = config.get("topics", {})

    for topic_key, topic_cfg in topics.items():
        label = topic_cfg.get("label", topic_key)
        print(f"  📂 {label}...")
        articles = []
        articles += so.fetch(topic_cfg.get("stackoverflow_tags", []))
        articles += gh.fetch(topic_cfg.get("github_topics", []))
        articles += rd.fetch(topic_cfg.get("reddit_subreddits", []))
        articles += ss.fetch(topic_cfg.get("substack_feeds", []))
        all_articles[topic_key] = articles

    global_feeds = config.get("substack_feeds_global", [])
    if global_feeds:
        print("  📂 综合阅读 · Substack...")
        all_articles["_global"] = ss.fetch(global_feeds)
        config["topics"]["_global"] = {"label": "综合阅读 · Substack"}

    reporter = Reporter(config)
    filepath = reporter.write(date.today(), all_articles)

    total = sum(len(v) for v in all_articles.values())
    print(f"\n✅ 共抓取 {total} 条 → {filepath}")
    print(f'   打开: open "{filepath}"')


if __name__ == "__main__":
    # Allow running from any directory
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    if "--legacy" in sys.argv:
        sys.argv.remove("--legacy")
        main()
    else:
        from tech_digest.__main__ import main as tech_main
        raise SystemExit(tech_main())
