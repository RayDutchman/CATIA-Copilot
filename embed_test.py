"""
embed_test.py

将一个 win32 原生小面板嵌入 CATIA V5 的 3D 视图区域内部。
面板位于 3D 视图右上角（避开罗盘），包含"BOM属性补全"按钮。

架构：
  - 面板父窗口 = MDIClient（唯一不会被 OpenGL 覆盖的稳定父窗口）
  - 坐标换算：GetWindowRect(view) → ScreenToClient(mdi_hwnd) 得到精确 MDI 客户区坐标
    （避免 MapWindowPoints 在 MDI 框架下的偏移问题）
  - Z 序：hWndInsertAfter=view_hwnd，每次 update 都刷新，保持面板在 view 正上方
  - WinEventHook 监听 CATIA 进程，回调只做 PostMessage，不阻塞 CATIA
  - 50ms 去抖定时器合并高频事件
  - 500ms 定时器扫描新出现的 view（新开文档自动创建面板）
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
MARGIN_RIGHT = 210   # 距 view 右边缘（避开罗盘）
MARGIN_TOP   = 4     # 距 view 客户区顶部

ID_BTN_BOM = 1001

WM_APP_UPDATE = win32con.WM_APP + 1  # 保留但不再使用（直接在回调里更新）

VIEW_CLASSES = (
    "CATFrmNavigGraphicWindow",
    "CATMuiGraphAnd3DWindow",
)

EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_SHOW           = 0x8002
EVENT_OBJECT_HIDE           = 0x8003
EVENT_SYSTEM_FOREGROUND     = 0x0003
WINEVENT_OUTOFCONTEXT       = 0x0000

TIMER_DEBOUNCE      = 43
DEBOUNCE_INTERVAL   = 50   # ms
TIMER_SCAN_VIEWS    = 44
SCAN_VIEWS_INTERVAL = 3000  # ms
TIMER_SIGINT        = 99

# ── ctypes ────────────────────────────────────────────────────────────────

class POINT(ctypes.Structure):
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
    "host_hwnd":       None,
    "hook_handles":    [],
    "_cb_ref":         None,
    "_update_pending": False,  # 保留字段，回调直接更新后不再使用
    "panels":          {},   # view_hwnd → panel_hwnd
    "hfont":           None,
}

# ── 查找窗口 ──────────────────────────────────────────────────────────────

def find_catia_mdi():
    catia_results = []
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


def _enum_views(mdi_hwnd):
    results = []
    def _cb(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        if any(cls.startswith(vc) for vc in VIEW_CLASSES):
            if win32gui.GetParent(hwnd) == mdi_hwnd and win32gui.IsWindowVisible(hwnd):
                results.append(hwnd)
    try:
        win32gui.EnumChildWindows(mdi_hwnd, _cb, None)
    except Exception:
        pass
    return results

# ── 坐标计算 ──────────────────────────────────────────────────────────────

def calc_panel_pos(view_hwnd, mdi_hwnd):
    """
    用 GetWindowRect(view) + ScreenToClient(mdi_hwnd) 计算面板在 MDI 客户区的坐标。
    这是最可靠的换算方式，完全绕开 MapWindowPoints 在 MDI 框架下的偏移问题。

    面板贴在 view 客户区右上角：
      - x = view客户区右边缘 - PANEL_W - MARGIN_RIGHT
      - y = view客户区顶边
    """
    # view 的屏幕坐标 rect（包含非客户区，即标题栏+边框）
    vl, vt, vr, vb = win32gui.GetWindowRect(view_hwnd)
    # view 客户区在屏幕上的起点（左上角）
    # ClientToScreen(view, (0,0)) 比 GetWindowRect 更准确（排除边框）
    pt_lt = POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(view_hwnd, ctypes.byref(pt_lt))
    # view 客户区右上角屏幕坐标
    client_w = win32gui.GetClientRect(view_hwnd)[2]
    pt_rt = POINT(pt_lt.x + client_w, pt_lt.y)
    # 换算到 MDI 客户区坐标
    ctypes.windll.user32.ScreenToClient(mdi_hwnd, ctypes.byref(pt_lt))
    ctypes.windll.user32.ScreenToClient(mdi_hwnd, ctypes.byref(pt_rt))
    # 面板坐标（限制在 MDI 范围内）
    mdi_w = win32gui.GetClientRect(mdi_hwnd)[2]
    right_x = min(pt_rt.x, mdi_w)
    panel_x = max(right_x - PANEL_W - MARGIN_RIGHT, pt_lt.x)
    panel_y = pt_lt.y + MARGIN_TOP
    return panel_x, panel_y

# ── 面板管理 ──────────────────────────────────────────────────────────────

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

def _panel_wndproc(hwnd, msg, wparam, lparam):
    if msg == win32con.WM_COMMAND:
        if win32api.LOWORD(wparam) == ID_BTN_BOM:
            _launch_bom_dialog(hwnd)
            return 0
    elif msg == win32con.WM_PAINT:
        hdc, ps = win32gui.BeginPaint(hwnd)
        rc = win32gui.GetClientRect(hwnd)
        brush = win32gui.CreateSolidBrush(win32api.RGB(240, 240, 240))
        win32gui.FillRect(hdc, rc, brush)
        win32gui.DeleteObject(brush)
        win32gui.EndPaint(hwnd, ps)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def create_panel_for_view(view_hwnd):
    """
    为 view_hwnd 在 MDIClient 下创建面板。
    父窗口 = MDIClient，坐标用 ClientToScreen+ScreenToClient 精确换算。
    Z 序：hWndInsertAfter=view_hwnd（面板在 view 正上方）。
    """
    if view_hwnd in _state["panels"]:
        return
    mdi_hwnd  = _state["mdi_hwnd"]
    hinstance = win32api.GetModuleHandle(None)

    panel_x, panel_y = calc_panel_pos(view_hwnd, mdi_hwnd)

    panel_hwnd = win32gui.CreateWindow(
        "CATIACopilotPanel", "",
        win32con.WS_CHILD | win32con.WS_CLIPSIBLINGS,  # 先隐藏，由 update_all_panels 决定显隐
        panel_x, panel_y, PANEL_W, PANEL_H,
        mdi_hwnd, 0, hinstance, None,
    )
    if not panel_hwnd:
        print(f"  CreateWindow 失败 view={view_hwnd} err={ctypes.windll.kernel32.GetLastError()}")
        return

    # Z 序：置顶（MDIClient 子窗口不支持 HWND_TOPMOST，用 HWND_TOP）
    win32gui.SetWindowPos(
        panel_hwnd, win32con.HWND_TOP,
        panel_x, panel_y, PANEL_W, PANEL_H,
        win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
    )

    btn_hwnd = win32gui.CreateWindow(
        "BUTTON", "BOM 属性补全",
        win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
        2, 2, PANEL_W - 4, PANEL_H - 4,
        panel_hwnd, ID_BTN_BOM, hinstance, None,
    )
    if _state["hfont"]:
        ctypes.windll.user32.SendMessageW(btn_hwnd, win32con.WM_SETFONT, _state["hfont"], 1)

    _state["panels"][view_hwnd] = panel_hwnd
    cls = win32gui.GetClassName(view_hwnd)
    print(f"  创建面板 view={view_hwnd}({cls}) panel={panel_hwnd} btn={btn_hwnd}")
    print(f"    MDI坐标=({panel_x},{panel_y})")
    # 立即触发一次 update，决定是否显示
    update_all_panels()


def update_all_panels():
    """
    更新所有面板：只显示当前 Z 序最顶的 view 对应的面板，其余隐藏。
    对可见面板每次刷新坐标（view 可能移动/缩放）。
    """
    _state["_update_pending"] = False
    mdi_hwnd = _state["mdi_hwnd"]
    if not mdi_hwnd:
        return

    top_view = _get_top_view(mdi_hwnd)
    dead = []

    for view_hwnd, panel_hwnd in list(_state["panels"].items()):
        if not win32gui.IsWindow(view_hwnd) or not win32gui.IsWindowVisible(view_hwnd):
            dead.append(view_hwnd)
            try:
                win32gui.DestroyWindow(panel_hwnd)
            except Exception:
                pass
            continue
        try:
            if view_hwnd == top_view:
                panel_x, panel_y = calc_panel_pos(view_hwnd, mdi_hwnd)
                win32gui.SetWindowPos(
                    panel_hwnd, win32con.HWND_TOP,
                    panel_x, panel_y, PANEL_W, PANEL_H,
                    win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
                )
            else:
                win32gui.ShowWindow(panel_hwnd, win32con.SW_HIDE)
        except Exception as e:
            print(f"  update_all_panels 异常 view={view_hwnd}: {e}")

    for v in dead:
        del _state["panels"][v]
        print(f"  移除已关闭 view={v} 的面板")


def _get_top_view(mdi_hwnd):
    """
    返回 MDIClient 中 Z 序最顶的可见 3D view（即当前激活/最前面的窗口）。
    GW_CHILD 返回 Z 序最顶的子窗口，依次向后遍历找第一个 view。
    """
    hwnd = ctypes.windll.user32.GetWindow(mdi_hwnd, 5)  # GW_CHILD = 5
    while hwnd:
        cls = win32gui.GetClassName(hwnd)
        if any(cls.startswith(vc) for vc in VIEW_CLASSES) and win32gui.IsWindowVisible(hwnd):
            return hwnd
        hwnd = ctypes.windll.user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT = 2
    return None


def scan_new_views():
    """扫描 MDI 下新出现的 view，为其创建面板。"""
    mdi_hwnd = _state["mdi_hwnd"]
    if not mdi_hwnd:
        return
    for v in _enum_views(mdi_hwnd):
        if v not in _state["panels"]:
            create_panel_for_view(v)

    mdi_hwnd = _state["mdi_hwnd"]
    if not mdi_hwnd:
        return
    for v in _enum_views(mdi_hwnd):
        if v not in _state["panels"]:
            create_panel_for_view(v)

# ── WinEventHook ──────────────────────────────────────────────────────────

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
        # 直接在回调里更新（OUTOFCONTEXT 模式下回调在我们自己线程执行，不阻塞 CATIA）
        update_all_panels()
    return _WinEventProc(_callback)

# ── 字体 ──────────────────────────────────────────────────────────────────

def _create_ui_font():
    lf = LOGFONT()
    lf.lfHeight   = -11
    lf.lfWeight   = 400
    lf.lfCharSet  = 0
    lf.lfQuality  = 2
    lf.lfFaceName = "Arial"
    return ctypes.windll.gdi32.CreateFontIndirectW(ctypes.byref(lf))

# ── 宿主窗口过程 ──────────────────────────────────────────────────────────

def _host_wndproc(hwnd, msg, wparam, lparam):
    if msg == win32con.WM_TIMER:
        if wparam == TIMER_SCAN_VIEWS:
            scan_new_views()
            return 0
        elif wparam == TIMER_SIGINT:
            return 0
    elif msg == win32con.WM_DESTROY:
        ctypes.windll.user32.KillTimer(hwnd, TIMER_SCAN_VIEWS)
        ctypes.windll.user32.KillTimer(hwnd, TIMER_SIGINT)
        for h in _state["hook_handles"]:
            try:
                ctypes.windll.user32.UnhookWinEvent(h)
            except Exception:
                pass
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

# ── BOM 对话框 ────────────────────────────────────────────────────────────

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
    catia_hwnd, mdi_hwnd = find_catia_mdi()
    if not catia_hwnd:
        print("ERROR: 未找到 CATIA V5 主窗口")
        sys.exit(1)
    if not mdi_hwnd:
        print("ERROR: 未找到 MDIClient")
        sys.exit(1)

    _, catia_pid = win32process.GetWindowThreadProcessId(catia_hwnd)
    print(f"CATIA hwnd={catia_hwnd}  pid={catia_pid}")
    print(f"MDI   hwnd={mdi_hwnd}   client={win32gui.GetClientRect(mdi_hwnd)}")

    hinstance = win32api.GetModuleHandle(None)
    _state["hfont"] = _create_ui_font()

    # 注册宿主窗口类
    wc_host = win32gui.WNDCLASS()
    wc_host.hInstance     = hinstance
    wc_host.lpszClassName = "CATIACopilotHost"
    wc_host.lpfnWndProc   = WNDPROCTYPE(_host_wndproc)
    wc_host.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    try:
        win32gui.RegisterClass(wc_host)
    except Exception as e:
        print(f"RegisterClass Host: {e} (忽略)")

    # 注册面板窗口类
    wc_panel = win32gui.WNDCLASS()
    wc_panel.hInstance     = hinstance
    wc_panel.lpszClassName = "CATIACopilotPanel"
    wc_panel.lpfnWndProc   = WNDPROCTYPE(_panel_wndproc)
    wc_panel.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc_panel.hbrBackground = win32con.COLOR_BTNFACE + 1
    try:
        win32gui.RegisterClass(wc_panel)
    except Exception as e:
        print(f"RegisterClass Panel: {e} (忽略)")

    # 清理残留面板
    old_panels = []
    def _find_old(hwnd, _):
        if win32gui.GetClassName(hwnd) == "CATIACopilotPanel":
            old_panels.append(hwnd)
    try:
        win32gui.EnumChildWindows(mdi_hwnd, _find_old, None)
    except Exception:
        pass
    for h in old_panels:
        try:
            win32gui.DestroyWindow(h)
        except Exception:
            pass
    if old_panels:
        print(f"已清理 {len(old_panels)} 个残留面板")

    # 创建宿主窗口（隐藏，用于接收消息和定时器）
    host_hwnd = win32gui.CreateWindow(
        "CATIACopilotHost", "CATIACopilotHost",
        win32con.WS_POPUP,
        0, 0, 1, 1,
        0, 0, hinstance, None,
    )
    if not host_hwnd:
        print(f"ERROR: 创建宿主窗口失败 err={ctypes.windll.kernel32.GetLastError()}")
        sys.exit(1)

    _state["host_hwnd"]  = host_hwnd
    _state["mdi_hwnd"]   = mdi_hwnd
    _state["catia_hwnd"] = catia_hwnd
    _state["catia_pid"]  = catia_pid
    print(f"宿主窗口 hwnd={host_hwnd}")

    # 为已有 view 创建面板
    views = _enum_views(mdi_hwnd)
    print(f"找到 {len(views)} 个 3D 视图")
    for v in views:
        create_panel_for_view(v)

    # 注册 WinEventHook
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

    ctypes.windll.user32.SetTimer(host_hwnd, TIMER_SCAN_VIEWS, SCAN_VIEWS_INTERVAL, None)

    import signal
    def _sigint(sig, frame):
        ctypes.windll.user32.PostQuitMessage(0)
    signal.signal(signal.SIGINT, _sigint)
    ctypes.windll.user32.SetTimer(host_hwnd, TIMER_SIGINT, 500, None)

    print("✓ 就绪。按 Ctrl+C 退出。")
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
