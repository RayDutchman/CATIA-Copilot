"""
embed_test.py

灏嗕竴涓?win32 鍘熺敓灏忛潰鏉垮祵鍏?CATIA V5 鐨?3D 瑙嗗浘鍖哄煙鍐呴儴銆?闈㈡澘浣嶄簬 3D 瑙嗗浘鍙充笂瑙掞紙閬垮紑缃楃洏锛夛紝鍖呭惈"BOM灞炴€цˉ鍏?鎸夐挳銆?
鏋舵瀯锛?  - 闈㈡澘鐖剁獥鍙?= MDIClient锛堜笉鍦?OpenGL 娓叉煋閾惧唴閮紝涓嶄細琚?3D 鍐呭瑕嗙洊锛?  - 鍧愭爣鎹㈢畻锛欳lientToScreen(view,(0,0)) + ScreenToClient(mdi_hwnd)
    绮剧‘寰楀埌 view 瀹㈡埛鍖哄湪 MDI 鍧愭爣绯讳腑鐨勪綅缃紝缁曞紑 MapWindowPoints 鐨?MDI 鍋忕Щ闂
  - 鍚屼竴鏃跺埢鍙樉绀?Z 搴忔渶椤讹紙褰撳墠婵€娲伙級view 瀵瑰簲鐨勯潰鏉匡紝鍏朵綑闅愯棌
  - Z 搴忥細HWND_TOP锛屾瘡娆?update 閮藉埛鏂?  - WinEventHook锛圤UTOFCONTEXT锛夌洃鍚?CATIA 杩涚▼锛屽洖璋冪洿鎺ヨ皟 update_all_panels锛?    涓嶆敞鍏?CATIA 绾跨▼锛屼笉闃诲 CATIA
  - 3000ms 瀹氭椂鍣ㄦ壂鎻忔柊鍑虹幇鐨?view锛堟柊寮€鏂囨。鑷姩鍒涘缓闈㈡澘锛?"""

import sys
import ctypes
import ctypes.wintypes
import win32gui
import win32con
import win32api
import win32process

# 鈹€鈹€ 闈㈡澘澶栬甯搁噺 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

PANEL_W      = 152   # 闈㈡澘瀹藉害锛堝儚绱狅級
PANEL_H      = 32    # 闈㈡澘楂樺害锛堝儚绱狅級
MARGIN_RIGHT = 210   # 璺?view 瀹㈡埛鍖哄彸杈圭紭鐨勮窛绂伙紙閬垮紑鍙充笅瑙掔綏鐩橈級
MARGIN_TOP   = 4     # 璺?view 瀹㈡埛鍖洪《杈圭殑璺濈

ID_BTN_BOM = 1001    # 鎸夐挳鎺т欢 ID

# 鈹€鈹€ 3D 瑙嗗浘绐楀彛绫诲悕 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

VIEW_CLASSES = (
    "CATFrmNavigGraphicWindow",   # 浜у搧绐楀彛锛屽疄娴嬮浂浠剁殑 Analysis 涔熷睘浜?CATFrmNavigGraphicWindow
    "CATMuiGraphAnd3DWindow",     # 闆朵欢绐楀彛
    "CATGraphAndDrwWindow",       # 宸ョ▼鍥剧獥鍙?)

# 鈹€鈹€ WinEvent 浜嬩欢绫诲瀷锛圫etWinEventHook 鍙傛暟锛夆攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 杩欎簺甯搁噺鏉ヨ嚜 Windows SDK <WinUser.h>

# 浠绘剰绐楀彛鐨勪綅缃垨灏哄鍙戠敓鍙樺寲锛堟嫋鍔ㄣ€佺缉鏀俱€佹渶澶у寲锛?EVENT_OBJECT_LOCATIONCHANGE = 0x800B
# 绐楀彛鍙樹负鍙锛堟柊寮€鏂囨。鏃?view 鍑虹幇锛?EVENT_OBJECT_SHOW           = 0x8002
# 绐楀彛鍙樹负涓嶅彲瑙侊紙鍏抽棴鏂囨。鏃?view 闅愯棌锛?EVENT_OBJECT_HIDE           = 0x8003
# 鍓嶅彴绐楀彛鍒囨崲锛堢偣鍑讳笉鍚?view 鏍囬鏍忔縺娲伙級
EVENT_SYSTEM_FOREGROUND     = 0x0003

# Hook 妯″紡锛氬洖璋冨湪璋冪敤鏂硅嚜宸辩殑绾跨▼鎵ц锛屼笉娉ㄥ叆鐩爣杩涚▼
# 鍏抽敭瀹夊叏鎺柦鈥斺€斾笉娉ㄥ叆 CATIA 杩涚▼锛屼笉浼氬鑷?CATIA 宕╂簝鎴栨閿?WINEVENT_OUTOFCONTEXT = 0x0000

# 鈹€鈹€ 瀹氭椂鍣?ID锛圫etTimer 鐨?nIDEvent锛屽€煎彧瑕佷笉鍐茬獊鍗冲彲锛夆攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

# 姣?3000ms 鎵弿涓€娆?MDIClient锛屽彂鐜版柊 view 鏃跺垱寤哄搴旈潰鏉匡紙鏂板紑鏂囨。锛?TIMER_SCAN_VIEWS    = 44
SCAN_VIEWS_INTERVAL = 3000  # ms

# 姣?500ms 鍞ら啋娑堟伅寰幆锛岃 Python signal 妯″潡鏈夋満浼氬鐞?Ctrl+C
# 锛圥umpMessages 闃诲鏈熼棿 signal 涓嶄細琚鐞嗭級
TIMER_SIGINT = 99

# 鈹€鈹€ ctypes 缁撴瀯 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

# 鈹€鈹€ 鍏ㄥ眬鐘舵€?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

_state = {
    "catia_hwnd":   None,
    "catia_pid":    None,
    "mdi_hwnd":     None,
    "host_hwnd":    None,      # 瀹夸富绐楀彛锛堥殣钘忥紝鐢ㄤ簬鎺ユ敹瀹氭椂鍣ㄦ秷鎭級
    "hook_handles": [],        # WinEventHook 鍙ユ焺鍒楄〃
    "_cb_ref":      None,      # WinEventProc 寮曠敤锛堥槻姝㈣ GC锛?    "panels":       {},        # view_hwnd 鈫?panel_hwnd
    "hfont":        None,      # 鎵€鏈夋寜閽叡鐢ㄧ殑瀛椾綋鍙ユ焺
}

# 鈹€鈹€ 鏌ユ壘绐楀彛 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def find_catia_mdi():
    """杩斿洖 (catia_hwnd, mdi_hwnd)锛屾壘涓嶅埌鍒欏搴旈」涓?None銆?""
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
    """鏋氫妇 MDIClient 涓嬫墍鏈夊彲瑙?3D 瑙嗗浘绐楀彛锛岃繑鍥?hwnd 鍒楄〃銆?""
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


def _get_top_view(mdi_hwnd):
    """
    杩斿洖 MDIClient 涓?Z 搴忔渶椤剁殑鍙 3D view锛堝綋鍓嶆縺娲?鏈€鍓嶉潰鐨勭獥鍙ｏ級銆?    GW_CHILD(5) 杩斿洖 Z 搴忔渶椤剁殑瀛愮獥鍙ｏ紝GW_HWNDNEXT(2) 渚濇鍚戝悗閬嶅巻銆?    """
    hwnd = ctypes.windll.user32.GetWindow(mdi_hwnd, 5)   # GW_CHILD
    while hwnd:
        cls = win32gui.GetClassName(hwnd)
        if any(cls.startswith(vc) for vc in VIEW_CLASSES) and win32gui.IsWindowVisible(hwnd):
            return hwnd
        hwnd = ctypes.windll.user32.GetWindow(hwnd, 2)   # GW_HWNDNEXT
    return None

# 鈹€鈹€ 鍧愭爣璁＄畻 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def calc_panel_pos(view_hwnd, mdi_hwnd):
    """
    璁＄畻闈㈡澘鍦?MDI 瀹㈡埛鍖哄潗鏍囩郴涓殑浣嶇疆锛堣创 view 鍙充笂瑙掞紝閬垮紑缃楃洏锛夈€?
    鎹㈢畻鏂瑰紡锛?      ClientToScreen(view, (0,0))      鈫?view 瀹㈡埛鍖哄乏涓婅鐨勫睆骞曞潗鏍?      ScreenToClient(mdi_hwnd, pt)     鈫?杞崲鍒?MDI 瀹㈡埛鍖哄潗鏍?    姣?MapWindowPoints 鏇村彲闈狅紝涓嶅彈 MDI 妗嗘灦鍐呴儴鍋忕Щ褰卞搷銆?    """
    client_w = win32gui.GetClientRect(view_hwnd)[2]
    # view 瀹㈡埛鍖哄乏涓婅 鈫?灞忓箷鍧愭爣
    pt_lt = POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(view_hwnd, ctypes.byref(pt_lt))
    # view 瀹㈡埛鍖哄彸涓婅灞忓箷鍧愭爣
    pt_rt = POINT(pt_lt.x + client_w, pt_lt.y)
    # 鎹㈢畻鍒?MDI 瀹㈡埛鍖哄潗鏍?    ctypes.windll.user32.ScreenToClient(mdi_hwnd, ctypes.byref(pt_lt))
    ctypes.windll.user32.ScreenToClient(mdi_hwnd, ctypes.byref(pt_rt))
    # 闄愬埗鍦?MDI 鑼冨洿鍐咃紝涓嶈秴鍑哄彸杈圭紭
    mdi_w   = win32gui.GetClientRect(mdi_hwnd)[2]
    right_x = min(pt_rt.x, mdi_w)
    panel_x = max(right_x - PANEL_W - MARGIN_RIGHT, pt_lt.x)
    panel_y = pt_lt.y + MARGIN_TOP
    return panel_x, panel_y

# 鈹€鈹€ 闈㈡澘绐楀彛杩囩▼ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

def _panel_wndproc(hwnd, msg, wparam, lparam):
    """闈㈡澘绐楀彛杩囩▼锛氬鐞嗘寜閽偣鍑诲拰鑳屾櫙缁樺埗銆?""
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

# 鈹€鈹€ 闈㈡澘鍒涘缓 / 鏇存柊 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def create_panel_for_view(view_hwnd):
    """
    涓?view_hwnd 鍦?MDIClient 涓嬪垱寤洪潰鏉匡紙鍒濆闅愯棌锛夈€?    鍒涘缓瀹屾垚鍚庣珛鍗宠皟鐢?update_all_panels 鍐冲畾鏄惁鏄剧ず銆?    """
    if view_hwnd in _state["panels"]:
        return
    mdi_hwnd  = _state["mdi_hwnd"]
    hinstance = win32api.GetModuleHandle(None)
    panel_x, panel_y = calc_panel_pos(view_hwnd, mdi_hwnd)

    # 鍒濆闅愯棌锛堟棤 WS_VISIBLE锛夛紝鐢?update_all_panels 鍐冲畾鏄鹃殣
    panel_hwnd = win32gui.CreateWindow(
        "CATIACopilotPanel", "",
        win32con.WS_CHILD | win32con.WS_CLIPSIBLINGS,
        panel_x, panel_y, PANEL_W, PANEL_H,
        mdi_hwnd, 0, hinstance, None,
    )
    if not panel_hwnd:
        print(f"  CreateWindow 澶辫触 view={view_hwnd} err={ctypes.windll.kernel32.GetLastError()}")
        return

    btn_hwnd = win32gui.CreateWindow(
        "BUTTON", "BOM 灞炴€цˉ鍏?,
        win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
        2, 2, PANEL_W - 4, PANEL_H - 4,
        panel_hwnd, ID_BTN_BOM, hinstance, None,
    )
    if _state["hfont"]:
        ctypes.windll.user32.SendMessageW(btn_hwnd, win32con.WM_SETFONT, _state["hfont"], 1)

    _state["panels"][view_hwnd] = panel_hwnd
    cls = win32gui.GetClassName(view_hwnd)
    print(f"  鍒涘缓闈㈡澘 view={view_hwnd}({cls}) panel={panel_hwnd} btn={btn_hwnd}")
    print(f"    MDI鍧愭爣=({panel_x},{panel_y})")
    update_all_panels()


def update_all_panels():
    """
    閬嶅巻鎵€鏈夐潰鏉匡細
    - 鍙樉绀哄綋鍓?Z 搴忔渶椤讹紙婵€娲伙級view 鐨勯潰鏉匡紝鍏朵綑闅愯棌
    - 瀵瑰彲瑙侀潰鏉块噸鏂拌绠楀潗鏍囧苟缃《锛圚WND_TOP锛夛紝璺熼殢 view 浣嶇疆/灏哄鍙樺寲
    - 绉婚櫎宸查攢姣?闅愯棌鐨?view 瀵瑰簲鐨勯潰鏉?    """
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
            print(f"  update_all_panels 寮傚父 view={view_hwnd}: {e}")

    for v in dead:
        del _state["panels"][v]
        print(f"  绉婚櫎宸插叧闂?view={v} 鐨勯潰鏉?)


def scan_new_views():
    """鎵弿 MDIClient 涓嬫柊鍑虹幇鐨?view锛屼负鍏跺垱寤洪潰鏉匡紙鏂板紑鏂囨。鏃惰Е鍙戯級銆?""
    mdi_hwnd = _state["mdi_hwnd"]
    if not mdi_hwnd:
        return
    for v in _enum_views(mdi_hwnd):
        if v not in _state["panels"]:
            create_panel_for_view(v)

# 鈹€鈹€ WinEventHook 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
        # OUTOFCONTEXT 妯″紡锛氬洖璋冨湪鎴戜滑鑷繁鐨勭嚎绋嬫墽琛岋紝鐩存帴鏇存柊闈㈡澘锛屼笉闃诲 CATIA
        update_all_panels()
    return _WinEventProc(_callback)

# 鈹€鈹€ 瀛椾綋 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _create_ui_font():
    """鍒涘缓 Arial 8pt 瀛椾綋锛屼笌 CATIA 妯″瀷鏍戝瓧浣撲竴鑷达紙96 DPI 涓?lfHeight=-11锛夈€?""
    lf = LOGFONT()
    lf.lfHeight   = -11
    lf.lfWeight   = 400
    lf.lfCharSet  = 0
    lf.lfQuality  = 2   # PROOF_QUALITY锛屾姉閿娇
    lf.lfFaceName = "Arial"
    return ctypes.windll.gdi32.CreateFontIndirectW(ctypes.byref(lf))

# 鈹€鈹€ 瀹夸富绐楀彛杩囩▼ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _host_wndproc(hwnd, msg, wparam, lparam):
    """
    瀹夸富绐楀彛锛堥殣钘忕殑 WS_POPUP锛夌殑绐楀彛杩囩▼銆?    浠呯敤浜庢壙杞藉畾鏃跺櫒娑堟伅锛屼笉鏄剧ず浠讳綍 UI銆?    """
    if msg == win32con.WM_TIMER:
        if wparam == TIMER_SCAN_VIEWS:
            scan_new_views()
            return 0
        elif wparam == TIMER_SIGINT:
            return 0   # 鍞ら啋娑堟伅寰幆锛岃 Python signal 鏈夋満浼氬鐞?Ctrl+C
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

# 鈹€鈹€ BOM 瀵硅瘽妗嗗惎鍔?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _launch_bom_dialog(parent_hwnd):
    """瀛愯繘绋嬪惎鍔?BomEditDialog锛圱ODO: 鏀逛负鍚岃繘绋嬬嚎绋嬶紝娑堥櫎 CATIA 闂儊锛夈€?""
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
            f"鍚姩 BOM 瀵硅瘽妗嗗け璐ワ細{e}",
            "CATIA Copilot - 閿欒",
            win32con.MB_OK | win32con.MB_ICONERROR,
        )

# 鈹€鈹€ 涓诲嚱鏁?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def main():
    catia_hwnd, mdi_hwnd = find_catia_mdi()
    if not catia_hwnd:
        print("ERROR: 鏈壘鍒?CATIA V5 涓荤獥鍙?)
        sys.exit(1)
    if not mdi_hwnd:
        print("ERROR: 鏈壘鍒?MDIClient")
        sys.exit(1)

    _, catia_pid = win32process.GetWindowThreadProcessId(catia_hwnd)
    print(f"CATIA hwnd={catia_hwnd}  pid={catia_pid}")
    print(f"MDI   hwnd={mdi_hwnd}   client={win32gui.GetClientRect(mdi_hwnd)}")

    hinstance = win32api.GetModuleHandle(None)
    _state["hfont"] = _create_ui_font()

    # 娉ㄥ唽瀹夸富绐楀彛绫?    wc_host = win32gui.WNDCLASS()
    wc_host.hInstance     = hinstance
    wc_host.lpszClassName = "CATIACopilotHost"
    wc_host.lpfnWndProc   = WNDPROCTYPE(_host_wndproc)
    wc_host.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    try:
        win32gui.RegisterClass(wc_host)
    except Exception as e:
        print(f"RegisterClass Host: {e} (蹇界暐)")

    # 娉ㄥ唽闈㈡澘绐楀彛绫?    wc_panel = win32gui.WNDCLASS()
    wc_panel.hInstance     = hinstance
    wc_panel.lpszClassName = "CATIACopilotPanel"
    wc_panel.lpfnWndProc   = WNDPROCTYPE(_panel_wndproc)
    wc_panel.hCursor       = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc_panel.hbrBackground = win32con.COLOR_BTNFACE + 1
    try:
        win32gui.RegisterClass(wc_panel)
    except Exception as e:
        print(f"RegisterClass Panel: {e} (蹇界暐)")

    # 娓呯悊涓婃杩愯娈嬬暀鐨勯潰鏉?    old_panels = []
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
        print(f"宸叉竻鐞?{len(old_panels)} 涓畫鐣欓潰鏉?)

    # 鍒涘缓瀹夸富绐楀彛锛堥殣钘忕殑 WS_POPUP锛屼粎鐢ㄤ簬鎵胯浇瀹氭椂鍣級
    host_hwnd = win32gui.CreateWindow(
        "CATIACopilotHost", "CATIACopilotHost",
        win32con.WS_POPUP,
        0, 0, 1, 1,
        0, 0, hinstance, None,
    )
    if not host_hwnd:
        print(f"ERROR: 鍒涘缓瀹夸富绐楀彛澶辫触 err={ctypes.windll.kernel32.GetLastError()}")
        sys.exit(1)

    _state["host_hwnd"]  = host_hwnd
    _state["mdi_hwnd"]   = mdi_hwnd
    _state["catia_hwnd"] = catia_hwnd
    _state["catia_pid"]  = catia_pid
    print(f"瀹夸富绐楀彛 hwnd={host_hwnd}")

    # 涓哄凡鏈?view 鍒涘缓闈㈡澘
    views = _enum_views(mdi_hwnd)
    print(f"鎵惧埌 {len(views)} 涓?3D 瑙嗗浘")
    for v in views:
        create_panel_for_view(v)

    # 娉ㄥ唽 WinEventHook锛岀洃鍚?CATIA 杩涚▼鐨勭獥鍙ｄ簨浠?    cb_ref = _make_event_callback()
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
    print(f"宸叉敞鍐?{len(_state['hook_handles'])} 涓?WinEventHook")

    ctypes.windll.user32.SetTimer(host_hwnd, TIMER_SCAN_VIEWS, SCAN_VIEWS_INTERVAL, None)

    import signal
    def _sigint(sig, frame):
        ctypes.windll.user32.PostQuitMessage(0)
    signal.signal(signal.SIGINT, _sigint)
    ctypes.windll.user32.SetTimer(host_hwnd, TIMER_SIGINT, 500, None)

    print("鉁?灏辩华銆傛寜 Ctrl+C 閫€鍑恒€?)
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
