"""候选入库、证据审核、跨来源身份合并与每日精选。模型阅读由 Codex 执行。"""
import hashlib
import json
import math
import re
from collections import Counter
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml

TOPIC_NAMES = {'language':'语言与 Agent','vision':'视觉','multimodal':'多模态',
               'reinforcement':'强化学习','systems':'AI 系统'}
NEWS_SECTIONS = ('研究动态','工程实践','开源与工具','深度解读')


def enabled(root):
    return (root / 'digest_sources.yaml').exists() and config(root).get('enabled', False)


def config(root):
    return yaml.safe_load((root / 'digest_sources.yaml').read_text())


def registry(root):
    from .__main__ import load_settings
    x, papers = load_settings(root)
    result = [dict(a, id='x:' + a['handle'].lower(), kind='x', adapter='browser') for a in x['accounts']]
    result += [dict(s, name=s.get('name') or ' / '.join(s['venues']), kind='paper', adapter=s.get('adapter','conference'), cadence=s.get('cadence','daily')) for s in papers['sources']]
    result += config(root)['sources']
    ids = [s['id'] for s in result]
    if len(ids) != len(set(ids)): raise ValueError('来源 ID 重复')
    return result


def source_aliases(root):
    return {old:s['id'] for s in registry(root) for old in s.get('legacy_ids', [])}


def init(conn, root):
    from .site import init_editions
    init_editions(conn)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pipeline_meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, data TEXT NOT NULL,
      state TEXT NOT NULL, reason TEXT NOT NULL, discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidate_aliases (alias TEXT PRIMARY KEY, candidate_id TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS candidate_state ON candidates(state);
    CREATE TABLE IF NOT EXISTS merge_suggestions (left_id TEXT, right_id TEXT, similarity REAL,
      PRIMARY KEY(left_id,right_id));
    CREATE TABLE IF NOT EXISTS source_state (id TEXT PRIMARY KEY, checked_at TEXT, coverage TEXT,
      cursor TEXT, backfill_cursor TEXT, backfill_complete INTEGER DEFAULT 0, failures INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS source_runs (id INTEGER PRIMARY KEY, source_id TEXT, observed_at TEXT,
      status TEXT, details TEXT);
    CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, candidate_id TEXT, action TEXT,
      reason TEXT, observed_at TEXT);
    ''')
    from .events import init as init_events
    init_events(conn)
    if not conn.execute("SELECT 1 FROM pipeline_meta WHERE key='history_migrated'").fetchone():
        aliases = source_aliases(root)
        for uid, day, raw in conn.execute('SELECT id,day,card FROM items').fetchall():
            card = json.loads(raw)
            card['source_id'] = aliases.get(card['source_id'], card['source_id'])
            card['discovery_sources'] = [card['source_id']]
            card['id'] = uid
            conn.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?)',
                         (uid,json.dumps(card,ensure_ascii=False),'recommended','历史日报',day,day))
            for alias in identity_aliases(card):
                conn.execute('INSERT OR IGNORE INTO candidate_aliases VALUES (?,?)',(alias,uid))
        for old, new in aliases.items():
            row = conn.execute('SELECT checked_at,window_months FROM checks WHERE id=?',(old,)).fetchone()
            if row: conn.execute('INSERT OR IGNORE INTO checks VALUES (?,?,?)',(new,*row))
        conn.execute("INSERT INTO pipeline_meta VALUES ('history_migrated','2')")


def connection(root):
    from .__main__ import database
    conn = database(root)
    init(conn, root)
    conn.commit()
    return conn


def due(root, now):
    from .__main__ import timestamp
    with connection(root) as conn:
        previous = {r[0]:r[1:] for r in conn.execute('SELECT id,checked_at,failures,cursor FROM source_state')}
    result = []
    for s in registry(root):
        cadence = s.get('cadence','daily')
        scheduled = cadence != 'rotating' or now.date().toordinal() % 3 == s['rotation_group']
        if cadence == 'twice_weekly': scheduled = now.weekday() in {1,4}
        # 首次身份检查不受轮换限制；随后分组三天检查一次。
        if s['id'] not in previous: scheduled = True
        if scheduled:
            state = previous.get(s['id'])
            retry=json.loads(state[2] or '{}').get('retry_not_before') if state else None
            if retry and timestamp(retry)>now:continue
            checked=state[0] if state else None
            if s['adapter']=='browser' and state:
                checked=json.loads(state[2] or '{}').get('content_checked_at')
            if not checked or state[1]>0 or now.date()>timestamp(checked).date(): result.append(s)
    return result


def plan(root, now, all_sources=False):
    from .__main__ import window_start
    cfg = config(root)
    with connection(root) as conn:
        states = {r[0]:dict(coverage=r[1],cursor=json.loads(r[2] or '{}'),
                          backfill_cursor=json.loads(r[3] or '{}'),backfill_complete=bool(r[4]))
                  for r in conn.execute('SELECT id,coverage,cursor,backfill_cursor,backfill_complete FROM source_state')}
    return {'pipeline_version':2,'date':now.date().isoformat(),'timezone':'Asia/Shanghai',
            'observed_at':now.isoformat(),'window':{'start':window_start(now,cfg['window_months']).date().isoformat(),'end':now.date().isoformat()},
            'sources':[dict(s, progress=states.get(s['id'],{'backfill_complete':False})) for s in (registry(root) if all_sources else due(root,now))],
            'selection':cfg['selection'],'topics':TOPIC_NAMES,
            'input_schema':{'observed_at':'本次带时区的时间','sources':'id/status/note/evidence_urls/coverage/window_months；可附 phase: identity/content、cursor、backfill_cursor、backfill_complete',
                            'items':'原始候选：kind/source_id/title/url/abstract/标识符/各事件日期；缺日期或评分也入库等待审核',
                            'reviews':'id/review_version:4/paper_type/type_details/findings/research_tags/problem/contribution/results/conditions/reading_advice/subtopic/claims/score_reasons/summary/why/limitations/action/reading_depth/topic/quality_scores/review_evidence/date_evidence_url；论文另填 review_status/venue/track/verification_url/first_public_date；资讯另填 news_section/event_id，视频另填 transcript_url/transcript_excerpt'},
            'commands':['--collect','--review-queue','--input state/tech/collection.json','--compose --dry-run','--compose --push','--health'],
            'cost_policy':'公开接口/RSS优先，Codex读取浏览器及原创摘要；不调用付费模型或抓取API'}


def canonical_url(value):
    from .__main__ import https_url
    p = https_url(value)
    query = [(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in {'ref','source'}]
    return urlunparse((p.scheme,p.netloc.lower(),p.path.rstrip('/') or '/',p.params,urlencode(sorted(query)),''))


def identity_aliases(card):
    aliases = []
    kind = card.get('kind')
    url = card.get('url','')
    if kind == 'paper':
        doi = str(card.get('doi') or '').lower().strip().removeprefix('https://doi.org/').removeprefix('doi:')
        if doi:
            if not re.fullmatch(r'10\.\d{4,9}/\S+',doi): raise ValueError('DOI 格式无效')
            aliases.append('doi:'+doi)
        arxiv = str(card.get('arxiv_id') or '').removeprefix('ARXIV:')
        if not arxiv:
            p = urlparse(url)
            if p.hostname in {'arxiv.org','export.arxiv.org'}:
                arxiv = re.sub(r'^/(abs|pdf)/','',p.path).removesuffix('.pdf')
        if arxiv:
            arxiv = re.sub(r'v\d+$','',arxiv)
            if not re.fullmatch(r'(\d{4}\.\d{4,5}|[a-z.-]+/\d{7})',arxiv): raise ValueError('arXiv ID 无效')
            aliases.append('arxiv:'+arxiv)
        for field,prefix in [('openreview_id','openreview:'),('semantic_scholar_id','s2:')]:
            if card.get(field): aliases.append(prefix+str(card[field]))
        p = urlparse(url)
        if p.hostname == 'openreview.net':
            forum = dict(parse_qsl(p.query)).get('id')
            if forum: aliases.append('openreview:'+forum)
    if kind == 'x':
        match = re.search(r'/status/(\d+)',url)
        if match: aliases.append('x:'+match[1])
    if url: aliases.append('url:'+('paper:' if kind=='paper' else 'news:')+canonical_url(url))
    return aliases


def _merge_data(old, new):
    result = dict(old)
    # 原始采集不能抹掉已审核的中文卡片，补充发现渠道和缺失元数据。
    for key,value in new.items():
        if key not in result or result[key] in (None,'',[]): result[key] = value
    for key in ('updated_date','updated_at'):
        if new.get(key) and (not old.get(key) or str(new[key])>str(old[key])):result[key]=new[key]
    # 官方采集可补充事件证据，不能抹除既有证据或改写已核验首次公开日。
    for key in ('official_events',):
        if new.get(key):
            unique={json.dumps(v,sort_keys=True):v for v in old.get(key,[])+new[key]}
            result[key]=list(unique.values())
    if new.get('official_metadata'):result['official_metadata']=new['official_metadata']
    if 'publication_missing' in new:result['publication_missing']=new['publication_missing']
    result['discovery_sources'] = sorted(set(old.get('discovery_sources',[])+new.get('discovery_sources',[])))
    return result


def store_candidate(conn, root, raw, now, source_map=None, aliases=None):
    card = dict(raw)
    aliases=aliases if aliases is not None else source_aliases(root)
    source_map=source_map if source_map is not None else {s['id']:s for s in registry(root)}
    sid=aliases.get(card.get('source_id'),card.get('source_id'))
    source=source_map.get(sid)
    if source is None or card.get('kind') != source['kind']: raise ValueError('来源或类型无效')
    card['source_id'] = sid
    if not isinstance(card.get('title'),str) or not card['title'].strip(): raise ValueError('候选缺少标题')
    card['url'] = canonical_url(card['url'])
    if card['kind'] == 'x':
        p = urlparse(card['url'])
        if p.hostname not in {'x.com','twitter.com','www.x.com','www.twitter.com'} or not re.fullmatch('/'+re.escape(source['handle'])+r'/status/\d+',p.path,re.I):
            raise ValueError('X 原文与名单作者不匹配')
    discovery=[aliases.get(value,value) for value in card.get('discovery_sources',[])]
    known=source_map
    if any(value not in known or (known[value]['kind']=='paper')!=(card['kind']=='paper') for value in discovery):raise ValueError('发现渠道无效')
    card['discovery_sources'] = sorted(set(discovery+[sid]))
    keys = identity_aliases(card)
    matches = {r[0] for key in keys for r in conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(key,))}
    old_rows = [conn.execute('SELECT id,data,state FROM candidates WHERE id=?',(uid,)).fetchone() for uid in matches]
    old_rows = sorted([r for r in old_rows if r],key=lambda r:({'recommended':0,'reviewed':1,'pending':2}.get(r[2],3),r[0]))
    uid = old_rows[0][0] if old_rows else 'candidate:'+hashlib.sha256(keys[0].encode()).hexdigest()[:24]
    state = old_rows[0][2] if old_rows else 'pending'
    if old_rows:card=_merge_data(json.loads(old_rows[0][1]),card)
    for old_id,data,old_state in old_rows[1:]:
        card=_merge_data(card,json.loads(data))
        conn.execute('UPDATE candidate_aliases SET candidate_id=? WHERE candidate_id=?',(uid,old_id))
        conn.execute('INSERT OR REPLACE INTO candidate_aliases VALUES (?,?)',(old_id,uid))
        conn.execute('UPDATE feedback SET candidate_id=? WHERE candidate_id=?',(uid,old_id))
        event_rows=conn.execute('SELECT * FROM event_links WHERE left_id=? OR right_id=?',(old_id,old_id)).fetchall()
        conn.execute('DELETE FROM event_links WHERE left_id=? OR right_id=?',(old_id,old_id))
        for a,b,*fields in event_rows:
            a=uid if a==old_id else a;b=uid if b==old_id else b
            if a!=b:conn.execute('INSERT OR REPLACE INTO event_links VALUES (?,?,?,?,?,?)',(*( [a,b] if fields[0]=='extends' else sorted([a,b]) ),*fields))
        conn.execute('DELETE FROM candidates WHERE id=?',(old_id,))
        conn.execute('DELETE FROM merge_suggestions WHERE left_id=? OR right_id=?',(old_id,old_id))
        for related_id,related_data in conn.execute('SELECT id,data FROM candidates').fetchall():
            related=json.loads(related_data)
            if related.get('theme_id')==old_id:
                related['theme_id']=uid
                conn.execute('UPDATE candidates SET data=? WHERE id=?',(json.dumps(related,ensure_ascii=False),related_id))
        if card.get('theme_id')==old_id:card['theme_id']=uid
    card['id'] = uid
    if 'theme_id' not in card and card['kind']=='paper': card['theme_id']=uid
    conn.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
                 (uid,json.dumps(card,ensure_ascii=False),state,'待核验与评分',now.isoformat(),now.isoformat()))
    for key in keys: conn.execute('INSERT OR REPLACE INTO candidate_aliases VALUES (?,?)',(key,uid))
    if len(old_rows)>1:
        from .events import rebuild
        rebuild(conn)
    if not old_rows and card['kind']=='paper':
        title = re.sub(r'\W+','',card['title']).lower()
        for other_id,data in conn.execute('SELECT id,data FROM candidates WHERE id!=?',(uid,)).fetchall():
            other = json.loads(data)
            if other.get('kind')!='paper':continue
            score = SequenceMatcher(None,title,re.sub(r'\W+','',other['title']).lower()).ratio()
            if score >= .90:
                conn.execute('INSERT OR IGNORE INTO merge_suggestions VALUES (?,?,?)',(*sorted([uid,other_id]),score))
    return uid, bool(old_rows)


def _day(value):
    from datetime import date
    if not isinstance(value,str): raise ValueError('事件日期缺失')
    return date.fromisoformat(value[:10]).isoformat()


def link_related(conn):
    rows=conn.execute('SELECT id,data FROM candidates').fetchall()
    paper_urls={canonical_url(c['url']):uid for uid,raw in rows for c in [json.loads(raw)] if c['kind']=='paper'}
    for uid,raw in rows:
        c=json.loads(raw)
        if c['kind']=='paper':continue
        candidates=[]
        if c.get('related_paper_alias'):candidates.append(c['related_paper_alias'])
        for text in c.get('outbound_urls',[])+[c.get('raw_text','')]:
            candidates.extend('arxiv:'+re.sub(r'v\d+$','',v) for v in re.findall(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)',text))
        related=paper_urls.get(c['url'])
        for alias in candidates:
            row=conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(alias,)).fetchone()
            if row:related=row[0];break
        if related:
            c['theme_id']=related
            conn.execute('UPDATE candidates SET data=? WHERE id=?',(json.dumps(c,ensure_ascii=False),uid))


def content_date(card):
    # 普通 arXiv 版本更新不能刷新两个月窗口。
    if card['kind']=='paper' and card.get('review_status')=='preprint':
        if card.get('first_public_at'):
            from .__main__ import timestamp
            return timestamp(card['first_public_at']).date().isoformat()
        return _day(card.get('first_public_date'))
    if card.get('published_at') or card.get('accepted_at'):
        from .__main__ import timestamp
        return timestamp(card.get('published_at') or card['accepted_at']).date().isoformat()
    return _day(card.get('published_date') or card.get('accepted_date'))


def validate_review(root, card, now):
    from .__main__ import window_start, https_url
    for field in ('summary','why','limitations','reading_depth','evidence_excerpt','date_evidence_url'):
        if not isinstance(card.get(field),str) or not card[field].strip(): raise ValueError('审核缺少 '+field)
    if card['reading_depth'] not in {'摘要','关键章节','正文','帖子','字幕','复现'}: raise ValueError('阅读深度无效')
    if card.get('topic') not in TOPIC_NAMES: raise ValueError('缺少有效主题')
    if not isinstance(card.get('review_evidence'),list) or not card['review_evidence']:raise ValueError('缺少已读证据链接')
    for url in card['review_evidence']+[card['date_evidence_url']]: https_url(url)
    for field in ('code_url','verification_url','change_evidence_url','identity_evidence_url','transcript_url'):
        if card.get(field):https_url(card[field])
    values = card.get('quality_scores',{})
    if not isinstance(values,dict):raise ValueError('quality_scores 必须为对象')
    for key in config(root)['selection']['weights']:
        value = values.get(key)
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not 0<=value<=5:
            raise ValueError('评分必须为 0–5：'+key)
    day = content_date(card)
    if not window_start(now,config(root)['window_months']).date().isoformat() <= day <= now.date().isoformat():
        raise ValueError('内容超出最近两个自然月窗口')
    card['published_label'] = day
    if card['kind']=='paper':
        review_status = card.get('review_status')
        if review_status in {'accepted','published'}:
            from .__main__ import load_settings
            official = [s for s in load_settings(root)[1]['sources'] if card.get('venue') in s['venues']]
            if not official: raise ValueError('会刊不在核验名单')
            verify = https_url(card.get('verification_url',''))
            domains = {urlparse(s['url']).hostname for s in official}
            if card['venue'] in {'ICLR','TMLR'}:domains.add('openreview.net')
            if card['venue']=='NeurIPS':domains.add('neurips.cc')
            if verify.hostname not in domains:raise ValueError('缺少对应会刊官方核验')
            if card.get('track') not in {'main','journal','findings','workshop','demo'}:raise ValueError('会刊轨道无效')
        elif review_status=='preprint':
            if not any(a.startswith('arxiv:') for a in identity_aliases(card)):raise ValueError('预印本缺少 arXiv 身份')
            if not card.get('abstract'):raise ValueError('预印本缺少公开摘要')
            card['venue']='arXiv';card['track']='preprint'
            if card.get('first_public_at'):card['first_public_date']=day
        else:raise ValueError('论文发表状态未知')
        first = card.get('first_public_date')
        if first:
            first=_day(first)
            if first>now.date().isoformat():raise ValueError('首次公开日期在未来')
        card['event_type']='publication_update' if review_status!='preprint' and (not first or first<window_start(now,2).date().isoformat()) else 'new_research'
    else:
        if card.get('news_section') not in NEWS_SECTIONS:raise ValueError('资讯栏目无效')
        if not card.get('event_id'):raise ValueError('资讯缺少事件身份')
        source_map={s['id']:s for s in registry(root)}
        source = source_map[card['source_id']]
        adapters={source_map[sid]['adapter'] for sid in card.get('discovery_sources',[]) if sid in source_map}
        adapters.add(source['adapter'])
        original=urlparse(card['url'])
        video=original.hostname in {'youtube.com','www.youtube.com','m.youtube.com','youtu.be'}
        repo=original.hostname=='github.com' and bool(re.fullmatch(r'/[\w.-]+/[\w.-]+/?',original.path))
        if card['kind']=='x' and source.get('identity_status')!='verified':
            if not card.get('identity_verified') or not card.get('identity_evidence_url'):raise ValueError('作者身份尚未核验')
            https_url(card['identity_evidence_url'])
        if video or 'youtube' in adapters:
            if not card.get('transcript_excerpt') or not card.get('transcript_url') or card['reading_depth'] not in {'字幕','正文'}:
                raise ValueError('视频没有可读字幕／逐字稿')
            https_url(card['transcript_url'])
        if (repo or adapters & {'trending','github_org'}) and (not card.get('change_evidence_url') or not card.get('technical_change')):
            raise ValueError('开源项目缺少具体技术变化证据')
        card['author']=source['name'] if card['kind']=='x' else card.get('author') or source['name']
        card['organization']=bool(source.get('organization'))
        card['author_key']=source['id'] if card['kind']=='x' else card['author'].strip().casefold()
    if card.get('review_version',1)>=3:
        from .quality import validate
        validate(card)
    return card


def ingest(root, payload, now):
    from .__main__ import timestamp
    if not isinstance(payload,dict) or not isinstance(payload.get('observed_at'),str):raise ValueError('采集批次必须包含 observed_at 字符串')
    for field in ('items','reviews','sources'):
        if not isinstance(payload.get(field,[]),list) or any(not isinstance(row,dict) for row in payload.get(field,[])):raise ValueError(field+' 必须为对象数组')
    observed = timestamp(payload['observed_at'])
    if observed>now+timedelta(minutes=5) or now-observed>timedelta(hours=6):raise ValueError('采集批次过期或来自未来')
    source_map = {s['id']:s for s in registry(root)}
    alias_map = source_aliases(root)
    stats, rejected, ids = [], [], []
    reviewed_count=0
    with connection(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        for raw in payload.get('items',[]):
            try:
                uid,merged = store_candidate(conn,root,raw,observed,source_map=source_map,aliases=alias_map)
                ids.append({'id':uid,'merged':merged,'source_id':alias_map.get(raw.get('source_id'),raw.get('source_id'))})
            except (ValueError,KeyError,TypeError) as exc:rejected.append({'title':raw.get('title',''),'reason':str(exc)})
        for raw in payload.get('reviews',[]):
            uid=raw.get('id')
            row=conn.execute('SELECT data,state FROM candidates WHERE id=?',(uid,)).fetchone()
            if not row:
                rejected.append({'id':uid,'reason':'审核 ID 不存在'});continue
            if row[1]=='recommended':continue
            card=json.loads(row[0]);card.update({k:v for k,v in raw.items() if k not in {'id','source_id','kind','url','discovery_sources'}})
            try:
                if card.get('review_version',1)>=3:
                    from .media import prepare
                    prepare(root,card)
                card=validate_review(root,card,now)
                card['reviewed_at']=now.isoformat()
                reviewed_count+=1
                conn.execute("UPDATE candidates SET data=?,state='reviewed',reason='',updated_at=? WHERE id=?",(json.dumps(card,ensure_ascii=False),observed.isoformat(),uid))
            except (ValueError,KeyError,TypeError) as exc:
                rejected.append({'id':uid,'reason':str(exc)})
                conn.execute("UPDATE candidates SET state='pending',reason=? WHERE id=?",(str(exc),uid))
        link_related(conn)
        for raw in payload.get('sources',[]):
            entry=dict(raw);sid=alias_map.get(entry.get('id'),entry.get('id'));entry['id']=sid
            entry['observed_at']=observed.isoformat()
            if sid not in source_map or entry.get('status') not in {'ok','blocked','error','needs_browser'}:raise ValueError('来源状态无效')
            if not entry.get('note') or not entry.get('evidence_urls'):raise ValueError('来源缺少实际检查说明')
            for url in entry['evidence_urls']:canonical_url(url)
            if entry.get('coverage') not in {'sample','index_only','window_checked','unknown'}:raise ValueError('覆盖程度无效')
            old=conn.execute('SELECT cursor,backfill_cursor,backfill_complete,failures FROM source_state WHERE id=?',(sid,)).fetchone() or ('{}','{}',0,0)
            # 完成标记必须伴随整个窗口核验，不用访问成功覆盖已有进度。
            complete=bool(entry.get('backfill_complete')) and entry['coverage']=='window_checked' and entry.get('window_months')==2
            cursor=dict(json.loads(old[0] or '{}'));cursor.update(entry.get('cursor') or {})
            if entry['status']=='ok':cursor.pop('retry_not_before',None)
            if source_map[sid]['adapter']=='browser' and entry['status']=='ok' and entry['coverage'] in {'sample','window_checked'} and entry.get('phase')!='identity':
                cursor['content_checked_at']=observed.isoformat()
            entry['cursor']=cursor
            conn.execute('INSERT OR REPLACE INTO source_state VALUES (?,?,?,?,?,?,?)',
                         (sid,observed.isoformat(),entry['coverage'],json.dumps(cursor),
                          json.dumps(entry.get('backfill_cursor',json.loads(old[1] or '{}'))),int(complete or old[2]),0 if entry['status']=='ok' else old[3]+1))
            discovered=sum(alias_map.get(c.get('source_id'),c.get('source_id'))==sid for c in payload.get('items',[]))
            entry['discovered']=discovered
            entry['merged']=sum(x['merged'] for x in ids if x['source_id']==sid)
            entry['accepted_candidates']=sum(x['source_id']==sid for x in ids)
            entry['candidate_ids']=sorted({x['id'] for x in ids if x['source_id']==sid})
            conn.execute('INSERT INTO source_runs(source_id,observed_at,status,details) VALUES (?,?,?,?)',(sid,observed.isoformat(),entry['status'],json.dumps(entry,ensure_ascii=False)))
            stats.append(entry)
        if payload.get('weekly_review'):
            if not isinstance(payload['weekly_review'],str):raise ValueError('weekly_review 必须是字符串')
            conn.execute('INSERT OR REPLACE INTO pipeline_meta VALUES (?,?)',('weekly_review:'+now.date().isoformat(),payload['weekly_review']))
    return {'pipeline_version':2,'ingested':len(ids),'candidate_ids':ids,'reviews':reviewed_count,
            'source_results':stats,'rejected':rejected,'delivery':'not_sent'}


def tokens(text):
    return re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]',str(text).lower())


def bm25(documents, query):
    corpus=[Counter(tokens(d)) for d in documents];n=len(corpus)
    lengths=[sum(c.values()) for c in corpus];avg=sum(lengths)/max(n,1) or 1
    df=Counter(t for c in corpus for t in c)
    result=[]
    for c,length in zip(corpus,lengths):
        total=0
        for term in set(tokens(query)):
            f=c[term]
            if f:total+=math.log(1+(n-df[term]+.5)/(df[term]+.5))*f*2.2/(f+1.2*(.25+.75*length/avg))
        result.append(total)
    return result


def review_queue(root, now):
    from .__main__ import window_start
    cfg=config(root);groups={}
    cutoff=window_start(now,2).date().isoformat()
    with connection(root) as conn:
        suggestions=[dict(left_id=r[0],right_id=r[1],similarity=r[2]) for r in conn.execute('SELECT * FROM merge_suggestions')]
        rows=[dict(json.loads(r[0]),pending_reason=r[1]) for r in conn.execute("SELECT data,reason FROM candidates WHERE state='pending'")]
        minimum=cfg['selection'].get('minimum_review_version',1)
        if minimum>=3:
            rows += [dict(json.loads(raw),pending_reason='旧审核须补充新版结构与证据') for raw, in conn.execute("SELECT data FROM candidates WHERE state='reviewed'") if json.loads(raw).get('review_version',1)<minimum]
        from .preferences import profile,retrieval_query
        interest_profile=profile(conn,now)
        from .events import suggestions as event_suggestions
        event_pool=[json.loads(raw) for raw, in conn.execute('SELECT data FROM candidates ORDER BY updated_at DESC,id LIMIT 250')]
        seen={c['id'] for c in event_pool}
        event_pool += [json.loads(raw) for raw, in conn.execute("SELECT data FROM candidates WHERE state='recommended' ORDER BY updated_at DESC,id LIMIT 100") if json.loads(raw)['id'] not in seen]
        story_suggestions=event_suggestions(event_pool)
        eligible=[]
        for c in rows:
            known=c.get('published_date') or c.get('accepted_date') or (c.get('first_public_date') if c.get('review_status')=='preprint' else None)
            if known and not cutoff<=known[:10]<=now.date().isoformat():
                conn.execute('UPDATE candidates SET reason=? WHERE id=?',('现有日期超出窗口，若有近期录用／发表须补官方证据',c['id']))
            else:eligible.append(c)
        rows=eligible
    publication_tasks=sorted((c for c in rows if c['kind']=='paper' and c.get('source_id','').startswith('paper:')),
                             key=lambda c:(bool(c.get('official_events')),c.get('accepted_date') or c.get('published_date') or '',c['id']),reverse=True)[:20]
    for kind,limit in [('paper',cfg['selection']['review_paper_limit']),('news',cfg['selection']['review_news_limit'])]:
        group=[c for c in rows if (c['kind']=='paper')==(kind=='paper')]
        docs=[c['title']+' '+c.get('abstract','')+' '+c.get('raw_text','')[:12000] for c in group]
        query=' '.join(term for terms in cfg['topics'].values() for term in terms)+' '+retrieval_query(interest_profile)
        scores=bm25(docs,query)
        # BM25 只初筛，保留少量低关键词匹配项，避免漏掉新方向。
        ordered=sorted(zip(group,scores),key=lambda pair:(-pair[1],pair[0]['id']))
        topic_scores={topic:bm25(docs,' '.join(terms)) for topic,terms in cfg['topics'].items()}
        inferred={c['id']:max(topic_scores,key=lambda t:topic_scores[t][i]) for i,c in enumerate(group)}
        for c,_ in ordered:c['prefilter_topic']=inferred[c['id']]
        if kind=='paper' and len(ordered)>limit:
            # 初筛也均衡分配，避免最终多样性规则被单一主题淹没的审核池架空。
            quota=max(1,(limit-3)//len(TOPIC_NAMES));balanced=[];seen=set()
            for topic in TOPIC_NAMES:
                pool=[pair for pair in ordered if inferred[pair[0]['id']]==topic]
                balanced+=pool[:quota];seen.update(c['id'] for c,_ in pool[:quota])
            balanced+= [pair for pair in ordered if pair[0]['id'] not in seen]
            ordered=balanced
        reserve=min(3,limit,len(ordered)) if len(ordered)>limit else 0
        exploration=sorted(ordered[limit-reserve:],key=lambda pair:(pair[0].get('first_public_date') or pair[0].get('published_date') or '',pair[0]['id']),reverse=True)[:reserve]
        groups[kind]=[dict(c,prefilter_score=round(score,3)) for c,score in ordered[:limit-reserve]+exploration]
        if kind=='paper':
            official=[c for c in publication_tasks if c.get('official_events')][:min(5,limit)]
            chosen={c['id'] for c in official}
            groups[kind]=(official+[c for c in groups[kind] if c['id'] not in chosen])[:limit]
    for card in groups['paper']:
        if card.get('official_events') or card.get('verification_note'):
            card['publication_task']={'priority':'先核验官方事件与日期；缺证据不标录用',
                'events':card.get('official_events',[]),'missing':card.get('publication_missing',[])}
    from .research import instructions
    return {'pipeline_version':2,'observed_at':now.isoformat(),'queue':groups,'merge_suggestions':suggestions,
            'event_suggestions':story_suggestions,'publication_tasks':publication_tasks,'research_schema':instructions(),'preference_profile':interest_profile,
            'instructions':'使用 review_version:4；逐条 findings 关联 claims.id，记录指标、比较对象、实验条件与支持理由。按论文类型填写 type_details；未知明确注明，重点须读关键章节。近重复仅建议，核验后 --link-event。'}


def feedback(root, uid, action, reason, now):
    if action not in {'liked','disliked','read'}:raise ValueError('反馈必须为 liked/disliked/read')
    with connection(root) as conn:
        if not conn.execute('SELECT 1 FROM candidates WHERE id=?',(uid,)).fetchone():
            row=conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(uid,)).fetchone()
            if not row:raise ValueError('未知候选 ID 或身份别名')
            uid=row[0]
        conn.execute('INSERT INTO feedback(candidate_id,action,reason,observed_at) VALUES (?,?,?,?)',(uid,action,reason,now.isoformat()))
    return {'id':uid,'action':action,'reason':reason}


def feedback_actions(conn):
    result={}
    for uid,action in conn.execute("SELECT candidate_id,action FROM feedback WHERE action!='read' ORDER BY id"):
        result[uid]=action
    return result


def dedup_keys(card):
    return {prefix+str(card[field]) for field,prefix in [('theme_id','theme:'),('event_id','event:'),('story_id','story:')] if card.get(field)} or {'item:'+card['id']}


def heat(card):
    try:value=float(card.get('engagement',0) or 0)
    except (ValueError,TypeError):return 0
    return value if math.isfinite(value) and value>=0 else 0


def rank(root, conn, cards, now=None):
    cfg=config(root)['selection'];actions=feedback_actions(conn)
    historical=set()
    for (raw,) in conn.execute("SELECT data FROM candidates WHERE state='recommended'"):
        historical.update(dedup_keys(json.loads(raw)))
    names={s['id']:s['name'] for s in registry(root)}
    from .preferences import profile,adjustment
    from .__main__ import TZ
    from datetime import datetime
    interest_profile=profile(conn,now or datetime.now(TZ))
    result=[]
    for c in cards:
        if dedup_keys(c)&historical:continue
        c=dict(c)
        if actions.get(c['id'])=='disliked':continue
        base=20*sum(c['quality_scores'][k]*w for k,w in cfg['weights'].items())
        if base<cfg['minimum_score']:continue
        c['discovery_labels']=[names[sid] for sid in c.get('discovery_sources',[]) if sid in names]
        c['quality_score']=round(base,2)
        boost,_=adjustment(c,interest_profile)
        c['ranking_score']=round(base+boost,2)
        # 偏好信号只用于本地排序解释，不进入公开日报卡片。
        result.append(c)
    return sorted(result,key=lambda c:(-c['ranking_score'],-heat(c),c['id']))


def choose(root, cards):
    cfg=config(root)['selection'];cards=sorted(cards,key=lambda c:(-c.get('ranking_score',c.get('quality_score',0)),-heat(c),c['id']));papers=[c for c in cards if c['kind']=='paper'];news=[c for c in cards if c['kind']!='paper']
    selected=[];ids=set();topics=Counter();subtopics=Counter();themes=set();authors=Counter();ph=0
    def add(c,section=None):
        nonlocal ph
        keys=dedup_keys(c)
        if c['id'] in ids or keys&themes:return False
        if c['kind']=='paper':
            if sum(s['kind']=='paper' for s in selected)>=cfg['paper_limit']:return False
            if topics[c['topic']]>=cfg['maximum_papers_per_topic']:return False
            sub=c.get('subtopic')
            if sub and subtopics[sub]>=cfg.get('maximum_papers_per_subtopic',3):return False
            topics[c['topic']]+=1
            if sub:subtopics[sub]+=1
        else:
            if sum(s['kind']!='paper' for s in selected)>=cfg['news_limit']:return False
            author=c.get('author_key') or (c.get('author') or c['source_id']).strip().casefold()
            if authors[author]>=2:return False
            if c['source_id']=='news:producthunt' and ph>=1:return False
            authors[author]+=1
            if c['source_id']=='news:producthunt':ph+=1
        c=dict(c)
        if section:c['paper_section']=section
        selected.append(c);ids.add(c['id']);themes.update(keys)
        return True
    def section(c):
        if c['review_status'] in {'accepted','published'}:return 'peer_reviewed'
        return 'arxiv'
    buckets={key:[] for key in cfg['section_targets']}
    for c in papers:
        buckets[section(c)].append(c)
        if 'discovery:semantic-scholar' in c.get('discovery_sources',[]):buckets['recommended'].append(c)
    # 各栏先照目标取，同时在每栏优先补充未覆盖主题。
    for name,target in cfg['section_targets'].items():
        pool=buckets[name]
        count=0
        while count<target and sum(c['kind']=='paper' for c in selected)<cfg['paper_limit']:
            candidates=[c for c in pool if c['id'] not in ids and topics[c['topic']]<cfg['maximum_papers_per_topic']]
            if not candidates:break
            if len(topics)<cfg['minimum_paper_topics']:
                diverse=[c for c in candidates if c['topic'] not in topics]
                if diverse:candidates=diverse
            if add(candidates[0],name):count+=1
            else:pool=[c for c in pool if c['id']!=candidates[0]['id']]
    for c in papers:
        if len([s for s in selected if s['kind']=='paper'])>=cfg['paper_limit']:break
        add(c,section(c))
    for c in news:
        if len([s for s in selected if s['kind']!='paper'])>=cfg['news_limit']:break
        add(c)
    return selected


def compose(root, now, dry_run=False):
    from .site import publication_lock
    with publication_lock(root):return _compose(root,now,dry_run=dry_run)


def update_source_metrics(conn, entry, candidates, selected, day):
    """按当前批次、当天和累计三种范围统计，历史审核不充当当天审核。"""
    sid=entry['id']
    pool=[c for c in candidates if sid in c.get('discovery_sources',[])]
    def canonical(uid):
        row=conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(uid,)).fetchone()
        return row[0] if row else uid
    batch_ids={canonical(uid) for uid in entry.get('candidate_ids',[])}
    runs=[json.loads(r[0]) for r in conn.execute('SELECT details FROM source_runs WHERE source_id=?',(sid,))]
    daily=[r for r in runs if r.get('observed_at','')[:10]==day]
    day_ids={canonical(uid) for r in daily for uid in r.get('candidate_ids',[])}
    selected_ids={c['id'] for c in selected}
    def metrics(scope, current=False):
        verified=[c for c in scope if c.get('_candidate_state') in {'reviewed','recommended'}
                  and (not current or c.get('reviewed_at','')[:10]==day)]
        dated=sum(bool(c.get('date_evidence_url')) and bool(c.get('first_public_date') or c.get('published_date') or c.get('accepted_date')) for c in scope)
        return dict(candidates=len(scope),verified=len(verified),selected=sum(c['id'] in selected_ids for c in scope),
                    date_evidence_completeness=dated/len(scope) if scope else None)
    batch=metrics([c for c in pool if c['id'] in batch_ids],True)
    batch.update(discovered=entry.get('discovered',0),merged=entry.get('merged',0))
    daily_metrics=metrics([c for c in pool if c['id'] in day_ids],True)
    # 当天可审核历史池内候选；独立统计，不受当天是否重新发现限制。
    daily_metrics['verified']=sum(c.get('_candidate_state') in {'reviewed','recommended'} and c.get('reviewed_at','')[:10]==day for c in pool)
    daily_metrics['selected']=sum(c['id'] in selected_ids for c in pool)
    daily_metrics.update(discovered=sum(r.get('discovered',0) for r in daily),merged=sum(r.get('merged',0) for r in daily))
    cumulative=metrics(pool)
    cumulative.update(discovered=sum(r.get('discovered',0) for r in runs),merged=sum(r.get('merged',0) for r in runs),
                      selected=sum(c.get('_candidate_state')=='recommended' for c in pool))
    entry.update(batch_metrics=batch,daily_metrics=daily_metrics,cumulative_metrics=cumulative,
                 verified=batch['verified'],selected=batch['selected'],date_evidence_completeness=batch['date_evidence_completeness'])


def _compose(root, now, dry_run=False):
    from .__main__ import atomic_write, window_start
    from .site import queue_edition
    day=now.date().isoformat();cfg=config(root);rejected=[]
    expected={s['id'] for s in due(root,now)}
    with connection(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT data,sent_at FROM web_editions WHERE day=?',(day,)).fetchone()
        if row and row[1]:return {'day':day,'status':'ok','delivery':'already_sent','frozen':True,'selected_items':len(json.loads(row[0])['cards'])}
        attempt=conn.execute('SELECT state FROM send_attempts WHERE day=?',(day,)).fetchone()
        if row and attempt and attempt[0] in {'sending','uncertain'}:
            return {'day':day,'status':'partial','delivery':'needs_reconciliation','frozen':True,'selected_items':len(json.loads(row[0])['cards'])}
        latest=[json.loads(r[0]) for r in conn.execute('SELECT details FROM source_runs WHERE id IN (SELECT max(id) FROM source_runs GROUP BY source_id)')]
        fresh=[s for s in latest if s.get('observed_at',day)[:10]==day]
        missing=expected-{s['id'] for s in fresh}
        fresh += [{'id':sid,'status':'not_checked','coverage':'unknown','note':'本次尚未读取来源，保留待检查'} for sid in sorted(missing)]
        cards=[]
        for uid,raw in conn.execute("SELECT id,data FROM candidates WHERE state='reviewed'").fetchall():
            try:
                card=validate_review(root,json.loads(raw),now)
                if card.get('review_version',1)<cfg['selection'].get('minimum_review_version',1):raise ValueError('旧审核须补充新版结构与证据')
                if cfg['selection'].get('minimum_review_version',1)>=3:
                    from .quality import validate
                    validate(card)
                cards.append(card)
            except (ValueError,KeyError,TypeError) as exc:
                rejected.append({'id':uid,'reason':str(exc)})
        ranked=rank(root,conn,cards,now);selected=choose(root,ranked)
        modern=cfg['selection'].get('minimum_review_version',1)>=3
        if modern:
            from .quality import focus_cards,check
            from .media import prepare
            focus_ids={c['id'] for c in focus_cards(selected)}
            for c in selected:
                c['focus']=c['id'] in focus_ids
                if not c['focus']:c.pop('figure',None)
                prepare(root,c)
                c['historical_backfill']=(now.date()-__import__('datetime').date.fromisoformat(content_date(c))).days>7
            diagnostic=check(selected,root)
            bad={e.get('id') for e in diagnostic['errors']}
            selected=[c for c in selected if c['id'] not in bad]
        for c in selected:
            c['related_links']=[{'url':other['url'],'title':other['title']} for other in ranked
                                if other['id']!=c['id'] and ((c.get('theme_id') and c.get('theme_id')==other.get('theme_id')) or (c.get('story_id') and c.get('story_id')==other.get('story_id')))]
            from .events import relations
            for relation in relations(conn,c['id']):
                other=conn.execute('SELECT data,state FROM candidates WHERE id=?',(relation['other_id'],)).fetchone()
                if other and other[1] in {'reviewed','recommended'}:
                    record=json.loads(other[0])
                    c['related_links'].append(dict(title=record['title'],url=record['url'],relation=relation['relation'],direction=relation['direction'],reason=relation['reason'],evidence_url=relation['evidence_url']))
        ok=sum(s.get('status')=='ok' for s in fresh)
        status='failed' if ok==0 else ('partial' if any(s.get('status')!='ok' for s in fresh) else 'ok')
        counts={'paper':sum(c['kind']=='paper' for c in selected),'news':sum(c['kind']!='paper' for c in selected)}
        shortfalls=[]
        section_counts=Counter(c.get('paper_section') for c in selected if c['kind']=='paper')
        for section,target in cfg['selection']['section_targets'].items():
            if section_counts[section]<target:
                label={'peer_reviewed':'已录用会议／正式期刊','arxiv':'arXiv 前沿','recommended':'Semantic Scholar 推荐'}[section]
                shortfalls.append(label+'本期核验入选 '+str(section_counts[section])+'/'+str(target)+'，缺额仅用其他已核验合格候选补位。')
        if counts['paper']<cfg['selection']['paper_limit']:shortfalls.append('论文合格候选、主题多样性与去重筛选后入选 %d/%d 篇；待审核和受限来源见采集说明。'%(counts['paper'],cfg['selection']['paper_limit']))
        if counts['news']<cfg['selection']['news_limit']:shortfalls.append('资讯精选 %d/%d 条，不以营销或重复内容补位。'%(counts['news'],cfg['selection']['news_limit']))
        data={'pipeline_version':3 if modern else 2,'day':day,'cards':selected,'sources':fresh,'status':status,'rejected':rejected,'shortfalls':shortfalls,
              'windows':{k:{'start':window_start(now,2).date().isoformat(),'end':day} for k in ['paper','x','news']},'weekly_review':''}
        if modern:data['quality']=check(selected,root)
        weekly=conn.execute("SELECT value FROM pipeline_meta WHERE key='weekly_review:'+?",(day,)).fetchone()
        if weekly:data['weekly_review']=weekly[0]
        result={'day':day,'status':status,'selected_items':len(selected),'counts':counts,'shortfalls':shortfalls,'rejected':rejected,'cards':selected,'delivery':'not_sent'}
        if not dry_run and status!='failed' and selected:
            # 只有微信确认接受后才写入历史推荐，生成失败不会消耗候选。
            conn.execute("INSERT INTO deliveries(day,content,status) VALUES (?,'','pending') ON CONFLICT(day) DO UPDATE SET status='pending' WHERE status!='sent'",(day,))
        if not dry_run:
            selected_ids={c['id'] for c in selected};ranked_ids={c['id'] for c in ranked};actions=feedback_actions(conn)
            for c in cards:
                reason='已精选，等待发布与确认发送' if c['id'] in selected_ids else '明确不感兴趣' if actions.get(c['id'])=='disliked' else '历史事件／主题已推荐或评分低于65分' if c['id'] not in ranked_ids else '去重、主题／作者上限或排名未入选'
                conn.execute('UPDATE candidates SET reason=? WHERE id=?',(reason,c['id']))
            for item in rejected:conn.execute('UPDATE candidates SET reason=? WHERE id=?',(item['reason'],item['id']))
            metric_candidates=[dict(json.loads(raw),_candidate_state=state) for raw,state in conn.execute('SELECT data,state FROM candidates')]
            for entry in fresh:
                sid=entry['id']
                if entry['status']=='not_checked':continue
                update_source_metrics(conn,entry,metric_candidates,selected,day)
                rowid=conn.execute('SELECT max(id) FROM source_runs WHERE source_id=?',(sid,)).fetchone()[0]
                conn.execute('UPDATE source_runs SET details=? WHERE id=?',(json.dumps(entry,ensure_ascii=False),rowid))
            if status!='failed' and selected:queue_edition(conn,day,data)
    if not dry_run:atomic_write(root/'state/tech/last_run.json',json.dumps({k:v for k,v in result.items() if k!='cards'},ensure_ascii=False,indent=2))
    return result


def resolve_delivery(root,day,decision,reason,now):
    if decision not in {'sent','retry'} or not reason.strip():raise ValueError('核对结论必须为 sent/retry，并用 --reason 记录依据')
    with connection(root) as conn:
        row=conn.execute('SELECT state FROM send_attempts WHERE day=?',(day,)).fetchone()
        if not row or row[0] not in {'sending','uncertain'}:raise ValueError('没有待核对的发送记录')
        conn.execute('UPDATE send_attempts SET state=?,reason=? WHERE day=?',(decision,reason,day))
        if decision=='sent':
            conn.execute('UPDATE web_editions SET sent_at=? WHERE day=?',(now.isoformat(),day))
            conn.execute("UPDATE deliveries SET status='sent',content='',sent_at=? WHERE day=?",(now.isoformat(),day))
            data=json.loads(conn.execute('SELECT data FROM web_editions WHERE day=?',(day,)).fetchone()[0])
            from .site import mark_recommended
            mark_recommended(conn,day,data['cards'])
    return {'day':day,'resolution':decision}


def health(root, now):
    cfg=config(root)
    with connection(root) as conn:
        counts=dict(conn.execute('SELECT state,count(*) FROM candidates GROUP BY state'))
        recent=[dict(id=r[0],checked_at=r[1],coverage=r[2],backfill_complete=bool(r[3]),failures=r[4],cursor=json.loads(r[5] or '{}'),backfill_cursor=json.loads(r[6] or '{}')) for r in conn.execute('SELECT id,checked_at,coverage,backfill_complete,failures,cursor,backfill_cursor FROM source_state')]
        pending=conn.execute('SELECT count(*) FROM web_editions WHERE sent_at IS NULL').fetchone()[0]
        last_edition=conn.execute('SELECT max(day) FROM web_editions').fetchone()[0]
        quality_row=conn.execute('SELECT data FROM web_editions ORDER BY day DESC LIMIT 1').fetchone()
        from .quality import check
        quality_health=check(json.loads(quality_row[0])['cards'],root) if quality_row and json.loads(quality_row[0]).get('pipeline_version',1)>=3 else {'status':'legacy','metrics':{},'warnings':[{'reason':'最新正式日报仍使用旧审核格式'}]}
        publication_pending=[]
        for uid,raw,reason in conn.execute("SELECT id,data,reason FROM candidates WHERE state='pending'"):
            card=json.loads(raw)
            if not card.get('source_id','').startswith('paper:'):continue
            missing=card.get('publication_missing') or ['官方发表状态、轨道与事件日期待审核']
            publication_pending.append({'id':uid,'source_id':card['source_id'],'missing':missing,'reason':reason})
        last_sent=conn.execute('SELECT max(day) FROM web_editions WHERE sent_at IS NOT NULL').fetchone()[0]
        last_metrics=[json.loads(r[0]) for r in conn.execute('SELECT details FROM source_runs WHERE id IN (SELECT max(id) FROM source_runs GROUP BY source_id)')]
        uncertain=conn.execute("SELECT day,state FROM send_attempts WHERE state IN ('sending','uncertain')").fetchall()
        publications=[dict(day=r[0],stage=r[1],updated_at=r[2]) for r in conn.execute('SELECT * FROM publication_runs ORDER BY day DESC LIMIT 7')]
    trials=[]
    for path in sorted((root/'state/tech/trials').glob('*.json')):
        state=json.loads(path.read_text());trials.append({'id':state['id'],'delivery':state.get('delivery'),'stage':state.get('stage'),'quality':state.get('data',{}).get('quality',{})})
    uncollected=sorted({s['id'] for s in registry(root)}-{s['id'] for s in recent})
    warnings=['最新日报质量检查存在阻断问题'] if quality_health.get('status')=='failed' else []
    warnings+=['连续失败来源：'+s['id'] for s in recent if s['failures']>=3]
    warnings+=['试刊微信结果待核对：'+t['id'] for t in trials if t['delivery'] in {'sending','uncertain'}]
    warnings+=['微信发送结果待核对：'+day for day,_ in uncertain]
    deadline=now.replace(hour=10,minute=30,second=0,microsecond=0)+timedelta(minutes=cfg.get('runtime',{}).get('delivery_grace_minutes',120))
    from .__main__ import timestamp
    anchor=(now if now>=deadline else now-timedelta(days=1)).date()
    state_map={s['id']:s for s in recent}
    stale=[]
    for source in registry(root):
        state=state_map.get(source['id'])
        if not state:continue  # 未检查来源单独列出。
        required=anchor
        cadence=source.get('cadence','daily')
        if cadence=='rotating':
            while required.toordinal()%3!=source['rotation_group']:required-=timedelta(days=1)
        elif cadence=='twice_weekly':
            while required.weekday() not in {1,4}:required-=timedelta(days=1)
        elif cadence=='weekly':required-=timedelta(days=6)
        checked=state['cursor'].get('content_checked_at') if source['adapter']=='browser' else state['checked_at']
        try:expired=not checked or timestamp(checked).date()<required
        except (ValueError,TypeError):expired=True
        if expired:stale.append(dict(id=source['id'],last_content_check=checked,required_since=required.isoformat(),cadence=cadence))
    warnings+=['来源检查逾期：'+s['id'] for s in stale]
    overdue=now>=deadline and last_sent!=now.date().isoformat()
    if overdue:warnings.append('当天日报尚未确认推送，已超过开始时间后的运行宽限；检查采集、Pages及微信阶段')
    warnings+=['页面发布失败：'+p['day'] for p in publications if p['stage']=='error' and p['day']==now.date().isoformat()]
    gaps=[{'id':s['id'],'status':s['status'],'note':s.get('note','')} for s in last_metrics if s['status']!='ok']
    return {'pipeline_version':2,'status':'partial' if warnings or gaps or uncollected else 'ok','observed_at':now.isoformat(),
            'candidates':counts,'sources':recent,'uncollected_sources':uncollected,'source_gaps':gaps,'stale_sources':stale,
            'latest_edition':last_edition,'latest_sent_edition':last_sent,'delivery_overdue':overdue,
            'quality':quality_health,'trials':trials,'source_metrics':last_metrics,'publication_stages':publications,'pending_editions':pending,
            'publication_verification':{'pending':len(publication_pending),'tasks':publication_pending[:30],'policy':'官方状态/轨道/精确事件日期缺失留待核验，不用预印本掩盖正式发表缺口'},
            'uncertain_deliveries':uncertain,'warnings':warnings,
            'runtime':'本机Codex需运行、联网，X登录有效；10:30开始，不保证关机状态下采集'}
