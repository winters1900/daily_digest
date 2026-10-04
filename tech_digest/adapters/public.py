import concurrent.futures
import html
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from .. import pipeline


class SourceError(RuntimeError):
    def __init__(self, message, status='error', retry_at=None):
        super().__init__(message)
        self.status=status
        self.retry_at=retry_at


class Http:
    """有界重试，异常消息不携带认证头、参数或密钥。"""
    def __init__(self, session=None, sleep=time.sleep):
        self.session=session or requests.Session();self.sleep=sleep
        self.session.headers.update({'User-Agent':'DailyDigest/2.0 (personal research digest)','Accept':'application/json,application/atom+xml,text/html;q=0.8'})

    def request(self, method, url, **kwargs):
        for attempt in range(3):
            try:
                response=self.session.request(method,url,timeout=(5,12),**kwargs)
            except requests.RequestException:
                if attempt==2:raise SourceError('网络请求失败') from None
                self.sleep(2**attempt);continue
            if response.status_code in {401,403}:raise SourceError('来源拒绝访问或需要登录','blocked')
            if response.status_code==429:
                delay=response.headers.get('Retry-After','3')
                try:seconds=max(0,float(delay))
                except ValueError:
                    try:seconds=max(0,(parsedate_to_datetime(delay)-datetime.now(timezone.utc)).total_seconds())
                    except (ValueError,TypeError):seconds=3
                if attempt==2 or seconds>20:
                    raise SourceError('接口限流，按 Retry-After 延后', 'blocked',
                                      (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat())
                self.sleep(seconds);continue
            if response.status_code>=500 and attempt<2:self.sleep(2**attempt);continue
            if response.status_code>=400:raise SourceError('HTTP '+str(response.status_code))
            return response
        raise SourceError('重试已耗尽')

    def get(self,url,**kwargs):return self.request('GET',url,**kwargs)


class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.links=[];self.text=[];self.current=None;self.suppress=0;self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style'}:self.suppress+=1
        if tag=='a' and attrs.get('href'):self.current=[attrs['href'],[]]
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.suppress=max(0,self.suppress-1)
        if tag=='a' and self.current:
            self.links.append((self.current[0],' '.join(self.current[1]).strip()));self.current=None
    def handle_data(self,data):
        if not self.suppress:self.text.append(data)
        if self.current and not self.suppress:self.current[1].append(data.strip())


def entry(source,title,url,**kwargs):
    return dict(kind=source['kind'],source_id=source['id'],title=html.unescape(title),url=url,**kwargs)


def _date(value, tz=None):
    if not value:return None
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(tz).date().isoformat() if tz else str(value)[:10]


def rss(source,http,now,progress):
    response=http.get(source['url']);feed=feedparser.parse(response.content)
    if not feed.entries:raise SourceError('未读到 RSS 条目，需浏览器确认','needs_browser')
    result=[]
    for e in feed.entries[:60]:
        link=e.get('link','')
        if link.startswith('http://'):link='https://'+link[7:]
        if not link.startswith('https://'):continue
        pub=e.get('published_parsed')  # updated 不充当首次发表。
        day=datetime(*pub[:6],tzinfo=timezone.utc).astimezone(now.tzinfo).date().isoformat() if pub else None
        body=e.get('content',[{}])[0].get('value') or e.get('summary','')
        result.append(entry(source,e.get('title',''),link,published_date=day,raw_text=' '.join(Page(body).text)[:14000],date_evidence_url=source['url'],author=e.get('author',source['name'])))
    return result,{'coverage':'sample','cursor':{'latest_entry':result[0]['url'] if result else None},'note':'读取 RSS 最新条目；RSS 不保证完整两个月覆盖'}


_arxiv_lock=threading.Lock()


def arxiv(source,http,now,progress):
    """最新页发现 + 固定历史快照 + 可恢复增量分页；后页失败不丢前页。"""
    from ..__main__ import window_start
    import copy
    cutoff=window_start(now,2).astimezone(timezone.utc).strftime('%Y%m%d%H%M')
    until=now.astimezone(timezone.utc).strftime('%Y%m%d%H%M')
    cursor=copy.deepcopy(progress.get('cursor',{}))
    history=copy.deepcopy(progress.get('backfill_cursor',{}))
    if history.get('sort_order')!='ascending':history={}
    history.setdefault('window_start',cutoff);history.setdefault('window_end',until)
    history.setdefault('offset',0);history.setdefault('sort_order','ascending')
    jobs=cursor.get('incremental_jobs',[])
    through=cursor.get('scheduled_through') or min(history['window_end'],until)
    if through<until:
        overlap=(datetime.strptime(through,'%Y%m%d%H%M')-timedelta(days=2)).strftime('%Y%m%d%H%M')
        start=max(cutoff,overlap)
        if jobs and jobs[-1].get('offset',0)==0:
            jobs[-1]['end']=until
        else:jobs.append({'start':start,'end':until,'offset':0})
    jobs=[job for job in jobs if job['end']>=cutoff]
    for job in jobs:
        if job['start']<cutoff:job.update(start=cutoff,offset=0)
    cursor.update(incremental_jobs=jobs,scheduled_through=until)
    cursor.pop('retry_not_before',None)
    result=[];failure=None;pages=0;category='('+' OR '.join('cat:'+cat for cat in source['categories'])+')'

    def fetch(start,end,offset,order):
        nonlocal pages
        query=category+' AND submittedDate:['+start+' TO '+end+']'
        with _arxiv_lock:
            response=http.get(source['url'],params={'search_query':query,'start':offset,'max_results':100,'sortBy':'submittedDate','sortOrder':order})
            http.sleep(3)
        feed=feedparser.parse(response.content)
        if feed.bozo or getattr(feed,'version',None)!='atom10':
            raise SourceError('arXiv 响应不是有效 Atom，未推进游标')
        raw_total=feed.feed.get('opensearch_totalresults')
        if raw_total is None or not re.fullmatch(r'\d+',str(raw_total)):
            raise SourceError('arXiv 缺少有效分页总数，未推进游标')
        total=int(raw_total)
        if not feed.entries and offset<total:raise SourceError('arXiv 分页为空但仍有未读取结果，保留游标等待重试')
        found=[]
        try:
            for e in feed.entries:
                rawid=e.id.rsplit('/abs/',1)[-1]
                found.append(entry(source,' '.join(e.title.split()),'https://arxiv.org/abs/'+rawid,
                    arxiv_id=re.sub(r'v\d+$','',rawid),first_public_date=_date(e.published,now.tzinfo),updated_date=_date(e.updated,now.tzinfo),
                    first_public_at=e.published,updated_at=e.updated,abstract=' '.join(e.summary.split()),
                    authors=[a.name for a in e.get('authors',[])],review_status='preprint',
                    date_evidence_url='https://arxiv.org/abs/'+rawid,categories=[t['term'] for t in e.get('tags',[])]))
        except (AttributeError,ValueError,KeyError,TypeError):raise SourceError('arXiv 条目无法解析，未推进该页游标') from None
        result.extend(found);pages+=1
        return len(feed.entries),total

    try:
        fetch(cutoff,until,0,'descending')
        if history['window_end']<cutoff:
            history.update(finished=True,completion_reason='历史快照已超出当前窗口')
        for _ in range(source.get('backfill_pages',1)):
            if history.get('finished'):break
            count,total=fetch(history['window_start'],history['window_end'],history['offset'],'ascending')
            history.update(offset=history['offset']+count,total=total)
            history['finished']=history['offset']>=total
        for _ in range(source.get('incremental_pages',4)):
            if not jobs:break
            job=jobs[0]
            count,total=fetch(job['start'],job['end'],job['offset'],'ascending')
            job.update(offset=job['offset']+count,total=total)
            if job['offset']>=total:jobs.pop(0)
    except SourceError as exc:
        failure=exc
        if exc.retry_at:cursor['retry_not_before']=exc.retry_at
    except (ValueError,KeyError,TypeError,requests.RequestException):
        failure=SourceError('arXiv 响应无法解析，保留已读取分页与游标')
    unique={c['arxiv_id']:c for c in reversed(result)}
    full=bool(history.get('finished')) and not jobs and not failure and not history.get('completion_reason')
    cursor['latest_id']=result[0]['arxiv_id'] if result else cursor.get('latest_id')
    meta={'coverage':'window_checked' if full else 'sample','backfill_complete':full,
          'backfill_cursor':history,'cursor':cursor,
          'note':'读取 %d 页；历史补查 %d/%s；待完成增量区间 %d；按首次提交时间筛选，未完成分页不代表全窗口覆盖'%(pages,history['offset'],history.get('total','未知'),len(jobs))}
    if failure:meta.update(status=failure.status,note=meta['note']+'；'+str(failure))
    return list(unique.values()),meta

def hf(source,http,now,progress):
    result=[]
    for record in http.get(source['url']).json():
        p=record.get('paper',{});pid=p.get('id')
        if not pid:continue
        result.append(entry(source,p.get('title',''),'https://arxiv.org/abs/'+pid,arxiv_id=pid,
                            abstract=p.get('summary',''),hf_published_at=p.get('publishedAt'),
                            authors=[a.get('name','') for a in p.get('authors',[])],engagement=p.get('upvotes',0),review_status='preprint',
                            verification_note='HF 日期仅为发现线索；审核时回到 arXiv 核验首次公开日期'))
    return result,{'coverage':'sample','note':'HF 每日列表，原论文日期待 arXiv 核验'}


def semantic(source,http,now,progress,root):
    seeds=yaml_seeds(root)
    positive=[s['paper_id'] for s in seeds if s.get('verified')]
    negative=[]
    with pipeline.connection(root) as conn:
        for uid,action in pipeline.feedback_actions(conn).items():
            row=conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()
            if not row:continue
            c=json.loads(row[0]);aliases=pipeline.identity_aliases(c)
            pid=next(('ARXIV:'+v[6:] for v in aliases if v.startswith('arxiv:')),None) or next(('DOI:'+v[4:] for v in aliases if v.startswith('doi:')),None) or c.get('semantic_scholar_id')
            if pid:
                (negative if action=='disliked' else positive).append(pid)
    positive=sorted(set(positive)-set(negative))
    if not positive:raise SourceError('没有已核验的种子论文','blocked')
    headers={}
    from ..credentials import semantic_key
    try:key=semantic_key()
    except RuntimeError:raise SourceError('系统钥匙串不可用，无法读取推荐接口凭据','blocked') from None
    if key:headers['x-api-key']=key
    response=http.request('POST',source['url'],params={'limit':100,'fields':'paperId,title,abstract,authors,url,externalIds,publicationDate,venue,year'},
                          json={'positivePaperIds':positive,'negativePaperIds':sorted(set(negative))},headers=headers)
    result=[]
    for p in response.json().get('recommendedPapers',[]):
        ids=p.get('externalIds') or {};url=p.get('url','')
        if ids.get('ArXiv'):url='https://arxiv.org/abs/'+ids['ArXiv']
        elif ids.get('DOI'):url='https://doi.org/'+ids['DOI']
        if not url.startswith('https://'):continue
        result.append(entry(source,p.get('title',''),url,semantic_scholar_id=p['paperId'],doi=ids.get('DOI'),arxiv_id=ids.get('ArXiv'),
                            abstract=p.get('abstract') or '',authors=[a['name'] for a in p.get('authors',[])],
                            s2_publication_date=p.get('publicationDate'),venue_hint=p.get('venue'),verification_note='推荐不代表已录用；日期与发表状态须回官方核验'))
    return result,{'coverage':'sample','note':'正向 %d、负向 %d 个明确种子；推荐元数据等待官方核验'%(len(positive),len(negative))}


def yaml_seeds(root):
    import yaml
    p=root/'research_seeds.yaml'
    return yaml.safe_load(p.read_text()).get('seeds',[]) if p.exists() else []


def hn(source,http,now,progress):
    ids=http.get(source['url']).json()[:60];result=[]
    def item(uid):return http.get('https://hacker-news.firebaseio.com/v0/item/'+str(uid)+'.json').json()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for p in pool.map(item,ids):
            if not p or p.get('type')!='story' or p.get('dead') or p.get('deleted'):continue
            url=p.get('url') or 'https://news.ycombinator.com/item?id='+str(p['id'])
            if url.startswith('http://'):url='https://'+url[7:]
            if not url.startswith('https://'):continue
            result.append(entry(source,p['title'],url,hn_id=p['id'],published_date=None,
                                discovered_event_date=datetime.fromtimestamp(p['time'],now.tzinfo).date().isoformat(),
                                engagement=p.get('score',0),discussion_url='https://news.ycombinator.com/item?id='+str(p['id']),
                                verification_note='HN 提交时间不是原文发表时间；需打开原文核验'))
    return result,{'coverage':'sample','note':'HN 前 60 条，仅发现线索，原文日期与内容待核验'}


def page(source,http,now,progress):
    response=http.get(source['url']);parsed=Page(response.text);result=[];seen=set()
    host=urlparse(source['url']).hostname
    for path,title in parsed.links:
        url=urljoin(source['url'],path)
        if urlparse(url).hostname!=host or url in seen or not title or len(title)<12:continue
        if not url.startswith('https://') or pipeline.canonical_url(url).rstrip('/')==pipeline.canonical_url(source['url']).rstrip('/'):continue
        if re.search(r'^(skip to|call for papers|submission format|proceedings specification|editorial board|editorial policies|contributions$)',title,re.I):continue
        if source['kind']=='paper':
            if not re.search(r'(paper|forum|abstract|html|s42256|volume|v\d+/|\.php)',url,re.I):continue
        else:
            if not re.search(r'(research|blog|news|release|publication)',url):continue
        seen.add(url);result.append(entry(source,title[:500],url,verification_note='索引链接待阅读全文，不能据目录推断日期或贡献'))
        if len(result)>=60:break
    return result,{'coverage':'index_only','note':'读取官方索引 %d 个候选链接，文章日期和正文待核验'%len(result),
                   'cursor':{'last_index':source['url']},'browser_required':source['url'] if not result else None}


def trending(source,http,now,progress):
    response=http.get(source['url']);p=Page(response.text);result=[];seen=set()
    for path,title in p.links:
        match=re.fullmatch(r'/([\w.-]+)/([\w.-]+)',path)
        if not match or path in seen or not title:continue
        if match[1] in {'collections','topics','sponsors','settings','login','orgs'}:continue
        seen.add(path);result.append(entry(source,title,'https://github.com'+path,repo=match[1]+'/'+match[2],
                                          verification_note='上榜不是新发布，需核验 README、Release 和具体变化'))
        if len(result)>=25:break
    return result,{'coverage':'index_only','note':'发现 GitHub Trending 项目，未以星数／提交时间推断技术新进展'}


def github_org(source,http,now,progress):
    result=[]
    for p in http.get(source['url'],params={'sort':'updated','per_page':25}).json():
        result.append(entry(source,p['full_name'],p['html_url'],repo=p['full_name'],abstract=p.get('description') or '',
                            verification_note='仓库更新时间不充当技术发布时间，审核具体 Release 或官方说明'))
    return result,{'coverage':'index_only','note':'读取官方组织仓库，具体技术发布需进一步核验'}


def youtube(source,http,now,progress):
    # 读取公开频道元数据，不抓私有字幕接口，也不调用付费转录。
    response=http.get(source['url']);text=response.text
    match=re.search(r'"(?:externalId|channelId)":"(UC[\w-]+)"',text)
    if not match:raise SourceError('频道 ID 不可读取，需要正常浏览器核验','needs_browser')
    feed_source=dict(source,url='https://www.youtube.com/feeds/videos.xml?channel_id='+match[1])
    result,meta=rss(feed_source,http,now,progress)
    for c in result:
        c['verification_note']='只读视频元数据；需取得公开字幕／官方逐字稿后审核，不能据标题生成内容摘要'
    meta['note']='频道 RSS 已发现视频；公开字幕仍待浏览器读取'
    meta['cursor']={'channel_id':match[1]}
    return result,meta


def conference(source,http,now,progress):
    if source['id'] not in {'paper:icml','paper:iclr','paper:tmlr','paper:cvpr','paper:eccv'}:
        return page(source,http,now,progress)
    from .official import conference as scan
    return scan(source,http,now,progress)

ADAPTERS={'rss':rss,'arxiv':arxiv,'hf':hf,'hn':hn,'conference':conference,'page':page,'trending':trending,'github_org':github_org,'youtube':youtube}


def collect(root,now,only=None):
    from ..__main__ import atomic_write
    schedule=pipeline.plan(root,now,all_sources=bool(only))
    known={s['id'] for s in pipeline.registry(root)}
    if only and set(only)-known:raise ValueError('未知来源 ID：'+', '.join(sorted(set(only)-known)))
    selected=[s for s in schedule['sources'] if not only or s['id'] in only]
    from ..__main__ import timestamp
    selected=[s for s in selected if not s['progress'].get('cursor',{}).get('retry_not_before') or timestamp(s['progress']['cursor']['retry_not_before'])<=now]
    def work(source):
        base={'id':source['id'],'observed_at':now.isoformat(),'window_months':2,'evidence_urls':[source['url']]}
        if source['adapter']=='browser':return [],dict(base,status='needs_browser',coverage='unknown',note='使用已登录 Codex 浏览器核验固定作者身份并读取原创帖子')
        try:
            http=Http()
            if source['adapter']=='semantic':items,meta=semantic(source,http,now,source['progress'],root)
            else:items,meta=ADAPTERS[source['adapter']](source,http,now,source['progress'])
            return items,dict(base,status=meta.pop('status','ok'),**meta)
        except SourceError as exc:
            return [],dict(base,status=exc.status,coverage='unknown',note=str(exc),cursor={'retry_not_before':exc.retry_at} if exc.retry_at else source['progress'].get('cursor',{}))
        except Exception:
            return [],dict(base,status='error',coverage='unknown',note='来源适配器无法解析响应，其他来源继续处理')
    items=[];statuses=[];ids=[];rejected=[]
    path=root/'state/tech/public_collection.json'
    # 每个来源完成即提交事务与恢复记录；一个慢来源／进程中断不丢其他源。
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(work,source):source for source in selected}
        for future in concurrent.futures.as_completed(futures):
            found,status=future.result()
            checkpoint={'pipeline_version':2,'observed_at':now.isoformat(),'sources':[status],'items':found,'reviews':[]}
            source_file=root/'state/tech/checkpoints'/ (status['id'].replace(':','_')+'.json')
            atomic_write(source_file,json.dumps(checkpoint,ensure_ascii=False,indent=2))
            saved=pipeline.ingest(root,checkpoint,now)
            items+=found;statuses+=saved['source_results'];ids+=saved['candidate_ids'];rejected+=saved['rejected']
            payload={'pipeline_version':2,'observed_at':now.isoformat(),'sources':statuses,'items':items,'reviews':[]}
            atomic_write(path,json.dumps(payload,ensure_ascii=False,indent=2))
    if not selected:
        atomic_write(path,json.dumps({'observed_at':now.isoformat(),'sources':[],'items':[],'reviews':[]},ensure_ascii=False))
    # 补查清单从持久状态重建，局部采集不能抹掉其他来源的未完成任务。
    with pipeline.connection(root) as conn:
        latest=[json.loads(r[0]) for r in conn.execute('SELECT details FROM source_runs WHERE id IN (SELECT max(id) FROM source_runs GROUP BY source_id)')]
    browser=[s for s in latest if s['status'] in {'needs_browser','blocked'} or s['coverage']=='index_only' or (s['coverage']=='sample' and s['id'] not in {'discovery:arxiv','discovery:hf','discovery:semantic-scholar'})]
    tasks={'observed_at':now.isoformat(),'sources':browser,'instructions':'使用正常 Codex 浏览器读取；身份检查用 phase: identity，内容检查用 phase: content，并保留真实证据、范围与补查游标。'}
    atomic_write(root/'state/tech/browser_tasks.json',json.dumps(tasks,ensure_ascii=False,indent=2))
    outcome='failed' if statuses and not any(s['status']=='ok' for s in statuses) else 'partial' if any(s['status']!='ok' for s in statuses) else 'partial' if any((not only or s['id'] in only) and s not in selected for s in schedule['sources']) else 'ok'
    return {'pipeline_version':2,'status':outcome,'ingested':len(ids),'candidate_ids':ids,'reviews':0,
            'source_results':statuses,'rejected':rejected,'delivery':'not_sent',
            'deferred_sources':[s['id'] for s in schedule['sources'] if (not only or s['id'] in only) and s not in selected],
            'collection_path':str(path),'browser_tasks':str(root/'state/tech/browser_tasks.json')}
