# 技术知识日报运行手册

## 实际接入方式

`main.py` 默认进入新技术日报程序，原四来源版本保留为 `main.py --legacy`。新程序由 Codex 的浏览器和联网读取能力采集，Python 不连接 X 私有接口、不导出 Cookie、不调用付费摘要 API。采集与中文筛选由当前 Codex 模型执行，因此会消耗 Codex 账号额度。

每日北京时间 10:30，自动任务在此聊天执行以下完整流程，报告通过邮件模块相同的 Server酱通道发送到个人微信，复用系统钥匙串的 `serverchan_key`。失败或登录失效必须明确报告，不能写成“无更新”。

## 每次采集步骤

1. 工作目录 `/Users/winters/Desktop/daily-digest`，执行 `.venv/bin/python main.py --plan`。输出 `sources` 是本次应检查的全部来源：每日作者每次检查，每周作者首次检查后每七天检查。论文来源每日检查。
2. 根据 `x_accounts.yaml` 只访问计划中博主个人页。浏览器优先复用已登录 X 标签；旧标签不存在时新建标签验证登录。用正常页面操作获取帖子内容，必要时展开正文或打开详情、滚动读取。不得采集社区、首页推荐、回复页或名单外作者。不要按浏览器首次加载的空白或加载提示判定无更新。
3. 来源状态每项都填写：`ok` 是内容实际加载并完成本次筛选；`blocked` 是登录/验证码/不可访问；`error` 是其他读取故障。`note` 写实际检查范围与入选/未入选原因，`evidence_urls` 写真实读取链接。来源必须记录 `coverage: window_checked/sample/index_only/unknown` 与实际使用的 `window_months: 2`。来源索引可访问不等于整个窗口筛选完成；索引只读用index_only，少量页面用sample。不能把访问成功等同完整覆盖，未充分检查时不能声称窗口内无更新。首次扩大窗口后所有固定作者都要重新检查，不能沿用原48小时或七天判断。
4. 论文按 `paper_sources.yaml` 主题和规则从官方目录、文章页、录用决定页读取。可以用网页搜索定位，但最终核验必须用官方来源。不把所有 PMLR 论文视为 ICML，也不把 OpenReview 投稿视为录用。只选符合主题且能说明贡献、依据和局限的论文，主会/Findings/Workshop 分开标注。预印本当前不进入精选。只使用年份且没有精确日期的论文不得入选。
5. 时间：博主与论文统一限定最近两个自然月，按北京时间自然日向前回退两个月，包含起始日；月末取目标月最后一天。例如2026-10-03对应2026-08-03至2026-10-03。采集频率daily/weekly不改变内容窗口。以 `--plan` 的 `windows` 为准。只有旧预印本近期发表时用 `event_type: publication_update`，首次公开日期未知时也用该标记。保持论文首次公开、录用和发表日期分开。不得用采集时间充当发表时间。相对时间使用 `relative_age_hours` 并保留原文依据，采集批次六小时内有效。
6. 用当前 Codex 模型生成原创中文卡片，基于实际读到的正文。每条包括标题、摘要、价值、局限、实践动作、阅读深度与原文链接，X另填 `content_type: 工程实践/研究解读/作者观点/待核验线索`；优先带方法、代码、实验设置的技术内容，观点与线索作为补充，不用泛泛学习建议凑数。`evidence_excerpt` 保留短摘录用于审计。仅看到摘要时用 `reading_depth: 摘要`，不得标记为正文精读；X 可见帖子正文使用 `帖子`，截断要注明。每日博主最多五条、每人最多两条；论文目标每天十篇，上限十篇；按配置先检索约三十篇候选，再核验并排序筛选。跨来源发现与历史去重，优先相关性、证据和信息密度；合格且未推荐的论文不足十篇时如实说明原因，不能放宽两个月窗口、重复推荐或虚构内容凑数。没有优质新内容就留空。
7. 将采集结果保存为 `state/tech/collection.json`。格式见 `--plan` 的 `input_schema`。`observed_at` 使用本次真实时间、包含时区。来源条目含 `id/status/note/evidence_urls`，卡片 `source_id` 必须匹配计划。论文日期必须说明事件类型：单篇文章发表、录用或论文集上线；不能把卷集上线日期写成论文首次发表日期。可用 `date_label` 与 `date_evidence_url` 明示。论文 `verification_url` 必须指向对应会刊官方域名，ICLR/TMLR 可用 OpenReview 最终决定。可补充 `theme_id` 合并博主解读和同一原论文。
8. 执行 `.venv/bin/python main.py --input state/tech/collection.json --push`。检查 JSON 输出和退出状态：0=全部本次来源正常，2=部分失败或漏检，1=全部失败/输入错误。逐条检查 `rejected`；修正字段或证据，不改造日期让旧内容入选。输入不合法时不得报告任务完成。
9. 技术日报现在生成HTML，写入本仓库 `docs/YYYY-MM-DD.html`，归档首页为 `docs/index.html`，最新一期为 `docs/latest.html`；不生成本地Markdown。配置见 `tech_delivery.yaml`，唯一远端为 `https://github.com/winters1900/daily_digest.git`。程序只暂存生成的HTML、CSS与公开字体资源、提交中文归档信息并推送main。不可使用 `git add .`，不可上传邮箱报告、state目录、配置中的私人邮箱或任何密钥。项目代码已在同一仓库维护。
10. 推送后程序校验GitHub Pages实际页面的版本标记，确认当前HTML已上线，再经系统钥匙串中的Server酱配置把标题和阅读链接发到微信。不会在微信发送整篇正文。每天最多推送一次链接；发送失败或网页未就绪用 `.venv/bin/python main.py --publish YYYY-MM-DD` 重试，未上线时不发送失效链接。当天已推送后新采集内容仍留待次日，修订页面不重复发微信。成功以Server酱接受请求为准，不代表用户已阅读。正常发送在聊天保持安静；只有推送失败、登录失效或需要用户处理时通知。


## 论文发现与每周精读

官方论文列表通常批量发布，不能保证每天有新论文。期刊可以补充连续更新。每周六从本周已选论文中选一篇，根据公开全文撰写精读内容，放入采集JSON的 `weekly_review` 字符串，合并到周六HTML日报；微信推送同一页面链接，不另存Markdown：问题、方法、实验设置、比较基线、局限、复现路径。无法获取全文时明确跳过全文精读并记录原因，不购买全文、不虚构实验。周报区分已完成的阅读与尚未执行的实验计划，不声称已复现。

## 本地命令

```sh
.venv/bin/python main.py --plan
.venv/bin/python main.py --input state/tech/collection.json --push
.venv/bin/python main.py --push
.venv/bin/python main.py --build-site
.venv/bin/python main.py --publish YYYY-MM-DD
.venv/bin/python main.py --status
.venv/bin/python -m unittest discover -s tests -p 'test_tech_digest.py'
```

`--save-local` 仅在用户明确要求本地导出时使用。此前历史Markdown保留，今后技术HTML统一存入GitHub仓库docs目录。

独立执行 Python 只会导入最近的有效采集批次，不能替代浏览器采集。定时采集需要 Codex 自动任务运行及有效的浏览器会话。电脑休眠、网络中断、登录过期或网页改版会影响运行。

## 最近两个月的发现策略

时间窗口是候选范围，不是每天重复两个月内的内容。首次扩大窗口应补查此前被短窗口排除的文章，优先近期且信息密度高的未推荐内容；后续结合历史去重增量发现。X可先在每位名单作者个人页检查原创，再用该作者的站内搜索限定起始日，打开候选原帖核验；不收集名单外搜索结果。官方论文来源按卷期和文章页筛选，若检索受限则记录实际覆盖范围。无法完成窗口检查时保持sample/index_only并留待后续补查，不伪造检查成功。

## 页面与GitHub

开头只显示日期与标题，不放“北京时间／优质内容”等说明。论文优先展示，博主分享和周报分区；阅读范围、采集状态放在末尾折叠区。标题与正文统一使用中文衬线体，思源宋体（Noto Serif SC）字体与许可证随网页托管，不依赖外部字体服务。HTML支持手机屏幕和系统深色模式，所有外部文本经转义与标签过滤。GitHub Pages从main分支的docs目录发布，网站为 https://winters1900.github.io/daily_digest/ 。采集依然由本机Codex自动任务执行；GitHub负责存储和托管页面。
