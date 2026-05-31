"""
CATIA 单文档操作模块。

提供以单个 CATIA 文档为操作对象的工具函数，与 bom_collect.py（遍历产品树）
的区别在于粒度：本模块操作单个文件，不遍历产品树。

公开接口：
  find_open_document(file_path)          – 在已打开文档中按路径查找 COM 文档对象
  rename_document(file_path, new_pn, …)  – 通过 CATIA SaveAs 将文档另存为新文件名

后续可扩展：
  get_document_properties(…)             – 读取单个文档的属性（标准 + 用户自定义）
  set_document_properties(…)             – 写入单个文档的属性
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _is_catia_com_error(exc: Exception) -> bool:
    """如果 *exc* 是来自 CATIA COM 层的 ``pywintypes.com_error`` 则返回 True。

    用于区分用户主动取消信号（用户在 CATIA 自己的 SaveAs 对话框中点击取消或否时，
    CATIA 会抛出 COM 错误）与真正的操作系统级错误（如磁盘已满或权限拒绝）。
    后者是普通 Python 异常，必须始终报告给用户。
    """
    try:
        import pywintypes  # noqa: PLC0415
        return isinstance(exc, pywintypes.com_error)
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def find_open_document(file_path: str):
    """在 CATIA 已打开文档中按路径查找，返回 COM 文档对象或 None。

    只检查已打开文档，不会主动打开文件。路径比对使用 ``Path.resolve()``
    以兼容 Windows 大小写不敏感和符号链接。

    参数
    ----
    file_path:
        要查找的文件完整路径。

    返回
    ----
    COM 文档对象，或 None（未找到时）。
    """
    from catia_copilot.catia.connection import get_catia_v5_application

    target = Path(file_path).resolve()
    try:
        app   = get_catia_v5_application()
        docs  = app.Documents
        for i in range(1, docs.Count + 1):
            try:
                d = docs.Item(i)
                if Path(d.FullName).resolve() == target:
                    return d
            except Exception:
                pass
    except Exception as e:
        logger.debug("find_open_document: 无法访问 CATIA Documents：%s", e)
    return None


def rename_document(
    file_path: str,
    new_part_number: str,
    delete_old: bool = False,
    *,
    target_path: str | None = None,
) -> tuple[str, bool]:
    """将 CATIA 文档另存为新路径（SaveAs）。

    参数
    ----
    file_path:
        源文件的完整路径。文件可以尚未保存到磁盘（在 CATIA 内存中打开即可）。
    new_part_number:
        新零件编号，将作为新文件名的 stem（不含扩展名）。
        当 ``target_path`` 不为 None 时，此参数仅用于日志，不影响目标路径。
    delete_old:
        SaveAs 成功后是否删除旧文件。仅当旧文件实际存在于磁盘时才执行删除。
    target_path:
        可选。指定完整的目标文件路径（含目录和文件名）。
        不为 None 时优先使用，忽略 ``new_part_number`` 的目录推导。
        适用于用户自选目标路径的场景（如 _rename_selected_file）。

    返回
    ----
    ``(new_file_path, was_skipped_by_user)``

    - ``new_file_path``：新文件的完整路径（即使 was_skipped_by_user=True 也返回预期路径）。
    - ``was_skipped_by_user``：True 表示用户在 CATIA 的 SaveAs 对话框中主动取消，
      不是错误，调用方可以选择静默跳过。

    异常
    ----
    非 COM 错误（OSError、PermissionError 等）直接向上抛出，由调用方处理。

    设计说明
    --------
    - ``_is_catia_com_error`` 判断在底层做，因为它依赖 COM 异常类型。
    - ``delete_old`` 的 ``os.remove`` 在本函数内执行（属于文件系统操作，
      但与 SaveAs 结果强耦合，放在底层更安全，避免调用方遗漏）。
    - 不使用 doc_cache：每次调用内部自行查找文档，避免调用方持有 COM 对象引用。
      批量场景下文件数量通常不超过几十个，性能影响可忽略。
    """
    from catia_copilot.catia.connection import get_catia_v5_application

    src    = Path(file_path).resolve()
    ext    = Path(file_path).suffix
    # target_path 优先；未指定时在同目录下用新零件编号构造目标路径
    new_fp = str(target_path) if target_path is not None else \
             str(Path(file_path).parent / (new_part_number + ext))

    target_existed_before = Path(new_fp).exists()
    file_on_disk          = src.exists()

    app       = get_catia_v5_application()
    app.Visible = True
    documents = app.Documents

    # 查找文档：先在已打开文档中找，找不到则打开
    target_doc = None
    for i in range(1, documents.Count + 1):
        try:
            d = documents.Item(i)
            if Path(d.FullName).resolve() == src:
                target_doc = d
                break
        except Exception:
            pass

    if target_doc is None and file_on_disk:
        documents.Open(str(src))
        candidate = documents.Item(documents.Count)
        try:
            if Path(candidate.FullName).resolve() == src:
                target_doc = candidate
        except Exception:
            pass
        # 若 Item(Count) 不匹配（极少数情况），再扫描一遍
        if target_doc is None:
            for i in range(1, documents.Count + 1):
                try:
                    d = documents.Item(i)
                    if Path(d.FullName).resolve() == src:
                        target_doc = d
                        break
                except Exception:
                    pass

    if target_doc is None:
        raise FileNotFoundError(
            f"无法在 CATIA 中找到或打开文档：{file_path}\n"
            "请确认该文件已在 CATIA 中打开。"
        )

    try:
        target_doc.SaveAs(new_fp)
    except Exception as e:
        # 判断是否为用户主动取消（COM 错误 + 源文件完好 + 目标文件未被创建）
        source_intact = not file_on_disk or src.exists()
        if _is_catia_com_error(e) and source_intact and (
            target_existed_before or not Path(new_fp).exists()
        ):
            logger.info(
                "rename_document: SaveAs skipped for %s "
                "(user cancelled or declined overwrite in CATIA; exception: %s)",
                src.name, e,
            )
            return new_fp, True  # was_skipped_by_user = True
        raise  # 非用户取消，向上抛出

    # SaveAs 成功，按需删除旧文件
    if delete_old and src != Path(new_fp).resolve():
        try:
            os.remove(file_path)
        except Exception as del_err:
            logger.warning("rename_document: 删除旧文件失败 %s: %s", file_path, del_err)

    logger.info("rename_document: %s -> %s", src.name, Path(new_fp).name)
    return new_fp, False  # was_skipped_by_user = False
