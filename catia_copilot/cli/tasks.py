"""前台等待与独立操作进程；弹窗响应接续原任务。"""
import json
import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid
from catia_copilot.cli.scheduler import (SchedulerLaunch, SchedulerUncertain,
                                         is_job_breakaway_denied, process_in_job)

import win32api
import win32event
import win32process
import pywintypes

from catia_copilot.catia.dialogs import catia_process_ids, scan_dialogs, respond as respond_dialog
from catia_copilot.cli.backend import execute as execute_operation


def task_root():
    return Path(os.environ.get('CATKIT_TASKS_DIR') or
                str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Catkit' / 'tasks'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding='utf-8')
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def process_identity(pid):
    handle = win32api.OpenProcess(0x1000 | 0x100000, False, pid)
    try:
        return {'pid': pid, 'created': str(win32process.GetProcessTimes(handle)['CreationTime'])}
    finally:
        handle.Close()


def worker_alive(identity):
    try:
        handle = win32api.OpenProcess(0x1000 | 0x100000, False, identity['pid'])
        try:
            return (str(win32process.GetProcessTimes(handle)['CreationTime']) == identity['created']
                    and win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT)
        finally:
            handle.Close()
    except (OSError, pywintypes.error, KeyError):
        return False


def spawn_worker(task_id, root, *, command=None):
    env = dict(os.environ, CATKIT_TASKS_DIR=str(root))
    if command is not None:
        command = list(command)
    elif '__compiled__' in globals() or getattr(sys, 'frozen', False):
        command = [sys.executable, '_worker', task_id]
    else:
        command = [sys.executable, '-m', 'catia_copilot.cli', '_worker', task_id]
        source_root = str(Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = source_root + os.pathsep + env.get('PYTHONPATH', '')
    directory = root / task_id
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with (directory / 'worker.log').open('ab', buffering=0) as output:
        options = dict(stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                       close_fds=True, env=env, cwd=os.getcwd())
        try:
            process = subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
        except OSError as exc:
            if not is_job_breakaway_denied(exc, in_job=process_in_job()):
                raise
            if command[1:3] == ['-m', 'catia_copilot.cli']:
                command = [command[0], '-m', 'catia_copilot.cli', '_worker', task_id]
            return SchedulerLaunch().start(task_id, command, cwd=options['cwd'], root=root,
                                           log_path=directory / 'worker.log',
                                           source_root=str(Path(__file__).resolve().parents[2]))
    identity = process_identity(process.pid)
    identity['breakaway'] = True
    return identity


@contextmanager
def task_guard(root):
    """仅保护短暂的任务认领/响应操作，不在 COM 调用期间持锁。"""
    key = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
    mutex = win32event.CreateMutex(None, False, 'Local\\CatkitTasks-' + key)
    acquired = win32event.WaitForSingleObject(mutex, 5000)
    if acquired not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
        mutex.Close()
        raise RuntimeError('任务状态正在被另一进程更新，请稍后重试')
    try:
        yield
    finally:
        win32event.ReleaseMutex(mutex)
        mutex.Close()


class TaskManager:
    def __init__(self, root=None, *, launcher=spawn_worker, scanner=scan_dialogs,
                 responder=respond_dialog, pid_provider=catia_process_ids,
                 alive=worker_alive, poll_interval=0.25):
        self.root = Path(root) if root is not None else task_root()
        self.launcher, self.scanner, self.responder = launcher, scanner, responder
        self.pid_provider, self.alive = pid_provider, alive
        self.poll_interval = poll_interval

    def directory(self, task_id):
        if not re.fullmatch(r'[0-9a-f]{32}', task_id):
            raise ValueError('无效 task_id')
        directory = self.root / task_id
        if not (directory / 'request.json').is_file():
            raise ValueError('任务不存在：' + task_id)
        return directory

    def _cleanup_scheduler(self, task_id, directory):
        launch = read_json(directory / 'launch.json') or {}
        name = launch.get('scheduler')
        if name != 'Catkit-' + task_id:
            return
        try:
            from catia_copilot.cli.scheduler import delete_task
            delete_task(name)
        except Exception:
            # 清理失败不覆盖已经持久化的业务结果/worker 状态。
            return

    def start(self, tool, parameters, *, wait_seconds=30):
        with task_guard(self.root):
            active = read_json(self.root / 'active.json')
            if active:
                previous = self.directory(active['task_id'])
                terminal = read_json(previous / 'result.json')
                if terminal is None:
                    state = self._state(active['task_id'])
                    if state['status'] in ('launch_failed', 'worker_lost', 'completed'):
                        (self.root / 'active.json').unlink(missing_ok=True)
                        active = None
                if active is None:
                    pass
                else:
                    identity = read_json(previous / 'worker.json')
                    if terminal is None and (identity is None or identity.get('scheduler_pending') or self.alive(identity)):
                        return {'status': 'busy', 'task_id': active['task_id'], 'executed': False,
                                'requested_tool': tool,
                                'next': 'catkit task status --task ' + active['task_id']}
            task_id = uuid.uuid4().hex
            directory = self.root / task_id
            write_json(directory / 'request.json', {'tool': tool, 'parameters': parameters,
                       'cwd': os.getcwd(), 'created_at': time.time()})
            pids = self.pid_provider()
            if len(pids) == 1:
                write_json(directory / 'catia.json', {'pids': pids})
            write_json(self.root / 'active.json', {'task_id': task_id})
            try:
                identity = self.launcher(task_id, self.root)
                if identity.get('scheduler_pending'):
                    write_json(directory / 'launch.json', identity)
                else:
                    write_json(directory / 'worker.json', identity)
            except Exception as exc:
                if isinstance(exc, SchedulerUncertain):
                    write_json(directory / 'launch.json', {'scheduler_pending': True,
                                                          'scheduler': str(exc).split(':', 1)[0]})
                else:
                    write_json(directory / 'result.json', {'result': {'error': str(exc), 'task_id': task_id}})
        return self.wait(task_id, wait_seconds=wait_seconds)

    def _state(self, task_id):
        directory = self.directory(task_id)
        terminal = read_json(directory / 'result.json')
        if terminal is not None:
            self._cleanup_scheduler(task_id, directory)
            return {'status': 'completed', 'task_id': task_id, 'result': terminal['result']}
        identity = read_json(directory / 'worker.json')
        launch = read_json(directory / 'launch.json') or {}
        if launch.get('scheduler_pending') and identity is None:
            try:
                from catia_copilot.cli.scheduler import task_state
                state = task_state(launch.get('scheduler')) if launch.get('scheduler') else None
            except Exception as exc:
                return {'status': 'running', 'task_id': task_id, 'scheduler': 'query_error',
                        'monitor_message': str(exc), 'log_path': str(directory / 'worker.log')}
            if state is None:
                # Run 与 worker 完成/删除任务并发时，重新核对握手及业务结果。
                terminal = read_json(directory / 'result.json')
                identity = read_json(directory / 'worker.json')
                if terminal is not None:
                    self._cleanup_scheduler(task_id, directory)
                    return {'status': 'completed', 'task_id': task_id, 'result': terminal['result']}
                if identity is not None:
                    return self._state(task_id)
                self._cleanup_scheduler(task_id, directory)
                return {'status': 'worker_lost', 'task_id': task_id,
                        'error': '计划任务不存在且无 worker 握手/结果；不会重复派发'}
            if (isinstance(state, dict) and state.get('state') == 3 and
                    state.get('last_run_time') is not None and not state.get('instances') and
                    state.get('last_task_result', 0) != 0):
                # 只有调度器确认曾运行且实例已结束、结果失败时才判定启动失败。
                if read_json(directory / 'worker.json') is None and read_json(directory / 'result.json') is None:
                    self._cleanup_scheduler(task_id, directory)
                    active = read_json(self.root / 'active.json') or {}
                    if active.get('task_id') == task_id:
                        (self.root / 'active.json').unlink(missing_ok=True)
                    return {'status': 'launch_failed', 'task_id': task_id,
                            'error': '计划任务已尝试运行但失败；不会重复派发'}
            terminal = read_json(directory / 'result.json')
            identity = read_json(directory / 'worker.json')
            if terminal is not None:
                self._cleanup_scheduler(task_id, directory)
                return {'status': 'completed', 'task_id': task_id, 'result': terminal['result']}
            if identity is not None:
                return self._state(task_id)
            return {'status': 'running', 'task_id': task_id, 'scheduler_state': state,
                    'log_path': str(directory / 'worker.log')}
        if identity is not None and identity.get('scheduler_pending'):
            return {'status': 'running', 'task_id': task_id, 'log_path': str(directory / 'worker.log')}
        if identity is not None and not self.alive(identity):
            terminal = read_json(directory / 'result.json')
            if terminal is not None:
                self._cleanup_scheduler(task_id, directory)
                return {'status': 'completed', 'task_id': task_id, 'result': terminal['result']}
            self._cleanup_scheduler(task_id, directory)
            return {'status': 'worker_lost', 'task_id': task_id,
                    'error': '操作进程已退出且未留下结果；不会自动重复执行，CATIA 状态需另行确认'}
        scope = read_json(directory / 'catia.json')
        if scope is None:
            pids = self.pid_provider()
            if len(pids) == 1:
                scope = {'pids': pids}
                write_json(directory / 'catia.json', scope)
            else:
                return {'status': 'running', 'task_id': task_id,
                        'dialog_monitor': 'multiple_catia_instances' if pids else 'no_catia_window'}
        try:
            dialogs = self.scanner(scope['pids'])
        except Exception as exc:
            # Win32 窗口可在枚举与包装之间销毁；观察失败不代表业务进程失败。
            return {'status': 'running', 'task_id': task_id,
                    'dialog_monitor': 'scan_error', 'monitor_message': str(exc),
                    'log_path': str(directory / 'worker.log'),
                    'next': 'catkit task wait --task ' + task_id}
        if dialogs:
            snapshot = dialogs[0]
            write_json(directory / 'dialog.json', snapshot)
            return {'status': 'waiting_for_dialog', 'task_id': task_id, 'dialog': snapshot,
                    'log_path': str(directory / 'worker.log'),
                    'next': 'catkit dialog respond --task ' + task_id + ' --button <button-id>'}
        return {'status': 'running', 'task_id': task_id, 'log_path': str(directory / 'worker.log')}

    def status(self, task_id):
        return self._state(task_id)

    def wait(self, task_id, *, wait_seconds=30, ignore_fingerprint=None):
        start = time.monotonic()
        deadline = start + max(0, wait_seconds)
        while True:
            state = self._state(task_id)
            if state['status'] == 'completed':
                return state['result']
            if state['status'] in ('worker_lost', 'launch_failed'):
                return state
            if state['status'] == 'waiting_for_dialog':
                unchanged = state['dialog']['fingerprint'] == ignore_fingerprint
                if not unchanged or time.monotonic() - start >= 1:
                    return state
            if time.monotonic() >= deadline:
                return state
            time.sleep(self.poll_interval)

    def respond(self, task_id, *, button=None, control=None, text=None, wait_seconds=30):
        directory = self.directory(task_id)
        with task_guard(self.root):
            terminal = read_json(directory / 'result.json')
            snapshot = read_json(directory / 'dialog.json')
            if snapshot is None:
                if terminal is not None:
                    return terminal['result']
                raise ValueError('任务尚无弹窗快照；先执行 task status 或 task wait')
            identity = read_json(directory / 'worker.json')
            if terminal is None and identity and not self.alive(identity):
                return self._state(task_id)
            self.responder(snapshot, button=button, control=control, text=text)
            (directory / 'dialog.json').unlink(missing_ok=True)
        return self.wait(task_id, wait_seconds=wait_seconds, ignore_fingerprint=snapshot['fingerprint'])


def run_worker(task_id, *, operation=execute_operation, root=None):
    manager = TaskManager(root=root)
    directory = manager.directory(task_id)
    # 隐藏入口即使被再次调用，也不能重复执行原操作。
    try:
        with (directory / 'worker.claim').open('x', encoding='ascii') as claim:
            claim.write(str(os.getpid()))
    except FileExistsError:
        return 0
    write_json(directory / 'worker.json', process_identity(os.getpid()))
    request = read_json(directory / 'request.json')
    try:
        os.chdir(request['cwd'])
        result = operation(request['tool'], request['parameters'])
        payload = json.loads(result) if isinstance(result, str) else result
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        payload = {'error': str(exc) or type(exc).__name__, 'task_id': task_id}
    write_json(directory / 'result.json', {'result': payload})
    identity = read_json(directory / 'launch.json') or {}
    if identity.get('scheduler'):
        from catia_copilot.cli.scheduler import delete_task
        try:
            delete_task(identity['scheduler'])
        except Exception:
            traceback.print_exc(file=sys.stderr)
    return 0


def execute(tool, parameters):
    manager = TaskManager()
    if tool == '_task_status':
        return manager.status(parameters['task'])
    if tool == '_task_wait':
        return manager.wait(parameters['task'], wait_seconds=parameters.get('timeout', 30))
    if tool == '_dialog_respond':
        return manager.respond(parameters['task'], button=parameters.get('button'),
                               control=parameters.get('control'), text=parameters.get('text'),
                               wait_seconds=parameters.get('timeout', 30))
    return manager.start(tool, parameters)
