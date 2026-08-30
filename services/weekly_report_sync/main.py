# -*- coding: utf-8 -*-
"""
周报邮件发送后自动归档至 SeedDMS 脚本入口
用法：
    python services/weekly_report_sync/main.py            # 执行一次扫描同步
    python services/weekly_report_sync/main.py --daemon   # 作为常驻后台轮询守护进程运行
"""

import sys
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path 中
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config_loader import get_service_config
from common.logger import setup_logger
from services.weekly_report_sync.core import sync_once, run_daemon

# 初始化日志记录器
logger = setup_logger(
    name="weekly_report_sync",
    log_filename="weekly_report_sync.log",
)


def main():
    parser = argparse.ArgumentParser(
        description="周报邮件发送后自动备份至 SeedDMS 知识库工具",
    )
    parser.add_argument(
        "-d", "--daemon",
        action="store_true",
        help="以常驻后台守护进程方式运行（自动按配置的时间间隔轮询检查新邮件）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅执行一次邮件检查与同步归档（适用于计划任务 / Windows Task Scheduler）",
    )

    args = parser.parse_args()
    config = get_service_config("weekly_report_sync")

    if not config:
        logger.error("未找到 weekly_report_sync 配置文件，请检查 config/weekly_report_sync.yaml 是否存在！")
        sys.exit(1)

    if args.daemon:
        run_daemon(config)
    else:
        # 默认执行一次同步
        sync_once(config)


if __name__ == "__main__":
    main()
