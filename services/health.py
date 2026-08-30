# -*- coding: utf-8 -*-
"""统一服务依赖健康探测。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin

import requests

from common.config_loader import get_project_root


def _result(ok: bool, message: str) -> Dict[str, Any]:
    return {"ok": ok, "message": message}


def _resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else get_project_root() / path


def probe_event_queue(config: Dict[str, Any]) -> Dict[str, Any]:
    """验证 hMailServer Event 队列目录可创建、可写。"""
    if not config.get("enabled", True):
        return _result(True, "hMailServer Event 已禁用")

    queue_dir = _resolve_project_path(config.get("queue_dir", "./data/mail_event_queue"))
    probe_file = queue_dir / ".healthcheck.tmp"
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("ok", encoding="ascii")
        probe_file.unlink()
        return _result(True, f"hMailServer Event 队列可写: {queue_dir}")
    except Exception as exc:
        try:
            if probe_file.exists():
                probe_file.unlink()
        except Exception:
            pass
        return _result(False, f"hMailServer Event 队列不可写: {exc}")


def probe_seeddms(config: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """验证 SeedDMS Web 登录及目标文件夹访问。"""
    if not config.get("enabled", True):
        return _result(True, "SeedDMS 已禁用")

    base_url = config.get("api_url", "").rstrip("/")
    username = config.get("username", "")
    password = config.get("password", "")
    folder_id = int(config.get("target_folder_id", 24))
    if not base_url or not username or not password:
        return _result(False, "SeedDMS 配置不完整")

    try:
        with requests.Session() as session:
            session.get(f"{base_url}/out/out.Login.php", timeout=timeout)
            response = session.post(
                f"{base_url}/op/op.Login.php",
                data={"login": username, "pwd": password, "lang": "zh_CN"},
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            folder_response = session.get(
                f"{base_url}/out/out.ViewFolder.php",
                params={"folderid": folder_id},
                timeout=timeout,
                allow_redirects=True,
            )
            folder_response.raise_for_status()
            if "out.Login.php" in folder_response.url:
                return _result(False, "SeedDMS 登录失败")
        return _result(True, "SeedDMS 登录及目标文件夹访问正常")
    except Exception as exc:
        return _result(False, f"SeedDMS 访问失败: {exc}")


def probe_redmine(config: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """验证 Redmine API 和已配置的 API Key。"""
    base_url = config.get("redmine_url", "").rstrip("/")
    keys = list((config.get("users") or {}).values())
    api_key = config.get("default_api_key") or (keys[0] if keys else "")
    if not base_url or not api_key:
        return _result(False, "Redmine 地址或 API Key 未配置")

    try:
        response = requests.get(
            urljoin(f"{base_url}/", "users/current.json"),
            headers={"X-Redmine-API-Key": api_key},
            timeout=timeout,
        )
        if response.status_code != 200:
            return _result(False, f"Redmine API 返回 HTTP {response.status_code}")
        return _result(True, "Redmine API 认证正常")
    except Exception as exc:
        return _result(False, f"Redmine 访问失败: {exc}")


def probe_dependencies(
    git_config: Dict[str, Any],
    report_config: Dict[str, Any],
    timeout: float = 5,
) -> Dict[str, Dict[str, Any]]:
    """并行探测 Event 队列、SeedDMS 与 Redmine。"""
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "hmail_event_queue": executor.submit(
                probe_event_queue,
                report_config.get("hmail_event", {}),
            ),
            "seeddms": executor.submit(
                probe_seeddms,
                report_config.get("seeddms", {}),
                timeout,
            ),
            "redmine": executor.submit(probe_redmine, git_config, timeout),
        }
        return {name: future.result() for name, future in futures.items()}
