from datetime import datetime
from pathlib import Path

import requests

from .secrets import get_secret


ORDER = ("求职", "学业", "其他待办")


def render(today, items):
    lines = [f"# 重要邮件日报 · {today}", ""]
    for category in ORDER:
        selected = [item for item in items if item["category"] == category]
        if not selected:
            continue
        lines.extend([f"## {category}（{len(selected)}）", ""])
        for item in selected:
            mail = item["mail"]
            subject = mail["subject"].replace("\n", " ").replace("\r", " ")
            sender = mail["sender"].replace("\n", " ").replace("\r", " ")
            lines.append(f"- **[{mail['account']}] {subject}** — {sender}")
            lines.append(f"  {item['summary']}")
            if item["deadline"]:
                lines.append(f"  截止：{item['deadline']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_report(directory, today, content):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    target = path / f"{today}.md"
    target.touch(mode=0o600, exist_ok=True)
    target.chmod(0o600)
    target.write_text(content, encoding="utf-8")
    return target


def send_wechat(today, content, title=None):
    key = get_secret("serverchan_key")
    try:
        response = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title or f"重要邮件日报 {today}", "desp": content},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        # 请求异常可能携带含 SendKey 的完整 URL，不写入日志。
        raise RuntimeError("Server酱连接失败或HTTP请求被拒绝") from None
    if response.json().get("code") != 0:
        raise RuntimeError("Server酱未接受推送")
