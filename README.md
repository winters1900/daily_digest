# Daily Digest

技术知识与重要邮件日报，支持中文摘要、去重和微信推送。

技术日报从固定 X 作者、官方会刊、arXiv、Semantic Scholar、HF 和 AI 技术资讯来源筛选最近两个自然月的内容，目标10篇论文＋最多10条资讯，每天北京时间10:30运行。邮件日报使用独立配置，每天10:00处理已启用的收件箱。登录凭据保存在系统钥匙串，运行状态和邮件正文不上传到仓库。

## 安装

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp mail_digest.example.yaml mail_digest.yaml
cp config.example.yaml config.yaml
```

技术日报完整流程见 [TECH_DIGEST.md](TECH_DIGEST.md)，邮件配置见 [MAIL_DIGEST.md](MAIL_DIGEST.md)。X名单在 [x_accounts.yaml](x_accounts.yaml)，论文来源在 [paper_sources.yaml](paper_sources.yaml)。

```sh
.venv/bin/python main.py --plan
.venv/bin/python main.py --collect
.venv/bin/python main.py --review-queue
.venv/bin/python main.py --input state/tech/reviews.json
.venv/bin/python main.py --compose --dry-run
.venv/bin/python main.py --compose --push
.venv/bin/python main.py --health
.venv/bin/python main.py --status
.venv/bin/python main.py --build-site
.venv/bin/python main.py --publish YYYY-MM-DD
.venv/bin/python -m unittest discover -s tests
```

Python负责校验、去重与投递；X采集由已登录的Codex浏览器执行，不能用GitHub Actions直接复用本机的登录会话。每日HTML简报保存在本仓库的 `docs/` 目录，由GitHub Pages托管：[阅读首页](https://winters1900.github.io/daily_digest/)。微信推送只包含标题和页面链接。密钥、邮箱数据、数据库及本地缓存不入库。

需要逐源诊断时，可运行 `.venv/bin/python -m tech_digest.source_audit`。它独立抽样，原始结果留在 `state/tech/source_audits/`，浏览器来源需补查，失败来源保留真实原因。用 `--render <结果JSON>` 检查 HTML，核验后用 `--publish <结果JSON>` 单独归档并推送微信；页面上线前不发送，结果不明确时不自动重发。测试报告不修改已发送日报，也不把窗口外样本加入日常精选。
