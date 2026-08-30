# -*- coding: utf-8 -*-
"""
SeedDMS 文档管理系统 API 交互封装模块
支持文档上传、元数据设置、自动按年份查找/创建子目录并归档。
"""

import os
import re
import logging
from pathlib import Path
from typing import Dict, Optional, Union, Tuple
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("weekly_report_sync.seeddms")


class SeedDMSClient:
    """SeedDMS API 交互客户端"""

    def __init__(self, config: Dict):
        self.enabled = bool(config.get("enabled", True))
        self.api_url = config.get("api_url", "http://192.168.1.208:8080").rstrip("/")
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.target_folder_id = int(config.get("target_folder_id", 24))
        self.auto_create_year_folder = bool(config.get("auto_create_year_folder", True))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DevOpsAutomation/1.0",
        })
        self._is_logged_in = False
        self._year_folder_cache: Dict[str, int] = {}

    def login(self) -> bool:
        """
        登录 SeedDMS 获取会话 Cookie
        """
        if not self.enabled:
            return True

        # 方式 1: Web 表单标准登录
        web_login_url = f"{self.api_url}/op/op.Login.php"
        try:
            # 先 GET 一次登录页以获取可能的初始 cookies / formtoken
            self.session.get(f"{self.api_url}/out/out.Login.php", timeout=10)
            
            login_data = {
                "login": self.username,
                "pwd": self.password,
                "lang": "zh_CN",
            }
            resp = self.session.post(web_login_url, data=login_data, timeout=15, allow_redirects=True)
            
            # 检查登录是否成功（通过判断响应页面是否包含登出链接或 ViewFolder）
            if "out.Logout.php" in resp.text or "out.ViewFolder.php" in resp.text or resp.status_code == 200:
                # 进一步验证访问目标文件夹
                check_resp = self.session.get(
                    f"{self.api_url}/out/out.ViewFolder.php?folderid={self.target_folder_id}",
                    timeout=10,
                )
                if "out.Login.php" not in check_resp.url and check_resp.status_code == 200:
                    self._is_logged_in = True
                    logger.info(f"成功登录 SeedDMS (账号: {self.username})")
                    return True

            # 方式 2: REST API 登录回退
            rest_login_url = f"{self.api_url}/restapi/index.php/login"
            resp_rest = self.session.post(
                rest_login_url,
                json={"user": self.username, "pass": self.password},
                timeout=15,
            )
            if resp_rest.status_code == 200:
                self._is_logged_in = True
                logger.info(f"通过 REST API 成功登录 SeedDMS (账号: {self.username})")
                return True

            logger.error(f"登录 SeedDMS 失败，请检查账号密码是否正确 (账号: {self.username})")
            return False
        except Exception as e:
            logger.error(f"连接 SeedDMS 失败 [{self.api_url}]: {e}", exc_info=True)
            return False

    def get_subfolders(self, parent_folder_id: int) -> Dict[str, int]:
        """
        获取指定父目录下的所有子文件夹名称及 ID 映射
        :return: {'2026': 25, '2025': 20, ...}
        """
        if not self._is_logged_in:
            if not self.login():
                return {}

        url = f"{self.api_url}/out/out.ViewFolder.php?folderid={parent_folder_id}"
        subfolders = {}
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return {}

            soup = BeautifulSoup(resp.text, "html.parser")
            # 解析页面中的文件夹链接，格式如 out.ViewFolder.php?folderid=XX
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                match = re.search(r"out\.ViewFolder\.php\?folderid=(\d+)", href)
                if match:
                    fid = int(match.group(1))
                    if fid != parent_folder_id:
                        folder_name = a_tag.get_text(strip=True)
                        if folder_name:
                            subfolders[folder_name] = fid

            logger.debug(f"父目录 #{parent_folder_id} 下子文件夹: {subfolders}")
            return subfolders
        except Exception as e:
            logger.warning(f"获取父目录 #{parent_folder_id} 子目录列表异常: {e}")
            return {}

    def create_subfolder(self, parent_folder_id: int, folder_name: str, comment: str = "") -> Optional[int]:
        """
        在指定父目录下创建新的子文件夹
        :return: 新建子文件夹的 ID
        """
        if not self._is_logged_in:
            if not self.login():
                return None

        # 方式 1: Web 接口添加文件夹
        add_url = f"{self.api_url}/op/op.AddFolder.php"
        data = {
            "folderid": parent_folder_id,
            "name": folder_name,
            "comment": comment or f"{folder_name}年工作周报归档",
            "formtoken": "1",
        }
        try:
            resp = self.session.post(add_url, data=data, timeout=15, allow_redirects=True)
            if resp.status_code in [200, 302]:
                # 重新扫描获取新建文件夹的 ID
                subfolders = self.get_subfolders(parent_folder_id)
                new_id = subfolders.get(folder_name)
                if new_id:
                    logger.info(f"在父目录 #{parent_folder_id} 下成功创建年份子目录 [{folder_name}] (ID: {new_id})")
                    return new_id

            # 方式 2: REST API 添加文件夹
            rest_url = f"{self.api_url}/restapi/index.php/folders/{parent_folder_id}/folder"
            resp_rest = self.session.post(
                rest_url,
                json={"name": folder_name, "comment": comment},
                timeout=15,
            )
            if resp_rest.status_code in [200, 201]:
                res_data = resp_rest.json()
                new_id = res_data.get("id") or res_data.get("folderid")
                if new_id:
                    logger.info(f"通过 REST API 成功创建子目录 [{folder_name}] (ID: {new_id})")
                    return int(new_id)

            logger.error(f"在父目录 #{parent_folder_id} 下创建子目录 [{folder_name}] 失败")
            return None
        except Exception as e:
            logger.error(f"创建子文件夹异常: {e}", exc_info=True)
            return None

    def get_or_create_year_folder(self, parent_folder_id: int, year: str) -> int:
        """
        按年份自动获取或创建子文件夹 ID
        """
        cache_key = f"{parent_folder_id}_{year}"
        if cache_key in self._year_folder_cache:
            return self._year_folder_cache[cache_key]

        # 检查已存在的子目录
        subfolders = self.get_subfolders(parent_folder_id)
        # 支持精确匹配 "2026" 或 "2026年"
        target_id = subfolders.get(year) or subfolders.get(f"{year}年")

        if target_id:
            logger.info(f"找到已存在的年份归档目录 [{year}] (ID: {target_id})")
            self._year_folder_cache[cache_key] = target_id
            return target_id

        # 不存在则自动创建
        logger.info(f"未找到年份目录 [{year}]，正在父目录 #{parent_folder_id} 下自动创建...")
        new_id = self.create_subfolder(parent_folder_id, folder_name=f"{year}年", comment=f"{year}年度周会与周报归档")
        if new_id:
            self._year_folder_cache[cache_key] = new_id
            return new_id

        logger.warning(f"无法创建年份目录 [{year}]，将直接归档到父目录 #{parent_folder_id}")
        return parent_folder_id

    def upload_document(
        self,
        file_path: Union[str, Path],
        doc_name: Optional[str] = None,
        comment: Optional[str] = None,
        year: Optional[str] = None,
        folder_id: Optional[int] = None,
    ) -> bool:
        """
        上传文档至 SeedDMS（支持按年份自动归档到对应子目录）
        :param file_path: 本地待上传文件路径
        :param doc_name: 在 SeedDMS 显示的文档标题
        :param comment: 文档备注说明
        :param year: 年份（如 '2026'），开启 auto_create_year_folder 时将自动查找/创建该年份子目录
        :param folder_id: 手动指定目标目录 ID (优先级高于默认配置)
        :return: 是否上传成功
        """
        if not self.enabled:
            logger.info("SeedDMS 备份已在配置中禁用，跳过归档")
            return True

        path_obj = Path(file_path)
        if not path_obj.exists():
            logger.error(f"归档失败，文件不存在: {file_path}")
            return False

        if not self._is_logged_in:
            if not self.login():
                return False

        # 确定归档目标文件夹 ID
        target_fid = folder_id or self.target_folder_id
        if self.auto_create_year_folder and year:
            target_fid = self.get_or_create_year_folder(target_fid, str(year))

        doc_name = doc_name or path_obj.stem
        comment = comment or "自动归档工作周报"

        # 方式 1: 优先使用标准 Web 上传接口 (op.AddDocument.php)
        web_upload_url = f"{self.api_url}/op/op.AddDocument.php"
        try:
            with open(path_obj, "rb") as f:
                files = {"userfile[]": (path_obj.name, f, "application/octet-stream")}
                data = {
                    "folderid": target_fid,
                    "name": doc_name,
                    "comment": comment,
                    "sequence": 1,
                    "formtoken": "1",
                }
                resp_web = self.session.post(web_upload_url, data=data, files=files, timeout=60, allow_redirects=True)
                if resp_web.status_code in [200, 302] and "out.ViewFolder.php" in resp_web.text:
                    logger.info(f"周报文件成功备份至 SeedDMS (Folder ID: #{target_fid}, 标题: [{doc_name}], 文件: {path_obj.name})")
                    return True

            # 方式 2: REST API 上传接口回退
            rest_upload_url = f"{self.api_url}/restapi/index.php/folders/{target_fid}/document"
            with open(path_obj, "rb") as f:
                files = {"file": (path_obj.name, f, "application/octet-stream")}
                data = {
                    "name": doc_name,
                    "comment": comment,
                    "sequence": 1,
                }
                resp = self.session.post(rest_upload_url, data=data, files=files, timeout=60)
                if resp.status_code in [200, 201]:
                    logger.info(f"通过 REST API 成功备份至 SeedDMS (Folder ID: #{target_fid}, 标题: [{doc_name}])")
                    return True

            logger.error(f"SeedDMS 上传失败: HTTP {resp_web.status_code}")
            return False
        except Exception as e:
            logger.error(f"备份到 SeedDMS 过程中出现异常: {e}", exc_info=True)
            return False
