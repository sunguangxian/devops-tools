# -*- coding: utf-8 -*-
"""
统一服务启动器 (DevOps Automation Runner)
用法示例：
    python run.py                         # 启动合并后的统一服务（Git-Redmine Webhook + 周报后台自动归档）
    python run.py server                  # 同上，启动统一主服务
    python run.py git_redmine_sync        # 仅独立启动 Git-Redmine Webhook 服务
    python run.py weekly_report_sync      # 仅独立运行周报归档任务（支持参数如 --once 或 -d）
    python run.py --help                  # 查看帮助
"""

import sys
import argparse
from pathlib import Path

# 将项目根目录添加到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_unified_server():
    """启动合并后的统一服务（Webhook 接收 + 周报邮件后台监控）"""
    from services.server import main
    main()


def run_git_redmine_sync():
    """独立启动 Git-Redmine Webhook 同步服务"""
    from services.git_redmine_sync.app import main
    main()


def run_weekly_report_sync():
    """独立运行周报邮件发送后归档至 SeedDMS 工具"""
    from services.weekly_report_sync.main import main
    main()


SERVICES = {
    "server": {
        "func": run_unified_server,
        "help": "【推荐】启动统一合并服务（同时运行 Git-Redmine Webhook 与周报邮件后台归档）",
    },
    "git_redmine_sync": {
        "func": run_git_redmine_sync,
        "help": "仅独立运行 Git -> Redmine Webhook 自动同步服务",
    },
    "weekly_report_sync": {
        "func": run_weekly_report_sync,
        "help": "仅独立运行周报邮件监听与 SeedDMS 归档任务",
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

    # 捕获未知参数（便于透传给子脚本如 weekly_report_sync --once）
    args, unknown = parser.parse_known_args(argv)

    selected = SERVICES.get(args.service)
    if selected:
        # 子服务会再次解析 sys.argv，只保留需要透传给它的参数。
        # 例如: run.py weekly_report_sync --once -> 子脚本仅收到 --once。
        sys.argv = [sys.argv[0], *unknown]
        selected["func"]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
