"""
BOM 行属性同步工具。

从 myPDM 前端 syncRows.ts 移植。
按 PartNumber 同步同零部件所有实例行的属性更新。
"""
from __future__ import annotations

from typing import Any


def sync_rows_by_part_number(
    rows: list[dict],
    changed_row: dict,
    prop_name: str,
    value: Any,
) -> list[dict]:
    """按 PartNumber 查找同零部件实例行，同步属性值。

    参数：
        rows: BOM 行列表
        changed_row: 被修改的行引用
        prop_name: 属性名（如 "Revision", "规格型号" 等）
        value: 新属性值

    PartNumber 为空时回退为仅按 path 更新当前行。
    不修改原数组中的 dict，返回新的行列表。
    """
    part_number = changed_row.get("part_number", "").strip()
    changed_path = changed_row.get("path", "")

    new_rows = []
    for row in rows:
        new_row = dict(row)
        if part_number:
            # 按件号匹配
            if row.get("part_number", "").strip() == part_number:
                _update_property(new_row, prop_name, value)
        else:
            # 无件号，按路径精确匹配
            if row.get("path", "") == changed_path:
                _update_property(new_row, prop_name, value)
        new_rows.append(new_row)

    return new_rows


def _update_property(row: dict, prop_name: str, value: Any) -> None:
    """更新行字典中的属性值，自动判断 builtin 还是 user_properties。"""
    builtin = row.get("builtin", {})
    if prop_name in builtin:
        new_builtin = dict(builtin)
        new_builtin[prop_name] = str(value) if value is not None else ""
        row["builtin"] = new_builtin
    else:
        user_props = row.get("user_properties", {})
        new_user_props = dict(user_props)
        new_user_props[prop_name] = str(value) if value is not None else ""
        row["user_properties"] = new_user_props
