"""通过 pywinauto Win32 读取并响应 CATIA 对话框。"""

import hashlib
import os
import sys
import ctypes
import warnings
from ctypes import wintypes

import win32gui
import win32process

# 导入前指定 STA，与 CATIA 操作进程保持一致。
sys.coinit_flags = 2

with warnings.catch_warnings():
    warnings.filterwarnings('ignore', message='Apply externally defined coinit_flags: 2', category=UserWarning, module='pywinauto')
    from pywinauto import Desktop

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL


def _visible_window_pids():
    pids = set()

    def collect(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindow(hwnd, 4) == 0:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pids.add(pid)
        return True

    win32gui.EnumWindows(collect, None)
    return pids


def _process_executable_name(pid):
    query_limited_information = 0x1000
    handle = _kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value)
    finally:
        _kernel32.CloseHandle(handle)


def catia_process_ids() -> list[int]:
    return sorted(pid for pid in _visible_window_pids()
                  if (_process_executable_name(pid) or "").casefold() == "cnext.exe")


def _owner(hwnd):
    return win32gui.GetWindow(hwnd, 4)


def _children(window):
    try:
        return window.descendants()
    except Exception:
        return []


def _control_entry(control):
    try:
        text = control.window_text()
    except Exception:
        text = ""
    try:
        enabled = bool(control.is_enabled())
    except Exception:
        enabled = False
    return {"id": str(control.handle), "text": text or "", "enabled": enabled}


def _window_text(window, children):
    values = []
    for control in children:
        try:
            class_name = control.class_name().casefold()
            value = control.window_text() if class_name in {
                "static", "edit", "button"} else ""
        except Exception:
            value = ""
        if value and value not in values:
            values.append(value)
    return "\n".join(values)


def _dialog_record(window, pid):
    children = _children(window)
    buttons, edits = [], []
    for control in children:
        try:
            class_name = control.class_name().casefold()
        except Exception:
            class_name = ""
        item = _control_entry(control)
        if class_name == "button":
            buttons.append(item)
        elif class_name == "edit":
            edits.append(item)
    try:
        title = window.window_text() or ""
    except Exception:
        title = ""
    text = _window_text(window, children)
    signature = repr((window.handle, pid, title, text, buttons, edits))
    fingerprint = hashlib.sha256(signature.encode("utf-8", "replace")).hexdigest()
    return {"id": str(window.handle), "hwnd": int(window.handle), "process_id": int(pid),
            "title": title, "text": text, "buttons": buttons, "edits": edits,
            "fingerprint": fingerprint}


def scan_dialogs(process_ids: list[int]) -> list[dict]:
    result = []
    desktop = Desktop(backend="win32")
    for requested_pid in process_ids:
        for window in desktop.windows(process=requested_pid):
            try:
                hwnd = int(window.handle)
                pid = int(window.process_id())
                if pid != requested_pid or not win32gui.IsWindowVisible(hwnd):
                    continue
                class_name = window.class_name()
                modal_custom = _owner(hwnd) and not win32gui.IsWindowEnabled(_owner(hwnd))
                if class_name != "#32770" and not modal_custom:
                    continue
                folded_class = class_name.casefold()
                if "tooltip" in folded_class or "longhelp" in folded_class:
                    continue
                record = _dialog_record(window, pid)
                # 点击后窗口可能在枚举期间销毁，不能把拆卸中的空控件当作新弹窗。
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    continue
                if not record['title'] and not record['text'] and not any(
                        item['enabled'] for item in record['buttons'] + record['edits']):
                    continue
                result.append(record)
            except Exception:
                continue
    return result


def respond(dialog: dict, *, button: str | None = None,
            control: str | None = None, text: str | None = None) -> None:
    current = next((item for item in scan_dialogs([dialog["process_id"]])
                    if item["hwnd"] == dialog["hwnd"]), None)
    if current is None or current["fingerprint"] != dialog["fingerprint"]:
        raise ValueError("Dialog is stale or its fingerprint changed")
    if button is None and control is None:
        raise ValueError("Specify a button or edit control")
    if button is not None and control is not None:
        raise ValueError("Specify only one target control")
    target_id = button if button is not None else control
    items = current["buttons"] if button is not None else current["edits"]
    if target_id not in {item["id"] for item in items}:
        raise ValueError("Target control does not belong to the scanned dialog")
    hwnd = int(target_id)
    if not win32gui.IsChild(dialog["hwnd"], hwnd):
        raise ValueError("Target control is no longer owned by the dialog")
    item = next(item for item in items if item["id"] == target_id)
    if not item["enabled"]:
        raise ValueError("Target control is disabled")
    if control is not None:
        if text is None:
            raise ValueError("Text is required for an edit control")
        wrapper = Desktop(backend="win32").window(handle=hwnd).wrapper_object()
        wrapper.set_edit_text(text)
        return
    win32gui.PostMessage(hwnd, 0x00F5, 0, 0)  # BM_CLICK，异步投递
