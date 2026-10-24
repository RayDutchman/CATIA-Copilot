"""迁移前后工具契约的一致性，不调用 CATIA。"""

import ast
import hashlib
import json
from pathlib import Path
import unittest
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {'tool_read_file', 'tool_write_file', 'tool_list_directory', 'tool_update_memory'}


class TestMigration(unittest.TestCase):
    def test_installer_version_matches_application(self):
        from catia_copilot.constants import APP_VERSION
        package = json.loads((ROOT / 'packages/catkit/package.json').read_text(encoding='utf-8'))
        self.assertEqual(package['version'], APP_VERSION)

    def test_tool_service_import_does_not_load_qt(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from catia_copilot.catia.tool_service import tools_map; assert len(tools_map)==21; assert not any(n.startswith('PySide6') for n in sys.modules)"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_business_constants_do_not_load_qt(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from catia_copilot import constants_data; assert not any(n.startswith('PySide6') for n in sys.modules)"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gui_constants_reexport_same_data(self):
        from catia_copilot import constants, constants_data
        for name in ('BomNodeType', 'BOM_DEFAULT_COLUMNS', 'PRESET_USER_REF_PROPERTIES', 'PRODUCT_ATTR_READ_MAP'):
            self.assertIs(getattr(constants, name), getattr(constants_data, name))

    def test_tool_functions_preserve_implementation(self):
        baseline = json.loads((ROOT / 'catia_copilot/tests/fixtures/catkit_legacy_contract.json').read_text(encoding='utf-8'))
        new = ast.parse((ROOT / 'catia_copilot/catia/tool_service.py').read_text(encoding='utf-8'))
        originals = baseline['function_ast_sha256']
        migrated = {n.name: n for n in new.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(len(originals), 21)
        for name, node in originals.items():
            with self.subTest(tool=name):
                self.assertEqual(node, hashlib.sha256(ast.dump(migrated[name]).encode('utf-8')).hexdigest())

    def test_schema_has_exactly_21_tools(self):
        from catia_copilot.catia.tool_schema import tools_schema
        names = [s['function']['name'] for s in tools_schema]
        self.assertEqual(len(names), 21)
        self.assertEqual(len(set(names)), 21)
        self.assertIn('run_modeling_script', names)
        self.assertNotIn('update_memory', names)
