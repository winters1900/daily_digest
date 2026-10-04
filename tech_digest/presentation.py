"""第三版图文卡片；与历史页面样式隔离。"""
import hashlib
import json
from pathlib import Path

from . import site
from .quality import focus_cards


def figure_html(f):
    if not f:return ''
    if not f.get('asset'):return '<p>'+site.link(f['source_url'],'查看原论文 '+f['figure_number'])+'</p>'
    title=('据原文数据重绘 · ' if f['kind']=='redraw' else '原论文关键图 · ')+f['figure_number']
    content='<figure class="paper-figure"><a href="'+site.esc(f['asset'])+'" target="_blank" rel="noopener"><img src="'+site.esc(f['asset'])+'" width="'+str(f['width'])+'" height="'+str(f['height'])+'" loading="lazy" decoding="async" alt="'+site.esc(f['alt'])+'"></a><figcaption><strong>'+site.esc(title)+'</strong><p>'+site.esc(f['caption'])+'</p>'
    if f.get('conditions'):content+='<p>条件：'+site.esc(f['conditions'])+'</p>'
    content+=site.link(f['source_url'],'出处 · '+f['version'])
    if f['kind']=='original':content+=' · '+site.esc(f['attribution'])+' · '+site.link(f['license_url'],f['license'])
    return content+'</figcaption></figure>'


def card_html(c):
    e=site.esc;p=site.prose;link=site.link
    state={'preprint':'预印本 · 未同行评审','accepted':'已录用','published':'已发表'}.get(c.get('review_status'),'技术资讯')
    track={'main':'主会','journal':'期刊','findings':'Findings','workshop':'Workshop','demo':'Demo'}.get(c.get('track'),'')
    out='<article class="card'+(' focus-card' if c.get('focus') else '')+'"><div class="eyebrow"><span class="badge">'+e(state)+'</span><span>'+e(c.get('venue',c.get('author','')))+' '+e(track)+'</span><span>阅读：'+e(c['reading_depth'])+'</span></div>'
    out+='<h3>'+link(c['url'],c['title'])+'</h3><div class="meta">'+e(c.get('published_label',''))+((' · 历史内容重审' if c.get('trial_revisit') else ' · 历史补查首次推荐') if c.get('historical_backfill') else '')+(' · 发表动态' if c.get('event_type')=='publication_update' else '')+'</div>'
    for label,key in [('研究问题','problem'),('核心贡献','contribution'),('结果与条件','results')]:
        out+='<div class="card-fact"><strong>'+label+'</strong>'+p(c[key])+'</div>'
    out+='<p class="conditions">'+e(c['conditions'])+'</p>'
    out+=figure_html(c.get('figure'))
    out+='<p class="value"><strong>为什么读</strong>'+e(c['why'])+'</p><p><strong>主要限制</strong> '+e(c['limitations'])+'</p>'
    out+='<div class="action"><strong>阅读建议</strong>'+e(c['reading_advice'])+'</div>'
    out+='<div class="links">'+link(c['url'],'原文 ↗')
    for key,label in [('code_url','代码 ↗'),('verification_url','会刊核验 ↗'),('date_evidence_url','日期依据 ↗')]:
        if c.get(key):out+=link(c[key],label)
    out+='</div><details><summary>结论证据与阅读记录</summary><ul>'
    for claim in c['claims']:out+='<li>'+e(claim['text'])+' · '+link(claim['url'],claim['locator'])+'<br><small>'+e(claim['excerpt'])+'</small></li>'
    out+='</ul>'
    if c.get('read_sections'):out+='<p>'+e('；'.join(c['read_sections'].values()))+'</p>'
    out+='<p>发现渠道：'+e(' / '.join(c.get('discovery_labels',c.get('discovery_sources',[]))))+'</p></details></article>'
    return out


def edition_html(data):
    from .pipeline import NEWS_SECTIONS
    cards=data['cards'];e=site.esc
    groups=[('peer_reviewed','已录用会议／正式期刊'),('arxiv','arXiv 前沿'),('recommended','Semantic Scholar 推荐')]
    sections=[(a,t,[c for c in cards if c['kind']=='paper' and c.get('paper_section')==a]) for a,t in groups]
    sections += [('news-'+str(i),t,[c for c in cards if c['kind']!='paper' and c['news_section']==t]) for i,t in enumerate(NEWS_SECTIONS)]
    sections=[s for s in sections if s[2]]
    title='技术日报 · 优化试刊' if data.get('edition_type')=='trial' else '技术日报'
    body='<div class="masthead"><div class="date">'+e(data['day'].replace('-','.'))+'</div><h1>'+title+'</h1><div class="tabs">'+''.join('<a href="#'+a+'">'+e(t)+' · '+str(len(g))+'</a>' for a,t,g in sections)+'</div></div>'
    focus=focus_cards(cards)
    if focus:body+='<div class="highlights"><strong>本期重点精读</strong>'+''.join('<a href="#item-'+e(c['id'])+'">'+e(c['title'])+'</a>' for c in focus)+'</div>'
    body+='<div class="layout"><div>'
    for a,t,g in sections:
        ordered=sorted(g,key=lambda c:not c.get('focus',False))
        body+='<section class="section" id="'+a+'"><div class="section-title"><h2>'+e(t)+'</h2></div>'
        body+=''.join('<div id="item-'+e(c['id'])+'">'+card_html(c)+'</div>' for c in ordered)+'</section>'
    body+='<details class="collection"><summary>入选说明与来源覆盖</summary>'
    for reason in data.get('shortfalls',[]):body+='<p>'+e(reason)+'</p>'
    if data.get('edition_type')=='trial':body+='<p>本期为独立图文试刊，重新核验既有内容，不代表新增采集或改变历史推荐记录。</p>'
    for source in data.get('sources',[]):body+='<p>'+e(source['id'])+' · '+e(source.get('coverage','unknown'))+' · '+e(source.get('note',''))+'</p>'
    for w in data.get('quality',{}).get('warnings',[]):body+='<p>'+e(w['reason'])+'</p>'
    body+='</details></div><aside class="side"><div class="side-inner"><p>本期目录</p>'+''.join('<a href="#'+a+'">'+e(t)+'</a>' for a,t,_ in sections)+'</div></aside></div>'
    style=(Path(__file__).parent/'web/digest.css').read_bytes()+b'\n'+(Path(__file__).parent/'web/quality.css').read_bytes()
    revision=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()+style+Path(__file__).read_bytes()).hexdigest()
    import re
    return re.sub(r'assets/digest\.css\?v=[a-f0-9]+','assets/digest-v3.css?v='+hashlib.sha256(style).hexdigest()[:12],site.shell(title+' · '+data['day'],body,revision))
