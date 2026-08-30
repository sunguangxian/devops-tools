# -*- coding: utf-8 -*-
"""
Redmine API 交互封装模块
"""

import logging
from typing import Dict, Optional, Any, Union

try:
    from redminelib import Redmine
except ImportError:
    Redmine = None

logger = logging.getLogger("services.git_redmine_sync.redmine")


class RedmineService:
    """Redmine 操作服务类"""

    def __init__(self, redmine_url: str, user_keys: Dict[str, str], default_api_key: Optional[str] = None):
        self.redmine_url = redmine_url.rstrip("/")
        self.user_keys = user_keys or {}
        self.default_api_key = default_api_key
        self._status_id_cache: Dict[str, int] = {}

    def get_client(self, user_name: str) -> Optional[Any]:
        """
        获取指定 Git 用户的 Redmine 客户端实例
        """
        if Redmine is None:
            raise ImportError("未检测到 python-redmine 依赖包，请先运行: pip install python-redmine")

        api_key = self.user_keys.get(user_name) or self.default_api_key
        if not api_key:
            logger.warning(f"用户 [{user_name}] 未配置 Redmine API Key，且无默认 Key")
            return None
        return Redmine(self.redmine_url, key=api_key)

    def get_status_id_map(self, redmine: Any) -> Dict[str, int]:
        """
        获取 Redmine 状态名称到 ID 的映射（优先读缓存）
        """
        if self._status_id_cache:
            return self._status_id_cache

        try:
            mapping = {}
            for s in redmine.issue_status.all():
                mapping[s.name] = s.id
            self._status_id_cache = mapping
            return mapping
        except Exception as e:
            logger.error(f"获取 Redmine 问题状态列表失败: {e}", exc_info=True)
            return {}

    def update_issue(
        self,
        redmine: Any,
        issue_id: Union[str, int],
        status_id: Optional[int] = None,
        note: Optional[str] = None,
        done_ratio: Optional[int] = 100,
    ) -> bool:
        """
        更新 Redmine 问题状态与备注
        """
        data: Dict[str, Any] = {}
        if status_id is not None:
            data["status_id"] = status_id
            if done_ratio is not None:
                data["done_ratio"] = done_ratio
        if note:
            data["notes"] = note

        if not data:
            return False

        try:
            redmine.issue.update(int(issue_id), **data)
            logger.info(f"成功更新 Redmine 问题 #{issue_id}: status_id={status_id}, has_note={bool(note)}")
            return True
        except Exception as e:
            logger.error(f"更新 Redmine 问题 #{issue_id} 失败: {e}", exc_info=True)
            return False
