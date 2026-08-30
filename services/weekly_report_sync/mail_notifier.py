# -*- coding: utf-8 -*-
"""周会纪要归档结果邮件通知。"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List

logger = logging.getLogger("weekly_report_sync.mail_notifier")


def send_archive_notification(
    notification_config: Dict[str, Any],
    source_subject: str,
    archived_items: List[Dict[str, str]],
) -> bool:
    """通过 hMailServer SMTP 发送归档结果通知。"""
    if not notification_config.get("enabled", False) or not archived_items:
        return True

    username = notification_config.get("username", "")
    password = notification_config.get("password", "")
    recipient = notification_config.get("recipient") or username
    smtp_host = notification_config.get("smtp_host", "127.0.0.1")
    smtp_port = int(notification_config.get("smtp_port", 25))
    use_ssl = bool(notification_config.get("use_ssl", False))
    use_starttls = bool(notification_config.get("starttls", False))

    if not username or not password or not recipient:
        logger.error("归档通知配置不完整：缺少 SMTP 账号、密码或通知收件人")
        return False

    message = EmailMessage()
    message["From"] = username
    message["To"] = recipient
    message["Subject"] = notification_config.get("subject", "周会纪要自动归档完成")

    lines = [
        "周会纪要已成功归档到 SeedDMS。",
        "",
        f"原邮件主题：{source_subject}",
        f"归档附件数：{len(archived_items)}",
        "",
        "归档明细：",
    ]
    for item in archived_items:
        lines.append(f"- {item['filename']} -> {item['year']}年/{item['category']}")
    message.set_content("\n".join(lines))

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    try:
        with smtp_class(smtp_host, smtp_port, timeout=20) as smtp:
            if use_starttls and not use_ssl:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        logger.info(f"归档完成通知已发送至 [{recipient}]")
        return True
    except Exception as exc:
        logger.error(f"发送归档完成通知失败 [{recipient}]: {exc}", exc_info=True)
        return False
