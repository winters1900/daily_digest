"""本地显式兴趣；已读、未点击与系统配置不产生偏好。"""
import json
import re
from datetime import timedelta


def terms(card):
    return set(t.strip().casefold() for t in card.get('research_tags',[]) if isinstance(t,str) and t.strip())


def matches(term,text):
    term=term.casefold().strip();text=text.casefold()
    return bool(re.search(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',text)) if re.search('[a-z]',term) else term in text


def set_preference(root,term,weight,reason,now,ttl_days=None):
    from .pipeline import connection
    if not isinstance(term,str) or not 2<=len(term.strip())<=80 or not reason.strip():raise ValueError('偏好需要明确关键词和原因')
    weight=float(weight)
    if not -5<=weight<=5:raise ValueError('偏好权重须为 -5 至 5')
    if ttl_days is not None and not 1<=ttl_days<=365:raise ValueError('临时偏好期限须为1至365天')
    record=dict(term=term.strip().casefold(),weight=weight,reason=reason,created_at=now.isoformat(),
                expires_at=(now+timedelta(days=ttl_days)).isoformat() if ttl_days else None,origin='explicit_user')
    with connection(root) as conn:
        conn.execute('INSERT OR REPLACE INTO pipeline_meta VALUES (?,?)',('preference:'+record['term'],json.dumps(record,ensure_ascii=False)))
    return record


def profile(conn,now):
    from .__main__ import timestamp
    manual=[];fine={};topic={}
    for raw, in conn.execute("SELECT value FROM pipeline_meta WHERE key LIKE 'preference:%' ORDER BY key"):
        entry=json.loads(raw)
        if entry.get('expires_at') and timestamp(entry['expires_at'])<=now:continue
        manual.append(entry)
    # 最后一次明确反馈生效；read不覆盖先前喜欢/不感兴趣。
    latest={}
    for uid,action,reason,at in conn.execute("SELECT candidate_id,action,reason,observed_at FROM feedback WHERE action!='read' ORDER BY id"):
        latest[uid]=(action,reason,at)
    for uid,(action,reason,at) in latest.items():
        row=conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()
        if not row:continue
        card=json.loads(row[0]);sign=1 if action=='liked' else -1
        topic[card.get('topic')]=topic.get(card.get('topic'),0)+sign
        age=max(0,(now-timestamp(at)).total_seconds()/86400)
        # 长期小幅信号 + 半衰期30天的近期信号。只有明确反馈才能生成。
        value=sign*(.25+.75*2**(-age/30))
        for tag in terms(card):fine[tag]=fine.get(tag,0)+value
    return {'manual':manual,'fine_weights':fine,'topic_weights':topic,'policy':'五方向保留覆盖约束；明确反馈影响排序，已读与未点击不影响兴趣'}


def adjustment(card,profile):
    text=' '.join(str(card.get(k,'')) for k in ('title','abstract','problem','contribution'))
    tags=terms(card);signals=[]
    overridden={e['term'] for e in profile['manual']}
    for entry in profile['manual']:
        if entry['term'] in tags or matches(entry['term'],text):signals.append((entry['term'],entry['weight']))
    for term,weight in profile['fine_weights'].items():
        if term not in overridden and (term in tags or matches(term,text)):signals.append((term,weight))
    if signals:return max(-5,min(5,sum(v for _,v in signals))),[{'term':t,'weight':round(v,3)} for t,v in signals]
    value=profile['topic_weights'].get(card.get('topic'),0)
    return max(-5,min(5,value)),[]


def retrieval_query(profile):
    return ' '.join([e['term'] for e in profile['manual'] if e['weight']>0]+[t for t,w in profile['fine_weights'].items() if w>0])
