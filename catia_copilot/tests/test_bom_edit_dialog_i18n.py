"""bom_edit_dialog_v3 的 i18n 数据流测试（Phase 2 Task 2.2）。

覆盖核心正确性边界：
1. Source 下拉框 itemData 固定为存储值 "0"/"1"/"2"，显示文案由工厂生成。
2. 写回/撤销/重做路径传存储值（raw）而非显示文案 —— 英文界面下不会把
   "Purchased" 写进 part_master 或 CATIA。
3. 界面表头走 bom_column_display 工厂；导出表头保持恒中文。
4. _read_source_raw / _set_combo_value 的行为。
"""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

from catia_copilot.ui.bom_edit_dialog_v3 import (  # noqa: E402
    BomEditDialogV3,
    _build_source_combo,
    _set_combo_value,
)

_QAPP = QApplication.instance() or QApplication([])


def _make_dialog() -> BomEditDialogV3:
    dlg = BomEditDialogV3()
    dlg._columns = ["#", "Source"]
    dlg._rows = [{"_inst_key": 111, "_pm_key": "pm1", "Source": "1"}]
    dlg._part_masters = {"pm1": {"source": "1"}}
    dlg._pm_key_to_inst_keys = {"pm1": [111]}
    dlg._inst_key_to_info = {111: {"product": MagicMock()}}
    dlg._inst_key_to_product = {}
    dlg._inst_to_items = {}
    dlg._item_by_row = [None]
    dlg._is_updating = False
    return dlg


class TestSourceCombo(unittest.TestCase):
    def test_build_source_combo_item_data_is_store_value(self) -> None:
        combo = _build_source_combo()
        self.assertEqual(combo.count(), 3)
        for i, raw in enumerate(("0", "1", "2")):
            self.assertEqual(combo.itemData(i), raw)

    def test_build_source_combo_labels_are_chinese_without_translator(self) -> None:
        combo = _build_source_combo()
        self.assertEqual(combo.itemText(0), "未知")
        self.assertEqual(combo.itemText(1), "自制")
        self.assertEqual(combo.itemText(2), "外购")

    def test_set_combo_value_prefers_item_data(self) -> None:
        combo = _build_source_combo()
        combo.setCurrentIndex(2)
        _set_combo_value(combo, "1")
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual(combo.currentData(), "1")

    def test_set_combo_value_falls_back_to_text(self) -> None:
        combo = _build_source_combo()
        _set_combo_value(combo, "外购")
        self.assertEqual(combo.currentIndex(), 2)

    def test_read_source_raw_from_widget(self) -> None:
        dlg = _make_dialog()
        dlg._table.setColumnCount(2)
        combo = _build_source_combo()
        combo.setCurrentIndex(2)
        item = QTreeWidgetItem(dlg._table, ["#", "外购"])
        dlg._table.setItemWidget(item, 1, combo)
        dlg._item_by_row = [item]
        self.assertEqual(dlg._read_source_raw(0, "fallback"), "2")

    def test_on_source_changed_passes_store_value(self) -> None:
        dlg = _make_dialog()
        dlg._table.setColumnCount(2)
        combo = _build_source_combo()
        combo.setCurrentIndex(2)
        item = QTreeWidgetItem(dlg._table, ["#", "外购"])
        dlg._table.setItemWidget(item, 1, combo)
        dlg._item_by_row = [item]
        with patch.object(dlg, "_handle_combo_col_change") as h:
            dlg._on_source_changed(0, "外购")
        h.assert_called_once_with(0, "Source", display_value="外购", store_value="2")


class TestHandleComboColChange(unittest.TestCase):
    def test_source_write_uses_store_value(self) -> None:
        dlg = _make_dialog()
        with patch.object(dlg, "_write_cell_to_catia", return_value=True) as wc, \
                patch.object(dlg, "_sync_pn_siblings_in_ui"), \
                patch.object(dlg, "_refresh_keys_appearance"):
            dlg._handle_combo_col_change(0, "Source", display_value="外购", store_value="2")
        self.assertEqual(dlg._part_masters["pm1"]["source"], "2")
        wc.assert_called_once_with(111, "Source", "2")

    def test_source_write_failure_rolls_back_store_value(self) -> None:
        dlg = _make_dialog()
        with patch.object(dlg, "_write_cell_to_catia", return_value=False), \
                patch.object(dlg, "_refresh_keys_appearance"):
            dlg._handle_combo_col_change(0, "Source", display_value="外购", store_value="2")
        self.assertEqual(dlg._part_masters["pm1"]["source"], "1")

    def test_undo_stack_stores_raw_value_not_display(self) -> None:
        dlg = _make_dialog()
        with patch.object(dlg, "_write_cell_to_catia", return_value=True), \
                patch.object(dlg, "_sync_pn_siblings_in_ui"), \
                patch.object(dlg, "_refresh_keys_appearance"):
            dlg._handle_combo_col_change(0, "Source", display_value="外购", store_value="2")
        self.assertEqual(len(dlg._undo_stack), 1)
        self.assertEqual(dlg._undo_stack[0], [("pm1", "Source", "1", "2")])

    def test_apply_field_changes_undo_writes_store_value(self) -> None:
        dlg = _make_dialog()
        dlg._part_masters["pm1"]["source"] = "2"
        with patch.object(dlg, "_write_cell_to_catia", return_value=True) as wc:
            dlg._apply_field_changes([("pm1", "Source", "1", "2")], forward=False)
        self.assertEqual(dlg._part_masters["pm1"]["source"], "1")
        self.assertEqual(wc.call_args.args[2], "1")

    def test_apply_field_changes_redo_writes_store_value(self) -> None:
        dlg = _make_dialog()
        dlg._part_masters["pm1"]["source"] = "1"
        with patch.object(dlg, "_write_cell_to_catia", return_value=True) as wc:
            dlg._apply_field_changes([("pm1", "Source", "1", "2")], forward=True)
        self.assertEqual(dlg._part_masters["pm1"]["source"], "2")
        self.assertEqual(wc.call_args.args[2], "2")


class TestHeaders(unittest.TestCase):
    def test_display_headers_chinese_without_translator(self) -> None:
        dlg = BomEditDialogV3()
        dlg._columns = ["#", "Type", "Part Number", "Source"]
        dlg._show_filepath_col = False
        self.assertEqual(dlg._display_headers(), ["#", "类型", "零件编号", "源"])

    def test_display_headers_filepath_special_case(self) -> None:
        dlg = BomEditDialogV3()
        dlg._columns = ["#", "Type", "Filename"]
        dlg._show_filepath_col = True
        self.assertEqual(dlg._display_headers(), ["#", "类型", "完整路径"])

    def test_export_header_stays_chinese(self) -> None:
        dlg = BomEditDialogV3()
        self.assertEqual(dlg._export_header("Part Number"), "零件编号")
        self.assertEqual(dlg._export_header("Source"), "源")
        dlg._show_filepath_col = True
        self.assertEqual(dlg._export_header("Filename"), "完整路径")


if __name__ == "__main__":
    unittest.main()