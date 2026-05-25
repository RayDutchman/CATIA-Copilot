"""
CATPart 模板刷写。

提供：
- apply_part_template() – 向 CATPart 文件添加标准用户自定义属性
"""

import logging
from pathlib import Path

from catia_copilot.constants import PRESET_USER_REF_PROPERTIES

logger = logging.getLogger(__name__)


def _find_open_document(application, resolved_path: Path):
    """如果 *resolved_path* 已打开则返回 CATIA 文档对象，否则返回 ``None``。"""
    try:
        docs = application.Documents
        for i in range(1, docs.Count + 1):
            try:
                doc = docs.Item(i)  # single COM call; reused below
                full_name = doc.FullName
                if Path(full_name).resolve() == resolved_path:
                    return doc
            except Exception:
                continue
    except Exception:
        pass
    return None


def apply_part_template(
    file_paths: list[str],
    output_folder: str | None = None,
    *,
    progress_callback=None,
    keep_open: bool = False,
) -> tuple[int, list[str]]:
    """如果缺少标准用户自定义属性，则向每个 CATPart 添加这些属性。

    属性以空字符串添加，并在刷写后自动保存文件。*output_folder* 为与通用
    :class:`~catia_copilot.ui.convert_dialog.FileConvertDialog` 的 API
    兼容性而接受，但实际不使用（零件就地保存）。

    参数
    ----------
    progress_callback:
        可选的 ``(index, total)`` 回调，在处理每个文件后调用，与
        :class:`~catia_copilot.ui.convert_dialog.FileConvertDialog` 兼容。
    keep_open:
        当为 ``True`` 时，刷写后**不**关闭文档。在操作已在 CATIA 中打开的
        文档时使用此选项（例如通过"使用当前活动文档"选择的当前活动文档）。
        当为 ``False``（默认值）时，如果文件尚未打开则打开，并且仅在刷写前
        未打开时才在之后关闭。

    返回
    -------
    tuple[int, list[str]]
        ``(success_count, failed_messages)``，其中 *failed_messages* 包含
        每个无法刷写的文件的一个人类可读字符串。
    """
    from catia_copilot.catia.connection import get_catia_v5_application

    application = get_catia_v5_application()
    application.Visible = True

    succeeded: list[str] = []
    failed:    list[str] = []
    total = len(file_paths)

    for idx, path in enumerate(file_paths):
        src = Path(path)
        was_already_open = False
        opened_doc = None  # the document we opened ourselves (if any)
        try:
            if keep_open:
                # The document is already active in CATIA – skip re-opening it.
                logger.info(f"Stamping active document: {src.name}")
                doc = application.ActiveDocument
                was_already_open = True
            else:
                src = src.resolve()
                existing_doc = _find_open_document(application, src)
                if existing_doc is not None:
                    logger.info(f"File already open, reusing: {src.name}")
                    was_already_open = True
                    existing_doc.Activate()
                else:
                    logger.info(f"Opening: {src}")
                    application.Documents.Open(str(src))
                    # Capture the document we just opened so that the finally
                    # block closes exactly this doc (not whatever is active later).
                    opened_doc = application.ActiveDocument
                doc = application.ActiveDocument

            product    = doc.Product
            user_props = product.UserRefProperties

            added: list[str] = []
            for prop_name in PRESET_USER_REF_PROPERTIES:
                # 用 Item(name) 探测属性是否已存在；找不到时 CATIA 会抛 COM 错误
                already = False
                try:
                    user_props.Item(prop_name)
                    already = True
                except Exception:
                    pass

                if not already:
                    try:
                        user_props.CreateString(prop_name, "")
                        added.append(prop_name)
                        logger.info(f"  Added property: '{prop_name}'")
                    except Exception as ce:
                        logger.warning(f"  CreateString('{prop_name}') 失败: {ce}")
                else:
                    logger.info(f"  Skipped (already exists): '{prop_name}'")

            try:
                doc.Save()
                logger.info(f"  Saved: {src.name}")
            except Exception as save_err:
                logger.warning(f"  Could not save {src.name} (may be unsaved): {save_err}")

            succeeded.append(f"{src.name} (+{len(added)} added)")

        except Exception as e:
            logger.error(f"  ERROR processing {src.name}: {e}")
            failed.append(f"{src.name}: {e}")
        finally:
            if not was_already_open and opened_doc is not None:
                try:
                    opened_doc.Close()
                except Exception:
                    pass

        if progress_callback is not None:
            try:
                progress_callback(idx, total)
            except Exception:
                pass

    return len(succeeded), failed
