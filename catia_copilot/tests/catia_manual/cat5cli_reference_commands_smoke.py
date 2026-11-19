"""自建夹具验收组件定义替换、断链重连及图纸生成源重绑；需人工运行 CATIA。"""
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

ROOT = Path(__file__).resolve().parents[3]
TEMP_ROOT = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'Temp' / 'opencode'


def main(exe=None):
    import pythoncom
    import win32com.client

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from catia_copilot.catia.modeling import ModelingContext

    pythoncom.CoInitialize()
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    original_alerts = app.DisplayFileAlerts
    app.DisplayFileAlerts = False
    folder = Path(tempfile.mkdtemp(prefix='reference-commands-' + uuid.uuid4().hex[:8] + '-', dir=TEMP_ROOT))
    tasks = folder / 'tasks'
    owned = []
    pending = False
    command = [str(exe)] if exe else [sys.executable, '-X', 'utf8', '-m', 'catia_copilot.cli']
    records = []

    def call(*args, expected=0):
        nonlocal pending
        proc = subprocess.run([*command, *map(str, args)], cwd=ROOT, capture_output=True,
                              encoding='utf-8', timeout=180,
                              env={**os.environ, 'CAT5CLI_TASKS_DIR': str(tasks)})
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = {'raw_stdout': proc.stdout, 'raw_stderr': proc.stderr}
        if payload.get('status') in ('running', 'waiting_for_dialog') and args[:2] != ('task', 'wait'):
            pending = True
            task_id = payload['task_id']
            result = call('task', 'wait', '--task', task_id, '--timeout', '10', expected=expected)
            return result
        pending = payload.get('status') in ('running', 'waiting_for_dialog', 'busy')
        records.append({'args': list(args), 'exit': proc.returncode, 'result': payload})
        assert not pending and proc.returncode == expected, (args, proc.returncode, payload, proc.stderr)
        return payload

    def new_part(name, path, size):
        ctx = ModelingContext()
        part = ctx.create_part(name, 'Cat5cli reference fixture')
        doc = app.ActiveDocument
        owned.append(doc)
        sketch = ctx.add_sketch(part, 'xy')
        ctx.draw_rect(sketch, 0, 0, size, size)
        ctx.add_pad(part, sketch, size)
        ctx.update_part(part)
        doc.SaveAs(str(path))
        return doc

    def source_full_name(drawing, sheet_name, view_name):
        source = drawing.Sheets.Item(sheet_name).Views.Item(view_name).GenerativeBehavior.Document
        candidates = [source]
        try:
            candidates.append(source.Parent)
        except Exception:
            pass
        try:
            candidates.append(source.ReferenceProduct.Parent)
        except Exception:
            pass
        for candidate in candidates:
            try:
                return str(candidate.FullName)
            except Exception:
                continue
        raise AssertionError('生成源无法通过 FullName 读回')

    def close_owned():
        # 关闭本夹具下所有文档（包括 CLI 自动打开的源引用），不动用户文档。
        paths = []
        for index in range(1, app.Documents.Count + 1):
            doc = app.Documents.Item(index)
            try:
                if Path(str(doc.FullName)).resolve().is_relative_to(folder.resolve()):
                    paths.append(str(doc.FullName))
            except Exception:
                continue
        # 重新枚举后逐项关闭仍打开的自建文档，兼容 CLI 打开的引用源。
        for index in range(app.Documents.Count, 0, -1):
            doc = app.Documents.Item(index)
            try:
                path = Path(str(doc.FullName)).resolve()
                if path.is_relative_to(folder.resolve()):
                    doc.Close()
            except Exception:
                continue
        remaining = []
        for index in range(1, app.Documents.Count + 1):
            doc = app.Documents.Item(index)
            try:
                if Path(str(doc.FullName)).resolve().is_relative_to(folder.resolve()):
                    remaining.append(str(doc.FullName))
            except Exception:
                continue
        if remaining:
            raise RuntimeError('自建夹具文档仍打开，保留目录: ' + repr(remaining))
        owned.clear()

    try:
        leaf_a_path = folder / 'LeafA.CATPart'
        leaf_b_path = folder / 'LeafB.CATPart'
        target_part_path = folder / 'DrawingPart.CATPart'
        alternate_product_path = folder / 'Alternate.CATProduct'
        sub_path = folder / 'SharedSub.CATProduct'
        top_path = folder / 'Assembly.CATProduct'
        drawing_path = folder / 'Layout.CATDrawing'
        new_part('LeafA_' + uuid.uuid4().hex[:6], leaf_a_path, 10)
        new_part('LeafB_' + uuid.uuid4().hex[:6], leaf_b_path, 15)
        new_part('DrawingPart_' + uuid.uuid4().hex[:6], target_part_path, 8)
        new_part('AlternatePart_' + uuid.uuid4().hex[:6], folder / 'AlternatePart.CATPart', 6)
        alternate = app.Documents.Add('Product')
        owned.append(alternate)
        alternate.Product.PartNumber = 'Alternate_' + uuid.uuid4().hex[:6]
        alternate.Product.Products.AddComponentsFromFiles((str(folder / 'AlternatePart.CATPart'),), 'All')
        alternate.SaveAs(str(alternate_product_path))

        sub = app.Documents.Add('Product')
        owned.append(sub)
        sub.Product.PartNumber = 'SharedSub_' + uuid.uuid4().hex[:6]
        sub.Product.Products.AddComponentsFromFiles((str(leaf_a_path),), 'All')
        sub.SaveAs(str(sub_path))
        top = app.Documents.Add('Product')
        owned.append(top)
        top.Product.PartNumber = 'Assembly_' + uuid.uuid4().hex[:6]
        top.Product.Products.AddComponentsFromFiles((str(sub_path), str(sub_path)), 'All')
        top.SaveAs(str(top_path))

        drawing = app.Documents.Add('Drawing')
        owned.append(drawing)
        sheet = drawing.Sheets.Item(1)
        sheet_name = str(sheet.Name)
        view = sheet.Views.Add('ReferenceProbe')
        view.GenerativeBehavior.Document = top.Product
        view.GenerativeBehavior.DefineFrontView(1., 0., 0., 0., 1., 0.)
        view.GenerativeBehavior.Update()
        drawing.SaveAs(str(drawing_path))
        close_owned()

        # 从磁盘改名让两处共享定义同时断链；只通过稳定 node 指向其中一处请求重连。
        old_leaf = folder / 'LeafA.CATPart'
        new_leaf = folder / 'LeafA_moved.CATPart'
        old_leaf.rename(new_leaf)
        top = app.Documents.Open(str(top_path))
        owned.append(top)
        # --source-doc 目标要求已打开；用 CLI 打开并记录为夹具文档。
        call('document', 'open', '--file-path', str(target_part_path))
        call('document', 'open', '--file-path', str(alternate_product_path))
        def register_fixture_docs():
            for index in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(index)
                try:
                    if Path(str(doc.FullName)).resolve().is_relative_to(folder.resolve()) and doc not in owned:
                        owned.append(doc)
                except Exception:
                    continue
        register_fixture_docs()
        call('product', 'component', 'replace', '--doc', str(top_path), '--node', '0.0.0',
             '--file-path', str(new_leaf), '--all-instances')
        # 显式 --all-instances：两条共享装配分支都应指向新文件。
        nested_paths = []
        for index in (1, 2):
            nested_paths.append(str(top.Product.Products.Item(index).ReferenceProduct.Products.Item(1)
                                     .ReferenceProduct.Parent.FullName))
        assert all(Path(value).resolve() == new_leaf.resolve() for value in nested_paths), nested_paths
        call('product', 'component', 'replace', '--doc', str(top_path), '--node', '0',
             '--file-path', str(new_leaf), expected=1)

        drawing = app.Documents.Open(str(drawing_path))
        owned.append(drawing)
        call('drawing', 'source', 'set', '--doc', str(drawing_path), '--sheet', sheet_name,
             '--view', 'ReferenceProbe', '--source-doc', str(target_part_path))
        assert Path(source_full_name(drawing, sheet_name, 'ReferenceProbe')).resolve() == target_part_path.resolve()
        drawing.Sheets.Item(sheet_name).Views.Item('ReferenceProbe').GenerativeBehavior.Update()

        call('drawing', 'source', 'set', '--doc', str(drawing_path), '--sheet', sheet_name,
             '--view', 'ReferenceProbe', '--source-doc', str(alternate_product_path))
        assert Path(source_full_name(drawing, sheet_name, 'ReferenceProbe')).resolve() == alternate_product_path.resolve()
        drawing.Sheets.Item(sheet_name).Views.Item('ReferenceProbe').GenerativeBehavior.Update()

        # 显式通过 CLI 保存被测 Product、Drawing；源文件不作保存。
        call('document', 'save', '--file-path', str(sub_path))
        call('document', 'save', '--file-path', str(top_path))
        call('document', 'save', '--file-path', str(drawing_path))
        close_owned()
        top = app.Documents.Open(str(top_path))
        owned.append(top)
        paths = [str(top.Product.Products.Item(i).ReferenceProduct.Products.Item(1).ReferenceProduct.Parent.FullName)
                 for i in (1, 2)]
        assert all(Path(value).resolve() == new_leaf.resolve() for value in paths), paths
        drawing = app.Documents.Open(str(drawing_path))
        owned.append(drawing)
        assert Path(source_full_name(drawing, sheet_name, 'ReferenceProbe')).resolve() == alternate_product_path.resolve()
        print(json.dumps({'通过': True, '目录': str(folder), '组件共享定义读回': paths,
                           '图纸源读回': str(alternate_product_path), '命令记录': records}, ensure_ascii=False, indent=2))
    except Exception as exc:
        (folder / 'failure.json').write_text(json.dumps({'error': str(exc), 'commands': records},
                                                         ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        app.DisplayFileAlerts = original_alerts
        if pending:
            print('待处理 task，保留夹具目录:', folder)
        else:
            try:
                app.DisplayFileAlerts = False
                close_owned()
            finally:
                app.DisplayFileAlerts = original_alerts
                if previous is not None:
                    previous.Activate()
            if sys.exc_info()[0] is None:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                print('失败证据及夹具保留:', folder)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, help='指定 standalone cat5cli.exe；省略时从源码运行')
    main(parser.parse_args().exe)
