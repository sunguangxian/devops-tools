# -*- coding: utf-8 -*-
"""
DevOps 统一服务 (Unified Automation Server)
将 Git-Redmine Webhook 同步与周报邮件自动归档整合为一个统一常驻后台服务。
"""

import sys
import threading
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify
from common.config_loader import get_service_config
from common.logger import setup_logger
from services.git_redmine_sync.core import process_webhook
from services.weekly_report_sync.core import sync_once, run_daemon

logger = setup_logger(
    name="unified_server",
    log_filename="server.log",
)

app = Flask(__name__)


# ------------------------------------------------------------------------------
# Web 路由
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """服务主页概览"""
    return jsonify({
        "system": "DevOps & Server Automation Server",
        "status": "running",
        "services": {
            "git_redmine_sync": {
                "webhook_url": "/webhook",
                "method": "POST",
                "description": "GitLab/Gitea Webhook -> Redmine 问题状态同步",
            },
            "weekly_report_sync": {
                "trigger_url": "/sync/weekly_report",
                "method": "POST",
                "description": "hMailServer 邮件监听 -> SeedDMS 知识库自动备份",
            },
        },
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
    """健康检查接口"""
    git_cfg = get_service_config("git_redmine_sync")
    report_cfg = get_service_config("weekly_report_sync")

    return jsonify({
        "status": "healthy",
        "git_redmine_sync_configured": bool(git_cfg.get("redmine_url")),
        "weekly_report_sync_configured": bool(report_cfg.get("mail_monitor", {}).get("username")),
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    接收来自 GitLab / Gitea 的 Webhook 推送事件并同步至 Redmine
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
        logger.error(f"处理 Webhook 时发生未捕获异常: {e}", exc_info=True)
        return "Internal error handled", 200


@app.route("/sync/weekly_report", methods=["POST", "GET"])
def trigger_weekly_report_sync():
    """
    手动通过 HTTP 接口触发一次周报邮件检查与 SeedDMS 归档
    """
    try:
        config = get_service_config("weekly_report_sync")
        if not config:
            return jsonify({"status": "error", "message": "未找到 weekly_report_sync 配置"}), 500

        archived = sync_once(config)
        return jsonify({
            "status": "success",
            "message": f"周报扫描归档完成，本次共处理归档 {archived} 个文件",
            "archived_count": archived,
        }), 200
    except Exception as e:
        logger.error(f"手动触发周报同步异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 后台周报邮件监听线程
# ------------------------------------------------------------------------------

def start_weekly_report_background_worker():
    """
    启动周报邮件监听的后台守护线程
    """
    report_cfg = get_service_config("weekly_report_sync")
    mail_cfg = report_cfg.get("mail_monitor", {})
    username = mail_cfg.get("username", "")

    if not username:
        logger.info("未配置周报邮箱账号 (username 为空)，周报后台监听线程未启用")
        return

    interval = int(mail_cfg.get("poll_interval_seconds", 60))
    logger.info(f"正在启动周报邮件监听后台线程 (监控账号: {username}, 轮询间隔: {interval}s)...")

    def worker_loop():
        # 启动时稍作延迟，避开 Flask 初始化高峰
        time.sleep(3)
        while True:
            try:
                sync_once(report_cfg)
            except Exception as e:
                logger.error(f"周报监听后台线程轮询异常: {e}", exc_info=True)
            time.sleep(interval)

    t = threading.Thread(target=worker_loop, name="WeeklyReportWorker", daemon=True)
    t.start()
    logger.info("周报邮件监听后台线程启动成功")


# ------------------------------------------------------------------------------
# 统一服务主入口
# ------------------------------------------------------------------------------

def main():
    """启动统一合并服务"""
    git_cfg = get_service_config("git_redmine_sync")
    server_cfg = git_cfg.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 5000))
    debug = bool(server_cfg.get("debug", False))

    logger.info("==================================================================")
    logger.info("正在启动 DevOps 统一自动化服务 (Unified Automation Server)")
    logger.info("==================================================================")

    # 1. 启动周报后台监听线程
    start_weekly_report_background_worker()

    # 2. 启动 Flask Webhook 服务
    logger.info(f"HTTP 服务已就绪: http://{host}:{port} (支持 /webhook 与 /sync/weekly_report)")
    # use_reloader=False 防止 debug 模式在多线程下重复拉起 background worker
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
