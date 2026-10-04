# AI 技术日报运行手册

## 运行方式与成本

Semantic Scholar API Key 获批后，在本机终端运行 `.venv/bin/python -m tech_digest.credentials`，按提示粘贴（输入隐藏），保存到 macOS 钥匙串。每日采集会自动读取；`DAILY_DIGEST_S2_API_KEY` 环境变量优先。不要把 Key 放进公开配置或聊天。未配置 Key 时保留匿名请求；保存成功不代表接口验证通过。

健康检查的 `stale_sources` 按每日、三组轮换或每周两次的频率列出逾期来源；当天 12:30 前留有执行宽限，浏览器来源按实际内容检查时间判断，身份检查不能替代。来源指标分别提供 `batch_metrics`（最后采集批次）、`daily_metrics`（当天）和 `cumulative_metrics`（累计）。发现／合并数按采集条目计，候选／核验数按去重身份计；当天核验须有当天真实审核记录，历史核验不回填。累计入选数为已确认发送的候选数，当天入选数为本次精选数；空统计的日期完整率为 null。旧采集批次缺少候选 ID 时，不推断其批次候选或核验数。

每天北京时间 10:30 由现有 Codex 自动任务启动，工作目录 `/Users/winters/Desktop/daily-digest`。Python 采集公开 API、RSS 和目录；当前 Codex 模型通过正常浏览器补充登录来源、读原文、生成原创中文审核卡片。继续使用现有 Codex 额度，不调用付费摘要、X 抓取或转录服务，不导出 Cookie。本机需在线，Codex 和 X 会话需有效；关机、休眠或额度不足会影响执行。GitHub 负责归档和 Pages 托管，不负责登录采集。

日报目标 10 篇论文、最多 10 条资讯。论文按已录用会议／正式期刊、arXiv 前沿、Semantic Scholar 推荐顺序分区，目标为 5/3/2，跨区共享额度，不足从剩余合格候选补位。预印本明确标注未同行评审，发现渠道与发表状态独立。五个主题等权，候选充足时至少覆盖四类，单类最多三篇；资讯按研究动态、工程实践、开源与工具、深度解读组织，每作者最多两条，同一事件只展示一次，Product Hunt 最多一条。质量不足时少推并说明。

标题、正文使用自托管中文衬线体；顶部显示日期、标题、目录与重点入口。采集说明放在末尾折叠区，不添加“北京时间／优质内容”开头。

## 每日完整流程

1. 执行 `--health` 检查发送待核对、来源连续失败和历史补查进度，再执行 `--plan` 获取本次来源与时间窗口。
2. 执行 `--collect`。原始内容进入 SQLite 待审核池，采集批次为 `state/tech/public_collection.json`，浏览器任务为 `state/tech/browser_tasks.json`。每个来源完成即入库并保存 state/tech/checkpoints/ 恢复批次，进程中断可继续未完成来源；一个来源异常不会撤销其他来源的成果。公开接口限流遵守 Retry-After，每次最多三次请求；长等待保存下次可重试时间，不阻塞全部来源。返回非零不代表合格候选全部丢失。
3. 通过正常 Codex 浏览器处理浏览器任务与官方核验。X 只读配置内固定作者的个人页、原帖、作者站内搜索，不读首页推荐或社区。新账号 `identity_status: pending` 必须取得可信身份链接后才允许入选；机构账号单独标注。原 10 位每日检查，新增 30 位分三组轮换。首次检查全部账号不受轮换限制。Reddit 仅限配置的五个版块。
4. 每个来源提交真实状态 `ok/blocked/error/needs_browser`、`note`、`evidence_urls`、`coverage: window_checked/sample/index_only/unknown` 和 `window_months: 2`。读取目录不是全文阅读，抽样不是全量覆盖。X 身份检查填 phase: identity，实际帖子检查填 phase: content；只核验身份不能跳过当天内容采集。浏览器补查未完成时保存 `backfill_cursor`；只有实际检查完整窗口才能设置 `backfill_complete: true`。
5. 执行 `--review-queue`，每次最多 50 篇论文、40 条资讯，经主题 BM25 初筛；论文审核队列也分配主题配额，保留少量探索项，显示 pending_reason。打开原文核验，完成结构化评分，不把元数据、热度或关键词匹配视为审核完成。重复执行可继续处理剩余队列。
6. 保存当前时间的审核 JSON，运行 `--input` 导入。逐条检查 `rejected`，缺证据留在待审核池，不修改日期强行入选。运行 `--compose --dry-run` 查看精选并逐条核验。
7. 正式执行 `--compose --push`：生成日报、提交公开 HTML/CSS/字体到唯一仓库 winters1900/daily_digest，确认 Pages 版本上线后才通过邮件模块的 Server酱发微信阅读链接。正常完成保持安静，只有故障或需处理时通知。
8. 发送后当日集合冻结，后补候选留次日。页面未就绪可重试 `--publish YYYY-MM-DD`。发送超时、进程中断或结果不明确记录为待核对，禁止盲目重发；核对 Server酱发送记录后用 `--resolve-delivery YYYY-MM-DD sent/retry --reason 核对依据` 明确结论。

所有来源失败时保留上一份有效日报。部分失败仍可发布合格精选，来源缺口明确显示；没有合格内容不推空日报。Server酱接受请求不代表用户已阅读。

## 审核 JSON

```json
{
  "observed_at": "带时区的当前真实时间",
  "sources": [],
  "items": [],
  "reviews": [{
    "id": "--review-queue 中的候选 ID",
    "title": "中文标题",
    "review_version": 4,
    "topic": "language",
    "subtopic": "reasoning",
    "problem": "研究要解决的问题",
    "contribution": "区别于已有工作的贡献",
    "results": "已核验的主要结论；数值必须有证据",
    "conditions": "实验或适用条件；摘要未提供则明确未知",
    "reading_advice": "建议接下来阅读的章节或核验动作",
    "claims": [{"id": "claim-1", "text": "已核验结论", "url": "https://原文", "locator": "Abstract 或具体章节/图表号", "excerpt": "支持该结论的短原文证据"}],
    "paper_type": "method",
    "research_tags": ["test time compute"],
    "type_details": {"mechanism": "方法机制；摘要未说明则明确未知", "baselines": "已核验基线或明确未知", "ablations": "消融证据或明确未知"},
    "findings": [{"statement": "已核验结论", "metric": "结论对应的指标", "conditions": "具体适用条件或摘要未提供的边界", "comparison": "比较对象或明确未提供", "claim_ids": ["claim-1"], "verdict": "supported", "support_reason": "原文定位支持该结论的具体理由"}],
    "score_reasons": {"relevance": "主题关联依据", "evidence": "实际阅读范围", "novelty": "新增信息依据", "recency": "真实首次日期", "reproducibility": "可得实现或实验说明"},
    "summary": "问题、贡献、结果及实验条件；重要数值附可定位依据",
    "why": "阅读价值",
    "limitations": "方法局限、阅读边界",
    "action": "可选的具体实践建议",
    "reading_depth": "摘要",
    "evidence_excerpt": "短证据摘录或定位说明",
    "review_evidence": ["https://原文"],
    "date_evidence_url": "https://日期依据",
    "quality_scores": {"relevance": 4, "evidence": 3, "novelty": 4, "recency": 4, "reproducibility": 3},
    "review_status": "preprint",
    "first_public_date": "YYYY-MM-DD"
  }]
}
```

五项评分均为 0–5：相关性30%、证据质量25%、新增信息20%、时效15%、代码／数据／可复现性10%；加权换算为100分，65分门槛，热度只在同分时排序。评分必须基于读到的证据，时效依据真实事件日期。主题为 language/vision/multimodal/reinforcement/systems；阅读深度为摘要/正文/帖子/字幕/复现，未执行实验不得标记复现。

论文 `review_status` 为 accepted/published/preprint。录用或发表须提供官方 `verification_url`、配置内 `venue` 和 `track: main/journal/findings/workshop/demo`，日期为 `accepted_date/published_date`，保留 `first_public_date/updated_date`。arXiv 须有公开摘要和身份 ID。资讯须有 `published_date`、`news_section`、`event_id`；X 新作者另附 `identity_verified` 与 `identity_evidence_url`；视频必须有可读 `transcript_url/transcript_excerpt`，仅标题不能生成摘要；GitHub 项目必须有 `technical_change/change_evidence_url`，提交时间与星数不能冒充发布。

日期限定最近两个自然月，包含起始日，月末取目标月最后一天。例如 2026-10-04 对应 2026-08-04 至 2026-10-04。arXiv 普通更新、HF 上榜和转载不刷新首次公开日期；旧论文近期录用／发表标为发表动态。Semantic Scholar 与 HF 元数据回到官方论文核验日期，不自动视作录用。仅年份、日期缺失、未来日期不得入选。

## 来源、身份与反馈

公开配置：`digest_sources.yaml`、`paper_sources.yaml`、`x_accounts.yaml`、`research_seeds.yaml`。arXiv 八类、Semantic Scholar、HF Daily Papers、AI 官方来源、HN、Reddit、六个 YouTube 频道、GitHub Trending、Product Hunt 已有适配器；会刊官方目录增加 CVPR、ECCV。适配器能发现候选不代表已完成原文核验。免费接口不可用时保留来源缺口；YouTube 无字幕时只保留线索。

arXiv 历史快照按提交时间升序分页保存 offset，同时读取最新页；后续新增内容独立建立带两天重叠的增量区间，分页结果超过100条时继续读取并保存未完成队列，直到覆盖完整区间。默认每次最多读取1个最新页、10个历史页和20个增量页，每页100条，每个请求至少间隔三秒；此预算用于避免两个月约数万条候选的补查长时间落后，接口限流时以实际游标为准。后页失败保留前页候选、游标及限流时间，不丢弃全部结果。其他 RSS 通常只提供有限最新条目，历史不足由浏览器任务按时间区间继续补查，不能声称已检查全窗口。各源独立保存检查时间、覆盖、游标、故障和每次发现／合并／核验／入选指标。

DOI、去版本 arXiv ID、OpenReview ID、Semantic Scholar ID 进行身份合并；桥接合并保留已审核卡片、反馈、旧候选 ID 与相关资讯的主题引用。标题相似只生成待核验建议，不能擅自合并。博主解读、新闻与论文关联同一主题，避免重复占额度；同一事件即使改用不同主题 ID 或换来源，也不会跨日重新推荐。旧数字论文来源 ID 自动映射稳定 ID；旧采集批次缺少新证据或评分进入待审核，历史日报不重新排序。

种子按五领域各两篇，官方身份已核验，标为系统配置，可早于窗口；推荐结果仍必须满足窗口。参考项目仅借鉴设计，未复制其源码。喜欢／不感兴趣／已读支持聊天或 CLI：

```sh
.venv/bin/python main.py --feedback arxiv:2609.12345 liked --reason '关注这个方法'
.venv/bin/python main.py --feedback 候选ID disliked --reason '与方向无关'
.venv/bin/python main.py --feedback 候选ID read
```

聊天中明确反馈须由 Codex 找到对应候选并调用上述命令。仅喜欢和不感兴趣影响主题排序与推荐种子，已读不算负反馈，未点击也不算负反馈。首版无网页反馈服务器。

## 命令与交付

```sh
.venv/bin/python main.py --plan
.venv/bin/python main.py --collect
.venv/bin/python main.py --collect --source discovery:arxiv
.venv/bin/python main.py --review-queue
.venv/bin/python main.py --input state/tech/reviews.json
.venv/bin/python main.py --compose --dry-run
.venv/bin/python main.py --compose --push
.venv/bin/python main.py --compose
.venv/bin/python main.py --publish YYYY-MM-DD --no-notify
.venv/bin/python main.py --publish YYYY-MM-DD
.venv/bin/python main.py --health
.venv/bin/python main.py --status
.venv/bin/python -m unittest discover -s tests
```

`--no-notify` 用于发布演练，不发送微信；`--dry-run` 不生成发送队列。退出0正常、2部分来源故障、1失败或输入错误。--collect --source 可主动复查已检查来源，但不能提前绕过 Retry-After；未知来源 ID 明确拒绝。--health 分别展示采集缺口、补查队列、发布阶段和微信发送状态；默认10:30启动后留120分钟宽限，超过12:30仍未确认当日发送会报告漏发风险，宽限可在 runtime 中配置。周六可将真实全文精读写入 `weekly_review` 合并当日页面，无法取得正文须跳过并说明。

公开仓库只保存代码、公开配置、`docs/YYYY-MM-DD.html`、`index.html`、`latest.html` 和资源。候选、原始内容、反馈、邮件数据与凭据留在忽略的 state 或钥匙串；不得 `git add .`。网页为 https://winters1900.github.io/daily_digest/ 。旧四来源程序保留 `main.py --legacy`，邮件模块独立每天10:00，不受技术日报改动影响。

## 图文质量与第四版审核

日常精选现要求 `review_version: 4`。旧采集和历史归档仍可读取；未发送的旧审核会重新进入审核队列，补证后才参加新日报。摘要的 evidence 最高3/5；reproducibility≥4须提供 `reproducibility_evidence_url/reproducibility_note`，展示代码须提供 `code_evidence_url`。录用/发表论文另填 `publication_evidence: {url,locator,excerpt,event_date,venue,track}`，必须对应官方论文详情或最终决定，与日期、会刊、轨道一致；主页和索引不得替代。

每条填 `problem/contribution/results/conditions/reading_advice/subtopic/score_reasons`。`claims` 是 `{id,text,url,locator,excerpt}` 数组，重要数值须与原文段落、表格或图号相符；自动检查不能代替模型阅读。定位不能用通用说明，重复摘录或三篇以上相同评分向量需要复核。

`subtopic` 使用 quality.py 的统一标签；视频跨主方向统一归入 video。同一细分方向最多三篇。每天2–3篇重点论文填 `focus: true`，阅读深度为关键章节/正文/复现，并填 `read_sections: {method,experiments,limitations}`。作者没有独立局限章节时，记录实际读过的实验边界和自身判断，不能编造章节。只读摘要不能标重点。

重点论文可填一张 `figure`，每期最多三张：
- 通用字段：kind(original/redraw/link)、source_url、figure_number、version、caption、alt。
- 原图：image_url、license(CC0/CC-BY/CC-BY-SA)、license_url、attribution、license_evidence、third_party_check。核实当前版本许可及第三方材料，图注保留署名和许可，格式转换要声明。
- 重绘：data([{label,value}])、data_evidence([{url,locator,excerpt}])、conditions、unit、chart_title。每个数值必须对应原文；只比较一致条件，注明重绘、未经复现，不编造数据。

下载只允许公开官方白名单HTTPS主机，不导出登录凭据；重定向逐跳检查，限流遵守Retry-After，最多三次。资源转换为不携带元数据的WebP并记录哈希、尺寸；单张目标800KB。下载或许可失败降级文字卡片，保留诊断。网页使用独立digest-v3.css，旧日报页面保持冻结。图片须和HTML版本一起确认上线，才能发微信。

```sh
.venv/bin/python main.py --quality-check --input state/tech/reviews-v4.json
.venv/bin/python main.py --trial state/tech/quality-trial-YYYY-MM-DD-HHMMSS.json --dry-run
.venv/bin/python main.py --trial state/tech/quality-trial-YYYY-MM-DD-HHMMSS.json
.venv/bin/python main.py --trial state/tech/quality-trial-YYYY-MM-DD-HHMMSS.json --push
.venv/bin/python main.py --resolve-trial quality-trial-YYYY-MM-DD-HHMMSS sent --reason '核对通道记录依据'
```

独立试刊JSON使用 `{id,day,cards,sources,shortfalls}`；cards填写完整候选身份和新版审核字段，允许重审旧日报，不改变正常候选和历史推荐。试刊状态位于state/tech/trials；sending/uncertain必须人工核对后sent/retry，sent重复执行不重发。试刊在归档单列，latest始终指正式日报。--health增加重点精读数、有效图数、证据完整率、正式发表数、模板审核提醒及试刊投递状态。

官方会刊优先按PMLR精确ICML卷、CVF主会论文详情、ECVA论文页和TMLR官方名单分层发现。OpenReview API v2按官方 venueid 获取录用候选，并记录 pdate 或官方最终决定事件日期；投稿日期、会议召开日期不代替录用日期。API限流或无效响应时保留真实故障，并降级官方目录发现；最终决定与精确日期继续由正常浏览器核验；没有日期或只见投稿页不能录用入选。来源游标记录待查详情，抽样不会冒充完整窗口。

## 参考项目复核（2026-10-05）

本次阅读参考项目当前说明与关键实现，并对照本地代码；以下设计已完成本地实现及演练；API与浏览器来源仍按实际访问结果报告覆盖，不能把适配器实现视为来源已完成核验。

| 项目 | 尚可借鉴的能力 | 本系统的取舍 |
|---|---|---|
| [DailyPaper](https://github.com/LucaJiang/DailyPaper) | 正负种子推荐、先召回再补充摘要 | 推荐与种子已有；继续核验原文，不采用年份替代精确日期 |
| [arxiv-daily-plus](https://github.com/sujin-koo/arxiv-daily-plus) | 自然语言兴趣描述、BM25/DPR/SPLADE 初筛 | BM25 已有；先补方法词与同义词召回，语义模型须经本地效果评估后再引入 |
| [paper-claw](https://github.com/Jian-Lang/personalized-research-paper-claw) | 区分方法、Benchmark、混合论文的笔记模板；按领域组织关联论文 | 优先增加论文类型审核要求，保持每篇最多一图；会议快照只能作发现入口，其配额回溯不能代替日期窗口 |
| [PaperFlow](https://github.com/OpenRaiser/PaperFlow) | 长短期兴趣、可回放评估、研究 Wiki | 借鉴显式偏好与评估；不采用未选择论文的弱负反馈。模拟用户标签不能当作本用户真实偏好 |
| [ai-news-brief](https://github.com/frankzch/ai-news-brief) | SimHash 与向量相似去重；讨论上下文 | 增加同一事件的近重复建议与模型核验，保留一手来源和有价值的不同意见；不用热度替代证据 |
| [ai-daily-digest](https://github.com/AustinWp/ai-daily-digest) | 今日看点、推荐理由、来源与分类统计 | 可增加仅依据入选内容的短导读；不能把单日样本称为行业趋势，不采用评分失败后的默认分进入精选 |

本次已增加会刊日期证据队列、类型审核、事件关联、离线回放、细分显式偏好、主题归档和依据重点内容生成的短导读。官方核验覆盖仍需持续补查；DPR/SPLADE语义模型尚未引入。评估使用真实历史候选、冻结入选集合和人工核验样例，分别衡量事实支持、条件完整、重复、覆盖与阅读价值；不以文章数量或结构完整代替内容质量。

现有 `claim_evidence_completeness` 只统计证据 URL、定位与摘录是否齐全，不证明结论正确。数值核验现采用完整数字匹配，区分正负号与百分数，兼容千分位、科学记数及等值精度；仍须人工检查对应指标、基线、数据集与实验条件，不能因不同表格碰巧出现相同数字就视为结论已核实。


## 第四版审核、关联与离线评估

论文填写 `paper_type`，可选 method、benchmark、dataset、survey、theory、systems、benchmark-method。`--review-queue` 返回各类型的 `type_details` 必填字段；重点还须填写 `type_evidence_note`，记录实际核验位置。摘要未提供的信息明确写未知，不伪造基线、消融、污染检查或硬件条件。

每条 `findings` 都对应唯一的 `claims.id`，记录指标、实验条件、比较对象及支持理由。重要数字只能引用该 finding 对应的摘录，不能借用其他表格的相同数字；results 中的数字必须由 findings 覆盖。结构检查仍不能证明原文真实支持结论，模型须逐条阅读核验。历史第三版页面保持冻结，只有再次参与新一期选取的旧卡片需要补审。

近重复候选以 `event_suggestions` 提供，不自动合并。原文核验后使用 `--link-event`；duplicate/commentary/contrast 聚合同一事件，论文之间只允许 extends/compares/complements，不合并论文身份。不同意见保留为关联阅读，已确认事件跨日去重。关系和反馈保存在本地，公开页面仅展示已审核的阅读链接及必要说明。

```sh
.venv/bin/python main.py --link-event 候选A 候选B --relation commentary --reason '已核验为同一发布的解读' --evidence-url https://原文
.venv/bin/python main.py --preference 'test time compute' 3 --reason '明确关注' --ttl-days 30
.venv/bin/python main.py --preference 'test time compute' 0 --reason '撤销该关键词偏好'
.venv/bin/python main.py --profile
.venv/bin/python main.py --evaluate
.venv/bin/python main.py --evaluate --input state/tech/labeled-replay.json
```

显式关键词权重为 -5 至5，不设置期限时长期保留，0取消该词的偏好影响。只有喜欢／不感兴趣产生细分标签偏好；同一候选重复反馈不叠加，短期分量按30天半衰期衰减，已读与未点击保持中性。偏好排序调整最多正负5分，不绕过65分质量门槛或主题配额，私人关键词和反馈原因不写入公开日报。

离线评估输入为 `{episodes:[{id,at,candidates,relevance_labels,content_labels}]}`。candidates 是完整卡片，相关性标签为池内 ID 对应的0–3分；内容标签为 `{候选ID:{supported:true,conditions_complete:true,reading_value:true}}`。仅人工逐条核验的布尔标签可作为事实支持／条件完整／阅读价值指标；完整候选池均有相关性标注才报告 Precision/NDCG，未标注不视为不相关。无标注历史回放只报告结构诊断，不改候选、偏好、发送或推荐历史。

`docs/themes.html` 仅从已发送日报的冻结集合建立五方向主题归档，不读取未入选池和私人配置。日报短导读仅依据重点论文的问题与贡献生成，不能把单日样本称为行业趋势。新增试刊通过 `--trial ... --push --no-notify` 验证上线，不替换 latest、不消耗正常候选、不额外发送微信。
