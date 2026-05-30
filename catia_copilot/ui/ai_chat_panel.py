"""
AI 聊天面板 - CATIA Copilot 主窗口新 Tab 页。

布局：
  顶部工具栏：模型选择 / 清空 / 设置
  中部滚动区：消息气泡（用户/AI/工具调用卡片）
  底部输入区：多行输入框 + 发送按钮

线程安全：
  AgentWorker 在后台线程运行，通过 tool_call_requested Signal 请求主线程执行工具。
  主线程执行完毕后调用 worker.receive_tool_result() 传回结果。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from PySide6.QtCore import Qt, QEvent, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QTextEdit, QTextBrowser,
    QFrame, QSizePolicy, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QDialogButtonBox, QToolButton,
)

from catia_copilot.ui.theme_manager import theme_signal, theme_manager
from catia_copilot.ai import config as ai_config
from catia_copilot.ai.agent import AgentWorker
from catia_copilot.ai.tools import tools_map

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 颜色常量（深色/浅色双主题）
# ---------------------------------------------------------------------------

_COLORS = {
    "dark": {
        "user_bg":     "#1e3a5f",
        "user_fg":     "#e8f0fe",
        "ai_bg":       "#2a2a2a",
        "ai_fg":       "#e0e0e0",
        "tool_bg":     "#1a2a1a",
        "tool_fg":     "#a0d0a0",
        "tool_border": "#3a5a3a",
        "progress_fg": "#808080",
    },
    "light": {
        "user_bg":     "#dce8ff",
        "user_fg":     "#1a1a2e",
        "ai_bg":       "#f5f5f5",
        "ai_fg":       "#1a1a1a",
        "tool_bg":     "#f0fff0",
        "tool_fg":     "#2e7d32",
        "tool_border": "#a5d6a7",
        "progress_fg": "#757575",
    },
}


def _c(key: str) -> str:
    mode = theme_manager.current_mode()
    palette = _COLORS.get("dark" if mode == "dark" else "light", _COLORS["light"])
    return palette.get(key, "")


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
        bg, fg = _c("user_bg"), _c("user_fg")
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._build_ui()
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
        """流式追加 token（纯文本模式）。"""
        self._buffer += token
        cursor = self._browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._browser.setTextCursor(cursor)
        self._browser.insertPlainText(token)

    def set_final_text(self, text: str):
        """流式结束后，用 Markdown 重新渲染完整内容。"""
        self._buffer = text
        self._browser.setMarkdown(text)
        self._adjust_height()

    def _apply_style(self):
        bg, fg = _c("ai_bg"), _c("ai_fg")
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

        # 标题行
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

        # 可折叠内容区
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
        bg = _c("tool_bg")
        fg = _c("tool_fg")
        border = _c("tool_border")
        prog_fg = _c("progress_fg")
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
    AI 运行时参数对话框。

    Provider / API Key / 模型列表 通过直接编辑 ai_config.json 管理
    （格式与 Standard-Agent-Server 的 models_config.json 相同）。
    此对话框只管理运行时参数：temperature / max_tool_rounds / timeout。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 助手设置")
        self.setMinimumWidth(420)
        self._cfg = ai_config.load()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 配置文件路径提示
        cfg_path = ai_config.get_config_path()
        hint = QLabel(
            f"Provider / API Key / 模型列表 请直接编辑：\n{cfg_path}\n"
            f"（格式参考 ai_config.example.json）"
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(1)
        self._temperature.setValue(self._cfg.get("temperature", 0.7))
        form.addRow("Temperature:", self._temperature)

        self._max_rounds = QSpinBox()
        self._max_rounds.setRange(1, 50)
        self._max_rounds.setValue(self._cfg.get("max_tool_rounds", 20))
        form.addRow("最大工具调用轮数:", self._max_rounds)

        self._timeout = QSpinBox()
        self._timeout.setRange(10, 600)
        self._timeout.setSuffix(" 秒")
        self._timeout.setValue(self._cfg.get("timeout", 120))
        form.addRow("请求超时:", self._timeout)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_and_accept(self):
        # 只更新运行时参数，保留 providers 等字段不变
        cfg = ai_config.load()
        cfg["temperature"]    = self._temperature.value()
        cfg["max_tool_rounds"] = self._max_rounds.value()
        cfg["timeout"]        = self._timeout.value()
        try:
            ai_config.save(cfg)
        except Exception as e:
            logger.error("保存 AI 配置失败：%s", e)
        self.accept()

    def get_config(self) -> dict:
        return ai_config.load()


# ---------------------------------------------------------------------------
# 主聊天面板
# ---------------------------------------------------------------------------

class AIChatPanel(QWidget):
    """
    AI 聊天面板，作为主窗口的新 Tab 页嵌入。

    职责：
      - 管理对话历史（messages list）
      - 创建/停止 AgentWorker
      - 在主线程执行工具调用（COM 线程安全）
      - 渲染消息气泡和工具调用卡片
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[dict[str, Any]] = []
        self._worker: AgentWorker | None = None
        self._current_ai_widget: AIMessageWidget | None = None
        self._current_tool_widget: ToolCallWidget | None = None
        self._build_ui()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_chat_area(), 1)
        root.addWidget(self._build_input_area())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        layout.addWidget(QLabel("模型:"))

        self._model_combo = QComboBox()
        self._model_combo.setFixedWidth(200)
        self._model_combo.setEditable(True)  # 允许手动输入
        cfg = ai_config.load()
        model_ids = ai_config.list_model_ids(cfg)
        default_model = ai_config.get_default_model_id(cfg)
        if model_ids:
            self._model_combo.addItems(model_ids)
        else:
            self._model_combo.addItem(default_model)
        # 选中默认模型
        idx = self._model_combo.findText(default_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentText(default_model)
        layout.addWidget(self._model_combo)

        layout.addStretch()

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setFixedWidth(50)
        self._clear_btn.clicked.connect(self._clear_chat)
        layout.addWidget(self._clear_btn)

        self._settings_btn = QPushButton("⚙ 设置")
        self._settings_btn.setFixedWidth(60)
        self._settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_btn)

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
        self._chat_layout.addStretch()  # 撑底，消息从顶部开始堆叠

        self._scroll.setWidget(self._chat_container)
        return self._scroll

    def _build_input_area(self) -> QWidget:
        area = QWidget()
        area.setFixedHeight(80)
        layout = QHBoxLayout(area)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._input_box = QTextEdit()
        self._input_box.setPlaceholderText("输入消息... (Ctrl+Enter 发送，Enter 换行)")
        self._input_box.installEventFilter(self)
        layout.addWidget(self._input_box, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(60, 60)
        self._send_btn.clicked.connect(self._send_message)
        layout.addWidget(self._send_btn)

        return area

    # ── 事件过滤（Ctrl+Enter 发送）────────────────────────────────────────────

    def eventFilter(self, obj, event: QEvent) -> bool:
        if obj is self._input_box and event.type() == QEvent.Type.KeyPress:
            key_event = event  # type: QKeyEvent
            if (key_event.key() == Qt.Key.Key_Return
                    and key_event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._send_message()
                return True
        return super().eventFilter(obj, event)

    # ── 消息发送 ──────────────────────────────────────────────────────────────

    def _send_message(self):
        text = self._input_box.toPlainText().strip()
        if not text:
            return
        if self._worker and self._worker.isRunning():
            return  # 正在处理中，忽略

        self._input_box.clear()

        # 显示用户消息气泡
        self._add_user_message(text)

        # 构建对话历史
        cfg = ai_config.load()
        # 用工具栏中选中的模型覆盖默认模型
        selected_model = self._model_combo.currentText().strip()
        if selected_model:
            cfg["default_model"] = selected_model

        if not self._messages:
            # 首次对话，插入 system prompt
            sys_prompt = cfg.get("system_prompt", "")
            if sys_prompt:
                self._messages.append({"role": "system", "content": sys_prompt})

        self._messages.append({"role": "user", "content": text})

        # 创建 AI 消息占位 widget
        self._current_ai_widget = AIMessageWidget()
        self._insert_widget(self._current_ai_widget)

        # 启动 AgentWorker
        self._worker = AgentWorker(self._messages, cfg)
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
        # 新建工具调用卡片
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

        # 工具调用完成后，新建下一轮 AI 消息 widget
        self._current_ai_widget = AIMessageWidget()
        self._insert_widget(self._current_ai_widget)

    @Slot()
    def _on_turn_finished(self):
        pass  # 可用于显示"思考中..."等状态

    @Slot(str)
    def _on_all_done(self, final_text: str):
        # 用 Markdown 重新渲染最后一条 AI 消息
        if self._current_ai_widget:
            self._current_ai_widget.set_final_text(final_text)
            self._current_ai_widget = None

        # 把 assistant 回复加入历史
        self._messages.append({"role": "assistant", "content": final_text})

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    @Slot(str)
    def _on_error(self, error_msg: str):
        # 显示错误消息
        err_widget = AIMessageWidget()
        err_widget.set_final_text(f"**错误：** {error_msg}")
        self._insert_widget(err_widget)

        self._current_ai_widget = None
        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    # ── 工具调用（主线程执行，COM 安全）──────────────────────────────────────

    @Slot(str, str, str)
    def _execute_tool_in_main_thread(self, tool_name: str, args_str: str, tool_id: str):
        """
        在主线程中执行工具函数（保证 COM STA 安全），
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

        # 注入 progress_signal（工具函数通过它推送进度）
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
        # chat_layout 最后一项是 stretch，插入到倒数第二位
        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, widget)

    def _clear_chat(self):
        """清空聊天记录和对话历史。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)

        self._messages.clear()
        self._current_ai_widget = None
        self._current_tool_widget = None

        # 移除所有消息 widget（保留最后的 stretch）
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    def _open_settings(self):
        dlg = AISettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 重新加载配置，刷新模型下拉框
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

    def stop_agent(self):
        """主窗口关闭时调用，停止后台线程。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        # 子 widget 各自监听主题变化，这里只需刷新面板背景（如有需要）
        pass
