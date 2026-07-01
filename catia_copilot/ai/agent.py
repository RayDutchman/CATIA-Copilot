"""
AI Agent 核心模块。

AgentWorker 在后台 QThread 中运行，通过 urllib 直接调用 OpenAI 兼容 API，
支持流式输出和多轮工具调用循环。

COM 线程安全策略（方案 B）：
  - AgentWorker 运行在后台线程，不直接调用 CATIA COM
  - 需要执行工具时，emit tool_call_requested Signal
  - 主线程执行工具函数后，通过 threading.Event + 共享变量把结果传回
  - AgentWorker 阻塞等待结果（最多 10 分钟）
"""

from __future__ import annotations

import json
import logging
import threading
import traceback
import urllib.error
import urllib.request
from typing import Any

from PySide6.QtCore import QThread, Signal

from catia_copilot.ai import config as ai_config
from catia_copilot.ai import providers as ai_providers
from catia_copilot.ai.tools import tools_schema

logger = logging.getLogger(__name__)

# 工具调用等待超时（秒）
_TOOL_TIMEOUT = 600


class AgentWorker(QThread):
    """
    后台 AI Agent 线程。

    生命周期：
      1. 外部创建并设置 messages / config
      2. start() 启动线程
      3. 通过 Signal 向 UI 推送进度
      4. all_done 或 error_occurred 后线程结束
    """

    # ── 向 UI 推送的信号 ────────────────────────────────────────────
    token_received   = Signal(str)       # 流式 token
    tool_started     = Signal(str, str, str)  # 工具调用开始（工具名, JSON 参数, tool_call_id）
    tool_progress    = Signal(str)       # 工具执行进度
    tool_finished    = Signal(str, str)  # 工具调用完成（工具名, 结果）
    turn_finished    = Signal()          # 一轮 LLM 回复完成
    all_done         = Signal(str)       # 全部完成（最终完整回复）
    error_occurred   = Signal(str)       # 错误信息
    usage_updated    = Signal(int, int)  # token 用量更新（input_tokens, output_tokens）

    # 工具调用请求（发给主线程执行）
    tool_call_requested = Signal(str, str, str)  # 工具名, JSON 参数, 请求 ID

    def __init__(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        parent=None,
    ):
        super().__init__(parent)
        self._messages = list(messages)
        self._config = config
        self._stop = False
        self._tool_result_event = threading.Event()
        self._tool_result_value: str = ""
        self._resp = None   # 当前 HTTP 响应对象，stop() 时强制关闭以打断阻塞读取

    def stop(self) -> None:
        """请求停止。关闭当前 HTTP 响应以立即打断阻塞的流式读取。"""
        self._stop = True
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass

    def receive_tool_result(self, result: str) -> None:
        """主线程执行完工具后调用，将结果传回 AgentWorker。"""
        self._tool_result_value = result
        self._tool_result_event.set()

    def run(self) -> None:
        try:
            self._agent_loop()
        except Exception:
            tb = traceback.format_exc()
            logger.error("AgentWorker 异常：%s", tb)
            self.error_occurred.emit(f"Agent 内部错误：\n{tb}")

    def _agent_loop(self) -> None:
        """多轮工具调用主循环。"""
        messages = list(self._messages)
        max_rounds = self._config.get("max_tool_rounds", 20)

        for _ in range(max_rounds):
            if self._stop:
                self.error_occurred.emit("已取消")
                return

            text_chunks: list[str] = []
            tool_calls: list[dict] = []

            try:
                for event_type, payload in self._stream_llm(messages, tools_schema):
                    if self._stop:
                        self.error_occurred.emit("已取消")
                        return
                    if event_type == "text":
                        text_chunks.append(payload)
                        self.token_received.emit(payload)
                    elif event_type == "tool_calls":
                        tool_calls = payload
                    elif event_type == "error":
                        self.error_occurred.emit(payload)
                        return
            except Exception:
                self.error_occurred.emit(f"调用 LLM 失败：\n{traceback.format_exc()}")
                return

            self.turn_finished.emit()
            full_text = "".join(text_chunks)

            if not tool_calls:
                self.all_done.emit(full_text)
                return

            # 把 assistant 消息加入历史
            messages.append({
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                if self._stop:
                    self.error_occurred.emit("已取消")
                    return
                self.tool_started.emit(tc["name"], tc["arguments"], tc["id"])
                result_str = self._request_tool_execution(tc["name"], tc["arguments"], tc["id"])
                self.tool_finished.emit(tc["name"], result_str)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

        self.error_occurred.emit(f"已达到最大工具调用轮数（{max_rounds}），停止。")

    def _request_tool_execution(self, tool_name: str, tool_args_str: str, tool_id: str) -> str:
        """向主线程请求执行工具，阻塞等待结果。"""
        self._tool_result_event.clear()
        self._tool_result_value = ""
        self.tool_call_requested.emit(tool_name, tool_args_str, tool_id)
        got = self._tool_result_event.wait(timeout=_TOOL_TIMEOUT)
        if not got:
            return json.dumps(
                {"error": f"工具 {tool_name} 执行超时（{_TOOL_TIMEOUT}s）"},
                ensure_ascii=False,
            )
        return self._tool_result_value

    # ── LLM 流式调用 ──────────────────────────────────────────────

    def _stream_llm(self, messages: list[dict], tools: list[dict]):
        """
        流式调用 LLM，yield (event_type, payload) 元组：
          ("text", str)         — 文字 token
          ("tool_calls", list)  — 完整的工具调用列表
          ("error", str)        — 错误信息

        根据 provider_type 自动分发到对应 provider 实现。
        """
        cfg = self._config
        model_id = cfg.get("default_model", "gpt-4o")

        # 从配置中路由到具体 provider
        provider, model_cfg = ai_config.get_provider_for_model(cfg, model_id)
        provider_type = provider.get("provider_type", "openai").lower()
        timeout = cfg.get("timeout", 120)
        temperature = cfg.get("temperature", 0.7)
        supports_tools = model_cfg.get("supports_tools", True)
        max_tokens = model_cfg.get("max_tokens", 16384)

        if provider_type == "bedrock":
            yield from self._stream_bedrock(messages, tools if supports_tools else None,
                                            provider, model_id, max_tokens, temperature)
        elif provider_type == "ollama":
            yield from self._stream_ollama(messages, tools if supports_tools else None,
                                           provider, model_id, max_tokens, timeout)
        elif provider_type == "anthropic":
            yield from self._stream_openai_compat(
                messages, tools if supports_tools else None,
                provider, model_id, max_tokens, temperature, timeout,
                provider_type="anthropic",
            )
        elif provider_type in ("vertex",):
            yield from self._stream_vertex(messages, tools if supports_tools else None,
                                           provider, model_id, max_tokens, temperature, timeout)
        else:
            # openai / openrouter / deepseek / alibaba / iflytek / github / custom / 其他
            yield from self._stream_openai_compat(
                messages, tools if supports_tools else None,
                provider, model_id, max_tokens, temperature, timeout,
                provider_type=provider_type,
            )

    def _stream_openai_compat(self, messages, tools, provider_cfg, model_id,
                              max_tokens, temperature, timeout, provider_type="openai"):
        """
        通用 OpenAI 兼容流式实现，同时处理 Anthropic / GitHub Copilot / 讯飞等。
        使用 providers.py 构造 URL、headers 和 request body，
        并用对应的 StreamParser 解析 SSE。
        """
        provider_id = provider_cfg.get("provider_id", provider_type)

        # GitHub Copilot：先获取 copilot_token
        token = None
        if provider_type == "github":
            oauth_token = provider_cfg.get("oauth_token", "")
            if not oauth_token:
                yield "error", "GitHub Copilot 未配置 oauth_token，请先绑定 GitHub 账号"
                return
            try:
                token = ai_providers.get_copilot_token(oauth_token)
            except RuntimeError as e:
                yield "error", str(e)
                return

        try:
            url = ai_providers.get_api_url(provider_id, provider_cfg)
        except Exception as e:
            yield "error", f"获取 API URL 失败: {e}"
            return

        headers = ai_providers.get_api_headers(provider_id, provider_cfg, token=token)
        body = ai_providers.build_request_body(
            provider_id=provider_id,
            model=model_id,
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            api_url=url,
            provider_cfg=provider_cfg,
        )
        # 讯飞：根据 model 修正实际 URL 和 model 字段
        if provider_type == "iflytek":
            url = ai_providers._iflytek_chat_url(model_id)
            body["model"] = ai_providers._iflytek_real_model(model_id)

        body_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        # 选择流解析器
        if provider_type == "anthropic":
            parser = ai_providers.AnthropicStreamParser()
            parse_fn = parser.parse_line
        else:
            parse_fn = lambda line: ai_providers._parse_openai_stream(line)

        tool_calls_acc: dict[int, dict] = {}
        last_usage: tuple[int, int] | None = None

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._resp = resp
                try:
                    for raw_line in resp:
                        if self._stop:
                            return
                        line = raw_line.decode("utf-8").rstrip("\n\r")
                        delta = parse_fn(line)
                        if delta is None:
                            continue

                        # 完成标志
                        if delta.get("done"):
                            break

                        # token 用量
                        usage = delta.get("usage")
                        if usage and isinstance(usage, dict):
                            last_usage = (
                                usage.get("prompt_tokens", 0),
                                usage.get("completion_tokens", 0),
                            )

                        # 文字内容
                        content = delta.get("content")
                        if content:
                            yield "text", content

                        # 工具调用增量
                        tcs = delta.get("tool_calls")
                        if tcs:
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                                entry = tool_calls_acc[idx]
                                if tc.get("id"):
                                    entry["id"] = tc["id"]
                                if tc.get("name"):
                                    entry["name"] += tc["name"]
                                if tc.get("arguments"):
                                    entry["arguments"] += tc["arguments"]

                        # 结束原因
                        finish = delta.get("finish_reason")
                        if finish in ("stop", "end_turn", "tool_calls", "tool_use", "length"):
                            if finish == "length" and tool_calls_acc:
                                yield "error", (
                                    "LLM 输出因 token 超限被截断，工具调用参数不完整。"
                                    "请尝试缩短对话历史或减少上下文消息数。"
                                )
                                return
                            break

                except Exception:
                    if self._stop:
                        return
                    raise
                finally:
                    self._resp = None

        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                err_msg = json.loads(body_bytes.decode("utf-8")).get("error", {}).get("message", str(e))
            except Exception:
                err_msg = body_bytes.decode("utf-8", errors="replace")
            yield "error", f"HTTP {e.code}：{err_msg}"
            return
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, OSError)) and "timed out" in str(e.reason).lower():
                yield "error", f"请求超时（{timeout}s）"
            else:
                yield "error", f"网络错误：{e.reason}"
            return
        except Exception:
            yield "error", traceback.format_exc()
            return

        if last_usage:
            self.usage_updated.emit(*last_usage)

        if tool_calls_acc:
            yield "tool_calls", list(tool_calls_acc.values())

    def _stream_ollama(self, messages, tools, provider_cfg, model_id, max_tokens, timeout):
        """Ollama 原生 /api/chat NDJSON 流式实现。"""
        base_url = provider_cfg.get("api_base", "http://localhost:11434").rstrip("/")
        # 若是 OpenAI compat URL，转换为 native URL
        if "/v1" in base_url:
            url = ai_providers.get_ollama_native_url(base_url + "/chat/completions")
        else:
            url = f"{base_url}/api/chat"

        body = ai_providers.build_ollama_native_body(
            model=model_id,
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=max_tokens,
        )
        body_bytes = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        tool_calls_acc: dict[int, dict] = {}
        last_usage: tuple[int, int] | None = None

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._resp = resp
                try:
                    for raw_line in resp:
                        if self._stop:
                            return
                        line = raw_line.decode("utf-8")
                        delta = ai_providers._parse_ollama_native_stream(line)
                        if delta is None:
                            continue
                        if delta.get("done"):
                            usage = delta.get("usage")
                            if usage:
                                last_usage = (
                                    usage.get("prompt_tokens", 0),
                                    usage.get("completion_tokens", 0),
                                )
                            break
                        content = delta.get("content")
                        if content:
                            yield "text", content
                        tcs = delta.get("tool_calls")
                        if tcs:
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                                entry = tool_calls_acc[idx]
                                if tc.get("name"):
                                    entry["name"] += tc["name"]
                                if tc.get("arguments"):
                                    entry["arguments"] += tc["arguments"]
                except Exception:
                    if self._stop:
                        return
                    raise
                finally:
                    self._resp = None
        except urllib.error.HTTPError as e:
            yield "error", f"Ollama HTTP {e.code}：{e.read().decode('utf-8', errors='replace')[:200]}"
            return
        except Exception:
            yield "error", traceback.format_exc()
            return

        if last_usage:
            self.usage_updated.emit(*last_usage)
        if tool_calls_acc:
            yield "tool_calls", list(tool_calls_acc.values())

    def _stream_bedrock(self, messages, tools, provider_cfg, model_id, max_tokens, temperature):
        """AWS Bedrock converse_stream 实现（需要 boto3）。"""
        try:
            import boto3
        except ImportError:
            yield "error", "AWS Bedrock 需要安装 boto3：pip install boto3"
            return

        region = provider_cfg.get("aws_region", "us-east-1")
        access_key = provider_cfg.get("aws_access_key_id", "")
        secret_key = provider_cfg.get("aws_secret_access_key", "")

        bedrock = boto3.client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )

        # 转换 messages 为 Bedrock converse 格式
        system_parts = []
        bedrock_msgs = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": content or ""})
            elif role == "user":
                bedrock_msgs.append({"role": "user", "content": [{"text": content or ""}]})
            elif role == "assistant":
                if msg.get("tool_calls"):
                    parts = []
                    if content:
                        parts.append({"text": content})
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        args_str = fn.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except Exception:
                            args = {}
                        parts.append({
                            "toolUse": {
                                "toolUseId": tc.get("id", ""),
                                "name": fn.get("name", ""),
                                "input": args,
                            }
                        })
                    bedrock_msgs.append({"role": "assistant", "content": parts})
                else:
                    bedrock_msgs.append({"role": "assistant", "content": [{"text": content or ""}]})
            elif role == "tool":
                bedrock_msgs.append({
                    "role": "user",
                    "content": [{
                        "toolResult": {
                            "toolUseId": msg.get("tool_call_id", ""),
                            "content": [{"text": content or ""}],
                        }
                    }]
                })

        # 转换 tools
        bedrock_tools = None
        if tools:
            tool_specs = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t["function"]
                    tool_specs.append({
                        "toolSpec": {
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "inputSchema": {"json": fn.get("parameters", {"type": "object", "properties": {}})},
                        }
                    })
            if tool_specs:
                bedrock_tools = {"tools": tool_specs}

        kwargs = {
            "modelId": model_id,
            "messages": bedrock_msgs,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_parts:
            kwargs["system"] = system_parts
        if bedrock_tools:
            kwargs["toolConfig"] = bedrock_tools

        try:
            response = bedrock.converse_stream(**kwargs)
            stream = response.get("stream")
            if not stream:
                yield "error", "Bedrock 返回空流"
                return

            tool_calls_acc = {}
            input_tokens = 0
            output_tokens = 0

            for event in stream:
                if self._stop:
                    return
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        yield "text", delta["text"]
                    if "toolUse" in delta:
                        idx = event["contentBlockDelta"].get("contentBlockIndex", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        tool_calls_acc[idx]["arguments"] += delta["toolUse"].get("input", "")
                elif "contentBlockStart" in event:
                    start = event["contentBlockStart"].get("start", {})
                    if "toolUse" in start:
                        idx = event["contentBlockStart"].get("contentBlockIndex", 0)
                        tool_calls_acc[idx] = {
                            "id": start["toolUse"].get("toolUseId", ""),
                            "name": start["toolUse"].get("name", ""),
                            "arguments": "",
                        }
                elif "metadata" in event:
                    usage = event["metadata"].get("usage", {})
                    input_tokens = usage.get("inputTokens", 0)
                    output_tokens = usage.get("outputTokens", 0)

            if input_tokens or output_tokens:
                self.usage_updated.emit(input_tokens, output_tokens)
            if tool_calls_acc:
                yield "tool_calls", list(tool_calls_acc.values())

        except Exception:
            yield "error", traceback.format_exc()

    def _stream_vertex(self, messages, tools, provider_cfg, model_id,
                       max_tokens, temperature, timeout):
        """Google Vertex AI streamGenerateContent 实现（需要 google-auth）。"""
        project = provider_cfg.get("gcp_project_id", "")
        region = provider_cfg.get("gcp_region", "us-central1")
        if not project:
            yield "error", "Vertex AI 未配置 gcp_project_id"
            return

        try:
            access_token = ai_providers._get_vertex_access_token(provider_cfg)
        except Exception as e:
            yield "error", f"Vertex AI 认证失败: {e}"
            return

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        if ai_providers._is_vertex_anthropic_model(model_id):
            body = ai_providers._build_vertex_anthropic_body(
                model_id, messages, tools, stream=True,
                max_tokens=max_tokens, temperature=temperature,
            )
            base_url = (
                f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
                f"/locations/{region}/publishers/anthropic/models/{model_id}:streamRawPredict"
            )
            # Anthropic on Vertex 用 AnthropicStreamParser
            parser = ai_providers.AnthropicStreamParser()
            parse_fn = parser.parse_line
        else:
            body = ai_providers._build_vertex_body(
                model_id, messages, tools, stream=True,
                max_tokens=max_tokens, temperature=temperature,
            )
            base_url = (
                f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
                f"/locations/{region}/publishers/google/models/{model_id}:streamGenerateContent"
            )
            vertex_parser = ai_providers._VertexStreamParser()
            parse_fn = vertex_parser.parse_line

        url = base_url
        body_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        tool_calls_acc: dict[int, dict] = {}
        last_usage: tuple[int, int] | None = None

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._resp = resp
                try:
                    for raw_line in resp:
                        if self._stop:
                            return
                        line = raw_line.decode("utf-8").rstrip()
                        delta = parse_fn(line)
                        if delta is None:
                            continue
                        if delta.get("done"):
                            break
                        usage = delta.get("usage")
                        if usage:
                            last_usage = (
                                usage.get("prompt_tokens", 0),
                                usage.get("completion_tokens", 0),
                            )
                        content = delta.get("content")
                        if content:
                            yield "text", content
                        tcs = delta.get("tool_calls")
                        if tcs:
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                                entry = tool_calls_acc[idx]
                                if tc.get("name"):
                                    entry["name"] += tc["name"]
                                if tc.get("arguments"):
                                    entry["arguments"] += tc["arguments"]
                        finish = delta.get("finish_reason")
                        if finish in ("stop", "end_turn", "tool_calls", "tool_use"):
                            break
                except Exception:
                    if self._stop:
                        return
                    raise
                finally:
                    self._resp = None
        except urllib.error.HTTPError as e:
            yield "error", f"Vertex HTTP {e.code}：{e.read().decode('utf-8', errors='replace')[:300]}"
            return
        except Exception:
            yield "error", traceback.format_exc()
            return

        if last_usage:
            self.usage_updated.emit(*last_usage)
        if tool_calls_acc:
            yield "tool_calls", list(tool_calls_acc.values())

