"""当前交互用户范围内的一次性 worker 计划任务。"""
import subprocess
from pathlib import Path


def is_job_breakaway_denied(exc, *, in_job):
    return bool(in_job and getattr(exc, 'winerror', None) == 5)


def process_in_job():
    import win32api
    import win32job
    return bool(win32job.IsProcessInJob(win32api.GetCurrentProcess(), None))


class SchedulerUncertain(RuntimeError):
    """任务运行请求已提交但调度器未确认结果，禁止重复派发。"""


class SchedulerLaunch:
    def _register_and_run(self, task_id, command, *, cwd):
        import win32com.client
        import win32api
        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        folder = service.GetFolder('\\')
        name = 'Catkit-' + task_id
        definition = service.NewTask(0)
        definition.RegistrationInfo.Description = 'Catkit 临时操作 worker'
        definition.Principal.UserId = win32api.GetUserName()
        definition.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN
        definition.Principal.RunLevel = 0  # TASK_RUNLEVEL_LUA
        definition.Settings.Enabled = True
        definition.Settings.StartWhenAvailable = True
        definition.Settings.ExecutionTimeLimit = 'PT0S'
        definition.Settings.MultipleInstances = 2  # TASK_INSTANCES_IGNORE_NEW
        definition.Settings.DisallowStartIfOnBatteries = False
        definition.Settings.StopIfGoingOnBatteries = False
        action = definition.Actions.Create(0)
        action.Path = command[0]
        action.Arguments = __import__('subprocess').list2cmdline(command[1:])
        action.WorkingDirectory = cwd
        folder.RegisterTaskDefinition(name, definition, 2, '', '', 3)  # TASK_CREATE，禁止覆盖
        try:
            folder.GetTask(name).Run(None)
        except Exception as exc:
            # Run 异常可能发生在任务已启动后，调用方必须保留 pending 状态。
            raise SchedulerUncertain(f'{name}: {exc}') from exc
        return {'scheduler': name, 'scheduler_pending': True}

    def start(self, task_id, command, *, cwd, root, log_path, source_root=None):
        from catia_copilot.cli.tasks import write_json
        root = Path(root)
        name = 'Catkit-' + task_id
        is_module = len(command) > 2 and command[1:3] == ['-m', 'catia_copilot.cli']
        is_worker = len(command) > 1 and command[1] == '_worker'
        if is_module:
            command = [*command, str(root), str(log_path)]
            cwd = source_root or cwd
        elif is_worker:
            command = [*command, str(root), str(log_path)]
        # 在 Run 之前持久化任务名，快速 worker 也能清理自己的注册项。
        write_json(root / task_id / 'launch.json', {'scheduler': name, 'scheduler_pending': True})
        try:
            return self._register_and_run(task_id, command, cwd=cwd)
        except SchedulerUncertain:
            raise
        except Exception:
            try:
                delete_task(name)
            except Exception:
                pass
            (root / task_id / 'launch.json').unlink(missing_ok=True)
            raise


def delete_task(task_name):
    if not task_name:
        return
    import win32com.client
    service = win32com.client.Dispatch('Schedule.Service')
    service.Connect()
    service.GetFolder('\\').DeleteTask(task_name, 0)


def task_state(task_name):
    """返回调度状态及最近运行结果；任务不存在时返回 None。"""
    import win32com.client
    service = win32com.client.Dispatch('Schedule.Service')
    service.Connect()
    try:
        task = service.GetFolder('\\').GetTask(task_name)
        definition = task.Definition
        instances = task.GetInstances(0)
        last_run = task.LastRunTime
        if getattr(last_run, 'year', 0) <= 1601:
            last_run = None
        return {'state': task.State, 'last_run_time': last_run,
                'last_task_result': task.LastTaskResult, 'instances': instances.Count}
    except Exception as exc:
        details = getattr(exc, 'args', ())
        nested = details[2] if len(details) > 2 and isinstance(details[2], tuple) else ()
        if (getattr(exc, 'hresult', None) in (-2147024894, -2147216615) or
                any(value in (-2147024894, -2147216615) for value in nested if isinstance(value, int))):
            return None
        raise
