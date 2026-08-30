# -*- coding: utf-8 -*-
"""
统一服务启动器 (DevOps Automation Runner)
用法示例：
    python run.py                         # Git-Redmine Webhook + hMailServer Event 邮件归档
    python run.py server                  # 同上，启动统一主服务
    python run.py git_redmine_sync        # 仅独立启动 Git-Redmine Webhook 服务
    python run.py weekly_report_sync      # 仅独立消费 hMailServer Event 队列
    python run.py --help
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_unified_server():
    """启动统一服务：Webhook 接收 + hMailServer Event 队列消费。"""
    from services.server import main
    main()


def run_git_redmine_sync():
    from services.git_redmine_sync.app import main
    main()


def run_weekly_report_sync():
    """独立消费 hMailServer Event 邮件队列并归档至 SeedDMS。"""
    from services.weekly_report_sync.main import main
    main()


SERVICES = {
    "server": {
        "func": run_unified_server,
        "help": "【推荐】启动统一服务（Git-Redmine Webhook + hMailServer Event 邮件归档）",
    },
    "git_redmine_sync": {
        "func": run_git_redmine_sync,
        "help": "仅独立运行 Git -> Redmine Webhook 自动同步服务",
    },
    "weekly_report_sync": {
        "func": run_weekly_report_sync,
        "help": "仅独立消费 hMailServer Event 队列并归档 SeedDMS",
    },
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="DevOps & Server Automation Tools 统一服务启动器",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    choices_help = "\n".join([f"  {k:20s}: {v['help']}" for k, v in SERVICES.items()])
    parser.add_argument(
        "service",
        nargs="?",
        default="server",
        choices=list(SERVICES.keys()),
        help=f"指定要运行的服务模式（默认为 server 统一服务）：\n{choices_help}",
    )

    args, unknown = parser.parse_known_args(argv)
    selected = SERVICES.get(args.service)
    if selected:
        sys.argv = [sys.argv[0], *unknown]
        selected["func"]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
