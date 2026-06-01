"""
主题管理器：完全使用 Windows 原生主题，跟随系统深色/浅色设置。

- 启动时应用 windows11 风格 + native.qss（项目最小覆盖）
- windows11 风格原生支持深色模式，自动读取系统 QPalette，无需手动构建调色板
- 系统深色/浅色切换时自动重新应用，DWM 标题栏颜色同步跟随
- 不再使用 qdarkstyle，不再提供手动主题切换
- QSS 通过 QApplication.setStyleSheet() 全局应用，对话框等顶层窗口均生效
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QObject, Signal, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


class _ThemeSignalEmitter(QObject):
    """向外广播系统主题切换事件的轻量信号发射器。"""
    #: 系统主题变化后发出，携带当前模式 "dark" 或 "light"
    theme_changed = Signal(str)


#: 全局单例，供 dialog 等订阅主题切换：
#:   from catia_copilot.ui.theme_manager import theme_signal
#:   theme_signal.theme_changed.connect(my_slot)
theme_signal = _ThemeSignalEmitter()


# QSS 从同目录的独立文件加载，模块导入时读取一次
# 文件缺失时回退空字符串，样式降级为系统原生，程序仍可正常启动
_UI_DIR = Path(__file__).parent

# ── 日志字体常量（native.qss 占位符替换用）──────────────────────────
LOG_FONT_FAMILY = '"Consolas", "Cascadia Code", "NSimSun", monospace'
LOG_FONT_SIZE   = "9pt"


def _load_qss(name: str) -> str:
    try:
        return (_UI_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return ""


NATIVE_QSS = _load_qss("native.qss")


def _apply_dwm_dark_mode(dark: bool) -> None:
    """通过 Windows DWM API 将所有顶层窗口的系统标题栏切换为深/浅色。
    非 Windows 平台或 DWM 不可用时静默跳过。
    """
    try:
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            _dwm_set(dwmapi, int(widget.winId()), dark)
    except Exception:
        pass


def _dwm_set(dwmapi, hwnd: int, dark: bool) -> None:
    """对单个 HWND 设置标题栏颜色，跟随系统深色/浅色模式。
    优先用 DWMWA_CAPTION_COLOR（Win11）设置精确颜色，
    回退到 DWMWA_USE_IMMERSIVE_DARK_MODE（Win10）。
    """
    # DWMWA_CAPTION_COLOR = 35，颜色格式为 COLORREF（0x00BBGGRR）
    caption_color = 0x002b2b2b if dark else 0x00ffffff
    try:
        dwmapi.DwmSetWindowAttribute(
            hwnd, 35,
            ctypes.byref(ctypes.c_int(caption_color)),
            ctypes.sizeof(ctypes.c_int()),
        )
        return
    except Exception:
        pass
    # Win10 回退：DWMWA_USE_IMMERSIVE_DARK_MODE = 20 / 19
    value = ctypes.c_int(1 if dark else 0)
    for attr in (20, 19):
        try:
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            break
        except Exception:
            continue


class _DwmEventFilter(QObject):
    """监听顶层窗口的 Show 事件，确保新打开的对话框也能获得正确的标题栏颜色。"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and getattr(obj, "isWindow", lambda: False)():
            try:
                dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
                dark = theme_manager.current_mode() == "dark"
                _dwm_set(dwmapi, int(obj.winId()), dark)
            except Exception:
                pass
        return False


class ThemeManager:
    """Windows 原生主题管理器（单例）。

    - 始终使用 windows11 风格，原生支持深色/浅色模式，完全跟随系统 QPalette
    - 系统深色/浅色切换时自动重新应用 native.qss 并通知订阅者
    - 不提供手动主题切换
    """

    _instance: "ThemeManager | None" = None
    _window = None

    def __new__(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, window) -> None:
        """注册主窗口，立即应用主题，并监听后续系统主题变化。"""
        self._window = window
        # 安装事件过滤器，确保后续新打开的对话框也能获得正确的标题栏颜色
        app = QApplication.instance()
        if app:
            self._dwm_filter = _DwmEventFilter()
            app.installEventFilter(self._dwm_filter)
        self._apply()
        # 系统主题变化时重新应用
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: self._apply()
        )

    def current_mode(self) -> str:
        """返回当前系统主题模式：``'dark'`` 或 ``'light'``。"""
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _apply(self) -> None:
        """应用 windows11 风格 + native.qss，跟随系统深色/浅色。"""
        if self._window is None:
            return
        app = QApplication.instance()
        if app is None:
            return

        mode = self.current_mode()

        # windows11 风格：原生支持深色模式，自动读取系统 QPalette，
        # 字体、圆角、颜色、背景均由 Windows 系统主题决定，无需手动构建调色板。
        # 回退：若当前 Qt 版本不支持 windows11 风格则降级为 windowsvista。
        from PySide6.QtWidgets import QStyleFactory
        style_name = "windows11" if "windows11" in QStyleFactory.keys() else "windowsvista"
        app.setStyle(style_name)

        # native.qss 只含项目专属控件的最小覆盖（日志字体、状态标签颜色等）
        qss = NATIVE_QSS \
            .replace("@log_font_family", LOG_FONT_FAMILY) \
            .replace("@log_font_size",   LOG_FONT_SIZE)
        app.setStyleSheet(qss)

        # 通知订阅者（如 AI 聊天面板的气泡颜色逻辑）系统主题已变化
        theme_signal.theme_changed.emit(mode)

        # 通知 Windows DWM 将所有顶层窗口的系统标题栏颜色跟随系统主题
        _apply_dwm_dark_mode(mode == "dark")


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()



class _ThemeSignalEmitter(QObject):
    """向外广播系统主题切换事件的轻量信号发射器。"""
    #: 系统主题变化后发出，携带当前模式 "dark" 或 "light"
    theme_changed = Signal(str)


#: 全局单例，供 dialog 等订阅主题切换：
#:   from catia_copilot.ui.theme_manager import theme_signal
#:   theme_signal.theme_changed.connect(my_slot)
theme_signal = _ThemeSignalEmitter()


# QSS 从同目录的独立文件加载，模块导入时读取一次
# 文件缺失时回退空字符串，样式降级为系统原生，程序仍可正常启动
_UI_DIR = Path(__file__).parent

# ── 日志字体常量（native.qss 占位符替换用）──────────────────────────
LOG_FONT_FAMILY = '"Consolas", "Cascadia Code", "NSimSun", monospace'
LOG_FONT_SIZE   = "9pt"


def _load_qss(name: str) -> str:
    try:
        return (_UI_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return ""


NATIVE_QSS = _load_qss("native.qss")


def _make_dark_palette() -> QPalette:
    """构建与 Windows 11 深色模式视觉一致的 QPalette。

    windowsvista 风格本身不支持深色模式（始终返回浅色调色板），
    需要手动设置 QPalette 才能让窗口背景、控件颜色跟随系统深色主题。
    色值参考 Windows 11 深色模式的系统颜色。
    """
    p = QPalette()
    # 窗口 / 对话框背景
    p.setColor(QPalette.ColorRole.Window,          QColor("#202020"))
    p.setColor(QPalette.ColorRole.WindowText,      QColor("#ffffff"))
    # 输入框 / 列表 / 树控件背景
    p.setColor(QPalette.ColorRole.Base,            QColor("#2d2d2d"))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#3a3a3a"))
    p.setColor(QPalette.ColorRole.Text,            QColor("#ffffff"))
    # 按钮
    p.setColor(QPalette.ColorRole.Button,          QColor("#2d2d2d"))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor("#ffffff"))
    # 选中高亮（Windows 11 蓝）
    p.setColor(QPalette.ColorRole.Highlight,       QColor("#0078d4"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    # 工具提示
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#2d2d2d"))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor("#ffffff"))
    # 占位符文字
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
    # 边框 / 分隔线辅助色
    p.setColor(QPalette.ColorRole.Mid,             QColor("#555555"))
    p.setColor(QPalette.ColorRole.Midlight,        QColor("#404040"))
    p.setColor(QPalette.ColorRole.Dark,            QColor("#1a1a1a"))
    p.setColor(QPalette.ColorRole.Light,           QColor("#404040"))
    # 禁用状态：文字变灰
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#666666"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#666666"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#666666"))
    return p



    """通过 Windows DWM API 将所有顶层窗口的系统标题栏切换为深/浅色。
    非 Windows 平台或 DWM 不可用时静默跳过。
    """
    try:
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            _dwm_set(dwmapi, int(widget.winId()), dark)
    except Exception:
        pass


def _dwm_set(dwmapi, hwnd: int, dark: bool) -> None:
    """对单个 HWND 设置标题栏颜色，跟随系统深色/浅色模式。
    优先用 DWMWA_CAPTION_COLOR（Win11）设置精确颜色，
    回退到 DWMWA_USE_IMMERSIVE_DARK_MODE（Win10）。
    """
    # DWMWA_CAPTION_COLOR = 35，颜色格式为 COLORREF（0x00BBGGRR）
    # 深色模式用深色标题栏，浅色模式用白色标题栏
    caption_color = 0x002b2b2b if dark else 0x00ffffff
    try:
        dwmapi.DwmSetWindowAttribute(
            hwnd, 35,
            ctypes.byref(ctypes.c_int(caption_color)),
            ctypes.sizeof(ctypes.c_int()),
        )
        return
    except Exception:
        pass
    # Win10 回退：DWMWA_USE_IMMERSIVE_DARK_MODE = 20 / 19
    value = ctypes.c_int(1 if dark else 0)
    for attr in (20, 19):
        try:
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            break
        except Exception:
            continue


class _DwmEventFilter(QObject):
    """监听顶层窗口的 Show 事件，确保新打开的对话框也能获得正确的标题栏颜色。"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and getattr(obj, "isWindow", lambda: False)():
            try:
                dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
                dark = theme_manager.current_mode() == "dark"
                _dwm_set(dwmapi, int(obj.winId()), dark)
            except Exception:
                pass
        return False


class ThemeManager:
    """Windows 原生主题管理器（单例）。

    - 始终使用 windowsvista 风格，完全跟随 Windows 系统主题
    - 系统深色/浅色切换时自动重新应用 native.qss 并通知订阅者
    - 不提供手动主题切换
    """

    _instance: "ThemeManager | None" = None
    _window = None

    def __new__(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, window) -> None:
        """注册主窗口，立即应用主题，并监听后续系统主题变化。"""
        self._window = window
        # 安装事件过滤器，确保后续新打开的对话框也能获得正确的标题栏颜色
        app = QApplication.instance()
        if app:
            self._dwm_filter = _DwmEventFilter()
            app.installEventFilter(self._dwm_filter)
        self._apply()
        # 系统主题变化时重新应用
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: self._apply()
        )

    def current_mode(self) -> str:
        """返回当前系统主题模式：``'dark'`` 或 ``'light'``。"""
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _apply(self) -> None:
        """应用 windowsvista 风格 + QPalette + native.qss，跟随系统深色/浅色。"""
        if self._window is None:
            return
        app = QApplication.instance()
        if app is None:
            return

        mode = self.current_mode()

        # windowsvista 风格负责控件形状/绘制（按钮凸起感、边框等）
        app.setStyle("windowsvista")

        # windowsvista 风格不支持深色模式，需手动设置 QPalette 让背景/文字跟随系统。
        # 浅色模式：重置为风格默认调色板（让 windowsvista 自己决定）。
        # 深色模式：手动构建与 Windows 11 深色模式一致的调色板。
        if mode == "dark":
            app.setPalette(_make_dark_palette())
        else:
            app.setPalette(app.style().standardPalette())

        # native.qss 只含项目专属控件的最小覆盖（日志字体、状态标签颜色等）
        qss = NATIVE_QSS \
            .replace("@log_font_family", LOG_FONT_FAMILY) \
            .replace("@log_font_size",   LOG_FONT_SIZE)
        app.setStyleSheet(qss)

        # 通知订阅者（如 AI 聊天面板的气泡颜色逻辑）系统主题已变化
        theme_signal.theme_changed.emit(mode)

        # 通知 Windows DWM 将所有顶层窗口的系统标题栏颜色跟随系统主题
        _apply_dwm_dark_mode(mode == "dark")


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
