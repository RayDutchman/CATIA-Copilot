"""目标机类型库生成回归，不修改系统类型库或构建机全局缓存。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TestComtypesRuntime(unittest.TestCase):
    def test_cache_lock_matches_windows_extended_path_alias(self):
        from catia_copilot.cli.comtypes_runtime import _cache_lock_name
        with tempfile.TemporaryDirectory(prefix='cat5cli-lock-') as directory:
            ordinary = Path(directory).resolve()
            extended = Path('\\\\?\\' + str(ordinary))
            self.assertEqual(_cache_lock_name(ordinary), _cache_lock_name(extended))

    def test_real_typelib_timestamp_change_regenerates_local_wrapper(self):
        setup = '''
import json
import os
from pathlib import Path
import shutil
import comtypes.gen
from catia_copilot.cli.comtypes_runtime import cache_directory
cache = cache_directory()
cache.mkdir(parents=True, exist_ok=True)
comtypes.gen.__path__ = [str(cache)]
import comtypes.client
comtypes.client.gen_dir = str(cache)
copy = Path(os.environ['CAT5CLI_HOME']) / 'copied-stdole2.tlb'
shutil.copy2(Path(os.environ['WINDIR']) / 'System32/stdole2.tlb', copy)
comtypes.client.GetModule(str(copy))
stat = copy.stat()
os.utime(copy, (stat.st_atime, stat.st_mtime + 120))
'''
        probe = '''
from pathlib import Path
from catia_copilot.cli.comtypes_runtime import prepare_com_runtime, cache_directory
prepare_com_runtime()
import comtypes.client
module = comtypes.client.GetModule('stdole2.tlb')
wrapper = cache_directory() / '_00020430_0000_0000_C000_000000000046_0_2_0.py'
assert wrapper.exists()
assert 'copied-stdole2.tlb' not in wrapper.read_text(encoding='utf-8')
print('regenerated after real typelib mtime change')
'''
        with tempfile.TemporaryDirectory(prefix='cat5cli-typelib-change-') as home:
            for script in (setup, probe):
                result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], capture_output=True,
                                        encoding='utf-8', env={**os.environ, 'CAT5CLI_HOME': home}, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_target_cache_is_used_and_stale_wrapper_is_regenerated(self):
        script = '''
import json
import os
from pathlib import Path
import comtypes
from catia_copilot.cli.comtypes_runtime import cache_directory, prepare_com_runtime
cache = cache_directory()
cache.mkdir(parents=True, exist_ok=True)
wrapper = cache / '_00020430_0000_0000_C000_000000000046_0_2_0.py'
wrapper.write_text("raise ImportError('Typelib different than module')\\n", encoding='utf-8')
prepare_com_runtime()
import comtypes.client
module = comtypes.client.GetModule('stdole2.tlb')
import comtypes.gen
paths = [m.__file__ for name, m in __import__('sys').modules.items()
         if name.startswith('comtypes.gen.') and getattr(m, '__file__', None)]
assert paths and all(Path(p).is_relative_to(cache) for p in paths), paths
assert 'raise ImportError' not in wrapper.read_text(encoding='utf-8')
print(json.dumps({'cache': str(cache), 'generated': len(paths)}))
'''
        with tempfile.TemporaryDirectory(prefix='cat5cli cache 中文 ') as home:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], capture_output=True,
                                    encoding='utf-8', env={**os.environ, 'CAT5CLI_HOME': home}, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(Path(data['cache']).is_relative_to(Path(home).resolve()))
            self.assertGreater(data['generated'], 0)

    def test_two_processes_share_clean_cache_without_partial_modules(self):
        script = 'from catia_copilot.cli.comtypes_runtime import prepare_com_runtime; prepare_com_runtime(); print("ready")'
        with tempfile.TemporaryDirectory(prefix='cat5cli-cache-race-') as home:
            processes = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script], stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, encoding='utf-8', env={**os.environ, 'CAT5CLI_HOME': home})
                         for _ in range(2)]
            outputs = [process.communicate(timeout=60) for process in processes]
            for process, (out, err) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, err)
                self.assertIn('ready', out)

    def test_build_excludes_machine_generated_wrappers(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, str(root / 'scripts/build_cat5cli.py'), '--dry-run'],
                                capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertNotIn('--include-package=comtypes.gen', args)
        self.assertIn('--include-module=comtypes.gen', args)
        self.assertTrue(any('comtypes.gen.*' in arg and arg.startswith('--nofollow-import-to=') for arg in args))
        self.assertIn('--include-module=comtypes._tlib_version_checker', args)
