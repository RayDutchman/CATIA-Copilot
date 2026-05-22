"""
CATIA COM 操作通用工具函数。
"""

import logging

logger = logging.getLogger(__name__)


def open_catia_file(documents, file_path: str, foreground: bool = False):
    """在 CATIA 中打开（或切换到）指定文件，返回文档对象。

    - 若文件已在 documents 集合中且有独立窗口：调用 ``doc.Activate()`` 切换，
      不重新打开（避免 CATIA 弹出"是否重新加载"询问）。
    - 若文件仅在内存中（子零件加载态，无独立窗口）或尚未打开：调用
      ``documents.Open()``，让 CATIA 打开独立文档窗口。
    - 路径比对使用小写，兼容 Windows 文件系统大小写不敏感特性。
    - 不负责 ``application.Visible`` 控制，由调用方按需设置。
    - 传给 COM 的路径直接使用原始字符串，不经额外转换。

    Args:
        documents:   CATIA ``Application.Documents`` COM 对象。
        file_path:   要打开的文件的 Windows 绝对路径字符串（如 ``D:\\foo\\bar.CATPart``）。
        foreground:  为 True 时，操作完成后额外调用 bring_catia_to_foreground()
                     将 CATIA V5 主窗口置于 Windows 桌面前台。默认 False。

    Returns:
        对应的 CATIA 文档 COM 对象。

    Raises:
        RuntimeError: ``documents.Open()`` 返回 None 时抛出。
    """
    file_path_lower = file_path.lower()

    # 检查是否已在 documents 集合中
    doc = None
    for i in range(1, documents.Count + 1):
        try:
            d = documents.Item(i)
            if d.FullName.lower() == file_path_lower:
                doc = d
                break
        except Exception:
            pass

    if doc is not None:
        # 已在集合中：尝试 Activate，再验证是否真正切换成功
        logger.debug(f"open_catia_file: 已打开，尝试 Activate → {file_path}")
        activated = False
        try:
            doc.Activate()
            # 验证切换是否成功（有独立窗口的文档 Activate 后会成为 ActiveDocument）
            try:
                active_name = documents.Application.ActiveDocument.FullName
                if active_name.lower() == file_path_lower:
                    activated = True
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"open_catia_file: Activate() 失败：{e}")

        if not activated:
            # 无独立窗口（仅在内存中），需要 Open 打开窗口
            logger.debug(f"open_catia_file: Activate 未切换成功，改用 documents.Open → {file_path}")
            doc = documents.Open(file_path)
            if doc is None:
                raise RuntimeError(
                    f"documents.Open() 返回 None，CATIA 可能无法打开该文件：{file_path}"
                )
    else:
        # 未在集合中：直接 Open
        logger.debug(f"open_catia_file: 未打开，documents.Open → {file_path}")
        doc = documents.Open(file_path)
        logger.debug(f"open_catia_file: Open 返回 {doc}")
        if doc is None:
            raise RuntimeError(
                f"documents.Open() 返回 None，CATIA 可能无法打开该文件：{file_path}"
            )

    if foreground:
        bring_catia_to_foreground()

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
