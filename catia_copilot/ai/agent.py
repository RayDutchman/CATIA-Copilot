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

    # ── 向 UI 推送的信号 ──────────────────────────────────────────────────────
    # 流式 token（AI 文字回复片段）
    token_received = Signal(str)
    # 工具调用开始（工具名, JSON 格式的参数字符串）
    tool_started = Signal(str, str)
    # 工具执行进度（来自 progress_callback 的字符串消息）
    tool_progress = Signal(str)
    # 工具调用完成（工具名, 结果字符串）
    tool_finished = Signal(str, str)
    # 一轮 LLM 回复完成（无论是否有工具调用）
    turn_finished = Signal()
    # 全部完成（最终完整回复文本）
    all_done = Signal(str)
    # 错误信息
    error_occurred = Signal(str)

    # ── 工具调用请求（发给主线程执行）────────────────────────────────────────
    # 参数：工具名, JSON 参数字符串, 请求 ID（用于匹配结果）
    tool_call_requested = Signal(str, str, str)

    def __init__(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        parent=None,
    ):
        super().__init__(parent)
        self._messages = list(messages)  # 对话历史（含 system prompt）
        self._config = config
        self._stop = False

        # 工具调用结果同步机制
        self._tool_result_event = threading.Event()
        self._tool_result_value: str = ""

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """请求停止（设置标志，不强制终止线程）。"""
        self._stop = True

    def receive_tool_result(self, result: str) -> None:
        """
        主线程执行完工具后调用此方法，将结果传回 AgentWorker。
        必须在主线程调用。
        """
        self._tool_result_value = result
        self._tool_result_event.set()

    # ── 线程主体 ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self._agent_loop()
        except Exception:
            tb = traceback.format_exc()
            logger.error("AgentWorker 异常：%s", tb)
            self.error_occurred.emit(f"Agent 内部错误：\n{tb}")

    def _agent_loop(self) -> None:
        """多轮工具调用主循环。"""
        from catia_copilot.ai.tools import tools_schema

        messages = list(self._messages)
        max_rounds = self._config.get("max_tool_rounds", 20)
        final_text = ""

        for round_idx in range(max_rounds):
            if self._stop:
                self.error_occurred.emit("已取消")
                return

            # 调用 LLM（流式）
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
                        tool_calls = payload  # list[{id, name, arguments}]

                    elif event_type == "error":
                        self.error_occurred.emit(payload)
                        return

            except Exception:
                tb = traceback.format_exc()
                self.error_occurred.emit(f"调用 LLM 失败：\n{tb}")
                return

            self.turn_finished.emit()
            full_text = "".join(text_chunks)

            # 没有工具调用 → 对话结束
            if not tool_calls:
                final_text = full_text
                self.all_done.emit(final_text)
                return

            # 有工具调用 → 把 assistant 消息加入历史
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if full_text:
                assistant_msg["content"] = full_text
            else:
                assistant_msg["content"] = None
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            messages.append(assistant_msg)

            # 依次执行工具
            for tc in tool_calls:
                if self._stop:
                    self.error_occurred.emit("已取消")
                    return

                tool_name = tc["name"]
                tool_args_str = tc["arguments"]
                tool_id = tc["id"]

                self.tool_started.emit(tool_name, tool_args_str)

                # 请求主线程执行工具
                result_str = self._request_tool_execution(tool_name, tool_args_str, tool_id)

                self.tool_finished.emit(tool_name, result_str)

                # 把工具结果加入历史
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": result_str,
                })

        # 超过最大轮数
        self.error_occurred.emit(f"已达到最大工具调用轮数（{max_rounds}），停止。")

    def _request_tool_execution(self, tool_name: str, tool_args_str: str, tool_id: str) -> str:
        """
        向主线程请求执行工具，阻塞等待结果。
        使用 threading.Event 同步。
        """
        self._tool_result_event.clear()
        self._tool_result_value = ""

        # 发信号给主线程
        self.tool_call_requested.emit(tool_name, tool_args_str, tool_id)

        # 等待主线程回调 receive_tool_result()
        got_result = self._tool_result_event.wait(timeout=_TOOL_TIMEOUT)
        if not got_result:
            return json.dumps({"error": f"工具 {tool_name} 执行超时（{_TOOL_TIMEOUT}s）"}, ensure_ascii=False)

        return self._tool_result_value

    # ── LLM 流式调用 ──────────────────────────────────────────────────────────

    def _stream_llm(self, messages: list[dict], tools: list[dict]):
        """
        流式调用 LLM，yield (event_type, payload) 元组：
          ("text", str)          — 文字 token
          ("tool_calls", list)   — 完整的工具调用列表
          ("error", str)         — 错误信息
        """
        cfg = self._config
        api_base = cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model", "gpt-4o")
        temperature = cfg.get("temperature", 0.7)
        timeout = cfg.get("timeout", 120)

        url = f"{api_base}/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "temperature": temperature,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # 收集流式 tool_calls delta（需要拼接）
                tool_calls_acc: dict[int, dict] = {}  # index → {id, name, arguments}

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

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")

                    # 文字内容
                    content = delta.get("content")
                    if content:
                        yield "text", content

                    # 工具调用 delta 拼接
                    delta_tools = delta.get("tool_calls", [])
                    for dt in delta_tools:
                        idx = dt.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        tc = tool_calls_acc[idx]
                        if dt.get("id"):
                            tc["id"] = dt["id"]
                        fn = dt.get("function", {})
                        if fn.get("name"):
                            tc["name"] += fn["name"]
                        if fn.get("arguments"):
                            tc["arguments"] += fn["arguments"]

                    # 流结束
                    if finish_reason in ("stop", "tool_calls", "length"):
                        break

                # 如果有工具调用，yield 完整列表
                if tool_calls_acc:
                    yield "tool_calls", list(tool_calls_acc.values())

        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                err_body = json.loads(body_bytes.decode("utf-8"))
                err_msg = err_body.get("error", {}).get("message", str(e))
            except Exception:
                err_msg = body_bytes.decode("utf-8", errors="replace")
            yield "error", f"HTTP {e.code}：{err_msg}"

        except urllib.error.URLError as e:
            yield "error", f"网络错误：{e.reason}"

        except TimeoutError:
            yield "error", f"请求超时（{timeout}s）"

        except Exception:
            yield "error", traceback.format_exc()
