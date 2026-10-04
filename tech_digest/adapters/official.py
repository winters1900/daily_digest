"""官方会刊的分层发现；缺日期或最终决定时保留待核验，不猜录用。"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin,urlparse

from .public import Page,entry,SourceError


class Metadata(HTMLParser):
    def __init__(self,text):
        super().__init__();self.fields={};self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta':
            name=a.get('name',a.get('property','')).lower()
            if name and a.get('content'):self.fields.setdefault(name,[]).append(a['content'])


def conference(source,http,now,progress):
    if source.get('openreview_groups'):
        from .openreview import collect
        found,meta=collect(source,http,now,progress)
        if meta['status']=='ok':return found,meta
        # API失效不丢已有官方目录入口；失败状态保留，目录不冒充最终决定。
        fallback=dict(source);fallback.pop('openreview_groups')
        try:
            entries,extra=conference(fallback,http,now,progress)
            existing={c['url'] for c in found}
            found.extend(c for c in entries if c['url'] not in existing)
            cursor=dict(extra.get('cursor',{}));cursor.update(meta.get('cursor',{}));meta['cursor']=cursor
            meta['coverage']=extra.get('coverage','index_only')
            meta['note']+='；官方目录补充 %d 条待核验线索，不视为 API 已恢复'%len(entries)
            meta['evidence_urls']=list(dict.fromkeys(meta.get('evidence_urls',[])+[source['url']]))
        except SourceError:pass
        return found,meta
    root=source['url'];text=http.get(root).text;page=Page(text)
    venues=source.get('venues',[]);sid=source['id'];indices=[]
    if sid=='paper:icml':
        # PMLR contains many unrelated workshops. Only exact ICML conference volumes.
        blocks=re.findall(r'<li\b[^>]*>(.*?)</li>',text,re.S|re.I)
        rows=[(' '.join(Page(block).text),Page(block).links) for block in blocks] if blocks else [(t,[(h,t)]) for h,t in page.links]
        for title,row_links in rows:
            if re.search(r'(international conference on machine learning|Proceedings of ICML \d{4}\s*$|ICML \d{4} Proceedings\s*$)',title,re.I) and not re.search('workshop',title,re.I):
                for href,_ in row_links:
                    url=urljoin(root,href)
                    if urlparse(url).hostname=='proceedings.mlr.press' and re.search(r'/v\d+/?$',url):indices.append(url.rstrip('/')+'/')
    elif sid=='paper:cvpr':
        indices=[urljoin(root,h) for h,t in page.links if re.search(r'/CVPR\d{4}/?$',urljoin(root,h)) and int(re.search(r'CVPR(\d{4})',h)[1])>=now.year-1]
    else:indices=[root]
    if not indices:return [],{'status':'needs_browser','coverage':'index_only','note':'官方主页未取得精确会议卷入口，需浏览器补查','browser_required':root,'cursor':progress.get('cursor',{})}
    links=[]
    for index in list(dict.fromkeys(indices))[:2]:
        parsed=page if index==root else Page(http.get(index).text)
        if sid=='paper:cvpr':
            all_url=next((urljoin(index,h) for h,t in parsed.links if 'day=all' in h),None)
            if all_url:parsed=Page(http.get(all_url).text)
        detail_links=parsed.links
        if sid=='paper:icml':
            detail_links=[]
            volume_text=http.get(index).text
            for block in re.findall(r'<div class="paper">(.*?)</div>',volume_text,re.S|re.I):
                title_match=re.search(r'<p class="title">(.*?)</p>',block,re.S|re.I)
                title=' '.join(Page(title_match[1]).text) if title_match else ''
                for h,t in Page(block).links:
                    if re.search(r'/v\d+/[^/]+\.html$',h) and title:detail_links.append((h,title))
            if not detail_links:detail_links=parsed.links
        if sid=='paper:tmlr':
            detail_links=[]
            for block in re.findall(r'<li\b[^>]*>(.*?)</li>',text,re.S|re.I):
                row=Page(block);title=next((t for h,t in row.links if 'openreview.net/pdf?' in h and len(t)>12),'')
                forum=next((h for h,t in row.links if 'openreview.net/forum?' in h),None)
                if title and forum:detail_links.append((forum,title))
        for h,t in detail_links:
            url=urljoin(index,h);host=urlparse(url).hostname
            if host not in {'proceedings.mlr.press','openaccess.thecvf.com','www.ecva.net','openreview.net','jmlr.org'}:continue
            if len(t)<12:continue
            if not re.search(r'/v\d+/[^/]+\.html$|_paper\.(?:html|php)$|openreview\.net/forum\?id=',url):continue
            links.append((url,t))
    cursor=dict(progress.get('cursor',{}));done=cursor.get('checked_details',[])
    pending=cursor.get('pending_details',[])
    queued={p[0] for p in pending}|set(done)
    for url,title in links:
        if url not in queued:pending.append([url,title]);queued.add(url)
    result=[];budget=source.get('detail_budget',12);failure=None
    for url,title in pending[:budget]:
        # OpenReview forum is a dynamic page. Keep the official acceptance-list linkage;
        # the final decision and its timestamp still need a normal browser reading.
        if urlparse(url).hostname=='openreview.net':
            result.append(entry(source,title,url,openreview_id=re.search(r'id=([^&]+)',url)[1],verification_url=root,
                                publication_missing=['官方最终决定与轨道','录用/发表精确日期'],
                                verification_note='来自官方会刊名单，最终录用决定、轨道与日期仍待核验'))
        else:
            try:detail=http.get(url).text
            except SourceError as exc:failure=exc;break
            meta=Metadata(detail).fields;content=Page(detail)
            value=lambda k:next(iter(meta.get(k,[])),None)
            date=value('citation_publication_date') or value('citation_date')
            exact=re.fullmatch(r'\d{4}[-/]\d{2}[-/]\d{2}',date or '')
            abstract=value('description') or value('dc.description') or ''
            if not abstract:
                plain=' '.join(content.text);match=re.search(r'Abstract\s+(.{80,4000}?)(?:BibTeX|Download PDF|Paper and Supplementary Material|Copyright|References)',plain,re.S|re.I)
                if match:abstract=match[1].strip()
            result.append(entry(source,value('citation_title') or title,url,abstract=abstract,
                                authors=meta.get('citation_author',[]),published_date=date.replace('/','-') if exact else None,
                                doi=value('citation_doi'),verification_url=url,
                                publication_missing=[] if exact else ['官方论文页缺少精确发表日期'],
                                verification_note='官方论文详情已读取；日期仅采用明确年月日，发表状态和轨道待审核'))
        done.append(url)
    cursor.update(checked_details=done[-10000:],pending_details=pending[len(result):])
    if failure and failure.retry_at:cursor['retry_not_before']=failure.retry_at
    return result,{'status':failure.status if failure else ('ok' if result else 'needs_browser'),'coverage':'sample','note':'分层处理官方论文详情／决定入口 %d 条；未完成 %d 条，日期与最终决定逐条审核，非完整窗口覆盖'%(len(result),len(pending[len(result):])),
                   'cursor':cursor,'browser_required':root}
