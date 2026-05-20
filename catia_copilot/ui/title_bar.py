"""
自定义标题栏组件（无边框窗口专用）。

布局（左 → 右）：
  [应用图标] [标题]  [导出][BOM][图纸][工具]  ──弹性空白──  [≡][🌙/☀][─][□][✕]

功能：
- 窗口拖拽（鼠标拖拽标题栏区域移动窗口）
- 双击标题栏切换最大化 / 还原
- Tab 切换信号（与主窗口 QStackedWidget 联动）
- 主题切换信号
- 更多功能菜单入口（由主窗口填充内容）
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QButtonGroup

from catia_copilot.constants import APP_NAME


class _TabButton(QPushButton):
    """标题栏 Tab 切换按钮（可选中状态）。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("titleTabButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class TitleBar(QWidget):
    """无边框窗口的自定义标题栏。

    信号：
        tab_changed(int)       : 用户切换了 Tab，携带新 Tab 索引
        theme_toggle_requested : 用户点击了主题切换按钮
        more_requested(QPoint) : 用户点击了更多菜单按钮，携带弹出位置（全局坐标）
    """

    tab_changed            = Signal(int)
    theme_toggle_requested = Signal()
    more_requested         = Signal(QPoint)

    # Tab 标签与顺序（须与主窗口 QStackedWidget 页面索引对应）
    TAB_LABELS: list[str] = ["导出", "BOM", "图纸", "工具"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(40)
        # 拖拽辅助变量
        self._drag_pos: QPoint | None = None
        self._build_ui()

    # ── UI 构建 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(0)

        # ── 左：图标 + 标题 ──────────────────────────────────────────────
        self._icon_label = QLabel()
        self._icon_label.setObjectName("titleBarIcon")
        self._icon_label.setFixedSize(18, 18)
        self._icon_label.setScaledContents(True)
        layout.addWidget(self._icon_label)

        layout.addSpacing(8)

        self._title_label = QLabel(APP_NAME)
        self._title_label.setObjectName("titleBarTitle")
        layout.addWidget(self._title_label)

        layout.addSpacing(16)

        # ── 中：Tab 按钮组 ───────────────────────────────────────────────
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for i, label in enumerate(self.TAB_LABELS):
            btn = _TabButton(label)
            self._tab_group.addButton(btn, i)
            layout.addWidget(btn)
        # 默认激活第一个 Tab
        self._tab_group.button(0).setChecked(True)
        self._tab_group.idClicked.connect(self.tab_changed)

        # ── 弹性空白 ─────────────────────────────────────────────────────
        layout.addStretch()

        # ── 右：功能按钮 + 窗口控制 ────────────────────────────────────
        # 更多菜单
        self._btn_more = QPushButton("≡")
        self._btn_more.setObjectName("titleBarMoreBtn")
        self._btn_more.setFixedSize(34, 32)
        self._btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_more.setToolTip("更多功能")
        self._btn_more.clicked.connect(self._emit_more_requested)
        layout.addWidget(self._btn_more)

        # 主题切换
        self._btn_theme = QPushButton("🌙")
        self._btn_theme.setObjectName("titleBarThemeBtn")
        self._btn_theme.setFixedSize(34, 32)
        self._btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_theme.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_theme.setToolTip("切换深色 / 浅色主题")
        self._btn_theme.clicked.connect(self.theme_toggle_requested)
        layout.addWidget(self._btn_theme)

        layout.addSpacing(4)

        # 最小化
        self._btn_min = QPushButton("─")
        self._btn_min.setObjectName("titleBarMinBtn")
        self._btn_min.setFixedSize(46, 40)
        self._btn_min.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_min.clicked.connect(lambda: self.window().showMinimized())
        layout.addWidget(self._btn_min)

        # 最大化 / 还原
        self._btn_max = QPushButton("□")
        self._btn_max.setObjectName("titleBarMaxBtn")
        self._btn_max.setFixedSize(46, 40)
        self._btn_max.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_max.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_max.clicked.connect(self._toggle_maximize)
        layout.addWidget(self._btn_max)

        # 关闭
        self._btn_close = QPushButton("✕")
        self._btn_close.setObjectName("titleBarCloseBtn")
        self._btn_close.setFixedSize(46, 40)
        self._btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(lambda: self.window().close())
        layout.addWidget(self._btn_close)

    # ── 公开方法 ───────────────────────────────────────────────────────────

    def set_app_icon(self, icon: QIcon) -> None:
        """设置标题栏左侧应用图标。"""
        pix = icon.pixmap(QSize(16, 16))
        self._icon_label.setPixmap(pix)

    def set_theme_icon(self, is_dark: bool) -> None:
        """根据当前主题更新主题按钮图标（深色 → 显示太阳，浅色 → 显示月亮）。"""
        self._btn_theme.setText("☀" if is_dark else "🌙")

    def set_active_tab(self, index: int) -> None:
        """程序化切换激活的 Tab（不触发 tab_changed 信号）。"""
        btn = self._tab_group.button(index)
        if btn is not None:
            btn.setChecked(True)

    # ── 私有辅助 ───────────────────────────────────────────────────────────

    def _toggle_maximize(self) -> None:
        win = self.window()
        if win.isMaximized():
            win.showNormal()
            self._btn_max.setText("□")
        else:
            win.showMaximized()
            self._btn_max.setText("❐")

    def _emit_more_requested(self) -> None:
        """计算更多菜单的弹出位置（按钮正下方）并发射信号。"""
        pos = self._btn_more.mapToGlobal(
            QPoint(0, self._btn_more.height())
        )
        self.more_requested.emit(pos)

    # ── 鼠标事件（拖拽 + 双击最大化）─────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_pos is not None
        ):
            # 最大化状态拖拽时先还原
            if self.window().isMaximized():
                self.window().showNormal()
                self._btn_max.setText("□")
            self.window().move(
                event.globalPosition().toPoint() - self._drag_pos
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击标题栏区域切换最大化状态。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
        super().mouseDoubleClickEvent(event)
