"""导出不得重复打开或关闭调用前已打开的文档。"""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from catia_copilot.catia.conversion import convert_part_to_step, convert_drawing_to_pdf


class TestExportOpenDocuments(unittest.TestCase):
    def test_step_reuses_existing_document_without_reopen_or_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'part.CATPart')
            doc = Mock(FullName=path)
            doc.ExportData.side_effect = lambda dest, kind: Path(dest).write_text(kind)
            docs = Mock(Count=1)
            docs.Item.return_value = doc
            docs.Open.side_effect = AssertionError('must not reopen')
            with patch('catia_copilot.catia.conversion.get_catia_v5_application', return_value=SimpleNamespace(Documents=docs)):
                self.assertEqual(convert_part_to_step([path], output_folder=directory), 1)
            docs.Open.assert_not_called()
            doc.Close.assert_not_called()
            self.assertTrue((Path(directory) / 'MD_part.stp').exists())

    def test_pdf_exports_returned_document_and_closes_only_new_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'drawing.CATDrawing')
            doc = Mock()
            doc.DrawingRoot.Sheets.Count = 1
            doc.ExportData.side_effect = lambda dest, kind: Path(dest).write_text(kind)
            docs = Mock(Count=0)
            docs.Open.return_value = doc
            app = SimpleNamespace(Documents=docs, ActiveDocument=Mock())
            with patch('catia_copilot.catia.conversion.get_catia_v5_application', return_value=app):
                self.assertEqual(convert_drawing_to_pdf([path], output_folder=directory, update_before_export=True), 1)
            doc.Update.assert_called_once()
            doc.Close.assert_called_once()
            app.ActiveDocument.ExportData.assert_not_called()
