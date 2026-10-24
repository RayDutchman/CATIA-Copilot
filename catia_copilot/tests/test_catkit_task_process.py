"""验证前台退出后 worker 存活，以及另一进程响应后取回同一结果。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from catia_copilot.cli.tasks import read_json, worker_alive


@unittest.skipUnless(sys.platform == 'win32', '需要 Windows 标准对话框')
class TestTaskProcess(unittest.TestCase):
    def test_dialog_survives_frontend_exit_and_is_resumed_once(self):
        driver = Path(__file__).parent / 'fixtures/dialog_task_driver.py'
        with tempfile.TemporaryDirectory(prefix='catkit-dialog-') as directory:
            root = Path(directory)
            def run(*args):
                result = subprocess.run([sys.executable, str(driver), args[0], directory, *args[1:]],
                                        capture_output=True, encoding='utf-8', timeout=25)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            blocked = run('start')
            self.assertEqual(blocked['status'], 'waiting_for_dialog', blocked)
            task_id = blocked['task_id']
            worker = read_json(root / task_id / 'worker.json')
            self.assertTrue(worker_alive(worker), '前台命令结束后 worker 不应退出')
            buttons = blocked['dialog']['buttons']
            self.assertEqual(len(buttons), 2)
            result = run('respond', task_id, buttons[-1]['id'])
            self.assertTrue(result.get('success'), result)
            self.assertEqual(result['calls'], 1)
            self.assertEqual(result, run('wait', task_id))
            self.assertEqual((root / 'calls.txt').read_text(), '1')
