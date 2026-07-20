"""
CATIA 装配体结构树递归读取模块。

对标 myPDM cad_bridge/catia/client.py 的 catia.assembly.read_tree 方法。
读取 CATIA 活动文档的完整产品结构树，含属性、变换矩阵、源文件路径等信息。

主要函数：
- detect_catia_status()    检测 CATIA 运行状态与活动文档
- read_assembly_tree()     递归读取装配体产品结构树
"""
from __future__ import annotations

import logging
from typing import Any

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.document import get_bom_node_type
from catia_copilot.constants import (
    PRODUCT_ATTR_READ_MAP,
    BomNodeType,
)

logger = logging.getLogger(__name__)


def _read_product_position(product) -> list[float] | None:
    try:
        pos = product.Position
        if pos is None:
            return None
        raw = pos.GetComponents()
        if raw is not None and len(raw) == 12:
            return [float(v) for v in raw]
    except Exception:
        pass
    try:
        coords = []
        for axis in range(3):
            for el in range(4):
                try:
                    coord = pos.GetComponent(axis * 4 + el)
                    coords.append(float(coord))
                except Exception:
                    coords.append(0.0)
        if coords:
            return coords
    except Exception as e:
        logger.debug(f"读取变换矩阵失败: {e}")
    return None


def _read_builtin_properties(product) -> dict[str, str]:
    result: dict[str, str] = {}
    for display_name, com_name in PRODUCT_ATTR_READ_MAP.items():
        try:
            val = getattr(product, com_name, "")
            if isinstance(val, str):
                result[display_name] = val
            else:
                result[display_name] = str(val) if val is not None else ""
        except Exception:
            result[display_name] = ""
    return result


def _read_user_properties(product) -> dict[str, str]:
    """读取自定义属性：存货类别、规格型号、物料类型、重量。
    
    与 bom_collect._get_user_prop 逻辑一致：
    优先用 ReferenceProduct 读取，逐个尝试 UserRefProperties 和 Parameters。
    """
    import logging
    _log = logging.getLogger(__name__)
    
    props_to_read = ["存货类别", "规格型号", "物料类型", "重量"]

    result: dict[str, str] = {}
    
    # 构建目标列表：ReferenceProduct 优先
    targets = []
    try:
        ref = product.ReferenceProduct
        if ref is not None:
            targets.append(ref)
    except Exception:
        pass
    targets.append(product)

    for prop_name in props_to_read:
        value = None
        for target in targets:
            # 方式1：UserRefProperties.Item(name)
            try:
                prop = target.UserRefProperties.Item(prop_name)
                v = prop.Value
                if v is not None:
                    value = str(v)
                    break
            except Exception:
                pass
            # 方式2：getattr(user_ref_props, name) — 部分 CATIA 版本的访问方式
            try:
                props = target.UserRefProperties
                v = getattr(props, prop_name, None)
                if v is not None and hasattr(v, 'Value'):
                    v = v.Value
                if v is not None:
                    value = str(v)
                    break
            except Exception:
                pass
            # 方式3：Parameters.Item(name)
            try:
                param = target.Parameters.Item(prop_name)
                v = param.Value
                if v is not None:
                    try:
                        value = f"{float(v):.3f}"
                    except (ValueError, TypeError):
                        value = str(v)
                    break
            except Exception:
                continue
        if value is not None and value:
            result[prop_name] = value

    # 调试：记录读取到的属性数量
    if result:
        _log.debug(f"读取用户属性成功: {list(result.keys())} path={product.Name}")
    
    return result


def _get_document_path(product) -> str:
    try:
        return str(product.ReferenceProduct.Parent.FullName)
    except Exception:
        pass
    try:
        return str(product.ReferenceProduct.FullName)
    except Exception:
        pass
    return ""


def detect_catia_status() -> dict:
    result = {
        "active": False,
        "has_document": False,
        "doc_name": "",
        "doc_type": "",
        "doc_path": "",
    }
    try:
        app = get_catia_v5_application()
        if app is None:
            return result
        result["active"] = True

        doc = app.ActiveDocument
        if doc is None:
            return result
        result["has_document"] = True
        result["doc_name"] = str(doc.Name) if doc.Name else ""
        result["doc_type"] = str(doc.Type) if hasattr(doc, "Type") else ""

        try:
            path = doc.FullName
            if path:
                result["doc_path"] = str(path)
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"检测 CATIA 状态失败: {e}")
    return result


def read_assembly_tree(catia_app=None) -> dict | None:
    if catia_app is None:
        catia_app = get_catia_v5_application()

    doc = catia_app.ActiveDocument
    if doc is None:
        logger.warning("CATIA 活动文档为空")
        return None
    if not hasattr(doc, "Product") or doc.Product is None:
        logger.warning("活动文档不是装配体（无 Product 对象）")
        return None

    return _read_product_recursive(doc.Product, [], "")


def _read_product_recursive(
    product, path_indices: list[int], parent_filepath: str
) -> dict:
    instance_name = ""
    try:
        instance_name = str(product.Name) if product.Name else ""
    except Exception:
        pass

    doc_path = _get_document_path(product)

    node_type = get_bom_node_type(product, parent_filepath, filepath=doc_path)
    is_assembly = node_type in BomNodeType.ASSEMBLY_TYPES

    builtin = _read_builtin_properties(product)
    user_props = _read_user_properties(product)
    matrix = _read_product_position(product)

    part_number = builtin.get("Part Number", instance_name)

    path_str = "0" if not path_indices else ".".join(str(i) for i in path_indices)

    node = {
        "instance_name": instance_name,
        "part_number": part_number,
        "path": path_str,
        "is_assembly": is_assembly,
        "doc_path": doc_path,
        "builtin": builtin,
        "user_properties": user_props,
        "matrix": matrix,
        "children": [],
    }

    if is_assembly:
        try:
            products = product.Products
            if products is not None:
                child_count = products.Count
                for i in range(1, child_count + 1):
                    try:
                        child_product = products.Item(i)
                        child_indices = list(path_indices) + [i - 1]
                        child_node = _read_product_recursive(
                            child_product, child_indices, doc_path
                        )
                        node["children"].append(child_node)
                    except Exception as e:
                        logger.debug(f"读取子节点 {i} 失败: {e}")
                        continue
        except Exception as e:
            logger.debug(f"获取子产品集合失败: {e}")

    return node
