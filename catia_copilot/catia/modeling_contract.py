# -*- coding: utf-8 -*-
"""建模 API 单一契约注册表与两个消费端渲染数据（S1）。signature 与 modeling.py 形参逐字符一致。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelingApiSpec:
    name: str
    category: str
    signature: str  # 形参串（不含 self/方法名），与 ast.unparse(参数AST) 逐字符一致
    notes: str      # English one-line description with parameter names and details


MODELING_API_CATALOG: tuple[ModelingApiSpec, ...] = (
    # 文档与零件
    ModelingApiSpec("create_part", "Documents and Parts", "name='Part', nomenclature=''", "Create a CATPart; name sets PartNumber and nomenclature sets the descriptive name"),
    ModelingApiSpec("get_active_part", "Documents and Parts", "", "Get the active document Part"),
    ModelingApiSpec("update_part", "Documents and Parts", "part", "Update the model after every change and at the end of build"),
    ModelingApiSpec("save_part", "Documents and Parts", "part, path", "Save the part to another path"),
    # 草图
    ModelingApiSpec("add_sketch", "Sketches", "part, plane='xy'", "Create a sketch on a reference plane: xy, yz, or zx"),
    ModelingApiSpec("add_sketch_at_height", "Sketches", "part, height, base_plane='xy'", "Create an offset sketch height mm above base_plane; use when the height is known"),
    ModelingApiSpec("add_sketch_on_pad_top", "Sketches", "part, pad", "Create a B-Rep-supported sketch on the Pad top face; it follows Pad depth changes"),
    ModelingApiSpec("add_sketch_on_pad_side", "Sketches", "part, pad, edge_index", "Create a B-Rep-supported sketch on a Pad side face. edge_index is 1-based; for draw_rect: 1=Y=y, 2=X=x+w, 3=Y=y+h, 4=X=x. H follows face width and V=Z+ for XY rectangular Pads; do not generalize to rotated faces"),
    ModelingApiSpec("add_sketch_on_pad_bottom", "Sketches", "part, pad", "Create a B-Rep-supported sketch on the Pad bottom face"),
    # 图元
    ModelingApiSpec("draw_rect", "Geometry", "sketch, x, y, width, height", "Draw a rectangle from lower-left x,y with width and height in mm; for revolved profiles x is inner radius, width is wall thickness, y is axial start, and height is axial length"),
    ModelingApiSpec("draw_circle", "Geometry", "sketch, cx, cy, radius", "Draw a circle from center cx,cy and radius in mm"),
    ModelingApiSpec("draw_arc", "Geometry", "sketch, cx, cy, radius, start_angle, end_angle", "Draw an arc; angles are degrees, counterclockwise, with 0° pointing right"),
    ModelingApiSpec("draw_line", "Geometry", "sketch, x1, y1, x2, y2", "Draw a line segment in mm"),
    ModelingApiSpec("draw_slot", "Geometry", "sketch, x1, y1, x2, y2, radius", "Draw a slot from two centerline endpoints and a semicircle radius in mm"),
    ModelingApiSpec("draw_point", "Geometry", "sketch, x, y", "Draw a positioning point, for example to locate a hole"),
    # 特征
    ModelingApiSpec("add_pad", "Features", "part, sketch, depth, symmetric=False, second_depth=None", "Extrude; symmetric=True creates total thickness 2×depth; second_depth=N creates an asymmetric two-sided pad"),
    ModelingApiSpec("add_pocket", "Features", "part, sketch, depth", "Cut a pocket in mm from a sketch on a reference plane"),
    ModelingApiSpec("add_shaft", "Features", "part, sketch, axis='z'", "Revolve 360°; call prepare_revolute_axis before add_sketch"),
    ModelingApiSpec("add_groove", "Features", "part, sketch, axis='z'", "Create a revolved cut; requires an existing solid and update_part"),
    ModelingApiSpec("add_hole_from_sketch", "Features", "part, sketch, diameter, depth", "Create a hole from a sketch using diameter and depth in mm"),
    ModelingApiSpec("prepare_revolute_axis", "Features", "part, axis='z'", "Create the revolution axis before calling add_sketch"),
    # 修饰（倒圆角）
    ModelingApiSpec("add_auto_fillet", "Fillets", "part, radius, inner_radius=None", "Automatically fillet suitable edges; inner_radius defaults to radius; call update_part afterward"),
    ModelingApiSpec("add_fillet_edges", "Fillets", "part, edge_refs, radius", "Fillet the specified edge references; call update_part afterward"),
    ModelingApiSpec("make_pad_edge_ref", "Fillets", "part, pad, face_a_brep, face_b_brep", "Low-level: build a Pad edge reference from two faces"),
    ModelingApiSpec("get_pad_face_brep", "Fillets", "pad, face, edge_index=1", "Low-level: get a Pad face BRep string"),
    # 面/边查询
    ModelingApiSpec("get_pad_faces", "Face and Edge Queries", "part, pad", "List candidate Pad faces with geometry_type planar/cylindrical/unknown; normal is available only for endpoint-confirmed planar rectangles. Enumeration failures raise errors rather than assuming four edges"),
    ModelingApiSpec("get_pad_faces_by_normal", "Face and Edge Queries", "part, pad, normal, tolerance_deg=5.0", "Filter faces by a finite nonzero target normal and tolerance in [0,180]; only known planar normals match"),
    ModelingApiSpec("get_pad_face_edges", "Face and Edge Queries", "part, pad, face_info", "List all edge references for a Pad face"),
    ModelingApiSpec("get_pocket_faces", "Face and Edge Queries", "part, pocket", "List Pocket faces; normals are reported only for confirmed planar geometry, and opening faces belong to the underlying Pad"),
    ModelingApiSpec("get_pocket_face_edges", "Face and Edge Queries", "part, pocket, face_info", "List all edge references for a Pocket face"),
    ModelingApiSpec("get_pocket_opening_edges", "Face and Edge Queries", "part, pocket, pad", "List Pocket opening edges at the Pad top face"),
    ModelingApiSpec("get_shaft_faces", "Face and Edge Queries", "part, shaft", "List candidate Shaft surfaces; unresolved geometry has unknown type and null normal/origin"),
    ModelingApiSpec("get_shaft_face_edges", "Face and Edge Queries", "part, shaft, face_info", "List intersection edges between a Shaft face and adjacent faces"),
    # 查询与步骤记录
    ModelingApiSpec("list_features", "Queries and Steps", "part", "List model features"),
    ModelingApiSpec("list_sketches", "Queries and Steps", "part", "List sketches"),
    ModelingApiSpec("get_mass_props", "Queries and Steps", "part", "Read mass properties; may return None when unavailable or invalid. Mass depends on density; the default density may be used when no material is assigned and does not represent the real material"),
    # 里程碑
    ModelingApiSpec("step", "Milestones", "name, feature=None", "Record a milestone without a CATIA operation; feature is optional"),
)


# 故障阵列：源码存在但方向参数有 bug，不入目录、不渲染为可用 API；steps 为 @property 由测试过滤。
EXCLUDED_MODELING_APIS: dict[str, str] = {
    "add_rect_pattern": "Direction parameters are known to be incorrect; use is not recommended",
    "add_circ_pattern": "Direction parameters are known to be incorrect; use is not recommended",
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


# Revolved-profile axis mapping follows the existing modeling convention.
_AXIS_MAPPING_TEXT = """### Revolved Profile Axis Mapping
The sketch plane contains the revolution axis. The axis parameter determines how sketch H/V map to 3D:
- axis="z" → plane="zx": axis=V(Z), radial=H(-X), H>0
- axis="y" → plane="xy": axis=V(Y), radial=H(X), H>0
- axis="x" → plane="xy": axis=H(X), radial=V(Y), V>0
- Start radial profiles at the inner radius; draw_rect x is the H start and y is the V start (mm/degrees).
- Call prepare_revolute_axis before add_sketch so the axis precedes the sketch.
- Verify the H/V mapping for each scenario against the actual CATIA environment."""

_FACE_SEMANTICS_TEXT = """### Face and Edge Query Semantics
- Face descriptions use source='feature_inference' for candidate faces inferred from features and sketches, not final CATIA topology enumeration. face_brep/type/edge_index retain their defined meanings. geometry_type is planar/cylindrical/unknown; edge_count is the filtered sketch-edge count, not a guaranteed final topology face count.
- normal is valid only for endpoint-confirmed planar rectangular faces. Cylindrical, revolved, and unresolved faces have normal=None; do not substitute a rectangular-face normal.
- Pocket side normals are opposite the corresponding Pad outward normals. The opening face belongs to the underlying Pad.
- If a profile cannot be enumerated, get_pad_faces/get_pocket_faces/get_shaft_faces raise an error instead of assuming four edges. Query errors are returned through the tool exception path and do not create failed_step records."""


def build_modeling_prompt_section() -> str:
    """构建建模系统提示章节：以注册表为唯一事实源渲染 API 目录与关键纪律。"""
    return f"""**Modeling**

Follow the API catalog below; do not call APIs outside it. On failure, correct the specific parameter instead of rewriting the entire script.

### Parameterizing Dimensions
Use named variables for dimensions, declare and comment them near the top of build, and use literals for fixed coordinates and edge indices. Expressions such as base_w / 2 are allowed.

{_AXIS_MAPPING_TEXT}

{_FACE_SEMANTICS_TEXT}

### Three Sketch Types
1. Reference-plane sketch: add_sketch(part, 'xy'/'yz'/'zx').
2. Offset sketch: add_sketch_at_height(part, height); use when the height is known.
3. B-Rep-supported sketch: add_sketch_on_pad_top/add_sketch_on_pad_side/add_sketch_on_pad_bottom; these follow Pad depth changes and are recommended for additive modeling.

### Step Discipline
Call update_part after every feature/modification and at the end of build. get_mass_props may return None if unavailable or invalid. Mass depends on density; CATIA may use a default density when no material is assigned, which does not represent the actual material.

### Runnable Examples (correct failed_step/error locally; do not blindly rerun create_part; call update_part at the end of build)
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
    head = "Complete modeling-script contract. The script must use def build(ctx): as its only entry point; do not execute modeling operations at module scope."
    lines: list[str] = [head]
    lines.extend(_render_catalog_lines().splitlines())
    lines.append("")
    lines.extend(_AXIS_MAPPING_TEXT.splitlines())
    lines.append("")
    lines.extend(_FACE_SEMANTICS_TEXT.splitlines())
    lines.extend(
        [
            "",
            "### Conventions",
            "Use named, commented variables for dimensions and literals for fixed coordinates/edge indices. For revolutions, call prepare_revolute_axis before add_sketch. Call update_part after each change and at build end. Fix only the failing parameter.",
            "",
            "## Key Constraints",
            "1. Call only ctx methods listed in the API catalog above; do not use other modeling APIs.",
            "2. Lay out revolved profiles using the axis mapping above and begin the radial dimension at the inner radius.",
            "3. Mass properties may be None and depend on material density; they do not identify the actual material.",
            "",
            "### Examples (correct failed_step/error locally; do not blindly rerun create_part; call update_part at build end)",
        ]
    )
    for block in _example_blocks():
        lines.append(f"```python\n{block}```")
    lines.extend(
        [
            "",
            "### Execution Target and Status",
            "Optional target_document_id accepts the current CATIA document name, full path, or PartNumber. Execution is rejected if it does not match the active document.",
            "Optional verification checks required_features, feature_count, mass_kg, and cog_mm; failed checks set verification=failed.",
            "Results distinguish execution, model_update, and verification states. A failed state query must not be reported as successful model verification.",
        ]
    )
    return "\n".join(lines)


def _example_blocks() -> tuple[str, ...]:
    """返回可在 FakeCtx 下执行的示例脚本集；尺寸用具名变量、固定坐标/边索引写字面量并注释。"""
    return (
        '''def build(ctx):
    length = 100.0   # Length along X
    width  = 60.0    # Width along Y
    height = 30.0    # Height along Z
    part = ctx.create_part(name="Base")
    sk   = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk, 0, 0, length, width)
    ctx.add_pad(part, sk, depth=height)
    ctx.step("Main body complete")
    ctx.update_part(part)
''',
        '''def build(ctx):
    inner_radius = 25.0   # Inner radius (H start)
    wall         = 25.0   # Wall thickness along H
    height       = 80.0   # Axial length along V
    axis         = "y"
    part = ctx.create_part(name="Cylinder")
    ctx.prepare_revolute_axis(part, axis)   # Must precede add_sketch
    sk   = ctx.add_sketch(part, "xy")       # axis="y" → plane="xy"
    ctx.draw_rect(sk, inner_radius, 0, wall, height)
    ctx.add_shaft(part, sk, axis=axis)
    ctx.update_part(part)
''',
        '''def build(ctx):
    base_w = 100.0   # Base width
    base_l = 60.0    # Base length
    h1     = 30.0    # First layer height
    h2     = 15.0    # Second layer height
    r      = 20.0    # Cylinder radius
    part = ctx.create_part(name="Stepped Part")
    sk1  = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk1, 0, 0, base_w, base_l)
    pad1 = ctx.add_pad(part, sk1, depth=h1)
    ctx.update_part(part)
    sk2  = ctx.add_sketch_on_pad_top(part, pad1)   # B-Rep support follows Pad depth
    ctx.draw_circle(sk2, base_w / 2, base_l / 2, r)  # Center uses fixed dimensions; expressions are allowed
    ctx.add_pad(part, sk2, depth=h2)
    ctx.update_part(part)
''',
        '''def build(ctx):
    w     = 50.0    # Length
    l     = 40.0    # Width
    depth = 20.0    # Extrusion depth
    r     = 3.0     # Fillet radius
    part = ctx.create_part(name="Filleted Part")
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
