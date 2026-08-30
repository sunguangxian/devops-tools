# -*- coding: utf-8 -*-

import threading
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from services.weekly_report_sync.core import sync_once
from services.weekly_report_sync.imap_listener import (
    extract_recipient_addresses,
    has_required_recipients,
)
from services.weekly_report_sync.mail_notifier import send_archive_notification


class WeeklyReportSyncConcurrencyTests(unittest.TestCase):
    def test_sync_once_serializes_concurrent_calls(self):
        active_calls = 0
        max_active_calls = 0
        state_lock = threading.Lock()

        def fetch_emails(*args, **kwargs):
            nonlocal active_calls, max_active_calls
            with state_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            time.sleep(0.05)
            with state_lock:
                active_calls -= 1
            return []

        config = {
            "mail_monitor": {},
            "filter_rules": {},
            "storage": {},
        }
        start_barrier = threading.Barrier(3)
        results = []

        def run_sync():
            start_barrier.wait()
            results.append(sync_once(config))

        with patch(
            "services.weekly_report_sync.core.IMAPMailListener.fetch_unprocessed_emails",
            side_effect=fetch_emails,
        ):
            threads = [threading.Thread(target=run_sync) for _ in range(2)]
            for thread in threads:
                thread.start()
            start_barrier.wait()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual([0, 0], sorted(results))
        self.assertEqual(1, max_active_calls)
        self.assertTrue(all(not thread.is_alive() for thread in threads))


class MailRecipientRuleTests(unittest.TestCase):
    def test_extracts_group_addresses_from_to_and_cc(self):
        message = EmailMessage()
        message["To"] = "老板 <boss@hklf.com>, sw_group@hklf.com"
        message["Cc"] = "硬件研发组 <hw_group@hklf.com>"

        recipients = extract_recipient_addresses(message)

        self.assertIn("sw_group@hklf.com", recipients)
        self.assertIn("hw_group@hklf.com", recipients)

    def test_boss_only_does_not_contain_required_groups(self):
        message = EmailMessage()
        message["To"] = "boss@hklf.com"
        required = {"sw_group@hklf.com", "hw_group@hklf.com"}

        self.assertFalse(has_required_recipients(message, required))

    def test_both_groups_across_to_and_cc_match(self):
        message = EmailMessage()
        message["To"] = "sw_group@hklf.com"
        message["Cc"] = "hw_group@hklf.com"
        required = {"SW_GROUP@HKLF.COM", "hw_group@hklf.com"}

        self.assertTrue(has_required_recipients(message, required))


class WeeklyReportSyncRoutingTests(unittest.TestCase):
    def test_attachment_name_controls_year_category_and_document_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "研发项目周会会议纪要（2019-07-15).docx"
            attachment.write_bytes(b"test")
            history_file = Path(temp_dir) / "history.json"
            email_item = {
                "unique_key": "message-1",
                "subject": "周报",
                "sender_email": "user@example.com",
                "attachments": [attachment],
            }

            with patch(
                "services.weekly_report_sync.core.IMAPMailListener.fetch_unprocessed_emails",
                return_value=[email_item],
            ), patch("services.weekly_report_sync.core.SeedDMSClient") as client_class:
                client_class.return_value.upload_document.return_value = True
                archived = sync_once({
                    "mail_monitor": {},
                    "filter_rules": {},
                    "storage": {"temp_dir": temp_dir, "history_file": str(history_file)},
                    "seeddms": {"document_name_template": "{filename}"},
                })

            self.assertEqual(1, archived)
            client_class.return_value.upload_document.assert_called_once_with(
                file_path=attachment,
                doc_name=attachment.name,
                comment="由邮件监控自动备份",
                year="2019",
                category="研发项目周会会议纪要",
            )

    def test_unrecognized_attachment_is_not_archived_or_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "无日期会议纪要.docx"
            attachment.write_bytes(b"test")
            email_item = {
                "unique_key": "message-2",
                "subject": "周报",
                "sender_email": "user@example.com",
                "attachments": [attachment],
            }

            with patch(
                "services.weekly_report_sync.core.IMAPMailListener.fetch_unprocessed_emails",
                return_value=[email_item],
            ), patch("services.weekly_report_sync.core.SeedDMSClient") as client_class:
                archived = sync_once({
                    "mail_monitor": {},
                    "filter_rules": {},
                    "storage": {
                        "temp_dir": temp_dir,
                        "history_file": str(Path(temp_dir) / "history.json"),
                    },
                    "seeddms": {"document_name_template": "{filename}"},
                })

            self.assertEqual(0, archived)
            self.assertTrue(attachment.exists())
            client_class.return_value.upload_document.assert_not_called()

    def test_successful_email_sends_archive_notification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "研发项目周会会议纪要(2026-08-30).docx"
            attachment.write_bytes(b"test")
            email_item = {
                "unique_key": "message-3",
                "subject": "研发项目周会会议纪要",
                "sender_email": "user@example.com",
                "attachments": [attachment],
            }

            with patch(
                "services.weekly_report_sync.core.IMAPMailListener.fetch_unprocessed_emails",
                return_value=[email_item],
            ), patch("services.weekly_report_sync.core.SeedDMSClient") as client_class, patch(
                "services.weekly_report_sync.core.send_archive_notification"
            ) as notify:
                client_class.return_value.upload_document.return_value = True
                sync_once({
                    "mail_monitor": {"username": "user@example.com"},
                    "filter_rules": {},
                    "notification": {"enabled": True},
                    "storage": {
                        "temp_dir": temp_dir,
                        "history_file": str(Path(temp_dir) / "history.json"),
                    },
                    "seeddms": {"document_name_template": "{filename}"},
                })

            notify.assert_called_once_with(
                mail_config={"username": "user@example.com"},
                notification_config={"enabled": True},
                source_subject="研发项目周会会议纪要",
                archived_items=[{
                    "filename": "研发项目周会会议纪要(2026-08-30).docx",
                    "year": "2026",
                    "category": "研发项目周会会议纪要",
                }],
            )


class ArchiveNotificationTests(unittest.TestCase):
    def test_notifier_uses_configured_smtp_and_recipient(self):
        smtp = unittest.mock.MagicMock()
        smtp_context = unittest.mock.MagicMock()
        smtp.return_value.__enter__.return_value = smtp_context

        with patch("services.weekly_report_sync.mail_notifier.smtplib.SMTP", smtp):
            result = send_archive_notification(
                mail_config={"username": "sender@example.com", "password": "secret"},
                notification_config={
                    "enabled": True,
                    "smtp_host": "mail.example.com",
                    "smtp_port": 25,
                    "recipient": "owner@example.com",
                },
                source_subject="项目周会",
                archived_items=[{
                    "filename": "项目周会(2026-08-30).docx",
                    "year": "2026",
                    "category": "项目周会",
                }],
            )

        self.assertTrue(result)
        smtp.assert_called_once_with("mail.example.com", 25, timeout=20)
        smtp_context.login.assert_called_once_with("sender@example.com", "secret")
        sent_message = smtp_context.send_message.call_args.args[0]
        self.assertEqual("owner@example.com", sent_message["To"])
        self.assertIn("2026年/项目周会", sent_message.get_content())


if __name__ == "__main__":
    unittest.main()
