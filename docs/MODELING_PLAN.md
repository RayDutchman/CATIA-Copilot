# AI 辅助建模功能计划

## 背景

已验证 pycatia 写入 API 实际可用（`experiments/debug_create_part.py`），
完整链路跑通：新建零件 → 在 XY 平面新建草图 → 画矩形 → Pad → 刷新 → Analyze 读取几何中心。

连接层使用项目自有的 `wrap_application()` / `wrap_product()`，不使用 pycatia 的 `catia()` 入口，
保持连接稳定性。

---

## 目标

让 AI 通过自然语言指令在 CATIA 中完成零件建模。
用户说："建一个 100×50×20 的底座，四角有 M4 通孔"，
AI 拆解为工具调用序列，在 CATIA 中自动完成操作。

---

## 阶段一：建模工具层（`catia_copilot/catia/modeling.py`）

封装 pycatia 建模操作为稳定的 Python 函数，屏蔽 COM / pycatia 细节，供 AI 工具层调用。

### 文件创建类

```python
create_part(name: str) -> Part
    # 新建 CATPart 文档，返回 pycatia Part 对象
    # 内部：app.documents.add("Part") → PartDocument → part
```

### 草图类

```python
add_sketch(part, plane: str) -> Sketch
    # plane: "xy" | "yz" | "zx"
    # 内部：origin_elements.plane_xy/yz/zx
    #        create_reference_from_object()  ← 必须这一步，plane_xy 返回 AnyObject 非 Reference
    #        part.main_body.sketches.add(ref)

draw_rect(sketch, x: float, y: float, w: float, h: float) -> None
    # 在草图内画矩形，单位 mm
    # 内部：open_edition() → Factory2D.create_line × 4 → close_edition()

draw_circle(sketch, cx: float, cy: float, r: float) -> None
    # 画圆
    # 内部：open_edition() → Factory2D.create_circle → close_edition()
```

### 特征类

```python
add_pad(part, sketch, depth: float) -> Pad
    # 拉伸，单位 mm
    # 内部：part.shape_factory.add_new_pad(sketch, depth)

add_pocket(part, sketch, depth: float) -> Pocket
    # 挖槽，单位 mm
    # 内部：part.shape_factory.add_new_pocket(sketch, depth)

add_hole(part, sketch, diameter: float, depth: float) -> Hole
    # 简单盲孔，在已有草图（点）上打孔
    # 内部：part.shape_factory.add_new_hole_from_sketch(sketch, diameter, depth)

add_fillet(part, edge_ref, radius: float) -> EdgeFillet
    # 圆角

add_chamfer(part, edge_ref, length: float, angle: float) -> Chamfer
    # 倒角

add_rect_pattern(part, feat, nx: int, ny: int, dx: float, dy: float)
    # 矩形阵列

add_circ_pattern(part, feat, n: int, total_angle: float)
    # 圆形阵列
```

### 查询类（AI 感知当前模型状态）

```python
list_features(part) -> list[str]
    # 返回当前 Body 所有特征名称列表

get_mass_props(part) -> dict
    # 调用 Analyze API，返回 {mass, cog, inertia}
    # 复用现有 _measure_part_mass_props_analyze 路径

update_part(part) -> None
    # part.update()

save_part(part, path: str) -> None
    # part 文档另存为
```

### 关键技术坑（已验证）

| 问题 | 解决方案 |
|---|---|
| `plane_xy` 返回 `AnyObject` 而非 `Reference` | 必须经 `part.create_reference_from_object()` 转换 |
| `Part` 没有 `sketches` 属性 | 草图挂在 `part.main_body.sketches` 下 |
| 边/面的 Reference 构造 | 通过 `part.create_reference_from_b_rep_name()` 或遍历几何元素定位 |

---

## 阶段二：AI 工具定义层（扩展 `catia_copilot/ai/tools.py`）

将阶段一函数包装为 AI 可调用工具，定义 JSON schema。

```python
@tool
def catia_create_part(name: str) -> str:
    """在 CATIA 中新建一个零件文档，返回零件名称"""

@tool
def catia_add_sketch(plane: Literal["xy", "yz", "zx"]) -> str:
    """在指定基准平面新建草图，返回草图名"""

@tool
def catia_draw_rectangle(sketch_name: str,
                         x: float, y: float,
                         width: float, height: float) -> str:
    """在草图中绘制矩形，坐标和尺寸单位均为 mm"""

@tool
def catia_draw_circle(sketch_name: str,
                      cx: float, cy: float, radius: float) -> str:
    """在草图中绘制圆，单位 mm"""

@tool
def catia_add_pad(sketch_name: str, depth: float) -> str:
    """将草图拉伸指定深度（mm），返回特征名"""

@tool
def catia_add_pocket(sketch_name: str, depth: float) -> str:
    """在实体上按草图挖槽"""

@tool
def catia_add_hole(sketch_name: str,
                   diameter: float, depth: float) -> str:
    """打孔，diameter/depth 单位 mm"""

@tool
def catia_add_fillet(feature_name: str, radius: float) -> str:
    """对指定特征的所有边倒圆角"""

@tool
def catia_add_rect_pattern(feature_name: str,
                            nx: int, ny: int,
                            dx: float, dy: float) -> str:
    """矩形阵列，nx/ny 为数量，dx/dy 为间距（mm）"""

@tool
def catia_update() -> str:
    """刷新当前零件模型"""

@tool
def catia_get_mass_props() -> dict:
    """读取当前零件的质量、重心、惯量"""

@tool
def catia_list_features() -> list[str]:
    """列出当前零件所有特征名称，供 AI 感知模型状态"""
```

---

## 阶段三：对话流集成（`catia_copilot/ai/`）

- **意图识别**：区分"建模请求"与"信息查询"
- **多步规划**：LLM 将用户描述拆解为工具调用序列，执行前向用户展示计划并确认
- **状态感知**：每步执行后调用 `catia_list_features` / `catia_get_mass_props`，
  让 LLM 知道当前模型状态
- **错误恢复**：某步失败时 LLM 诊断并给出修正方案，而不是中止整个流程

---

## 阶段四：测试与迭代（`experiments/`）

按复杂度递增验证：

| 用例 | 涉及功能 |
|---|---|
| 长方体 | create_part + draw_rect + pad |
| 带通孔底座 | + pocket / hole |
| 法兰盘 | draw_circle + shaft + circ_pattern |
| 壳体 | + fillet + shell |
| 多特征组合件 | 综合 |
| 装配体 | ProductDocument + 摆放位置 |

---

## 最小可行版本（MVP）

阶段一完成以下五个函数即为 MVP：

1. `create_part`
2. `add_sketch`（仅支持 xy/yz/zx 三个基准面）
3. `draw_rect`
4. `add_pad`
5. `update_part`

能让 AI 说"建一个 100×50×20 的长方体"并在 CATIA 中跑通，MVP 完成。

---

## 文件结构

```
catia_copilot/
  catia/
    modeling.py          ← 阶段一（新建）
  ai/
    tools.py             ← 阶段二（扩展）
experiments/
  debug_create_part.py   ← 已验证的端到端测试
docs/
  MODELING_PLAN.md       ← 本文件
```

---

*计划制定日期：2026-06-02*
