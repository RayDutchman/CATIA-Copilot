"""
从 CATPart 模板文件通过 Documents.NewFrom() 新建零件。

主要函数：
- create_part_from_template() — 核心逻辑
"""

import logging
from typing import Callable

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.document import get_document_type

logger = logging.getLogger(__name__)


def _next_available_part_number(app) -> str:
    """生成不与已打开文档重名的默认零件编号，格式为 Part1、Part2 ...

    遍历 CATIA 当前所有已打开文档的 PartNumber，找到最小可用的 PartN。
    """
    existing: set[str] = set()
    try:
        docs = app.Documents
        for i in range(1, docs.Count + 1):
            try:
                pn = docs.Item(i).Product.PartNumber
                if pn:
                    existing.add(pn.strip())
            except Exception:
                continue
    except Exception:
        pass

    n = 1
    while True:
        candidate = f"Part{n}"
        if candidate not in existing:
            return candidate
        n += 1


def create_part_from_template(
    template_path: str,
    input_callback: Callable[[str, str], tuple[str, bool]],
) -> dict:
    """从指定 CATPart 模板文件通过 NewFrom 创建新零件。

    流程：先向用户请求 PartNumber，再执行 NewFrom，最后写入 PartNumber 并更新零件。

    Parameters
    ----------
    template_path:
        模板 CATPart 文件的完整路径。
    input_callback:
        签名 ``(title: str, default_value: str) -> (text: str, ok: bool)``。
        由 UI 层提供，用于弹窗向用户请求 PartNumber 输入。
        - title 固定为 ``"PartNumber"``，default_value 为自动生成的 PartN 默认值。
        - 用户取消（ok=False）时中止整个操作。

    Returns
    -------
    dict
        ``{"success": bool, "message": str, "details": list[str]}``
    """
    details: list[str] = []

    try:
        app = get_catia_v5_application()

        details.append(f"模板路径：{template_path}")

        # ── 1. 先请求用户输入 PartNumber ─────────────────────────────────
        default_pn = _next_available_part_number(app)
        part_number, ok = input_callback("PartNumber", default_pn)

        if not ok:
            return {
                "success": False,
                "message": "用户取消操作。",
                "details": details,
            }

        # 用户输入为空时使用自动生成的默认值
        new_part_number = part_number.strip() if part_number.strip() else default_pn
        details.append(f"新零件号：{new_part_number}")

        # ── 2. NewFrom 创建新文档 ─────────────────────────────────────────
        try:
            new_doc = app.Documents.NewFrom(template_path)
            details.append("NewFrom 调用成功，新文档已创建。")
        except Exception as e:
            return {
                "success": False,
                "message": f"Documents.NewFrom() 调用失败：{e}",
                "details": details,
            }

        # 验证返回文档类型
        new_doc_type = get_document_type(new_doc)
        if new_doc_type != "PartDocument":
            return {
                "success": False,
                "message": (
                    f"NewFrom 返回的文档类型不是 PartDocument（实际：{new_doc_type}），"
                    "请检查模板文件是否为有效的 CATPart。"
                ),
                "details": details,
            }

        # ── 3. 写入 PartNumber ────────────────────────────────────────────
        try:
            new_doc.Product.PartNumber = new_part_number
            details.append(f"PartNumber 已设置为：{new_part_number}")
        except Exception as e:
            logger.warning(f"设置 PartNumber 失败：{e}")
            details.append(f"警告：PartNumber 设置失败（{e}）。")

        # ── 4. 更新零件 ───────────────────────────────────────────────────
        try:
            new_doc.Part.Update()
            details.append("零件更新完成。")
        except Exception as e:
            logger.warning(f"零件更新失败：{e}")
            details.append(f"警告：零件更新失败（{e}）。")

        return {
            "success": True,
            "message": "新零件创建成功。",
            "details": details,
        }

    except Exception as e:
        logger.error(f"从模板新建零件失败：{e}", exc_info=True)
        return {
            "success": False,
            "message": f"从模板新建零件时发生意外错误：{e}",
            "details": details,
        }
