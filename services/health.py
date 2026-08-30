# -*- coding: utf-8 -*-
"""统一服务依赖健康探测。"""

import imaplib
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict
from urllib.parse import urljoin

import requests


def _result(ok: bool, message: str) -> Dict[str, Any]:
    return {"ok": ok, "message": message}


def _create_imap_client(host: str, port: int, use_ssl: bool, timeout: float):
    """创建带网络超时的 IMAP 客户端，兼容 Python 3.8。"""
    if use_ssl:
        class TimeoutIMAP4SSL(imaplib.IMAP4_SSL):
            def _create_socket(self):
                sock = socket.create_connection((self.host, self.port), timeout=timeout)
                return self.ssl_context.wrap_socket(sock, server_hostname=self.host)

        return TimeoutIMAP4SSL(host, port)

    class TimeoutIMAP4(imaplib.IMAP4):
        def _create_socket(self):
            return socket.create_connection((self.host, self.port), timeout=timeout)

    return TimeoutIMAP4(host, port)


def probe_imap(mail_config: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """验证 IMAP 端口、协议握手和邮箱凭证。"""
    host = mail_config.get("imap_host", "")
    port = int(mail_config.get("imap_port", 143))
    username = mail_config.get("username", "")
    password = mail_config.get("password", "")
    if not host or not username or not password:
        return _result(False, "IMAP 配置不完整")

    mail = None
    try:
        mail = _create_imap_client(
            host,
            port,
            bool(mail_config.get("use_ssl", False)),
            timeout,
        )
        mail.sock.settimeout(timeout)
        mail.login(username, password)
        return _result(True, "IMAP 登录正常")
    except Exception as exc:
        return _result(False, f"IMAP 登录失败: {exc}")
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


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
    """并行探测三个外部依赖，避免健康接口串行累积等待。"""
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "imap": executor.submit(probe_imap, report_config.get("mail_monitor", {}), timeout),
            "seeddms": executor.submit(probe_seeddms, report_config.get("seeddms", {}), timeout),
            "redmine": executor.submit(probe_redmine, git_config, timeout),
        }
        return {name: future.result() for name, future in futures.items()}
