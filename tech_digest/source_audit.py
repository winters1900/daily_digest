"""独立来源实测：每源一个样本，与已发送日报隔离。"""
import argparse
import concurrent.futures
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import pipeline, site
from .adapters import public
from .__main__ import ROOT, atomic_write, window_start, timestamp


LABELS={'content_verified':'正文／摘要可读','discovery_only':'发现入口可读，原文未核验',
        'metadata_only':'视频元数据可读，字幕未取得','blocked':'受阻','error':'采集失败','empty':'未取得合格样本','needs_browser':'待浏览器补查'}


def save(path, data):
    atomic_write(path,json.dumps(data,ensure_ascii=False,indent=2))


def verify_sample(source, candidate, http):
    """访问样本原文，索引、登录墙和视频标题不能冒充正文。"""
    record=dict(title=candidate['title'],url=candidate['url'],date=candidate.get('published_date') or candidate.get('first_public_date'),
                date_evidence_url=candidate.get('date_evidence_url'),status='discovery_only',note='已发现样本，需继续核验内容及事件日期')
    if source['adapter']=='youtube':
        return dict(record,status='metadata_only',note='已取得频道视频条目，尚未取得字幕，不能生成视频摘要')
    if source['adapter'] in {'trending','github_org'}:
        repo=candidate.get('repo')
        if not repo:return record
        response=http.get('https://api.github.com/repos/'+repo+'/readme',headers={'Accept':'application/vnd.github.raw+json'})
        if len(response.text.strip())>200:
            record.update(status='content_verified',note='仓库 README 可读；技术发布需另核验 Release，仓库更新时间不当成发布日期')
        return record
    response=http.get(record['url']);parsed=public.Page(response.text)
    text=' '.join(parsed.text)
    if any(s in text.lower() for s in ['prove your humanity','verify you are human','just a moment...','verifying your browser','please complete the verification above']):
        return dict(record,status='blocked',note='原文出现访问验证，未取得可读内容')
    if source['kind']=='paper':
        actual=bool(re.search(r'abstract|摘要',text,re.I)) and len(text)>600
        # 会议索引、投稿入口及搜索页不能当成论文摘要。
        actual=actual and bool(re.search(r'/abs/|/forum\?|/abstract\.|/papers/.+\.html|/articles/|/hash/.+\.html|/v\d+/[^/]+\.html|_paper\.(?:php|html)|aclanthology\.org/\d{4}\.[^/]+/\Z|/conference/[^/]+/presentation/',record['url']))
    else:
        actual=len(text)>1000 and pipeline.canonical_url(record['url']).rstrip('/')!=pipeline.canonical_url(source['url']).rstrip('/')
    if actual:record.update(status='content_verified',note='样本原文页面可读；日期完整性和选题质量单独审核，未声称复现')
    return record


def probe(source, now):
    row=dict(id=source['id'],name=source.get('name') or '/'.join(source.get('venues',[])),kind=source['kind'],
             adapter=source['adapter'],source_url=source['url'],organization=source.get('organization',False),checked_at=now.isoformat(),status='needs_browser',note='需使用正常登录浏览器检查')
    if source['adapter']=='browser':return row
    retry=source.get('progress',{}).get('cursor',{}).get('retry_not_before')
    if retry and timestamp(retry)>now:
        return dict(row,status='blocked',retry_at=retry,note='来源仍在 Retry-After 冷却期，未提前请求')
    try:
        # 独立只读抽样，不推进日常补查游标，也不将测试样本标记为已推荐。
        http=public.Http();settings=dict(source,backfill_pages=0,incremental_pages=0)
        fn=public.semantic if source['adapter']=='semantic' else public.ADAPTERS[source['adapter']]
        args=[settings,http,now,{}]+([ROOT] if source['adapter']=='semantic' else [])
        candidates,meta=fn(*args)
        row.update(discovered=len(candidates),coverage=meta.get('coverage'),note=meta.get('note',''))
        row['retry_at']=meta.get('cursor',{}).get('retry_not_before')
        if not candidates:return dict(row,status=meta.get('status','empty'),note=row['note']+'；未取得样本')
        cutoff=window_start(now,2).date().isoformat()
        dated=[c for c in candidates if cutoff<=(c.get('published_date') or c.get('first_public_date') or '')[:10]<=now.date().isoformat()]
        selected=next(iter(dated or candidates))
        sample=verify_sample(source,selected,http)
        row.update(status=sample['status'],sample=sample,note=sample['note'])
        if not sample.get('date'):row['note']+='；精确日期未知，不能直接进入最近两个月日报'
        elif not cutoff<=sample['date'][:10]<=now.date().isoformat():row['note']+='；样本在日报窗口外，仅证明可读取，不进入日常精选'
    except public.SourceError as exc:
        row.update(status=exc.status,note=str(exc),retry_at=exc.retry_at)
    except Exception:
        row.update(status='error',note='来源响应或解析失败；未把配置存在当成接通')
    return row


def collect():
    now=datetime.now(ZoneInfo('Asia/Shanghai'));identifier='source-check-'+now.strftime('%Y-%m-%d-%H%M%S')
    path=ROOT/'state/tech/source_audits'/ (identifier+'.json')
    report=dict(id=identifier,observed_at=now.isoformat(),window_start=window_start(now,2).date().isoformat(),
                rows=[],stage='collecting',delivery='not_sent')
    sources=pipeline.plan(ROOT,now,all_sources=True)['sources']
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(probe,s,now):s for s in sources}
        for f in concurrent.futures.as_completed(futures):
            report['rows'].append(f.result());save(path,report)
    order={s['id']:i for i,s in enumerate(sources)}
    report['rows'].sort(key=lambda r:order[r['id']]);report['stage']='collected';save(path,report)
    return path


def render(report):
    counts={s:sum(r['status']==s for r in report['rows']) for s in LABELS}
    body='<nav><a class="brand" href="index.html">技术日报<span>来源实测</span></a></nav><main><header class="masthead"><p>'+site.esc(report['observed_at'][:10])+'</p><h1>全部来源实测</h1></header>'
    body+='<p>逐源抽样，不受日常精选额度限制。正文可读、目录线索和字幕缺失分开记录；受限来源不伪造样本。此次测试不代表完整两个月覆盖，也不代表每条样本符合日报质量要求。</p>'
    body+='<p>来源总数 '+str(len(report['rows']))+'；'+ ' · '.join(site.esc(LABELS[k])+str(v) for k,v in counts.items() if v)+'</p>'
    body+='<p>日常精选现为最多 10 篇论文＋10 条资讯，质量门槛不变。本页仅用于来源诊断，窗口外样本不进入日报。</p>'
    body+='<nav aria-label="目录"><a href="#paper">论文来源</a> · <a href="#x">固定 X 作者</a> · <a href="#news">资讯与视频来源</a></nav>'
    for kind,label in [('paper','论文来源'),('x','固定 X 作者'),('news','资讯与视频来源')]:
        body+='<section id="'+kind+'" class="section"><div class="section-title"><h2>'+label+'</h2></div>'
        for r in report['rows']:
            if r['kind']!=kind:continue
            sample=r.get('sample',{});body+='<article class="card"><div class="meta"><span class="badge">'+site.esc(LABELS[r['status']])+'</span><span>'+site.esc(r['id'])+'</span></div><h3>'+site.link(r['source_url'],r['name'])+'</h3>'
            if sample:body+='<p>'+site.link(sample['url'],sample['title'])+'</p><p>样本日期：'+site.esc(sample.get('date') or '未知')+'</p>'
            if r.get('organization'):body+='<p>机构官方账号</p>'
            body+='<p>'+site.esc(r['note'])+'</p></article>'
        body+='</section>'
    body+='</main>'
    revision=hashlib.sha256(body.encode()).hexdigest()[:20]
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="source-audit-revision" content="'+revision+'"><title>全部来源实测</title><link rel="stylesheet" href="assets/digest.css"></head><body>'+body+'</body></html>',revision


def publish(path):
    from mail_digest.delivery import send_wechat
    with site.publication_lock(ROOT):
        report=json.loads(path.read_text());identifier=report['id']
        if not re.fullmatch(r'source-check-\d{4}-\d{2}-\d{2}-\d{6}',identifier):raise ValueError('测试报告 ID 无效')
        if report.get('delivery')=='sent':return dict(delivery='already_sent',url=report['url'])
        if report.get('delivery') in {'sending','uncertain'}:raise RuntimeError('测试推送结果待核对，禁止自动重发')
        page,rev=render(report);filename='docs/'+identifier+'.html';atomic_write(ROOT/filename,page)
        if site.git(ROOT,['diff','--cached','--name-only']):raise RuntimeError('暂存区有其他修改')
        site.git(ROOT,['add','--',filename])
        if site.git(ROOT,['diff','--cached','--name-only']):site.git(ROOT,['commit','-m','归档全部来源实测报告'])
        site.git(ROOT,['push','origin','main']);report['stage']='github_pushed';save(path,report)
        url=site.settings(ROOT)['base_url'].rstrip('/')+'/'+identifier+'.html'
        deadline=time.monotonic()+site.settings(ROOT).get('wait_seconds',180)
        while True:
            try:
                response=requests.get(url,timeout=15,headers={'Cache-Control':'no-cache'})
                ready=response.status_code==200 and 'content="'+rev+'"' in response.text
            except requests.RequestException:ready=False
            if ready:break
            if time.monotonic()>=deadline:raise RuntimeError('测试页面尚未上线，不发送无效链接；可稍后重试发布')
            time.sleep(5)
        report.update(stage='pages_ready',url=url,delivery='sending');save(path,report)
        try:send_wechat(report['observed_at'][:10],'[打开全部来源实测报告]('+url+')',title='技术来源实测 · '+report['observed_at'][:10])
        except Exception:
            report['delivery']='uncertain';save(path,report);raise RuntimeError('微信结果待核对，不自动重发') from None
        report.update(delivery='sent',sent_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat());save(path,report)
        return dict(delivery='sent',url=url,sources=len(report['rows']))


def main():
    parser=argparse.ArgumentParser(description='独立来源实测，原始数据留本地，公开HTML单独推送')
    parser.add_argument('--publish',type=Path);parser.add_argument('--render',type=Path)
    args=parser.parse_args()
    if args.publish:result=publish(args.publish)
    elif args.render:
        report=json.loads(args.render.read_text());page,_=render(report);atomic_write(ROOT/('docs/'+report['id']+'.html'),page);result={'rendered':report['id']}
    else:result={'report':str(collect())}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
