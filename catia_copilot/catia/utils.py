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
