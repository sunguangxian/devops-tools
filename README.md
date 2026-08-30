# DevOps & Server Automation Tools (自动化运维与服务器工具集)

这是一个专为企业内网/私有云服务器打造的 DevOps 自动化工具集合。

每个脚本/服务**独立配置、独立运行、互不干扰**，并通过统一的配置加载、日志记录与安全防护机制，方便平滑扩展与维护，且具备完善的 GitHub Public 开源脱敏设计。

---

## 🌟 当前已包含工具 / 服务

### 1. Git -> Redmine 状态自动同步服务 (`git_redmine_sync`)
- **多平台 Webhook 兼容**：原生支持 GitLab、Gitea 等代码托管平台的 Push 事件 Webhook。
- **真实提交人身份同步**：通过将 Git 用户名与个人 Redmine API Key 进行映射，在 Redmine 问题更新时记录真实的提交人与经办人，而非统一的机器人账号。
- **丰富的关联指令匹配**：
  - **关闭/解决问题**：`fix redmine-#123`、`fixes redmine-#123`、`close redmine-#123`、`closes redmine-#123`、`修复 redmine-#123`、`解决 redmine-#123`（自动将状态变更为“已解决/已关闭”，完成度置为 100%）。
  - **关联备注**：`refs redmine-#123`（仅追加提交日志备注，不修改问题状态）。
- **详尽的 Commit 备注拼装**：自动在 Redmine 备注中记录代码仓库、分支、Commit 短哈希、作者、完整 Commit 消息以及变更文件列表（`+ 新增` / `M 修改` / `- 删除`）。
- **分支过滤策略**：支持配置生效分支白名单（如仅 `main` / `master` / `release` 生效）。

---

## 📁 模块化与独立配置目录结构

**每个工具都有专属独立的配置文件（`<script_name>.example.yaml` 和 `<script_name>.yaml`）**：

```text
.
├── config/                                  # 统一配置目录
│   ├── git_redmine_sync.example.yaml        # [公开] Git-Redmine 同步服务的配置模板
│   ├── git_redmine_sync.yaml                # [私有] Git-Redmine 同步服务的实际生产配置（已忽略）
│   │
│   ├── <新脚本名>.example.yaml               # [公开] 新脚本的配置模板
│   └── <新脚本名>.yaml                       # [私有] 新脚本的实际生产配置（已忽略）
│
├── common/                                  # 基础通用模块
│   ├── __init__.py
│   ├── config_loader.py                     # 独立配置加载器（按脚本名自动加载对应 YAML）
│   └── logger.py                            # 统一日志记录器（控制台格式化 + 文件自动轮转）
│
├── services/                                # 常驻服务模块（Web/Webhook/API 服务）
│   ├── __init__.py
│   └── git_redmine_sync/                    # Git -> Redmine 状态同步服务
│       ├── __init__.py
│       ├── app.py                           # Flask Webhook 入口
│       ├── core.py                          # Webhook Payload 与 Commit 正则解析逻辑
│       └── redmine_client.py                # Redmine API 交互封装
│
├── scripts/                                 # 独立脚本目录（定时任务、一次性运维/巡检脚本）
│   └── README.md                            # 独立脚本扩展与编写指南
│
├── logs/                                    # 运行日志目录（已被 .gitignore 忽略）
│   └── .gitkeep
│
├── .gitignore                               # Git 忽略规则（自动保护所有 *.yaml 生产配置）
├── requirements.txt                         # 项目依赖清单
├── run.py                                   # 统一服务启动器
└── README.md                                # 项目说明文档
```

---

## 🚀 快速上手：以 `git_redmine_sync` 为例

### 1. 安装依赖

```bash
# 进入项目目录
cd flask

# 推荐创建并激活 Python 虚拟环境 (Python 3.8+)
python -m venv venv
source venv/bin/activate  # Linux / macOS
# 或者 Windows: .\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置该服务的专属配置文件

从模板复制并修改：

```bash
cp config/git_redmine_sync.example.yaml config/git_redmine_sync.yaml
```

编辑 `config/git_redmine_sync.yaml`，填入实际参数：

```yaml
# HTTP 服务监听
server:
  host: "0.0.0.0"
  port: 5000
  debug: false

# 您的 Redmine 地址
redmine_url: "http://redmine.example.com:3000"

# Redmine 问题状态名称
status:
  resolved: "已解决"
  closed: "已关闭"

# 仅在指定分支生效（空列表 [] 表示所有分支生效）
valid_branches: []

# Git 用户名 -> Redmine API Key 映射
# 获取路径：Redmine 个人中心 -> 右侧“API访问键” -> 显示
users:
  git_username_1: "your_redmine_api_key_1"
  git_username_2: "your_redmine_api_key_2"
```

### 3. 启动服务

```bash
# 方式一：通过统一启动器启动（推荐）
python run.py git_redmine_sync

# 方式二：直接运行该服务
python services/git_redmine_sync/app.py
```

服务运行后，可通过访问 `http://<服务器IP>:5000/health` 检查健康状态。

---

## ⚙️ GitLab / Gitea Webhook 设置

1. 进入代码仓库设置：**Settings** -> **Webhooks**。
2. **URL** 填入：`http://<部署服务器IP>:5000/webhook`。
3. **Trigger (触发器)** 勾选：**Push events (推送事件)**。
4. 测试发送请求，返回 `200 OK` 即配置成功。

---

## 📝 Commit Message 写法示例

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

## 🖥️ 生产环境后台守护部署

### 方案 A：Windows 服务器（使用 NSSM 注册为系统服务，推荐）

在 Windows Server 环境下，推荐使用 **NSSM (Non-Sucking Service Manager)** 将 Python 服务注册为开机自启、崩溃自动重启的 Windows 后台服务。

#### 1. 下载并安装 NSSM
- 访问 [NSSM 官网下载页面](https://nssm.cc/download) 下载最新压缩包（如 `nssm-2.24.zip`）。
- 解压后，根据系统架构进入 `win64`（或 `win32`）目录。
- 将 `nssm.exe` 复制到项目根目录，或放入系统 PATH（如 `C:\Windows\System32`）。

#### 2. 安装与配置服务

以**管理员身份**打开 PowerShell 或 CMD，执行以下命令：

##### 方式 1：通过命令行一键安装（推荐）
```powershell
# 假设项目路径为 C:\Tools\flask，Python 虚拟环境路径为 C:\Tools\flask\venv\Scripts\python.exe
# 1. 注册服务
nssm install GitRedmineSync "C:\Tools\flask\venv\Scripts\python.exe" "C:\Tools\flask\run.py git_redmine_sync"

# 2. 设置工作目录 (Startup Directory)
nssm set GitRedmineSync AppDirectory "C:\Tools\flask"

# 3. 配置控制台标准输出与错误日志输出（可选，服务本身已有日志轮转）
nssm set GitRedmineSync AppStdout "C:\Tools\flask\logs\stdout.log"
nssm set GitRedmineSync AppStderr "C:\Tools\flask\logs\stderr.log"

# 4. 设置自动重启策略
nssm set GitRedmineSync AppRestartDelay 5000

# 5. 启动服务
nssm start GitRedmineSync
```

##### 方式 2：通过图形化界面（GUI）安装
```powershell
nssm install GitRedmineSync
```
在弹出的 GUI 窗口中配置：
- **Application 选项卡**：
  - **Path**：选择 Python 解释器路径（例如 `C:\Tools\flask\venv\Scripts\python.exe`）
  - **Startup directory**：填写项目根目录（例如 `C:\Tools\flask`）
  - **Arguments**：填写 `run.py git_redmine_sync`
- **I/O 选项卡**（可选）：
  - **Output (stdout)**：`C:\Tools\flask\logs\stdout.log`
  - **Error (stderr)**：`C:\Tools\flask\logs\stderr.log`
- 点击 **Install service** 完成安装。

#### 3. 常用服务运维管理命令
```powershell
# 启动服务
nssm start GitRedmineSync

# 停止服务
nssm stop GitRedmineSync

# 重启服务
nssm restart GitRedmineSync

# 查看服务运行状态
nssm status GitRedmineSync

# 打开 GUI 窗口修改服务配置
nssm edit GitRedmineSync

# 卸载/删除服务
nssm remove GitRedmineSync confirm
```

---

### 方案 B：Linux 服务器（使用 Systemd 守护进程）

创建服务文件 `/etc/systemd/system/git-redmine-sync.service`：

```ini
[Unit]
Description=Git to Redmine Webhook Sync Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/tools/flask
ExecStart=/opt/tools/flask/venv/bin/python /opt/tools/flask/run.py git_redmine_sync
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

管理命令：
```bash
sudo systemctl daemon-reload
sudo systemctl enable git-redmine-sync   # 设置开机自启
sudo systemctl start git-redmine-sync    # 启动服务
sudo systemctl status git-redmine-sync   # 查看状态
sudo systemctl restart git-redmine-sync  # 重启服务
```

---

## 🧩 如何新增一个独立的脚本（如数据库备份、巡检监控）

添加新工具非常简单，完全遵循独立解耦原则：

1. **新建专属配置模板与运行配置**：
   - 创建 `config/db_backup.example.yaml`（公开模板）
   - 复制为 `config/db_backup.yaml`（本地生产填真实密码，`.gitignore` 已自动忽略防泄露）
2. **编写脚本（如 `scripts/db_backup.py`）**：
   ```python
   from common.config_loader import get_service_config
   from common.logger import setup_logger

   # 自动加载 config/db_backup.yaml
   config = get_service_config("db_backup")
   # 自动写入 logs/db_backup.log
   logger = setup_logger(name="db_backup", log_filename="db_backup.log")

   logger.info(f"开始备份数据库: {config.get('db_host')}")
   ```

详细规范请参阅 [`scripts/README.md`](scripts/README.md)。

---

## 🔒 GitHub 公开安全防护

本项目已配置通用 `.gitignore` 规则：
- 自动忽略 `config/*.yaml`、`config/*.json`（仅保留 `*.example.yaml` 示例）。
- 自动忽略 `logs/*` 和 `*.log`。
- 源码中零敏感信息硬编码，可安全推送到 GitHub Public 仓库。
