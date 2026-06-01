# CATIA V5 PartDocument COM API 参考

本文档记录通过 `win32com` 访问 CATIA V5 `PartDocument` 中各类 COM 对象的方式，
基于实测（`tmp/explore_part_document.py`）整理，供开发参考。

---

## 连接入口

```python
from catia_copilot.catia.connection import get_catia_v5_application

app  = get_catia_v5_application()   # CATIA.Application COM 对象
doc  = app.ActiveDocument           # 当前活动文档
part = doc.Part                     # Part 对象（仅 CATPart 有效）
```

> `get_catia_v5_application()` 确保只连接 CATIA V5，不会误启动 3DEXPERIENCE，
> 详见 `catia_copilot/catia/connection.py`。

---

## 文档层（Document）

```python
doc.Name      # 文件名，如 "Front_Upright_0421.CATPart"
doc.FullName  # 完整路径，如 "D:\...\Front_Upright_0421.CATPart"
doc.Part      # → Part 对象（见下节）
doc.Product   # → Product 对象（CATPart 同样有此接口，用于读写元数据）
```

---

## 元数据读写（通过 `doc.Product`）

CATPart 和 CATProduct 均可通过 `doc.Product` 读写内置属性。

### 读取

```python
product = doc.Product

product.PartNumber    # 零件号，如 "Front_Upright_0421"
product.Revision      # 版本，如 "A"
product.Definition    # 定义，如 "Front_Upright_0421"
product.Nomenclature  # 命名（中文名称），如 "前转向节"
product.DescriptionRef  # 描述，如 "描述一二"
product.Source        # 来源：0=Unknown, 1=Made, 2=Bought
```

### 写入

直接赋值即可，无需特殊步骤（CATPart 不需要 `ApplyWorkMode`）：

```python
product.PartNumber   = "NEW_PN_001"
product.Revision     = "B"
product.Definition   = "新定义"
product.Nomenclature = "新命名"
product.DescriptionRef = "新描述"
product.Source       = 1    # int：0/1/2
```

写入后需在 CATIA 中手动保存，或调用 `doc.Save()`。

### 通过 `product.ReferenceProduct` 写入（CATProduct 子节点）

对于 CATProduct 树中的子节点，写入时需优先尝试 `ReferenceProduct`：

```python
# bom_write.py 中的模式
for target in [product.ReferenceProduct, product]:
    try:
        setattr(target, "PartNumber", value)
        break
    except Exception:
        continue
```

### 用户自定义属性（UserRefProperties）

```python
props = product.UserRefProperties         # 或 product.ReferenceProduct.UserRefProperties

# 读取
val = props.Item("物料编码").Value

# 更新已有属性
props.Item("物料编码").Value = "MAT-001"

# 新建属性（不存在时）
props.CreateString("物料编码", "MAT-001")
```

### 已知可写属性映射（`constants.PRODUCT_ATTR_WRITE_MAP`）

| BOM 列名 | COM 属性名 | 说明 |
|----------|-----------|------|
| `Part Number` | `PartNumber` | 零件号 |
| `Nomenclature` | `Nomenclature` | 命名 |
| `Revision` | `Revision` | 版本 |
| `Definition` | `Definition` | 定义 |
| `Source` | `Source` | 来源（写 int：0/1/2） |
| `Description` | `DescriptionRef` | 描述 |

---

## Part 对象（`doc.Part`）

### 基础属性

```python
part = doc.Part

part.Name          # Part 名称（同文件名去扩展名）
part.UserSurfaces  # UserSurfaces 集合（FT&A 相关）
```

### 当前激活对象

```python
part.InWorkObject       # 当前激活的 Body 或 Feature（COM 对象）
part.InWorkObject.Name  # 名称，如 "零件几何体"

# 注意：part.CurrentBody / part.CurrentShape 在未激活 Body 时会报错
```

---

## 原点基准元素（OriginElements）

三个全局基准平面，始终存在。

```python
origin = part.OriginElements
xy = origin.PlaneXY   # XY 平面（COM Reference 对象）
yz = origin.PlaneYZ   # YZ 平面
zx = origin.PlaneZX   # ZX 平面
```

---

## 坐标系集合（AxisSystems）

```python
axis_systems = part.AxisSystems      # AxisSystems 集合
count = axis_systems.Count
ax = axis_systems.Item(1)            # 按索引（1-based）
ax = axis_systems.Item("坐标系.1")   # 按名称
ax.Name                              # 名称
```

---

## 几何元素集合（GeometricElements）

包含所有 3D 参考点、线、面（包括 Body 内部的边界引用）。元素数量通常较多（本例 944 个）。

```python
geo_elts = part.GeometricElements
count = geo_elts.Count
elt = geo_elts.Item(1)
elt.Name   # 如 "原点"、"直线.1"、"圆.1"
```

---

## Bodies（实体集合）

```python
bodies = part.Bodies
count  = bodies.Count

for i in range(1, count + 1):
    body = bodies.Item(i)
    body.Name    # 如 "零件几何体"、"PartBody"

# 注意：bodies.MainBody 在部分文件中会报错（未设置 MainBody 标记）
# 推荐改用：
main_body = bodies.Item(1)
```

### Body 内部集合

```python
# 草图集合
sketches = body.Sketches
sk = sketches.Item(1)
sk.Name    # 如 "草图.1"

# 特征集合（凸台、凹槽、倒角等）
shapes = body.Shapes
sh = shapes.Item(1)
sh.Name    # 如 "凸台.1"

# 混合形状（Body 内的 GSD 特征）
hybrid_shapes = body.HybridShapes
hs = hybrid_shapes.Item(1)
hs.Name    # 如 "直线.1"

# 有序几何集合（通常为 0）
ogs = body.OrderedGeometricalSets
```

---

## HybridBodies（GSD 几何体集合）

用于存放曲面/线框特征（GenerativeShapeDesign 工作台）。
**注意：HybridBody 没有 `Sketches` 属性，草图只属于 Body。**

```python
hybrid_bodies = part.HybridBodies
count = hybrid_bodies.Count

for i in range(1, count + 1):
    hb = hybrid_bodies.Item(i)
    hb.Name    # 如 "外部参考"、"硬点"

    # HybridBody 内的 GSD 特征
    hs_coll = hb.HybridShapes
    for j in range(1, hs_coll.Count + 1):
        hs = hs_coll.Item(j)
        hs.Name
```

---

## 有序几何集合（OrderedGeometricalSets）

```python
ogs = part.OrderedGeometricalSets   # Part 级别的 OGS
count = ogs.Count                   # 若不使用 OGS 工作台则为 0
```

---

## 约束集合（Constraints）

```python
constraints = part.Constraints
count = constraints.Count           # 3D Part 约束，草图约束不在此处
c = constraints.Item(1)
c.Name
```

---

## 关系/公式集合（Relations）

```python
relations = part.Relations
count = relations.Count
rel = relations.Item(1)
rel.Name    # 如 "公式.1"
```

---

## 参数集合（Parameters）

包含文档中所有参数，含草图约束尺寸，数量通常很大（本例 1796 个）。

```python
parameters = part.Parameters
count = parameters.Count

p = parameters.Item(1)
p.Name           # 完整路径名，如 "Front_Upright_0421\草图.1\距离.1\Distance"
p.Value          # 当前值（数值或布尔）
p.UserAccessMode # 1=普通, 0=只读（来自公式驱动）
```

---

## 工厂对象（Factories）

用于**创建**新特征，不用于读取。

```python
sf  = part.ShapeFactory         # ShapeFactory：创建实体特征（凸台、凹槽等）
hsf = part.HybridShapeFactory   # HybridShapeFactory：创建 GSD 特征（点、线、面等）
ifc = part.GetCustomerFactory("InstanceFactory")  # InstanceFactory：实例化 UDF/PowerCopy
```

---

## FT&A 注释集合（AnnotationSets）

需要 FT&A 模块授权，无此模块时集合为空（Count=0）。

```python
ann_sets = part.AnnotationSets
count = ann_sets.Count
```

---

## 实测数据参考（Front_Upright_0421.CATPart）

| 集合 | Count |
|------|-------|
| AxisSystems | 1 |
| GeometricElements | 944 |
| Bodies | 2（零件几何体 / Steer） |
| Body[1].Sketches | 23 |
| Body[1].Shapes | 46 |
| Body[1].HybridShapes | 27 |
| Body[2].Sketches | 9 |
| Body[2].Shapes | 12 |
| Body[2].HybridShapes | 10 |
| HybridBodies | 4（外部参考 / 硬点 / 草图几何集合上/下半） |
| HybridBody[i].HybridShapes | 2 / 5 / 25 / 29 |
| OrderedGeometricalSets | 0 |
| Constraints | 0 |
| Relations | 1 |
| Parameters | 1796 |
| AnnotationSets | 0 |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `catia_copilot/catia/connection.py` | `get_catia_v5_application()` 连接入口 |
| `catia_copilot/catia/bom_collect.py` | 读取 Product 树属性 |
| `catia_copilot/catia/bom_write.py` | 写回 Product 属性（含 UserRefProperties） |
| `catia_copilot/constants.py` | `PRODUCT_ATTR_READ_MAP` / `PRODUCT_ATTR_WRITE_MAP` |
| `tmp/explore_part_document.py` | PartDocument 对象探索脚本（可重复运行） |
| `tmp/test_product_write.py` | Product 可写属性测试脚本（读→改→验证→还原） |
