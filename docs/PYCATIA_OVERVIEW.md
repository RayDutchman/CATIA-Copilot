# pycatia 模块功能摘要

> 基于 pycatia 源码分析，记录各模块的主要类与功能，  
> 重点标注与本项目相关的 SAFEARRAY ByRef 封装模式和潜在改进点。

---

## 背景：SAFEARRAY ByRef 问题

CATIA COM API 中大量方法使用 `CATSafeArrayVariant` ByRef 输出参数（如
`Position.GetComponents`、`Analyze.GetGravityCenter`、`Inertia.GetInertiaMatrix` 等）。
win32com 无法正确处理此类调用：

- 无参调用报 `E_FAIL`
- 传入 `[0.0]*N` 时，C++ 层将结果写入 ByRef 数组，但 Python list 拿不到修改后的值，始终返回全零

**pycatia 的统一解法**：通过 `SystemService.Evaluate` 在 CATIA 进程内执行 VBA 宏，
在宏内分配数组并填充后作为函数返回值传回 Python。

---

## in_interfaces（52 个文件）— 核心基础接口

| 类 | 主要功能 |
|---|---|
| `Application` | CATIA 应用程序顶层对象：`Visible`、`ActiveDocument`、`Documents`、`SystemService`、`Printers`、`Windows` |
| `Document` | 通用文档对象：`FullName`、`Saved`、`Save()`、`SaveAs()`、`Close()`、`Activate()`、`ExportData()` |
| `Documents` | 文档集合：`Open()`、`NewFrom()`、`Count`、`Item()`；pycatia 提供 `__iter__` 迭代器 |
| `Selection` | 选择集：`Add()`、`Clear()`、`Search()`、`VisProperties`、`Count` |
| `VisPropertySet` | 图形属性集：`get_show()` — 直接无参调用 `GetShow()`，取返回元组 `[1]` |
| `Position` | 位置对象：`get_components()` — **VBA Evaluate 封装 ByRef**，返回 12 元素 tuple（列主序旋转矩阵 + 平移，单位 mm） |
| `Move` | 移动变换对象（`Position` 的父类）：`apply()` |
| `Reference` | 几何引用对象，用于约束、测量的几何输入 |
| `Camera3D` / `Camera2D` | 三维/二维相机视点 |
| `Viewer3D` / `Viewer2D` | 三维/二维视图窗口 |
| `FileSystem` / `File` / `Folder` | 文件系统访问（读取目录、文件属性等） |
| `SystemService` | 系统服务：`evaluate()`（VBA 宏注入）、`execute_script()`、`execute_processus()`、`environ()` |
| `AnyObject` | 所有 CATIA 对象的公共基类：`name`、`parent`、`application` |

**关键实现参考（`Position.get_components`）：**
```python
vba_code = """
Public Function get_components(position)
    Dim oAxisComponentsArray(11)
    position.GetComponents oAxisComponentsArray
    get_components = oAxisComponentsArray
End Function
"""
system_service = self.application.system_service
return system_service.evaluate(vba_code, 0, "get_components", [self.com_object])
```

---

## product_structure_interfaces（8 个文件）— 产品结构

| 类 | 主要功能 |
|---|---|
| `Product` | 产品/零件对象：`PartNumber`、`Definition`、`Nomenclature`、`Revision`、`Source`、`Description`；`analyze` 属性返回 `Analyze` 对象；`products` 返回子件集合；`UserRefProperties`（通过 `parameters` 访问） |
| `Products` | 产品集合：`Count`、`Item()`、`Add()`、`Remove()` |
| `ProductDocument` | 产品文档：`.product` 属性返回根 `Product` |
| `Analyze` | **`mass`（直接属性，kg）**；**`volume`**（直接属性，m³）；**`wet_area`**（直接属性）；`get_gravity_center()` — VBA Evaluate 封装，返回 3 元素 tuple（mm）；`get_inertia()` — VBA Evaluate 封装，返回 9 元素 tuple（列主序，kg·m²） |
| `Publications` / `Publication` | 发布接口管理 |
| `AssemblyConvertor` | 装配转换工具 |

**`Analyze` 与"惯量包络体"参数的区别：**

| 方式 | 数据来源 | 前提条件 | 适用场景 |
|---|---|---|---|
| `Analyze.mass / get_gravity_center / get_inertia` | CATIA 基于材料属性实时计算 | 零件须已赋材料 | 快速估算，不依赖用户手动测量 |
| `part.Parameters` 读 `惯量包络体.N\*` | 用户在 SPA 中执行"测量惯量+保持测量"后写入参数树 | 用户须预先执行 SPA 保持测量 | 精确值，本项目当前使用此方式 |
| `Inertia`（SPA 工作台） | SPA 实时计算 | 需激活 SPA 工作台 | 最精确，可读主惯性矩和主轴 |

---

## space_analyses_interfaces（18 个文件）— SPA 工作台

| 类 | 主要功能 |
|---|---|
| `SpaWorkbench` | SPA 工作台入口，通过 `part.get_item("SpaWorkbench")` 获取 |
| `Inertia` | 通过 `GetTechnologicalObject("Inertia")` 获取；`mass`（kg，直接属性）；`density`（kg/m³，可读写）；`get_cog_position()` — VBA Evaluate；`get_inertia_matrix()` — VBA Evaluate，返回 9 元素 tuple；`get_principal_axes()` — VBA Evaluate，返回 9 元素 tuple；`get_principal_moments()` — VBA Evaluate，返回 3 元素 tuple |
| `Measurable` | 可测量对象：`GetLength()`、`GetArea()`、`GetVolume()`、`GetMinimumDistance()` |
| `Distance` / `Distances` | 距离分析 |
| `Clash` / `Clashes` | 干涉/碰撞检查 |
| `Section` / `Sections` | 截面分析 |
| `InertiaS` / `Inertias` | 惯量集合管理 |

---

## mec_mod_interfaces（44 个文件）— 机械建模

| 类 | 主要功能 |
|---|---|
| `Part` | 零件对象：`Analyze`、`Bodies`、`HybridBodies`、`Parameters`、`Relations`、`Constraints`、`Origin`、`Axes`（坐标系集合） |
| `PartDocument` | 零件文档：`.part` 属性 |
| `Body` / `Bodies` | 实体 / 实体集合：`Shapes`、`Name` |
| `HybridBody` / `HybridBodies` | 混合几何体（曲面/线/点） |
| `Constraints` / `Constraint` | 约束管理：`Type`、`Status`、`Mode` |
| `AxisSystem` / `AxisSystems` | 坐标系：`Name`、`OriginPoint`、`XAxisDirection`、`YAxisDirection` |
| `PartServices` | 零件级服务接口 |
| `Sketches` | 草图集合 |
| `Shape` / `Shapes` | 特征形状基类 |

---

## knowledge_interfaces（41 个文件）— 知识工程 / 参数

| 类 | 主要功能 |
|---|---|
| `Parameters` | 参数集合：`Item(name)`、`CreateReal()`、`CreateInteger()`、`CreateString()`、`CreateBoolean()`、`CreateDimension()`、`Count` |
| `Parameter` | 通用参数基类：`Name`、`Value`（读写） |
| `RealParam` | 浮点参数：`Value`、`Minimum`、`Maximum` |
| `IntParam` | 整型参数：`Value` |
| `StrParam` | 字符串参数：`Value` |
| `BoolParam` | 布尔参数：`Value` |
| `Length` / `Angle` / `Dimension` | 带单位量纲参数：`Value`（SI）、`ValueAsString()` |
| `Relations` | 公式/规则集合：`Item()`、`Count` |
| `Formula` | 公式对象：`Body`（公式字符串）、`Parameter`（目标参数） |
| `Rule` / `Check` | 知识规则与检查 |
| `DesignTable` | 设计表：`Synchronize()`、`FileName` |
| `Optimization` | 优化器 |
| `Unit` / `Units` | 单位管理：`Symbol`、`Magnitude` |

---

## cat_mat_interfaces（9 个文件）— 材料

| 类 | 主要功能 |
|---|---|
| `Material` | 材料对象：`Name`、`Type`；通过 `GetTechnologicalObject("AnalysisMaterial")` 读取力学属性（密度等） |
| `Materials` | 材料集合：`Count`、`Item()` |
| `MaterialDocument` | 材料库文档（.CATMaterial）：`MaterialFamilies` |
| `MaterialFamilies` / `MaterialFamily` | 材料族：按族分组管理材料 |
| `MaterialManager` | 材料管理器：将材料应用到零件 |
| `AnalysisMaterial` | 分析材料属性：`Density`（kg/m³）、`YoungModulus`、`PoissonRatio` 等力学参数 |
| `PositionedMaterial` | 带位置的材料实例 |

---

## drafting_interfaces（39 个文件）— 图纸

| 类 | 主要功能 |
|---|---|
| `DrawingDocument` | 图纸文档：`DrawingRoot`、`Parameters`、`Update()`、`ExportData()` |
| `DrawingRoot` | 图纸根对象：`Sheets`、`ActiveSheet`、`Standard` |
| `DrawingSheet` | 图纸页：`Views`、`Activate()`、`Name`、`Scale` |
| `DrawingView` | 视图：`IsGenerative`、`GenerativeBehavior`、`Texts`、`Dimensions` |
| `DrawingViewGenerativeBehavior` | 生成式视图行为：`Document`（链接产品）、`Update()` |
| `DrawingText` / `DrawingTexts` | 文字注释：`Text`、`SetFontSize()` |
| `DrawingDimension` / `DrawingDimensions` | 标注：`Value`、`Tolerance` |
| `DrawingTable` / `DrawingTables` | 表格：`NumberOfRows`、`NumberOfColumns`、`GetCellString()` |
| `DrawingArrow` | 箭头注释 |

---

## hybrid_shape_interfaces（112 个文件）— 线框与曲面

覆盖 180+ 类，是 pycatia 中最大的模块，对应 CATIA 创成式外形设计（GSD）工作台。

| 分类 | 主要类 |
|---|---|
| 点 | `HybridShapePointCoord`、`HybridShapePointOnCurve`、`HybridShapePointOnPlane`、`HybridShapePointOnSurface` |
| 线 | `HybridShapeLinePtPt`、`HybridShapeLinePtDir`、`HybridShapeLineAngle`、`HybridShapeLineBisecting` |
| 平面 | `HybridShapePlaneExplicit`、`HybridShapePlaneOffset`、`HybridShapePlane3Points` |
| 曲线 | `HybridShapeSpline`、`HybridShapeCircle2PointsRad`、`HybridShapeCircleExplicit`、`HybridShapePolyline` |
| 曲面 | `HybridShapeExtract`、`HybridShapeExtrude`、`HybridShapeFill`、`HybridShapeRevolve`、`HybridShapeSweep*` |
| 操作 | `HybridShapeTranslate`、`HybridShapeRotate`、`HybridShapeSymmetry`、`HybridShapeScaling`、`HybridShapeAffinity` |
| 工厂 | `HybridShapeFactory` — 创建所有上述类型的工厂对象 |

---

## part_interfaces（69 个文件）— 零件特征

对应 CATIA 零件设计（Part Design）工作台。

| 分类 | 主要类 |
|---|---|
| 基础特征 | `Pad`（拉伸）、`Pocket`（挖槽）、`Shaft`（旋转体）、`Groove`（旋转槽）、`Hole`（孔） |
| 修饰特征 | `Fillet`（圆角）、`Chamfer`（倒角）、`Draft`（拔模）、`Shell`（抽壳） |
| 变换特征 | `TranslationPattern`、`CircPattern`（圆形阵列）、`RectPattern`（矩形阵列）、`Mirror` |
| 布尔操作 | `Add`、`Remove`、`Intersect`、`Union` |
| 工厂 | `ShapeFactory` — 创建零件特征的工厂对象 |

---

## sketcher_interfaces（14 个文件）— 草图

| 类 | 主要功能 |
|---|---|
| `Sketch` | 草图对象：`Constraints`、`GeometricElements`、`Factory2D` |
| `Factory2D` | 2D 几何工厂：`CreateLine()`、`CreateCircle()`、`CreatePoint()`、`CreateSpline()` |
| `Line2D` | 直线：`StartPoint`、`EndPoint` |
| `Circle2D` | 圆：`Center`、`Radius` |
| `Point2D` | 点：`X`、`Y` |
| `Curve2D` | 曲线基类 |
| `Geometry2D` | 2D 几何基类 |

---

## assembly_interfaces（8 个文件）— 装配特征

> 注意：这里的"装配特征"是指装配设计工作台中直接在装配层面创建的特征，
> 不是约束关系（约束在 `mec_mod_interfaces.Constraints` 中）。

| 类 | 主要功能 |
|---|---|
| `AssemblyBoolean` | 装配级布尔操作 |
| `AssemblyFeature` / `AssemblyFeatures` | 装配特征基类与集合 |
| `AssemblyHole` | 装配级孔特征 |
| `AssemblyPocket` | 装配级挖槽特征 |
| `AssemblySplit` | 装配级分割特征 |
| `AssemblyConstraintSettingAtt` | 约束设置属性 |
| `AssemblyGeneralSettingAtt` | 装配通用设置属性 |

---

## system_interfaces（25 个文件）— 系统基础

| 类 | 主要功能 |
|---|---|
| `AnyObject` | 所有 CATIA 对象公共基类：`name`、`parent`、`application`、`get_item()` |
| `SystemService` | 系统服务：`evaluate(vba_code, type, func, args)` — VBA 宏注入；`execute_script()`；`execute_processus()`；`execute_background_processus()`；`environ(var_name)` — 读环境变量；`print()` |
| `Collection` | COM 集合基类，提供 `Count`、`Item()` 的统一封装 |
| `CatBaseDispatch` / `CatBaseUnknown` | win32com IDispatch / IUnknown 的 Python 基类 |
| `SettingController` | 设置控制器基类：`Save()`、`Reset()` |
| `SettingRepository` | 设置仓库：读写 CATIA 持久化设置 |

---

## analysis_interfaces（40 个文件）— 有限元分析

| 类 | 主要功能 |
|---|---|
| `AnalysisDocument` | FEM 文档对象 |
| `AnalysisModel` / `AnalysisModels` | 分析模型集合 |
| `AnalysisMesh` / `AnalysisMeshes` | 网格对象集合 |
| `AnalysisSet` / `AnalysisSets` | 分析集合（边界条件、载荷等） |
| `AnalysisCase` / `AnalysisCases` | 分析工况 |
| `AnalysisSolver` | 求解器 |
| `AnalysisResults` | 分析结果读取 |

---

## navigator_interfaces（17 个文件）— DMU 导航

| 类 | 主要功能 |
|---|---|
| `NavigatorDocument` | DMU 导航文档 |
| `NavigatorScenes` / `NavigatorScene` | 场景管理 |
| `AnnotationSet` | 标注集合 |
| `NavigatorBehaviors` | 行为集合 |
| `NavigatorComponentFilter` | 组件过滤器 |

---

## 其他模块快览

| 模块目录 | 文件数 | 用途 |
|---|---|---|
| `kinematics_interfaces` | — | DMU 运动学仿真（机构、接头、命令） |
| `manufacturing_interfaces` | — | 数控加工（刀具路径、加工操作） |
| `prismatic_machining_interfaces` | — | 棱柱件加工（铣削、钻孔操作） |
| `surface_machining_interfaces` | — | 曲面加工（多轴铣削操作） |
| `fitting_interfaces` | — | 装配仿真（插装/拆卸序列） |
| `simulation_interfaces` | — | 仿真基础接口 |
| `structure_interfaces` | — | 结构管钢型材设计 |
| `electrical_schematic_interfaces` | — | 电气原理图接口 |
| `cat_tps_interfaces` | — | 三维公差标注（GD&T / TPS） |
| `cat_mat_interfaces` | 9 | 材料库管理（见上方详细介绍） |
| `threed_xml_interfaces` | — | 3D XML 文件格式导出/导入 |
| `enumeration` | — | CATIA 所有枚举类型定义（`CatWorkModeType` 等） |
| `exception_handling` | — | pycatia 异常类定义 |

---

## 对本项目的潜在改进点

### 已解决
- **`Position.GetComponents`**（`mass_props_collect.py`）：已通过 `_vba_safearray()` 封装解决，与 pycatia 原理一致。

### 可参考但暂不融入

| 场景 | pycatia 方式 | 当前项目方式 | 说明 |
|---|---|---|---|
| 读零件质量/重心（无 SPA 保持测量时） | `product.Analyze.mass`、`get_gravity_center()` | 读 `惯量包络体.N` 参数 | 两者来源不同，不可替换；可作为 fallback 备选 |
| 读 SPA 惯量矩阵 | `Inertia.get_inertia_matrix()` (VBA Evaluate) | 读参数树 `IoxG`/`IoyG`/`IozG` 等 | 同上 |
| `Selection.VisProperties.GetShow` | 无参调用，取 `result[1]` | 已在本次重构中对齐 | 已修复 |
| Documents 遍历 | `for doc in documents_obj`（`__iter__`） | `for i in range(1, N+1): docs.Item(i)` | win32com 对象无法使用 pycatia 迭代器，现有方式正确 |

### 有价值但需评估

- **`Analyze.GetGravityCenter` / `GetInertia` 作为 fallback**：当零件没有 SPA 保持测量时，可通过此路径提供估算值，告知用户数据来源为材料属性计算而非实测。VBA 模式与 `_vba_safearray()` 完全一致，实现成本低。
- **`Inertia.get_principal_axes()` / `get_principal_moments()`**：SPA 主惯性轴和主惯量矩，`mass_props_calc.py` 中目前用 Jacobi 迭代自行计算，可考虑读 CATIA 直接给出的值作为交叉验证。

---

*文档生成日期：2026-06-02，基于 pycatia 源码分析（安装路径：`C:\Users\Chen Weibo\AppData\Roaming\Python\Python314\site-packages\pycatia`）。*
