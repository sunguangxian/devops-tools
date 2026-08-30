# -*- coding: utf-8 -*-

import unittest

from scripts.organize_weekly_reports import parse_destination
from services.weekly_report_sync.filename_rules import parse_attachment_destination


class ParseDestinationTests(unittest.TestCase):
    def test_mixed_parentheses_date(self):
        self.assertEqual(
            ("2019", "研发项目周会会议纪要"),
            parse_destination("研发项目周会会议纪要（2019-07-15).docx"),
        )

    def test_missing_closing_parenthesis(self):
        self.assertEqual(
            ("2023", "F103E项目管理纪要"),
            parse_destination("F103E项目管理纪要(2023-08-07.xlsx"),
        )

    def test_month_suffix(self):
        self.assertEqual(
            ("2024", "公司项目月度总结"),
            parse_destination("公司项目月度总结202411.docx"),
        )

    def test_known_legacy_override(self):
        self.assertEqual(
            ("2024", "F103E项目月度总结"),
            parse_destination("F103E项目11月总结.docx"),
        )

    def test_unrecognized_filename(self):
        self.assertIsNone(parse_attachment_destination("无日期会议纪要.docx"))


if __name__ == "__main__":
    unittest.main()
