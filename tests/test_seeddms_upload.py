# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from services.weekly_report_sync.seeddms_client import SeedDMSClient


class SeedDMSUploadRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = SeedDMSClient({"target_folder_id": 24})
        self.client._is_logged_in = True
        self.client.get_or_create_year_folder = Mock(return_value=100)
        self.client.get_or_create_category_folder = Mock(return_value=101)

    def _temporary_attachment(self):
        temp_dir = tempfile.TemporaryDirectory()
        path = Path(temp_dir.name) / "研发项目周会会议纪要（2026-08-30).docx"
        path.write_bytes(b"test attachment")
        return temp_dir, path

    def test_existing_name_uploads_new_version(self):
        temp_dir, path = self._temporary_attachment()
        self.addCleanup(temp_dir.cleanup)
        self.client.list_folder_contents = Mock(return_value={
            "folders": {},
            "documents": [{"id": 501, "name": path.name}],
        })
        self.client._upload_new_version = Mock(return_value=True)
        self.client._upload_new_document = Mock(return_value=True)

        result = self.client.upload_document(
            path,
            doc_name=path.name,
            year="2026",
            category="研发项目周会会议纪要",
        )

        self.assertTrue(result)
        self.client._upload_new_version.assert_called_once()
        self.client._upload_new_document.assert_not_called()

    def test_new_name_creates_document(self):
        temp_dir, path = self._temporary_attachment()
        self.addCleanup(temp_dir.cleanup)
        self.client.list_folder_contents = Mock(return_value={"folders": {}, "documents": []})
        self.client._upload_new_version = Mock(return_value=True)
        self.client._upload_new_document = Mock(return_value=True)

        result = self.client.upload_document(
            path,
            doc_name=path.name,
            year="2026",
            category="研发项目周会会议纪要",
        )

        self.assertTrue(result)
        self.client._upload_new_document.assert_called_once()
        self.client._upload_new_version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
