"""来源诊断不能把索引当原文，也不能发送未上线或重复的测试报告。"""
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tech_digest import source_audit as audit
from tech_digest.adapters import public


class SourceAuditTests(unittest.TestCase):
    def test_index_with_abstract_word_is_not_paper(self):
        http=Mock();http.get.return_value.text='<h1>Abstract index</h1>'+('directory '*200)
        source=dict(kind='paper',adapter='conference',url='https://jmlr.org/')
        index=audit.verify_sample(source,dict(title='Index',url='https://jmlr.org/papers/'),http)
        paper=audit.verify_sample(source,dict(title='Paper',url='https://jmlr.org/papers/v27/example.html'),http)
        self.assertEqual(index['status'],'discovery_only')
        self.assertEqual(paper['status'],'content_verified')

    def test_browser_verification_is_blocked(self):
        http=Mock();http.get.return_value.text='<h1>Verifying your browser</h1>'
        result=audit.verify_sample(dict(kind='paper',adapter='conference'),dict(title='Paper',url='https://openreview.net/forum?id=x'),http)
        self.assertEqual(result['status'],'blocked')

    def test_page_prefilter_discards_anchor_and_submission_navigation(self):
        http=Mock();http.get.return_value.text='''<a href="#content">Skip to main content</a>
        <a href="/research/submission">Submission Format</a>
        <a href="/research/real-article">A real research article</a>'''
        source=dict(id='news:test',kind='news',url='https://example.org/research')
        items,_=public.page(source,http,None,{})
        self.assertEqual([x['url'] for x in items],['https://example.org/research/real-article'])

    def report(self):
        return dict(id='source-check-2026-10-04-113103',observed_at='2026-10-04',rows=[],delivery='not_sent')

    def test_html_escapes_untrusted_source_and_keeps_missing_date(self):
        report=self.report();report['rows']=[dict(id='x:test',kind='x',name='<script>x</script>',status='content_verified',source_url='https://x.com/test',note='<img>',sample=dict(title='test',url='javascript:bad',date=None))]
        html,_=audit.render(report)
        self.assertNotIn('<script>',html);self.assertNotIn('href="javascript:',html)
        self.assertIn('未知',html)

    def test_pages_not_ready_does_not_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'report.json';path.write_text(json.dumps(self.report()))
            with patch.object(audit,'ROOT',root),patch.object(audit.site,'publication_lock',return_value=nullcontext()),patch.object(audit.site,'git',return_value=''),patch.object(audit.site,'settings',return_value={'base_url':'https://example.org','wait_seconds':0}),patch.object(audit.requests,'get',return_value=SimpleNamespace(status_code=404,text='')),patch('mail_digest.delivery.send_wechat') as send:
                with self.assertRaisesRegex(RuntimeError,'尚未上线'):audit.publish(path)
                send.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['delivery'],'not_sent')

    def test_uncertain_delivery_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'report.json';r=self.report();r['delivery']='uncertain';path.write_text(json.dumps(r))
            with patch.object(audit,'ROOT',root),patch.object(audit.site,'publication_lock',return_value=nullcontext()),patch('mail_digest.delivery.send_wechat') as send:
                with self.assertRaisesRegex(RuntimeError,'禁止自动重发'):audit.publish(path)
                send.assert_not_called()
