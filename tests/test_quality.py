import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image
from tech_digest import quality,media,pipeline,site,trial
from tech_digest.adapters import official
import test_pipeline as fixtures


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PipelineTests();self.f.setUp();self.addCleanup(self.f.temp.cleanup)
        self.root=self.f.root;self.now=self.f.now
        self.card=dict(self.f.raw(),id='test:1',review_status='preprint',venue='arXiv',track='preprint',
                       review_version=3,topic='systems',subtopic='training_optimization',reading_depth='关键章节',
                       focus=True,summary='方法与实验条件',why='节省内存',limitations='未经复现',
                       problem='内存占用',contribution='稀疏状态',results='峰值27.54 GB',conditions='固定模型与硬件',
                       reading_advice='核对基线与配置',evidence_excerpt='表4',review_evidence=['https://arxiv.org/html/2609.00001v1'],
                       date_evidence_url='https://arxiv.org/abs/2609.00001',
                       quality_scores={'relevance':4.5,'evidence':4.5,'novelty':4,'recency':4,'reproducibility':3},
                       score_reasons={k:'基于具体方法和实验' for k in quality.SCORES},
                       claims=[dict(text='峰值27.54 GB',excerpt='peak 27.54 GB',locator='Table 4',url='https://arxiv.org/html/2609.00001v1#A4.T4')],
                       read_sections={'method':'§3 稀疏更新','experiments':'§6 与表4','limitations':'任务和硬件边界'})

    def figure(self):
        return dict(kind='redraw',source_url='https://arxiv.org/html/2609.00001v1',figure_number='Table 4',
                    caption='固定条件的对照',alt='峰值内存对照',version='v1',conditions='相同模型与任务',unit='GB',
                    data=[{'label':'A','value':27.54}],data_evidence=self.card['claims'])

    def test_numeric_claim_requires_corresponding_evidence(self):
        bad=dict(self.card,results='峰值42.91 GB')
        self.assertEqual(quality.check([bad])['status'],'failed')
        self.assertEqual(quality.check([self.card])['status'],'ok')

    def test_abstract_cannot_be_focus_or_high_evidence(self):
        for card in [dict(self.card,reading_depth='摘要',focus=False),dict(self.card,reading_depth='摘要',quality_scores=dict(self.card['quality_scores'],evidence=3))]:
            self.assertTrue(quality.check([card])['errors'])

    def test_reproducibility_and_code_need_verification(self):
        for card in [dict(self.card,code_url='https://github.com/a/b'),dict(self.card,quality_scores=dict(self.card['quality_scores'],reproducibility=4))]:
            self.assertTrue(quality.check([card])['errors'])

    def test_repeated_scores_warn_without_forcing_different_scores(self):
        cards=[dict(self.card,id=str(i),focus=i<2,topic='systems' if i==0 else 'language') for i in range(3)]
        result=quality.check(cards)
        self.assertFalse(result['errors']);self.assertTrue(result['warnings'])

    def test_subtopic_cap_across_main_topics_and_order_independence(self):
        cards=[dict(self.card,id=str(i),topic=list(pipeline.TOPIC_NAMES)[i%5],ranking_score=90-i) for i in range(5)]
        a=pipeline.choose(self.root,cards);b=pipeline.choose(self.root,cards[::-1])
        self.assertEqual([c['id'] for c in a],[c['id'] for c in b]);self.assertEqual(len(a),3)

    def test_focus_diversity_and_limit(self):
        cards=[dict(self.card,id=str(i),topic='systems' if i<3 else 'language',ranking_score=90-i) for i in range(4)]
        chosen=quality.focus_cards(cards)
        self.assertEqual(len(chosen),3);self.assertEqual(len({c['topic'] for c in chosen}),2)

    def test_license_missing_blocks_descriptor_and_prepare_downgrades(self):
        figure=dict(self.figure(),kind='original',image_url='https://arxiv.org/x.png')
        self.assertTrue(quality.check([dict(self.card,figure=figure)])['errors'])
        card=media.prepare(self.root,dict(self.card,figure=figure))
        self.assertNotIn('figure',card);self.assertIn('media_warning',card)
        self.assertFalse(quality.check([card])['errors'])

    def test_private_and_untrusted_image_hosts_rejected(self):
        for url in ['http://arxiv.org/a.png','https://localhost/a.png','https://arxiv.org:8000/a.png','https://user:secret@arxiv.org/a.png']:
            with self.assertRaises(ValueError):media.safe_url(url)
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):media.safe_url('https://arxiv.org/a.png')

    def test_image_normalization_strips_metadata_and_limits_dimensions(self):
        img=Image.new('RGB',(2500,1000),'white');buf=io.BytesIO();img.save(buf,format='PNG')
        normalized,size=media.normalize(buf.getvalue())
        self.assertEqual(size,(2000,800));self.assertLess(len(normalized),800*1024)
        with self.assertRaises(OSError):media.normalize(b'<html>not image</html>')

    def test_redraw_verifies_values_and_asset_hash(self):
        f=self.figure();f['data'][0]['value']=12.5
        with self.assertRaises(ValueError):media.validate_descriptor(f)
        f=self.figure();card=media.prepare(self.root,dict(self.card,figure=f))
        self.assertIn('asset',card['figure']);raw=media.asset_bytes(self.root,card['figure'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),card['figure']['sha256'])
        path=self.root/'state/tech/media'/card['figure']['asset'].split('/')[-1];path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):media.asset_bytes(self.root,card['figure'])

    def test_missing_asset_and_unsafe_html(self):
        card=media.prepare(self.root,dict(self.card,figure=self.figure()))
        card['figure']['caption']='<script>evil()</script>';card['figure']['alt']='" onerror="evil()'
        data=dict(day='2026-10-04',cards=[dict(card,paper_section='arxiv')],pipeline_version=3)
        html=site.edition_html(data)
        self.assertNotIn('<script>',html);self.assertNotIn(' onerror="evil()',html)
        self.assertIn('loading="lazy"',html);self.assertIn('digest-v3.css',html)
        self.assertNotIn('id="peer_reviewed"',html)
        card['figure']['asset']='assets/figures/'+'0'*64+'.webp'
        self.assertTrue(quality.check([card],self.root)['errors'])

    def test_old_review_is_queued_again_under_new_contract(self):
        uid=self.f.insert_reviewed(1)
        import yaml
        cfg=pipeline.config(self.root);cfg['selection']['minimum_review_version']=3
        (self.root/'digest_sources.yaml').write_text(yaml.safe_dump(cfg))
        self.assertIn(uid,[c['id'] for c in pipeline.review_queue(self.root,self.now)['queue']['paper']])
        self.assertFalse(pipeline.compose(self.root,self.now,dry_run=True)['cards'])

    def test_timestamp_and_date_only_have_distinct_precision(self):
        card=dict(self.card,first_public_at='2026-09-20T18:30:00Z')
        self.assertEqual(pipeline.content_date(card),'2026-09-21')
        self.assertEqual(pipeline.content_date(self.card),'2026-09-20')

    def test_official_volume_does_not_include_workshop_or_invent_dates(self):
        responses={'https://proceedings.mlr.press/':'<a href="v300/">International Conference on Machine Learning</a><a href="v301/">ICML Workshop</a>',
                   'https://proceedings.mlr.press/v300/':'<a href="a.html">A sufficiently long research paper</a>',
                   'https://proceedings.mlr.press/v300/a.html':'<meta name="citation_title" content="Paper"><meta name="citation_date" content="2026">Abstract enough text'}
        http=SimpleNamespace(get=lambda url:SimpleNamespace(text=responses[url]))
        cards,meta=official.conference(dict(id='paper:icml',kind='paper',url='https://proceedings.mlr.press/',venues=['ICML']),http,self.now,{})
        self.assertEqual(len(cards),1);self.assertIsNone(cards[0]['published_date']);self.assertNotIn('review_status',cards[0]);self.assertEqual(meta['coverage'],'sample')

    def trial_input(self):
        path=self.root/'input.json'
        data=dict(id='quality-trial-2026-10-04-120000',cards=[self.card],day='2026-10-04',sources=[])
        path.write_text(json.dumps(data));return path

    def test_trial_dry_run_does_not_create_edition_or_change_candidates(self):
        uid=self.f.insert_reviewed(1);path=self.trial_input()
        with pipeline.connection(self.root) as conn:before=list(conn.execute('select * from candidates'))
        result=trial.run(self.root,path,self.now,dry_run=True)
        self.assertEqual(result['delivery'],'not_sent')
        with pipeline.connection(self.root) as conn:
            self.assertEqual(list(conn.execute('select * from candidates')),before)
            self.assertEqual(conn.execute('select count(*) from web_editions').fetchone()[0],0)

    def test_trial_uncertain_and_sent_are_frozen(self):
        path=self.trial_input();dest=self.root/'state/tech/trials'/path.name
        dest=dest.with_name('quality-trial-2026-10-04-120000.json');dest.parent.mkdir(parents=True)
        for state in ['sending','uncertain']:
            dest.write_text(json.dumps(dict(delivery=state)))
            with self.assertRaises(RuntimeError):trial.run(self.root,path,self.now,push=True)
        dest.write_text(json.dumps(dict(delivery='sent',url='https://example.org/trial')))
        with patch('mail_digest.delivery.send_wechat') as send:
            self.assertEqual(trial.run(self.root,path,self.now,push=True)['delivery'],'already_sent');send.assert_not_called()

    def test_pages_missing_image_prevents_wechat(self):
        path=self.trial_input()
        from tech_digest.__main__ import atomic_write
        atomic_write(self.root/'tech_delivery.yaml','github_pages:\n  enabled: true\n  repository: a/b\n  base_url: https://example.org\n  wait_seconds: 0\n')
        def git(root,args):return 'https://github.com/a/b.git' if args[0]=='remote' else ''
        def response(*a,**kw):
            html=(self.root/'docs/quality-trial-2026-10-04-120000.html').read_text()
            return SimpleNamespace(status_code=200,text=html)
        with patch.object(site,'git',side_effect=git),patch('requests.get',side_effect=response),patch.object(media,'remote_ready',return_value=False),patch('mail_digest.delivery.send_wechat') as send:
            with self.assertRaises(RuntimeError):trial.run(self.root,path,self.now,push=True)
            send.assert_not_called()

    def test_publication_requires_article_decision_and_matching_event(self):
        c=dict(self.card,review_status='accepted',verification_url='https://proceedings.mlr.press/',
               venue='ICML',track='main',published_label='2026-09-20')
        c['publication_evidence']=dict(url=c['verification_url'],locator='Main conference',excerpt='Accepted',event_date='2026-09-20',venue='ICML',track='main')
        self.assertTrue(quality.check([c])['errors'])
        c['verification_url']='https://proceedings.mlr.press/v267/example25a.html'
        c['publication_evidence']['url']=c['verification_url']
        self.assertFalse(quality.check([c])['errors'])
        c['publication_evidence']['event_date']='2026-09-19'
        self.assertTrue(quality.check([c])['errors'])

    def test_pmlr_title_and_abstract_links_are_separate(self):
        responses={'https://proceedings.mlr.press/':'<li><a href="v300">Volume 300</a> Proceedings of ICML 2026</li><li><a href="v301">Volume 301</a> TerraBytes at ICML 2026</li>',
                   'https://proceedings.mlr.press/v300/':'<div class="paper"><p class="title">Actual research paper title</p><a href="https://proceedings.mlr.press/v300/a.html">abs</a></div>',
                   'https://proceedings.mlr.press/v300/a.html':'<meta name="citation_title" content="Actual research paper title"><meta name="citation_publication_date" content="2026/09/20">'}
        http=SimpleNamespace(get=lambda url:SimpleNamespace(text=responses[url]))
        cards,meta=official.conference(dict(id='paper:icml',kind='paper',url='https://proceedings.mlr.press/',venues=['ICML']),http,self.now,{})
        self.assertEqual(len(cards),1);self.assertEqual(cards[0]['published_date'],'2026-09-20')

    def test_sent_html_is_not_regenerated_and_latest_copies_snapshot(self):
        with pipeline.connection(self.root) as conn:
            conn.execute('INSERT INTO web_editions VALUES (?,?,?,?)',('2026-10-04',json.dumps(dict(day='2026-10-04',pipeline_version=2,cards=[self.card])),None,self.now.isoformat()))
        from tech_digest.__main__ import atomic_write
        atomic_write(self.root/'docs/2026-10-04.html','saved historical snapshot')
        site.build_site(self.root)
        self.assertEqual((self.root/'docs/2026-10-04.html').read_text(),'saved historical snapshot')
        self.assertEqual((self.root/'docs/latest.html').read_text(),'saved historical snapshot')

    def test_assets_need_css_and_image_hash_not_just_http200(self):
        c=media.prepare(self.root,dict(self.card,figure=self.figure()))
        with patch('requests.get',return_value=SimpleNamespace(status_code=200,content=b'wrong version')):
            self.assertFalse(media.remote_ready('https://example.org',[c]))

    def test_image_dimensions_cannot_inject_html(self):
        c=media.prepare(self.root,dict(self.card,figure=self.figure()))
        c['figure']['width']='100" onerror="evil()'
        self.assertTrue(quality.check([c],self.root)['errors'])

    def test_claim_text_cannot_self_certify_an_unsupported_number(self):
        c=copy.deepcopy(self.card);c['claims'][0]['excerpt']='generic evidence without measured value'
        self.assertTrue(quality.check([c])['errors'])
