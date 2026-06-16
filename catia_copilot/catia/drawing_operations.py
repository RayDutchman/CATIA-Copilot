"""
图纸操作模块

提供新建图纸和刷新图纸功能，替代原有的 VBScript 宏实现。

主要功能：
- generate_drawing()           - 从当前活动的 CATPart/CATProduct 生成新图纸
- refresh_drawing()            - 刷新当前活动图纸的参数信息
- sync_to_drawing_parameters() - 同步零件属性到图纸参数（核心逻辑）
"""

import logging
from pathlib import Path
from typing import Callable

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.document import (
    get_document_type,  # noqa: F401 — re-exported for callers
)
from catia_copilot.constants import (
    DRAWING_SYNC_STANDARD_PARAMS,
    DRAWING_SYNC_USER_PROPS,
)

logger = logging.getLogger(__name__)


def sync_to_drawing_parameters(
    part_doc,
    drawing_doc,
    property_names: list[str] | None = None,
    input_callback: Callable[[str, str], tuple[str, bool]] | None = None,
) -> dict:
    """同步零件属性到图纸参数
    
    从零件/产品文档读取标准属性和用户自定义属性，写入图纸参数。
    图纸中若不存在对应参数，则跳过（不自动新建，不报错）。
    
    Args:
        part_doc: 零件或产品文档 COM 对象
        drawing_doc: 图纸文档 COM 对象
        property_names: 需要同步的用户自定义属性名列表，
                       默认为 DRAWING_SYNC_USER_PROPS（["物料编码", "材料", "重量"]）
        input_callback: 当属性不存在时的回调函数，接收 (属性名, 零件编号)，
                       返回 (用户输入值, 是否确认)。如果为 None，缺失属性将以空值写入。
                       
    Returns:
        同步日志字典，包含：
        - "success": bool - 是否成功
        - "message": str - 总体消息
        - "details": list[str] - 详细日志列表
        - "part_number": str - 零件编号
    """
    log_details = []
    
    # 默认属性列表
    if property_names is None:
        property_names = list(DRAWING_SYNC_USER_PROPS)
    
    try:
        # 1. 读取零件标准属性
        part_number = ""
        nomenclature = ""
        revision = ""
        
        try:
            part_number = part_doc.Product.PartNumber
            nomenclature = part_doc.Product.Nomenclature
            revision = part_doc.Product.Revision
        except Exception as e:
            logger.warning(f"读取零件标准属性失败: {e}")
            
        # 2. 获取零件用户自定义属性集合
        part_user_props = None
        try:
            part_user_props = part_doc.Product.UserRefProperties
        except Exception:
            pass
            
        if part_user_props is None:
            log_details.append("零件无用户自定义属性集合")
            
        # 3. 获取图纸参数集合
        params = drawing_doc.Parameters
        
        # 4. 同步标准属性（PartNumber / Nomenclature / Revision）
        standard_props = dict(zip(
            DRAWING_SYNC_STANDARD_PARAMS,
            [part_number, nomenclature, revision],
        ))
        
        for param_name, value in standard_props.items():
            try:
                param = params.Item(param_name)
                param.Value = value
                log_details.append(f"已同步：{param_name} = {value}")
            except Exception:
                # 图纸中不存在该参数，跳过（不新建）
                log_details.append(f"图纸中无参数 {param_name}，已跳过")
                
        # 5. 批量同步自定义属性
        for prop_name in property_names:
            prop_value = ""
            prop_exists = False
            
            # 尝试从零件读取属性值
            if part_user_props is not None:
                try:
                    tmp_prop = part_user_props.Item(prop_name)
                    if tmp_prop is not None:
                        prop_value = tmp_prop.Value
                        prop_exists = True
                except Exception:
                    pass
                    
            # 属性不存在时的处理
            if not prop_exists:
                if input_callback is not None:
                    # 调用回调函数获取用户输入
                    user_input, confirmed = input_callback(prop_name, part_number)
                    if confirmed:
                        prop_value = user_input
                        
                        # 将填入的值写回零件属性
                        if prop_value:
                            try:
                                if part_user_props is None:
                                    part_user_props = part_doc.Product.UserRefProperties
                                if part_user_props is not None:
                                    part_user_props.CreateString(prop_name, prop_value)
                                    log_details.append(f"已创建零件属性：{prop_name} = {prop_value}")
                            except Exception as e:
                                logger.warning(f"创建零件属性 {prop_name} 失败: {e}")
                    else:
                        log_details.append(f"用户取消输入属性：{prop_name}")
                else:
                    log_details.append(f"属性 {prop_name} 不存在，以空值写入")
                    
            # 写入图纸参数（图纸中不存在该参数则跳过，不新建）
            try:
                param = params.Item(prop_name)
                param.Value = prop_value
                log_details.append(f"已同步：{prop_name} = {prop_value}")
            except Exception:
                log_details.append(f"图纸中无参数 {prop_name}，已跳过")
                
        # 6. 更新图纸显示
        try:
            drawing_doc.Update()
            log_details.append("图纸已更新")
        except Exception as e:
            logger.warning(f"更新图纸失败: {e}")
            
        return {
            "success": True,
            "message": "同步完成",
            "details": log_details,
            "part_number": part_number,
        }
        
    except Exception as e:
        logger.error(f"同步属性到图纸参数失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"同步失败: {e}",
            "details": log_details,
            "part_number": "",
        }


def generate_drawing(
    template_path: str,
    property_names: list[str] | None = None,
    input_callback: Callable[[str, str], tuple[str, bool]] | None = None,
) -> dict:
    """从当前活动的 CATPart/CATProduct 生成新图纸
    
    Args:
        template_path: 图纸模板文件的完整路径（.CATDrawing）
        property_names: 需要同步的用户自定义属性名列表，
                       默认为 DRAWING_SYNC_USER_PROPS（["物料编码", "材料", "重量"]）
        input_callback: 当属性不存在时的回调函数，接收 (属性名, 零件编号)，
                       返回 (用户输入值, 是否确认)
                       
    Returns:
        操作结果字典，包含：
        - "success": bool - 是否成功
        - "message": str - 总体消息
        - "details": list[str] - 详细日志列表
        - "drawing_doc": COM 对象 - 创建的图纸文档（成功时）
        
    Raises:
        RuntimeError: 当前文档不是零件/产品，或创建图纸失败时
    """
    try:
        app = get_catia_v5_application()
        
        # 1. 验证当前活动文档是否为零件或产品
        active_doc = app.ActiveDocument
        doc_type = get_document_type(active_doc)
        
        if doc_type not in ("PartDocument", "ProductDocument"):
            raise RuntimeError(
                f"请先激活一个 CATPart 或 CATProduct 文档！\n"
                f"当前文档类型: {doc_type}"
            )
            
        part_doc = active_doc
        
        # 2. 验证模板文件存在
        template_path_obj = Path(template_path)
        if not template_path_obj.exists():
            raise RuntimeError(f"图纸模板文件不存在: {template_path}")
            
        # 3. 从模板创建新图纸
        logger.info(f"正在从模板创建图纸: {template_path}")
        drawing_doc = app.Documents.NewFrom(str(template_path))
        
        # 4. 验证返回类型
        drawing_type = get_document_type(drawing_doc)
        if drawing_type != "DrawingDocument":
            raise RuntimeError(
                f"NewFrom 返回类型错误: {drawing_type}\n"
                f"期望: DrawingDocument"
            )
            
        # 5. 同步零件属性到图纸参数
        sync_result = sync_to_drawing_parameters(
            part_doc,
            drawing_doc,
            property_names=property_names,
            input_callback=input_callback,
        )
        
        # 6. 激活第一张图纸页
        try:
            drawing_sheet = drawing_doc.Sheets.Item(1)
            drawing_sheet.Activate()
            sync_result["details"].append("已激活第一张图纸页")
        except Exception as e:
            logger.warning(f"激活图纸页失败: {e}")
            
        sync_result["drawing_doc"] = drawing_doc
        return sync_result
        
    except Exception as e:
        logger.error(f"生成图纸失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"生成图纸失败: {e}",
            "details": [],
        }


def refresh_drawing(
    property_names: list[str] | None = None,
    input_callback: Callable[[str, str], tuple[str, bool]] | None = None,
) -> dict:
    """刷新当前活动图纸的参数信息
    
    从图纸参数中读取 PartNumber，在已打开的文档中查找对应的零件/产品，
    然后同步属性到图纸参数。
    
    Args:
        property_names: 需要同步的用户自定义属性名列表，
                       默认为 DRAWING_SYNC_USER_PROPS（["物料编码", "材料", "重量"]）
        input_callback: 当属性不存在时的回调函数，接收 (属性名, 零件编号)，
                       返回 (用户输入值, 是否确认)
                       
    Returns:
        操作结果字典，包含：
        - "success": bool - 是否成功
        - "message": str - 总体消息
        - "details": list[str] - 详细日志列表
        
    Raises:
        RuntimeError: 当前文档不是图纸，或找不到对应的零件/产品时
    """
    try:
        app = get_catia_v5_application()
        
        # 1. 验证当前活动文档是否为图纸
        active_doc = app.ActiveDocument
        doc_type = get_document_type(active_doc)
        
        if doc_type != "DrawingDocument":
            raise RuntimeError(
                f"请先激活一个 CATDrawing 图纸文档！\n"
                f"当前文档类型: {doc_type}"
            )
            
        drawing_doc = active_doc
        
        # 2. 从图纸参数中读取 PartNumber
        pn_param = None
        try:
            pn_param = drawing_doc.Parameters.Item("PartNumber")
        except Exception:
            pass
            
        if pn_param is None:
            raise RuntimeError(
                '图纸参数中未找到 "PartNumber"，无法自动匹配零件。\n'
                '请确保图纸模板中包含 PartNumber 参数。'
            )
            
        target_pn = pn_param.Value
        
        if not target_pn or not target_pn.strip():
            raise RuntimeError(
                "图纸参数 PartNumber 为空，无法自动匹配零件。\n"
                "请先手动设置图纸的 PartNumber 参数。"
            )
            
        # 3. 在所有已打开文档中查找匹配零件编号的零件或产品
        documents = app.Documents
        found_doc = None
        
        for i in range(1, documents.Count + 1):
            try:
                doc = documents.Item(i)
                doc_type_check = get_document_type(doc)
                
                if doc_type_check in ("PartDocument", "ProductDocument"):
                    try:
                        pn = doc.Product.PartNumber
                        if pn == target_pn:
                            found_doc = doc
                            break
                    except Exception:
                        pass
            except Exception:
                continue
                
        if found_doc is None:
            raise RuntimeError(
                f'未找到零件编号为 "{target_pn}" 的零件或产品文档。\n'
                f'请确保对应文档已在 CATIA 中打开。'
            )
            
        # 4. 同步零件属性到图纸参数
        sync_result = sync_to_drawing_parameters(
            found_doc,
            drawing_doc,
            property_names=property_names,
            input_callback=input_callback,
        )
        
        return sync_result
        
    except Exception as e:
        logger.error(f"刷新图纸失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"刷新图纸失败: {e}",
            "details": [],
        }
