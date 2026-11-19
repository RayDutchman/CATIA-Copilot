import os
from pathlib import Path
import subprocess
import sys
import unittest


class CliMetadataImportTests(unittest.TestCase):
    def _run_cli(self, *arguments):
        root = Path(__file__).resolve().parents[2]
        script = r'''
import importlib.abc
import runpy
import sys

blocked = ('pywinauto', 'comtypes', 'pythoncom', 'win32com')
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('blocked optional COM dependency: ' + fullname)
sys.meta_path.insert(0, BlockImports())
sys.argv = ['cat5cli'] + sys.argv[1:]
runpy.run_module('catia_copilot.cli', run_name='__main__')
'''
        env = os.environ.copy()
        env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
        return subprocess.run(
            [sys.executable, '-c', script, *arguments], cwd=root, env=env,
            capture_output=True, text=True, timeout=30,
        )

    def test_help_version_and_schema_do_not_import_com_dependencies(self):
        for arguments in (('--help',), ('--version',), ('schema',)):
            with self.subTest(arguments=arguments):
                result = self._run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('blocked optional COM dependency', result.stderr)
                self.assertTrue(result.stdout.strip())

    def test_business_command_still_loads_tasks(self):
        result = self._run_cli('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('blocked optional COM dependency', result.stdout)


if __name__ == '__main__':
    unittest.main()
