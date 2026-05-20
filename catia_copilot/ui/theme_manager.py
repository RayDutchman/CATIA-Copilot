"""
主题管理器：Fluent Design 深色 / 浅色主题切换与持久化。

使用 QSettings 存储用户偏好，运行时动态更新 QApplication 全局样式表。
无需第三方依赖，全部通过自定义 QSS 实现 Windows 11 Fluent Design 风格。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

# ── QSettings 标识 ─────────────────────────────────────────────────────────
_ORG     = "ChenWeibo"
_APP     = "CATIACopilot"
_KEY     = "ui/theme"
_DEFAULT = "dark"

# ══════════════════════════════════════════════════════════════════════════════
#  深色主题 QSS
# ══════════════════════════════════════════════════════════════════════════════
DARK_QSS = """
/* ═══════════════════════════════════════════════════════════════════
   CATIA Copilot – Fluent Design Dark Theme
   ═══════════════════════════════════════════════════════════════════ */

* { outline: none; }

QWidget {
    font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 9pt;
    color: #e8e8e8;
    background-color: transparent;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}
QMainWindow { background-color: #1c1c1c; }
QDialog     { background-color: #202020; }

/* ── 标题栏 ──────────────────────────────────────────────────────── */
QWidget#titleBar {
    background-color: #2b2b2b;
    border-bottom: 1px solid #383838;
}
QLabel#titleBarTitle {
    color: #e0e0e0;
    font-size: 9pt;
    font-weight: 500;
    background: transparent;
}

/* ── Tab 按钮 ────────────────────────────────────────────────────── */
QPushButton#titleTabButton {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    color: #909090;
    font-size: 9pt;
    padding: 0 16px;
    margin: 4px 1px 0 1px;
    min-height: 32px;
}
QPushButton#titleTabButton:hover {
    background-color: #363636;
    color: #e0e0e0;
}
QPushButton#titleTabButton:checked {
    border-bottom: 2px solid #0078d4;
    color: #e0e0e0;
    background-color: #323232;
    font-weight: 600;
}

/* ── 标题栏控制按钮（最小化 / 最大化）────────────────────────────── */
QPushButton#titleBarMinBtn,
QPushButton#titleBarMaxBtn {
    background: transparent;
    border: none;
    color: #c0c0c0;
    font-size: 10pt;
    border-radius: 0px;
    min-width: 46px;
}
QPushButton#titleBarMinBtn:hover,
QPushButton#titleBarMaxBtn:hover {
    background-color: #444444;
    color: #ffffff;
}
QPushButton#titleBarMinBtn:pressed,
QPushButton#titleBarMaxBtn:pressed { background-color: #555555; }

/* ── 关闭按钮（红色 hover）───────────────────────────────────────── */
QPushButton#titleBarCloseBtn {
    background: transparent;
    border: none;
    color: #c0c0c0;
    font-size: 10pt;
    border-radius: 0px;
    min-width: 46px;
}
QPushButton#titleBarCloseBtn:hover {
    background-color: #c42b1c;
    color: #ffffff;
}
QPushButton#titleBarCloseBtn:pressed { background-color: #b52015; }

/* ── 更多菜单 / 主题按钮 ─────────────────────────────────────────── */
QPushButton#titleBarMoreBtn,
QPushButton#titleBarThemeBtn {
    background: transparent;
    border: none;
    color: #c0c0c0;
    font-size: 11pt;
    border-radius: 4px;
    min-width: 32px;
}
QPushButton#titleBarMoreBtn:hover,
QPushButton#titleBarThemeBtn:hover {
    background-color: #444444;
    color: #ffffff;
}

/* ── 分组框（卡片）────────────────────────────────────────────────── */
QGroupBox {
    background-color: #252525;
    border: 1px solid #363636;
    border-radius: 6px;
    margin-top: 10px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
    color: #e0e0e0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #808080;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 0.8px;
}

/* ── 普通按钮 ──────────────────────────────────────────────────────── */
QPushButton {
    background-color: #3c3c3c;
    border: 1px solid #505050;
    border-radius: 4px;
    color: #e8e8e8;
    padding: 5px 14px;
    min-height: 26px;
    font-size: 9pt;
    text-align: left;
    padding-left: 10px;
}
QPushButton:hover {
    background-color: #484848;
    border-color: #626262;
}
QPushButton:pressed {
    background-color: #2e2e2e;
    border-color: #444444;
}
QPushButton:disabled {
    color: #555555;
    background-color: #2a2a2a;
    border-color: #383838;
}
QPushButton:default {
    border: 1px solid #0078d4;
    background-color: #0078d4;
    color: #ffffff;
    text-align: center;
}
QPushButton:default:hover  { background-color: #1a8fe3; border-color: #1a8fe3; }
QPushButton:default:pressed { background-color: #006cc1; }

/* ── 节标题标签 ─────────────────────────────────────────────────────── */
QLabel#sectionLabel {
    color: #606060;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 6px 0 2px 0;
    background: transparent;
}

/* ── 表格 ──────────────────────────────────────────────────────────── */
QTableWidget {
    background-color: #1e1e1e;
    gridline-color: #2e2e2e;
    border: 1px solid #363636;
    border-radius: 4px;
    selection-background-color: #004578;
    selection-color: #ffffff;
    alternate-background-color: #222222;
}
QHeaderView::section {
    background-color: #2a2a2a;
    color: #808080;
    border: none;
    border-right: 1px solid #363636;
    border-bottom: 1px solid #363636;
    padding: 5px 8px;
    font-weight: 700;
    font-size: 8pt;
    letter-spacing: 0.3px;
}
QTableWidget QTableCornerButton::section {
    background-color: #2a2a2a;
    border: none;
}

/* ── 文本输入 ──────────────────────────────────────────────────────── */
QLineEdit {
    background-color: #2a2a2a;
    border: 1px solid #505050;
    border-radius: 4px;
    color: #e8e8e8;
    padding: 4px 8px;
    min-height: 22px;
}
QLineEdit:focus    { border-color: #0078d4; background-color: #2e2e2e; }
QLineEdit:read-only { background-color: #242424; color: #808080; border-color: #3a3a3a; }

/* ── 下拉框 ────────────────────────────────────────────────────────── */
QComboBox {
    background-color: #2a2a2a;
    border: 1px solid #505050;
    border-radius: 4px;
    color: #e8e8e8;
    padding: 4px 8px;
    min-height: 22px;
}
QComboBox:hover  { border-color: #646464; }
QComboBox:focus  { border-color: #0078d4; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #808080;
    width: 0; height: 0; margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #2a2a2a;
    border: 1px solid #505050;
    border-radius: 4px;
    selection-background-color: #004578;
    color: #e8e8e8;
    outline: none;
}

/* ── 复选框 ────────────────────────────────────────────────────────── */
QCheckBox { color: #e8e8e8; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #505050;
    border-radius: 3px;
    background-color: #2a2a2a;
}
QCheckBox::indicator:hover   { border-color: #0078d4; }
QCheckBox::indicator:checked { background-color: #0078d4; border-color: #0078d4; }

/* ── 状态栏 ────────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #202020;
    color: #606060;
    border-top: 1px solid #2e2e2e;
    font-size: 8.5pt;
}
QStatusBar::item { border: none; }

/* ── CATIA 连接状态指示器 ───────────────────────────────────────────── */
QLabel#catiaStatusLabel { padding: 0 8px; font-size: 8.5pt; color: #606060; }
QLabel#catiaStatusLabel[catiaConnected="true"]   { color: #4ec94e; }
QLabel#catiaStatusLabel[catiaConnected="false"]  { color: #f04b4b; }
QLabel#catiaStatusLabel[catiaConnected="broken"] { color: #e0a030; }

/* ── 滚动条（细条风格）─────────────────────────────────────────────── */
QScrollBar:vertical   { background: transparent; width: 8px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #505050; border-radius: 4px; min-height: 24px; min-width: 24px;
}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover { background: #686868; }
QScrollBar::add-line:vertical,  QScrollBar::sub-line:vertical  { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── 文本编辑（日志窗口）──────────────────────────────────────────── */
QTextEdit {
    background-color: #1a1a1a;
    color: #c0c0c0;
    border: 1px solid #363636;
    border-radius: 4px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 8.5pt;
}

/* ── 菜单 ──────────────────────────────────────────────────────────── */
QMenu {
    background-color: #282828;
    border: 1px solid #404040;
    border-radius: 6px;
    padding: 4px 0;
    color: #e0e0e0;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 3px;
    margin: 1px 4px;
}
QMenu::item:selected { background-color: #004578; color: #ffffff; }
QMenu::separator { height: 1px; background-color: #363636; margin: 3px 8px; }

/* ── 进度条 ────────────────────────────────────────────────────────── */
QProgressBar {
    background-color: #2a2a2a;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    text-align: center;
    color: #e0e0e0;
    font-size: 8.5pt;
    min-height: 6px;
}
QProgressBar::chunk { background-color: #0078d4; border-radius: 3px; }

/* ── 树控件 ────────────────────────────────────────────────────────── */
QTreeWidget {
    background-color: #1e1e1e;
    border: 1px solid #363636;
    border-radius: 4px;
    alternate-background-color: #222222;
}
QTreeWidget::item:selected { background-color: #004578; color: #ffffff; }
QTreeWidget::item:hover    { background-color: #2a2a2a; }

/* ── 对话框内部 ────────────────────────────────────────────────────── */
QDialog QGroupBox { background-color: #242424; }
"""

# ══════════════════════════════════════════════════════════════════════════════
#  浅色主题 QSS
# ══════════════════════════════════════════════════════════════════════════════
LIGHT_QSS = """
/* ═══════════════════════════════════════════════════════════════════
   CATIA Copilot – Fluent Design Light Theme
   ═══════════════════════════════════════════════════════════════════ */

* { outline: none; }

QWidget {
    font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 9pt;
    color: #1a1a1a;
    background-color: transparent;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}
QMainWindow { background-color: #f3f3f3; }
QDialog     { background-color: #f5f5f5; }

/* ── 标题栏 ──────────────────────────────────────────────────────── */
QWidget#titleBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e0e0e0;
}
QLabel#titleBarTitle {
    color: #1a1a1a;
    font-size: 9pt;
    font-weight: 500;
    background: transparent;
}

/* ── Tab 按钮 ────────────────────────────────────────────────────── */
QPushButton#titleTabButton {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    color: #606060;
    font-size: 9pt;
    padding: 0 16px;
    margin: 4px 1px 0 1px;
    min-height: 32px;
}
QPushButton#titleTabButton:hover {
    background-color: #f0f0f0;
    color: #1a1a1a;
}
QPushButton#titleTabButton:checked {
    border-bottom: 2px solid #0078d4;
    color: #0078d4;
    background-color: #f5f5f5;
    font-weight: 600;
}

/* ── 标题栏控制按钮 ───────────────────────────────────────────────── */
QPushButton#titleBarMinBtn,
QPushButton#titleBarMaxBtn {
    background: transparent;
    border: none;
    color: #505050;
    font-size: 10pt;
    border-radius: 0px;
    min-width: 46px;
}
QPushButton#titleBarMinBtn:hover,
QPushButton#titleBarMaxBtn:hover { background-color: #e0e0e0; color: #1a1a1a; }

QPushButton#titleBarCloseBtn {
    background: transparent;
    border: none;
    color: #505050;
    font-size: 10pt;
    border-radius: 0px;
    min-width: 46px;
}
QPushButton#titleBarCloseBtn:hover { background-color: #c42b1c; color: #ffffff; }
QPushButton#titleBarCloseBtn:pressed { background-color: #b52015; }

QPushButton#titleBarMoreBtn,
QPushButton#titleBarThemeBtn {
    background: transparent;
    border: none;
    color: #505050;
    font-size: 11pt;
    border-radius: 4px;
    min-width: 32px;
}
QPushButton#titleBarMoreBtn:hover,
QPushButton#titleBarThemeBtn:hover { background-color: #e0e0e0; color: #1a1a1a; }

/* ── 分组框（卡片）────────────────────────────────────────────────── */
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    margin-top: 10px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
    color: #1a1a1a;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #888888;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 0.8px;
}

/* ── 普通按钮 ──────────────────────────────────────────────────────── */
QPushButton {
    background-color: #f5f5f5;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
    color: #1a1a1a;
    padding: 5px 14px;
    min-height: 26px;
    font-size: 9pt;
    text-align: left;
    padding-left: 10px;
}
QPushButton:hover   { background-color: #e8e8e8; border-color: #b8b8b8; }
QPushButton:pressed { background-color: #d8d8d8; border-color: #aaaaaa; }
QPushButton:disabled { color: #b0b0b0; background-color: #f0f0f0; border-color: #e0e0e0; }
QPushButton:default {
    border: 1px solid #0078d4;
    background-color: #0078d4;
    color: #ffffff;
    text-align: center;
}
QPushButton:default:hover   { background-color: #1a8fe3; border-color: #1a8fe3; }
QPushButton:default:pressed { background-color: #006cc1; }

/* ── 节标题标签 ─────────────────────────────────────────────────────── */
QLabel#sectionLabel {
    color: #909090;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 6px 0 2px 0;
    background: transparent;
}

/* ── 表格 ──────────────────────────────────────────────────────────── */
QTableWidget {
    background-color: #ffffff;
    gridline-color: #e8e8e8;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    selection-background-color: #cce4ff;
    selection-color: #000000;
    alternate-background-color: #f8f8f8;
}
QHeaderView::section {
    background-color: #f0f0f0;
    color: #606060;
    border: none;
    border-right: 1px solid #e0e0e0;
    border-bottom: 1px solid #e0e0e0;
    padding: 5px 8px;
    font-weight: 700;
    font-size: 8pt;
}
QTableWidget QTableCornerButton::section { background-color: #f0f0f0; border: none; }

/* ── 文本输入 ──────────────────────────────────────────────────────── */
QLineEdit {
    background-color: #ffffff;
    border: 1px solid #cccccc;
    border-radius: 4px;
    color: #1a1a1a;
    padding: 4px 8px;
    min-height: 22px;
}
QLineEdit:focus    { border-color: #0078d4; }
QLineEdit:read-only { background-color: #f5f5f5; color: #888888; border-color: #e0e0e0; }

/* ── 下拉框 ────────────────────────────────────────────────────────── */
QComboBox {
    background-color: #ffffff;
    border: 1px solid #cccccc;
    border-radius: 4px;
    color: #1a1a1a;
    padding: 4px 8px;
    min-height: 22px;
}
QComboBox:hover { border-color: #aaaaaa; }
QComboBox:focus { border-color: #0078d4; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #606060;
    width: 0; height: 0; margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #cccccc;
    border-radius: 4px;
    selection-background-color: #cce4ff;
    color: #1a1a1a;
    outline: none;
}

/* ── 复选框 ────────────────────────────────────────────────────────── */
QCheckBox { color: #1a1a1a; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #cccccc;
    border-radius: 3px;
    background-color: #ffffff;
}
QCheckBox::indicator:hover   { border-color: #0078d4; }
QCheckBox::indicator:checked { background-color: #0078d4; border-color: #0078d4; }

/* ── 状态栏 ────────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #f3f3f3;
    color: #888888;
    border-top: 1px solid #e0e0e0;
    font-size: 8.5pt;
}
QStatusBar::item { border: none; }

/* ── CATIA 连接状态指示器 ───────────────────────────────────────────── */
QLabel#catiaStatusLabel { padding: 0 8px; font-size: 8.5pt; color: #888888; }
QLabel#catiaStatusLabel[catiaConnected="true"]   { color: #2a9d2a; }
QLabel#catiaStatusLabel[catiaConnected="false"]  { color: #cc2222; }
QLabel#catiaStatusLabel[catiaConnected="broken"] { color: #c97a00; }

/* ── 滚动条（细条风格）─────────────────────────────────────────────── */
QScrollBar:vertical   { background: transparent; width: 8px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #c8c8c8; border-radius: 4px; min-height: 24px; min-width: 24px;
}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover { background: #aaaaaa; }
QScrollBar::add-line:vertical,  QScrollBar::sub-line:vertical  { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── 文本编辑（日志窗口）──────────────────────────────────────────── */
QTextEdit {
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 8.5pt;
}

/* ── 菜单 ──────────────────────────────────────────────────────────── */
QMenu {
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 4px 0;
    color: #1a1a1a;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 3px;
    margin: 1px 4px;
}
QMenu::item:selected { background-color: #cce4ff; color: #000000; }
QMenu::separator { height: 1px; background-color: #e8e8e8; margin: 3px 8px; }

/* ── 进度条 ────────────────────────────────────────────────────────── */
QProgressBar {
    background-color: #e8e8e8;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
    text-align: center;
    color: #1a1a1a;
    font-size: 8.5pt;
    min-height: 6px;
}
QProgressBar::chunk { background-color: #0078d4; border-radius: 3px; }

/* ── 树控件 ────────────────────────────────────────────────────────── */
QTreeWidget {
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    alternate-background-color: #f8f8f8;
}
QTreeWidget::item:selected { background-color: #cce4ff; color: #000000; }
QTreeWidget::item:hover    { background-color: #f0f0f0; }

/* ── 对话框内部 ────────────────────────────────────────────────────── */
QDialog QGroupBox { background-color: #f8f8f8; }
"""


class ThemeManager:
    """全局主题管理器（单例）。

    职责：
    - 从 QSettings 读取上次保存的主题
    - 调用 QApplication.setStyleSheet() 应用对应 QSS
    - 提供 toggle() 方法在深色/浅色之间切换
    """

    _instance: "ThemeManager | None" = None
    _is_dark: bool = True

    def __new__(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_saved_theme(self) -> None:
        """从 QSettings 读取上次保存的主题偏好并立即应用。"""
        settings = QSettings(_ORG, _APP)
        theme = settings.value(_KEY, _DEFAULT)
        self._is_dark = (theme == "dark")
        self._apply()

    def toggle(self) -> bool:
        """切换深色 / 浅色主题，持久化偏好，返回切换后是否为深色。"""
        self._is_dark = not self._is_dark
        settings = QSettings(_ORG, _APP)
        settings.setValue(_KEY, "dark" if self._is_dark else "light")
        self._apply()
        return self._is_dark

    @property
    def is_dark(self) -> bool:
        """当前是否为深色主题。"""
        return self._is_dark

    def _apply(self) -> None:
        """将对应主题的 QSS 设置到 QApplication 全局样式表。"""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(DARK_QSS if self._is_dark else LIGHT_QSS)


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
