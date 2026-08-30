# -*- coding: utf-8 -*-
"""
统一日志管理模块
支持控制台彩色/格式化输出与日志文件按大小轮转（RotatingFileHandler）。
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from common.config_loader import get_project_root


def setup_logger(
    name: str = "app",
    log_filename: Optional[str] = None,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    """
    配置并返回统一格式的 Logger 实例
    :param name: Logger 名称
    :param log_filename: 日志文件名称（保存在项目 logs 目录下，如 'git_redmine_sync.log'）
    :param level: 日志级别
    :param max_bytes: 单个日志文件最大字节数
    :param backup_count: 日志文件保留备份数量
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 Handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出 Handler
    if log_filename:
        logs_dir = get_project_root() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / log_filename

        try:
            file_handler = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"无法初始化日志文件 [{log_path}]: {e}")

    return logger
