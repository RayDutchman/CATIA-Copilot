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
# 默认 System Prompt
# 当 ai_config.json 中 system_prompt 为空时自动使用。
# 用户可在配置文件中填写自定义 system_prompt 覆盖此默认值。
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
你是 CATIA Copilot，一个运行在 Windows 上的 CATIA V5 工程助手。
你通过工具调用与 CATIA 交互，帮助工程师完成 BOM 采集、图纸导出、属性写回、依赖查询等任务。

## 基本原则

- **不要在每次操作前主动调用 check_catia_connection**；仅当某个工具返回的错误信息指向 CATIA 无法连接（如 "CATIA 未连接"、COM 连接失败等）时，才调用它来诊断原因。
- 需要文件路径时，先调用 get_open_documents 获取当前已打开文档的准确路径，不要猜测或编造路径。
- 不确定用户意图时，先询问，不要擅自执行可能修改文件的操作。
- 工具返回 error 时，向用户说明原因，不要静默重试。

## 工具使用规范

**BOM 操作**
- collect_bom / export_bom_to_excel：file_path 传 null 使用当前活动文档。
- write_bom_to_catia 写回属性后，必须调用 save_catia_document 保存，否则修改在关闭 CATIA 后丢失。
- write_bom_to_catia 的 custom_columns 需与 collect_bom 时使用的 custom_columns 保持一致。

**图纸操作**
- generate_drawing：调用前确认 active_document 是目标零件/产品，不是图纸。
- refresh_drawing：调用前确认 active_document 是 CATDrawing，不是零件。
- 两者的区别：generate_drawing 从模板创建新图纸；refresh_drawing 刷新已有图纸的标题栏。

**质量特性**
- collect_mass_props 的数据来源由 source 参数控制：
  - "analyze"（默认）：通过 pycatia Analyze API 实时计算，需零件已赋材料；若未赋材料，CATIA 使用默认密度 1000 kg/m³ 计算，结果仍会返回但不代表真实材料。
  - "keep_inertia"：读取 CATIA SPA"测量惯量 + 保持测量"写入的"惯量包络体"参数，需用户预先在零件文档中建立保持测量；若未建立，Weight 等字段为 null。
- 只需要总重量和重心时，传 summary_only=true 减少返回数据量。

**建模**
- 使用 run_modeling_script 工具在 CATIA 中建模。
- 调用前如遇 CATIA 连接错误才检查连接（check_catia_connection），平时无需预先调用。
- 脚本必须包含 `def build(ctx):` 函数（注意：参数是 ctx，不是无参）。
- 通过 ctx 调用所有建模 API，不需要 import 任何模块。
- 所有几何参数单位为 mm。
- build(ctx) 末尾必须调用 ctx.update_part(part) 刷新模型，否则特征不会显示。
- 用 ctx.step("描述") 在关键节点打里程碑标记，便于调试定位。

**build(ctx) 可用 API 清单**

  文档与零件：
    ctx.create_part(name)           → Part    新建 CATPart
    ctx.get_active_part()           → Part    获取当前活动文档的 Part
    ctx.update_part(part)                     刷新模型（必须在末尾调用）
    ctx.save_part(part, path)                 另存为

  草图：
    ctx.add_sketch(part, plane)     → Sketch  plane: "xy"/"yz"/"zx"
    ctx.draw_rect(sk, x, y, w, h)            画矩形（左下角坐标+宽高，mm）
    ctx.draw_circle(sk, cx, cy, r)           画圆（圆心+半径，mm）
    ctx.draw_point(sk, x, y)        → Point2D 画定位点（用于孔定位）

  特征：
    ctx.add_pad(part, sk, depth)    → Pad     拉伸
    ctx.add_pocket(part, sk, depth) → Pocket  挖槽（目前仅支持基准面草图）
    ctx.add_shaft(part, sk, axis="z") → Shaft   旋转体（360°），axis 默认 "z"
    ctx.add_groove(part, sk, axis="z")→ Groove  环形槽（旋转切除，需已有实体），axis 默认 "z"
    ctx.add_hole_from_sketch(part, sk, diameter, depth) → Hole  打孔
    ctx.prepare_revolute_axis(part, axis="z")   提前创建旋转轴线（必须在 add_sketch 之前调用！）

  草图创建：
    ctx.add_sketch(part, plane)              → 在基准平面（"xy"/"yz"/"zx"）上建草图
    ctx.add_sketch_at_height(part, h, base)  → 在距基准平面 h mm 处建偏移草图
      **在已有凸台顶面继续建模时，必须用 add_sketch_at_height，不能用 add_sketch！**
      示例：底层 Pad 高 20mm → ctx.add_sketch_at_height(part, 20, "xy")

  旋转体 / 环形槽约束（重要）：

    【草图平面与旋转轴对应关系】
    - axis="z"：草图在 ZX 平面（plane="zx"）；旋转轴=V(Z)；半径方向=H(-X)；约束 H>0
    - axis="y"：草图在 XY 平面（plane="xy"）；旋转轴=V(Y)；半径方向=H(X)；约束 H>0
    - axis="x"：草图在 XY 平面（plane="xy"）；旋转轴=H(X)；半径方向=V(Y)；约束 V>0

    【draw_rect 坐标说明】
    draw_rect(sk, x, y, w, h) 中 x=H起点, y=V起点, w=H方向宽度, h=V方向高度
    - axis="z"/"y"：x=内径（半径起点，H方向），y=轴向起点（V方向），w=壁厚，h=轴向长度
    - axis="x"：x=轴向起点（H方向），y=内径（半径起点，V方向），w=轴向长度，h=壁厚

    【必须先建轴线再建草图】
    旋转体特征树要求轴线节点在草图节点之前，必须严格按以下顺序：
      1. ctx.prepare_revolute_axis(part, axis)   ← 先建轴线
      2. ctx.add_sketch(part, plane)             ← 再建草图
      3. ctx.draw_rect / ctx.draw_circle ...
      4. ctx.add_shaft(part, sk, axis)
      5. ctx.update_part(part)

    - add_groove 前提：Part 已有实体且已 update_part；环形槽轮廓需位于实体内部

    【示例：外径100 内径50 高度80 绕Y轴旋转圆筒】
        ctx.prepare_revolute_axis(part, "y")
        sk = ctx.add_sketch(part, "xy")
        ctx.draw_rect(sk, 25, 0, 25, 80)  # x=H起=内径25, y=V起=0, w=壁厚25, h=高度80
        shaft = ctx.add_shaft(part, sk, axis="y")

  修饰（当前需要 edge_ref，暂不可用，后续版本开放）：
    ctx.add_edge_fillet(part, edge_ref, radius)
    ctx.add_chamfer(part, edge_ref, length, angle=45)

  阵列（当前方向参数有 bug，暂不推荐使用）：
    ctx.add_rect_pattern(part, feature, nx, ny, dx, dy)
    ctx.add_circ_pattern(part, feature, count, total_angle=360)

  查询（不计入步骤记录）：
    ctx.list_features(part)         → list[str]
    ctx.list_sketches(part)         → list[str]
    ctx.get_mass_props(part)        → dict | None

  里程碑：
    ctx.step("描述")                           打标记，不执行 CATIA 操作

**脚本模板**::

    def build(ctx):
        part = ctx.create_part("零件名")
        sk   = ctx.add_sketch(part, "xy")
        ctx.draw_rect(sk, 0, 0, 100, 50)
        pad  = ctx.add_pad(part, sk, 20)
        ctx.step("主体完成")
        ctx.update_part(part)

**失败处理**
- 执行失败时返回 failed_step（哪步失败）、error（错误信息）、steps（完整步骤记录）。
- 根据 failed_step 和 error 定位问题，修正后重新调用，不要重写无关步骤。

**文档属性**
- get_document_properties：读取单个文档的标准属性（Part Number / Revision / Nomenclature 等）和用户自定义属性。file_path 传 null 读取活动文档。
- set_document_properties：写入单个文档的属性。standard 支持 Part Number / Nomenclature / Revision / Definition / Source / Description（Description 通过 DescriptionRef 写入，经实测可写）；user_defined 只能使用预设字段（见下方约束）。
- 与 write_bom_to_catia 的区别：write_bom_to_catia 遍历整棵产品树批量写回；set_document_properties 只操作单个已打开文档，适合精确修改单个零件。
- 修改属性后若不传 save=true，需手动调用 save_catia_document 保存。

**用户自定义属性约束（重要）**
- user_defined 的键必须来自以下预设列表，不得自行创建新字段名：
  零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注
- 部分字段有固定可选值，必须严格使用，不得填写列表以外的值：
  - 设计状态：草稿 / 冻结 / 发布 / 废弃
  - 存货类别：物料-复材件 / 物料-金属件 / 物料-标准件 / 物料-非标件 / 物料-钣金件 / 物料-塑胶件 / 物料-橡胶件 / 物料-电子件 / 物料-泡沫 / 物料-软包 / 物料-辅材 / 物料-组件 / 物料-虚拟件 / 半成品-组件 / 成品-整机
- 其余字段（零件类型、材料、重量、物料编码、规格型号、备注）为自由文本，无固定可选值。
- 用户要求写入不在预设列表中的字段名时，必须先告知用户该字段不在预设范围内，并询问确认后再执行。

**文件系统**
- read_file / write_file 仅支持文本格式（txt/csv/json/md 等），不支持 CATPart/xlsx 等二进制文件。
- list_directory 可用于批量操作前枚举目标文件，支持 glob 过滤（如 pattern="*.CATDrawing"）。

**依赖查询**
- find_dependencies：正向查询，返回指定文件引用的子文档。
- find_reverse_dependencies：反向查询，在已打开文档中找哪些文档引用了指定文件。
- find_part_for_drawing / find_drawing_for_part：启发式互查，返回候选路径列表，可能为空。

## 回复风格

- 用中文回复。
- 操作完成后简洁说明结果，不要重复罗列工具的返回 JSON。
- 遇到错误时，解释可能的原因并给出下一步建议。
"""

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
    from catia_copilot.utils import open_catia_file
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
    pn_data: dict[str, dict[str, str]] | None = None,
    custom_columns: list[str] | None = None,
    progress_signal=None,
    **_kwargs,
) -> str:
    if not pn_data:
        return json.dumps({"error": "pn_data 不能为空"}, ensure_ascii=False)
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
    source: str = "analyze",
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
    from catia_copilot.catia.document import get_document_type

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


# --- 17. get_document_properties ---

def tool_get_document_properties(
    file_path: str | None = None,
    **_kwargs,
) -> str:
    """读取 CATIA 文档的标准属性和用户自定义属性。

    file_path 为 null 时读取当前活动文档。
    """
    from catia_copilot.utils import get_catia_v5_com_dispatch
    from catia_copilot.catia.document import get_document_properties

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


# --- 18. set_document_properties ---

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
    from catia_copilot.utils import get_catia_v5_com_dispatch
    from catia_copilot.catia.document import set_document_properties
    from catia_copilot.constants import (
        PRESET_USER_REF_PROPERTIES,
        PRESET_USER_REF_PROPERTY_OPTIONS,
    )

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


# --- 20. find_part_for_drawing ---

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


# --- 21. find_drawing_for_part ---

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


# --- 22. read_file ---

# 允许读取的文本文件扩展名白名单
_READ_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".txt", ".csv", ".json", ".jsonl", ".md", ".log",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml",
})
# 单文件读取大小上限（字节）
_READ_MAX_BYTES: int = 1 * 1024 * 1024  # 1 MB


def tool_read_file(
    file_path: str,
    encoding: str = "utf-8",
    max_lines: int | None = None,
    **_kwargs,
) -> str:
    """读取文本文件内容并返回。

    仅支持文本格式（txt/csv/json/md/log/xml/yaml/ini/cfg/toml/jsonl）。
    文件大小超过 1 MB 时拒绝读取。
    """
    from pathlib import Path

    path = Path(file_path)

    # 扩展名白名单检查
    ext = path.suffix.lower()
    if ext not in _READ_ALLOWED_EXTS:
        ext_display = ext if ext else "（无扩展名）"
        return json.dumps(
            {"error": f"不支持的文件类型 {ext_display!r}，允许的类型：{sorted(_READ_ALLOWED_EXTS)}"},
            ensure_ascii=False,
        )

    # 文件存在性检查
    if not path.exists():
        return json.dumps({"error": f"文件不存在：{file_path}"}, ensure_ascii=False)
    if not path.is_file():
        return json.dumps({"error": f"路径不是文件：{file_path}"}, ensure_ascii=False)

    # 大小检查
    size = path.stat().st_size
    if size > _READ_MAX_BYTES:
        return json.dumps(
            {"error": f"文件过大（{size / 1024:.1f} KB），上限 1 MB"},
            ensure_ascii=False,
        )

    try:
        text = path.read_text(encoding=encoding, errors="replace")
    except Exception as e:
        return json.dumps({"error": f"读取失败：{e}"}, ensure_ascii=False)

    lines = text.splitlines()
    total_lines = len(lines)

    if max_lines is not None and max_lines > 0:
        lines = lines[:max_lines]
        truncated = total_lines > max_lines
    else:
        truncated = False

    return json.dumps(
        {
            "file_path": str(path),
            "total_lines": total_lines,
            "returned_lines": len(lines),
            "truncated": truncated,
            "content": "\n".join(lines),
        },
        ensure_ascii=False,
    )


# --- 23. list_directory ---

# 列目录时显示的文件扩展名（其他文件也会列出，只是 type 字段不同）
_CATIA_EXTS: frozenset[str] = frozenset({
    ".catpart", ".catproduct", ".catdrawing",
    ".catvbs", ".catscript", ".catvba",
    ".xlsx", ".csv", ".pdf", ".stp", ".step",
    ".txt", ".json", ".md", ".log",
})


def tool_list_directory(
    dir_path: str,
    pattern: str = "*",
    recursive: bool = False,
    max_depth: int = 2,
    **_kwargs,
) -> str:
    """列出目录内容，返回文件和子目录列表。

    recursive=True 时递归列出，最大深度由 max_depth 控制（默认 2，最小有效值 1）。
    pattern 支持 glob 通配符过滤文件名，例如 *.CATDrawing、*.csv；目录始终列出。
    """
    import fnmatch
    from pathlib import Path

    path = Path(dir_path)

    if not path.exists():
        return json.dumps({"error": f"目录不存在：{dir_path}"}, ensure_ascii=False)
    if not path.is_dir():
        return json.dumps({"error": f"路径不是目录：{dir_path}"}, ensure_ascii=False)

    # max_depth 最小有效值为 1（列出根目录第一层）
    effective_depth = max(1, max_depth)

    entries: list[dict] = []

    def _collect(current: Path, depth: int) -> None:
        if depth > effective_depth:
            return
        try:
            items = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        for item in items:
            is_file = item.is_file()
            # 文件按 pattern 过滤；目录始终列出（用于递归展示结构）
            if is_file and not fnmatch.fnmatch(item.name, pattern):
                continue
            entry: dict = {
                "name":  item.name,
                "path":  str(item),
                "type":  "file" if is_file else "directory",
                "depth": depth,
            }
            if is_file:
                entry["size_kb"] = round(item.stat().st_size / 1024, 1)
                entry["ext"]     = item.suffix.lower()
            entries.append(entry)
            if recursive and not is_file:
                _collect(item, depth + 1)

    _collect(path, 1)

    return json.dumps(
        {
            "dir_path":    str(path),
            "entry_count": len(entries),
            "entries":     entries,
        },
        ensure_ascii=False,
    )


# --- 24. write_file ---

# 允许写入的文本文件扩展名白名单
_WRITE_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".txt", ".csv", ".json", ".jsonl", ".md", ".log",
})
# 单次写入大小上限（字节）
_WRITE_MAX_BYTES: int = 2 * 1024 * 1024  # 2 MB


def tool_write_file(
    file_path: str,
    content: str,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    **_kwargs,
) -> str:
    """将文本内容写入文件。

    仅支持文本格式（txt/csv/json/jsonl/md/log）。
    mode: overwrite=覆盖（默认）；append=追加；create_new=仅当文件不存在时创建。
    写入内容超过 2 MB 时拒绝。
    """
    from pathlib import Path

    path = Path(file_path)

    # 扩展名白名单检查
    if path.suffix.lower() not in _WRITE_ALLOWED_EXTS:
        return json.dumps(
            {"error": f"不支持的文件类型 '{path.suffix}'，允许的类型：{sorted(_WRITE_ALLOWED_EXTS)}"},
            ensure_ascii=False,
        )

    # 内容大小检查
    content_bytes = content.encode(encoding, errors="replace")
    if len(content_bytes) > _WRITE_MAX_BYTES:
        return json.dumps(
            {"error": f"内容过大（{len(content_bytes) / 1024:.1f} KB），上限 2 MB"},
            ensure_ascii=False,
        )

    # mode 检查
    if mode not in ("overwrite", "append", "create_new"):
        return json.dumps(
            {"error": f"无效的 mode '{mode}'，可选值：overwrite / append / create_new"},
            ensure_ascii=False,
        )

    if mode == "create_new" and path.exists():
        return json.dumps(
            {"error": f"文件已存在（mode=create_new 不允许覆盖）：{file_path}"},
            ensure_ascii=False,
        )

    # 自动创建父目录
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return json.dumps({"error": f"创建目录失败：{e}"}, ensure_ascii=False)

    write_mode = "a" if mode == "append" else "w"
    try:
        with path.open(write_mode, encoding=encoding) as f:
            f.write(content)
    except Exception as e:
        return json.dumps({"error": f"写入失败：{e}"}, ensure_ascii=False)

    return json.dumps(
        {
            "success":    True,
            "file_path":  str(path),
            "mode":       mode,
            "size_kb":    round(path.stat().st_size / 1024, 1),
        },
        ensure_ascii=False,
    )



# ---------------------------------------------------------------------------
# 建模工具
# ---------------------------------------------------------------------------

def tool_run_modeling_script(
    script: str,
    progress_signal=None,
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
    import importlib.util
    import sys
    import tempfile
    import traceback as _traceback
    from pathlib import Path
    from catia_copilot.catia.modeling import ModelingContext, ModelingStepError

    if progress_signal:
        progress_signal.emit("正在执行建模脚本...")

    # 将脚本写入临时文件（importlib 需要文件路径；同时保留供调试）
    tmp_dir = Path(tempfile.gettempdir()) / "catia_copilot_modeling"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / "generated_model.py"

    try:
        script_path.write_text(script, encoding="utf-8")
    except Exception as e:
        return json.dumps({"error": f"写入脚本失败: {e}"}, ensure_ascii=False)

    # 检查脚本中是否定义了 build()
    if "def build(" not in script and "def build (" not in script:
        return json.dumps(
            {"error": "脚本中未找到 build(ctx) 函数，请确保脚本包含 def build(ctx): ..."},
            ensure_ascii=False,
        )

    # 加载模块
    try:
        sys.modules.pop("_catia_generated_model", None)
        spec   = importlib.util.spec_from_file_location("_catia_generated_model", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        err = _traceback.format_exc()
        logger.error(f"[MODELING] 脚本加载失败:\n{err}")
        return json.dumps(
            {"error": "脚本语法错误或 import 失败", "traceback": err,
             "script_path": str(script_path)},
            ensure_ascii=False,
        )

    if progress_signal:
        progress_signal.emit("脚本已加载，正在调用 build(ctx)...")

    # 创建执行上下文并调用 build(ctx)
    ctx = ModelingContext()
    try:
        module.build(ctx)

    except ModelingStepError as mse:
        # 步骤级失败：有精确的步骤定位信息
        logger.error(f"[MODELING] 步骤 [{mse.step_name}] 失败:\n{mse.traceback_str}")
        return json.dumps(
            {
                "success":             False,
                "failed_step":         mse.step_name,
                "error":               str(mse.original_error),
                "traceback":           mse.traceback_str,
                "steps":               ctx.steps,
                "features_at_failure": mse.features_at_failure,
                "script_path":         str(script_path),
            },
            ensure_ascii=False,
        )

    except Exception:
        # build() 本身（非 _run 包裹的代码）抛出的异常
        err = _traceback.format_exc()
        logger.error(f"[MODELING] build() 执行失败:\n{err}")
        return json.dumps(
            {
                "success":     False,
                "error":       "build() 执行失败（非建模步骤异常）",
                "traceback":   err,
                "steps":       ctx.steps,
                "script_path": str(script_path),
            },
            ensure_ascii=False,
        )

    # 执行成功，读取当前零件状态
    if progress_signal:
        progress_signal.emit("build(ctx) 执行完成，正在读取模型状态...")

    try:
        from catia_copilot.catia.modeling import get_active_part, list_features, get_mass_props
        part     = get_active_part()
        features = list_features(part)
        mp       = get_mass_props(part)
        result: dict = {
            "success":     True,
            "part_name":   part.name,
            "features":    features,
            "steps":       ctx.steps,
            "script_path": str(script_path),
        }
        if mp:
            result["mass_kg"] = round(mp["mass"], 6)
            result["cog_mm"]  = [round(v, 3) for v in mp["cog"]]
    except Exception as e:
        result = {
            "success":     True,
            "steps":       ctx.steps,
            "script_path": str(script_path),
            "note":        f"build(ctx) 已执行，但读取模型状态失败: {e}",
        }

    return json.dumps(result, ensure_ascii=False)


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
    "get_document_properties":     tool_get_document_properties,
    "set_document_properties":     tool_set_document_properties,
    "find_part_for_drawing":       tool_find_part_for_drawing,
    "find_drawing_for_part":       tool_find_drawing_for_part,
    "update_memory":               tool_update_memory,
    "read_file":                   tool_read_file,
    "list_directory":              tool_list_directory,
    "write_file":                  tool_write_file,
    "run_modeling_script":         tool_run_modeling_script,
}

# 发给 LLM 的 JSON Schema 列表
tools_schema: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_catia_connection",
            "description": ("检查 CATIA V5 的 COM 连接状态。""返回值：connected=连接正常；broken=CATIA 进程存在但 COM 连接失败（通常是权限问题）；""disconnected=CATIA 未运行。""建议在执行任何 CATIA 操作前先调用此工具确认连接。"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_catia_connection",
            "description": ("对 CATIA V5 COM 连接进行详细诊断。""返回字段包括：status（连接状态）、""doc_count（已打开文档数）、active_doc（活动文档名）、""catia_process_running（进程是否存在）、error（错误描述）等。""适用于排查连接异常；日常使用 check_catia_connection 即可。"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_catia_file",
            "description": ("在 CATIA 中打开指定文件（CATPart / CATProduct / CATDrawing）。""若文件已打开则激活并切换到该文档，不会重复打开。""文件不存在时返回 error。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的完整 Windows 路径，例如 D:\\\\project\\\\part.CATPart",
                    },
                    "foreground": {
                        "type": "boolean",
                        "description": "是否将 CATIA 窗口切换到前台（置顶显示），默认 true",
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
                "采集 CATIA CATProduct 的 BOM（物料清单），返回层级零件清单。"
                "file_path 为 null 时使用当前活动文档。"
                "返回 {row_count, rows}，每行包含 Level/Type/Part Number/Nomenclature/"
                "Definition/Revision/Source/Description/Quantity 等标准列，"
                "以及 custom_columns 中指定的用户自定义属性列。"
                "summarize=true 时返回去重汇总结果（无 Level 列，Quantity 为全树累计）。"
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
                        "description": ("要采集的标准列名列表。""可选值：Level, Type, Part Number, Nomenclature, Definition, Revision, Source, Description, Quantity。""默认采集全部标准列。"),
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("用户自定义属性列名列表（通过 CATIA 零件属性对话框写入的属性）。""预设属性名包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"),
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
            "description": ("将 CATProduct 的 BOM 导出为 Excel（.xlsx）或 CSV 文件。""输出文件名规则：{原文件名}_BOM.xlsx（层级模式）或 {原文件名}_汇总BOM.xlsx（汇总模式）。""传 [null] 使用当前活动文档。返回 {success, exported_files}（导出文件路径列表）。"),
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
                        "description": ("要导出的标准列名列表。""可选值：Level, Type, Part Number, Nomenclature, Definition, Revision, Source, Description, Quantity。""默认导出全部标准列。"),
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
            "description": ("将编辑后的属性值批量写回 CATIA 文档中各零件的用户自定义属性。""注意：写回后不会自动保存，需调用 save_catia_document 保存文件，否则修改在关闭 CATIA 后丢失。""file_path 为 null 时操作当前活动文档。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "pn_data": {
                        "type": "object",
                        "description": ("要写入的数据，格式：{零件编号(Part Number): {列名: 新值}}。""列名可以是标准列（Nomenclature/Definition/Revision/Source）或用户自定义属性名。""只需包含需要修改的字段，未提供的字段保持不变。"),
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("pn_data 中包含的用户自定义属性列名列表（非标准列）。""预设属性名包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。""不在此列表中的列名会被当作标准属性处理。"),
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
            "description": ("批量将 CATDrawing 图纸文件导出为 PDF。""输出文件名规则：{prefix}{原文件名（不含扩展名）}{suffix}.pdf。""默认 prefix=DR_，suffix 为空，即 DR_{原文件名}.pdf。""返回 {success, exported_count, total}。"),
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
            "description": ("批量将 CATPart / CATProduct 导出为 STEP（.stp）格式。""输出文件名规则：{prefix}{原文件名（不含扩展名）}{suffix}.stp。""默认 prefix=MD_，suffix 为空，即 MD_{原文件名}.stp。""返回 {success, exported_count, total}。"),
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
            "description": ("正向依赖查询：返回指定文件直接引用的子文档路径列表。""CATProduct 返回直接子件路径；CATDrawing 返回视图链接的零件/产品路径。""activate=true（默认）时若文件未打开则自动打开；""activate=false 时只查询已打开文档，适合批量遍历场景。""返回 {dependency_count, dependencies}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "目标文件的完整路径",
                    },
                    "activate": {
                        "type": "boolean",
                        "description": ("是否自动打开并激活目标文档，默认 true。""设为 false 时只在已打开文档中查找，不打开新文件，适合批量遍历。"),
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
            "description": ("反向依赖查询：在当前 CATIA 中已打开的文档里，查找哪些文档直接引用了指定文件。""只检查已打开的 CATProduct 和 CATDrawing，不会主动打开新文件。""典型用途：删除零件前确认哪些装配体/图纸引用了它。""返回 {dependency_count, dependencies}。"),
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
                "采集 CATProduct 产品树中所有节点的质量特性（质量 kg、重心 m、转动惯量 kg*m^2），"
                "重心和惯量坐标已变换到根产品坐标系。"
                "数据来源：CATIA SPA 工具写入的惯量包络体保持测量参数；"
                "若零件未做保持测量，对应行的 Weight 等字段为 null。"
                "file_path 为 null 时使用当前活动文档。"
                "返回 {row_count, rows}，每行含 Level/Type/Part Number/Weight/CogX/CogY/CogZ/Ixx 等字段。"
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
                        "description": ("同一零件有多个实例（重复使用）时的读取策略：""all=读取全部实例（默认，每个实例单独一行）；""first=只读第一个实例；last=只读最后一个实例。"),
                    },
                    "skip_hidden": {
                        "type": "boolean",
                        "description": "是否跳过隐藏零件，默认 false",
                    },
                    "summary_only": {
                        "type": "boolean",
                        "description": ("为 true 时只返回根节点（Level=0，即整个产品）的质量特性摘要，""字段：Weight/CogX/CogY/CogZ/Ixx/Iyy/Izz/Ixy/Ixz/Iyz。""适合只需要总重量和重心的场景，大幅减少 token 消耗。默认 false。"),
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
                "从当前活动的 CATPart 或 CATProduct 生成新图纸（基于模板复制）。"
                "调用前请确认目标零件/产品已在 CATIA 中激活（通过 get_open_documents 确认 active_document）。"
                "生成的图纸保存在与零件相同的目录下。"
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
                        "description": (
                            "要从零件同步到图纸标题栏的属性名列表。"
                            "默认同步：物料编码、材料、重量。"
                            "可扩展为其他用户自定义属性名，如：零件类型、设计状态、规格型号等。"
                        ),
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
                "刷新当前活动 CATDrawing 的标题栏参数，从关联零件同步属性值。"
                "与 generate_drawing 的区别：refresh_drawing 作用于已存在的图纸（当前活动文档），"
                "不创建新文件；generate_drawing 从模板创建新图纸。"
                "调用前请确认活动文档是 CATDrawing（通过 get_open_documents 确认）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要从关联零件同步到图纸标题栏的属性名列表。"
                            "默认同步：物料编码、材料、重量。"
                        ),
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
            "description": ("为 CATPart 文件批量添加/刷写标准用户自定义属性。""标准属性包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。""已存在的属性不会被覆盖，只添加缺失的属性。""keep_open=false（默认）时处理完成后关闭文件以释放内存。"),
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
                "返回 {active_document（完整路径或 null）, open_documents（列表，每项含 name/path/type）}。"
                "type 取值：PartDocument / ProductDocument / DrawingDocument / Other。"
                "AI 在执行任何需要文件路径的操作前，应先调用此工具获取准确路径。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_catia_document",
            "description": ("保存 CATIA 文档到磁盘。file_path 为 null 时保存当前活动文档。""write_bom_to_catia 等写回操作完成后必须调用此工具，否则修改在关闭 CATIA 后丢失。""返回 {success, saved_path} 或 {error}。"),
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
                "策略说明（按优先级）："
                "pn_param_open_docs=读图纸 PartNumber 参数，在已打开文档中匹配；"
                "pn_param_scan_dirs=读图纸 PartNumber 参数，在上级目录中扫描同名文件；"
                "same_name_scan_dirs=用图纸文件名在上级目录中扫描同名零件文件；"
                "strip_prefix_scan_dirs=去掉图纸文件名前缀后扫描；"
                "doc_file_links=通过图纸生成式视图的 COM 链接直接读取关联零件（需图纸已打开）。"
                "返回 {matches: [路径列表]}，未找到时为空列表。"
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
                "策略说明（按优先级）："
                "pn_param_open_drws=在已打开图纸中找 PartNumber 参数与零件编号匹配的图纸；"
                "pn_param_scan_drws=在上级目录中扫描文件名与零件 PartNumber 相同的图纸；"
                "same_name_scan_dirs=在上级目录中扫描文件名与零件文件名相同的图纸；"
                "strip_prefix_scan_dirs=扫描上级目录中去掉前缀后与零件文件名匹配的图纸；"
                "doc_file_links=遍历已打开图纸，反查其视图链接是否指向该零件（需图纸已打开）。"
                "返回 {matches: [路径列表]}，未找到时为空列表。"
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
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取文本文件内容并返回。"
                "支持格式：txt / csv / json / jsonl / md / log / xml / yaml / yml / ini / cfg / toml。"
                "文件大小上限 1 MB。"
                "返回 {file_path, total_lines, returned_lines, truncated, content}。"
                "典型用途：读取 export_bom_to_excel 导出的 CSV、用户的配置文件或说明文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要读取的文件完整路径",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8，中文 Excel 导出的 CSV 通常需要 gbk 或 utf-8-sig",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "最多返回的行数，不填则返回全部内容",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "列出目录内容，返回文件和子目录列表。"
                "每项包含 name / path / type（file 或 directory）/ depth / size_kb / ext。"
                "支持 glob 通配符过滤（pattern 参数），例如 *.CATDrawing、*.csv。"
                "典型用途：批量操作前枚举目标文件，或排查文件是否存在。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "要列出的目录完整路径",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "glob 过滤模式，默认 * 列出全部。例如 *.CATDrawing 只列图纸文件",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出子目录，默认 false",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "recursive=true 时的最大递归深度，默认 2",
                    },
                },
                "required": ["dir_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将文本内容写入文件。"
                "支持格式：txt / csv / json / jsonl / md / log。"
                "写入内容上限 2 MB。父目录不存在时自动创建。"
                "典型用途：将 collect_bom 返回的数据保存为 JSON/CSV，或生成分析报告。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "目标文件完整路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文本内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append", "create_new"],
                        "description": (
                            "写入模式：overwrite=覆盖已有文件（默认）；"
                            "append=追加到文件末尾；"
                            "create_new=仅当文件不存在时创建，已存在则返回 error"
                        ),
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_properties",
            "description": (
                "读取 CATIA 文档的属性，包括标准属性和用户自定义属性。"
                "file_path 为 null 时读取当前活动文档。"
                "返回字段：file_path（文档路径）、doc_type（文档类型）、"
                "standard（标准属性字典，含 Part Number / Nomenclature / Revision / "
                "Definition / Source / Description）、"
                "user_defined（用户自定义属性字典）。"
                "典型用途：查看零件的版本号、命名规范、自定义属性；"
                "在修改属性前先读取当前值作为参考。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "目标文档的完整路径（.CATPart 或 .CATProduct）。"
                            "不填或传 null 则读取当前活动文档。"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_document_properties",
            "description": (
                "写入 CATIA 文档的属性（标准属性和/或用户自定义属性）。"
                "file_path 为 null 时操作当前活动文档。"
                "standard 支持的键：Part Number、Nomenclature、Revision、Definition、Source、Description。"
                "Description 通过 DescriptionRef 写入，经实测可写。"
                "save=true 时写入后立即保存文档；默认 false，需手动调用 save_catia_document。"
                "written_user_defined（已写入的自定义属性列表）、"
                "skipped（跳过的属性列表）、saved（是否已保存）。"
                "【重要】user_defined 的键必须来自预设列表："
                "零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"
                "不得自行创建预设列表以外的字段名。"
                "设计状态的合法值：草稿 / 冻结 / 发布 / 废弃。"
                "存货类别的合法值：物料-复材件 / 物料-金属件 / 物料-标准件 / 物料-非标件 / "
                "物料-钣金件 / 物料-塑胶件 / 物料-橡胶件 / 物料-电子件 / 物料-泡沫 / "
                "物料-软包 / 物料-辅材 / 物料-组件 / 物料-虚拟件 / 半成品-组件 / 成品-整机。"
                "用户要求写入不在预设列表中的字段名时，必须先告知用户并询问确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "目标文档的完整路径（.CATPart 或 .CATProduct）。"
                            "不填或传 null 则操作当前活动文档。"
                        ),
                    },
                    "standard": {
                        "type": "object",
                        "description": (
                            "要写入的标准属性字典。"
                            "支持的键：Part Number、Nomenclature、Revision、Definition、Source、Description。"
                            "Description 通过 DescriptionRef 写入，经实测可写。"
                            "Source 的合法值：Unknown、Made、Bought。"
                            "例：{\"Revision\": \"B\", \"Description\": \"主轴支架\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "user_defined": {
                        "type": "object",
                        "description": (
                            "要写入的用户自定义属性字典。"
                            "键必须来自预设列表：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"
                            "设计状态合法值：草稿 / 冻结 / 发布 / 废弃。"
                            "存货类别合法值：物料-复材件 / 物料-金属件 / 物料-标准件 / 物料-非标件 / "
                            "物料-钣金件 / 物料-塑胶件 / 物料-橡胶件 / 物料-电子件 / 物料-泡沫 / "
                            "物料-软包 / 物料-辅材 / 物料-组件 / 物料-虚拟件 / 半成品-组件 / 成品-整机。"
                            "例：{\"设计状态\": \"冻结\", \"存货类别\": \"物料-金属件\", \"材料\": \"Q235\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "save": {
                        "type": "boolean",
                        "description": "写入后是否立即保存文档，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_modeling_script",
            "description": (
                "在 CATIA 中执行 AI 生成的 Python 建模脚本，完成零件建模。\n\n"
                "脚本必须包含 def build(ctx): 函数（注意：参数是 ctx，不是无参）。\n"
                "通过 ctx 调用所有建模 API，不需要任何 import 语句。\n\n"
                "可用 API（通过 ctx 调用）：\n"
                "  ctx.create_part(name)                          新建零件，返回 Part\n"
                "  ctx.get_active_part()                          获取活动文档的 Part\n"
                "  ctx.update_part(part)                          刷新模型（必须在末尾调用）\n"
                "  ctx.save_part(part, path)                      另存为\n"
                "  ctx.add_sketch(part, plane)                    新建草图，plane='xy'/'yz'/'zx'\n"
                "  ctx.add_sketch_at_height(part, h, base='xy')   在距基准平面 h mm 处建草图（在顶面继续建模时用）\n"
                "  ctx.draw_rect(sk, x, y, w, h)                  画矩形（左下角+宽高，mm）\n"
                "  ctx.draw_circle(sk, cx, cy, r)                 画圆（圆心+半径，mm）\n"
                "  ctx.draw_point(sk, x, y)                       画定位点\n"
                "  ctx.add_pad(part, sk, depth)                   拉伸，mm\n"
                "  ctx.add_pocket(part, sk, depth)                挖槽，mm（仅基准面草图）\n"
                "  ctx.add_hole_from_sketch(part, sk, d, depth)   打孔\n"
                "  ctx.list_features(part)                        查询特征列表\n"
                "  ctx.list_sketches(part)                        查询草图列表\n"
                "  ctx.get_mass_props(part)                       查询质量特性\n"
                "  ctx.step('描述')                               可选里程碑标记\n\n"
                "暂不可用（后续版本开放）：\n"
                "  ctx.add_edge_fillet / ctx.add_chamfer          需要 edge_ref，当前无法构造\n"
                "  ctx.add_rect_pattern / ctx.add_circ_pattern    方向参数有 bug\n\n"
                "脚本模板：\n"
                "  def build(ctx):\n"
                "      part = ctx.create_part('零件名')\n"
                "      sk   = ctx.add_sketch(part, 'xy')\n"
                "      ctx.draw_rect(sk, 0, 0, 100, 50)\n"
                "      pad  = ctx.add_pad(part, sk, 20)\n"
                "      ctx.step('主体完成')\n"
                "      ctx.update_part(part)\n\n"
                "成功：返回 success=true、零件名、特征列表、步骤记录。\n"
                "失败：返回 success=false、failed_step（哪步失败）、error、完整步骤记录。\n"
                "根据 failed_step 和 error 定位并修正，不要重写无关步骤。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": (
                            "完整的 Python 脚本字符串，必须包含 def build(ctx): 函数。"
                            "通过 ctx 调用所有建模 API，不需要任何 import 语句。"
                        ),
                    },
                },
                "required": ["script"],
            },
        },
    },
]
