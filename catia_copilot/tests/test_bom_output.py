"""验证导出入口使用与采集一致的行和列，不重新选择文档。"""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class TestBomOutput(unittest.TestCase):
    def test_collect_view_is_written_without_target_metadata(self):
        from catia_copilot.catia import bom_output
        data = {'view': 'summary', 'columns': ['Part Number', 'Quantity', '备注'],
                'rows': [{'Part Number': 'P1', 'Quantity': 3, '备注': '支架',
                          'targets': [{'node': '0.0'}, {'node': '0.1'}]}], 'row_count': 1}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'bom.csv'
            with patch.object(bom_output, 'collect_bom', return_value=data) as collect:
                result = bom_output.export_bom(str(output), doc='Assembly', view='summary', custom_columns=['备注'])
            self.assertEqual(collect.call_args.kwargs['doc'], 'Assembly')
            self.assertEqual(collect.call_args.kwargs['view'], 'summary')
            with output.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1], ['P1', '3', '支架'])
            self.assertEqual(result['row_count'], 1)
            self.assertEqual(result['view'], 'summary')

    def test_incomplete_collection_does_not_export_partial_data_as_success(self):
        from catia_copilot.catia import bom_output
        with patch.object(bom_output, 'collect_bom', return_value={'error': '无法定位目标'}), \
             patch.object(bom_output, 'export_bom_rows') as export:
            result = bom_output.export_bom('ignored.csv')
        self.assertIn('error', result)
        export.assert_not_called()
