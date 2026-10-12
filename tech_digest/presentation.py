"""第三版图文卡片；Janus Client 风格渲染。"""
import hashlib
import json
from pathlib import Path

from . import site
from .quality import focus_cards


def figure_html(f):
    if not f:
        return ''
    if not f.get('asset'):
        return '<p>' + site.link(f['source_url'], '查看原论文 ' + f['figure_number']) + '</p>'
    title = ('据原文数据重绘 · ' if f['kind'] == 'redraw' else '原论文关键图 · ') + f['figure_number']
    content = '<figure class="paper-figure"><a href="' + site.esc(f['asset']) + '" target="_blank" rel="noopener"><img src="' + site.esc(f['asset']) + '" width="' + str(f['width']) + '" height="' + str(f['height']) + '" loading="lazy" decoding="async" alt="' + site.esc(f['alt']) + '"></a><figcaption><strong>' + site.esc(title) + '</strong><p>' + site.esc(f['caption']) + '</p>'
    if f.get('conditions'):
        content += '<p>条件：' + site.esc(f['conditions']) + '</p>'
    content += site.link(f['source_url'], '出处 · ' + f['version'])
    if f.get('kind') == 'original':
        content += ' · ' + site.esc(f['attribution']) + ' · ' + site.link(f['license_url'], f['license'])
    return content + '</figcaption></figure>'


def card_html(c):
    e = site.esc
    p = site.prose
    link = site.link
    state = {'preprint': '预印本', 'accepted': '已录用', 'published': '已发表'}.get(c.get('review_status'), '技术资讯')
    track = {'main': '主会', 'journal': '期刊', 'findings': 'Findings', 'workshop': 'Workshop', 'demo': 'Demo'}.get(c.get('track'), '')
    venue = c.get('venue', c.get('author', ''))
    if track:
        venue = venue + ' ' + track

    out = '<article class="paper' + (' focus-paper' if c.get('focus') else '') + '">'
    # title
    out += '<h3>' + link(c['url'], c['title']) + '</h3>'

    # date
    date_parts = [c.get('published_label', '')]
    if c.get('historical_backfill'):
        date_parts.append('历史补查首次推荐' if not c.get('trial_revisit') else '历史内容重审')
    if c.get('event_type') == 'publication_update':
        date_parts.append('发表动态')
    out += '<div class="paper-date">' + e(' · '.join(x for x in date_parts if x)) + '</div>'

    # keywords (papers only)
    if c.get('paper_type'):
        from .research import PAPER_TYPES
        kw = e(PAPER_TYPES[c['paper_type']][0])
        if c.get('research_tags'):
            kw += ' · ' + e(' / '.join(c['research_tags']))
        out += '<div class="paper-field"><div class="field-label">Keywords</div><p>' + kw + '</p></div>'

    # fields
    facts = [('研究问题', 'problem'), ('核心贡献', 'contribution'), ('结果与条件', 'results')] if c['kind'] == 'paper' else [('解决什么问题', 'problem'), ('技术变化', 'contribution'), ('价值与适用条件', 'results')]
    for label, key in facts:
        out += '<div class="paper-field"><div class="field-label">' + label + '</div>' + p(c[key]) + '</div>'

    # conditions
    if c.get('conditions'):
        out += '<p class="paper-conditions">' + e(c['conditions']) + '</p>'

    # figure
    out += figure_html(c.get('figure'))

    # why / limitations / reading advice — combined callout
    why_parts = []
    if c.get('why'):
        why_parts.append('<p><strong>为什么读</strong>　' + e(c['why']) + '</p>')
    if c.get('limitations'):
        why_parts.append('<p><strong>主要限制</strong>　' + e(c['limitations']) + '</p>')
    if c.get('reading_advice'):
        why_parts.append('<p><strong>阅读建议</strong>　' + e(c['reading_advice']) + '</p>')
    if why_parts:
        out += '<div class="paper-why">' + ''.join(why_parts) + '</div>'

    # links
    out += '<div class="paper-links">' + link(c['url'], '原文 ↗')
    for key, label in [('code_url', '代码 ↗'), ('verification_url', '会刊核验 ↗'), ('date_evidence_url', '日期依据 ↗')]:
        if c.get(key):
            out += link(c[key], label)
    out += '</div>'

    # details: claims
    out += '<details><summary>结论证据与阅读记录</summary><ul>'
    for claim in c['claims']:
        out += '<li>' + e(claim['text']) + ' · ' + link(claim['url'], claim['locator']) + '<br><small>' + e(claim['excerpt']) + '</small></li>'
    out += '</ul>'
    if c.get('findings'):
        out += '<dl>'
        for f in c['findings']:
            out += '<dt>' + e(f['statement']) + '</dt><dd>指标：' + e(f['metric']) + '；比较：' + e(f['comparison']) + '；条件：' + e(f['conditions']) + '<br>' + e(f['support_reason']) + '</dd>'
        out += '</dl>'
    if c.get('type_details'):
        from .research import DETAIL_LABELS
        out += '<dl>' + ''.join('<dt>' + e(DETAIL_LABELS.get(k, k)) + '</dt><dd>' + e(v) + '</dd>' for k, v in c['type_details'].items()) + '</dl>'
    if c.get('read_sections'):
        out += '<p>' + e('；'.join(c['read_sections'].values())) + '</p>'
    out += '<p>发现渠道：' + e(' / '.join(c.get('discovery_labels', c.get('discovery_sources', [])))) + '</p></details>'

    # related links
    if c.get('related_links'):
        labels = {'duplicate': '同一事件', 'commentary': '相关解读', 'contrast': '不同观点', 'extends': '研究延伸', 'compares': '比较研究', 'complements': '互补研究'}
        out += '<details><summary>相关研究与解读</summary><ul>'
        seen = set()
        for r in c['related_links']:
            if r['url'] in seen:
                continue
            seen.add(r['url'])
            label = labels.get(r.get('relation'), '相关内容')
            if r.get('relation') == 'extends':
                label = '扩展的前作' if r.get('direction') == 'outgoing' else '后续扩展'
            out += '<li>' + e(label) + ' · ' + link(r['url'], r['title'])
            if r.get('reason'):
                out += ' · ' + e(r['reason'])
            out += '</li>'
        out += '</ul></details>'

    return out + '</article>'


def edition_html(data):
    from .pipeline import NEWS_SECTIONS
    cards = data['cards']
    e = site.esc
    groups = [('peer_reviewed', '已录用会议／正式期刊'), ('arxiv', 'arXiv 前沿'), ('recommended', 'Semantic Scholar 推荐')]
    sections = [(a, t, [c for c in cards if c['kind'] == 'paper' and c.get('paper_section') == a]) for a, t in groups]
    sections += [('news-' + str(i), t, [c for c in cards if c['kind'] != 'paper' and c['news_section'] == t]) for i, t in enumerate(NEWS_SECTIONS)]
    sections = [s for s in sections if s[2]]

    title = '技术日报 · 优化试刊' if data.get('edition_type') == 'trial' else '技术日报'

    # hero
    body = '<header class="hero"><div class="hero-meta"><span>' + e(data['day'].replace('-', ' 年 ').replace('-', ' 月 ') + ' 日') + '</span></div>'
    body += '<h1>' + e(title) + '</h1></header>'

    # TOC
    body += '<aside class="toc"><div class="toc-title">目录</div><ul>'
    for a, t, _ in sections:
        body += '<li><a href="#' + a + '">' + e(t) + '</a></li>'
    body += '</ul></aside>'

    # article
    body += '<article class="article">'
    for a, t, g in sections:
        body += '<h2 id="' + a + '">' + e(t) + '</h2>'
        ordered = sorted(g, key=lambda c: not c.get('focus', False))
        for c in ordered:
            body += '<div id="item-' + e(c['id']) + '">' + card_html(c) + '</div>'

    # collection notes
    body += '<details class="collection-notes"><summary>入选说明与来源覆盖</summary>'
    for reason in data.get('shortfalls', []):
        body += '<p>' + e(reason) + '</p>'
    if data.get('edition_type') == 'trial':
        body += '<p>本期为独立图文试刊，重新核验既有内容，不代表新增采集或改变历史推荐记录。</p>'
    for source in data.get('sources', []):
        body += '<p>' + e(source['id']) + ' · ' + e(source.get('coverage', 'unknown')) + ' · ' + e(source.get('note', '')) + '</p>'
    for w in data.get('quality', {}).get('warnings', []):
        body += '<p>' + e(w['reason']) + '</p>'
    body += '</details>'
    body += '<p style="margin-top:24px;"><a href="themes.html" style="color:var(--accent);">按研究主题回看 →</a></p>'
    body += '</article>'

    # shell with revision
    style = (Path(__file__).parent / 'web/janus.css').read_bytes()
    revision = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode() + style + Path(__file__).read_bytes()).hexdigest()
    nav_links = [('#' + a, t) for a, t, _ in sections] + [('index.html', '归档')]
    return site.janus_shell(title + ' · ' + data['day'], body, revision, nav_links=nav_links)
