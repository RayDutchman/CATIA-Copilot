"""经用户授权运行的 Cat5cli 实机工具验收；仅修改本次临时夹具。"""
import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pythoncom

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from catia_copilot.catia.connection import get_catia_v5_application, wrap_product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', type=Path, help='从现有夹具目录继续未通过的项目')
    args = parser.parse_args()
    directory = args.resume.resolve() if args.resume else Path(tempfile.mkdtemp(prefix='catkit-live-', dir='C:/Users/Chen Weibo/AppData/Local/Temp/opencode'))
    report = directory / 'report.json'
    previous = json.loads(report.read_text(encoding='utf-8')) if args.resume else {}
    records = [case for case in previous.get('cases', []) if case['status'] == 'PASS']
    if args.resume and not (directory / 'report-initial.json').exists():
        shutil.copy2(report, directory / 'report-initial.json')
    pythoncom.CoInitialize()
    app = get_catia_v5_application()
    original_names = previous.get('original_documents') or [app.Documents.Item(i).Name for i in range(1, app.Documents.Count + 1)]

    def persist():
        report.write_text(json.dumps({'directory': str(directory), 'original_documents': original_names,
                                      'cases': records}, ensure_ascii=False, indent=2), encoding='utf-8')

    def cli(label, command, parameters=None, check=None, expected_exit=0):
        for case in records:
            if case['case'] == label and case['status'] == 'PASS':
                return case['result']
        args = [sys.executable, '-m', 'catia_copilot.cli', *command]
        for key, value in (parameters or {}).items():
            flag = '--' + key.replace('_', '-')
            if isinstance(value, bool):
                args.append(flag if value else '--no-' + key.replace('_', '-'))
            elif isinstance(value, (dict, list)):
                param_file = directory / f'{len(records):02d}-{key}.json'
                param_file.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                args.extend([flag, '@' + str(param_file)])
            elif value is not None:
                args.extend([flag, str(value)])
        start = time.monotonic()
        try:
            result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=180)
        except subprocess.TimeoutExpired:
            records.append({'case': label, 'status': 'TIMEOUT', 'command': args[3:]})
            persist()
            raise
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {'raw_stdout': result.stdout}
        error = None
        try:
            assert result.returncode == expected_exit, f'exit {result.returncode}, expected {expected_exit}'
            if check:
                check(payload)
        except Exception as exc:
            error = str(exc)
        records.append({'case': label, 'command': args[3:], 'status': 'FAIL' if error else 'PASS',
                        'seconds': round(time.monotonic() - start, 2), 'exit': result.returncode,
                        'result': payload, 'stderr': result.stderr, 'assertion_error': error})
        persist()
        print(f'{label}: {records[-1]["status"]}', flush=True)
        return payload

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def activate(path):
        for i in range(1, app.Documents.Count + 1):
            doc = app.Documents.Item(i)
            if str(doc.FullName).lower() == str(path).lower():
                doc.Activate()
                return doc
        return app.Documents.Open(str(path))

    try:
        cli('01-status', ['status'], check=lambda p: require(p.get('status') == 'connected', 'not connected'))
        cli('02-diagnose', ['status', '--diagnose'])
        cli('03-documents-initial', ['document', 'list'], check=lambda p: require(len(p.get('open_documents', [])) >= 1, 'initial documents missing'))
        part_a = directory / 'CKT_BASE.CATPart'
        part_b = directory / 'CKT_CAP.CATPart'
        script = directory / 'fixtures.py'
        script.write_text(
            'def build(ctx):\n'
            '    a = ctx.create_part("CKT_BASE", "Cat5cli base fixture")\n'
            '    sk = ctx.add_sketch(a, "xy")\n'
            '    ctx.draw_rect(sk, 0, 0, 100, 60)\n'
            '    ctx.add_pad(a, sk, 20)\n'
            '    ctx.update_part(a)\n'
            f'    ctx.save_part(a, {str(part_a)!r})\n'
            '    b = ctx.create_part("CKT_CAP", "Cat5cli cap fixture")\n'
            '    sk = ctx.add_sketch(b, "xy")\n'
            '    ctx.draw_rect(sk, 0, 0, 40, 30)\n'
            '    ctx.add_pad(b, sk, 10)\n'
            '    ctx.update_part(b)\n'
            f'    ctx.save_part(b, {str(part_b)!r})\n', encoding='utf-8')
        cli('04-run-fixtures', ['run'], {'file': str(script), 'verification': {'feature_count': 1,
            'cog_mm': {'min': [19.9, 14.9, 4.9], 'max': [20.1, 15.1, 5.1]}}},
            check=lambda p: require(p.get('verification', {}).get('status') == 'passed' and part_a.exists() and part_b.exists(), 'fixture geometry/save verification failed'))
        if not part_a.exists() or not part_b.exists():
            raise RuntimeError('Fixture creation failed; inspect report before further operations')
        cli('05-open-part', ['document', 'open'], {'file_path': str(part_a)})
        cli('06-apply-template', ['part', 'template', 'apply'], {'file_paths': [str(part_a), str(part_b)], 'keep_open': True},
            check=lambda p: require(p.get('success_count') == 2, 'template count'))
        cli('07-set-properties', ['document', 'properties', 'set'], {'file_path': str(part_a),
            'standard': {'Nomenclature': 'Cat5cli test base', 'Revision': 'A'},
            'user_defined': {'材料': 'TEST-MATERIAL', '备注': 'CLI smoke fixture'}, 'save': True})
        cli('08-get-properties', ['document', 'properties', 'get'], {'file_path': str(part_a)},
            check=lambda p: require(p.get('standard', {}).get('Nomenclature') == 'Cat5cli test base', 'property not read back'))
        cli('09-save-part', ['document', 'save'], {'file_path': str(part_a)})

        assembly_path = directory / 'CKT_ASSEMBLY.CATProduct'
        if args.resume:
            assembly = activate(assembly_path)
        else:
            assembly = app.Documents.Add('Product')
            assembly.Product.PartNumber = 'CKT_ASSEMBLY'
            wrap_product(assembly.Product).products.add_components_from_files((str(part_a), str(part_b)), 'All')
            assembly.Product.Update()
            assembly.SaveAs(str(assembly_path))
        assembly.Activate()
        cli('10-bom-collect', ['bom', 'collect'], check=lambda p: require(p.get('row_count', 0) >= 3, 'assembly rows missing'))
        cli('11-bom-summary', ['bom', 'collect'], {'summarize': True})
        cli('12-bom-csv', ['bom', 'export'], {'file_paths': [None], 'output_folder': str(directory / 'bom'), 'output_format': 'csv'},
            check=lambda p: require(bool(p.get('exported_files')) and all(Path(f).is_file() for f in p['exported_files']), 'CSV missing'))
        cli('13-bom-xlsx', ['bom', 'export'], {'file_paths': [str(assembly_path)], 'output_folder': str(directory / 'bom'), 'output_format': 'xlsx'},
            check=lambda p: require(bool(p.get('exported_files')) and all(Path(f).is_file() for f in p['exported_files']), 'XLSX missing'))
        activate(assembly_path)
        cli('14-bom-write', ['bom', 'write'], {'pn_data': {'CKT_BASE': {'Nomenclature': 'BOM write test', '备注': 'BOM updated'}}, 'custom_columns': ['备注']})
        cli('15-check-bom-write', ['document', 'properties', 'get'], {'file_path': str(part_a)},
            check=lambda p: require(p.get('standard', {}).get('Nomenclature') == 'BOM write test', 'BOM write not read back'))
        activate(assembly_path)
        cli('16-mass', ['product', 'mass', 'collect'], {'summary_only': True})
        cli('17-dependencies', ['document', 'dependencies', 'list'], {'target_path': str(assembly_path)})
        cli('18-reverse-dependencies', ['document', 'dependencies', 'reverse'], {'target_path': str(part_a)})
        cli('19-step', ['part', 'export', 'step'], {'file_paths': [str(part_a), str(assembly_path)], 'output_folder': str(directory / 'step')},
            check=lambda p: require(p.get('exported_count') == 2 and len(list((directory / 'step').glob('*.stp'))) == 2, 'STEP export incomplete'))

        template = directory / 'template.CATDrawing'
        shutil.copy2(ROOT / 'drawing_templates/EHANG_A4.CATDrawing', template)
        activate(part_a)
        drawing_result = cli('20-drawing-create', ['drawing', 'create'], {'template_path': str(template)},
            check=lambda p: require(p.get('success') is True, 'drawing generation failed'))
        if drawing_result.get('success'):
            drawing = app.ActiveDocument
            drawing_path = directory / 'DR_CKT_BASE.CATDrawing'
            drawing.SaveAs(str(drawing_path))
            cli('21-drawing-refresh', ['drawing', 'refresh'], check=lambda p: require(p.get('success') is True, 'drawing refresh failed'))
            cli('22-find-part', ['document', 'find', 'part'], {'drawing_path': str(drawing_path), 'max_parent_levels': 0},
                check=lambda p: require(str(part_a).lower() in [str(x).lower() for x in p.get('matches', [])], 'part association missing'))
            cli('23-find-drawing', ['document', 'find', 'drawing'], {'part_path': str(part_a), 'max_parent_levels': 0},
                check=lambda p: require(str(drawing_path).lower() in [str(x).lower() for x in p.get('matches', [])], 'drawing association missing'))
            cli('24-pdf', ['drawing', 'export', 'pdf'], {'file_paths': [str(drawing_path)], 'output_folder': str(directory / 'pdf'), 'update_before_export': True},
                check=lambda p: require(p.get('exported_count') == 1 and len(list((directory / 'pdf').glob('*.pdf'))) == 1, 'PDF missing'))

        cli('25-negative-script', ['run'], {'script': 'not a build function'}, expected_exit=1,
            check=lambda p: require(p.get('success') is False, 'invalid script not rejected'))
        cli('26-negative-properties', ['document', 'properties', 'set'], expected_exit=1,
            check=lambda p: require(bool(p.get('error')), 'original validation missing'))
        cli('27-documents-final', ['document', 'list'])
    except Exception as exc:
        records.append({'case': 'harness', 'status': 'FAIL', 'error': str(exc)})
        persist()
        raise
    finally:
        pythoncom.CoUninitialize()
        print('REPORT=' + str(report), flush=True)
    print(json.dumps({'passed': sum(r['status'] == 'PASS' for r in records),
                      'failed': sum(r['status'] == 'FAIL' for r in records)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
