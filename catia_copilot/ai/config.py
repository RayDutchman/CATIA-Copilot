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

    provider_dict 中新增 provider_id / provider_type 字段，供 agent.py 路由使用：
      - provider_id：配置文件中的 key（如 "openai"、"claude-direct"）
      - provider_type：协议类型（"openai" / "anthropic" / "bedrock" / "vertex" /
                       "ollama" / "iflytek" / "github" / "openrouter" / "custom"）
        若配置中未指定 provider_type，根据 api_base / api_key 特征自动推断，
        兜底为 "openai"（向后兼容旧配置）。

    优先级：
    1. 精确匹配任意 provider 的 models 列表
    2. 只有一个 provider：透传
    3. 多个 provider：使用 default_provider 透传
    """
    model_id = model_id.strip()
    providers = cfg.get("providers", {})

    def _enrich(pid: str, provider: dict, model: dict) -> tuple[dict, dict]:
        """给 provider_dict 补充 provider_id / provider_type 字段。"""
        provider = dict(provider)
        provider.setdefault("provider_id", pid)
        # 若已显式配置 provider_type，直接用
        if "provider_type" not in provider:
            provider["provider_type"] = _infer_provider_type(pid, provider)
        return provider, model

    # 精确匹配：优先在 default_provider 里查找，避免同名 model 被错误的 provider 路由
    default_id = cfg.get("default_provider", "")
    if default_id and default_id in providers:
        for model in providers[default_id].get("models", []):
            if model.get("id", "").strip() == model_id:
                return _enrich(default_id, providers[default_id], model)

    for pid, provider in providers.items():
        for model in provider.get("models", []):
            if model.get("id", "").strip() == model_id:
                return _enrich(pid, provider, model)

    synthetic = {"id": model_id, "supports_tools": True, "max_tokens": 8192}

    if len(providers) == 1:
        pid, provider = next(iter(providers.items()))
        logger.warning("[CONFIG] 模型 %r 不在列表中，透传到 %s", model_id, provider.get("name"))
        return _enrich(pid, provider, synthetic)

    default_id = cfg.get("default_provider", "")
    if default_id and default_id in providers:
        pid, provider = default_id, providers[default_id]
    else:
        pid, provider = next(iter(providers.items()), ("", {}))
    logger.warning("[CONFIG] 模型 %r 不在列表中，透传到默认 provider %r",
                   model_id, provider.get("name"))
    return _enrich(pid, provider, synthetic)


def _infer_provider_type(provider_id: str, provider: dict) -> str:
    """根据 provider_id 和配置字段推断 provider_type（兜底逻辑）。

    优先级：
    1. provider 配置里的 provider_type 字段（已在调用方判断）
    2. provider_id 关键字匹配
    3. api_base URL 特征匹配
    4. 兜底 "openai"
    """
    pid = (provider_id or "").lower()
    base = (provider.get("api_base") or "").lower()

    # provider_id 关键字
    if pid in ("anthropic", "claude"):
        return "anthropic"
    if pid in ("bedrock", "aws", "aws_bedrock"):
        return "bedrock"
    if pid in ("vertex", "google_vertex", "gcp"):
        return "vertex"
    if pid in ("ollama",) or ":11434" in base:
        return "ollama"
    if pid in ("iflytek", "xfyun", "spark"):
        return "iflytek"
    if pid in ("github", "copilot", "github_copilot"):
        return "github"
    if pid in ("openrouter",) or "openrouter.ai" in base:
        return "openrouter"

    # api_base 特征
    if "anthropic.com" in base:
        return "anthropic"
    if "bedrock-runtime" in base or "amazonaws.com" in base:
        return "bedrock"
    if "aiplatform.googleapis.com" in base:
        return "vertex"
    if "xf-yun.com" in base or "spark-api" in base:
        return "iflytek"
    if "githubcopilot.com" in base:
        return "github"

    return "openai"


def get_default_model_id(cfg: dict) -> str:
    """返回默认模型 ID。"""
    return cfg.get("default_model", "gpt-4o")


def list_model_ids(cfg: dict) -> list[str]:
    """返回所有已启用的模型 ID 列表（扁平，供兼容旧代码使用）。"""
    ids = []
    for provider in cfg.get("providers", {}).values():
        for model in provider.get("models", []):
            if not model.get("enabled", True):  # enabled 默认为 True
                continue
            mid = model.get("id", "").strip()
            if mid:
                ids.append(mid)
    return ids


def list_models_grouped(cfg: dict) -> list[dict]:
    """返回按 provider 分组的模型列表，供 UI 下拉框分组展示。

    返回格式：
    [
        {
            "provider_key":  str,   # providers dict 中的 key
            "provider_name": str,   # provider 显示名
            "provider_type": str,   # "openai" / "anthropic" / ...
            "models": [
                {"id": str, "name": str, "enabled": bool},
                ...
            ]
        },
        ...
    ]
    只包含 models 列表非空的 provider。
    """
    groups = []
    for pid, provider in cfg.get("providers", {}).items():
        models_raw = provider.get("models", [])
        if not models_raw:
            continue
        models = []
        for m in models_raw:
            mid = m.get("id", "").strip()
            if not mid:
                continue
            models.append({
                "id":      mid,
                "name":    m.get("name", mid),
                "enabled": m.get("enabled", True),
            })
        if models:
            groups.append({
                "provider_key":  pid,
                "provider_name": provider.get("provider_type", pid),
                "provider_type": provider.get("provider_type", "openai"),
                "models":        models,
            })
    return groups

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
