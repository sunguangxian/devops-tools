# 独立运维与自动化脚本目录 (Scripts)

本目录用于存放各类独立的运维、巡检、备份或定时自动化任务脚本。

## 目录组织与配置文件规范

为了保证每个脚本职责明确、互相独立，**每个脚本都拥有自己独立的配置与日志**：

```text
flask/
├── config/
│   ├── git_redmine_sync.example.yaml     # git_redmine_sync 的配置模板
│   ├── git_redmine_sync.yaml             # git_redmine_sync 的实际生产配置（.gitignore 忽略）
│   │
│   ├── db_backup.example.yaml            # 示例：数据库备份脚本的配置模板
│   ├── db_backup.yaml                    # 示例：数据库备份脚本的实际生产配置（.gitignore 忽略）
│   │
│   ├── host_monitor.example.yaml         # 示例：服务器监控脚本配置模板
│   └── host_monitor.yaml                 # 示例：服务器监控脚本实际配置
│
├── scripts/
│   ├── db_backup.py                      # 独立脚本 1
│   └── host_monitor.py                   # 独立脚本 2
```

---

## 新建脚本开发规范

新建任何独立脚本（如 `scripts/db_backup.py`）时，只需 3 步：

### 1. 在 `config/` 下创建独立的配置模板与运行配置
- `config/db_backup.example.yaml` （提交到 GitHub 公开仓库，填占位符）：
  ```yaml
  db_host: "127.0.0.1"
  db_port: 3306
  db_user: "backup_user"
  db_password: "your_password"
  backup_dir: "/data/backups"
  retention_days: 7
  ```
- `config/db_backup.yaml` （在服务器上填入实际配置，`.gitignore` 会自动忽略，防止泄露）。

### 2. 在脚本中引入统一配置加载与日志组件
```python
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# 确保能引入 common 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config_loader import get_service_config
from common.logger import setup_logger

# 1. 自动读取 config/db_backup.yaml
config = get_service_config("db_backup")

# 2. 自动输出到 logs/db_backup.log 和控制台
logger = setup_logger(name="db_backup", log_filename="db_backup.log")

def main():
    logger.info("开始执行数据库备份任务...")
    host = config.get("db_host")
    logger.info(f"连接数据库: {host}")
    # 执行业务逻辑...

if __name__ == "__main__":
    main()
```

### 3. 配置 Linux Crontab 定时运行
```bash
# 每天凌晨 2 点执行备份脚本
0 2 * * * /opt/tools/flask/venv/bin/python /opt/tools/flask/scripts/db_backup.py >> /dev/null 2>&1
```
