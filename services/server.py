# -*- coding: utf-8 -*-
"""
DevOps 统一服务 (Unified Automation Server)
将 Git-Redmine Webhook 同步与周报邮件自动归档整合为一个统一常驻后台服务。
"""

import sys
import threading
import time
from datetime import datetime
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
from services.health import probe_dependencies

logger = setup_logger(
    name="unified_server",
    log_filename="server.log",
)

app = Flask(__name__)

_worker_lock = threading.Lock()
_worker_thread = None
_worker_state = {
    "last_started": None,
    "last_completed": None,
    "last_error": None,
    "last_archived_count": None,
}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_worker_health():
    """返回周报后台线程的存活状态和最近一次运行结果。"""
    with _worker_lock:
        state = dict(_worker_state)
        state["alive"] = bool(_worker_thread and _worker_thread.is_alive())
    state["ok"] = state["alive"] and not state["last_error"]
    return state


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
    """检查后台任务状态及 IMAP、SeedDMS、Redmine 的实际连通性。"""
    git_cfg = get_service_config("git_redmine_sync")
    report_cfg = get_service_config("weekly_report_sync")
    timeout = float(git_cfg.get("server", {}).get("health_timeout_seconds", 5))
    dependencies = probe_dependencies(git_cfg, report_cfg, timeout=timeout)
    worker = get_worker_health()
    healthy = worker["ok"] and all(item["ok"] for item in dependencies.values())

    return jsonify({
        "status": "healthy" if healthy else "unhealthy",
        "worker": worker,
        "dependencies": dependencies,
    }), 200 if healthy else 503


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
            with _worker_lock:
                _worker_state["last_started"] = _utc_now()
            try:
                archived = sync_once(report_cfg)
                with _worker_lock:
                    _worker_state["last_completed"] = _utc_now()
                    _worker_state["last_error"] = None
                    _worker_state["last_archived_count"] = archived
            except Exception as e:
                with _worker_lock:
                    _worker_state["last_completed"] = _utc_now()
                    _worker_state["last_error"] = str(e)
                logger.error(f"周报监听后台线程轮询异常: {e}", exc_info=True)
            time.sleep(interval)

    global _worker_thread
    _worker_thread = threading.Thread(
        target=worker_loop,
        name="WeeklyReportWorker",
        daemon=True,
    )
    _worker_thread.start()
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
    threads = int(server_cfg.get("threads", 4))

    logger.info("==================================================================")
    logger.info("正在启动 DevOps 统一自动化服务 (Unified Automation Server)")
    logger.info("==================================================================")

    # 1. 启动周报后台监听线程
    start_weekly_report_background_worker()

    # 2. 使用 Windows 兼容的生产 WSGI 服务启动 HTTP 接口
    logger.info(f"HTTP 服务已就绪: http://{host}:{port} (支持 /webhook 与 /sync/weekly_report)")
    from waitress import serve
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
