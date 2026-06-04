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

        通过 config.get_provider_for_model 路由到对应 provider，
        支持 ai_config.json 的多 provider / 多模型配置。
        """
        cfg = self._config
        model_id = cfg.get("default_model", "gpt-4o")

        # 通过 config 模块路由到具体 provider
        provider, model_cfg = ai_config.get_provider_for_model(cfg, model_id)

        api_base = provider.get("api_base", "https://api.openai.com").rstrip("/")
        api_key  = provider.get("api_key", "")
        timeout  = cfg.get("timeout", 120)
        temperature = cfg.get("temperature", 0.7)
        supports_tools = model_cfg.get("supports_tools", True)

        url = f"{api_base}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model":          model_id,
            "messages":       messages,
            "stream":         True,
            "stream_options": {"include_usage": True},   # 获取 token 用量
            "temperature":    temperature,
        }
        if tools and supports_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept":        "text/event-stream",
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._resp = resp
                tool_calls_acc: dict[int, dict] = {}
                last_usage: tuple[int, int] | None = None

                try:
                    for raw_line in resp:
                        if self._stop:
                            return
                        line = raw_line.decode("utf-8").rstrip("\n\r")
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # 捕获 token 用量（stream_options.include_usage 时出现）
                        usage = chunk.get("usage")
                        if usage:
                            last_usage = (
                                usage.get("prompt_tokens", 0),
                                usage.get("completion_tokens", 0),
                            )

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason")

                        content = delta.get("content")
                        if content:
                            yield "text", content

                        for dt in delta.get("tool_calls", []):
                            idx = dt.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            tc = tool_calls_acc[idx]
                            if dt.get("id"):                    tc["id"] = dt["id"]
                            fn = dt.get("function", {})
                            if fn.get("name"):                  tc["name"] += fn["name"]
                            if fn.get("arguments"):             tc["arguments"] += fn["arguments"]

                        if finish_reason in ("stop", "tool_calls", "length"):
                            if finish_reason == "length" and tool_calls_acc:
                                yield "error", (
                                    "LLM 输出因 token 超限被截断，工具调用参数不完整。"
                                    "请尝试缩短对话历史或减少上下文消息数。"
                                )
                                return
                            break

                except Exception:
                    # resp.close() 被 stop() 调用后读取会抛异常；若是正常停止则静默退出
                    if self._stop:
                        return
                    raise
                finally:
                    self._resp = None

                if last_usage:
                    self.usage_updated.emit(*last_usage)

                if tool_calls_acc:
                    yield "tool_calls", list(tool_calls_acc.values())

        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                err_msg = json.loads(body_bytes.decode("utf-8")).get("error", {}).get("message", str(e))
            except Exception:
                err_msg = body_bytes.decode("utf-8", errors="replace")
            yield "error", f"HTTP {e.code}：{err_msg}"

        except urllib.error.URLError as e:
            # urllib 超时时 e.reason 是 socket.timeout 实例
            if isinstance(e.reason, (TimeoutError, OSError)) and "timed out" in str(e.reason).lower():
                yield "error", f"请求超时（{timeout}s）"
            else:
                yield "error", f"网络错误：{e.reason}"

        except Exception:
            yield "error", traceback.format_exc()
