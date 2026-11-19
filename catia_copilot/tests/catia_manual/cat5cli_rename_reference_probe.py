"""自建夹具：文件系统改名、底向上加载/重连、普通Save及冷重开验证。"""
import json
import hashlib
import os
from pathlib import Path
import time
import traceback
import uuid


def build(ctx):
    import win32com.client
    from catia_copilot.catia.connection import wrap_product

    app = win32com.client.GetActiveObject('CATIA.Application')
    previous = app.ActiveDocument if app.Documents.Count else None
    # run会复制脚本，夹具以工作目录定位；可用环境变量选择独立验证目录。
    probe_root = Path(os.environ.get('CAT5CLI_PROBE_ROOT') or Path.cwd() / '.superpowers')
    base = probe_root / ('rename-reference-' + uuid.uuid4().hex[:8])
    old_dir = base / 'VL01_project'
    parts_dir = old_dir / 'VT28_parts'
    vendor_dir = base / 'vendor'
    parts_dir.mkdir(parents=True)
    vendor_dir.mkdir()
    report_path = base / 'report.json'
    report = {'folder': str(base), 'steps': [], 'replacement_calls': [], 'passed': False}
    owned = []
    alerts = app.DisplayFileAlerts
    app.DisplayFileAlerts = False

    def checkpoint(stage, **data):
        report['stage'] = stage
        report.update(data)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def operation(name, function):
        checkpoint(name)
        start = time.perf_counter()
        value = function()
        report['steps'].append({'operation': name, 'seconds': round(time.perf_counter() - start, 4)})
        checkpoint(name + ':done')
        return value

    def create_part(name, path, size):
        doc = app.Documents.Add('Part')
        owned.append(doc)
        doc.Activate()
        doc.Product.PartNumber = name
        part = ctx.get_active_part()
        sketch = ctx.add_sketch(part, 'xy')
        ctx.draw_rect(sketch, 0, 0, size, size)
        ctx.add_pad(part, sketch, size)
        ctx.update_part(part)
        reference = doc.Part.CreateReferenceFromObject(doc.Part.OriginElements.PlaneXY)
        reference = doc.Product.CreateReferenceFromName(doc.Product.Name + '/!' + reference.DisplayName)
        doc.Product.Publications.Add('MountPlane')
        doc.Product.Publications.SetDirect('MountPlane', reference)
        doc.SaveAs(str(path))  # 仅创建初始夹具；迁移阶段禁止SaveAs。
        return doc

    def position(product):
        return list(wrap_product(product).position.get_components())

    def fix_reference(parent, child, name):
        reference = parent.CreateReferenceFromName(parent.Name + '/' + child.Name + '/!')
        constraint = parent.Connections('CATIAConstraints').AddMonoEltCst(0, reference)
        constraint.Name = name
        return constraint

    def snapshot(document):
        output = {'path': document.FullName, 'part_number': document.Product.PartNumber, 'nodes': [], 'constraints': []}
        def walk(product, node):
            reference = product.ReferenceProduct
            entry = {'node': node, 'name': product.Name, 'part_number': product.PartNumber,
                     'path': reference.Parent.FullName, 'position': position(product)}
            pubs = reference.Publications
            entry['publications'] = [{'name': pubs.Item(i).Name, 'target': pubs.Item(i).Valuation.DisplayName}
                                     for i in range(1, pubs.Count + 1)]
            output['nodes'].append(entry)
            try:
                constraints = reference.Connections('CATIAConstraints')
                for i in range(1, constraints.Count + 1):
                    cst = constraints.Item(i)
                    output['constraints'].append({'owner': node, 'name': cst.Name, 'type': cst.Type, 'status': cst.Status,
                                                  'dimension': float(cst.Dimension.Value) if cst.Type == 1 else None})
            except Exception:
                if product.Products.Count:
                    raise
            for i in range(1, product.Products.Count + 1):
                walk(product.Products.Item(i), node + '.' + str(i - 1))
        walk(document.Product, '0')
        output['mass'] = float(document.Product.Analyze.Mass)
        return output

    def close_owned():
        for doc in reversed(owned):
            try:
                doc.Close()
            except Exception:
                pass
        owned.clear()
        # 引用文件不能遗留在会话中，否则不能算冷重开。
        remaining = [app.Documents.Item(i).FullName for i in range(1, app.Documents.Count + 1)
                     if str(app.Documents.Item(i).FullName).startswith(str(base))]
        if remaining:
            raise RuntimeError('Owned documents still loaded: ' + repr(remaining))

    try:
        leaf_path = parts_dir / 'VL01_LEAF.CATPart'
        vendor_path = vendor_dir / 'VT28_VENDOR.CATPart'
        sub_path = old_dir / 'VL01_SUB.CATProduct'
        top_path = old_dir / 'VT28_TOP.CATProduct'
        drawing_path = old_dir / 'VL01_LAYOUT.CATDrawing'
        leaf = operation('fixture:create_leaf', lambda: create_part('VL01_LEAF', leaf_path, 10))
        vendor = operation('fixture:create_vendor', lambda: create_part('VT28_VENDOR', vendor_path, 5))
        vendor_hash = hashlib.sha256(vendor_path.read_bytes()).hexdigest()
        sub = app.Documents.Add('Product')
        owned.append(sub)
        sub.Product.PartNumber = 'VL01_SUB'
        sub.Product.Products.AddComponentsFromFiles((str(leaf_path), str(leaf_path)), 'All')
        for i, name in ((1, 'Mount_A'), (2, 'Mount_B')):
            child = sub.Product.Products.Item(i)
            child.Name = name
            wrap_product(child).position.set_components((1, 0, 0, 0, 1, 0, 0, 0, 1, (i - 1) * 30, 0, 0))
            if i == 1:
                fix_reference(sub.Product, child, 'Fix_' + name)
        plane_name = leaf.Part.CreateReferenceFromObject(leaf.Part.OriginElements.PlaneYZ).DisplayName
        reference_a = sub.Product.CreateReferenceFromName(sub.Product.Name + '/Mount_A/!' + plane_name)
        reference_b = sub.Product.CreateReferenceFromName(sub.Product.Name + '/Mount_B/!' + plane_name)
        distance = sub.Product.Connections('CATIAConstraints').AddBiEltCst(1, reference_a, reference_b)
        distance.Name = 'Mount_Distance'
        distance.Dimension.Value = 30.0
        sub.Product.Update()
        operation('fixture:save_sub', lambda: sub.SaveAs(str(sub_path)))
        top = app.Documents.Add('Product')
        owned.append(top)
        top.Product.PartNumber = 'VT28_TOP'
        top.Product.Products.AddComponentsFromFiles((str(sub_path), str(leaf_path), str(vendor_path)), 'All')
        for i, name in ((1, 'Module'), (2, 'DirectLeaf'), (3, 'Vendor')):
            child = top.Product.Products.Item(i)
            child.Name = name
            wrap_product(child).position.set_components((1, 0, 0, 0, 1, 0, 0, 0, 1, i * 100, 0, 0))
            fix_reference(top.Product, child, 'Fix_' + name)
        top.Product.Update()
        operation('fixture:save_top', lambda: top.SaveAs(str(top_path)))
        drawing = app.Documents.Add('Drawing')
        owned.append(drawing)
        view = drawing.Sheets.Item(1).Views.Add('ProbeFront')
        view.GenerativeBehavior.Document = top.Product
        view.GenerativeBehavior.DefineFrontView(1., 0., 0., 0., 1., 0.)
        view.GenerativeBehavior.Update()
        view.Texts.Add('VT28 drawing note', 10, 10)
        operation('fixture:save_drawing', lambda: drawing.SaveAs(str(drawing_path)))
        before = snapshot(top)
        assert before['constraints'] and all(c['status'] == 0 for c in before['constraints']), before['constraints']
        checkpoint('fixture:ready', before=before, vendor_sha256_before=vendor_hash)
        close_owned()
        # 基线取完整夹具创建并关闭后，排除初次装配/图纸保存对供应商夹具的影响。
        vendor_hash = hashlib.sha256(vendor_path.read_bytes()).hexdigest()
        checkpoint('fixture:closed', vendor_sha256_migration_baseline=vendor_hash)

        # 从此处起，文件名/目录名使用文件系统重命名，内容只经CATIA普通Save写入。
        migration_start = time.perf_counter()
        new_dir = base / 'VE01_project'
        operation('rename:project_directory', lambda: old_dir.rename(new_dir))
        operation('rename:parts_directory', lambda: (new_dir / 'VT28_parts').rename(new_dir / 'VE01_parts'))
        old_files = [leaf_path, sub_path, top_path, drawing_path]
        mapping = {}
        for old in old_files:
            current = new_dir / str(old.relative_to(old_dir)).replace('VT28_parts', 'VE01_parts')
            target = current.with_name(current.name.replace('VL01', 'VE01').replace('VT28', 'VE01'))
            operation('rename:' + current.name, lambda current=current, target=target: current.rename(target))
            mapping[str(old)] = str(target)
        checkpoint('rename:done', mapping=mapping)

        def open_new(old):
            doc = app.Documents.Open(mapping[str(old)])
            owned.append(doc)
            return doc

        leaf = operation('load:new_leaf', lambda: open_new(leaf_path))
        sub = operation('load:new_sub', lambda: open_new(sub_path))
        def reconnect(parent, owner_node):
            # 保留已正确解析的新引用，只对仍指向旧路径的组件调用替换接口。
            for i in range(1, parent.Products.Count + 1):
                component = parent.Products.Item(i)
                try:
                    full_name = str(component.ReferenceProduct.Parent.FullName)
                except Exception:
                    # 直接改名后引用可能已经断开，只能依据改名前清单定位，不能现查FullName。
                    node = owner_node + '.' + str(i - 1)
                    full_name = next(item['path'] for item in before['nodes'] if item['node'] == node)
                if full_name in mapping:
                    name = component.Name
                    parent.Products.ReplaceComponent(component, mapping[full_name], True)
                    report['replacement_calls'].append({'owner': parent.PartNumber, 'instance': name, 'old': full_name, 'new': mapping[full_name]})
        operation('reconnect:sub', lambda: reconnect(sub.Product, '0.0'))
        top = operation('load:new_top', lambda: open_new(top_path))
        operation('reconnect:top', lambda: reconnect(top.Product, '0'))
        drawing = operation('load:new_drawing', lambda: open_new(drawing_path))
        view = drawing.Sheets.Item(1).Views.Item('ProbeFront')
        try:
            source = str(view.GenerativeBehavior.Document.ReferenceProduct.Parent.FullName)
        except Exception:
            source = str(top_path)  # 来自本夹具预先记录的生成源，不猜用户图纸的目标。
        checkpoint('loaded:new_paths', drawing_source_before_rebind=source)
        if source in mapping:
            view.GenerativeBehavior.Document = top.Product
            report['drawing_rebound'] = True
        for doc in (leaf, sub, top):
            doc.Product.PartNumber = doc.Product.PartNumber.replace('VL01', 'VE01').replace('VT28', 'VE01')
        view.Texts.Item(1).Text = 'VE01 drawing note'
        for doc in (leaf, sub, top):
            doc.Product.Update()
            operation('save:' + doc.Name, doc.Save)
        view.GenerativeBehavior.Update()
        operation('save:' + drawing.Name, drawing.Save)
        checkpoint('migration:saved', migration_seconds=time.perf_counter() - migration_start,
                   vendor_sha256_after_save=hashlib.sha256(vendor_path.read_bytes()).hexdigest())
        close_owned()
        checkpoint('migration:closed', vendor_sha256_after_close=hashlib.sha256(vendor_path.read_bytes()).hexdigest())

        # 不预加载任何零件：直接打开顶层，再验证图纸。
        top = operation('cold_reopen:top', lambda: open_new(top_path))
        after = snapshot(top)
        for old_node, new_node in zip(before['nodes'], after['nodes']):
            assert old_node['node'] == new_node['node']
            expected = mapping.get(old_node['path'], old_node['path'])
            assert new_node['path'] == expected, (old_node, new_node)
            expected_pn = (old_node['part_number'].replace('VL01', 'VE01').replace('VT28', 'VE01')
                           if old_node['path'] in mapping else old_node['part_number'])
            assert new_node['part_number'] == expected_pn, (old_node, new_node)
            assert max(abs(a - b) for a, b in zip(old_node['position'], new_node['position'])) < 1e-8
            if old_node['node'] != '0':
                assert old_node['name'] == new_node['name'], (old_node, new_node)
            assert [p['name'] for p in old_node['publications']] == [p['name'] for p in new_node['publications']]
            for old_pub, new_pub in zip(old_node['publications'], new_node['publications']):
                expected_target = (old_pub['target'].replace('VL01', 'VE01').replace('VT28', 'VE01')
                                   if old_node['path'] in mapping else old_pub['target'])
                assert new_pub['target'] == expected_target, (old_pub, new_pub)
        assert len(before['nodes']) == len(after['nodes'])
        assert before['constraints'] == after['constraints'], (before['constraints'], after['constraints'])
        assert abs(before['mass'] - after['mass']) < 1e-9
        drawing = operation('cold_reopen:drawing', lambda: open_new(drawing_path))
        view = drawing.Sheets.Item(1).Views.Item('ProbeFront')
        drawing_source = str(view.GenerativeBehavior.Document.ReferenceProduct.Parent.FullName)
        assert drawing_source == mapping[str(top_path)], drawing_source
        assert view.Texts.Item(1).Text == 'VE01 drawing note'
        view.GenerativeBehavior.Update()
        assert not old_dir.exists()
        assert all(not Path(path).exists() for path in old_files)
        assert vendor_path.is_file()
        vendor_after = hashlib.sha256(vendor_path.read_bytes()).hexdigest()
        checkpoint('verification:vendor_hash', vendor_sha256_after=vendor_after)
        assert vendor_after == vendor_hash
        checkpoint('verified', after=after, drawing_source=drawing_source, vendor_sha256_after=vendor_after, passed=True)
    except Exception as exc:
        checkpoint('failed', error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        try:
            close_owned()
        finally:
            app.DisplayFileAlerts = alerts
            if previous is not None:
                previous.Activate()
        print('Rename reference probe report: ' + str(report_path))
