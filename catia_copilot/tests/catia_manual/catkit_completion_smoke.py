"""新命令实机验证：仅创建/关闭自己的夹具，临时文件自动清理。"""
import argparse
import csv
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

import win32com.client

ROOT = Path(__file__).resolve().parents[3]


def main(executable=None):
    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    owned, results = [], []
    pending = False
    # 路径集中在已忽略的开发工作目录，结束后清理。
    with nullcontext(tempfile.mkdtemp(prefix='completion-', dir=ROOT / '.superpowers')) as temp:
        folder = Path(temp)

        def call(*args, expected_exit=0):
            nonlocal pending
            command = [str(executable)] if executable else [sys.executable, '-m', 'catia_copilot.cli']
            process = subprocess.run([*command, *args],
                                     cwd=ROOT, capture_output=True, encoding='utf-8', timeout=50,
                                     env={**os.environ, 'CATKIT_TASKS_DIR': str(folder / 'tasks')})
            payload = json.loads(process.stdout)
            pending = payload.get('status') in ('running', 'waiting_for_dialog', 'busy')
            results.append({'command': list(args), 'exit': process.returncode,
                            'expected_exit': expected_exit, 'result': payload})
            # 仅确认本夹具另存操作的单按钮提示，随后以原任务最终结果判断成功。
            # 不重新发起保存，不对其他弹窗做自动选择。
            if payload.get('status') == 'waiting_for_dialog' and args[:2] == ('document', 'save-as'):
                dialog = payload['dialog']
                buttons = [button for button in dialog['buttons'] if button['enabled']]
                if (str(folder) in dialog['text'] and len(buttons) == 1
                        and buttons[0]['text'].replace('&', '').strip().lower() in ('确定', 'ok')):
                    return call('dialog', 'respond', '--task', payload['task_id'], '--button', buttons[0]['id'],
                                expected_exit=expected_exit)
            if pending and args[:2] == ('dialog', 'respond'):
                return call('task', 'wait', '--task', payload['task_id'], '--timeout', '5', expected_exit=expected_exit)
            if process.returncode != expected_exit or pending:
                raise RuntimeError(json.dumps(results[-1], ensure_ascii=False))
            return payload

        try:
            # COM 仅准备夹具；被验收操作全部从 CLI 调用。
            a = app.Documents.Add('Part')
            owned.append(a)
            name_a = a.Name
            b = app.Documents.Add('Part')
            owned.append(b)
            name_b = b.Name
            script = folder / 'build.py'
            script.write_text('def build(ctx):\n    p = ctx.get_active_part()\n    s = ctx.add_sketch(p, "xy")\n    ctx.draw_rect(s, 0, 0, 10, 20)\n    ctx.add_pad(p, s, 5)\n    ctx.update_part(p)\n', encoding='utf-8')
            call('run', '--file', str(script))
            a.Activate()
            call('document', 'current')
            call('document', 'info', '--doc', name_b)
            call('part', 'info', '--doc', name_b)
            assert call('part', 'tree', '--doc', name_b)['complete']
            call('part', 'update', '--doc', name_b)
            mass = call('part', 'mass', '--doc', name_b)
            assert mass['units']['inertia'] == 'kg*mm^2'
            assert abs(mass['inertia'][0][0] - mass['mass'] * (20**2 + 5**2) / 12) < 1e-9
            part_path = folder / (folder.name + '_part.CATPart')
            call('document', 'save-as', '--doc', name_b, '--file-path', str(part_path))
            assert part_path.is_file()
            call('document', 'activate', '--doc', str(part_path))
            call('document', 'close', '--doc', str(part_path))
            owned.remove(b)
            product = app.Documents.Add('Product')
            owned.append(product)
            product.Product.Products.AddComponentsFromFiles((str(part_path), str(part_path)), 'All')
            product_path = folder / (folder.name + '_assembly.CATProduct')
            call('document', 'save-as', '--file-path', str(product_path))
            call('product', 'info')
            call('product', 'tree')
            call('product', 'tree', '--node', '0.0')
            a.Activate()
            full = call('bom', 'collect', '--doc', str(product_path), '--view', 'full')
            hierarchical = call('bom', 'collect', '--doc', str(product_path))
            summary = call('bom', 'collect', '--doc', str(product_path), '--view', 'summary')
            assert full['row_count'] == 3, full
            assert hierarchical['row_count'] == 2, hierarchical
            assert summary['row_count'] == 1 and summary['rows'][0]['Quantity'] == 2, summary
            changes = folder / 'changes.json'
            changes.write_text(json.dumps([
                {'node': '0.0', 'scope': 'master', 'column': 'Revision', 'value': 'B'},
                {'node': '0.0', 'scope': 'master', 'column': 'Quantity', 'value': '99'},
                {'node': '0.1', 'scope': 'instance', 'column': 'Instance Name', 'value': 'BOMTest.2'},
                {'node': '0.0', 'scope': 'master', 'column': '备注', 'value': '中文验证'},
            ]), encoding='utf-8')
            custom_columns = json.dumps(['备注'], ensure_ascii=False)
            edited = call('bom', 'edit', '--doc', str(product_path), '--changes', '@' + str(changes),
                          '--custom-columns', custom_columns, expected_exit=1)
            assert edited['applied_count'] == 3 and edited['failed_count'] == 1, edited
            reread = call('bom', 'collect', '--doc', str(product_path), '--view', 'full',
                          '--columns', json.dumps(['Part Number', 'Revision', 'Instance Name', 'Quantity']),
                          '--custom-columns', custom_columns)
            assert all(row['Revision'] == 'B' for row in reread['rows'][1:]), reread
            assert reread['rows'][2]['Instance Name'] == 'BOMTest.2', reread
            assert all(row['备注'] == '中文验证' for row in reread['rows'][1:]), reread
            output = folder / 'bom.csv'
            exported = call('bom', 'export', '--doc', str(product_path), '--view', 'summary', '--output-file', str(output),
                            '--custom-columns', custom_columns)
            assert exported['row_count'] == 1
            with output.open(encoding='utf-8-sig', newline='') as stream:
                exported_rows = list(csv.reader(stream))
            assert len(exported_rows) == 2
            assert exported_rows[1][summary['columns'].index('Quantity')] == '2'
            assert exported_rows[1][-1] == '中文验证'
            skipped = call('bom', 'export', '--doc', str(product_path), '--output-file', str(output), '--overwrite', 'skip')
            assert skipped['skipped']
            call('bom', 'export', '--doc', str(product_path), '--view', 'summary', '--output-file', str(output), '--overwrite', 'replace')
            # 自建嵌入部件带一个引用零件，验证字段写回不影响宿主。
            component = product.Product.Products.AddNewProduct('BOMEmbedded')
            component.Products.AddComponentsFromFiles((str(part_path),), 'All')
            product.Product.Revision = 'HOST'
            product.Product.UserRefProperties.CreateString('备注', '宿主备注')
            component.Revision = 'COMPONENT'
            component.ReferenceProduct.UserRefProperties.CreateString('备注', '部件备注')
            embedded_changes = folder / 'embedded-changes.json'
            embedded_changes.write_text(json.dumps([
                {'node': '0.2', 'scope': 'master', 'column': 'Revision', 'value': 'COMPONENT-EDITED'},
                {'node': '0.2', 'scope': 'master', 'column': '备注', 'value': '部件已修改'},
            ], ensure_ascii=False), encoding='utf-8')
            embedded_result = call('bom', 'edit', '--doc', str(product_path), '--changes', '@' + str(embedded_changes),
                                   '--custom-columns', custom_columns)
            assert embedded_result['applied_count'] == 2, embedded_result
            assert embedded_result['results'][0]['old_value'] == 'COMPONENT', embedded_result
            assert product.Product.Revision == 'HOST'
            assert product.Product.UserRefProperties.Item('备注').Value == '宿主备注'
            assert component.Revision == 'COMPONENT-EDITED'
            assert component.ReferenceProduct.UserRefProperties.Item('备注').Value == '部件已修改'
            embedded_rows = call('bom', 'collect', '--doc', str(product_path), '--view', 'full',
                                 '--custom-columns', custom_columns)['rows']
            embedded_row = next(row for row in embedded_rows if any(t['node'] == '0.2' for t in row['targets']))
            assert embedded_row['Revision'] == 'COMPONENT-EDITED' and embedded_row['备注'] == '部件已修改', embedded_row
            call('document', 'save', '--file-path', str(part_path))
            call('document', 'save', '--file-path', str(product_path))
            product.Activate()
            call('product', 'export', 'step', '--files', json.dumps([str(product_path)]))
            assert (folder / ('MD_' + product_path.stem + '.stp')).is_file()
            drawing = app.Documents.Add('Drawing')
            owned.append(drawing)
            view = drawing.Sheets.Item(1).Views.Add('CompletionFront')
            view.GenerativeBehavior.Document = product.Product
            view.GenerativeBehavior.DefineFrontView(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
            view.GenerativeBehavior.Update()
            info = call('drawing', 'info')
            assert info['complete']
            assert any(v['links'] for s in info['sheets'] for v in s['views'])
        finally:
            if pending:
                print('Pending task preserved at: ' + str(folder))
            else:
                alerts = app.DisplayFileAlerts
                try:
                    app.DisplayFileAlerts = False
                    for document in reversed(owned):
                        try:
                            document.Close()
                        except Exception:
                            pass
                finally:
                    app.DisplayFileAlerts = alerts
                    if previous is not None:
                        previous.Activate()
                for result in results:
                    for key in ('script_path', 'run_record_path'):
                        generated = result['result'].get(key)
                        if generated:
                            Path(generated).unlink(missing_ok=True)
                shutil.rmtree(folder)
            print(json.dumps([{'command': r['command'][:3], 'exit': r['exit'],
                               'expected_exit': r['expected_exit'],
                               'complete': r['result'].get('complete'),
                               'errors': r['result'].get('errors', [])} for r in results], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, help='指定 standalone EXE 验收；省略时使用源码 CLI')
    main(parser.parse_args().exe)
