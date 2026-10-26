"""行数据 BOM 导出回归测试。"""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from catia_copilot.catia import bom_export, file_interaction


class TestBomRowExport(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.rows = [
            {"Part Number": "零件-甲", "Quantity": 3, "Custom": "中文值"},
            {"Part Number": "零件-乙", "Quantity": 1, "Custom": ""},
        ]

    def output(self, name: str) -> Path:
        return Path(self.temp_dir.name) / name

    def test_writes_xlsx_headers_rows_and_custom_columns_without_connecting(self) -> None:
        output = self.output("bom.xlsx")
        with patch.object(bom_export, "get_catia_v5_application", side_effect=AssertionError):
            result = bom_export.export_bom_rows(
                self.rows, ["Part Number", "Quantity", "Custom"], output
            )
        self.assertEqual(result, {"success": True, "output_file": str(output), "row_count": 2})
        workbook = openpyxl.load_workbook(output, data_only=True)
        sheet = workbook.active
        self.assertEqual(list(sheet.values), [
            ("零件编号", "数量", "Custom"),
            ("零件-甲", 3, "中文值"),
            ("零件-乙", 1, None),
        ])

    def test_writes_csv_with_utf8_bom_and_correct_rows(self) -> None:
        output = self.output("bom.csv")
        result = bom_export.export_bom_rows(
            self.rows, ["Part Number", "Quantity", "Custom"], output
        )
        self.assertEqual(result["row_count"], 2)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        with output.open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [
                ["零件编号", "数量", "Custom"],
                ["零件-甲", "3", "中文值"],
                ["零件-乙", "1", ""],
            ])

    def test_writes_empty_rows_with_header(self) -> None:
        output = self.output("empty.csv")
        result = bom_export.export_bom_rows([], ["Part Number"], output)
        self.assertEqual(result["row_count"], 0)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [["零件编号"]])

    def test_rejects_other_extensions(self) -> None:
        with self.assertRaises(ValueError):
            bom_export.export_bom_rows(self.rows, ["Part Number"], self.output("bom.txt"))

    def test_existing_file_defaults_to_error(self) -> None:
        output = self.output("existing.csv")
        output.write_text("original", encoding="utf-8")
        with patch.object(file_interaction, "overwrite_handler", None):
            with self.assertRaises(FileExistsError):
                bom_export.export_bom_rows(self.rows, ["Part Number"], output)
        self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_existing_file_can_be_skipped_once(self) -> None:
        output = self.output("existing.csv")
        output.write_text("original", encoding="utf-8")
        with patch.object(file_interaction, "overwrite_handler", return_value="skip"):
            result = bom_export.export_bom_rows(self.rows, ["Part Number"], output)
        self.assertEqual(result, {"success": True, "skipped": True, "output_file": str(output), "row_count": 0})
        self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_existing_file_can_be_skipped_for_all(self) -> None:
        output = self.output("existing.csv")
        output.write_text("original", encoding="utf-8")
        with patch.object(file_interaction, "overwrite_handler", return_value="skip_all"):
            result = bom_export.export_bom_rows(self.rows, ["Part Number"], output)
        self.assertTrue(result["skipped"])
        self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_existing_file_can_be_overwritten_once(self) -> None:
        output = self.output("existing.csv")
        output.write_text("original", encoding="utf-8")
        with patch.object(file_interaction, "overwrite_handler", return_value="overwrite"):
            result = bom_export.export_bom_rows(self.rows, ["Part Number"], output)
        self.assertTrue(result["success"])
        self.assertEqual(result["row_count"], 2)
        self.assertNotEqual(output.read_text(encoding="utf-8"), "original")

    def test_existing_file_can_be_overwritten_for_all(self) -> None:
        output = self.output("existing.csv")
        output.write_text("original", encoding="utf-8")
        with patch.object(file_interaction, "overwrite_handler", return_value="overwrite_all"):
            result = bom_export.export_bom_rows(self.rows, ["Part Number"], output)
        self.assertTrue(result["success"])
        self.assertEqual(result["row_count"], 2)
        self.assertNotEqual(output.read_text(encoding="utf-8"), "original")

    def test_cli_backend_replace_and_skip_policies(self) -> None:
        from catia_copilot.cli import backend

        def write_rows(output_file, rows, columns, progress_signal=None):
            return bom_export.export_bom_rows(rows, columns, output_file)

        with patch.dict(backend.tools_map, {"export_bom_to_excel": write_rows}):
            replace_output = self.output("backend-replace.csv")
            replace_output.write_text("original", encoding="utf-8")
            replaced = backend.execute("export_bom_to_excel", {
                "output_file": replace_output,
                "rows": self.rows,
                "columns": ["Part Number"],
                "overwrite": "replace",
            })
            skip_output = self.output("backend-skip.csv")
            skip_output.write_text("original", encoding="utf-8")
            skipped = backend.execute("export_bom_to_excel", {
                "output_file": skip_output,
                "rows": self.rows,
                "columns": ["Part Number"],
                "overwrite": "skip",
            })

        self.assertTrue(replaced["success"])
        self.assertEqual(replaced["row_count"], 2)
        self.assertTrue(skipped["skipped"])
        self.assertEqual(skip_output.read_text(encoding="utf-8"), "original")

    def test_legacy_export_still_writes_active_document_rows(self) -> None:
        class Documents:
            Count = 0

        documents = Documents()

        class Application:
            Documents = documents
            ActiveDocument = type("Document", (), {"FullName": "C:/source.CATProduct"})()

        output_dir = Path(self.temp_dir.name) / "legacy"
        with patch.object(bom_export, "get_catia_v5_application", return_value=Application()), \
                patch.object(bom_export, "collect_bom_rows_archive", return_value=self.rows):
            paths = bom_export.export_bom_to_excel(
                [None], output_folder=str(output_dir), columns=["Part Number", "Quantity"],
                output_format="xlsx",
            )
        self.assertEqual(paths, [(output_dir / "source_BOM.xlsx").resolve()])
        sheet = openpyxl.load_workbook(paths[0], data_only=True).active
        self.assertEqual(list(sheet.values), [
            ("零件编号", "数量"),
            ("零件-甲", 3),
            ("零件-乙", 1),
        ])


if __name__ == "__main__":
    unittest.main()
