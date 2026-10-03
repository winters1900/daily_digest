# 固定 X 作者采集

完整工作流程见 [TECH_DIGEST.md](TECH_DIGEST.md)。只读取 [x_accounts.yaml](x_accounts.yaml) 中的固定作者个人页、原帖与作者站内搜索，不读取首页推荐或社区，不导出 Cookie。

原 10 位每日检查，参考名单新增 30 位分三组轮换。首次核验所有新账号身份，`pending` 不代表已接通，须从可信官方关联页确认身份；机构账号单独标注。正常浏览器登录失效直接记 blocked，不绕过访问限制。每次记录实际读取范围与原文链接，少量帖子为 sample，不将访问成功当作整个两个月无更新。

Python `--collect` 会将这些来源列入 `state/tech/browser_tasks.json`，由当前 Codex 任务读取；浏览器采集原始 items 入候选池，完整 reviews 才能参与精选。各作者与其他资讯共享最多8条额度，每人最多两条；论文、解读和新闻同一主题只展示一次。已发送后补采留次日。

每日10:30自动任务依赖本机 Codex 在线与有效会话；GitHub Actions 无法复用此登录。HTML 上线后微信只推阅读链接，故障需要处理时通知。
