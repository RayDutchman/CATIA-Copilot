# CATIA V5 B-Rep 命名与建模 API 参考

> 本文档记录通过 `pycatia` + `win32com` 对 CATIA V5（中文安装）进行自动化建模时的关键发现。
> 由 M1 探索脚本（`experiments/explore_brepnames_v*.py`）实验得出。

---

## 1. 特征内部名称映射

CATIA 中文安装时，UI 显示中文名，但 B-Rep 内部使用英文名：

| UI 中文名 | COM/BRep 英文内部名 |
|-----------|-------------------|
| 凸台.1    | Pad.1             |
| 凹槽.1    | Pocket.1          |
| 旋转体.1  | Shaft.1           |
| 环形槽.1  | Groove.1          |

---

## 2. B-Rep Reference 构造

### 2.1 输入格式（中文特征名）

```python
brep_name = "Face:(Brp:(凸台.1;N);None:())"
ref = part_com.CreateReferenceFromBRepName(brep_name, part_com)
```

- `N` 为面序号（0 起，穷举）
- `CreateReferenceFromBRepName` 对中文特征名有效，不报错
- 返回的 Reference 对象有 `.DisplayName` 属性

### 2.2 DisplayName 格式（英文内部格式）

```
FSur:(Face:(Brp:(Pad.1;N);None:();Cf8:());WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR3_SP2)
```

- 内部使用英文名（`Pad.1`），不受 UI 语言影响
- 用 DisplayName 重新构造的 Reference 与原始 Reference 等效

### 2.3 重要限制

- 通过 `CreateReferenceFromBRepName` 构造的 Reference **不能用于 SPA 测量**
  - `spa.GetMeasurable(ref).Area` → 失败
  - `spa.GetMeasurable(ref).GetNormal()` → 失败
- N=0..19 穷举时，所有 N 均能构造 Reference（不报错），但多数是无效引用（实际面数约为 6）
- **绕过方案**：见第 4 节（定位草图 + 坐标系识别）

---

## 3. GeometricElements 结构

`Part.GeometricElements` 在一个含单个 Pad 的零件中包含 **16 项**：

| 索引 | 类型（推测） | 说明 |
|------|------------|------|
| [1]  | `原点`   | **草图.1 的绝对轴：原点**（草图内部坐标系，非零件原点） |
| [2]  | `横向`   | **草图.1 的绝对轴：H 方向**（草图 H 轴，非零件 Z 轴） |
| [3]  | `纵向`   | **草图.1 的绝对轴：V 方向**（草图 V 轴，非零件 Y 轴） |
| [4]  | `绝对轴` | 草图绝对轴容器节点，不可直接用 |
| [5]~[8]  | `直线.1`~`直线.4` | 草图中的线段（约束线或轮廓边） |
| [9]~[16] | `点.1`~`点.8`     | 草图顶点 / 实体顶点 |

> **注意**：GE 中的 `原点`/`横向`/`纵向` 是草图 `绝对轴` 下的子节点，属于草图内部坐标系，
> **不是零件的 X/Y/Z 轴**。`GE[2]（横向）`在 ZX 平面草图 Shaft 测试里碰巧成功，
> 是因为该草图的 H 方向恰好与 Z 方向对齐，属于巧合，不能作为通用 Z 轴使用。

**真正的零件 Z 轴获取方式待确认**（见第 5.3 节）。

```python
from pycatia.in_interfaces.reference import Reference as PyRef

ge    = part_com.GeometricElements
ref   = PyRef(part_com.CreateReferenceFromObject(ge.Item(i)))
shaft.revolute_axis = ref  # 直线项可直接用
```

---

## 4. 定位草图（替代 B-Rep 面 Reference）

### 4.1 核心思路

实体 B-Rep 面无法直接通过 COM 获取可用的草图支撑 Reference。
**解决方案**：用坐标平面 + `set_absolute_axis_data` 定位草图到目标位置。

### 4.2 API

```python
# 9个 double：origin(3) + H轴方向(3) + V轴方向(3)
sketch.set_absolute_axis_data((
    ox, oy, oz,      # 草图原点（3D 坐标）
    hx, hy, hz,      # H 轴方向（单位向量）
    vx, vy, vz,      # V 轴方向（单位向量）
))

# 读取当前坐标系
ax = sketch.get_absolute_axis_data()
origin = ax[0:3]
h_axis = ax[3:6]
v_axis = ax[6:9]
```

### 4.3 在 Pad 顶面建草图（示例）

```python
import math

def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def normalize(v):
    mag = math.sqrt(sum(x*x for x in v))
    return tuple(x/mag for x in v) if mag > 1e-9 else v

# 在 Pad 顶面（高度 z_top）中心建草图
sk = ppy.main_body.sketches.add(plane_ref(ppy, "xy"))
sk.set_absolute_axis_data((
    cx, cy, z_top,   # origin = 目标位置（如顶面中心）
    1.0, 0.0, 0.0,   # H = X 方向
    0.0, 1.0, 0.0,   # V = Y 方向
))
# 草图法向 = H×V = (0,0,1)，向上拉伸
```

### 4.4 面方向识别

```python
ax     = sk.get_absolute_axis_data()
normal = normalize(cross(ax[3:6], ax[6:9]))

# normal ≈ (0,0,+1) → 顶面 / XY 平行面（朝上）
# normal ≈ (0,0,-1) → 底面 / XY 平行面（朝下）
# normal ≈ (+1,0,0) → YZ 平行侧面
# normal ≈ (0,+1,0) → XZ 平行侧面
```

---

## 5. Shaft（旋转体）建模

### 5.1 pycatia 属性名

```python
shaft.revolute_axis = z_ref   # 正确（不是 revolution_axis）
```

> `revolution_axis` 是错误的，pycatia 将 COM 属性 `RevoluteAxis` 重命名为 `revolute_axis`

### 5.2 正确创建流程

**关键约束**：
1. `add_new_shaft` 必须在 HybridShape 元素（轴线）**之前**调用
2. 轴线建好后设置 `revolute_axis`，再统一 `update()`
3. 不能先 `update()` 再设轴（会因 RevoluteAxis 未设置而失败，之后无法恢复）

```python
# 1. 在 ZX 平面画闭合轮廓（全在 V ≥ 0 侧，即不跨越旋转轴）
sk_s = ppy.main_body.sketches.add(plane_ref(ppy, "zx"))
f2d  = sk_s.open_edition()
f2d.create_line(r_inner, 0,  r_outer, 0   )  # 底边
f2d.create_line(r_outer, 0,  r_outer, height)  # 外边
f2d.create_line(r_outer, height, r_inner, height)  # 顶边
f2d.create_line(r_inner, height, r_inner, 0   )  # 内边
sk_s.close_edition()

# 2. 创建 Shaft（不立即 update）
shaft = ppy.shape_factory.add_new_shaft(sk_s)

# 3. 建 Z 轴参考线（update_object 不触发整体 update）
z_ref = make_z_line(ppy)   # 见 5.3

# 4. 设旋转轴 → update
shaft.revolute_axis = z_ref
ppy.update()
```

### 5.3 轴线参考线构造（X 轴 / Y 轴 / Z 轴）

**当前状态：已解决，树结构已验证（2026-06-02）**

**最终方案**：
1. `HybridShapeFactory.AddNewLinePtPt(pt_start, pt_end)` 创建轴方向线
2. `line.Name = "Z 轴"` 命名（与 CATIA GUI 一致）
3. `body_com.InsertHybridShape(line)` 将线直接插入 `MainBody.HybridShapes`（**不产生额外"几何图形集"**）
4. `CreateReferenceFromObject(line)` 获取 Reference → 赋给 `shaft.revolute_axis`

**已验证树结构**：`Z 轴` 出现在"旋转体.1"子节点下，与 CATIA GUI 手工建立的结果一致（可编辑）。
若执行"隔离"操作则与手工创建完全相同。

**注意**：点坐标为绝对无引用坐标（点.1、点.2），用户无法通过约束驱动，可接受。

```python
def _get_axis_ref(part_doc_com, axis_name, pt_start, pt_end):
    """通用：在 MainBody 中创建（或复用）命名轴线，返回 Reference。"""
    from pycatia.in_interfaces.reference import Reference as PyRef
    part_com = part_doc_com.Part
    body_com = part_com.MainBody
    hsf      = part_com.HybridShapeFactory
    # 复用已有
    try:
        return PyRef(part_com.CreateReferenceFromObject(
            body_com.HybridShapes.Item(axis_name)))
    except Exception:
        pass
    pt1  = hsf.AddNewPointCoord(*pt_start)
    pt2  = hsf.AddNewPointCoord(*pt_end)
    line = hsf.AddNewLinePtPt(
        part_com.CreateReferenceFromObject(pt1),
        part_com.CreateReferenceFromObject(pt2))
    line.Name = axis_name
    body_com.InsertHybridShape(line)
    return PyRef(part_com.CreateReferenceFromObject(line))

# 快捷函数
_get_z_axis_ref = lambda doc: _get_axis_ref(doc, "Z 轴", (0,0,0), (0,0,1))
_get_x_axis_ref = lambda doc: _get_axis_ref(doc, "X 轴", (0,0,0), (1,0,0))
_get_y_axis_ref = lambda doc: _get_axis_ref(doc, "Y 轴", (0,0,0), (0,1,0))
```

**排除的错误方案**：
- `Part.GeometricElements.Item(2)`：新 Part GE.Count=0，旧 Part GE[1-4] 是草图内部坐标系元素（非零件轴）
- `body.HybridShapes.Item("Z 轴")` 直接访问：仅在 GUI 已使用过 Z 轴的 Part 上存在，新 Part 不可用
- `Part.FindObjectByName("Z 轴")`：找到对象但 `CreateReferenceFromObject` 报错（不可引用）
- `Part.OriginElements`：仅提供三个基准平面，无轴线属性
- `body.AppendHybridShape`：Body 无此方法（HybridBody 才有）；正确方法是 `body.InsertHybridShape`

### 5.4 ZX 平面草图坐标系约定

在 ZX 平面的草图中：
- **H 轴 = Z 方向**（水平）
- **V 轴 = X 方向**（垂直）
- 旋转轴（默认）= V=0 的水平线 = Z 轴

因此草图坐标 `(H, V)` 对应三维 `(Z, X)`，旋转轮廓需满足 V ≥ 0（即 X ≥ 0）。

---

## 6. Groove（环形槽）建模

### 6.1 前提条件

**Groove 必须在已有实体的 Part 上操作**（可以是 Shaft 或 Pad）。

### 6.2 创建流程

```python
# 前提：Part 已有实体（ppy.update() 后）

# 草图：ZX 平面，闭合矩形，位于实体内部
sk_g = ppy.main_body.sketches.add(plane_ref(ppy, "zx"))
f2d  = sk_g.open_edition()
f2d.create_line(r0, z0,  r1, z0 )
f2d.create_line(r1, z0,  r1, z1 )
f2d.create_line(r1, z1,  r0, z1 )
f2d.create_line(r0, z1,  r0, z0 )
sk_g.close_edition()

groove = ppy.shape_factory.add_new_groove(sk_g)
groove.revolute_axis = z_ref   # 同 Shaft，需 Z 轴参考
ppy.update()
```

---

## 7. 待解决问题

| 编号 | 问题 | 优先级 |
|------|------|--------|
| P1 | ~~零件真正 Z 轴的 COM 获取方式~~ | **已解决**：`HybridShapeFactory.AddNewLinePtPt((0,0,0),(0,0,1))` → `CreateReferenceFromObject` |
| P2 | ~~实体 B-Rep 面 → 草图直接支撑（关联顶面建模）~~ | **已解决（2026-06-11）**，见下方 |
| P3 | 侧面（非水平面）建草图 | 中，可用 add_sketch_on_pad_top 处理水平面，侧面待探索 |
| P4 | Groove 在 Shaft Update 后 `AddNewGroove` 失败的根本原因 | 中 |

### P2 解决方案（B-Rep 面直接作为草图支撑）

**关键发现来源**：VBA 宏录制（在顶面手工建草图后录制宏）。

#### 正确方法

```python
# 顶面（idx=2）
ref_str = f"Selection_RSur:(Face:(Brp:({en_name};2);None:());{en_name}_ResultOUT)"

# 底面（idx=1）
ref_str = f"Selection_RSur:(Face:(Brp:({en_name};1);None:());{en_name}_ResultOUT)"

# 侧面（草图第 N 条边对应的面）
# 草图英文名：草图.N → Sketch.N（固定映射）
en_sk   = "Sketch." + cn_sketch_name.split(".")[-1]
ref_str = (f"Selection_RSur:(Face:(Brp:({en_pad};0:(Brp:({en_sk};{edge_index})));"
           f"None:());{en_pad}_ResultOUT)")

# 通用步骤
ref_com = part_com.CreateReferenceFromName(ref_str)   # 注意：FromName 不是 FromBRepName
sketch  = part.main_body.sketches.add(PyRef(ref_com))
```

**侧面 edge_index 与 draw_rect(x,y,w,h) 的对应关系**（由绘制顺序决定）：

| edge_index | 侧面 | 草图 origin | H 轴 | V 轴 |
|-----------|------|------------|------|------|
| 1 | Y=y 的面 | (x, y, 0) | X+ | Z+ |
| 2 | X=x+w 的面 | (x+w, y, 0) | Y+ | Z+ |
| 3 | Y=y+h 的面 | (x+w, y+h, 0) | X- | Z+ |
| 4 | X=x 的面 | (x, y+h, 0) | Y- | Z+ |

**已实现的 API（`modeling.py` + `ModelingContext`）**：
- `add_sketch_on_pad_top(part, pad)` — 顶面
- `add_sketch_on_pad_bottom(part, pad)` — 底面
- `add_sketch_on_pad_side(part, pad, edge_index)` — 侧面

---

## 8. 实验脚本索引

| 脚本 | 版本 | 主要内容 |
|------|------|---------|
| `experiments/explore_brepnames.py` | v7 | B-Rep 枚举 + SPA 尝试 |
| `experiments/explore_brepnames_v8.py` | v8 | SPA COM 方法诊断 |
| `experiments/explore_brepnames_v9.py` | v9 | CreateReferenceFromBRepName 参数变体 |
| `experiments/explore_brepnames_v10.py` | v10 | GeometricElements + RevoluteAxis |
| `experiments/explore_brepnames_v11.py` | v11 | **revolute_axis 修复 + Shaft 成功** |
| `experiments/explore_brepnames_v12.py` | v12 | sketches.add + get_absolute_axis_data |
| `experiments/explore_brepnames_v13.py` | v13 | PyRef 包装修复 + 面识别 |
| `experiments/explore_brepnames_v14.py` | v14 | 定位草图（set_absolute_axis_data）|

---

## 9. Pocket 面 / 边 BRep 格式（2026-06-11，宏录制验证）

### 9.1 面格式

| 面类型 | BRep 格式 | 说明 |
|--------|-----------|------|
| 底面（挖槽最深处） | `Face:(Brp:(Pocket.1;2);None:();Cf14:())` | 固定 idx=2 |
| 侧面 | `Face:(Brp:(Pocket.1;0:(Brp:(Sketch.2;N)));None:();Cf14:())` | N=草图边索引 |
| **开口面** | **不属于 Pocket**，是下层 Pad 的顶面 | 用 `_brep_face_top(en_pad)` |

> 注意：Pocket 无 `idx=1` 的面，开口面归属下层特征。

### 9.2 边格式（由宏直接验证）

```
# 开口楞（= Pad.1 顶面 × Pocket.1 侧面(草图边2)）
REdge:(Edge:(Face:(Brp:(Pad.1;2);None:();Cf14:());
             Face:(Brp:(Pocket.1;0:(Brp:(Sketch.2;2)));None:();Cf14:());
             None:(Limits1:();Limits2:());Cf14:());
       WithTemporaryBody;WithoutBuildError;
       WithSelectingFeatureSupport;MFBRepVersion_CXR29)

# Pocket 侧楞（侧面1 × 侧面4）
REdge:(Edge:(Face:(Brp:(Pocket.1;0:(Brp:(Sketch.2;1)));...);
             Face:(Brp:(Pocket.1;0:(Brp:(Sketch.2;4)));...);...))

# Pocket 底楞（侧面4 × 底面）
REdge:(Edge:(Face:(Brp:(Pocket.1;0:(Brp:(Sketch.2;4)));...);
             Face:(Brp:(Pocket.1;2);...);...))
```

`CreateReferenceFromBRepName` 第二参数传 `pocket_com`（`Shapes.Item(pocket.name)`）。

### 9.3 封装 API

```python
ctx.get_pocket_faces(part, pocket)                   # 底面+侧面列表（不含开口面）
ctx.get_pocket_face_edges(part, pocket, face_info)   # 某面的边引用列表
ctx.get_pocket_opening_edges(part, pocket, pad)      # 开口楞（需传下层 Pad）
```

---

## 10. Shaft（旋转体）面 / 边 BRep 格式（2026-06-11，宏录制验证）

### 10.1 面格式

Shaft **所有面**均使用侧面格式，**无** `idx=1/2` 的顶/底面：

```
Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;N)));None:();Cf14:())
```

N 对应草图轮廓边索引（1 起）。对矩形轮廓旋转体（4 条边）：外圆面/上端面/内圆面/下端面，具体哪条边对应哪个面取决于草图绘制顺序。

### 10.2 边格式（由宏直接验证）

```
# 外圆面(边2) × 上端面(边3) 的交线
REdge:(Edge:(Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;2)));...);
             Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;3)));...);...))

# 外圆面(边1) × 上端面(边2) 的交线
REdge:(Edge:(Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;1)));...);
             Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;2)));...);...))
```

`CreateReferenceFromBRepName` 第二参数传 `shaft_com`。

### 10.3 封装 API

```python
ctx.get_shaft_faces(part, shaft)                     # 所有面列表（type=surface）
ctx.get_shaft_face_edges(part, shaft, face_info)     # 某面与相邻面的交线
```

---

## 11. 几何查询 API 总结（2026-06-11）

所有几何查询均**不依赖 SPA**（SPA `GetPlane`/`GetDirection` 对所有已知 Reference 格式均失败），
改用从特征的草图坐标系（`get_absolute_axis_data`）纯数学推导：

| 数据 | 来源 |
|------|------|
| 面法向 | `H × V`（H/V 轴叉积，来自草图 axis_data） |
| 顶/底面位置 | 草图原点 ± depth |
| 侧面法向 | 基于 `draw_rect` 绘制顺序（边1=-V, 边2=+H, 边3=+V, 边4=-H） |
| 草图边数 | `GeometricElements` 中 `type ≠ 1（坐标轴）且 ≠ 2（点）` 的数量 |

```python
# 典型使用：按法向找面，取该面所有边，倒圆角
top   = ctx.get_pad_faces_by_normal(part, pad, (0,0,1))[0]
edges = ctx.get_pad_face_edges(part, pad, top)
ctx.add_fillet_edges(part, edges, 3.0)
ctx.update_part(part)
```

---

## 12. 其他 API 修复记录（2026-06-11）

### create_part 命名 bug
原代码 `part.part.PartNumber = name` 走 `CATIAPart` 接口，该接口无 `PartNumber` 属性，
静默失败（`except: pass` 吃掉异常），零件名始终为 CATIA 自动分配的 `PartN`。

修复：改为 `app_com.ActiveDocument.Product.PartNumber = name`。

新增 `nomenclature` 参数写入 `Product.Nomenclature`：
- `name`（PartNumber）：零件号、件号、编号 → 显示在特征树节点
- `nomenclature`：命名、用途描述（如"底座"）→ 不显示在树，存在属性中

### add_shaft / add_groove 轴线顺序
原代码先 `add_new_shaft` 再 `InsertHybridShape(轴线)`，导致特征树里轴线在 Shaft 之后
（CATIA 按插入时间排序，HybridShape 追加在已有实体特征末尾）。
修复：对调顺序，先建轴线再建 Shaft/Groove。

### add_auto_fillet
```python
ctx.add_auto_fillet(part, radius=3.0)              # 外/内角统一
ctx.add_auto_fillet(part, radius=3.0, inner_radius=1.0)  # 分别指定
ctx.update_part(part)
```
对应 VBA：`shapeFactory.AddNewAutoFillet(3.0, 3.0); autoFillet.RoundRadiusActivation = True`
