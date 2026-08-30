# -*- coding: utf-8 -*-
"""
周报邮件监听与 SeedDMS 自动归档核心调度模块
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Any

from common.config_loader import get_project_root
from services.weekly_report_sync.seeddms_client import SeedDMSClient
from services.weekly_report_sync.imap_listener import IMAPMailListener

logger = logging.getLogger("weekly_report_sync.core")


def load_processed_history(history_file_path: Path) -> Set[str]:
    """
    加载已处理邮件的历史记录 (Message-ID 集合)
    """
    if not history_file_path.exists():
        return set()
    try:
        with open(history_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception as e:
        logger.warning(f"读取历史记录文件出错 [{history_file_path}]: {e}")
        return set()


def save_processed_history(history_file_path: Path, processed_set: Set[str]):
    """
    持久化保存已处理邮件的 Message-ID 集合
    """
    try:
        history_file_path.parent.mkdir(parents=True, exist_ok=True)
        # 只保留最新的 2000 条记录，防止文件无限膨胀
        save_list = list(processed_set)[-2000:]
        with open(history_file_path, "w", encoding="utf-8") as f:
            json.dump(save_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存已处理记录失败 [{history_file_path}]: {e}")


def get_date_context() -> Dict[str, str]:
    """获取当前年月日及周数上下文"""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return {
        "year": str(year),
        "week": f"{week:02d}",
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_template(template_str: str, context: Dict[str, str]) -> str:
    """动态替换命名模板"""
    if not template_str:
        return ""
    result = template_str
    for k, v in context.items():
        result = result.replace(f"{{{k}}}", str(v))
    return result


def sync_once(config: Dict[str, Any]) -> int:
    """
    执行单次邮件扫描并同步至 SeedDMS
    :return: 成功备份归档的文件数
    """
    storage_cfg = config.get("storage", {})
    temp_dir = get_project_root() / storage_cfg.get("temp_dir", "./data/temp_attachments")
    history_file = get_project_root() / storage_cfg.get("history_file", "./data/processed_emails.json")

    processed_ids = load_processed_history(history_file)

    # 1. 连接 IMAP 抓取新周报邮件及附件
    listener = IMAPMailListener(config.get("mail_monitor", {}))
    new_emails = listener.fetch_unprocessed_emails(
        processed_message_ids=processed_ids,
        filter_rules=config.get("filter_rules", {}),
        temp_dir=temp_dir,
    )

    if not new_emails:
        logger.debug("未发现需要归档的新周报邮件")
        return 0

    logger.info(f"发现 {len(new_emails)} 封待归档的周报邮件，开始备份到 SeedDMS ...")

    # 2. 初始化 SeedDMS 客户端
    seeddms_cfg = config.get("seeddms", {})
    dms_client = SeedDMSClient(seeddms_cfg)

    doc_name_tmpl = seeddms_cfg.get("document_name_template", "【周报】{subject} - {sender}")
    comment_tmpl = seeddms_cfg.get("comment", "由邮件监控自动备份")

    archived_count = 0

    for item in new_emails:
        unique_key = item["unique_key"]
        subject = item["subject"]
        sender = item["sender_email"]
        attachments = item.get("attachments", [])

        if not attachments:
            logger.info(f"邮件 [{subject}] 未检测到有效周报附件，标记为已处理")
            processed_ids.add(unique_key)
            continue

        email_all_archived = True
        for att_path in attachments:
            ctx = get_date_context()
            ctx.update({
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
                year=ctx.get("year"),
            )

            if success:
                archived_count += 1
                # 上传成功后清理本地临时文件
                try:
                    att_path.unlink()
                except Exception:
                    pass
            else:
                email_all_archived = False
                logger.error(f"附件 [{att_path.name}] 上传 SeedDMS 失败")

        if email_all_archived:
            processed_ids.add(unique_key)

    # 保存处理状态
    save_processed_history(history_file, processed_ids)
    logger.info(f"本轮同步完成，共成功备份 {archived_count} 个周报附件到 SeedDMS")
    return archived_count


def run_daemon(config: Dict[str, Any]):
    """
    后台守护进程模式：定时循环轮询
    """
    interval = int(config.get("mail_monitor", {}).get("poll_interval_seconds", 60))
    logger.info(f"启动周报邮件监控后台守护进程 (轮询间隔: {interval} 秒)...")

    while True:
        try:
            sync_once(config)
        except Exception as e:
            logger.error(f"周报同步轮询发生异常: {e}", exc_info=True)

        time.sleep(interval)
