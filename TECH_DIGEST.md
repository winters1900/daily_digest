# AI 技术日报运行手册

## 运行方式与成本

Semantic Scholar API Key 获批后，在本机终端运行 `.venv/bin/python -m tech_digest.credentials`，按提示粘贴（输入隐藏），保存到 macOS 钥匙串。每日采集会自动读取；`DAILY_DIGEST_S2_API_KEY` 环境变量优先。不要把 Key 放进公开配置或聊天。未配置 Key 时保留匿名请求；保存成功不代表接口验证通过。

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
    "topic": "language",
    "summary": "问题、贡献、结果及实验条件；重要数值附可定位依据",
    "why": "阅读价值",
    "limitations": "方法局限、阅读边界",
    "action": "可选的具体实践建议",
    "reading_depth": "摘要",
    "evidence_excerpt": "短证据摘录或定位说明",
    "review_evidence": ["https://原文"],
    "date_evidence_url": "https://日期依据",
    "quality_scores": {"relevance": 4, "evidence": 4, "novelty": 4, "recency": 4, "reproducibility": 3},
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
