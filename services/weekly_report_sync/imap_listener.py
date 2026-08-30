# -*- coding: utf-8 -*-
"""
hMailServer IMAP 邮件监听与附件解析模块
负责连接 IMAP 服务器，检索符合周报规则的新发送/接收邮件，并自动提取附件。
"""

import os
import re
import email
import imaplib
import logging
from email.header import decode_header
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("weekly_report_sync.imap_listener")


def decode_str(header_value: Optional[str]) -> str:
    """
    解码邮件头部字段（如 Subject、From），防止乱码
    """
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
    """
    从 From 头中提取出纯邮箱地址
    例如: "张三 <zhangsan@company.com>" -> "zhangsan@company.com"
    """
    match = re.search(r"[\w\.-]+@[\w\.-]+", from_header)
    return match.group(0).lower() if match else from_header.lower()


class IMAPMailListener:
    """IMAP 邮件监听器"""

    def __init__(self, config: Dict):
        self.host = config.get("imap_host", "127.0.0.1")
        self.port = int(config.get("imap_port", 143))
        self.use_ssl = bool(config.get("use_ssl", False))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.folder = config.get("folder", "Sent")

    def connect(self) -> Optional[imaplib.IMAP4]:
        """
        连接并登录 IMAP 服务器
        """
        try:
            if self.use_ssl:
                mail = imaplib.IMAP4_SSL(self.host, self.port)
            else:
                mail = imaplib.IMAP4(self.host, self.port)

            mail.login(self.username, self.password)
            return mail
        except Exception as e:
            logger.error(f"IMAP 连接/登录失败 [{self.host}:{self.port}]: {e}", exc_info=True)
            return None

    def fetch_unprocessed_emails(
        self,
        processed_message_ids: set,
        filter_rules: Dict[str, Any],
        temp_dir: Path,
    ) -> List[Dict[str, Any]]:
        """
        拉取并解析符合过滤规则且未处理过的邮件
        :return: 邮件信息列表，包含附件路径
        """
        mail = self.connect()
        if not mail:
            return []

        results = []
        try:
            # 选择监控的文件夹 (如 "Sent" 或 "INBOX")
            status, _ = mail.select(f'"{self.folder}"', readonly=True)
            if status != "OK":
                # 尝试未带双引号形式
                status, _ = mail.select(self.folder, readonly=True)
                if status != "OK":
                    logger.warning(f"无法打开邮件文件夹 [{self.folder}]，尝试打开 [INBOX]")
                    mail.select("INBOX", readonly=True)

            # 搜索全部邮件 ID（可根据需要调整搜索范围，如最近 50 封）
            status, data = mail.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                mail.logout()
                return []

            email_ids = data[0].split()
            # 获取最近的 30 封邮件，倒序处理
            recent_ids = email_ids[-30:]
            recent_ids.reverse()

            keywords = [kw.lower() for kw in filter_rules.get("subject_keywords", ["周报"])]
            allowed_senders = [s.lower() for s in filter_rules.get("allowed_senders", [])]
            allowed_extensions = [ext.lower() for ext in filter_rules.get("allowed_extensions", [])]

            temp_dir.mkdir(parents=True, exist_ok=True)

            for eid in recent_ids:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                msg_id = msg.get("Message-ID", "").strip()
                # 如果没有 Message-ID，则用 Date + Subject 作为唯一标识
                subject = decode_str(msg.get("Subject", ""))
                date_str = msg.get("Date", "")
                unique_key = msg_id if msg_id else f"{date_str}_{subject}"

                if unique_key in processed_message_ids:
                    continue

                # 1. 检查主题关键词过滤
                subject_lower = subject.lower()
                if keywords and not any(kw in subject_lower for kw in keywords):
                    continue

                # 2. 检查发件人过滤
                from_raw = decode_str(msg.get("From", ""))
                sender_email = extract_email_address(from_raw)
                if allowed_senders and sender_email not in allowed_senders:
                    logger.info(f"发件人 [{sender_email}] 不在白名单列表中，跳过")
                    continue

                logger.info(f"匹配到周报邮件: [{subject}], 发件人: [{from_raw}], Message-ID: [{unique_key}]")

                # 3. 提取附件
                attachments = []
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

                    # 扩展名过滤
                    if allowed_extensions and ext not in allowed_extensions:
                        logger.info(f"附件 [{filename}] 格式不在白名单内，跳过")
                        continue

                    # 保存附件到本地临时目录
                    safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
                    filepath = temp_dir / safe_filename
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            with open(filepath, "wb") as f:
                                f.write(payload)
                            attachments.append(filepath)
                            logger.info(f"成功提取周报附件: {filename} ({len(payload)} bytes)")
                    except Exception as e:
                        logger.error(f"提取保存附件出错 [{filename}]: {e}", exc_info=True)

                results.append({
                    "unique_key": unique_key,
                    "subject": subject,
                    "from": from_raw,
                    "sender_email": sender_email,
                    "date": date_str,
                    "attachments": attachments,
                })

            mail.logout()
        except Exception as e:
            logger.error(f"检索邮件过程中发生异常: {e}", exc_info=True)
            try:
                mail.logout()
            except Exception:
                pass

        return results
