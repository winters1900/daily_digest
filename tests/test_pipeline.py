import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

from tech_digest import pipeline as p, site
from tech_digest.adapters.public import Http, SourceError, arxiv, youtube
from tech_digest.__main__ import ROOT


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ['digest_sources.yaml','paper_sources.yaml','x_accounts.yaml','research_seeds.yaml']:
            shutil.copyfile(ROOT/name,self.root/name)
        # Historical contract regression fixtures keep version-two selection explicitly.
        import yaml
        cfg=yaml.safe_load((self.root/'digest_sources.yaml').read_text())
        cfg['selection']['minimum_review_version']=1
        (self.root/'digest_sources.yaml').write_text(yaml.safe_dump(cfg))
        self.now=datetime(2026,10,4,10,30,tzinfo=ZoneInfo('Asia/Shanghai'))

    def source(self,sid='discovery:arxiv'):
        return {'id':sid,'status':'ok','coverage':'sample','window_months':2,'note':'读取真实范围','evidence_urls':['https://arxiv.org/']}

    def raw(self,index=1,**changes):
        c=dict(kind='paper',source_id='discovery:arxiv',title='Research %d'%index,
               url='https://arxiv.org/abs/2609.%05d'%index,arxiv_id='2609.%05d'%index,
               first_public_date='2026-09-20',abstract='Language vision multimodal reinforcement inference benchmark methods',authors=['Author'])
        c.update(changes);return c

    def ingest(self,items=None,reviews=None,**changes):
        payload=dict(observed_at=self.now.isoformat(),items=items or [],reviews=reviews or [],sources=[self.source()])
        payload.update(changes);return p.ingest(self.root,payload,self.now)

    def review(self,uid,topic='language',**changes):
        r=dict(id=uid,title='中文论文 '+uid[-5:],topic=topic,summary='问题、方法、实验结果及条件的原创解读',why='主题研究价值',limitations='摘要阅读，未复现',action='检查方法和基线',
               reading_depth='摘要',evidence_excerpt='short evidence',review_evidence=['https://arxiv.org/abs/2609.00001'],date_evidence_url='https://arxiv.org/abs/2609.00001',
               quality_scores={k:4 for k in p.config(self.root)['selection']['weights']},review_status='preprint')
        r.update(changes);return r

    def insert_reviewed(self,index,topic='language',**changes):
        raw=self.raw(index,**changes)
        uid=self.ingest([raw])['candidate_ids'][0]['id']
        result=self.ingest(reviews=[self.review(uid,topic)])
        self.assertFalse(result['rejected'])
        return uid

    def test_legacy_numeric_source_and_unscored_wait_for_review(self):
        result=self.ingest([self.raw(source_id='paper:1',url='https://proceedings.mlr.press/v306/example.html',arxiv_id=None,summary='旧摘要')])
        with p.connection(self.root) as conn:
            raw,state=conn.execute('SELECT data,state FROM candidates').fetchone()
        self.assertEqual(state,'pending');self.assertEqual(json.loads(raw)['source_id'],'paper:icml')
        self.assertEqual(p.compose(self.root,self.now,dry_run=True)['selected_items'],0)

    def test_arxiv_versions_and_multiple_discovery_sources_merge(self):
        first=self.ingest([self.raw()])['candidate_ids'][0]['id']
        second=self.ingest([self.raw(source_id='discovery:hf',arxiv_id='2609.00001v3',url='https://arxiv.org/abs/2609.00001v3')])['candidate_ids'][0]['id']
        self.assertEqual(first,second)
        with p.connection(self.root) as conn:
            c=json.loads(conn.execute('SELECT data FROM candidates').fetchone()[0])
            self.assertEqual(len(c['discovery_sources']),2)

    def test_doi_bridge_merges_two_existing_identities(self):
        first=self.ingest([self.raw()])['candidate_ids'][0]['id']
        self.ingest([self.raw(2,arxiv_id=None,doi='10.1234/research',url='https://doi.org/10.1234/research')])
        self.ingest([self.raw(1,doi='10.1234/research')])
        with p.connection(self.root) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM candidates').fetchone()[0],1)

    def test_similar_title_only_suggests_merge(self):
        self.ingest([self.raw(1,title='A precise method for visual retrieval'),self.raw(2,title='A precise method for visual retrieval')])
        with p.connection(self.root) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM candidates').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM merge_suggestions').fetchone()[0],1)

    def test_paper_and_linked_news_are_related_not_identity_merged(self):
        paper=self.insert_reviewed(1)
        raw=dict(kind='news',source_id='news:hn',title='Paper explanation',url='https://arxiv.org/abs/2609.00001',published_date='2026-10-03',raw_text='https://arxiv.org/abs/2609.00001')
        news=self.ingest([raw])['candidate_ids'][0]['id']
        self.assertNotEqual(paper,news)
        self.ingest(reviews=[self.review(news,review_status=None,news_section='深度解读',event_id='paper-one',author='Author')])
        result=p.compose(self.root,self.now,dry_run=True)
        self.assertEqual(result['selected_items'],1)
        self.assertTrue(result['cards'][0]['related_links'])

    def test_old_preprint_update_cannot_refresh_window(self):
        uid=self.ingest([self.raw(first_public_date='2026-07-01',updated_date='2026-10-04')])['candidate_ids'][0]['id']
        result=self.ingest(reviews=[self.review(uid)])
        self.assertIn('窗口',result['rejected'][0]['reason'])

    def test_recent_acceptance_of_old_paper_marked_publication_update(self):
        uid=self.ingest([self.raw(first_public_date='2025-01-01',published_date='2026-10-02')])['candidate_ids'][0]['id']
        result=self.ingest(reviews=[self.review(uid,review_status='accepted',venue='ICML',track='main',verification_url='https://proceedings.mlr.press/v306/test.html')])
        self.assertFalse(result['rejected'])
        self.assertEqual(p.compose(self.root,self.now,dry_run=True)['cards'][0]['event_type'],'publication_update')

    def test_missing_date_wrong_venue_and_nonfinite_scores_wait(self):
        uid=self.ingest([self.raw(first_public_date=None)])['candidate_ids'][0]['id']
        self.assertTrue(self.ingest(reviews=[self.review(uid)])['rejected'])
        uid2=self.ingest([self.raw(2,published_date='2026-10-03')])['candidate_ids'][0]['id']
        self.assertTrue(self.ingest(reviews=[self.review(uid2,review_status='accepted',venue='ICML',track='main',verification_url='https://example.org/')])['rejected'])
        scores={k:4 for k in p.config(self.root)['selection']['weights']};scores['evidence']=float('nan')
        self.assertTrue(self.ingest(reviews=[self.review(uid2,quality_scores=scores)])['rejected'])

    def test_rank_order_independence_and_low_quality_rejected(self):
        a=self.insert_reviewed(1);b=self.insert_reviewed(2,'vision')
        scores={k:1 for k in p.config(self.root)['selection']['weights']}
        self.ingest(reviews=[self.review(a,quality_scores=scores)])
        self.assertEqual([c['id'] for c in p.compose(self.root,self.now,dry_run=True)['cards']],[b])
        with p.connection(self.root) as conn:
            cards=[json.loads(r[0]) for r in conn.execute('SELECT data FROM candidates')]
            self.assertEqual([c['id'] for c in p.rank(self.root,conn,cards)],[c['id'] for c in p.rank(self.root,conn,list(reversed(cards)))])

    def test_section_quotas_diversity_and_shortfall_fill(self):
        cards=[]
        for i in range(30):
            c=self.raw(i+1);c.update(id=str(i),topic=list(p.TOPIC_NAMES)[i%5],quality_scores={k:4 for k in p.config(self.root)['selection']['weights']},ranking_score=90-i,
                                   review_status='accepted' if i<15 else 'preprint',discovery_sources=['discovery:semantic-scholar'] if i>=20 else ['discovery:arxiv'])
            cards.append(c)
        selected=p.choose(self.root,cards)
        self.assertEqual(len(selected),10)
        self.assertEqual([sum(c['paper_section']==s for c in selected) for s in ['peer_reviewed','arxiv','recommended']],[5,3,2])
        self.assertGreaterEqual(len({c['topic'] for c in selected}),4)
        self.assertTrue(all(sum(c['topic']==t for c in selected)<=3 for t in p.TOPIC_NAMES))
        only=copy.deepcopy(cards[:15]);self.assertEqual(len(p.choose(self.root,only)),10)

    def test_news_limit_author_limit_event_dedup_and_producthunt_limit(self):
        cards=[]
        for i in range(20):
            cards.append(dict(id=str(i),kind='news',source_id='news:producthunt' if i<3 else 'news:hn',author='A' if i<6 else str(i),event_id='same' if i in {7,8} else str(i),topic='systems',ranking_score=100-i))
        selected=p.choose(self.root,cards)
        self.assertEqual(len(selected),10)
        self.assertLessEqual(sum(c['source_id']=='news:producthunt' for c in selected),1)
        self.assertLessEqual(sum(c['author']=='A' for c in selected),2)
        self.assertEqual(len({c['event_id'] for c in selected}),10)

    def test_dislike_excludes_read_is_not_negative(self):
        a=self.insert_reviewed(1);b=self.insert_reviewed(2,'vision')
        p.feedback(self.root,a,'disliked','不感兴趣',self.now);p.feedback(self.root,b,'read','已读',self.now)
        self.assertEqual([c['id'] for c in p.compose(self.root,self.now,dry_run=True)['cards']],[b])

    def test_source_progress_does_not_claim_full_window_and_plan_rotates(self):
        self.ingest(sources=[dict(self.source(),cursor={'last':'one'},backfill_cursor={'offset':100},backfill_complete=True)])
        plan=p.plan(self.root,self.now)
        with p.connection(self.root) as conn:
            self.assertFalse(conn.execute('SELECT backfill_complete FROM source_state').fetchone()[0])
        future=p.plan(self.root,self.now+timedelta(days=1))
        arxiv_source=next(s for s in future['sources'] if s['id']=='discovery:arxiv')
        self.assertEqual(arxiv_source['progress']['backfill_cursor']['offset'],100)

    def test_dry_run_and_total_failure_do_not_queue_empty_report(self):
        self.insert_reviewed(1)
        p.compose(self.root,self.now,dry_run=True)
        with p.connection(self.root) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM web_editions').fetchone()[0],0)
        self.ingest(sources=[dict(self.source(),status='blocked')])
        self.assertEqual(p.compose(self.root,self.now)['status'],'failed')
        with p.connection(self.root) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM web_editions').fetchone()[0],0)

    def test_delivery_freezes_set_and_ambiguous_send_requires_reconciliation(self):
        self.insert_reviewed(1)
        p.compose(self.root,self.now)
        (self.root/'tech_delivery.yaml').write_text('github_pages:\n  enabled: true\n  repository: winters1900/daily_digest\n  base_url: https://winters1900.github.io/daily_digest\n  wait_seconds: 0\n')
        with p.connection(self.root) as conn:data=json.loads(conn.execute('SELECT data FROM web_editions').fetchone()[0])
        fake_git=lambda root,args:'https://github.com/winters1900/daily_digest.git' if args[:2]==['remote','get-url'] else ''
        with patch.object(site,'git',side_effect=fake_git),patch.object(site.requests,'get',return_value=SimpleNamespace(status_code=200,text=site.edition_html(data))),patch('mail_digest.delivery.send_wechat',side_effect=RuntimeError('timeout')) as send:
            with self.assertRaisesRegex(RuntimeError,'不明确'):site.publish_pending(self.root,self.now)
            with self.assertRaisesRegex(RuntimeError,'待核对'):site.publish_pending(self.root,self.now)
            send.assert_called_once()
        self.assertEqual(p.compose(self.root,self.now)['delivery'],'needs_reconciliation')
        p.resolve_delivery(self.root,'2026-10-04','sent','已核对通道记录',self.now)
        self.insert_reviewed(2,'vision')
        self.assertEqual(p.compose(self.root,self.now)['selected_items'],1)
        tomorrow=self.now+timedelta(days=1)
        self.now=tomorrow;self.ingest(sources=[self.source()])
        self.assertEqual(p.compose(self.root,tomorrow,dry_run=True)['selected_items'],1)

    def test_html_sections_and_untrusted_content(self):
        uid=self.insert_reviewed(1)
        result=p.compose(self.root,self.now)
        with p.connection(self.root) as conn:data=json.loads(conn.execute('SELECT data FROM web_editions').fetchone()[0])
        data['cards'][0]['summary']='<script>bad()</script><a href="javascript:alert(1)">x</a>'
        rendered=site.edition_html(data)
        for label in ['已录用会议／正式期刊','arXiv 前沿','Semantic Scholar 推荐','未同行评审']:self.assertIn(label,rendered)
        self.assertNotIn('<script>',rendered);self.assertNotIn('href="javascript:',rendered)

    def test_compose_push_targets_today_not_old_unsent_dry_run(self):
        from tech_digest import __main__ as app
        result={'day':'2026-10-04','status':'ok','selected_items':1}
        with patch.object(app,'ROOT',self.root), patch.object(p,'compose',return_value=result), patch.object(site,'publish_pending',return_value={'delivery':'published'}) as publish, patch('builtins.print'):
            self.assertEqual(app.main(['--compose','--push','--no-notify']),0)
        self.assertEqual(publish.call_args.kwargs,{'day':'2026-10-04','notify':False})

    def test_video_without_readable_transcript_cannot_be_reviewed(self):
        uid=self.ingest([dict(kind='news',source_id='youtube:yannic',title='Video',url='https://www.youtube.com/watch?v=abc',published_date='2026-09-20')])['candidate_ids'][0]['id']
        review=self.review(uid,news_section='深度解读',event_id='video:abc',reading_depth='摘要')
        result=self.ingest(reviews=[review])
        self.assertIn('字幕',result['rejected'][0]['reason'])

    def test_arxiv_snapshot_pagination_and_beijing_date(self):
        item=SimpleNamespace(id='https://arxiv.org/abs/2609.00001v2',title='Paper',published='2026-09-20T20:00:00Z',updated='2026-10-01T00:00:00Z',summary='Abstract')
        item.get=lambda key,default=None:default
        fake=SimpleNamespace(bozo=False,version='atom10',entries=[item],feed={'opensearch_totalresults':300})
        http=Mock();http.get.return_value.content=b'atom'
        source={'id':'discovery:arxiv','kind':'paper','url':'https://export.arxiv.org/api/query','categories':['cs.AI']}
        progress={'backfill_cursor':{'offset':100,'window_start':'202608040000','window_end':'202610032359','sort_order':'ascending'}}
        with patch('tech_digest.adapters.public.feedparser.parse',return_value=fake):
            cards,meta=arxiv(source,http,self.now,progress)
        self.assertEqual(http.get.call_args_list[1].kwargs['params']['start'],100)
        self.assertIn('202610032359',http.get.call_args_list[1].kwargs['params']['search_query'])
        self.assertEqual(cards[0]['first_public_date'],'2026-09-21')
        self.assertEqual(meta['backfill_cursor']['offset'],101)
        self.assertFalse(meta['backfill_complete'])


class HttpTests(unittest.TestCase):
    def response(self,code,headers=None):return SimpleNamespace(status_code=code,headers=headers or {})
    def test_retry_after_is_respected(self):
        session=Mock();session.headers={};session.request.side_effect=[self.response(429,{'Retry-After':'4'}),self.response(200)]
        sleep=Mock();Http(session,sleep).get('https://example.org/')
        sleep.assert_called_once_with(4)
    def test_long_retry_after_is_deferred_not_retried_early(self):
        session=Mock();session.headers={};session.request.return_value=self.response(429,{'Retry-After':'120'})
        sleep=Mock()
        with self.assertRaises(SourceError) as exc:Http(session,sleep).get('https://example.org/')
        self.assertTrue(exc.exception.retry_at);sleep.assert_not_called();self.assertEqual(session.request.call_count,1)
    def test_login_denial_is_not_retried(self):
        session=Mock();session.headers={};session.request.return_value=self.response(403)
        with self.assertRaises(SourceError) as exc:Http(session,Mock()).get('https://example.org/')
        self.assertEqual(exc.exception.status,'blocked');self.assertEqual(session.request.call_count,1)


if __name__=='__main__':unittest.main()
