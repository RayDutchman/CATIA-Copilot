"""
BOM 数据收集辅助模块。

提供：
- get_product_filepath()     – 解析 CATIA 产品的支持文件路径
- collect_bom_rows()         – 遍历产品树并返回行字典列表
- flatten_bom_to_summary()   – 将层级 BOM 压缩为平面汇总
                               （唯一零件及累计数量）
"""

import logging
from collections.abc import Callable
from pathlib import Path

from catia_copilot.constants import FILENAME_NOT_FOUND, FILENAME_UNSAVED, BomNodeType

logger = logging.getLogger(__name__)


def get_product_filepath(product) -> str:
    """返回支持 CATIA 产品 *product* 的文档完整路径。

    使用 ``product.ReferenceProduct.Parent.FullName`` – 纯 COM 路径，
    适用于独立产品/零件和嵌入式部件（无自己的文件，返回父级路径）。
    失败时返回空字符串。

    参数：
        product: win32com CATIA 产品对象（直接 dispatch）

    返回：
        文档完整路径，或空字符串（失败时）
    """
    try:
        return product.ReferenceProduct.Parent.FullName
    except Exception as e:
        logger.debug(f"无法获取产品文件路径: {e}")
        return ""


def collect_bom_rows(
    file_path: str | None,
    columns: list[str],
    custom_columns: list[str],
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict]:
    """Return a list of row dicts representing the hierarchical BOM.

    Parameters
    ----------
    file_path:
        Path to a ``.CATProduct`` file.  Pass ``None`` to use the currently
        active CATIA document without opening or closing any file.
    columns:
        The column names (internal) to read for each product node.
    custom_columns:
        Column names that are user-defined properties (read via
        ``UserRefProperties``).
    progress_callback:
        Optional callable invoked with the current row count after each node
        is appended to the result list.  May raise an exception to abort the
        traversal (e.g. when the user cancels).
    """
    from catia_copilot.catia.connection import get_catia_v5_application
    from catia_copilot.constants import PRODUCT_ATTR_READ_MAP
    from catia_copilot.catia.document import get_bom_node_type

    # CatWorkModeType 枚举值（来自 CATIA V5 COM API）
    CATIA_DESIGN_MODE        = 2  # catWorkModeDesign
    CATIA_VISUALIZATION_MODE = 1  # catWorkModeVisualization

    # win32com Product 对象的内置属性（CamelCase COM 属性名）
    # 集中定义在 constants.PRODUCT_ATTR_READ_MAP，此处直接引用
    DIRECT_ATTR_MAP = PRODUCT_ATTR_READ_MAP

    def _get_prop(product, name: str) -> str:
        attr = DIRECT_ATTR_MAP.get(name)
        if not attr:
            return ""
        targets = [product]
        try:
            targets.insert(0, product.ReferenceProduct)
        except Exception:
            pass
        for target in targets:
            try:
                value = getattr(target, attr)
                if value is not None:
                    return str(value)
            except Exception as e:
                logger.debug(f"无法从 {target} 获取属性 {name}: {e}")
        return ""

    def _get_user_prop(product, name: str) -> str:
        targets = [product]
        try:
            targets.insert(0, product.ReferenceProduct)
        except Exception:
            pass
        for target in targets:
            try:
                prop  = target.UserRefProperties.Item(name)
                value = prop.Value
                if value is not None and str(value).strip():
                    return str(value)
            except Exception:
                pass
        return ""

    _total_count: int = 0

    # Cache properties by filepath to avoid redundant DESIGN_MODE switches and
    # COM property reads for the same physical document referenced multiple
    # times in the assembly tree (e.g. the same fastener used 50 times).
    # NOTE: this dict is local to each collect_bom_rows() call, so it is
    # discarded after the traversal and never shared across invocations.
    _props_cache: dict[str, dict] = {}

    def _traverse(product, rows: list, level: int, parent_filepath: str = "") -> None:
        nonlocal _total_count
        try:
            pn = product.PartNumber
        except Exception:
            name = product.Name
            pn   = name.rsplit(".", 1)[0] if "." in name else name

        filepath  = get_product_filepath(product)
        not_found = not bool(filepath)
        # 文件路径存在于CATIA内存中，但文件尚未保存到磁盘（从未保存过）
        no_file   = bool(filepath) and not Path(filepath).exists()

        # Embedded 部件 share the parent product's backing file, so the
        # file-based property cache must NOT be used for them – each 部件
        # has its own COM property values that must be read individually.
        is_embedded = (bool(filepath) and bool(parent_filepath)
                       and filepath == parent_filepath)

        # Use the cache to skip DESIGN_MODE + property reads for repeated
        # files, but only for standalone products/parts (not embedded 部件).
        cached = (not is_embedded
                  and bool(filepath) and filepath in _props_cache)
        is_readable = True

        if not_found:
            # No backing file: skip all property reads to avoid redundant
            # DEBUG messages.  Properties will be empty strings.
            # Note: _not_found and _unreadable are mutually exclusive —
            # _unreadable is reserved for lightweight/visualization-mode nodes
            # where the mode switch (apply_work_mode) fails.
            props = {col: "" for col in columns}
            props["_is_readable"] = True
        elif not cached:
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
                    is_readable = False

            props: dict = {}
            for col in columns:
                if col in DIRECT_ATTR_MAP:
                    props[col] = _get_prop(product, col)
                elif col in custom_columns:
                    props[col] = _get_user_prop(product, col)
            props["_is_readable"] = is_readable

            if filepath and not is_embedded:
                _props_cache[filepath] = props
        else:
            props       = _props_cache[filepath]
            is_readable = bool(props.get("_is_readable", True))

        row: dict = {
            "Level":        level,
            "Part Number":  pn,
            "Filename":     (FILENAME_UNSAVED   if no_file   else
                             Path(filepath).name if filepath else FILENAME_NOT_FOUND),
            "_filepath":    filepath,
            "_not_found":   not_found,
            "_no_file":     no_file,
            "_unreadable":  not is_readable,
        }

        try:
            row["Type"] = get_bom_node_type(product, parent_filepath, filepath=filepath)
        except Exception:
            row["Type"] = ""

        for col in columns:
            if col in DIRECT_ATTR_MAP or col in custom_columns:
                row[col] = props.get(col, "")

        rows.append(row)
        _total_count += 1
        if progress_callback is not None:
            progress_callback(_total_count)

        try:
            products  = product.Products
            count     = products.Count
            if count == 0:
                return
            children: dict = {}
            for i in range(1, count + 1):
                try:
                    child = products.Item(i)
                    try:
                        cpn = child.PartNumber
                    except Exception:
                        try:
                            cpn = child.ReferenceProduct.PartNumber
                        except Exception:
                            n   = child.Name
                            cpn = n.rsplit(".", 1)[0] if "." in n else n
                except Exception:
                    continue
                if cpn not in children:
                    children[cpn] = {"product": child, "qty": 0}
                children[cpn]["qty"] += 1

            for cpn, data in children.items():
                child_rows: list = []
                _traverse(data["product"], child_rows, level + 1,
                          parent_filepath=filepath)
                if child_rows:
                    child_rows[0]["Quantity"] = data["qty"]
                rows.extend(child_rows)
        except Exception:
            pass

    # ── CATIA connection ────────────────────────────────────────────────────
    application = get_catia_v5_application()
    application.Visible = True
    documents   = application.Documents

    if file_path is None:
        root_product = application.ActiveDocument.Product
        rows: list[dict] = []
        _traverse(root_product, rows, level=0)
        return rows

    from catia_copilot.utils import open_catia_file  # noqa: PLC0415
    target_doc = open_catia_file(documents, file_path)
    root_product = target_doc.Product
    rows = []
    _traverse(root_product, rows, level=0)
    return rows


def check_unsaved_docs(bom_rows: list[dict]) -> list[str]:
    """检查 BOM 行中处于未保存状态的文档，返回供 UI 展示的描述字符串列表。

    涵盖两种场景：
      1. ``_no_file == True``：零件从未保存到磁盘（CATIA 内存中有，磁盘无文件）。
         此类零件的属性/几何体无法上传，直接从 bom_rows 标记读取，无需 COM 查询。
         条目格式：``"{Part Number}（从未保存到磁盘）"``

      2. ``Document.Saved == False``：文件已存在于磁盘，但自上次保存后有未提交修改。
         通过 COM 枚举 CATIA 已打开文档并检查 ``Saved`` 属性。
         条目格式：``"{filename.CATPart}（有未提交修改）"``

    参数：
        bom_rows: collect_bom_rows 返回的行字典列表。

    返回：
        描述字符串列表，供对话框逐条展示。
        若 CATIA 未运行或 COM 调用失败，仍返回第一段（_no_file）结果，
        不因 COM 异常丢失最确定的那部分信息。
    """
    result: list[str] = []

    # ── 第一段：_no_file 行（从未保存到磁盘），直接从标记读取 ─────────────────
    seen_pn: set[str] = set()
    for row in bom_rows:
        if not row.get("_no_file"):
            continue
        pn = str(row.get("Part Number", "")).strip() or str(row.get("Filename", "")).strip()
        if pn and pn not in seen_pn:
            seen_pn.add(pn)
            result.append(f"{pn}（从未保存到磁盘）")

    # ── 第二段：有效 _filepath 文件，通过 COM 检查 Document.Saved ─────────────
    try:
        application = get_catia_v5_application()
        documents   = application.Documents
    except Exception as exc:
        logger.warning(f"check_unsaved_docs：无法连接 CATIA ，跳过 Document.Saved 检查 — {exc}")
        return result  # 至少返回第一段结果

    # 构建 resolved_path → doc 的映射
    open_docs: dict[Path, object] = {}
    for i in range(1, documents.Count + 1):
        try:
            doc = documents.Item(i)
            open_docs[Path(doc.FullName).resolve()] = doc
        except Exception:
            pass

    seen_path: set[Path] = set()
    for row in bom_rows:
        # 跳过已在第一段报告的行，以及无有效路径的行
        if row.get("_no_file") or row.get("_not_found") or row.get("_unreadable"):
            continue
        fp = str(row.get("_filepath", "")).strip()
        if not fp:
            continue
        try:
            resolved = Path(fp).resolve()
        except Exception:
            continue
        if resolved in seen_path:
            continue
        seen_path.add(resolved)
        doc = open_docs.get(resolved)
        if doc is None:
            # 文件未在 CATIA 中打开，不可能有未保存修改
            continue
        try:
            if not doc.Saved:
                result.append(f"{Path(fp).name}（有未提交修改）")
        except Exception:
            pass

    return result


def flatten_bom_to_summary(
    rows: list[dict],
    include_assemblies: bool = False,
    sort_column: str | None = None,
) -> list[dict]:
    """Collapse a hierarchical BOM into a flat summary BOM.

    Each unique part appears exactly once in the result.  All node types
    (零件, 产品, 部件) are identified by their Part Number.  Using Part Number
    as the universal key is consistent with CATIA's own identity model and
    correctly handles embedded sub-assemblies (部件), which share their
    parent product's backing filepath and therefore cannot be distinguished
    by filepath alone.

    The ``Quantity`` value is the *total* count across the whole assembly tree,
    computed by multiplying the per-level quantities along every path from the
    root to that part.

    The root row (Level == 0) is always excluded from the result.

    Parameters
    ----------
    rows:
        The hierarchical BOM rows returned by :func:`collect_bom_rows`.
    include_assemblies:
        When ``False`` (default) rows whose ``Type`` is ``"产品"`` or
        ``"部件"`` are omitted so that only leaf parts appear in the summary.
        Set to ``True`` to include sub-assemblies and assemblies as well.
    sort_column:
        Internal column name to sort the result by.  Sorting is case-
        insensitive string comparison.  Defaults to ``"Part Number"`` when
        ``None``.

    Returns
    -------
    list[dict]
        Flat list of row dicts.  Each dict contains the same keys as the input
        rows except that ``Level`` is removed and ``Quantity`` reflects the
        total accumulated count.
    """
    if not rows:
        return []

    # ── Step 1: compute absolute quantity for every row ──────────────────────
    # We walk the rows in traversal order.  A stack tracks (level, cum_qty)
    # for each ancestor on the current path.
    # cum_qty[i] = cumulative quantity multiplier up to and including level i.
    cum_qty_stack: list[tuple[int, int]] = []  # (level, cumulative_qty)

    # absolute_qtys[i] = total count of rows[i] in the whole assembly
    absolute_qtys: list[int] = []

    for row in rows:
        level = row.get("Level", 0)
        qty   = int(row.get("Quantity", 1) or 1)

        # Pop stack entries that belong to a sibling or a higher-level ancestor
        while cum_qty_stack and cum_qty_stack[-1][0] >= level:
            cum_qty_stack.pop()

        # Parent's cumulative multiplier (1 if this is the root)
        parent_cum = cum_qty_stack[-1][1] if cum_qty_stack else 1

        abs_qty = parent_cum * qty
        absolute_qtys.append(abs_qty)
        cum_qty_stack.append((level, abs_qty))

    # ── Step 2: deduplicate ─────────────────────────────────────────────────
    # Key: Part Number for all node types.
    # For each key we keep the first row's attributes and accumulate quantity.
    seen_order:  list[str]       = []   # insertion-ordered keys
    summary:     dict[str, dict] = {}   # key → merged row dict
    key_to_qty:  dict[str, int]  = {}   # key → accumulated total qty

    _assembly_types = BomNodeType.ASSEMBLY_TYPES

    for row, abs_qty in zip(rows, absolute_qtys):
        level = row.get("Level", 0)

        # Skip the root assembly (level 0) only when assemblies are not included
        if level == 0 and not include_assemblies:
            continue

        # Optionally skip sub-assemblies and assemblies
        if not include_assemblies and row.get("Type", "") in _assembly_types:
            continue

        # Always use Part Number as the deduplication key: consistent for
        # 零件, 产品, and 部件 (embedded 部件 share the parent filepath so
        # filepath-based dedup would incorrectly merge them with the parent).
        key = str(row.get("Part Number", ""))
        if not key:
            continue

        if key not in summary:
            seen_order.append(key)
            merged = {k: v for k, v in row.items() if k != "Level"}
            merged["Quantity"] = abs_qty
            summary[key]       = merged
            key_to_qty[key]    = abs_qty
        else:
            key_to_qty[key]          += abs_qty
            summary[key]["Quantity"]  = key_to_qty[key]

    # ── Step 3: sort and return ───────────────────────────────────────────────
    result    = [summary[k] for k in seen_order]
    sort_key  = sort_column if sort_column else "Part Number"
    def _sort_key(r: dict) -> tuple:
        val = r.get(sort_key, "")
        try:
            return (0, float(val), "")
        except (TypeError, ValueError):
            return (1, 0.0, str(val).lower())

    result.sort(key=_sort_key)
    return result
