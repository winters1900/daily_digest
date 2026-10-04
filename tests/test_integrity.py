"""覆盖来源接通后才会暴露的完整性与故障恢复问题。"""
import copy
import json
import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
import test_pipeline as fixtures
from tech_digest import pipeline as p, site
from tech_digest.adapters import public as a


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.PipelineTests();self.fx.setUp()
        self.addCleanup(self.fx.temp.cleanup)
        self.root=self.fx.root;self.now=self.fx.now

    def news(self,index,event='event',**extra):
        raw=dict(kind='news',source_id='news:hn',title='Research news '+str(index),
                 url='https://example.org/news/'+str(index),published_date='2026-10-03')
        raw.update(extra)
        uid=self.fx.ingest([raw])['candidate_ids'][0]['id']
        self.fx.ingest(reviews=[self.fx.review(uid,news_section='研究动态',event_id=event,author='Author')])
        return uid

    def test_cross_source_video_and_repository_still_require_evidence(self):
        for url,sid,missing in [('https://www.youtube.com/watch?v=demo','youtube:lex','字幕'),
                                ('https://github.com/example/research','news:github-trending','技术变化')]:
            raw=dict(kind='news',source_id='news:hn',title='Research',url=url,published_date='2026-10-03')
            uid=self.fx.ingest([raw])['candidate_ids'][0]['id']
            self.fx.ingest([dict(raw,source_id=sid)])
            review=self.fx.review(uid,news_section='深度解读',event_id=url)
            result=self.fx.ingest(reviews=[review])
            self.assertIn(missing,result['rejected'][0]['reason'])
            if 'youtube' in url:
                review.update(transcript_url=url,transcript_excerpt='Actual readable transcript',reading_depth='字幕')
            else:
                review.update(change_evidence_url=url+'/releases/tag/v1',technical_change='New inference implementation')
            self.assertFalse(self.fx.ingest(reviews=[review])['rejected'])

    def test_direct_video_link_requires_transcript_without_channel_discovery(self):
        uid=self.fx.ingest([dict(kind='news',source_id='news:hn',title='Video',url='https://youtu.be/demo',published_date='2026-10-03')])['candidate_ids'][0]['id']
        result=self.fx.ingest(reviews=[self.fx.review(uid,news_section='深度解读',event_id='video')])
        self.assertIn('字幕',result['rejected'][0]['reason'])

    def test_stale_sources_detected_even_after_today_delivery(self):
        with p.connection(self.root) as conn:
            for source in p.registry(self.root):
                conn.execute('INSERT OR REPLACE INTO source_state VALUES (?,?,?,?,?,?,?)',
                             (source['id'],'2026-01-01T10:30:00+08:00','sample','{}','{}',0,0))
            conn.execute('INSERT INTO web_editions VALUES (?,?,?,?)',('2026-10-04','{}',None,self.now.isoformat()))
        result=p.health(self.root,self.now.replace(hour=13))
        self.assertFalse(result['delivery_overdue'])
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['stale_sources']),len(p.registry(self.root)))

    def test_health_respects_rotation_weekdays_and_today_grace(self):
        when=self.now.replace(hour=13)
        with p.connection(self.root) as conn:
            for source in p.registry(self.root):
                last=when-timedelta(days=1)
                if source.get('cadence')=='rotating':
                    while last.date().toordinal()%3!=source['rotation_group']:last-=timedelta(days=1)
                elif source.get('cadence')=='twice_weekly':
                    while last.weekday() not in {1,4}:last-=timedelta(days=1)
                cursor={'content_checked_at':last.isoformat()}
                conn.execute('INSERT OR REPLACE INTO source_state VALUES (?,?,?,?,?,?,?)',
                             (source['id'],last.isoformat(),'sample',json.dumps(cursor),'{}',0,0))
        self.assertFalse(p.health(self.root,self.now)['stale_sources'])
        late=p.health(self.root,when)['stale_sources']
        self.assertIn('discovery:arxiv',{r['id'] for r in late})
        self.assertNotIn('news:producthunt',{r['id'] for r in late})
        for source in p.registry(self.root):
            if source.get('cadence')=='rotating':
                self.assertEqual(source['id'] in {r['id'] for r in late},when.date().toordinal()%3==source['rotation_group'])

    def test_identity_only_browser_check_is_reported_as_stale(self):
        self.fx.ingest(sources=[dict(self.fx.source('x:karpathy'),phase='identity',coverage='index_only')])
        self.assertIn('x:karpathy',{r['id'] for r in p.health(self.root,self.now.replace(hour=13))['stale_sources']})

    def test_batch_daily_and_cumulative_metrics_have_distinct_scopes(self):
        old=self.now-timedelta(days=1)
        raw=self.fx.raw(1)
        uid=p.ingest(self.root,dict(observed_at=old.isoformat(),items=[raw],sources=[self.fx.source()],reviews=[]),old)['candidate_ids'][0]['id']
        p.ingest(self.root,dict(observed_at=old.isoformat(),reviews=[self.fx.review(uid)]),old)
        raw2=self.fx.raw(2,first_public_date=None)
        self.fx.ingest([raw2])
        self.fx.ingest(sources=[self.fx.source()])
        p.compose(self.root,self.now)
        with p.connection(self.root) as conn:
            entry=json.loads(conn.execute('SELECT details FROM source_runs ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(entry['batch_metrics']['candidates'],0)
        self.assertEqual(entry['verified'],0)
        self.assertIsNone(entry['date_evidence_completeness'])
        self.assertEqual(entry['daily_metrics']['verified'],0)
        self.assertEqual(entry['daily_metrics']['selected'],1)
        self.assertEqual(entry['cumulative_metrics']['candidates'],2)
        self.assertEqual(entry['cumulative_metrics']['verified'],1)
        self.assertEqual(entry['cumulative_metrics']['date_evidence_completeness'],.5)

    def test_review_time_uses_actual_execution_not_previous_day_collection(self):
        now=self.now.replace(hour=0,minute=30)
        collected=now-timedelta(hours=1)
        uid=p.ingest(self.root,dict(observed_at=collected.isoformat(),items=[self.fx.raw()]),now)['candidate_ids'][0]['id']
        p.ingest(self.root,dict(observed_at=collected.isoformat(),reviews=[self.fx.review(uid)]),now)
        with p.connection(self.root) as conn:
            card=json.loads(conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()[0])
        self.assertEqual(card['reviewed_at'],now.isoformat())

    def test_invalid_batch_shape_is_rejected_without_partial_mutation(self):
        for field in ('items','sources','reviews'):
            with self.assertRaisesRegex(ValueError,'对象数组'):
                p.ingest(self.root,dict(observed_at=self.now.isoformat(),**{field:['bad']}),self.now)
        with p.connection(self.root) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_version_date_is_updated_without_changing_first_public_age(self):
        uid=self.fx.ingest([self.fx.raw(first_public_date='2026-07-01',updated_date='2026-07-02')])['candidate_ids'][0]['id']
        self.fx.ingest([self.fx.raw(arxiv_id='2609.00001v2',updated_date='2026-10-03')])
        with p.connection(self.root) as conn:
            card=json.loads(conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()[0])
            self.assertEqual(card['updated_date'],'2026-10-03')
            self.assertEqual(card['first_public_date'],'2026-07-01')
        self.assertTrue(self.fx.ingest(reviews=[self.fx.review(uid)])['rejected'])

    def test_invalid_heat_does_not_make_ranking_order_dependent(self):
        first=self.fx.insert_reviewed(1);second=self.fx.insert_reviewed(2,'vision')
        with p.connection(self.root) as conn:
            cards=[json.loads(r[0]) for r in conn.execute('SELECT data FROM candidates')]
            cards[0]['engagement']=float('nan');cards[1]['engagement']='N/A'
            one=[c['id'] for c in p.rank(self.root,conn,cards)]
            two=[c['id'] for c in p.rank(self.root,conn,list(reversed(cards)))]
            self.assertEqual(one,two)

    def test_official_paper_source_has_name_and_can_enter_report(self):
        uid=self.fx.ingest([self.fx.raw(source_id='paper:icml',published_date='2026-10-02')])['candidate_ids'][0]['id']
        result=self.fx.ingest(reviews=[self.fx.review(uid,review_status='accepted',venue='ICML',track='main',verification_url='https://proceedings.mlr.press/v306/test.html')])
        self.assertFalse(result['rejected'])
        self.assertIn('ICML',p.compose(self.root,self.now,dry_run=True)['cards'][0]['discovery_labels'])

    def test_identity_check_is_not_a_daily_content_check(self):
        status=dict(self.fx.source('x:karpathy'),coverage='index_only',phase='identity')
        self.fx.ingest(sources=[status])
        self.assertIn('x:karpathy',{s['id'] for s in p.due(self.root,self.now)})
        self.fx.ingest(sources=[dict(status,coverage='sample',phase='content')])
        self.assertNotIn('x:karpathy',{s['id'] for s in p.due(self.root,self.now)})

    def test_bridge_keeps_reviewed_data_feedback_and_related_theme(self):
        winner=self.fx.insert_reviewed(1)
        loser=self.fx.ingest([self.fx.raw(2,arxiv_id=None,doi='10.1234/bridge',url='https://doi.org/10.1234/bridge')])['candidate_ids'][0]['id']
        news=self.news(1,related_paper_alias='doi:10.1234/bridge')
        p.feedback(self.root,loser,'liked','明确喜欢',self.now)
        self.fx.ingest([self.fx.raw(doi='10.1234/bridge')])
        with p.connection(self.root) as conn:
            raw,state=conn.execute('SELECT data,state FROM candidates WHERE id=?',(winner,)).fetchone()
            self.assertEqual(state,'reviewed');self.assertTrue(json.loads(raw)['summary'])
            self.assertEqual(json.loads(conn.execute('SELECT data FROM candidates WHERE id=?',(news,)).fetchone()[0])['theme_id'],winner)
            self.assertEqual(p.feedback_actions(conn)[winner],'liked')
        self.assertEqual(p.feedback(self.root,loser,'read','旧链接仍可反馈',self.now)['id'],winner)

    def test_previous_event_and_linked_paper_are_not_recommended_twice(self):
        first=self.news(1,'same-event')
        with p.connection(self.root) as conn:conn.execute("UPDATE candidates SET state='recommended' WHERE id=?",(first,))
        self.news(2,'same-event')
        self.assertEqual(p.compose(self.root,self.now,dry_run=True)['selected_items'],0)
        paper=self.fx.insert_reviewed(3)
        with p.connection(self.root) as conn:conn.execute("UPDATE candidates SET state='recommended' WHERE id=?",(paper,))
        self.news(4,'different-id',related_paper_alias='arxiv:2609.00003')
        self.assertEqual(p.compose(self.root,self.now,dry_run=True)['selected_items'],0)

    def test_event_dedup_cannot_be_bypassed_by_different_theme_ids(self):
        cards=[dict(id=str(i),kind='news',source_id='news:hn',author=str(i),
                    event_id='same',theme_id='theme'+str(i),topic='systems',ranking_score=90-i) for i in range(3)]
        self.assertEqual(len(p.choose(self.root,cards)),1)

    def test_total_limit_survives_oversized_section_targets(self):
        cfg=p.config(self.root);cfg['selection']['paper_limit']=2
        (self.root/'digest_sources.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True))
        cards=[]
        for i in range(20):
            c=self.fx.raw(i+1)
            c.update(id=str(i),topic=list(p.TOPIC_NAMES)[i%5],ranking_score=100-i,review_status='accepted')
            cards.append(c)
        one=p.choose(self.root,cards);two=p.choose(self.root,list(reversed(cards)))
        self.assertEqual(len(one),2);self.assertEqual([c['id'] for c in one],[c['id'] for c in two])

    def test_compose_cannot_change_snapshot_while_publication_is_running(self):
        self.fx.insert_reviewed(1);p.compose(self.root,self.now)
        with site.publication_lock(self.root):
            with self.assertRaisesRegex(RuntimeError,'另一进程'):p.compose(self.root,self.now)

    def test_sent_snapshot_is_immutable_even_when_queue_is_called_directly(self):
        self.fx.insert_reviewed(1);p.compose(self.root,self.now)
        with p.connection(self.root) as conn:
            raw=conn.execute('SELECT data FROM web_editions').fetchone()[0]
            conn.execute('UPDATE web_editions SET sent_at=?',(self.now.isoformat(),))
            changed=json.loads(raw);changed['cards']=[]
            site.queue_edition(conn,self.now.date().isoformat(),changed)
            self.assertEqual(conn.execute('SELECT data FROM web_editions').fetchone()[0],raw)

    def test_confirmation_marks_canonical_id_after_draft_id_was_merged(self):
        winner=self.fx.insert_reviewed(1)
        loser=self.fx.ingest([self.fx.raw(2,arxiv_id=None,doi='10.1234/bridge',url='https://doi.org/10.1234/bridge')])['candidate_ids'][0]['id']
        with p.connection(self.root) as conn:old=json.loads(conn.execute('SELECT data FROM candidates WHERE id=?',(loser,)).fetchone()[0])
        self.fx.ingest([self.fx.raw(doi='10.1234/bridge')])
        with p.connection(self.root) as conn:
            site.mark_recommended(conn,'2026-10-04',[old])
            self.assertEqual(conn.execute('SELECT state FROM candidates WHERE id=?',(winner,)).fetchone()[0],'recommended')

    def test_review_queue_keeps_minority_topics_and_rejection_reason(self):
        raw=[self.fx.raw(i+1,title='Language LLM agent reasoning '+str(i),abstract='language llm agent reasoning') for i in range(60)]
        raw += [self.fx.raw(70+i,title=title,abstract=title) for i,title in enumerate(['Image visual segmentation','Multimodal audio speech','Reinforcement reward policy','Inference serving GPU'])]
        self.fx.ingest(raw)
        queue=p.review_queue(self.root,self.now)['queue']['paper']
        self.assertEqual(len(queue),50)
        self.assertEqual({c['prefilter_topic'] for c in queue},set(p.TOPIC_NAMES))
        self.assertTrue(all('pending_reason' in c for c in queue))

    def test_source_failure_does_not_abort_other_sources_or_expose_exception(self):
        def rss(source,*args):
            if source['id']=='news:deepmind':raise AttributeError('secret-token-is-not-logged')
            return [dict(kind='news',source_id=source['id'],title='Research',url='https://example.org/article')],dict(coverage='sample',note='实际读到 RSS')
        with patch.dict(a.ADAPTERS,{'rss':rss}):
            result=a.collect(self.root,self.now,only=['news:openai','news:deepmind'])
        self.assertEqual(result['status'],'partial');self.assertEqual(result['ingested'],1)
        self.assertNotIn('secret-token',json.dumps(result))
        self.assertTrue((self.root/'state/tech/checkpoints/news_openai.json').exists())

    def test_process_interruption_keeps_completed_source_transaction(self):
        ready=threading.Event();original=p.ingest
        def rss(source,*args):
            if source['id']=='news:deepmind':
                ready.wait(5);raise KeyboardInterrupt()
            return [dict(kind='news',source_id=source['id'],title='Research',url='https://example.org/article')],dict(coverage='sample',note='实际读取')
        def save(*args,**kwargs):
            result=original(*args,**kwargs);ready.set();return result
        with patch.dict(a.ADAPTERS,{'rss':rss}),patch.object(p,'ingest',side_effect=save):
            with self.assertRaises(KeyboardInterrupt):a.collect(self.root,self.now,only=['news:openai','news:deepmind'])
        with p.connection(self.root) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM candidates').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT source_id FROM source_runs').fetchone()[0],'news:openai')

    def test_manual_source_check_respects_retry_after_and_keeps_browser_backlog(self):
        def rss(source,*args):return [],dict(coverage='sample',note='实际读取 RSS')
        with patch.dict(a.ADAPTERS,{'rss':rss}):
            a.collect(self.root,self.now,only=['news:openai'])
            a.collect(self.root,self.now,only=['news:deepmind'])
            tasks=json.loads((self.root/'state/tech/browser_tasks.json').read_text())
            self.assertEqual({s['id'] for s in tasks['sources']},{'news:openai','news:deepmind'})
            with p.connection(self.root) as conn:
                conn.execute('UPDATE source_state SET cursor=? WHERE id=?',(json.dumps({'retry_not_before':(self.now+timedelta(hours=1)).isoformat()}),'news:openai'))
            result=a.collect(self.root,self.now,only=['news:openai'])
            self.assertEqual(result['deferred_sources'],['news:openai']);self.assertEqual(result['status'],'partial')
            with self.assertRaisesRegex(ValueError,'未知来源'):a.collect(self.root,self.now,only=['unknown:id'])

    def test_semantic_negative_feedback_removes_seed_with_different_identifier(self):
        uid=self.fx.ingest([self.fx.raw(arxiv_id='1706.03762',url='https://arxiv.org/abs/1706.03762',semantic_scholar_id='s2-hash')])['candidate_ids'][0]['id']
        p.feedback(self.root,uid,'disliked','明确不感兴趣',self.now)
        http=Mock();http.request.return_value.json.return_value={'recommendedPapers':[]}
        source=next(s for s in p.registry(self.root) if s['id']=='discovery:semantic-scholar')
        a.semantic(source,http,self.now,{},self.root)
        body=http.request.call_args.kwargs['json']
        self.assertIn('ARXIV:1706.03762',body['negativePaperIds'])
        self.assertNotIn('ARXIV:1706.03762',body['positivePaperIds'])

    def test_health_detects_overdue_delivery_and_partial_collection(self):
        before=p.health(self.root,self.now);after=p.health(self.root,self.now.replace(hour=13))
        self.assertFalse(before['delivery_overdue']);self.assertTrue(after['delivery_overdue'])
        self.assertEqual(after['status'],'partial');self.assertTrue(after['warnings'])


class ArxivIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,4,10,30,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.source=dict(id='discovery:arxiv',kind='paper',url='https://export.arxiv.org/api/query',categories=['cs.AI'])
    def feed(self,offset,count,total):
        entries=[]
        for i in range(offset,offset+count):
            e=SimpleNamespace(id='https://arxiv.org/abs/2610.%05dv1'%(i+1),title='Paper '+str(i),published='2026-10-02T00:00:00Z',updated='2026-10-02T00:00:00Z',summary='Abstract')
            e.get=lambda key,default=None:default;entries.append(e)
        return SimpleNamespace(bozo=False,version='atom10',entries=entries,feed={'opensearch_totalresults':total})

    def test_html_response_does_not_complete_or_advance_backfill(self):
        http=Mock();http.get.return_value.content=b'<html><body>Verifying your browser</body></html>'
        progress=dict(backfill_cursor={'window_start':'202608031600','window_end':'202610030230','offset':100,'sort_order':'ascending'})
        cards,meta=a.arxiv(self.source,http,self.now,progress)
        self.assertFalse(cards);self.assertEqual(meta['status'],'error')
        self.assertFalse(meta['backfill_complete']);self.assertEqual(meta['backfill_cursor']['offset'],100)
        self.assertEqual(http.get.call_count,1)

    def test_valid_empty_atom_is_distinguished_from_missing_metadata(self):
        for metadata,expected in [('<opensearch:totalResults>0</opensearch:totalResults>','window_checked'),('','sample')]:
            http=Mock();http.get.return_value.content=('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'+metadata+'</feed>').encode()
            cards,meta=a.arxiv(self.source,http,self.now,{})
            self.assertFalse(cards);self.assertEqual(meta['coverage'],expected)
            self.assertEqual(meta['backfill_complete'],expected=='window_checked')
    def test_incremental_overflow_fetches_beyond_latest_100(self):
        http=Mock();http.get.return_value.content=b'atom'
        progress=dict(cursor={'scheduled_through':'202610030230'},backfill_cursor={'window_start':'202608031600','window_end':'202610030230','offset':1000,'finished':True,'sort_order':'ascending'})
        feeds=[self.feed(150,100,250),self.feed(0,100,250),self.feed(100,100,250),self.feed(200,50,250)]
        with patch.object(a.feedparser,'parse',side_effect=feeds):
            cards,meta=a.arxiv(self.source,http,self.now,progress)
        self.assertEqual(len(cards),250);self.assertFalse(meta['cursor']['incremental_jobs'])
        self.assertEqual([c.kwargs['params']['start'] for c in http.get.call_args_list],[0,0,100,200])
        self.assertEqual(meta['coverage'],'window_checked')

    def test_later_page_limit_retains_earlier_pages_and_unfinished_cursor(self):
        http=Mock();http.get.side_effect=[SimpleNamespace(content=b'atom'),SimpleNamespace(content=b'atom'),a.SourceError('接口限流','blocked','2026-10-04T06:00:00+00:00')]
        progress=dict(cursor={'scheduled_through':'202610030230'},backfill_cursor={'window_start':'202608031600','window_end':'202610030230','offset':100,'sort_order':'ascending'})
        with patch.object(a.feedparser,'parse',side_effect=[self.feed(0,1,300),self.feed(1,1,300)]):
            cards,meta=a.arxiv(self.source,http,self.now,progress)
        self.assertEqual(len(cards),2);self.assertEqual(meta['status'],'blocked')
        self.assertEqual(meta['backfill_cursor']['offset'],101)
        self.assertEqual(meta['cursor']['incremental_jobs'][0]['offset'],0)
        self.assertIn('retry_not_before',meta['cursor']);self.assertFalse(meta['backfill_complete'])


if __name__=='__main__':unittest.main()
