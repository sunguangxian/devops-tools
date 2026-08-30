# -*- coding: utf-8 -*-
"""
hMailServer Event 周报归档入口。

用法：
    python services/weekly_report_sync/main.py            # 立即消费一次本地事件队列
    python services/weekly_report_sync/main.py --daemon   # 常驻消费队列并重试失败事件
"""

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config_loader import get_service_config
from common.logger import setup_logger
from services.weekly_report_sync.core import run_daemon, sync_once

logger = setup_logger(
    name="weekly_report_sync",
    log_filename="weekly_report_sync.log",
)


def main():
    parser = argparse.ArgumentParser(
        description="hMailServer Event 邮件附件自动归档至 SeedDMS",
    )
    parser.add_argument(
        "-d", "--daemon",
        action="store_true",
        help="常驻消费本地 Event 队列；不访问 IMAP",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅立即消费一次 Event 队列",
    )

    args = parser.parse_args()
    config = get_service_config("weekly_report_sync")
    if not config:
        logger.error("未找到 config/weekly_report_sync.yaml")
        sys.exit(1)

    if args.daemon:
        run_daemon(config)
    else:
        sync_once(config)


if __name__ == "__main__":
    main()
