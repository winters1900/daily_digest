import argparse
import getpass
import json
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .delivery import render, save_report, send_wechat
from .processing import classify_ai, match_rule, rule_result, SENSITIVE_LINE
from .secrets import get_secret, put_secret
from .sources import fetch_gmail, fetch_imap, gmail_credentials
from .state import State


ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Shanghai")


def config():
    with (ROOT / "mail_digest.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def run():
    cfg = config()
    now = datetime.now(timezone.utc)
    day = now.astimezone(TZ).date().isoformat()
    state = State(ROOT / cfg["output"]["state_db"])
    failures = []
    try:
        for account, settings in cfg["accounts"].items():
            if not settings.get("enabled", True):
                continue
            if not settings.get("address"):
                failures.append(f"{account}: 未填写邮箱地址")
                continue
            previous = state.scan_time(account)
            since = datetime.fromisoformat(previous) - timedelta(days=1) if previous else now - timedelta(hours=24)
            try:
                if account == "gmail":
                    mails = fetch_gmail(account, since, int(cfg["ai"]["excerpt_chars"]), settings["address"])
                else:
                    mails = fetch_imap(account, settings, since, int(cfg["ai"]["excerpt_chars"]))
                state.add_messages(account, mails, now.isoformat())
                print(f"{account}: 读取 {len(mails)} 封")
            except Exception as exc:
                failures.append(f"{account}: {type(exc).__name__}")

        ai_candidates = []
        for account, message_id, mail in state.pending():
            # 对验证码邮件不生成摘要，也不提交第三方模型。
            if SENSITIVE_LINE.search(mail["subject"]):
                state.classify(account, message_id, {"important": False})
                continue
            category = match_rule(mail, cfg["rules"])
            if category:
                state.classify(account, message_id, rule_result(mail, category))
                continue
            ai_candidates.append((account, message_id, mail))

        max_ai = int(cfg["ai"].get("max_messages_per_run", 100))
        for account, message_id, mail in ai_candidates[:max_ai]:
            try:
                state.classify(account, message_id, classify_ai(mail, cfg["ai"]))
            except Exception as exc:
                failures.append(f"DeepSeek: {type(exc).__name__}")
                break

        items = state.undelivered()
        if items and not state.pushed_today(day):
            content = render(day, items)
            report = save_report(ROOT / cfg["output"]["directory"], day, content)
            print(f"日报已写入 {report}")
            try:
                send_wechat(day, content)
                state.mark_sent(day, now.isoformat(), items)
                print("微信推送成功")
            except Exception as exc:
                failures.append(f"Server酱: {type(exc).__name__}")
        elif items:
            print("今日已推送；新增邮件留待明日")
        else:
            print("无待推送的重要邮件")
    finally:
        state.close()
    for failure in failures:
        print(f"警告：{failure}", file=sys.stderr)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description="三邮箱重要邮件日报")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="读取、分类并推送")
    set_secret = sub.add_parser("set-secret", help="输入并保存密钥到 macOS 钥匙串")
    set_secret.add_argument("name", choices=["nju_password", "qq_password", "deepseek_key", "serverchan_key"])
    client = sub.add_parser("gmail-client", help="导入 Google 桌面 OAuth 客户端 JSON")
    client.add_argument("path", type=Path)
    sub.add_parser("gmail-auth", help="在浏览器完成 Gmail 只读授权")
    check = sub.add_parser("check", help="测试邮箱连接，不推送")
    check.add_argument("account", choices=["nju", "qq", "gmail", "all"])
    sub.add_parser("test-push", help="发送一条无邮件内容的测试消息")
    sub.add_parser("install-schedule", help="验证配置后安装每天10点的 macOS 定时任务")
    args = parser.parse_args()
    if args.command == "set-secret":
        put_secret(args.name, getpass.getpass(f"输入 {args.name}: "))
        print("已存入钥匙串")
    elif args.command == "gmail-client":
        data = json.loads(args.path.read_text(encoding="utf-8"))
        if "installed" not in data:
            parser.error("需要 Google 桌面应用 OAuth 客户端 JSON")
        put_secret("gmail_client", json.dumps(data))
        print("客户端配置已存入钥匙串；原文件请移出项目目录")
    elif args.command == "gmail-auth":
        gmail_credentials(interactive=True)
        print("Gmail 只读授权完成")
    elif args.command == "check":
        cfg = config()
        names = ([name for name, account in cfg["accounts"].items() if account.get("enabled", True)]
                 if args.account == "all" else [args.account])
        failed = False
        for name in names:
            settings = cfg["accounts"][name]
            if not settings.get("enabled", True):
                print(f"{name}: 已暂停接入")
                continue
            if not settings.get("address"):
                print(f"{name}: 未填写邮箱地址")
                failed = True
                continue
            try:
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                if name == "gmail":
                    mails = fetch_gmail(name, since, 100, settings["address"])
                else:
                    mails = fetch_imap(name, settings, since, 100)
                print(f"{name}: 连接成功，最近24小时 {len(mails)} 封")
            except Exception as exc:
                print(f"{name}: 连接失败（{type(exc).__name__}）", file=sys.stderr)
                failed = True
        sys.exit(1 if failed else 0)
    elif args.command == "test-push":
        send_wechat(datetime.now(TZ).date().isoformat(), "邮件日报连接测试。此消息不包含邮件内容。")
        print("测试消息已发送")
    elif args.command == "install-schedule":
        cfg = config()
        active = {name: account for name, account in cfg["accounts"].items() if account.get("enabled", True)}
        if not active or any(not account.get("address") for account in active.values()):
            parser.error("请先填写启用邮箱的地址并运行 check all")
        required = ["deepseek_key", "serverchan_key"]
        required += [f"{name}_password" for name in active if name != "gmail"]
        if "gmail" in active:
            required.append("gmail_token")
        for name in required:
            get_secret(name)
        python = ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            parser.error("请先创建 .venv 并安装 requirements.txt")
        state_dir = ROOT / "state"
        state_dir.mkdir(exist_ok=True)
        state_dir.chmod(0o700)
        for log_name in ("mail.log", "mail-error.log"):
            log_path = state_dir / log_name
            log_path.touch(mode=0o600, exist_ok=True)
            log_path.chmod(0o600)
        label = "com.daily-digest.mail"
        plist = {
            "Label": label,
            "ProgramArguments": [str(python), "-m", "mail_digest", "run"],
            "WorkingDirectory": str(ROOT),
            "StartCalendarInterval": [
                {"Hour": 10, "Minute": 0},
                {"Hour": 10, "Minute": 30},
                {"Hour": 12, "Minute": 0},
                {"Hour": 18, "Minute": 0},
            ],
            "RunAtLoad": True,
            "StandardOutPath": str(state_dir / "mail.log"),
            "StandardErrorPath": str(state_dir / "mail-error.log"),
        }
        target_dir = Path.home() / "Library" / "LaunchAgents"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{label}.plist"
        target.write_bytes(plistlib.dumps(plist))
        target.chmod(0o600)
        domain = f"gui/{__import__('os').getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        print(f"已安装每天北京时间10点定时任务：{target}")
    else:
        sys.exit(run())


if __name__ == "__main__":
    main()
