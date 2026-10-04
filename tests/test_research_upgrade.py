import copy
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import yaml
import test_quality as fixtures
from tech_digest import pipeline,quality,research,events,preferences,evaluation,themes,site
from tech_digest.adapters import openreview
from tech_digest.adapters.public import SourceError


class ResearchUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.QualityTests();self.f.setUp();self.addCleanup(self.f.f.temp.cleanup)
        self.root=self.f.root;self.now=self.f.now
        self.card=copy.deepcopy(self.f.card)
        self.card.update(review_version=4,paper_type='systems',research_tags=['sparse optimizer'],
            type_details={k:'原文明确的设置与边界' for k in research.PAPER_TYPES['systems'][1]},
            type_evidence_note='方法与实验章节',findings=[dict(statement='峰值27.54 GB',metric='峰值内存',
                conditions='固定模型与硬件',comparison='相同模型基线',claim_ids=['memory'],
                verdict='supported',support_reason='Table 4 同行相同工作负载的峰值内存列')])
        self.card['claims'][0]['id']='memory'

    def insert(self,index=1,**extra):
        raw=self.f.f.raw(index,**extra);uid=self.f.f.ingest([raw])['candidate_ids'][0]['id']
        c=dict(self.card,id=uid);c.update(extra)
        review={k:v for k,v in c.items() if k not in {'source_id','kind','url','arxiv_id','first_public_date','abstract'}}
        result=self.f.f.ingest(reviews=[review]);self.assertFalse(result['rejected'])
        return uid

    def news(self,index,**extra):
        raw=dict(kind='news',source_id='news:hn',title='Sparse memory optimization '+str(index),
                 url='https://example.org/news/'+str(index),published_date='2026-10-03')
        raw.update(extra);uid=self.f.f.ingest([raw])['candidate_ids'][0]['id']
        self.f.f.ingest(reviews=[self.f.f.review(uid,topic='systems',news_section='工程实践',event_id='event-'+str(index))])
        return uid

    def test_paper_types_require_distinct_fields(self):
        self.assertFalse(quality.check([self.card])['errors'])
        for type_name,(_,keys) in research.PAPER_TYPES.items():
            c=dict(self.card,paper_type=type_name,type_details={k:'未知明确标注并保留边界' for k in keys})
            self.assertFalse(quality.check([c])['errors'])
            c['type_details'].pop(keys[0]);self.assertTrue(quality.check([c])['errors'])

    def test_claim_numbers_cannot_borrow_from_other_experiment(self):
        c=copy.deepcopy(self.card)
        c['claims'].append(dict(id='other',text='另一个实验',excerpt='27.54',locator='Table 9',url=c['url']))
        c['claims'][0]['excerpt']='127.54 GB'
        self.assertTrue(quality.check([c])['errors'])

    def test_findings_need_conditions_comparison_and_support(self):
        for key in ('conditions','comparison','support_reason','claim_ids'):
            c=copy.deepcopy(self.card);c['findings'][0].pop(key)
            self.assertTrue(quality.check([c])['errors'])
        c=copy.deepcopy(self.card);c['findings'][0]['verdict']='uncertain'
        self.assertTrue(quality.check([c])['errors'])

    def test_unmapped_result_number_blocked(self):
        c=copy.deepcopy(self.card);c['results']='峰值27.54 GB，速度2倍'
        c['claims'][0]['excerpt']='27.54 GB and 2 times'
        self.assertTrue(quality.check([c])['errors'])

    def test_v3_waits_for_upgrade_but_history_is_untouched(self):
        uid=self.f.f.insert_reviewed(1)
        cfg=pipeline.config(self.root);cfg['selection']['minimum_review_version']=4
        (self.root/'digest_sources.yaml').write_text(yaml.safe_dump(cfg))
        queued=pipeline.review_queue(self.root,self.now)
        self.assertIn(uid,[c['id'] for c in queued['queue']['paper']])
        self.assertEqual(queued['research_schema']['review_version'],4)
        self.assertFalse(pipeline.compose(self.root,self.now,dry_run=True)['cards'])

    def test_fine_preferences_expire_and_match_word_boundaries(self):
        preferences.set_preference(self.root,'sparse optimizer',4,'明确关注',self.now,2)
        with pipeline.connection(self.root) as conn:
            p=preferences.profile(conn,self.now);later=preferences.profile(conn,self.now+timedelta(days=2))
        self.assertEqual(preferences.adjustment(self.card,p)[0],4)
        self.assertFalse(later['manual'])
        self.assertFalse(preferences.matches('rag','storage'))
        self.assertTrue(preferences.matches('rag','RAG retrieval'))

    def test_read_and_unselected_do_not_change_preferences(self):
        uid=self.insert();pipeline.feedback(self.root,uid,'read','已读',self.now)
        with pipeline.connection(self.root) as conn:p=preferences.profile(conn,self.now)
        self.assertFalse(p['fine_weights']);self.assertFalse(p['topic_weights'])

    def test_explicit_likes_decay_without_repeat_amplification(self):
        uid=self.insert()
        for _ in range(3):pipeline.feedback(self.root,uid,'liked','关注方法',self.now)
        with pipeline.connection(self.root) as conn:
            p=preferences.profile(conn,self.now);later=preferences.profile(conn,self.now+timedelta(days=30))
        self.assertEqual(p['fine_weights']['sparse optimizer'],1)
        self.assertEqual(later['fine_weights']['sparse optimizer'],.625)
        preferences.set_preference(self.root,'sparse optimizer',0,'撤销此关键词偏好',self.now)
        with pipeline.connection(self.root) as conn:self.assertEqual(preferences.adjustment(self.card,preferences.profile(conn,self.now))[0],0)

    def test_similarity_only_suggests_and_does_not_merge(self):
        a=dict(self.card,source_id='news:hn',kind='news',id='a',title='Sparse optimizer memory performance',raw_text='Sparse optimizer memory performance workload hardware comparison')
        b=dict(a,id='b',source_id='news:nvidia')
        result=events.suggestions([a,b]);self.assertEqual(len(result),1)
        self.assertNotIn('story_id',a)
        self.assertFalse(events.suggestions([dict(a,kind='paper'),dict(b,kind='paper')]))

    def test_confirmed_event_cross_day_dedup_and_private_feedback_preserved(self):
        a=self.news(1);b=self.news(2)
        pipeline.feedback(self.root,b,'liked','明确喜欢',self.now)
        with pipeline.connection(self.root) as conn:conn.execute("UPDATE candidates SET state='recommended' WHERE id=?",(a,))
        events.link(self.root,a,b,'contrast','同一发布但比较结论不同','https://example.org/evidence',self.now)
        with pipeline.connection(self.root) as conn:
            cards=[json.loads(r[0]) for r in conn.execute('SELECT data FROM candidates')]
            self.assertEqual(len(cards),2);self.assertEqual(cards[0]['story_id'],cards[1]['story_id'])
            self.assertFalse(pipeline.rank(self.root,conn,[c for c in cards if c['id']==b],self.now))
            self.assertEqual(conn.execute('SELECT count(*) FROM feedback').fetchone()[0],1)

    def test_paper_relationships_keep_both_and_direction(self):
        a=self.insert(1);b=self.insert(2)
        with self.assertRaises(ValueError):events.link(self.root,a,b,'duplicate','相似标题','https://arxiv.org/',self.now)
        events.link(self.root,b,a,'extends','新论文扩展前作','https://arxiv.org/',self.now)
        with pipeline.connection(self.root) as conn:
            self.assertEqual(events.relations(conn,b)[0]['direction'],'outgoing')
            self.assertEqual(events.relations(conn,a)[0]['direction'],'incoming')
            self.assertTrue(all('story_id' not in json.loads(r[0]) for r in conn.execute('SELECT data FROM candidates')))

    def test_bridge_identity_merge_preserves_event_edges(self):
        a=self.insert(1);b=self.f.f.ingest([self.f.f.raw(2,arxiv_id=None,doi='10.1234/bridge',url='https://doi.org/10.1234/bridge')])['candidate_ids'][0]['id']
        n=self.news(3)
        events.link(self.root,b,n,'commentary','介绍该论文','https://example.org/evidence',self.now)
        self.f.f.ingest([self.f.f.raw(1,doi='10.1234/bridge')])
        with pipeline.connection(self.root) as conn:
            self.assertTrue(events.relations(conn,a))
            self.assertFalse(conn.execute('SELECT 1 FROM candidates WHERE id=?',(b,)).fetchone())

    def test_replay_is_label_aware_and_does_not_consume_candidates(self):
        c=dict(self.card,focus=False);bad=dict(c,id='bad',quality_scores={k:1 for k in quality.SCORES})
        payload=dict(episodes=[dict(id='fixed',at=self.now.isoformat(),candidates=[bad,c],relevance_labels={c['id']:3,'bad':0},
            content_labels={c['id']:{'supported':True,'conditions_complete':True}})])
        with pipeline.connection(self.root) as conn:before=conn.execute('SELECT count(*) FROM candidates').fetchone()[0]
        r=evaluation.replay(self.root,payload,self.now)['episodes'][0]
        self.assertEqual(r['selected'],[c['id']]);self.assertEqual(r['precision'],1);self.assertEqual(r['ndcg'],1)
        self.assertIsNone(r['content_metrics']['reading_value']['rate'])
        with pipeline.connection(self.root) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM candidates').fetchone()[0],before)
        payload['episodes'][0]['relevance_labels'].pop('bad')
        self.assertIsNone(evaluation.replay(self.root,payload,self.now)['episodes'][0]['precision'])
        payload['episodes'][0]['content_labels']['unknown']={'supported':True}
        with self.assertRaises(ValueError):evaluation.replay(self.root,payload,self.now)
        payload['episodes'][0]['content_labels']={c['id']:{'supported':'true'}}
        with self.assertRaises(ValueError):evaluation.replay(self.root,payload,self.now)

    def test_theme_page_and_typed_cards_escape_and_do_not_expose_profile(self):
        c=dict(self.card,title='<script>bad</script>',paper_section='arxiv',type_details=dict(self.card['type_details'],hardware='<script>bad</script>'))
        page=site.edition_html(dict(day='2026-10-04',cards=[c],pipeline_version=3))
        self.assertNotIn('<script>',page);self.assertIn('系统论文',page);self.assertIn('阅读导引',page)
        page=themes.render([dict(day='2026-10-04',cards=[c])]);self.assertNotIn('<script>',page)
        self.assertIn('topic-systems',page);self.assertNotIn('fine_weights',page)
        self.assertNotIn('.html#item-',page)
        page=themes.render([dict(day='2026-10-04',cards=[c],pipeline_version=3)])
        self.assertIn('.html#item-',page)

    def test_openreview_date_only_from_accepted_state(self):
        source=dict(kind='paper',id='paper:tmlr',venues=['TMLR'],type='journal')
        ms=int(self.now.timestamp()*1000)
        note=dict(id='forum1',forum='forum1',pdate=ms,content={k:{'value':v} for k,v in dict(title='Method',venueid='TMLR/Submitted',abstract='Abstract').items()})
        c=openreview.candidate(source,note,self.now,'TMLR');self.assertFalse(c['official_events'])
        note['content']['venueid']['value']='TMLR'
        c=openreview.candidate(source,note,self.now,'TMLR');self.assertEqual(c['accepted_date'],'2026-10-04')
        self.assertNotIn('review_status',c);self.assertNotIn('first_public_date',c)
        note['pdate']=ms+86400000;c=openreview.candidate(source,note,self.now,'TMLR');self.assertFalse(c['official_events'])

    def test_openreview_decision_requires_official_invitation_and_signature(self):
        source=dict(kind='paper',id='paper:iclr',venues=['ICLR'],type='conference')
        group='ICLR.cc/2026/Conference';ms=int(self.now.timestamp()*1000)
        reply=dict(id='decision',forum='forum1',cdate=ms,content={'decision':{'value':'Accept (Poster)'}},
                   invitations=[group+'/Submission1/-/Decision'],signatures=['~Author1'])
        note=dict(id='forum1',forum='forum1',content={'title':{'value':'Title'}},details={'replies':[reply]})
        self.assertFalse(openreview.candidate(source,note,self.now,group)['official_events'])
        reply['signatures']=[group+'/Program_Chairs']
        self.assertTrue(openreview.candidate(source,note,self.now,group)['official_events'])
        reply['content']['decision']['value']='Reject'
        self.assertFalse(openreview.candidate(source,note,self.now,group)['official_events'])

    def test_openreview_partial_failure_keeps_prior_cursor_and_items(self):
        source=dict(kind='paper',id='paper:iclr',venues=['ICLR'],type='conference',url='https://iclr.cc/',openreview_groups=['one','two'],detail_budget=1)
        note=dict(id='forum1',forum='forum1',content={'title':{'value':'Title'},'venueid':{'value':'one'}})
        http=SimpleNamespace(get=lambda url,**kw:SimpleNamespace(json=lambda:dict(notes=[note],count=2)))
        http.get=unittest.mock.Mock(side_effect=[SimpleNamespace(json=lambda:dict(notes=[note],count=2)),SourceError('限流','blocked','2026-10-04T11:00:00+08:00')])
        items,meta=openreview.collect(source,http,self.now,{})
        self.assertEqual(len(items),1);self.assertEqual(meta['status'],'blocked')
        self.assertEqual(meta['cursor']['openreview_scan']['one']['offset'],1)
        self.assertIn('retry_not_before',meta['cursor'])

    def test_openreview_failure_preserves_official_index_discovery(self):
        from tech_digest.adapters import official
        source=dict(kind='paper',id='paper:tmlr',venues=['TMLR'],type='journal',url='https://jmlr.org/tmlr/papers/',openreview_groups=['TMLR'])
        def get(url,**kw):
            if 'api2' in url:raise SourceError('限流','blocked','2026-10-04T11:00:00+08:00')
            return SimpleNamespace(text='<li><a href="https://openreview.net/pdf?id=forum1">An official research paper title</a><a href="https://openreview.net/forum?id=forum1">Forum</a></li>')
        found,meta=official.conference(source,SimpleNamespace(get=get),self.now,{})
        self.assertEqual(len(found),1);self.assertEqual(meta['status'],'blocked')
        self.assertIn('retry_not_before',meta['cursor']);self.assertNotIn('review_status',found[0])
        self.assertTrue(found[0]['publication_missing'])


if __name__=='__main__':unittest.main()
