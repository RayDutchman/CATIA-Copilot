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
from PySide6.QtWidgets import QApplication, QStyleFactory

# 字体常量从 ui_layout 统一管理
from catia_copilot.ui.ui_layout import L


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

# windows11 风格原生支持深色模式；回退到 windowsvista（Qt < 6.7）
_STYLE_NAME = "windows11" if "windows11" in QStyleFactory.keys() else "windowsvista"


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

        # windows11 风格原生支持深色模式，自动读取系统 QPalette，
        # 字体、圆角、颜色、背景均由 Windows 系统主题决定，无需手动构建调色板。
        app.setStyle(_STYLE_NAME)

        # native.qss 只含项目专属控件的最小覆盖（日志字体、状态标签颜色等）
        # 字体常量统一从 L（ui_layout.py）读取
        qss = NATIVE_QSS \
            .replace("@mono_font_family",   L.MONO_FONT_FAMILY) \
            .replace("@mono_font_size_pt",  L.MONO_FONT_SIZE_PT) \
            .replace("@label_font_size_pt", L.LABEL_FONT_SIZE_PT) \
            .replace("@hint_font_size_pt",  L.HINT_FONT_SIZE_PT) \
            .replace("@status_font_size_pt", L.STATUS_FONT_SIZE_PT)
        app.setStyleSheet(qss)

        # 通知订阅者（如 AI 聊天面板的气泡颜色逻辑）系统主题已变化
        theme_signal.theme_changed.emit(mode)

        # 通知 Windows DWM 将所有顶层窗口的系统标题栏颜色跟随系统主题
        _apply_dwm_dark_mode(mode == "dark")


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
