"""
embed_test.py

将一个 win32 原生小面板嵌入 CATIA V5 的 3D 视图区域内部。
面板位于 3D 视图右上角（避开罗盘），包含"BOM属性补全"按钮。

跟随机制（安全版）：
  - SetWinEventHook 监听 CATIA 进程事件，但回调只做 PostMessage，
    不在回调里直接调用任何 Win32 API，防止阻塞 CATIA 导致死机。
  - 面板收到自定义消息后在自己的消息循环里执行 update_panel_position。
  - update_panel_position 用 hWndInsertAfter=view_hwnd 保证面板始终在 view 正上方。
  - 去抖定时器（50ms）合并高频事件，_update_pending flag 防止消息队列积压。
  - 位置缓存 _last_pos，位置未变时只刷新 Z 序，不重复移动。
  - 无 3D 视图时进入等待模式，每 500ms 检测一次，检测到后自动显示按钮。
"""

import sys
import ctypes
import ctypes.wintypes
import win32gui
import win32con
import win32api
import win32process

# ── 常量 ──────────────────────────────────────────────────────────────────

PANEL_W      = 152
PANEL_H      = 32
MARGIN_RIGHT = 210   # 距 3D 视图右边缘（避开罗盘）
MARGIN_TOP   = 0     # 贴住顶部

ID_BTN_BOM = 1001

# 自定义消息：WinEventHook 回调通过此消息通知面板更新位置
WM_APP_UPDATE = win32con.WM_APP + 1
# 自定义消息：等待 3D 视图出现时的轮询触发
WM_APP_WAIT_VIEW = win32con.WM_APP + 2

# 3D 视图窗口类名（普通态 / MDI 最大化态）
VIEW_CLASSES = (
    "CATFrmNavigGraphicWindow",
    "CATMuiGraphAnd3DWindow",
)

# WinEvent 常量
EVENT_OBJECT_LOCATIONCHANGE  = 0x800B
EVENT_OBJECT_SHOW            = 0x8002
EVENT_OBJECT_HIDE            = 0x8003
EVENT_SYSTEM_FOREGROUND      = 0x0003
WINEVENT_OUTOFCONTEXT        = 0x0000

# 去抖：连续收到多条 WM_APP_UPDATE 只执行一次更新
TIMER_DEBOUNCE    = 43
DEBOUNCE_INTERVAL = 50   # ms

# 等待 3D 视图出现时的轮询定时器
TIMER_WAIT_VIEW    = 44
WAIT_VIEW_INTERVAL = 500  # ms

# ── ctypes 结构 ───────────────────────────────────────────────────────────

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class LOGFONT(ctypes.Structure):
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

# ── 全局状态 ──────────────────────────────────────────────────────────────

_state = {
    "catia_hwnd":      None,
    "catia_pid":       None,
    "mdi_hwnd":        None,
    "view_hwnd":       None,
    "panel_hwnd":      None,
    "btn_hwnd":        None,
    "hook_handles":    [],
    "_cb_ref":         None,
    "_update_pending": False,   # 消息去重 flag
    "_last_pos":       None,    # (x, y, view_hwnd) 缓存
}

# ── 查找 CATIA 窗口层级 ────────────────────────────────────────────────────

def find_catia_windows():
    """返回 (catia_hwnd, mdi_hwnd, view_hwnd)，任一找不到则为 None。"""
    catia_results = []
    def _cb_catia(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd)
        if t.startswith("CATIA V5") or t.startswith("CATIA P3"):
            l, top, r, b = win32gui.GetWindowRect(hwnd)
            catia_results.append((hwnd, (r - l) * (b - top)))
    win32gui.EnumWindows(_cb_catia, None)
    if not catia_results:
        return None, None, None
    catia_results.sort(key=lambda x: x[1], reverse=True)
    catia_hwnd = catia_results[0][0]

    mdi_hwnd = None
    def _cb_mdi(hwnd, _):
        nonlocal mdi_hwnd
        if win32gui.GetClassName(hwnd) == "MDIClient" and win32gui.GetParent(hwnd) == catia_hwnd:
            mdi_hwnd = hwnd
    win32gui.EnumChildWindows(catia_hwnd, _cb_mdi, None)
    if not mdi_hwnd:
        return catia_hwnd, None, None

    view_hwnd = _find_best_view(mdi_hwnd)
    return catia_hwnd, mdi_hwnd, view_hwnd


def _find_best_view(mdi_hwnd):
    """
    在 MDIClient 下找面积最大的可见 3D 视图窗口。
    最大化时 CATMuiGraphAnd3DWindow 会铺满，面积最大；
    非最大化时 CATFrmNavigGraphicWindow 面积最大。
    """
    results = []
    def _cb(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        if any(cls.startswith(vc) for vc in VIEW_CLASSES) and win32gui.GetParent(hwnd) == mdi_hwnd:
            if win32gui.IsWindowVisible(hwnd):
                l, top, r, b = win32gui.GetWindowRect(hwnd)
                results.append((hwnd, (r - l) * (b - top)))
    try:
        win32gui.EnumChildWindows(mdi_hwnd, _cb, None)
    except Exception:
        pass
    if not results:
        return None
    results.sort(key=lambda x: x[1], reverse=True)
    return results[0][0]


def calc_panel_pos(view_hwnd, mdi_hwnd):
    """计算面板在 MDIClient 客户区坐标系中的位置（右上角，避开罗盘）。"""
    vc = win32gui.GetClientRect(view_hwnd)
    pt = _POINT(vc[2], 0)
    ctypes.windll.user32.MapWindowPoints(view_hwnd, mdi_hwnd, ctypes.byref(pt), 1)
    mdi_w = win32gui.GetClientRect(mdi_hwnd)[2]
    right_x = min(pt.x, mdi_w)
    panel_x = max(right_x - PANEL_W - MARGIN_RIGHT, 0)
    panel_y = pt.y + MARGIN_TOP
    return panel_x, panel_y

# ── 更新面板位置 ───────────────────────────────────────────────────────────

def update_panel_position():
    """
    重新找活动 3D 视图并移动面板到右上角（避开罗盘）。
    - hWndInsertAfter = view_hwnd：面板插到 view Z 序正上方，
      MDI 子窗口激活重排后面板仍可见。
    - 位置未变时只刷新 Z 序（SWP_NOMOVE|SWP_NOSIZE），避免闪烁。
    """
    panel_hwnd = _state["panel_hwnd"]
    mdi_hwnd   = _state["mdi_hwnd"]
    _state["_update_pending"] = False
    if not panel_hwnd or not mdi_hwnd:
        return

    new_view = _find_best_view(mdi_hwnd)
    _state["view_hwnd"] = new_view

    if not new_view:
        win32gui.ShowWindow(panel_hwnd, win32con.SW_HIDE)
        _state["_last_pos"] = None
        return

    panel_x, panel_y = calc_panel_pos(new_view, mdi_hwnd)
    last = _state["_last_pos"]

    if last and last == (panel_x, panel_y, new_view):
        # 位置未变，只刷新 Z 序
        win32gui.SetWindowPos(
            panel_hwnd, new_view,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
        )
        return

    _state["_last_pos"] = (panel_x, panel_y, new_view)
    win32gui.SetWindowPos(
        panel_hwnd, new_view,
        panel_x, panel_y, PANEL_W, PANEL_H,
        win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
    )

# ── WinEventHook 回调（只做 PostMessage，绝不阻塞）──────────────────────

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

def _make_event_callback():
    def _callback(hHook, event, hwnd, idObject, idChild, dwThread, dwTime):
        # 消息去重：队列里已有一条待处理则不再投递
        if _state["_update_pending"]:
            return
        panel_hwnd = _state["panel_hwnd"]
        if panel_hwnd:
            _state["_update_pending"] = True
            ctypes.windll.user32.PostMessageW(panel_hwnd, WM_APP_UPDATE, 0, 0)
    return _WinEventProc(_callback)

# ── 字体（Arial 8pt，与 CATIA 模型树一致）────────────────────────────────

def _create_ui_font():
    lf = LOGFONT()
    lf.lfHeight   = -11
    lf.lfWeight   = 400
    lf.lfCharSet  = 0
    lf.lfQuality  = 2
    lf.lfFaceName = "Arial"
    return ctypes.windll.gdi32.CreateFontIndirectW(ctypes.byref(lf))

# ── Win32 窗口过程 ─────────────────────────────────────────────────────────

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_APP_UPDATE:
        # 重置去抖定时器（50ms 内不再有新事件才真正更新）
        ctypes.windll.user32.SetTimer(hwnd, TIMER_DEBOUNCE, DEBOUNCE_INTERVAL, None)
        return 0

    elif msg == win32con.WM_TIMER:
        if wparam == TIMER_DEBOUNCE:
            ctypes.windll.user32.KillTimer(hwnd, TIMER_DEBOUNCE)
            update_panel_position()
            return 0

        elif wparam == TIMER_WAIT_VIEW:
            mdi_hwnd = _state["mdi_hwnd"]
            if mdi_hwnd:
                view = _find_best_view(mdi_hwnd)
                if view:
                    ctypes.windll.user32.KillTimer(hwnd, TIMER_WAIT_VIEW)
                    _state["view_hwnd"] = view
                    panel_x, panel_y = calc_panel_pos(view, mdi_hwnd)
                    _state["_last_pos"] = (panel_x, panel_y, view)
                    win32gui.SetWindowPos(
                        hwnd, view,
                        panel_x, panel_y, PANEL_W, PANEL_H,
                        win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
                    )
                    print(f"检测到 3D 视图 hwnd={view}，按钮已显示")
            return 0

    elif msg == win32con.WM_COMMAND:
        if win32api.LOWORD(wparam) == ID_BTN_BOM:
            _launch_bom_dialog(hwnd)
            return 0

    elif msg == win32con.WM_DESTROY:
        ctypes.windll.user32.KillTimer(hwnd, TIMER_DEBOUNCE)
        ctypes.windll.user32.KillTimer(hwnd, TIMER_WAIT_VIEW)
        for h in _state["hook_handles"]:
            try:
                ctypes.windll.user32.UnhookWinEvent(h)
            except Exception:
                pass
        win32gui.PostQuitMessage(0)
        return 0

    elif msg == win32con.WM_PAINT:
        hdc, ps = win32gui.BeginPaint(hwnd)
        rc = win32gui.GetClientRect(hwnd)
        brush = win32gui.CreateSolidBrush(win32api.RGB(245, 245, 245))
        win32gui.FillRect(hdc, rc, brush)
        win32gui.DeleteObject(brush)
        win32gui.EndPaint(hwnd, ps)
        return 0

    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

# ── 触发 BOM 编辑对话框 ───────────────────────────────────────────────────

def _launch_bom_dialog(parent_hwnd):
    import subprocess, os
    project_root = os.path.dirname(os.path.abspath(__file__))
    script = (
        "import sys; sys.path.insert(0, r'" + project_root + "'); "
        "from catia_copilot.ui.bom_edit_dialog import BomEditDialog; "
        "from PySide6.QtWidgets import QApplication; "
        "app = QApplication(sys.argv); "
        "dlg = BomEditDialog(); "
        "dlg.exec()"
    )
    try:
        subprocess.Popen([sys.executable, "-c", script])
    except Exception as e:
        win32gui.MessageBox(
            parent_hwnd,
            f"启动 BOM 对话框失败：{e}",
            "CATIA Copilot - 错误",
            win32con.MB_OK | win32con.MB_ICONERROR,
        )

# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    catia_hwnd, mdi_hwnd, view_hwnd = find_catia_windows()

    if not catia_hwnd:
        print("ERROR: 未找到 CATIA V5 主窗口")
        sys.exit(1)
    if not mdi_hwnd:
        print("ERROR: 未找到 MDIClient")
        sys.exit(1)
    # view_hwnd 可以为 None，此时进入等待模式

    _, catia_pid = win32process.GetWindowThreadProcessId(catia_hwnd)
    print(f"CATIA hwnd={catia_hwnd}  pid={catia_pid}")
    print(f"MDI   hwnd={mdi_hwnd}   client={win32gui.GetClientRect(mdi_hwnd)}")
    if view_hwnd:
        print(f"View  hwnd={view_hwnd}  client={win32gui.GetClientRect(view_hwnd)}")
    else:
        print("View  未找到，将在检测到 3D 视图窗口后自动显示按钮")

    hinstance = win32api.GetModuleHandle(None)

    # 清理残留面板
    old_panels = []
    def _find_old(hwnd, _):
        if win32gui.GetClassName(hwnd) == "CATIACopilotPanel":
            old_panels.append(hwnd)
    win32gui.EnumChildWindows(mdi_hwnd, _find_old, None)
    for h in old_panels:
        try:
            win32gui.DestroyWindow(h)
        except Exception:
            pass
    if old_panels:
        print(f"已清理 {len(old_panels)} 个残留面板")

    # 注册窗口类
    wc = win32gui.WNDCLASS()
    wc.hInstance     = hinstance
    wc.lpszClassName = "CATIACopilotPanel"
    wc.lpfnWndProc   = WNDPROCTYPE(_wndproc)
    wc.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc.hbrBackground = win32con.COLOR_BTNFACE + 1
    try:
        win32gui.RegisterClass(wc)
    except Exception as e:
        print(f"RegisterClass: {e} (忽略)")

    # 计算初始位置（无 view 时放在 MDI 左上角，隐藏状态）
    if view_hwnd:
        panel_x, panel_y = calc_panel_pos(view_hwnd, mdi_hwnd)
        initial_visible = win32con.WS_VISIBLE
    else:
        panel_x, panel_y = 0, 0
        initial_visible = 0

    print(f"Panel 初始位置 ({panel_x}, {panel_y})  {PANEL_W}×{PANEL_H}")

    # 创建面板（父窗口 = MDIClient）
    panel_hwnd = win32gui.CreateWindow(
        "CATIACopilotPanel", "",
        win32con.WS_CHILD | initial_visible | win32con.WS_CLIPSIBLINGS,
        panel_x, panel_y, PANEL_W, PANEL_H,
        mdi_hwnd, 0, hinstance, None,
    )
    if not panel_hwnd:
        print(f"ERROR: CreateWindow 失败 err={ctypes.windll.kernel32.GetLastError()}")
        sys.exit(1)

    if view_hwnd:
        _state["_last_pos"] = (panel_x, panel_y, view_hwnd)
        win32gui.SetWindowPos(
            panel_hwnd, view_hwnd,
            panel_x, panel_y, PANEL_W, PANEL_H,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
        )

    # 创建按钮
    btn_hwnd = win32gui.CreateWindow(
        "BUTTON", "BOM 属性补全",
        win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
        2, 2, PANEL_W - 4, PANEL_H - 4,
        panel_hwnd, ID_BTN_BOM, hinstance, None,
    )

    # 设置字体
    hfont = _create_ui_font()
    if hfont:
        ctypes.windll.user32.SendMessageW(btn_hwnd, win32con.WM_SETFONT, hfont, 1)

    print(f"Panel hwnd={panel_hwnd}  Button hwnd={btn_hwnd}")

    # 保存全局状态
    _state["catia_hwnd"] = catia_hwnd
    _state["catia_pid"]  = catia_pid
    _state["mdi_hwnd"]   = mdi_hwnd
    _state["view_hwnd"]  = view_hwnd
    _state["panel_hwnd"] = panel_hwnd
    _state["btn_hwnd"]   = btn_hwnd

    # 注册 WinEventHook（OUTOFCONTEXT：回调在我们自己线程执行，不阻塞 CATIA）
    cb_ref = _make_event_callback()
    _state["_cb_ref"] = cb_ref
    for event_min, event_max in (
        (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
        (EVENT_OBJECT_SHOW,           EVENT_OBJECT_HIDE),
        (EVENT_SYSTEM_FOREGROUND,     EVENT_SYSTEM_FOREGROUND),
    ):
        h = ctypes.windll.user32.SetWinEventHook(
            event_min, event_max,
            None, cb_ref,
            catia_pid, 0,
            WINEVENT_OUTOFCONTEXT,
        )
        if h:
            _state["hook_handles"].append(h)
    print(f"已注册 {len(_state['hook_handles'])} 个 WinEventHook")

    # 无 view 时启动等待定时器
    if not view_hwnd:
        ctypes.windll.user32.SetTimer(panel_hwnd, TIMER_WAIT_VIEW, WAIT_VIEW_INTERVAL, None)
        print("等待 3D 视图窗口出现（每 500ms 检测一次）...")

    # Ctrl+C 支持
    import signal
    def _sigint(sig, frame):
        ctypes.windll.user32.PostQuitMessage(0)
    signal.signal(signal.SIGINT, _sigint)
    TIMER_SIGINT = 99
    ctypes.windll.user32.SetTimer(panel_hwnd, TIMER_SIGINT, 500, None)

    print("✓ 就绪。按 Ctrl+C 退出。")
    win32gui.PumpMessages()
    ctypes.windll.user32.KillTimer(panel_hwnd, TIMER_SIGINT)


if __name__ == "__main__":
    main()
