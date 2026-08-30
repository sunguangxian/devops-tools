# -*- coding: utf-8 -*-
"""
hMailServer IMAP 邮件监听与附件解析模块。
负责连接 IMAP 服务器，使用 UID 增量检索新发送/接收邮件，并自动提取附件。
"""

import email
import hashlib
import imaplib
import logging
import re
import shutil
import socket
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.weekly_report_sync.filename_rules import parse_attachment_destination

logger = logging.getLogger("weekly_report_sync.imap_listener")


class MailFetchResult(list):
    """兼容 list 的 IMAP 扫描结果，并携带 UID 游标信息。"""

    def __init__(
        self,
        items=None,
        *,
        max_uid: int = 0,
        failed_uids=None,
        uid_validity: Optional[int] = None,
        scan_ok: bool = True,
        uid_reset: bool = False,
    ):
        super().__init__(items or [])
        self.max_uid = int(max_uid or 0)
        self.failed_uids = list(failed_uids or [])
        self.uid_validity = uid_validity
        self.scan_ok = bool(scan_ok)
        self.uid_reset = bool(uid_reset)


def decode_str(header_value: Optional[str]) -> str:
    """解码邮件头部字段（如 Subject、From），防止乱码。"""
    if not header_value:
        return ""
    decoded_fragments = decode_header(header_value)
    result = []
    for text, encoding in decoded_fragments:
        if isinstance(text, bytes):
            try:
                result.append(text.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                result.append(text.decode("gbk", errors="replace"))
        else:
            result.append(str(text))
    return "".join(result)


def extract_email_address(from_header: str) -> str:
    """从 From 头中提取纯邮箱地址。"""
    match = re.search(r"[\w\.-]+@[\w\.-]+", from_header)
    return match.group(0).lower() if match else from_header.lower()


def extract_recipient_addresses(message: Message) -> set:
    """提取 To 和 CC 中的全部收件人地址。"""
    header_values = message.get_all("To", []) + message.get_all("Cc", [])
    return {
        address.strip().lower()
        for _, address in getaddresses(header_values)
        if address.strip()
    }


def has_required_recipients(message: Message, required_recipients: set) -> bool:
    """判断 To/CC 是否包含全部最终分发收件人。"""
    normalized_required = {address.strip().lower() for address in required_recipients}
    return normalized_required.issubset(extract_recipient_addresses(message))


def _create_imap_client(host: str, port: int, use_ssl: bool, timeout: float):
    """创建带连接超时的 IMAP 客户端，兼容不支持 timeout 参数的 Python 版本。"""
    if use_ssl:
        class TimeoutIMAP4SSL(imaplib.IMAP4_SSL):
            def _create_socket(self, *args, **kwargs):
                sock = socket.create_connection((self.host, self.port), timeout=timeout)
                return self.ssl_context.wrap_socket(sock, server_hostname=self.host)

        return TimeoutIMAP4SSL(host, port)

    class TimeoutIMAP4(imaplib.IMAP4):
        def _create_socket(self, *args, **kwargs):
            return socket.create_connection((self.host, self.port), timeout=timeout)

    return TimeoutIMAP4(host, port)


def _get_uid_validity(mail: imaplib.IMAP4) -> Optional[int]:
    """读取当前邮箱目录 UIDVALIDITY，用于检测 UID 序列是否被服务器重置。"""
    try:
        _, values = mail.response("UIDVALIDITY")
        if not values:
            return None
        raw = values[-1]
        text = raw.decode("ascii", errors="ignore") if isinstance(raw, bytes) else str(raw)
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else None
    except Exception:
        return None


def _cleanup_message_dir(message_dir: Path):
    try:
        if message_dir.exists():
            shutil.rmtree(message_dir)
    except Exception:
        pass


class IMAPMailListener:
    """IMAP 邮件监听器。"""

    def __init__(self, config: Dict):
        self.host = config.get("imap_host", "127.0.0.1")
        self.port = int(config.get("imap_port", 143))
        self.use_ssl = bool(config.get("use_ssl", False))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.folder = config.get("folder", "Sent")
        self.timeout = float(config.get("timeout_seconds", 10))
        self.initial_scan_limit = max(int(config.get("initial_scan_limit", 30)), 0)

    def connect(self) -> Optional[imaplib.IMAP4]:
        """连接并登录 IMAP 服务器。"""
        try:
            mail = _create_imap_client(
                self.host,
                self.port,
                self.use_ssl,
                self.timeout,
            )
            if getattr(mail, "sock", None) is not None:
                mail.sock.settimeout(self.timeout)
            mail.login(self.username, self.password)
            return mail
        except Exception as exc:
            logger.error(
                f"IMAP 连接/登录失败 [{self.host}:{self.port}]: {exc}",
                exc_info=True,
            )
            return None

    def fetch_unprocessed_emails(
        self,
        processed_message_ids: set,
        filter_rules: Dict[str, Any],
        temp_dir: Path,
        last_uid: int = 0,
        expected_uid_validity: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        使用 IMAP UID 增量拉取并解析符合规则且未处理过的邮件。

        返回值保持 list 兼容，同时携带 max_uid、failed_uids、uid_validity、scan_ok 等属性。
        """
        mail = self.connect()
        if not mail:
            return MailFetchResult(scan_ok=False, max_uid=last_uid)

        results = MailFetchResult(max_uid=last_uid)
        try:
            status, _ = mail.select(f'"{self.folder}"', readonly=True)
            if status != "OK":
                status, _ = mail.select(self.folder, readonly=True)
            if status != "OK":
                logger.warning(f"无法打开邮件文件夹 [{self.folder}]，尝试打开 [INBOX]")
                status, _ = mail.select("INBOX", readonly=True)
            if status != "OK":
                logger.error(f"无法打开邮件文件夹 [{self.folder}] 或 [INBOX]")
                results.scan_ok = False
                return results

            uid_validity = _get_uid_validity(mail)
            results.uid_validity = uid_validity

            start_uid = max(int(last_uid or 0) + 1, 1)
            bootstrap_scan = int(last_uid or 0) <= 0 and expected_uid_validity is None
            if (
                expected_uid_validity is not None
                and uid_validity is not None
                and int(expected_uid_validity) != int(uid_validity)
            ):
                logger.warning(
                    "检测到 IMAP UIDVALIDITY 变化 (%s -> %s)，重新建立 UID 游标",
                    expected_uid_validity,
                    uid_validity,
                )
                start_uid = 1
                results.uid_reset = True
                bootstrap_scan = True

            status, data = mail.uid("search", None, "UID", f"{start_uid}:*")
            if status != "OK":
                logger.error(f"IMAP UID SEARCH 失败，起始 UID: {start_uid}")
                results.scan_ok = False
                return results

            uid_numbers = []
            for raw_uid in (data[0].split() if data and data[0] else []):
                try:
                    uid_numbers.append(int(raw_uid))
                except (TypeError, ValueError):
                    logger.warning(f"忽略无法解析的 IMAP UID: {raw_uid!r}")

            uid_numbers.sort()
            if uid_numbers:
                results.max_uid = uid_numbers[-1]

            # 第一次建立状态或 UIDVALIDITY 重置时，仅回看最近 N 封，避免把多年历史邮件重新归档。
            if (
                bootstrap_scan
                and self.initial_scan_limit > 0
                and len(uid_numbers) > self.initial_scan_limit
            ):
                logger.info(
                    "首次建立 IMAP UID 游标，仅回看最近 %s 封邮件（总邮件数: %s）",
                    self.initial_scan_limit,
                    len(uid_numbers),
                )
                uid_numbers = uid_numbers[-self.initial_scan_limit:]

            keywords = [
                kw.lower()
                for kw in filter_rules.get("subject_keywords", ["周报"])
            ]
            allowed_senders = [
                sender.lower()
                for sender in filter_rules.get("allowed_senders", [])
            ]
            required_recipients = {
                address.lower()
                for address in filter_rules.get("required_recipients", [])
            }
            allowed_extensions = [
                ext.lower()
                for ext in filter_rules.get("allowed_extensions", [])
            ]

            temp_dir.mkdir(parents=True, exist_ok=True)

            # 必须按 UID 从小到大处理，便于失败时安全回退游标。
            for uid in uid_numbers:
                try:
                    status, msg_data = mail.uid("fetch", str(uid), "(RFC822)")
                    if status != "OK" or not msg_data:
                        results.failed_uids.append(uid)
                        logger.warning(f"读取 IMAP UID={uid} 失败，将在下一轮重试")
                        continue

                    raw_email = None
                    for part in msg_data:
                        if isinstance(part, tuple) and len(part) >= 2:
                            raw_email = part[1]
                            break
                    if not raw_email:
                        results.failed_uids.append(uid)
                        logger.warning(f"IMAP UID={uid} 未返回 RFC822 正文，将在下一轮重试")
                        continue

                    msg = email.message_from_bytes(raw_email)
                    msg_id = msg.get("Message-ID", "").strip()
                    subject = decode_str(msg.get("Subject", ""))
                    date_str = msg.get("Date", "")
                    unique_key = msg_id if msg_id else f"{date_str}_{subject}"

                    if unique_key in processed_message_ids:
                        continue

                    subject_lower = subject.lower()
                    subject_matches = not keywords or any(
                        keyword in subject_lower for keyword in keywords
                    )

                    from_raw = decode_str(msg.get("From", ""))
                    sender_email = extract_email_address(from_raw)
                    if allowed_senders and sender_email not in allowed_senders:
                        logger.info(f"发件人 [{sender_email}] 不在白名单列表中，跳过")
                        continue

                    recipient_addresses = extract_recipient_addresses(msg)
                    missing_recipients = required_recipients - recipient_addresses
                    if not has_required_recipients(msg, required_recipients):
                        logger.info(
                            "邮件 [%s] 尚未发送给全部研发组，缺少: %s，跳过",
                            subject,
                            ", ".join(sorted(missing_recipients)),
                        )
                        continue

                    logger.info(
                        "匹配到周报邮件: [%s], 发件人: [%s], Message-ID: [%s], UID: %s",
                        subject,
                        from_raw,
                        unique_key,
                        uid,
                    )

                    # 每封邮件使用独立临时目录，避免不同邮件中的同名附件互相覆盖。
                    message_hash = hashlib.sha256(
                        unique_key.encode("utf-8", errors="replace")
                    ).hexdigest()[:24]
                    message_temp_dir = temp_dir / message_hash
                    message_temp_dir.mkdir(parents=True, exist_ok=True)

                    attachments = []
                    attachment_name_matches = False
                    extraction_failed = False
                    for part in msg.walk():
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
                            logger.info(f"附件 [{filename}] 格式不在白名单内，跳过")
                            continue
                        if parse_attachment_destination(filename):
                            attachment_name_matches = True

                        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
                        filepath = message_temp_dir / safe_filename
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                with open(filepath, "wb") as file_obj:
                                    file_obj.write(payload)
                                attachments.append(filepath)
                                logger.info(
                                    f"成功提取周报附件: {filename} ({len(payload)} bytes)"
                                )
                        except Exception as exc:
                            extraction_failed = True
                            logger.error(
                                f"提取保存附件出错 [{filename}]: {exc}",
                                exc_info=True,
                            )

                    if not subject_matches and not attachment_name_matches:
                        logger.info(
                            f"邮件 [{subject}] 的主题和附件名均不符合周会归档规则，跳过"
                        )
                        _cleanup_message_dir(message_temp_dir)
                        continue

                    results.append({
                        "uid": uid,
                        "unique_key": unique_key,
                        "subject": subject,
                        "from": from_raw,
                        "sender_email": sender_email,
                        "recipients": sorted(recipient_addresses),
                        "date": date_str,
                        "attachments": attachments,
                        "temp_dir": message_temp_dir,
                        "extraction_failed": extraction_failed,
                    })
                except Exception as exc:
                    results.failed_uids.append(uid)
                    logger.error(
                        f"解析 IMAP UID={uid} 邮件发生异常，将在下一轮重试: {exc}",
                        exc_info=True,
                    )

            return results
        except Exception as exc:
            results.scan_ok = False
            logger.error(f"检索邮件过程中发生异常: {exc}", exc_info=True)
            return results
        finally:
            try:
                mail.logout()
            except Exception:
                pass
