"""
主题管理器：支持手动切换深色 / 浅色主题，偏好持久化到 QSettings。

- 启动时读取 QSettings 中保存的手动偏好；若无则跟随系统主题
- toggle() 在深/浅色之间切换并写入 QSettings，下次启动自动恢复
- 系统主题变化时，仅在无手动偏好的情况下自动跟随
- QSS 通过 QApplication.setStyleSheet() 全局应用，对话框等顶层窗口均生效
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


class _ThemeSignalEmitter(QObject):
    """向外广播主题切换事件的轻量信号发射器。"""
    #: 切换完成后发出，携带新主题名称 "dark" 或 "light"
    theme_changed = Signal(str)


#: 全局单例，供 dialog 等订阅主题切换：
#:   from catia_copilot.ui.theme_manager import theme_signal
#:   theme_signal.theme_changed.connect(my_slot)
theme_signal = _ThemeSignalEmitter()

# ══════════════════════════════════════════════════════════════════════════════
#  深色主题 QSS
# ══════════════════════════════════════════════════════════════════════════════
DARK_QSS = """
/* ═══════════════════════════════════════════════════════════════════
   CATIA Copilot – Fluent Design Dark Theme
   ═══════════════════════════════════════════════════════════════════ */

* { outline: none; }

QWidget {
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
    margin: 4px 0 0 0;
    min-height: 32px;
    min-width: 0;
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

/* ── 标题栏 caption 按钮（≡ / 最小化 / 最大化）Win11 原生规格 ───── */
QPushButton#titleBarMoreBtn,
QPushButton#titleBarMinBtn,
QPushButton#titleBarMaxBtn {
    background: transparent;
    border: none;
    border-radius: 0px;
    color: #c0c0c0;
    font-size: 10pt;
    padding: 0;
    min-width: 46px;  max-width: 46px;
    min-height: 40px; max-height: 40px;
}
QPushButton#titleBarMoreBtn:hover,
QPushButton#titleBarMinBtn:hover,
QPushButton#titleBarMaxBtn:hover {
    background-color: rgba(255, 255, 255, 26);
    color: #ffffff;
}
QPushButton#titleBarMoreBtn:pressed,
QPushButton#titleBarMinBtn:pressed,
QPushButton#titleBarMaxBtn:pressed {
    background-color: rgba(255, 255, 255, 18);
    color: #d0d0d0;
}

/* ── 关闭按钮（红色 hover）───────────────────────────────────────── */
QPushButton#titleBarCloseBtn {
    background: transparent;
    border: none;
    border-radius: 0px;
    color: #c0c0c0;
    font-size: 10pt;
    padding: 0;
    min-width: 46px;  max-width: 46px;
    min-height: 40px; max-height: 40px;
}
QPushButton#titleBarCloseBtn:hover {
    background-color: #c42b1c;
    color: #ffffff;
}
QPushButton#titleBarCloseBtn:pressed {
    background-color: #a82315;
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

/* ── 日志面板（深色主题）──────────────────────────────────────────── */
QWidget#logPanel {
    background-color: #141414;
    border-top: 1px solid #333333;
}
QPlainTextEdit#logView {
    background-color: #141414;
    color: #d4d4d4;
    border: none;
    border-radius: 0px;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 9pt;
}
QLabel#logPathLabel {
    color: #555555;
    font-size: 8.5pt;
    background: transparent;
}
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
    margin: 4px 0 0 0;
    min-height: 32px;
    min-width: 0;
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

/* ── 标题栏 caption 按钮（≡ / 最小化 / 最大化）Win11 原生规格 ───── */
QPushButton#titleBarMoreBtn,
QPushButton#titleBarMinBtn,
QPushButton#titleBarMaxBtn {
    background: transparent;
    border: none;
    border-radius: 0px;
    color: #505050;
    font-size: 10pt;
    padding: 0;
    min-width: 46px;  max-width: 46px;
    min-height: 40px; max-height: 40px;
}
QPushButton#titleBarMoreBtn:hover,
QPushButton#titleBarMinBtn:hover,
QPushButton#titleBarMaxBtn:hover {
    background-color: rgba(0, 0, 0, 26);
    color: #191919;
}
QPushButton#titleBarMoreBtn:pressed,
QPushButton#titleBarMinBtn:pressed,
QPushButton#titleBarMaxBtn:pressed {
    background-color: rgba(0, 0, 0, 18);
    color: #191919;
}

/* ── 关闭按钮（红色 hover）───────────────────────────────────────── */
QPushButton#titleBarCloseBtn {
    background: transparent;
    border: none;
    border-radius: 0px;
    color: #505050;
    font-size: 10pt;
    padding: 0;
    min-width: 46px;  max-width: 46px;
    min-height: 40px; max-height: 40px;
}
QPushButton#titleBarCloseBtn:hover {
    background-color: #c42b1c;
    color: #ffffff;
}
QPushButton#titleBarCloseBtn:pressed {
    background-color: #a82315;
    color: #ffffff;
}

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

/* ── 日志面板（浅色主题）──────────────────────────────────────────── */
QWidget#logPanel {
    background-color: #f3f3f3;
    border-top: 1px solid #e0e0e0;
}
QPlainTextEdit#logView {
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #e0e0e0;
    border-radius: 3px;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 9pt;
}
QLabel#logPathLabel {
    color: #888888;
    font-size: 8.5pt;
    background: transparent;
}
"""


class ThemeManager:
    """跟随系统主题 / 手动切换的主窗口 QSS 管理器（单例）。

    - 启动时读取 QSettings 中保存的手动偏好；若无则读取系统主题
    - toggle() 在深/浅色之间切换并持久化到 QSettings
    - 系统主题变化时：仅在无手动偏好时自动跟随
    """

    _SETTINGS_ORG  = "CATIACopilot"
    _SETTINGS_APP  = "theme"
    _SETTINGS_KEY  = "mode"

    _instance: "ThemeManager | None" = None
    _window = None
    _manual: str | None = None   # None=跟随系统; "dark"/"light"=手动覆盖

    def __new__(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, window) -> None:
        """注册主窗口，立即应用主题，并监听后续系统主题变化。"""
        self._window = window
        # 读取上次保存的手动偏好
        saved = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP).value(
            self._SETTINGS_KEY, None
        )
        if saved in ("dark", "light"):
            self._manual = saved
        self._apply()
        # 系统主题变化时重新应用（_apply 内部会判断是否有手动覆盖）
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: self._apply()
        )

    def toggle(self) -> None:
        """在深色 / 浅色之间切换并持久化。"""
        new_mode = "light" if self._current_mode() == "dark" else "dark"
        self._manual = new_mode
        QSettings(self._SETTINGS_ORG, self._SETTINGS_APP).setValue(
            self._SETTINGS_KEY, new_mode
        )
        self._apply()

    def current_mode(self) -> str:
        """返回当前生效的主题名称：'dark' 或 'light'。"""
        return self._current_mode()

    def _current_mode(self) -> str:
        if self._manual:
            return self._manual
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _apply(self) -> None:
        """根据当前主题选择 QSS 并通过 QApplication 全局应用（所有窗口均生效）。"""
        if self._window is None:
            # 尚未调用 register()，暂不应用
            return
        mode = self._current_mode()
        qss = DARK_QSS if mode == "dark" else LIGHT_QSS
        # 应用到整个 application，所有顶层窗口（包括 QDialog）均生效
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss)
        # 通知订阅者（如 dialog 中的行着色逻辑）主题已切换
        theme_signal.theme_changed.emit(mode)


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
