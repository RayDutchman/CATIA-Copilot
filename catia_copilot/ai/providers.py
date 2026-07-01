"""
AI 多 Provider 支持层。

从 AiAssistant 参考实现提取，去掉所有服务端依赖（FastAPI、SQLAlchemy、多用户 CRUD），
保留纯客户端函数/类，供 agent.py 调用。

provider_type 字段对应值：
  openai      -- OpenAI 兼容协议（默认，向后兼容）
  anthropic   -- Anthropic Messages API（原生流式）
  bedrock     -- AWS Bedrock converse-stream（需要 boto3）
  vertex      -- Google Vertex AI（需要 google-auth）
  ollama      -- Ollama 原生 /api/chat（NDJSON 流）
  iflytek     -- 讯飞星火 HTTP OpenAI 兼容
  github      -- GitHub Copilot（oauth_token 换 copilot_token）
  openrouter  -- OpenRouter（OpenAI 兼容 + 特殊 headers）
  custom      -- 自定义 OpenAI 兼容端点
"""
from __future__ import annotations

import json
import logging
import re
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# GitHub Copilot token 缓存 {oauth_token: (copilot_token, expires_at)}
_copilot_token_cache: dict[str, tuple[str, float]] = {}
_copilot_token_lock = threading.Lock()

_RESPONSES_API_MODEL_PREFIXES = (
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
)

def is_responses_api_model(model_id: str) -> bool:
    """判断 GitHub Copilot 模型是否必须走 /v1/responses 端点"""
    if not model_id:
        return False
    m = model_id.lower().split("/")[-1]
    return any(m.startswith(p) for p in _RESPONSES_API_MODEL_PREFIXES)


PROVIDER_DEFS = {
    "github": {
        "name": "GitHub Copilot",
        "group": "free",
        "icon": "GH",
        "color": "#4ec94e",
        "auth_type": "oauth",          # oauth = 设备验证流程
        "api_format": "copilot",        # copilot = 自有格式 (OpenAI兼容但有特殊header)
        "base_url": "https://api.individual.githubcopilot.com",
        "description": "通过 GitHub OAuth 登录，免费使用",
        "fields": [],                   # oauth 不需要手动输入
        "tags": ["免费"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "group": "platform",
        "icon": "OR",
        "color": "#c084fc",
        "auth_type": "api_key",
        "api_format": "openai",         # OpenAI 兼容格式
        "base_url": "https://openrouter.ai/api/v1",
        "description": "一个Key调用所有模型",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-or-v1-...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": ["⭐推荐"],
        "default_models": [
            {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4.6"},
            {"id": "anthropic/claude-opus-4-20250514", "name": "Claude Opus 4.6"},
            {"id": "openai/gpt-4o", "name": "GPT-4o"},
            {"id": "openai/o4-mini", "name": "o4-mini"},
            {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1"},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3"},
            {"id": "google/gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        ],
    },
    "anthropic": {
        "name": "Anthropic",
        "group": "direct",
        "icon": "A",
        "color": "#d4a574",
        "auth_type": "api_key",
        "api_format": "anthropic",      # Anthropic Messages API 格式
        "base_url": "https://api.anthropic.com",
        "description": "Claude 官方直连",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-ant-...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL", "placeholder": "https://api.anthropic.com", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "max_input": 1000000, "max_output": 128000},
            {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "max_input": 1000000, "max_output": 128000},
            {"id": "claude-opus-4-5-20251101", "name": "Claude Opus 4.5", "max_input": 200000, "max_output": 64000},
            {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5", "max_input": 1000000, "max_output": 64000},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "max_input": 200000, "max_output": 64000},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "group": "direct",
        "icon": "OA",
        "color": "#74aa9c",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://api.openai.com/v1",
        "description": "GPT / o 系列官方直连",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "o4-mini", "name": "o4-mini"},
            {"id": "gpt-4.1", "name": "GPT-4.1"},
        ],
    },
    "deepseek": {
        "name": "DeepSeek",
        "group": "direct",
        "icon": "DS",
        "color": "#667eea",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "description": "DeepSeek V4 Flash / V4 Pro",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro (思考模式)"},
        ],
    },
    "alibaba": {
        "name": "阿里云通义",
        "group": "direct",
        "icon": "🔮",
        "color": "#ff6a00",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "description": "通义千问 Qwen 系列模型",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [
            {"id": "qwen-plus", "name": "Qwen Plus"},
            {"id": "qwen-turbo", "name": "Qwen Turbo"},
            {"id": "qwen-max", "name": "Qwen Max"},
            {"id": "qwen3-235b-a22b", "name": "Qwen3 235B"},
            {"id": "qwen3.6-plus", "name": "Qwen3.6 Plus"},
        ],
    },
    "google": {
        "name": "Google Gemini",
        "group": "direct",
        "icon": "G",
        "color": "#4285f4",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "description": "Google Gemini 系列模型，支持 Realtime 语音",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "AIzaSy...", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite"},
        ],
    },
    "volcengine": {
        "name": "火山方舟(豆包)",
        "group": "direct",
        "icon": "🌋",
        "color": "#ff6a3d",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "description": "字节跳动豆包大模型，高性价比",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "输入火山方舟 API Key", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
            {"key": "s2s_app_id", "label": "语音 App ID (S2S)", "placeholder": "语音服务的 App ID（可选，用于实时语音通话）", "type": "text", "required": False},
            {"key": "s2s_access_key", "label": "语音 Access Key (S2S)", "placeholder": "语音服务的 Access Key（可选）", "type": "password", "required": False},
        ],
        "tags": ["国产"],
        "default_models": [
            {"id": "doubao-seed-2-0-pro-260215", "name": "豆包 Seed 2.0 Pro"},
            {"id": "doubao-seed-2-0-lite-260215", "name": "豆包 Seed 2.0 Lite"},
            {"id": "doubao-seed-2-0-mini-260215", "name": "豆包 Seed 2.0 Mini"},
            {"id": "doubao-1.5-pro-256k-250115", "name": "豆包 1.5 Pro 256K"},
            {"id": "deepseek-v3-2-251201", "name": "DeepSeek V3 (方舟)"},
            {"id": "deepseek-r1-250528", "name": "DeepSeek R1 (方舟)"},
        ],
    },
    "bigmodel": {
        "name": "智谱 BigModel",
        "group": "direct",
        "icon": "智",
        "color": "#3859ff",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "description": "智谱 AI GLM 系列模型（OpenAI 兼容）",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "在 https://open.bigmodel.cn/usercenter/apikeys 获取", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": ["国产"],
        "default_models": [
            {"id": "glm-5.1", "name": "GLM-5.1"},
            {"id": "glm-4.7", "name": "GLM-4.7"},
            {"id": "glm-4.5-air", "name": "GLM-4.5 Air"},
            {"id": "glm-4-flash", "name": "GLM-4 Flash (免费)"},
            {"id": "glm-4v-plus", "name": "GLM-4V Plus (视觉)"},
        ],
        "help": {
            "title": "智谱 BigModel 接入步骤",
            "steps": [
                "访问智谱 AI 开放平台并登录账号",
                "进入「API Keys」管理页面创建一个 API Key",
                "复制 API Key 粘贴到上方输入框",
                "保存后会自动拉取你账号下可用的模型列表",
            ],
            "links": [
                {"label": "🔑 获取 API Key", "url": "https://open.bigmodel.cn/usercenter/apikeys"},
                {"label": "📖 模型概览", "url": "https://docs.bigmodel.cn/cn/guide/models/overview"},
                {"label": "📖 HTTP API 文档", "url": "https://docs.bigmodel.cn/cn/guide/develop/http/introduction"},
                {"label": "💰 价格说明", "url": "https://www.bigmodel.cn/pricing"},
            ],
            "tip": "智谱 GLM 系列完全 OpenAI 兼容，支持流式/工具调用/多模态。如使用 GLM Coding 套餐需改 base_url 为 /api/coding/paas/v4。",
        },
    },
    "minimax": {
        "name": "MiniMax",
        "group": "direct",
        "icon": "MM",
        "color": "#10b981",
        "auth_type": "api_key",
        "api_format": "anthropic",
        "base_url": "https://api.minimaxi.com/anthropic",
        "description": "MiniMax M2.7 推理模型（Anthropic 兼容）",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "sk-...", "type": "password", "required": True},
        ],
        "tags": ["推理模型"],
        "default_models": [
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7"},
            {"id": "MiniMax-M2.7-highspeed", "name": "MiniMax M2.7 Highspeed"},
        ],
    },
    "stepfun": {
        "name": "阶跃星辰",
        "group": "direct",
        "icon": "阶",
        "color": "#6366f1",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://api.stepfun.com/v1",
        "description": "阶跃星辰 Step 系列模型，支持 Realtime 实时语音对话",
        "fields": [
            {"key": "api_key", "label": "API Key", "placeholder": "在 https://platform.stepfun.com/ 获取", "type": "password", "required": True},
            {"key": "base_url", "label": "Base URL (可选)", "placeholder": "留空使用官方地址，填写则走自定义中转", "type": "text", "required": False},
        ],
        "tags": ["国产", "实时语音"],
        "default_models": [
            {"id": "step-2-16k", "name": "Step 2 16K"},
            {"id": "step-2-mini", "name": "Step 2 Mini"},
            {"id": "step-1-8k", "name": "Step 1 8K"},
        ],
        "help": {
            "title": "阶跃星辰接入步骤",
            "steps": [
                "访问阶跃星辰开放平台并注册账号",
                "进入「API 密钥」页面创建一个 API Key",
                "复制 API Key 粘贴到上方输入框并保存",
                "实时语音(Realtime)功能需要使用 Step-Audio 模型，在语音设置中选择阶跃即可",
            ],
            "links": [
                {"label": "🔑 获取 API Key", "url": "https://platform.stepfun.com/apiKey"},
                {"label": "📖 Realtime API 文档", "url": "https://platform.stepfun.com/docs/zh/api-reference/realtime/chat"},
                {"label": "📖 模型列表", "url": "https://platform.stepfun.com/docs/zh/model"},
                {"label": "💰 价格说明", "url": "https://platform.stepfun.com/pricing"},
            ],
            "tip": "阶跃 Realtime API 兼容 OpenAI 协议，支持端到端语音对话和实时翻译。Step-Audio 2 系列模型支持中英双语实时语音交互。",
        },
    },
    "iflytek": {
        "name": "讯飞星火",
        "group": "direct",
        "icon": "讯",
        "color": "#e65100",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "https://spark-api-open.xf-yun.com",
        "description": "讯飞星火大模型，OpenAI 兼容 HTTP 接口（X2 / X1.5 / Ultra-32K / Pro / Pro-128K / Lite）",
        "fields": [
            {"key": "api_password", "label": "APIPassword", "placeholder": "讯飞控制台「HTTP 服务接口认证信息 → APIPassword」（一长串）", "type": "password", "required": True},
            {"key": "app_id", "label": "APPID（可选，S2S 用）", "placeholder": "讯飞控制台「APPID」", "type": "text", "required": False},
            {"key": "api_secret", "label": "APISecret（可选，S2S 用）", "placeholder": "讯飞控制台「APISecret」", "type": "password", "required": False},
            {"key": "api_key", "label": "APIKey（可选，S2S 用）", "placeholder": "讯飞控制台「APIKey」", "type": "password", "required": False},
        ],
        "tags": ["国产"],
        "default_models": [
            {"id": "spark-x2",     "name": "Spark X2 (推理 64K/128K)"},
            {"id": "spark-x1.5",   "name": "Spark X1.5 (推理)"},
            {"id": "4.0Ultra",     "name": "Spark Ultra-32K"},
            {"id": "pro-128k",     "name": "Spark Pro-128K"},
            {"id": "generalv3",    "name": "Spark Pro (8K)"},
            {"id": "lite",         "name": "Spark Lite (4K)"},
            {"id": "generalv3.5",  "name": "Spark Max (8K, 26.3 下线)"},
            {"id": "max-32k",      "name": "Spark Max-32K (26.3 下线)"},
        ],
        "help": {
            "title": "讯飞星火接入步骤",
            "steps": [
                "访问讯飞开放平台 → 控制台 → 你的应用",
                "找到「HTTP 服务接口认证信息」，复制 APIPassword（一长串）",
                "把 APIPassword 粘贴到上方第一个输入框并保存",
                "下面三个 WebSocket 凭证（APPID/APISecret/APIKey）只在使用 S2S 实时语音时才需要",
            ],
            "links": [
                {"label": "🔑 创建应用获取凭证", "url": "https://www.xfyun.cn/services/online.spr"},
                {"label": "📖 OpenAI 兼容文档", "url": "https://www.xfyun.cn/doc/spark/HTTP%E8%B0%83%E7%94%A8%E6%96%87%E6%A1%A3.html"},
                {"label": "📖 超拟人交互 (S2S)", "url": "https://www.xfyun.cn/doc/spark/sparkos_interactive.html"},
                {"label": "💰 价格说明", "url": "https://www.xfyun.cn/doc/spark/sparkos_interactive.html"},
            ],
            "tip": "文本聊天（4.0 Ultra/3.5/Pro/Lite）只需要 APIPassword 一个字段。WebSocket 三件套用于将来的端到端实时语音 (S2S)，可以暂时留空。",
        },
    },
    "bedrock": {
        "name": "AWS Bedrock",
        "group": "cloud",
        "icon": "AWS",
        "color": "#ff9900",
        "auth_type": "aws",
        "api_format": "bedrock",
        "base_url": "",                 # 动态构建
        "description": "企业级 AWS 云服务",
        "fields": [
            {"key": "aws_access_key_id", "label": "Access Key ID", "placeholder": "AKIA...", "type": "text", "required": True},
            {"key": "aws_secret_access_key", "label": "Secret Access Key", "placeholder": "", "type": "password", "required": True},
            {"key": "aws_region", "label": "Region", "placeholder": "us-east-1", "type": "select",
             "options": ["us-east-1", "us-west-2", "eu-west-1", "ap-northeast-1"], "required": True},
        ],
        "tags": [],
        "default_models": [
            {"id": "anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4.6"},
            {"id": "anthropic.claude-opus-4-20250514-v1:0", "name": "Claude Opus 4.6"},
        ],
    },
    "vertex": {
        "name": "Google Vertex AI",
        "group": "cloud",
        "icon": "GC",
        "color": "#4285f4",
        "auth_type": "gcp",
        "api_format": "vertex",
        "base_url": "",                 # 动态构建
        "description": "企业级 Google 云服务",
        "fields": [
            {"key": "gcp_project_id", "label": "Project ID", "placeholder": "my-project-123", "type": "text", "required": True},
            {"key": "gcp_service_account_json", "label": "服务账号 JSON Key", "placeholder": "可选：粘贴 JSON；留空则使用本机 ADC", "type": "textarea", "required": False},
            {"key": "gcp_region", "label": "Region", "placeholder": "us-central1", "type": "text", "required": True},
        ],
        "help": {
            "title": "如何获取 Google Vertex AI 凭证",
            "steps": [
                "1) 创建/选择 GCP 项目，记下 Project ID 填入上方",
                "2) 在「APIs & Services」启用 Vertex AI API（必须）",
                "3) 在「IAM & Admin → Service Accounts」创建服务账号，授予角色 Vertex AI User",
                "4) 给该服务账号「创建密钥 → JSON」，下载 JSON 后整段粘贴到上方",
                "5) 首次使用建议同时启用 Cloud Resource Manager / Service Usage API（便于排查权限）",
            ],
            "links": [
                {"label": "① 创建/选择项目", "url": "https://console.cloud.google.com/projectcreate"},
                {"label": "② 启用 Vertex AI API（必须）", "url": "https://console.cloud.google.com/apis/library/aiplatform.googleapis.com"},
                {"label": "③ 创建服务账号", "url": "https://console.cloud.google.com/iam-admin/serviceaccounts"},
                {"label": "④ 在 IAM 中授予「Vertex AI User」角色", "url": "https://console.cloud.google.com/iam-admin/iam"},
                {"label": "⑤ 启用 Cloud Resource Manager API（可选）", "url": "https://console.cloud.google.com/apis/library/cloudresourcemanager.googleapis.com"},
                {"label": "⑥ 启用 Service Usage API（可选）", "url": "https://console.cloud.google.com/apis/library/serviceusage.googleapis.com"},
                {"label": "📖 Vertex AI 模型文档", "url": "https://cloud.google.com/vertex-ai/generative-ai/docs/learn/models"},
            ],
            "tip": "若服务账号 JSON 留空，系统会自动使用本机 ADC（gcloud auth application-default login 生成的凭证）。Region 默认 us-central1。",
        },
        "tags": [],
        "default_models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite"},
            {"id": "claude-opus-4-7", "name": "Vertex Claude Opus 4.7"},
            {"id": "claude-opus-4-6", "name": "Vertex Claude Opus 4.6"},
            {"id": "claude-sonnet-4-6", "name": "Vertex Claude Sonnet 4.6"},
            {"id": "claude-opus-4-5", "name": "Vertex Claude Opus 4.5"},
            {"id": "claude-sonnet-4-5", "name": "Vertex Claude Sonnet 4.5"},
            {"id": "claude-haiku-4-5", "name": "Vertex Claude Haiku 4.5"},
        ],
    },
    "custom": {
        "name": "本地/自定义",
        "group": "custom",
        "icon": "🏠",
        "color": "#22c55e",
        "auth_type": "api_key",
        "api_format": "openai",
        "base_url": "",                 # 用户自定义
        "description": "Ollama / vLLM / 自定义兼容API",
        "fields": [
            {"key": "local_type", "label": "服务类型", "type": "select", "options": [
                {"value": "ollama", "label": "🦙 Ollama (本地)"},
                {"value": "vllm", "label": "⚡ vLLM (本地)"},
                {"value": "custom", "label": "🔗 自定义URL"},
            ], "required": True},
            {"key": "custom_name", "label": "连接名称", "placeholder": "例: 小主vLLM、公司内网API", "type": "text", "required": True, "show_if": {"local_type": "custom"}},
            {"key": "base_url", "label": "Base URL", "placeholder": "http://192.168.1.100:8080/v1", "type": "text", "required": True, "show_if": {"local_type": "custom"}},
            {"key": "api_key", "label": "API Key", "placeholder": "sk-... (无密钥留空)", "type": "password", "required": False, "show_if": {"local_type": "custom"}},
            {"key": "custom_model_name", "label": "模型名称", "placeholder": "例: llama-3.1-70b (留空自动检测)", "type": "text", "required": False},
        ],
        "tags": [],
        "default_models": [],
    },
}

# Provider 排序（前端展示顺序）
PROVIDER_ORDER = ["github", "openrouter", "anthropic", "openai", "deepseek", "alibaba", "google", "volcengine", "bigmodel", "minimax", "stepfun", "iflytek", "bedrock", "vertex", "custom"]
GROUP_NAMES = {
    "free": "🆓 免费",
    "platform": "🌐 聚合平台",
    "direct": "🔗 官方直连",
    "cloud": "☁️ 企业云服务",
    "custom": "🛠️ 自定义",
}


# ============================================================
# 已知模型上下文限制（API 不返回时的补充数据源）
# ============================================================


# ============================================================
# 思考级别(Thinking Level)定义
# ============================================================
# 各模型支持的思考档位及对应 API 参数
# 返回格式: {"levels": ["off","low","medium","high"], "default": "medium"}
# 不在此表中的模型 = 不支持思考级别

def get_thinking_config(model_id: str, api_url: str = "") -> dict | None:
    """根据模型ID返回思考级别配置，不支持则返回None"""
    m = model_id.lower().split("/")[-1]  # 去掉 provider 前缀

    # Opus 4.7+ 只支持 adaptive thinking (type=adaptive + output_config.effort)
    # 必须在通用 claude-opus-4 匹配之前优先匹配
    if m.startswith("claude-opus-4-7") or m.startswith("claude-opus-4.7"):
        return {
            "levels": [
                {"id": "off",    "label": "Off"},
                {"id": "low",    "label": "Low"},
                {"id": "medium", "label": "Medium"},
                {"id": "high",   "label": "High"},
                {"id": "xhigh",  "label": "XHigh"},
                {"id": "max",    "label": "Max"},
            ],
            "default": "high",
            "param_type": "anthropic_adaptive",  # thinking.type=adaptive + output_config.effort
        }

    # Claude Opus 4.6 / Sonnet 4.6+ 推荐 adaptive, 也兼容 enabled
    if any(m.startswith(p) for p in (
        "claude-sonnet-4-6", "claude-sonnet-4.6",
        "claude-opus-4-6", "claude-opus-4.6",
        "claude-haiku-4",
    )) or any(x in m for x in ("claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4.")):
        return {
            "levels": [
                {"id": "off",    "label": "Off"},
                {"id": "low",    "label": "Low"},
                {"id": "medium", "label": "Medium"},
                {"id": "high",   "label": "High"},
                {"id": "max",    "label": "Max"},
            ],
            "default": "high",
            "param_type": "anthropic_adaptive",
        }

    # Claude Sonnet 4.5 / Opus 4.5 等旧模型: enabled + budget_tokens
    if any(m.startswith(p) for p in (
        "claude-sonnet-4", "claude-opus-4",
        # Copilot 格式(用点号)
    )) or any(x in m for x in ("claude-sonnet-4.", "claude-opus-4.")):
        return {
            "levels": [
                {"id": "off",    "label": "Off",    "budget_tokens": 0},
                {"id": "low",    "label": "Low",    "budget_tokens": 2048},
                {"id": "medium", "label": "Medium", "budget_tokens": 10240},
                {"id": "high",   "label": "High",   "budget_tokens": 32768},
                {"id": "max",    "label": "Max",    "budget_tokens": 131072},
            ],
            "default": "medium",
            "param_type": "anthropic_thinking",  # thinking.budget_tokens
        }

    # OpenAI o1 / o3 / o4 系列: reasoning_effort
    if any(m.startswith(p) for p in ("o1", "o3", "o4")):
        return {
            "levels": [
                {"id": "low",    "label": "Low"},
                {"id": "medium", "label": "Medium"},
                {"id": "high",   "label": "High"},
            ],
            "default": "medium",
            "param_type": "openai_reasoning",  # reasoning_effort
        }

    # Gemini 2.5 Pro / Flash: thinkingConfig.thinkingBudget (OpenAI compat: reasoning_effort)
    if "gemini-2.5" in m or "gemini-2-5" in m:
        return {
            "levels": [
                {"id": "off",    "label": "Off",    "budget_tokens": 0},
                {"id": "low",    "label": "Low",    "budget_tokens": 2048},
                {"id": "medium", "label": "Medium", "budget_tokens": 8192},
                {"id": "high",   "label": "High",   "budget_tokens": 24576},
            ],
            "default": "medium",
            "param_type": "gemini_thinking",  # OpenAI compat: reasoning_effort 或 google native
        }

    # Gemma4 / QwQ 等支持 thinking 的模型
    if any(x in m for x in ("gemma-4", "gemma4", "qwq")):
        # 根据 api_url 区分: Ollama原生用 think 参数, vLLM/其他用 chat_template_kwargs
        _is_ollama_api = is_ollama_url(api_url) if api_url else True  # 无url时默认ollama(兼容旧调用)
        return {
            "levels": [
                {"id": "off",    "label": "Off",    "budget_tokens": 0},
                {"id": "low",    "label": "Low",    "budget_tokens": 2048},
                {"id": "medium", "label": "Medium", "budget_tokens": 8192},
                {"id": "high",   "label": "High",   "budget_tokens": 32768},
            ],
            "default": "off",
            "param_type": "ollama_think" if _is_ollama_api else "vllm_thinking",
        }

    # DeepSeek R1: 内置思考，无法关闭/调整
    if "deepseek-r1" in m or "deepseek-reasoner" in m or "deepseek-v4-pro" in m:
        return None  # 无法控制

    return None


KNOWN_MODEL_LIMITS = {
    # 讯飞星火 (HTTP OpenAI 兼容, base=https://spark-api-open.xf-yun.com, 不同模型走不同子路径)
    # X2/X1.5 走 /x2/ /v2/ , 其余走 /v1/  (详见 get_api_url 里的路由)
    "spark-x2":             {"max_input": 65536,   "max_output": 131072},  # X2 推理: 输入64K 输出128K
    "spark-x1.5":           {"max_input": 32768,   "max_output": 32768},   # X1.5 推理
    "4.0Ultra":             {"max_input": 32768,   "max_output": 8192},    # Ultra-32K (控制台显示 32K)
    "pro-128k":             {"max_input": 131072,  "max_output": 4096},
    "max-32k":              {"max_input": 32768,   "max_output": 4096},
    "generalv3.5":          {"max_input": 8192,    "max_output": 4096},    # Max
    "generalv3":            {"max_input": 8192,    "max_output": 4096},    # Pro
    "lite":                 {"max_input": 4096,    "max_output": 1024},
    # DeepSeek V4 (2026-04-24 发布, 1M ctx)
    "deepseek-v4-flash":    {"max_input": 1000000, "max_output": 384000},
    "deepseek-v4-pro":      {"max_input": 1000000, "max_output": 384000},
    # DeepSeek V3/R1 (将于 2026-07-24 停用, 实际已路由到 V4-Flash)
    "deepseek-chat":        {"max_input": 1000000, "max_output": 384000},
    "deepseek-reasoner":    {"max_input": 1000000, "max_output": 384000},
    # 阿里云通义 Qwen
    "qwen3.6-plus":         {"max_input": 1000000, "max_output": 65536},
    "qwen3-235b-a22b":      {"max_input": 128000,  "max_output": 8192},
    "qwen-plus":            {"max_input": 1000000, "max_output": 32768},
    "qwen-turbo":           {"max_input": 1000000, "max_output": 8192},
    "qwen-max":             {"max_input": 32768,   "max_output": 8192},
    # OpenAI
    "gpt-4o":               {"max_input": 128000,  "max_output": 16384},
    "gpt-4o-mini":          {"max_input": 128000,  "max_output": 16384},
    "gpt-4.1":              {"max_input": 1047576, "max_output": 32768},
    "gpt-4.1-mini":         {"max_input": 1047576, "max_output": 32768},
    "gpt-4.1-nano":         {"max_input": 1047576, "max_output": 32768},
    "o1":                   {"max_input": 200000,  "max_output": 100000},
    "o1-mini":              {"max_input": 128000,  "max_output": 65536},
    "o3":                   {"max_input": 200000,  "max_output": 100000},
    "o3-mini":              {"max_input": 200000,  "max_output": 100000},
    "o4-mini":              {"max_input": 200000,  "max_output": 100000},
    # Anthropic (直连/Vertex Partner Models fallback)
    "claude-opus-4-7":      {"max_input": 200000,  "max_output": 128000},
    "claude-opus-4-6":      {"max_input": 1000000, "max_output": 128000},
    "claude-sonnet-4-6":    {"max_input": 1000000, "max_output": 128000},
    "claude-opus-4-5":      {"max_input": 200000,  "max_output": 64000},
    "claude-sonnet-4-5":    {"max_input": 1000000, "max_output": 64000},
    "claude-haiku-4-5":     {"max_input": 200000,  "max_output": 64000},
    "claude-opus-4-5-20251101":   {"max_input": 200000, "max_output": 64000},
    "claude-sonnet-4-5-20250929": {"max_input": 1000000, "max_output": 64000},
    "claude-haiku-4-5-20251001":  {"max_input": 200000, "max_output": 64000},
    # OpenRouter 格式
    "anthropic/claude-opus-4-6":    {"max_input": 1000000, "max_output": 128000},
    "anthropic/claude-sonnet-4-6":  {"max_input": 1000000, "max_output": 128000},
    "openai/gpt-4o":                {"max_input": 128000,  "max_output": 16384},
    "deepseek/deepseek-chat":       {"max_input": 1000000, "max_output": 384000},
    "deepseek/deepseek-reasoner":   {"max_input": 1000000, "max_output": 384000},
    # GitHub Copilot 格式 (带点号)
    "claude-opus-4.6":      {"max_input": 200000,  "max_output": 16384},
    "claude-sonnet-4.6":    {"max_input": 200000,  "max_output": 16384},
    # 火山方舟(豆包)
    "doubao-seed-2-0-pro-260215":   {"max_input": 224000,  "max_output": 32768},
    "doubao-seed-2-0-lite-260215":  {"max_input": 224000,  "max_output": 32768},
    "doubao-seed-2-0-mini-260215":  {"max_input": 224000,  "max_output": 32768},
    "doubao-1.5-pro-256k-250115":   {"max_input": 224000,  "max_output": 32768},
    "deepseek-v3-2-251201":         {"max_input": 1000000, "max_output": 384000},
    "deepseek-r1-250528":           {"max_input": 1000000, "max_output": 384000},
    # MiniMax
    "MiniMax-M2.7":         {"max_input": 196608,  "max_output": 128000},
    "MiniMax-M2.7-highspeed": {"max_input": 196608,  "max_output": 128000},
    # OpenRouter 格式 (更多)
    "anthropic/claude-sonnet-4-20250514":   {"max_input": 1000000, "max_output": 128000},
    "anthropic/claude-opus-4-20250514":     {"max_input": 1000000, "max_output": 128000},
    "openai/o4-mini":                       {"max_input": 200000,  "max_output": 100000},
    "deepseek/deepseek-r1":                 {"max_input": 1000000, "max_output": 384000},
    # AWS Bedrock 格式
    "anthropic.claude-sonnet-4-20250514-v1:0": {"max_input": 1000000, "max_output": 128000},
    "anthropic.claude-opus-4-20250514-v1:0":   {"max_input": 1000000, "max_output": 128000},
    # Google Gemini (官方API实测确认 input=1048576, 2025-2026)
    "gemini-2.0-flash":              {"max_input": 1048576, "max_output": 8192},
    "gemini-2.0-flash-lite":         {"max_input": 1048576, "max_output": 8192},
    "gemini-2.0-flash-001":          {"max_input": 1048576, "max_output": 8192},
    "gemini-2.5-flash":              {"max_input": 1048576, "max_output": 65536},
    "gemini-2.5-flash-lite":         {"max_input": 1048576, "max_output": 65536},
    "gemini-2.5-pro":                {"max_input": 1048576, "max_output": 65536},
    "gemini-3-pro-preview":          {"max_input": 1048576, "max_output": 65536},
    "gemini-3-flash-preview":        {"max_input": 1048576, "max_output": 65536},
    "gemini-3.1-pro-preview":        {"max_input": 1048576, "max_output": 65536},
    "gemini-3.1-flash-lite-preview": {"max_input": 1048576, "max_output": 65536},
    "gemini-3.1-flash-preview":      {"max_input": 1048576, "max_output": 65536},
    # OpenRouter 别名
    "google/gemini-2.5-pro":         {"max_input": 1048576, "max_output": 65536},
    "google/gemini-2.5-flash":       {"max_input": 1048576, "max_output": 65536},
    "google/gemini-3-pro-preview":   {"max_input": 1048576, "max_output": 65536},
    "google/gemini-3.1-pro-preview": {"max_input": 1048576, "max_output": 65536},
    # ===== OpenAI gpt-5.x 系列 (官方公开数据) =====
    "gpt-5":                {"max_input": 272000,  "max_output": 128000},
    "gpt-5-mini":           {"max_input": 272000,  "max_output": 128000},
    "gpt-5-nano":           {"max_input": 272000,  "max_output": 128000},
    "gpt-5-pro":            {"max_input": 400000,  "max_output": 32000},
    "gpt-5.1":              {"max_input": 272000,  "max_output": 128000},
    "gpt-5.1-mini":         {"max_input": 272000,  "max_output": 128000},
    "gpt-5.2":              {"max_input": 272000,  "max_output": 128000},
    "gpt-5.2-mini":         {"max_input": 272000,  "max_output": 128000},
    "gpt-5.2-codex":        {"max_input": 400000,  "max_output": 128000},
    "gpt-5.3":              {"max_input": 272000,  "max_output": 128000},
    "gpt-5.3-codex":        {"max_input": 400000,  "max_output": 128000},
    "gpt-5.4":              {"max_input": 272000,  "max_output": 128000},
    "gpt-5.4-mini":         {"max_input": 272000,  "max_output": 128000},
    "gpt-5.5":              {"max_input": 272000,  "max_output": 128000},
    "gpt-5.5-mini":         {"max_input": 272000,  "max_output": 128000},
    "gpt-5.5-pro":          {"max_input": 360000,  "max_output": 32000},
    # OpenAI Realtime / Audio / Image (聊天里基本用不到温度但也填上)
    "gpt-realtime":         {"max_input": 32000,   "max_output": 4096},
    "gpt-4o-realtime-preview": {"max_input": 128000, "max_output": 4096},
    "gpt-4o-mini-realtime-preview": {"max_input": 128000, "max_output": 4096},
    "gpt-image-1":          {"max_input": 32000,   "max_output": 4096},
    # ===== Anthropic 补充 (旧 alias) =====
    "claude-3-5-sonnet-latest":     {"max_input": 200000, "max_output": 8192},
    "claude-3-5-haiku-latest":      {"max_input": 200000, "max_output": 8192},
    "claude-3-7-sonnet-latest":     {"max_input": 200000, "max_output": 64000},
    "claude-sonnet-4-20250514":     {"max_input": 1000000, "max_output": 64000},
    "claude-opus-4-20250514":       {"max_input": 200000,  "max_output": 32000},
    "claude-haiku-4-20250514":      {"max_input": 200000,  "max_output": 64000},
    # ===== 智谱 BigModel GLM 系列 (官方文档) =====
    "glm-5.1":              {"max_input": 200000,  "max_output": 32768},
    "glm-4.7":              {"max_input": 200000,  "max_output": 32768},
    "glm-4.6":              {"max_input": 200000,  "max_output": 32768},
    "glm-4.5":              {"max_input": 128000,  "max_output": 16384},
    "glm-4.5-air":          {"max_input": 128000,  "max_output": 16384},
    "glm-4.5-x":            {"max_input": 128000,  "max_output": 16384},
    "glm-4-plus":           {"max_input": 128000,  "max_output": 4096},
    "glm-4-flash":          {"max_input": 128000,  "max_output": 4096},
    "glm-4-flashx":         {"max_input": 128000,  "max_output": 4096},
    "glm-4-long":           {"max_input": 1000000, "max_output": 4096},
    "glm-4-air":            {"max_input": 128000,  "max_output": 4096},
    "glm-4v":               {"max_input": 8192,    "max_output": 1024},
    "glm-4v-plus":          {"max_input": 16384,   "max_output": 1024},
    "glm-zero-preview":     {"max_input": 16384,   "max_output": 12288},
    "glm-realtime":         {"max_input": 32000,   "max_output": 4096},
    # ===== 阶跃星辰 stepfun (官方文档 platform.stepfun.com/docs/zh/model) =====
    "step-2-16k":           {"max_input": 16000,   "max_output": 16000},
    "step-2-mini":          {"max_input": 16000,   "max_output": 16000},
    "step-2-16k-exp":       {"max_input": 16000,   "max_output": 16000},
    "step-1-8k":            {"max_input": 8000,    "max_output": 4096},
    "step-1-32k":           {"max_input": 32000,   "max_output": 8192},
    "step-1-128k":          {"max_input": 128000,  "max_output": 8192},
    "step-1-256k":          {"max_input": 256000,  "max_output": 8192},
    "step-1-flash":         {"max_input": 8000,    "max_output": 4096},
    "step-1v-8k":           {"max_input": 8000,    "max_output": 4096},
    "step-1v-32k":          {"max_input": 32000,   "max_output": 8192},
    "step-1.5v-mini":       {"max_input": 32000,   "max_output": 8192},
    "step-1.5v-turbo":      {"max_input": 32000,   "max_output": 8192},
    "step-1o-vision-32k":   {"max_input": 32000,   "max_output": 8192},
    "step-1o-audio":        {"max_input": 8000,    "max_output": 4096},
    "step-audio":           {"max_input": 8000,    "max_output": 4096},
    "step-audio-2":         {"max_input": 8000,    "max_output": 4096},
    "step-audio-2-mini":    {"max_input": 8000,    "max_output": 4096},
    "step-audio-r1.1":      {"max_input": 8000,    "max_output": 4096},
    "step-r1-v-mini":       {"max_input": 32000,   "max_output": 8192},
    # ===== 阿里通义千问补充 =====
    "qwen3.6-max":          {"max_input": 1000000, "max_output": 32768},
    "qwen3-72b-instruct":   {"max_input": 32768,   "max_output": 8192},
    "qwen3-32b":            {"max_input": 128000,  "max_output": 8192},
    "qwen3-coder-plus":     {"max_input": 1000000, "max_output": 65536},
    "qwen3-coder-flash":    {"max_input": 1000000, "max_output": 65536},
    "qwen-coder-plus":      {"max_input": 131072,  "max_output": 8192},
    "qwen-coder-turbo":     {"max_input": 131072,  "max_output": 8192},
    "qwen-vl-plus":         {"max_input": 32768,   "max_output": 8192},
    "qwen-vl-max":          {"max_input": 32768,   "max_output": 8192},
    "qwen-vl-ocr":          {"max_input": 34096,   "max_output": 4096},
    "qwen-long":            {"max_input": 10000000, "max_output": 8192},
    "qwen-math-plus":       {"max_input": 4096,    "max_output": 3072},
    "qwen-math-turbo":      {"max_input": 4096,    "max_output": 3072},
    "qwq-plus":             {"max_input": 131072,  "max_output": 8192},
    "qwq-32b":              {"max_input": 131072,  "max_output": 8192},
    "qwq-32b-preview":      {"max_input": 32768,   "max_output": 16384},
    "qvq-72b-preview":      {"max_input": 32768,   "max_output": 16384},
    # ===== 火山豆包补充 =====
    "doubao-1.5-pro-32k-250115":    {"max_input": 32000,  "max_output": 12288},
    "doubao-1.5-lite-32k-250115":   {"max_input": 32000,  "max_output": 12288},
    "doubao-1.5-vision-pro-250115": {"max_input": 96000,  "max_output": 16384},
    "doubao-1.5-vision-pro-32k-250115": {"max_input": 32000, "max_output": 16384},
    "doubao-pro-32k":               {"max_input": 32000,  "max_output": 4096},
    "doubao-pro-128k":              {"max_input": 128000, "max_output": 4096},
    "doubao-pro-256k":               {"max_input": 256000, "max_output": 4096},
    "doubao-lite-32k":              {"max_input": 32000,  "max_output": 4096},
    "doubao-lite-128k":             {"max_input": 128000, "max_output": 4096},
    "doubao-vision-lite-32k":       {"max_input": 32000,  "max_output": 4096},
    "doubao-seed-1-6":              {"max_input": 256000, "max_output": 16384},
    "doubao-seed-1-6-flash":        {"max_input": 256000, "max_output": 16384},
    "doubao-1-5-thinking-pro":      {"max_input": 128000, "max_output": 16384},
    # ===== MiniMax 补充 =====
    "MiniMax-M3":           {"max_input": 196608, "max_output": 128000},
    "MiniMax-M3-pro":       {"max_input": 196608, "max_output": 128000},
    "MiniMax-Text-01":      {"max_input": 1000000, "max_output": 1000000},
    "abab7-chat-preview":   {"max_input": 245760, "max_output": 8192},
    "abab6.5s-chat":        {"max_input": 245760, "max_output": 8192},
    "MiniMax-Reasoner":     {"max_input": 200000, "max_output": 80000},
    # ===== Gemini 补充 =====
    "gemini-3-pro":          {"max_input": 1048576, "max_output": 65536},
    "gemini-3-flash":        {"max_input": 1048576, "max_output": 65536},
    "gemini-3.1-pro":        {"max_input": 1048576, "max_output": 65536},
    "gemini-3.1-flash":      {"max_input": 1048576, "max_output": 65536},
    "gemini-2.5-pro-preview": {"max_input": 1048576, "max_output": 65536},
    "gemini-2.5-flash-preview": {"max_input": 1048576, "max_output": 65536},
    "gemini-live-2.5-flash-native-audio": {"max_input": 32000, "max_output": 8000},
    "gemini-2.5-flash-native-audio-latest": {"max_input": 32000, "max_output": 8000},
    "gemini-3.1-flash-live-preview": {"max_input": 32000, "max_output": 8000},
    # ===== OpenRouter 别名补充 =====
    "openai/gpt-5":           {"max_input": 272000, "max_output": 128000},
    "openai/gpt-5-pro":       {"max_input": 400000, "max_output": 32000},
    "openai/gpt-5.5":         {"max_input": 272000, "max_output": 128000},
    "openai/gpt-5.5-pro":     {"max_input": 360000, "max_output": 32000},
    "openai/gpt-4.1":         {"max_input": 1047576, "max_output": 32768},
    "openai/o1":              {"max_input": 200000, "max_output": 100000},
    "openai/o3":              {"max_input": 200000, "max_output": 100000},
    "openai/o3-mini":         {"max_input": 200000, "max_output": 100000},
    "anthropic/claude-opus-4-7":   {"max_input": 200000, "max_output": 128000},
    "anthropic/claude-opus-4-5":   {"max_input": 200000, "max_output": 64000},
    "anthropic/claude-sonnet-4-5": {"max_input": 1000000, "max_output": 64000},
    "anthropic/claude-haiku-4-5":  {"max_input": 200000, "max_output": 64000},
    "google/gemini-3-pro":          {"max_input": 1048576, "max_output": 65536},
    "google/gemini-3.1-pro":        {"max_input": 1048576, "max_output": 65536},
    "google/gemini-3.1-flash":      {"max_input": 1048576, "max_output": 65536},
    "deepseek/deepseek-v4-flash":   {"max_input": 1000000, "max_output": 384000},
    "deepseek/deepseek-v4-pro":     {"max_input": 1000000, "max_output": 384000},
    "qwen/qwen-plus":               {"max_input": 1000000, "max_output": 32768},
    "qwen/qwen-max":                {"max_input": 32768,   "max_output": 8192},
    "qwen/qwen3-coder-plus":        {"max_input": 1000000, "max_output": 65536},
    "z-ai/glm-4.7":                 {"max_input": 200000,  "max_output": 32768},
    "z-ai/glm-4.5-air":             {"max_input": 128000,  "max_output": 16384},
}
# ============================================================
# Provider 连接存储（持久化到磁盘）
# ============================================================


# 内存缓存: {username: {provider_id: {credentials..., connected_at, models, status}}}


def get_provider_def(provider_id: str) -> Optional[dict]:
    """获取 provider 静态定义。custom_* 动态 provider 返回 custom 模板"""
    if provider_id.startswith("custom_"):
        return PROVIDER_DEFS.get("custom")
    return PROVIDER_DEFS.get(provider_id)


# ============================================================
# API 调用辅助 - 根据 provider 构建请求参数
# ============================================================

def _iflytek_strip_images(messages: list) -> list:
    """讯飞所有当前模型 (X2/X1.5/Ultra/Pro/Pro-128K/Lite) 都是纯文本模型,
    不支持 image_url。把多模态 content 数组里的 image_url 部分去掉,
    只保留 text; 若整条没文本则用占位提示替换。"""
    if not isinstance(messages, list):
        return messages
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m); continue
        cv = m.get("content")
        if isinstance(cv, list):
            kept = []
            had_img = False
            for part in cv:
                if not isinstance(part, dict):
                    kept.append(part); continue
                t = part.get("type")
                if t == "image_url" or t == "input_image" or t == "image":
                    had_img = True
                    continue
                kept.append(part)
            if had_img:
                # 把数组合并为字符串 (讯飞对 content 数组兼容性差, 这里全部展平)
                txt_parts = []
                for k in kept:
                    if isinstance(k, dict):
                        txt_parts.append(k.get("text") or k.get("content") or "")
                    else:
                        txt_parts.append(str(k))
                txt = "".join(txt_parts).strip()
                if not txt:
                    txt = "[图片已自动忽略 - 当前模型不支持图片输入]"
                else:
                    txt = "[图片已自动忽略] " + txt
                new_m = dict(m)
                new_m["content"] = txt
                out.append(new_m)
            else:
                # 没图但是数组, 也展平为字符串
                txt = "".join((k.get("text","") if isinstance(k,dict) else str(k)) for k in kept)
                new_m = dict(m); new_m["content"] = txt
                out.append(new_m)
        else:
            out.append(m)
    return out


def _iflytek_chat_url(model: str) -> str:
    """讯飞按 model 选择真实 API 路径 (X2 走 /x2, X1.5 走 /v2, 其余走 /v1)."""
    pdef = PROVIDER_DEFS.get("iflytek", {})
    base = pdef.get("base_url", "https://spark-api-open.xf-yun.com").rstrip("/")
    m = (model or "").lower()
    if m in ("spark-x2", "spark_x2", "x2"):
        return f"{base}/x2/chat/completions"
    if m in ("spark-x1.5", "spark_x1.5", "x1.5", "spark-x1_5", "x1"):
        return f"{base}/v2/chat/completions"
    return f"{base}/v1/chat/completions"


def _iflytek_real_model(model: str) -> str:
    """讯飞 X1.5/X2 实际请求 body 里 model 字段统一为 spark-x; 其它保持原值."""
    m = (model or "").lower()
    if m in ("spark-x2", "spark_x2", "x2", "spark-x1.5", "spark_x1.5", "x1.5", "spark-x1_5", "x1"):
        return "spark-x"
    return model


def get_api_url(provider_id: str, provider_cfg: dict, endpoint: str = "chat/completions") -> str:
    """根据 provider 获取 API URL"""
    pdef = get_provider_def(provider_id)
    if not pdef:
        raise ValueError(f"Unknown provider: {provider_id}")

    user_prov = provider_cfg or {}

    if provider_id == "github":
        return f"{pdef['base_url']}/{endpoint}"

    if provider_id == "bedrock":
        region = (user_prov or {}).get("aws_region", "us-east-1")
        # Bedrock 用 converse-stream endpoint
        return f"https://bedrock-runtime.{region}.amazonaws.com"

    if provider_id == "vertex":
        project = (user_prov or {}).get("gcp_project_id", "")
        region = (user_prov or {}).get("gcp_region", "us-central1")
        return f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models"

    if provider_id == "anthropic" or (pdef.get("api_format") == "anthropic" and provider_id != "github"):
        base = (user_prov or {}).get("base_url", pdef["base_url"]) or pdef["base_url"]
        if endpoint == "chat/completions":
            return f"{base}/v1/messages"
        return f"{base}/v1/{endpoint}"

    if provider_id == "iflytek":
        # 讯飞按 model 路由到不同子路径:
        #   spark-x2   -> /x2/chat/completions   (model 字段=spark-x)
        #   spark-x1.5 -> /v2/chat/completions   (model 字段=spark-x)
        #   其它      -> /v1/chat/completions   (model 字段保持原值)
        base = (user_prov or {}).get("base_url", pdef["base_url"]) or pdef["base_url"]
        base = base.rstrip("/")
        # 从调用方读 model 不方便, 这里默认 /v1; build_request_body 会通过 _iflytek_route 改写
        # 真正按 model 选择路径在 _iflytek_chat_url(model) 辅助函数中
        return f"{base}/v1/{endpoint}"

    # OpenAI 兼容格式 (openai / deepseek / openrouter / custom / custom_*)
    if provider_id == "custom" or provider_id.startswith("custom_"):
        base = (user_prov or {}).get("base_url", "").rstrip("/")
        if not base:
            return ""
        # 智能补 /v1: 用户可能填 http://host:port 或 http://host:port/v1
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/{endpoint}"
    
    _user_base = (user_prov or {}).get("base_url", "")
    if _user_base:
        # 用户自定义了 base_url，智能补 /v1
        base = _user_base.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
    else:
        base = pdef["base_url"]
    return f"{base}/{endpoint}"


def get_api_headers(provider_id: str, provider_cfg: dict, token: str = None) -> dict:
    """根据 provider 获取请求 headers"""
    pdef = get_provider_def(provider_id)
    if not pdef:
        return {}

    user_prov = provider_cfg or {}

    if provider_id == "github":
        # GitHub Copilot 用短期 token (由 _get_copilot_token 获取)
        _INTEGRATION_MAP = {
            "github_cli": "vscode-chat", "vscode": "vscode-chat",
            "jetbrains": "jetbrains", "vim": "vim", "github_web": "github.com",
        }
        _ck = (user_prov.get("copilot_client") or "github_cli")
        _integration = _INTEGRATION_MAP.get(_ck, "vscode-chat")
        return {
            "Authorization": f"Bearer {token or ''}",
            "Copilot-Integration-Id": _integration,
            "Content-Type": "application/json",
        }

    if provider_id == "anthropic" or (pdef.get("api_format") == "anthropic" and provider_id not in ("github",)):
        api_key = user_prov.get("api_key", "")
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "context-management-2025-06-27",
            "Content-Type": "application/json",
        }

    if provider_id == "openrouter":
        api_key = user_prov.get("api_key", "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ehangos.com",
            "X-Title": "EhangOS AI Assistant",
        }

    if provider_id == "bedrock":
        # Bedrock 需要 AWS SigV4 签名，这里返回基础 headers
        # 实际签名在调用时处理
        return {
            "Content-Type": "application/json",
        }

    if provider_id == "vertex":
        # Vertex AI 使用 Google OAuth2 Bearer token（服务账号JSON或本机ADC）
        access_token = _get_vertex_access_token(user_prov)
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    if provider_id == "iflytek":
        # 讯飞星火 HTTP OpenAI 兼容接口必须用 APIPassword 作 Bearer Token，
        # 注意：APIPassword 与 WebSocket 三件套里的 APIKey 是两回事，
        # 必须在控制台「HTTP 服务接口认证信息」一栏单独复制
        api_password = user_prov.get("api_password", "")
        if not api_password:
            raise RuntimeError("讯飞星火未填 APIPassword：请去讯飞控制台「HTTP 服务接口认证信息」复制 APIPassword 并保存（注意它与 WebSocket 服务接口的 APIKey 是不同的字段）")
        return {
            "Authorization": f"Bearer {api_password}",
            "Content-Type": "application/json",
        }

    # OpenAI 兼容 (openai / deepseek / custom)
    api_key = user_prov.get("api_key", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def build_request_body(provider_id: str, model: str, messages: list,
                       tools: list = None, stream: bool = True,
                       max_tokens: int = 16384, thinking_level: str = "", temperature: float = None,
                       api_url: str = "", provider_cfg: dict = None) -> dict:
    """根据 provider 的 api_format 构建请求体"""
    pdef = get_provider_def(provider_id)
    if not pdef:
        raise ValueError(f"Unknown provider: {provider_id}")

    _brb_api_url = api_url or pdef.get("base_url", "") or ""
    api_format = pdef["api_format"]

    if api_format == "anthropic":
        return _build_anthropic_body(model, messages, tools, stream, max_tokens, thinking_level, temperature)

    if api_format == "vertex":
        return _build_vertex_body(model, messages, tools, stream, max_tokens, thinking_level, temperature)

    # copilot / openai 兼容格式
    # GPT-5.x / o1 / o3 / o4 等新模型要求 max_completion_tokens 而非 max_tokens
    _model_lower = model.lower().split("/")[-1]  # 去掉 provider 前缀
    _use_completion_tokens = any(_model_lower.startswith(p) for p in (
        "gpt-5", "o1", "o3", "o4",
    ))

    # image_url 视觉支持：user/assistant消息支持，但 tool_result 里的 image_url Copilot不接受
    # 不支持的 provider（如 DeepSeek）在 assistant.py 中提前剥离

    # 非 Anthropic 格式不支持 document 类型，降级为文字提示
    _cleaned_msgs = []
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, list):
            _new_parts = []
            # tool_result 里 Copilot 只接受纯文本，剥掉 image_url
            _is_tool_msg = msg.get("role") == "tool"
            for part in c:
                if _is_tool_msg and part.get("type") == "image_url":
                    continue  # Copilot tool_result 不支持 image_url
                if part.get("type") == "document":
                    # document 类型（PDF）：非 Anthropic 格式不支持，降级为占位文字
                    _new_parts.append({"type": "text", "text": "[PDF document not supported in this provider]"})
                else:
                    _new_parts.append(part)
            _cleaned_msgs.append({k: v for k, v in {**msg, "content": _new_parts}.items() if not k.startswith("_")})
        else:
            _cleaned_msgs.append({k: v for k, v in msg.items() if not k.startswith("_")})

    # v778: 校验 tool_calls 完整性 + tool_result 配对
    # Step 1: 过滤残缺 tool_calls (缺 id 或 function.name)
    _valid_tc_ids = set()
    for _cm in _cleaned_msgs:
        if _cm.get("role") == "assistant" and _cm.get("tool_calls"):
            _valid_tcs = [tc for tc in _cm["tool_calls"]
                          if tc.get("id") and tc.get("function", {}).get("name")]
            if _valid_tcs:
                _cm["tool_calls"] = _valid_tcs
                for _tc in _valid_tcs:
                    _valid_tc_ids.add(_tc["id"])
            else:
                _cm.pop("tool_calls", None)
    # Step 2: 过滤孤儿 tool_result (tool_call_id 为空或找不到对应 tool_call)
    if _valid_tc_ids:
        _cleaned_msgs = [_cm for _cm in _cleaned_msgs
                         if _cm.get("role") != "tool"
                         or (_cm.get("tool_call_id") and _cm["tool_call_id"] in _valid_tc_ids)]
    else:
        # 没有任何有效 tool_call，移除所有 tool_result 消息
        _cleaned_msgs = [_cm for _cm in _cleaned_msgs if _cm.get("role") != "tool"]

    # DeepSeek thinking 模式 (V4-Pro / reasoner) 强制要求每个历史 assistant 消息带 reasoning_content 字段
    # 否则报 400: "reasoning_content in the thinking mode must be passed back to the API"
    _model_low = model.lower()
    if "deepseek-v4-pro" in _model_low or "deepseek-reasoner" in _model_low:
        _ds_msgs = []
        for _m in _cleaned_msgs:
            if _m.get("role") == "assistant" and "reasoning_content" not in _m:
                _ds_msgs.append({**_m, "reasoning_content": ""})
            else:
                _ds_msgs.append(_m)
        _cleaned_msgs = _ds_msgs

    body = {
        "model": model,
        "messages": _cleaned_msgs,
        "stream": stream,
    }
    if _use_completion_tokens:
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if stream:
        body["stream_options"] = {"include_usage": True}
    if tools:
        body["tools"] = tools

    # 思考级别注入
    if thinking_level:
        tc = get_thinking_config(model, api_url=_brb_api_url)
        if tc:
            if tc["param_type"] == "openai_reasoning":
                # o1/o3/o4: reasoning_effort
                body["reasoning_effort"] = thinking_level
            elif tc["param_type"] == "gemini_thinking":
                # Gemini 2.5 via OpenAI compat: reasoning_effort
                if thinking_level == "off":
                    body["reasoning_effort"] = "none"
                else:
                    body["reasoning_effort"] = thinking_level
            elif tc["param_type"] == "anthropic_adaptive":
                # Copilot 代理 Claude Opus 4.7+ / 4.6 / Sonnet 4.6: adaptive thinking
                if thinking_level != "off":
                    body["thinking"] = {"type": "adaptive"}
                    body["output_config"] = {"effort": thinking_level}
                    if body.get("max_tokens", 0) < 16384:
                        body["max_tokens"] = 16384
            elif tc["param_type"] == "anthropic_thinking":
                # Copilot 代理旧 Claude 模型: enabled + budget_tokens
                if thinking_level != "off":
                    level_map = {l["id"]: l for l in tc["levels"]}
                    lv = level_map.get(thinking_level, level_map.get("medium"))
                    if lv and lv.get("budget_tokens", 0) > 0:
                        body["thinking"] = {
                            "type": "enabled",
                            "budget_tokens": lv["budget_tokens"]
                        }
                        if body.get("max_tokens", 0) < lv["budget_tokens"] + 4096:
                            body["max_tokens"] = lv["budget_tokens"] + 16384
            elif tc["param_type"] == "ollama_think":
                # Ollama原生接口: think + think_budget
                _think_on = thinking_level != "off"
                body["think"] = _think_on
                if _think_on:
                    level_map = {l["id"]: l for l in tc["levels"]}
                    lv = level_map.get(thinking_level)
                    if lv and lv.get("budget_tokens"):
                        body["think_budget"] = lv["budget_tokens"]
            elif tc["param_type"] == "vllm_thinking":
                # vLLM: chat_template_kwargs.enable_thinking
                _think_on = thinking_level != "off"
                body["chat_template_kwargs"] = {"enable_thinking": _think_on}
    else:
        # 没有 thinking_level 但模型支持 ollama_think/vllm_thinking → 显式关闭
        tc = get_thinking_config(model, api_url=_brb_api_url)
        if tc:
            if tc.get("param_type") == "ollama_think":
                body["think"] = False
            elif tc.get("param_type") == "vllm_thinking":
                body["chat_template_kwargs"] = {"enable_thinking": False}

    return body


# ============================================================
# Google Vertex AI (Gemini) 实现
# ============================================================
_VERTEX_TOKEN_CACHE = {}  # cache_key -> {token, exp}


def _find_adc_credentials_path() -> str | None:
    """查找本机 ADC 凭证文件。"""
    import os
    candidates = [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        "/home/hhz/.config/gcloud/application_default_credentials.json",
        "/root/.config/gcloud/application_default_credentials.json",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def _load_vertex_credentials(user_prov: dict) -> tuple[dict, str]:
    """加载 Vertex 凭证，返回 (credential_json, source)。支持服务账号 JSON 或 ADC。"""
    raw = (user_prov or {}).get("gcp_service_account_json") or ""
    if raw.strip():
        if isinstance(raw, dict):
            return raw, "service_account"
        try:
            return json.loads(raw), "service_account"
        except Exception as e:
            raise ValueError(f"服务账号 JSON 解析失败: {e}")
    adc = _find_adc_credentials_path()
    if not adc:
        raise FileNotFoundError("未找到 ADC 凭证文件")
    with open(adc, "r", encoding="utf-8") as f:
        return json.load(f), "adc"


def _get_vertex_access_token(user_prov: dict, force_refresh: bool = False) -> str:
    """获取 Vertex AI OAuth token（服务账号 JWT 或 ADC refresh_token）。同步函数，供 header 构建使用。"""
    import requests
    creds, source = _load_vertex_credentials(user_prov or {})
    now = int(time.time())
    cache_key = f"{source}:{creds.get('client_email') or creds.get('client_id') or creds.get('quota_project_id') or creds.get('project_id')}"
    cached = _VERTEX_TOKEN_CACHE.get(cache_key)
    if (not force_refresh) and cached and cached.get("exp", 0) > now + 120:
        return cached["token"]

    if source == "service_account":
        email = creds.get("client_email")
        private_key = creds.get("private_key")
        token_uri = creds.get("token_uri") or "https://oauth2.googleapis.com/token"
        if not email or not private_key:
            raise ValueError("服务账号 JSON 缺少 client_email/private_key")
        try:
            import jwt as _jwt
        except Exception as e:
            raise RuntimeError("缺少 PyJWT 依赖，无法签发服务账号 JWT") from e
        payload = {
            "iss": email,
            "sub": email,
            "aud": token_uri,
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        }
        assertion = _jwt.encode(payload, private_key, algorithm="RS256")
        resp = requests.post(token_uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }, timeout=20)
    else:
        token_uri = creds.get("token_uri") or "https://oauth2.googleapis.com/token"
        resp = requests.post(token_uri, data={
            "client_id": creds.get("client_id"),
            "client_secret": creds.get("client_secret"),
            "refresh_token": creds.get("refresh_token"),
            "grant_type": "refresh_token",
        }, timeout=20)

    if resp.status_code != 200:
        raise RuntimeError(f"Google OAuth token 获取失败 HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Google OAuth token 响应缺少 access_token")
    _VERTEX_TOKEN_CACHE[cache_key] = {"token": token, "exp": now + int(data.get("expires_in", 3600))}
    return token


def _vertex_clean_schema(schema):
    """Gemini functionDeclarations 对 JSON Schema 比较挑，去掉不兼容字段。"""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    banned = {"additionalProperties", "$schema", "strict"}
    out = {}
    for k, v in schema.items():
        if k in banned:
            continue
        if k in ("properties", "$defs", "definitions") and isinstance(v, dict):
            out[k] = {kk: _vertex_clean_schema(vv) for kk, vv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _vertex_clean_schema(v)
        elif isinstance(v, dict):
            out[k] = _vertex_clean_schema(v)
        elif isinstance(v, list):
            out[k] = [_vertex_clean_schema(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


def _is_vertex_anthropic_model(model: str) -> bool:
    """Vertex AI Partner Models: Anthropic Claude 模型。"""
    return bool(model) and model.lower().split('/')[-1].startswith("claude-")


def _build_vertex_anthropic_body(model: str, messages: list, tools: list = None,
                                 stream: bool = True, max_tokens: int = 16384,
                                 thinking_level: str = "") -> dict:
    """将 OpenAI messages/tools 转换为 Vertex AI Anthropic Messages 格式。
    Vertex Claude 与 Anthropic 官方 Messages API 基本一致，但 anthropic_version 必须放在 body。
    """
    body = _build_anthropic_body(model, messages, tools, stream, max_tokens, thinking_level)
    body["anthropic_version"] = "vertex-2023-10-16"
    # Vertex endpoint 的模型由 URL 指定，body 中不要带 model
    body.pop("model", None)
    return body


def _build_vertex_body(model: str, messages: list, tools: list = None,
                       stream: bool = True, max_tokens: int = 16384, thinking_level: str = "",
                       temperature: float = None) -> dict:
    """将 OpenAI messages/tools 转换为 Vertex AI Gemini generateContent 格式。"""
    system_parts = []
    contents = []
    tool_name_by_id = {}

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append({"text": content})
            elif isinstance(content, list):
                for p in content:
                    if p.get("type") == "text" and p.get("text"):
                        system_parts.append({"text": p.get("text", "")})
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id", "")
            name = tool_name_by_id.get(call_id) or "tool_result"
            out = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            contents.append({
                "role": "user",
                "parts": [{"functionResponse": {"name": name, "response": {"content": out or ""}}}],
            })
            continue

        parts = []
        if isinstance(content, str):
            if content:
                parts.append({"text": content})
        elif isinstance(content, list):
            for p in content:
                pt = p.get("type")
                if pt == "text":
                    parts.append({"text": p.get("text", "")})
                elif pt == "image_url":
                    url = p.get("image_url", {}).get("url", "")
                    if url.startswith("data:") and ";base64," in url:
                        head, b64 = url.split(",", 1)
                        mime = head.split(";", 1)[0].replace("data:", "") or "image/png"
                        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                    elif url:
                        parts.append({"fileData": {"fileUri": url, "mimeType": "image/png"}})
                elif pt == "video_url":
                    url = p.get("video_url", {}).get("url", "")
                    if url.startswith("data:") and ";base64," in url:
                        head, b64 = url.split(",", 1)
                        mime = head.split(";", 1)[0].replace("data:", "") or "video/mp4"
                        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                    elif url:
                        parts.append({"fileData": {"fileUri": url, "mimeType": "video/mp4"}})

        if role == "assistant" and msg.get("tool_calls"):
            if parts:
                contents.append({"role": "model", "parts": parts})
                parts = []
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                call_id = tc.get("id", "")
                if call_id:
                    tool_name_by_id[call_id] = name
                args = fn.get("arguments") or "{}"
                if isinstance(args, str):
                    try:
                        args_obj = json.loads(args) if args.strip() else {}
                    except Exception:
                        args_obj = {"_raw": args}
                else:
                    args_obj = args
                contents.append({"role": "model", "parts": [{"functionCall": {"name": name, "args": args_obj}}]})
            continue

        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})

    body = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
    gen = {"maxOutputTokens": max_tokens}
    if temperature is not None:
        gen["temperature"] = temperature
    if thinking_level == "off":
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    body["generationConfig"] = gen

    if tools:
        decls = []
        for t in tools:
            if t.get("type") != "function":
                continue
            fn = t.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            decls.append({
                "name": name,
                "description": fn.get("description", ""),
                "parameters": _vertex_clean_schema(fn.get("parameters", {"type": "object", "properties": {}})),
            })
        if decls:
            body["tools"] = [{"functionDeclarations": decls}]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    return body


class _VertexStreamParser:
    """解析 Vertex AI Gemini streamGenerateContent SSE。"""
    def __init__(self):
        self._tool_idx = 0

    def parse_line(self, line: str):
        line = line.strip()
        if not line.startswith("data: "):
            return None
        data_str = line[6:]
        if not data_str or data_str == "[DONE]":
            return {"finish_reason": "stop", "done": True}
        try:
            data = json.loads(data_str)
        except Exception:
            return None
        result = {"content": None, "tool_calls": None, "finish_reason": None, "usage": None, "thinking": None}
        if "error" in data:
            err = data.get("error") or {}
            result["finish_reason"] = "error"
            result["content"] = f"[Vertex AI error: {err.get('message', 'unknown')}]"
            return result
        usage = data.get("usageMetadata") or {}
        if usage:
            result["usage"] = {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            }
        cands = data.get("candidates") or []
        for cand in cands:
            content = cand.get("content") or {}
            parts = content.get("parts") or []
            for part in parts:
                if "text" in part:
                    if part.get("thought"):
                        result["thinking"] = (result.get("thinking") or "") + (part.get("text") or "")
                    else:
                        result["content"] = (result.get("content") or "") + (part.get("text") or "")
                if "functionCall" in part:
                    fc = part.get("functionCall") or {}
                    args = fc.get("args") or {}
                    if result.get("tool_calls") is None:
                        result["tool_calls"] = []
                    result["tool_calls"].append({
                        "index": self._tool_idx,
                        "id": f"call_vertex_{self._tool_idx}",
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(args, ensure_ascii=False),
                    })
                    self._tool_idx += 1
            fr = cand.get("finishReason")
            if fr:
                result["finish_reason"] = "tool_calls" if result.get("tool_calls") else ("stop" if fr == "STOP" else fr.lower())
        if not any(result.get(k) is not None for k in ("content", "tool_calls", "finish_reason", "usage", "thinking")):
            return None
        return result


def _build_anthropic_body(model: str, messages: list, tools: list = None,
                          stream: bool = True, max_tokens: int = 16384, thinking_level: str = "",
                          temperature: float = None) -> dict:
    """将 OpenAI 格式的 messages 转换为 Anthropic Messages API 格式"""
    # 提取 system message
    system_text = ""
    api_messages = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system_text += (content or "") + "\n"
            continue

        if role == "tool":
            # Anthropic 用 tool_result 类型
            _new_msg = {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content or "",
                }]
            }
            # v11: 透传 cc 锚点标记 (assistant.py 打在原 message 上)
            api_messages.append(_new_msg)
            continue

        if role == "assistant" and msg.get("tool_calls"):
            # 助手消息带 tool_calls → Anthropic tool_use
            content_blocks = []
            if content:
                content_blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except:
                    args = {"raw": args_str}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            _new_msg = {"role": "assistant", "content": content_blocks}
            api_messages.append(_new_msg)
            continue

        # 普通 user / assistant 消息
        if role in ("user", "assistant"):
            _is_summary = msg.get("_is_summary", False)
            # 摘要消息：用 _summary_segments 拆成 list of text blocks 利于缓存
            # 老段落 block 的 hash 完全稳定，新增段只会成为新末尾 block
            if _is_summary and msg.get("_summary_segments"):
                _segments = msg.get("_summary_segments") or []
                _prefix = msg.get("_summary_prefix") or ""
                _blocks = []
                for _idx, _seg_text in enumerate(_segments):
                    if _idx == 0:
                        _blocks.append({"type": "text", "text": _prefix + _seg_text})
                    else:
                        _blocks.append({"type": "text", "text": "\n\n---\n\n" + _seg_text})
                api_messages.append({"role": role, "content": _blocks, "_is_summary": True})
                continue
            if isinstance(content, list):
                # 多模态消息：将 OpenAI 格式图片转为 Anthropic 格式
                _converted = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "image_url":
                        _url = part.get("image_url", {}).get("url", "")
                        if _url.startswith("data:"):
                            _mime_part, _, _b64 = _url.partition(",")
                            _media_type = _mime_part.split(":")[1].split(";")[0] if ":" in _mime_part else "image/png"
                            _converted.append({"type": "image", "source": {"type": "base64", "media_type": _media_type, "data": _b64}})
                    elif part.get("type") == "text":
                        _converted.append({"type": "text", "text": part.get("text", "")})
                    else:
                        _converted.append(part)
                _new_msg = {"role": role, "content": _converted}
            else:
                _new_msg = {"role": role, "content": content or ""}
            api_messages.append(_new_msg)

    # Anthropic 要求第一条必须是 user
    # 如果不是，插入一个空 user 消息
    if api_messages and api_messages[0]["role"] != "user":
        api_messages.insert(0, {"role": "user", "content": "(continued)"})

    # Anthropic 要求 user/assistant 严格交替，需要合并连续同 role 消息
    merged = _merge_consecutive_roles(api_messages)

    body = {
        "model": model,
        "messages": merged,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if system_text.strip():
        body["system"] = system_text.strip()
    if tools:
        body["tools"] = _convert_tools_to_anthropic(tools)

    # ---- Prompt Caching ----
    # Anthropic 最多支持 4 个 cache breakpoint（cache_control 标记）
    # 策略：每个 breakpoint 之前的所有内容都被一起缓存，前缀必须严格匹配
    # 分配（v3 - 合并 system+tools，新增滚动锚点）：
    #   1. tools 末尾 -> 缓存 system + tools 整段（两段都是常量，合并保护）
    #   2. 摘要 user 倒数第二个 block 末尾 -> 缓存摘要前 N-1 段（追加新段时旧 block 位置不变）
    #   3. 上一轮真实 user 消息末尾（滚动锚点） -> 缓存所有"截至上轮你提问之前"的历史
    #      关键：跨工具循环时位置不变，多次 API 调用都命中同一段
    #      下一次发新消息时，本轮 user 升级为"上一轮 user"，断点平滑滚动
    #   4. 最后一条 message 末尾 -> 当前轮整 prefix 写入 cache
    # Anthropic extended thinking / adaptive thinking
    if thinking_level and thinking_level != "off":
        tc = get_thinking_config(model)
        if tc and tc["param_type"] == "anthropic_adaptive":
            # Opus 4.7+ / Opus 4.6 / Sonnet 4.6: adaptive thinking
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": thinking_level}
            # adaptive thinking 自动管理 token budget, 但 max_tokens 仍需足够大
            if body.get("max_tokens", 0) < 16384:
                body["max_tokens"] = 16384
        elif tc and tc["param_type"] == "anthropic_thinking":
            level_map = {l["id"]: l for l in tc["levels"]}
            lv = level_map.get(thinking_level, level_map.get("medium"))
            if lv and lv.get("budget_tokens", 0) > 0:
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": lv["budget_tokens"]
                }
                # extended thinking 要求 max_tokens 覆盖 budget_tokens
                if body.get("max_tokens", 0) < lv["budget_tokens"] + 4096:
                    body["max_tokens"] = lv["budget_tokens"] + 16384

    # v13: 记录 4 个 cc 的累计 token (供前端命中判定)
    # ========== v17: 1 灯命中校验 (基于 warm_body 字节对比) ==========
    # 灯绿条件: 本轮 messages 前 N 条 (N=len(warm_msgs)) 字节 == 上轮 warm_body messages 字节
    # 即: 本轮装填时上轮的 prefix 是否被原样保留下来 (含 cache_control 字段, cache_control 不参与 hash)
    # 服务端按字节匹配 cache, prefix 一致 → 必然命中, prefix 漂移 → miss
    # 清理内部标记字段（Anthropic 不识别，以 _ 开头的元数据）
    for _m in body.get("messages") or []:
        for _k in list(_m.keys()):
            if isinstance(_k, str) and _k.startswith("_"):
                _m.pop(_k, None)

    # === DBG_BODY_DIFF: 拿上一轮真实发出的 body (warm_body_json) 对比本轮当前 body, 找第一条不一致的消息 ===
    # 比较时机: 在写入新 cache 之前, DB 里 warm_body_json 还是上一轮的真实发送内容
    # 比较内容: 实物 JSON 字节, 不比 hash
    # 关键: 比较 "去掉最后一个 cache_control" 之后的 messages, 避免 cache_control 位置移动造成误判
    try:
        if stream and (max_tokens or 0) > 100:
            _sid_diff = "_no_session_"
            if _sid_diff:
    # [REMOVED] except Exception as _e_diff_outer:
        # [REMOVED] print(f"[providers] DBG_BODY_DIFF outer err: {_e_diff_outer}", flush=True)

    # 保温用: 只缓存"正常 chat"的 body (stream=True 且 max_tokens>1, 排除保温自身的调用)
    # 保温接口读这份 body 重发, cache prefix 100% 一致
    # 同时持久化到 DB (assistant_sessions.warm_body_json), 后端重启不丢
    # [REMOVED] try:
        # [REMOVED] if stream and (max_tokens or 0) > 100:
            # [REMOVED] _sid_cache = "_no_session_"
            # [REMOVED] if _sid_cache:
                import copy as _copy_mod
                import time as _time_mod
                _body_copy = _copy_mod.deepcopy(body)
                _ts_now = _time_mod.time()
    except Exception as _ce:
        print(f"[providers] cache_body failed: {_ce}", flush=True)

    # ========== Context Management (服务端自动清理旧 tool 内容 + thinking) ==========
    # Anthropic beta API: 当 input tokens 超过 trigger 时，服务端自动清理最老的 tool_result
    # 内容替换为占位符，模型仍能看到 assistant 的文字总结，不影响理解
    # 同时清理旧 thinking blocks 只保留最近 2 轮，节省 token
    return body


# ============================================================
# GitHub Copilot Responses API 实现
# - 请求体：input + tools(扁平) + max_output_tokens
# - 响应：SSE 事件流，包含 response.output_text.delta / response.function_call_arguments.delta 等
# ============================================================

def _build_responses_body(model: str, messages: list, tools: list = None,
                          stream: bool = True, max_tokens: int = 16384,
                          thinking_level: str = "", temperature: float = None) -> dict:
    """将 OpenAI chat/completions 格式的 messages 转换为 Responses API 的 input 格式"""
    instructions = ""  # system 文本合并到这里
    input_items = []   # Responses API 的 input 数组

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                instructions += content + "\n"
            elif isinstance(content, list):
                for p in content:
                    if p.get("type") == "text":
                        instructions += p.get("text", "") + "\n"
            continue

        if role == "tool":
            # tool 结果：function_call_output 单独一项
            tool_call_id = msg.get("tool_call_id", "")
            output_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            input_items.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": output_text or "",
            })
            continue

        if role == "assistant" and msg.get("tool_calls"):
            # 助手消息含 tool_calls：每个 tool_call 转为 function_call 一项
            # 如果同时有 text content，先单独发一条 message
            if content:
                _text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": _text}],
                })
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "{}")
                if not isinstance(args_str, str):
                    args_str = json.dumps(args_str, ensure_ascii=False)
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": args_str,
                })
            continue

        if role in ("user", "assistant"):
            # 普通消息
            content_parts = []
            if isinstance(content, str):
                content_parts.append({
                    "type": "input_text" if role == "user" else "output_text",
                    "text": content
                })
            elif isinstance(content, list):
                for part in content:
                    pt = part.get("type")
                    if pt == "text":
                        content_parts.append({
                            "type": "input_text" if role == "user" else "output_text",
                            "text": part.get("text", "")
                        })
                    elif pt == "image_url":
                        # Responses API 用 input_image
                        url = part.get("image_url", {}).get("url", "")
                        content_parts.append({
                            "type": "input_image",
                            "image_url": url,
                        })
                    else:
                        # 其他类型透传（input_audio 等）
                        content_parts.append(part)
            if content_parts:
                input_items.append({
                    "type": "message",
                    "role": role,
                    "content": content_parts,
                })

    body = {
        "model": model,
        "input": input_items,
        "stream": stream,
        "max_output_tokens": max_tokens,
        "store": False,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if instructions.strip():
        body["instructions"] = instructions.strip()
    if tools:
        # OpenAI tool 格式: {"type":"function","function":{"name","description","parameters"}}
        # Responses API tool 格式: {"type":"function","name","description","parameters"}（扁平）
        flat_tools = []
        for t in tools:
            if t.get("type") == "function":
                fn = t.get("function", {})
                flat_tools.append({
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                flat_tools.append(t)
        body["tools"] = flat_tools

    # thinking_level 映射到 reasoning.effort
    if thinking_level and thinking_level != "off":
        # gpt-5.x 都支持 reasoning.effort: minimal/low/medium/high
        body["reasoning"] = {"effort": thinking_level}

    return body


class _ResponsesStreamParser:
    """GitHub Copilot Responses API 的 SSE 解析器（事件序列有状态）

    主要事件：
    - response.created / response.in_progress: 元数据，忽略
    - response.output_item.added: 新建 output 项（function_call 或 message）
    - response.output_text.delta: 文本增量
    - response.function_call_arguments.delta: 工具调用参数增量
    - response.function_call_arguments.done: 工具调用参数完成
    - response.output_item.done: 该项完成
    - response.completed: 整个响应完成（携带 usage）
    """
    def __init__(self):
        # output_index → {type, id, call_id, name, arguments_buf}
        self._items = {}
        self._tool_call_idx_counter = 0  # 给前端用的递增索引

    def parse_line(self, line: str):
        line = line.strip()
        if not line.startswith("data: "):
            return None
        data_str = line[6:]
        if data_str == "[DONE]":
            return {"finish_reason": "stop", "done": True}
        try:
            data = json.loads(data_str)
        except Exception:
            return None
        et = data.get("type", "")
        result = {
            "content": None,
            "tool_calls": None,
            "finish_reason": None,
            "usage": None,
            "thinking": None,
        }

        if et == "response.output_text.delta":
            result["content"] = data.get("delta", "") or None

        elif et in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta", "response.reasoning.delta"):
            result["thinking"] = data.get("delta", "") or None

        elif et == "response.output_item.added":
            item = data.get("item", {})
            if item.get("type") == "function_call":
                idx = data.get("output_index", 0)
                self._items[idx] = {
                    "tool_idx": self._tool_call_idx_counter,
                    "call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments_buf": "",
                }
                self._tool_call_idx_counter += 1
                # 提前发一个空 tool_call 注册名字和 id
                result["tool_calls"] = [{
                    "index": self._items[idx]["tool_idx"],
                    "id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": "",
                }]

        elif et == "response.function_call_arguments.delta":
            idx = data.get("output_index", 0)
            delta = data.get("delta", "")
            it = self._items.get(idx)
            if it and delta:
                it["arguments_buf"] += delta
                result["tool_calls"] = [{
                    "index": it["tool_idx"],
                    "id": None,  # 增量不带 id（前端用 index 关联）
                    "name": None,
                    "arguments": delta,
                }]

        elif et == "response.completed":
            resp = data.get("response", {})
            usage = resp.get("usage")
            if usage:
                # 字段名映射到 OpenAI 格式
                result["usage"] = {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            # finish_reason: 检查是否有 function_call
            output = resp.get("output", [])
            has_func = any(o.get("type") == "function_call" for o in output)
            result["finish_reason"] = "tool_calls" if has_func else "stop"

        elif et in ("response.failed", "response.incomplete"):
            err = data.get("response", {}).get("error") or data.get("error") or {}
            result["finish_reason"] = "error"
            result["content"] = f"[Responses API error: {err.get('message', et)}]"

        # 没有任何字段填充时返回 None 让上层跳过
        if not any(result.get(k) is not None for k in ("content", "tool_calls", "finish_reason", "usage", "thinking")):
            return None
        return result


def _merge_consecutive_roles(messages: list) -> list:
    """合并连续相同 role 的消息（Anthropic 要求严格交替）

    【铁律】带 _is_summary=True 标记的摘要消息禁止与其他消息合并：
    - 摘要 user 消息一旦合并真实 user 内容, cc2 会错误打在用户原文 block 上
    - 用户原文每次不同 → cc2 hash 每次变 → cache 全失效
    - 改为：摘要后面紧跟同 role(user) 真实消息时, 插一个空 assistant 占位
    """
    if not messages:
        return []
    merged = [messages[0]]
    for msg in messages[1:]:
        prev = merged[-1]
        # 摘要消息绝不合并：通过插入 assistant 占位强制隔开
        if prev.get("_is_summary") or msg.get("_is_summary"):
            if msg["role"] == prev["role"]:
                # 同 role 但有摘要标记: 插一条空 assistant 隔开
                merged.append({"role": "assistant", "content": "OK"})
            merged.append(msg)
            continue
        if msg["role"] == prev["role"]:
            # 合并检查：如果前一条 content 含 tool_result，不能和后续图片/文本合并
            # Anthropic 不允许 [tool_result, image] 混在同一 user content 里
            prev_content = prev["content"]
            curr_content = msg["content"]
            _prev_has_tool_result = isinstance(prev_content, list) and any(
                isinstance(p, dict) and p.get("type") == "tool_result" for p in prev_content
            )
            # v1.3.409 修复: 只有当 curr 含非 tool_result block (image/text 等) 时才隔开
            # 纯 tool_result + 纯 tool_result 是 Anthropic 官方支持的标准合并场景, 必须允许合并
            # 否则会导致 [tool_use A, tool_use B] 后只跟 [result A], B 找不到对应 result 而 400
            _curr_is_pure_tool_result = isinstance(curr_content, list) and len(curr_content) > 0 and all(
                isinstance(p, dict) and p.get("type") == "tool_result" for p in curr_content
            )
            if _prev_has_tool_result and not _curr_is_pure_tool_result:
                # curr 含 image/text 等其他类型, 隔开避免 [tool_result, image] 混合触发 Anthropic 400
                merged.append({"role": "assistant", "content": "OK"})
                merged.append(msg)
            else:
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    merged[-1]["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    merged[-1]["content"] = prev_content + curr_content
                elif isinstance(prev_content, str) and isinstance(curr_content, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev_content}] + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, str):
                    merged[-1]["content"] = prev_content + [{"type": "text", "text": curr_content}]
                # v15 (2026-05-16): 合并时透传 cc 锚点标记到 prev, 防止丢失
        else:
            merged.append(msg)
    return merged


def _convert_tools_to_anthropic(tools: list) -> list:
    """将 OpenAI 格式的 tools 转换为 Anthropic 格式"""
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        fn = tool["function"]
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def parse_stream_response(provider_id: str, line: str) -> Optional[dict]:
    """解析 SSE 流中的一行，返回标准化的 delta 对象
    
    返回格式统一为:
    {
        "content": str or None,
        "tool_calls": [{index, id, name, arguments}] or None,
        "finish_reason": str or None,
        "usage": {prompt_tokens, completion_tokens, total_tokens} or None,
        "thinking": str or None,        # 思考内容（如果有）
    }
    """
    pdef = get_provider_def(provider_id)
    if not pdef:
        return None

    api_format = pdef["api_format"]

    if api_format == "anthropic":
        return _parse_anthropic_stream(line)

    # copilot / openai 兼容
    return _parse_openai_stream(line)


def _parse_openai_stream(line: str) -> Optional[dict]:
    """解析 OpenAI / Copilot 格式的 SSE 行"""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    data_str = line[6:]
    if data_str == "[DONE]":
        return {"finish_reason": "stop", "done": True}
    try:
        data = json.loads(data_str)
    except:
        return None

    choice = (data.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    _raw_usage = data.get("usage")
    if _raw_usage and isinstance(_raw_usage, dict):
        _cached = (_raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        if _cached:
            _raw_usage = dict(_raw_usage)
            _raw_usage["cached_input_tokens"] = _cached  # 统一字段名供后端使用
    result = {
        "content": delta.get("content"),
        "tool_calls": None,
        "finish_reason": choice.get("finish_reason"),
        "usage": _raw_usage,
        "thinking": None,
    }

    # thinking (如 deepseek-reasoner 用 reasoning_content, Ollama 用 reasoning)
    if "reasoning_content" in delta:
        result["thinking"] = delta["reasoning_content"]
    elif "reasoning" in delta and delta["reasoning"]:
        result["thinking"] = delta["reasoning"]

    # tool_calls
    if delta.get("tool_calls"):
        result["tool_calls"] = []
        for tc in delta["tool_calls"]:
            result["tool_calls"].append({
                "index": tc.get("index", 0),
                "id": tc.get("id"),
                "name": tc.get("function", {}).get("name"),
                "arguments": tc.get("function", {}).get("arguments"),
            })

    return result


# Anthropic SSE 状态跟踪（每个流需要独立实例）
class AnthropicStreamParser:
    """Anthropic Messages API 的 SSE 解析器
    
    Anthropic SSE 事件类型:
    - message_start: 包含 message metadata
    - content_block_start: 新 content block 开始 (text / tool_use / thinking)
    - content_block_delta: content block 增量
    - content_block_stop: content block 结束
    - message_delta: 消息级别更新 (stop_reason, usage)
    - message_stop: 消息结束
    """
    def __init__(self):
        self._current_block_type = None
        self._current_tool_id = None
        self._current_tool_name = None
        self._block_index = 0
        self._usage = None

    def parse_line(self, line: str) -> Optional[dict]:
        line = line.strip()
        if not line:
            return None

        # 分离 event type 和 data
        if line.startswith("event: "):
            self._last_event = line[7:]
            return None
        if not line.startswith("data: "):
            return None

        data_str = line[6:]
        try:
            data = json.loads(data_str)
        except:
            return None

        event_type = getattr(self, '_last_event', '')

        if event_type == "message_start":
            msg = data.get("message", {})
            self._usage = msg.get("usage")
            return None

        if event_type == "content_block_start":
            block = data.get("content_block", {})
            self._current_block_type = block.get("type")
            self._block_index = data.get("index", 0)
            if self._current_block_type == "tool_use":
                self._current_tool_id = block.get("id", "")
                self._current_tool_name = block.get("name", "")
                return {
                    "content": None,
                    "tool_calls": [{
                        "index": self._block_index,
                        "id": self._current_tool_id,
                        "name": self._current_tool_name,
                        "arguments": None,
                    }],
                    "finish_reason": None,
                    "usage": None,
                    "thinking": None,
                }
            return None

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "text_delta":
                return {
                    "content": delta.get("text"),
                    "tool_calls": None,
                    "finish_reason": None,
                    "usage": None,
                    "thinking": None,
                }
            if delta_type == "thinking_delta":
                return {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": None,
                    "usage": None,
                    "thinking": delta.get("thinking"),
                }
            if delta_type == "input_json_delta":
                return {
                    "content": None,
                    "tool_calls": [{
                        "index": self._block_index,
                        "id": None,
                        "name": None,
                        "arguments": delta.get("partial_json", ""),
                    }],
                    "finish_reason": None,
                    "usage": None,
                    "thinking": None,
                }
            return None

        if event_type == "message_delta":
            delta = data.get("delta", {})
            usage = data.get("usage")
            if self._usage and usage:
                # 合并 input + output usage，透传 cache token 字段
                _cache_write = self._usage.get("cache_creation_input_tokens", 0) or 0
                _cache_read  = self._usage.get("cache_read_input_tokens", 0) or 0
                _input_tokens = self._usage.get("input_tokens", 0) or 0
                _output_tokens = usage.get("output_tokens", 0) or 0
                usage = {
                    "prompt_tokens": _input_tokens,
                    "completion_tokens": _output_tokens,
                    "total_tokens": _input_tokens + _output_tokens,
                    "cache_creation_input_tokens": _cache_write,
                    "cache_read_input_tokens": _cache_read,
                }
            return {
                "content": None,
                "tool_calls": None,
                "finish_reason": delta.get("stop_reason"),
                "usage": usage,
                "thinking": None,
            }

        if event_type == "message_stop":
            return {"finish_reason": "stop", "done": True, "content": None,
                    "tool_calls": None, "usage": None, "thinking": None}

        return None


# ============================================================
# Provider 验证 (validate key)
# ============================================================

def get_provider_for_model(cfg: dict, model_id: str) -> Optional[str]:
    """根据 ai_config.json 配置和 model_id 返回对应的 provider_id。

    优先级：
    1. 精确匹配 provider.models 列表里的 model.id
    2. 只有一个 provider：透传
    3. 多个 provider：用 default_provider 字段
    """
    model_id = (model_id or "").strip()
    providers = cfg.get("providers", {})
    for pid, pdata in providers.items():
        for m in pdata.get("models", []):
            if (m.get("id") or "").strip() == model_id:
                return pid
    if len(providers) == 1:
        return next(iter(providers))
    default_id = cfg.get("default_provider", "")
    if default_id and default_id in providers:
        return default_id
    return next(iter(providers), None)


def get_copilot_token(oauth_token: str, timeout: int = 15) -> str:
    """用 GitHub OAuth token 换取 Copilot API token（同步版，线程安全）。

    返回 copilot_token 字符串；失败时抛出 RuntimeError。
    结果按 oauth_token 缓存，避免每次请求都刷新。
    """
    now = time.time()
    with _copilot_token_lock:
        cached = _copilot_token_cache.get(oauth_token)
        if cached and now < cached[1] - 60:
            return cached[0]

    url = "https://api.github.com/copilot_internal/v2/token"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"token {oauth_token}", "User-Agent": "CATIA-Copilot/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data["token"]
        expires_at = data.get("expires_at", now + 1500)
        with _copilot_token_lock:
            _copilot_token_cache[oauth_token] = (token, float(expires_at))
        return token
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"获取 Copilot token 失败 HTTP {e.code}: {body[:200]}")
    except Exception as e:
        raise RuntimeError(f"获取 Copilot token 失败: {e}")

def is_ollama_url(url: str) -> bool:
    """检测URL是否指向Ollama（端口11434）"""
    return ":11434" in (url or "")


def get_ollama_native_url(openai_url: str) -> str:
    """将OpenAI compat URL转换为Ollama原生API URL
    例: http://localhost:11434/v1/chat/completions -> http://localhost:11434/api/chat
    """
    # 去掉/v1/chat/completions等后缀，拼上/api/chat
    import re as _re
    base = _re.sub(r'/v1/chat/completions$', '', openai_url)
    if not base:
        base = openai_url.rstrip('/').rsplit('/', 2)[0] if '/v1/' in openai_url else openai_url.rstrip('/')
    return f"{base}/api/chat"


def build_ollama_native_body(model: str, messages: list, tools: list = None,
                              stream: bool = True, max_tokens: int = 16384,
                              thinking_level: str = "") -> dict:
    """构建Ollama原生API请求体，将OpenAI格式消息转为Ollama格式"""
    # 转换messages：OpenAI格式 → Ollama原生格式
    converted = []
    for msg in messages:
        m = dict(msg)
        # content 数组格式（OpenAI多模态）→ Ollama原生格式（content字符串 + images数组）
        if isinstance(m.get("content"), list):
            _texts = []
            _images = []
            for part in m["content"]:
                if part.get("type") == "text":
                    _texts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    _url = part.get("image_url", {}).get("url", "")
                    # data:image/xxx;base64,XXXX → 提取纯base64
                    if _url.startswith("data:") and ";base64," in _url:
                        _images.append(_url.split(";base64,", 1)[1])
                    elif _url.startswith("http"):
                        _images.append(_url)  # Ollama也支持URL
            m["content"] = "\n".join(_texts) if _texts else ""
            if _images:
                m["images"] = _images
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # arguments: 字符串 → 对象
            new_tcs = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {"raw": args}
                new_tcs.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            m["tool_calls"] = new_tcs
        elif m.get("role") == "tool":
            # 去掉 tool_call_id（Ollama不用）
            m.pop("tool_call_id", None)
        converted.append(m)

    # thinking_level 控制: off/空=禁用, low/medium/high=启用+budget
    _think_on = bool(thinking_level and thinking_level != "off")
    body = {
        "model": model,
        "messages": converted,
        "stream": stream,
        "think": _think_on,
        "options": {
            "num_predict": max_tokens,
        }
    }
    if _think_on:
        tc = get_thinking_config(model, api_url="http://localhost:11434")
        if tc and tc.get("param_type") == "ollama_think":
            for lv in tc["levels"]:
                if lv["id"] == thinking_level and lv.get("budget_tokens"):
                    body["think_budget"] = lv["budget_tokens"]
                    break
    if tools:
        body["tools"] = tools
    return body


import re as _re_ollama
_OLLAMA_CHANNEL_RE = _re_ollama.compile(
    r'<\|(?:channel|tool_response|tool_call)>(?:thought)?|'
    r'<(?:channel|tool_call|tool_response)\|>(?:thought)?|'
    r'}\s*<tool_call\|>|'
    r'\(Actual thought process ended\)|Sigh\.'
)


def _parse_ollama_native_stream(line: str) -> dict | None:
    """解析Ollama原生NDJSON流格式
    每行是一个JSON: {"model":"...","message":{"role":"assistant","content":"...","thinking":"...","tool_calls":[...]},"done":false}
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    if data.get("done"):
        return {"done": True, "usage": {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }}

    msg = data.get("message", {})
    result = {
        "content": msg.get("content") or None,
        "thinking": msg.get("thinking") or None,
        "tool_calls": None,
        "finish_reason": None,
        "usage": None,
    }

    # 清洗content中可能残留的channel标签
    if result["content"]:
        _c = result["content"]
        _c = _OLLAMA_CHANNEL_RE.sub('', _c)
        result["content"] = _c or None

    # Tool calls
    if msg.get("tool_calls"):
        result["tool_calls"] = []
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            args_str = json.dumps(fn.get("arguments", {})) if isinstance(fn.get("arguments"), dict) else str(fn.get("arguments", ""))
            result["tool_calls"].append({
                "index": 0,
                "id": tc.get("id") or f"call_{id(tc)}",
                "name": fn.get("name", ""),
                "arguments": args_str,
            })
        result["finish_reason"] = "tool_calls"
        # 有tool_calls时丢弃content（Gemma4会在content里幻觉工具结果）
        result["content"] = None

    return result
