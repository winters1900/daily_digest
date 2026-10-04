import argparse
import calendar
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml
from mail_digest.delivery import send_wechat

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo('Asia/Shanghai')
STATUSES = {'ok', 'error', 'blocked'}
READING = {'帖子', '摘要', '正文', '复现'}


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return result.astimezone(TZ)


def load_settings(root):
    def read(name):
        return yaml.safe_load((root / name).read_text(encoding='utf-8'))
    return read('x_accounts.yaml'), read('paper_sources.yaml')


def window_start(now, months):
    """按北京时间自然日回退自然月，月末截到目标月最后一天。"""
    if not isinstance(months, int) or months < 1:
        raise ValueError('window_months 必须是正整数')
    year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
    month += 1
    return now.replace(year=year, month=month,
                       day=min(now.day, calendar.monthrange(year, month)[1]),
                       hour=0, minute=0, second=0, microsecond=0)


def source_window(root, kind, now):
    x, papers = load_settings(root)
    months = x['collection']['window_months'] if kind == 'x' else papers['selection']['window_months']
    return {'months': months, 'start': window_start(now, months).date().isoformat(),
            'end': now.date().isoformat()}


def sources(root):
    from . import pipeline
    if pipeline.enabled(root): return pipeline.registry(root)
    x, papers = load_settings(root)
    result = []
    for account in x['accounts']:
        if account.get('core') is False: continue
        result.append(dict(account, id='x:' + account['handle'].lower(), kind='x', cadence=account.get('legacy_cadence',account['cadence'])))
    for index, source in enumerate(papers['sources']):
        if papers.get('version') == 2 and not source.get('legacy_ids'): continue
        result.append(dict(source, id='paper:' + str(index), kind='paper', cadence='daily'))
    return result


def database(root):
    folder = root / 'state' / 'tech'
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(folder / 'digest.sqlite3'), timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, day TEXT NOT NULL, card TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, checked_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS deliveries (day TEXT PRIMARY KEY, content TEXT NOT NULL, status TEXT NOT NULL, sent_at TEXT, error TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS deferred (id TEXT PRIMARY KEY, card TEXT NOT NULL)')
    if 'window_months' not in {row[1] for row in conn.execute('PRAGMA table_info(checks)')}:
        conn.execute('ALTER TABLE checks ADD COLUMN window_months INTEGER')
    conn.commit()
    return conn


def due_sources(root, now):
    from . import pipeline
    if pipeline.enabled(root): return pipeline.due(root,now)
    with database(root) as conn:
        previous = {row[0]: row[1:] for row in conn.execute('SELECT id, checked_at, window_months FROM checks')}
    # 首次检查所有来源；每周作者七天内已检查过时跳过。
    return [s for s in sources(root) if s['cadence'] != 'weekly' or s['id'] not in previous
            or previous[s['id']][1] != source_window(root, s['kind'], now)['months']
            or now - timestamp(previous[s['id']][0]) >= timedelta(days=7)]


def plan(root, now):
    from . import pipeline
    if pipeline.enabled(root): return pipeline.plan(root,now)
    return {
        'date': now.date().isoformat(), 'timezone': 'Asia/Shanghai',
        'observed_at': now.isoformat(), 'sources': due_sources(root, now),
        'windows': {kind: source_window(root, kind, now) for kind in ('x', 'paper')},
        'selection': load_settings(root)[1]['selection'],
        'input_path': str(root / 'state/tech/collection.json'),
        'instructions': str(root / 'TECH_DIGEST.md'),
        'cost_policy': '读取正常网页，不调用付费 X 或摘要 API；由当前 Codex 任务完成摘要。',
        'input_schema': {
            'observed_at': '带时区的本次采集时间',
            'sources': [{'id': '来自 plan 的来源 id', 'status': 'ok/error/blocked',
                         'note': '检查范围、无入选原因或具体故障', 'evidence_urls': ['实际读取的来源网页'],
                         'window_months': '本批次实际使用的月份窗口，整数',
                         'coverage': 'window_checked/sample/index_only/unknown；受限时不宣称窗口覆盖'}],
            'items': [{'kind': 'x/paper', 'source_id': '来自 plan', 'title': '中文标题',
                       'url': '原文 HTTPS 链接', 'published_date': 'YYYY-MM-DD 或 published_at',
                       'summary': '原创中文摘要', 'why': '价值与适用范围',
                       'action': '实践动作', 'limitations': '局限与未验证点',
                       'reading_depth': '帖子/摘要/正文/复现',
                       'content_type': 'X专用：工程实践/研究解读/作者观点/待核验线索',
                       'evidence_excerpt': '简短原文依据，非整篇复制',
                       'venue': '论文专用：会刊', 'review_status': 'published/accepted/preprint',
                       'track': 'main/journal/findings/workshop/demo',
                       'verification_url': '论文专用：官方论文页或最终决定页',
                       'first_public_date': '可选：首次公开日期', 'accepted_date': '可选',
                       'doi': '可选：DOI', 'arxiv_id': '可选', 'code_url': '可选',
                       'event_type': 'new_research/publication_update，论文必填',
                       'theme_id': '可选：同一论文与博主解读使用相同主题 id'}]}}


def https_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('原文必须是无凭据的 HTTPS 链接')
    return parsed


def publication_date(card, observed_at):
    if card.get('published_at'):
        return timestamp(card['published_at'])
    if card.get('published_date'):
        return datetime.strptime(card['published_date'], '%Y-%m-%d').replace(tzinfo=TZ)
    if card.get('relative_age_hours') is not None:
        age = float(card['relative_age_hours'])
        if age < 0:
            raise ValueError('相对时间不能为负')
        return observed_at - timedelta(hours=age)
    raise ValueError('缺少发布时间，不能作为最新内容入选')


def validate_card(card, source_map, root, now, observed_at):
    card = dict(card)
    for field in ('source_id', 'kind', 'title', 'url', 'summary', 'why', 'limitations', 'reading_depth', 'evidence_excerpt'):
        if not isinstance(card.get(field), str) or not card[field].strip():
            raise ValueError('缺少字段：' + field)
    if card['reading_depth'] not in READING:
        raise ValueError('阅读深度无效')
    source = source_map.get(card['source_id'])
    if source is None or source['kind'] != card['kind']:
        raise ValueError('来源不在固定名单或类型不匹配')
    cutoff = window_start(now, source_window(root, card['kind'], now)['months'])
    pub = publication_date(card, observed_at)
    if pub > now + timedelta(minutes=5):
        raise ValueError('发布时间在未来')
    url = https_url(card['url'])
    if card['kind'] == 'x':
        match = re.fullmatch(r'/([^/]+)/status/(\d+)/?', url.path)
        if url.hostname not in {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'} or not match:
            raise ValueError('X 原文不是帖子链接')
        if match[1].lower() != source['handle'].lower():
            raise ValueError('X 帖子作者不在该固定来源')
        card['url'] = 'https://x.com/' + source['handle'] + '/status/' + match[2]
        card['id'] = 'x:' + match[2]
        expired = pub < cutoff
        if card.get('content_type') not in {None, '工程实践', '研究解读', '作者观点', '待核验线索'}:
            raise ValueError('博主内容类型无效')
    elif card['kind'] == 'paper':
        for field in ('venue', 'review_status', 'track', 'verification_url', 'event_type'):
            if not card.get(field):
                raise ValueError('论文缺少字段：' + field)
        if card['venue'] not in source['venues']:
            raise ValueError('论文会刊不在该来源')
        if card['review_status'] not in {'published', 'accepted'}:
            raise ValueError('预印本或未知录用状态不进入会刊精选')
        if card['event_type'] not in {'new_research', 'publication_update'}:
            raise ValueError('论文时间事件无效')
        verify = https_url(card['verification_url'])
        allowed = {urlparse(source['url']).hostname}
        if card['venue'] in {'ICLR', 'TMLR'}:
            allowed.add('openreview.net')
        if card['venue'] == 'NeurIPS':
            allowed.add('neurips.cc')
        if verify.hostname not in allowed:
            raise ValueError('缺少对应会刊官方核验来源')
        if card['track'] not in {'main', 'journal', 'findings', 'workshop', 'demo'}:
            raise ValueError('论文轨道无效')
        for field in ('first_public_date', 'accepted_date'):
            if card.get(field):
                day = datetime.strptime(card[field], '%Y-%m-%d').date()
                if day > now.date():
                    raise ValueError(field + '在未来')
        expired = pub < cutoff
        if not card.get('first_public_date'):
            card['event_type'] = 'publication_update'
        if card.get('first_public_date') and card['first_public_date'] < cutoff.date().isoformat():
            card['event_type'] = 'publication_update'
        card['id'] = 'paper:' + (card.get('doi') or card.get('arxiv_id') or card['url']).lower()
    else:
        raise ValueError('未知内容类型')
    if expired:
        raise ValueError('内容超出时间窗口')
    card['published_label'] = card.get('published_at') or card.get('published_date') or ('采集时约 %s 小时前' % card['relative_age_hours'])
    card['author'] = source.get('name', '')
    return card


def md(text):
    return str(text).replace('\n', ' ').replace('\r', '').replace('[', '\\[').replace(']', '\\]')


def render(day, cards, statuses, result, rejected, windows=None):
    lines = ['# 技术知识日报 · ' + day, '',
             '**采集执行：' + {'ok': '已完成本批次导入', 'partial': '部分来源失败或未检查', 'failed': '采集失败'}[result] + '**', '']
    if windows:
        lines += ['筛选窗口：博主 ' + windows['x']['start'] + ' 至 ' + windows['x']['end']
                  + '；论文 ' + windows['paper']['start'] + ' 至 ' + windows['paper']['end'] + '（最近两个自然月）。', '']
    limited = sum(s.get('coverage') != 'window_checked' for s in statuses)
    lines += ['**覆盖范围：** ' + (str(limited) + ' 个来源仅抽样、索引检查或未标明覆盖；不能据此判断整个窗口没有优质内容。'
                                  if limited else '本批次来源已按窗口检查；页面访问仍可能受平台限制。'), '',
              '## 固定博主精选', '']
    for section, group in [('x', '## 论文精选'), ('paper', None)]:
        selected = [c for c in cards if c['kind'] == section]
        if not selected:
            lines += ['本次没有符合时间与质量条件的新内容；请同时检查下方来源状态。', '']
        themes = set()
        for c in selected:
            if section == 'x' and c.get('theme_id') and any(other['kind'] == 'paper' and other.get('theme_id') == c['theme_id'] for other in cards):
                continue
            if c.get('theme_id') and c['theme_id'] in themes:
                continue
            if c.get('theme_id'):
                themes.add(c['theme_id'])
            lines += ['### [' + md(c['title']) + '](' + c['url'] + ')', '']
            if section == 'x':
                lines += ['作者：' + md(c['author']) + '；发布时间：' + c['published_label'] + '；阅读深度：' + c['reading_depth'],
                          '内容类型：' + c.get('content_type', '未分类') + '；' + ('作者观点或待核验观察，不作为研究结论。' if c.get('content_type') in {'作者观点', '待核验线索'} else '实践经验与事实结论仍需依据原文核验。'), '']
            else:
                event = '近期发表／录用动态' if c['event_type'] == 'publication_update' else '新研究'
                lines += [md(c['venue']) + ' · ' + md(c['track']) + ' · ' + md(c['review_status']) + ' · ' + event,
                          c.get('date_label', '发表／录用日期') + '：' + c['published_label'] + '；首次公开：' + str(c.get('first_public_date') or '未知') + '；阅读深度：' + c['reading_depth'],
                          '[会刊核验](' + c['verification_url'] + ')', '']
            lines += [c['summary'], '', '**阅读价值：** ' + c['why'], '', '**局限：** ' + c['limitations'], '']
            if c.get('action'):
                lines += ['**实践动作：** ' + c['action'], '']
            if c.get('code_url'):
                lines += ['[代码](' + c['code_url'] + ')', '']
            if c.get('date_evidence_url'):
                lines += ['[发表日期依据](' + c['date_evidence_url'] + ')', '']
            linked = [other for other in cards if c.get('theme_id') and other.get('theme_id') == c['theme_id'] and other['id'] != c['id']]
            if linked:
                lines += ['相关解读／原论文：' + '、'.join('[' + md(a['title']) + '](' + a['url'] + ')' for a in linked), '']
        if group:
            lines += [group, '']
    lines += ['## 来源检查', '', '| 来源 | 访问状态 | 覆盖程度 | 检查范围／原因 |', '|---|---|---|---|']
    for s in statuses:
        label = s['id']
        lines.append('| ' + md(label) + ' | ' + md(s['status']) + ' | ' + {'window_checked': '窗口检查', 'sample': '抽样', 'index_only': '仅索引', 'unknown': '未核验'}.get(s.get('coverage'), '未核验') + ' | ' + md(s.get('note', '')).replace('|', '\\|') + ' |')
    if rejected:
        lines += ['', '## 未入选内容', ''] + ['- ' + md(r) for r in rejected]
    lines += ['', '---', '本报告由浏览器／官方网页采集及 Codex 摘要生成；未调用付费 X 或摘要 API。']
    return '\n'.join(lines) + '\n'


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix='.digest-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def ingest(root, payload, now, save_local=False):
    from . import pipeline
    if pipeline.enabled(root): return pipeline.ingest(root,payload,now)
    observed = timestamp(payload['observed_at'])
    if observed > now + timedelta(minutes=5) or now - observed > timedelta(hours=6):
        raise ValueError('采集批次过期或来自未来；请重新采集')
    source_map = {s['id']: s for s in sources(root)}
    expected = {s['id'] for s in due_sources(root, now)}
    statuses = {}
    for entry in payload['sources']:
        sid = entry['id']
        if sid not in source_map or sid in statuses or entry['status'] not in STATUSES:
            raise ValueError('来源状态未知、重复或不合法：' + sid)
        if not entry.get('note') or not entry.get('evidence_urls'):
            raise ValueError('来源缺少检查说明或实际访问链接：' + sid)
        for url in entry['evidence_urls']:
            https_url(url)
        if entry.get('coverage', 'unknown') not in {'window_checked', 'sample', 'index_only', 'unknown'}:
            raise ValueError('覆盖程度无效：' + sid)
        statuses[sid] = entry
    for sid in expected - statuses.keys():
        statuses[sid] = {'id': sid, 'status': 'not_checked', 'note': '本次未提供采集结果'}
    accepted, rejected = [], []
    for item in payload['items']:
        try:
            if statuses.get(item['source_id'], {}).get('status') != 'ok':
                raise ValueError('来源未检查成功')
            accepted.append(validate_card(item, source_map, root, now, observed))
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append(str(item.get('title', '未命名')) + '：' + str(exc))
    checked = sum(s['status'] == 'ok' for s in statuses.values())
    failed = any(s['status'] != 'ok' for s in statuses.values())
    result = 'failed' if checked == 0 else ('partial' if failed else 'ok')
    day = now.date().isoformat()
    x_cfg, paper_cfg = load_settings(root)
    daily_limits = {'x': x_cfg['collection']['daily_target_items'],
                    'paper': paper_cfg['selection']['daily_target_items']}
    output = root / 'reports/tech' / (day + '.md')
    with database(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        from .site import init_editions
        init_editions(conn)
        previous = {r[0] for r in conn.execute('SELECT id FROM items WHERE day != ?', (day,))}
        today = {r[0]: json.loads(r[1]) for r in conn.execute('SELECT id, card FROM items WHERE day = ?', (day,))}
        sent_today = (conn.execute("SELECT 1 FROM deliveries WHERE day=? AND status='sent'", (day,)).fetchone() is not None
                      or conn.execute('SELECT 1 FROM web_editions WHERE day=? AND sent_at IS NOT NULL', (day,)).fetchone() is not None)
        for sid, raw in conn.execute('SELECT id,card FROM deferred').fetchall():
            try:
                accepted.append(validate_card(json.loads(raw), source_map, root, now, observed))
            except ValueError:
                conn.execute('DELETE FROM deferred WHERE id=?', (sid,))
        new = 0
        for card in accepted:
            if card['id'] in previous:
                rejected.append(card['title'] + '：历史已推荐')
                continue
            if sent_today and card['id'] not in today:
                conn.execute('INSERT OR REPLACE INTO deferred VALUES (?,?)', (card['id'], json.dumps(card, ensure_ascii=False)))
                rejected.append(card['title'] + '：当日已推送，已留待次日')
                continue
            author_count = sum(c['source_id'] == card['source_id'] for c in today.values())
            if card['kind'] == 'x' and card['id'] not in today and author_count >= 2:
                rejected.append(card['title'] + '：超过单作者配额')
                continue
            limit = daily_limits[card['kind']]
            if card['id'] not in today and sum(c['kind'] == card['kind'] for c in today.values()) >= limit:
                rejected.append(card['title'] + '：超过当日精选配额')
                continue
            if card['id'] not in today:
                new += 1
            today[card['id']] = card
            conn.execute('DELETE FROM deferred WHERE id=?', (card['id'],))
        cards = list(today.values())
        from .site import queue_edition
        queue_edition(conn, day, {'day': day, 'cards': cards, 'sources': list(statuses.values()),
                                'status': result, 'rejected': rejected,
                                'windows': {kind: source_window(root, kind, now) for kind in ('x', 'paper')},
                                'weekly_review': payload.get('weekly_review', '')})
        # 失败重试不覆盖同一天已经生成的有效日报。
        report_path = output if result != 'failed' else root / 'reports/tech' / (day + '-failure.md')
        content = render(day, cards, list(statuses.values()), result, rejected,
                         {kind: source_window(root, kind, now) for kind in ('x', 'paper')})
        if payload.get('weekly_review'):
            if not isinstance(payload['weekly_review'], str):
                raise ValueError('weekly_review 必须是正文字符串')
            content += '\n## 本周论文精读\n\n' + payload['weekly_review'] + '\n'
        if save_local:
            atomic_write(report_path, content)
        # 一天只投递一次。发送失败保留正文供重试，成功后清空队列正文。
        conn.execute("INSERT INTO deliveries(day,content,status) VALUES (?,?,'pending') "
                     "ON CONFLICT(day) DO UPDATE SET content=excluded.content "
                     "WHERE deliveries.status != 'sent'", (day, content))
        for card in cards:
            conn.execute('INSERT OR REPLACE INTO items VALUES (?, ?, ?)', (card['id'], day, json.dumps(card, ensure_ascii=False)))
        for sid, check in statuses.items():
            if check['status'] == 'ok' and check.get('window_months') == source_window(root, source_map[sid]['kind'], now)['months']:
                conn.execute('INSERT OR REPLACE INTO checks(id, checked_at, window_months) VALUES (?, ?, ?)',
                             (sid, observed.isoformat(), check['window_months']))
        conn.execute('INSERT INTO runs(observed_at,status,details) VALUES (?,?,?)', (observed.isoformat(), result, json.dumps(list(statuses.values()), ensure_ascii=False)))
    report = str(report_path) if save_local else None
    details = {'day': day, 'status': result, 'report': report, 'new_items': new, 'selected_items': len(cards),
               'checked_sources': checked, 'total_sources': len(statuses), 'rejected': rejected,
               'delivery': 'pending_or_already_sent'}
    atomic_write(root / 'state/tech/last_run.json', json.dumps(details, ensure_ascii=False, indent=2))
    return details


def push_pending(root, now):
    from .site import enabled, publish_pending
    if enabled(root):
        return publish_pending(root, now)
    with database(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT day,content FROM deliveries WHERE status='pending' ORDER BY day LIMIT 1").fetchone()
        if row is None:
            return {'delivery': 'nothing_pending'}
        day, content = row
        try:
            send_wechat(day, content, title='技术知识日报 ' + day)
        except Exception:
            # 不记录异常文本，第三方请求异常可能含凭据。
            conn.execute("UPDATE deliveries SET error=? WHERE day=?", ('微信推送失败，保留待重试', day))
            conn.commit()
            raise RuntimeError('微信推送失败，正文已保留待重试；请检查邮件模块的Server酱配置和网络') from None
        conn.execute("UPDATE deliveries SET status='sent',content='',sent_at=?,error=NULL WHERE day=?", (now.isoformat(), day))
    path = root / 'state/tech/last_run.json'
    if path.exists():
        details = json.loads(path.read_text(encoding='utf-8'))
        if details['day'] == day:
            details['delivery'] = 'sent'
            atomic_write(path, json.dumps(details, ensure_ascii=False, indent=2))
    return {'delivery': 'sent', 'day': day}


def main(argv=None):
    parser = argparse.ArgumentParser(description='固定博主与论文技术日报')
    parser.add_argument('--plan', action='store_true', help='输出本次需要采集的来源与输入格式')
    parser.add_argument('--input', type=Path, help='导入浏览器／官方网页采集的 JSON')
    parser.add_argument('--status', action='store_true', help='查看最近运行状态')
    parser.add_argument('--push', action='store_true', help='通过邮件模块同一微信通道推送，失败可独立重试')
    parser.add_argument('--save-local', action='store_true', help='可选：另存本地Markdown，默认只推送')
    parser.add_argument('--publish', metavar='YYYY-MM-DD', help='发布已归档日报HTML并推送链接')
    parser.add_argument('--build-site', action='store_true', help='生成本仓库docs目录的HTML日报')
    parser.add_argument('--collect', action='store_true', help='采集公开来源并保存候选及浏览器补查任务')
    parser.add_argument('--source', action='append', help='限定公开采集来源，可重复指定稳定 ID')
    parser.add_argument('--review-queue', action='store_true', help='输出 BM25 初筛后的待审核候选')
    parser.add_argument('--quality-check', action='store_true', help='检查新版审核与图片证据，可配合 --input')
    parser.add_argument('--trial', type=Path, help='独立图文试刊JSON，配合 --dry-run/--push')
    parser.add_argument('--resolve-trial', nargs=2, metavar=('ID','DECISION'), help='人工核对试刊 sent/retry，须提供 --reason')
    parser.add_argument('--compose', action='store_true', help='按证据评分与分区配额生成日报')
    parser.add_argument('--dry-run', action='store_true', help='只预览精选，不生成或发送日报')
    parser.add_argument('--health', action='store_true', help='候选、覆盖进度与投递健康检查')
    parser.add_argument('--evaluate', action='store_true', help='离线质量回放，--input指定人工标注样例；不修改推荐历史')
    parser.add_argument('--profile', action='store_true', help='查看仅由明确反馈产生的本地兴趣')
    parser.add_argument('--preference', nargs=2, metavar=('TERM','WEIGHT'), help='明确设置细分关键词偏好，权重-5至5，0取消')
    parser.add_argument('--ttl-days', type=int, help='临时偏好有效天数，省略为长期')
    parser.add_argument('--link-event', nargs=2, metavar=('LEFT','RIGHT'), help='核验后关联同一事件或论文关系，不合并论文身份')
    parser.add_argument('--relation', default='duplicate', help='duplicate/commentary/contrast/extends/compares/complements')
    parser.add_argument('--evidence-url', help='事件关联核验依据，HTTPS')
    parser.add_argument('--feedback', nargs=2, metavar=('ID','ACTION'), help='记录 liked/disliked/read 明确反馈')
    parser.add_argument('--reason', default='', help='反馈原因')
    parser.add_argument('--no-notify', action='store_true', help='发布网页而不发送微信')
    parser.add_argument('--resolve-delivery', nargs=2, metavar=('DAY','DECISION'), help='人工核对发送记录后标记 sent/retry，必须说明依据')
    args = parser.parse_args(argv)
    now = datetime.now(TZ)
    from . import pipeline
    try:
        if args.evaluate:
            from .evaluation import replay,archived
            result=replay(ROOT,json.loads(args.input.read_text()),now) if args.input else archived(ROOT,now)
        elif args.profile:
            from .preferences import profile
            with pipeline.connection(ROOT) as conn:result=profile(conn,now)
        elif args.preference:
            from .preferences import set_preference
            result=set_preference(ROOT,*args.preference,args.reason,now,args.ttl_days)
        elif args.link_event:
            from .events import link
            result=link(ROOT,*args.link_event,args.relation,args.reason,args.evidence_url or '',now)
        elif args.trial:
            from .trial import run
            result=run(ROOT,args.trial,now,push=args.push,dry_run=args.dry_run,notify=not args.no_notify)
        elif args.resolve_trial:
            from .trial import resolve
            result=resolve(ROOT,*args.resolve_trial,args.reason,now)
        elif args.quality_check:
            from .quality import check
            if args.input:
                payload=json.loads(args.input.read_text());cards=payload.get('cards',payload.get('reviews',[]))
                if 'reviews' in payload and 'cards' not in payload:
                    enriched=[]
                    with pipeline.connection(ROOT) as conn:
                        for review in cards:
                            row=conn.execute('SELECT data FROM candidates WHERE id=?',(review.get('id'),)).fetchone()
                            if not row:raise ValueError('审核ID不存在：'+str(review.get('id')))
                            card=json.loads(row[0]);card.update({k:v for k,v in review.items() if k not in {'id','source_id','kind','url','discovery_sources'}})
                            card['published_label']=pipeline.content_date(card)
                            enriched.append(card)
                    cards=enriched
            else:
                with pipeline.connection(ROOT) as conn:
                    cards=[json.loads(r[0]) for r in conn.execute("SELECT data FROM candidates WHERE state='reviewed'")]
            result=check(cards,ROOT)
        elif args.collect:
            from .adapters.public import collect
            result=collect(ROOT,now,only=args.source)
        elif args.review_queue:
            result=pipeline.review_queue(ROOT,now)
        elif args.compose:
            result=pipeline.compose(ROOT,now,dry_run=args.dry_run)
            if args.push and not args.dry_run and result['status']!='failed' and result['selected_items'] and not result.get('frozen'):
                from .site import publish_pending
                result.update(publish_pending(ROOT,now,day=result['day'],notify=not args.no_notify))
        elif args.health:
            result=pipeline.health(ROOT,now)
        elif args.feedback:
            result=pipeline.feedback(ROOT,*args.feedback,args.reason,now)
        elif args.resolve_delivery:
            result=pipeline.resolve_delivery(ROOT,*args.resolve_delivery,args.reason,now)
        elif args.plan:
            result = plan(ROOT, now)
        elif args.publish:
            from .site import publish_pending
            result = publish_pending(ROOT, now, day=args.publish,notify=not args.no_notify)
        elif args.build_site:
            from .site import build_site
            result = {'files': build_site(ROOT)}
        elif args.status:
            path = ROOT / 'state/tech/last_run.json'
            if not path.exists():
                raise ValueError('尚未完成采集；先运行 --plan')
            result = json.loads(path.read_text(encoding='utf-8'))
        elif args.push and not args.input:
            result = push_pending(ROOT, now)
        else:
            path = args.input or ROOT / 'state/tech/collection.json'
            if not path.exists():
                raise ValueError('没有浏览器采集批次；按 TECH_DIGEST.md 采集后用 --input 导入')
            result = ingest(ROOT, json.loads(path.read_text(encoding='utf-8')), now, save_local=args.save_local)
            if args.push:
                if pipeline.enabled(ROOT): result.update(pipeline.compose(ROOT,now))
                if result.get('status')=='failed' or result.get('selected_items')==0:
                    raise ValueError('采集失败或没有合格精选，不推送空日报')
                if pipeline.enabled(ROOT):
                    if not result.get('frozen'):
                        from .site import publish_pending
                        result.update(publish_pending(ROOT,now,day=result['day'],notify=not args.no_notify))
                else:result.update(push_pending(ROOT, now))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return {'partial': 2, 'failed': 1}.get(result.get('status'), 0)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print('技术日报失败：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
