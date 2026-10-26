"""CATIA 工具服务与业务分发，独立于聊天界面。"""

from __future__ import annotations
import fnmatch
import importlib.util
import json
import logging
import sys
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable
from catia_copilot.catia.bom_service import collect_bom, edit_bom
from catia_copilot.catia.bom_output import export_bom
from catia_copilot.catia.bom_write import write_bom_to_catia
from catia_copilot.catia.conversion import convert_drawing_to_pdf, convert_part_to_step
from catia_copilot.catia.dependencies import (
    find_dependencies,
    find_drawing_for_part,
    find_part_for_drawing,
    find_reverse_dependencies,
)
from catia_copilot.catia.document import (
    get_document_properties,
    get_document_type,
    set_document_properties,
)
from catia_copilot.catia.drawing_operations import generate_drawing, refresh_drawing
from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.document_scope import describe_document, matches_document
from catia_copilot.catia.modeling_verifier import verify_model_state
from catia_copilot.catia.mass_props_collect import collect_mass_props_rows
from catia_copilot.catia.modeling import (
    ModelingCancelledError,
    ModelingContext,
    ModelingStepError,
)
from catia_copilot.catia.template import apply_part_template
from catia_copilot.constants_data import (
    PRESET_USER_REF_PROPERTIES,
    PRESET_USER_REF_PROPERTY_OPTIONS,
)
from catia_copilot.utils import (
    check_catia_connection,
    diagnose_catia_connection,
    get_catia_v5_com_dispatch,
    open_catia_file,
)

logger = logging.getLogger(__name__)

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


def _wrap(fn: Callable, *args, **kwargs) -> str:
    """统一异常捕获包装，返回 JSON 字符串。

    供工具函数内部使用，将底层异常转换为 {"error": str} 格式，
    避免 traceback 暴露给 LLM。
    """
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        logger.error("工具调用异常：%s", traceback.format_exc())
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def tool_check_catia_connection(**_kwargs) -> str:
    status = check_catia_connection()
    return json.dumps({"status": status}, ensure_ascii=False)


def tool_diagnose_catia_connection(**_kwargs) -> str:
    info = diagnose_catia_connection()
    # 过滤掉不可序列化的字段
    safe = {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    return json.dumps(safe, ensure_ascii=False, indent=2)


def tool_open_catia_file(file_path: str, foreground: bool = True, **_kwargs) -> str:
    app = get_catia_v5_com_dispatch()
    if app is None:
        return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)
    try:
        doc = open_catia_file(app.Documents, file_path, foreground=foreground)
        name = getattr(doc, "Name", file_path)
        return json.dumps({"success": True, "document_name": name}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def tool_write_bom_to_catia(
    file_path: str | None = None,
    pn_data: dict[str, dict[str, str]] | None = None,
    custom_columns: list[str] | None = None,
    progress_signal=None,
    **_kwargs,
) -> str:
    if not pn_data:
        return json.dumps({"error": "pn_data 不能为空"}, ensure_ascii=False)

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    write_bom_to_catia(file_path, pn_data, custom_columns or [], progress_callback=cb)
    return json.dumps({"success": True}, ensure_ascii=False)


def tool_convert_to_pdf(
    file_paths: list[str],
    output_folder: str | None = None,
    prefix: str = "DR_",
    suffix: str = "",
    update_before_export: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:

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


def tool_convert_to_step(
    file_paths: list[str],
    output_folder: str | None = None,
    prefix: str = "MD_",
    suffix: str = "",
    progress_signal=None,
    **_kwargs,
) -> str:

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


def tool_find_dependencies(
    target_path: str,
    activate: bool = True,
    progress_signal=None,
    **_kwargs,
) -> str:

    cb = make_str_progress_callback(progress_signal.emit) if progress_signal else None
    deps = find_dependencies(target_path, progress_callback=cb, activate=activate)
    return json.dumps(
        {"dependency_count": len(deps), "dependencies": deps},
        ensure_ascii=False,
    )


def tool_find_reverse_dependencies(
    target_path: str,
    progress_signal=None,
    **_kwargs,
) -> str:

    cb = make_str_progress_callback(progress_signal.emit) if progress_signal else None
    deps = find_reverse_dependencies(target_path, progress_callback=cb)
    return json.dumps(
        {"dependency_count": len(deps), "dependencies": deps},
        ensure_ascii=False,
    )


def tool_collect_mass_props(
    file_path: str | None = None,
    read_mode: str = "all",
    skip_hidden: bool = False,
    source: str = "analyze",
    summary_only: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:

    cb = make_progress_callback(progress_signal.emit) if progress_signal else None
    rows = collect_mass_props_rows(
        file_path,
        progress_callback=cb,
        read_mode=read_mode,
        skip_hidden=skip_hidden,
        source=source,
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


def tool_generate_drawing(
    template_path: str,
    property_values: dict[str, str] | None = None,
    property_names: list[str] | None = None,
    **_kwargs,
) -> str:
    """
    从当前活动零件/产品生成图纸。
    property_values: {属性名: 属性值} 字典，AI 需预先提供所有需要的属性值。
    property_names: 要同步的属性名列表，默认 ["物料编码","材料","重量"]。
    """

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


def tool_refresh_drawing(
    property_values: dict[str, str] | None = None,
    property_names: list[str] | None = None,
    **_kwargs,
) -> str:
    """
    刷新当前活动图纸的参数（从对应零件同步属性）。
    property_values: {属性名: 属性值} 字典，AI 需预先提供所有需要的属性值。
    """

    values = property_values or {}

    def _input_callback(prop_name: str, current_value: str) -> tuple[str, bool]:
        new_val = values.get(prop_name, current_value)
        return new_val, True

    result = refresh_drawing(
        property_names=property_names,
        input_callback=_input_callback,
    )
    return json.dumps(result, ensure_ascii=False)


def tool_apply_part_template(
    file_paths: list[str],
    keep_open: bool = False,
    progress_signal=None,
    **_kwargs,
) -> str:

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


def tool_get_open_documents(**_kwargs) -> str:
    """返回当前 CATIA 中所有已打开文档的完整路径列表及活动文档路径。"""

    app = get_catia_v5_com_dispatch()
    if app is None:
        return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)

    open_docs: list[dict] = []
    try:
        docs = app.Documents
        for i in range(1, docs.Count + 1):
            try:
                doc = docs.Item(i)
                open_docs.append({
                    "name": doc.Name,
                    "path": doc.FullName,
                    "type": get_document_type(doc),
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


def tool_save_catia_document(
    file_path: str | None = None,
    **_kwargs,
) -> str:
    """保存 CATIA 文档。file_path 为 null 时保存当前活动文档。"""

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


def tool_get_document_properties(
    file_path: str | None = None,
    **_kwargs,
) -> str:
    """读取 CATIA 文档的标准属性和用户自定义属性。

    file_path 为 null 时读取当前活动文档。
    """

    # 解析目标路径：null → 活动文档
    if file_path is None:
        app = get_catia_v5_com_dispatch()
        if app is None:
            return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)
        try:
            file_path = app.ActiveDocument.FullName
        except Exception:
            return json.dumps({"error": "当前没有活动文档"}, ensure_ascii=False)

    try:
        result = get_document_properties(file_path)
        return json.dumps(result, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"读取属性失败：{e}"}, ensure_ascii=False)


def tool_set_document_properties(
    file_path: str | None = None,
    standard: dict | None = None,
    user_defined: dict | None = None,
    save: bool = False,
    **_kwargs,
) -> str:
    """写入 CATIA 文档的标准属性和/或用户自定义属性。

    file_path 为 null 时操作当前活动文档。
    """

    if not standard and not user_defined:
        return json.dumps(
            {"error": "standard 和 user_defined 不能同时为空，请至少提供一个要写入的属性"},
            ensure_ascii=False,
        )

    # --- 校验 user_defined 字段名和枚举值 ---
    if user_defined:
        preset_set = set(PRESET_USER_REF_PROPERTIES)
        invalid_keys = [k for k in user_defined if k not in preset_set]
        if invalid_keys:
            return json.dumps(
                {
                    "error": (
                        f"user_defined 包含不在预设列表中的字段名：{invalid_keys}。"
                        f"允许的字段名：{PRESET_USER_REF_PROPERTIES}。"
                        "请告知用户该字段不在预设范围内，并询问确认后再执行。"
                    )
                },
                ensure_ascii=False,
            )
        invalid_values = {}
        for k, v in user_defined.items():
            allowed = PRESET_USER_REF_PROPERTY_OPTIONS.get(k)
            if allowed and v not in allowed:
                invalid_values[k] = {"value": v, "allowed": allowed}
        if invalid_values:
            return json.dumps(
                {
                    "error": (
                        f"user_defined 包含不合法的枚举值：{invalid_values}。"
                        "请使用 allowed 列表中的值。"
                    )
                },
                ensure_ascii=False,
            )

    # 解析目标路径：null → 活动文档
    if file_path is None:
        app = get_catia_v5_com_dispatch()
        if app is None:
            return json.dumps({"error": "CATIA 未连接"}, ensure_ascii=False)
        try:
            file_path = app.ActiveDocument.FullName
        except Exception:
            return json.dumps({"error": "当前没有活动文档"}, ensure_ascii=False)

    try:
        result = set_document_properties(
            file_path,
            standard=standard,
            user_defined=user_defined,
            save=save,
        )
        result["file_path"] = file_path
        return json.dumps(result, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"写入属性失败：{e}"}, ensure_ascii=False)


def tool_find_part_for_drawing(
    drawing_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    **_kwargs,
) -> str:
    """给定 CATDrawing 路径，启发式查找对应的 CATPart/CATProduct 文件路径列表。"""

    matches = find_part_for_drawing(
        drawing_path,
        strategies=strategies,
        max_parent_levels=max_parent_levels,
    )
    return json.dumps({"matches": matches}, ensure_ascii=False)


def tool_find_drawing_for_part(
    part_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    **_kwargs,
) -> str:
    """给定 CATPart/CATProduct 路径，启发式查找对应的 CATDrawing 文件路径列表。"""

    matches = find_drawing_for_part(
        part_path,
        strategies=strategies,
        max_parent_levels=max_parent_levels,
    )
    return json.dumps({"matches": matches}, ensure_ascii=False)


def tool_run_modeling_script(
    script: str,
    target_document_id: str | None = None,
    verification: dict | None = None,
    progress_signal=None,
    cancel_check=None,
    **_kwargs,
) -> str:
    """在 CATIA 中执行 AI 生成的建模脚本（逐步执行 + 结构化反馈）。

    脚本必须包含 ``def build(ctx):`` 函数，通过 ctx 对象调用所有建模 API。
    ctx 会自动记录每一步的执行状态，失败时精确报告哪一步出错。

    执行流程：
      1. 将脚本写入临时文件（便于调试）
      2. 通过 importlib 加载模块
      3. 创建 ModelingContext，调用 build(ctx)
      4. 返回结构化步骤记录 + 最终模型状态

    成功返回结构：
      {
        "success": true,
        "part_name": "...",
        "features": ["凸台.1", ...],
        "steps": [
          {"step": "create_part('底座')", "status": "ok", "features_after": []},
          ...
        ],
        "mass_kg": 0.123,   // 有材料时才有
        "cog_mm":  [x,y,z]
      }

    失败返回结构：
      {
        "success": false,
        "failed_step": "add_pad(depth=20)",
        "error": "COMException: ...",
        "traceback": "...",
        "steps": [
          {"step": "create_part('底座')", "status": "ok", ...},
          {"step": "add_sketch(plane='xy')", "status": "ok", ...},
          {"step": "add_pad(depth=20)", "status": "error", ...}
        ],
        "features_at_failure": ["草图.1"]
      }

    脚本示例::

        def build(ctx):
            part = ctx.create_part("底座")
            sk   = ctx.add_sketch(part, "xy")
            ctx.draw_rect(sk, 0, 0, 100, 50)
            pad  = ctx.add_pad(part, sk, 20)
            ctx.step("底座主体完成")          # 可选里程碑

            sk2  = ctx.add_sketch(part, "xy")
            ctx.draw_circle(sk2, 50, 25, 15)
            ctx.add_pocket(part, sk2, 10)
            ctx.step("挖槽完成")

            ctx.update_part(part)
    """

    run_id = uuid.uuid4().hex
    tmp_dir = Path(tempfile.gettempdir()) / "catia_copilot_modeling"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / f"generated_model_{run_id}.py"
    run_record_path = tmp_dir / f"modeling_run_{run_id}.json"

    def finish(payload: dict) -> str:
        """写入本次运行记录，并返回给 Agent 的 JSON。"""
        payload.setdefault("run_id", run_id)
        payload.setdefault("script_path", str(script_path))
        payload["run_record_path"] = str(run_record_path)
        try:
            run_record_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[MODELING] 写入运行记录失败：%s", exc)
        return json.dumps(payload, ensure_ascii=False)

    status_base = {
        "execution": "not_started",
        "model_update": "not_started",
        "verification": "not_run",
    }
    try:
        document_before = describe_document(get_catia_v5_application().ActiveDocument)
    except Exception as exc:
        return finish(
            {"success": False, "run_id": run_id, "status": status_base,
             "error": f"无法确定 CATIA 活动文档：{exc}"},
        )

    if not matches_document(document_before, target_document_id):
        return finish(
            {
                "success": False,
                "run_id": run_id,
                "status": status_base,
                "error": "目标文档与当前活动文档不匹配，已拒绝执行建模脚本",
                "target_document": document_before.to_dict(),
                "requested_document_id": target_document_id,
            },
        )

    if progress_signal:
        progress_signal.emit("正在执行建模脚本...")

    # 将脚本写入临时文件（每次运行独立保留，便于调试）
    module_name = f"_catia_generated_model_{run_id}"

    try:
        script_path.write_text(script, encoding="utf-8")
    except Exception as e:
        return finish(
            {"success": False, "run_id": run_id, "status": status_base,
             "error": f"写入脚本失败: {e}", "script_path": str(script_path)},
        )

    # 检查脚本中是否定义了 build()
    if "def build(" not in script and "def build (" not in script:
        return finish(
            {"success": False, "run_id": run_id, "status": status_base,
             "error": "脚本中未找到 build(ctx) 函数，请确保脚本包含 def build(ctx): ...",
             "script_path": str(script_path)},
        )

    # 加载模块
    try:
        sys.modules.pop(module_name, None)
        spec   = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {script_path} 创建 ModuleSpec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        err = traceback.format_exc()
        logger.error(f"[MODELING] 脚本加载失败:\n{err}")
        return finish(
            {"success": False, "run_id": run_id, "status": status_base,
             "error": "脚本语法错误或 import 失败", "traceback": err,
             "script_path": str(script_path)},
        )

    if progress_signal:
        progress_signal.emit("脚本已加载，正在调用 build(ctx)...")

    # 创建执行上下文并调用 build(ctx)
    ctx = ModelingContext(cancel_check=cancel_check)
    try:
        module.build(ctx)

    except ModelingCancelledError as mce:
        logger.info("[MODELING] %s", mce)
        return finish(
            {
                "success": False,
                "run_id": run_id,
                "status": {**status_base, "execution": "cancelled"},
                "target_document": document_before.to_dict(),
                "error": str(mce),
                "steps": ctx.steps,
            }
        )

    except ModelingStepError as mse:
        # 步骤级失败：有精确的步骤定位信息
        logger.error(f"[MODELING] 步骤 [{mse.step_name}] 失败:\n{mse.traceback_str}")
        return finish(
            {
                "success":             False,
                "run_id":              run_id,
                "status":              {**status_base, "execution": "failed"},
                "target_document":    document_before.to_dict(),
                "failed_step":         mse.step_name,
                "error":               str(mse.original_error),
                "traceback":           mse.traceback_str,
                "steps":               ctx.steps,
                "features_at_failure": mse.features_at_failure,
                "script_path":         str(script_path),
            },
        )

    except Exception:
        # build() 本身（非 _run 包裹的代码）抛出的异常
        err = traceback.format_exc()
        logger.error(f"[MODELING] build() 执行失败:\n{err}")
        return finish(
            {
                "success":     False,
                "run_id":      run_id,
                "status":      {**status_base, "execution": "failed"},
                "target_document": document_before.to_dict(),
                "error":       "build() 执行失败（非建模步骤异常）",
                "traceback":   err,
                "steps":       ctx.steps,
                "script_path": str(script_path),
            },
        )

    # 执行成功，读取当前零件状态
    if progress_signal:
        progress_signal.emit("build(ctx) 执行完成，正在读取模型状态...")

    try:
        from catia_copilot.catia.modeling import (
            get_active_part,
            get_mass_props,
            list_features,
        )
        part     = get_active_part()
        features = list_features(part)
        mp       = get_mass_props(part)
        document_after = describe_document(get_catia_v5_application().ActiveDocument)
        created_new_document = any(
            step.get("step", "").startswith("create_part(") for step in ctx.steps
        )
        if document_before.key == document_after.key:
            binding_status = "same_document"
        elif created_new_document:
            binding_status = "created_document"
        else:
            binding_status = "document_changed"
        result: dict = {
            "success":     True,
            "run_id":      run_id,
            "status": {
                "execution": "succeeded",
                "model_update": "succeeded",
                "verification": "not_run",
            },
            "document_binding": binding_status,
            "document_before": document_before.to_dict(),
            "document_after": document_after.to_dict(),
            "part_name":   part.name,
            "features":    features,
            "steps":       ctx.steps,
            "script_path": str(script_path),
        }
        if mp:
            result["mass_kg"] = round(mp["mass"], 6)
            result["cog_mm"]  = [round(v, 3) for v in mp["cog"]]
        verification_result = verify_model_state(features, mp, verification)
        result["verification"] = verification_result
        result["status"]["verification"] = verification_result["status"]
    except Exception as e:
        result = {
            "success":     True,
            "run_id":      run_id,
            "status": {
                "execution": "succeeded",
                "model_update": "unknown",
                "verification": "not_run",
            },
            "document_before": document_before.to_dict(),
            "steps":       ctx.steps,
            "script_path": str(script_path),
            "note":        f"build(ctx) 已执行，但读取模型状态失败: {e}",
        }

    return finish(result)


tools_map = {
    'check_catia_connection': tool_check_catia_connection,
    'diagnose_catia_connection': tool_diagnose_catia_connection,
    'open_catia_file': tool_open_catia_file,
    'collect_bom': collect_bom,
    'export_bom_to_excel': export_bom,
    'edit_bom': edit_bom,
    'write_bom_to_catia': tool_write_bom_to_catia,
    'convert_to_pdf': tool_convert_to_pdf,
    'convert_to_step': tool_convert_to_step,
    'find_dependencies': tool_find_dependencies,
    'find_reverse_dependencies': tool_find_reverse_dependencies,
    'collect_mass_props': tool_collect_mass_props,
    'generate_drawing': tool_generate_drawing,
    'refresh_drawing': tool_refresh_drawing,
    'apply_part_template': tool_apply_part_template,
    'get_open_documents': tool_get_open_documents,
    'save_catia_document': tool_save_catia_document,
    'get_document_properties': tool_get_document_properties,
    'set_document_properties': tool_set_document_properties,
    'find_part_for_drawing': tool_find_part_for_drawing,
    'find_drawing_for_part': tool_find_drawing_for_part,
    'run_modeling_script': tool_run_modeling_script,
}

from .inspection_service import inspection_tools
tools_map.update(inspection_tools)
