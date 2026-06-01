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

from PySide6.QtCore import Qt, QObject, Signal
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


def _apply_dwm_caption_color(dark: bool) -> None:
    """通过 DWMWA_CAPTION_COLOR（attr=35，Win11 专属）设置精确标题栏颜色。

    windows11 风格已自动处理 DWMWA_USE_IMMERSIVE_DARK_MODE（深/浅切换），
    此函数只补充精确的 COLORREF 颜色值，不重复设置 attr=20/19，
    避免与风格内部调用竞争产生 "Unable to set light window border" 警告。
    非 Windows 或 DWM 不可用时静默跳过。
    """
    try:
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        app = QApplication.instance()
        if app is None:
            return
        # DWMWA_CAPTION_COLOR = 35，颜色格式 COLORREF（0x00BBGGRR）
        caption_color = ctypes.c_int(0x002b2b2b if dark else 0x00f3f3f3)
        for widget in app.topLevelWidgets():
            try:
                dwmapi.DwmSetWindowAttribute(
                    int(widget.winId()), 35,
                    ctypes.byref(caption_color),
                    ctypes.sizeof(caption_color),
                )
            except Exception:
                pass
    except Exception:
        pass


class _DwmEventFilter(QObject):
    """监听顶层窗口的 Show 事件，为新打开的对话框补充精确标题栏颜色。

    windows11 风格自动处理深/浅切换（DWMWA_USE_IMMERSIVE_DARK_MODE），
    但不设置精确 COLORREF 颜色（DWMWA_CAPTION_COLOR=35），
    此过滤器只补充后者，不重复前者。
    """

    def eventFilter(self, obj: QObject, event) -> bool:
        if event.type() == 17 and getattr(obj, "isWindow", lambda: False)():
            # QEvent.Type.Show = 17
            try:
                dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
                dark = theme_manager.current_mode() == "dark"
                caption_color = ctypes.c_int(0x002b2b2b if dark else 0x00f3f3f3)
                dwmapi.DwmSetWindowAttribute(
                    int(obj.winId()), 35,
                    ctypes.byref(caption_color),
                    ctypes.sizeof(caption_color),
                )
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
            .replace("@mono_font_family",    L.MONO_FONT_FAMILY) \
            .replace("@mono_font_size_pt",   L.MONO_FONT_SIZE_PT) \
            .replace("@label_font_size_pt",  L.LABEL_FONT_SIZE_PT) \
            .replace("@hint_font_size_pt",   L.HINT_FONT_SIZE_PT) \
            .replace("@status_font_size_pt", L.STATUS_FONT_SIZE_PT) \
            .replace("@button_font_size_pt", L.BUTTON_FONT_SIZE_PT) \
            .replace("@tab_font_size_pt",    L.TAB_FONT_SIZE_PT)
        app.setStyleSheet(qss)

        # 通知订阅者（如 AI 聊天面板的气泡颜色逻辑）系统主题已变化
        theme_signal.theme_changed.emit(mode)

        # 通知 Windows DWM 设置精确标题栏颜色（DWMWA_CAPTION_COLOR=35）
        # windows11 风格已自动处理深/浅切换，此处只补充精确颜色，不重复 attr=20/19
        _apply_dwm_caption_color(mode == "dark")


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
