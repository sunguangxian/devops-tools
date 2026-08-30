# -*- coding: utf-8 -*-

import sys
import unittest
from unittest.mock import patch

import run
from services.health import probe_dependencies, probe_imap, probe_redmine, probe_seeddms
from services.server import app


class RunnerArgumentForwardingTests(unittest.TestCase):
    def test_weekly_report_arguments_are_forwarded_without_service_name(self):
        received = []
        service = {"func": lambda: received.append(list(sys.argv)), "help": "test"}

        with patch.dict(run.SERVICES, {"weekly_report_sync": service}, clear=True), patch.object(
            sys, "argv", ["run.py", "weekly_report_sync", "--once"]
        ):
            run.main()

        self.assertEqual([["run.py", "--once"]], received)


class DependencyHealthTests(unittest.TestCase):
    def test_imap_reports_success_after_login(self):
        mail = unittest.mock.MagicMock()
        with patch("services.health._create_imap_client", return_value=mail):
            result = probe_imap({
                "imap_host": "mail.example.com",
                "imap_port": 143,
                "username": "user@example.com",
                "password": "secret",
            }, timeout=2)

        self.assertTrue(result["ok"])
        mail.login.assert_called_once_with("user@example.com", "secret")

    def test_seeddms_rejects_redirect_back_to_login(self):
        session = unittest.mock.MagicMock()
        session.__enter__.return_value = session
        login_response = unittest.mock.MagicMock()
        login_response.url = "http://dms.example.com/out/out.Home.php"
        folder_response = unittest.mock.MagicMock()
        folder_response.url = "http://dms.example.com/out/out.Login.php"
        session.post.return_value = login_response
        session.get.side_effect = [unittest.mock.MagicMock(), folder_response]

        with patch("services.health.requests.Session", return_value=session):
            result = probe_seeddms({
                "api_url": "http://dms.example.com",
                "username": "user",
                "password": "secret",
                "target_folder_id": 24,
            }, timeout=2)

        self.assertFalse(result["ok"])

    def test_redmine_requires_successful_api_response(self):
        response = unittest.mock.MagicMock(status_code=401)
        with patch("services.health.requests.get", return_value=response):
            result = probe_redmine({
                "redmine_url": "http://redmine.example.com",
                "users": {"git-user": "secret"},
            }, timeout=2)

        self.assertFalse(result["ok"])
        self.assertIn("401", result["message"])

    def test_dependency_checks_run_all_probes(self):
        with patch("services.health.probe_imap", return_value={"ok": True}), patch(
            "services.health.probe_seeddms", return_value={"ok": True}
        ), patch("services.health.probe_redmine", return_value={"ok": True}):
            result = probe_dependencies({}, {}, timeout=2)

        self.assertEqual({"imap", "seeddms", "redmine"}, set(result))
        self.assertTrue(all(item["ok"] for item in result.values()))


class ManualWeeklyReportTriggerTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.config = {
            "manual_trigger": {
                "enabled": True,
                "api_key": "test-secret-key",
            }
        }

    def test_get_is_not_allowed(self):
        response = self.client.get("/sync/weekly_report")
        self.assertEqual(405, response.status_code)

    def test_missing_api_key_is_rejected(self):
        with patch("services.server.get_service_config", return_value=self.config):
            response = self.client.post("/sync/weekly_report")
        self.assertEqual(401, response.status_code)

    def test_valid_api_key_triggers_sync(self):
        with patch("services.server.get_service_config", return_value=self.config), patch(
            "services.server.sync_once", return_value=2
        ) as sync:
            response = self.client.post(
                "/sync/weekly_report",
                headers={"X-API-Key": "test-secret-key"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.get_json()["archived_count"])
        sync.assert_called_once_with(self.config)


if __name__ == "__main__":
    unittest.main()
