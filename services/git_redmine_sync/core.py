# -*- coding: utf-8 -*-
"""
Webhook Payload 解析与同步核心业务逻辑
"""

import re
import logging
from typing import Dict, List, Tuple, Any, Optional

from services.git_redmine_sync.redmine_client import RedmineService

logger = logging.getLogger("services.git_redmine_sync.core")

# 正则匹配规则：支持 fixes/close/refs/修复/解决 等动作与 redmine-#123 格式
ISSUE_PATTERN = re.compile(
    r"\b(?P<action>fix|fixes|close|closes|refs|修复|解决)\s+redmine-#(?P<id>\d+)",
    re.IGNORECASE,
)

CLOSE_ACTIONS = {"fix", "fixes", "close", "closes", "修复", "解决"}


def extract_issues(message: str) -> List[Tuple[str, str]]:
    """
    从 Commit Message 中提取所有关联的动作和 Redmine Issue ID
    :return: 列表 [(action, issue_id), ...]
    """
    if not message:
        return []
    return [(m.group("action").lower(), m.group("id")) for m in ISSUE_PATTERN.finditer(message)]


def build_commit_note(
    source: str,
    project: str,
    branch: str,
    commit_id: str,
    user_name: str,
    message: str,
    commit: Dict[str, Any],
) -> str:
    """
    格式化生成附加到 Redmine Issue 的备注文本
    """
    lines = [
        f"[{source.upper()}] {project} / {branch}",
        f"commit: {commit_id}",
        f"author: {user_name}",
        "",
        "message:",
        message.strip(),
    ]

    files: List[str] = []
    files.extend([f"+ {f}" for f in commit.get("added", [])])
    files.extend([f"M {f}" for f in commit.get("modified", [])])
    files.extend([f"- {f}" for f in commit.get("removed", [])])

    if files:
        lines.append("")
        lines.append("files:")
        for f in files:
            lines.append(f"  {f}")

    return "\n".join(lines)


def process_webhook(payload: Dict[str, Any], config: Dict[str, Any]) -> Tuple[str, int]:
    """
    处理 Webhook 请求 Payload
    :param payload: Webhook POST 数据字典
    :param config: git_redmine_sync 配置字典
    :return: (响应消息, HTTP状态码)
    """
    if not payload or not isinstance(payload, dict):
        return "Empty or invalid payload", 200

    # 1. 识别 Git 平台来源
    if "object_kind" in payload:
        source = "gitlab"
        user_name = payload.get("user_username")
        project = payload.get("project", {}).get("name", "Unknown-Project")
    elif "repository" in payload and "commits" in payload:
        source = "gitea"
        user_name = payload.get("pusher", {}).get("username")
        project = payload.get("repository", {}).get("name", "Unknown-Project")
    else:
        logger.warning(f"未知或不支持的 Webhook 格式: {list(payload.keys())}")
        return "Unknown webhook source", 200

    # 2. 检查分支过滤
    branch_raw = payload.get("ref", "")
    branch = branch_raw.split("/")[-1] if branch_raw.startswith("refs/heads/") else branch_raw

    valid_branches = config.get("valid_branches") or []
    if valid_branches and branch not in valid_branches:
        logger.info(f"分支 [{branch}] 不在允许列表 {valid_branches} 中，已忽略")
        return "Branch ignored", 200

    if not user_name:
        logger.warning(f"无法从 Webhook Payload 中解析出提交用户 (来源: {source})")
        return "User not found in payload", 200

    # 3. 初始化 Redmine 客户端
    redmine_url = config.get("redmine_url", "")
    user_keys = config.get("users", {})
    default_key = config.get("default_api_key")

    redmine_service = RedmineService(redmine_url, user_keys, default_key)
    redmine = redmine_service.get_client(user_name)

    if not redmine:
        logger.warning(f"用户 [{user_name}] 未配置 Redmine API Key，跳过处理")
        return "User not mapped", 200

    # 4. 获取 Redmine 状态映射
    status_cfg = config.get("status", {})
    resolved_name = status_cfg.get("resolved", "已解决")
    closed_name = status_cfg.get("closed", "已关闭")

    status_map = redmine_service.get_status_id_map(redmine)
    resolved_id = status_map.get(resolved_name)
    closed_id = status_map.get(closed_name)
    target_close_id = closed_id or resolved_id

    # 5. 遍历处理 Commits
    commits = payload.get("commits", [])
    processed_count = 0

    for commit in commits:
        msg = commit.get("message", "")
        cid = str(commit.get("id", ""))[:7]
        issues = extract_issues(msg)

        if not issues:
            continue

        note = build_commit_note(
            source=source,
            project=project,
            branch=branch,
            commit_id=cid,
            user_name=user_name,
            message=msg,
            commit=commit,
        )

        for action, issue_id in issues:
            is_close_action = action in CLOSE_ACTIONS
            status_id = target_close_id if is_close_action else None

            logger.info(
                f"处理 Commit {cid} | 作者: {user_name} | 动作: {action} | "
                f"Issue: #{issue_id} | 目标状态ID: {status_id}"
            )
            redmine_service.update_issue(
                redmine=redmine,
                issue_id=issue_id,
                status_id=status_id,
                note=note,
                done_ratio=100 if is_close_action else None,
            )
            processed_count += 1

    logger.info(f"Webhook 处理完成，共同步 {processed_count} 个问题变更 (来源: {source}, 分支: {branch})")
    return "OK", 200
