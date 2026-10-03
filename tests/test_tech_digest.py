import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tech_digest import __main__ as app


class TechDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('x_accounts.yaml', 'paper_sources.yaml'):
            shutil.copyfile(app.ROOT / name, self.root / name)
        self.now = datetime(2026, 10, 3, 14, 0, tzinfo=ZoneInfo('Asia/Shanghai'))

    def ingest(self, root, payload, now):
        return app.ingest(root, payload, now, save_local=True)

    def payload(self, cards=None):
        return {'observed_at': self.now.isoformat(), 'sources': [
            {'id': s['id'], 'status': 'ok', 'note': '实际检查了最新页面', 'evidence_urls': [s['url']],
             'window_months': 2, 'coverage': 'window_checked'}
            for s in app.sources(self.root)], 'items': cards or []}

    def card(self, post='123', **changes):
        card = {'kind': 'x', 'source_id': 'x:simonw', 'title': '技术实测',
                'url': 'https://x.com/simonw/status/' + post, 'summary': '中文实验摘要',
                'why': '工程实践', 'limitations': '仅阅读帖子，未复现',
                'action': '检查实验', 'reading_depth': '帖子',
                'evidence_excerpt': '原文实验依据', 'published_date': '2026-10-03'}
        card.update(changes)
        return card

    def paper(self, **changes):
        card = self.card(kind='paper', source_id='paper:1', title='检索研究',
                         url='https://proceedings.mlr.press/v306/test26.html', venue='ICML',
                         review_status='published', track='main', reading_depth='摘要',
                         verification_url='https://proceedings.mlr.press/v306/test26.html',
                         event_type='new_research', first_public_date='2026-10-02')
        card.update(changes)
        return card

    def test_end_to_end_and_same_day_retry(self):
        payload = self.payload([self.card()])
        first = self.ingest(self.root, payload, self.now)
        second = self.ingest(self.root, payload, self.now)
        self.assertEqual(first['status'], 'ok')
        self.assertEqual(second['new_items'], 0)
        self.assertEqual(second['selected_items'], 1)
        content = Path(first['report']).read_text()
        self.assertIn('阅读深度：帖子', content)
        self.assertIn('https://x.com/simonw/status/123', content)

    def test_previous_day_is_not_recommended_again(self):
        card = self.card()
        self.ingest(self.root, self.payload([card]), self.now)
        tomorrow = self.now + timedelta(days=1)
        payload = self.payload([card])
        payload['observed_at'] = tomorrow.isoformat()
        result = self.ingest(self.root, payload, tomorrow)
        self.assertEqual(result['selected_items'], 0)
        self.assertIn('历史已推荐', result['rejected'][0])

    def test_same_day_new_batch_cannot_exceed_author_limit(self):
        self.ingest(self.root, self.payload([self.card('1'), self.card('2')]), self.now)
        result = self.ingest(self.root, self.payload([self.card('3')]), self.now)
        self.assertEqual(result['selected_items'], 2)
        self.assertEqual(result['new_items'], 0)
        self.assertIn('超过单作者配额', result['rejected'][0])

    def test_missing_sources_is_partial(self):
        payload = self.payload()
        payload['sources'] = payload['sources'][:1]
        result = self.ingest(self.root, payload, self.now)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('not_checked', Path(result['report']).read_text())

    def test_all_failed_does_not_overwrite_report(self):
        good = self.ingest(self.root, self.payload([self.card()]), self.now)
        before = Path(good['report']).read_text()
        payload = self.payload()
        for source in payload['sources']:
            source.update(status='blocked', note='登录或来源不可访问')
        result = self.ingest(self.root, payload, self.now)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(before, Path(good['report']).read_text())
        self.assertTrue(result['report'].endswith('-failure.md'))

    def test_expired_batch_rejected(self):
        payload = self.payload()
        payload['observed_at'] = (self.now - timedelta(hours=7)).isoformat()
        with self.assertRaises(ValueError):
            self.ingest(self.root, payload, self.now)

    def test_outside_roster_and_old_posts_rejected(self):
        payload = self.payload([self.card(url='https://x.com/random/status/123'),
                                self.card('124', published_date='2026-07-01')])
        result = self.ingest(self.root, payload, self.now)
        self.assertEqual(result['selected_items'], 0)
        self.assertEqual(len(result['rejected']), 2)

    def test_paper_preprint_and_wrong_verification_rejected(self):
        payload = self.payload([self.paper(review_status='preprint'),
                                self.paper(verification_url='https://arxiv.org/abs/2609.00001')])
        result = self.ingest(self.root, payload, self.now)
        self.assertEqual(result['selected_items'], 0)
        self.assertEqual(len(result['rejected']), 2)

    def test_old_preprint_recent_publication_labeled(self):
        result = self.ingest(self.root, self.payload([self.paper(first_public_date='2025-01-01')]), self.now)
        self.assertIn('近期发表／录用动态', Path(result['report']).read_text())

    def test_weekly_sources_only_due_after_seven_days(self):
        self.assertEqual(len(app.due_sources(self.root, self.now)), 20)
        self.ingest(self.root, self.payload(), self.now)
        self.assertEqual(len(app.due_sources(self.root, self.now + timedelta(days=1))), 16)
        self.assertEqual(len(app.due_sources(self.root, self.now + timedelta(days=7))), 20)

    def test_two_month_window_for_daily_weekly_and_papers(self):
        for card in [self.card(published_date='2026-08-03'),
                     self.card('124', source_id='x:antirez', url='https://x.com/antirez/status/124', published_date='2026-08-03'),
                     self.paper(published_date='2026-08-03')]:
            app.validate_card(card, {s['id']: s for s in app.sources(self.root)}, self.root, self.now, self.now)
            card['published_date'] = '2026-08-02'
            with self.assertRaisesRegex(ValueError, '超出时间窗口'):
                app.validate_card(card, {s['id']: s for s in app.sources(self.root)}, self.root, self.now, self.now)

    def test_calendar_month_end_and_leap_year(self):
        self.assertEqual(app.window_start(self.now.replace(month=4, day=30), 2).date().isoformat(), '2026-02-28')
        self.assertEqual(app.window_start(self.now.replace(year=2024, month=4, day=30), 2).date().isoformat(), '2024-02-29')
        self.assertEqual(app.window_start(self.now.replace(month=1, day=31), 2).date().isoformat(), '2025-11-30')

    def test_legacy_checks_force_window_rescan(self):
        payload = self.payload()
        for entry in payload['sources']:
            entry.pop('window_months')
        self.ingest(self.root, payload, self.now)
        self.assertEqual(len(app.due_sources(self.root, self.now + timedelta(days=1))), 20)

    def test_index_access_is_not_window_coverage(self):
        payload = self.payload()
        for entry in payload['sources']:
            entry['coverage'] = 'index_only'
        result = self.ingest(self.root, payload, self.now)
        content = Path(result['report']).read_text()
        self.assertIn('20 个来源仅抽样', content)
        self.assertIn('仅索引', content)
        self.assertIn('2026-08-03 至 2026-10-03', content)

    def test_per_author_cap_and_twitter_normalization(self):
        payload = self.payload([self.card('1', url='https://twitter.com/simonw/status/1?utm_source=x'), self.card('2'), self.card('3')])
        result = self.ingest(self.root, payload, self.now)
        self.assertEqual(result['selected_items'], 2)
        self.assertIn('https://x.com/simonw/status/1', Path(result['report']).read_text())

    def test_atomic_failure_does_not_commit_seen(self):
        with patch.object(app, 'atomic_write', side_effect=OSError('磁盘错误')):
            with self.assertRaises(OSError):
                self.ingest(self.root, self.payload([self.card()]), self.now)
        result = self.ingest(self.root, self.payload([self.card()]), self.now)
        self.assertEqual(result['new_items'], 1)

    def test_source_status_duplicates_rejected(self):
        payload = self.payload()
        payload['sources'].append(copy.deepcopy(payload['sources'][0]))
        with self.assertRaises(ValueError):
            self.ingest(self.root, payload, self.now)

    def test_related_paper_and_post_render_one_card(self):
        result = self.ingest(self.root, self.payload([self.card(theme_id='same'), self.paper(theme_id='same')]), self.now)
        content = Path(result['report']).read_text()
        self.assertEqual(content.count('### ['), 1)
        self.assertIn('相关解读／原论文', content)

    def test_daily_ten_papers_and_retry_limit(self):
        papers = [self.paper(url='https://proceedings.mlr.press/v306/test%d.html' % i,
                             title='检索研究%d' % i) for i in range(10)]
        result = self.ingest(self.root, self.payload(papers), self.now)
        self.assertEqual(result['selected_items'], 10)
        eleventh = self.paper(url='https://proceedings.mlr.press/v306/extra.html')
        result = self.ingest(self.root, self.payload([eleventh]), self.now)
        self.assertEqual(result['selected_items'], 10)
        self.assertIn('超过当日精选配额', result['rejected'][0])
        self.assertEqual(app.plan(self.root, self.now)['selection']['daily_target_items'], 10)


    def test_wechat_default_no_local_report_and_no_duplicate(self):
        result = app.ingest(self.root, self.payload([self.card()]), self.now)
        self.assertIsNone(result['report'])
        self.assertFalse((self.root / 'reports/tech').exists())
        with patch.object(app, 'send_wechat') as sender:
            self.assertEqual(app.push_pending(self.root, self.now)['delivery'], 'sent')
            self.assertEqual(app.push_pending(self.root, self.now)['delivery'], 'nothing_pending')
            sender.assert_called_once()
            self.assertEqual(sender.call_args.kwargs['title'], '技术知识日报 2026-10-03')
        with app.database(self.root) as conn:
            self.assertEqual(conn.execute('SELECT content FROM deliveries').fetchone()[0], '')
        app.ingest(self.root, self.payload([self.card()]), self.now)
        with patch.object(app, 'send_wechat') as sender:
            app.push_pending(self.root, self.now)
            sender.assert_not_called()

    def test_wechat_failure_keeps_retry_content(self):
        app.ingest(self.root, self.payload([self.card()]), self.now)
        with patch.object(app, 'send_wechat', side_effect=RuntimeError('mock secret')):
            with self.assertRaisesRegex(RuntimeError, '保留待重试'):
                app.push_pending(self.root, self.now)
        with app.database(self.root) as conn:
            row = conn.execute('SELECT status,content,error FROM deliveries').fetchone()
            self.assertEqual(row[0], 'pending')
            self.assertIn('技术实测', row[1])
            self.assertNotIn('mock secret', row[2])
        with patch.object(app, 'send_wechat') as sender:
            app.push_pending(self.root, self.now)
            sender.assert_called_once()

    def test_new_items_after_push_are_deferred_to_next_day(self):
        app.ingest(self.root, self.payload([self.card('1')]), self.now)
        with patch.object(app, 'send_wechat'):
            app.push_pending(self.root, self.now)
        result = app.ingest(self.root, self.payload([self.card('2')]), self.now)
        self.assertEqual(result['new_items'], 0)
        self.assertIn('已留待次日', result['rejected'][0])
        tomorrow = self.now + timedelta(days=1)
        payload = self.payload()
        payload['observed_at'] = tomorrow.isoformat()
        result = app.ingest(self.root, payload, tomorrow)
        self.assertEqual(result['new_items'], 1)
        with patch.object(app, 'send_wechat') as sender:
            app.push_pending(self.root, tomorrow)
            self.assertIn('status/2', sender.call_args.args[1])

if __name__ == '__main__':
    unittest.main()
