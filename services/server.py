# -*- coding: utf-8 -*-
"""DevOps 统一服务：Git-Redmine Webhook + hMailServer Event 邮件归档。"""

import hmac
import sys
import threading
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request

from common.config_loader import get_service_config
from common.logger import setup_logger
from services.git_redmine_sync.core import process_webhook
from services.health import probe_dependencies
from services.weekly_report_sync.core import get_pending_queue_count, sync_once

logger = setup_logger(
    name="unified_server",
    log_filename="server.log",
)

app = Flask(__name__)

_worker_lock = threading.Lock()
_worker_thread = None
_queue_wakeup = threading.Event()
_worker_state = {
    "last_started": None,
    "last_completed": None,
    "last_error": None,
    "last_archived_count": None,
}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_worker_health():
    with _worker_lock:
        state = dict(_worker_state)
        state["alive"] = bool(_worker_thread and _worker_thread.is_alive())
    try:
        state["pending_events"] = get_pending_queue_count(
            get_service_config("weekly_report_sync")
        )
    except Exception:
        state["pending_events"] = None
    state["ok"] = state["alive"] and not state["last_error"]
    return state


def _valid_api_key(provided_key: str, expected_key: str) -> bool:
    return bool(
        provided_key
        and expected_key
        and hmac.compare_digest(str(provided_key), str(expected_key))
    )


@app.route("/", methods=["GET"])
def index():
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
                "event_url": "/event/hmailserver",
                "manual_url": "/sync/weekly_report",
                "method": "POST",
                "description": "hMailServer OnAcceptMessage -> 本地队列 -> SeedDMS",
            },
        },
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
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
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            logger.warning("收到无效或空的 Webhook 请求体")
            return "Invalid JSON body", 200

        config = get_service_config("git_redmine_sync")
        msg, code = process_webhook(data, config)
        return msg, code
    except Exception as exc:
        logger.error(f"处理 Webhook 时发生未捕获异常: {exc}", exc_info=True)
        return "Internal error handled", 200


@app.route("/event/hmailserver", methods=["POST"])
def hmailserver_event_handler():
    """EventHandlers.vbs 的轻量通知入口，只唤醒本地队列消费者。"""
    config = get_service_config("weekly_report_sync")
    event_cfg = config.get("hmail_event", {})

    if not event_cfg.get("enabled", True):
        return jsonify({"status": "disabled"}), 403

    allow_remote = bool(event_cfg.get("allow_remote", False))
    remote_addr = request.remote_addr or ""
    if not allow_remote and remote_addr not in {"127.0.0.1", "::1"}:
        logger.warning(f"拒绝非本机 hMailServer Event 请求: {remote_addr}")
        return jsonify({"status": "error", "message": "local requests only"}), 403

    expected_key = str(event_cfg.get("api_key", "")).strip()
    provided_key = request.headers.get("X-API-Key", "")
    if not _valid_api_key(provided_key, expected_key):
        logger.warning("收到未授权的 hMailServer Event 请求")
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    _queue_wakeup.set()
    return jsonify({"status": "accepted"}), 202


@app.route("/sync/weekly_report", methods=["POST"])
def trigger_weekly_report_sync():
    """手工立即消费一次 hMailServer 本地事件队列。"""
    try:
        config = get_service_config("weekly_report_sync")
        if not config:
            return jsonify({"status": "error", "message": "未找到 weekly_report_sync 配置"}), 500

        trigger_cfg = config.get("manual_trigger", {})
        if not trigger_cfg.get("enabled", True):
            return jsonify({"status": "error", "message": "手工触发接口已禁用"}), 403

        expected_key = str(trigger_cfg.get("api_key", "")).strip()
        provided_key = request.headers.get("X-API-Key", "")
        if not _valid_api_key(provided_key, expected_key):
            logger.warning("收到未授权的周报手工触发请求")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        archived = sync_once(config)
        return jsonify({
            "status": "success",
            "message": f"Event 队列处理完成，本次归档 {archived} 个文件",
            "archived_count": archived,
            "pending_events": get_pending_queue_count(config),
        }), 200
    except Exception as exc:
        logger.error(f"手动处理周报 Event 队列异常: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


def start_weekly_report_background_worker():
    """后台消费本地 Event 队列；hMailServer 事件立即唤醒，超时仅用于失败重试。"""
    report_cfg = get_service_config("weekly_report_sync")
    event_cfg = report_cfg.get("hmail_event", {})
    if not event_cfg.get("enabled", True):
        logger.info("hMailServer Event 邮件归档已禁用")
        return

    retry_interval = max(int(event_cfg.get("retry_interval_seconds", 30)), 1)
    logger.info(
        "正在启动 hMailServer Event 队列消费者 (失败重试间隔: %ss, 当前待处理: %s)",
        retry_interval,
        get_pending_queue_count(report_cfg),
    )

    def worker_loop():
        while True:
            with _worker_lock:
                _worker_state["last_started"] = _utc_now()
            try:
                archived = sync_once(report_cfg)
                with _worker_lock:
                    _worker_state["last_completed"] = _utc_now()
                    _worker_state["last_error"] = None
                    _worker_state["last_archived_count"] = archived
            except Exception as exc:
                with _worker_lock:
                    _worker_state["last_completed"] = _utc_now()
                    _worker_state["last_error"] = str(exc)
                logger.error(f"hMailServer Event 队列处理异常: {exc}", exc_info=True)

            _queue_wakeup.wait(timeout=retry_interval)
            _queue_wakeup.clear()

    global _worker_thread
    _worker_thread = threading.Thread(
        target=worker_loop,
        name="HMailEventWorker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("hMailServer Event 队列消费者启动成功")


def main():
    git_cfg = get_service_config("git_redmine_sync")
    server_cfg = git_cfg.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 5000))
    threads = int(server_cfg.get("threads", 4))

    logger.info("==================================================================")
    logger.info("正在启动 DevOps 统一自动化服务 (Unified Automation Server)")
    logger.info("==================================================================")

    start_weekly_report_background_worker()

    logger.info(
        f"HTTP 服务已就绪: http://{host}:{port} "
        "(支持 /webhook、/event/hmailserver、/sync/weekly_report)"
    )
    from waitress import serve

    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
