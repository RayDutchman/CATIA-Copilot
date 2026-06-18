"""
AI Agent 配置管理模块。

配置文件：项目根目录下的 ai_config.json（已加入 .gitignore，不提交）。
格式与 Standard-Agent-Server 的 models_config.json 完全相同，可直接照抄。

ai_config.example.json 是可提交的模板文件。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 配置文件统一存放在 %APPDATA%\CATIA Copilot\，开发与打包环境行为一致。

_USER_DATA_DIR = Path.home() / "AppData" / "Roaming" / "CATIA Copilot"
_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
_CONFIG_PATH = _USER_DATA_DIR / "ai_config.json"

# 无配置时的兜底
_FALLBACK_CONFIG: dict[str, Any] = {
    "providers": {},
    "default_provider": "",
    "default_model": "gpt-4o",
}

# DEFAULT_SYSTEM_PROMPT 定义在 tools.py，此处不重复定义。
# 使用方：from catia_copilot.ai.tools import DEFAULT_SYSTEM_PROMPT

# 运行时参数默认值
DEFAULTS_RUNTIME: dict[str, Any] = {
    "max_tool_rounds": 20,
    "temperature":     0.7,
    "timeout":         120,
}


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    """
    加载 ai_config.json。
    文件不存在或解析失败时返回兜底配置。
    返回的 dict 结构与 models_config.json 相同，额外包含运行时参数。
    """
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        logger.info("[CONFIG] 已加载 ai_config.json，providers=%s",
                    list(cfg.get("providers", {}).keys()))
    except FileNotFoundError:
        logger.warning("[CONFIG] ai_config.json 不存在，使用兜底配置")
        cfg = dict(_FALLBACK_CONFIG)
    except Exception as e:
        logger.error("[CONFIG] 加载 ai_config.json 失败：%s，使用兜底配置", e)
        cfg = dict(_FALLBACK_CONFIG)

    # 补全运行时参数
    for k, v in DEFAULTS_RUNTIME.items():
        cfg.setdefault(k, v)

    return cfg


def save(cfg: dict[str, Any]) -> None:
    """保存配置到 ai_config.json。"""
    to_save: dict[str, Any] = {}
    for k in ("providers", "default_provider", "default_model"):
        if k in cfg:
            to_save[k] = cfg[k]
    for k in DEFAULTS_RUNTIME:
        if k in cfg:
            to_save[k] = cfg[k]
    try:
        with _CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[CONFIG] 保存 ai_config.json 失败：%s", e)
        raise


def get_config_path() -> Path:
    """返回配置文件路径（供 UI 显示用）。打包后指向 %APPDATA%/CATIA Copilot/ai_config.json。"""
    return _CONFIG_PATH


# ---------------------------------------------------------------------------
# 模型路由
# ---------------------------------------------------------------------------

def get_provider_for_model(cfg: dict, model_id: str) -> tuple[dict, dict]:
    """
    根据 model_id 查找对应的 provider 和 model 配置。
    返回 (provider_dict, model_dict)。

    优先级：
    1. 精确匹配任意 provider 的 models 列表
    2. 只有一个 provider：透传
    3. 多个 provider：使用 default_provider 透传
    """
    model_id = model_id.strip()
    for provider in cfg.get("providers", {}).values():
        for model in provider.get("models", []):
            if model["id"] == model_id:
                return provider, model

    synthetic = {"id": model_id, "supports_tools": True, "max_tokens": 8192}
    providers = cfg.get("providers", {})

    if len(providers) == 1:
        provider = next(iter(providers.values()))
        logger.warning("[CONFIG] 模型 %r 不在列表中，透传到 %s", model_id, provider.get("name"))
        return provider, synthetic

    default_id = cfg.get("default_provider", "")
    provider = providers.get(default_id) or next(iter(providers.values()), {})
    logger.warning("[CONFIG] 模型 %r 不在列表中，透传到默认 provider %r",
                   model_id, provider.get("name"))
    return provider, synthetic


def get_default_model_id(cfg: dict) -> str:
    """返回默认模型 ID。"""
    return cfg.get("default_model", "gpt-4o")


def list_model_ids(cfg: dict) -> list[str]:
    """返回所有已配置的模型 ID 列表（供 UI 下拉框使用）。"""
    ids = []
    for provider in cfg.get("providers", {}).values():
        for model in provider.get("models", []):
            mid = model.get("id", "").strip()
            if mid:
                ids.append(mid)
    return ids


# ---------------------------------------------------------------------------
# 从 API 拉取模型列表
# ---------------------------------------------------------------------------

def fetch_models_from_api(api_base: str, api_key: str, timeout: int = 15) -> list[dict]:
    """
    调用 GET {api_base}/v1/models，返回模型 dict 列表。
    每个 dict 格式：{id, name, supports_tools, max_tokens}
    失败时返回空列表。
    """
    url = f"{api_base.rstrip('/')}/v1/models"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(5, timeout),  # (连接超时 5s, 读取超时 timeout s)
        )
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("data", []):
            mid = m.get("id", "").strip()
            if mid:
                models.append({
                    "id":             mid,
                    "name":           mid,
                    "supports_tools": True,
                    "max_tokens":     8192,
                })
        logger.info("[CONFIG] 从 %s 拉取到 %d 个模型", url, len(models))
        return models
    except Exception as e:
        logger.warning("[CONFIG] 拉取模型列表失败：%s", e)
        return []
