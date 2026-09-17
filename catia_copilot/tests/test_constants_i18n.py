"""constants 显示工厂与业务不变量测试（Phase 2 Task 2.1）。

验证三件事：
1. 业务不变量的原值保持不变（Source 存储值 "0"/"1"/"2"、BomNodeType 英文 key、
   文件名哨兵常量、PRESET 用户属性名、显示字典键集合等）——这些值一旦被翻译，
   导出/写回/持久化路径就会失真。
2. 新增的显示工厂函数在中文环境下返回源中文，且对未知 key 原样透传。
3. 安装英文翻译器后，显示工厂返回 qm 中的译文（可翻译性证明）。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_copilot import constants as C  # noqa: E402
from catia_copilot import i18n  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])


class TestBusinessInvariants(unittest.TestCase):
    """这些值绝不能被翻译或改写——它们是存储/导出/写回的真实数据。"""

    def test_source_store_values_unchanged(self) -> None:
        self.assertEqual(C.SOURCE_TO_DISPLAY, {"0": "未知", "1": "自制", "2": "外购"})
        self.assertEqual(C.SOURCE_FROM_DISPLAY, {"未知": "0", "自制": "1", "外购": "2"})
        self.assertEqual(C.SOURCE_OPTIONS, ["未知", "自制", "外购"])

    def test_bom_node_type_keys_unchanged(self) -> None:
        self.assertEqual(C.BomNodeType.PART, "Part")
        self.assertEqual(C.BomNodeType.PRODUCT, "Product")
        self.assertEqual(C.BomNodeType.COMPONENT, "Component")
        self.assertEqual(C.BomNodeType.MIRROR, "Mirror")

    def test_filename_sentinels_unchanged(self) -> None:
        self.assertEqual(C.FILENAME_NOT_FOUND, "未检索到")
        self.assertEqual(C.FILENAME_UNSAVED, "未保存")

    def test_preset_user_ref_property_value_unchanged(self) -> None:
        self.assertIn("零件类型", C.PRESET_USER_REF_PROPERTIES)
        options = C.PRESET_USER_REF_PROPERTY_OPTIONS
        self.assertEqual(options["设计状态"], ["草稿", "冻结", "发布", "废弃"])
        self.assertEqual(options["PLM_Version"], ["A", "B", "C", "D", "E", "F", "G", "H"])

    def test_bom_column_display_names_keys(self) -> None:
        expected_keys = {
            "#", "Level", "Type", "Filename", "Filepath", "Part Number",
            "Nomenclature", "Definition", "Revision", "Source",
            "Description", "Quantity", "Instance Name", "description_inst",
        }
        self.assertEqual(set(C.BOM_COLUMN_DISPLAY_NAMES), expected_keys)

    def test_source_internal_column_constants(self) -> None:
        self.assertEqual(C.BOM_ROW_NUMBER_COLUMN, "#")
        self.assertEqual(C.BOM_INSTANCE_NAME_COLUMN, "Instance Name")

    def test_product_attr_maps_use_english_com_attr_names(self) -> None:
        self.assertEqual(C.PRODUCT_ATTR_WRITE_MAP["Source"], "Source")
        self.assertEqual(C.PRODUCT_ATTR_WRITE_MAP["Part Number"], "PartNumber")


class TestDisplayFactoriesChinese(unittest.TestCase):
    """未安装翻译器/词条缺失时工厂必须回退源中文。"""

    def test_type_display(self) -> None:
        self.assertEqual(C.type_display(C.BomNodeType.PART), "零件")
        self.assertEqual(C.type_display(C.BomNodeType.PRODUCT), "产品")
        self.assertEqual(C.type_display(C.BomNodeType.COMPONENT), "部件")
        self.assertEqual(C.type_display(C.BomNodeType.MIRROR), "对称件")
        self.assertEqual(C.type_display("Unknown"), "Unknown")

    def test_source_display(self) -> None:
        self.assertEqual(C.source_display("0"), "未知")
        self.assertEqual(C.source_display("1"), "自制")
        self.assertEqual(C.source_display("2"), "外购")
        self.assertEqual(C.source_display("9"), "9")

    def test_bom_column_display(self) -> None:
        self.assertEqual(C.bom_column_display("Part Number"), "零件编号")
        self.assertEqual(C.bom_column_display("#"), "#")
        self.assertEqual(C.bom_column_display("Description"), "描述")
        self.assertEqual(C.bom_column_display("NoSuchColumn"), "NoSuchColumn")

    def test_mass_props_column_display(self) -> None:
        self.assertEqual(C.mass_props_column_display("Density"), "密度 (kg/m³)")
        self.assertEqual(C.mass_props_column_display("Weight"), "重量 (kg)")
        self.assertEqual(C.mass_props_column_display("CogX"), "重心 X (mm)")
        self.assertEqual(C.mass_props_column_display("Status"), "状态")


class TestDisplayFactoriesTranslatable(unittest.TestCase):
    """安装含对应词条的英文 qm 后，工厂必须返回英文译文（而非仍然回退中文）。"""

    _kept = []

    def tearDown(self) -> None:
        for translator in self._kept:
            if translator is not None:
                _QAPP.removeTranslator(translator)
        self._kept.clear()
        super().tearDown()

    def _install_mini_en_qm(self) -> None:
        d = Path(tempfile.mkdtemp())
        ts = d / "mini.ts"
        ts.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<TS version="2.1" language="en_US">\n'
            '<context><name>CATIACopilot</name>\n'
            '<message><source>零件</source><translation>Part</translation></message>\n'
            '<message><source>外购</source><translation>Purchased</translation></message>\n'
            '<message><source>零件编号</source><translation>Part Number</translation></message>\n'
            '<message><source>密度 (kg/m³)</source><translation>Density (kg/m³)</translation></message>\n'
            "</context></TS>",
            encoding="utf-8",
        )
        subprocess.run(
            ["pyside6-lrelease", str(ts)], check=True, capture_output=True
        )
        from PySide6.QtCore import QTranslator  # noqa: PLC0415

        tr = QTranslator(_QAPP)
        self.assertTrue(tr.load(str(d / "mini.qm")))
        self._kept.append(tr)
        _QAPP.installTranslator(tr)

    def test_factories_translate_under_en(self) -> None:
        self._install_mini_en_qm()
        self.assertEqual(i18n.translate("CATIACopilot", "零件"), "Part")
        self.assertEqual(C.type_display("Part"), "Part")
        self.assertEqual(C.source_display("2"), "Purchased")
        self.assertEqual(C.bom_column_display("Part Number"), "Part Number")
        self.assertEqual(C.mass_props_column_display("Density"), "Density (kg/m³)")


if __name__ == "__main__":
    unittest.main()