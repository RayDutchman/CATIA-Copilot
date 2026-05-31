"""
AI 会话数据结构模块。

ChatSession 是单个对话会话的完整状态，包含：
  - 元数据（id、名称、工作空间、时间戳）
  - per-session 模型参数（可覆盖全局 ai_config.json）
  - 对话历史（OpenAI messages 格式）
  - session 级记忆（持久化，本 session 特有）
  - 运行时状态（不持久化，每次加载时重置）

序列化：to_dict() / from_dict()，不依赖 pydantic。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatSession:
    """单个 AI 对话会话的完整状态。"""

    # ── 1. 元数据 ────────────────────────────────────────────────────────────
    session_id:  str                  # uuid hex 前 8 位，由 SessionManager 生成
    user_id:     str = "local"        # 预留，当前固定值
    name:        str = "\u65b0\u5bf9\u8bdd"  # 显示在侧边栏的会话名（"新对话"）
    model:       str = ""             # 空字符串 = 使用全局默认模型
    workspace:   str | None = None    # 工作空间目录绝对路径，None = 不限制
    created_at:  str = ""             # ISO 8601 时间戳（UTC）
    updated_at:  str = ""             # ISO 8601 时间戳，每次写入时更新

    # ── 2. 模型参数（None = 跟随全局 ai_config.json）────────────────────────
    config: dict[str, Any] = field(default_factory=lambda: {
        "temperature":          None,   # None = 使用全局值；0–2
        "top_p":                None,   # 预留，暂不传给 API；0–1
        "max_tokens":           None,   # 预留，暂不传给 API
        "max_context_messages": 100,    # 发给 LLM 的最近消息数上限（system 不计入）
    })

    # ── 3. 对话历史（OpenAI messages 格式，直接发给 LLM）────────────────────
    messages: list[dict[str, Any]] = field(default_factory=list)

    # ── 4. 运行时状态（不持久化，每次加载时重置为空）────────────────────────
    session_state: dict[str, Any] = field(default_factory=lambda: {
        "current_step":       None,  # 当前工作流阶段
        "extracted_entities": {},    # 对话中提取的变量
        "temp_data":          {},    # 临时工作内存
    })

    # ── 5. Session 级记忆（持久化，本 session 特有）─────────────────────────
    #   与全局 memory.md 互补：
    #   - memory.md：AI 主动维护的跨 session 长期知识
    #   - session.memory：本 session 内积累的上下文记忆
    memory: dict[str, Any] = field(default_factory=lambda: {
        "user_preferences":  {},   # 本 session 的偏好（语言、风格等）
        "hard_constraints":  [],   # 本 session 的强制规则
        "historical_faults": [],   # 本 session 的错误记录
    })

    # ── 序列化 ───────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可存储的 dict，跳过 session_state（运行时状态）。"""
        return {
            "session_id":  self.session_id,
            "user_id":     self.user_id,
            "name":        self.name,
            "model":       self.model,
            "workspace":   self.workspace,
            "created_at":  self.created_at,
            "updated_at":  self.updated_at,
            "config":      self.config,
            "messages":    self.messages,
            "memory":      self.memory,
            # session_state 不持久化
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatSession":
        """从 JSON dict 反序列化，session_state 重置为空。"""
        session = cls(session_id=d["session_id"])
        session.user_id    = d.get("user_id", "local")
        session.name       = d.get("name", "\u65b0\u5bf9\u8bdd")
        session.model      = d.get("model", "")
        session.workspace  = d.get("workspace")
        session.created_at = d.get("created_at", "")
        session.updated_at = d.get("updated_at", "")
        # 合并 config：先用默认值，再用文件中的值覆盖
        default_cfg: dict[str, Any] = {
            "temperature":          None,
            "top_p":                None,
            "max_tokens":           None,
            "max_context_messages": 100,
        }
        default_cfg.update(d.get("config", {}))
        session.config = default_cfg
        session.messages = d.get("messages", [])
        # 合并 memory：先用默认值，再用文件中的值覆盖
        default_mem: dict[str, Any] = {
            "user_preferences":  {},
            "hard_constraints":  [],
            "historical_faults": [],
        }
        default_mem.update(d.get("memory", {}))
        session.memory = default_mem
        # session_state 保持默认（不从文件恢复）
        return session

    def to_index_entry(self) -> dict[str, Any]:
        """返回轻量索引条目（不含 messages，供 index.json 使用）。"""
        return {
            "session_id":  self.session_id,
            "name":        self.name,
            "created_at":  self.created_at,
            "updated_at":  self.updated_at,
            "workspace":   self.workspace,
        }
