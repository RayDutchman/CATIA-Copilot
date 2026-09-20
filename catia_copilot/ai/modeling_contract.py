# -*- coding: utf-8 -*-
"""建模 API 单一契约注册表与两个消费端渲染数据（S1）。signature 与 modeling.py 形参逐字符一致。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelingApiSpec:
    name: str
    category: str
    signature: str  # 形参串（不含 self/方法名），与 ast.unparse(参数AST) 逐字符一致
    notes: str      # 中文、单行，带参数名与具体说明


MODELING_API_CATALOG: tuple[ModelingApiSpec, ...] = (
    # 文档与零件
    ModelingApiSpec("create_part", "文档与零件", "name='Part', nomenclature=''", "新建 CATPart；name=零件号(PartNumber)、nomenclature=命名(如'底座')"),
    ModelingApiSpec("get_active_part", "文档与零件", "", "获取活动文档的 Part"),
    ModelingApiSpec("update_part", "文档与零件", "part", "刷新模型（build 末尾与每次修改后必须调用）"),
    ModelingApiSpec("save_part", "文档与零件", "part, path", "另存为"),
    # 草图
    ModelingApiSpec("add_sketch", "草图", "part, plane='xy'", "在基准平面('xy'/'yz'/'zx')建草图"),
    ModelingApiSpec("add_sketch_at_height", "草图", "part, height, base_plane='xy'", "在 base_plane 上方 height mm 处建偏移草图（已知高度时用）"),
    ModelingApiSpec("add_sketch_on_pad_top", "草图", "part, pad", "Pad 顶面 B-Rep 支撑草图（随 Pad 深度变化，叠加建模推荐）"),
    ModelingApiSpec("add_sketch_on_pad_side", "草图", "part, pad, edge_index", "Pad 侧面 B-Rep 支撑草图；edge_index 从 1 起：draw_rect(x,y,w,h) 的 1=Y=y面,2=X=x+w面,3=Y=y+h面,4=X=x面；XY矩形Pad侧面场景下 H 沿面宽、V=Z+（高度方向），不能推广到任意旋转面"),
    ModelingApiSpec("add_sketch_on_pad_bottom", "草图", "part, pad", "Pad 底面 B-Rep 支撑草图"),
    # 图元
    ModelingApiSpec("draw_rect", "图元", "sketch, x, y, width, height", "画矩形（左下角 x,y + 宽 width/高 height，mm）；旋转体场景：x=半径向起点（内半径），width=壁厚（半径向宽度），y=轴向起点，height=轴向长度"),
    ModelingApiSpec("draw_circle", "图元", "sketch, cx, cy, radius", "画圆（圆心 cx,cy + 半径 radius，mm）"),
    ModelingApiSpec("draw_arc", "图元", "sketch, cx, cy, radius, start_angle, end_angle", "画圆弧（半径 + 起止角，度，逆时针，0°=水平右）"),
    ModelingApiSpec("draw_line", "图元", "sketch, x1, y1, x2, y2", "画直线段，mm"),
    ModelingApiSpec("draw_slot", "图元", "sketch, x1, y1, x2, y2, radius", "画腰形槽（中轴两端点 + 半圆半径，mm）"),
    ModelingApiSpec("draw_point", "图元", "sketch, x, y", "画定位点（用于孔定位）"),
    # 特征
    ModelingApiSpec("add_pad", "特征", "part, sketch, depth, symmetric=False, second_depth=None", "拉伸；symmetric=True 对称(总厚2×depth)、second_depth=N 双向非对称"),
    ModelingApiSpec("add_pocket", "特征", "part, sketch, depth", "挖槽，mm（仅基准面草图）"),
    ModelingApiSpec("add_shaft", "特征", "part, sketch, axis='z'", "旋转体（360°）；先 prepare_revolute_axis 再 add_sketch"),
    ModelingApiSpec("add_groove", "特征", "part, sketch, axis='z'", "环形槽（旋转切除，需已有实体且 update_part）"),
    ModelingApiSpec("add_hole_from_sketch", "特征", "part, sketch, diameter, depth", "对草图打孔（直径 diameter + 深度 depth，mm）"),
    ModelingApiSpec("prepare_revolute_axis", "特征", "part, axis='z'", "预建旋转轴线，必须先于 add_sketch 调用"),
    # 修饰（倒圆角）
    ModelingApiSpec("add_auto_fillet", "修饰（倒圆角）", "part, radius, inner_radius=None", "自动圆角所有适合边；inner_radius 缺省=radius；需 update_part"),
    ModelingApiSpec("add_fillet_edges", "修饰（倒圆角）", "part, edge_refs, radius", "对指定边引用列表圆角；需 update_part"),
    ModelingApiSpec("make_pad_edge_ref", "修饰（倒圆角）", "part, pad, face_a_brep, face_b_brep", "低层：由两面 BRep 构造 Pad 边引用"),
    ModelingApiSpec("get_pad_face_brep", "修饰（倒圆角）", "pad, face, edge_index=1", "低层：获取 Pad 面 BRep 字符串"),
    # 面/边查询
    ModelingApiSpec("get_pad_faces", "面/边查询", "part, pad", "Pad 全部面（geometry_type=planar/cylindrical/unknown；normal 仅 planar 且矩形经端点确认时非 None，圆柱侧面 normal=None；edge_index=过滤后草图边序号；轮廓不可枚举时报错，不再默认4边；错误经工具回显，不产生 failed_step）"),
    ModelingApiSpec("get_pad_faces_by_normal", "面/边查询", "part, pad, normal, tolerance_deg=5.0", "按法向筛选面（校验 target 为有限非零向量、tolerance_deg∈[0,180]；仅匹配 normal 非 None 的 planar 面，圆柱/未知跳过）；normal=(nx,ny,nz) 如(0,0,1)=朝上"),
    ModelingApiSpec("get_pad_face_edges", "面/边查询", "part, pad, face_info", "Pad 某面的所有边引用列表"),
    ModelingApiSpec("get_pocket_faces", "面/边查询", "part, pocket", "Pocket 自身面（geometry_type=planar/cylindrical/unknown；normal 仅已确认 planar 时非 None，cylindrical 侧面为 None；Pocket 侧法向=Pad 外法向反向；轮廓不可枚举时报错，不再默认4边；开口面属下层 Pad）"),
    ModelingApiSpec("get_pocket_face_edges", "面/边查询", "part, pocket, face_info", "Pocket 某面的所有边引用列表"),
    ModelingApiSpec("get_pocket_opening_edges", "面/边查询", "part, pocket, pad", "Pocket 开口楞（=Pad 顶面×Pocket 侧面）"),
    ModelingApiSpec("get_shaft_faces", "面/边查询", "part, shaft", "Shaft 面列表（type=surface；旋转面类型未解析时 geometry_type=unknown、normal=None、origin=None；edge_index=过滤后草图边序号）"),
    ModelingApiSpec("get_shaft_face_edges", "面/边查询", "part, shaft, face_info", "Shaft 面与相邻面的交线边引用列表"),
    # 查询与步骤记录
    ModelingApiSpec("list_features", "查询与步骤记录", "part", "查询特征列表"),
    ModelingApiSpec("list_sketches", "查询与步骤记录", "part", "查询草图列表"),
    ModelingApiSpec("get_mass_props", "查询与步骤记录", "part", "查询质量特性；可能返回 None（读取失败或质量无效）；质量取决于密度，未指定材料可能使用默认密度，不代表真实材料"),
    # 里程碑
    ModelingApiSpec("step", "里程碑", "name, feature=None", "打里程碑标记，不执行 CATIA 操作；feature 可选"),
)


# 故障阵列：源码存在但方向参数有 bug，不入目录、不渲染为可用 API；steps 为 @property 由测试过滤。
EXCLUDED_MODELING_APIS: dict[str, str] = {
    "add_rect_pattern": "方向参数有 bug，暂不推荐使用",
    "add_circ_pattern": "方向参数有 bug，暂不推荐使用",
}


def _render_catalog_lines() -> str:
    """按目录分组渲染注册表：`ctx.<name>(<signature>)  <notes>`，两个消费端共用单源。"""
    lines: list[str] = []
    last_cat: str | None = None
    for spec in MODELING_API_CATALOG:
        if spec.category != last_cat:
            last_cat = spec.category
            lines.append(f"### {last_cat}")
        sig = f"({spec.signature})" if spec.signature else "()"
        lines.append(f"- `ctx.{spec.name}{sig}`  {spec.notes}")
    return "\n".join(lines)


# 旋转体轴向映射沿用 defaultprompt 手册：axis→草图平面、轴向/半径向、正半径约束。
_AXIS_MAPPING_TEXT = """### 旋转体轴向映射（沿用 defaultprompt 手册）
旋转轮廓草图所在平面包含旋转轴，H/V 坐标与三维轴向由 axis 决定：
- axis="z" → plane="zx"：轴=V(Z)、半径向=H(-X)、H>0
- axis="y" → plane="xy"：轴=V(Y)、半径向=H(X)、H>0
- axis="x" → plane="xy"：轴=H(X)、半径向=V(Y)、V>0
- 旋转体半径向一律用"内半径"起笔；draw_rect 的 x=H 起点、y=V 起点（mm/度）；
- 必须先用 prepare_revolute_axis，再 add_sketch（轴线先于草图）。
- 注：上述轴→平面映射沿用当前 defaultprompt 手册，实际 H/V→轴向对应需实机验证各场景。"""

_FACE_SEMANTICS_TEXT = """### 面/边查询语义
- 每个面描述含 source='feature_inference'（由特征+草图推导的候选特征面，非 CATIA 最终拓扑枚举；face_brep/type/edge_index 字段语义不变）、geometry_type（planar/cylindrical/unknown）与 edge_count（过滤后的草图边数，候选侧面数，不宣称最终拓扑面数恒定）。
- normal 仅对端点确认的 planar 矩形面成立；圆柱侧面、旋转面及其它无法解析的面 normal=None，不得用矩形法向代替。
- Pocket 自身侧面法向 = 对应 Pad 外侧法向反向；开口面属下层 Pad。
- 轮廓不可枚举时 get_pad_faces/get_pocket_faces/get_shaft_faces 抛错（不再默认4边）。该错误经工具通用异常回显 traceback 消息，不产生 failed_step（查询类调用不经步骤执行流）。"""


def build_modeling_prompt_section() -> str:
    """构建建模系统提示章节：以注册表为唯一事实源渲染 API 目录与关键纪律。"""
    return f"""**建模**

严格按下列 API 目录操作，禁止调用目录外名字；失败时先局部精准修正出错参数，不重写整段脚本。

### 尺寸参数化
尺寸形参一律用具名变量，在 build 顶部声明并加注释；固定坐标与固定边索引可写字面量；允许用 base_w / 2 这类表达式。

{_AXIS_MAPPING_TEXT}

{_FACE_SEMANTICS_TEXT}

### 三类草图
1. 基准面草图：add_sketch(part, 'xy'/'yz'/'zx')，建在基准平面；
2. 偏移草图：add_sketch_at_height(part, height)，已知高度时用；
3. B-Rep 面支撑草图：add_sketch_on_pad_top/add_sketch_on_pad_side/add_sketch_on_pad_bottom，随 Pad 深度变化，叠加建模推荐。

### 步骤纪律
每次特征/修饰修改后必须 update_part，build 末尾必须 update_part；质量查询 get_mass_props 可能返回 None（读取失败或质量无效），且质量取决于密度，未指定材料可能使用默认密度，不代表真实材料，不要据此断言材料。

### 示例脚本（全部可执行；失败时按 failed_step/error 输出局部精准修正，不盲目重跑 create_part；build 末尾必须 update_part）
```python
{_example_blocks()[0]}```
```python
{_example_blocks()[1]}```
```python
{_example_blocks()[2]}```
```python
{_example_blocks()[3]}```

### API 目录
{_render_catalog_lines()}

"""


def build_run_modeling_script_description() -> str:
    """构建"运行建模脚本"工具参数说明：同样以注册表为唯一事实源，以 ## 关键约束 收束。"""
    head = "这是建模脚本的完整契约。脚本必须以 def build(ctx): 作为唯一入口，模块顶层不得执行任何建模调用。"
    lines: list[str] = [head]
    lines.extend(_render_catalog_lines().splitlines())
    lines.append("")
    lines.extend(_AXIS_MAPPING_TEXT.splitlines())
    lines.append("")
    lines.extend(_FACE_SEMANTICS_TEXT.splitlines())
    lines.extend(
        [
            "",
            "### 体例",
            "尺寸形参用具名变量并注释；固定坐标/边索引写字面量。旋转体先 prepare_revolute_axis 后 add_sketch；每次修改后与 build 末尾 update_part；失败时局部精准修正，只改出错参数。",
            "",
            "## 关键约束",
            "1. 只允许调用上方 API 目录列出的 ctx 方法，禁止使用目录外建模 API；",
            "2. 旋转体剖面严格按轴向映射表布局，半径向用\"内半径\"起笔；",
            "3. 质量特性可能返回 None，且依赖用户材料密度，不代表真实材料。",
            "",
            "### 示例脚本（失败时按 failed_step/error 输出局部精准修正，不盲目重跑 create_part；build 末尾必须 update_part）",
        ]
    )
    for block in _example_blocks():
        lines.append(f"```python\n{block}```")
    lines.extend(
        [
            "",
            "### 执行目标与状态",
            "可选参数 target_document_id 可传当前 CATIA 文档名、完整路径或 PartNumber；与活动文档不匹配时工具拒绝执行。",
            "可选参数 verification 可验收 required_features、feature_count、mass_kg、cog_mm；验收失败会明确标记 verification=failed。",
            "返回结果区分 execution、model_update、verification 三层状态；查询状态失败时不得把模型验证说成通过。",
        ]
    )
    return "\n".join(lines)


def _example_blocks() -> tuple[str, ...]:
    """返回可在 FakeCtx 下执行的示例脚本集；尺寸用具名变量、固定坐标/边索引写字面量并注释。"""
    return (
        '''def build(ctx):
    length = 100.0   # 长度（X 向）
    width  = 60.0    # 宽度（Y 向）
    height = 30.0    # 高度（Z 向）
    part = ctx.create_part(name="底座")
    sk   = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk, 0, 0, length, width)
    ctx.add_pad(part, sk, depth=height)
    ctx.step("主体完成")
    ctx.update_part(part)
''',
        '''def build(ctx):
    inner_radius = 25.0   # 内半径（H 起点）
    wall         = 25.0   # 壁厚（H 方向）
    height       = 80.0   # 轴向长度（V 方向）
    axis         = "y"
    part = ctx.create_part(name="圆筒")
    ctx.prepare_revolute_axis(part, axis)   # 必须先于 add_sketch
    sk   = ctx.add_sketch(part, "xy")       # axis="y" → plane="xy"
    ctx.draw_rect(sk, inner_radius, 0, wall, height)
    ctx.add_shaft(part, sk, axis=axis)
    ctx.update_part(part)
''',
        '''def build(ctx):
    base_w = 100.0   # 底层宽度
    base_l = 60.0    # 底层长度
    h1     = 30.0    # 第一层高度
    h2     = 15.0    # 第二层高度
    r      = 20.0    # 圆柱半径
    part = ctx.create_part(name="叠加件")
    sk1  = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk1, 0, 0, base_w, base_l)
    pad1 = ctx.add_pad(part, sk1, depth=h1)
    ctx.update_part(part)
    sk2  = ctx.add_sketch_on_pad_top(part, pad1)   # B-Rep 面支撑，随 Pad 深度变化
    ctx.draw_circle(sk2, base_w / 2, base_l / 2, r)  # 圆心=固定坐标（形心），允许表达式
    ctx.add_pad(part, sk2, depth=h2)
    ctx.update_part(part)
''',
        '''def build(ctx):
    w     = 50.0    # 长度
    l     = 40.0    # 宽度
    depth = 20.0    # 拉伸深度
    r     = 3.0     # 圆角半径
    part = ctx.create_part(name="圆角件")
    sk   = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk, 0, 0, w, l)
    pad  = ctx.add_pad(part, sk, depth=depth)
    ctx.update_part(part)
    top   = ctx.get_pad_faces_by_normal(part, pad, normal=(0, 0, 1), tolerance_deg=5.0)[0]
    edges = ctx.get_pad_face_edges(part, pad, top)
    ctx.add_fillet_edges(part, edges, radius=r)
    ctx.update_part(part)
''',
    )
