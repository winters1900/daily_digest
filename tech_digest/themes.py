"""只从冻结的已发送日报生成公开主题归档，不读取私人偏好或候选池。"""
from collections import defaultdict


def render(editions):
    from . import site,pipeline
    groups=defaultdict(list);seen=set()
    for edition in editions:
        for card in edition['cards']:
            uid=card.get('id',card['url'])
            if uid in seen:continue
            seen.add(uid);groups[card.get('topic','other')].append((edition['day'],card,edition.get('pipeline_version')))
    e=site.esc
    body='<div class="masthead"><div class="date">RESEARCH</div><h1>研究主题归档</h1><p>来自已推送日报，按研究方向回看。</p></div>'
    body+='<div class="tabs">'+''.join('<a href="#topic-'+e(t)+'">'+e(pipeline.TOPIC_NAMES.get(t,'其他'))+'</a>' for t in groups)+'</div>'
    for topic,rows in groups.items():
        body+='<section class="section" id="topic-'+e(topic)+'"><h2>'+e(pipeline.TOPIC_NAMES.get(topic,'其他'))+'</h2>'
        for day,c,version in rows:
            body+='<article class="card"><div class="meta">'+e(day)+' · '+e(c.get('reading_depth','未记录'))+'</div><h3>'+site.link(c['url'],c['title'])+'</h3>'
            body+='<p>'+e(c.get('contribution') or c.get('summary',''))+'</p><p>'+e(' / '.join(c.get('research_tags',[])))+'</p>'
            anchor='#item-'+e(c['id']) if version==3 and c.get('id') else ''
            body+='<a href="'+e(day)+'.html'+anchor+'">阅读当日日报 →</a>'
            for r in c.get('related_links',[]):body+=' · '+site.link(r['url'],r.get('title','相关研究'))
            body+='</article>'
        body+='</section>'
    if not groups:body+='<p>暂无已推送的主题内容。</p>'
    return site.shell('研究主题归档',body)
