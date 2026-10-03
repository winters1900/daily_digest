import json
import ssl
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mail_digest import __main__ as app
from mail_digest.delivery import render
from mail_digest.processing import classify_ai, match_rule, rule_result, safe_excerpt
from mail_digest.state import State
from mail_digest.sources import fetch_imap


def mail(account, mid, subject, excerpt="", sender="notice@example.com"):
    return {"account": account, "id": mid, "subject": subject,
            "excerpt": excerpt, "sender": sender,
            "received": "2026-09-27T00:00:00+00:00"}


class ProcessingTests(unittest.TestCase):
    @patch("mail_digest.sources.time.sleep")
    @patch("mail_digest.sources._fetch_imap_once")
    def test_imap_retries_transient_tls_close(self, fetch_once, sleep):
        fetch_once.side_effect = [ssl.SSLError("connection closed"), []]
        self.assertEqual(fetch_imap("nju", {}, datetime.now(timezone.utc), 100), [])
        self.assertEqual(fetch_once.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("mail_digest.sources.get_secret", return_value="dummy")
    @patch("mail_digest.sources.imaplib.IMAP4_SSL")
    def test_imap_uses_readonly_peek(self, factory, _):
        conn = factory.return_value
        conn.select.return_value = ("OK", [b"1"])
        conn.response.return_value = ("OK", [b"7"])
        raw = b"From: Professor <teacher@nju.edu.cn>\r\nSubject: Notice\r\nDate: Sun, 27 Sep 2026 10:00:00 +0800\r\n\r\nPlease reply"
        conn.uid.side_effect = [
            ("OK", [b"42"]),
            ("OK", [(b'1 (UID 42 INTERNALDATE "27-Sep-2026 10:00:00 +0800" BODY[] {123}', raw)]),
        ]
        result = fetch_imap("nju", {"address": "a@smail.nju.edu.cn", "host": "imap.exmail.qq.com"},
                            datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc), 100)
        self.assertEqual(result[0]["id"], "7:42")
        conn.select.assert_called_once_with("INBOX", readonly=True)
        self.assertEqual(conn.uid.call_args_list[1].args[2], "(BODY.PEEK[] INTERNALDATE)")

    def test_rule_category_and_explicit_deadline(self):
        item = mail("nju", "1", "关于面试，截止 2026-09-30 14:00", "请准时参加")
        rules = {"求职": {"keywords": ["面试"]}}
        self.assertEqual(match_rule(item, rules), "求职")
        self.assertEqual(rule_result(item, "求职")["deadline"], "2026-09-30 14:00")

    def test_verification_line_removed(self):
        self.assertNotIn("123456", safe_excerpt("验证码：123456\n课程安排见附件", 100))

    @patch("mail_digest.processing.get_secret", return_value="dummy")
    @patch("mail_digest.processing.requests.post")
    def test_ai_finds_missed_academic_mail(self, post, _):
        post.return_value.json.return_value = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"important": True, "category": "学业", "summary": "需要提交材料", "deadline": ""})}}]}
        item = mail("gmail", "2", "提交材料", "请提交项目材料")
        result = classify_ai(item, {"model": "deepseek-flash", "excerpt_chars": 100})
        self.assertTrue(result["important"])
        self.assertEqual(result["category"], "学业")
        self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})

    def test_cross_account_render(self):
        items = [
            {"mail": mail("nju", "1", "面试"), "category": "求职", "summary": "准备面试", "deadline": ""},
            {"mail": mail("qq", "2", "选课"), "category": "学业", "summary": "确认课程", "deadline": ""},
        ]
        content = render("2026-09-27", items)
        self.assertIn("[nju] 面试", content)
        self.assertIn("[qq] 选课", content)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = {
            "accounts": {"nju": {"address": "a@smail.nju.edu.cn", "host": "unused"},
                         "qq": {"address": "b@qq.com", "host": "unused"},
                         "gmail": {"address": "c@gmail.com"}},
            "rules": {"求职": {"keywords": ["面试"]}, "学业": {"keywords": ["课程"]}},
            "ai": {"excerpt_chars": 100, "max_messages_per_run": 100},
            "output": {"directory": "reports/mail", "state_db": "state/mail.sqlite3"},
        }

    def _run(self, imap_mails, gmail_mails, ai_result=None, push_error=None):
        ai_result = ai_result or {"important": True, "category": "学业", "summary": "需要处理", "deadline": ""}
        def imap(name, *_):
            return imap_mails.get(name, [])
        with patch.object(app, "ROOT", self.root), patch.object(app, "config", return_value=self.cfg), \
             patch.object(app, "fetch_imap", side_effect=imap), \
             patch.object(app, "fetch_gmail", return_value=gmail_mails), \
             patch.object(app, "classify_ai", return_value=ai_result) as ai, \
             patch.object(app, "send_wechat", side_effect=push_error) as push:
            code = app.run()
        return code, ai, push

    def test_rules_ai_and_no_duplicate_on_repeat(self):
        imap = {"nju": [mail("nju", "1", "面试通知", "请参加")],
                "qq": [mail("qq", "2", "课程调整", "本周")],}
        gmail = [mail("gmail", "3", "材料确认", "请回复确认")]
        code, ai, push = self._run(imap, gmail)
        self.assertEqual(code, 0)
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(push.call_count, 1)
        code, ai, push = self._run(imap, gmail)
        self.assertEqual(code, 0)
        self.assertEqual(ai.call_count, 0)
        self.assertEqual(push.call_count, 0)

    def test_ai_failure_keeps_unclassified_for_retry(self):
        item = mail("gmail", "1", "材料确认", "请回复")
        with patch.object(app, "ROOT", self.root), patch.object(app, "config", return_value=self.cfg), \
             patch.object(app, "fetch_imap", return_value=[]), patch.object(app, "fetch_gmail", return_value=[item]), \
             patch.object(app, "classify_ai", side_effect=RuntimeError("offline")), \
             patch.object(app, "send_wechat") as push:
            self.assertEqual(app.run(), 1)
            push.assert_not_called()
        state = State(self.root / "state/mail.sqlite3")
        self.assertEqual(len(state.pending()), 1)
        state.close()

    def test_push_failure_retries_without_losing_items(self):
        imap = {"nju": [mail("nju", "1", "面试通知", "请参加")]}
        code, _, push = self._run(imap, [], push_error=RuntimeError("offline"))
        self.assertEqual(code, 1)
        self.assertEqual(push.call_count, 1)
        state = State(self.root / "state/mail.sqlite3")
        self.assertEqual(len(state.undelivered()), 1)
        state.close()
        code, _, push = self._run(imap, [])
        self.assertEqual(code, 0)
        self.assertEqual(push.call_count, 1)

    def test_one_push_per_day(self):
        self._run({"nju": [mail("nju", "1", "面试通知")]}, [])
        _, _, push = self._run({"nju": [mail("nju", "2", "面试安排")]}, [])
        push.assert_not_called()
        state = State(self.root / "state/mail.sqlite3")
        self.assertEqual(len(state.undelivered()), 1)
        state.close()

    def test_rule_still_processed_when_ai_limit_reached(self):
        self.cfg["ai"]["max_messages_per_run"] = 0
        imap = {"nju": [mail("nju", "1", "普通通知"), mail("nju", "2", "面试通知")],}
        code, ai, push = self._run(imap, [])
        self.assertEqual(code, 0)
        ai.assert_not_called()
        self.assertEqual(push.call_count, 1)
        self.assertIn("面试通知", push.call_args.args[1])

    def test_disabled_gmail_not_fetched(self):
        self.cfg["accounts"]["gmail"]["enabled"] = False
        code, _, push = self._run({"nju": [mail("nju", "1", "面试通知")]}, [])
        self.assertEqual(code, 0)
        self.assertEqual(push.call_count, 1)
        state = State(self.root / "state/mail.sqlite3")
        self.assertIsNone(state.scan_time("gmail"))
        state.close()


if __name__ == "__main__":
    unittest.main()
