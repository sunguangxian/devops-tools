# -*- coding: utf-8 -*-
"""
独立配置加载器模块
支持为每个脚本/服务加载专属配置文件（如 config/<service_name>.yaml），
并自动回退到示例模板（如 config/<service_name>.example.yaml）及支持环境变量自定义。
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("common.config_loader")

# 获取项目根目录 (common 目录的上一级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_project_root() -> Path:
    """获取项目根目录路径"""
    return PROJECT_ROOT


def find_service_config_file(service_name: str) -> Optional[Path]:
    """
    寻找指定服务/脚本的独立配置文件路径，优先级：
    1. 环境变量 <SERVICE_NAME_UPPER>_CONFIG_PATH
    2. config/<service_name>.yaml
    3. config/<service_name>.yml
    4. config/<service_name>.json
    5. config/<service_name>.example.yaml (仅作缺省提示与降级)
    """
    # 1. 检查环境变量
    env_var_name = f"{service_name.upper()}_CONFIG_PATH"
    env_path = os.getenv(env_var_name)
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        logger.warning(f"环境变量 {env_var_name} 指定的文件不存在: {env_path}")

    config_dir = PROJECT_ROOT / "config"

    # 2. 查找实际生产配置文件
    candidates = [
        config_dir / f"{service_name}.yaml",
        config_dir / f"{service_name}.yml",
        config_dir / f"{service_name}.json",
    ]

    for p in candidates:
        if p.exists():
            return p

    # 3. 兜底查找对应服务的示例配置
    example_candidates = [
        config_dir / f"{service_name}.example.yaml",
        config_dir / f"{service_name}.example.yml",
        config_dir / f"{service_name}.example.json",
    ]

    for p in example_candidates:
        if p.exists():
            logger.warning(
                f"[{service_name}] 未找到正式配置文件 {service_name}.yaml，将尝试使用示例配置: {p.name}。"
                f"请复制 config/{p.name} 为 config/{service_name}.yaml 并配置实际参数。"
            )
            return p

    return None


def load_file_content(file_path: Path) -> Dict[str, Any]:
    """
    解析指定路径的 YAML 或 JSON 配置文件
    """
    try:
        suffix = file_path.suffix.lower()
        if suffix in [".yaml", ".yml"]:
            try:
                import yaml
                with open(file_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except ImportError:
                logger.error("解析 YAML 配置失败：请先安装 PyYAML (pip install pyyaml)")
                return {}
        elif suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        else:
            logger.error(f"不支持的配置文件类型: {file_path}")
            return {}
    except Exception as e:
        logger.error(f"读取配置文件出错 [{file_path}]: {e}", exc_info=True)
        return {}


# 缓存已加载的配置，避免频繁读取磁盘
_cached_service_configs: Dict[str, Dict[str, Any]] = {}


def get_service_config(service_name: str, reload: bool = False) -> Dict[str, Any]:
    """
    获取指定服务/脚本的独立配置字典
    :param service_name: 服务/脚本名称，例如 'git_redmine_sync'
    :param reload: 是否强制重新加载
    """
    global _cached_service_configs

    if not reload and service_name in _cached_service_configs:
        return _cached_service_configs[service_name]

    config_path = find_service_config_file(service_name)
    if not config_path:
        logger.error(f"未找到服务 [{service_name}] 的任何配置文件！请在 config/ 下创建 {service_name}.yaml")
        return {}

    cfg = load_file_content(config_path)
    _cached_service_configs[service_name] = cfg
    return cfg
