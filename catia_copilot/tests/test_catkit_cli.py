"""CLI 参数和工具分发测试，不运行 CATIA。"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TestCli(unittest.TestCase):
    def invoke(self, args, payload=None):
        from catia_copilot.cli.app import main
        calls = []
        def execute(name, parameters):
            calls.append((name, parameters))
            return json.dumps(payload if payload is not None else {'success': True})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(args, executor=execute)
        return code, out.getvalue(), calls

    def test_default_arguments_are_left_to_service(self):
        code, out, calls = self.invoke(['bom', 'collect'])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [('collect_bom', {})])
        self.assertEqual(json.loads(out), {'success': True})

    def test_status_diagnose(self):
        self.assertEqual(self.invoke(['status', '--diagnose'])[2], [('diagnose_catia_connection', {})])

    def test_explicit_document_and_mutually_exclusive_active(self):
        self.assertEqual(self.invoke(['document', 'info', '--doc', 'B'])[2], [('document_info', {'doc': 'B'})])
        code, _, calls = self.invoke(['document', 'info', '--doc', 'B', '--active'])
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_product_export_files_and_target_conflict(self):
        self.assertEqual(self.invoke(['product', 'export', 'step', '--files', '["C:/a.CATProduct"]'])[2],
                         [('export_product_step', {'file_paths': ['C:/a.CATProduct']})])
        for options in (['--node', '0.0'], ['--doc', 'A', '--files', '[]']):
            code, _, calls = self.invoke(['product', 'export', 'step', *options])
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

    def test_partial_inspection_is_not_success_exit(self):
        self.assertEqual(self.invoke(['part', 'tree'], {'complete': False, 'errors': [{'error': 'unreadable'}]})[0], 1)

    def test_duplicate_file_aliases_are_rejected(self):
        code, _, calls = self.invoke(['product', 'export', 'step', '--files', '["a.CATProduct"]', '--file-paths', '["b.CATProduct"]'])
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_dialog_response_dispatches_to_original_task(self):
        _, _, calls = self.invoke(['dialog', 'respond', '--task', 'abc', '--button', '42'])
        self.assertEqual(calls[0][0], '_dialog_respond')
        self.assertEqual(calls[0][1]['task'], 'abc')
        self.assertEqual(calls[0][1]['button'], '42')

    def test_task_status_and_wait(self):
        self.assertEqual(self.invoke(['task', 'status', '--task', 'abc'])[2][0][0], '_task_status')
        _, _, calls = self.invoke(['task', 'wait', '--task', 'abc', '--timeout', '5'])
        self.assertEqual(calls[0][1]['timeout'], 5)

    def test_waiting_for_dialog_is_a_nonterminal_success(self):
        payload = {'status': 'waiting_for_dialog', 'task_id': 'abc', 'dialog': {'text': 'Continue?'}}
        code, out, _ = self.invoke(['document', 'save'], payload)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), payload)

    def test_json_and_boolean_options(self):
        calls = self.invoke(['bom', 'export', '--output-file', 'D:/bom.csv', '--view', 'summary',
                             '--columns', '["Part Number","Quantity"]', '--no-include-assemblies'])[2]
        self.assertEqual(calls[0][1], {'output_file': 'D:/bom.csv', 'view': 'summary',
                                     'columns': ['Part Number', 'Quantity'], 'include_assemblies': False})

    def test_bom_uses_existing_document_selection(self):
        code, _, calls = self.invoke(['bom', 'collect', '--doc', 'Assembly', '--view', 'full'])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [('collect_bom', {'doc': 'Assembly', 'view': 'full'})])
        self.assertEqual(self.invoke(['bom', 'collect', '--doc', 'A', '--active'])[0], 2)

    def test_bom_rejects_removed_path_and_summary_options(self):
        for args in (['bom', 'collect', '--file-path', 'a.CATProduct'],
                     ['bom', 'collect', '--summarize'],
                     ['bom', 'export', '--output-file', 'a.csv', '--file-paths', '[null]']):
            code, _, calls = self.invoke(args)
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

    def test_bom_edit_changes_file_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            changes = [{'node': '0.0', 'scope': 'master', 'column': 'Revision', 'value': 'B'}]
            p = Path(temp) / 'changes.json'
            p.write_text(json.dumps(changes), encoding='utf-8')
            code, _, calls = self.invoke(['bom', 'edit', '--changes', '@' + str(p)],
                                         {'success': False, 'applied_count': 0, 'failed_count': 1})
            self.assertEqual(code, 1)
            self.assertEqual(calls, [('edit_bom', {'changes': changes})])

    def test_run_file_preserves_script_and_optional_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / '中文.py'
            p.write_text('def build(ctx):\n    pass\n', encoding='utf-8')
            calls = self.invoke(['run', '--file', str(p)])[2]
            self.assertEqual(calls, [('run_modeling_script', {'script': p.read_text(encoding='utf-8')})])

    def test_errors_preserve_payload(self):
        payload = {'error': 'failure'}
        code, out, _ = self.invoke(['status'], payload)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), payload)

    def test_disconnected_status_is_not_success(self):
        for status in ('disconnected', 'broken'):
            with self.subTest(status=status):
                code, out, _ = self.invoke(['status'], {'status': status})
                self.assertEqual(code, 3)
                self.assertEqual(json.loads(out), {'status': status})

    def test_schema_without_backend(self):
        code, out, calls = self.invoke(['schema', 'document', 'properties', 'set'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['function']['name'], 'set_document_properties')
        self.assertEqual(calls, [])

    def test_unknown_schema_path_returns_usage_error(self):
        code, out, calls = self.invoke(['schema', 'bogus'])
        self.assertEqual(code, 2)
        self.assertIn('error', json.loads(out))
        self.assertEqual(calls, [])

    def test_diagnostic_schema_is_discoverable(self):
        code, out, calls = self.invoke(['schema', 'status', '--diagnose'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['function']['name'], 'diagnose_catia_connection')
        self.assertEqual(calls, [])

    def test_help_does_not_load_backend(self):
        proc = subprocess.run([sys.executable, '-X', 'utf8', '-c',
            "import sys; from catia_copilot.cli.app import main; main(['--help']); assert 'catia_copilot.catia.tool_service' not in sys.modules"], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('CATIA', proc.stdout)

    def test_all_21_tools_have_commands(self):
        from catia_copilot.cli.app import COMMANDS
        from catia_copilot.catia.tool_schema import tools_schema
        self.assertEqual(set(COMMANDS.values()) | {'diagnose_catia_connection'}, {s['function']['name'] for s in tools_schema})

    def test_every_tool_can_be_dispatched_with_required_inputs(self):
        from catia_copilot.cli.app import COMMANDS, SCHEMAS
        for path, tool in COMMANDS.items():
            with self.subTest(tool=tool):
                args = list(path)
                params = SCHEMAS[tool]['function']['parameters']
                for key in params.get('required', []):
                    field = params['properties'][key]
                    value = '[]' if field.get('type') == 'array' else '{}' if field.get('type') == 'object' else 'example'
                    args += ['--'+key.replace('_', '-'), value]
                code, _, calls = self.invoke(args)
                self.assertEqual(code, 0)
                self.assertEqual(calls[0][0], tool)

    def test_json_file_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / '参数.json'
            p.write_text('{"Part Number":"P1"}', encoding='utf-8')
            calls = self.invoke(['document', 'properties', 'set', '--standard', '@'+str(p)])[2]
            self.assertEqual(calls[0][1], {'standard': {'Part Number': 'P1'}})
