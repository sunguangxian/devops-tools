# -*- coding: utf-8 -*-
"""hMailServer Event 邮件队列与 SeedDMS 自动归档核心调度。"""

import hashlib
import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from common.config_loader import get_project_root
from services.weekly_report_sync.filename_rules import parse_attachment_destination
from services.weekly_report_sync.mail_event import parse_event_message
from services.weekly_report_sync.mail_notifier import send_archive_notification
from services.weekly_report_sync.seeddms_client import SeedDMSClient

logger = logging.getLogger("weekly_report_sync.core")

STATE_VERSION = 3
MAX_PROCESSED_MESSAGES = 2000
_sync_lock = threading.Lock()


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else get_project_root() / path


def get_queue_dir(config: Dict[str, Any]) -> Path:
    return _resolve_project_path(
        config.get("hmail_event", {}).get("queue_dir", "./data/mail_event_queue")
    )


def get_pending_queue_count(config: Dict[str, Any]) -> int:
    queue_dir = get_queue_dir(config)
    if not queue_dir.exists():
        return 0
    return sum(1 for path in queue_dir.glob("*.eml") if path.is_file())


def _empty_sync_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "processed_messages": {},
        "attachments": {},
        "notifications": {},
    }


def load_sync_state(history_file_path: Path) -> Dict[str, Any]:
    """加载 Event 模式 JSON 状态；不兼容旧 IMAP UID 状态。"""
    if not history_file_path.exists():
        return _empty_sync_state()

    try:
        data = json.loads(history_file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            logger.warning(
                "同步状态文件不是 Event 模式版本，将忽略旧状态: %s",
                history_file_path,
            )
            return _empty_sync_state()

        state = _empty_sync_state()
        for key in ("processed_messages", "attachments", "notifications"):
            value = data.get(key, {})
            if isinstance(value, dict):
                state[key] = value
        return state
    except Exception as exc:
        logger.warning(f"读取同步状态文件出错 [{history_file_path}]: {exc}")
        return _empty_sync_state()


def save_sync_state(history_file_path: Path, state: Dict[str, Any]):
    """原子持久化 Message-ID、附件重试和通知状态。"""
    try:
        history_file_path.parent.mkdir(parents=True, exist_ok=True)
        processed = state.setdefault("processed_messages", {})
        if len(processed) > MAX_PROCESSED_MESSAGES:
            ordered = sorted(
                processed.items(),
                key=lambda item: (item[1] or {}).get("processed_at", ""),
            )[-MAX_PROCESSED_MESSAGES:]
            state["processed_messages"] = dict(ordered)
            retained = set(state["processed_messages"])
            state["notifications"] = {
                key: value
                for key, value in state.setdefault("notifications", {}).items()
                if key in retained
            }

        state["version"] = STATE_VERSION
        temp_path = history_file_path.with_suffix(history_file_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(history_file_path)
    except Exception as exc:
        logger.error(f"保存同步状态失败 [{history_file_path}]: {exc}", exc_info=True)


def get_date_context() -> Dict[str, str]:
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return {
        "year": str(year),
        "week": f"{week:02d}",
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_template(template_str: str, context: Dict[str, str]) -> str:
    result = template_str or ""
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def _file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_processed(state: Dict[str, Any], unique_key: str):
    state.setdefault("processed_messages", {})[unique_key] = {
        "processed_at": _now_text(),
    }


def _safe_unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
    except Exception:
        pass


def _cleanup_message_temp(item: Dict[str, Any], temp_root: Path):
    raw_path = item.get("temp_dir")
    if not raw_path:
        return
    try:
        message_dir = Path(raw_path).resolve()
        root = temp_root.resolve()
        if message_dir != root and root in message_dir.parents and message_dir.exists():
            shutil.rmtree(message_dir)
    except Exception as exc:
        logger.warning(f"清理邮件临时目录失败 [{raw_path}]: {exc}")


def _uploaded_items(email_attachment_state: Dict[str, Any]):
    items = []
    seen = set()
    for record in email_attachment_state.values():
        if not isinstance(record, dict) or record.get("status") != "uploaded":
            continue
        key = (record.get("filename"), record.get("year"), record.get("category"))
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "filename": record.get("filename", ""),
            "year": record.get("year", ""),
            "category": record.get("category", ""),
        })
    return items


def sync_once(config: Dict[str, Any]) -> int:
    """处理 hMailServer Event 队列中的全部待处理 .eml。"""
    with _sync_lock:
        return _sync_once(config)


def _sync_once(config: Dict[str, Any]) -> int:
    storage_cfg = config.get("storage", {})
    queue_dir = get_queue_dir(config)
    temp_dir = _resolve_project_path(
        storage_cfg.get("temp_dir", "./data/temp_attachments")
    )
    history_file = _resolve_project_path(
        storage_cfg.get("history_file", "./data/processed_emails.json")
    )

    queue_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    state = load_sync_state(history_file)
    processed_ids = set(state.get("processed_messages", {}))
    queue_files = sorted(
        (path for path in queue_dir.glob("*.eml") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )

    if not queue_files:
        logger.debug("hMailServer Event 队列为空")
        return 0

    dms_client = SeedDMSClient(config.get("seeddms", {}))
    seeddms_cfg = config.get("seeddms", {})
    doc_name_tmpl = seeddms_cfg.get("document_name_template", "{filename}")
    comment_tmpl = seeddms_cfg.get("comment", "由 hMailServer Event 自动归档")
    notification_cfg = config.get("notification", {})
    archived_count = 0

    logger.info("发现 %s 个 hMailServer 邮件事件，开始处理", len(queue_files))

    for eml_path in queue_files:
        item = parse_event_message(
            eml_path=eml_path,
            filter_rules=config.get("filter_rules", {}),
            temp_root=temp_dir,
            processed_message_ids=processed_ids,
        )

        if item is None:
            _safe_unlink(eml_path)
            continue
        if item.get("parse_failed"):
            logger.error(f"保留无法解析的邮件事件，等待后续重试: [{eml_path.name}]")
            continue
        if item.get("duplicate"):
            _safe_unlink(eml_path)
            continue

        unique_key = item["unique_key"]
        subject = item["subject"]
        sender = item["sender_email"]
        attachments = item.get("attachments", [])
        email_all_archived = not bool(item.get("extraction_failed", False))
        email_attachment_state = state.setdefault("attachments", {}).setdefault(
            unique_key, {}
        )

        if not attachments:
            if email_all_archived:
                logger.info(f"邮件 [{subject}] 无有效归档附件，事件处理完成")
                _mark_processed(state, unique_key)
                processed_ids.add(unique_key)
                state["attachments"].pop(unique_key, None)
                _cleanup_message_temp(item, temp_dir)
                _safe_unlink(eml_path)
                save_sync_state(history_file, state)
            continue

        for att_path in attachments:
            att_path = Path(att_path)
            destination = parse_attachment_destination(att_path.name)
            if not destination:
                email_all_archived = False
                logger.error(
                    f"附件名无法解析年份和类别，保留事件等待处理: [{att_path.name}]"
                )
                continue

            attachment_year, category = destination
            try:
                content_sha256 = _file_sha256(att_path)
            except Exception as exc:
                email_all_archived = False
                logger.error(f"计算附件 SHA256 失败 [{att_path.name}]: {exc}")
                continue

            attachment_key = f"{att_path.name}|{content_sha256}"
            existing = email_attachment_state.get(attachment_key, {})
            if existing.get("status") == "uploaded":
                logger.info(f"附件 [{att_path.name}] 已成功归档，本轮跳过重复上传")
                _safe_unlink(att_path)
                continue

            ctx = get_date_context()
            ctx.update({
                "year": attachment_year,
                "category": category,
                "subject": subject,
                "sender": sender,
                "filename": att_path.name,
                "basename": att_path.stem,
            })
            doc_name = render_template(doc_name_tmpl, ctx)
            comment = render_template(comment_tmpl, ctx)

            logger.info(f"正在上传附件 [{att_path.name}] 到 SeedDMS (标题: {doc_name}) ...")
            success = dms_client.upload_document(
                file_path=att_path,
                doc_name=doc_name,
                comment=comment,
                year=attachment_year,
                category=category,
            )

            retry_count = int(existing.get("retry_count", 0) or 0)
            if success:
                archived_count += 1
                email_attachment_state[attachment_key] = {
                    "filename": att_path.name,
                    "sha256": content_sha256,
                    "year": attachment_year,
                    "category": category,
                    "status": "uploaded",
                    "retry_count": retry_count,
                    "updated_at": _now_text(),
                }
                # 每个附件成功后立即保存，进程异常退出也不会重复上传。
                save_sync_state(history_file, state)
                _safe_unlink(att_path)
            else:
                email_all_archived = False
                email_attachment_state[attachment_key] = {
                    "filename": att_path.name,
                    "sha256": content_sha256,
                    "year": attachment_year,
                    "category": category,
                    "status": "failed",
                    "retry_count": retry_count + 1,
                    "updated_at": _now_text(),
                }
                save_sync_state(history_file, state)
                logger.error(f"附件 [{att_path.name}] 上传 SeedDMS 失败，将保留事件重试")

        if not email_all_archived:
            continue

        archived_items = _uploaded_items(email_attachment_state)
        notification_status = "disabled"
        if notification_cfg.get("enabled", False) and archived_items:
            notified = send_archive_notification(
                notification_config=notification_cfg,
                source_subject=subject,
                archived_items=archived_items,
            )
            notification_status = "sent" if notified else "failed"

        state.setdefault("notifications", {})[unique_key] = {
            "status": notification_status,
            "updated_at": _now_text(),
        }
        _mark_processed(state, unique_key)
        processed_ids.add(unique_key)
        state["attachments"].pop(unique_key, None)
        _cleanup_message_temp(item, temp_dir)
        _safe_unlink(eml_path)
        save_sync_state(history_file, state)

    logger.info(
        "本轮 Event 队列处理完成，共成功归档 %s 个附件，待处理事件 %s 个",
        archived_count,
        get_pending_queue_count(config),
    )
    return archived_count


def run_daemon(config: Dict[str, Any]):
    """独立运行时定期消费本地 Event 队列；不访问 IMAP。"""
    interval = int(config.get("hmail_event", {}).get("retry_interval_seconds", 30))
    logger.info(f"启动 hMailServer Event 队列消费者 (失败重试间隔: {interval} 秒)...")
    while True:
        try:
            sync_once(config)
        except Exception as exc:
            logger.error(f"Event 队列处理异常: {exc}", exc_info=True)
        time.sleep(interval)
