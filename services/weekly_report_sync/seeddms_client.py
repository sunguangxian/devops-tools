# -*- coding: utf-8 -*-
"""
SeedDMS 文档管理系统 API 交互封装模块
支持文档上传、元数据设置、自动按年份查找/创建子目录并归档。
"""

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple
from urllib.parse import urljoin
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
        self._category_folder_cache: Dict[str, int] = {}

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

        return self.list_folder_contents(parent_folder_id)["folders"]

    def list_folder_contents(self, folder_id: int) -> Dict[str, Any]:
        """读取文件夹的直接子文件夹和文档。"""
        if not self._is_logged_in:
            if not self.login():
                return {"folders": {}, "documents": []}

        url = f"{self.api_url}/out/out.ViewFolder.php"
        try:
            resp = self.session.get(
                url,
                params={"action": "folderList", "folderid": folder_id, "orderby": "u"},
                timeout=30,
            )
            if resp.status_code != 200:
                return {"folders": {}, "documents": []}

            resp.encoding = "utf-8-sig"
            soup = BeautifulSoup(resp.text, "html.parser")
            folders: Dict[str, int] = {}
            documents: List[Dict[str, Any]] = []

            for row in soup.select("tr.table-row-folder"):
                match = re.search(r"folder_(\d+)", row.get("rel", ""))
                name = row.get("data-name", "").strip()
                if match and name:
                    folders[name] = int(match.group(1))

            for row in soup.select("tr.table-row-document"):
                match = re.search(r"document_(\d+)", row.get("rel", ""))
                name = row.get("data-name", "").strip()
                if match and name:
                    documents.append({"id": int(match.group(1)), "name": name})

            logger.debug(
                f"文件夹 #{folder_id} 包含 {len(folders)} 个子文件夹、{len(documents)} 个文档"
            )
            return {"folders": folders, "documents": documents}
        except Exception as e:
            logger.warning(f"读取文件夹 #{folder_id} 内容异常: {e}")
            return {"folders": {}, "documents": []}

    def _load_operation_form(self, path: str, action_suffix: str):
        """读取 SeedDMS 操作表单及其实时 CSRF token。"""
        resp = self.session.get(f"{self.api_url}{path}", timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8-sig"
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form", action=lambda value: value and action_suffix in value)
        if not form:
            raise RuntimeError(f"未找到 SeedDMS 操作表单: {action_suffix}")

        data: Dict[str, Any] = {}
        for field in form.find_all(["input", "textarea", "select"]):
            name = field.get("name")
            if not name or field.get("type") == "file":
                continue
            if field.get("type") in {"checkbox", "radio"} and not field.has_attr("checked"):
                continue
            if field.name == "select":
                selected = field.find("option", selected=True) or field.find("option")
                data[name] = selected.get("value", "") if selected else ""
            else:
                data[name] = field.get("value", "")

        action_url = urljoin(resp.url, form.get("action", ""))
        method = form.get("method", "get").lower()
        return action_url, method, data

    def create_subfolder(self, parent_folder_id: int, folder_name: str, comment: str = "") -> Optional[int]:
        """
        在指定父目录下创建新的子文件夹
        :return: 新建子文件夹的 ID
        """
        if not self._is_logged_in:
            if not self.login():
                return None

        existing_id = self.get_subfolders(parent_folder_id).get(folder_name)
        if existing_id:
            return existing_id

        try:
            add_url, method, data = self._load_operation_form(
                f"/out/out.AddSubFolder.php?folderid={parent_folder_id}",
                "op.AddSubFolder.php",
            )
            data.update({
                "folderid": parent_folder_id,
                "name": folder_name,
                "comment": comment or f"{folder_name}工作周报归档",
                "sequence": data.get("sequence") or 1,
            })
            request_method = self.session.post if method == "post" else self.session.get
            resp = request_method(add_url, data=data if method == "post" else None,
                                  params=data if method != "post" else None,
                                  timeout=15, allow_redirects=True)
            if resp.status_code in [200, 302]:
                # 重新扫描获取新建文件夹的 ID
                subfolders = self.get_subfolders(parent_folder_id)
                new_id = subfolders.get(folder_name)
                if new_id:
                    logger.info(f"在父目录 #{parent_folder_id} 下成功创建年份子目录 [{folder_name}] (ID: {new_id})")
                    return new_id

            # REST API 添加文件夹回退
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

    def move_document(self, document_id: int, target_folder_id: int) -> bool:
        """将现有文档移动到指定文件夹。"""
        if not self._is_logged_in:
            if not self.login():
                return False

        try:
            action_url, method, data = self._load_operation_form(
                f"/out/out.MoveDocument.php?documentid={document_id}",
                "op.MoveDocument.php",
            )
            data.update({"documentid": document_id, "targetid": target_folder_id})
            if method == "post":
                resp = self.session.post(action_url, data=data, timeout=15, allow_redirects=True)
            else:
                resp = self.session.get(action_url, params=data, timeout=15, allow_redirects=True)

            if resp.status_code != 200 or "out.Login.php" in resp.url:
                logger.error(
                    f"移动文档 #{document_id} 到文件夹 #{target_folder_id} 失败: HTTP {resp.status_code}"
                )
                return False

            target_documents = self.list_folder_contents(target_folder_id)["documents"]
            moved = any(item["id"] == int(document_id) for item in target_documents)
            if not moved:
                logger.error(f"移动文档 #{document_id} 后未在目标文件夹 #{target_folder_id} 中找到")
            return moved
        except Exception as e:
            logger.error(f"移动文档 #{document_id} 异常: {e}", exc_info=True)
            return False

    def remove_empty_folder(self, folder_id: int, parent_folder_id: int) -> bool:
        """删除空文件夹；非空或不属于指定父目录时拒绝操作。"""
        if not self._is_logged_in:
            if not self.login():
                return False

        parent_folders = self.get_subfolders(parent_folder_id)
        if folder_id not in parent_folders.values():
            logger.error(f"拒绝删除：文件夹 #{folder_id} 不属于父目录 #{parent_folder_id}")
            return False

        contents = self.list_folder_contents(folder_id)
        if contents["folders"] or contents["documents"]:
            logger.error(f"拒绝删除非空文件夹 #{folder_id}")
            return False

        try:
            action_url, method, data = self._load_operation_form(
                f"/out/out.RemoveFolder.php?folderid={folder_id}",
                "op.RemoveFolder.php",
            )
            data["folderid"] = folder_id
            if method == "post":
                resp = self.session.post(action_url, data=data, timeout=15, allow_redirects=True)
            else:
                resp = self.session.get(action_url, params=data, timeout=15, allow_redirects=True)

            if resp.status_code != 200 or "out.Login.php" in resp.url:
                logger.error(f"删除文件夹 #{folder_id} 失败: HTTP {resp.status_code}")
                return False

            removed = folder_id not in self.get_subfolders(parent_folder_id).values()
            if not removed:
                logger.error(f"删除后文件夹 #{folder_id} 仍存在")
            return removed
        except Exception as e:
            logger.error(f"删除空文件夹 #{folder_id} 异常: {e}", exc_info=True)
            return False

    def get_or_create_year_folder(self, parent_folder_id: int, year: str) -> Optional[int]:
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

        logger.error(f"无法创建年份目录 [{year}]")
        return None

    def get_or_create_category_folder(self, year_folder_id: int, category: str) -> Optional[int]:
        """在年份目录下获取或创建文档类别目录。"""
        cache_key = f"{year_folder_id}_{category}"
        if cache_key in self._category_folder_cache:
            return self._category_folder_cache[cache_key]

        target_id = self.get_subfolders(year_folder_id).get(category)
        if not target_id:
            target_id = self.create_subfolder(
                year_folder_id,
                folder_name=category,
                comment=f"{category}自动归档",
            )
        if target_id:
            self._category_folder_cache[cache_key] = target_id
        return target_id

    def _upload_new_document(self, path_obj: Path, target_fid: int,
                             doc_name: str, comment: str) -> bool:
        action_url, method, data = self._load_operation_form(
            f"/out/out.AddDocument.php?folderid={target_fid}",
            "op.AddDocument.php",
        )
        data.update({
            "folderid": target_fid,
            "name": doc_name,
            "comment": comment,
            "version_comment": comment,
            "reqversion": data.get("reqversion") or "1",
        })
        with open(path_obj, "rb") as file_obj:
            files = {"qqfile": (path_obj.name, file_obj, "application/octet-stream")}
            if method == "post":
                resp = self.session.post(
                    action_url, data=data, files=files, timeout=60, allow_redirects=True
                )
            else:
                resp = self.session.get(
                    action_url, params=data, timeout=60, allow_redirects=True
                )
        if resp.status_code != 200 or "out.Login.php" in resp.url:
            return False
        return any(
            item["name"] == doc_name
            for item in self.list_folder_contents(target_fid)["documents"]
        )

    def _upload_new_version(self, path_obj: Path, document_id: int, comment: str) -> bool:
        action_url, method, data = self._load_operation_form(
            f"/out/out.UpdateDocument.php?documentid={document_id}",
            "op.UpdateDocument.php",
        )
        data.update({"documentid": document_id, "comment": comment})
        with open(path_obj, "rb") as file_obj:
            files = {"qqfile": (path_obj.name, file_obj, "application/octet-stream")}
            if method == "post":
                resp = self.session.post(
                    action_url, data=data, files=files, timeout=60, allow_redirects=True
                )
            else:
                resp = self.session.get(
                    action_url, params=data, timeout=60, allow_redirects=True
                )
        return resp.status_code == 200 and "out.Login.php" not in resp.url

    def upload_document(
        self,
        file_path: Union[str, Path],
        doc_name: Optional[str] = None,
        comment: Optional[str] = None,
        year: Optional[str] = None,
        category: Optional[str] = None,
        folder_id: Optional[int] = None,
    ) -> bool:
        """
        上传文档至 SeedDMS（支持按年份自动归档到对应子目录）
        :param file_path: 本地待上传文件路径
        :param doc_name: 在 SeedDMS 显示的文档标题
        :param comment: 文档备注说明
        :param year: 年份（如 '2026'），开启 auto_create_year_folder 时将自动查找/创建该年份子目录
        :param category: 附件类别，用于在年份目录下创建第二级分类目录
        :param folder_id: 手动指定目标根目录 ID (优先级高于默认配置)
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
            year_folder_id = self.get_or_create_year_folder(target_fid, str(year))
            if not year_folder_id:
                return False
            target_fid = year_folder_id
            if category:
                category_folder_id = self.get_or_create_category_folder(target_fid, category)
                if not category_folder_id:
                    logger.error(f"无法创建或获取类别目录 [{category}]")
                    return False
                target_fid = category_folder_id

        doc_name = doc_name or path_obj.name
        comment = comment or "自动归档工作周报"

        try:
            existing = next(
                (
                    item for item in self.list_folder_contents(target_fid)["documents"]
                    if item["name"] == doc_name
                ),
                None,
            )
            if existing:
                success = self._upload_new_version(path_obj, existing["id"], comment)
                action = f"更新同名文档 #{existing['id']} 的版本"
            else:
                success = self._upload_new_document(path_obj, target_fid, doc_name, comment)
                action = "创建新文档"

            if success:
                logger.info(
                    f"周报归档成功: {action} | Folder #{target_fid} | [{doc_name}]"
                )
            else:
                logger.error(f"SeedDMS 归档失败: {action} | Folder #{target_fid} | [{doc_name}]")
            return success
        except Exception as e:
            logger.error(f"备份到 SeedDMS 过程中出现异常: {e}", exc_info=True)
            return False
