# -*- coding: utf-8 -*-
"""hMailServer Event 邮件快照解析与附件提取。"""

import email
import hashlib
import logging
import re
import shutil
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Dict, Optional

from services.weekly_report_sync.filename_rules import parse_attachment_destination

logger = logging.getLogger("weekly_report_sync.mail_event")


def decode_str(header_value: Optional[str]) -> str:
    if not header_value:
        return ""
    result = []
    for text, encoding in decode_header(header_value):
        if isinstance(text, bytes):
            try:
                result.append(text.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                result.append(text.decode("gbk", errors="replace"))
        else:
            result.append(str(text))
    return "".join(result)


def extract_email_address(from_header: str) -> str:
    match = re.search(r"[\w.+'-]+@[\w.-]+", from_header or "")
    return match.group(0).lower() if match else (from_header or "").strip().lower()


def extract_recipient_addresses(message: Message) -> set:
    header_values = message.get_all("To", []) + message.get_all("Cc", [])
    return {
        address.strip().lower()
        for _, address in getaddresses(header_values)
        if address.strip()
    }


def has_required_recipients(message: Message, required_recipients: set) -> bool:
    normalized = {address.strip().lower() for address in required_recipients}
    return normalized.issubset(extract_recipient_addresses(message))


def _cleanup_dir(path: Path):
    try:
        if path.exists():
            shutil.rmtree(path)
    except Exception:
        pass


def parse_event_message(
    eml_path: Path,
    filter_rules: Dict[str, Any],
    temp_root: Path,
    processed_message_ids: set,
) -> Optional[Dict[str, Any]]:
    """解析 hMailServer 复制出的 .eml；不符合归档规则时返回 None。"""
    try:
        raw_email = eml_path.read_bytes()
        message = email.message_from_bytes(raw_email)
    except Exception as exc:
        logger.error(f"读取邮件事件文件失败 [{eml_path}]: {exc}", exc_info=True)
        return {
            "parse_failed": True,
            "queue_file": eml_path,
            "attachments": [],
        }

    subject = decode_str(message.get("Subject", ""))
    date_str = message.get("Date", "")
    msg_id = message.get("Message-ID", "").strip()
    unique_key = msg_id or f"sha256:{hashlib.sha256(raw_email).hexdigest()}"

    if unique_key in processed_message_ids:
        return {
            "duplicate": True,
            "unique_key": unique_key,
            "queue_file": eml_path,
            "attachments": [],
        }

    from_raw = decode_str(message.get("From", ""))
    sender_email = extract_email_address(from_raw)
    allowed_senders = {
        str(sender).strip().lower()
        for sender in filter_rules.get("allowed_senders", [])
        if str(sender).strip()
    }
    if allowed_senders and sender_email not in allowed_senders:
        logger.debug(f"事件邮件发件人 [{sender_email}] 不在归档白名单，忽略")
        return None

    recipient_addresses = extract_recipient_addresses(message)
    required_recipients = {
        str(address).strip().lower()
        for address in filter_rules.get("required_recipients", [])
        if str(address).strip()
    }
    if required_recipients and not has_required_recipients(message, required_recipients):
        logger.debug(
            "事件邮件 [%s] 未包含全部目标收件组，忽略；缺少: %s",
            subject,
            ", ".join(sorted(required_recipients - recipient_addresses)),
        )
        return None

    keywords = [
        str(keyword).lower()
        for keyword in filter_rules.get("subject_keywords", ["周报"])
        if str(keyword)
    ]
    subject_matches = not keywords or any(keyword in subject.lower() for keyword in keywords)
    allowed_extensions = {
        str(ext).lower()
        for ext in filter_rules.get("allowed_extensions", [])
        if str(ext)
    }

    message_hash = hashlib.sha256(unique_key.encode("utf-8", errors="replace")).hexdigest()[:24]
    message_temp_dir = temp_root / message_hash
    message_temp_dir.mkdir(parents=True, exist_ok=True)

    attachments = []
    attachment_name_matches = False
    extraction_failed = False

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if not filename:
            continue
        filename = decode_str(filename)
        ext = Path(filename).suffix.lower()
        if allowed_extensions and ext not in allowed_extensions:
            continue
        if parse_attachment_destination(filename):
            attachment_name_matches = True

        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
        filepath = message_temp_dir / safe_filename
        try:
            payload = part.get_payload(decode=True)
            if payload:
                filepath.write_bytes(payload)
                attachments.append(filepath)
        except Exception as exc:
            extraction_failed = True
            logger.error(f"提取附件失败 [{filename}]: {exc}", exc_info=True)

    if not subject_matches and not attachment_name_matches:
        _cleanup_dir(message_temp_dir)
        return None

    logger.info(
        "匹配到 hMailServer 邮件事件: [%s], 发件人: [%s], Message-ID: [%s]",
        subject,
        sender_email,
        unique_key,
    )
    return {
        "unique_key": unique_key,
        "subject": subject,
        "from": from_raw,
        "sender_email": sender_email,
        "recipients": sorted(recipient_addresses),
        "date": date_str,
        "attachments": attachments,
        "temp_dir": message_temp_dir,
        "queue_file": eml_path,
        "extraction_failed": extraction_failed,
    }
