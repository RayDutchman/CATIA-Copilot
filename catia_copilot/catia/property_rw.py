"""
CATIA 产品属性读写模块。

对标 myPDM cad_bridge/catia/client.py 的 catia.assembly.read_properties 和
catia.property.write 方法。按装配树路径定位实例并读写属性。

路径格式：根节点为 "0"，一级子节点为 "0.0"、"0.1"（0-based）。
"""
from __future__ import annotations

import logging
from typing import Any

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.constants import PRODUCT_ATTR_READ_MAP, PRODUCT_ATTR_WRITE_MAP

logger = logging.getLogger(__name__)


def _resolve_product_by_path(product, path: str):
    """按路径字符串（如 "0"、"0.1"、"0.1.2"）定位产品实例。

    返回：(product, parent_product) 元组，失败返回 (None, None)。
    注意：0-based indexing 与 CATIA COM 的 1-based indexing 之间的转换。
    """
    if not path or path == "0":
        return product, None

    try:
        indices = [int(s) for s in path.split(".")]
    except ValueError:
        logger.warning(f"无效路径格式: {path}")
        return None, None

    current = product
    parent = None

    for i in range(1, len(indices)):
        idx = indices[i]
        if current is None:
            break
        parent = current
        try:
            products = current.Products
            if products is None or products.Count <= idx:
                logger.debug(f"路径 {path}: 索引 {idx} 超出子节点范围 "
                             f"({products.Count if products else 0})")
                return None, None
            current = products.Item(idx + 1)  # COM 是 1-based
        except Exception as e:
            logger.debug(f"路径 {path}: 定位第 {idx} 个子节点失败: {e}")
            return None, None

    return current, parent


def read_properties(path: str, product_doc=None) -> dict[str, str] | None:
    """读取指定路径实例的全部属性（内置属性 + 用户自定义属性）。

    返回：{属性名: 属性值} 字典，失败返回 None。
    """
    if product_doc is None:
        try:
            app = get_catia_v5_application()
            product_doc = app.ActiveDocument.Product if app.ActiveDocument else None
        except Exception as e:
            logger.warning(f"获取 CATIA 活动文档失败: {e}")
            return None

    if product_doc is None:
        return None

    prod, _parent = _resolve_product_by_path(product_doc, path)
    if prod is None:
        logger.warning(f"路径 {path} 找不到对应实例")
        return None

    result: dict[str, str] = {}

    for display_name, com_name in PRODUCT_ATTR_READ_MAP.items():
        try:
            val = getattr(prod, com_name, "")
            result[display_name] = str(val) if val is not None else ""
        except Exception:
            result[display_name] = ""

    try:
        user_props = prod.UserRefProperties
        if user_props is not None:
            count = user_props.Count
            known_com_names = set(PRODUCT_ATTR_READ_MAP.values())
            for i in range(1, count + 1):
                try:
                    name = str(user_props.Item(i).Name)
                    value = str(user_props.Item(i).Value)
                    if name and name not in known_com_names:
                        result[name] = value
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"读取用户属性失败: {e}")

    return result


def write_property(path: str, product_doc, prop_name: str, value: Any) -> bool:
    """写入属性到指定路径的 CATIA 实例。

    自动判断属性类型：内置属性走 COM 直接赋值，其他走 UserRefProperties。
    返回：True 表示写入成功。
    """
    prod, _parent = _resolve_product_by_path(product_doc, path)
    if prod is None:
        logger.warning(f"write_property: 路径 {path} 找不到实例")
        return False

    write_map = PRODUCT_ATTR_WRITE_MAP
    com_name = write_map.get(prop_name)

    if com_name is not None:
        try:
            setattr(prod, com_name, str(value))
            logger.debug(f"写入内置属性: {path}.{com_name} = {value}")
            return True
        except Exception as e:
            logger.warning(f"写入内置属性失败 {path}.{com_name}: {e}")
            return False

    try:
        user_props = prod.UserRefProperties
        if user_props is not None:
            try:
                prop = user_props.Item(prop_name)
                prop.Value = str(value)
            except Exception:
                user_props.Add(prop_name, str(value))
            logger.debug(f"写入用户属性: {path}.{prop_name} = {value}")
            return True
    except Exception as e:
        logger.warning(f"写入用户属性失败 {path}.{prop_name}: {e}")
        return False
