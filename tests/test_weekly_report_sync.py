# -*- coding: utf-8 -*-

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from services.weekly_report_sync.core import load_sync_state, sync_once
from services.weekly_report_sync.mail_event import (
    extract_recipient_addresses,
    has_required_recipients,
    parse_event_message,
)
from services.weekly_report_sync.mail_notifier import send_archive_notification


def build_message(
    message_id: str,
    attachments,
    subject="项目周会",
    sender="sender@example.com",
    to="group@example.com",
):
    message = EmailMessage()
    message["Message-ID"] = message_id
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content("test")
    for filename, payload in attachments:
        message.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename=filename,
        )
    return message.as_bytes()


class MailRecipientRuleTests(unittest.TestCase):
    def test_extracts_group_addresses_from_to_and_cc(self):
        message = EmailMessage()
        message["To"] = "老板 <boss@hklf.com>, sw_group@hklf.com"
        message["Cc"] = "硬件研发组 <hw_group@hklf.com>"
        recipients = extract_recipient_addresses(message)
        self.assertIn("sw_group@hklf.com", recipients)
        self.assertIn("hw_group@hklf.com", recipients)

    def test_both_groups_across_to_and_cc_match(self):
        message = EmailMessage()
        message["To"] = "sw_group@hklf.com"
        message["Cc"] = "hw_group@hklf.com"
        required = {"SW_GROUP@HKLF.COM", "hw_group@hklf.com"}
        self.assertTrue(has_required_recipients(message, required))


class MailEventParserTests(unittest.TestCase):
    def test_same_attachment_name_from_different_messages_uses_separate_temp_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_eml = root / "1.eml"
            second_eml = root / "2.eml"
            first_eml.write_bytes(build_message(
                "<message-1@example.com>",
                [("项目周会(2026-08-30).docx", b"first")],
            ))
            second_eml.write_bytes(build_message(
                "<message-2@example.com>",
                [("项目周会(2026-08-30).docx", b"second")],
            ))

            filters = {"subject_keywords": ["周会"]}
            first = parse_event_message(first_eml, filters, root / "temp", set())
            second = parse_event_message(second_eml, filters, root / "temp", set())

            self.assertNotEqual(first["attachments"][0].parent, second["attachments"][0].parent)
            self.assertEqual(b"first", first["attachments"][0].read_bytes())
            self.assertEqual(b"second", second["attachments"][0].read_bytes())

    def test_sender_whitelist_filters_unrelated_inbound_mail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            eml = root / "mail.eml"
            eml.write_bytes(build_message(
                "<external@example.com>",
                [("项目周会(2026-08-30).docx", b"test")],
                sender="external@example.net",
            ))

            item = parse_event_message(
                eml,
                {"allowed_senders": ["sender@example.com"]},
                root / "temp",
                set(),
            )
            self.assertIsNone(item)


class EventQueueSyncTests(unittest.TestCase):
    def _config(self, root: Path):
        return {
            "hmail_event": {"queue_dir": str(root / "queue")},
            "filter_rules": {
                "allowed_senders": ["sender@example.com"],
                "subject_keywords": ["周会"],
            },
            "storage": {
                "temp_dir": str(root / "temp"),
                "history_file": str(root / "state.json"),
            },
            "notification": {"enabled": False},
            "seeddms": {"document_name_template": "{filename}"},
        }

    def test_event_is_archived_and_queue_file_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self._config(root)
            queue = Path(config["hmail_event"]["queue_dir"])
            queue.mkdir(parents=True)
            eml = queue / "1.eml"
            eml.write_bytes(build_message(
                "<message-1@example.com>",
                [("研发项目周会会议纪要(2026-08-30).docx", b"test")],
            ))

            with patch("services.weekly_report_sync.core.SeedDMSClient") as client_class:
                client_class.return_value.upload_document.return_value = True
                archived = sync_once(config)

            self.assertEqual(1, archived)
            self.assertFalse(eml.exists())
            state = load_sync_state(Path(config["storage"]["history_file"]))
            self.assertIn("<message-1@example.com>", state["processed_messages"])

    def test_partial_failure_does_not_reupload_successful_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self._config(root)
            queue = Path(config["hmail_event"]["queue_dir"])
            queue.mkdir(parents=True)
            eml = queue / "2.eml"
            eml.write_bytes(build_message(
                "<message-retry@example.com>",
                [
                    ("软件周会(2026-08-30).docx", b"first"),
                    ("硬件周会(2026-08-30).docx", b"second"),
                ],
            ))

            with patch("services.weekly_report_sync.core.SeedDMSClient") as client_class:
                client = client_class.return_value
                client.upload_document.side_effect = [True, False, True]
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
            self.assertFalse(eml.exists())

    def test_old_imap_state_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                '{"version": 2, "last_uid": 100, "processed_messages": {}}',
                encoding="utf-8",
            )
            state = load_sync_state(state_path)
            self.assertEqual(3, state["version"])
            self.assertNotIn("last_uid", state)


class ArchiveNotificationTests(unittest.TestCase):
    def test_notifier_uses_configured_smtp_and_recipient(self):
        smtp = unittest.mock.MagicMock()
        smtp_context = unittest.mock.MagicMock()
        smtp.return_value.__enter__.return_value = smtp_context

        with patch("services.weekly_report_sync.mail_notifier.smtplib.SMTP", smtp):
            result = send_archive_notification(
                notification_config={
                    "enabled": True,
                    "smtp_host": "mail.example.com",
                    "smtp_port": 25,
                    "username": "sender@example.com",
                    "password": "secret",
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
