"""独立进程模拟标准 Windows 模态框，不连接 CATIA。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.coinit_flags = 2

import win32con
import win32gui
from catia_copilot.cli.tasks import TaskManager, read_json, run_worker, spawn_worker


def operation(tool, params):
    marker = Path(params['marker'])
    count = int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(count + 1))
    answer = win32gui.MessageBox(0, 'Controlled dialog continuation test', 'Catkit isolated fixture', win32con.MB_YESNO)
    return {'success': True, 'answer': answer, 'calls': count + 1}


def main():
    mode, root_arg = sys.argv[1:3]
    root = Path(root_arg)
    if mode == 'worker':
        return run_worker(sys.argv[3], operation=operation, root=root)
    def launcher(task_id, task_root):
        return spawn_worker(task_id, task_root, command=[sys.executable, str(Path(__file__).resolve()), 'worker', str(root), task_id])
    def pids():
        files = list(root.glob('*/worker.json'))
        return [read_json(files[0])['pid']] if files else []
    manager = TaskManager(root=root, launcher=launcher, pid_provider=pids)
    if mode == 'start':
        result = manager.start('fixture', {'marker': str(root / 'calls.txt')}, wait_seconds=15)
    elif mode == 'respond':
        result = manager.respond(sys.argv[3], button=sys.argv[4], wait_seconds=15)
    else:
        result = manager.wait(sys.argv[3], wait_seconds=1)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
