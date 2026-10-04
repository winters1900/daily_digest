"""独立优化试刊：不消耗候选，不覆盖正式日报。"""
import json
import hashlib
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import site,quality,media,pipeline
from .__main__ import atomic_write,database


def prepare(root,data,now):
    data=json.loads(json.dumps(data))
    identifier=data.get('id','')
    if not re.fullmatch(r'quality-trial-\d{4}-\d{2}-\d{2}-\d{6}',identifier):raise ValueError('试刊ID无效')
    cards=data.get('cards',[])
    if not cards:raise ValueError('试刊为空')
    for c in cards:
        media.prepare(root,c)
        pipeline.validate_review(root,c,now)
        c['quality_score']=round(20*sum(c['quality_scores'][k]*w for k,w in pipeline.config(root)['selection']['weights'].items()),2)
        c['ranking_score']=c['quality_score']
    eligible=[c for c in cards if c['quality_score']>=pipeline.config(root)['selection']['minimum_score']]
    cards=pipeline.choose(root,eligible)
    focus_ids={c['id'] for c in quality.focus_cards(cards)}
    for c in cards:
        c['focus']=c['id'] in focus_ids
        c['trial_revisit']=True
        if not c['focus']:c.pop('figure',None)
        media.prepare(root,c)
        c['historical_backfill']=(now.date()-datetime.fromisoformat(c['published_label']).date()).days>7
    diagnostic=quality.check(cards,root)
    if diagnostic['errors']:raise ValueError('试刊质量检查失败：'+json.dumps(diagnostic['errors'],ensure_ascii=False))
    data.update(cards=cards,day=data.get('day',now.date().isoformat()),pipeline_version=3,edition_type='trial',quality=diagnostic,status='ok')
    data['shortfalls']=data.get('shortfalls',[])+['本期论文 %d/10、资讯 %d/10；按实际核验质量选取。'%(sum(c['kind']=='paper' for c in cards),sum(c['kind']!='paper' for c in cards))]
    if not cards:raise ValueError('没有达到质量门槛的内容')
    return data


def run(root,path,now,push=False,dry_run=False,notify=True):
    with site.publication_lock(root):
        raw=json.loads(path.read_text());identifier=raw.get('id','')
        if not re.fullmatch(r'quality-trial-\d{4}-\d{2}-\d{2}-\d{6}',identifier):raise ValueError('试刊ID无效')
        state_path=root/'state/tech/trials'/(identifier+'.json')
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        if state.get('delivery')=='sent':return {'delivery':'already_sent','url':state['url'],'id':identifier}
        if state.get('delivery') in {'sending','uncertain'}:raise RuntimeError('试刊微信结果待核对，禁止自动重发')
        input_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        data=state['data'] if state.get('input_sha')==input_sha and state.get('data') else prepare(root,raw,now)
        if dry_run:return {'id':identifier,'quality':data['quality'],'cards':data['cards'],'delivery':'not_sent'}
        directory=root/'docs';paths=media.public_assets(root,data['cards'],directory)
        filename='docs/'+identifier+'.html';page=site.edition_html(data);atomic_write(root/filename,page);paths.append(filename)
        css=(Path(site.__file__).parent/'web/digest.css').read_text()+'\n'+(Path(site.__file__).parent/'web/quality.css').read_text()
        atomic_write(directory/'assets/digest-v3.css',css);paths.append('docs/assets/digest-v3.css')
        index=directory/'index.html'
        if index.exists():
            archive=re.sub(r'<section id="trials">.*?</section>','',index.read_text(),flags=re.S)
            atomic_write(index,archive.replace('</main>',site.trial_archive(directory)+'</main>'))
            paths.append('docs/index.html')
        state.update(id=identifier,input_sha=input_sha,data=data,delivery='not_sent',stage='rendered');atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2))
        if not push:return {'id':identifier,'path':str(root/filename),'quality':data['quality'],'delivery':'not_sent'}
        cfg=site.settings(root)
        if not site.enabled(root):raise RuntimeError('网页托管未启用')
        if site.git(root,['remote','get-url','origin']).rstrip('/')!='https://github.com/'+cfg['repository']+'.git':raise ValueError('Git远端与配置不一致')
        if site.git(root,['diff','--cached','--name-only']):raise RuntimeError('暂存区有其他修改')
        site.git(root,['add','--',*paths])
        if site.git(root,['diff','--cached','--name-only']):site.git(root,['commit','-m','归档图文优化试刊 '+data['day']])
        site.git(root,['push','origin','main']);state['stage']='github_pushed';atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2))
        url=cfg['base_url'].rstrip('/')+'/'+identifier+'.html';revision=re.search(r'name="digest-revision" content="([a-f0-9]+)"',page)[1]
        deadline=time.monotonic()+cfg.get('wait_seconds',180)
        while True:
            try:
                response=requests.get(url,timeout=15,headers={'Cache-Control':'no-cache'})
                ready=response.status_code==200 and 'content="'+revision+'"' in response.text and media.remote_ready(cfg['base_url'],data['cards'])
            except requests.RequestException:ready=False
            if ready:break
            if time.monotonic()>=deadline:raise RuntimeError('试刊页面或图片尚未上线，未发送微信；可重试同一试刊')
            time.sleep(5)
        state.update(url=url,stage='pages_ready');atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2))
        if notify:
            from mail_digest.delivery import send_wechat
            state['delivery']='sending';atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2))
            try:send_wechat(data['day'],'[打开图文优化试刊]('+url+')',title='技术日报 · 图文试刊 '+data['day'])
            except Exception:
                state['delivery']='uncertain';atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2));raise RuntimeError('试刊发送结果不明确，须核对后处理') from None
            state.update(delivery='sent',sent_at=now.isoformat(),stage='sent')
        atomic_write(state_path,json.dumps(state,ensure_ascii=False,indent=2))
        return {'id':identifier,'url':url,'delivery':state['delivery'] if notify else 'published','quality':data['quality']}


def resolve(root,identifier,decision,reason,now):
    if not re.fullmatch(r'quality-trial-\d{4}-\d{2}-\d{2}-\d{6}',identifier) or decision not in {'sent','retry'} or not reason.strip():raise ValueError('试刊核对参数无效')
    with site.publication_lock(root):
        path=root/'state/tech/trials'/(identifier+'.json');state=json.loads(path.read_text())
        if state.get('delivery') not in {'sending','uncertain'}:raise ValueError('没有待核对试刊')
        state.update(delivery='sent' if decision=='sent' else 'not_sent',resolution_reason=reason,resolved_at=now.isoformat())
        atomic_write(path,json.dumps(state,ensure_ascii=False,indent=2))
        return {'id':identifier,'resolution':decision}
