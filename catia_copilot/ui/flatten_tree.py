"""
装配树扁平化算法。

从 myPDM 前端 flattenTree.ts 移植，逻辑完全一致：
同父节点下同件号（PartNumber）的实例合并为一行，用量累加，所有变换矩阵保留。
件号为空的节点不参与合并。
"""
from __future__ import annotations

from typing import Any


def flatten_tree(assembly_root: dict) -> list[dict]:
    """递归扁平化 CATIA 装配树为 BOM 行列表。

    返回：BOMRow 格式的 dict 列表，按深度优先遍历排列。
    """
    result: list[dict] = []
    _flatten_node(assembly_root, [], result)
    return result


def _flatten_node(node: dict, path_indices: list[int], result: list[dict]) -> None:
    """递归处理单个节点。"""
    children = node.get("children", [])

    # 收集子节点的 part_number 用于合并判断
    child_pns: dict[str, list[dict]] = {}
    child_order: list[str] = []

    for child in children:
        child_pn = child.get("part_number", "").strip()
        if child_pn:
            if child_pn not in child_pns:
                child_pns[child_pn] = []
                child_order.append(child_pn)
            child_pns[child_pn].append(child)
        else:
            # 件号为空：不合并，直接展开
            _flatten_node(child, [], result)

    # 对每个唯一的子件号，合并实例
    for pn in child_order:
        instances = child_pns[pn]
        first = instances[0]

        # 收集所有实例的变换矩阵
        matrices = []
        for inst in instances:
            m = inst.get("matrix")
            if m is not None:
                matrices.append({
                    "matrix": m,
                    "label": inst.get("instance_name", ""),
                })

        row = {
            "instance_name": first.get("instance_name", ""),
            "part_number": pn,
            "path": first.get("path", ""),
            "level": first.get("path", "0").count("."),
            "is_assembly": first.get("is_assembly", False),
            "quantity": len(instances),
            "instances": matrices,
            "doc_path": first.get("doc_path", ""),
            "builtin": dict(first.get("builtin", {})),
            "user_properties": dict(first.get("user_properties", {})),
            "pdm_match": None,
            "match_status": "unknown",
            "checkout_status": None,
        }
        result.append(row)

        # 递归处理子节点（只处理第一个实例的子结构，合并后结构相同）
        if first.get("is_assembly"):
            first_children = first.get("children", [])
            if first_children:
                for child in first_children:
                    _flatten_node(child, [], result)


def build_path_indices(path_str: str) -> list[int]:
    """将路径字符串 "0.1.2" 转换为索引列表 [0, 1, 2]。"""
    if not path_str or path_str == "0":
        return []
    try:
        return [int(s) for s in path_str.split(".")]
    except ValueError:
        return []


def flatten_tree_hierarchical(assembly_root: dict) -> list[dict]:
    """将装配树转换为层级 BOM 树（保留父子关系）。
    
    同父节点下同件号的实例合并，用量累加，变换矩阵保留。
    件号为空的不合并。返回包含 children 字段的树形列表。
    """
    children = assembly_root.get("children", [])
    result: list[dict] = []
    _flatten_node_hierarchical(children, result)
    return result


def _flatten_node_hierarchical(nodes: list[dict], result: list[dict]) -> None:
    """递归处理子节点列表，按件号合并。"""
    child_pns: dict[str, list[dict]] = {}
    child_order: list[str] = []

    for node in nodes:
        pn = node.get("part_number", "").strip()
        if pn:
            if pn not in child_pns:
                child_pns[pn] = []
                child_order.append(pn)
            child_pns[pn].append(node)
        else:
            # 件号为空：展开（不合并），直接显示其子节点
            _flatten_node_hierarchical(node.get("children", []), result)

    for pn in child_order:
        instances = child_pns[pn]
        first = instances[0]

        matrices = []
        for inst in instances:
            m = inst.get("matrix")
            if m is not None:
                matrices.append({
                    "matrix": m,
                    "label": inst.get("instance_name", ""),
                })

        # 递归收集子节点（只处理第一个实例的子结构）
        sub_children: list[dict] = []
        if first.get("is_assembly"):
            first_children = first.get("children", [])
            if first_children:
                _flatten_node_hierarchical(first_children, sub_children)

        row = {
            "instance_name": first.get("instance_name", ""),
            "part_number": pn,
            "path": first.get("path", ""),
            "level": first.get("path", "0").count("."),
            "is_assembly": first.get("is_assembly", False),
            "quantity": len(instances),
            "instances": matrices,
            "doc_path": first.get("doc_path", ""),
            "builtin": dict(first.get("builtin", {})),
            "user_properties": dict(first.get("user_properties", {})),
            "pdm_match": None,
            "match_status": "unknown",
            "checkout_status": None,
            "children": sub_children,
        }
        result.append(row)
