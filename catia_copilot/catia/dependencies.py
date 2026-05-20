"""
CATIA 依赖项查找器。

提供：
- find_dependencies() – 收集目标 CATIA 文件依赖的所有文档
"""

import logging
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def find_dependencies(
    target_path: str,
    progress_callback: Callable[[str], None] | None = None,
) -> list[str]:
    """返回 *target_path* 依赖的所有文件的完整路径。

    在运行中的 CATIA 实例中打开目标文件；CATIA 会自动加载所有引用的文档。
    该函数收集每个新打开文档的路径，然后在返回前关闭所有这些文档。

    参数
    ----------
    target_path:
        ``.CATPart``、``.CATProduct`` 或 ``.CATDrawing`` 的绝对路径。
    progress_callback:
        可选的 ``callable(str)``，在搜索运行时使用状态消息调用。
    """
    from catia_copilot.catia.connection import get_catia_v5_application

    target      = Path(target_path).resolve()
    application = get_catia_v5_application()
    application.Visible = True
    documents   = application.Documents

    # 在我们执行任何操作之前，已打开文档的快照
    already_open: set[Path] = set()
    for i in range(1, documents.Count + 1):
        try:
            already_open.add(Path(documents.Item(i).FullName).resolve())
        except Exception:
            pass

    logger.info(f"Opening target for dependency scan: {target}")
    if progress_callback:
        progress_callback("正在打开文件，请稍候…")

    documents.Open(str(target))

    results:      list[str]  = []
    newly_opened: set[Path]  = set()

    for i in range(1, documents.Count + 1):
        try:
            doc      = documents.Item(i)
            doc_path = Path(doc.FullName).resolve()
            if doc_path == target or doc_path in already_open:
                continue
            newly_opened.add(doc_path)
            results.append(str(doc_path))
            logger.info(f"  Dependency: {doc_path}")
        except Exception as e:
            logger.debug(f"  Could not read document {i}: {e}")

    # 关闭我们打开的所有文档（目标文件最后关闭）
    for i in range(documents.Count, 0, -1):
        try:
            doc      = documents.Item(i)
            doc_path = Path(doc.FullName).resolve()
            if doc_path in newly_opened or doc_path == target:
                doc.Close()
        except Exception:
            pass

    logger.info(
        f"Dependency scan complete: {len(results)} found for {target.name}"
    )
    return results
