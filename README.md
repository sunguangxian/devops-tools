# DevOps Tools & Server Automation Platform (自动化运维与服务器工具集)

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)
![Architecture](https://img.shields.io/badge/Architecture-Modular%20%26%20Decoupled-orange.svg)

专为企业内网/私有云服务器打造的轻量级 DevOps 自动化运维工具集合。

支持**合并为统一常驻后台服务运行**（单进程兼顾 HTTP Webhook 接收与后台邮件静默监听轮询），同时也支持每个脚本**独立配置、独立调试、互相解耦**。具备统一的配置加载、日志记录（按大小自动轮转）与严格的开源安全防泄露设计。

---

## 🌟 核心功能特性

### 1. Git -> Redmine 状态自动同步服务 (`git_redmine_sync`)
- **多平台 Webhook 兼容**：原生支持 GitLab、Gitea 等代码托管平台的 Push 事件 Webhook。
- **真实提交人身份同步**：通过将 Git 用户名与个人 Redmine API Key 进行映射，在 Redmine 问题更新时记录真实的提交人与经办人，而非统一的机器人账号。
- **丰富的关联指令匹配**：
  - **关闭/解决问题**：`fix redmine-#123`、`fixes redmine-#123`、`close redmine-#123`、`closes redmine-#123`、`修复 redmine-#123`、`解决 redmine-#123`（自动将状态变更为“已解决/已关闭”，完成度置为 100%）。
  - **关联引用备注**：`refs redmine-#123`（仅追加提交日志备注，不修改问题状态）。
- **详尽的 Commit 备注拼装**：自动在 Redmine 备注中记录代码仓库、分支、Commit 短哈希、作者、完整 Commit 消息以及变更文件列表（`+ 新增` / `M 修改` / `- 删除`）。
- **分支过滤策略**：支持配置生效分支白名单（如仅 `main` / `master` / `release` 生效）。

### 2. 周报邮件发送后自动归档至 SeedDMS (`weekly_report_sync`)
- **零干扰自动归档**：您依然使用日常的邮件客户端（如 Foxmail、Outlook、Webmail 等）正常发送周报，无需改变任何个人习惯。
- **hMailServer IMAP 静默监听**：服务自动监听发件箱（Sent）或指定归档邮箱，匹配主题关键词（如“周报”、“工作周报”）并自动提取附件（.docx / .xlsx / .pdf 等）。
- **最终分发识别**：可要求 To/CC 同时包含软件、硬件研发组地址，先发给老板确认的邮件不会提前归档。
- **按年份与类别自动归档**：从附件名末尾日期提取年份，并按“年份/文档类别/原附件名”自动创建目录，例如 `2026年/研发项目周会会议纪要/研发项目周会会议纪要(2026-08-30).docx`。
- **同名文档版本更新**：目标类别目录中已存在完全同名文档时，通过 SeedDMS 更新接口新增版本，不创建重复文档。
- **Message-ID 去重防护**：持久化记录已处理邮件唯一标识，防止重复归档。
- **完成邮件通知**：每封原邮件的附件全部归档成功后，通过 hMailServer 向本人发送附件及归档路径汇总。

### 3. 统一合并后台服务 (Unified Automation Server)
- **极简运维部署**：启动单个主程序 `python run.py` 即可同时运行 Waitress Web 服务与后台守护监听线程，无需维护多个进程。
- **标准 HTTP 接口**：
  - `GET /`：服务主页与功能清单概览
  - `GET /health`：全局健康检查与各子功能配置就绪状态
  - `POST /webhook`：接收 GitLab / Gitea 推送事件
  - `POST /sync/weekly_report`：手动 HTTP 触发一次周报即时扫描与归档

---

## 📁 目录结构与设计

**每个功能保持专属独立的配置文件（`<service_name>.example.yaml` 和 `<service_name>.yaml`）**：

```text
devops-tools/
├── config/                                  # 统一配置目录
│   ├── git_redmine_sync.example.yaml        # [公开模板] Git-Redmine 同步服务配置
│   ├── git_redmine_sync.yaml                # [私有生产] Git-Redmine 同步服务配置（已忽略）
│   │
│   ├── weekly_report_sync.example.yaml      # [公开模板] 周报邮件监听与 SeedDMS 归档配置
│   ├── weekly_report_sync.yaml              # [私有生产] 周报邮件监听与 SeedDMS 归档配置（已忽略）
│   │
│   ├── <新脚本名>.example.yaml               # [公开模板] 后续新脚本配置模板
│   └── <新脚本名>.yaml                       # [私有生产] 后续新脚本实际配置（已忽略）
│
├── common/                                  # 基础通用模块
│   ├── __init__.py
│   ├── config_loader.py                     # 独立配置加载器（按名称自动查找加载 YAML）
│   └── logger.py                            # 统一日志记录器（控制台输出 + 按大小自动轮转）
│
├── services/                                # 服务模块
│   ├── __init__.py
│   ├── server.py                            # 统一合并服务（整合 Webhook 与后台监听线程）
│   │
│   ├── git_redmine_sync/                    # Git -> Redmine 状态同步子模块
│   │   ├── __init__.py
│   │   ├── app.py                           # 独立 Flask Webhook 入口
│   │   ├── core.py                          # Payload 与 Commit 正则解析逻辑
│   │   └── redmine_client.py                # Redmine API 交互封装
│   │
│   └── weekly_report_sync/                  # 周报邮件监听与 SeedDMS 归档子模块
│       ├── __init__.py
│       ├── main.py                          # 独立启动入口
│       ├── core.py                          # 调度与去重核心逻辑
│       ├── filename_rules.py                # 附件名年份与文档类别解析规则
│       ├── imap_listener.py                 # hMailServer IMAP 邮件监听与附件提取
│       └── seeddms_client.py                # SeedDMS API、两级目录与版本更新
│
├── scripts/                                 # 独立脚本目录（定时任务、一次性运维/巡检脚本）
│   └── README.md                            # 独立脚本编写与扩展规范
│
├── logs/                                    # 运行日志目录（已被 .gitignore 忽略）
│   └── .gitkeep
│
├── .gitignore                               # Git 忽略规则（自动保护所有 *.yaml 生产配置）
├── requirements.txt                         # 项目依赖清单
├── run.py                                   # 统一服务启动器入口
└── README.md                                # 项目说明文档
```

---

## 🚀 快速上手与配置

### 1. 安装依赖环境

```bash
# 进入项目目录
cd devops-tools

# 推荐使用受支持的 64 位 Python 创建并激活虚拟环境 (Python 3.9+)
python -m venv venv
source venv/bin/activate  # Linux / macOS
# 或者 Windows: .\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置专属参数文件

复制示例模板并根据实际环境填写配置：

#### (1) 配置 Git-Redmine 状态同步：
```bash
cp config/git_redmine_sync.example.yaml config/git_redmine_sync.yaml
```
编辑 `config/git_redmine_sync.yaml`，填入 Redmine 地址、状态名称以及 Git 用户名对应的 Redmine API Key。

#### (2) 配置周报邮件归档至 SeedDMS：
```bash
cp config/weekly_report_sync.example.yaml config/weekly_report_sync.yaml
```
编辑 `config/weekly_report_sync.yaml`，填入 hMailServer IMAP 账号密码及 SeedDMS 连接参数与目标目录 ID。

---

### 3. 启动运行

```bash
# 【方式一：推荐】启动统一合并服务（同时运行 Webhook 接收 + 周报邮件后台监控）
python run.py

# 【方式二】单独启动调试某个子模块：
python run.py git_redmine_sync        # 仅独立启动 Git-Redmine Webhook 服务
python run.py weekly_report_sync      # 仅独立执行一次周报邮件检查与归档
```

服务启动后，访问 `http://<服务器IP>:5000/health` 可检查后台轮询线程、最近一次执行结果，以及 IMAP、SeedDMS、Redmine 的实时连接状态。任一关键组件异常时返回 HTTP 503。

---

## 🖥️ 生产环境后台守护部署（Windows NSSM 一键部署）

在 Windows Server 环境下，推荐使用 **NSSM (Non-Sucking Service Manager)** 将合并后的统一服务注册为 **单个** Windows 系统服务，实现开机自启与崩溃自愈。

### 1. 准备 NSSM
- 访问 [NSSM 官网下载](https://nssm.cc/download) 并解压。
- 将 `nssm.exe` 复制到项目根目录（或放入系统 `PATH` 路径下）。

### 2. 注册系统服务

以**管理员身份**打开 PowerShell 或 CMD：

```powershell
# 假设项目部署在 C:\devops-tools，虚拟环境 Python 解释器为 C:\devops-tools\venv\Scripts\python.exe
# 1. 注册统一服务（HTTP 由 Windows 兼容的 Waitress 承载）
nssm install DevOpsAutomation "C:\devops-tools\venv\Scripts\python.exe" "C:\devops-tools\run.py"

# 2. 设置工作目录
nssm set DevOpsAutomation AppDirectory "C:\devops-tools"

# 3. 设置日志重定向
nssm set DevOpsAutomation AppStdout "C:\devops-tools\logs\stdout.log"
nssm set DevOpsAutomation AppStderr "C:\devops-tools\logs\stderr.log"

# 4. 配置崩溃自动重启延迟
nssm set DevOpsAutomation AppRestartDelay 5000

# 5. 启动服务
nssm start DevOpsAutomation
```

### 3. 常用服务运维命令
```powershell
nssm status DevOpsAutomation         # 查看运行状态
nssm restart DevOpsAutomation        # 重启服务
nssm stop DevOpsAutomation           # 停止服务
nssm edit DevOpsAutomation           # 打开 GUI 界面修改配置
nssm remove DevOpsAutomation confirm # 卸载服务
```

---

## ⚙️ GitLab / Gitea Webhook 设置

1. 进入代码仓库设置：**Settings** -> **Webhooks**。
2. **URL** 填入：`http://<部署服务器IP>:5000/webhook`。
3. **Trigger (触发器)** 勾选：**Push events (推送事件)**。
4. 点击测试发送请求，返回 `200 OK` 即配置成功。

---

## 📝 Commit Message 写法示例

在提交代码时引用或关闭 Redmine 问题：

```bash
# 解决并关闭问题（支持中英文）
git commit -m "fix redmine-#1024 修复登录页面在移动端的适配问题"
git commit -m "解决 redmine-#1025 优化接口查询性能"

# 仅关联引用问题（只添加提交记录备注，不改变状态）
git commit -m "refs redmine-#1026 完善单元测试"

# 支持单次提交同时操作多个问题
git commit -m "fixes redmine-#101 修复异常; refs redmine-#102 补充相关日志"
```

---

## 🧩 如何扩展新的运维脚本

添加新工具遵循独立解耦原则：

1. 在 `config/` 下创建 `<script_name>.example.yaml` 与 `<script_name>.yaml`。
2. 在 `scripts/<script_name>.py` 中调用公共组件：
   ```python
   from common.config_loader import get_service_config
   from common.logger import setup_logger

   config = get_service_config("my_script")
   logger = setup_logger(name="my_script", log_filename="my_script.log")
   ```
详细说明请参阅 [`scripts/README.md`](scripts/README.md)。

---

## 🔒 GitHub 公开安全防护

本项目严格遵循开源安全规范：
- `.gitignore` 自动忽略所有 `config/*.yaml` 生产配置文件及日志文件，仅提交脱敏的 `*.example.yaml` 模板。
- 源码与文档中零敏感凭据硬编码，保障私有内网信息与密钥安全。
