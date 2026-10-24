"""迁出工具的真实 Python 执行路径，使用替身隔离 CATIA。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from catia_copilot.catia import tool_service, modeling
from catia_copilot.cli import backend


class TestRuntime(unittest.TestCase):
    def test_script_loads_and_preserves_optional_verification(self):
        document = SimpleNamespace(Name='A.CATPart', FullName='D:/A.CATPart', Type='PartDocument', Product=SimpleNamespace(PartNumber='A'))
        context = SimpleNamespace(steps=[])
        script = "def build(ctx):\n    ctx.steps.append({'step': 'fixture', 'status': 'ok'})\n"
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(tool_service.tempfile, 'gettempdir', return_value=directory), \
             patch.object(tool_service, 'get_catia_v5_application', return_value=SimpleNamespace(ActiveDocument=document)), \
             patch.object(tool_service, 'ModelingContext', return_value=context), \
             patch.object(modeling, 'get_active_part', return_value=SimpleNamespace(name='A')), \
             patch.object(modeling, 'list_features', return_value=['凸台.1']), \
             patch.object(modeling, 'get_mass_props', return_value=None):
            result = json.loads(tool_service.tool_run_modeling_script(script))
            self.assertTrue(result['success'])
            self.assertEqual(result['steps'][0]['step'], 'fixture')
            self.assertEqual(result['verification']['status'], 'not_requested')
            self.assertEqual(Path(result['script_path']).read_text(encoding='utf-8'), script)
            recorded = json.loads(Path(result['run_record_path']).read_text(encoding='utf-8'))
            self.assertEqual(recorded, result)

    def test_backend_balances_com_and_restores_interaction_after_failure(self):
        old = backend.file_interaction.overwrite_handler
        def fail(**kwargs):
            self.assertEqual(kwargs['progress_signal'].__class__, backend.Progress)
            self.assertEqual(backend.file_interaction.overwrite_handler(Path('x')), 'skip_all')
            raise RuntimeError('fixture')
        with patch.dict(backend.tools_map, {'fixture': fail}), \
             patch.object(backend.pythoncom, 'CoInitialize') as initialize, \
             patch.object(backend.pythoncom, 'CoUninitialize') as uninitialize:
            with self.assertRaises(RuntimeError):
                backend.execute('fixture', {'overwrite': 'skip'})
            initialize.assert_called_once()
            uninitialize.assert_called_once()
        self.assertIs(backend.file_interaction.overwrite_handler, old)
