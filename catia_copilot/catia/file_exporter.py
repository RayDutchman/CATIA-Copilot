"""
CATIA 文件导出模块。

对标 myPDM cad_bridge 的 STP 导出和 PDF 转换功能：
- export_stp(): 将零部件导出为 STEP (.stp) 格式
- export_pdf(): 将 CATDrawing 转换为 PDF

所有导出文件保存到本地临时目录，由调用方负责上传到 myPDM 后端。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

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


def export_pdf(drawing_path: str, output_path: str | None = None) -> str | None:
    """将 CATDrawing 转换为 PDF。

    参数：
        drawing_path: CATDrawing 文件的完整路径
        output_path: 输出路径（可选，不传则自动创建临时文件）

    返回：生成的 .pdf 文件路径，失败返回 None。
    """
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)

    output_dir = os.path.dirname(output_path) or "."
    input_stem = Path(drawing_path).stem
    expected_file = os.path.join(output_dir, f"{input_stem}.pdf")

    from catia_copilot.catia.conversion import convert_drawing_to_pdf

    try:
        count = convert_drawing_to_pdf(
            [drawing_path],
            output_folder=output_dir,
            prefix="",
            suffix="",
        )
        if count > 0 and os.path.exists(expected_file):
            if expected_file != output_path:
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(expected_file, output_path)
            logger.info(f"PDF 导出成功: {output_path}")
            return output_path
    except Exception as e:
        logger.warning(f"PDF 导出失败 {drawing_path}: {e}")

    return None
