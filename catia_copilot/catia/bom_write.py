"""
BOM 写回 CATIA 模块。

提供：
- write_bom_to_catia() – 遍历产品树并通过 COM 将编辑后的属性写回
- write_cell()    – 通过缓存 COM 引用直接写入单个单元格（无树遍历）
"""

import logging
from collections.abc import Callable
from pathlib import Path

from catia_copilot.constants import (
    BOM_READONLY_COLUMNS,
    SOURCE_FROM_DISPLAY,
    PRODUCT_ATTR_WRITE_MAP,
    CATIA_DESIGN_MODE,
    BOM_INSTANCE_NAME_COLUMN,
)
from catia_copilot.catia.connection import get_catia_v5_application

logger = logging.getLogger(__name__)


def _ensure_design_mode(product) -> None:
    """将产品切换到设计模式（如已处于设计模式则跳过）。"""
    try:
        if product.GetWorkMode() != CATIA_DESIGN_MODE:
            product.ApplyWorkMode(CATIA_DESIGN_MODE)
    except Exception:
        try:
            product.ApplyWorkMode(CATIA_DESIGN_MODE)
        except Exception:
            pass


def _set_prop(product, name: str, value: str) -> None:
    """通过 COM 写入内置产品属性（优先写 ReferenceProduct）。"""
    attr = PRODUCT_ATTR_WRITE_MAP.get(name)
    if not attr:
        return
    targets: list = []
    try:
        targets.append(product.ReferenceProduct)
    except Exception:
        pass
    targets.append(product)
    for target in targets:
        try:
            setattr(
                target,
                attr,
                int(SOURCE_FROM_DISPLAY.get(value, value))
                if name == "Source" else value,
            )
            return
        except Exception:
            continue


def _set_user_prop(product, name: str, value: str) -> None:
    """通过 COM 写入用户自定义属性（优先写 ReferenceProduct）。"""
    targets: list = []
    try:
        targets.append(product.ReferenceProduct)
    except Exception:
        pass
    targets.append(product)
    # Try to update an existing property first
    for target in targets:
        try:
            target.UserRefProperties.Item(name).Value = value
            return
        except Exception:
            pass
    # Property does not exist – create it on the first available target
    for target in targets:
        try:
            target.UserRefProperties.CreateString(name, value)
            return
        except Exception:
            continue


def write_cell(
    product,
    col: str,
    value: str,
    custom_columns: list[str],
) -> None:
    """通过缓存的 COM 引用直接将单个单元格值写入 CATIA 。

    与 ``write_bom_to_catia()`` 不同，此函数不遍历产品树——
    直接写入存储在 BOM 行中的 COM 引用 ``product``。

    适用于 V2 对话框的即时写回路径。对于独立文件（非嵌入部件），
    通过 ``ReferenceProduct`` 写入一次即可覆盖所有实例；
    对于嵌入部件，调用方应为每个实例分别调用此函数。

    参数：
        product: win32com ``Product`` 对象（缓存的 COM 引用）
        col:     BOM 列名（与常量中的映射键一致）
        value:   要写入的新值
        custom_columns: 用户自定义属性的列名列表（通过 UserRefProperties 写入）
    """
    if col in BOM_READONLY_COLUMNS:
        return
    _ensure_design_mode(product)
    if col == BOM_INSTANCE_NAME_COLUMN:
        # Per-instance name – written directly to the instance object, NOT
        # ReferenceProduct (which would affect the part definition, not the
        # instance name within the assembly).
        try:
            product.Name = value
        except Exception as e:
            logger.debug("write_cell: 无法设置实例名 → %s", e)
    elif col == "Part Number":
        try:
            product.PartNumber = value
        except Exception:
            try:
                product.ReferenceProduct.PartNumber = value
            except Exception:
                pass
    elif col in PRODUCT_ATTR_WRITE_MAP:
        _set_prop(product, col, value)
    elif col in custom_columns:
        _set_user_prop(product, col, value)


def write_bom_to_catia(
    file_path: str | None,
    pn_data: dict[str, dict[str, str]],
    custom_columns: list[str],
    progress_callback: Callable[[int], None] | None = None,
) -> None:
    """通过 COM 将编辑后的 BOM 属性写回 CATIA 。

    参数：
        file_path: 已编辑的 ``.CATProduct`` 文件路径，或 ``None`` 使用当前活动的 CATIA
                  文档（不打开或保存任何文件）
        pn_data: 从原始零件编号到 ``{列名: 新值}`` 的映射。
                仅需包含更改的字段。
        custom_columns: 用户自定义属性的列名（通过 ``UserRefProperties`` 写入）
        progress_callback: 可选的回调函数，在遍历期间访问每个节点后调用，传入当前节点计数。
                          可抛出异常以中止。遍历顺序为后序（子节点在父节点之前），
                          因此较深层级在父级之前写入 CATIA 。
    """
    # win32com Product 对象的可写内置属性（CamelCase COM 属性名）
    # 集中定义在 constants.PRODUCT_ATTR_WRITE_MAP，此处直接引用
    WRITABLE_DIRECT = PRODUCT_ATTR_WRITE_MAP

    _total_count: int = 0

    # Track backing filepaths that have already been written so that repeated
    # instances of the same physical document (e.g. the same fastener used
    # 50 times) are skipped together with their entire sub-tree.  This mirrors
    # the _props_cache optimization in collect_bom_rows and keeps the write-back
    # node count consistent with the read node count.
    # NOTE: nodes without a filepath (embedded sub-assemblies / 部件) are
    # always processed because they share the parent file but may represent
    # structurally distinct sub-trees.
    _written_fps: set[str] = set()

    # Mutable copy of the dirty-PN set used for early-exit: once every dirty
    # part has been written we can stop traversing the rest of the tree.
    remaining_pns: set[str] = set(pn_data.keys())

    def _traverse_write(product, parent_filepath: str = "") -> None:
        nonlocal _total_count
        # Early exit: nothing left to write.
        if not remaining_pns:
            return

        try:
            pn = product.PartNumber
        except Exception:
            name = product.Name
            pn   = name.rsplit(".", 1)[0] if "." in name else name

        # Resolve the backing filepath for this node.
        try:
            filepath = product.ReferenceProduct.Parent.FullName
        except Exception:
            filepath = ""

        # A node is an embedded 部件 (no own file) when its resolved filepath
        # is identical to its parent's filepath – the same logic used by
        # collect_bom_rows to set Type=="部件".  Such nodes must NOT be
        # de-duplicated via _written_fps: all siblings under the same 组件
        # resolve to the same parent path, so only the first one would ever
        # be visited if the guard were applied to them.
        _is_own_file = bool(filepath) and filepath != parent_filepath

        # If we have already processed this file (written its properties and
        # recursed into its children), skip the whole sub-tree.  Only apply
        # this guard for nodes that have their own backing file.
        if _is_own_file and filepath in _written_fps:
            return

        # ── Recurse into children FIRST (post-order / bottom-up) ────────────
        # This guarantees that deeper levels (e.g. level 6) are written to
        # CATIA before their parent levels (e.g. level 5).  The parent node's
        # PN remains in remaining_pns throughout child processing, so the
        # early-exit break inside the loop only fires when truly nothing is
        # left to write (i.e. the parent itself is also not dirty).
        try:
            count = product.Products.Count
            for i in range(1, count + 1):
                if not remaining_pns:
                    break
                try:
                    _traverse_write(product.Products.Item(i),
                                    parent_filepath=filepath)
                except Exception:
                    pass
        except Exception:
            pass

        if pn in pn_data:
            # Performance optimization: Check current work mode before switching
            # to avoid unnecessary DESIGN_MODE transitions (costly COM calls)
            try:
                current_mode = product.GetWorkMode()
                if current_mode != CATIA_DESIGN_MODE:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
            except Exception:
                # If GetWorkMode fails, try ApplyWorkMode anyway
                try:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
                except Exception:
                    pass
            for col, value in pn_data[pn].items():
                if col in BOM_READONLY_COLUMNS:
                    continue
                if col == "Part Number":
                    try:
                        product.PartNumber = value
                    except Exception:
                        pass
                elif col in WRITABLE_DIRECT:
                    _set_prop(product, col, value)
                elif col in custom_columns:
                    _set_user_prop(product, col, value)
            remaining_pns.discard(pn)

        _total_count += 1
        if progress_callback is not None:
            progress_callback(_total_count)

        # Mark this filepath as done after its sub-tree has been fully
        # traversed so that future identical references are skipped entirely.
        # Only standalone-file nodes are recorded; embedded 部件 nodes must
        # not pollute the set with the parent document's path.
        if filepath and _is_own_file:
            _written_fps.add(filepath)

    # ── CATIA connection ────────────────────────────────────────────────────
    application = get_catia_v5_application()
    application.Visible = True
    documents   = application.Documents

    if file_path is None:
        root_product = application.ActiveDocument.Product
        _traverse_write(root_product, parent_filepath="")
        logger.info("Write-back complete for active document (not saved)")
        return

    src = file_path
    from catia_copilot.utils import open_catia_file  # noqa: PLC0415
    target_doc = open_catia_file(documents, src)

    root_product = target_doc.Product
    _traverse_write(root_product, parent_filepath="")
    logger.info(
        f"Write-back complete for {Path(src).name} "
        "(not saved; user must save manually in CATIA)"
    )
