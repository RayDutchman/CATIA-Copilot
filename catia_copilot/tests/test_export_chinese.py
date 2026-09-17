"""导出路径 i18n 数据流测试（Phase 2）：英文界面下导出仍保持恒中文。

覆盖：
1. export_bom_dialog._make_col_item：QListWidgetItem 文案走 bom_column_display 工厂，
   语言缺失时回退中文，itemData 恒为内部列名（不因语言变化）。
2. _sort_col_combo 显示名也来自工厂。
3. 质量特性 CSV 导出表头/数据保持中文（在 test_mass_props_i18n 中已有 exporter 断言，
   此处验证列 item 的内部标识不受文案影响，避免导出列重名/错存）。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from catia_copilot.ui.export_bom_dialog import ExportBomDialog  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])


class TestExportBomColumnItems(unittest.TestCase):
    def test_make_col_item_text_is_chinese_fallback(self) -> None:
        item = ExportBomDialog._make_col_item("Part Number")
        self.assertEqual(item.text(), "零件编号")

    def test_make_col_item_keeps_internal_data(self) -> None:
        item = ExportBomDialog._make_col_item("Part Number")
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole), "Part Number")

    def test_make_col_item_unknown_key_falls_back_to_key(self) -> None:
        item = ExportBomDialog._make_col_item("Custom-Unknown")
        self.assertEqual(item.text(), "Custom-Unknown")
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole), "Custom-Unknown")

    def test_sort_combo_items_use_factory_labels(self) -> None:
        dlg = ExportBomDialog()
        texts = [dlg._sort_col_combo.itemText(i) for i in range(dlg._sort_col_combo.count())]
        self.assertIn("零件编号", texts)
        self.assertIn("层级", texts)

    def test_dialog_title_chinese(self) -> None:
        dlg = ExportBomDialog()
        self.assertEqual(dlg.windowTitle(), "从产品导出 BOM")


if __name__ == "__main__":
    unittest.main()