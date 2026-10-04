"""第四版审核：论文类型与逐条结论的实验语境。"""
from .quality import required, numeric_tokens

PAPER_TYPES = {
    'method': ('方法论文', ('mechanism','baselines','ablations')),
    'benchmark': ('评测论文', ('scope','construction','protocol','contamination','judge_bias')),
    'dataset': ('数据集论文', ('provenance','license','splits','leakage')),
    'survey': ('综述', ('search_scope','selection_criteria','coverage_gaps')),
    'theory': ('理论论文', ('assumptions','proof_scope','empirical_support')),
    'systems': ('系统论文', ('workload','hardware','comparison','cost')),
    'benchmark-method': ('评测与方法', ('scope','construction','protocol','contamination','judge_bias','mechanism','baselines','ablations')),
}
DETAIL_LABELS = dict(mechanism='方法机制',baselines='比较基线',ablations='消融与归因',scope='评测范围',
    construction='构建过程',protocol='评测协议',contamination='污染风险',judge_bias='评估器偏差',
    provenance='数据来源',license='数据许可',splits='数据划分',leakage='泄漏风险',search_scope='检索范围',
    selection_criteria='纳入标准',coverage_gaps='覆盖缺口',assumptions='理论假设',proof_scope='证明边界',
    empirical_support='实证支持',workload='工作负载',hardware='硬件与配置',comparison='比较公平性',cost='成本与代价')


def validate(card):
    if card.get('review_version',1)<4:return
    claims=card['claims'];by_id={}
    for claim in claims:
        required(claim,'id')
        if claim['id'] in by_id:raise ValueError('结论证据 ID 重复')
        by_id[claim['id']]=claim
    findings=card.get('findings')
    if not isinstance(findings,list) or not findings:raise ValueError('第四版审核缺少逐条结论 findings')
    covered=set()
    for finding in findings:
        for key in ('statement','metric','conditions','comparison','support_reason'):required(finding,key)
        refs=finding.get('claim_ids')
        if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in by_id for r in refs):
            raise ValueError('结论必须关联有效的证据 ID')
        if finding.get('verdict')!='supported':raise ValueError('缺证据或有争议的结论须留待审核')
        numbers=numeric_tokens(finding['statement'])
        evidence=numeric_tokens(' '.join(by_id[r]['excerpt'] for r in refs))
        if not numbers.issubset(evidence):raise ValueError('逐条结论数值与对应证据不一致')
        covered.update(numbers)
    if not numeric_tokens(card['results']).issubset(covered):raise ValueError('结果数值未关联逐条结论与实验条件')
    tags=card.get('research_tags',[])
    if not isinstance(tags,list) or len(tags)>8 or any(not isinstance(t,str) or not 2<=len(t.strip())<=80 for t in tags):
        raise ValueError('research_tags 最多八个明确的方法或任务标签')
    if card['kind']=='paper':
        paper_type=card.get('paper_type')
        if paper_type not in PAPER_TYPES:raise ValueError('缺少有效论文类型')
        details=card.get('type_details',{})
        for key in PAPER_TYPES[paper_type][1]:required(details,key)
        # 只读摘要可以指出未知；重点精读必须定位实际阅读的类型关键内容。
        if card.get('focus'):
            required(card,'type_evidence_note')


def instructions():
    return {'review_version':4,'paper_types':{k:{'label':v[0],'required_details':list(v[1])} for k,v in PAPER_TYPES.items()},
        'findings':'statement/metric/conditions/comparison/claim_ids/verdict:supported/support_reason；对应 claims 的唯一 id',
        'policy':'逐条阅读核对指标、基线、数据集、硬件及条件。support_reason 是模型阅读核验记录，不是程序证明；未知写明未报告。摘要不能填未读正文内容。',
        'research_tags':'最多八个方法或任务标签，用于检索、主题归档与明确反馈；不自动视为用户兴趣'}
