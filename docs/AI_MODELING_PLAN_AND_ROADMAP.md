# AI 辅助建模完整计划与路线图

## 背景与验证

已验证 pycatia 写入 API 实际可用（`experiments/debug_create_part.py`），
完整链路跑通：新建零件 → 在 XY 平面新建草图 → 画矩形 → Pad → 刷新 → Analyze 读取几何中心。

连接层使用项目自有的 `wrap_application()` / `wrap_product()`，不使用 pycatia 的 `catia()` 入口，
保持连接稳定性。

---

## 核心架构思路

### 脚本驱动建模（主流程）

AI 作为"规划者"，**一次性生成完整的 Python 脚本**，程序直接执行脚本完成建模。

```
用户描述 → AI 思考 → 生成 build(ctx) 脚本 → 程序执行 → 结果反馈给 AI
```

脚本通过 `ctx`（`ModelingContext`）调用所有建模 API，不需要任何 import。

---

## API 设计原则

### 语义化几何查询（核心未解决问题）

AI 能建多复杂的零件，取决于能否定位几何元素（面、边）。
这是**当前最关键的未完成功能**，优先级高于阵列、倒角等。

目标 API：
```python
get_top_face(part)                    # 最高 Z 的面
get_face_by_normal(part, [0, 0, 1])   # 法向量为 Z+ 的面
get_face_of_feature(part, "凸台.1", "top")  # 某特征的特定面
add_sketch_on_face(part, face_ref)    # 在已有实体面上直接建草图
```

---

## 阶段计划

### 阶段一：建模工具层 ✅（已完成）

`catia_copilot/catia/modeling.py` + `ModelingContext`，已验证端到端可用。

**已实现并验证的 API：**

| 分类 | 函数 |
|---|---|
| 文档 | `create_part(name, nomenclature)` / `get_active_part` / `update_part` / `save_part` |
| 草图-基准面 | `add_sketch(plane)` / `add_sketch_at_height(h, base)` |
| 草图-B-Rep面 | `add_sketch_on_pad_top` / `add_sketch_on_pad_bottom` / `add_sketch_on_pad_side` |
| 草图绘图 | `draw_rect` / `draw_circle` / `draw_arc` / `draw_line` / `draw_slot` / `draw_point` |
| 特征 | `add_pad(depth, symmetric, second_depth)` / `add_pocket` / `add_hole_from_sketch` |
| 旋转 | `add_shaft(axis)` / `add_groove(axis)` |
| 修饰 | `add_fillet_edges(edge_refs, r)` / `add_auto_fillet(r, inner_r)` / `add_chamfer` |
| 阵列 | `add_rect_pattern` / `add_circ_pattern`（方向参数有 bug，暂不可用） |
| 查询 | `list_features` / `list_sketches` / `get_mass_props` |
| 几何查询-Pad | `get_pad_faces` / `get_pad_faces_by_normal` / `get_pad_face_edges` |
| 几何查询-Pocket | `get_pocket_faces` / `get_pocket_face_edges` / `get_pocket_opening_edges` |
| 几何查询-Shaft | `get_shaft_faces` / `get_shaft_face_edges` |

---

### 阶段一补充 A：B-Rep 面直接支撑草图 ✅（已解决，2026-06-11）

**方法**：VBA 宏录制发现 `CreateReferenceFromName` 接受 `Selection_RSur:` 格式，
而之前尝试的 `CreateReferenceFromBRepName` 只能返回 `WithTemporaryBody` 引用（不可作为草图支撑）。

```python
en_name = "Pad.1"  # 从 凸台.1 推导
ref_str = f"Selection_RSur:(Face:(Brp:({en_name};2);None:());{en_name}_ResultOUT)"
ref_com = part_com.CreateReferenceFromName(ref_str)
sketch  = part.main_body.sketches.add(PyRef(ref_com))
```

API：`ctx.add_sketch_on_pad_top/bottom/side(part, pad)`

---

### 阶段一补充 B：几何查询 + BRep 边引用扩展 ✅（已完成，2026-06-11）

**几何查询**（不依赖 SPA，纯从草图坐标系推导）：
- `get_pad_faces_by_normal(part, pad, (0,0,1))` → 找顶面
- `get_pad_face_edges(part, pad, face_info)` → 取该面所有边引用
- Pocket / Shaft 同理有对应的 `get_pocket_*` / `get_shaft_*`

**BRep 边引用扩展**（宏录制验证）：
- Pocket 底楞、侧楞、开口楞（开口面=下层Pad顶面）
- Shaft 所有面均用侧面格式（无独立顶/底）
- 圆柱 Pad 侧面格式与矩形相同，已天然兼容

**新增修饰 API**：
- `add_auto_fillet(part, radius, inner_radius=None)` — 自动圆角，无需指定边

**Bug 修复**：
- `create_part` 命名无效：`part.part.PartNumber` → `Product.PartNumber`
- `add_shaft/groove` 轴线顺序颠倒：对调先建轴线再建特征
- `_pad_geometry` 草图边数穷举越界：改用 `GeometricElements` 计数

---

### 阶段二：AI 工具定义层 ✅（已完成）

`catia_copilot/ai/tools.py` — `run_modeling_script` 工具，含完整 System Prompt。

---

### 阶段三：脚本生成与执行 ✅（进行中）

已实现 `ModelingContext`（`build(ctx)` 签名）、结构化反馈、步骤记录。

---

## 后续计划

### 方向 C：AI 端到端建模验证（下一步）

用现有 API 让 AI Agent 完成较复杂零件的完整建模（顶面叠加 + 侧面草图 + 倒圆角），
验证整体链路，发现实际问题再针对性修。

### 方向 D：其他功能扩展（低优先级）

- Shell（抽壳）
- Mirror（镜像）
- 阵列修复（`add_rect_pattern` / `add_circ_pattern` 方向参数 bug）
- 装配层 API

---

## 关键技术结论汇总

### InWorkObject 机制

`update_part()` 后 IWO 停在最后一个固体特征。
`InsertHybridShape` 插入位置由当前 IWO 决定。
**修复方式**：操作前重置 `part.in_work_object = part.main_body`。

### 三轴旋转体约束映射（已验证）

| axis | 草图平面 | H 轴 | V 轴 | 轮廓约束 |
|------|---------|------|------|---------|
| `"z"` | `zx` | Z | X | H(X) > 0 |
| `"y"` | `xy` | X | Y | V(Y) > 0 |
| `"x"` | `xy` | X | Y | H(X) > 0（旋转轴=H） |

### 偏移平面草图（仍保留，用于已知高度的场景）

```python
# add_sketch_at_height — 手动指定高度，不关联特征
# 适用于：已知具体高度、不需要关联的场景
```

**注意**：`add_sketch_on_pad_top` 已改用 B-Rep 面直接支撑，两个 API 现在行为不同。

---

*最后更新：2026-06-11（方向A/B完成，方向C待验证）*
