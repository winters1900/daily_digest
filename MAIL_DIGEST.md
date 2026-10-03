# 三邮箱重要邮件日报

此功能与 `main.py` 技术日报独立。它只读三个收件箱，不改动邮件、标签或文件夹。每天北京时间 10:00 汇总求职、学业和其他待办邮件，生成本地报告并通过 Server酱发到个人微信。

## 安装

```bash
cd /Users/winters/Desktop/daily-digest
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

编辑 [mail_digest.yaml](/Users/winters/Desktop/daily-digest/mail_digest.yaml)，填写三个邮箱的 `address`。可在 `rules` 中添加可信发件人地址或主题关键词；不要在此文件填写密码或 API Key。建议将 Python 升级到 3.10 以上；当前机器的 3.9 可以运行，但 Google 库已提示其支持期结束。

当前按用户要求暂不接入 Gmail，配置中 `gmail.enabled: false`；以后完成 OAuth 授权后改为 `true` 即可。`check all` 和每日运行只处理已启用邮箱。

### 1. 南大学生邮箱

学校的[客户端说明](https://itsc.nju.edu.cn/1a/8f/c21586a334479/page.htm)要求启用 IMAP，并在开启扫码登录后使用“客户端专用密码”。网页邮箱：设置 → 客户端设置 → 开启 IMAP/POP/SMTP；在“微信绑定”中生成客户端专用密码。

```bash
.venv/bin/python -m mail_digest set-secret nju_password
```

### 2. QQ 邮箱

在 QQ 邮箱网页设置中开启 IMAP/SMTP，生成授权码。输入授权码，不要输入 QQ 登录密码。

```bash
.venv/bin/python -m mail_digest set-secret qq_password
```

### 3. Gmail

按 [Google 官方 Python 快速入门](https://developers.google.com/workspace/gmail/api/quickstart/python)建立 Google Cloud 项目、启用 Gmail API、创建“桌面应用”OAuth 客户端并下载 JSON。程序仅请求 `gmail.readonly` 权限。将下载文件放在项目目录**以外**，然后运行：

```bash
.venv/bin/python -m mail_digest gmail-client /你的私有目录/credentials.json
.venv/bin/python -m mail_digest gmail-auth
```

授权 JSON 和刷新令牌会存入 macOS 登录钥匙串。导入后请妥善保管或删除下载的 JSON。若 Google OAuth 应用仍处于测试模式，刷新令牌可能到期；到期时重新运行 `gmail-auth`。

### 4. DeepSeek 与微信

获取 DeepSeek API Key，并在 [Server酱](https://sct.ftqq.com/sendkey/)用微信登录、生成 SendKey、配置个人微信接收通道。Server酱免费额度以其当前页面为准。邮件标题和摘要会送到 Server酱；发件人、主题和最多 1200 字的正文摘录会送到 DeepSeek。验证码邮件不提交模型，摘录中的验证码行会被过滤。请不要把涉密邮件接入外部模型或推送服务。

```bash
.venv/bin/python -m mail_digest set-secret deepseek_key
.venv/bin/python -m mail_digest set-secret serverchan_key
```

## 验证与启用

```bash
.venv/bin/python -m mail_digest check all
.venv/bin/python -m mail_digest test-push
.venv/bin/python -m mail_digest run
.venv/bin/python -m mail_digest install-schedule
```

按上述顺序操作。`check all` 只测试读取，不分类、不推送；`test-push` 只发送不含邮件内容的测试消息。确认邮箱读取、微信收信及手动日报无误后，再安装 `launchd` 定时任务。安装会立即执行一次，之后按此 Mac 的本地时区每天 10:00 执行，并于 10:30、12:00、18:00 补跑；请确保系统时区是“上海”。若当天已推送，补跑不会重复通知。电脑关机时无法准时执行，重新登录后 `launchd` 会运行一次。程序会从上次成功扫描时间继续读取。

本地报告：`reports/mail/YYYY-MM-DD.md`。状态：`state/mail_digest.sqlite3`。两者包含私人邮件信息，文件权限设为仅当前用户可读，且已列入 `.gitignore`。一天最多推送一条；当天推送后到来的重要邮件留待次日。无重要邮件时不推送。遇到某个邮箱或模型暂时故障，其余可处理邮件仍会保存并推送，失败部分在下次运行时重试。

## 原技术日报的旧密钥

旧 `config.yaml` 曾含明文 GitHub Token 和 Reddit 凭据，已从该文件移除。请到对应服务撤销并重建这些旧密钥。GitHub 抓取如需认证，运行技术日报前设置 `DAILY_DIGEST_GITHUB_TOKEN` 环境变量。Reddit 抓取目前使用公开 RSS，旧 Reddit 凭据无需迁入。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```
