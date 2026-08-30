# -*- coding: utf-8 -*-
"""
Git - Redmine Webhook 同步服务 Flask Web 入口
"""

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify
from common.config_loader import get_service_config
from common.logger import setup_logger
from services.git_redmine_sync.core import process_webhook

# 初始化日志记录器
logger = setup_logger(
    name="git_redmine_sync",
    log_filename="git_redmine_sync.log",
)

app = Flask(__name__)


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    """健康检查接口"""
    return jsonify({
        "status": "running",
        "service": "git_redmine_sync",
        "version": "1.0.0",
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    接收来自 GitLab / Gitea 的 Webhook 推送事件
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            logger.warning("收到无效或空的 Webhook 请求体")
            return "Invalid JSON body", 200

        config = get_service_config("git_redmine_sync")
        msg, code = process_webhook(data, config)
        return msg, code

    except Exception as e:
        logger.error(f"处理 Webhook 请求时发生未捕获异常: {e}", exc_info=True)
        # 返回 200 状态码以防 Git 服务端频繁重试导致服务雪崩
        return "Internal error handled", 200


def main():
    """主运行入口"""
    config = get_service_config("git_redmine_sync")
    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 5000))
    debug = bool(server_cfg.get("debug", False))

    logger.info(f"启动 Git-Redmine Webhook 同步服务: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
