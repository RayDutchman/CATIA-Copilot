"""
CATIA 吸附边栏管理器。

监测 CATIA V5 主窗口的位置与尺寸，让本程序窗口自动贴靠在 CATIA 右侧，
随 CATIA 移动/缩放实时联动，效果类似"嵌入"但实现稳定。

使用方式：
    manager = CATIASidebarManager(main_window)
    manager.start()          # 开始监测
    manager.stop()           # 停止监测
    manager.is_active        # 当前是否处于吸附模式
"""

import logging
from typing import Optional

import win32con
import win32gui
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMainWindow

logger = logging.getLogger(__name__)

# CATIA V5 主窗口标题前缀（标题格式：「CATIA V5 - [文件名]」或「CATIA V5」）
_CATIA_TITLE_PREFIX = "CATIA V5"
# 轮询间隔（毫秒）
_POLL_INTERVAL_MS = 300
# 边栏与 CATIA 窗口之间的间距（像素）
_GAP_PX = 2


def _find_catia_hwnd() -> Optional[int]:
    """查找 CATIA V5 主窗口句柄，找不到返回 None。

    CATIA V5 的主框架窗口类名因版本和补丁而异（CATMain / CATDlgDocument / Afx:...），
    因此改为按标题前缀匹配：顶层可见窗口中，标题以 "CATIA V5" 开头且 rect 正常（非最小化）
    的最大窗口即为主框架。
    """
    results: list[tuple[int, int, int]] = []  # (hwnd, area, top)

    def _cb(hwnd: int, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.startswith(_CATIA_TITLE_PREFIX):
            return
        # 排除最小化状态（rect 在负坐标区）
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        if right <= 0 and bottom <= 0:
            return
        area = max(0, right - left) * max(0, bottom - top)
        results.append((hwnd, area, top))

    win32gui.EnumWindows(_cb, None)
    if not results:
        return None
    # 取面积最大的（主框架通常是最大的那个）
    results.sort(key=lambda x: x[1], reverse=True)
    return results[0][0]


def _get_window_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """获取窗口屏幕坐标 (left, top, right, bottom)，失败返回 None。"""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        return rect  # (left, top, right, bottom)
    except Exception:
        return None


def _is_window_visible(hwnd: int) -> bool:
    """判断窗口是否可见（未最小化）。"""
    try:
        if not win32gui.IsWindowVisible(hwnd):
            return False
        placement = win32gui.GetWindowPlacement(hwnd)
        # placement[1] 是 showCmd：SW_SHOWMINIMIZED = 2
        return placement[1] != win32con.SW_SHOWMINIMIZED
    except Exception:
        return False


class CATIASidebarManager(QObject):
    """
    监测 CATIA 主窗口位置，让宿主 QMainWindow 吸附在其右侧。

    状态变化：
      - CATIA 未运行 / 最小化 → 窗口保持普通独立模式（不强制移动）
      - CATIA 可见 → 窗口贴靠到 CATIA 右侧，高度与 CATIA 等高
      - CATIA 消失（进程退出）→ 退出吸附模式，窗口保持原位
    """

    # 吸附状态改变时发射（True=进入吸附, False=退出吸附）
    sidebar_mode_changed = Signal(bool)

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self._last_catia_rect: Optional[tuple] = None
        self._is_active: bool = False
        # 吸附模式下记住侧栏宽度（不因高度联动而改变用户设置的宽度）
        self._sidebar_width: int = window.width()

    # ── 公开接口 ──────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """当前是否处于 CATIA 吸附模式。"""
        return self._is_active

    def start(self) -> None:
        """启动轮询，开始监测 CATIA 窗口。"""
        self._timer.start()
        logger.debug("CATIASidebarManager 已启动")

    def stop(self) -> None:
        """停止轮询，退出吸附模式。"""
        self._timer.stop()
        if self._is_active:
            self._exit_sidebar_mode()
        logger.debug("CATIASidebarManager 已停止")

    # ── 内部逻辑 ──────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """定时轮询：检测 CATIA 窗口并决定是否需要重新定位侧栏。"""
        hwnd = _find_catia_hwnd()

        if hwnd is None or not _is_window_visible(hwnd):
            # CATIA 不在或已最小化 → 退出吸附模式
            if self._is_active:
                self._exit_sidebar_mode()
            return

        rect = _get_window_rect(hwnd)
        if rect is None:
            return

        # CATIA 窗口未变化时不做任何操作（减少无效 move）
        if rect == self._last_catia_rect and self._is_active:
            return

        self._last_catia_rect = rect
        self._attach_to_catia(rect)

    def _attach_to_catia(self, catia_rect: tuple[int, int, int, int]) -> None:
        """
        将本窗口定位到 CATIA 主窗口右侧。

        直接使用 win32 物理像素坐标调用 SetWindowPos，绕过 Qt 逻辑像素换算，
        避免多显示器 DPI 混用时 move()/resize() 坐标不准的问题。
        """
        left, top, right, bottom = catia_rect
        catia_h = bottom - top

        # 目标位置：紧贴 CATIA 右侧
        target_x = right + _GAP_PX
        target_y = top
        target_h = catia_h

        # 获取当前侧栏物理宽度（win32 rect 差值）
        try:
            cl, ct, cr, cb_ = win32gui.GetWindowRect(int(self._window.winId()))
            target_w = cr - cl  # 保持当前物理宽度
        except Exception:
            target_w = self._sidebar_width  # 回退到记录值

        # 检查是否超出屏幕右边界（用 Qt 获取屏幕逻辑尺寸再换算）
        screen = QApplication.screenAt(
            self._window.mapToGlobal(self._window.rect().center())
        )
        if screen:
            dpr = screen.devicePixelRatio()
            screen_phys_right = round(screen.geometry().right() * dpr)
            if target_x + target_w > screen_phys_right:
                # CATIA 贴右边缘时，改为贴靠 CATIA 左侧
                target_x = left - _GAP_PX - target_w

        # 直接用 win32 API 移动，物理像素，无 DPI 换算误差
        hwnd = int(self._window.winId())
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP,
            target_x, target_y, target_w, target_h,
            win32con.SWP_SHOWWINDOW,
        )

        if not self._is_active:
            self._is_active = True
            logger.info("进入 CATIA 吸附模式，物理坐标 (%d, %d)", target_x, target_y)
            self.sidebar_mode_changed.emit(True)

    def _exit_sidebar_mode(self) -> None:
        """退出吸附模式，恢复正常独立窗口状态。"""
        self._is_active = False
        self._last_catia_rect = None
        logger.info("退出 CATIA 吸附模式")
        self.sidebar_mode_changed.emit(False)
