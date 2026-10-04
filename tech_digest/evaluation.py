"""离线回放；人工标签与结构诊断分开，不以模拟反馈冒充真实反馈。"""
import json
import math

from . import pipeline,quality


def replay(root,payload,now):
    from .__main__ import timestamp
    episodes=payload.get('episodes')
    if not isinstance(episodes,list) or not episodes:raise ValueError('评估需要非空 episodes')
    reports=[]
    for episode in episodes:
        when=timestamp(episode['at']) if episode.get('at') else now
        pool=episode.get('candidates',[])
        if not isinstance(pool,list) or len({c['id'] for c in pool})!=len(pool):raise ValueError('评估候选 ID 缺失或重复')
        eligible=[];rejected=[]
        for card in pool:
            card=dict(card)
            try:
                pipeline.validate_review(root,card,when)
                if card.get('review_version',1)<pipeline.config(root)['selection'].get('minimum_review_version',1):raise ValueError('审核版本过旧')
                score=20*sum(card['quality_scores'][k]*w for k,w in pipeline.config(root)['selection']['weights'].items())
                if score<pipeline.config(root)['selection']['minimum_score']:raise ValueError('质量低于门槛')
                card['ranking_score']=score;eligible.append(card)
            except (ValueError,TypeError,KeyError) as exc:rejected.append({'id':card.get('id'),'reason':str(exc)})
        selected=pipeline.choose(root,eligible);ids=[c['id'] for c in selected]
        grades=episode.get('relevance_labels',{})
        if not isinstance(grades,dict) or any(k not in {c['id'] for c in pool} or isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=3 for k,v in grades.items()):raise ValueError('相关性标签须为池内 ID 对应的0至3分')
        labels=episode.get('content_labels',{})
        if not isinstance(labels,dict):raise ValueError('content_labels 必须为对象')
        pool_ids={c['id'] for c in pool}
        metrics={'supported','conditions_complete','reading_value'}
        if any(uid not in pool_ids or not isinstance(values,dict) or
               any(key not in metrics or not isinstance(value,bool) for key,value in values.items())
               for uid,values in labels.items()):
            raise ValueError('内容标签须为池内 ID 对应的人工核验布尔值')
        known=[uid for uid in ids if uid in grades]
        def dcg(values):return sum((2**v-1)/math.log2(i+2) for i,v in enumerate(values))
        # 部分标注不报告完整precision或NDCG；未标注不是不相关。
        complete=bool(pool) and len(grades)==len(pool)
        ideal=dcg(sorted(grades.values(),reverse=True)[:len(ids)]) if complete else None
        content={}
        for metric in ('supported','conditions_complete','reading_value'):
            values=[labels[uid][metric] for uid in ids if isinstance(labels.get(uid),dict) and isinstance(labels[uid].get(metric),bool)]
            content[metric]={'rate':sum(values)/len(values) if values else None,'annotated':len(values),'selected':len(ids)}
        reports.append({'id':episode.get('id'),'selected':ids,'rejected':rejected,
            'precision':sum(grades[uid]>0 for uid in ids)/len(ids) if complete and ids else None,
            'ndcg':dcg([grades[uid] for uid in ids])/ideal if complete and ideal else None,
            'relevance_label_coverage':len(known)/len(ids) if ids else None,'full_pool_labeled':complete,
            'content_metrics':content,'topic_coverage':len({c.get('topic') for c in selected if c['kind']=='paper'}),
            'duplicate_groups':sum(bool(pipeline.dedup_keys(c)&set().union(*(pipeline.dedup_keys(other) for other in selected[:i]))) for i,c in enumerate(selected)),
            'quality':quality.check(selected,root)})
    return {'status':'ok','episodes':reports,'label_policy':'仅人工显式标签衡量阅读价值与事实支持；字段完整率不代表事实正确率。离线回放不改变候选、反馈或推荐历史。'}


def archived(root,now):
    from .__main__ import database
    with database(root) as conn:
        from .site import init_editions
        init_editions(conn)
        rows=conn.execute('SELECT day,data FROM web_editions WHERE sent_at IS NOT NULL ORDER BY day DESC LIMIT 14').fetchall()
    return {'status':'ok','archive_diagnostics':[{'day':day,'quality':quality.check(json.loads(raw)['cards'],root)} for day,raw in rows],
            'relevance_metrics':None,'note':'历史结构回放；尚未提供人工标签，不能评估真实推荐准确率。用 --evaluate --input 指定标注样例。'}
