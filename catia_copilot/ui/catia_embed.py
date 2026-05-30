"""
catia_embed.py

将 win32 原生小面板嵌入 CATIA V5 的每个 3D 视图角落（默认右上角，避开罗盘）。
面板左侧为可拖拽区域（绘制网格点图标），右侧为"CATIA Copilot"菜单按钮。

用法::

    from catia_copilot.ui.catia_embed import CATIAEmbedManager

    manager = CATIAEmbedManager(callbacks={
        "bom_edit":   <callable>,   # BOM 属性补全
        "bom_export": <callable>,   # BOM 导出
        "mass_props": <callable>,   # 质量特性
        "close":      <callable>,   # 关闭面板（可选，默认调用 stop()）
    })
    manager.start()   # 在后台线程中运行消息循环
    ...
    manager.stop()    # 清理面板、退出消息循环

架构说明：
  - 面板父窗口 = MDIClient（不在 OpenGL 渲染链内，不会被 3D 内容覆盖）
  - 坐标换算：ClientToScreen(view,(0,0)) + ScreenToClient(mdi_hwnd)，
    精确绕开 MapWindowPoints 在 MDI 框架下的 ~53px 偏移
  - 同一时刻只显示 Z 序最顶（当前激活）view 的面板，其余隐藏
  - WinEventHook（OUTOFCONTEXT）监听 CATIA 进程，回调直接刷新面板，
    不注入 CATIA 进程，不会导致 CATIA 崩溃或死锁
  - 3000ms 定时器扫描新 view（新开文档自动创建面板）
  - 消息循环运行在专用后台线程，不阻塞 PySide6 主线程
  - 菜单项点击通过 callbacks dict 回调到主线程（调用方负责线程安全）
  - 面板位置用锚点 + 偏移量描述，拖拽后自动选最近角作为新锚点
"""

import ctypes
import ctypes.wintypes
import logging
import threading
from typing import Callable, Optional

import win32api
import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)

# ── 面板外观常量 ───────────────────────────────────────────────────────────

PANEL_W    = 176   # 面板总宽度（像素）：拖拽区 24 + 按钮 148 + 边距 4
PANEL_H    = 32    # 面板高度（像素）
DRAG_W     = 24    # 左侧拖拽区宽度（像素）

# 面板内控件布局（x 坐标均相对于面板客户区）
ID_BTN_MENU  = 1001   # 下拉菜单触发按钮的控件 ID
BTN_X        = DRAG_W + 2          # 按钮左边缘（拖拽区右侧 + 2px 间距）
BTN_W        = PANEL_W - BTN_X - 2 # 按钮宽度（右侧留 2px）
BTN_LABEL    = "CATIA Copilot"

# 锚点标识（面板吸附的 view 角落）
ANCHOR_TR = "TR"   # 右上角（默认，避开罗盘）
ANCHOR_TL = "TL"   # 左上角
ANCHOR_BR = "BR"   # 右下角
ANCHOR_BL = "BL"   # 左下角

# 默认锚点及偏移量（面板左上角相对于锚点的偏移，单位像素）
# 右上角，距右边缘 210px（避开罗盘），距顶边缘 4px
DEFAULT_ANCHOR    = ANCHOR_TR
DEFAULT_ANCHOR_DX = -(PANEL_W + 210)   # 向左偏移（负值）
DEFAULT_ANCHOR_DY = 4                  # 向下偏移（正值）

# ── 菜单项 ID ─────────────────────────────────────────────────────────────
# 值域 2001-2999，与控件 ID 不冲突

# BOM 相关
MENU_BOM_EDIT       = 2001   # BOM 属性补全
MENU_BOM_EXPORT     = 2002   # BOM 导出
MENU_MASS_PROPS     = 2003   # 质量特性
MENU_PLM_SYNC       = 2004   # 同步 BOM 到 PLM
MENU_PLM_WORKBENCH  = 2005   # PLM 工作台

# 导出相关
MENU_EXPORT_PDF     = 2010   # CATDrawing → PDF
MENU_EXPORT_STP     = 2011   # CATPart/CATProduct → STP

# 图纸相关
MENU_DRAWING_NEW    = 2020   # 新建图纸
MENU_DRAWING_REFRESH = 2021  # 刷新图纸

# 工具相关
MENU_STAMP_TEMPLATE = 2030   # 刷写零件模板
MENU_FASTENER_ASM   = 2031   # 快速装配紧固件
MENU_NUT_PLATE_ASM  = 2032   # 快速装配托板螺母
MENU_OPEN_RELATED   = 2033   # 打开关联图纸/零件
MENU_RUN_MACRO      = 2034   # 运行宏…
MENU_FIND_DEPS      = 2035   # 查找所有依赖项

# 面板控制
MENU_POS_RESET      = 2019   # 恢复默认位置
MENU_CLOSE          = 2099   # 关闭面板

# ── 文档视图窗口类名 ────────────────────────────────────────────────────────
# 包含所有 CATIA 文档类型的视图窗口

VIEW_CLASSES = (
    "CATFrmNavigGraphicWindow",   # 产品窗口
    "CATMuiGraphAnd3DWindow",     # 零件窗口
    "CATGraphAndDrwWindow",       # 工程图窗口
    
    # 其他文档类型（通用匹配）
    # 注意：使用 startswith 匹配，所以 "CATFrm" 会匹配所有 CATFrm* 窗口
    # 如果发现其他文档类型，可以在这里添加
)

# ── WinEvent 事件常量 ─────────────────────────────────────────────────────

EVENT_OBJECT_LOCATIONCHANGE = 0x800B   # 位置/尺寸变化（主驱动事件）
EVENT_OBJECT_SHOW           = 0x8002   # 窗口变为可见
EVENT_OBJECT_HIDE           = 0x8003   # 窗口变为不可见
EVENT_SYSTEM_FOREGROUND     = 0x0003   # 前台窗口切换

# 回调在调用方自己的线程执行，不注入目标进程
WINEVENT_OUTOFCONTEXT = 0x0000

# ── 定时器 ID ─────────────────────────────────────────────────────────────

TIMER_SCAN_VIEWS    = 44
SCAN_VIEWS_INTERVAL = 3000   # ms，扫描新 view 的周期

# ── ctypes 结构 ───────────────────────────────────────────────────────────

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _LOGFONT(ctypes.Structure):
    _fields_ = [
        ("lfHeight",         ctypes.c_long),
        ("lfWidth",          ctypes.c_long),
        ("lfEscapement",     ctypes.c_long),
        ("lfOrientation",    ctypes.c_long),
        ("lfWeight",         ctypes.c_long),
        ("lfItalic",         ctypes.c_byte),
        ("lfUnderline",      ctypes.c_byte),
        ("lfStrikeOut",      ctypes.c_byte),
        ("lfCharSet",        ctypes.c_byte),
        ("lfOutPrecision",   ctypes.c_byte),
        ("lfClipPrecision",  ctypes.c_byte),
        ("lfQuality",        ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName",       ctypes.c_wchar * 32),
    ]


# ctypes 函数类型：WinEvent 回调 & 窗口过程
_WinEventProc = ctypes.WINFUNCTYPE(
    None,
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LONG,
    ctypes.wintypes.LONG,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
)

_WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

# 模块级：窗口类只注册一次，记录是否已注册
_classes_registered = False
_classes_lock = threading.Lock()


def _ensure_window_classes_registered(hinstance, host_wndproc, panel_wndproc):
    """注册宿主窗口类和面板窗口类（只执行一次）。"""
    global _classes_registered
    with _classes_lock:
        if _classes_registered:
            return
        # 宿主窗口类（隐藏的 WS_POPUP，仅承载定时器）
        wc_host = win32gui.WNDCLASS()
        wc_host.hInstance     = hinstance
        wc_host.lpszClassName = "CATIACopilotHost"
        wc_host.lpfnWndProc   = host_wndproc
        wc_host.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        try:
            win32gui.RegisterClass(wc_host)
        except Exception as e:
            logger.debug("RegisterClass Host: %s (忽略，已注册)", e)

        # 面板窗口类
        wc_panel = win32gui.WNDCLASS()
        wc_panel.hInstance     = hinstance
        wc_panel.lpszClassName = "CATIACopilotPanel"
        wc_panel.lpfnWndProc   = panel_wndproc
        wc_panel.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wc_panel.hbrBackground = win32con.COLOR_BTNFACE + 1
        try:
            win32gui.RegisterClass(wc_panel)
        except Exception as e:
            logger.debug("RegisterClass Panel: %s (忽略，已注册)", e)

        _classes_registered = True


# ── 主类 ──────────────────────────────────────────────────────────────────

class CATIAEmbedManager:
    """
    管理嵌入 CATIA 3D 视图右上角的悬浮菜单面板。

    - ``start()``  在后台线程中初始化并运行消息循环
    - ``stop()``   向消息循环发送退出消息，清理所有面板和钩子
    - ``is_active`` 属性反映当前运行状态
    """

    def __init__(
        self,
        callbacks: Optional[dict[str, Callable]] = None,
        anchor:    str = DEFAULT_ANCHOR,
        anchor_dx: int = DEFAULT_ANCHOR_DX,
        anchor_dy: int = DEFAULT_ANCHOR_DY,
        position_changed_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        """
        Parameters
        ----------
        callbacks:
            菜单项点击时调用的函数字典，key 说明：
              "bom_edit"   - BOM 属性补全
              "bom_export" - BOM 导出
              "mass_props" - 质量特性
              "close"      - 关闭面板（可选；若缺失则调用 self.stop()）
            各回调在 win32 后台线程中被调用，调用方应自行通过信号机制派发到 Qt 主线程。
        anchor:
            面板吸附的 view 角落，取值 "TR"/"TL"/"BR"/"BL"（默认右上角）。
        anchor_dx:
            面板左上角相对于锚点的 x 偏移（像素，负值向左）。
        anchor_dy:
            面板左上角相对于锚点的 y 偏移（像素，正值向下）。
        position_changed_callback:
            用户拖拽或恢复默认后的回调，签名为 (anchor, dx, dy) -> None。
            在 win32 后台线程中调用，调用方负责线程安全。
        """
        self._callbacks: dict[str, Callable] = callbacks or {}
        self._anchor    = anchor
        self._anchor_dx = anchor_dx
        self._anchor_dy = anchor_dy
        self._position_changed_callback = position_changed_callback

        # 拖拽状态（仅在后台线程访问）
        self._drag_active  = False   # 是否正在拖拽
        self._drag_start_x = 0       # 拖拽起始点（屏幕坐标）
        self._drag_start_y = 0
        self._drag_panel_x = 0       # 拖拽开始时面板在 MDI 坐标系中的位置
        self._drag_panel_y = 0
        self._drag_view_hwnd = 0     # 拖拽时对应的 view 句柄

        # 运行时状态（仅在后台线程访问，无需加锁）
        self._catia_hwnd:  Optional[int] = None
        self._catia_pid:   Optional[int] = None
        self._mdi_hwnd:    Optional[int] = None
        self._host_hwnd:   Optional[int] = None
        self._panels:      dict[int, int] = {}   # view_hwnd → panel_hwnd
        self._hfont:       Optional[int] = None
        self._hook_handles: list[int] = []
        self._cb_ref       = None   # WinEventProc 引用（防止被 GC）
        self._host_wndproc_ref  = None   # 宿主窗口过程引用
        self._panel_wndproc_ref = None   # 面板窗口过程引用
        self._current_view_hwnd: Optional[int] = None  # 当前触发回调的 view_hwnd

        self._thread: Optional[threading.Thread] = None
        self._active = False

    # ── 公开接口 ──────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> bool:
        """
        查找 CATIA ，在后台线程启动嵌入逻辑。

        Returns
        -------
        bool
            True  = 成功找到 CATIA 并启动；
            False = 未找到 CATIA 或已在运行。
        """
        if self._active:
            logger.warning("CATIAEmbedManager 已在运行，忽略重复 start()")
            return False

        catia_hwnd, mdi_hwnd = self._find_catia_mdi()
        if not catia_hwnd:
            logger.warning("未找到 CATIA V5 主窗口")
            return False
        if not mdi_hwnd:
            logger.warning("未找到 MDIClient")
            return False

        self._catia_hwnd = catia_hwnd
        self._mdi_hwnd   = mdi_hwnd
        _, self._catia_pid = win32process.GetWindowThreadProcessId(catia_hwnd)
        logger.info("CATIA hwnd=%d pid=%d MDI hwnd=%d", catia_hwnd, self._catia_pid, mdi_hwnd)

        self._active = True
        self._thread = threading.Thread(
            target=self._run,
            name="CATIAEmbedThread",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self):
        """停止嵌入逻辑，销毁所有面板，等待后台线程退出。"""
        if not self._active:
            return
        self._active = False
        # 向消息循环发送退出消息
        if self._host_hwnd:
            try:
                win32gui.PostMessage(self._host_hwnd, win32con.WM_DESTROY, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("CATIAEmbedManager 已停止")

    # ── 后台线程入口 ──────────────────────────────────────────────────────

    def _run(self):
        """后台线程：初始化、运行消息循环、退出时清理。"""
        try:
            self._init_win32()
            logger.info("CATIAEmbedManager 就绪，进入消息循环")
            win32gui.PumpMessages()
        except Exception:
            logger.exception("CATIAEmbedManager 后台线程异常")
        finally:
            self._cleanup()
            self._active = False

    # ── win32 初始化 ──────────────────────────────────────────────────────

    def _init_win32(self):
        """在后台线程中完成所有 win32 对象的创建。"""
        hinstance = win32api.GetModuleHandle(None)
        self._hfont = self._create_ui_font()

        # ctypes 函数引用（防止 GC 回收，导致回调崩溃）
        self._host_wndproc_ref  = _WNDPROCTYPE(self._host_wndproc)
        self._panel_wndproc_ref = _WNDPROCTYPE(self._panel_wndproc)

        _ensure_window_classes_registered(
            hinstance,
            self._host_wndproc_ref,
            self._panel_wndproc_ref,
        )

        # 清理上次运行残留的面板
        self._cleanup_stale_panels()

        # 创建宿主窗口（隐藏，仅用于承载定时器）
        host_hwnd = win32gui.CreateWindow(
            "CATIACopilotHost", "CATIACopilotHost",
            win32con.WS_POPUP,
            0, 0, 1, 1,
            0, 0, hinstance, None,
        )
        if not host_hwnd:
            raise RuntimeError(
                f"创建宿主窗口失败 err={ctypes.windll.kernel32.GetLastError()}"
            )
        self._host_hwnd = host_hwnd
        logger.debug("宿主窗口 hwnd=%d", host_hwnd)

        # 为已有 view 创建面板
        views = self._enum_views()
        logger.info("找到 %d 个 3D 视图", len(views))
        for v in views:
            self._create_panel_for_view(v)

        # 注册 WinEventHook
        cb_ref = _WinEventProc(self._win_event_callback)
        self._cb_ref = cb_ref
        for event_min, event_max in (
            (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
            (EVENT_OBJECT_SHOW,           EVENT_OBJECT_HIDE),
            (EVENT_SYSTEM_FOREGROUND,     EVENT_SYSTEM_FOREGROUND),
        ):
            h = ctypes.windll.user32.SetWinEventHook(
                event_min, event_max,
                None, cb_ref,
                self._catia_pid, 0,
                WINEVENT_OUTOFCONTEXT,
            )
            if h:
                self._hook_handles.append(h)
        logger.info("已注册 %d 个 WinEventHook", len(self._hook_handles))

        # 启动扫描定时器（自动发现新开的文档/视图）
        ctypes.windll.user32.SetTimer(host_hwnd, TIMER_SCAN_VIEWS, SCAN_VIEWS_INTERVAL, None)

    # ── 窗口过程 ──────────────────────────────────────────────────────────

    def _host_wndproc(self, hwnd, msg, wparam, lparam):
        """宿主窗口过程：处理定时器和销毁消息。"""
        if msg == win32con.WM_TIMER:
            if wparam == TIMER_SCAN_VIEWS:
                self._scan_new_views()
                return 0
        elif msg == win32con.WM_DESTROY:
            ctypes.windll.user32.KillTimer(hwnd, TIMER_SCAN_VIEWS)
            for h in self._hook_handles:
                try:
                    ctypes.windll.user32.UnhookWinEvent(h)
                except Exception:
                    pass
            self._hook_handles.clear()
            # 销毁所有面板
            for panel_hwnd in list(self._panels.values()):
                try:
                    win32gui.DestroyWindow(panel_hwnd)
                except Exception:
                    pass
            self._panels.clear()
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _panel_wndproc(self, hwnd, msg, wparam, lparam):
        """面板窗口过程：处理按钮点击、拖拽和绘制。"""
        if msg == win32con.WM_COMMAND:
            ctrl_id = win32api.LOWORD(wparam)
            if ctrl_id == ID_BTN_MENU:
                self._show_popup_menu(hwnd)
                return 0

        elif msg == win32con.WM_PAINT:
            hdc, ps = win32gui.BeginPaint(hwnd)
            rc = win32gui.GetClientRect(hwnd)
            # 背景
            brush = win32gui.CreateSolidBrush(win32api.RGB(240, 240, 240))
            win32gui.FillRect(hdc, rc, brush)
            win32gui.DeleteObject(brush)
            # 拖拽区网格点（2×3，6 个小圆点）
            self._draw_drag_dots(hdc)
            win32gui.EndPaint(hwnd, ps)
            return 0

        elif msg == win32con.WM_SETCURSOR:
            # 鼠标在拖拽区时显示移动光标
            pt = _POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(pt))
            if pt.x < DRAG_W:
                cursor = ctypes.windll.user32.LoadCursorW(0, 32646)  # IDC_SIZEALL
                ctypes.windll.user32.SetCursor(cursor)
                return 1
            # 其余区域交给默认处理（子控件会自己处理）

        elif msg == win32con.WM_LBUTTONDOWN:
            pt_x = ctypes.c_int16(lparam & 0xFFFF).value
            pt_y = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
            if pt_x < DRAG_W:
                # 在拖拽区按下：开始拖拽
                self._drag_active = True
                # 记录鼠标起始屏幕坐标
                screen_pt = _POINT(pt_x, pt_y)
                ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(screen_pt))
                self._drag_start_x = screen_pt.x
                self._drag_start_y = screen_pt.y
                # 记录面板当前在 MDI 坐标系中的位置
                panel_rect = win32gui.GetWindowRect(hwnd)
                mdi_pt = _POINT(panel_rect[0], panel_rect[1])
                ctypes.windll.user32.ScreenToClient(self._mdi_hwnd, ctypes.byref(mdi_pt))
                self._drag_panel_x = mdi_pt.x
                self._drag_panel_y = mdi_pt.y
                # 记录对应的 view（从 panels 反查）
                self._drag_view_hwnd = self._panel_to_view(hwnd)
                ctypes.windll.user32.SetCapture(hwnd)
                return 0

        elif msg == win32con.WM_MOUSEMOVE:
            if self._drag_active:
                pt_x = ctypes.c_int16(lparam & 0xFFFF).value
                pt_y = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
                screen_pt = _POINT(pt_x, pt_y)
                ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(screen_pt))
                dx = screen_pt.x - self._drag_start_x
                dy = screen_pt.y - self._drag_start_y
                new_x = self._drag_panel_x + dx
                new_y = self._drag_panel_y + dy
                # clamp 到 view 范围内
                new_x, new_y = self._clamp_to_view(
                    new_x, new_y, self._drag_view_hwnd
                )
                win32gui.SetWindowPos(
                    hwnd, win32con.HWND_TOP,
                    new_x, new_y, PANEL_W, PANEL_H,
                    win32con.SWP_NOACTIVATE,
                )
                return 0

        elif msg == win32con.WM_LBUTTONUP:
            if self._drag_active:
                self._drag_active = False
                ctypes.windll.user32.ReleaseCapture()
                # 拖拽结束：根据面板当前位置更新锚点和偏移量，然后持久化
                if self._drag_view_hwnd:
                    self._update_anchor_from_panel(hwnd, self._drag_view_hwnd)
                return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _draw_drag_dots(self, hdc: int):
        """在拖拽区绘制 2×3 网格点（6 个小圆点，视觉上表示可拖拽）。"""
        DOT_R   = 2    # 圆点半径（像素）
        DOT_GAP = 5    # 圆点间距（像素）
        # 网格起始位置（在拖拽区内居中）
        grid_w = DOT_R * 2 + DOT_GAP + DOT_R * 2   # 2列
        grid_h = DOT_R * 2 + DOT_GAP + DOT_R * 2 + DOT_GAP + DOT_R * 2  # 3行
        start_x = (DRAG_W - grid_w) // 2
        start_y = (PANEL_H - grid_h) // 2

        brush = win32gui.CreateSolidBrush(win32api.RGB(160, 160, 160))
        old_brush = win32gui.SelectObject(hdc, brush)
        pen = win32gui.CreatePen(0, 0, win32api.RGB(160, 160, 160))
        old_pen = win32gui.SelectObject(hdc, pen)

        for row in range(3):
            for col in range(2):
                cx = start_x + col * (DOT_R * 2 + DOT_GAP) + DOT_R
                cy = start_y + row * (DOT_R * 2 + DOT_GAP) + DOT_R
                win32gui.Ellipse(
                    hdc,
                    cx - DOT_R, cy - DOT_R,
                    cx + DOT_R, cy + DOT_R,
                )

        win32gui.SelectObject(hdc, old_brush)
        win32gui.SelectObject(hdc, old_pen)
        win32gui.DeleteObject(brush)
        win32gui.DeleteObject(pen)

    # ── WinEvent 回调 ─────────────────────────────────────────────────────

    def _win_event_callback(self, hHook, event, hwnd, idObject, idChild, dwThread, dwTime):
        """
        OUTOFCONTEXT 回调：在我们自己的线程执行，直接刷新面板位置。
        不注入 CATIA 进程，不阻塞 CATIA 。

        EVENT_OBJECT_SHOW 时额外调用 _scan_new_views：
        MDI 子窗口最大化会导致旧 view（CATFrmNavigGraphicWindow）隐藏、
        新 view（CATMuiGraphAnd3DWindow）出现，若只调 _update_all_panels
        则新 view 没有面板，要等 3 秒定时器才补上。在 SHOW 事件时顺手扫描
        可以消除这个延迟。
        """
        if event == EVENT_OBJECT_SHOW:
            self._scan_new_views()
        self._update_all_panels()

    # ── 面板管理 ──────────────────────────────────────────────────────────

    def _create_panel_for_view(self, view_hwnd: int):
        """为指定 view 在 MDIClient 下创建面板（初始隐藏）。"""
        if view_hwnd in self._panels:
            return
        hinstance = win32api.GetModuleHandle(None)
        panel_x, panel_y = self._calc_panel_pos(view_hwnd)

        panel_hwnd = win32gui.CreateWindow(
            "CATIACopilotPanel", "",
            win32con.WS_CHILD | win32con.WS_CLIPSIBLINGS,
            panel_x, panel_y, PANEL_W, PANEL_H,
            self._mdi_hwnd, 0, hinstance, None,
        )
        if not panel_hwnd:
            logger.warning(
                "CreateWindow 失败 view=%d err=%d",
                view_hwnd,
                ctypes.windll.kernel32.GetLastError(),
            )
            return

        # 右侧功能按钮（拖拽区右侧，留 2px 上下边距）
        btn_hwnd = win32gui.CreateWindow(
            "BUTTON", BTN_LABEL,
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
            BTN_X, 2, BTN_W, PANEL_H - 4,
            panel_hwnd, ID_BTN_MENU, hinstance, None,
        )
        if self._hfont:
            ctypes.windll.user32.SendMessageW(btn_hwnd, win32con.WM_SETFONT, self._hfont, 1)

        # 拖拽区不创建子控件，由面板 WM_PAINT 直接绘制网格点，
        # WM_LBUTTONDOWN/MOUSEMOVE/LBUTTONUP 在面板窗口过程中处理

        self._panels[view_hwnd] = panel_hwnd
        cls = win32gui.GetClassName(view_hwnd)
        logger.debug(
            "创建面板 view=%d(%s) panel=%d btn=%d MDI 坐标=(%d,%d)",
            view_hwnd, cls, panel_hwnd, btn_hwnd, panel_x, panel_y,
        )
        self._update_all_panels()

    def _update_all_panels(self):
        """
        刷新所有面板的位置和显隐状态：
        - 只显示 Z 序最顶 view 的面板，其余隐藏
        - 对可见面板重新计算坐标并置顶（HWND_TOP）
        - 移除已销毁/隐藏的 view 对应的面板
        """
        mdi_hwnd = self._mdi_hwnd
        if not mdi_hwnd:
            return

        top_view = self._get_top_view()
        dead = []

        for view_hwnd, panel_hwnd in list(self._panels.items()):
            if not win32gui.IsWindow(view_hwnd) or not win32gui.IsWindowVisible(view_hwnd):
                dead.append(view_hwnd)
                try:
                    win32gui.DestroyWindow(panel_hwnd)
                except Exception:
                    pass
                continue
            try:
                if view_hwnd == top_view:
                    panel_x, panel_y = self._calc_panel_pos(view_hwnd)
                    win32gui.SetWindowPos(
                        panel_hwnd, win32con.HWND_TOP,
                        panel_x, panel_y, PANEL_W, PANEL_H,
                        win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
                    )
                else:
                    win32gui.ShowWindow(panel_hwnd, win32con.SW_HIDE)
            except Exception:
                logger.debug("update_all_panels 异常 view=%d", view_hwnd, exc_info=True)

        for v in dead:
            del self._panels[v]
            logger.debug("移除已关闭 view=%d 的面板", v)

    def _scan_new_views(self):
        """扫描 MDIClient 下新出现的 view，为其创建面板。"""
        for v in self._enum_views():
            if v not in self._panels:
                self._create_panel_for_view(v)

    # ── 坐标计算 ──────────────────────────────────────────────────────────

    def _calc_panel_pos(self, view_hwnd: int) -> tuple[int, int]:
        """
        根据当前锚点和偏移量，计算面板在 MDI 客户区坐标中的位置。

        锚点坐标（MDI 坐标系）：
          TL = view 左上角,  TR = view 右上角
          BL = view 左下角,  BR = view 右下角

        面板左上角 = 锚点坐标 + (anchor_dx, anchor_dy)

        最终做 clamp，确保面板完整落在 view 范围内。
        """
        left, top, right, bottom = self._get_view_bounds_in_mdi(view_hwnd)

        # 锚点坐标
        ax = right  if self._anchor in (ANCHOR_TR, ANCHOR_BR) else left
        ay = bottom if self._anchor in (ANCHOR_BL, ANCHOR_BR) else top

        panel_x = ax + self._anchor_dx
        panel_y = ay + self._anchor_dy

        # clamp：面板必须完整落在 view 范围内
        panel_x = max(left, min(panel_x, right  - PANEL_W))
        panel_y = max(top,  min(panel_y, bottom - PANEL_H))
        return panel_x, panel_y

    def _get_view_bounds_in_mdi(self, view_hwnd: int) -> tuple[int, int, int, int]:
        """
        返回 view 客户区在 MDI 坐标系中的边界 (left, top, right, bottom)。
        right 不超出 MDI 宽度。
        """
        client_rect = win32gui.GetClientRect(view_hwnd)
        view_w = client_rect[2]
        view_h = client_rect[3]

        pt_lt = _POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(view_hwnd, ctypes.byref(pt_lt))
        pt_rb = _POINT(pt_lt.x + view_w, pt_lt.y + view_h)
        ctypes.windll.user32.ScreenToClient(self._mdi_hwnd, ctypes.byref(pt_lt))
        ctypes.windll.user32.ScreenToClient(self._mdi_hwnd, ctypes.byref(pt_rb))

        mdi_w = win32gui.GetClientRect(self._mdi_hwnd)[2]
        return pt_lt.x, pt_lt.y, min(pt_rb.x, mdi_w), pt_rb.y

    def _clamp_to_view(self, panel_x: int, panel_y: int, view_hwnd: int) -> tuple[int, int]:
        """将面板坐标 clamp 到 view 范围内，返回修正后的 (x, y)。"""
        if not view_hwnd or not win32gui.IsWindow(view_hwnd):
            return panel_x, panel_y
        left, top, right, bottom = self._get_view_bounds_in_mdi(view_hwnd)
        panel_x = max(left, min(panel_x, right  - PANEL_W))
        panel_y = max(top,  min(panel_y, bottom - PANEL_H))
        return panel_x, panel_y

    def _panel_to_view(self, panel_hwnd: int) -> int:
        """从 panels 字典反查 panel_hwnd 对应的 view_hwnd，找不到返回 0。"""
        for view_hwnd, ph in self._panels.items():
            if ph == panel_hwnd:
                return view_hwnd
        return 0

    def _update_anchor_from_panel(self, panel_hwnd: int, view_hwnd: int):
        """
        拖拽结束后，根据面板当前位置重新计算锚点和偏移量。

        选择距面板中心最近的 view 角落作为新锚点，
        偏移量 = 面板左上角 - 锚点坐标。
        """
        if not win32gui.IsWindow(view_hwnd):
            return

        # 面板当前在 MDI 坐标系中的位置
        panel_rect = win32gui.GetWindowRect(panel_hwnd)
        mdi_pt = _POINT(panel_rect[0], panel_rect[1])
        ctypes.windll.user32.ScreenToClient(self._mdi_hwnd, ctypes.byref(mdi_pt))
        px, py = mdi_pt.x, mdi_pt.y

        left, top, right, bottom = self._get_view_bounds_in_mdi(view_hwnd)

        # 面板中心
        cx = px + PANEL_W // 2
        cy = py + PANEL_H // 2

        # 四个角的坐标和标识
        corners = [
            (ANCHOR_TL, left,  top),
            (ANCHOR_TR, right, top),
            (ANCHOR_BL, left,  bottom),
            (ANCHOR_BR, right, bottom),
        ]

        # 选距面板中心最近的角
        best_anchor, best_ax, best_ay = min(
            corners,
            key=lambda c: (cx - c[1]) ** 2 + (cy - c[2]) ** 2,
        )

        self._anchor    = best_anchor
        self._anchor_dx = px - best_ax
        self._anchor_dy = py - best_ay

        logger.debug(
            "锚点更新: %s dx=%d dy=%d", self._anchor, self._anchor_dx, self._anchor_dy
        )

        # 通知主线程持久化
        if self._position_changed_callback is not None:
            try:
                self._position_changed_callback(
                    self._anchor, self._anchor_dx, self._anchor_dy
                )
            except Exception:
                logger.exception("position_changed_callback 调用失败")

    # ── 窗口枚举 ──────────────────────────────────────────────────────────

    def _enum_views(self) -> list[int]:
        """枚举 MDIClient 下所有可见 3D 视图，返回 hwnd 列表。"""
        results: list[int] = []
        def _cb(hwnd, _):
            cls = win32gui.GetClassName(hwnd)
            if any(cls.startswith(vc) for vc in VIEW_CLASSES):
                if win32gui.GetParent(hwnd) == self._mdi_hwnd and win32gui.IsWindowVisible(hwnd):
                    results.append(hwnd)
        try:
            win32gui.EnumChildWindows(self._mdi_hwnd, _cb, None)
        except Exception:
            pass
        return results

    def _get_top_view(self) -> Optional[int]:
        """
        返回 MDIClient 中 Z 序最顶的可见 3D view。
        GW_CHILD(5) 返回最顶子窗口，GW_HWNDNEXT(2) 向后遍历。
        """
        hwnd = ctypes.windll.user32.GetWindow(self._mdi_hwnd, 5)   # GW_CHILD
        while hwnd:
            cls = win32gui.GetClassName(hwnd)
            if any(cls.startswith(vc) for vc in VIEW_CLASSES) and win32gui.IsWindowVisible(hwnd):
                return hwnd
            hwnd = ctypes.windll.user32.GetWindow(hwnd, 2)   # GW_HWNDNEXT
        return None

    # ── 静态查找 CATIA ────────────────────────────────────────────────────

    @staticmethod
    def _find_catia_mdi() -> tuple[Optional[int], Optional[int]]:
        """返回 (catia_hwnd, mdi_hwnd)，找不到则对应项为 None。"""
        catia_results: list[tuple[int, int]] = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            t = win32gui.GetWindowText(hwnd)
            if t.startswith("CATIA V5") or t.startswith("CATIA P3"):
                l, top, r, b = win32gui.GetWindowRect(hwnd)
                catia_results.append((hwnd, (r - l) * (b - top)))

        win32gui.EnumWindows(_cb, None)
        if not catia_results:
            return None, None
        catia_results.sort(key=lambda x: x[1], reverse=True)
        catia_hwnd = catia_results[0][0]

        mdi_hwnd = None

        def _cb_mdi(hwnd, _):
            nonlocal mdi_hwnd
            if win32gui.GetClassName(hwnd) == "MDIClient" and win32gui.GetParent(hwnd) == catia_hwnd:
                mdi_hwnd = hwnd

        win32gui.EnumChildWindows(catia_hwnd, _cb_mdi, None)
        return catia_hwnd, mdi_hwnd

    # ── 辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _create_ui_font() -> int:
        """创建 Arial 8pt 字体（96 DPI 下 lfHeight=-11），与 CATIA 模型树字体一致。"""
        lf = _LOGFONT()
        lf.lfHeight   = -11
        lf.lfWeight   = 400
        lf.lfCharSet  = 0
        lf.lfQuality  = 2   # PROOF_QUALITY
        lf.lfFaceName = "Arial"
        return ctypes.windll.gdi32.CreateFontIndirectW(ctypes.byref(lf))

    def _cleanup_stale_panels(self):
        """清理上次运行残留的 CATIACopilotPanel 窗口。"""
        if not self._mdi_hwnd:
            return
        old: list[int] = []

        def _cb(hwnd, _):
            if win32gui.GetClassName(hwnd) == "CATIACopilotPanel":
                old.append(hwnd)

        try:
            win32gui.EnumChildWindows(self._mdi_hwnd, _cb, None)
        except Exception:
            pass
        for h in old:
            try:
                win32gui.DestroyWindow(h)
            except Exception:
                pass
        if old:
            logger.info("已清理 %d 个残留面板", len(old))

    def _cleanup(self):
        """后台线程退出时的兜底清理（正常路径由 WM_DESTROY 处理）。"""
        for h in self._hook_handles:
            try:
                ctypes.windll.user32.UnhookWinEvent(h)
            except Exception:
                pass
        self._hook_handles.clear()
        if self._hfont:
            try:
                ctypes.windll.gdi32.DeleteObject(self._hfont)
            except Exception:
                pass
            self._hfont = None

    # ── 弹出菜单 ──────────────────────────────────────────────────────────

    def _show_popup_menu(self, panel_hwnd: int):
        """
        在面板按钮正下方弹出标准 win32 菜单。
        TrackPopupMenu 阻塞直到用户选择或取消，选中后分发到对应回调。
        """
        # 从 panel_hwnd 反查 view_hwnd
        view_hwnd = None
        for v, p in self._panels.items():
            if p == panel_hwnd:
                view_hwnd = v
                break
        
        hmenu = ctypes.windll.user32.CreatePopupMenu()
        if not hmenu:
            return

        MF_STRING    = 0x0000
        MF_SEPARATOR = 0x0800
        MF_POPUP     = 0x0010

        def append(menu, flags, item_id, text):
            ctypes.windll.user32.AppendMenuW(menu, flags, item_id, text)

        # 从主窗口的 _ACTION_LABELS 读取文字，保持与主菜单一致
        try:
            from catia_copilot.ui.main_window import MainWindow
            L = MainWindow._ACTION_LABELS
        except Exception:
            L = {}

        def label(key: str, fallback: str) -> str:
            return L.get(key, fallback)

        # ── 工作台 ────────────────────────────────────────────────────
        append(hmenu, MF_STRING,    MENU_BOM_EDIT,       label("bom_edit",        "BOM 工作台"))
        append(hmenu, MF_STRING,    MENU_MASS_PROPS,     label("mass_props",      "质量特性工作台"))
        append(hmenu, MF_STRING,    MENU_PLM_WORKBENCH,  label("plm_workbench",   "PLM 工作台"))
        append(hmenu, MF_SEPARATOR, 0,                   None)
        # ── 导出 ──────────────────────────────────────────────────────
        append(hmenu, MF_STRING,    MENU_BOM_EXPORT,     label("bom_export",      "从产品导出 BOM"))
        append(hmenu, MF_STRING,    MENU_EXPORT_PDF,     label("export_pdf",      "从图纸导出 PDF"))
        append(hmenu, MF_STRING,    MENU_EXPORT_STP,     label("export_stp",      "从产品/零件导出 STP"))
        append(hmenu, MF_SEPARATOR, 0,                   None)
        # ── 图纸 ──────────────────────────────────────────────────────
        append(hmenu, MF_STRING,    MENU_DRAWING_NEW,    label("drawing_new",     "新建图纸 (Python)"))
        append(hmenu, MF_STRING,    MENU_DRAWING_REFRESH, label("drawing_refresh", "刷新图纸 (Python)"))
        append(hmenu, MF_SEPARATOR, 0,                   None)
        # ── 工具 ──────────────────────────────────────────────────────
        append(hmenu, MF_STRING,    MENU_STAMP_TEMPLATE, label("stamp_template",  "刷写零件模板"))
        append(hmenu, MF_STRING,    MENU_FASTENER_ASM,   label("fastener_asm",    "快速装配紧固件"))
        append(hmenu, MF_STRING,    MENU_NUT_PLATE_ASM,  label("nut_plate_asm",   "快速装配托板螺母"))
        append(hmenu, MF_STRING,    MENU_OPEN_RELATED,   label("open_related",    "在图纸/零件间切换"))
        append(hmenu, MF_STRING,    MENU_FIND_DEPS,      label("find_deps",       "查找指向的文档"))

        # 运行宏子菜单
        macro_submenu = ctypes.windll.user32.CreatePopupMenu()
        macro_files = self._get_macro_files()
        self._macro_id_map = {}  # 临时映射：菜单ID → 宏文件路径

        if macro_files:
            for idx, macro_path in enumerate(macro_files):
                macro_id = 3000 + idx  # 动态ID范围 3000-3999
                self._macro_id_map[macro_id] = str(macro_path)
                append(macro_submenu, MF_STRING, macro_id, macro_path.name)
        else:
            append(macro_submenu, MF_STRING, 0, "（未找到宏文件）")
            ctypes.windll.user32.EnableMenuItem(macro_submenu, 0, 0x0001)  # MF_GRAYED

        append(hmenu, MF_POPUP, macro_submenu, label("run_macro", "运行宏"))

        append(hmenu, MF_SEPARATOR, 0,                   None)
        append(hmenu, MF_STRING,    MENU_POS_RESET,      "恢复默认位置")
        append(hmenu, MF_SEPARATOR, 0,                   None)
        append(hmenu, MF_STRING,    MENU_CLOSE,          "关闭面板")

        rect = win32gui.GetWindowRect(panel_hwnd)
        x = rect[0]   # 面板左边缘
        y = rect[3]   # 面板底边缘（菜单向下展开）

        TPM_RETURNCMD = 0x0100
        TPM_NONOTIFY  = 0x0080

        # TrackPopupMenu 的 hwnd 参数必须是顶层窗口，不能是 WS_CHILD
        # 使用宿主窗口（隐藏的消息循环窗口）作为 owner
        cmd = ctypes.windll.user32.TrackPopupMenu(
            hmenu,
            TPM_RETURNCMD | TPM_NONOTIFY,
            x, y, 0, self._host_hwnd, None,
        )
        ctypes.windll.user32.DestroyMenu(macro_submenu)
        ctypes.windll.user32.DestroyMenu(hmenu)

        logger.debug("TrackPopupMenu 返回 cmd=%s view_hwnd=%s", cmd, view_hwnd)
        if cmd:
            self._dispatch_menu_item(cmd, view_hwnd)

    def _get_macro_files(self):
        """扫描宏文件夹，返回所有宏文件的 Path 列表。"""
        from pathlib import Path
        try:
            from catia_copilot.utils import resource_path
            macros_dir = resource_path("macros")
            if not macros_dir.is_dir():
                return []
            
            MACRO_EXTENSIONS = frozenset({".catvbs", ".catscript", ".catvba"})
            return sorted(
                f for f in macros_dir.iterdir()
                if f.is_file() and f.suffix.lower() in MACRO_EXTENSIONS
            )
        except Exception:
            return []

    def _dispatch_menu_item(self, cmd: int, view_hwnd: int | None):
        """根据菜单项 ID 调用对应的回调函数。"""
        logger.debug("_dispatch_menu_item cmd=%d view_hwnd=%s", cmd, view_hwnd)
        
        # 处理宏文件 ID（3000-3999）：把路径存到实例变量，通过 run_macro_file 回调派发到主线程
        if 3000 <= cmd < 4000:
            macro_path = getattr(self, "_macro_id_map", {}).get(cmd)
            if macro_path:
                self._current_macro_path = macro_path   # 主线程回调读取
                cb = self._callbacks.get("run_macro_file")
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        logger.exception("run_macro_file callback 调用失败")
            return
        
        if cmd == MENU_POS_RESET:
            self._reset_position()
            return

        mapping = {
            MENU_BOM_EDIT:       "bom_edit",
            MENU_BOM_EXPORT:     "bom_export",
            MENU_MASS_PROPS:     "mass_props",
            MENU_PLM_WORKBENCH:  "plm_workbench",
            MENU_EXPORT_PDF:     "export_pdf",
            MENU_EXPORT_STP:     "export_stp",
            MENU_DRAWING_NEW:    "drawing_new",
            MENU_DRAWING_REFRESH: "drawing_refresh",
            MENU_STAMP_TEMPLATE: "stamp_template",
            MENU_FASTENER_ASM:   "fastener_asm",
            MENU_NUT_PLATE_ASM:  "nut_plate_asm",
            MENU_OPEN_RELATED:   "open_related",
            MENU_FIND_DEPS:      "find_deps",
            MENU_CLOSE:          "close",
        }
        key = mapping.get(cmd)
        if key is None:
            return

        if key == "close":
            cb = self._callbacks.get("close")
            if cb is not None:
                try:
                    cb()
                except Exception:
                    logger.exception("close callback 调用失败")
            else:
                if self._host_hwnd:
                    win32gui.PostMessage(self._host_hwnd, win32con.WM_DESTROY, 0, 0)
            return

        cb = self._callbacks.get(key)
        if cb is not None:
            logger.debug("调用回调 key=%s cb=%s view_hwnd=%s", key, cb, view_hwnd)
            self._current_view_hwnd = view_hwnd  # 设置当前 view，供回调读取
            try:
                cb()
            except Exception:
                logger.exception("%s callback 调用失败", key)
            finally:
                self._current_view_hwnd = None  # 清理
        else:
            logger.debug("无回调 key=%s，降级子进程", key)
            self._launch_subprocess_fallback(key)

    def _reset_position(self):
        """恢复默认锚点和偏移量，立即刷新面板，并持久化。"""
        self._anchor    = DEFAULT_ANCHOR
        self._anchor_dx = DEFAULT_ANCHOR_DX
        self._anchor_dy = DEFAULT_ANCHOR_DY
        self._update_all_panels()
        if self._position_changed_callback is not None:
            try:
                self._position_changed_callback(
                    self._anchor, self._anchor_dx, self._anchor_dy
                )
            except Exception:
                logger.exception("position_changed_callback 调用失败")

    # ── 降级子进程启动 ────────────────────────────────────────────────────

    def _launch_subprocess_fallback(self, key: str):
        """
        未提供 callback 时的降级模式：子进程启动对应对话框。
        会导致 CATIA 短暂闪烁，仅作兜底。
        """
        import subprocess
        import sys
        import os

        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        dialog_map = {
            "bom_edit":   ("catia_copilot.ui.bom_edit_dialog",   "BomEditDialog"),
            "bom_export": ("catia_copilot.ui.export_bom_dialog",  "ExportBomDialog"),
            "mass_props": ("catia_copilot.ui.mass_props_dialog",  "MassPropsDialog"),
        }
        entry = dialog_map.get(key)
        if entry is None:
            return
        module, cls = entry

        script = (
            f"import sys; sys.path.insert(0, r'{project_root}'); "
            f"from {module} import {cls}; "
            "from PySide6.QtWidgets import QApplication; "
            "app = QApplication(sys.argv); "
            f"dlg = {cls}(); "
            "dlg.exec()"
        )
        try:
            subprocess.Popen([sys.executable, "-c", script])
        except Exception as e:
            logger.error("子进程启动 %s 失败: %s", cls, e)
