"""迁移前后工具契约的一致性，不调用 CATIA。"""

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {'tool_read_file', 'tool_write_file', 'tool_list_directory', 'tool_update_memory'}
REPLACED_BOM_TOOLS = {'tool_collect_bom', 'tool_export_bom_to_excel'}
REVISED_TOOL_LOGIC = {'tool_set_document_properties'}


def restore_approved_messages(node, edits):
    """仅还原白名单中指定 AST 位置的文案，其他字面量及逻辑仍精确校验。"""
    node = copy.deepcopy(node)
    for edit in edits:
        target = node
        for key in edit['path'][:-1]:
            target = target[key] if isinstance(key, int) else getattr(target, key)
        assert isinstance(target, ast.Constant) and edit['path'][-1] == 'value'
        assert isinstance(target.value, str) and target.value == edit['english']
        target.value = edit['original']
    return node


class TestMigration(unittest.TestCase):
    def test_message_translation_allowlist_does_not_mask_logic_or_data_changes(self):
        original = ast.parse("def f():\n    return {'error': '文档不存在', 'key': '用户字段'}\n").body[0]
        translated = ast.parse("def f():\n    return {'error': 'Document not found', 'key': '用户字段'}\n").body[0]
        edits = [{'path': ['body', 0, 'value', 'values', 0, 'value'],
                  'original': '文档不存在', 'english': 'Document not found'}]
        restored = restore_approved_messages(translated, edits)
        self.assertEqual(ast.dump(restored), ast.dump(original))
        changed = ast.parse("def f():\n    return {'error': 'Document not found', 'key': 'changed'}\n").body[0]
        self.assertNotEqual(ast.dump(restore_approved_messages(changed, edits)), ast.dump(original))
        with self.assertRaises(AssertionError):
            restore_approved_messages(original, edits)

    def test_installer_version_matches_application(self):
        from catia_copilot.constants import APP_VERSION
        package = json.loads((ROOT / 'packages/catkit/package.json').read_text(encoding='utf-8'))
        self.assertEqual(package['version'], APP_VERSION)

    def test_tool_service_import_does_not_load_qt(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from catia_copilot.catia.tool_service import tools_map; assert len(tools_map)>=21; assert not any(n.startswith('PySide6') for n in sys.modules)"], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_business_constants_do_not_load_qt(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from catia_copilot import constants_data; assert not any(n.startswith('PySide6') for n in sys.modules)"], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gui_constants_reexport_same_data(self):
        from catia_copilot import constants, constants_data
        for name in ('BomNodeType', 'BOM_DEFAULT_COLUMNS', 'PRESET_USER_REF_PROPERTIES', 'PRODUCT_ATTR_READ_MAP'):
            self.assertIs(getattr(constants, name), getattr(constants_data, name))

    def test_tool_functions_preserve_implementation(self):
        baseline = json.loads((ROOT / 'catia_copilot/tests/fixtures/catkit_legacy_contract.json').read_text(encoding='utf-8'))
        new = ast.parse((ROOT / 'catia_copilot/catia/tool_service.py').read_text(encoding='utf-8'))
        originals = baseline['function_ast_sha256']
        translations = json.loads((ROOT / 'catia_copilot/tests/fixtures/catkit_english_messages.json').read_text(encoding='utf-8'))
        migrated = {n.name: n for n in new.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(len(originals), 21)
        self.assertEqual(len(set(originals) - REPLACED_BOM_TOOLS), 19)
        self.assertEqual(len(set(originals) - REPLACED_BOM_TOOLS - REVISED_TOOL_LOGIC), 18)
        self.assertTrue(REPLACED_BOM_TOOLS.isdisjoint(migrated))
        for name, node in originals.items():
            if name in REPLACED_BOM_TOOLS:
                # 用户明确调整了 BOM 契约；新行为由 BOM 回归覆盖，历史指纹不改。
                continue
            if name in REVISED_TOOL_LOGIC:
                # 用户明确取消公司预设限制，由自定义属性行为测试覆盖，历史指纹不改。
                continue
            with self.subTest(tool=name):
                restored = restore_approved_messages(migrated[name], translations.get(name, []))
                self.assertEqual(node, hashlib.sha256(ast.dump(restored).encode('utf-8')).hexdigest())

    def test_schema_preserves_legacy_tools_without_duplicate_names(self):
        from catia_copilot.catia.tool_schema import tools_schema
        names = [s['function']['name'] for s in tools_schema]
        baseline = json.loads((ROOT / 'catia_copilot/tests/fixtures/catkit_legacy_contract.json').read_text(encoding='utf-8'))
        legacy = {name.removeprefix('tool_') for name in baseline['function_ast_sha256']}
        self.assertTrue(legacy <= set(names))
        self.assertEqual(len(set(names)), len(names))
        self.assertIn('run_modeling_script', names)
        self.assertNotIn('update_memory', names)
