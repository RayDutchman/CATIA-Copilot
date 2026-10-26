"""单次 CATIA 请求中的 BOM 采集与字段编辑，不依赖 Qt。"""

from __future__ import annotations

from pathlib import Path

from catia_copilot.catia.bom_collect_v3 import (
    collect_bom_part_masters,
    flatten_bom_to_summary,
    iter_full_rows,
    iter_hierarchical_rows,
    rename_part_master,
    set_part_master_attr,
)
from catia_copilot.catia.bom_write import write_cell
from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.document_scope import describe_document, resolve_document
from catia_copilot.constants_data import (
    BOM_DEFAULT_COLUMNS,
    BOM_INSTANCE_NAME_COLUMN,
    BOM_READONLY_COLUMNS,
    PART_NUMBER_VALID_PATTERN,
    SOURCE_FROM_DISPLAY,
)


def _text(value) -> str:
    try:
        return str(value or "")
    except Exception:
        return ""


def _product_info(product) -> tuple[str, str, str, str]:
    try:
        pn = _text(product.PartNumber)
    except Exception:
        pn = _text(getattr(product, "Name", "")).rsplit(".", 1)[0]
    filepath = ""
    try:
        filepath = _text(product.ReferenceProduct.Parent.FullName)
    except Exception:
        pass
    try:
        name = _text(product.Name)
    except Exception:
        name = ""
    return pn, filepath, name, Path(filepath).name if filepath else ""


def _tree_targets(root_product) -> list[dict]:
    """按本次文档中的实际出现路径建立目标，不将 COM identity 对外暴露。"""
    targets = []

    def visit(product, path: tuple[int, ...], level: int, ancestors: frozenset[int],
              parent_filepath: str = "", host_file_pn: str = ""):
        pn, filepath, instance_name, filename = _product_info(product)
        embedded = bool(filepath and parent_filepath and filepath == parent_filepath)
        pm_key = f"{pn}:{host_file_pn}" if embedded else pn
        current_host_pn = host_file_pn if embedded else pn
        targets.append({
            "node": ".".join(map(str, path)),
            "level": level,
            "part_number": pn,
            "filepath": filepath,
            "filename": filename,
            "instance_name": instance_name,
            "_pm_key": pm_key,
            "_product": product,
        })
        marker = id(product)
        if marker in ancestors:
            node = ".".join(map(str, path))
            raise ValueError(f"Product tree 在 node {node} 存在循环引用")
        next_ancestors = ancestors | {marker}
        node = ".".join(map(str, path))
        try:
            products = product.Products
            count = products.Count
        except Exception as exc:
            raise ValueError(f"无法读取 Product tree node {node} 的 Products collection: {exc}") from exc
        for index in range(count):
            try:
                child = products.Item(index + 1)
            except Exception as exc:
                raise ValueError(
                    f"无法读取 Product tree node {node}.{index} 的子节点: {exc}"
                ) from exc
            if child is None:
                raise ValueError(f"Product tree node {node}.{index} 返回空子节点")
            visit(child, path + (index,), level + 1, next_ancestors,
                  filepath, current_host_pn)

    visit(root_product, (0,), 0, frozenset(), "", "")
    return targets


def _public_target(target: dict) -> dict:
    return {key: value for key, value in target.items() if not key.startswith("_")}


def _clean_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _prepare(doc=None, active=False):
    app = get_catia_v5_application()
    document = resolve_document(app, doc=doc, active=active)
    return app, document


_RESERVED_COLUMNS = set(BOM_DEFAULT_COLUMNS) | {
    "#", "Filename", "Filepath", BOM_INSTANCE_NAME_COLUMN, "description_inst",
    "targets", "node", "row_count", "view", "document", "success",
    "columns", "custom_columns", "rows",
}


def _validate_custom_columns(custom_columns):
    if custom_columns is None:
        return []
    if not isinstance(custom_columns, (list, tuple)) or not all(
        isinstance(column, str) for column in custom_columns
    ):
        raise TypeError("custom_columns 必须是字符串列表")
    invalid = [column for column in custom_columns
               if column in _RESERVED_COLUMNS or column.startswith("_")]
    if invalid:
        raise ValueError(f"自定义列不能覆盖保留字段：{invalid}")
    return list(dict.fromkeys(custom_columns))


def _collector_options(kwargs):
    options = dict(kwargs)
    signal = options.pop("progress_signal", None)
    callback = options.pop("progress_callback", None)
    if callback is not None and not callable(callback):
        raise TypeError("progress_callback 必须可调用")
    unknown = sorted(set(options) - {"config", "max_depth"})
    if unknown:
        raise TypeError(f"不支持的 BOM 采集参数：{', '.join(unknown)}")
    if signal is not None:
        emit = getattr(signal, "emit", None)
        if not callable(emit):
            raise TypeError("progress_signal 必须提供 emit(message)")
        original_callback = callback

        def report_progress(count):
            if original_callback is not None:
                original_callback(count)
            emit(f"进度：{count}")

        callback = report_progress
    if callback is not None:
        options["progress_callback"] = callback
    return options


def _assert_unambiguous_part_masters(targets):
    files_by_key = {}
    for target in targets:
        files_by_key.setdefault(target["_pm_key"], set()).add(target["filepath"])
    collisions = {key: sorted(paths) for key, paths in files_by_key.items() if len(paths) > 1}
    if collisions:
        key, paths = next(iter(collisions.items()))
        raise ValueError(f"主数据键 {key!r} 对应多个引用文件，BOM 目标歧义：{paths}")


def _collect(document, view, columns, custom_columns, include_assemblies, sort_column, kwargs):
    product = document.Product
    if view not in {"full", "hierarchical", "summary"}:
        raise ValueError("view 必须为 full、hierarchical 或 summary")
    if columns is not None and (not isinstance(columns, (list, tuple))
                                or not all(isinstance(column, str) for column in columns)):
        raise TypeError("columns 必须是字符串列表")
    requested_custom = _validate_custom_columns(custom_columns)
    requested_columns = list(dict.fromkeys(
        list(BOM_DEFAULT_COLUMNS if columns is None else columns) + requested_custom
    ))
    if view == "summary":
        requested_columns = [column for column in requested_columns if column != "Level"]
    hidden_columns = {
        "targets", "node", "row_count", "view", "document", "success",
        "columns", "custom_columns", "rows",
    }
    if hidden_columns.intersection(requested_columns):
        raise ValueError("columns 只能包含业务字段，不能包含定位或结果元数据")
    if any(column.startswith("_") for column in requested_columns):
        raise ValueError("columns 不能包含内部字段")
    collector_kwargs = _collector_options(kwargs)
    root_key, part_masters, inst_index = collect_bom_part_masters(
        None,
        requested_columns,
        requested_custom,
        root_product=product,
        **collector_kwargs,
    )
    targets = _tree_targets(product)
    _assert_unambiguous_part_masters(targets)
    if view == "full":
        rows = iter_full_rows(root_key, part_masters)
    elif view == "hierarchical":
        rows = iter_hierarchical_rows(root_key, part_masters)
    elif view == "summary":
        hierarchical = iter_hierarchical_rows(root_key, part_masters)
        rows = flatten_bom_to_summary(hierarchical, include_assemblies, sort_column)
    row_paths_by_level = {}
    used_full_paths = set()
    for row in rows:
        pn = _text(row.get("Part Number"))
        if view == "summary":
            matches = [target for target in targets if target["part_number"] == pn]
        else:
            level = int(row.get("Level", 0))
            parent_paths = [()] if level == 0 else row_paths_by_level.get(level - 1, [])
            matches = []
            for target in targets:
                path = tuple(int(part) for part in target["node"].split(".")) if target["node"] else ()
                if (target["part_number"] == pn and target["level"] == level
                        and (level == 0 or path[:-1] in parent_paths)):
                    matches.append(target)
        instance_name = _text(row.get("Instance Name"))
        if view == "full":
            available = [target for target in matches if target["node"] not in used_full_paths]
            if instance_name:
                named = [target for target in available if target["instance_name"] == instance_name]
                if named:
                    available = named
            if available:
                matches = available[:1]
                used_full_paths.add(matches[0]["node"])
        if not matches:
            raise ValueError(f"无法将 BOM 行可靠映射到 Product tree：{pn!r}")
        if view != "summary":
            row_paths_by_level[level] = [
                tuple(int(part) for part in target["node"].split(".")) if target["node"] else ()
                for target in matches
            ]
            for stale_level in tuple(row_paths_by_level):
                if stale_level > level:
                    del row_paths_by_level[stale_level]
        row["targets"] = [_public_target(target) for target in matches]
    public_rows = [_clean_row(row) for row in rows]
    return {
        "success": True,
        "view": view,
        "columns": requested_columns,
        "custom_columns": requested_custom,
        "document": describe_document(document).to_dict(),
        "rows": public_rows,
        "row_count": len(public_rows),
        "_part_masters": part_masters,
        "_inst_index": inst_index,
        "_targets": targets,
    }


def collect_bom(
    doc=None,
    active=False,
    view="hierarchical",
    columns=None,
    custom_columns=None,
    include_assemblies=False,
    sort_column=None,
    **kwargs,
) -> dict:
    """采集已打开文档中的 BOM；不负责打开、关闭或保存文档。"""
    _, document = _prepare(doc, active)
    result = _collect(document, view, columns, custom_columns,
                      include_assemblies, sort_column, kwargs)
    for internal_key in ("_part_masters", "_inst_index", "_targets"):
        result.pop(internal_key, None)
    return result


def _path_key(node) -> tuple[int, ...]:
    if not isinstance(node, str) or not node:
        raise ValueError("node 必须是以 0 开始的零基路径字符串")
    try:
        tokens = node.split(".")
        parts = tuple(int(part) for part in tokens)
    except (TypeError, ValueError):
        raise ValueError(f"无效 node 路径：{node!r}") from None
    if any(part < 0 or str(part) != token for part, token in zip(parts, tokens)) or parts[0] != 0:
        raise ValueError(f"无效 node 路径：{node!r}")
    return parts


def _node_index(targets):
    return {tuple(int(x) for x in target["node"].split(".")): target
            for target in targets}


def _ambiguous_pm_key(targets, pm_key):
    files = {target["filepath"] for target in targets if target["_pm_key"] == pm_key}
    return len(files) > 1


def _read_current(product, column, custom_columns):
    if column == BOM_INSTANCE_NAME_COLUMN:
        return _text(getattr(product, "Name", ""))
    if column == "Part Number":
        try:
            return _text(product.ReferenceProduct.PartNumber)
        except Exception:
            return _text(getattr(product, "PartNumber", ""))
    try:
        from catia_copilot.constants_data import PRODUCT_ATTR_READ_MAP
        attr = PRODUCT_ATTR_READ_MAP.get(column)
        if attr:
            return _text(getattr(product.ReferenceProduct, attr))
        if column in custom_columns:
            return _text(product.ReferenceProduct.UserRefProperties.Item(column).Value)
    except Exception:
        pass
    return ""


def _pn_conflicts(part_masters, pm_key, new_pn):
    """沿用工作台主数据 PN 冲突规则，分别检查独立文件和嵌入部件。"""
    current = part_masters.get(pm_key, {})
    if ":" not in pm_key:
        return any(
            key != pm_key and ":" not in key
            and _text(pm.get("part_number")) == new_pn
            for key, pm in part_masters.items()
        )
    host_file_pn = _text(current.get("host_file_pn"))
    host_pm = part_masters.get(host_file_pn)
    if host_pm and _text(host_pm.get("part_number")) == new_pn:
        return True
    return any(
        key != pm_key and _text(pm.get("host_file_pn")) == host_file_pn
        and host_file_pn and _text(pm.get("part_number")) == new_pn
        for key, pm in part_masters.items()
    )


def edit_bom(changes, doc=None, active=False, custom_columns=None, **kwargs) -> dict:
    """按顺序编辑当前打开文档，逐项返回结果，不自动保存。"""
    _, document = _prepare(doc, active)
    custom = _validate_custom_columns(custom_columns)
    collector_kwargs = _collector_options(kwargs)
    root_product = document.Product
    _, part_masters, inst_index = collect_bom_part_masters(
        None, list(BOM_DEFAULT_COLUMNS) + custom, custom,
        root_product=root_product, **collector_kwargs,
    )
    targets = _tree_targets(root_product)
    target_by_path = _node_index(targets)
    pm_key_to_inst_keys = {}
    for inst_key, inst_info in inst_index.items():
        pm_key_to_inst_keys.setdefault(inst_info["pm_key"], []).append(inst_key)

    results = []
    successful_assignments = {}
    for change in changes:
        result = ({key: change.get(key) for key in ("node", "scope", "column", "value")}
                  if isinstance(change, dict) else {"change": change})
        result["status"] = "failed"
        cache_renamed = False
        cached_pn = ""
        pm_key = ""
        try:
            if not isinstance(change, dict):
                raise ValueError("change 必须是对象")
            node = _path_key(change.get("node"))
            scope, column, value = change.get("scope"), change.get("column"), change.get("value")
            if not all(isinstance(item, str) for item in (scope, column, value)):
                raise ValueError("scope、column、value 必须是字符串")
            target = target_by_path.get(node)
            if target is None:
                raise ValueError("node 路径不存在")
            product = target["_product"]
            pn = target["part_number"]
            pm_key = target.get("_pm_key", pn)
            if scope == "instance":
                if column != BOM_INSTANCE_NAME_COLUMN:
                    raise ValueError("instance scope 仅支持 Instance Name")
                if target["level"] == 0:
                    raise ValueError("根节点没有 Instance Name，不能编辑")
            elif scope == "master":
                if (column in BOM_READONLY_COLUMNS
                        or column in {BOM_INSTANCE_NAME_COLUMN, "description_inst"}
                        or column.startswith("_")):
                    raise ValueError(f"master 字段不可写：{column}")
                if column not in {"Part Number", "Nomenclature", "Revision", "Definition", "Source", "Description"} and column not in custom:
                    raise ValueError(f"未知或只读字段：{column}")
                if pm_key not in part_masters:
                    raise ValueError("无法唯一定位主数据")
                if _ambiguous_pm_key(targets, pm_key):
                    raise ValueError(f"主数据键 {pm_key!r} 对应多个引用文件，目标歧义")
                if column == "Part Number":
                    if not value.strip() or not PART_NUMBER_VALID_PATTERN.fullmatch(value):
                        raise ValueError('Part Number 不能为空或包含工作台不允许的字符')
                    if _pn_conflicts(part_masters, pm_key, value):
                        raise ValueError(f"Part Number 冲突：{value}")
            else:
                raise ValueError("scope 必须为 master 或 instance")

            old_value = _read_current(product, column, custom)
            result["old_value"] = old_value
            assignment_key = (pm_key, column)
            if scope == "master" and successful_assignments.get(assignment_key) == value:
                result["new_value"] = value
                result["status"] = "success"
                result["message"] = "相同主数据字段和值已在本次请求中写入"
                results.append(result)
                continue
            if scope == "master" and column == "Part Number":
                cached_pn = _text(part_masters[pm_key].get("part_number"))
                if not rename_part_master(part_masters, pm_key_to_inst_keys, pm_key, value):
                    raise ValueError(f"无法更新 Part Number 主数据缓存：{pm_key}")
                cache_renamed = True
            if not write_cell(product, column, value, custom):
                if cache_renamed:
                    restored = rename_part_master(part_masters, pm_key_to_inst_keys, pm_key, cached_pn)
                    cache_renamed = False
                    if not restored:
                        raise RuntimeError(f"CATIA 写入失败且无法恢复 PN 缓存：{pm_key}")
                raise ValueError("CATIA 字段写入失败或字段只读")
            cache_renamed = False
            result["new_value"] = value
            result["status"] = "success"
            if scope == "master":
                if pm_key in part_masters:
                    if column != "Part Number":
                        stored_value = SOURCE_FROM_DISPLAY.get(value, value) if column == "Source" else value
                        set_part_master_attr(part_masters, pm_key, column, stored_value)
                    successful_assignments[assignment_key] = value
        except Exception as exc:
            if cache_renamed and pm_key in part_masters:
                if not rename_part_master(part_masters, pm_key_to_inst_keys, pm_key, cached_pn):
                    exc = RuntimeError(f"{exc}；同时无法恢复 PN 缓存：{pm_key}")
            result["status"] = "failed"
            result["error"] = str(exc)
        results.append(result)
    applied = sum(item["status"] == "success" for item in results)
    failed = len(results) - applied
    return {
        "success": failed == 0,
        "applied_count": applied,
        "failed_count": failed,
        "results": results,
        "document": describe_document(document).to_dict(),
    }
