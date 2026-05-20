"""
主题管理器：支持手动切换深色 / 浅色主题，偏好持久化到 QSettings。

- 启动时读取 QSettings 中保存的手动偏好；若无则跟随系统主题
- toggle() 在深/浅色之间切换并写入 QSettings，下次启动自动恢复
- 系统主题变化时，仅在无手动偏好的情况下自动跟随
- QSS 通过 QApplication.setStyleSheet() 全局应用，对话框等顶层窗口均生效
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QObject, Signal, QEvent
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


# QSS 从同目录的独立文件加载，模块导入时读取一次
# 文件缺失时回退空字符串，样式降级为系统原生，程序仍可正常启动
_UI_DIR = Path(__file__).parent

# ── 控件尺寸常量（调整这里即可全局生效）────────────────────────────
INDICATOR_SIZE  = 16   # radio / checkbox indicator 边长（px），Windows 11 原生值
CONTROL_SPACING = 8    # indicator 与文字之间的间距（px）
CHECKBOX_RADIUS = 4    # checkbox indicator 圆角（px），Windows 11 ControlCornerRadius
CONTROL_RADIUS  = 4    # 普通控件圆角（px）：按钮、输入框、下拉框、表格、树等
GROUPBOX_RADIUS = 6    # 分组框 / 菜单整体圆角（px）
BUTTON_HEIGHT   = 26   # QPushButton min-height（px）
INPUT_HEIGHT    = 22   # QLineEdit / QComboBox min-height（px）


def _load_qss(name: str) -> str:
    try:
        return (_UI_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return ""


DARK_QSS  = _load_qss("dark.qss")
LIGHT_QSS = _load_qss("light.qss")


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
    """对单个 HWND 设置标题栏颜色，与当前 QSS 主题保持一致。
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
                dark = theme_manager._current_mode() == "dark"
                _dwm_set(dwmapi, int(obj.winId()), dark)
            except Exception:
                pass
        return False


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
        # 安装事件过滤器，确保后续新打开的对话框也能获得正确的标题栏颜色
        app = QApplication.instance()
        if app:
            self._dwm_filter = _DwmEventFilter()
            app.installEventFilter(self._dwm_filter)
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
        _check_icon           = str(_UI_DIR / "check_white.svg").replace("\\", "/")
        _radio_checked_icon   = str(_UI_DIR / "radio_checked.svg").replace("\\", "/")
        _radio_unchecked_icon = str(_UI_DIR / f"radio_unchecked_{mode}.svg").replace("\\", "/")
        _chevron_down_icon    = str(_UI_DIR / f"chevron_down_{mode}.svg").replace("\\", "/")
        qss = (DARK_QSS if mode == "dark" else LIGHT_QSS) \
            .replace("@indicator_sizepx",  f"{INDICATOR_SIZE}px") \
            .replace("@control_spacingpx", f"{CONTROL_SPACING}px") \
            .replace("@checkbox_radiuspx", f"{CHECKBOX_RADIUS}px") \
            .replace("@control_radiuspx",  f"{CONTROL_RADIUS}px") \
            .replace("@groupbox_radiuspx", f"{GROUPBOX_RADIUS}px") \
            .replace("@button_heightpx",   f"{BUTTON_HEIGHT}px") \
            .replace("@input_heightpx",    f"{INPUT_HEIGHT}px") \
            .replace("@check_icon", _check_icon) \
            .replace("@radio_checked_icon", _radio_checked_icon) \
            .replace("@radio_unchecked_icon", _radio_unchecked_icon) \
            .replace("@chevron_down_icon", _chevron_down_icon)
        # 应用到整个 application，所有顶层窗口（包括 QDialog）均生效
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss)
        # 通知订阅者（如 dialog 中的行着色逻辑）主题已切换
        theme_signal.theme_changed.emit(mode)
        # 通知 Windows DWM 将所有顶层窗口的系统标题栏切换为深/浅色
        _apply_dwm_dark_mode(mode == "dark")


# 全局单例，供整个应用直接导入使用
theme_manager = ThemeManager()
