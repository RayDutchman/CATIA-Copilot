"""
CATIA COM 操作通用工具函数。
"""

import logging

logger = logging.getLogger(__name__)


def open_catia_file(documents, file_path: str):
    """通过 COM 在 CATIA 中打开文件，返回文档对象。

    - 无论文件是否已在 CATIA 中打开，均调用 ``documents.Open()``——
      CATIA V5 对已打开的文件会自动切换到对应文档窗口，无副作用。
    - 不负责 ``application.Visible``、置前台等 UI 操作，由调用方按需处理。
    - 不经 ``Path.resolve()``，避免在 WSL 环境下将 Windows 路径转为 /mnt/d/... 格式。

    Args:
        documents: CATIA ``Application.Documents`` COM 对象。
        file_path: 要打开的文件的 Windows 绝对路径字符串（如 ``D:\\foo\\bar.CATPart``）。

    Returns:
        打开后的 CATIA 文档 COM 对象。

    Raises:
        RuntimeError: ``documents.Open()`` 返回 None 时抛出。
    """
    logger.debug(f"open_catia_file: documents.Open({file_path})")
    doc = documents.Open(file_path)
    logger.debug(f"open_catia_file: 返回 {doc}")
    if doc is None:
        raise RuntimeError(
            f"documents.Open() 返回 None，CATIA 可能无法打开该文件：{file_path}"
        )
    return doc


def bring_catia_to_foreground() -> None:
    """将 CATIA V5 主窗口置于 Windows 前台。

    枚举所有顶层窗口，找到标题以 "CATIA V5" 开头的第一个可见窗口，
    先恢复（SW_RESTORE）再置前台（SetForegroundWindow）。

    - 回调内部对每个窗口的操作均有异常保护，不会因个别系统窗口权限问题中断枚举。
    - 若找不到 CATIA V5 窗口，记录 warning 但不抛出异常。
    """
    try:
        import win32gui  # type: ignore[import]
        import win32con  # type: ignore[import]
    except ImportError:
        logger.warning("bring_catia_to_foreground: win32gui 不可用，跳过置前台")
        return

    catia_hwnd = 0

    def _enum_cb(hwnd: int, _) -> bool:
        nonlocal catia_hwnd
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetWindowText(hwnd).startswith("CATIA V5"):
                catia_hwnd = hwnd
                return False  # 停止枚举
        except Exception:
            pass  # 某些系统窗口访问受限，静默跳过
        return True

    try:
        win32gui.EnumWindows(_enum_cb, None)
    except Exception as e:
        # EnumWindows 在回调返回 False 时也会抛异常（正常终止机制），忽略
        logger.debug(f"bring_catia_to_foreground: EnumWindows 结束：{e}")

    if catia_hwnd:
        try:
            win32gui.ShowWindow(catia_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(catia_hwnd)
            logger.debug(f"bring_catia_to_foreground: SetForegroundWindow hwnd={catia_hwnd}")
        except Exception as e:
            logger.warning(f"bring_catia_to_foreground: 置前台失败（已忽略）：{e}")
    else:
        logger.warning("bring_catia_to_foreground: 未找到 CATIA V5 主窗口")
