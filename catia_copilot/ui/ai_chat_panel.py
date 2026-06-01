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
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any
from PySide6.QtCore import Qt, QEvent, QRect, QSizeF, Signal, Slot, QTimer, QSettings
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QTextCursor
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
from catia_copilot.ui.ui_layout import L
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
        layout.setContentsMargins(*L.USER_MSG_MARGINS)

        # setTextFormat(PlainText) 防止用户输入被当成 HTML 解析
        self._label = QLabel(self._text)
        self._label.setObjectName("UserBubble")
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        # Expanding：撑满 USER_MSG_MARGINS.left 留白之外的全部宽度，
        # 气泡占满右侧可用空间，不会因 wordWrap 被压窄
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self._label)
        self._apply_style()

    def _apply_style(self):
        bg, fg = chat_colors().user_bg, chat_colors().user_fg
        self._label.setStyleSheet(
            f"QLabel#UserBubble {{ background-color: {bg}; color: {fg}; "
            f"border-radius: {L.USER_MSG_RADIUS}px; padding: {L.USER_MSG_PADDING}; "
            f"font-size: {L.USER_MSG_FONT_SIZE}px; }}"
        )

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 消息 Widget：AI 回复
# ---------------------------------------------------------------------------

class _BaseBrowser(QTextBrowser):
    """
    所有聊天气泡 QTextBrowser 的公共基类。

    提供两个共享行为：
    1. resizeEvent：宽度变化时通知布局系统重新询问 sizeHint。
    2. contextMenuEvent：过滤掉 "Copy Link Location" 等无关菜单项，
       只保留 Copy 和 Select All，并确保菜单使用 QApplication 的全局
       stylesheet（即系统主题），而不是从父 widget 继承错误的样式。

    _doc_height() 由子类实现，返回文档所需的像素高度。
    """

    def _doc_height(self) -> int:
        """按当前 viewport 宽度排版，返回文档所需高度。子类实现。"""
        raise NotImplementedError

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()

    def contextMenuEvent(self, event):
        """只保留 Copy 和 Select All，去掉 Copy Link Location 等无关项。

        创建无 parent 的 QMenu，使其直接从 QApplication 全局 stylesheet
        获取样式，而不是从父 widget 继承错误的背景色。
        """
        src = self.createStandardContextMenu(event.pos())
        # 创建无 parent 的菜单，确保从 QApplication stylesheet 获取主题样式
        menu = QMenu()
        for action in src.actions():
            # 过滤掉含 "link" 的菜单项（Copy Link Location 等）
            if "link" in action.text().lower():
                continue
            if action.isSeparator():
                menu.addSeparator()
            else:
                menu.addAction(action)
        src.deleteLater()
        # 清理首尾多余分隔线
        actions = menu.actions()
        while actions and actions[0].isSeparator():
            menu.removeAction(actions.pop(0))
        while actions and actions[-1].isSeparator():
            menu.removeAction(actions.pop())
        menu.exec(event.globalPos())


class _AutoHeightBrowser(_BaseBrowser):
    """
    高度随内容自动撑开的 QTextBrowser，不出现内部滚动条。

    原理：重写 sizeHint() / minimumSizeHint()，在里面按当前宽度重新排版文档
    并返回准确高度。内容或宽度变化时调用 updateGeometry() 通知布局系统重新
    询问 sizeHint()，由布局系统驱动高度更新，不需要手动 setFixedHeight。

    这是 Qt 官方推荐的"自动高度文本编辑器"做法，比手动计算 setFixedHeight
    更稳健：高度计算逻辑只在一处，布局系统负责何时应用。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.document().contentsChanged.connect(self.updateGeometry)

    def _doc_height(self) -> int:
        """按当前 viewport 宽度排版，返回文档所需高度（含 padding）。"""
        vw = self.viewport().width()
        if vw <= 0:
            return self.minimumHeight()
        doc = self.document()
        # 保存原始 pageSize，计算完后恢复，避免影响后续渲染
        old_size = doc.pageSize()
        doc.setPageSize(QSizeF(vw, 1e9))
        h = int(doc.size().height())
        doc.setPageSize(old_size)
        # 加上 QSS padding 上下各 AI_MSG_PADDING_V px
        return h + 2 * L.AI_MSG_PADDING_V

    def sizeHint(self):
        # 宽度返回 0，由布局系统（Expanding policy）决定；只约束高度
        return QSizeF(0, max(self._doc_height(), L.AI_MSG_MIN_HEIGHT)).toSize()

    def minimumSizeHint(self):
        # 最小宽度也为 0，允许窗口任意缩小而不溢出
        return QSizeF(0, max(self._doc_height(), L.AI_MSG_MIN_HEIGHT)).toSize()


class _BoundedHeightBrowser(_BaseBrowser):
    """
    高度随内容自动收缩、到上限后出现滚动条的 QTextBrowser。

    - 内容少时：sizeHint 返回实际文档高度，布局系统收缩 widget，无多余空白
    - 内容多时：高度到达 max_height 上限后停止增长，垂直滚动条按需出现
    - 不影响水平方向（Expanding policy）
    """

    def __init__(self, max_height: int, parent=None):
        super().__init__(parent)
        self._max_height = max_height
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 水平滚动条关闭（内容折行），垂直滚动条按需显示
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.document().contentsChanged.connect(self.updateGeometry)

    def _doc_height(self) -> int:
        """按当前 viewport 宽度排版，返回文档所需高度。"""
        vw = self.viewport().width()
        if vw <= 0:
            return 20  # 尚未布局时给一个最小值
        doc = self.document()
        old_size = doc.pageSize()
        doc.setPageSize(QSizeF(vw, 1e9))
        h = int(doc.size().height())
        doc.setPageSize(old_size)
        return h

    def sizeHint(self):
        h = min(self._doc_height(), self._max_height)
        return QSizeF(0, h).toSize()

    def minimumSizeHint(self):
        h = min(self._doc_height(), self._max_height)
        return QSizeF(0, h).toSize()


class AIMessageWidget(QFrame):
    """AI 回复消息，左对齐，支持流式追加和 Markdown 渲染。"""

    # 流式阶段每累积多少个 token 重新渲染一次 Markdown
    _RENDER_INTERVAL = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._token_count = 0
        self._inserted = False   # 是否已插入聊天布局，用于懒插入判断
        self._build_ui()
        # 初始隐藏：等第一个 token 到来再显示，避免空 widget 占位产生空白
        self._set_visible(False)
        theme_signal.theme_changed.connect(self._on_theme_changed)

    @property
    def is_inserted(self) -> bool:
        """是否已被插入聊天布局（懒插入标志）。"""
        return self._inserted

    @is_inserted.setter
    def is_inserted(self, value: bool) -> None:
        self._inserted = value

    def _set_visible(self, visible: bool) -> None:
        """显示/隐藏，同时切换 SizePolicy 让布局在隐藏时不保留空间。"""
        if visible:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)  # Qt QWIDGETSIZE_MAX
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.show()
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setFixedHeight(0)
            self.hide()

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*L.AI_MSG_MARGINS)

        self._browser = _AutoHeightBrowser()
        self._browser.setObjectName("AIBubble")
        self._browser.setOpenExternalLinks(True)
        self._browser.setReadOnly(True)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._browser)
        self._apply_style()

    def append_token(self, token: str):
        """流式追加 token。

        第一个 token 到来时显示 widget（之前隐藏以避免空白占位）。
        每累积 _RENDER_INTERVAL 个 token 用 setMarkdown 重新渲染一次，
        其余时间直接 insertPlainText 追加，平衡渲染质量与性能。
        """
        if not self._buffer:
            self._set_visible(True)

        self._buffer += token
        self._token_count += 1

        if self._token_count % self._RENDER_INTERVAL == 0:
            # 整批重新渲染 Markdown，setMarkdown 会完整替换文档内容
            self._browser.setMarkdown(self._buffer)
        else:
            cursor = self._browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._browser.setTextCursor(cursor)
            self._browser.insertPlainText(token)

    def set_final_text(self, text: str):
        """流式结束后（或工具调用前），用 Markdown 重新渲染完整内容。"""
        self._buffer = text
        self._set_visible(True)
        self._browser.setMarkdown(text)

    def _apply_style(self):
        c = chat_colors()
        self._browser.setStyleSheet(
            f"QTextBrowser#AIBubble {{ background-color: {c.ai_bg}; color: {c.ai_fg}; "
            f"border-radius: {L.AI_MSG_RADIUS}px; "
            f"border: 1px solid {c.ai_border}; "
            f"padding: {L.AI_MSG_PADDING_V}px {L.AI_MSG_PADDING_H}px; "
            f"font-size: {L.AI_MSG_FONT_SIZE}px; }}"
        )

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 消息 Widget：工具调用卡片
# ---------------------------------------------------------------------------

class ToolCallWidget(QFrame):
    """工具调用卡片，可折叠展开。"""

    @staticmethod
    def _fmt_arg(v) -> str:
        """将工具参数值格式化为简短的显示字符串（最多 40 字符）。

        字符串直接显示（不用 repr），避免路径中的反斜杠被双重转义。
        列表只显示第一个元素加省略号。其他类型用 str() 转换。
        """
        if isinstance(v, str):
            s = v
        elif isinstance(v, list):
            if not v:
                s = "[]"
            elif len(v) == 1:
                s = f"[{v[0]}]"
            else:
                s = f"[{v[0]}, ...]"
        else:
            s = str(v)
        return s[:40] + "…" if len(s) > 40 else s

    def __init__(self, tool_name: str, args_str: str, parent=None):
        super().__init__(parent)
        self._tool_name = tool_name
        self._args_str = args_str
        self._expanded = False
        self._build_ui()
        theme_signal.theme_changed.connect(self._on_theme_changed)

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("ToolCallCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*L.TOOL_CARD_MARGINS)
        outer.setSpacing(L.TOOL_CARD_SPACING)

        header = QHBoxLayout()
        header.setSpacing(L.TOOL_CARD_HEADER_SPACING)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("▶")
        self._toggle_btn.setFixedSize(*L.TOOL_CARD_TOGGLE_SIZE)
        self._toggle_btn.clicked.connect(self._toggle)
        _toggle_font = QFont("Segoe UI Emoji")
        _toggle_font.setPixelSize(10)
        self._toggle_btn.setFont(_toggle_font)

        try:
            args_dict = json.loads(self._args_str)
            args_summary = ", ".join(
                f"{k}={self._fmt_arg(v)}" for k, v in list(args_dict.items())[:3]
            )
        except Exception:
            args_summary = self._args_str[:60]

        self._title_label = QLabel(f"🔧 {self._tool_name}({args_summary})")
        self._title_label.setObjectName("ToolCardTitle")
        self._title_label.setTextFormat(Qt.TextFormat.PlainText)  # 防止路径中 < > & 被解析为 HTML
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_label.mousePressEvent = lambda _: self._toggle()

        header.addWidget(self._toggle_btn)
        header.addWidget(self._title_label, 1)
        outer.addLayout(header)

        self._content_widget = QWidget()
        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(*L.TOOL_CARD_CONTENT_MARGINS)
        content_layout.setSpacing(L.TOOL_CARD_CONTENT_SPACING)

        self._progress_label = QLabel()
        self._progress_label.setObjectName("ToolCardProgress")
        self._progress_label.setWordWrap(True)
        self._progress_label.setVisible(False)  # 无内容时不占位
        content_layout.addWidget(self._progress_label)

        self._result_browser = _BoundedHeightBrowser(L.TOOL_CARD_RESULT_MAX_HEIGHT)
        self._result_browser.setObjectName("ToolResultBrowser")
        content_layout.addWidget(self._result_browser)

        self._content_widget.setVisible(False)
        outer.addWidget(self._content_widget)
        self._apply_style()

    def _toggle(self):
        """切换展开/折叠状态。

        ChatScrollArea 的"接近底部才跟随"逻辑自动处理 scroll 位置：
          - 用户在底部时折叠/展开，scroll 跟随
          - 用户滚上去看历史时折叠/展开，scroll 不跟随，位置保持不变
        """
        self._expanded = not self._expanded
        self._content_widget.setVisible(self._expanded)
        self._toggle_btn.setText("▼" if self._expanded else "▶")

    def add_progress(self, msg: str):
        current = self._progress_label.text()
        self._progress_label.setText((current + "\n" + msg).strip() if current else msg)
        self._progress_label.setVisible(True)  # 有内容才显示

    def set_result(self, result: str):
        """填入工具调用结果，保持折叠状态（不自动展开）。"""
        try:
            parsed = json.loads(result)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            formatted = result
        self._result_browser.setPlainText(formatted)

    def _apply_style(self):
        bg = chat_colors().tool_bg
        fg = chat_colors().tool_fg
        border = chat_colors().tool_border
        prog_fg = chat_colors().progress_fg
        self.setStyleSheet(
            f"QFrame#ToolCallCard {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: {L.TOOL_CARD_RADIUS}px; }}"
        )
        self._title_label.setStyleSheet(
            f"QLabel#ToolCardTitle {{ color: {fg}; font-size: {L.TOOL_CARD_TITLE_FONT_SIZE}px; "
            f"font-weight: bold; background: transparent; border: none; }}"
        )
        self._progress_label.setStyleSheet(
            f"QLabel#ToolCardProgress {{ color: {prog_fg}; font-size: {L.TOOL_CARD_PROGRESS_FONT_SIZE}px; "
            f"background: transparent; border: none; }}"
        )
        self._result_browser.setStyleSheet(
            f"QTextBrowser#ToolResultBrowser {{ background-color: {bg}; color: {fg}; "
            f"font-size: {L.TOOL_CARD_RESULT_FONT_SIZE}px; "
            f"font-family: monospace; border: none; }}"
        )
        # QPalette 直接设置 viewport 背景，确保 windowsvista 风格的
        # QAbstractScrollArea 规则不会把 viewport 底色覆盖成主题色
        vp_pal = self._result_browser.viewport().palette()
        vp_pal.setColor(QPalette.ColorRole.Base, QColor(bg))
        vp_pal.setColor(QPalette.ColorRole.Window, QColor(bg))
        self._result_browser.viewport().setPalette(vp_pal)
        self._result_browser.viewport().setAutoFillBackground(True)

    @Slot(str)
    def _on_theme_changed(self, _mode: str):
        self._apply_style()


# ---------------------------------------------------------------------------
# 聊天容器（消息 widget 的父容器）
# ---------------------------------------------------------------------------

class _ChatContainer(QWidget):
    """
    聊天消息容器。

    重写 minimumSizeHint() 返回零宽度，使 QScrollArea（widgetResizable=True）
    能将容器宽度收缩到 viewport 宽度，而不被子 widget 的最小宽度撑开。
    这样主窗口可以任意缩小，内容随宽度折行，不会溢出右边界。
    """

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return hint.__class__(0, hint.height())


# ---------------------------------------------------------------------------
# 滚动区域（自动滚底）
# ---------------------------------------------------------------------------

class ChatScrollArea(QScrollArea):
    """聊天消息滚动区域。

    布局变化时，只有当滚动条已经在底部附近（距底部 ≤ _SNAP_THRESHOLD px）
    才自动跟随滚到底部。这样：
      - 流式输出时用户未手动滚动，scroll 在底部，自动跟随
      - 用户手动滚上去看历史，scroll 不在底部，不自动跟随
      - 折叠/展开工具卡片时，若用户在底部则跟随，否则保持位置不变
    不需要任何外部开关，也不存在竞态问题。
    """

    # 距底部多少像素以内视为"在底部"
    _SNAP_THRESHOLD = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        # windowsvista 风格会给 QScrollArea 加 border，
        # 用 objectName 选择器覆盖，只影响本实例，不影响其他 QScrollArea。
        self.setObjectName("ChatScrollArea")
        self.setStyleSheet(
            "QScrollArea#ChatScrollArea { border: none; padding: 0px; }"
        )
        # _at_bottom：用户是否在底部附近。初始为 True，表示新会话默认跟随。
        # 用户手动向上滚动时置 False，滚回底部时置 True。
        self._at_bottom = True
        self.verticalScrollBar().valueChanged.connect(self._on_value_changed)
        # rangeChanged 在 maximum 真正更新后触发，此时判断是否需要跟随。
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

    def _on_value_changed(self, value: int):
        """用户手动滚动时更新 _at_bottom 标志。"""
        sb = self.verticalScrollBar()
        self._at_bottom = (sb.maximum() - value <= self._SNAP_THRESHOLD)

    def _on_range_changed(self, _min: int, new_max: int):
        """maximum 更新后，若之前在底部则跟随滚到新底部。"""
        if self._at_bottom:
            self.verticalScrollBar().setValue(new_max)

    def reset_to_bottom(self):
        """强制重置到底部状态（切换会话时调用），下次 rangeChanged 时会跟随。"""
        self._at_bottom = True


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
        hint.setStyleSheet(f"color: gray; font-size: {L.SMALL_FONT_SIZE}px;")
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
        self._status_label.setStyleSheet(f"font-size: {L.SMALL_FONT_SIZE}px;")
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

        class _FetchThread(QThread):
            def __init__(self, base, key, parent=None):
                super().__init__(parent)
                self.base, self.key = base, key
                self.result = []

            def run(self):
                self.result = fetch_models_from_api(self.base, self.key, timeout=15)

        self._fetch_thread = _FetchThread(api_base, api_key, self)

        def _on_done():
            models = self._fetch_thread.result
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

        self._fetch_thread.finished.connect(_on_done)
        self._fetch_thread.start()

    def _test_connection(self):
        api_base = self._api_base.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        model = self._model_combo.currentText().strip()
        if not api_base or not api_key or not model:
            self._set_status("请先填写 API Base URL、API Key 和模型", error=True)
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("...")
        self._set_status("正在测试...")

        class _TestThread(QThread):
            def __init__(self, base, key, mdl, parent=None):
                super().__init__(parent)
                self.base, self.key, self.mdl = base, key, mdl
                self.status_msg = ""
                self.success = False

            def run(self):
                url = f"{self.base}/v1/chat/completions"
                body = json.dumps({
                    "model": self.mdl,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "max_tokens": 10,
                    "stream": False,
                }).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.key}",
                }
                t0 = time.time()
                try:
                    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
                    with urllib.request.urlopen(request, timeout=30) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    elapsed = time.time() - t0
                    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    tokens = data.get("usage", {}).get("total_tokens", "?")
                    self.status_msg = (
                        f"测试通过 ✔  耗时 {elapsed:.1f}s，消耗 {tokens} tokens，"
                        f"回复：「{reply[:40]}」"
                    )
                    self.success = True
                except Exception as e:
                    elapsed = time.time() - t0
                    self.status_msg = f"测试失败 ✖  {elapsed:.1f}s：{str(e)[:80]}"
                    self.success = False

        self._test_thread = _TestThread(api_base, api_key, model, self)

        def _on_done():
            self._test_btn.setEnabled(True)
            self._test_btn.setText("测试连接")
            self._set_status(self._test_thread.status_msg, error=not self._test_thread.success)

        self._test_thread.finished.connect(_on_done)
        self._test_thread.start()

    def _set_status(self, msg: str, error: bool = False):
        color = "#c0392b" if error else "gray"
        self._status_label.setStyleSheet(f"color: {color}; font-size: {L.SMALL_FONT_SIZE}px;")
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
        """返回当前生效的 AI 配置（从磁盘重新加载）。"""
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

    _BTN_H = L.HANDLE_BTN_HEIGHT   # 箭头按钮区域高度
    _W     = L.SPLITTER_HANDLE_WIDTH  # handle 宽度

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
        cy = self.height() // 2
        return QRect(0, cy - self._BTN_H // 2, self._W, self._BTN_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        c = chat_colors()
        # handle 整体背景（与侧边栏融合，不突兀）
        painter.fillRect(self.rect(), QColor(c.handle_bg))

        # 两侧边缘竖线：与分隔线同色，提示此处可拖动
        painter.setPen(QColor(c.divider))
        painter.drawLine(0, 0, 0, self.height() - 1)
        painter.drawLine(self._W - 1, 0, self._W - 1, self.height() - 1)

        # 箭头按钮区域背景（hover 时高亮）
        btn_rect = self._arrow_rect()
        if self._hovered:
            painter.fillRect(btn_rect, QColor(c.handle_hover))

        # 绘制箭头：指定 Segoe UI Emoji 字体确保现代矢量渲染
        arrow = "◀" if not self._is_collapsed() else "▶"
        painter.setPen(QColor(c.handle_fg))
        emoji_font = QFont("Segoe UI Emoji", -1)
        emoji_font.setPixelSize(10)
        painter.setFont(emoji_font)
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
            # 展开：恢复到默认宽度
            w = L.SIDEBAR_DEFAULT_WIDTH
            sp.setSizes([w, sp.width() - w - self._W])
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

    # 用户点击"新对话"按钮时发出，供 AIChatPanel 连接
    new_session_requested = Signal()

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
        header.setObjectName("SidebarHeader")
        header.setFixedHeight(L.SIDEBAR_HEADER_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(*L.SIDEBAR_HEADER_MARGINS)
        header_layout.setSpacing(L.SIDEBAR_HEADER_SPACING)
        title_lbl = QLabel("会话列表")
        title_lbl.setStyleSheet(f"font-weight: bold; font-size: {L.TITLE_FONT_SIZE}px;")
        header_layout.addWidget(title_lbl, 1)
        layout.addWidget(header)

        # 标题栏与列表之间的分隔线
        self._top_divider = QFrame()
        self._top_divider.setObjectName("SidebarDivider")
        self._top_divider.setFrameShape(QFrame.Shape.HLine)
        self._top_divider.setFixedHeight(L.SIDEBAR_DIVIDER_HEIGHT)
        layout.addWidget(self._top_divider)

        # 会话列表
        self._list = QListWidget()
        self._list.setObjectName("SessionList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setSpacing(L.SIDEBAR_LIST_SPACING)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list, 1)

        # 列表与底部按钮区之间的分隔线
        self._bottom_divider = QFrame()
        self._bottom_divider.setObjectName("SidebarDivider")
        self._bottom_divider.setFrameShape(QFrame.Shape.HLine)
        self._bottom_divider.setFixedHeight(L.SIDEBAR_DIVIDER_HEIGHT)
        layout.addWidget(self._bottom_divider)

        # 底部"新对话"按钮
        bottom = QWidget()
        bottom.setObjectName("SidebarBottom")
        bottom.setFixedHeight(L.SIDEBAR_BOTTOM_HEIGHT)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(*L.SIDEBAR_BOTTOM_MARGINS)
        self._new_btn = QPushButton("新对话")
        self._new_btn.setObjectName("SidebarNewBtn")
        self._new_btn.setFixedHeight(L.SIDEBAR_NEW_BTN_HEIGHT)
        self._new_btn.clicked.connect(self.new_session_requested)
        bottom_layout.addWidget(self._new_btn)
        layout.addWidget(bottom)

        self._apply_style()

    def _apply_style(self):
        c = chat_colors()
        # 用统一的 setStyleSheet 覆盖整个侧边栏的子控件样式。
        # 用 objectName 选择器精确定位，避免影响其他区域的同类控件。
        # native 主题下 sidebar_bg 接近系统原生色，不会突兀。
        self.setStyleSheet(
            # 整体容器背景
            f"SessionSidebar {{ background-color: {c.sidebar_bg}; }}"
            # 标题栏：无边框无圆角，背景与侧边栏融合
            f"QWidget#SidebarHeader {{ background-color: {c.sidebar_bg};"
            f" border: none; border-radius: 0px; }}"
            # 底部区域：同上
            f"QWidget#SidebarBottom {{ background-color: {c.sidebar_bg};"
            f" border: none; border-radius: 0px; }}"
            # 分隔线：显示为实色块，不依赖 QFrame 默认的 sunken/raised 渲染
            f"QFrame#SidebarDivider {{ background-color: {c.divider}; border: none; }}"
            # 会话列表：用 objectName 精确限定，避免宽泛选择器污染 QMenu 样式上下文
            f"QListWidget#SessionList {{ background-color: {c.sidebar_bg};"
            f" color: {c.sidebar_fg}; border: none; outline: none; }}"
            # 列表项：无边框无圆角无间距，选中/hover 色铺满整行
            # outline:none 消除 windows11 风格的焦点虚线框
            f"QListWidget#SessionList::item {{ padding: 6px 10px;"
            f" border: none; border-radius: 0px; margin: 0px; outline: none; }}"
            # 选中态：用 SessionSidebar 作为父选择器提升优先级，覆盖 windows11 风格的圆角绘制
            f"SessionSidebar QListWidget#SessionList::item:selected {{"
            f" background-color: {c.sidebar_sel}; color: {c.sidebar_fg};"
            f" border: none; border-radius: 0px; outline: none; }}"
            f"SessionSidebar QListWidget#SessionList::item:hover:!selected {{"
            f" background-color: {c.sidebar_hover};"
            f" border: none; border-radius: 0px; }}"
            # 滚动条：细条样式
            f"QListWidget#SessionList QScrollBar:vertical {{ width: 4px; border: none;"
            f" background: transparent; }}"
            f"QListWidget#SessionList QScrollBar::handle:vertical {{ background: {c.sidebar_fg};"
            f" border-radius: 2px; min-height: 20px; }}"
            f"QListWidget#SessionList QScrollBar::add-line:vertical,"
            f"QListWidget#SessionList QScrollBar::sub-line:vertical {{ height: 0px; }}"
            f"QListWidget#SessionList QScrollBar:horizontal {{ height: 0px; }}"
            # 新对话按钮：用 objectName 精确选择，避免被 native.qss 的宽泛 QPushButton 规则覆盖
            f"QPushButton#SidebarNewBtn {{ background-color: {c.sidebar_bg};"
            f" color: {c.sidebar_fg}; border: 1px solid {c.divider}; border-radius: 4px; }}"
            f"QPushButton#SidebarNewBtn:hover {{ background-color: {c.sidebar_hover}; }}"
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

        # 若删除的是当前会话，先把 panel 的当前会话置 None，
        # 防止后续 _switch_session 里的 save_session 把它复活写回 index
        if panel and is_current:
            panel.invalidate_current_session()

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
    ws = Path(workspace).resolve()
    for key in _PATH_KEYS:
        val = args.get(key)
        paths = [val] if isinstance(val, str) else (val if isinstance(val, list) else [])
        for p in paths:
            if p is None:
                continue
            try:
                if not Path(p).resolve().is_relative_to(ws):
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
        # 当前正在执行的工具调用信息（用于写入 session messages）
        # 格式：{"tool_call_id": str, "name": str, "arguments": str}
        self._pending_tool_call: dict | None = None
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
        self._splitter.setHandleWidth(L.SPLITTER_HANDLE_WIDTH)

        # 左侧边栏
        self._sidebar = SessionSidebar(self._sm, self._splitter)
        sid = self._current_session.session_id if self._current_session else None
        self._sidebar.refresh(sid)
        self._sidebar.new_session_requested.connect(self._new_session)
        self._splitter.addWidget(self._sidebar)

        # 右主区：toolbar + 垂直 splitter（聊天区 / 输入区）
        chat_area = QWidget()
        chat_area.setObjectName("ChatArea")
        chat_layout = QVBoxLayout(chat_area)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        chat_layout.addWidget(self._build_toolbar())

        # 垂直 splitter：上方聊天消息区，下方输入区，用户可拖动调整输入框高度
        self._input_splitter = QSplitter(Qt.Orientation.Vertical)
        self._input_splitter.setObjectName("InputSplitter")
        self._input_splitter.setHandleWidth(4)
        self._input_splitter.setChildrenCollapsible(False)
        self._input_splitter.addWidget(self._build_chat_area())
        self._input_splitter.addWidget(self._build_input_area())
        # 聊天区拉伸，输入区保持用户设定的高度
        self._input_splitter.setStretchFactor(0, 1)
        self._input_splitter.setStretchFactor(1, 0)
        # 恢复上次保存的输入区高度
        saved = QSettings("CATIACopilot", "AIChatPanel").value("input_height", L.INPUT_BOX_HEIGHT + 16, type=int)
        self._input_splitter.setSizes([9999, saved])
        self._input_splitter.splitterMoved.connect(self._save_input_height)
        chat_layout.addWidget(self._input_splitter, 1)
        self._splitter.addWidget(chat_area)

        # 初始状态：侧边栏收起（宽度 0），主区占满
        self._splitter.setSizes([0, 9999])
        # 拉伸因子：主区随窗口缩放，侧边栏保持用户拖动后的宽度
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        root.addWidget(self._splitter)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("ToolbarBar")
        bar.setFixedHeight(L.TOOLBAR_HEIGHT)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(*L.TOOLBAR_MARGINS)
        layout.setSpacing(L.TOOLBAR_SPACING)

        # 会话标题：固定宽度，elidedText 在 _update_session_title 里计算
        self._session_title = QLabel()
        self._session_title.setFixedWidth(L.SESSION_TITLE_WIDTH)
        self._session_title.setStyleSheet(
            f"font-weight: bold; font-size: {L.TITLE_FONT_SIZE}px;"
        )
        self._session_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._session_title.mousePressEvent = lambda _: self._rename_current_session()
        layout.addWidget(self._session_title)

        # 图标按钮公共样式
        _icon_style = (
            f"QPushButton {{ border: none; background: transparent; "
            f"font-size: {L.ICON_BTN_FONT_SIZE}px; padding: 0px; }}"
            f"QPushButton:hover {{ background: rgba(128,128,128,40); "
            f"border-radius: {L.ICON_BTN_RADIUS}px; }}"
        )
        # emoji 图标按钮使用 Segoe UI Emoji 字体，确保现代矢量渲染
        _emoji_font = QFont("Segoe UI Emoji")
        _emoji_font.setPixelSize(L.ICON_BTN_FONT_SIZE)

        # 铅笔图标（重命名）
        rename_btn = QPushButton("✏")
        rename_btn.setFixedSize(*L.ICON_BTN_SIZE)
        rename_btn.setToolTip("重命名会话")
        rename_btn.setStyleSheet(_icon_style)
        rename_btn.setFont(_emoji_font)
        rename_btn.clicked.connect(self._rename_current_session)
        layout.addWidget(rename_btn)

        # ⚙ 会话设置按钮
        self._session_cfg_btn = QPushButton("⚙")
        self._session_cfg_btn.setFixedSize(*L.ICON_BTN_SIZE)
        self._session_cfg_btn.setToolTip("会话设置")
        self._session_cfg_btn.setStyleSheet(_icon_style)
        self._session_cfg_btn.setFont(_emoji_font)
        self._session_cfg_btn.clicked.connect(self._open_session_config)
        layout.addWidget(self._session_cfg_btn)

        layout.addStretch()

        # 模型 ComboBox：可编辑模式下 Qt 独立计算下拉列表宽度，模型名不会被截断
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._model_combo.setMinimumContentsLength(L.MODEL_COMBO_MIN_CHARS)
        self._model_combo.setMaximumWidth(L.MODEL_COMBO_MAX_WIDTH)
        self._refresh_model_combo()
        layout.addWidget(self._model_combo)

        # ⚙ 全局设置：用 QToolButton 避免继承主窗口 Tab QSS
        self._settings_btn = QToolButton()
        self._settings_btn.setText("⚙ 全局设置")
        self._settings_btn.setToolTip("全局 AI 设置（API Key、默认模型、Temperature 等）")
        self._settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_btn)

        # 初始化会话标题
        self._update_session_title()

        # 把 toolbar + 底部分隔线包进一个容器，统一返回
        wrapper = QWidget()
        wrapper.setObjectName("ToolbarWrapper")
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(bar)
        self._toolbar_divider = QFrame()
        self._toolbar_divider.setObjectName("ToolbarDivider")
        self._toolbar_divider.setFrameShape(QFrame.Shape.HLine)
        self._toolbar_divider.setFixedHeight(L.TOOLBAR_DIVIDER_HEIGHT)
        self._apply_toolbar_divider_style()
        wrapper_layout.addWidget(self._toolbar_divider)
        return wrapper

    def _apply_toolbar_divider_style(self):
        """更新 toolbar 底部分隔线颜色（主题切换时调用）。"""
        self._toolbar_divider.setStyleSheet(
            f"QFrame#ToolbarDivider {{ background-color: {chat_colors().divider}; border: none; }}"
        )

    def _build_chat_area(self) -> QWidget:
        self._scroll = ChatScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._chat_container = _ChatContainer()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(*L.CHAT_MARGINS)
        self._chat_layout.setSpacing(L.CHAT_SPACING)
        self._chat_layout.addStretch()

        self._scroll.setWidget(self._chat_container)
        return self._scroll

    def _build_input_area(self) -> QWidget:
        area = QWidget()
        area.setObjectName("InputArea")
        layout = QHBoxLayout(area)
        layout.setContentsMargins(*L.INPUT_MARGINS)
        layout.setSpacing(L.INPUT_SPACING)

        self._input_box = QTextEdit()
        self._input_box.setAcceptRichText(False)   # 粘贴时自动剥离格式，只保留纯文本
        self._input_box.setMinimumHeight(60)       # 最小高度，防止拖到消失
        self._input_box.setPlaceholderText("输入消息... (Ctrl+Enter 发送，Enter 换行)")
        self._input_box.installEventFilter(self)
        layout.addWidget(self._input_box, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(*L.SEND_BTN_SIZE)
        self._send_btn.clicked.connect(self._send_message)
        layout.addWidget(self._send_btn)

        return area

    def _save_input_height(self):
        """拖动输入区分隔线后持久化输入区高度。"""
        sizes = self._input_splitter.sizes()
        if len(sizes) >= 2:
            QSettings("CATIACopilot", "AIChatPanel").setValue("input_height", sizes[1])

    # ── 会话管理 ──────────────────────────────────────────────────────────────

    def invalidate_current_session(self):
        """将当前会话置为 None（草稿状态），供 SessionSidebar 在删除当前会话时调用。

        不直接暴露 _current_session 属性，避免跨类的私有属性访问。
        """
        self._current_session = None

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
        self._pending_tool_call = None

    def _rebuild_chat_widgets(self):
        """根据当前会话的 messages 重建聊天区 widget。"""
        self._clear_chat_widgets()
        # 重建后需要滚到底部，提前把标志置 True，
        # 避免上一个会话停留在中间位置时 rangeChanged 不跟随
        self._scroll.reset_to_bottom()
        messages = self._current_session.messages

        # 建立 tool_call_id → tool 结果的映射，供重建 ToolCallWidget 时填入结果
        tool_results: dict[str, str] = {}
        for m in messages:
            if m.get("role") == "tool":
                tool_results[m.get("tool_call_id", "")] = m.get("content", "")

        # 过滤出需要渲染的消息（跳过 system），只取最近 60 条原始消息
        visible = [m for m in messages if m.get("role") != "system"][-60:]

        for msg in visible:
            role = msg.get("role")

            if role == "user":
                content = msg.get("content") or ""
                if content:
                    w = UserMessageWidget(content, self._chat_container)
                    self._insert_widget(w)

            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                content = msg.get("content") or ""

                # 先渲染 assistant 文字（若有）
                if content.strip():
                    w = AIMessageWidget(self._chat_container)
                    # 先插入布局再设置文字，避免 set_final_text 内的 show()
                    # 在 widget 无父级时创建临时顶层窗口
                    self._insert_widget(w)
                    w.set_final_text(content)

                # 再渲染工具调用卡片（若有），默认折叠，填入已有结果
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args = fn.get("arguments", "")
                        tc_id = tc.get("id", "")
                        card = ToolCallWidget(name, args, self._chat_container)
                        result = tool_results.get(tc_id, "")
                        if result:
                            card.set_result(result)  # 复用 set_result 的格式化逻辑
                        self._insert_widget(card)

            # role == "tool" 已通过 tool_results 映射处理，不单独渲染

        # 重建完成后滚到底部
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

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
        sys_prompt = cfg.get("system_prompt", "").strip()

        # 用户未配置 system_prompt 时，使用内置默认值
        if not sys_prompt:
            from catia_copilot.ai.tools import DEFAULT_SYSTEM_PROMPT
            sys_prompt = DEFAULT_SYSTEM_PROMPT

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
            # 截断后确保不以孤立的 tool 消息开头：
            # 若第一条是 role=tool，说明对应的 assistant(tool_calls) 被截掉了，
            # 继续向前丢弃直到第一条非 tool 消息，避免 API 返回 400
            while non_system and non_system[0].get("role") == "tool":
                non_system = non_system[1:]

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
        # per-session temperature 覆盖全局（此处 _current_session 必然非 None）
        session_temp = self._current_session.config.get("temperature")
        if session_temp is not None:
            cfg["temperature"] = session_temp

        # 创建 AI 消息占位 widget
        self._current_ai_widget = AIMessageWidget(self._chat_container)
        self._insert_widget(self._current_ai_widget)

        # 启动 AgentWorker
        self._worker = AgentWorker(messages_for_llm, cfg)
        self._worker.token_received.connect(self._on_token)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_progress.connect(self._on_tool_progress)
        self._worker.tool_finished.connect(self._on_tool_finished)
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
            # 懒插入：工具调用后创建的占位 widget 在第一个 token 到来时才插入布局
            if not self._current_ai_widget.is_inserted:
                self._insert_widget(self._current_ai_widget)
            self._current_ai_widget.append_token(token)

    @Slot(str, str, str)
    def _on_tool_started(self, tool_name: str, args_str: str, tool_call_id: str):
        # 工具调用开始前，先把 AI 已输出的文字 buffer 写入 session 并渲染
        # （保证 session 里 assistant content 在 assistant tool_calls 之前）
        if self._current_ai_widget and self._current_ai_widget._buffer.strip():
            buf = self._current_ai_widget._buffer
            self._current_ai_widget.set_final_text(buf)
            if self._current_session is not None:
                self._current_session.messages.append(
                    {"role": "assistant", "content": buf}
                )
        # 无论有无 buffer，当前 ai_widget 使命结束，置 None
        # （_on_tool_finished 会创建新的占位 widget）
        self._current_ai_widget = None

        # 使用 AgentWorker 传来的真实 tool_call_id，保证与 tool 结果消息的 id 一致
        self._pending_tool_call = {
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "arguments": args_str,
        }
        # 写入 assistant(tool_calls) 消息到 session
        if self._current_session is not None:
            self._current_session.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args_str},
                }],
            })

        self._current_tool_widget = ToolCallWidget(
            tool_name, args_str, self._chat_container
        )
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

        # 写入 tool 结果消息到 session
        if self._current_session is not None and self._pending_tool_call is not None:
            self._current_session.messages.append({
                "role": "tool",
                "tool_call_id": self._pending_tool_call["tool_call_id"],
                "content": result,
            })
        self._pending_tool_call = None

        # _on_tool_started 已处理并清空了 _current_ai_widget，
        # 这里创建新的占位 widget 等待下一轮 AI 文字（懒插入）
        self._current_ai_widget = AIMessageWidget(self._chat_container)

    @Slot(str)
    def _on_all_done(self, final_text: str):
        if self._current_ai_widget:
            if final_text.strip():
                # 懒插入：若 widget 尚未插入布局，先插入再渲染
                if not self._current_ai_widget.is_inserted:
                    self._insert_widget(self._current_ai_widget)
                self._current_ai_widget.set_final_text(final_text)
            else:
                # 无文字：无论是否已插入，都清理掉（已插入的是空 widget，隐藏即可）
                if not self._current_ai_widget.is_inserted:
                    self._current_ai_widget.deleteLater()
                # 已插入但无文字：_set_visible(False) 让它不占空间
                else:
                    self._current_ai_widget._set_visible(False)
            self._current_ai_widget = None

        # 追加 assistant 消息到会话历史（空文字不写入，避免部分模型报错）
        if self._current_session is not None:
            if final_text.strip():
                self._current_session.messages.append(
                    {"role": "assistant", "content": final_text}
                )
            # 无论有无文字都保存：本轮可能有 tool 消息需要持久化
            self._sm.save_session(self._current_session)
            self._sidebar.refresh(self._current_session.session_id)

        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")

    @Slot(str)
    def _on_error(self, error_msg: str):
        err_widget = AIMessageWidget(self._chat_container)
        err_widget.set_final_text(f"**错误：** {error_msg}")
        self._insert_widget(err_widget)

        self._current_ai_widget = None
        self._current_tool_widget = None
        self._pending_tool_call = None
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
        except json.JSONDecodeError as e:
            result = json.dumps(
                {"error": f"工具参数 JSON 解析失败：{e}，原始参数：{args_str[:200]}"},
                ensure_ascii=False,
            )
            self._worker.receive_tool_result(result)
            return

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
            # 工具函数应返回 JSON 字符串；若返回其他类型则强制序列化，
            # 防止 Signal(str) 在传递非字符串时抛出 TypeError
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            result = json.dumps(
                {"error": str(e), "traceback": traceback.format_exc()},
                ensure_ascii=False,
            )

        self._worker.receive_tool_result(result)

    # ── UI 辅助 ───────────────────────────────────────────────────────────────

    def _add_user_message(self, text: str):
        widget = UserMessageWidget(text, self._chat_container)
        self._insert_widget(widget)

    def _insert_widget(self, widget: QWidget):
        """在 stretch 之前插入消息 widget。

        ToolCallWidget 会自动包一层透明 wrapper 并应用 TOOL_CARD_OUTER_MARGINS，
        实现卡片外框到聊天区域边缘的外边距（Qt 没有直接的 widget 外边距 API）。
        """
        if isinstance(widget, ToolCallWidget):
            wrapper = QWidget(self._chat_container)
            wrapper.setStyleSheet("background: transparent;")
            wl = QVBoxLayout(wrapper)
            wl.setContentsMargins(*L.TOOL_CARD_OUTER_MARGINS)
            wl.setSpacing(0)
            wl.addWidget(widget)
            insert_target = wrapper
        else:
            insert_target = widget

        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, insert_target)
        # 标记 AIMessageWidget 已插入，供懒插入判断使用
        if isinstance(widget, AIMessageWidget):
            widget.is_inserted = True

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
        self._apply_toolbar_divider_style()
