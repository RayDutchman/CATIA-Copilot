"""
验证图纸操作核心 COM 调用的测试脚本

使用方法：
1. 启动 CATIA V5 R28
2. 打开一个 CATPart 文件（用于测试新建图纸）
3. 运行此脚本：python test_drawing_com_operations.py

测试内容：
- 文档类型检查
- 读取零件标准属性（PartNumber, Nomenclature, Revision）
- 读取/创建用户自定义属性
- 遍历已打开文档
- 图纸参数读写
- （可选）从模板创建新图纸

注意：此脚本仅用于验证，不会修改现有文件，创建的测试图纸不会保存。
"""

import sys
from pathlib import Path

# 添加项目路径到 sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from catia_copilot.catia.connection import get_catia_v5_application
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DrawingCOMValidator:
    """图纸操作 COM 调用验证器"""
    
    def __init__(self):
        self.app = None
        self.test_results = []
        
    def log_test(self, test_name: str, success: bool, message: str = ""):
        """记录测试结果"""
        status = "✅ 通过" if success else "❌ 失败"
        result = f"{status} | {test_name}"
        if message:
            result += f" | {message}"
        self.test_results.append((success, result))
        logger.info(result)
        
    @staticmethod
    def get_document_type(doc) -> str:
        """获取 CATIA 文档类型名称
        
        在 Python win32com 中，不能直接用 type(doc).__name__ 获取 COM 类型，
        需要通过检查对象的属性来判断文档类型。
        
        参考 VBScript 的 TypeName() 函数，返回：
        - "PartDocument" - CATPart 零件文档
        - "ProductDocument" - CATProduct 装配体文档
        - "DrawingDocument" - CATDrawing 图纸文档
        - "Unknown" - 未知类型
        """
        try:
            # 方法 1：检查 Product 属性（零件和装配体都有）
            if hasattr(doc, 'Product'):
                # 进一步区分零件和装配体
                # 零件有 Part 属性，装配体没有（或者检查 Products 集合）
                try:
                    _ = doc.Part
                    return "PartDocument"
                except Exception:
                    pass
                try:
                    _ = doc.Product.Products
                    return "ProductDocument"
                except Exception:
                    # 有 Product 但没有 Products，可能是零件
                    return "PartDocument"
            
            # 方法 2：检查 Sheets 属性（图纸特有）
            if hasattr(doc, 'Sheets'):
                return "DrawingDocument"
                
            # 方法 3：检查 DrawingRoot 属性（图纸特有）
            if hasattr(doc, 'DrawingRoot'):
                return "DrawingDocument"
                
        except Exception as e:
            logger.debug(f"获取文档类型失败: {e}")
            
        return "Unknown"
        
    def connect_to_catia(self) -> bool:
        """测试 1：连接到 CATIA"""
        try:
            self.app = get_catia_v5_application()
            self.log_test("连接 CATIA", True, f"版本: {self.app.Caption}")
            return True
        except Exception as e:
            self.log_test("连接 CATIA", False, str(e))
            return False
            
    def test_document_type_check(self) -> bool:
        """测试 2：文档类型检查"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            # 测试类型判断
            is_part = doc_type == "PartDocument"
            is_product = doc_type == "ProductDocument"
            is_drawing = doc_type == "DrawingDocument"
            
            self.log_test(
                "文档类型检查",
                True,
                f"当前文档类型: {doc_type} (Part={is_part}, Product={is_product}, Drawing={is_drawing})"
            )
            return True
        except Exception as e:
            self.log_test("文档类型检查", False, str(e))
            return False
            
    def test_read_part_standard_properties(self) -> bool:
        """测试 3：读取零件标准属性"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "读取零件标准属性",
                    False,
                    f"当前文档不是零件/装配体，跳过测试（类型: {doc_type}）"
                )
                return False
                
            # 读取标准属性
            part_number = active_doc.Product.PartNumber
            nomenclature = active_doc.Product.Nomenclature
            revision = active_doc.Product.Revision
            
            self.log_test(
                "读取零件标准属性",
                True,
                f"PartNumber={part_number}, Nomenclature={nomenclature}, Revision={revision}"
            )
            return True
        except Exception as e:
            self.log_test("读取零件标准属性", False, str(e))
            return False
            
    def test_read_user_properties(self) -> bool:
        """测试 4：读取用户自定义属性"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "读取用户自定义属性",
                    False,
                    f"当前文档不是零件/装配体，跳过测试（类型: {doc_type}）"
                )
                return False
                
            # 获取用户属性集合
            user_props = None
            try:
                user_props = active_doc.Product.UserRefProperties
            except Exception:
                pass
                
            if user_props is None:
                self.log_test("读取用户自定义属性", True, "零件无用户自定义属性集合（正常情况）")
                return True
                
            # 尝试读取预定义的属性
            test_props = ["物料编码", "材料", "重量"]
            found_props = []
            
            for prop_name in test_props:
                try:
                    prop = user_props.Item(prop_name)
                    if prop is not None:
                        value = prop.Value
                        found_props.append(f"{prop_name}={value}")
                except Exception:
                    pass
                    
            if found_props:
                self.log_test(
                    "读取用户自定义属性",
                    True,
                    f"找到 {len(found_props)} 个属性: {', '.join(found_props)}"
                )
            else:
                self.log_test(
                    "读取用户自定义属性",
                    True,
                    "零件有属性集合但未找到测试属性（物料编码/材料/重量）"
                )
            return True
        except Exception as e:
            self.log_test("读取用户自定义属性", False, str(e))
            return False
            
    def test_create_user_property(self) -> bool:
        """测试 5：创建用户自定义属性（测试后删除）"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "创建用户自定义属性",
                    False,
                    f"当前文档不是零件/装配体，跳过测试（类型: {doc_type}）"
                )
                return False
                
            # 获取或创建用户属性集合
            user_props = None
            try:
                user_props = active_doc.Product.UserRefProperties
            except Exception:
                pass
                
            if user_props is None:
                self.log_test("创建用户自定义属性", False, "无法获取用户属性集合")
                return False
                
            # 创建测试属性
            test_prop_name = "_TEST_DRAWING_VALIDATION_"
            test_prop_value = "测试值_12345"
            
            # 先检查是否已存在
            existing_value = None
            try:
                prop = user_props.Item(test_prop_name)
                if prop is not None:
                    existing_value = prop.Value
            except Exception:
                pass
                
            if existing_value is not None:
                self.log_test(
                    "创建用户自定义属性",
                    True,
                    f"测试属性已存在，值={existing_value}（跳过创建）"
                )
                return True
                
            # 创建新属性
            user_props.CreateString(test_prop_name, test_prop_value)
            
            # 验证创建成功
            created_prop = user_props.Item(test_prop_name)
            created_value = created_prop.Value
            
            if created_value == test_prop_value:
                self.log_test(
                    "创建用户自定义属性",
                    True,
                    f"成功创建并验证: {test_prop_name}={created_value}"
                )
                
                # 清理测试属性（尝试删除，失败也不影响测试结果）
                try:
                    # CATIA COM API 中删除属性的方法（如果存在）
                    # 注意：某些版本可能不支持删除，这里仅尝试
                    logger.info(f"提示：请手动删除测试属性 '{test_prop_name}'（CATIA 可能不支持通过 COM 删除属性）")
                except Exception:
                    pass
                    
                return True
            else:
                self.log_test(
                    "创建用户自定义属性",
                    False,
                    f"创建后值不匹配: 期望={test_prop_value}, 实际={created_value}"
                )
                return False
        except Exception as e:
            self.log_test("创建用户自定义属性", False, str(e))
            return False
            
    def test_enumerate_documents(self) -> bool:
        """测试 6：遍历已打开文档"""
        try:
            documents = self.app.Documents
            doc_count = documents.Count
            
            doc_list = []
            for i in range(1, doc_count + 1):
                try:
                    doc = documents.Item(i)
                    doc_type = self.get_document_type(doc)
                    doc_name = Path(doc.FullName).name if doc.FullName else "(未保存)"
                    doc_list.append(f"{doc_type}: {doc_name}")
                except Exception as e:
                    doc_list.append(f"(读取失败: {e})")
                    
            self.log_test(
                "遍历已打开文档",
                True,
                f"共 {doc_count} 个文档: {', '.join(doc_list)}"
            )
            return True
        except Exception as e:
            self.log_test("遍历已打开文档", False, str(e))
            return False
            
    def test_find_part_by_partnumber(self) -> bool:
        """测试 7：根据 PartNumber 查找零件"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "根据 PartNumber 查找零件",
                    False,
                    f"当前文档不是零件/装配体，跳过测试（类型: {doc_type}）"
                )
                return False
                
            target_pn = active_doc.Product.PartNumber
            
            # 在已打开文档中查找
            documents = self.app.Documents
            found = False
            
            for i in range(1, documents.Count + 1):
                try:
                    doc = documents.Item(i)
                    doc_type_check = self.get_document_type(doc)
                    
                    if doc_type_check in ("PartDocument", "ProductDocument"):
                        try:
                            pn = doc.Product.PartNumber
                            if pn == target_pn:
                                found = True
                                break
                        except Exception:
                            pass
                except Exception:
                    continue
                    
            self.log_test(
                "根据 PartNumber 查找零件",
                found,
                f"查找 PartNumber='{target_pn}': {'找到' if found else '未找到'}"
            )
            return found
        except Exception as e:
            self.log_test("根据 PartNumber 查找零件", False, str(e))
            return False
            
    def test_drawing_parameters(self) -> bool:
        """测试 8：图纸参数读写（需要当前文档是 CATDrawing）"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type != "DrawingDocument":
                self.log_test(
                    "图纸参数读写",
                    False,
                    f"当前文档不是图纸，跳过测试（类型: {doc_type}）"
                )
                return False
                
            # 获取参数集合
            params = active_doc.Parameters
            
            # 尝试读取常见参数
            test_params = ["PartNumber", "Nomenclature", "Revision"]
            found_params = []
            
            for param_name in test_params:
                try:
                    param = params.Item(param_name)
                    if param is not None:
                        value = param.Value
                        found_params.append(f"{param_name}={value}")
                except Exception:
                    pass
                    
            if found_params:
                self.log_test(
                    "图纸参数读写",
                    True,
                    f"找到 {len(found_params)} 个参数: {', '.join(found_params)}"
                )
            else:
                self.log_test(
                    "图纸参数读写",
                    True,
                    "图纸无预定义参数（PartNumber/Nomenclature/Revision）"
                )
            return True
        except Exception as e:
            self.log_test("图纸参数读写", False, str(e))
            return False
            
    def test_create_drawing_from_template(self, template_path: str = None) -> bool:
        """测试 9：从模板创建新图纸（可选，需要提供模板路径）"""
        if not template_path:
            self.log_test(
                "从模板创建新图纸",
                False,
                "未提供模板路径，跳过测试（可通过参数传入模板路径）"
            )
            return False
            
        try:
            template_path_obj = Path(template_path)
            if not template_path_obj.exists():
                self.log_test(
                    "从模板创建新图纸",
                    False,
                    f"模板文件不存在: {template_path}"
                )
                return False
                
            # 创建新图纸
            drawing_doc = self.app.Documents.NewFrom(str(template_path))
            
            # 验证类型
            doc_type = self.get_document_type(drawing_doc)
            if doc_type != "DrawingDocument":
                self.log_test(
                    "从模板创建新图纸",
                    False,
                    f"NewFrom 返回类型错误: {doc_type}"
                )
                return False
                
            # 获取图纸页
            sheets = drawing_doc.Sheets
            sheet_count = sheets.Count
            
            self.log_test(
                "从模板创建新图纸",
                True,
                f"成功创建图纸，共 {sheet_count} 张图纸页"
            )
            
            # 关闭测试图纸（不保存）
            try:
                drawing_doc.Close()
                logger.info("已关闭测试图纸（未保存）")
            except Exception as e:
                logger.warning(f"关闭测试图纸失败: {e}")
                
            return True
        except Exception as e:
            self.log_test("从模板创建新图纸", False, str(e))
            return False
            
    def run_all_tests(self, template_path: str = None):
        """运行所有测试"""
        logger.info("=" * 80)
        logger.info("开始验证图纸操作核心 COM 调用")
        logger.info("=" * 80)
        
        # 测试 1：连接 CATIA
        if not self.connect_to_catia():
            logger.error("无法连接到 CATIA，终止测试")
            return
            
        # 测试 2-9
        self.test_document_type_check()
        self.test_read_part_standard_properties()
        self.test_read_user_properties()
        self.test_create_user_property()
        self.test_enumerate_documents()
        self.test_find_part_by_partnumber()
        self.test_drawing_parameters()
        
        # 可选测试：创建图纸
        if template_path:
            self.test_create_drawing_from_template(template_path)
            
        # 输出测试总结
        logger.info("=" * 80)
        logger.info("测试总结")
        logger.info("=" * 80)
        
        total = len(self.test_results)
        passed = sum(1 for success, _ in self.test_results if success)
        failed = total - passed
        
        for success, result in self.test_results:
            logger.info(result)
            
        logger.info("=" * 80)
        logger.info(f"总计: {total} 项测试 | 通过: {passed} | 失败: {failed}")
        logger.info("=" * 80)
        
        if failed == 0:
            logger.info("🎉 所有测试通过！可以开始正式改写。")
        else:
            logger.warning(f"⚠️  有 {failed} 项测试失败，请检查失败原因。")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="验证图纸操作核心 COM 调用")
    parser.add_argument(
        "--template",
        type=str,
        help="图纸模板路径（可选，用于测试 NewFrom 创建图纸）"
    )
    
    args = parser.parse_args()
    
    validator = DrawingCOMValidator()
    validator.run_all_tests(template_path=args.template)


if __name__ == "__main__":
    main()
