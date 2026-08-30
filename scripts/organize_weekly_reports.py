# -*- coding: utf-8 -*-
"""将 SeedDMS 中已有周会文档整理为“年份/文档类别/原文件名”。"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config_loader import get_service_config
from common.logger import setup_logger
from services.weekly_report_sync.filename_rules import parse_attachment_destination
from services.weekly_report_sync.seeddms_client import SeedDMSClient


logger = setup_logger("organize_weekly_reports", "organize_weekly_reports.log")
YEAR_FOLDER_PATTERN = re.compile(r"^\d{4}年$")

# 该文件与“公司项目月度总结202411”同处一个旧目录，按同批 2024-11 资料归档。
DESTINATION_OVERRIDES = {
    "F103E项目11月总结.docx": ("2024", "F103E项目月度总结"),
}


def parse_destination(filename: str) -> Optional[Tuple[str, str]]:
    """从文件名提取 (年份, 文档类别)，并兼容已知旧文件。"""
    if filename in DESTINATION_OVERRIDES:
        return DESTINATION_OVERRIDES[filename]
    return parse_attachment_destination(filename)


def collect_documents(client: SeedDMSClient, root_folder_id: int) -> List[Dict[str, Any]]:
    """读取根目录及所有非目标年份目录中的直接文档。"""
    root_contents = client.list_folder_contents(root_folder_id)
    documents = [
        {**item, "source_folder_id": root_folder_id, "source_folder_name": "周会记录"}
        for item in root_contents["documents"]
    ]

    for folder_name, folder_id in root_contents["folders"].items():
        if YEAR_FOLDER_PATTERN.match(folder_name):
            continue
        contents = client.list_folder_contents(folder_id)
        if contents["folders"]:
            raise RuntimeError(f"旧目录 [{folder_name}] 中仍包含子目录，停止自动迁移")
        documents.extend(
            {**item, "source_folder_id": folder_id, "source_folder_name": folder_name}
            for item in contents["documents"]
        )
    return documents


def build_plan(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan = []
    unresolved = []
    seen_targets = set()

    for document in documents:
        destination = parse_destination(document["name"])
        if not destination:
            unresolved.append(document["name"])
            continue
        year, category = destination
        target_key = (year, category, document["name"])
        if target_key in seen_targets:
            raise RuntimeError(f"目标目录存在同名冲突: {year}年/{category}/{document['name']}")
        seen_targets.add(target_key)
        plan.append({**document, "year": year, "category": category})

    if unresolved:
        raise RuntimeError("以下文件无法解析年份或类别: " + ", ".join(unresolved))
    return plan


def apply_plan(client: SeedDMSClient, root_folder_id: int, plan: List[Dict[str, Any]]) -> int:
    target_cache: Dict[Tuple[str, str], int] = {}
    moved_count = 0

    for index, item in enumerate(plan, start=1):
        cache_key = (item["year"], item["category"])
        target_folder_id = target_cache.get(cache_key)
        if not target_folder_id:
            year_folder_id = client.create_subfolder(
                root_folder_id,
                f"{item['year']}年",
                f"{item['year']}年度周会资料",
            )
            if not year_folder_id:
                raise RuntimeError(f"无法创建或获取年份目录: {item['year']}年")
            target_folder_id = client.create_subfolder(
                year_folder_id,
                item["category"],
                f"{item['year']}年 {item['category']}",
            )
            if not target_folder_id:
                raise RuntimeError(f"无法创建或获取类别目录: {item['year']}年/{item['category']}")
            target_cache[cache_key] = target_folder_id

        if not client.move_document(item["id"], target_folder_id):
            raise RuntimeError(f"移动文档失败: #{item['id']} {item['name']}")
        moved_count += 1
        if index % 10 == 0 or index == len(plan):
            logger.info(f"整理进度: {index}/{len(plan)}")

    return moved_count


def remove_empty_legacy_folders(client: SeedDMSClient, root_folder_id: int) -> int:
    """删除根目录下已搬空的旧目录，保留“YYYY年”目标目录。"""
    root_contents = client.list_folder_contents(root_folder_id)
    targets = [
        (name, folder_id)
        for name, folder_id in root_contents["folders"].items()
        if not YEAR_FOLDER_PATTERN.match(name)
    ]

    removed_count = 0
    for name, folder_id in targets:
        contents = client.list_folder_contents(folder_id)
        if contents["folders"] or contents["documents"]:
            raise RuntimeError(f"旧目录 [{name}] 不是空目录，停止删除")
        if not client.remove_empty_folder(folder_id, root_folder_id):
            raise RuntimeError(f"删除旧空目录失败: #{folder_id} {name}")
        removed_count += 1
        logger.info(f"已删除旧空目录: {name} ({removed_count}/{len(targets)})")
    return removed_count


def main():
    parser = argparse.ArgumentParser(description="整理 SeedDMS 中已有的周会资料")
    parser.add_argument("--apply", action="store_true", help="实际创建目录并移动文档；默认仅预览")
    parser.add_argument(
        "--delete-empty-legacy",
        action="store_true",
        help="整理完成后删除根目录下已搬空的旧目录（必须与 --apply 同时使用）",
    )
    args = parser.parse_args()
    if args.delete_empty_legacy and not args.apply:
        parser.error("--delete-empty-legacy 必须与 --apply 同时使用")

    config = get_service_config("weekly_report_sync")
    seeddms_config = config.get("seeddms", {})
    root_folder_id = int(seeddms_config.get("target_folder_id", 24))
    client = SeedDMSClient(seeddms_config)
    if not client.login():
        raise SystemExit("SeedDMS 登录失败")

    documents = collect_documents(client, root_folder_id)
    plan = build_plan(documents)
    year_counts = Counter(item["year"] for item in plan)
    category_counts = Counter(item["category"] for item in plan)
    logger.info(f"待整理文档总数: {len(plan)}")
    logger.info(f"按年份统计: {dict(sorted(year_counts.items()))}")
    logger.info(f"按类别统计: {dict(sorted(category_counts.items()))}")

    if not args.apply:
        logger.info("当前为预览模式，未修改 SeedDMS；确认后使用 --apply 执行")
        return

    moved_count = apply_plan(client, root_folder_id, plan)
    logger.info(f"整理完成，共移动 {moved_count} 份文档")
    if args.delete_empty_legacy:
        removed_count = remove_empty_legacy_folders(client, root_folder_id)
        logger.info(f"旧目录清理完成，共删除 {removed_count} 个空目录")
    else:
        logger.info("旧空目录未删除")


if __name__ == "__main__":
    main()
