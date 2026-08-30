# DevOps Tools & Server Automation Platform

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

面向企业内网 Windows Server 的轻量级自动化工具集。目前主要包含：

- Git / Gitea Webhook -> Redmine 状态同步；
- hMailServer Event -> SeedDMS 周会/周报附件自动归档。

统一入口为 `python run.py`，由 Waitress 承载 HTTP 接口，并在同一进程内运行邮件事件队列消费者。

---

## 1. Git -> Redmine

支持 GitLab / Gitea Push Webhook，根据 Commit Message 自动更新 Redmine：

- `fix redmine-#123`
- `fixes redmine-#123`
- `close redmine-#123`
- `解决 redmine-#123`
- `refs redmine-#123`

可配置分支白名单、Git 用户 -> Redmine API Key 映射，并在 Redmine 备注中记录仓库、分支、Commit、作者和变更文件。

---

## 2. hMailServer Event -> SeedDMS

邮件归档已改为 **hMailServer Event 驱动**，不再通过 IMAP 扫描 Sent 文件夹。

流程：

```text
邮件经 hMailServer 投递
        │
        ▼
OnDeliverMessage
        │
        ├── 复制 oMessage.Filename
        │      到 data/mail_event_queue/*.eml
        │
        └── POST /event/hmailserver
                 │
                 ▼
          唤醒 Python 队列消费者
                 │
                 ▼
          解析 .eml / 过滤邮件
                 │
                 ▼
             提取附件
                 │
                 ▼
              SeedDMS
                 │
                 ▼
        processed_emails.json
```

### 主要特性

- 不依赖邮件客户端是否保存 Sent 副本；
- 不需要 IMAP 账号，也不存在轮询漏邮件问题；
- hMailServer Event 只负责复制 `.eml` 和本机 HTTP 通知，不等待 SeedDMS 上传；
- devops-tools 服务暂时停止时，`.eml` 会继续保留在本地队列，服务恢复后自动处理；
- Message-ID 防重复；无 Message-ID 时使用整封邮件 SHA256；
- 每封邮件使用独立附件临时目录，避免同名文件覆盖；
- 附件按 `文件名 + SHA256` 记录状态，部分上传失败后只重试失败附件；
- JSON 状态文件原子更新，不引入 SQLite；
- 可按发件人、主题关键词、To/CC、附件扩展名过滤；
- SeedDMS 按年份 / 类别自动建目录，并对同名文档新增版本；
- 全部附件成功后可通过 hMailServer SMTP 发送归档完成通知。

---

## 3. 目录结构

```text
devops-tools/
├── common/
│   ├── config_loader.py
│   └── logger.py
│
├── config/
│   ├── git_redmine_sync.example.yaml
│   └── weekly_report_sync.example.yaml
│
├── services/
│   ├── server.py
│   ├── health.py
│   ├── git_redmine_sync/
│   └── weekly_report_sync/
│       ├── main.py
│       ├── core.py
│       ├── mail_event.py
│       ├── mail_notifier.py
│       ├── filename_rules.py
│       └── seeddms_client.py
│
├── scripts/
│   └── hmailserver/
│       └── EventHandlers.example.vbs
│
├── data/
│   ├── mail_event_queue/
│   ├── temp_attachments/
│   └── processed_emails.json
│
├── tests/
├── run.py
└── requirements.txt
```

---

## 4. 安装

Windows PowerShell：

```powershell
cd C:\server-service\devops-tools
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制生产配置：

```powershell
Copy-Item config\git_redmine_sync.example.yaml config\git_redmine_sync.yaml
Copy-Item config\weekly_report_sync.example.yaml config\weekly_report_sync.yaml
```

生产 `*.yaml` 已由 `.gitignore` 排除，不应提交账号密码和 API Key。

---

## 5. weekly_report_sync 配置

关键配置示例：

```yaml
hmail_event:
  enabled: true
  queue_dir: "./data/mail_event_queue"
  api_key: "换成随机长字符串"
  allow_remote: false
  retry_interval_seconds: 30

filter_rules:
  allowed_senders:
    - "report_sender@company.com"

  subject_keywords:
    - "周会"
    - "会议纪要"

  required_recipients:
    - "software_group@company.com"
    - "hardware_group@company.com"

  allowed_extensions:
    - ".docx"
    - ".xlsx"
    - ".pdf"

notification:
  enabled: true
  smtp_host: "127.0.0.1"
  smtp_port: 25
  username: "report_sender@company.com"
  password: "邮件密码"
  recipient: "report_sender@company.com"

storage:
  temp_dir: "./data/temp_attachments"
  history_file: "./data/processed_emails.json"
```

`allowed_senders` 建议明确填写。`OnDeliverMessage` 会看到 hMailServer 的全部待投递邮件，发件人白名单可以避免外部来信被误识别为归档邮件。

---

## 6. 配置 hMailServer Event

仓库提供：

```text
scripts\hmailserver\EventHandlers.example.vbs
```

先修改其中三个常量：

```vbscript
Const DEVOPS_QUEUE_DIR = "C:\server-service\devops-tools\data\mail_event_queue"
Const DEVOPS_EVENT_URL = "http://127.0.0.1:5000/event/hmailserver"
Const DEVOPS_EVENT_KEY = "与 weekly_report_sync.yaml 一致"
```

然后把其中的代码合并到 hMailServer 的：

```text
C:\Program Files (x86)\hMailServer\Events\EventHandlers.vbs
```

如果你的 hMailServer 安装在 64 位 Program Files，则按实际安装目录修改。

在 hMailServer Administrator 中：

1. 打开 `Settings -> Advanced -> Scripts`；
2. Script language 选择 `VBScript`；
3. 勾选 `Enabled`；
4. 点击 `Check syntax`；
5. 点击 `Reload scripts`。

事件使用：

```vbscript
Sub OnDeliverMessage(oMessage)
```

事件内不会直接访问 SeedDMS，只会：

```text
CopyFile oMessage.Filename -> mail_event_queue
POST http://127.0.0.1:5000/event/hmailserver
```

因此 SeedDMS 慢或暂时不可用时，不会让 hMailServer 长时间等待上传。

### Windows 权限

hMailServer 服务运行账号必须至少能够写入：

```text
C:\server-service\devops-tools\data\mail_event_queue
```

devops-tools 服务运行账号需要能够读写：

```text
data\mail_event_queue
data\temp_attachments
data\processed_emails.json
```

---

## 7. 启动统一服务

```powershell
python run.py
```

主要接口：

```text
GET  /health
POST /webhook
POST /event/hmailserver
POST /sync/weekly_report
```

`/event/hmailserver` 默认只接受本机 `127.0.0.1 / ::1`，并要求：

```text
X-API-Key: <hmail_event.api_key>
```

`POST /sync/weekly_report` 是手工立即消费队列的接口，使用单独的 `manual_trigger.api_key`。

---

## 8. Windows NSSM 部署

假设项目目录：

```text
C:\server-service\devops-tools
```

注册服务：

```powershell
nssm install DevOpsAutomation `
  "C:\server-service\devops-tools\venv\Scripts\python.exe" `
  "C:\server-service\devops-tools\run.py"

nssm set DevOpsAutomation AppDirectory "C:\server-service\devops-tools"
nssm set DevOpsAutomation AppStdout "C:\server-service\devops-tools\logs\stdout.log"
nssm set DevOpsAutomation AppStderr "C:\server-service\devops-tools\logs\stderr.log"
nssm set DevOpsAutomation AppRestartDelay 5000
nssm start DevOpsAutomation
```

服务恢复后会首先扫描 `mail_event_queue` 中遗留的 `.eml`，所以 hMailServer 事件发生时即使 devops-tools 正在重启，也不会丢失已经成功复制到队列的邮件。

---

## 9. 健康检查

```text
http://<服务器IP>:5000/health
```

检查内容包括：

- hMailServer Event 队列目录是否可写；
- Event 消费线程是否存活；
- 当前待处理 `.eml` 数量；
- SeedDMS 登录与目标目录；
- Redmine API。

不再检查 IMAP。

---

## 10. 手工测试邮件事件

先手工复制一封 `.eml` 到：

```text
data\mail_event_queue\test.eml
```

然后：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:5000/event/hmailserver" `
  -Headers @{"X-API-Key"="你的 hmail_event.api_key"}
```

或者直接执行：

```powershell
python run.py weekly_report_sync --once
```

---

## 11. 测试

```powershell
python -m unittest discover -s tests -v
```

---

## 12. Git / Redmine Webhook

Gitea / GitLab Webhook URL：

```text
http://<服务器IP>:5000/webhook
```

建议只启用 Push events。

Commit Message 示例：

```bash
git commit -m "fix redmine-#1024 修复登录问题"
git commit -m "refs redmine-#1026 补充日志"
```
