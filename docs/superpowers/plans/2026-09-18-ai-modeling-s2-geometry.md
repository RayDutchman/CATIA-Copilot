# AI 建模几何查询可靠性（S2：planar/cylindrical/unknown 诚实几何分类）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. 计划是工程指导而非已执行代码：任何步骤均未完成，严格按 TDD 红→绿推进。

**Goal:** 把当前"固定矩形 side_normals + GeometricElements 异常 fallback=4"提升为诚实几何查询：面描述携带 `geometry_type`（`planar`/`cylindrical`/`unknown`），仅 planar 且由**端点确认的闭合轴对齐矩形**才有 `normal`；圆柱侧面 `normal=None` 并注明曲面；轮廓不可枚举时抛 `GeometryQueryError`。**BRep 侧面 `Sketch.M;K` 的 K 维持现状的"过滤非边元素后 ordinal 1..N"不变**（实测 GE 原始索引 [5]~[8]=直线.1~直线.4，有效 BRep 仍为 K1..4），`raw_collection_index` 仅入诊断；4 条直线**不能**证明矩形、不得套固定编号法向表；不改 `tools.py`、不改 BRep 字符串格式。

**基线（已验证，计数以实测为准）：** 全量 `python -m unittest discover -s catia_copilot/tests -p 'test_*.py'` ≈ 278 tests / 0 fail（规格注记 40.888s、本机实测约 40.5s，实现会话可能波动；**不写虚构准确数**，只承诺不得回归）。工作区未提交（S1 的 `modeling_contract.py`/`tools.py`/contract 测试已就位），`part_templates/` 含用户改动，**任何人不得触碰**。

**实机探查事实（截至本计划落盘）**：CATIA 连接成功（`get_catia_v5_application` 可取），但当前 ActiveDocument 的 `MainBody.Sketches.Count = 0`，端点/GeometricType 的真实 COM 值尚未测到；`pycatia.sketcher_interfaces.curve_2D`（**模块名大写 curve_2D**）静态存在，`Curve2D.get_end_points()`、`start_point`/`end_point`（→`Point2D.get_coordinates()`）已读静态定义确认可用。上述仅作 B1/B2 实机裁决项，**不当作已验证结论**。

**Architecture:** 新增纯标准库模块 `catia_copilot/catia/geometry_faces.py`（无 COM、可被 `modeling.py` 顶层 import）：常量（枚举值已读 pycatia `CatGeometricType` 静态定义，真实 COM 值由 Task4 B2 实机裁决）、`SketchEdgeClass`（ordinal index / `raw_collection_index` / type / kind / 可选 start,end 二元坐标）、`SketchOutline`、`GeometryQueryError`、`classify_element_kind`/`sketch_outline_from_element_types`/`read_error_outline`/`confirm_rect_outline`/`map_2d_normal_to_3d`（朴素矩形确认与按每边真实方向+CW/CCW 推导外法向）、`describe_sides`/`describe_surfaces`/`filter_faces_by_normal`/`side_neighbor_positions`。`modeling.py`：`_get_sk_edge_count_from_sketch` 保留接口但删除默认 4；新增共享读取 helper `_get_sketch_outline`（GE 全量枚举 + 尽力读端点，端点失败仅影响 normal），三个 `_*_geometry` 改为携带 outline（读取失败不 raise 仅记状态，`profile_edge_count` 取代 `sk_edge_count`）；三个 `get_*_faces` 在 outline 不可枚举时 raise `GeometryQueryError`；`get_pad_faces_by_normal` 走纯 `filter_faces_by_normal`（校验 target/tolerance、跳过 None）。S1 契约仅改 notes（单源自动同步两消费端），不改签名、不新增 ctx 方法；S2 不改 `tools.py`。

**Tech Stack:** 现有 `unittest` + `typing.TypedDict`/`dataclasses`（标准库），不新增依赖；Windows PowerShell + Python 3.13；COM 侧沿用 modeling.py 现有 pycatia/connection 模式。

## Global Constraints

- **不改已批准规格/路线图**；`part_templates/`、`docs/AI_MODELING_PLAN_AND_ROADMAP.md`、`catia_copilot/ai/tools.py` 禁止触碰。
- **BRep K = 过滤非边元素后的 ordinal 1..N（现状，绝对不换编号）**；`raw_collection_index` 仅诊断。实据：`BREP_NAMING_REFERENCE §3` 与 `ge_axes_output` 实测 GE[5..8]=直线.1..4，有效 BRep 面仍为 `Sketch.M;K1..4`。任何"把侧面索引切成原始索引"的旧表述作废；轴/点混排下 raw 与 ordinal 错位只做 B 项实机观察记录，不作为本轮行为变更。
- **不新增/删除/改名任何 ctx 方法，不改变公共签名**（S1 AST 契约测试必须继续全绿）；仅扩展返回 dict 字段、私有 geo dict 字段与 notes。
- **法向诚实性**：矩形法向必须由端点确认（4 边闭合链/非退化/轴对齐、对边反向平行）后按每条边的实际方向 + CW/CCW（质心朝内判定）推导外法向；端点缺失或确认失败 → `normal=None` + 中文原因，**不套表**。非矩形直线侧面可标 `planar` 但 `normal=None`；circle/arc 拉伸面 `cylindrical` 且 `normal=None`（无单一全局法向，不搞"圆心到端点近似"）；Shaft 面一律 `unknown`+`None`。
- **Pocket 侧面法向 = Pad 外法向反向**（语义修正，同步文档与测试；列入 B4 实机核验）。
- **错误语义**：ctx 查询函数不经 `_run`，`GeometryQueryError` 只经 tools 通用 `except` 回传 traceback 消息，**不产生 failed_step**——计划、notes、文档都不得承诺 failed_step；`raise ... from exc` 保留异常 cause。
- 不宣称未实机验证的结论：轴向/拓扑结论只写"已由宏/S1 验证"或"列入基准待实机确认"；纯逻辑单测与 CATIA 手动验收明确区分，手动基准不是 CI。
- 任何步骤不自动 `git add`/`git commit`（需用户授权，Conventional Commits）；中文注释、英文命名。
- **停止条件（整 S2）：** 需要大规模 COM 重构、实现 CAA/真实 CATFace 枚举、引入拓扑容差魔法、或改动已验证 BRep 格式/编号时，立即停止并汇报。若实机发现 BRep K 必须以原始索引取址（与现有实测冲突），立即停止重评估。若端点读取在实机上完全不可用，矩形法向降级为全部 `None`+原因（诚实降级）；但该情形 S2 **不得以"端点全败≠算失败"默认 PASS**——必须如实报告 PARTIAL（矩形法向未修复），禁止声称法向已修好，并另行决定整改方案。

---

## Task 1: 纯几何分类模块（无 COM）+ 单元测试

**Files/Interfaces:** Create `catia_copilot/catia/geometry_faces.py`、`catia_copilot/tests/test_geometry_faces.py`。纯模块导出（英文命名、中文 docstring）：

```python
# 常量（枚举值已读 pycatia CatGeometricType 静态定义；真实 COM 值由 Task4 B2 实机裁决；旧注释“4=圆/弧”有误，不沿用）：
KIND_PLANAR = "planar"; KIND_CYLINDRICAL = "cylindrical"; KIND_UNKNOWN = "unknown"
OUTLINE_RECT = "rect_like"; OUTLINE_CIRCULAR = "circular"; OUTLINE_SLOT = "slot_like"
OUTLINE_MIXED = "mixed"; OUTLINE_UNKNOWN = "unknown"      # outline 的 kind 推断
STATUS_OK = "ok"; STATUS_READ_ERROR = "read_error"        # sketch_outline_from_element_types 恒产出 status=ok；read-error 由上层 read_error_outline 构造，kind 恒 OUTLINE_UNKNOWN
GT_UNKNOWN = 0; GT_AXIS = 1; GT_POINT = 2; GT_LINE = 3
GT_CONTROL_POINT = 4; GT_CIRC_ARC = 5          # Circle2D：整圆与圆弧同类型
GT_ELLIPSE = 8; GT_SPLINE = 9
_ELEMENT_GEOMETRIC_TYPES = frozenset({GT_LINE, GT_CIRC_ARC, GT_ELLIPSE, GT_SPLINE})  # 录入 outline 的边类型

class GeometryQueryError(Exception): ...          # 消息须自带中文修复指引（tools 通用 except 直接回传 traceback）

@dataclass(frozen=True)
class SketchEdgeClass:
    index: int                  # 过滤非边元素后的 ordinal 1..N；BRep 侧面 Sketch.M;K 用此值（绝对不变）
    raw_collection_index: int   # GeometricElements 原始 1-based 位置；仅诊断，不参与 BRep/法向
    geometric_type: int
    kind: str                   # KIND_PLANAR / KIND_CYLINDRICAL / KIND_UNKNOWN
    start: tuple | None         # 草图 2D 坐标 (x,y)；COM 端点读取失败为 None
    end: tuple | None           # 草图 2D 坐标 (x,y)

@dataclass(frozen=True)
class SketchOutline:
    kind: str                   # 类型推断（含 rect_like 等），最终矩形仍须由端点复确认
    edges: tuple[SketchEdgeClass, ...]   # 已过滤，按原始枚举顺序
    edge_count: int | None     # 过滤后边数（=候选侧面数，非最终拓扑面数）；status!=ok 时 None
    status: str                # 正常=STATUS_OK（纯函数恒产）；READ_ERROR 由上层经 read_error_outline 构造
    note: str

class FaceDescriptor(TypedDict, total=False):     # 面描述模型：get_*_faces 每项的字段契约
    type: str                 # "top"/"bottom"/"side"/"surface"
    source: str               # 恒 "feature_inference"：由特征+草图推导的候选面，非最终拓扑枚举
    geometry_type: str
    normal: tuple | None      # 仅 planar 且端点确认矩形时非 None；其余一律 None
    normal_unresolved: str | None   # normal 为 None 的中文原因；已解析时为 None
    origin: tuple | None      # 顶/底=草图原点±depth；side 有端点则为端点 3D 坐标，否则 None（不给假点）
    face_brep: str
    edge_index: int | None    # ordinal（BRep K）
    edge_count: int | None    # outline.edge_count（草图边计数语义）
    error: str | None         # 恒为 None（读失败路径改为 raise，error 字段保留字段位）

def classify_element_kind(gt: int) -> str: ...
def sketch_outline_from_element_types(element_types: Iterable[int]) -> SketchOutline: ...
def read_error_outline(note: str) -> SketchOutline: ...   # 上层读失败状态纯构造器：status=READ_ERROR / kind=unknown / edges=() / edge_count=None / note 原样
def confirm_rect_outline(edges: Sequence[SketchEdgeClass]) -> tuple[list[tuple[float, float]] | None, str]: ...
def map_2d_normal_to_3d(n2: tuple[float, float], h_axis, v_axis) -> tuple | None: ...
def describe_sides(outline, brep_by_index: Mapping[int, str], h_axis, v_axis, *,
                   origin=None, outward: bool = True) -> list[FaceDescriptor]: ...
def describe_surfaces(outline, brep_by_index: Mapping[int, str], *, origin=None) -> list[FaceDescriptor]: ...
def filter_faces_by_normal(faces: Iterable[dict], normal, tolerance_deg: float = 5.0, *,
                           planar_only: bool = False) -> list[dict]: ...
def side_neighbor_positions(faces: Sequence[dict], edge_index: int | None) -> tuple[int, int] | None:
    """faces 中 edge_index（ordinal）相等项的列表位置 j 与循环相邻位 (prev_j, next_j)；
    按列表位置选邻，不依赖 raw_collection_index、不假设索引连续；查无→None；n==1→(0,0)（调用侧守卫跳过自引用）。
    纯只读：不改写 faces / edge_index / raw_collection_index / face_brep，不涉及任何 raw BRep 取址或替换。"""
```

行为规范：
- `classify_element_kind`：`GT_LINE`→`planar`；`GT_CIRC_ARC`→`cylindrical`；轴(1)/点(2)/控制点(4)/椭圆(8)/样条(9)/其它→`KIND_UNKNOWN`。
- `read_error_outline(note)`：读失败状态纯构造器，返回 `SketchOutline(status=STATUS_READ_ERROR, kind=OUTLINE_UNKNOWN, edges=(), edge_count=None, note=note)`；`STATUS_READ_ERROR` 只允许经此构造（上层 modeling.py 捕获 GE 枚举异常后调用），`sketch_outline_from_element_types` 永不产出该状态。
- `sketch_outline_from_element_types`：仅 `_ELEMENT_GEOMETRIC_TYPES` 内的元素进入 `edges`，`index`=过滤后 ordinal（1..N）、`raw_collection_index`=原始位置、`start/end=None`（纯模块无 COM）；正常返回 `status=STATUS_OK`；`edge_count=len(edges)`；kind 推断：空→`unknown`；4 条全 planar→`rect_like`（仅类型提示，非最终矩形结论）；1 条 cylindrical→`circular`；恰好 2 planar+2 cylindrical→`slot_like`；其它→`mixed`。
- `confirm_rect_outline(edges)`：依次检查 ①恰 4 条且均为 planar、start/end 均非 None；②闭合链 `edges[i].end≈edges[(i+1)%4].start`（容差 1e-6）；③非退化：每条边长>1e-6、对边反向平行（`d2≈-d0`、`d3≈-d1`）；④轴对齐：每条边方向平行于草图 (1,0) 或 (0,1)。全部通过 → 返回每边在 (H,V) 基的外法向二元组（按 edges 顺序，任一两元组恰为 (±1,0)/(0,±1)），推导：质心为内判据点，取"背离质心"一方的垂向为目标外法向，与枚举顺序（CW/CCW）无关；失败 → `(None, 中文原因)`（缺端点/未闭合/退化/非轴对齐分别说明）。
- `map_2d_normal_to_3d(n2, h_axis, v_axis)`：`normalize(n2[0]*h_axis + n2[1]*v_axis)`；模长过小返回 None。
- `describe_sides`：`outline.status != ok` → 抛 `GeometryQueryError`（带 note）。先 `(n2s, reason) = confirm_rect_outline(outline.edges)`；成功→逐边 planar、3D 法向=`map_2d_normal_to_3d`，`outward=False`（Pocket）时取反，`normal_unresolved=None`；失败→按边 kind：planar→`normal=None`、`normal_unresolved=reason`（缺端点时注明"无端点坐标，无法确认矩形，不得套表"）；cylindrical→`None`+"圆柱曲面无单一全局法向"；unknown→`geometry_type=unknown`+"元素类型无法归类为平面/圆柱"。每条：`source="feature_inference"`、`face_brep=brep_by_index[edge.index]`（按 ordinal 键）、`edge_index=edge.index`、`edge_count=outline.edge_count`、`type="side"`、`error=None`、`origin`：`origin`(sk_origin)与 `edge.start` 均可用时=「sk_origin + h_axis*start[0] + v_axis*start[1]」（真实在侧面上），否则 None。
- `describe_surfaces`（Shaft 用）：`status != ok` 抛错；每条 `type="surface"`、`source="feature_inference"`、`geometry_type=KIND_UNKNOWN`、`normal=None`、`normal_unresolved`="旋转面类型依赖边相对旋转轴的方向，需坐标解析；S2 不分类"、`origin=None`（无坐标依据不给假点）、`face_brep`/`edge_index`/`edge_count`/`error` 同上。
- `filter_faces_by_normal`：校验 `normal` 长度为 3 且各分量有限、模>0（否则 `ValueError("目标法向必须是有限非零向量")`）；校验 `tolerance_deg` 有限且 ∈[0,180]（否则 ValueError）；**跳过 `normal is None` 的面**（容差取 180 也不匹配未知法向）；`planar_only=True` 时再跳过 `geometry_type != planar`；其余与现行点积累加+余弦阈值一致。
- `side_neighbor_positions`：按字段匹配（可比对 edge_index），n≥2 时 prev/next 恒非 j；单元素→`(0,0)` 由调用侧 `prev_j != next_j` 守卫跳过；纯列表位置选邻（只读，不改写 BRep / raw 索引，不涉 raw BRep 替换）。

- [ ] **Step 1: 写失败测试**（`test_geometry_faces.py`，直接 `import catia_copilot.catia.geometry_faces as gf`）

```python
class TestClassifyElementKind:   # GT_LINE(3)→planar；GT_CIRC_ARC(5)→cylindrical；1/2/4/8/9/0→unknown
class TestSketchOutline:         # [3,3,3,3]→rect_like/4，raw=[1,2,3,4]、ordinal=[1,2,3,4]；
                                 # [5]→circular/1；[3,3,5,5]→slot_like/4；[3,5,8]→mixed；
                                 # [4]（ControlPoint2D，非边）→unknown/0；
                                 # [1,3,2,3,3,3]→rect_like，edges ordinal=[1,2,3,4]、raw_collection_index=[2,4,5,6]
class TestReadErrorOutlineState:  # read_error_outline("x")：status==READ_ERROR、kind=unknown、edges=()、edge_count=None、note 保留；
                                  # sketch_outline_from_element_types([3,3,3,3]).status==STATUS_OK；[4]→unknown/0 且 status==STATUS_OK
class TestConfirmRectOutline:
    test_draw_rect_order            # (x,y)-(x+w,y)-(x+w,y+h)-(x,y+h)-(x,y) 链：2D 外法向==
                                    # ((0,-1),(1,0),(0,1),(-1,0))（= side_normals 旧表的验证基准：
                                    # modeling.py 硬编码 1→-V/2→+H/3→+V/4→-H，S2 已删除，此处仅作期望值参照）
    test_reversed_draw_order        # 反序/CW 链：法向不变（与枚举方向无关）
    test_missing_endpoint            # 某边 start=None → (None, reason 含“端点”)
    test_not_closed / test_degenerate_zero_height / test_not_axis_aligned_45deg
class TestMap2DTo3D:                # (0,-1)*(1,0,0)+...；模长过小→None
class TestDescribeSides:
    test_rect_sides_normals         # outline(status=ok)+端点+true：四侧 planar，3D 法向==(-V,+H,+V,-H)
    test_rect_outward_false_reverses # outward=False（Pocket）：法向取反
    test_missing_endpoint_normal_none # 端点缺失：planar 且 normal None，normal_unresolved 含“端点”
    test_circular_cylindrical_none    # [5] 侧：normal None、含“圆柱”、edge_count==1
    test_slot_sides_unresolved        # 2 planar 的 normal None（不出现 -(0,0,0) 假值）+ 2 cylindrical None
    test_mixed_unknown_kind_fields
    test_brep_passthrough_by_ordinal_index  # brep_by_index 按 ordinal 键命中（raw 位移时仍正确）
    test_side_origin_real_vs_none    # 有 sk_origin+start → 3D 点；无 → None
    test_face_source_feature_inference # 每条字段 source=="feature_inference"（候选特征面语义）
    test_read_error_raises_geometry_query_error
class TestDescribeSurfaces:      # 每条 surface：kind unknown、normal None、note 含“旋转面”、origin None、edge_count 一致
class TestFilterFacesByNormal:   # normal 非零有限校验/容差超界 → ValueError；normal=None 面被跳过（容差 180 亦不匹配）；
                                 # planar_only=True 跳过 cylindrical；0 容差精确匹配
class TestSideNeighborPositions: # ordinal [1,2,3,4]（faces 顺序即枚举序）按列表位置 j±1 选邻；
                                 # raw 位移场景 raw=[5,6,7,8] 仍按列表位置；单元素→(0,0) 不自引用；查无→None；n>=2 邻位恒非 j
```

**纯层不测真实 COM 端点**：本 Task 测试全部使用纯数据/构造起点，不接真实 CATIA。真实端点读取由 modeling.py 的 `_edge_endpoints` 适配器承担（Task2 用 fake raw 对象单测其 路径A→B→None 决策），真实 COM 可用性由 Task4 B1 实机（含真实草图/特征构造）裁决——B1 是上线（进入 S3）依据，不属于本 Task。

Expected RED：`ModuleNotFoundError: No module named 'catia_copilot.catia.geometry_faces'`。

- [ ] **Step 2: 实现纯模块**（按上面 Interfaces 与行为规范完整实现，无 TODO/占位）
- [ ] **Step 3: 全绿**

```powershell
python -m unittest catia_copilot.tests.test_geometry_faces -v   # Expected: 全部 OK
```

---

## Task 2: modeling.py 几何查询接线（COM 侧，去 4 边默认）

**Files:** Modify `catia_copilot/catia/modeling.py`；在 `catia_copilot/tests/test_geometry_faces.py` 追加 1 个静态源守卫测试（RED→GREEN）。不改 BRep 辅助函数、不改任何公共签名。

- [ ] **Step 1: 写失败测试（先红；两类测试均不 import modeling.py——其模块顶层 import pycatia/COM 包装，无 COM 环境直接 import 不可行，计划不承诺该方式）**

1a. 静态守卫（防 fallback=4 回归）：

```python
class TestNoFallback4InModelingSource(unittest.TestCase):
    def test_modeling_src_has_no_default_rectangle_fallback(self):
        src = _MODELING_SRC.read_text(encoding="utf-8")   # 复用 test_modeling_contract 的 _MODELING_SRC 常量写法（_ROOT 同源）
        self.assertNotIn("默认矩形", src, "几何未知时不得 fallback=4（默认矩形）")
        self.assertNotIn("return 4", src)   # 覆盖 _get_sk_edge_count_from_sketch 与 _pad_geometry 的旧兜底
```

当前源码含 `return 4   # 默认矩形`（`_get_sk_edge_count_from_sketch` ~1089-1100）与 `sk_edge_count = 4   # 默认矩形`（`_pad_geometry` ~1165-1173）→ RED；Step 2 修完后 GREEN。

1b. read-error / 端点适配器接线测试（**无 COM fake 对象 + AST 受控加载**，沿用 S1“绝不 import modeling/tools、AST 受控求值”先例；仅标准库 ast/exec 与手写 fake 类，不引入 mock 框架/模块模拟层，保持 S2 边界）：
- 纯转换已拆到 geometry_faces：读取异常→`READ_ERROR` 状态由 `read_error_outline(note)` 承担（Task1 已直测）；本步 AST 测试只验证 modeling.py 捕获异常后**确实接线调用**它。自由符号不足即 RED（`_get_sketch_outline`/`_edge_endpoints` 尚不存在）。
- `_get_sketch_outline` 接线：`ast.parse(_MODELING_SRC)` 抽取其 FunctionDef，在注入命名空间（`SketchOutline`/`STATUS_OK`/`OUTLINE_UNKNOWN`/`read_error_outline`/`sketch_outline_from_element_types`/`_edge_endpoints`=stub/`replace`）exec；传入 fake sketch（`FakeGE`：`Count=1` 且 `Item(i)` 抛 COM 风格异常），断言返回 `status==STATUS_READ_ERROR`、note 含异常消息、`edge_count is None`、`kind==OUTLINE_UNKNOWN`。
- `_edge_endpoints` 决策：同法抽取后注入假 `_PyCurve2D`（静态包装类）与 fake raw GE 项（具 `start_point`/`end_point`→`get_coordinates()`），分别断言 路径A成功→直接返回、A抛→路径B成功、两条都抛→`None`（端点失败不影响枚举）。
- `describe_sides` 对 status!=ok 的 raise 已由 Task1 `test_read_error_raises_geometry_query_error` 纯层覆盖，get_*_faces 的 raise 语义不在此重复。

- [ ] **Step 2: 修改 modeling.py（接线）**

2a. 顶层 import：第 27 行 `from typing import Literal` 扩为 `from typing import Literal, Mapping, Sequence`；新增 `from dataclasses import replace`（配合 `_get_sketch_outline`；最终实现若改用非 replace 写法须同步删本 import 与全部代码示例引用，二选一保持一致）与 `from pycatia.sketcher_interfaces.curve_2D import Curve2D as _PyCurve2D`（**模块名大写 curve_2D**；纯包装类，模块顶层 import 不触发 COM）；geometry_faces 导入含 `GeometryQueryError, KIND_PLANAR, KIND_CYLINDRICAL, KIND_UNKNOWN, OUTLINE_UNKNOWN, STATUS_OK, STATUS_READ_ERROR, SketchOutline, SketchEdgeClass, _ELEMENT_GEOMETRIC_TYPES, classify_element_kind, sketch_outline_from_element_types, read_error_outline, confirm_rect_outline, map_2d_normal_to_3d, describe_sides, describe_surfaces, filter_faces_by_normal, side_neighbor_positions`（缺一即 NameError）。

2b. **保留接口、删除默认**：`_get_sk_edge_count_from_sketch` 改为 1 行薄壳（不再存在 `return 4`）：

```python
def _get_sk_edge_count_from_sketch(sketch_com) -> int | None:
    """兼容保留的旧计数接口：返回过滤后的草图边数；读失败返回 None（不再默认 4）。内部不再使用。"""
    return _get_sketch_outline(sketch_com).edge_count
```

新增共享读取 helper 与端点尽力读取（替换原内联计数）：

```python
def _edge_endpoints(sketch_com, raw_i) -> tuple | None:
    """尽力读取 GE 项的 (x0, y0, x1, y1)；失败返回 None。
    路径 A：_PyCurve2D 包装原始 GE 项 + get_end_points()（Line2D 继承 Curve2D）；
    路径 B：start_point/end_point.get_coordinates()。两路径方法存在性已静态读 pycatia 定义确认
    （pycatia.sketcher_interfaces.curve_2D：get_end_points / start_point / end_point；Point2D.get_coordinates），
    但原始 GE 项能否成功包装与真实 COM 值由 Task4 B1/B2 实机裁决。端点读取失败只影响 normal，不影响枚举。"""
    try:
        return _PyCurve2D(sketch_com.GeometricElements.Item(raw_i)).get_end_points()
    except Exception:
        pass
    try:
        cu = _PyCurve2D(sketch_com.GeometricElements.Item(raw_i))
        return (*cu.start_point.get_coordinates(), *cu.end_point.get_coordinates())
    except Exception:
        return None

def _get_sketch_outline(sketch_com) -> SketchOutline:
    """枚举草图轮廓：GeometricType + 原始索引 + 尽力端点。
    GE 整体枚举失败 → 经 read_error_outline 构造 status=READ_ERROR，不抛、不作任何默认（原返回 4 的兜底移除）。
    GeometricType（CatGeometricType 静态枚举定义，真实 COM 值由 Task4 B2 复核）：1=Axis2D,2=Point2D,
    3=Line2D,4=ControlPoint2D,5=Circle2D（整圆与圆弧）,8=Ellipse2D,9=Spline2D。3D 类型(10..12)、
    unknown(0)与控制点(4)不录入。"""
    try:
        ge = sketch_com.GeometricElements
        types = [ge.Item(i).GeometricType for i in range(1, ge.Count + 1)]
    except Exception as exc:
        return read_error_outline(f"GeometricElements 枚举失败: {exc}")
    outline = sketch_outline_from_element_types(types)   # 纯模块：ordinal/raw/kind/status=ok
    filled = []
    for edge in outline.edges:
        ep = _edge_endpoints(sketch_com, edge.raw_collection_index)
        st, en = ((ep[0], ep[1]), (ep[2], ep[3])) if ep else (None, None)
        filled.append(replace(edge, start=st, end=en))   # from dataclasses import replace
    return replace(outline, edges=tuple(filled))
```

2c. `_pad_geometry`（~1162-1173）：删除内联 try/except 计数，改 `outline = _get_sketch_outline(pad.sketch.com_object)`；返回 dict 增 `"outline": outline`、`"profile_edge_count": outline.edge_count`、删 `sk_edge_count` 键；docstring 增 outline/profile_edge_count 说明（profile_edge_count=候选侧面数，非最终拓扑面数）。

2d. `get_pad_faces`（~1213-1265）：开头若 `geo["outline"].status != STATUS_OK` → `raise GeometryQueryError(f"get_pad_faces 无法枚举 {pad.name} 草图轮廓：{outline.note}；已停止，不再默认4边；请用 list_features 确认特征状态或重建草图")`。顶/底面 dict 增 `source="feature_inference"`、`geometry_type=KIND_PLANAR`、`edge_count=outline.edge_count`、`normal_unresolved=None`、`error=None`；侧面删除固定 `side_normals` 表与 `for idx in range(1, ...)` 循环，改为：

```python
    brep_map = {e.index: _brep_face_side(geo["en_pad"], geo["en_sk"], e.index) for e in outline.edges}
    faces += describe_sides(outline, brep_map, geo["h_axis"], geo["v_axis"],
                            origin=geo["sk_origin"], outward=True)
```

2e. `get_pad_faces_by_normal`（~1271-1301）：主体替换为 `return filter_faces_by_normal(get_pad_faces(part, pad), normal, tolerance_deg)`（移除内部 `_dot` 循环与 (0,0,0) 兜底语义）；docstring 注明校验失败抛 ValueError、normal=None 自动跳过。

2f. `_pocket_geometry`（~1408）：`_get_sk_edge_count_from_sketch(...)` 改为 `outline = _get_sketch_outline(pocket.sketch.com_object)`；dict 增 `"outline"`、`"profile_edge_count"`、`"sk_origin": tuple(ax[0:3])`（ax 已读取），删 `sk_edge_count`；docstring 同步。

2g. `get_pocket_faces`（~1437-1474）：`outline.status != ok` → raise（消息同 2d 且指明已停止）；底面增 `source="feature_inference"`、`geometry_type=KIND_PLANAR`/`edge_count`/`normal_unresolved=None`/`error=None`（法向不变=+normal 指向开口）；侧面删除固定表，改 `describe_sides(outline, {e.index: f"Face:(Brp:({en};0:(Brp:({es};{e.index})));None:();Cf14:())" for e in outline.edges}, geo["h_axis"], geo["v_axis"], origin=geo["sk_origin"], outward=False)`；docstring 注明"Pocket 侧法向=Pad 外法向反向（B4 实机核验）"。

2h. `get_pocket_face_edges`/`get_pocket_opening_edges`（~1477-1561）：不需要改动。二者经 `get_pocket_faces` 传导 Pocket 侧 raise；`get_pocket_opening_edges` 调 `_pad_geometry(pad)` 只用 `en_pad`，Pad 侧 outline 读失败**不** raise（行为不变，注释说明）。

2i. `_shaft_geometry`（~1586）：同上替换为 `outline`（dict 增 `"outline"`、`"profile_edge_count"`、删 `sk_edge_count`）。

2j. `get_shaft_faces`（~1610-1616）：`outline.status != ok` → raise；每条面改由 `describe_surfaces(outline, {e.index: f"Face:(Brp:({en};0:(Brp:({es};{e.index})));None:();Cf14:())" for e in outline.edges})` 生成（全部 `geometry_type=unknown`、`normal=None`、`origin=None`）。

2k. `ModelingContext` 的 4 个查询包装器 `get_pad_faces`/`get_pad_faces_by_normal`/`get_pocket_faces`/`get_shaft_faces`：签名零改动、透传；docstring 增补返回字段语义与"轮廓不可枚举时抛 GeometryQueryError；该异常经 tools 通用 except 以 traceback 原样回显消息，**不经 _run、不产生 failed_step**"。

2l. **相邻面改为列表位置查找**（get_pad_face_edges ~1343-1357 / get_pocket_face_edges ~1509-1520 / get_shaft_face_edges ~1639-1652 三处）：对侧/全 faces 序列调用 `side_neighbor_positions(side_faces 或 all_faces, face_info["edge_index"])` 得 `(prev_j, next_j)`；**删除** `((idx-2) % n)`/`(idx % n)` 把 ordinal 当列表 rank 的逻辑（通用矩形 ordinal[1..4] 恰连续时结果一致；raw 位移仅诊断不参与）。查无→跳过相邻边；`prev_j == next_j`（n==1）→跳过自引用。公共签名与 BRep 字符串不变。

2m. 其余触及点自查：全文件 `sk_edge_count` 键不再使用（`get_pad_faces` 等引用一并清除）；`get_pocket_opening_edges` 遍历全部 side_faces 无相邻逻辑，不受影响；删除旧"默认矩形"注释。

- [ ] **Step 3: 全绿 + 手动验收前自查**

```powershell
python -m unittest catia_copilot.tests.test_geometry_faces -v                # Expected: 全部 OK（含静态守卫与 fake read-error/端点接线，计数以实测为准）
python -m unittest catia_copilot.tests.test_modeling_contract -v             # Expected: 全绿（签名/AST 不变式保持）
python -m unittest discover -s catia_copilot/tests -p 'test_*.py'            # Expected: OK，无回归（计数以实测为准）
if ($LASTEXITCODE -ne 0) { Write-Output "REGRESSION"; exit 1 }
```

**调用者处理清单（必须写入本任务 commit message / diff 说明，供复核）：**

| 原 fallback 触发点 | S2 新行为 |
|---|---|
| `_get_sk_edge_count_from_sketch`（保留接口） | 薄壳于 `_get_sketch_outline().edge_count`，**不再默认 4** |
| `_pad_geometry` 内联 try/except 计数 | 移除；携带 `outline`/`profile_edge_count` |
| `_pocket_geometry` / `_shaft_geometry` | 携带 `outline`/`profile_edge_count` |
| `get_pad_faces` | outline 不可枚举时 raise `GeometryQueryError`（含中文修复指引） |
| `get_pad_faces_by_normal` | 透传 raise；走 `filter_faces_by_normal`（校验 target/tolerance、跳过 None） |
| `get_pad_face_edges` | 透传 raise；相邻面按列表位置（`side_neighbor_positions`） |
| `get_pocket_faces` | 同 get_pad_faces 语义 raise；侧法向=Pad 反向 |
| `get_pocket_face_edges` / `get_pocket_opening_edges` | Pocket 侧 raise 透传（列表位置选邻）；Pad 侧 outline 读失败**不** raise（仅需 en_pad） |
| `get_shaft_faces` / `get_shaft_face_edges` | 同上 raise / 透传（列表位置选邻） |
| `ModelingContext.*` 4 包装器 | 透传；tools 通用 except 回传 traceback 消息（无 failed_step） |

**迁移影响（写进契约 notes 与文档）：** ① `get_*_faces` 返回 dict 新增字段，旧脚本读已有字段不受影响；② `face["normal"]` 可能为 `None`（圆柱/未解析/未知），依赖法向的脚本须先判 `geometry_type`/`normal is not None`；③ `get_pad_faces_by_normal` 自动跳过 `normal=None` 面；④ 轮廓不可枚举由"静默返 4 边"改为"抛 GeometryQueryError"，脚本按报错消息精准修正。

---

## Task 3: S1 契约同步（notes 单源更新）+ 契约测试扩展

**Files:** Modify `catia_copilot/ai/modeling_contract.py`（仅改 query API 的 `notes` 与新增语义约定常量，签名不动）、`catia_copilot/tests/test_modeling_contract.py`（新增测试类）。`tools.py` 不改；S1 AST 全等测试（catalog==methods、signature 逐字符、无幻象 API）必须继续全绿。

- [ ] **Step 1: 写失败测试（先红）：在 `test_modeling_contract.py` 追加类 `TestGeometryFaceSemanticsS2`**

```python
class TestGeometryFaceSemanticsS2(unittest.TestCase):
    def test_face_query_notes_carry_geometry_semantics(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("cylindrical", notes["get_pad_faces"])
        self.assertIn("unknown", notes["get_pad_faces"])
        self.assertIn("normal=None", notes["get_pad_faces"])
        self.assertIn("不再默认4边", notes["get_pad_faces"])

    def test_pocket_and_shaft_notes_synced(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("反向", notes["get_pocket_faces"])     # Pocket 侧法向=Pad 外法向反向
        self.assertIn("normal=None", notes["get_shaft_faces"])
        self.assertIn("unknown", notes["get_shaft_faces"])

    def test_by_normal_validation_and_skip_none(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("planar", notes["get_pad_faces_by_normal"])
        self.assertIn("跳过", notes["get_pad_faces_by_normal"])

    def test_notes_do_not_promise_failed_step(self):
        # 查询类调用不经 _run：notes/文档不得承诺 failed_step，只承诺工具错误消息回显
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("不产生 failed_step", out)
            self.assertIn("不再默认4边", out)

    def test_no_new_ctx_methods_and_s2_no_signature_change(self):
        methods = set(_ctx_public_methods()) - {"add_rect_pattern", "add_circ_pattern"}
        self.assertEqual(methods, {s.name for s in mc.MODELING_API_CATALOG})
```

Expected RED：当前 notes 无上述关键词（"cylindrical"/"反向"/"不产生 failed_step"等未渲染）。

- [ ] **Step 2: 更新 `modeling_contract.py` notes（4 条 query API notes + 新增常量块；编辑类 API notes 不动）**

新增常量块（渲染进 prompt 与 description，放在 `_AXIS_MAPPING_TEXT` 相邻）：

```python
_FACE_SEMANTICS_TEXT = """### 面/边查询语义
- 每个面描述含 source='feature_inference'（由特征+草图推导的候选特征面，非 CATIA 最终拓扑枚举；face_brep/type/edge_index 字段语义不变），geometry_type（planar/cylindrical/unknown）与 edge_count（过滤后的草图边数=候选侧面数；读取失败为 None，不宣称最终拓扑面数恒定）。
- normal 仅对 planar 面成立；圆柱侧面、旋转面及其它无法解析的面 normal=None。矩形侧面法向由端点确认的闭合轴对齐矩形按每条边真实方向推导（揭开固定编号表）。
- Pocket 自身侧面法向 = 对应 Pad 外侧法向反向；开口面属下层 Pad。
- 轮廓不可枚举时 get_pad_faces/get_pocket_faces/get_shaft_faces 抛错（不再默认4边）。该错误经工具通用异常回显 traceback 消息，不产生 failed_step（查询类调用不经步骤执行流）。"""
```

4 条 query notes 替换如下（每条独立写全关键语义，不用"同 get_pad_faces"式引用）：
- `get_pad_faces`：`"Pad 全部面（type=top/bottom/side；geometry_type=planar/cylindrical/unknown；normal 仅 planar 且矩形经端点确认时非 None，圆柱侧面 normal=None；edge_index=过滤后草图边序号；轮廓不可枚举时报错，不再默认4边；错误经工具回显、不产生 failed_step）"`
- `get_pad_faces_by_normal`：`"按法向筛选面（校验 target 为有限非零向量、tolerance_deg∈[0,180]；仅匹配 normal 非 None 的 planar 面，圆柱/未知跳过）；normal=(nx,ny,nz) 如(0,0,1)=朝上"`（notes 必须含 planar 与 跳过——Step1 `test_by_normal_validation_and_skip_none` 断言依赖，改 notes 时勿删）
- `get_pocket_faces`：`"Pocket 自身面（type=bottom/side；geometry_type=planar/cylindrical/unknown；normal 仅 planar 且已确认时非 None，cylindrical 侧面为 None；Pocket 侧法向=Pad 外法向反向；轮廓不可枚举时报错，不再默认4边；开口面属下层 Pad）"`
- `get_shaft_faces`：`"Shaft 面列表（type=surface；旋转面类型未解析时 geometry_type=unknown、normal=None、origin=None；edge_index=过滤后草图边序号）"`
- `get_pad_face_edges`/`get_pocket_face_edges`/`get_pocket_opening_edges`/`get_shaft_face_edges`：保持原 notes 不变（依赖的 get_*_faces 报错由其调用链自然传导）。

`build_modeling_prompt_section` 与 `build_run_modeling_script_description` 在 `_AXIS_MAPPING_TEXT` 之后各插入一行 `{_FACE_SEMANTICS_TEXT}`（同 `_AXIS_MAPPING_TEXT` 嵌入模式，两处同源不改写）。

- [ ] **Step 3: 全绿 + 全量回归**

```powershell
python -m unittest catia_copilot.tests.test_modeling_contract -v
python -m unittest catia_copilot.tests.test_geometry_faces -v      # S1 test_modeling_contract 对 tools.py 接线的 AST 断言仍绿
python -m unittest discover -s catia_copilot/tests -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { Write-Output "REGRESSION"; exit 1 }
```

---

## Task 4: CATIA 手动基准文档 + 单一 Python CLI 冒烟（非 CI）

**Files:** Create `docs/superpowers/benchmarks/2026-09-18-ai-modeling-s2-geometry-acceptance.md`、`catia_copilot/tests/catia_manual/s2_geometry_query_bench.py`（**不新建 PowerShell 包装**）。这是**人在 CATIA 开启后运行的冒烟基准**，明确不是 CI：任何几何/轴向/拓扑断言以实机记录为准；CI 不 import 该 CLI（保险 `if __name__ == "__main__"`，run 逻辑在 `main()` 中）。

- [ ] **Step 1: 写基准文档（含 8 个基准项 B1-B8，每项含 构造输入/预期/记录列/结论列）**

- **B1 矩形 Pad**：`draw_rect(0,0,100,60)+pad depth20`。记录：**元素类型序列实测**（预期 [3,3,3,3]）、**GE 原始索引与 ordinal（含直线序号）对照**、**`get_end_points()`/`start_point.get_coordinates()` 对原始 GE 项是否可包装**（Path A/B 成败；本项是"原始 GE 项能否包装"的实机裁决点）；`get_pad_faces` 共 6 条、top/bottom planar ±(0,0,1)、4 side planar 且端点法向 == (-V,+H,+V,-H)（实机记录 h_axis/v_axis 及世界坐标；一致=佐证，不一致按实测记录 FAIL/观察，不硬改）；圆角回归 `get_pad_faces_by_normal(顶面)+get_pad_face_edges → add_fillet_edges(r=3)+update`。不自动关/存/删任何文档。
- **B2 圆形/圆弧 Pad**：`draw_circle(50,30,20)+pad depth20`。记录元素类型序列与**原始 GeometricType 数值**（裁决 `Circle2D=5` 与旧注释"4=圆/弧"哪个正确）；共 3 条，side=1 条 `cylindrical`、`normal=None`、`edge_count=1`；**不得出现矩形法向**；圆角回归如实记录。
- **B3 slot/圆弧混合**：`draw_slot(25,30,75,30,r=15)+pad`。记录元素序列（预期 [3,3,5,5]）；side 4 条：2 planar（`normal=None`）+2 cylindrical（`normal=None`）；无假矩形法向。
- **B4 Pocket 开口/底/侧**：矩形 Pocket 于 Pad 上 → `get_pocket_faces`=1 bottom+4 side；**记录 Pocket 侧面法向与对应 Pad 侧面法向对比（裁决"反向"语义）**；`get_pocket_opening_edges` 开口楞 4 条+圆角 r=2；圆形 Pocket 样本 bottom+1 cylindrical side（normal=None）。
- **B5 Shaft 旋转面**：axis="z" 圆筒 → `get_shaft_faces` 每条 `unknown`/`normal=None`/`origin=None`/`edge_count`；`get_shaft_face_edges` 圆角回归 r=2。
- **B6 轴映射 y 冲突（单列实机验收）**：contract 断言 `axis="y"`→ sketch 在 `xy` 平面、轴=V(Y)；`modeling.py:add_shaft` docstring 断言 `axis="y"`→ sketch 须在 `YZ` 平面。分别按两套说明构造 axis="y" 旋转体样本，实机测量零件占位方向/轮廓与轴线相对关系，记录"哪个描述与实机一致/均不一致（三方结论）"。不得以"能建出来"作为轴向正确的结论。
- **B7 错误语义与失败退出**：readerror 路径由 **Task2 Step1 的 fake 接线测试**覆盖（无 COM 假对象 + AST 受控加载，不 import modeling.py；验证 `_get_sketch_outline` 产出 `READ_ERROR`）+ **Task1 纯函数**（`describe_sides` 在 status!=ok 时抛 `GeometryQueryError`，即 get_*_faces 的 raise 语义），不用 COM mock；**不构造"空草图 Pad"作注入样例**（Pad 构造先失败，非本轮注入路径）；实机只验证脚本退出码语义：`python s2_geometry_query_bench.py`（无 CATIA）→ 报告 **BLOCKED** 且退出码 **2**；`--fail-check` 开关在无 COM 下打印 FAIL 并退出 **1**，验证"失败退出非 0"。
- **B8 回归面**：基准执行前运行全量 discover，命令完整、不截断输出（不用 Select-Object），成败以 `$LASTEXITCODE` 判定：
```powershell
python -m unittest discover -s catia_copilot/tests -p 'test_*.py'            # 全量，不截断输出
if ($LASTEXITCODE -ne 0) { Write-Output "REGRESSION"; exit 1 }
```
基线 ≈ 278 tests / 0 fail（**以本次实测为准**，不写虚构准确数）；S2 执行前 `git status --short > "$env:TEMP\catia_s2_git_baseline.txt"` 记录基线，结束后与基线比对：S2 仅允许出现 geometry_faces.py / test_geometry_faces.py / modeling.py / modeling_contract.py / test_modeling_contract.py / benchmark 文档 / catia_manual 单文件；`tools.py`/`part_templates/` 属 S1 未提交+用户模板基线，不错误归因 S2；**全程不 `git add`**。

每项记录表列：`特征名 / 实测（geometry_type/normal/edge_count/error/原始索引/类型序列）` / `期望` / `结论(PASS/FAIL)` / `备注`。

- [ ] **Step 2: 写实验 Python CLI（`s2_geometry_query_bench.py`，单文件，无 ps1）**

- 顶部注释 `USE ONLY when user opened CATIA`；`main()` 内用 `catia_copilot.catia.connection.get_catia_v5_application()` 探测；探测失败 → `print("BLOCKED: ...")` + `sys.exit(2)`（报告 blocked 不是 PASS）。
- 参数：`--b6`（只跑 B6 轴映射样本）、`--fail-check`（无 CATIA 也可用的故意失败样本 → FAIL + exit 1）、默认 B1-B5；`--outdir` 默认 `%TEMP%\catia_s2_smoke`。
- 所有样本用 `create_part(name=...)` **新建未保存试验零件**；结束打印"可手动关闭试验零件"提示；**绝不自动保存/关闭/删除用户文档**；`part_templates/` 零触碰。
- 每个样本调用对应 `get_*_faces`，结果+元素类型序列+端点读取成败 + 原始 GE 索引写入 `--outdir\<样本>.jsonl`（repo 内不落盘），并打印可读摘要。
- 圆角回归用 `add_fillet_edges`+`update_part`，成败计入报告；Shaft/B6 输出实际占位测量（可 `get_mass_props` cog 或轴向参考）供人工复核。
- 结束打印汇总 PASS/FAIL/BLOCKED 计数；全部 PASS→`sys.exit(0)`，任一 FAIL→`sys.exit(1)`；不自动 `git add/commit`。
- 运行示例写入基准文档：
```powershell
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py          # B1-B5；无 CATIA → BLOCKED/exit 2
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py --b6     # 轴映射 y 冲突样本
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py --fail-check   # 故意失败 → FAIL/exit 1
```

- [ ] **Step 3: 卫生检查（不 stage、不 commit）**

```powershell
git diff --check
git diff --stat
git status --short > "$env:TEMP\catia_s2_git_now.txt"   # 与基线并排核对：
#   新增 ??：仅允许上述 5 个 S2 新文件（含单 CLI，无 ps1），其余说明来源
#   修改 M：仅允许 modeling.py / modeling_contract.py / test_modeling_contract.py
#   tools.py / part_templates 属 S1 未提交 + 用户改动基线，标注"基线"而非归因 S2
```

停在此处，向用户展示 diff；用户按 B1-B8 在 CATIA 实机执行基准后，把结论回填基准文档"实测值/结论"列（回填由用户完成或另例会商，不自动提交）。

---

## Self-Review 记录（精简）

- 覆盖：Task1 纯几何分类/矩形端点确认/诚实法向与校验（无 COM 可测）；Task2 无 COM fake 接线测试（AST 受控加载 + read_error_outline 纯转换）、去 fallback=4、ordinal 保持、共享 outline 读取、端点适配器 fake raw 单测、列表位置选邻（不涉 raw BRep 替换）；Task3 契约 notes 单源同步（by_normal notes 含 planar、无 failed_step 承诺）；Task4 手动基准（枚举值静态定义+B2 实机裁决、GE 包装裁决、Pocket 反向核验、轴映射 y 冲突 B6 单列、失败退出码）。
- 关键修正：BRep K=过滤后 ordinal（维持现状不回退）；raw 原始索引仅诊断；4 直线≠矩形（端点确认后逐边外法向）；枚举值已读 `CatGeometricType` 静态定义（4=ControlPoint2D、5=Circle2D，旧注释作废），真实 COM 值 B2 裁决；端点读取失败仅 normal=None；Pocket 侧=Pad 反向；ctx 查询无 failed_step；去掉 Select-Object 截断、去掉 PowerShell 包装文件；不写虚构测试增量，计数以实测为准；端点全败≠PASS，S2 须报告 PARTIAL 不默认通过。
- 预演依据：基线全量 discover ≈278 tests/0 fail（以实测为准）；`_get_sk_edge_count_from_sketch`/`_pad_geometry` 内联计数是唯一兜底源，移除后 get_*_faces 统一 raise；tools.py:1129 通用 except 原样回传 `GeometryQueryError` 消息（若无 failed_step）；S1 AST 全等断言对 notes 修改、无签名修改保持不动。
- 停止条件自查：不改 BRep 格式/编号、不新增 COM 能力集、不加拓扑容差魔法、不为测试引入复杂架构（仅标准库 ast/exec 与手写 fake 类）；端点读取不可用→矩形法向诚实降级 None 且如实报告 PARTIAL（不默认通过）；实机若推翻 ordinal/BRep 或几何枚举值假设→停止汇报。任何 `git add`/`git commit` 均须用户授权。