"""近重复只建议；显式核验后聚合事件，论文身份保持独立。"""
import hashlib
import json
import math
from collections import Counter,defaultdict


def init(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS event_links (left_id TEXT,right_id TEXT,relation TEXT,reason TEXT,evidence_url TEXT,observed_at TEXT,PRIMARY KEY(left_id,right_id))')


def suggestions(cards,limit=40):
    from .pipeline import tokens
    docs=[];df=Counter();index=defaultdict(list);result=[]
    # 有界候选池；保留高分条目和近期发现，避免全库两两比较。
    for card in cards:
        counts=Counter(tokens(' '.join(str(card.get(k,'')) for k in ('title','abstract','raw_text','summary'))[:16000]))
        counts={t:min(n,3) for t,n in counts.items() if len(t)>2 or '\u4e00'<=t<='\u9fff'}
        docs.append(counts);df.update(counts)
    n=len(cards);vectors=[];norms=[]
    for doc in docs:
        vec={t:f*(math.log((n+1)/(df[t]+1))+1) for t,f in doc.items()};vectors.append(vec)
        norms.append(math.sqrt(sum(v*v for v in vec.values())))
    for i,card in enumerate(cards):
        candidates=Counter(j for t in docs[i] if df[t]<=max(3,n//2) for j in index[t])
        for j,_ in candidates.most_common(30):
            other=cards[j]
            if card['id']==other['id'] or card.get('source_id')==other.get('source_id'):continue
            if card['kind']==other['kind']=='paper':continue
            if card.get('story_id') and card.get('story_id')==other.get('story_id'):continue
            a=card.get('first_public_date') or card.get('published_date');b=other.get('first_public_date') or other.get('published_date')
            if a and b:
                from datetime import date
                try:
                    if abs((date.fromisoformat(a[:10])-date.fromisoformat(b[:10])).days)>14:continue
                except ValueError:continue
            cosine=sum(vectors[i][t]*vectors[j].get(t,0) for t in vectors[i])/(norms[i]*norms[j] or 1)
            if cosine>=.48:
                result.append(dict(left_id=other['id'],right_id=card['id'],similarity=round(cosine,3),
                    reason='内容近似，需核对是否同一发布、版本或独立观点；尚未聚合'))
        for term in docs[i]:index[term].append(i)
    return sorted(result,key=lambda r:(-r['similarity'],r['left_id'],r['right_id']))[:limit]


def link(root,left,right,relation,reason,url,now):
    from .pipeline import connection
    from .__main__ import https_url
    if relation not in {'duplicate','commentary','contrast','extends','compares','complements'} or not reason.strip():raise ValueError('关联须有明确关系与核验原因')
    https_url(url)
    with connection(root) as conn:
        init(conn);cards=[]
        for uid in (left,right):
            row=conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()
            if not row:
                alias=conn.execute('SELECT candidate_id FROM candidate_aliases WHERE alias=?',(uid,)).fetchone()
                row=conn.execute('SELECT data FROM candidates WHERE id=?',(alias[0],)).fetchone() if alias else None
            if not row:raise ValueError('未知关联候选')
            cards.append(json.loads(row[0]))
        if cards[0]['id']==cards[1]['id']:raise ValueError('不能关联同一身份')
        paper_pair=all(c['kind']=='paper' for c in cards)
        if paper_pair!=(relation in {'extends','compares','complements'}):raise ValueError('不同论文仅建立研究关系，不按同一事件合并')
        a,b=[c['id'] for c in cards] if relation=='extends' else sorted(c['id'] for c in cards)
        conn.execute('INSERT OR REPLACE INTO event_links VALUES (?,?,?,?,?,?)',(a,b,relation,reason,url,now.isoformat()))
        rebuild(conn)
    return {'left_id':a,'right_id':b,'relation':relation,'identity_merged':False}


def rebuild(conn):
    init(conn);parents={}
    def find(x):
        parents.setdefault(x,x)
        if parents[x]!=x:parents[x]=find(parents[x])
        return parents[x]
    for a,b,relation in conn.execute('SELECT left_id,right_id,relation FROM event_links'):
        if relation in {'duplicate','commentary','contrast'}:parents[find(b)]=find(a)
    groups=defaultdict(list)
    for uid in list(parents):groups[find(uid)].append(uid)
    for members in groups.values():
        story='story:'+hashlib.sha256('|'.join(sorted(members)).encode()).hexdigest()[:24]
        for uid in members:
            row=conn.execute('SELECT data FROM candidates WHERE id=?',(uid,)).fetchone()
            if row:
                card=json.loads(row[0]);card['story_id']=story
                conn.execute('UPDATE candidates SET data=? WHERE id=?',(json.dumps(card,ensure_ascii=False),uid))


def relations(conn,uid):
    init(conn)
    return [dict(other_id=b if a==uid else a,relation=r,reason=reason,evidence_url=url,direction='outgoing' if a==uid else 'incoming')
            for a,b,r,reason,url in conn.execute('SELECT left_id,right_id,relation,reason,evidence_url FROM event_links WHERE left_id=? OR right_id=?',(uid,uid))]
