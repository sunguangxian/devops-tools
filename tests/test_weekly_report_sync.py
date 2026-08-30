# -*- coding: utf-8 -*-

import tempfile
import threading
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from services.weekly_report_sync.core import load_sync_state, sync_once
from services.weekly_report_sync.imap_listener import (
    IMAPMailListener,
    MailFetchResult,
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


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.sock = unittest.mock.MagicMock()
        self.fetched_uids = []

    def select(self, *args, **kwargs):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        if name == "UIDVALIDITY":
            return "UIDVALIDITY", [b"12345"]
        return None, None

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(self.messages))]
        if command == "fetch":
            uid = int(args[0])
            self.fetched_uids.append(uid)
            return "OK", [(b"RFC822", self.messages[uid])]
        raise AssertionError(f"unexpected UID command: {command}")

    def logout(self):
        return "BYE", [b""]


class IMAPIncrementalScanTests(unittest.TestCase):
    @staticmethod
    def _build_message(message_id, payload, subject="项目周会"):
        message = EmailMessage()
        message["Message-ID"] = message_id
        message["From"] = "sender@example.com"
        message["To"] = "group@example.com"
        message["Subject"] = subject
        message.set_content("test")
        message.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename="项目周会(2026-08-30).docx",
        )
        return message.as_bytes()

    def test_same_attachment_name_from_different_emails_uses_separate_directories(self):
        fake = FakeIMAP({
            10: self._build_message("<message-10@example.com>", b"first"),
            11: self._build_message("<message-11@example.com>", b"second"),
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            listener = IMAPMailListener({
                "username": "u",
                "password": "p",
                "initial_scan_limit": 30,
            })
            with patch.object(listener, "connect", return_value=fake):
                result = listener.fetch_unprocessed_emails(
                    processed_message_ids=set(),
                    filter_rules={"subject_keywords": ["周会"]},
                    temp_dir=Path(temp_dir),
                )

            self.assertEqual(2, len(result))
            self.assertNotEqual(
                result[0]["attachments"][0].parent,
                result[1]["attachments"][0].parent,
            )
            self.assertEqual(b"first", result[0]["attachments"][0].read_bytes())
            self.assertEqual(b"second", result[1]["attachments"][0].read_bytes())

    def test_initial_scan_limit_only_applies_when_state_is_missing(self):
        messages = {
            uid: self._build_message(f"<message-{uid}@example.com>", str(uid).encode())
            for uid in range(1, 41)
        }
        fake = FakeIMAP(messages)

        with tempfile.TemporaryDirectory() as temp_dir:
            listener = IMAPMailListener({
                "username": "u",
                "password": "p",
                "initial_scan_limit": 2,
            })
            with patch.object(listener, "connect", return_value=fake):
                result = listener.fetch_unprocessed_emails(
                    processed_message_ids=set(),
                    filter_rules={"subject_keywords": ["周会"]},
                    temp_dir=Path(temp_dir),
                    last_uid=0,
                    expected_uid_validity=None,
                )

        self.assertEqual([39, 40], fake.fetched_uids)
        self.assertEqual(40, result.max_uid)


class SyncStateTests(unittest.TestCase):
    def test_legacy_list_state_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "processed_emails.json"
            state_path.write_text('["old-message-id"]', encoding="utf-8")

            state = load_sync_state(state_path)

        self.assertEqual(0, state["last_uid"])
        self.assertEqual({}, state["processed_messages"])
        self.assertEqual(2, state["version"])


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
                notify.return_value = True
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

    def test_partial_failure_does_not_reupload_successful_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "软件周会(2026-08-30).docx"
            second = root / "硬件周会(2026-08-30).docx"
            history_file = root / "history.json"

            def make_result():
                first.write_bytes(b"first-content")
                second.write_bytes(b"second-content")
                return MailFetchResult([{
                    "uid": 100,
                    "unique_key": "message-retry",
                    "subject": "研发周会",
                    "sender_email": "user@example.com",
                    "attachments": [first, second],
                }], max_uid=100, uid_validity=12345)

            with patch(
                "services.weekly_report_sync.core.IMAPMailListener.fetch_unprocessed_emails",
                side_effect=[make_result(), make_result()],
            ), patch("services.weekly_report_sync.core.SeedDMSClient") as client_class:
                client = client_class.return_value
                client.upload_document.side_effect = [True, False, True]
                config = {
                    "mail_monitor": {},
                    "filter_rules": {},
                    "storage": {
                        "temp_dir": temp_dir,
                        "history_file": str(history_file),
                    },
                    "seeddms": {"document_name_template": "{filename}"},
                }

                first_count = sync_once(config)
                second_count = sync_once(config)

            self.assertEqual(1, first_count)
            self.assertEqual(1, second_count)
            self.assertEqual(3, client.upload_document.call_count)
            uploaded_names = [
                call.kwargs["file_path"].name
                for call in client.upload_document.call_args_list
            ]
            self.assertEqual([
                "软件周会(2026-08-30).docx",
                "硬件周会(2026-08-30).docx",
                "硬件周会(2026-08-30).docx",
            ], uploaded_names)
            state = load_sync_state(history_file)
            self.assertIn("message-retry", state["processed_messages"])
            self.assertNotIn("message-retry", state["attachments"])
            self.assertEqual(100, state["last_uid"])


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
