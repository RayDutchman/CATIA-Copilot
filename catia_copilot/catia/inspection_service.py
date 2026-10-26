"""CLI 对象查询及文档操作：复用业务层，不依赖 GUI。"""
from pathlib import Path

from .connection import get_catia_v5_application
from .document import get_document_type
from .document_scope import describe_document, resolve_document


def _info(document, app):
    result = describe_document(document).to_dict()
    result['type'] = get_document_type(document)
    try:
        result['active'] = document == app.ActiveDocument
    except Exception:
        result['active'] = False
    for name, attribute in [('saved', 'Saved'), ('read_only', 'ReadOnly')]:
        try:
            result[name] = bool(getattr(document, attribute))
        except Exception:
            result[name] = None
    return result


def document_info(doc=None, active=False, **_kwargs):
    app = get_catia_v5_application()
    return _info(resolve_document(app, doc, active), app)


def current_document(**_kwargs):
    return document_info(active=True)


def activate_document(doc=None, active=False, **_kwargs):
    app = get_catia_v5_application()
    document = resolve_document(app, doc, active)
    document.Activate()
    return {'success': True, 'document': _info(document, app)}


def save_document_as(file_path, doc=None, active=False, **_kwargs):
    app = get_catia_v5_application()
    document = resolve_document(app, doc, active)
    path = str(Path(file_path).resolve())
    # 使用原生 SaveAs，保留 CATIA 覆盖/许可提示交给既有弹窗任务处理。
    document.SaveAs(path)
    # CATIA 错误提示关闭后 FullName 也可能已改变，不能据此声称文件已经生成。
    if (str(Path(document.FullName).resolve()).casefold() != path.casefold()
            or not Path(path).is_file()):
        raise RuntimeError('另存未完成或被取消')
    return {'success': True, 'saved_path': document.FullName, 'document': _info(document, app)}


def close_document(doc=None, active=False, **_kwargs):
    app = get_catia_v5_application()
    document = resolve_document(app, doc, active)
    identity = describe_document(document).to_dict()
    document.Close()
    for index in range(1, app.Documents.Count + 1):
        if app.Documents.Item(index) == document:
            raise RuntimeError('关闭未完成或被取消')
    return {'success': True, 'closed': identity}


def _target(doc, active, kind):
    app = get_catia_v5_application()
    return app, resolve_document(app, doc, active, kind)


def part_info(doc=None, active=False, **_kwargs):
    app, document = _target(doc, active, 'PartDocument')
    return {'document': _info(document, app), 'name': str(document.Part.Name)}


def update_part_document(doc=None, active=False, **_kwargs):
    from pycatia.mec_mod_interfaces.part import Part
    from .modeling import update_part
    app, document = _target(doc, active, 'PartDocument')
    update_part(Part(document.Part))
    return {'success': True, 'document': _info(document, app)}


def part_mass(doc=None, active=False, **_kwargs):
    from pycatia.mec_mod_interfaces.part import Part
    from .modeling import get_mass_props
    app, document = _target(doc, active, 'PartDocument')
    result = get_mass_props(Part(document.Part), document=document)
    if result is None:
        raise RuntimeError('读取零件质量失败，请检查实体及材料')
    return {**result, 'document': _info(document, app),
            'units': {'mass': 'kg', 'cog': 'mm', 'inertia': 'kg*mm^2'}}


def part_tree(doc=None, active=False, **_kwargs):
    app, document = _target(doc, active, 'PartDocument')
    errors = []
    collections = {
        'Part': [('Bodies', 'Body'), ('HybridBodies', 'HybridBody'), ('OrderedGeometricalSets', 'OrderedGeometricalSet')],
        'Body': [('Shapes', 'Shape'), ('Sketches', 'Sketch'), ('HybridBodies', 'HybridBody'),
                 ('HybridShapes', 'HybridShape'), ('OrderedGeometricalSets', 'OrderedGeometricalSet')],
        'HybridBody': [('HybridBodies', 'HybridBody'), ('HybridShapes', 'HybridShape'),
                       ('HybridSketches', 'Sketch'), ('Bodies', 'Body')],
        'OrderedGeometricalSet': [('HybridShapes', 'HybridShape'), ('OrderedGeometricalSets', 'OrderedGeometricalSet')],
    }

    def walk(obj, kind, path):
        node = {'name': str(obj.Name), 'type': kind, 'path': path, 'children': []}
        for attribute, child_kind in collections.get(kind, []):
            try:
                items = getattr(obj, attribute)
                if items is None:
                    continue
                for index in range(1, items.Count + 1):
                    child_path = f'{path}/{attribute}/{index}'
                    try:
                        node['children'].append(walk(items.Item(index), child_kind, child_path))
                    except Exception as exc:
                        errors.append({'path': child_path, 'error': str(exc)})
            except Exception as exc:
                errors.append({'path': f'{path}/{attribute}', 'error': str(exc)})
        return node

    tree = walk(document.Part, 'Part', 'Part')
    return {'document': _info(document, app), 'tree': tree, 'complete': not errors,
            'scope': 'Bodies/Shapes/Sketches/HybridBodies/HybridShapes/OrderedGeometricalSets；不展开特征内部参数与布尔操作吸收体',
            'errors': errors}


def _linked_document(link):
    """生成链接可指向 Product/特征，通过已存在的 Parent 关系找文档。"""
    current = link
    for _ in range(32):
        if current is None:
            break
        if get_document_type(current) != 'Unknown':
            identity = describe_document(current)
            if identity.name or identity.full_name:
                return identity.to_dict()
        try:
            current = current.Parent
        except Exception:
            break
    raise RuntimeError('生成链接无法解析到关联文档')


def drawing_info(doc=None, active=False, **_kwargs):
    app, document = _target(doc, active, 'DrawingDocument')
    sheets, errors = [], []
    for index in range(1, document.Sheets.Count + 1):
        try:
            sheet = document.Sheets.Item(index)
            item = {'name': str(sheet.Name), 'views': []}
            sheets.append(item)
            for view_index in range(1, sheet.Views.Count + 1):
                view_result = {'index': view_index, 'links': []}
                item['views'].append(view_result)
                try:
                    view = sheet.Views.Item(view_index)
                    view_result['name'] = str(view.Name)
                    view_result['generative'] = bool(view.IsGenerative())
                    if view_result['generative']:
                        # CAA 定义此属性为生成视图使用的文档（或 Body 的父对象）。
                        # 不依赖 NextLink 的末尾异常去猜测链接枚举是否完整。
                        view_result['links'].append(_linked_document(view.GenerativeBehavior.Document))
                        view_result['link_scope'] = 'generative_document'
                except Exception as exc:
                    view_result['error'] = str(exc)
                    errors.append({'sheet': index, 'view': view_index, 'error': str(exc)})
        except Exception as exc:
            errors.append({'sheet': index, 'error': str(exc)})
    return {'document': _info(document, app), 'sheet_count': document.Sheets.Count,
            'sheets': sheets, 'complete': not errors, 'errors': errors}


def product_tree(doc=None, active=False, node=None, **_kwargs):
    from .assembly_reader import read_assembly_tree
    app, document = _target(doc, active, 'ProductDocument')
    errors = []
    tree = read_assembly_tree(app, document=document, errors=errors)
    if tree is None:
        raise RuntimeError('读取装配树失败')
    if node is not None:
        pending, found = [tree], None
        while pending:
            candidate = pending.pop()
            if candidate['path'] == node:
                found = candidate
                break
            pending.extend(candidate.get('children', []))
        if found is None:
            raise ValueError(f'装配节点不存在：{node}；请重新查询 tree')
        tree = found
    return {'document': _info(document, app), 'tree': tree,
            'complete': not errors, 'errors': errors,
            'node_path_note': '零基索引路径；装配树变化后须重新查询'}


def product_info(doc=None, active=False, **_kwargs):
    result = product_tree(doc, active)
    root = result.pop('tree')
    result['product'] = {key: value for key, value in root.items() if key != 'children'}
    result['child_count'] = len(root.get('children', []))
    return result


def merge_mass_files(files, output_file, overwrite='error', **_kwargs):
    from .mass_props_collect import load_rows, merge_rows, save_rows
    if not isinstance(files, list) or not files:
        raise ValueError('files 必须为非空 MPD 路径数组')
    target = Path(output_file).resolve()
    if overwrite not in ('error', 'skip', 'replace'):
        raise ValueError('无效覆盖策略')
    if target.exists():
        if overwrite == 'error':
            raise FileExistsError(f'输出文件已存在：{target}')
        if overwrite == 'skip':
            return {'success': True, 'skipped': True, 'output_file': str(target)}
    rows = []
    for file in files:
        rows = merge_rows(rows, load_rows(file))
    save_rows(rows, str(target))
    return {'success': True, 'row_count': len(rows), 'output_file': str(target),
            'coordinate_system': '输入数据沿用同一根坐标系，不执行额外变换'}


def remeasure_product_part(file_path, part_number='', read_mode='all', **_kwargs):
    from .mass_props_collect import remeasure_part_mass_props
    result = remeasure_part_mass_props(file_path, part_number, read_mode)
    if result is None:
        raise RuntimeError('保持测量读取失败：请确认目标零件已打开且包含测量参数')
    return {'file_path': file_path, 'mass_properties': result,
            'units': {'weight': 'kg', 'cog': 'mm', 'inertia': 'kg*mm^2'}}


def export_product_step(file_paths=None, output_folder=None, prefix='MD_', suffix='',
                        doc=None, active=False, progress_signal=None, **_kwargs):
    from .conversion import convert_part_to_step
    from . import file_interaction
    if file_paths is not None and (doc is not None or active):
        raise ValueError('文件集与 --doc/--active 互斥')
    if file_paths is None:
        _, document = _target(doc, active, 'ProductDocument')
        file_paths = [describe_document(document).full_name]
    if not isinstance(file_paths, list) or not file_paths:
        raise ValueError('file_paths 必须为非空 CATProduct 路径数组')
    if any(not isinstance(p, str) or Path(p).suffix.lower() != '.catproduct' for p in file_paths):
        raise ValueError('整装配导出需要已保存的 CATProduct 文件路径')
    results = []
    old_handler = file_interaction.overwrite_handler
    try:
        for file_path in file_paths:
            actions = []
            def observe(dest):
                if old_handler is None:
                    raise FileExistsError(f'输出文件已存在：{dest}；请指定 --overwrite')
                action = old_handler(dest)
                actions.append(action)
                return action
            file_interaction.overwrite_handler = observe
            if progress_signal:
                progress_signal.emit(f'导出装配：{file_path}')
            try:
                count = convert_part_to_step([file_path], output_folder, prefix, suffix)
                if count == 1:
                    results.append({'file_path': file_path, 'status': 'exported'})
                elif any(action in ('skip', 'skip_all') for action in actions):
                    results.append({'file_path': file_path, 'status': 'skipped'})
                else:
                    results.append({'file_path': file_path, 'status': 'failed', 'error': 'STEP 导出失败，详见 worker 日志'})
            except Exception as exc:
                results.append({'file_path': file_path, 'status': 'failed', 'error': str(exc)})
    finally:
        file_interaction.overwrite_handler = old_handler
    failed = sum(item['status'] == 'failed' for item in results)
    return {'success': not failed, 'total': len(results), 'failed_count': failed,
            'exported_count': sum(item['status'] == 'exported' for item in results),
            'skipped_count': sum(item['status'] == 'skipped' for item in results), 'results': results}


inspection_tools = {
    'current_document': current_document,
    'document_info': document_info,
    'activate_document': activate_document,
    'save_document_as': save_document_as,
    'close_document': close_document,
    'part_info': part_info,
    'part_tree': part_tree,
    'update_part_document': update_part_document,
    'part_mass': part_mass,
    'drawing_info': drawing_info,
    'product_info': product_info,
    'product_tree': product_tree,
    'merge_mass_files': merge_mass_files,
    'remeasure_product_part': remeasure_product_part,
    'export_product_step': export_product_step,
}
