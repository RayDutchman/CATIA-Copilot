"""多版本来源的STEP使用自身几何重导出，不猜测CAD来源。"""
import argparse
import json
from pathlib import Path
import time
import win32com.client

parser = argparse.ArgumentParser(description='Rename STEP product numbers through CATIA using an explicit input manifest.')
parser.add_argument('--input', type=Path, required=True, help='JSON array of STEP paths, or export report with ambiguous[].file')
parser.add_argument('--report', type=Path, required=True)
parser.add_argument('--relative-mass-tolerance', type=float, default=0.0001, help='Fraction, default 0.0001 = 0.01%%')
parser.add_argument('--limit', type=int)
args = parser.parse_args()
if not 0 <= args.relative_mass_tolerance <= 1:
    parser.error('Tolerance must be between 0 and 1')
source = json.loads(args.input.read_text(encoding='utf-8'))
paths = [Path(r['file']) for r in source['ambiguous']] if isinstance(source, dict) else [Path(p) for p in source]
paths = list(dict.fromkeys(str(p.resolve()) for p in paths))[:args.limit]
def renamed(value):
    return value.replace('VL01', 'VE01').replace('V01', 'VE01')
for raw in paths:
    p = Path(raw)
    if not p.is_file() or p.suffix.lower() not in ('.stp', '.step') or renamed(p.name) == p.name:
        raise ValueError('Invalid source STEP: ' + raw)
destinations = [str(Path(p).with_name(renamed(Path(p).name))).casefold() for p in paths]
if len(set(destinations)) != len(destinations):
    raise ValueError('Multiple sources map to the same destination')
target = args.report.resolve()
if target.exists():
    raise RuntimeError('Existing roundtrip report')
target.parent.mkdir(parents=True, exist_ok=True)
report = {'started': time.time(), 'relative_mass_tolerance': args.relative_mass_tolerance,
          'rules': {'VL01': 'VE01', 'V01': 'VE01'}, 'results': []}
def write():
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
def snapshot(product):
    result = []
    def walk(node, index):
        result.append({'node': index, 'pn': str(node.PartNumber), 'name': str(node.Name),
                       'children': int(node.Products.Count)})
        for i in range(1, node.Products.Count + 1):
            walk(node.Products.Item(i), index + '.' + str(i-1))
    walk(product, '0')
    return {'tree': result, 'mass': float(product.GetTechnologicalObject('Inertia').Mass)}
def update(product):
    product.PartNumber = renamed(str(product.PartNumber))
    for i in range(1, product.Products.Count + 1):
        child = product.Products.Item(i)
        update(child)
        child.Name = renamed(str(child.Name))
app = win32com.client.GetActiveObject('CATIA.Application')
def open_import(path):
    # STEP Open 返回临时导入文档；真正的几何文档需从 Documents 重新获取。
    expected = {path.stem + '.CATPart', path.stem + '.CATProduct'}
    existing = [app.Documents.Item(i) for i in range(1, app.Documents.Count + 1)
                if app.Documents.Item(i).Name in expected]
    if existing:
        raise RuntimeError('Imported document already loaded: ' + str(path))
    app.Documents.Open(str(path))
    found = [app.Documents.Item(i) for i in range(1, app.Documents.Count + 1)
             if app.Documents.Item(i).Name in expected]
    if len(found) != 1:
        raise RuntimeError('Cannot uniquely identify imported geometry: ' + str(path))
    found[0].Product.PartNumber
    return found[0]
alerts = bool(app.DisplayFileAlerts)
try:
    app.DisplayFileAlerts = False
    for raw in paths:
        old = Path(raw)
        new = old.with_name(renamed(old.name))
        event = {'old': str(old), 'new': str(new), 'started': time.time(), 'reused_output': new.exists()}
        report['results'].append(event)
        write()
        doc = None
        try:
            doc = open_import(old)
            event['before'] = snapshot(doc.Product)
            if not event['reused_output']:
                update(doc.Product)
                doc.ExportData(str(new), 'stp')
            if not new.is_file() or not new.stat().st_size:
                raise RuntimeError('Missing STEP output')
            doc.Close()
            doc = None
            doc = open_import(new)
            event['after'] = snapshot(doc.Product)
            left, right = event['before'], event['after']
            delta = abs(left['mass']-right['mass'])
            mass_ok = delta <= abs(left['mass']) * args.relative_mass_tolerance
            event['mass_relative_error'] = delta / abs(left['mass']) if left['mass'] else (0 if delta == 0 else None)
            structure_ok = [(r['node'], r['children']) for r in left['tree']] == [(r['node'], r['children']) for r in right['tree']]
            tokens = [r for r in right['tree'] if renamed(r['pn']) != r['pn']]
            numbers_ok = [renamed(r['pn']) for r in left['tree']] == [r['pn'] for r in right['tree']]
            event.update(mass_ok=mass_ok, structure_ok=structure_ok, remaining_number_tokens=tokens,
                          numbers_ok=numbers_ok, verified=mass_ok and structure_ok and numbers_ok and not tokens)
        except Exception as exc:
            event.update(verified=False, error=str(exc))
        finally:
            if doc is not None:
                try:
                    doc.Close()
                except Exception as exc:
                    event['close_error'] = str(exc)
            event['seconds'] = time.time()-event['started']
            write()
finally:
    app.DisplayFileAlerts = alerts
    report['seconds'] = time.time()-report['started']
    write()
print(json.dumps({'count': len(report['results']), 'verified': sum(r.get('verified', False) for r in report['results'])}))
raise SystemExit(0 if all(r.get('verified', False) and not r.get('close_error') for r in report['results']) else 1)
