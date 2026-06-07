"""
BOM 数据收集 V3 模块（part_master / instance 分离架构）。

提供：
- collect_bom_part_masters()  – 遍历产品树，返回 dict[str, dict]（PN → part_master）
                                每条 part_master 包含 PartMaster 级属性（可写属性、只读属性）
                                和 children 列表（装配结构）
- build_hierarchical_rows_v3() – 从 part_master 树派生层级 BOM 扁平行列表
                                  使用 (parent_pn, child_pn) 作为分组 key（替代不稳定的 id(parent)）

内部仍调用 bom_collect.collect_bom_rows() 作为中间产物，
collect_bom_rows() 本身不需要修改。

part_master 结构（每条对应一个零件文件）：
    {
        "part_number":  str,   # 唯一 key（PartNumber）
        "nomenclature": str,   # 术语（中文名称）
        "revision":     str,   # 版本
        "definition":   str,   # 定义
        "source":       str,   # 源（CATIA 原始值："0"/"1"/"2"）
        "description":  str,   # 描述
        # + 用户自定义列（dict 中额外 key）
        "type":         str,   # "Part"/"Product"/"Component"（只读）
        "filename":     str,   # 文件名（含扩展名，不含路径）
        "filepath":     str,   # 文件完整路径（只读；未保存零件可能为空）
        "_not_found":   bool,  # 文件未找到标志
        "_no_file":     bool,  # 从未保存到磁盘标志
        "_unreadable":  bool,  # COM 不可读标志
        "children": [          # 子件列表（有序，保持 CATIA 产品树顺序）
            {
                "child_pn": str,   # 子 part_master 的 part_number
                "instances": [     # 该子件在本装配中的所有实例
                    {
                        "inst_key":      int,        # id(product)，写回用
                        "instance_name": str,        # product.Name，实例级
                        "placement":     list|None,  # 4×4 变换矩阵（可为 None）
                    },
                ]
            },
        ]
    }
"""

import logging
from collections.abc import Callable

from catia_copilot.constants import (
    BOM_EDIT_COLUMN_ORDER,
    BOM_INSTANCE_NAME_COLUMN,
    SOURCE_TO_DISPLAY,
    BomNodeType,
)
from catia_copilot.catia.bom_collect import collect_bom_rows, build_hierarchical_rows

logger = logging.getLogger(__name__)

# PartMaster 级可写属性（对应 CATIA ReferenceProduct 的属性）
# Source 存原始值（"0"/"1"/"2"），显示层由调用方转换
_PART_MASTER_WRITABLE_COLS: tuple[str, ...] = (
    "Part Number", "Nomenclature", "Revision", "Definition", "Source", "Description",
)

# PartMaster 级只读属性（派生，不可写）
_PART_MASTER_READONLY_COLS: tuple[str, ...] = (
    "Type", "Filename",
)


def collect_bom_part_masters(
    file_path: str | None,
    columns: list[str],
    custom_columns: list[str],
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """遍历产品树，返回 (full_rows, part_masters)。

    full_rows:
        collect_bom_rows() 返回的逐实例扁平行列表，保持不变，供显示层使用。

    part_masters:
        dict[part_number → part_master dict]，每条 part_master 包含：
        - PartMaster 级属性（可写：PN/Nomenclature/Revision/Definition/Source/Description/自定义列）
        - 只读属性（Type/Filename/filepath/_flags）
        - children 列表（装配结构，有序）

    参数：
        file_path, columns, custom_columns, progress_callback:
            与 collect_bom_rows() 参数含义相同。

    返回：
        (full_rows, part_masters)
    """
    # ── Step 1: 调用现有 collect_bom_rows() 获取逐实例行列表 ────────────────
    full_rows = collect_bom_rows(
        file_path, columns, custom_columns,
        progress_callback=progress_callback,
    )

    # ── Step 2: 从 full_rows 构建 part_masters ───────────────────────────────
    part_masters: dict[str, dict] = {}

    # 确定所有需要存储的用户自定义列
    extra_cols = [c for c in custom_columns if c not in _PART_MASTER_WRITABLE_COLS
                  and c not in _PART_MASTER_READONLY_COLS]

    for row in full_rows:
        pn = str(row.get("Part Number", "")).strip()
        if not pn:
            # PN 为空的节点（异常情况），跳过 part_master 建立
            logger.warning("collect_bom_part_masters: 跳过 PN 为空的节点 filename=%s",
                           row.get("Filename", ""))
            continue

        if pn not in part_masters:
            # 首次出现该 PN：建立 part_master
            pm: dict = {
                "part_number":  pn,
                # ── 可写属性（直接从 row 读取原始值）──────────────────────────
                "nomenclature": str(row.get("Nomenclature", "")),
                "revision":     str(row.get("Revision", "")),
                "definition":   str(row.get("Definition", "")),
                "source":       str(row.get("Source", "")),   # 保留原始值 "0"/"1"/"2"
                "description":  str(row.get("Description", "")),
                # ── 用户自定义列 ──────────────────────────────────────────────
                **{col: str(row.get(col, "")) for col in extra_cols},
                # ── 只读属性 ──────────────────────────────────────────────────
                "type":         str(row.get("Type", "")),
                "filename":     str(row.get("Filename", "")),
                "filepath":     str(row.get("_filepath", "")),
                "_not_found":   bool(row.get("_not_found", False)),
                "_no_file":     bool(row.get("_no_file", False)),
                "_unreadable":  bool(row.get("_unreadable", False)),
                # ── 装配结构 ──────────────────────────────────────────────────
                "children":     [],  # 在第二次遍历中填充
            }
            part_masters[pn] = pm
        else:
            # 同 PN 已存在：不覆盖 PartMaster 级属性，但记录日志（供调试）
            # （PN 相同视为同一零件文件的不同实例）
            pass

    # ── Step 3: 填充 children 列表（装配结构）────────────────────────────────
    # 按 full_rows 中的 _parent_product 关系构建父子关联
    # 使用 id(parent_product) → parent_pn 的临时映射
    _product_to_pn: dict[int, str] = {}
    for row in full_rows:
        _p = row.get("_product")
        if _p is not None:
            pn = str(row.get("Part Number", "")).strip()
            if pn:
                _product_to_pn[id(_p)] = pn

    # 按（父 PN，子 PN）分组，保持 full_rows 的遍历顺序
    # 每组维护一个有序的 instances 列表
    # _children_map[parent_pn][child_pn] = list of instance dicts
    _children_map: dict[str, dict[str, list]] = {}

    for row in full_rows:
        _p          = row.get("_product")
        _pp         = row.get("_parent_product")
        inst_key    = id(_p) if _p is not None else None
        if inst_key is None:
            continue

        pn = str(row.get("Part Number", "")).strip()
        if not pn:
            continue

        # 根节点（Level == 0）没有父节点，不加入任何 children 列表
        if _pp is None:
            continue

        parent_pn = _product_to_pn.get(id(_pp), "")
        if not parent_pn:
            # 父节点 PN 未知（异常情况），跳过
            logger.debug("collect_bom_part_masters: 无法确定父节点 PN，inst_key=%d pn=%s",
                         inst_key, pn)
            continue

        instance_info: dict = {
            "inst_key":      inst_key,
            "instance_name": str(row.get(BOM_INSTANCE_NAME_COLUMN, "")),
            "placement":     None,  # 由 mass_props_collect 在需要时填充
        }

        # 初始化嵌套 dict
        if parent_pn not in _children_map:
            _children_map[parent_pn] = {}
        parent_children = _children_map[parent_pn]

        if pn not in parent_children:
            parent_children[pn] = []
        parent_children[pn].append(instance_info)

    # 将 _children_map 写入各 part_master 的 children 列表
    # 保持 full_rows 中 children 的出现顺序（已按遍历顺序插入 _children_map）
    for parent_pn, child_groups in _children_map.items():
        pm = part_masters.get(parent_pn)
        if pm is None:
            continue
        for child_pn, instances in child_groups.items():
            pm["children"].append({
                "child_pn":  child_pn,
                "instances": instances,
            })

    return full_rows, part_masters


def build_hierarchical_rows_v3(
    full_rows: list[dict],
    part_masters: dict[str, dict],
) -> list[dict]:
    """从逐实例行列表派生层级 BOM 行（V3 版本）。

    与 bom_collect.build_hierarchical_rows() 等效，但使用 (parent_pn, child_pn)
    作为分组 key，替代不稳定的 (id(parent_product), child_pn)。

    同父节点（parent_pn 相同）下同 PN 的多个实例合并为一行（代表行取第一个实例），
    Quantity 设为实例数。

    参数：
        full_rows:   collect_bom_rows() 返回的逐实例行列表。
        part_masters: collect_bom_part_masters() 返回的 part_master 字典。

    返回：
        层级 BOM 行列表（与 build_hierarchical_rows() 输出格式一致）。
    """
    # 构建 id(product) → part_number 映射
    _product_to_pn: dict[int, str] = {}
    for row in full_rows:
        _p = row.get("_product")
        if _p is not None:
            pn = str(row.get("Part Number", "")).strip()
            if pn:
                _product_to_pn[id(_p)] = pn

    result: list[dict] = []
    _hierarchical_range_v3(full_rows, 0, len(full_rows), result, _product_to_pn)
    return result


def _hierarchical_range_v3(
    full_rows: list[dict],
    start: int,
    end: int,
    result: list[dict],
    product_to_pn: dict[int, str],
) -> None:
    """内部递归辅助函数：处理 full_rows[start:end]，按 (parent_pn, child_pn) 分组。"""
    # 第一遍：收集同层直接子节点（跳过子树）
    groups: dict[tuple, list] = {}   # key → [(row, row_i, sub_end)]
    seen_order: list[tuple]   = []   # 插入顺序的 key 列表

    i = start
    while i < end:
        row      = full_rows[i]
        root_lvl = row["Level"]
        # 找出该节点子树的排他性结束位置
        sub_end  = i + 1
        while sub_end < end and full_rows[sub_end]["Level"] > root_lvl:
            sub_end += 1

        pn = str(row.get("Part Number", ""))

        # 用父节点 PN 作为分组 key 的一部分（比 id(parent) 更稳定）
        _pp = row.get("_parent_product")
        parent_pn = product_to_pn.get(id(_pp), "") if _pp is not None else ""
        key = (parent_pn, pn)

        if key not in groups:
            groups[key] = []
            seen_order.append(key)
        groups[key].append((row, i, sub_end))
        i = sub_end

    # 第二遍：构建代表行并递归
    for key in seen_order:
        instances                         = groups[key]
        first_row, first_i, first_sub_end = instances[0]

        rep             = dict(first_row)   # 浅拷贝
        rep["Quantity"] = len(instances)

        result.append(rep)

        # 递归进入代表行的子树
        if first_sub_end > first_i + 1:
            _hierarchical_range_v3(
                full_rows, first_i + 1, first_sub_end, result, product_to_pn
            )


def get_part_master_attr(
    part_masters: dict[str, dict],
    pn: str,
    col_name: str,
    default: str = "",
) -> str:
    """从 part_masters 读取指定列的属性值。

    Source 列返回原始值（"0"/"1"/"2"），显示层由调用方用 SOURCE_TO_DISPLAY 转换。

    参数：
        part_masters: collect_bom_part_masters() 返回的字典。
        pn:           part_number（唯一 key）。
        col_name:     BOM 列名（"Part Number"/"Nomenclature"/... 或自定义列）。
        default:      找不到时的默认值。
    """
    pm = part_masters.get(pn)
    if pm is None:
        return default

    # 列名到 part_master dict key 的映射（标准列）
    _col_to_key: dict[str, str] = {
        "Part Number":  "part_number",
        "Nomenclature": "nomenclature",
        "Revision":     "revision",
        "Definition":   "definition",
        "Source":       "source",
        "Description":  "description",
        "Type":         "type",
        "Filename":     "filename",
        "Filepath":     "filepath",
    }

    key = _col_to_key.get(col_name, col_name)  # 自定义列名 == dict key
    return str(pm.get(key, default))


def set_part_master_attr(
    part_masters: dict[str, dict],
    pn: str,
    col_name: str,
    value: str,
) -> bool:
    """在 part_masters 中写入指定列的属性值。

    只允许写可写属性（PartMaster 级）。只读属性（Type/Filename/Filepath）忽略写入。

    返回 True 表示写入成功，False 表示 pn 不存在或列名为只读列。
    """
    pm = part_masters.get(pn)
    if pm is None:
        return False

    _readonly = {"Type", "Filename", "Filepath", "type", "filename", "filepath"}
    if col_name in _readonly:
        return False

    _col_to_key: dict[str, str] = {
        "Part Number":  "part_number",
        "Nomenclature": "nomenclature",
        "Revision":     "revision",
        "Definition":   "definition",
        "Source":       "source",
        "Description":  "description",
    }
    key = _col_to_key.get(col_name, col_name)

    if col_name == "Part Number":
        # PN 修改需通过 _rename_part_master()，此处不允许直接写
        logger.warning("set_part_master_attr: PN 修改请使用 rename_part_master()")
        return False

    pm[key] = value
    return True


def rename_part_master(
    part_masters: dict[str, dict],
    pn_to_inst_keys: dict[str, list[int]],
    full_rows: list[dict],
    old_pn: str,
    new_pn: str,
) -> bool:
    """将 part_master 的 PartNumber 从 old_pn 改为 new_pn。

    同步更新：
    - part_masters dict key
    - part_masters[new_pn]["part_number"]
    - 所有引用该 PartMaster 的父 part_master 的 children[*]["child_pn"]
    - pn_to_inst_keys dict key
    - full_rows 中所有 "Part Number" == old_pn 的行

    返回 True 表示成功，False 表示 old_pn 不存在或 new_pn 已存在（冲突）。
    """
    if old_pn not in part_masters:
        logger.warning("rename_part_master: old_pn=%r 不存在", old_pn)
        return False

    if new_pn in part_masters:
        logger.warning("rename_part_master: new_pn=%r 已存在（冲突）", new_pn)
        return False

    # 迁移 part_masters dict
    pm = part_masters.pop(old_pn)
    pm["part_number"] = new_pn
    part_masters[new_pn] = pm

    # 更新所有引用该 PN 的父 part_master 的 children
    for other_pm in part_masters.values():
        for child_entry in other_pm.get("children", []):
            if child_entry.get("child_pn") == old_pn:
                child_entry["child_pn"] = new_pn

    # 迁移 pn_to_inst_keys
    if old_pn in pn_to_inst_keys:
        pn_to_inst_keys[new_pn] = pn_to_inst_keys.pop(old_pn)

    # 更新 full_rows 中的 "Part Number" 字段
    for row in full_rows:
        if str(row.get("Part Number", "")).strip() == old_pn:
            row["Part Number"] = new_pn

    return True
