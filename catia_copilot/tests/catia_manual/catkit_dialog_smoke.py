"""用正式 EXE 验证脚本 SaveAs 错误弹窗接续，仅操作自建零件。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import win32com.client

ROOT = Path(__file__).resolve().parents[3]


def main(exe):
    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    folder = Path(tempfile.mkdtemp(prefix='dialog-check-', dir=ROOT / '.superpowers'))
    document = app.Documents.Add('Part')
    pending = False
    result = {}
    try:
        marker = folder / 'calls.txt'
        script = folder / 'save.py'
        script.write_text('from pathlib import Path\ndef build(ctx):\n'
                          f'    marker = Path({str(marker)!r})\n'
                          '    marker.write_text(str(int(marker.read_text()) + 1) if marker.exists() else "1")\n'
                          '    part = ctx.get_active_part()\n'
                          f'    ctx.save_part(part, {str(folder / "missing" / "bad.CATPart")!r})\n', encoding='utf-8')

        def call(*args):
            completed = subprocess.run([str(exe), *args], capture_output=True, encoding='utf-8', timeout=45,
                                       env={**os.environ, 'CATKIT_TASKS_DIR': str(folder / 'tasks')})
            return completed.returncode, json.loads(completed.stdout)

        pending = True
        code, result = call('run', '--file', str(script))
        if result.get('status') == 'running':
            code, result = call('task', 'wait', '--task', result['task_id'], '--timeout', '10')
        if result.get('status') != 'waiting_for_dialog':
            pending = result.get('status') in ('running', 'busy')
            raise RuntimeError(f'未观察到预期保存错误弹窗：{result}')
        task = result['task_id']
        dialog = result['dialog']
        buttons = [button for button in dialog['buttons'] if button['enabled']]
        if len(buttons) != 1 or buttons[0]['text'].replace('&', '').strip().lower() not in ('确定', 'ok'):
            raise RuntimeError(f'非预期错误确认按钮，保留任务供人工处理：{result}')
        print(json.dumps({'task_id': task, 'dialog_seen': True, 'text': dialog['text']}, ensure_ascii=False))
        code, result = call('dialog', 'respond', '--task', task, '--button', buttons[0]['id'])
        pending = result.get('status') in ('running', 'waiting_for_dialog', 'busy')
        assert not pending, result
        assert code == 1, result
        assert marker.read_text() == '1'
        print(json.dumps({'continuation_verified': True, 'original_save_failed': True, 'script_calls': 1}, ensure_ascii=False))
    finally:
        if not pending:
            for key in ('script_path', 'run_record_path'):
                if result.get(key):
                    Path(result[key]).unlink(missing_ok=True)
            alerts = app.DisplayFileAlerts
            try:
                app.DisplayFileAlerts = False
                document.Close()
            finally:
                app.DisplayFileAlerts = alerts
                if previous is not None:
                    previous.Activate()
            shutil.rmtree(folder)
        else:
            print(f'任务尚未结束，保留夹具及任务记录：{folder}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    main(parser.parse_args().exe)
