# -*- coding: utf-8 -*-
"""周报邮件监听与 SeedDMS 自动归档核心调度模块。"""

import hashlib
import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Set

from common.config_loader import get_project_root
from services.weekly_report_sync.filename_rules import parse_attachment_destination
from services.weekly_report_sync.imap_listener import IMAPMailListener
from services.weekly_report_sync.mail_notifier import send_archive_notification
from services.weekly_report_sync.seeddms_client import SeedDMSClient

logger = logging.getLogger("weekly_report_sync.core")

STATE_VERSION = 2
MAX_PROCESSED_MESSAGES = 2000

# 统一服务允许后台轮询和 HTTP 手动触发同时调用 sync_once。
# 使用进程内互斥锁，避免重复读取/覆盖处理历史和临时附件。
_sync_lock = threading.Lock()


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _empty_sync_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_uid": 0,
        "uid_validity": None,
        "processed_messages": {},
        "attachments": {},
        "notifications": {},
    }


def load_sync_state(history_file_path: Path) -> Dict[str, Any]:
    """加载同步状态，并自动兼容旧版仅保存 Message-ID 列表的 JSON 文件。"""
    state = _empty_sync_state()
    if not history_file_path.exists():
        return state

    try:
        with open(history_file_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)

        # v1: ["message-id-1", "message-id-2", ...]
        if isinstance(data, list):
            state["processed_messages"] = {
                str(message_id): {"processed_at": ""}
                for message_id in data
                if message_id
            }
            logger.info(
                "检测到旧版邮件处理历史，将自动迁移为 UID/附件级 JSON 状态格式"
            )
            return state

        if not isinstance(data, dict):
            return state

        state["last_uid"] = int(data.get("last_uid", 0) or 0)
        state["uid_validity"] = data.get("uid_validity")

        processed = data.get("processed_messages", {})
        if isinstance(processed, list):
            processed = {
                str(message_id): {"processed_at": ""}
                for message_id in processed
                if message_id
            }
        if isinstance(processed, dict):
            state["processed_messages"] = processed

        attachments = data.get("attachments", {})
        if isinstance(attachments, dict):
            state["attachments"] = attachments

        notifications = data.get("notifications", {})
        if isinstance(notifications, dict):
            state["notifications"] = notifications

        return state
    except Exception as exc:
        logger.warning(f"读取同步状态文件出错 [{history_file_path}]: {exc}")
        return state


def save_sync_state(history_file_path: Path, state: Dict[str, Any]):
    """原子方式持久化 UID、Message-ID、附件重试和通知状态。"""
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
            notifications = state.setdefault("notifications", {})
            state["notifications"] = {
                key: value
                for key, value in notifications.items()
                if key in retained
            }

        state["version"] = STATE_VERSION
        temp_path = history_file_path.with_suffix(history_file_path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            json.dump(state, file_obj, ensure_ascii=False, indent=2)
        temp_path.replace(history_file_path)
    except Exception as exc:
        logger.error(f"保存同步状态失败 [{history_file_path}]: {exc}", exc_info=True)


# 保留旧函数名，避免现有调用方或测试依赖旧接口。
def load_processed_history(history_file_path: Path) -> Set[str]:
    return set(load_sync_state(history_file_path).get("processed_messages", {}))


def save_processed_history(history_file_path: Path, processed_set: Set[str]):
    state = load_sync_state(history_file_path)
    state["processed_messages"] = {
        str(message_id): {"processed_at": _now_text()}
        for message_id in processed_set
    }
    save_sync_state(history_file_path, state)


def get_date_context() -> Dict[str, str]:
    """获取当前年月日及周数上下文。"""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return {
        "year": str(year),
        "week": f"{week:02d}",
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_template(template_str: str, context: Dict[str, str]) -> str:
    """动态替换命名模板。"""
    if not template_str:
        return ""
    result = template_str
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


def _cleanup_message_temp(item: Dict[str, Any], temp_root: Path):
    """只清理监听器创建的单邮件子目录，避免误删配置的临时根目录。"""
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
    """执行单次邮件扫描并同步至 SeedDMS。"""
    with _sync_lock:
        return _sync_once(config)


def _sync_once(config: Dict[str, Any]) -> int:
    """在调用方持有同步锁时执行一次邮件扫描与归档。"""
    storage_cfg = config.get("storage", {})
    temp_dir = get_project_root() / storage_cfg.get(
        "temp_dir", "./data/temp_attachments"
    )
    history_file = get_project_root() / storage_cfg.get(
        "history_file", "./data/processed_emails.json"
    )

    state = load_sync_state(history_file)
    processed_ids = set(state.get("processed_messages", {}))
    last_uid = int(state.get("last_uid", 0) or 0)
    expected_uid_validity = state.get("uid_validity")

    listener = IMAPMailListener(config.get("mail_monitor", {}))
    new_emails = listener.fetch_unprocessed_emails(
        processed_message_ids=processed_ids,
        filter_rules=config.get("filter_rules", {}),
        temp_dir=temp_dir,
        last_uid=last_uid,
        expected_uid_validity=expected_uid_validity,
    )

    scan_ok = bool(getattr(new_emails, "scan_ok", True))
    uid_reset = bool(getattr(new_emails, "uid_reset", False))
    current_uid_validity = getattr(new_emails, "uid_validity", None)
    max_uid = int(getattr(new_emails, "max_uid", last_uid) or last_uid)
    failed_uids = {
        int(uid)
        for uid in getattr(new_emails, "failed_uids", [])
        if uid is not None
    }

    if uid_reset:
        last_uid = 0
        state["last_uid"] = 0
    if current_uid_validity is not None:
        state["uid_validity"] = current_uid_validity

    if not new_emails:
        if scan_ok:
            if failed_uids:
                state["last_uid"] = max(0, min(failed_uids) - 1)
            else:
                state["last_uid"] = max(last_uid, max_uid)
        save_sync_state(history_file, state)
        logger.debug("未发现需要归档的新周报邮件")
        return 0

    logger.info(f"发现 {len(new_emails)} 封待归档的周报邮件，开始备份到 SeedDMS ...")

    seeddms_cfg = config.get("seeddms", {})
    dms_client = SeedDMSClient(seeddms_cfg)
    doc_name_tmpl = seeddms_cfg.get("document_name_template", "{filename}")
    comment_tmpl = seeddms_cfg.get("comment", "由邮件监控自动备份")

    archived_count = 0
    notification_cfg = config.get("notification", {})
    mail_cfg = config.get("mail_monitor", {})
    processing_failed_uids = set()

    for item in new_emails:
        unique_key = item["unique_key"]
        subject = item["subject"]
        sender = item["sender_email"]
        attachments = item.get("attachments", [])
        uid = item.get("uid")
        email_all_archived = not bool(item.get("extraction_failed", False))

        email_attachment_state = state.setdefault("attachments", {}).setdefault(
            unique_key, {}
        )

        if not attachments:
            if email_all_archived:
                logger.info(f"邮件 [{subject}] 未检测到有效周报附件，标记为已处理")
                _mark_processed(state, unique_key)
                state["attachments"].pop(unique_key, None)
                _cleanup_message_temp(item, temp_dir)
                save_sync_state(history_file, state)
            elif uid is not None:
                processing_failed_uids.add(int(uid))
            continue

        for att_path in attachments:
            att_path = Path(att_path)
            destination = parse_attachment_destination(att_path.name)
            if not destination:
                email_all_archived = False
                logger.error(
                    f"附件名无法解析年份和类别，保留邮件等待处理: [{att_path.name}]"
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
                logger.info(
                    f"附件 [{att_path.name}] 已在前一轮成功归档，本轮跳过重复上传"
                )
                try:
                    att_path.unlink()
                except Exception:
                    pass
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
                # 上传成功立即落盘：即使进程随后退出，下轮也不会重复上传该附件。
                save_sync_state(history_file, state)
                try:
                    att_path.unlink()
                except Exception:
                    pass
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
                logger.error(f"附件 [{att_path.name}] 上传 SeedDMS 失败")
                save_sync_state(history_file, state)

        if email_all_archived:
            archived_items = _uploaded_items(email_attachment_state)
            notification_status = "disabled"
            if notification_cfg.get("enabled", False) and archived_items:
                notified = send_archive_notification(
                    mail_config=mail_cfg,
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
            state["attachments"].pop(unique_key, None)
            _cleanup_message_temp(item, temp_dir)
            save_sync_state(history_file, state)
        elif uid is not None:
            processing_failed_uids.add(int(uid))

    blocking_uids = failed_uids | processing_failed_uids
    if scan_ok:
        if blocking_uids:
            # 游标只推进到最早失败 UID 的前一封；后续邮件即使被重新扫描，
            # Message-ID 和附件 SHA256 状态也会阻止重复归档。
            state["last_uid"] = max(0, min(blocking_uids) - 1)
        else:
            state["last_uid"] = max(last_uid, max_uid)

    save_sync_state(history_file, state)
    logger.info(
        "本轮同步完成，共成功备份 %s 个周报附件到 SeedDMS，IMAP last_uid=%s",
        archived_count,
        state.get("last_uid", 0),
    )
    return archived_count


def run_daemon(config: Dict[str, Any]):
    """后台守护进程模式：定时循环轮询。"""
    interval = int(config.get("mail_monitor", {}).get("poll_interval_seconds", 60))
    logger.info(f"启动周报邮件监控后台守护进程 (轮询间隔: {interval} 秒)...")

    while True:
        try:
            sync_once(config)
        except Exception as exc:
            logger.error(f"周报同步轮询发生异常: {exc}", exc_info=True)
        time.sleep(interval)
