"""
CATIA Copilot AI Agent 工具定义模块。

将现有 catia/ 层的函数包装为 OpenAI function calling 格式：
  - tools_schema : 发给 LLM 的 JSON Schema 列表
  - tools_map    : {工具名: 包装函数} 映射，供 AgentWorker 调用

所有包装函数均在主线程执行（COM STA 要求），通过 progress_signal 回调
向 UI 推送进度信息。包装函数返回值统一为 str（JSON 或纯文本），
不含不可序列化的 COM 对象。
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 进度回调工厂
# ---------------------------------------------------------------------------

def make_progress_callback(emit_fn: Callable[[str], None]) -> Callable:
    """
    返回一个通用进度回调函数。
    emit_fn 是 Signal.emit，接受一个字符串参数。
    支持 (index,) 和 (index, total) 两种签名。
    """
    def _cb(*args):
        if len(args) == 1:
            emit_fn(f"进度：{args[0]}")
        elif len(args) == 2:
            emit_fn(f"进度：{args[0]}/{args[1]}")
        else:
            emit_fn(str(args))
    return _cb


def make_str_progress_callback(emit_fn: Callable[[str], None]) -> Callable:
    """返回接受字符串参数的进度回调（用于 find_dependencies 等）。"""
    def _cb(msg: str):
        emit_fn(str(msg))
    return _cb


# ---------------------------------------------------------------------------
# 工具包装函数
# ---------------------------------------------------------------------------

def _wrap(fn: Callable, *args, **kwargs) -> str:
    """统一异常捕获包装，返回 JSON 字符串。"""
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception:
        tb = traceback.format_exc()
        logger.error("工具调用异常：%s", tb)
        return json.dumps({"error": tb}, ensure_ascii=False)


# --- 1. check_catia_connection ---

def tool_check_catia_connection(**_kwargs) -> str:
    from catia_copilot.utils import check_catia_connection
    status = check_catia_connection()
    return json.dumps({"status": status}, ensure_ascii=False)


# --- 2. diagnose_catia_connection ---

def tool_diagnose_catia_connection(**_kwargs) -> str:
    from catia_copilot.utils import diagnose_catia_connection
    info = diagnose_catia_connection()
    # 过滤掉不可序列化的字段
    safe = {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    return json.dumps(safe, ensure_ascii=False, indent=2)


# --- 3. open_catia_file ---

def tool_open_catia_file(file_path: str, foreground: bool = True, **_kwargs) -> str:
    from catia_copilot.utils import get_catia_v5_com_dispatch
    from catia_copilot.catia.utils import open_catia_file
    app = get_catia_v5_com_dispatch()
    if app is None:
        return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)
    try:
        doc = open_catia_file(app.Documents, file_path, foreground=foreground)
        name = getattr(doc, "Name", file_path)
        return json.dumps({"success": True, "document_name": name}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# --- 4. collect_bom ---

def tool_collect_bom(
    file_path: str | None = None,
    columns: list[str] | None = None,
    custom_columns: list[str] | None = None,
    summarize: bool = False,
    include_assemblies: bool = False,
    sort_column: str | None = None,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.bom_collect import collect_bom_rows, flatten_bom_to_summary
    from catia_copilot.constants import BOM_DEFAULT_COLUMNS

    cols = columns or BOM_DEFAULT_COLUMNS
    custom_cols = custom_columns or []
    cb = make_progress_callback(progress_signal.emit) if progress_signal else None

    rows = collect_bom_rows(file_path, cols, custom_cols, progress_callback=cb)

    # 过滤内部键（以 _ 开头），只保留可读字段
    _INTERNAL = {"_filepath", "_not_found", "_no_file", "_unreadable",
                 "_placement", "_mass_props", "_root_mp", "_meas_failed"}
    clean_rows = [
        {k: v for k, v in row.items() if k not in _INTERNAL}
        for row in rows
    ]

    # summarize=True 时折叠为平面汇总（去重 + 累计数量）
    if summarize:
        clean_rows = flatten_bom_to_summary(clean_rows, include_assemblies, sort_column)

    return json.dumps(
        {"row_count": len(clean_rows), "rows": clean_rows},
        ensure_ascii=False,
        default=str,
    )


# --- 5. export_bom_to_excel ---

def tool_export_bom_to_excel(
    file_paths: list[str | None],
    output_folder: str | None = None,
    columns: list[str] | None = None,
    custom_columns: list[str] | None = None,
    summarize: bool = False,
    summary_include_assemblies: bool = False,
    summary_sort_column: str | None = None,
    output_format: str = "xlsx",
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.bom_export import export_bom_to_excel

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    paths = export_bom_to_excel(
        file_paths,
        output_folder=output_folder,
        columns=columns,
        custom_columns=custom_columns,
        row_progress_callback=cb,
        summarize=summarize,
        summary_include_assemblies=summary_include_assemblies,
        summary_sort_column=summary_sort_column,
        output_format=output_format,
    )
    return json.dumps(
        {"success": True, "exported_files": [str(p) for p in paths]},
        ensure_ascii=False,
    )


# --- 6. write_bom_to_catia ---

def tool_write_bom_to_catia(
    file_path: str | None = None,
    pn_data: dict[str, dict[str, str]] = None,
    custom_columns: list[str] | None = None,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.bom_write import write_bom_to_catia

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    write_bom_to_catia(file_path, pn_data, custom_columns or [], progress_callback=cb)
    return json.dumps({"success": True}, ensure_ascii=False)


# --- 7. convert_to_pdf ---

def tool_convert_to_pdf(
    file_paths: list[str],
    output_folder: str | None = None,
    prefix: str = "DR_",
    suffix: str = "",
    update_before_export: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.conversion import convert_drawing_to_pdf

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    count = convert_drawing_to_pdf(
        file_paths,
        output_folder=output_folder,
        prefix=prefix,
        suffix=suffix,
        progress_callback=cb,
        update_before_export=update_before_export,
    )
    return json.dumps(
        {"success": True, "exported_count": count, "total": len(file_paths)},
        ensure_ascii=False,
    )


# --- 8. convert_to_step ---

def tool_convert_to_step(
    file_paths: list[str],
    output_folder: str | None = None,
    prefix: str = "MD_",
    suffix: str = "",
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.conversion import convert_part_to_step

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    count = convert_part_to_step(
        file_paths,
        output_folder=output_folder,
        prefix=prefix,
        suffix=suffix,
        progress_callback=cb,
    )
    return json.dumps(
        {"success": True, "exported_count": count, "total": len(file_paths)},
        ensure_ascii=False,
    )


# --- 9. find_dependencies ---

def tool_find_dependencies(
    target_path: str,
    activate: bool = True,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.dependencies import find_dependencies

    cb = make_str_progress_callback(progress_signal.emit) if progress_signal else None
    deps = find_dependencies(target_path, progress_callback=cb, activate=activate)
    return json.dumps(
        {"dependency_count": len(deps), "dependencies": deps},
        ensure_ascii=False,
    )


# --- 10. find_reverse_dependencies ---

def tool_find_reverse_dependencies(
    target_path: str,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.dependencies import find_reverse_dependencies

    cb = make_str_progress_callback(progress_signal.emit) if progress_signal else None
    deps = find_reverse_dependencies(target_path, progress_callback=cb)
    return json.dumps(
        {"dependency_count": len(deps), "dependencies": deps},
        ensure_ascii=False,
    )


# --- 11. collect_mass_props ---

def tool_collect_mass_props(
    file_path: str | None = None,
    read_mode: str = "all",
    skip_hidden: bool = False,
    summary_only: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.mass_props_collect import collect_mass_props_rows

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    rows = collect_mass_props_rows(
        file_path,
        progress_callback=cb,
        read_mode=read_mode,
        skip_hidden=skip_hidden,
    )

    # 过滤内部键和不可序列化字段
    _INTERNAL = {"_filepath", "_placement", "_mass_props", "_root_mp",
                 "_not_found", "_no_file", "_unreadable", "_meas_failed"}
    clean_rows = [
        {k: v for k, v in row.items() if k not in _INTERNAL}
        for row in rows
    ]

    # summary_only=True 时只返回根节点（Level==0）的质量特性摘要
    if summary_only:
        root_rows = [r for r in clean_rows if r.get("Level") == 0]
        if root_rows:
            r = root_rows[0]
            summary = {k: r.get(k) for k in (
                "Weight", "CogX", "CogY", "CogZ",
                "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz",
            )}
            return json.dumps(summary, ensure_ascii=False, default=str)
        # 根节点不存在时退化为完整返回
    return json.dumps(
        {"row_count": len(clean_rows), "rows": clean_rows},
        ensure_ascii=False,
        default=str,
    )


# --- 12. generate_drawing ---

def tool_generate_drawing(
    template_path: str,
    property_values: dict[str, str] | None = None,
    property_names: list[str] | None = None,
    **_kwargs,
) -> str:
    """
    从当前活动零件/装配体生成图纸。
    property_values: {属性名: 属性值} 字典，AI 需预先提供所有需要的属性值。
    property_names: 要同步的属性名列表，默认 ["物料编码","材料","重量"]。
    """
    from catia_copilot.catia.drawing_operations import generate_drawing

    values = property_values or {}

    def _input_callback(prop_name: str, current_value: str) -> tuple[str, bool]:
        # 如果 AI 提供了该属性的值，直接使用；否则保留当前值
        new_val = values.get(prop_name, current_value)
        return new_val, True  # (新值, 确认)

    result = generate_drawing(
        template_path,
        property_names=property_names,
        input_callback=_input_callback,
    )
    # 过滤掉 COM 对象字段
    safe_result = {k: v for k, v in result.items() if k != "drawing_doc"}
    return json.dumps(safe_result, ensure_ascii=False)


# --- 13. refresh_drawing ---

def tool_refresh_drawing(
    property_values: dict[str, str] | None = None,
    property_names: list[str] | None = None,
    **_kwargs,
) -> str:
    """
    刷新当前活动图纸的参数（从对应零件同步属性）。
    property_values: {属性名: 属性值} 字典，AI 需预先提供所有需要的属性值。
    """
    from catia_copilot.catia.drawing_operations import refresh_drawing

    values = property_values or {}

    def _input_callback(prop_name: str, current_value: str) -> tuple[str, bool]:
        new_val = values.get(prop_name, current_value)
        return new_val, True

    result = refresh_drawing(
        property_names=property_names,
        input_callback=_input_callback,
    )
    return json.dumps(result, ensure_ascii=False)


# --- 14. apply_part_template ---

def tool_apply_part_template(
    file_paths: list[str],
    keep_open: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:
    from catia_copilot.catia.template import apply_part_template

    cb_list: list[str] = []

    def _cb(index: int, total: int):
        msg = f"进度：{index}/{total}"
        cb_list.append(msg)
        if progress_signal:
            progress_signal.emit(msg)

    success_count, failed = apply_part_template(
        file_paths,
        progress_callback=_cb,
        keep_open=keep_open,
    )
    return json.dumps(
        {
            "success_count": success_count,
            "failed_count": len(failed),
            "failed_messages": failed,
        },
        ensure_ascii=False,
    )


# --- 15. get_open_documents ---

def tool_get_open_documents(**_kwargs) -> str:
    """返回当前 CATIA 中所有已打开文档的完整路径列表及活动文档路径。"""
    from catia_copilot.utils import get_catia_v5_com_dispatch

    app = get_catia_v5_com_dispatch()
    if app is None:
        return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)

    # 文档类型通过后缀判断（大小写不敏感）
    _EXT_TYPE = {
        ".catpart":    "PartDocument",
        ".catproduct": "ProductDocument",
        ".catdrawing": "DrawingDocument",
    }

    open_docs: list[dict] = []
    try:
        docs = app.Documents
        for i in range(1, docs.Count + 1):
            try:
                doc = docs.Item(i)
                path = doc.FullName
                ext  = path.lower().rsplit(".", 1)[-1]
                doc_type = _EXT_TYPE.get(f".{ext}", "Other")
                open_docs.append({
                    "name": doc.Name,
                    "path": path,
                    "type": doc_type,
                })
            except Exception:
                continue
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    active_path: str | None = None
    try:
        active_path = app.ActiveDocument.FullName
    except Exception:
        pass  # 无活动文档时正常

    return json.dumps(
        {"active_document": active_path, "open_documents": open_docs},
        ensure_ascii=False,
    )


# --- 16. save_catia_document ---

def tool_save_catia_document(
    file_path: str | None = None,
    **_kwargs,
) -> str:
    """保存 CATIA 文档。file_path 为 null 时保存当前活动文档。"""
    from catia_copilot.utils import get_catia_v5_com_dispatch

    app = get_catia_v5_com_dispatch()
    if app is None:
        return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)

    try:
        if file_path is None:
            doc = app.ActiveDocument
        else:
            docs = app.Documents
            doc = None
            file_path_lower = file_path.lower()
            for i in range(1, docs.Count + 1):
                try:
                    d = docs.Item(i)
                    if d.FullName.lower() == file_path_lower:
                        doc = d
                        break
                except Exception:
                    continue
            if doc is None:
                return json.dumps(
                    {"error": f"文档未在 CATIA 中打开：{file_path}"},
                    ensure_ascii=False,
                )
        saved_path = doc.FullName
        doc.Save()
        return json.dumps({"success": True, "saved_path": saved_path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# --- 19. update_memory ---

def tool_update_memory(
    content: str,
    mode: str = "append",
    **_kwargs,
) -> str:
    """
    更新全局长期记忆文件（memory.md）。
    AI 在发现值得跨会话记住的信息时主动调用，
    例如用户的零件编号规范、常用目录路径、偏好设置等。

    mode:
      "append"  — 追加到文件末尾（默认）
      "prepend" — 插入到文件开头
      "replace" — 完全替换文件内容
    """
    from pathlib import Path as _Path

    # memory.md 位于项目根目录（此文件在 catia_copilot/ai/tools.py）
    memory_path = _Path(__file__).parent.parent.parent / "memory.md"

    try:
        if mode == "replace":
            memory_path.write_text(content, encoding="utf-8")
        elif mode == "prepend":
            existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
            memory_path.write_text(content + "\n\n" + existing, encoding="utf-8")
        else:  # append（默认）
            existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
            sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
            memory_path.write_text(existing + sep + content, encoding="utf-8")
        return json.dumps({"success": True, "mode": mode}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# --- 17. find_part_for_drawing ---

def tool_find_part_for_drawing(
    drawing_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    **_kwargs,
) -> str:
    """给定 CATDrawing 路径，启发式查找对应的 CATPart/CATProduct 文件路径列表。"""
    from catia_copilot.catia.dependencies import find_part_for_drawing

    matches = find_part_for_drawing(
        drawing_path,
        strategies=strategies,
        max_parent_levels=max_parent_levels,
    )
    return json.dumps({"matches": matches}, ensure_ascii=False)


# --- 18. find_drawing_for_part ---

def tool_find_drawing_for_part(
    part_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    **_kwargs,
) -> str:
    """给定 CATPart/CATProduct 路径，启发式查找对应的 CATDrawing 文件路径列表。"""
    from catia_copilot.catia.dependencies import find_drawing_for_part

    matches = find_drawing_for_part(
        part_path,
        strategies=strategies,
        max_parent_levels=max_parent_levels,
    )
    return json.dumps({"matches": matches}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

# 供 AgentWorker 调用的函数映射
# 注意：progress_signal 参数由 AgentWorker 在调用时注入，不来自 LLM
tools_map: dict[str, Callable] = {
    "check_catia_connection":      tool_check_catia_connection,
    "diagnose_catia_connection":   tool_diagnose_catia_connection,
    "open_catia_file":             tool_open_catia_file,
    "collect_bom":                 tool_collect_bom,
    "export_bom_to_excel":         tool_export_bom_to_excel,
    "write_bom_to_catia":          tool_write_bom_to_catia,
    "convert_to_pdf":              tool_convert_to_pdf,
    "convert_to_step":             tool_convert_to_step,
    "find_dependencies":           tool_find_dependencies,
    "find_reverse_dependencies":   tool_find_reverse_dependencies,
    "collect_mass_props":          tool_collect_mass_props,
    "generate_drawing":            tool_generate_drawing,
    "refresh_drawing":             tool_refresh_drawing,
    "apply_part_template":         tool_apply_part_template,
    "get_open_documents":          tool_get_open_documents,
    "save_catia_document":         tool_save_catia_document,
    "find_part_for_drawing":       tool_find_part_for_drawing,
    "find_drawing_for_part":       tool_find_drawing_for_part,
    "update_memory":               tool_update_memory,
}

# 发给 LLM 的 JSON Schema 列表
tools_schema: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_catia_connection",
            "description": "检查 CATIA V5 的连接状态。返回 connected/broken/disconnected。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_catia_connection",
            "description": "对 CATIA V5 连接进行详细诊断，返回版本、文档数、进程状态等信息。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_catia_file",
            "description": "在 CATIA 中打开指定文件（CATPart / CATProduct / CATDrawing）。若文件已打开则切换到该文档。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的完整 Windows 路径，例如 D:\\\\project\\\\part.CATPart",
                    },
                    "foreground": {
                        "type": "boolean",
                        "description": "是否将 CATIA 窗口置于前台，默认 true",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_bom",
            "description": (
                "采集 CATIA CATProduct 的 BOM（物料清单）数据，返回层级零件清单。"
                "file_path 为 null 时使用当前活动文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要采集的列名列表，默认使用系统预设列",
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "额外的用户自定义属性列名列表",
                    },
                    "summarize": {
                        "type": "boolean",
                        "description": "是否折叠为平面汇总（去重 + 累计数量），默认 false",
                    },
                    "include_assemblies": {
                        "type": "boolean",
                        "description": "汇总模式下是否包含装配体行，默认 false（仅在 summarize=true 时生效）",
                    },
                    "sort_column": {
                        "type": ["string", "null"],
                        "description": "汇总模式下的排序列名，默认按 Part Number 排序（仅在 summarize=true 时生效）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_bom_to_excel",
            "description": "将 CATProduct 的 BOM 导出为 Excel 或 CSV 文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": ["string", "null"]},
                        "description": "CATProduct 文件路径列表（传 [null] 使用当前活动文档）",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录路径，不填则与源文件同目录",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要导出的列名列表",
                    },
                    "summarize": {
                        "type": "boolean",
                        "description": "是否导出汇总模式（去重+累计数量），默认 false",
                    },
                    "summary_include_assemblies": {
                        "type": "boolean",
                        "description": "汇总模式下是否包含装配体行，默认 false（仅在 summarize=true 时生效）",
                    },
                    "summary_sort_column": {
                        "type": ["string", "null"],
                        "description": "汇总模式下的排序列名，默认按 Part Number 排序（仅在 summarize=true 时生效）",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["xlsx", "csv"],
                        "description": "输出格式，默认 xlsx",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_bom_to_catia",
            "description": "将编辑后的 BOM 属性批量写回 CATIA 文档（修改零件的用户自定义属性）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "pn_data": {
                        "type": "object",
                        "description": "要写入的数据，格式：{零件编号: {列名: 新值}}",
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "用户自定义属性列名列表",
                    },
                },
                "required": ["pn_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_to_pdf",
            "description": "批量将 CATDrawing 图纸文件导出为 PDF。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATDrawing 文件路径列表",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录，不填则与源文件同目录",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "输出文件名前缀，默认 DR_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "输出文件名后缀，默认空",
                    },
                    "update_before_export": {
                        "type": "boolean",
                        "description": "导出前是否先更新图纸，默认 false",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_to_step",
            "description": "批量将 CATPart / CATProduct 导出为 STEP 格式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATPart 或 CATProduct 文件路径列表",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录，不填则与源文件同目录",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "输出文件名前缀，默认 MD_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "输出文件名后缀，默认空",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_dependencies",
            "description": "正向依赖查询：返回指定文件引用的所有子文档路径列表（需要 CATIA 已打开该文件）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "目标文件的完整路径",
                    },
                    "activate": {
                        "type": "boolean",
                        "description": "是否激活目标文档，默认 true",
                    },
                },
                "required": ["target_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_reverse_dependencies",
            "description": "反向依赖查询：在当前已打开的文档中，查找哪些文档引用了指定文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "目标文件的完整路径",
                    },
                },
                "required": ["target_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_mass_props",
            "description": (
                "采集 CATProduct 产品树中所有零件的质量特性（质量、重心、转动惯量），"
                "坐标已变换到根坐标系。file_path 为 null 时使用当前活动文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "read_mode": {
                        "type": "string",
                        "enum": ["all", "first", "last"],
                        "description": "多实例读取模式：all=全部/first=第一个/last=最后一个，默认 all",
                    },
                    "skip_hidden": {
                        "type": "boolean",
                        "description": "是否跳过隐藏零件，默认 false",
                    },
                    "summary_only": {
                        "type": "boolean",
                        "description": "为 true 时只返回根节点（整体）的质量/重心/惯量摘要，减少 token 消耗，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_drawing",
            "description": (
                "从当前活动的 CATPart 或 CATProduct 生成新图纸。"
                "需要提供图纸模板路径，以及要同步到图纸的属性值。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_path": {
                        "type": "string",
                        "description": "CATDrawing 模板文件的完整路径",
                    },
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要同步的属性名列表，默认 [物料编码, 材料, 重量]",
                    },
                    "property_values": {
                        "type": "object",
                        "description": (
                            "属性名到属性值的映射，例如 {物料编码: ABC-001, 材料: 铝合金}。"
                            "不提供或留空时，保留 CATIA 文档中已有的属性值，不做修改。"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["template_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_drawing",
            "description": (
                "刷新当前活动图纸的参数，从对应零件同步属性到图纸标题栏。"
                "如需修改属性值，通过 property_values 提供。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要同步的属性名列表，默认 [物料编码, 材料, 重量]",
                    },
                    "property_values": {
                        "type": "object",
                        "description": "需要覆盖的属性值，例如 {重量: 1.23}",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_part_template",
            "description": "为 CATPart 文件批量添加标准用户自定义属性（刷写模板）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATPart 文件路径列表",
                    },
                    "keep_open": {
                        "type": "boolean",
                        "description": "处理后是否保持文件在 CATIA 中打开，默认 false",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_documents",
            "description": (
                "返回当前 CATIA 中所有已打开文档的完整路径列表及活动文档路径。"
                "AI 在需要知道当前打开了哪些文件时应首先调用此工具。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_catia_document",
            "description": "保存 CATIA 文档。file_path 为 null 时保存当前活动文档。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "要保存的文档完整路径，传 null 保存当前活动文档",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_part_for_drawing",
            "description": (
                "给定 CATDrawing 路径，启发式查找对应的 CATPart/CATProduct 文件路径列表。"
                "按优先级依次尝试多种策略（PartNumber 参数匹配、同名文件扫描等）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drawing_path": {
                        "type": "string",
                        "description": "CATDrawing 文件的完整路径",
                    },
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "pn_param_open_docs",
                                "pn_param_scan_dirs",
                                "same_name_scan_dirs",
                                "strip_prefix_scan_dirs",
                                "doc_file_links",
                            ],
                        },
                        "description": "要使用的查找策略列表，默认使用全部策略",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "目录向上搜索的最大层级数，默认 2",
                    },
                },
                "required": ["drawing_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_drawing_for_part",
            "description": (
                "给定 CATPart/CATProduct 路径，启发式查找对应的 CATDrawing 文件路径列表。"
                "按优先级依次尝试多种策略（PartNumber 参数匹配、同名文件扫描等）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part_path": {
                        "type": "string",
                        "description": "CATPart 或 CATProduct 文件的完整路径",
                    },
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "pn_param_open_drws",
                                "pn_param_scan_drws",
                                "same_name_scan_dirs",
                                "strip_prefix_scan_dirs",
                                "doc_file_links",
                            ],
                        },
                        "description": "要使用的查找策略列表，默认使用全部策略",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "目录向上搜索的最大层级数，默认 2",
                    },
                },
                "required": ["part_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": (
                "更新全局长期记忆文件（memory.md）。"
                "当发现值得跨会话记住的信息时调用，"
                "例如用户的零件编号规范、常用目录路径、偏好设置等。"
                "记忆内容会在后续所有会话中自动注入 system prompt。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要写入的内容（Markdown 格式）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["append", "prepend", "replace"],
                        "description": (
                            "写入模式：append=追加到末尾（默认），"
                            "prepend=插入到开头，replace=完全替换"
                        ),
                    },
                },
                "required": ["content"],
            },
        },
    },
]
