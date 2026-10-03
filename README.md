# Daily Digest

技术知识与重要邮件日报，支持中文摘要、去重和微信推送。

技术日报从固定 X 博主和官方会议、期刊来源筛选最近两个自然月的内容，每天北京时间10:30运行。邮件日报使用独立配置，每天10:00处理已启用的收件箱。登录凭据保存在系统钥匙串，运行状态和邮件正文不上传到仓库。

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
.venv/bin/python main.py --input state/tech/collection.json --push
.venv/bin/python main.py --status
.venv/bin/python -m unittest discover -s tests
```

Python负责校验、去重与投递；X采集由已登录的Codex浏览器执行，不能用GitHub Actions直接复用本机的登录会话。每日HTML简报将保存在本仓库的 `docs/` 目录，密钥、邮箱数据、数据库及本地缓存不入库。
