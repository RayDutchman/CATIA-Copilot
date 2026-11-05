"""计划任务回退的状态语义。"""
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from catia_copilot.cli.scheduler import SchedulerLaunch, is_job_breakaway_denied


class TestScheduler(unittest.TestCase):
    def test_only_winerror_5_in_job_qualifies(self):
        denied = OSError('access denied')
        denied.winerror = 5
        self.assertTrue(is_job_breakaway_denied(denied, in_job=True))
        self.assertFalse(is_job_breakaway_denied(PermissionError(5, 'denied'), in_job=False))
        self.assertFalse(is_job_breakaway_denied(PermissionError(2, 'missing'), in_job=True))
        self.assertFalse(is_job_breakaway_denied(PermissionError(5, 'I/O'), in_job=True))

    def test_launch_result_marks_uncertainty_after_run(self):
        launch = SchedulerLaunch()
        with tempfile.TemporaryDirectory() as temp:
            from pathlib import Path
            (Path(temp) / 'id').mkdir()
            with patch.object(launch, '_register_and_run', return_value={'scheduler': 'Cat5cli-one'}):
                self.assertEqual(launch.start('id', [], cwd='.', root=temp, log_path='x')['scheduler'], 'Cat5cli-one')

    def test_compiled_worker_receives_root_and_log_arguments(self):
        launch = SchedulerLaunch()
        with tempfile.TemporaryDirectory() as temp:
            from pathlib import Path
            (Path(temp) / 'id').mkdir()
            with patch.object(launch, '_register_and_run', side_effect=lambda task, command, cwd: command):
                command = launch.start('id', ['cat5cli.exe', '_worker', 'id'], cwd='.', root=temp, log_path='R:/tasks/id/worker.log')
        self.assertEqual(command, ['cat5cli.exe', '_worker', 'id', temp, 'R:/tasks/id/worker.log'])

    def test_source_module_uses_importable_cwd_and_request_root(self):
        launch = SchedulerLaunch()
        with tempfile.TemporaryDirectory() as temp:
            from pathlib import Path
            (Path(temp) / 'id').mkdir()
            with patch.object(launch, '_register_and_run', side_effect=lambda task, command, cwd: (command, cwd)):
                command, cwd = launch.start('id', ['python.exe', '-m', 'catia_copilot.cli', '_worker', 'id'],
                                            cwd='external', root=temp, log_path='R:/tasks/id/worker.log',
                                            source_root='R:/source')
        self.assertEqual(cwd, 'R:/source')
        self.assertEqual(command[-2:], [temp, 'R:/tasks/id/worker.log'])

    def test_non_access_denied_launch_error_is_not_scheduled(self):
        from catia_copilot.cli import tasks
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'id').mkdir()
            with patch.object(tasks.subprocess, 'Popen', side_effect=OSError(2, 'not found')):
                with patch.object(tasks, 'process_in_job', return_value=True):
                    with patch.object(tasks, 'SchedulerLaunch') as scheduler:
                        with self.assertRaises(OSError):
                            tasks.spawn_worker('id', root, command=['missing.exe'])
                        scheduler.assert_not_called()
