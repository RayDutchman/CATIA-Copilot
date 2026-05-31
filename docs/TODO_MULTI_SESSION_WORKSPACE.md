# TODO: 多 Session + 工作空间限制 实施规划

> 状态：待办  
> 来源：2026-05-30 规划讨论  
> 相关文件：`catia_copilot/ai/`、`catia_copilot/ui/ai_chat_panel.py`

---

## 一、数据结构

### 新文件：`catia_copilot/ai/session.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


@dataclass
class ChatSession:

    # ── 1. 元数据 ────────────────────────────────────────────────
    session_id:  str                 # uuid hex 前 8 位，由 SessionManager 生成
    user_id:     str = "local"       # 预留，当前固定值
    name:        str = "新对话"       # 显示在侧边栏的会话名
    model:       str = ""            # 空字符串 = 使用全局默认模型
    workspace:   str | None = None   # 工作空间目录绝对路径，None = 不限制
    created_at:  str = ""            # ISO 8601 时间戳（UTC）
    updated_at:  str = ""            # ISO 8601 时间戳，每次写入时更新

    # ── 2. 模型参数（None = 跟随全局 ai_config.json）────────────
    config: dict = field(default_factory=lambda: {
        "temperature":          None,   # None = 使用全局值；0–2
        "top_p":                None,   # 预留，暂不传给 API；0–1
        "max_tokens":           None,   # 预留，暂不传给 API
        "max_context_messages": 100,    # 发给 LLM 的最近消息数上限（system 不计入）
    })

    # ── 3. 对话历史（OpenAI messages 格式，直接发给 LLM）────────
    messages: list[dict] = field(default_factory=list)

    # ── 4. 运行时状态（不持久化，每次加载时重置为空）────────────
    session_state: dict = field(default_factory=lambda: {
        "current_step":       None,  # 当前工作流阶段
        "extracted_entities": {},    # 对话中提取的变量
        "temp_data":          {},    # 临时工作内存
    })

    # ── 5. Session 级记忆（持久化，本 session 特有）─────────────
    #   与全局 memory.md 互补：
    #   - memory.md：AI 主动维护的跨 session 长期知识
    #   - session.memory：本 session 内积累的上下文记忆
    memory: dict = field(default_factory=lambda: {
        "user_preferences":  {},   # 本 session 的偏好（语言、风格等）
        "hard_constraints":  [],   # 本 session 的强制规则
        "historical_faults": [],   # 本 session 的错误记录
    })

    # ── 序列化 ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
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
    def from_dict(cls, d: dict) -> "ChatSession":
        """从 JSON dict 反序列化，session_state 重置为空。"""
        session = cls(session_id=d["session_id"])
        session.user_id    = d.get("user_id", "local")
        session.name       = d.get("name", "新对话")
        session.model      = d.get("model", "")
        session.workspace  = d.get("workspace")
        session.created_at = d.get("created_at", "")
        session.updated_at = d.get("updated_at", "")
        session.config     = {**session.config, **d.get("config", {})}
        session.messages   = d.get("messages", [])
        session.memory     = {**session.memory, **d.get("memory", {})}
        # session_state 保持默认（不从文件恢复）
        return session
```

---

## 二、持久化

### 新文件：`catia_copilot/ai/session_manager.py`

**目录结构**（加入 `.gitignore`）：

```
ai_sessions/                    ← 项目根目录
├── index.json                  ← 轻量索引，只存元数据，不存 messages
│   格式：[{session_id, name, created_at, last_active}, ...]
│   按 last_active 降序排列（最近活跃的在最前）
├── session_a1b2c3d4.json       ← 完整 session 数据
└── session_e5f6g7h8.json
```

**主要接口**：

```python
class SessionManager:
    def create_session(self) -> ChatSession
    def load_session(self, session_id: str) -> ChatSession
    def save_session(self, session: ChatSession) -> None   # 实时调用
    def delete_session(self, session_id: str) -> None
    def list_sessions(self) -> list[dict]                  # 读 index.json，轻量
    def rename_session(self, session_id: str, name: str) -> None
```

**实时保存触发点**：
- 用户发送消息后（追加 user message）
- AI 回复完成后（追加 assistant message）
- per-session 配置变更后
- 工作空间变更后

**会话名自动生成**：新建时默认"新对话"，第一条 user message 发送后，取前 15 个字符作为会话名（异步，不阻塞发送流程）。

---

## 三、全局记忆（memory.md）

**文件路径**：项目根目录 `memory.md`（加入 `.gitignore`）

**机制**：
- 每次 LLM 请求时，若 `memory.md` 存在且非空，自动注入 system prompt（最多 8000 字符，参考 Standard-Agent-Server）
- AI 可通过 `tool_update_memory` 工具主动更新（需在 `tools.py` 新增此工具）

**与 session.memory 的分工**：

| | `memory.md` | `session.memory` |
|---|---|---|
| 范围 | 跨所有 session | 仅本 session |
| 更新者 | AI 主动调用工具 | AI 或系统自动记录 |
| 典型内容 | 用户公司的零件编号规范、常用目录路径 | 本次对话中用户说过的特定要求 |
| 持久化 | 是 | 是（存在 session 文件里） |

---

## 四、UI 结构

### 布局

```
AIChatPanel (QWidget)
└── QHBoxLayout（无间距）
    ├── _sidebar (QWidget, 宽 220px, 默认隐藏)
    │   ├── 顶部栏 (固定高度 40px)
    │   │   ├── QLabel("聊天")
    │   │   └── 搜索按钮（预留图标，暂不实现功能）
    │   ├── _session_list (QListWidget, 自定义 delegate, stretch=1)
    │   │   └── 每项：气泡图标 + 会话名 + "..." 菜单按钮
    │   │       当前活跃会话：蓝色文字高亮
    │   └── 底部 (固定高度 48px)
    │       └── QPushButton("⊕ 新对话")
    │
    └── _chat_area (QWidget, stretch=1)
        ├── _build_toolbar()
        │   └── 最左侧加 ≡ 按钮（切换侧边栏）
        ├── _build_chat_area()        ← 不变
        └── _build_input_area()
            └── 输入框下方加 ⚙ 按钮（触发 per-session 设置）
```

### 侧边栏折叠动画

```python
# 使用 QPropertyAnimation 对 maximumWidth 做动画
anim = QPropertyAnimation(self._sidebar, b"maximumWidth")
anim.setDuration(150)  # ms
anim.setStartValue(0 if opening else 220)
anim.setEndValue(220 if opening else 0)
anim.start()
```

默认收起（`maximumWidth = 0`，`setVisible(False)`）。

### 会话列表项（自定义 delegate）

每项显示：
- 左侧：对话气泡图标（`💬` 或 SVG）
- 中间：会话名（超长截断 + `...`）
- 右侧：`...` 按钮（鼠标悬停时显示），点击弹出 `QMenu`

`QMenu` 内容：
- 重命名
- 设置工作空间
- 删除（需二次确认）

---

## 五、per-session 设置面板

### 触发位置

输入框下方的 `⚙` 按钮，点击弹出 `SessionConfigDialog`（模态对话框）。

### 内容（参考 Chatbox "特定模型设置"）

| 控件 | 字段 | 范围 | 默认 |
|------|------|------|------|
| `QSlider` + `QSpinBox` | `max_context_messages` | 1–200 | 100 |
| `QSlider` + `QLabel` | `temperature` | 0–2，步长 0.1 | 未设置（None） |
| `QSlider` + `QLabel` | `top_p` | 0–1，步长 0.05 | 未设置（None）（预留） |
| `QSpinBox` | `max_tokens` | 1–32768 | 未设置（None）（预留） |
| `QComboBox` | `model` | 从 ai_config.json 读取 | 使用全局默认 |
| `QLineEdit` + 浏览按钮 | `workspace` | 目录路径 | 空（不限制） |
| 右上角"重置"按钮 | — | — | 恢复所有字段为 None |

**"未设置"的实现**：`QSlider` 最左端为特殊位置，对应 `None`，旁边显示"未设置"文字而不是数值。`top_p` 和 `max_tokens` 显示但暂不传给 API。

---

## 六、工作空间限制执行

### 执行位置

`AIChatPanel._execute_tool_in_main_thread` 调用工具前检查。

### 检查逻辑

```python
def _check_workspace(args: dict, workspace: str | None) -> str | None:
    """返回错误信息字符串，None 表示通过检查。"""
    if workspace is None:
        return None
    ws = Path(workspace).resolve()
    path_keys = ("file_path", "file_paths", "target_path",
                 "template_path", "output_folder")
    for key in path_keys:
        val = args.get(key)
        paths = [val] if isinstance(val, str) else (val if isinstance(val, list) else [])
        for p in paths:
            if p is None:
                continue
            try:
                if not Path(p).resolve().is_relative_to(ws):
                    return f"路径超出工作空间限制：\n{p}\n（工作空间：{workspace}）"
            except ValueError:
                return f"路径无效：{p}"
    return None
```

**豁免工具**（不涉及路径参数，跳过检查）：
- `check_catia_connection`
- `diagnose_catia_connection`
- `refresh_drawing`
- `collect_mass_props`（`file_path=None` 时使用活动文档，豁免）

---

## 七、新增工具：`tool_update_memory`

在 `catia_copilot/ai/tools.py` 新增，对应 Standard-Agent-Server 的 `update_global_memory`。

```python
def tool_update_memory(
    content: str,
    mode: str = "append",   # "append" | "prepend" | "replace"
    **_kwargs,
) -> str:
    """
    更新全局长期记忆文件（memory.md）。
    AI 在发现值得长期记住的信息时主动调用。
    """
```

**Schema**：

```json
{
  "name": "update_memory",
  "description": "更新全局长期记忆文件（memory.md）。当发现值得跨会话记住的信息时调用，例如用户的零件编号规范、常用目录路径、偏好设置等。",
  "parameters": {
    "content": {"type": "string", "description": "要写入的内容"},
    "mode": {
      "type": "string",
      "enum": ["append", "prepend", "replace"],
      "description": "append=追加到末尾，prepend=插入到开头，replace=完全替换"
    }
  }
}
```

---

## 八、新增/修改文件清单

| 文件 | 操作 | 主要内容 |
|------|------|----------|
| `catia_copilot/ai/session.py` | **新建** | `ChatSession` dataclass + 序列化/反序列化 |
| `catia_copilot/ai/session_manager.py` | **新建** | CRUD、持久化、index 管理、会话名自动生成 |
| `catia_copilot/ui/session_config_dialog.py` | **新建** | per-session 设置面板（滑块 + 工作空间选择） |
| `catia_copilot/ui/ai_chat_panel.py` | **修改** | 加侧边栏、≡ 按钮、⚙ 按钮、session 切换逻辑、workspace 检查 |
| `catia_copilot/ai/tools.py` | **修改** | 新增 `tool_update_memory` |
| `catia_copilot/constants.py` | **修改** | 新增 `AI_SESSIONS_DIR`、`AI_MEMORY_PATH`、`AI_MAX_CONTEXT_MESSAGES` |
| `.gitignore` | **修改** | 新增 `ai_sessions/`、`memory.md` |

---

## 九、实施顺序建议

1. `session.py` — 数据结构（无依赖，可独立测试）
2. `session_manager.py` — 持久化层（依赖 session.py）
3. `tools.py` — 新增 `tool_update_memory`（独立）
4. `session_config_dialog.py` — per-session 设置 UI（独立）
5. `ai_chat_panel.py` — 集成所有组件（最后，依赖以上全部）
6. `.gitignore` + `constants.py` — 配套修改

---

## 十、尚未决定的细节（实施时确认）

- 侧边栏会话列表的排序方式：按 `last_active` 降序（最近活跃在最前，推荐）还是按 `created_at`？
- 会话名自动生成的触发时机：第一条 user message 发送后立即改名，还是等 AI 第一次回复后？
- `memory.md` 注入 system prompt 的位置：在 `AgentWorker._agent_loop` 里，还是在 `AIChatPanel._send_message` 里构建 messages 时？
