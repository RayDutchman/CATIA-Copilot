"""
CATIA 单文档操作模块。

提供以单个 CATIA 文档为操作对象的工具函数，与 bom_collect.py（遍历产品树）
的区别在于粒度：本模块操作单个文件，不遍历产品树。

公开接口：
  get_document_type(doc)                 – 判断 COM 文档对象的类型（PartDocument 等）
  get_bom_node_type(product, parent_fp)  – 判断产品树节点类型（PART/PRODUCT/COMPONENT）
  find_open_document(file_path)          – 在已打开文档中按路径查找 COM 文档对象
  rename_document(file_path, new_pn, …)  – 通过 CATIA SaveAs 将文档另存为新文件名
  get_document_properties(file_path)     – 读取单个文档的属性（标准 + 用户自定义）
  set_document_properties(file_path, …) – 写入单个文档的属性
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pywintypes

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.constants import (
    PRODUCT_ATTR_READ_MAP,
    PRODUCT_ATTR_WRITE_MAP,
    DOC_EXT_TYPE_MAP,
    BomNodeType,
)

logger = logging.getLogger(__name__)

# Source 枚举值 ↔ 显示字符串（与 bom_collect 保持一致）
_SOURCE_TO_DISPLAY: dict[int, str] = {0: "Unknown", 1: "Made", 2: "Bought"}
_SOURCE_FROM_DISPLAY: dict[str, int] = {v: k for k, v in _SOURCE_TO_DISPLAY.items()}


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
        return isinstance(exc, pywintypes.com_error)
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def get_document_type(doc) -> str:
    """返回 CATIA COM 文档对象的类型字符串，与 VBScript TypeName() 一致。

    判断策略（优先级从高到低）：

    1. **后缀判断**（主路径）：通过 ``doc.FullName`` 的文件后缀快速判断。
       适用于已保存到磁盘的文档，准确且无副作用。

    2. **COM 属性探测**（后备）：当 ``FullName`` 无法提供有效后缀时
       （如文档从未保存，``FullName`` 为内存路径如 ``"Product1"``），
       通过探测 COM 对象上的特征属性来区分类型：

       - ``doc.Sheets`` 存在 → ``DrawingDocument``
       - ``doc.Part`` 存在  → ``PartDocument``
       - ``doc.Product`` 存在 → ``ProductDocument``

    参数
    ----
    doc:
        CATIA Document COM 对象（win32com dispatch）。

    返回
    ----
    ``"PartDocument"`` / ``"ProductDocument"`` / ``"DrawingDocument"`` /
    ``"Unknown"``
    """
    # 主路径：后缀判断
    try:
        ext = Path(doc.FullName).suffix.lower()
        if ext in DOC_EXT_TYPE_MAP:
            return DOC_EXT_TYPE_MAP[ext]
    except Exception:
        pass

    # 后备：COM 属性探测（处理未保存文档，FullName 无标准后缀）
    # 顺序很重要：先检查 Sheets（DrawingDocument 独有），
    # 再检查 Part（CATPart 独有），最后检查 Product（两者都有但 Part 已排除）
    try:
        _ = doc.Sheets
        return "DrawingDocument"
    except Exception:
        pass
    try:
        _ = doc.Part
        return "PartDocument"
    except Exception:
        pass
    try:
        _ = doc.Product
        return "ProductDocument"
    except Exception:
        pass

    logger.debug("get_document_type: 无法判断文档类型 %s", getattr(doc, "Name", "?"))
    return "Unknown"


def get_bom_node_type(product, parent_filepath: str, filepath: str | None = None) -> str:
    """判断产品树节点的 BOM 类型。

    封装 bom_collect / mass_props_collect / plm.sync 三处完全相同的判断逻辑，
    统一维护。

    判断规则（优先级从高到低）：

    1. ``filepath`` 为空（CATIA 无法解析文件引用）→ ``""``
    2. ``filepath == parent_filepath``（嵌入式部件，与父节点共享同一文件）
       → ``BomNodeType.COMPONENT``
    3. 后缀 ``.catpart`` → ``BomNodeType.PART``
    4. 其他（``.catproduct`` 或未知后缀）→ ``BomNodeType.PRODUCT``

    参数
    ----
    product:
        CATIA Product COM 对象（win32com dispatch）。
    parent_filepath:
        父节点的文件完整路径。根节点传空字符串 ``""``。
    filepath:
        可选。若调用方已通过 ``get_product_filepath`` 或
        ``product.ReferenceProduct.Parent.FullName`` 获取了路径，
        直接传入以避免重复 COM 调用。为 ``None`` 时函数内部自行获取。

    返回
    ----
    ``BomNodeType.PART`` / ``BomNodeType.PRODUCT`` / ``BomNodeType.COMPONENT``
    / ``""``（无法解析时）
    """
    if filepath is None:
        try:
            filepath = product.ReferenceProduct.Parent.FullName
        except Exception:
            filepath = ""

    if not filepath:
        return ""
    if bool(parent_filepath) and filepath == parent_filepath:
        return BomNodeType.COMPONENT
    ext = Path(filepath).suffix.lower()
    return BomNodeType.PART if ext == ".catpart" else BomNodeType.PRODUCT


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


# ---------------------------------------------------------------------------
# 文档属性读写
# ---------------------------------------------------------------------------


def _get_product_from_doc(doc):
    """从 COM 文档对象中提取 Product 对象（CATPart / CATProduct 均适用）。

    - CATProduct：直接返回 doc.Product
    - CATPart：通过 doc.Part 的 ReferenceProduct 获取（若可用），否则返回 None
    - CATDrawing 等：返回 None（无 Product 概念）
    """
    # CATProduct
    try:
        return doc.Product
    except Exception:
        pass
    # CATPart — Part 对象本身不是 Product，但可通过 ReferenceProduct 访问
    try:
        return doc.Part.ReferenceProduct
    except Exception:
        pass
    return None


def get_document_properties(
    file_path: str,
) -> dict:
    """读取 CATIA 文档的属性，包括标准属性和用户自定义属性。

    参数
    ----
    file_path:
        目标文档的完整路径。文档必须已在 CATIA 中打开。

    返回
    ----
    包含以下键的字典：

    ``standard``
        标准属性字典，键为属性名（如 ``"Part Number"``、``"Revision"``），
        值为字符串。包含：Part Number、Nomenclature、Revision、Definition、
        Source、Description。

    ``user_defined``
        用户自定义属性字典，键为属性名，值为字符串。
        读取失败的属性会被静默跳过。

    ``file_path``
        文档的完整路径（来自 COM ``FullName``）。

    ``doc_type``
        文档类型：``"PartDocument"``、``"ProductDocument"``、
        ``"DrawingDocument"`` 或 ``"Other"``。

    异常
    ----
    ``FileNotFoundError``
        文档未在 CATIA 中打开。
    ``RuntimeError``
        CATIA 未连接，或文档类型不支持属性读取（如 CATDrawing）。
    """

    app = get_catia_v5_application()
    doc = find_open_document(file_path)
    if doc is None:
        raise FileNotFoundError(
            f"文档未在 CATIA 中打开：{file_path}\n"
            "请先用 open_catia_file 打开该文档。"
        )

    full_name = doc.FullName
    doc_type  = get_document_type(doc)

    product = _get_product_from_doc(doc)
    if product is None:
        raise RuntimeError(
            f"文档类型 {doc_type} 不支持属性读取（无 Product 对象）。"
        )

    # --- 读取标准属性 ---
    standard: dict[str, str] = {}
    for display_name, com_attr in PRODUCT_ATTR_READ_MAP.items():
        # 优先从 ReferenceProduct 读，回退到 product 本身
        targets = []
        try:
            targets.append(product.ReferenceProduct)
        except Exception:
            pass
        targets.append(product)

        for target in targets:
            try:
                raw = getattr(target, com_attr)
                if raw is None:
                    continue
                if display_name == "Source":
                    standard[display_name] = _SOURCE_TO_DISPLAY.get(int(raw), str(raw))
                else:
                    standard[display_name] = str(raw)
                break
            except Exception:
                pass
        else:
            standard[display_name] = ""

    # --- 读取用户自定义属性 ---
    user_defined: dict[str, str] = {}
    targets = []
    try:
        targets.append(product.ReferenceProduct)
    except Exception:
        pass
    targets.append(product)

    for target in targets:
        try:
            props = target.UserRefProperties
            for i in range(1, props.Count + 1):
                try:
                    p = props.Item(i)
                    name = p.Name
                    if name not in user_defined:
                        val = p.Value
                        user_defined[name] = str(val) if val is not None else ""
                except Exception:
                    pass
            break  # 成功读取后不再尝试下一个 target
        except Exception:
            pass

    logger.debug(
        "get_document_properties: %s — %d standard, %d user_defined",
        Path(full_name).name, len(standard), len(user_defined),
    )
    return {
        "file_path":    full_name,
        "doc_type":     doc_type,
        "standard":     standard,
        "user_defined": user_defined,
    }


def set_document_properties(
    file_path: str,
    standard: dict[str, str] | None = None,
    user_defined: dict[str, str] | None = None,
    save: bool = False,
) -> dict:
    """写入 CATIA 文档的属性。

    参数
    ----
    file_path:
        目标文档的完整路径。文档必须已在 CATIA 中打开。
    standard:
        要写入的标准属性字典。支持的键：
        ``Part Number``、``Nomenclature``、``Revision``、
        ``Definition``、``Source``、``Description``。
        ``Description`` 通过 COM 属性 ``DescriptionRef`` 写入，经实测可写。
    user_defined:
        要写入的用户自定义属性字典。属性不存在时自动创建（CreateString）。
    save:
        写入完成后是否立即保存文档（调用 ``doc.Save()``）。
        默认 False，由调用方决定何时保存。

    返回
    ----
    包含以下键的字典：

    ``written_standard``
        成功写入的标准属性名列表。

    ``written_user_defined``
        成功写入的用户自定义属性名列表。

    ``skipped``
        被跳过的属性名列表（只读属性或写入失败）。

    ``saved``
        是否执行了保存操作。

    异常
    ----
    ``FileNotFoundError``
        文档未在 CATIA 中打开。
    ``RuntimeError``
        CATIA 未连接，或文档类型不支持属性写入。
    """

    get_catia_v5_application()  # 确认 CATIA 已连接，未连接时抛出 RuntimeError
    doc = find_open_document(file_path)
    if doc is None:
        raise FileNotFoundError(
            f"文档未在 CATIA 中打开：{file_path}\n"
            "请先用 open_catia_file 打开该文档。"
        )

    product = _get_product_from_doc(doc)
    if product is None:
        raise RuntimeError(
            "文档类型不支持属性写入（无 Product 对象）。"
        )

    written_standard:     list[str] = []
    written_user_defined: list[str] = []
    skipped:              list[str] = []

    # --- 写入标准属性 ---
    for name, value in (standard or {}).items():
        com_attr = PRODUCT_ATTR_WRITE_MAP.get(name)
        if com_attr is None:
            logger.warning("set_document_properties: 未知标准属性 %s，已跳过", name)
            skipped.append(name)
            continue

        # 优先写 ReferenceProduct，回退到 product 本身
        targets = []
        try:
            targets.append(product.ReferenceProduct)
        except Exception:
            pass
        targets.append(product)

        written = False
        for target in targets:
            try:
                if name == "Source":
                    int_val = _SOURCE_FROM_DISPLAY.get(value, None)
                    if int_val is None:
                        try:
                            int_val = int(value)
                        except (ValueError, TypeError):
                            int_val = 0
                    setattr(target, com_attr, int_val)
                elif name == "Part Number":
                    target.PartNumber = value
                else:
                    setattr(target, com_attr, value)
                written = True
                break
            except Exception as e:
                logger.debug("set_document_properties: 写 %s 到 %s 失败: %s", name, target, e)

        if written:
            written_standard.append(name)
        else:
            skipped.append(name)

    # --- 写入用户自定义属性 ---
    targets = []
    try:
        targets.append(product.ReferenceProduct)
    except Exception:
        pass
    targets.append(product)

    for name, value in (user_defined or {}).items():
        written = False
        # 先尝试更新已有属性
        for target in targets:
            try:
                target.UserRefProperties.Item(name).Value = value
                written = True
                break
            except Exception:
                pass
        # 属性不存在则创建
        if not written:
            for target in targets:
                try:
                    target.UserRefProperties.CreateString(name, value)
                    written = True
                    break
                except Exception:
                    pass

        if written:
            written_user_defined.append(name)
        else:
            skipped.append(name)
            logger.warning("set_document_properties: 无法写入用户属性 %s", name)

    # --- 可选保存 ---
    saved = False
    if save:
        try:
            doc.Save()
            saved = True
            logger.info("set_document_properties: 已保存 %s", Path(file_path).name)
        except Exception as e:
            logger.warning("set_document_properties: 保存失败 %s: %s", file_path, e)

    logger.info(
        "set_document_properties: %s — standard=%s, user_defined=%s, skipped=%s",
        Path(file_path).name, written_standard, written_user_defined, skipped,
    )
    return {
        "written_standard":     written_standard,
        "written_user_defined": written_user_defined,
        "skipped":              skipped,
        "saved":                saved,
    }
