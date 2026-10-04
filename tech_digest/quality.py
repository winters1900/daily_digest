"""新版审核契约和质量诊断；不根据文字长度冒充原文阅读。"""
from collections import Counter
import re

SUBTOPICS = {'agents','reasoning','language_learning','video','image_generation','image_understanding',
             'audio_speech','multimodal_representation','robotics','rl_optimization','inference_serving',
             'training_optimization','retrieval','evaluation','data_methods','other'}
SCORES = ('relevance','evidence','novelty','recency','reproducibility')


def numeric_tokens(text):
    """按完整数值匹配证据，避免27.54匹配到127.54或27.549。"""
    from decimal import Decimal
    tokens=set()
    pattern=r'[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?:%|×)?'
    for match in re.finditer(pattern,text):
        raw=match.group();suffix=raw[-1] if raw[-1] in '%×' else ''
        number=raw[:-1] if suffix else raw
        tokens.add((Decimal(number.replace(',','')),suffix))
    return tokens


def required(obj, key):
    if not isinstance(obj.get(key), str) or not obj[key].strip():
        raise ValueError('新版审核缺少 ' + key)


def validate(card):
    from .__main__ import https_url
    if card.get('review_version', 1) < 3:
        raise ValueError('旧审核须补充新版结构与证据后再参与新日报')
    for key in ('problem','contribution','results','conditions','reading_advice'):
        required(card,key)
    if card.get('subtopic') not in SUBTOPICS:raise ValueError('细分主题无效')
    reasons=card.get('score_reasons',{})
    for key in SCORES:required(reasons,key)
    evidence=card.get('claims',[])
    if not isinstance(evidence,list):raise ValueError('claims 必须为数组')
    for claim in evidence:
        for key in ('text','locator','excerpt','url'):required(claim,key)
        https_url(claim['url'])
    numbers=numeric_tokens(card['results'])
    support=' '.join(c['excerpt'] for c in evidence)
    if not numbers.issubset(numeric_tokens(support)):raise ValueError('重要数值缺少对应可定位证据')
    if not evidence:raise ValueError('结论缺少可定位证据')
    if card['reading_depth']=='摘要' and card['quality_scores']['evidence']>3:
        raise ValueError('摘要阅读的证据评分不得超过3/5')
    if card['quality_scores']['reproducibility']>=4:
        required(card,'reproducibility_evidence_url');https_url(card['reproducibility_evidence_url'])
        required(card,'reproducibility_note')
    if card.get('code_url'):
        required(card,'code_evidence_url');https_url(card['code_evidence_url'])
    if card.get('focus'):
        if card['kind']!='paper' or card['reading_depth'] not in {'关键章节','正文','复现'}:
            raise ValueError('重点论文须实际阅读关键章节')
        sections=card.get('read_sections',{})
        for key in ('method','experiments','limitations'):required(sections,key)
    if card.get('review_status') in {'accepted','published'}:
        from urllib.parse import urlparse
        publication=card.get('publication_evidence',{})
        for key in ('url','locator','excerpt','event_date','venue','track'):required(publication,key)
        if publication['url']!=card.get('verification_url') or publication['venue']!=card.get('venue') or publication['track']!=card.get('track'):
            raise ValueError('录用/发表证据与会刊、轨道不一致')
        if publication['event_date']!=card.get('published_label'):
            raise ValueError('录用/发表事件日期与原文证据不一致')
        parsed=urlparse(publication['url'])
        if not re.search(r'/forum\?id=|/v\d+/[^/]+\.html|/papers/(?:v\d+/|volume\d+/)|/hash/.+\.html|_paper\.(?:html|php)|/articles/|aclanthology\.org/\d{4}\.[^/]+/|/conference/[^/]+/presentation/|/\d{4}/[^/]+\.html',publication['url']):
            raise ValueError('会议主页或索引不能作为论文录用/发表证据')
        https_url(publication['url'])
    figure=card.get('figure')
    if figure:
        from .media import validate_descriptor
        validate_descriptor(figure)
    return card


def check(cards, root=None):
    errors=[];warnings=[]
    vectors=Counter(tuple(c.get('quality_scores',{}).get(k) for k in SCORES) for c in cards)
    excerpts=Counter(c.get('evidence_excerpt','').strip() for c in cards)
    for card in cards:
        try:
            validate(card)
            if root and card.get('figure',{}).get('asset'):
                from .media import asset_bytes
                asset_bytes(root,card['figure'])
        except (ValueError,TypeError,KeyError,OSError) as exc:
            errors.append({'id':card.get('id'),'reason':str(exc)})
        if vectors[tuple(card.get('quality_scores',{}).get(k) for k in SCORES)]>=3:
            warnings.append({'id':card.get('id'),'reason':'三篇以上评分向量相同，请复核各项依据'})
        if card.get('evidence_excerpt') and excerpts[card['evidence_excerpt'].strip()]>=2:
            warnings.append({'id':card.get('id'),'reason':'证据摘录重复，请核对原文定位'})
        if card.get('media_warning'):warnings.append({'id':card.get('id'),'reason':card['media_warning']})
    focus=[c for c in cards if c.get('focus')]
    if len(focus)<2:warnings.append({'reason':'重点精读不足两篇，按实际阅读深度展示'})
    if len(focus)>3:errors.append({'reason':'重点论文最多三篇'})
    if len(focus)>=2 and len({c['topic'] for c in focus})<2:
        warnings.append({'reason':'重点论文集中于一个方向'})
    sub=Counter(c.get('subtopic') for c in cards if c['kind']=='paper')
    if any(v>3 for v in sub.values()):errors.append({'reason':'同一细分方向超过三篇'})
    if sum(bool(c.get('figure',{}).get('asset')) for c in cards)>3:
        errors.append({'reason':'配图超过三张'})
    return {'status':'failed' if errors else 'ok','errors':errors,'warnings':warnings,
            'metrics':metrics(cards),'review_version':3}


def metrics(cards):
    claims=[v for c in cards for v in c.get('claims',[])]
    return {'focus_papers':sum(c['kind']=='paper' and bool(c.get('focus')) for c in cards),
            'figures':sum(bool(c.get('figure',{}).get('asset')) for c in cards),
            'peer_reviewed':sum(c.get('review_status') in {'accepted','published'} for c in cards),
            'claim_evidence_completeness':sum(bool(v.get('url') and v.get('locator') and v.get('excerpt')) for v in claims)/len(claims) if claims else None,
            'structured_reviews':sum(c.get('review_version',1)>=3 for c in cards)}


def focus_cards(cards):
    pool=sorted((c for c in cards if c['kind']=='paper' and c.get('focus')),
                key=lambda c:(-c.get('ranking_score',0),c['id']))
    picked=[]
    for c in pool:
        if c['topic'] not in {p['topic'] for p in picked}:picked.append(c)
        if len(picked)==3:return picked
    return (picked+[c for c in pool if c not in picked])[:3]
