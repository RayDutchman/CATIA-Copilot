"""
AI 聊天面板 - CATIA Copilot 主窗口新 Tab 页。

布局：
  左侧边栏（可折叠，宽 220px，默认收起）：会话列表 + 新建按钮
  右主区：顶部工具栏 + 聊天消息区 + 底部输入区

线程安全：
  AgentWorker 在后台线程运行，通过 tool_call_requested Signal 请求主线程执行工具。
  主线程执行完毕后调用 worker.receive_tool_result() 传回结果。

会话管理：
  每个会话对应一个 ChatSession，实时持久化到 ai_sessions/ 目录。
  工作空间限制在 _execute_tool_in_main_thread 中执行。
  全局记忆（memory.md）在 _send_message 中构建 messages 时注入。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QEvent, Slot, QEasingCurve
from PySide6.QtGui import QTextCursor, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QTextEdit, QTextBrowser,
    QFrame, QSizePolicy, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QDialogButtonBox, QToolButton, QListWidget, QListWidgetItem,
    QMenu, QInputDialog, QMessageBox, QSplitter, QSplitterHandle,
)

from catia_copilot.ui.theme_manager import theme_signal, theme_manager
from catia_copilot.ui.ui_colors import get_chat_colors
from catia_copilot.ai import config as ai_config
from catia_copilot.ai.agent import AgentWorker
from catia_copilot.ai.tools import tools_map
from catia_copilot.ai.session import ChatSession
from catia_copilot.ai.session_manager import SessionManager

logger = logging.getLogger(__name__)

# 项目根目录（用于读取 memory.md）
_BASE_DIR = Path(__file__).parent.parent.parent
_MEMORY_PATH = _BASE_DIR / "memory.md"
# 记忆注入上限（字符数）
_MEMORY_MAX_CHARS = 8000


def chat_colors():
    """返回当前主题的 AI 聊天面板颜色集（ChatColors）。"""
    return get_chat_colors(theme_manager.current_mode())


# ---------------------------------------------------------------------------
# 消息 Widget：用户消息
# ---------------------------------------------------------------------------

class UserMessageWidget(QFrame):
    """用户消息气泡，右对齐。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._build_ui()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(40, 4, 8, 4)

        self._label = QLabel(self._text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addStretch()
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignRight)
        self._apply_style()

    def _apply_style(self):
        bg, fg = chat_colors().user_bg, chat_colors().user_fg
        self._label.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; "
            f"border-radius: 8px; padding: 8px 12px; font-size: 13px; }}"
        )

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 消息 Widget：AI 回复
# ---------------------------------------------------------------------------

class AIMessageWidget(QFrame):
    """AI 回复消息，左对齐，支持流式追加和 Markdown 渲染。"""

    # 流式阶段每累积多少个 token 重新渲染一次 Markdown
    _RENDER_INTERVAL = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._token_count = 0
        self._build_ui()
        # 初始隐藏：等第一个 token 到来再显示，避免空 widget 占位产生空白
        self.hide()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 40, 4)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setReadOnly(True)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._browser.document().contentsChanged.connect(self._adjust_height)
        layout.addWidget(self._browser)
        self._apply_style()

    def _adjust_height(self):
        doc_height = int(self._browser.document().size().height())
        self._browser.setFixedHeight(max(doc_height + 8, 40))

    def append_token(self, token: str):
        """流式追加 token。

        第一个 token 到来时显示 widget（之前隐藏以避免空白占位）。
        每累积 _RENDER_INTERVAL 个 token 用 setMarkdown 重新渲染一次，
        其余时间直接 insertPlainText 追加，平衡渲染质量与性能。
        """
        if not self._buffer:
            self.show()

        self._buffer += token
        self._token_count += 1

        if self._token_count % self._RENDER_INTERVAL == 0:
            self._browser.setMarkdown(self._buffer)
            cursor = self._browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._browser.setTextCursor(cursor)
        else:
            cursor = self._browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._browser.setTextCursor(cursor)
            self._browser.insertPlainText(token)

    def set_final_text(self, text: str):
        """流式结束后（或工具调用前），用 Markdown 重新渲染完整内容。"""
        self._buffer = text
        self.show()
        self._browser.setMarkdown(text)
        self._adjust_height()

    def _apply_style(self):
        bg, fg = chat_colors().ai_bg, chat_colors().ai_fg
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background-color: {bg}; color: {fg}; "
            f"border-radius: 8px; padding: 6px 10px; "
            f"font-size: 13px; border: none; }}"
        )

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 消息 Widget：工具调用卡片
# ---------------------------------------------------------------------------

class ToolCallWidget(QFrame):
    """工具调用卡片，可折叠展开。"""

    def __init__(self, tool_name: str, args_str: str, parent=None):
        super().__init__(parent)
        self._tool_name = tool_name
        self._args_str = args_str
        self._expanded = False
        self._build_ui()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("▶")
        self._toggle_btn.setFixedSize(16, 16)
        self._toggle_btn.clicked.connect(self._toggle)

        try:
            args_dict = json.loads(self._args_str)
            args_summary = ", ".join(
                f"{k}={repr(v)[:20]}" for k, v in list(args_dict.items())[:3]
            )
        except Exception:
            args_summary = self._args_str[:60]

        self._title_label = QLabel(f"🔧 {self._tool_name}({args_summary})")
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_label.mousePressEvent = lambda _: self._toggle()

        header.addWidget(self._toggle_btn)
        header.addWidget(self._title_label, 1)
        outer.addLayout(header)

        self._content_widget = QWidget()
        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(20, 2, 0, 2)
        content_layout.setSpacing(2)

        self._progress_label = QLabel()
        self._progress_label.setWordWrap(True)
        content_layout.addWidget(self._progress_label)

        self._result_browser = QTextBrowser()
        self._result_browser.setReadOnly(True)
        self._result_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._result_browser.setMaximumHeight(200)
        content_layout.addWidget(self._result_browser)

        self._content_widget.setVisible(False)
        outer.addWidget(self._content_widget)
        self._apply_style()

    def _toggle(self):
        self._expanded = not self._expanded
        self._content_widget.setVisible(self._expanded)
        self._toggle_btn.setText("▼" if self._expanded else "▶")

    def add_progress(self, msg: str):
        current = self._progress_label.text()
        self._progress_label.setText((current + "\n" + msg).strip() if current else msg)

    def set_result(self, result: str):
        try:
            parsed = json.loads(result)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            formatted = result
        self._result_browser.setPlainText(formatted)
        if not self._expanded:
            self._toggle()

    def _apply_style(self):
        bg = chat_colors().tool_bg
        fg = chat_colors().tool_fg
        border = chat_colors().tool_border
        prog_fg = chat_colors().progress_fg
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 6px; }}"
        )
        self._title_label.setStyleSheet(
            f"QLabel {{ color: {fg}; font-size: 12px; font-weight: bold; "
            f"background: transparent; border: none; }}"
        )
        self._progress_label.setStyleSheet(
            f"QLabel {{ color: {prog_fg}; font-size: 11px; "
            f"background: transparent; border: none; }}"
        )
        self._result_browser.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {fg}; "
            f"font-size: 11px; font-family: monospace; border: none; }}"
        )

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 滚动区域（自动滚底）
# ---------------------------------------------------------------------------

class ChatScrollArea(QScrollArea):
    """聊天消息滚动区域，布局变化时自动滚到底部。"""

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().maximum()
            )
        return super().event(event)


# ---------------------------------------------------------------------------
# AI 设置对话框
# ---------------------------------------------------------------------------

class AISettingsDialog(QDialog):
    """
    AI 配置对话框。

    包含：API Base URL、API Key、模型选择（刷新模型列表 + 测试连接）、运行时参数。
    刷新模型列表时调用 fetch_models_from_api 并将结果写入 ai_config.json。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 助手设置")
        self.setMinimumWidth(480)
        self._cfg = ai_config.load()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        cfg_path = ai_config.get_config_path()
        hint = QLabel(f"配置文件：{cfg_path}")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        providers = self._cfg.get("providers", {})
        first_provider = next(iter(providers.values()), {})

        self._api_base = QLineEdit(first_provider.get("api_base", "https://api.openai.com"))
        self._api_base.setPlaceholderText("https://api.openai.com")
        form.addRow("API Base URL:", self._api_base)

        self._api_key = QLineEdit(first_provider.get("api_key", ""))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-...")
        form.addRow("API Key:", self._api_key)

        model_row = QHBoxLayout()
        model_row.setSpacing(4)

        current_models = [m["id"] for m in first_provider.get("models", [])]
        default_id = self._cfg.get("default_model", "")

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(current_models)
        idx = self._model_combo.findText(default_id)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentText(default_id)
        model_row.addWidget(self._model_combo, 1)

        self._fetch_btn = QPushButton("刷新模型列表")
        self._fetch_btn.setToolTip("从 API 拉取模型列表并更新下拉选项")
        self._fetch_btn.clicked.connect(self._fetch_models)
        model_row.addWidget(self._fetch_btn)

        self._test_btn = QPushButton("测试连接")
        self._test_btn.setToolTip("发送一条测试消息，验证 API 可用")
        self._test_btn.clicked.connect(self._test_connection)
        model_row.addWidget(self._test_btn)

        form.addRow("默认模型：", model_row)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(1)
        self._temperature.setValue(self._cfg.get("temperature", 0.7))
        temp_row = QHBoxLayout()
        temp_row.addWidget(self._temperature)
        temp_row.addWidget(QLabel("  (0=最确定，1=平衡，2=最富创造性；建议 0.5–0.7)"), 1)
        form.addRow("Temperature:", temp_row)

        self._max_rounds = QSpinBox()
        self._max_rounds.setRange(1, 50)
        self._max_rounds.setValue(self._cfg.get("max_tool_rounds", 20))
        rounds_row = QHBoxLayout()
        rounds_row.addWidget(self._max_rounds)
        rounds_row.addWidget(QLabel("  (单次回复中 AI 调用工具的最大次数)"), 1)
        form.addRow("最大工具调用轮数:", rounds_row)

        self._timeout = QSpinBox()
        self._timeout.setRange(10, 600)
        self._timeout.setSuffix(" 秒")
        self._timeout.setValue(self._cfg.get("timeout", 120))
        timeout_row = QHBoxLayout()
        timeout_row.addWidget(self._timeout)
        timeout_row.addWidget(QLabel("  (单次 LLM 请求的最长等待时间)"), 1)
        form.addRow("请求超时:", timeout_row)

        layout.addLayout(form)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 11px;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fetch_models(self):
        api_base = self._api_base.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        if not api_base or not api_key:
            self._set_status("请先填写 API Base URL 和 API Key", error=True)
            return

        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setText("...")
        self._set_status("正在拉取模型列表...")

        from catia_copilot.ai.config import fetch_models_from_api
        models = fetch_models_from_api(api_base, api_key, timeout=15)

        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("刷新模型列表")

        if not models:
            self._set_status("未获取到模型，请检查 API Base URL 和 API Key", error=True)
            return

        model_ids = [m["id"] for m in models]
        self._set_status(f"已获取 {len(models)} 个模型")

        current = self._model_combo.currentText()
        self._model_combo.clear()
        self._model_combo.addItems(model_ids)
        idx = self._model_combo.findText(current)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        elif model_ids:
            self._model_combo.setCurrentIndex(0)

        self._persist_models(api_base, api_key, models)

    def _test_connection(self):
        import urllib.request as _req
        import json as _json
        import time as _time
        api_base = self._api_base.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        model = self._model_combo.currentText().strip()
        if not api_base or not api_key or not model:
            self._set_status("请先填写 API Base URL、API Key 和模型", error=True)
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("...")
        self._set_status("正在测试...")

        url = f"{api_base}/v1/chat/completions"
        body = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 10,
            "stream": False,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        t0 = _time.time()
        try:
            request = _req.Request(url, data=body, headers=headers, method="POST")
            with _req.urlopen(request, timeout=30) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            elapsed = _time.time() - t0
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", "?")
            self._set_status(
                f"测试通过 ✔  耗时 {elapsed:.1f}s，消耗 {tokens} tokens，"
                f"回复：「{reply[:40]}」"
            )
        except Exception as e:
            elapsed = _time.time() - t0
            self._set_status(f"测试失败 ✖  {elapsed:.1f}s：{str(e)[:80]}", error=True)
        finally:
            self._test_btn.setEnabled(True)
            self._test_btn.setText("测试连接")

    def _set_status(self, msg: str, error: bool = False):
        color = "#c0392b" if error else "gray"
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(msg)

    def _persist_models(self, api_base: str, api_key: str, models: list):
        cfg = ai_config.load()
        providers = cfg.get("providers", {})
        if providers:
            provider_key = next(iter(providers))
            provider = providers[provider_key]
        else:
            provider_key = "default"
            provider = {"name": "Default"}
            providers[provider_key] = provider
        provider["api_base"] = api_base
        provider["api_key"] = api_key
        provider["models"] = models
        cfg["providers"] = providers
        if not cfg.get("default_provider"):
            cfg["default_provider"] = provider_key
        try:
            ai_config.save(cfg)
        except Exception as e:
            logger.error("写入模型列表失败：%s", e)

    def _save_and_accept(self):
        api_base = self._api_base.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        default_model = self._model_combo.currentText().strip()

        cfg = ai_config.load()
        providers = cfg.get("providers", {})
        if providers:
            provider_key = next(iter(providers))
            provider = providers[provider_key]
        else:
            provider_key = "default"
            provider = {"name": "Default", "models": []}
            providers[provider_key] = provider
        provider["api_base"] = api_base
        provider["api_key"] = api_key
        cfg["providers"] = providers
        if not cfg.get("default_provider"):
            cfg["default_provider"] = provider_key
        cfg["default_model"]   = default_model
        cfg["temperature"]     = self._temperature.value()
        cfg["max_tool_rounds"] = self._max_rounds.value()
        cfg["timeout"]         = self._timeout.value()
        try:
            ai_config.save(cfg)
        except Exception as e:
            logger.error("保存 AI 配置失败：%s", e)
        self.accept()

    def get_config(self) -> dict:
        return ai_config.load()


# ---------------------------------------------------------------------------
# 自定义 Splitter Handle：中间有折叠/展开箭头按钮
# ---------------------------------------------------------------------------

class _CollapseHandle(QSplitterHandle):
    """
    QSplitter 的自定义 handle。

    宽度 14px，中间位置绘制一个 ◀/▶ 箭头按钮区域（高 40px）。
    点击箭头区域时折叠/展开左侧侧边栏（index=0 的 widget）。
    拖动 handle 其余区域仍可调整宽度。
    """

    _BTN_H = 40   # 箭头按钮区域高度
    _W     = 14   # handle 宽度

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setFixedWidth(self._W)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self._hovered = False

    def _is_collapsed(self) -> bool:
        sp = self.splitter()
        if sp is None:
            return False
        sizes = sp.sizes()
        return len(sizes) > 0 and sizes[0] == 0

    def _arrow_rect(self):
        """返回箭头按钮区域的 QRect（垂直居中）。"""
        from PySide6.QtCore import QRect
        cy = self.height() // 2
        return QRect(0, cy - self._BTN_H // 2, self._W, self._BTN_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        c = chat_colors()
        # handle 整体背景（与侧边栏融合，不突兀）
        painter.fillRect(self.rect(), QColor(c.handle_bg))

        # 箭头按钮区域背景（hover 时高亮）
        btn_rect = self._arrow_rect()
        if self._hovered:
            painter.fillRect(btn_rect, QColor(c.handle_hover))

        # 绘制箭头
        arrow = "◀" if not self._is_collapsed() else "▶"
        painter.setPen(QColor(c.handle_fg))
        painter.setFont(self.font())
        painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, arrow)

    def mousePressEvent(self, event):
        if self._arrow_rect().contains(event.pos()):
            self._toggle_collapse()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        was = self._hovered
        self._hovered = self._arrow_rect().contains(event.pos())
        if was != self._hovered:
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def _toggle_collapse(self):
        sp = self.splitter()
        if sp is None:
            return
        if self._is_collapsed():
            # 展开：恢复到默认宽度 220
            sp.setSizes([220, sp.width() - 220 - self._W])
        else:
            # 折叠：左侧宽度设为 0
            sp.setSizes([0, sp.width()])
        self.update()


class AISplitter(QSplitter):
    """带自定义 handle 的 QSplitter。"""

    def createHandle(self) -> QSplitterHandle:
        return _CollapseHandle(Qt.Orientation.Horizontal, self)


# ---------------------------------------------------------------------------
# 侧边栏会话列表
# ---------------------------------------------------------------------------

class SessionSidebar(QWidget):
    """
    可折叠侧边栏，显示会话列表。

    宽度由 AISplitter 控制，不再自行管理动画。
    """

    # 侧边栏默认宽度（splitter 初始化时使用）
    EXPANDED_WIDTH = 220

    def __init__(self, session_manager: SessionManager, parent=None):
        super().__init__(parent)
        self._sm = session_manager
        self._current_session_id: str | None = None
        self._build_ui()
        theme_signal.theme_changed.connect(self._apply_style)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部标题栏
        header = QWidget()
        header.setFixedHeight(40)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(4)
        title_lbl = QLabel("会话列表")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header_layout.addWidget(title_lbl, 1)
        layout.addWidget(header)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        layout.addWidget(line)

        # 会话列表
        self._list = QListWidget()
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setSpacing(1)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list, 1)

        # 底部"新对话"按钮
        bottom = QWidget()
        bottom.setFixedHeight(48)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(8, 4, 8, 4)
        self._new_btn = QPushButton("新对话")
        self._new_btn.setFixedHeight(32)
        bottom_layout.addWidget(self._new_btn)
        layout.addWidget(bottom)

        self._apply_style()

    def _apply_style(self):
        c = chat_colors()
        # 用 QPalette 设置背景色，比 QSS 类名选择器在 QSplitter 内更可靠
        from PySide6.QtGui import QPalette
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c.sidebar_bg))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._list.setStyleSheet(
            f"QListWidget {{ background-color: {c.sidebar_bg}; color: {c.sidebar_fg}; border: none; }}"
            f"QListWidget::item {{ padding: 6px 10px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {c.sidebar_sel}; }}"
            f"QListWidget::item:hover:!selected {{ background-color: {c.sidebar_hover}; }}"
            f"QListWidget QScrollBar:vertical {{ width: 4px; border: none; background: transparent; }}"
            f"QListWidget QScrollBar::handle:vertical {{ background: {c.sidebar_fg}; "
            f"border-radius: 2px; min-height: 20px; }}"
            f"QListWidget QScrollBar::add-line:vertical, "
            f"QListWidget QScrollBar::sub-line:vertical {{ height: 0px; }}"
            f"QListWidget QScrollBar:horizontal {{ height: 0px; }}"
        )
        # 顶部标题栏和底部按钮区背景跟随侧边栏
        self.setStyleSheet(
            f"SessionSidebar, SessionSidebar > QWidget {{ background-color: {c.sidebar_bg}; }}"
            f"SessionSidebar QPushButton {{ background-color: {c.sidebar_bg}; "
            f"color: {c.sidebar_fg}; border: 1px solid {c.sidebar_hover}; border-radius: 4px; }}"
            f"SessionSidebar QPushButton:hover {{ background-color: {c.sidebar_hover}; }}"
        )

    def refresh(self, current_session_id: str | None = None):
        """重新加载会话列表。"""
        if current_session_id is not None:
            self._current_session_id = current_session_id
        entries = self._sm.list_sessions()
        self._list.clear()
        for entry in entries:
            sid = entry.get("session_id", "")
            name = entry.get("name", "新对话")
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            item.setToolTip(
                f"ID: {sid}\n"
                f"创建：{entry.get('created_at', '')}\n"
                f"工作空间：{entry.get('workspace') or '不限制'}"
            )
            self._list.addItem(item)
            if sid == self._current_session_id:
                self._list.setCurrentItem(item)

    def set_current(self, session_id: str | None):
        """高亮当前会话；传 None 时取消所有高亮（草稿状态）。"""
        self._current_session_id = session_id
        if session_id is None:
            self._list.clearSelection()
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == session_id:
                self._list.setCurrentItem(item)
                break

    def _on_item_clicked(self, item: QListWidgetItem):
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid and sid != self._current_session_id:
            # 通知父级切换会话
            panel = self._find_panel()
            if panel:
                panel._switch_session(sid)

    def _on_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        is_current = (sid == self._current_session_id)
        menu = QMenu(self)
        rename_act = menu.addAction("重命名")
        cfg_act = menu.addAction("会话设置…")
        ws_act = menu.addAction("设置工作空间")
        # "清空消息"只对当前活跃会话显示
        clear_act = None
        if is_current:
            menu.addSeparator()
            clear_act = menu.addAction("清空消息记录")
        menu.addSeparator()
        del_act = menu.addAction("删除")
        action = menu.exec(self._list.mapToGlobal(pos))
        if action == rename_act:
            self._rename_session(sid, item)
        elif action == cfg_act:
            self._open_session_config_for(sid)
        elif action == ws_act:
            self._set_workspace(sid)
        elif clear_act and action == clear_act:
            panel = self._find_panel()
            if panel:
                panel._clear_chat()
        elif action == del_act:
            self._delete_session(sid)

    def _rename_session(self, session_id: str, item: QListWidgetItem):
        old_name = item.text()
        new_name, ok = QInputDialog.getText(
            self, "重命名会话", "新名称：", text=old_name
        )
        if ok and new_name.strip():
            self._sm.rename_session(session_id, new_name.strip())
            item.setText(new_name.strip())
            # 如果是当前会话，通知面板更新标题
            panel = self._find_panel()
            if panel and session_id == self._current_session_id:
                panel._update_session_title()

    def _open_session_config_for(self, session_id: str):
        """从侧边栏右键菜单打开指定会话的设置对话框。"""
        panel = self._find_panel()
        if panel is None:
            return
        # 如果是当前会话，直接复用面板的方法
        if session_id == self._current_session_id:
            panel._open_session_config()
        else:
            # 非当前会话：加载后弹对话框，保存后不切换
            session = self._sm.load_session(session_id)
            if session is None:
                return
            from catia_copilot.ui.session_config_dialog import SessionConfigDialog
            dlg = SessionConfigDialog(session, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._sm.save_session(session)
                self.refresh()

    def _set_workspace(self, session_id: str):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择工作空间目录")
        if folder:
            self._sm.set_workspace(session_id, folder)
            self.refresh()

    def _delete_session(self, session_id: str):
        ret = QMessageBox.question(
            self, "删除会话",
            "确定要删除这个会话吗？此操作不可撤销。",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        panel = self._find_panel()
        is_current = (session_id == self._current_session_id)

        # 若删除的是当前会话，先把 panel._current_session 置 None，
        # 防止后续 _switch_session 里的 save_session 把它复活写回 index
        if panel and is_current:
            panel._current_session = None

        self._sm.delete_session(session_id)
        self.refresh()

        if panel and is_current:
            entries = self._sm.list_sessions()
            if entries:
                panel._switch_session(entries[0]["session_id"])
            else:
                panel._new_session()

    def _find_panel(self) -> "AIChatPanel | None":
        """向上查找 AIChatPanel 父级。"""
        w = self.parent()
        while w is not None:
            if isinstance(w, AIChatPanel):
                return w
            w = w.parent()
        return None


# ---------------------------------------------------------------------------
# AIChatPanel 主面板
# ---------------------------------------------------------------------------

# 豁免工作空间检查的工具（不涉及文件路径，或使用活动文档）
_WORKSPACE_EXEMPT_TOOLS = frozenset({
    "check_catia_connection",
    "diagnose_catia_connection",
    "refresh_drawing",
    "get_open_documents",
    "save_catia_document",
    "update_memory",
})

# 涉及路径的参数名
_PATH_KEYS = ("file_path", "file_paths", "target_path",
              "template_path", "output_folder", "drawing_path",
              "part_path")


def _check_workspace(args: dict, workspace: str | None) -> str | None:
    """
    检查工具参数中的路径是否在工作空间内。
    返回错误信息字符串，None 表示通过检查。
    """
    if workspace is None:
        return None
    from pathlib import Path as _Path
    ws = _Path(workspace).resolve()
    for key in _PATH_KEYS:
        val = args.get(key)
        paths = [val] if isinstance(val, str) else (val if isinstance(val, list) else [])
        for p in paths:
            if p is None:
                continue
            try:
                if not _Path(p).resolve().is_relative_to(ws):
                    return (
                        f"路径超出工作空间限制：\n{p}\n"
                        f"（工作空间：{workspace}）"
                    )
            except ValueError:
                return f"路径无效：{p}"
    return None


class AIChatPanel(QWidget):
    """
    AI 聊天面板，作为主窗口的新 Tab 页嵌入。

    职责：
      - 管理多个 ChatSession（通过 SessionManager）
      - 侧边栏显示会话列表，支持切换/新建/重命名/删除
      - 创建/停止 AgentWorker
      - 在主线程执行工具调用（COM 线程安全）
      - 渲染消息气泡和工具调用卡片
      - 工作空间限制检查
      - 全局记忆（memory.md）注入
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sm = SessionManager()
        # None = 草稿状态（用户尚未发送任何消息，不写磁盘）
        self._current_session: ChatSession | None = None
        # 启动时恢复上次活跃的会话（updated_at 最新），若无则保持草稿状态
        entries = self._sm.list_sessions()
        if entries:
            # 按 updated_at 降序取最近活跃的会话
            latest = max(entries, key=lambda e: e.get("updated_at", ""))
            self._current_session = self._sm.load_session(latest["session_id"])
        self._worker: AgentWorker | None = None
        self._current_ai_widget: AIMessageWidget | None = None
        self._current_tool_widget: ToolCallWidget | None = None
        self._build_ui()
        # 启动时重建历史消息 widget
        if self._current_session is not None:
            self._rebuild_chat_widgets()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # QSplitter：左侧边栏 + 右主区，handle 上有折叠/展开箭头
        self._splitter = AISplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.setHandleWidth(14)

        # 左侧边栏
        self._sidebar = SessionSidebar(self._sm, self._splitter)
        sid = self._current_session.session_id if self._current_session else None
        self._sidebar.refresh(sid)
        self._sidebar._new_btn.clicked.connect(self._new_session)
        self._splitter.addWidget(self._sidebar)

        # 右主区
        chat_area = QWidget()
        chat_layout = QVBoxLayout(chat_area)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        chat_layout.addWidget(self._build_toolbar())
        chat_layout.addWidget(self._build_chat_area(), 1)
        chat_layout.addWidget(self._build_input_area())
        self._splitter.addWidget(chat_area)

        # 初始状态：侧边栏收起（宽度 0），主区占满
        self._splitter.setSizes([0, 9999])
        # 拉伸因子：主区随窗口缩放，侧边栏保持用户拖动后的宽度
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        root.addWidget(self._splitter)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        # 会话标题：固定宽度 120px，elidedText 在 _update_session_title 里计算
        self._session_title = QLabel()
        self._session_title.setFixedWidth(120)
        self._session_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._session_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._session_title.mousePressEvent = lambda _: self._rename_current_session()
        layout.addWidget(self._session_title)

        # 图标按钮公共样式
        _icon_style = (
            "QPushButton { border: none; background: transparent; "
            "font-size: 15px; padding: 0px; }"
            "QPushButton:hover { background: rgba(128,128,128,40); border-radius: 4px; }"
        )

        # 铅笔图标（重命名）
        rename_btn = QPushButton("✏")
        rename_btn.setFixedSize(28, 28)
        rename_btn.setToolTip("重命名会话")
        rename_btn.setStyleSheet(_icon_style)
        rename_btn.clicked.connect(self._rename_current_session)
        layout.addWidget(rename_btn)

        # ⚙ 会话设置按钮
        self._session_cfg_btn = QPushButton("⚙")
        self._session_cfg_btn.setFixedSize(28, 28)
        self._session_cfg_btn.setToolTip("会话设置")
        self._session_cfg_btn.setStyleSheet(_icon_style)
        self._session_cfg_btn.clicked.connect(self._open_session_config)
        layout.addWidget(self._session_cfg_btn)

        layout.addStretch()

        # 模型 ComboBox：可编辑模式（setEditable(True)）下 Qt 会独立计算下拉列表宽度，
        # 不受控件宽度限制，模型名不会被截断；只读模式在 Windows 下有此 bug。
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._model_combo.setMinimumContentsLength(16)
        self._model_combo.setMaximumWidth(200)
        self._refresh_model_combo()
        layout.addWidget(self._model_combo)

        # ⚙ 全局设置：用 QToolButton 避免继承主窗口 Tab QSS
        self._settings_btn = QToolButton()
        self._settings_btn.setText("⚙ 设置")
        self._settings_btn.setToolTip("全局 AI 设置（API Key、默认模型、Temperature 等）")
        self._settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_btn)

        # 初始化会话标题
        self._update_session_title()

        return bar

    def _build_chat_area(self) -> QWidget:
        self._scroll = ChatScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(8, 8, 8, 8)
        self._chat_layout.setSpacing(6)
        self._chat_layout.addStretch()

        self._scroll.setWidget(self._chat_container)
        return self._scroll

    def _build_input_area(self) -> QWidget:
        area = QWidget()
        layout = QHBoxLayout(area)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        self._input_box = QTextEdit()
        self._input_box.setFixedHeight(72)
        self._input_box.setPlaceholderText("输入消息... (Ctrl+Enter 发送，Enter 换行)")
        self._input_box.installEventFilter(self)
        layout.addWidget(self._input_box, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(60, 60)
        self._send_btn.clicked.connect(self._send_message)
        layout.addWidget(self._send_btn)

        return area

    # ── 会话管理 ──────────────────────────────────────────────────────────────

    def _new_session(self):
        """切换到草稿状态（不立即创建 ChatSession，发送第一条消息时才创建）。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        # 保存当前会话（若有）
        if self._current_session is not None:
            self._sm.save_session(self._current_session)
        # 进入草稿状态
        self._current_session = None
        self._sidebar.set_current(None)
        self._clear_chat_widgets()
        self._update_session_title()
        self._refresh_model_combo_for_session()

    def _switch_session(self, session_id: str):
        """切换到指定会话。"""
        if (self._current_session is not None
                and session_id == self._current_session.session_id):
            return
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        # 保存当前会话（若有）
        if self._current_session is not None:
            self._sm.save_session(self._current_session)
        # 加载目标会话
        session = self._sm.load_session(session_id)
        if session is None:
            return
        self._current_session = session
        self._sidebar.set_current(session_id)
        self._rebuild_chat_widgets()
        self._update_session_title()
        self._refresh_model_combo_for_session()

    def _rename_current_session(self):
        """弹出对话框重命名当前会话（草稿状态下不可用）。"""
        if self._current_session is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名会话", "新名称：",
            text=self._current_session.name
        )
        if ok and new_name.strip():
            self._current_session.name = new_name.strip()
            self._sm.save_session(self._current_session)
            self._update_session_title()
            self._sidebar.refresh(self._current_session.session_id)

    def _update_session_title(self):
        """更新工具栏会话标题（固定宽度 120px，用 elidedText 截断）。"""
        session = self._current_session
        name = session.name if session else "新对话"
        fm = self._session_title.fontMetrics()
        # 留 4px 内边距余量
        elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, 116)
        self._session_title.setText(elided)
        # tooltip：省略时显示完整名 + 工作空间；未省略时只显示工作空间
        self._update_session_title_tooltip(elided != name)

    def _update_session_title_tooltip(self, name_elided: bool = False):
        """更新会话名和 ⚙ 按钮的 tooltip（含工作空间信息）。"""
        session = self._current_session
        name = session.name if session else "新对话"
        ws = session.workspace if session else None
        ws_text = ws if ws else "不限制"

        # 会话名 tooltip：省略时显示完整名，始终附上工作空间
        title_tip_parts = []
        if name_elided:
            title_tip_parts.append(name)
        title_tip_parts.append(f"工作空间：{ws_text}")
        self._session_title.setToolTip("\n".join(title_tip_parts))

        # ⚙ 会话设置按钮 tooltip
        self._session_cfg_btn.setToolTip(
            f"会话设置（模型、上下文长度、工作空间）\n当前工作空间：{ws_text}"
        )

    def _refresh_model_combo_for_session(self):
        """根据当前会话的 model 字段刷新工具栏模型下拉框。"""
        self._refresh_model_combo()
        session = self._current_session
        if session is None:
            return
        session_model = session.model
        if session_model:
            idx = self._model_combo.findText(session_model)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)

    # ── 聊天区 widget 管理 ────────────────────────────────────────────────────

    def _clear_chat_widgets(self):
        """清空聊天区所有消息 widget（保留 stretch）。"""
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._current_ai_widget = None
        self._current_tool_widget = None

    def _rebuild_chat_widgets(self):
        """根据当前会话的 messages 重建聊天区 widget（只重建最近 30 条可见消息）。"""
        self._clear_chat_widgets()
        messages = self._current_session.messages
        # 过滤出 user/assistant 消息（跳过 system/tool）
        visible = [
            m for m in messages
            if m.get("role") in ("user", "assistant")
            and m.get("content")
        ]
        # 只重建最近 30 条，避免大量历史消息导致卡顿
        for msg in visible[-30:]:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                w = UserMessageWidget(content)
                self._insert_widget(w)
            elif role == "assistant":
                w = AIMessageWidget()
                w.set_final_text(content)
                self._insert_widget(w)

    # ── 模型下拉框 ────────────────────────────────────────────────────────────

    def _refresh_model_combo(self):
        cfg = ai_config.load()
        model_ids = ai_config.list_model_ids(cfg)
        default_model = ai_config.get_default_model_id(cfg)
        self._model_combo.clear()
        if model_ids:
            self._model_combo.addItems(model_ids)
        else:
            self._model_combo.addItem(default_model)
        idx = self._model_combo.findText(default_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentText(default_model)

    def _show_status(self, msg: str):
        w = AIMessageWidget()
        w.set_final_text(f"*{msg}*")
        self._insert_widget(w)

    # ── 设置对话框 ────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = AISettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 全局设置变更后，刷新模型列表（以设置里的为准）
            self._refresh_model_combo_for_session()

    def _open_session_config(self):
        from catia_copilot.ui.session_config_dialog import SessionConfigDialog

        # 草稿状态下先创建会话，否则设置无处保存
        if self._current_session is None:
            self._current_session = self._sm.create_session()
            self._sidebar.refresh(self._current_session.session_id)
            self._sidebar.set_current(self._current_session.session_id)
            self._update_session_title()

        def _on_clear():
            self._clear_chat_widgets()
            self._sm.save_session(self._current_session)

        dlg = SessionConfigDialog(self._current_session, self, on_clear=_on_clear)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._sm.save_session(self._current_session)
            self._update_session_title_tooltip()
            self._refresh_model_combo_for_session()

    # ── 事件过滤（Ctrl+Enter 发送）────────────────────────────────────────────

    def eventFilter(self, obj, event: QEvent) -> bool:
        if obj is self._input_box and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if (key_event.key() == Qt.Key.Key_Return
                    and key_event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._send_message()
                return True
        return super().eventFilter(obj, event)

    # ── 消息发送 ──────────────────────────────────────────────────────────────

    def _build_messages_for_llm(self) -> list[dict]:
        """
        构建发给 LLM 的 messages 列表：
          1. system prompt（来自 ai_config.json）
          2. 全局记忆（memory.md，若存在则追加到 system prompt）
          3. 最近 max_context_messages 条对话历史（system 不计入）
        """
        cfg = ai_config.load()
        sys_prompt = cfg.get("system_prompt", "")

        # 注入全局记忆
        if _MEMORY_PATH.exists():
            try:
                memory_text = _MEMORY_PATH.read_text(encoding="utf-8").strip()
                if memory_text:
                    memory_text = memory_text[:_MEMORY_MAX_CHARS]
                    sys_prompt = (
                        sys_prompt + "\n\n---\n## 长期记忆\n" + memory_text
                        if sys_prompt else "## 长期记忆\n" + memory_text
                    )
            except Exception as e:
                logger.warning("读取 memory.md 失败：%s", e)

        # 过滤出非 system 消息
        session = self._current_session
        msgs = session.messages if session else []
        non_system = [m for m in msgs if m.get("role") != "system"]

        # 截断到 max_context_messages
        max_ctx = (session.config.get("max_context_messages") or 100) if session else 100
        if max_ctx > 0:
            non_system = non_system[-max_ctx:]

        result = []
        if sys_prompt:
            result.append({"role": "system", "content": sys_prompt})
        result.extend(non_system)
        return result

    def _send_message(self):
        text = self._input_box.toPlainText().strip()
        if not text:
            return
        if self._worker and self._worker.isRunning():
            return

        self._input_box.clear()

        # 草稿状态：发送第一条消息时才正式创建会话并写磁盘
        if self._current_session is None:
            self._current_session = self._sm.create_session()
            self._sidebar.refresh(self._current_session.session_id)
            self._sidebar.set_current(self._current_session.session_id)
            self._update_session_title()

        # 显示用户消息气泡
        self._add_user_message(text)

        # 追加到会话历史
        self._current_session.messages.append({"role": "user", "content": text})
        # 实时保存（追加 user message 后）
        self._sm.save_session(self._current_session)

        # 构建发给 LLM 的 messages（含 system + memory + 截断历史）
        messages_for_llm = self._build_messages_for_llm()

        # 构建运行时 config
        cfg = ai_config.load()
        selected_model = self._model_combo.currentText().strip()
        if selected_model:
            cfg["default_model"] = selected_model
        # per-session temperature 覆盖全局
        if self._current_session is not None:
            session_temp = self._current_session.config.get("temperature")
            if session_temp is not None:
                cfg["temperature"] = session_temp

        # 创建 AI 消息占位 widget
        self._current_ai_widget = AIMessageWidget()
        self._insert_widget(self._current_ai_widget)

        # 启动 AgentWorker
        self._worker = AgentWorker(messages_for_llm, cfg)
        self._worker.token_received.connect(self._on_token)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_progress.connect(self._on_tool_progress)
        self._worker.tool_finished.connect(self._on_tool_finished)
        self._worker.turn_finished.connect(self._on_turn_finished)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.tool_call_requested.connect(self._execute_tool_in_main_thread)
        self._worker.start()

        self._send_btn.setEnabled(False)
        self._send_btn.setText("...")

    # ── AgentWorker 信号处理 ──────────────────────────────────────────────────

    @Slot(str)
    def _on_token(self, token: str):
        if self._current_ai_widget:
            self._current_ai_widget.append_token(token)

    @Slot(str, str)
    def _on_tool_started(self, tool_name: str, args_str: str):
        self._current_tool_widget = ToolCallWidget(tool_name, args_str)
        self._insert_widget(self._current_tool_widget)

    @Slot(str)
    def _on_tool_progress(self, msg: str):
        if self._current_tool_widget:
            self._current_tool_widget.add_progress(msg)

    @Slot(str, str)
    def _on_tool_finished(self, tool_name: str, result: str):
        if self._current_tool_widget:
            self._current_tool_widget.set_result(result)
        self._current_tool_widget = None

        if self._current_ai_widget and self._current_ai_widget._buffer.strip():
            self._current_ai_widget.set_final_text(self._current_ai_widget._buffer)
        self._current_ai_widget = None

        self._current_ai_widget = AIMessageWidget()
        self._insert_widget(self._current_ai_widget)

    @Slot()
    def _on_turn_finished(self):
        pass

    @Slot(str)
    def _on_all_done(self, final_text: str):
        if self._current_ai_widget:
            self._current_ai_widget.set_final_text(final_text)
            self._current_ai_widget = None

        # 追加 assistant 消息到会话历史
        if self._current_session is not None:
            self._current_session.messages.append({"role": "assistant", "content": final_text})
            # 实时保存
            self._sm.save_session(self._current_session)
            # 刷新侧边栏（updated_at 已更新）
            self._sidebar.refresh(self._current_session.session_id)

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    @Slot(str)
    def _on_error(self, error_msg: str):
        err_widget = AIMessageWidget()
        err_widget.set_final_text(f"**错误：** {error_msg}")
        self._insert_widget(err_widget)

        self._current_ai_widget = None
        # 保存会话（即使出错也保留已有历史）
        if self._current_session is not None:
            self._sm.save_session(self._current_session)

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    # ── 工具调用（主线程执行，COM 安全）──────────────────────────────────────

    @Slot(str, str, str)
    def _execute_tool_in_main_thread(self, tool_name: str, args_str: str, tool_id: str):
        """
        在主线程中执行工具函数（保证 COM STA 安全）。
        执行前检查工作空间限制（豁免工具除外）。
        执行完毕后调用 worker.receive_tool_result() 传回结果。
        """
        if self._worker is None:
            return

        tool_fn = tools_map.get(tool_name)
        if tool_fn is None:
            result = json.dumps(
                {"error": f"未知工具：{tool_name}"},
                ensure_ascii=False,
            )
            self._worker.receive_tool_result(result)
            return

        try:
            args = json.loads(args_str) if args_str.strip() else {}
        except json.JSONDecodeError:
            args = {}

        # 工作空间检查（豁免工具跳过）
        if tool_name not in _WORKSPACE_EXEMPT_TOOLS:
            ws = self._current_session.workspace if self._current_session else None
            err = _check_workspace(args, ws)
            if err:
                result = json.dumps({"error": err}, ensure_ascii=False)
                self._worker.receive_tool_result(result)
                return

        # 注入 progress_signal
        args["progress_signal"] = self._worker.tool_progress

        try:
            result = tool_fn(**args)
        except Exception as e:
            import traceback
            result = json.dumps(
                {"error": str(e), "traceback": traceback.format_exc()},
                ensure_ascii=False,
            )

        self._worker.receive_tool_result(result)

    # ── UI 辅助 ───────────────────────────────────────────────────────────────

    def _add_user_message(self, text: str):
        widget = UserMessageWidget(text)
        self._insert_widget(widget)

    def _insert_widget(self, widget: QWidget):
        """在 stretch 之前插入消息 widget。"""
        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, widget)

    def _clear_chat(self):
        """清空当前会话的聊天记录和对话历史。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)

        if self._current_session is not None:
            self._current_session.messages.clear()
            self._sm.save_session(self._current_session)
        self._clear_chat_widgets()

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    def stop_agent(self):
        """主窗口关闭时调用：停止后台线程，保存当前会话。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        # 关闭时持久化当前会话（确保最新消息不丢失）
        if self._current_session is not None:
            self._sm.save_session(self._current_session)

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        pass
