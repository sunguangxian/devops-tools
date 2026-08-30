# -*- coding: utf-8 -*-
"""
统一服务/脚本启动入口
用法示例：
    python run.py                         # 默认启动 git_redmine_sync 服务
    python run.py git_redmine_sync        # 启动 git_redmine_sync 服务
    python run.py --help                  # 查看帮助
"""

import sys
import argparse
from pathlib import Path

# 将项目根目录添加到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_git_redmine_sync():
    """启动 Git-Redmine Webhook 同步服务"""
    from services.git_redmine_sync.app import main
    main()


SERVICES = {
    "git_redmine_sync": {
        "func": run_git_redmine_sync,
        "help": "启动 Git -> Redmine Webhook 自动同步服务",
    },
}


def main():
    parser = argparse.ArgumentParser(
        description="DevOps & Server Automation Tools 服务启动器",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    choices_help = "\n".join([f"  {k:20s}: {v['help']}" for k, v in SERVICES.items()])
    parser.add_argument(
        "service",
        nargs="?",
        default="git_redmine_sync",
        choices=list(SERVICES.keys()),
        help=f"指定要运行的服务名称（默认为 git_redmine_sync）：\n{choices_help}",
    )

    args = parser.parse_args()

    selected = SERVICES.get(args.service)
    if selected:
        selected["func"]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
