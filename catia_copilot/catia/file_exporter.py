"""
CATIA 文件导出模块。

按装配树路径定位实例并导出 STEP (.stp)。

PDF 转换直接复用 catia_copilot.catia.conversion.convert_drawing_to_pdf，
不在此处重复实现。

所有导出文件保存到本地临时目录，由调用方负责上传到 myPDM 后端。
"""
from __future__ import annotations

import logging
import os
import tempfile

from catia_copilot.catia.connection import get_catia_v5_application

logger = logging.getLogger(__name__)


def export_stp(path: str, product_doc=None, output_path: str | None = None) -> str | None:
    """将指定路径的 CATIA 零部件导出为 STP 格式。

    参数：
        path: 装配树路径（如 "0"、"0.1.2"）
        product_doc: CATIA ProductDocument（可选，不传则用活动文档）
        output_path: 输出路径（可选，不传则自动创建临时文件）

    返回：生成的 .stp 文件路径，失败返回 None。
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

    from catia_copilot.catia.property_rw import _resolve_product_by_path

    prod, _parent = _resolve_product_by_path(product_doc, path)
    if prod is None:
        logger.warning(f"export_stp: 路径 {path} 找不到实例")
        return None

    part_number = ""
    try:
        part_number = str(prod.PartNumber) if prod.PartNumber else ""
    except Exception:
        part_number = "export"

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".stp", prefix=f"{part_number}_")
        os.close(fd)

    try:
        app = get_catia_v5_application()
        doc = prod.ReferenceProduct.Parent
        doc.ExportData(output_path, "stp")
        logger.info(f"STP 导出成功: {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"STP 导出失败 {path}: {e}")
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        return None
