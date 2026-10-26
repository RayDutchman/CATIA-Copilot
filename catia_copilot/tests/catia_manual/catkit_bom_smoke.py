"""BOM 专项实机验证，仅操作自建未保存装配，不执行 CAD 保存或 STEP 导出。"""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import openpyxl
import win32com.client

ROOT = Path(__file__).resolve().parents[3]


def main(executable=None):
    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    folder = Path(tempfile.mkdtemp(prefix='bom-check-', dir=ROOT / '.superpowers'))
    document = app.Documents.Add('Product')
    pending = False
    results = []

    def call(*args, expected_exit=0):
        nonlocal pending
        command = [str(executable)] if executable else [sys.executable, '-m', 'catia_copilot.cli']
        process = subprocess.run([*command, *args], cwd=ROOT, capture_output=True,
                                 encoding='utf-8', timeout=45,
                                 env={**os.environ, 'CATKIT_TASKS_DIR': str(folder / 'tasks')})
        payload = json.loads(process.stdout)
        pending = payload.get('status') in ('running', 'waiting_for_dialog', 'busy')
        results.append({'command': list(args[:2]), 'exit': process.returncode, 'expected_exit': expected_exit})
        assert not pending and process.returncode == expected_exit, payload
        return payload

    try:
        root = document.Product
        root.PartNumber = 'BOMCLI_' + uuid.uuid4().hex[:8]
        root.Revision = 'HOST'
        root.UserRefProperties.CreateString('备注', '宿主备注')
        first = root.Products.AddNewProduct('ComponentA')
        second = root.Products.AddNewProduct('ComponentB')
        first.Revision = 'OLD'
        first.ReferenceProduct.UserRefProperties.CreateString('备注', '原备注')
        target = document.Name
        if previous is not None:
            previous.Activate()
        for view in ('hierarchical', 'full', 'summary'):
            params = ['bom', 'collect', '--doc', target, '--view', view, '--custom-columns', '["备注"]']
            if view == 'summary':
                params.append('--include-assemblies')
            data = call(*params)
            assert data['view'] == view
            # 沿用现有汇总规则：include_assemblies 同时包含根装配。
            assert data['row_count'] == 3, data
            assert '备注' in data['columns']
        assert call('bom', 'collect', '--doc', target, '--view', 'summary')['row_count'] == 0
        changes = folder / 'changes.json'
        changes.write_text(json.dumps([
            {'node': '0.0', 'scope': 'master', 'column': 'Revision', 'value': 'NEW'},
            {'node': '0.0', 'scope': 'master', 'column': '备注', 'value': '中文修改'},
            {'node': '0.1', 'scope': 'instance', 'column': 'Instance Name', 'value': 'Renamed.2'},
            {'node': '0.0', 'scope': 'master', 'column': 'Quantity', 'value': '99'},
        ], ensure_ascii=False), encoding='utf-8')
        edited = call('bom', 'edit', '--doc', target, '--changes', '@' + str(changes),
                      '--custom-columns', '["备注"]', expected_exit=1)
        assert edited['applied_count'] == 3 and edited['failed_count'] == 1, edited
        assert root.Revision == 'HOST' and root.UserRefProperties.Item('备注').Value == '宿主备注'
        assert first.Revision == 'NEW' and first.ReferenceProduct.UserRefProperties.Item('备注').Value == '中文修改'
        assert second.Name == 'Renamed.2'
        reread = call('bom', 'collect', '--doc', target, '--view', 'full', '--custom-columns', '["备注"]')
        row = next(row for row in reread['rows'] if any(t['node'] == '0.0' for t in row['targets']))
        assert row['Revision'] == 'NEW' and row['备注'] == '中文修改', row
        for extension in ('csv', 'xlsx'):
            dest = folder / ('bom.' + extension)
            result = call('bom', 'export', '--doc', target, '--output-file', str(dest), '--custom-columns', '["备注"]')
            assert result['row_count'] == 3 and dest.is_file(), result
            if extension == 'csv':
                with dest.open(encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.reader(stream))
                assert len(rows) == 4 and any('中文修改' in row for row in rows)
            else:
                workbook = openpyxl.load_workbook(dest, read_only=True)
                try:
                    rows = list(workbook.active.values)
                    assert len(rows) == 4 and any('中文修改' in row for row in rows)
                finally:
                    workbook.close()
            skipped = call('bom', 'export', '--doc', target, '--output-file', str(dest), '--overwrite', 'skip')
            assert skipped['skipped']
            call('bom', 'export', '--doc', target, '--output-file', str(dest), '--overwrite', 'replace')
        print(json.dumps({'bom_verified': True, 'host_unchanged': True, 'commands': results}, ensure_ascii=False))
    finally:
        if pending:
            print('Pending fixture retained: ' + str(folder))
        else:
            alerts = app.DisplayFileAlerts
            try:
                app.DisplayFileAlerts = False
                document.Close()
            finally:
                app.DisplayFileAlerts = alerts
                if previous is not None:
                    previous.Activate()
            # 前台读到结果后 worker 可能还在删除计划任务；等待其释放本轮日志句柄。
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
    parser.add_argument('--exe', type=Path)
    main(parser.parse_args().exe)
