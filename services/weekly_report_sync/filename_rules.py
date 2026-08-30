# -*- coding: utf-8 -*-
"""周报附件文件名解析规则。"""

import re
from pathlib import Path
from typing import Optional, Tuple


DATE_SUFFIX_PATTERN = re.compile(
    r"^(?P<category>.*?)\s*[（(]\s*(?P<year>\d{4})-\d{1,2}-\d{1,2}\s*[）)]?\s*$"
)
MONTH_SUFFIX_PATTERN = re.compile(r"^(?P<category>.*?)(?P<year>\d{4})(?P<month>\d{2})$")


def parse_attachment_destination(filename: str) -> Optional[Tuple[str, str]]:
    """从附件名提取 (年份, 类别)，类别为去掉末尾日期后的文件名。"""
    stem = Path(filename).stem.strip()

    match = DATE_SUFFIX_PATTERN.match(stem)
    if match:
        category = match.group("category").strip()
        if category:
            return match.group("year"), category

    match = MONTH_SUFFIX_PATTERN.match(stem)
    if match and 1 <= int(match.group("month")) <= 12:
        category = match.group("category").strip()
        if category:
            return match.group("year"), category
    return None
