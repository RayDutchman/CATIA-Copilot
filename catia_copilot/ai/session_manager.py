"""
AI 会话持久化管理模块。

目录结构（项目根目录，已加入 .gitignore）：
  ai_sessions/
    index.json              ← 轻量索引，只存元数据，不存 messages
    session_<id>.json       ← 完整 session 数据

index.json 格式：
  [
    {"session_id": "a1b2c3d4", "name": "...", "created_at": "...",
     "updated_at": "...", "workspace": null},
    ...
  ]
  按 created_at 降序排列（新建的在最前）。

主要接口：
  SessionManager.create_session()   → ChatSession
  SessionManager.load_session(id)   → ChatSession
  SessionManager.save_session(s)    → None（实时调用）
  SessionManager.delete_session(id) → None
  SessionManager.list_sessions()    → list[dict]（读 index，轻量）
  SessionManager.rename_session(id, name) → None
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catia_copilot.ai.session import ChatSession

logger = logging.getLogger(__name__)

# 项目根目录（此文件在 catia_copilot/ai/session_manager.py）
_BASE_DIR = Path(__file__).parent.parent.parent
_SESSIONS_DIR = _BASE_DIR / "ai_sessions"
_INDEX_PATH   = _SESSIONS_DIR / "index.json"


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"session_{session_id}.json"


def _ensure_dir() -> None:
    """确保 ai_sessions/ 目录存在。"""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# index.json 读写
# ---------------------------------------------------------------------------

def _read_index() -> list[dict[str, Any]]:
    """读取 index.json，失败时返回空列表。"""
    try:
        with _INDEX_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("[SESSION] 读取 index.json 失败：%s", e)
    return []


def _write_index(entries: list[dict[str, Any]]) -> None:
    """写入 index.json（按 created_at 降序排列）。"""
    _ensure_dir()
    # 按 created_at 降序（新建的在最前）
    sorted_entries = sorted(
        entries,
        key=lambda e: e.get("created_at", ""),
        reverse=True,
    )
    try:
        with _INDEX_PATH.open("w", encoding="utf-8") as f:
            json.dump(sorted_entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[SESSION] 写入 index.json 失败：%s", e)


def _update_index_entry(session: ChatSession) -> None:
    """在 index 中更新或插入一条记录。"""
    entries = _read_index()
    entry = session.to_index_entry()
    # 替换已有条目，或追加新条目
    found = False
    for i, e in enumerate(entries):
        if e.get("session_id") == session.session_id:
            entries[i] = entry
            found = True
            break
    if not found:
        entries.append(entry)
    _write_index(entries)


def _remove_index_entry(session_id: str) -> None:
    """从 index 中删除一条记录。"""
    entries = _read_index()
    entries = [e for e in entries if e.get("session_id") != session_id]
    _write_index(entries)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    AI 会话的 CRUD 管理器。

    所有方法均为同步操作，在主线程调用。
    """

    def create_session(self) -> ChatSession:
        """
        创建新会话，写入磁盘，返回 ChatSession 对象。
        session_id 取 uuid4 hex 前 8 位。
        """
        _ensure_dir()
        session_id = uuid.uuid4().hex[:8]
        now = _now_iso()
        session = ChatSession(
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        self.save_session(session)
        logger.info("[SESSION] 创建新会话 %s", session_id)
        return session

    def load_session(self, session_id: str) -> ChatSession | None:
        """
        从磁盘加载会话。
        文件不存在或解析失败时返回 None。
        """
        path = _session_path(session_id)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            session = ChatSession.from_dict(data)
            logger.debug("[SESSION] 加载会话 %s（%d 条消息）",
                         session_id, len(session.messages))
            return session
        except FileNotFoundError:
            logger.warning("[SESSION] 会话文件不存在：%s", path)
            return None
        except Exception as e:
            logger.error("[SESSION] 加载会话 %s 失败：%s", session_id, e)
            return None

    def save_session(self, session: ChatSession) -> None:
        """
        将会话写入磁盘，同时更新 index.json。
        每次 AI 回复完成后调用（实时持久化）。
        """
        _ensure_dir()
        session.updated_at = _now_iso()
        path = _session_path(session.session_id)
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("[SESSION] 写入会话 %s 失败：%s", session.session_id, e)
            return
        _update_index_entry(session)
        logger.debug("[SESSION] 已保存会话 %s", session.session_id)

    def delete_session(self, session_id: str) -> None:
        """删除会话文件和 index 条目。"""
        path = _session_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            logger.error("[SESSION] 删除会话文件 %s 失败：%s", session_id, e)
        _remove_index_entry(session_id)
        logger.info("[SESSION] 已删除会话 %s", session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        返回所有会话的轻量元数据列表（读 index.json，不加载 messages）。
        按 created_at 降序（新建的在最前）。
        """
        return _read_index()

    def rename_session(self, session_id: str, name: str) -> None:
        """
        重命名会话：更新 session 文件和 index。
        """
        session = self.load_session(session_id)
        if session is None:
            logger.warning("[SESSION] 重命名失败：会话 %s 不存在", session_id)
            return
        session.name = name.strip() or "\u65b0\u5bf9\u8bdd"
        self.save_session(session)
        logger.info("[SESSION] 会话 %s 重命名为 %r", session_id, session.name)

    def set_workspace(self, session_id: str, workspace: str | None) -> None:
        """设置会话工作空间路径。"""
        session = self.load_session(session_id)
        if session is None:
            return
        session.workspace = workspace
        self.save_session(session)

    def get_or_create_default(self) -> ChatSession:
        """
        返回最近一个会话；若无任何会话则自动创建一个。
        用于首次启动时初始化。
        """
        entries = self.list_sessions()
        if entries:
            # 取 created_at 最新的（index 已按降序排列）
            session = self.load_session(entries[0]["session_id"])
            if session is not None:
                return session
        return self.create_session()
