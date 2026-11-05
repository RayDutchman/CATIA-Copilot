"""弹窗接续使用原任务，不重启业务执行。"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from catia_copilot.cli.tasks import TaskManager, run_worker, write_json


class TestTasks(unittest.TestCase):
    def test_task_root_environment_precedence(self):
        from unittest.mock import patch
        from catia_copilot.cli.tasks import task_root
        with patch.dict(os.environ, {'CAT5CLI_TASKS_DIR': 'D:/explicit/tasks',
                                     'CAT5CLI_HOME': 'D:/home',
                                     'LOCALAPPDATA': 'D:/local'}, clear=False):
            self.assertEqual(task_root(), Path('D:/explicit/tasks'))
        with patch.dict(os.environ, {'CAT5CLI_HOME': 'D:/home',
                                     'LOCALAPPDATA': 'D:/local'}, clear=False):
            os.environ.pop('CAT5CLI_TASKS_DIR', None)
            self.assertEqual(task_root(), Path('D:/home/tasks'))
        with patch.dict(os.environ, {'LOCALAPPDATA': 'D:/local'}, clear=False):
            os.environ.pop('CAT5CLI_TASKS_DIR', None)
            os.environ.pop('CAT5CLI_HOME', None)
            self.assertEqual(task_root(), Path('D:/local/Cat5cli/tasks'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.launches = []
        self.dialog = {'id': '42', 'hwnd': 42, 'process_id': 99, 'fingerprint': 'first',
                       'text': 'Continue?', 'buttons': [{'id': '43', 'text': 'No', 'enabled': True}]}
        self.visible = [self.dialog]
        def launch(task_id, root):
            self.launches.append(task_id)
            return {'pid': 123, 'created': 'fixture'}
        self.manager = TaskManager(root=Path(self.temp.name), launcher=launch,
            scanner=lambda pids: self.visible, responder=self.respond,
            pid_provider=lambda: [99], alive=lambda meta: True, poll_interval=0.001)

    def respond(self, dialog, **kwargs):
        self.assertEqual(dialog, self.dialog)
        self.assertEqual(kwargs['button'], '43')
        self.visible = []
        write_json(self.manager.root / self.launches[0] / 'result.json',
                   {'result': {'success': True, 'executed': 1}})

    def test_dialog_response_collects_original_result_without_relaunch(self):
        blocked = self.manager.start('fixture', {'value': 1}, wait_seconds=0.01)
        self.assertEqual(blocked['status'], 'waiting_for_dialog')
        task = blocked['task_id']
        self.assertEqual(self.manager.respond(task, button='43'), {'success': True, 'executed': 1})
        self.assertEqual(self.manager.wait(task), {'success': True, 'executed': 1})
        self.assertEqual(self.launches, [task])
        request = json.loads((self.manager.root / task / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['parameters'], {'value': 1})

    def test_long_operation_returns_running_and_is_not_cancelled(self):
        self.visible = []
        result = self.manager.start('fixture', {}, wait_seconds=0)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(len(self.launches), 1)

    def test_worker_loss_is_reported_without_retry(self):
        self.visible = []
        self.manager.alive = lambda meta: False
        result = self.manager.start('fixture', {}, wait_seconds=0)
        self.assertEqual(result['status'], 'worker_lost')
        self.assertIn('error', result)
        self.assertEqual(len(self.launches), 1)

    def test_completed_result_wins_over_unrelated_dialog(self):
        blocked = self.manager.start('fixture', {})
        write_json(self.manager.root / blocked['task_id'] / 'result.json', {'result': {'rows': []}})
        self.assertEqual(self.manager.wait(blocked['task_id']), {'rows': []})

    def test_invalid_task_id_cannot_escape_task_directory(self):
        with self.assertRaises(ValueError):
            self.manager.status('../elsewhere')

    def test_result_written_during_liveness_check_is_not_worker_loss(self):
        self.visible = []
        def exits(meta):
            write_json(self.manager.root / self.launches[0] / 'result.json', {'result': {'success': True}})
            return False
        self.manager.alive = exits
        self.assertEqual(self.manager.start('fixture', {}), {'success': True})

    def test_multiple_catia_processes_are_not_guessed(self):
        self.manager.pid_provider = lambda: [99, 100]
        result = self.manager.start('fixture', {}, wait_seconds=0)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['dialog_monitor'], 'multiple_catia_instances')

    def test_transient_scan_failure_does_not_lose_running_task(self):
        scans = []
        def scan(pids):
            scans.append(True)
            if len(scans) == 1:
                raise RuntimeError('Handle 123 is not a vaild window handle')
            return [self.dialog]
        self.manager.scanner = scan
        result = self.manager.start('fixture', {}, wait_seconds=0.1)
        self.assertEqual(result['status'], 'waiting_for_dialog')
        self.assertEqual(result['task_id'], self.launches[0])
        self.assertEqual(len(self.launches), 1)

    def test_scan_failure_returns_task_id_at_wait_timeout(self):
        def scan(pids):
            raise RuntimeError('scan unavailable')
        self.manager.scanner = scan
        result = self.manager.start('fixture', {}, wait_seconds=0)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['task_id'], self.launches[0])
        self.assertEqual(result['dialog_monitor'], 'scan_error')
        self.assertNotIn('error', result)

    def test_second_command_does_not_compete_for_same_dialog(self):
        first = self.manager.start('first', {})
        second = self.manager.start('second', {})
        self.assertEqual(second['status'], 'busy')
        self.assertFalse(second['executed'])
        self.assertEqual(second['task_id'], first['task_id'])
        self.assertEqual(len(self.launches), 1)

    def test_scheduler_pending_remains_busy_and_running_not_terminal(self):
        self.manager.launcher = lambda task_id, root: {'scheduler_pending': True, 'scheduler': 'Cat5cli-test'}
        from unittest.mock import patch
        with patch('catia_copilot.cli.scheduler.task_state', return_value=4):
            result = self.manager.start('first', {}, wait_seconds=0)
            self.assertEqual(result['status'], 'running')
            self.assertEqual(self.manager.status(result['task_id'])['status'], 'running')
            busy = self.manager.start('second', {}, wait_seconds=0)
        self.assertEqual(busy['status'], 'busy')
        self.assertEqual(busy['task_id'], result['task_id'])

    def test_ready_scheduler_with_failed_run_result_is_terminal_and_releases_slot(self):
        self.manager.launcher = lambda task_id, root: {'scheduler_pending': True, 'scheduler': 'Cat5cli-' + task_id}
        from unittest.mock import patch
        with patch('catia_copilot.cli.scheduler.task_state', return_value={'state': 3, 'last_run_time': '2026-10-26', 'last_task_result': 1, 'instances': 0}):
            first = self.manager.start('first', {}, wait_seconds=0)
            self.assertEqual(first['status'], 'launch_failed')
            self.assertFalse((self.manager.root / 'active.json').exists())
            self.manager.launcher = lambda task_id, root: {'pid': 321, 'created': 'fixture'}
            self.visible = []
            second = self.manager.start('second', {}, wait_seconds=0)
        self.assertEqual(second['status'], 'running')
        self.assertNotEqual(second['task_id'], first['task_id'])

    def test_ready_scheduler_without_run_is_not_failure(self):
        self.manager.launcher = lambda task_id, root: {'scheduler_pending': True, 'scheduler': 'Cat5cli-' + task_id}
        from unittest.mock import patch
        with patch('catia_copilot.cli.scheduler.task_state', return_value={'state': 3, 'last_run_time': None, 'last_task_result': 0, 'instances': 0}):
            result = self.manager.start('first', {}, wait_seconds=0)
        self.assertEqual(result['status'], 'running')
        self.assertIn('task_id', result)

    def test_fast_scheduler_completion_race_prefers_worker_result(self):
        self.manager.launcher = lambda task_id, root: {'scheduler_pending': True, 'scheduler': 'Cat5cli-' + task_id}
        from unittest.mock import patch
        def info(_name):
            task_id = json.loads((self.manager.root / 'active.json').read_text(encoding='utf-8'))['task_id']
            write_json(self.manager.root / task_id / 'result.json', {'result': {'done': True}})
            return {'state': 3, 'last_run_time': '2026-10-26', 'last_task_result': 0, 'instances': 0}
        with patch('catia_copilot.cli.scheduler.task_state', side_effect=info):
            self.assertEqual(self.manager.start('first', {}, wait_seconds=0), {'done': True})

    def test_pending_scheduler_vanish_rereads_result_and_cleans_task(self):
        task_id = 'b' * 32
        directory = Path(self.temp.name) / task_id
        write_json(directory / 'request.json', {'tool': 'fixture', 'parameters': {}})
        write_json(directory / 'launch.json', {'scheduler_pending': True, 'scheduler': 'Cat5cli-' + task_id})
        def missing(_name):
            write_json(directory / 'result.json', {'result': {'ok': True}})
            return None
        from unittest.mock import patch
        with patch('catia_copilot.cli.scheduler.task_state', side_effect=missing):
            with patch('catia_copilot.cli.scheduler.delete_task') as cleanup:
                self.assertEqual(self.manager.status(task_id)['status'], 'completed')
                cleanup.assert_called_once_with('Cat5cli-' + task_id)

    def test_completed_status_cleanup_failure_does_not_replace_result(self):
        task_id = 'c' * 32
        directory = Path(self.temp.name) / task_id
        write_json(directory / 'request.json', {'tool': 'fixture', 'parameters': {}})
        write_json(directory / 'launch.json', {'scheduler': 'Cat5cli-' + task_id})
        write_json(directory / 'result.json', {'result': {'ok': True}})
        from unittest.mock import patch
        with patch('catia_copilot.cli.scheduler.delete_task', side_effect=OSError('no permission')):
            self.assertEqual(self.manager.status(task_id), {'status': 'completed', 'task_id': task_id,
                                                            'result': {'ok': True}})

    def test_duplicate_worker_claim_does_not_execute_twice(self):
        task_id = 'a' * 32
        directory = Path(self.temp.name) / task_id
        write_json(directory / 'request.json', {'tool': 'fixture', 'parameters': {}, 'cwd': os.getcwd()})
        calls = []
        operation = lambda tool, params: calls.append(tool) or {'ok': True}
        self.assertEqual(run_worker(task_id, operation=operation, root=self.temp.name), 0)
        self.assertEqual(run_worker(task_id, operation=operation, root=self.temp.name), 0)
        self.assertEqual(calls, ['fixture'])

    def test_response_is_sent_even_if_read_operation_finished(self):
        blocked = self.manager.start('read', {})
        write_json(self.manager.root / blocked['task_id'] / 'result.json', {'result': {'rows': []}})
        responded = []
        self.manager.responder = lambda dialog, **kw: responded.append(dialog['id'])
        self.assertEqual(self.manager.respond(blocked['task_id'], button='43'), {'rows': []})
        self.assertEqual(responded, ['42'])
        self.assertEqual(self.manager.respond(blocked['task_id'], button='43'), {'rows': []})
        self.assertEqual(responded, ['42'])
