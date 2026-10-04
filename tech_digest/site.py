"""日报 HTML 与同仓库归档；只发布公开技术内容。"""
import hashlib
from contextlib import contextmanager
import fcntl
import html
import json
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import bleach
import markdown
import requests
import yaml


def esc(value):
    return html.escape(str(value), quote=True)


def link(url, label):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return esc(label)
    return '<a href="' + esc(url) + '" rel="noopener noreferrer">' + esc(label) + '</a>'


def prose(value):
    return bleach.clean(markdown.markdown(str(value), extensions=['tables', 'fenced_code']),
                        tags=['p','a','strong','em','code','pre','h1','h2','h3','h4','ul','ol','li','blockquote','br','hr','table','thead','tbody','tr','th','td'],
                        attributes={'a':['href','title']}, protocols=['https'], strip=True)


def shell(title, body, revision=''):
    style_version = hashlib.sha256((Path(__file__).parent / 'web/digest.css').read_bytes()).hexdigest()[:12]
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="digest-revision" content="''' + esc(revision) + '''"><title>''' + esc(title) + '''</title>
<link rel="stylesheet" href="assets/digest.css?v=''' + style_version + '''"></head><body>
<header><nav><a class="brand" href="index.html">DIGEST<span>技术日报</span></a><a href="index.html">历史归档 ↗</a></nav></header>
<main>''' + body + '''</main><footer><span>技术日报</span><a href="index.html">查看全部日报 →</a></footer></body></html>'''


def card_html(c, related):
    paper = c['kind'] == 'paper'
    category = c.get('venue', '论文') if paper else ('机构发布' if c.get('organization') else c.get('content_type', '技术分享'))
    observation = category in {'作者观点', '待核验线索'} or c.get('review_status') == 'preprint'
    identity = category if paper else c.get('author', '')
    track = {'main':'主会','journal':'期刊','findings':'Findings','workshop':'Workshop','demo':'Demo'}.get(c.get('track'), '')
    meta = (track + ' · ' if paper and track else '') + c.get('published_label', c.get('published_date', '日期待核验'))
    if paper:
        meta += ' · ' + c.get('date_label', '首次公开日期' if c.get('review_status')=='preprint' else '发表／录用日期') + ' · 首次公开：' + c.get('first_public_date', '未知')
        if c.get('event_type') == 'publication_update':
            meta += ' · 发表动态'
    review_label = {'published':'已发表', 'accepted':'已录用','preprint':'预印本 · 未同行评审'}.get(c.get('review_status'), '已核验')
    content = '<article class="card"><div class="eyebrow"><span class="badge' + (' observation' if observation else '') + '">' + esc(category) + '</span><span>' + esc(identity if not paper else review_label) + '</span><span>阅读：' + esc(c.get('reading_depth','未标明')) + '</span></div>'
    content += '<h3>' + link(c['url'], c['title']) + '</h3><div class="meta">' + esc(meta) + '</div>'
    content += prose(c['summary']) + '<p class="value"><strong>为什么读</strong>' + esc(c['why']) + '</p>'
    if c.get('action'):
        content += '<div class="action"><strong>可以怎么做</strong>' + esc(c['action']) + '</div>'
    content += '<div class="links">' + link(c['url'], '原文 ↗')
    for field, label in [('code_url','代码 ↗'),('verification_url','会刊核验 ↗'),('date_evidence_url','日期依据 ↗')]:
        if c.get(field): content += link(c[field], label)
    for other in related: content += link(other['url'], other['title'])
    for other in c.get('related_links',[]): content += link(other['url'], other['title'])
    if c.get('discovery_sources'):
        content += '<span class="discovery">发现渠道：' + esc(' / '.join(c.get('discovery_labels',c['discovery_sources']))) + '</span>'
    content += '</div><details><summary>阅读边界与局限</summary>' + prose(c['limitations']) + ('<p><strong>证据定位</strong> '+esc(c['evidence_excerpt'])+'</p>' if c.get('evidence_excerpt') else '') + '</details></article>'
    return content


def edition_html(data):
    if data.get('pipeline_version') == 3:
        from .presentation import edition_html as modern_html
        return modern_html(data)
    if data.get('pipeline_version') == 2: return edition_v2_html(data)
    day = data['day']
    cards = data['cards']
    revision = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
                              + (Path(__file__).parent / 'web/digest.css').read_bytes()).hexdigest()
    papers = [c for c in cards if c['kind'] == 'paper']
    themes = {c.get('theme_id') for c in papers if c.get('theme_id')}
    posts = [c for c in cards if c['kind'] == 'x' and c.get('theme_id') not in themes]
    tabs = '<a href="#papers">论文 · ' + str(len(papers)) + '</a><a href="#posts">博主 · ' + str(len(posts)) + '</a>'
    if data.get('weekly_review'): tabs += '<a href="#weekly">本周精读</a>'
    body = '<div class="masthead"><div class="date">' + esc(day.replace('-', '.')) + '</div><h1>技术日报</h1><div class="tabs">' + tabs + '</div></div><div class="layout"><div>'
    for anchor, title, subtitle, selected in [('papers','论文精选','RESEARCH',papers),('posts','博主分享','PRACTICE & IDEAS',posts)]:
        body += '<section class="section" id="' + anchor + '"><div class="section-title"><h2>' + title + '</h2><span>' + subtitle + '</span></div>'
        seen = set()
        for c in selected:
            if c.get('theme_id') and c['theme_id'] in seen: continue
            seen.add(c.get('theme_id'))
            related = [a for a in cards if c.get('theme_id') and a.get('theme_id') == c['theme_id'] and a['id'] != c['id']]
            body += card_html(c, related)
        if not selected: body += '<p class="empty">本期暂无入选内容。</p>'
        body += '</section>'
    if data.get('weekly_review'):
        review = prose(data['weekly_review']).replace('<h1>', '<h3>').replace('</h1>', '</h3>').replace('<h2>', '<h4>').replace('</h2>', '</h4>')
        body += '<section class="section" id="weekly"><div class="section-title"><h2>本周精读</h2><span>DEEP READ</span></div><div class="weekly">' + review + '</div></section>'
    sources = data.get('sources', [])
    limited = sum(s.get('coverage') != 'window_checked' for s in sources)
    body += '<details class="collection"><summary>采集说明与来源 · ' + str(len(sources)) + '</summary>'
    if data.get('windows'):
        w = data['windows']['x']
        body += '<p>筛选范围：' + esc(w['start']) + ' 至 ' + esc(w['end']) + '</p>'
    if limited: body += '<p>' + str(limited) + ' 个来源仅抽样、索引检查或未标明覆盖，不代表窗口内的内容已全部检查。</p>'
    if data.get('status') != 'ok': body += '<p>本期采集存在未检查或失败的来源。</p>'
    body += '<ul>' + ''.join('<li><strong>' + esc(s['id']) + '</strong> · ' + esc(s.get('status','unknown')) + ' — ' + esc(s.get('note','')) + '</li>' for s in sources) + '</ul></details>'
    body += '</div><aside class="side"><div class="side-inner"><p>本期目录</p><a href="#papers">01　论文精选</a><a href="#posts">02　博主分享</a>' + ('<a href="#weekly">03　本周精读</a>' if data.get('weekly_review') else '') + '<a href="index.html">历史归档 →</a></div></aside></div>'
    return shell('技术日报 · ' + day, body, revision)


def edition_v2_html(data):
    from .pipeline import NEWS_SECTIONS
    day=data['day'];cards=data['cards']
    revision=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()
                            +(Path(__file__).parent/'web/digest.css').read_bytes()+Path(__file__).read_bytes()).hexdigest()
    groups=[('peer_reviewed','已录用会议／正式期刊','PEER REVIEWED'),('arxiv','arXiv 前沿','PREPRINTS'),('recommended','Semantic Scholar 推荐','FOR YOU')]
    sections=[(anchor,title,subtitle,[c for c in cards if c['kind']=='paper' and c.get('paper_section')==anchor]) for anchor,title,subtitle in groups]
    sections += [('news-'+str(i),name,'NEWS & IDEAS',[c for c in cards if c['kind']!='paper' and c.get('news_section')==name]) for i,name in enumerate(NEWS_SECTIONS)]
    tabs=''.join('<a href="#'+anchor+'">'+esc(title)+' · '+str(len(group))+'</a>' for anchor,title,_,group in sections if group or anchor in {'peer_reviewed','arxiv','recommended'})
    body='<div class="masthead"><div class="date">'+esc(day.replace('-','.'))+'</div><h1>技术日报</h1><div class="tabs">'+tabs+'</div></div>'
    featured=sorted(cards,key=lambda c:-c.get('ranking_score',0))[:3]
    if featured:
        body+='<div class="highlights"><strong>本期重点</strong>'+''.join('<a href="#item-'+esc(c['id'])+'">'+esc(c['title'])+'</a>' for c in featured)+'</div>'
    body+='<div class="layout"><div>'
    for anchor,title,subtitle,selected in sections:
        if not selected and anchor.startswith('news-'):continue
        body+='<section class="section" id="'+anchor+'"><div class="section-title"><h2>'+title+'</h2><span>'+subtitle+'</span></div>'
        for c in selected:body+='<div id="item-'+esc(c['id'])+'">'+card_html(c,[])+'</div>'
        if not selected:body+='<p class="empty">本期没有通过核验、去重与质量筛选的候选。</p>'
        body+='</section>'
    if data.get('weekly_review'):
        review=prose(data['weekly_review']).replace('<h1>','<h3>').replace('</h1>','</h3>').replace('<h2>','<h4>').replace('</h2>','</h4>')
        body+='<section class="section" id="weekly"><div class="section-title"><h2>本周精读</h2></div><div class="weekly">'+review+'</div></section>'
    body+='<details class="collection"><summary>采集说明与来源 · '+str(len(data.get('sources',[])))+'</summary>'
    window=data.get('windows',{}).get('paper',{})
    body+='<p>筛选范围：'+esc(window.get('start',''))+' 至 '+esc(window.get('end',''))+'</p>'
    body+=''.join('<p>'+esc(reason)+'</p>' for reason in data.get('shortfalls',[]))
    body+='<ul>'
    for source in data.get('sources',[]):
        body+='<li><strong>'+esc(source['id'])+'</strong> · '+esc(source.get('status','unknown'))+' · '+esc(source.get('coverage','unknown'))+'<br>'+esc(source.get('note',''))
        body+='<br>发现 '+str(source.get('discovered',0))+' · 合并 '+str(source.get('merged',0))+' · 核验 '+str(source.get('verified',0))+' · 入选 '+str(source.get('selected',0))+'</li>'
    body+='</ul><p>抽样或索引读取不代表已检查整个窗口；发现渠道不等于同行评审状态。</p></details></div>'
    body+='<aside class="side"><div class="side-inner"><p>本期目录</p>'+''.join('<a href="#'+a+'">'+esc(t)+'</a>' for a,t,_,g in sections if g or a in {'peer_reviewed','arxiv','recommended'})+'</div></aside></div>'
    return shell('技术日报 · '+day,body,revision)


def init_editions(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS web_editions (day TEXT PRIMARY KEY, data TEXT NOT NULL, url TEXT, sent_at TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS publication_runs (day TEXT PRIMARY KEY, stage TEXT NOT NULL, updated_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS send_attempts (day TEXT PRIMARY KEY, state TEXT NOT NULL, attempted_at TEXT, reason TEXT)')


def queue_edition(conn, day, data):
    init_editions(conn)
    old = conn.execute('SELECT data,sent_at FROM web_editions WHERE day=?', (day,)).fetchone()
    if old and old[1]:return
    if old and data.get('status') == 'failed' and json.loads(old[0]).get('status') != 'failed': return
    conn.execute('INSERT INTO web_editions(day,data) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET data=excluded.data',
                 (day, json.dumps(data, ensure_ascii=False)))


def build_site(root, directory=None):
    from .__main__ import database, atomic_write
    directory = directory or root / 'docs'
    with database(root) as conn:
        init_editions(conn)
        rows=list(conn.execute('SELECT data,sent_at FROM web_editions ORDER BY day DESC'))
        editions = [json.loads(row[0]) for row in rows]
        frozen={json.loads(raw)['day'] for raw,sent in rows if sent}
    if not editions: raise ValueError('没有已存档的日报内容')
    paths = []
    for data in editions:
        filename = data['day'] + '.html'
        if data['day'] not in frozen or not (directory/filename).exists():
            atomic_write(directory / filename, edition_html(data))
        if data.get('pipeline_version')==3:
            from .media import public_assets
            paths.extend(public_assets(root,data['cards'],directory))
        paths.append('docs/' + filename)
    atomic_write(directory / 'latest.html', (directory/(editions[0]['day']+'.html')).read_text())
    archive = '<div class="masthead"><div class="date">ARCHIVE</div><h1>日报归档</h1></div><div class="archive">'
    for data in editions:
        titles = ' / '.join(c['title'] for c in data['cards'][:3])
        archive += '<a href="' + esc(data['day']) + '.html"><time>' + esc(data['day']) + '</time><h2>技术日报</h2><p>' + esc(titles or '采集记录') + '</p></a>'
    archive += '</div>'+trial_archive(directory)
    atomic_write(directory / 'index.html', shell('日报归档', archive))
    atomic_write(directory / 'assets/digest-v3.css', (Path(__file__).parent/'web/digest.css').read_text()+'\n'+(Path(__file__).parent/'web/quality.css').read_text())
    paths.append('docs/assets/digest-v3.css')
    atomic_write(directory / 'assets/digest.css', (Path(__file__).parent / 'web/digest.css').read_text())
    for source in sorted((Path(__file__).parent / 'web/fonts').glob('*')):
        if not source.is_file(): continue
        destination = directory / 'assets/fonts' / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        paths.append('docs/assets/fonts/' + source.name)
    atomic_write(directory / '.nojekyll', '')
    return paths + ['docs/index.html','docs/latest.html','docs/assets/digest.css','docs/.nojekyll']


def trial_archive(directory):
    entries=[]
    for path in sorted(directory.glob('quality-trial-*.html'),reverse=True):
        if not re.fullmatch(r'quality-trial-\d{4}-\d{2}-\d{2}-\d{6}\.html',path.name):continue
        entries.append('<a href="'+esc(path.name)+'"><time>'+esc(path.name[14:24])+'</time><h2>图文优化试刊</h2></a>')
    return '<section id="trials"><h2>独立试刊</h2><div class="archive">'+''.join(entries)+'</div></section>' if entries else ''


def settings(root):
    p = root / 'tech_delivery.yaml'
    return yaml.safe_load(p.read_text()).get('github_pages', {}) if p.exists() else {}


def enabled(root):
    return bool(settings(root).get('enabled'))


def git(root, args):
    result = subprocess.run(['git','-c','credential.helper=!gh auth git-credential', *args], cwd=root,
                            capture_output=True, text=True, timeout=120)
    if result.returncode: raise RuntimeError('Git归档或推送失败；请检查工作区及GitHub登录')
    return result.stdout.strip()


@contextmanager
def publication_lock(root):
    lock = root / 'state/tech/publish.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('另一进程正在生成或发布日报，请稍后重试') from None
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def publication_phase(root,day,stage,now):
    from .__main__ import database
    with database(root) as conn:
        init_editions(conn)
        conn.execute('INSERT OR REPLACE INTO publication_runs VALUES (?,?,?)',(day,stage,now.isoformat()))


def mark_recommended(conn,day,cards):
    for card in cards:
        uid=card['id']
        alias=conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(uid,)).fetchone()
        if alias:uid=alias[0]
        conn.execute("UPDATE candidates SET state='recommended',reason='微信已接受推送' WHERE id=?",(uid,))
        conn.execute('INSERT OR REPLACE INTO items VALUES (?,?,?)',(card['id'],day,json.dumps(card,ensure_ascii=False)))


def publish_pending(root, now, day=None, notify=True):
    from .__main__ import database
    with publication_lock(root):
        try:return _publish_pending(root, now, day, notify)
        except Exception:
            with database(root) as conn:
                init_editions(conn)
                conn.execute("UPDATE publication_runs SET stage='error',updated_at=? WHERE stage IN ('publishing','github_pushed') AND (? IS NULL OR day=?)",(now.isoformat(),day,day))
            raise


def _publish_pending(root, now, day=None, notify=True):
    from .__main__ import database, atomic_write
    from mail_digest.delivery import send_wechat
    cfg = settings(root)
    if not enabled(root): raise RuntimeError('HTML已可归档，网页托管尚未启用')
    expected = 'https://github.com/' + cfg['repository'] + '.git'
    if git(root, ['remote','get-url','origin']).rstrip('/') != expected:
        raise ValueError('Git远端与日报仓库配置不一致')
    with database(root) as conn:
        init_editions(conn)
        row = conn.execute('SELECT day,data,sent_at FROM web_editions WHERE day=?', (day,)).fetchone() if day else conn.execute('SELECT day,data,sent_at FROM web_editions WHERE sent_at IS NULL ORDER BY day LIMIT 1').fetchone()
    if row is None: return {'delivery':'nothing_pending'}
    day, raw, sent = row
    data=json.loads(raw)
    if data.get('status')=='failed' or not data.get('cards'):raise RuntimeError('采集失败或日报为空，不发布无效日报')
    publication_phase(root,day,'publishing',now)
    paths = build_site(root)
    staged = git(root, ['diff','--cached','--name-only'])
    if staged: raise RuntimeError('暂存区有其他修改，请先处理后再发布日报')
    git(root, ['add','--', *paths])
    if git(root, ['diff','--cached','--name-only']):
        git(root, ['commit','-m','归档技术日报 ' + day])
    git(root, ['push','origin','main'])
    publication_phase(root,day,'github_pushed',now)
    url = cfg['base_url'].rstrip('/') + '/' + day + '.html'
    revision = re.search(r'name="digest-revision" content="([a-f0-9]+)"', (root/'docs'/(day+'.html')).read_text())[1]
    deadline = time.monotonic() + cfg.get('wait_seconds',180)
    while True:
        try:
            response = requests.get(url, timeout=15, headers={'Cache-Control':'no-cache'})
            ready = response.status_code == 200 and ('content="' + revision + '"') in response.text
        except requests.RequestException: ready = False
        if ready and data.get('pipeline_version')==3:
            from .media import remote_ready
            try:ready=remote_ready(cfg['base_url'],data['cards'])
            except requests.RequestException:ready=False
        if ready: break
        if time.monotonic() >= deadline: raise RuntimeError('HTML已推送到GitHub，网页尚未发布；可用 --publish 重试，不发送无效链接')
        time.sleep(5)
    publication_phase(root,day,'pages_ready',now)
    if notify and not sent:
        with database(root) as conn:
            attempt=conn.execute('SELECT state FROM send_attempts WHERE day=?',(day,)).fetchone()
            if attempt and attempt[0] in {'sending','uncertain'}:
                raise RuntimeError('微信发送结果待核对；请先核对 Server酱记录，再使用 --resolve-delivery，避免重复发送')
            conn.execute("INSERT OR REPLACE INTO send_attempts VALUES (?,'sending',?,NULL)",(day,now.isoformat()))
        try:
            send_wechat(day, '[打开技术日报](' + url + ')\n\n[历史归档](' + cfg['base_url'].rstrip('/') + '/index.html)', title='技术日报 ' + day)
        except Exception:
            with database(root) as conn:
                conn.execute("UPDATE send_attempts SET state='uncertain',reason='发送请求未获得可靠确认，需核对通道记录' WHERE day=?",(day,))
            raise RuntimeError('微信发送结果不明确，已保留待核对；不会自动重发') from None
        sent = now.isoformat()
    with database(root) as conn:
        conn.execute('UPDATE web_editions SET url=?,sent_at=? WHERE day=?', (url,sent,day))
        if sent:
            conn.execute("INSERT OR REPLACE INTO send_attempts VALUES (?,'sent',?,NULL)",(day,sent))
            conn.execute("UPDATE deliveries SET status='sent',content='',sent_at=?,error=NULL WHERE day=?", (sent,day))
            if data.get('pipeline_version') in {2,3}:
                mark_recommended(conn,day,data['cards'])
    status = {'delivery':'sent' if sent else 'published','day':day,'url':url}
    p = root / 'state/tech/last_run.json'
    if p.exists():
        info = json.loads(p.read_text())
        if info.get('day') == day:
            info.update(status)
            atomic_write(p,json.dumps(info,ensure_ascii=False,indent=2))
    return status
