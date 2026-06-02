# Modeling Layer Handoff

## 当前状态

分支：`feat/modeling-layer`

### 已完成

| 文件 | 内容 |
|---|---|
| `catia_copilot/catia/modeling.py` | 阶段一基础建模 API（见下方函数清单） |
| `catia_copilot/ai/tools.py` | `tool_run_modeling_script` 已注册，`tools_map` + `tools_schema` + `DEFAULT_SYSTEM_PROMPT` 已更新 |
| `experiments/debug_create_part.py` | 原始端到端验证（new part → sketch → pad） |
| `experiments/test_modeling_api.py` | modeling.py 接口验证 |
| `experiments/test_agent_modeling.py` | 模拟 agent 调用 run_modeling_script |
| `docs/AI_MODELING_PLAN_AND_ROADMAP.md` | 完整四阶段计划与架构决策 |
| `docs/PYCATIA_OVERVIEW.md` | pycatia 模块参考 |

### modeling.py 现有函数

```
# 文档
create_part(name)                         新建零件，返回 Part
get_active_part()                         获取活动文档的 Part
update_part(part)                         刷新模型
save_part(part, path)                     另存为

# 草图（仅支持三个基准面，不支持实体面）
add_sketch(part, plane)                   plane="xy"/"yz"/"zx"
draw_rect(sketch, x, y, width, height)   画矩形
draw_circle(sketch, cx, cy, radius)      画圆
draw_point(sketch, x, y)                 画点（用于孔定位）

# 特征（已实现）
add_pad(part, sketch, depth)             拉伸
add_pocket(part, sketch, depth)          挖槽（当前只能在基准面，有 bug）
add_hole_from_sketch(part, sketch, d, depth)  孔（基准面定位，有 bug）

# 特征（签名存在但 edge_ref 无法构造，AI 不可用）
add_edge_fillet(part, edge_ref, radius)
add_chamfer(part, edge_ref, length, angle=45)

# 阵列（参数不完整，实际调用可能失败）
add_rect_pattern(part, feature, nx, ny, dx, dy)
add_circ_pattern(part, feature, count, total_angle=360)

# 查询
list_features(part)
list_sketches(part)
get_mass_props(part)
```

---

## 已知问题

### Bug 1：Pocket/Hole 在基准面上无效

`add_pocket` / `add_hole_from_sketch` 当前在基准面（XY/YZ/ZX）上建草图，
这会导致挖槽/打孔无法切入实体（草图平面和实体不相交）。
**根因**：缺少 `add_sketch_on_face`，无法在已有实体的面上建草图。

### Bug 2：edge_ref 无法构造

`add_edge_fillet` / `add_chamfer` 需要传入 `edge_ref: Reference`，
但框架没有任何辅助函数从实体上拾取边构造 Reference。
这两个函数目前对 AI 完全不可用。

### Bug 3：add_rect_pattern / add_circ_pattern 参数不完整

pycatia `add_new_rect_pattern` 需要方向引用（`i_dir1: Reference, i_dir2: Reference`），
当前封装硬传了 `feature` 对象代替方向，大概率运行时失败。

---

## 下一步工作（按优先级）

### P0：B-Rep 名称实测（前置，解锁后续所有工作）

在 `experiments/` 写探索脚本，建一个典型零件（Pad + Pocket + Hole），
用 VBA Evaluate 或遍历 `Part.GeometricElements` 打印所有面/边的 B-Rep 名称，
总结命名规律（`Face:(Brp:(Pad.1;N);...)` 的 N 与 top/bottom/side 的对应关系）。

```python
# 目标脚本：experiments/explore_brepnames.py
# 建：100x50x20 的 Pad，在顶面建 40x40 的 Pocket，
# 打印所有面的 B-Rep 名称和法向量，总结规律
```

### P1：Reference 辅助层（核心，其余均依赖）

新增到 `modeling.py`：

```python
def get_face_of_feature(part, feature_name: str, position: str) -> Reference:
    """
    position: "top" | "bottom" | "side_1" | "side_2" ...
    内部通过 B-Rep 名称规律构造 Reference。
    """

def get_edges_of_feature(part, feature_name: str, position: str) -> list[Reference]:
    """
    position: "top" | "bottom" | "side"
    返回该位置所有边的 Reference 列表。
    """

def get_axis_ref(part, axis: str) -> Reference:
    """axis: "x" | "y" | "z" → origin_elements 的坐标轴引用"""

def get_plane_ref(part, plane: str) -> Reference:
    """plane: "xy" | "yz" | "zx"（_plane_ref 的公开版本）"""
```

### P2：草图增强

```python
def add_sketch_on_face(part, face_ref) -> Sketch:
    """在已有实体的面上建草图（扩展 add_sketch 接受 Reference）"""

def draw_line(sketch, x1, y1, x2, y2) -> None:
    """单独画一条线段（旋转体轮廓/轴线需要）"""
```

同时修改 `add_sketch` 兼容 Reference 输入：

```python
def add_sketch(part, plane) -> Sketch:
    # plane: "xy"/"yz"/"zx" 字符串，或直接传入 Reference 对象
```

### P3：新增特征

```python
# 基于草图
def add_shaft(part, sketch, start_angle=0.0, end_angle=360.0) -> Shaft
def add_groove(part, sketch, start_angle=0.0, end_angle=360.0) -> Groove
def add_hole_at_point(part, face_ref, x, y, diameter, depth) -> Hole

# 语义化倒圆角/倒角（不需要 AI 构造 edge_ref）
def add_fillet_edges(part, feature_name, position, radius) -> ConstRadEdgeFillet
    # position: "top" | "bottom" | "all"
def add_chamfer_edges(part, feature_name, position, length, angle=45) -> Chamfer

# 变换
def add_mirror(part, feature, plane) -> Mirror
    # plane: "xy"/"yz"/"zx" 字符串 或 Reference
def add_rotate(part, feature, axis, angle) -> Rotate
    # axis: "x"/"y"/"z" 字符串 或 Reference
def add_translate(part, feature, dx=0, dy=0, dz=0) -> Translate

# 修复阵列
def add_rect_pattern(part, feature, nx, ny, dx, dy, dir_x="x", dir_y="y")
def add_circ_pattern(part, feature, count, total_angle=360, center=None, axis="z")
```

### P4：更新 tools_schema description

`tool_run_modeling_script` 的 schema description 中暴露给 AI 的 API 清单需要同步更新，
加入所有 P1-P3 新增函数的签名和用法说明。

---

## 技术参考

### ShapeFactory 关键方法签名（pycatia 已验证）

```python
add_new_shaft(i_sketch: Sketch) -> Shaft
    # 草图须含轮廓线 + 旋转轴线

add_new_groove(i_sketch: Sketch) -> Groove
    # 同上，但切除实体

add_new_hole(i_support: Reference, i_depth: float) -> Hole
    # i_support: 面引用，孔垂直于面

add_new_hole_from_sketch(i_sketch: Sketch, i_depth: float) -> Hole
    # 草图须含点，孔轴穿过该点垂直于草图平面

add_new_chamfer(i_object_to_chamfer: Reference, i_propagation: int,
                i_mode: int, i_orientation: int,
                i_length1: float, i_length2_or_angle: float) -> Chamfer
    # mode=1(两长度) or 2(长度+角度)
    # 常用: propagation=1, mode=2, orientation=1

add_new_solid_edge_fillet_with_constant_radius(
    i_edge_to_fillet: Reference, i_propag_mode: int, i_radius: float
) -> ConstRadEdgeFillet
    # 注意：add_new_edge_fillet_with_constant_radius 已废弃(V5R14)，用 solid 版本

add_new_mirror(i_mirroring_element: Reference) -> Mirror
    # i_mirroring_element: 平面引用

add_new_rotate2(i_axis: Reference, i_angle: float) -> AnyObject
    # 返回值需转为 Rotate

add_new_translate2(i_distance: float) -> Translate
    # 方向需通过返回对象的 hybrid_shape 进一步设置

add_new_rect_pattern(i_shape_to_copy, i_nb_dir1, i_nb_dir2,
                     i_step_dir1, i_step_dir2,
                     i_pos_dir1, i_pos_dir2,
                     i_dir1: Reference, i_dir2: Reference,
                     i_rev_dir1, i_rev_dir2, i_rotation_angle) -> RectPattern
    # 方向引用必须是真正的 Reference（线段/轴），不能传特征对象

add_new_circ_pattern(i_shape_to_copy, i_nb_radial, i_nb_angular,
                     i_step_radial, i_step_angular,
                     i_pos_radial, i_pos_angular,
                     i_center: Reference, i_axis: Reference,
                     i_reversed, i_angle, i_radius_aligned) -> CircPattern
```

### Part.create_reference_from_b_rep_name

```python
part.part.create_reference_from_b_rep_name(b_rep_name: str) -> Reference
# 直接用 B-Rep 名称字符串构造 Reference
# 名称格式示例（待实测确认）：
#   顶面：  "Face:(Brp:(Pad.1;2);None:())"
#   底面：  "Face:(Brp:(Pad.1;1);None:())"
#   顶部边："Edge:(Brp:(Pad.1;...);...)"
```

### 变换特征的 Rotate 对象属性

```python
rotate.angle_value: float   # 可读写，旋转角度
rotate.axis: Reference      # 可读写，旋转轴
```

### Mirror 对象属性

```python
mirror.mirroring_plane: Reference  # 可读写，镜像平面
```

---

## 测试用例（P3 完成后验证）

每个用例对应一个 `experiments/` 脚本：

```
test_shaft.py           圆柱体（旋转体）+ 旋转槽
test_pocket_on_face.py  长方体 + 顶面挖槽（验证 add_sketch_on_face）
test_hole_on_face.py    长方体 + 顶面打孔（验证 add_hole_at_point）
test_fillet_chamfer.py  长方体 + 顶边圆角 + 底边倒角（验证语义化边引用）
test_mirror.py          L 形截面旋转体 + 镜像
test_pattern.py         带孔底座 + 矩形阵列（验证修复后的 add_rect_pattern）
test_complex_part.py    综合：底座 + 凸台 + 顶面孔 + 圆角（AI 一次性建出）
```

---

## 关于 agent 感知当前模型状态

当前 `tool_run_modeling_script` 执行完后返回特征列表，
但 agent 在生成脚本前无法知道 CATIA 当前打开了什么、活动零件的特征树是什么。

后续需新增 `tool_get_catia_model_state` 工具：

```python
# 返回结构：
{
  "active_doc": "Part7.CATPart",
  "open_docs": [
    {
      "name": "Part7.CATPart",
      "type": "CATPart",
      "path": "...",
      "features": ["草图.1", "凸台.1"],
      "sketches": ["草图.1"]
    }
  ]
}
```

这个工具实现成本低，但属于"在已有零件上修改"工作流的前置，
当前短期目标（一次描述建复杂零件）不依赖它，**暂缓实现**。

---

*Handoff 写于 2026-06-02，分支 feat/modeling-layer，commit 3959b47*
