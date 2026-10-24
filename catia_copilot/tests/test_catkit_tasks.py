"""弹窗接续使用原任务，不重启业务执行。"""
import json
from pathlib import Path
import tempfile
import unittest

from catia_copilot.cli.tasks import TaskManager, write_json


class TestTasks(unittest.TestCase):
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

    def test_response_is_sent_even_if_read_operation_finished(self):
        blocked = self.manager.start('read', {})
        write_json(self.manager.root / blocked['task_id'] / 'result.json', {'result': {'rows': []}})
        responded = []
        self.manager.responder = lambda dialog, **kw: responded.append(dialog['id'])
        self.assertEqual(self.manager.respond(blocked['task_id'], button='43'), {'rows': []})
        self.assertEqual(responded, ['42'])
        self.assertEqual(self.manager.respond(blocked['task_id'], button='43'), {'rows': []})
        self.assertEqual(responded, ['42'])
