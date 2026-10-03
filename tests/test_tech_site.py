import copy
from datetime import timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tech_digest import site
import test_tech_digest as fixtures


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TechDigestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)
        self.root = self.fixture.root
        self.now = self.fixture.now
        self.card = self.fixture.card()

    def test_html_archive_from_real_ingestion(self):
        from tech_digest.__main__ import ingest
        ingest(self.root, self.fixture.payload([self.card]), self.now)
        files = site.build_site(self.root)
        self.assertIn('docs/2026-10-03.html', files)
        html = (self.root / 'docs/2026-10-03.html').read_text()
        self.assertIn('技术实测', html)
        self.assertNotIn('北京时间；', html)
        self.assertNotIn('优质内容', html)
        self.assertIn('<details class="collection">', html)
        self.assertIn('2026-10-03.html', (self.root / 'docs/index.html').read_text())

    def test_untrusted_content_cannot_inject_script_or_link(self):
        from tech_digest.__main__ import ingest
        card = dict(self.card, title='<img src=x onerror=alert(1)>',
                    summary='<script>alert(1)</script><a href="javascript:alert(1)">click</a>',
                    code_url='javascript:alert(1)')
        ingest(self.root, self.fixture.payload([card]), self.now)
        site.build_site(self.root)
        html = (self.root / 'docs/2026-10-03.html').read_text()
        self.assertNotIn('<script>', html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('<img src=x', html)

    def test_failed_retry_preserves_successful_html(self):
        from tech_digest.__main__ import ingest
        payload = self.fixture.payload([self.card])
        ingest(self.root, payload, self.now)
        site.build_site(self.root)
        before = (self.root / 'docs/2026-10-03.html').read_text()
        bad = self.fixture.payload()
        for s in bad['sources']: s['status'] = 'blocked'
        ingest(self.root, bad, self.now)
        site.build_site(self.root)
        self.assertEqual(before, (self.root / 'docs/2026-10-03.html').read_text())

    def test_shared_theme_is_not_repeated(self):
        from tech_digest.__main__ import ingest
        ingest(self.root,self.fixture.payload([dict(self.card,theme_id='same'),self.fixture.paper(theme_id='same')]),self.now)
        site.build_site(self.root)
        html = (self.root / 'docs/2026-10-03.html').read_text()
        self.assertEqual(html.count('<article class="card">'),1)
        self.assertIn('技术实测',html)

    def web_payload(self):
        from tech_digest.__main__ import ingest, database
        (self.root/'tech_delivery.yaml').write_text('github_pages:\n  enabled: true\n  repository: winters1900/daily_digest\n  base_url: https://winters1900.github.io/daily_digest\n  wait_seconds: 0\n')
        ingest(self.root,self.fixture.payload([self.card]),self.now)
        with database(self.root) as conn:
            import json
            return json.loads(conn.execute('SELECT data FROM web_editions').fetchone()[0])

    def fake_git(self, root, args):
        return 'https://github.com/winters1900/daily_digest.git' if args[:2]==['remote','get-url'] else ''

    def test_pages_ready_before_wechat_and_no_duplicate(self):
        data=self.web_payload()
        with patch.object(site,'git',side_effect=self.fake_git), patch.object(site.requests,'get',return_value=SimpleNamespace(status_code=200,text=site.edition_html(data))), patch('mail_digest.delivery.send_wechat') as send:
            first=site.publish_pending(self.root,self.now)
            second=site.publish_pending(self.root,self.now,day='2026-10-03')
            send.assert_called_once()
            self.assertEqual(first['delivery'],'sent')
            self.assertEqual(second['delivery'],'sent')
            self.assertIn('https://winters1900.github.io/daily_digest/2026-10-03.html',send.call_args.args[1])
            self.assertNotIn('中文实验摘要',send.call_args.args[1])

    def test_pages_not_ready_does_not_send_bad_link(self):
        self.web_payload()
        with patch.object(site,'git',side_effect=self.fake_git), patch.object(site.requests,'get',return_value=SimpleNamespace(status_code=404,text='not found')), patch('mail_digest.delivery.send_wechat') as send:
            with self.assertRaisesRegex(RuntimeError,'网页尚未发布'):
                site.publish_pending(self.root,self.now)
            send.assert_not_called()

    def test_html_sent_additions_wait_until_next_day(self):
        from tech_digest.__main__ import ingest, database
        data = self.web_payload()
        with patch.object(site,'git',side_effect=self.fake_git), patch.object(site.requests,'get',return_value=SimpleNamespace(status_code=200,text=site.edition_html(data))), patch('mail_digest.delivery.send_wechat'):
            site.publish_pending(self.root,self.now)
        with database(self.root) as conn:
            self.assertEqual(conn.execute('SELECT status,content FROM deliveries').fetchone(), ('sent',''))
            # 兼容已有 HTML 已发出、旧队列仍为 pending 的历史状态。
            conn.execute("UPDATE deliveries SET status='pending'")
        extra = self.fixture.card(url='https://x.com/simonw/status/999999',title='次日推荐')
        result = ingest(self.root,self.fixture.payload([extra]),self.now+timedelta(minutes=1))
        self.assertEqual(result['selected_items'],1)
        self.assertIn('已留待次日',result['rejected'][0])
        with database(self.root) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM deferred').fetchone()[0],1)
        tomorrow = self.now+timedelta(days=1)
        payload = self.fixture.payload([])
        payload['observed_at'] = tomorrow.isoformat()
        result = ingest(self.root,payload,tomorrow)
        self.assertEqual(result['selected_items'],1)
        with database(self.root) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM deferred').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
