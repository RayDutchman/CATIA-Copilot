"""
AI Agent 配置管理模块。

配置文件保存在项目根目录下的 ai_config.json（已加入 .gitignore）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 配置文件名
_CONFIG_FILENAME = "ai_config.json"

# 默认配置
_DEFAULTS: dict[str, Any] = {
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o",
    "system_prompt": (
        "你是 CATIA Copilot 的 AI 助手，专门帮助工程师操作 CATIA V5。\n"
        "你可以调用工具来采集 BOM、导出文件、查找依赖、计算质量特性等。\n"
        "每次调用工具前，先简要说明你要做什么；工具返回结果后，用中文总结结果。\n"
        "如果工具调用失败，分析原因并提出解决建议。"
    ),
    "max_tool_rounds": 20,
    "temperature": 0.7,
    "timeout": 120,
}


def _get_config_path() -> Path:
    """返回配置文件的绝对路径（项目根目录 / ai_config.json）。"""
    # 本文件在 catia_copilot/ai/config.py，向上两级是项目根目录
    return Path(__file__).parent.parent.parent / _CONFIG_FILENAME


def load() -> dict[str, Any]:
    """
    加载 AI 配置。若文件不存在或解析失败，返回默认配置。
    返回的 dict 保证包含所有默认键（缺失键用默认值补全）。
    """
    cfg = dict(_DEFAULTS)
    path = _get_config_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            # 用已保存的值覆盖默认值（只覆盖已知键）
            for key in _DEFAULTS:
                if key in saved:
                    cfg[key] = saved[key]
        except Exception as e:
            logger.warning("读取 AI 配置失败，使用默认配置：%s", e)
    return cfg


def save(cfg: dict[str, Any]) -> None:
    """
    保存 AI 配置到 ai_config.json。
    只保存已知键，忽略未知键。
    """
    path = _get_config_path()
    to_save = {key: cfg[key] for key in _DEFAULTS if key in cfg}
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存 AI 配置失败：%s", e)
        raise


def get_config_path() -> Path:
    """返回配置文件路径（供 UI 显示用）。"""
    return _get_config_path()
