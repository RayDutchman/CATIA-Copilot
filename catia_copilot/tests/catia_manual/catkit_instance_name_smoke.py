"""实例改名实机回归：重复文件子装配的定义联动及根层独立实例，不保存修改。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import win32com.client

ROOT = Path(__file__).resolve().parents[3]


def main(installed=False, exe=None):
    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    folder = Path(tempfile.mkdtemp(prefix='instance-smoke-', dir=ROOT / '.superpowers'))
    owned, results = [], []
    pending = False
    command = ([str(Path(os.environ['LOCALAPPDATA']) / 'Cat5cli/runtime/cat5cli.exe')]
               if installed else [sys.executable, '-m', 'catia_copilot.cli'])
    if exe is not None:
        command = [str(exe)]

    def call(*args, expected=0):
        nonlocal pending
        proc = subprocess.run([*command, *args], cwd=ROOT, capture_output=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CAT5CLI_TASKS_DIR': str(folder / 'tasks')})
        payload = json.loads(proc.stdout)
        if payload.get('status') in ('running', 'waiting_for_dialog') and args[:2] != ('task', 'wait'):
            # 原操作可能已完成；仅等待原任务，不点击与本次操作无关的启动窗口。
            return call('task', 'wait', '--task', payload['task_id'], '--timeout', '3', expected=expected)
        pending = payload.get('status') in ('running', 'waiting_for_dialog', 'busy')
        results.append({'command': args[:2], 'exit': proc.returncode, 'result': payload})
        assert not pending and proc.returncode == expected, payload
        return payload

    try:
        sub = app.Documents.Add('Product')
        owned.append(sub)
        sub.Product.PartNumber = 'Nested_' + uuid.uuid4().hex[:8]
        sub.Product.Products.AddNewProduct('Leaf_' + uuid.uuid4().hex[:8])
        path = folder / 'sub.CATProduct'
        sub.SaveAs(str(path))
        assert path.is_file()
        top = app.Documents.Add('Product')
        owned.append(top)
        top.Product.PartNumber = 'Top_' + uuid.uuid4().hex[:8]
        top.Product.Products.AddComponentsFromFiles((str(path), str(path)), 'All')
        original_second = top.Product.Products.Item(2).Name
        changes = [
            {'node': '0.0.0', 'scope': 'instance', 'column': 'Instance Name', 'value': 'ChildFirst.1'},
            {'node': '0.1.0', 'scope': 'instance', 'column': 'Instance Name', 'value': 'ChildFinal.1'},
            {'node': '0', 'scope': 'instance', 'column': 'Instance Name', 'value': 'InvalidRoot'},
            {'node': '0.0', 'scope': 'instance', 'column': 'Instance Name', 'value': 'SubFirst.1'},
        ]
        input_file = folder / 'changes.json'
        input_file.write_text(json.dumps(changes), encoding='utf-8')
        edited = call('bom', 'edit', '--doc', top.Name, '--changes', '@' + str(input_file), expected=1)
        assert edited['applied_count'] == 3 and edited['failed_count'] == 1, edited
        assert edited['results'][1]['old_value'] == 'ChildFirst.1', edited
        data = call('bom', 'collect', '--doc', top.Name, '--view', 'full')
        rows = {r['targets'][0]['node']: r for r in data['rows']}
        assert rows['0.0.0']['Instance Name'] == 'ChildFinal.1', rows
        assert rows['0.1.0']['Instance Name'] == 'ChildFinal.1', rows
        assert rows['0.0']['Instance Name'] == 'SubFirst.1', rows
        assert rows['0.1']['Instance Name'] == original_second, rows
        assert sub.Product.Products.Item(1).Name == 'ChildFinal.1'
        print(json.dumps({'passed': True, 'installed': installed, 'applied_count': 3,
                          'expected_failure_count': 1, 'shared_definition_verified': True,
                          'top_level_isolation_verified': True}, ensure_ascii=False))
    finally:
        if pending:
            print('Pending task preserved:', folder)
        else:
            alerts = app.DisplayFileAlerts
            try:
                app.DisplayFileAlerts = False
                for doc in reversed(owned):
                    try:
                        doc.Close()
                    except Exception:
                        pass
            finally:
                app.DisplayFileAlerts = alerts
                if previous is not None:
                    previous.Activate()
            for attempt in range(20):
                try:
                    shutil.rmtree(folder)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--exe', type=Path, help='指定候选 EXE 的路径')
    args = parser.parse_args()
    main(args.installed, args.exe)
