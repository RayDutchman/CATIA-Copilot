# -*- coding: utf-8 -*-
"""纯几何分类模块（S2 Task1）：无 COM，纯函数推导候选面。

常量枚举值参考 pycatia CatGeometricType 静态定义（GT_CONTROL_POINT=4、
GT_CIRC_ARC=5）；真实 COM 枚举值由 Task4 B2 实机裁决。旧注释「4=圆/弧」有误，不沿用。

本模块职责：
- 把草图的原始几何元素类型列表过滤为边集合（SketchEdgeClass），推导 outline 类型；
- 用纯几何规则确认矩形轮廓并推导各边外法向（H,V 二维基）；
- 产出候选侧/旋转面描述（source=feature_inference，不触碰任何真实拓扑枚举）；
- 提供法向筛选与侧面循环邻接位置查询的纯工具。
"""
import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, TypedDict

# ---- 面/轮廓分类常量 ----------------------------------------------------
KIND_PLANAR = "planar"
KIND_CYLINDRICAL = "cylindrical"
KIND_UNKNOWN = "unknown"

OUTLINE_RECT = "rect_like"
OUTLINE_CIRCULAR = "circular"
OUTLINE_SLOT = "slot_like"
OUTLINE_MIXED = "mixed"
OUTLINE_UNKNOWN = "unknown"      # outline 的 kind 推断

STATUS_OK = "ok"
STATUS_READ_ERROR = "read_error"  # 仅经 read_error_outline 构造

# ---- 几何元素枚举（pycatia CatGeometricType 静态定义） -----------------
GT_UNKNOWN = 0
GT_AXIS = 1
GT_POINT = 2
GT_LINE = 3
GT_CONTROL_POINT = 4
GT_CIRC_ARC = 5   # Circle2D：整圆与圆弧同类型
GT_ELLIPSE = 8
GT_SPLINE = 9

# 录入 outline 的边类型（其余元素视为辅助/控制元素，不参与边集合）
_ELEMENT_GEOMETRIC_TYPES = frozenset({GT_LINE, GT_CIRC_ARC, GT_ELLIPSE, GT_SPLINE})

# ---- 数值容差 ------------------------------------------------------------
_CLOSURE_TOL = 1e-6     # 闭合链 / 退化边 / 对边反向平行 / 轴对齐判定容差
_VEC_EPS = 1e-12        # 向量归一化模长下限（模长过小视为退化）


class GeometryQueryError(Exception):
    """几何查询失败。消息自带中文修复指引，tools 通用 except 直接回传 traceback。"""


@dataclass(frozen=True)
class SketchEdgeClass:
    """过滤非边元素后的草图边。

    index                : 过滤后 ordinal 1..N；BRep 侧面 Sketch.M;K 用此值（绝对不变）
    raw_collection_index : GeometricElements 原始 1-based 位置；仅诊断，不参与 BRep/法向
    geometric_type       : 几何元素枚举
    kind                 : KIND_PLANAR / KIND_CYLINDRICAL / KIND_UNKNOWN
    start / end          : 草图 2D 坐标 (x,y)；COM 端点读取失败为 None
    """
    index: int
    raw_collection_index: int
    geometric_type: int
    kind: str
    start: tuple | None
    end: tuple | None


@dataclass(frozen=True)
class SketchOutline:
    """某草图 feature 的轮廓推断结果（纯数据结构）。"""
    kind: str                  # 类型推断（含 rect_like 等），最终矩形须由端点复确认
    edges: tuple[SketchEdgeClass, ...]  # 已过滤，按原始枚举顺序
    edge_count: int | None     # 过滤后边数（=候选侧面数）；status!=ok 时 None
    status: str                # STATUS_OK（纯函数恒产）/ STATUS_READ_ERROR（上层构造）
    note: str


class FaceDescriptor(TypedDict, total=False):
    """面描述模型：get_*_faces 每项的字段契约。"""
    type: str                 # "top"/"bottom"/"side"/"surface"
    source: str               # 恒 "feature_inference"：由特征+草图推导的候选面，非最终拓扑枚举
    geometry_type: str
    normal: tuple | None
    normal_unresolved: str | None   # normal 为 None 的中文原因；已解析时为 None
    origin: tuple | None      # 顶/底=草图原点±depth；side 有端点则为端点 3D 坐标，否则 None
    face_brep: str
    edge_index: int | None    # ordinal（BRep K）
    edge_count: int | None    # outline.edge_count（草图边计数语义）
    error: str | None         # 恒为 None（读失败路径改为 raise，error 字段保留字段位）


# ---- 基础向量/数值工具 ---------------------------------------------------
def _vadd(a, b):
    """两向量逐分量相加。"""
    return tuple(x + y for x, y in zip(a, b))


def _vsub(a, b):
    """两向量逐分量相减。"""
    return tuple(x - y for x, y in zip(a, b))


def _vneg(a):
    """向量取负。"""
    return tuple(-x for x in a)


def _vlen(v):
    """向量欧氏模长。"""
    return math.sqrt(sum(x * x for x in v))


def _approx_vec(a, b, tol):
    """逐分量绝对差不超过 tol 视为近似相等。"""
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


def _normalize(v):
    """单位化向量；模长过小返回 None。"""
    m = _vlen(v)
    if m <= _VEC_EPS:
        return None
    return tuple(x / m for x in v)


def classify_element_kind(gt: int) -> str:
    """几何元素类型 -> 面类型归类。

    GT_LINE(3) -> planar；GT_CIRC_ARC(5) -> cylindrical；
    轴/点/控制点/椭圆/样条/未知 -> unknown。
    """
    if gt == GT_LINE:
        return KIND_PLANAR
    if gt == GT_CIRC_ARC:
        return KIND_CYLINDRICAL
    return KIND_UNKNOWN


def read_error_outline(note: str) -> SketchOutline:
    """读失败状态纯构造器：status=READ_ERROR / kind=unknown / edges=() / edge_count=None。

    上层 modeling.py 捕获几何元素枚举异常后调用；sketch_outline_from_element_types 永不产出该状态。
    """
    return SketchOutline(status=STATUS_READ_ERROR, kind=OUTLINE_UNKNOWN,
                         edges=(), edge_count=None, note=note)


def _infer_outline_kind(edges: Sequence[SketchEdgeClass]) -> str:
    """按边集合推断 outline kind（仅类型提示，最终矩形须由端点复确认）。"""
    if not edges:
        return OUTLINE_UNKNOWN
    if len(edges) == 4 and all(e.kind == KIND_PLANAR for e in edges):
        return OUTLINE_RECT
    if len(edges) == 1 and edges[0].kind == KIND_CYLINDRICAL:
        return OUTLINE_CIRCULAR
    planar_count = sum(1 for e in edges if e.kind == KIND_PLANAR)
    cyl_count = sum(1 for e in edges if e.kind == KIND_CYLINDRICAL)
    if len(edges) == 4 and planar_count == 2 and cyl_count == 2:
        return OUTLINE_SLOT
    return OUTLINE_MIXED


def sketch_outline_from_element_types(element_types: Iterable[int]) -> SketchOutline:
    """由几何元素类型序列构造 SketchOutline（纯模块无 COM，start/end 恒为 None）。

    仅 _ELEMENT_GEOMETRIC_TYPES 内元素进入 edges：index=过滤后 ordinal（1..N），
    raw_collection_index=原始位置。status 恒为 STATUS_OK。
    """
    edges: list[SketchEdgeClass] = []
    for raw_index, gt in enumerate(element_types, start=1):
        if gt not in _ELEMENT_GEOMETRIC_TYPES:
            continue
        edges.append(SketchEdgeClass(
            index=len(edges) + 1,
            raw_collection_index=raw_index,
            geometric_type=gt,
            kind=classify_element_kind(gt),
            start=None,
            end=None,
        ))
    return SketchOutline(kind=_infer_outline_kind(edges), edges=tuple(edges),
                         edge_count=len(edges), status=STATUS_OK, note="")


def confirm_rect_outline(edges: Sequence[SketchEdgeClass]) -> tuple[list[tuple[float, float]] | None, str]:
    """端点复确认 4 条平面边是否构成轴对齐矩形，成功返回各边 (H,V) 外法向。

    检查顺序：
    ① 恰 4 条且均 planar、start/end 非 None；
    ② 闭合链 edges[i].end≈edges[(i+1)%4].start（容差 1e-6）；
    ③ 非退化：每条边长>1e-6，且对边反向平行（d2≈-d0、d3≈-d1）；
    ④ 轴对齐：每条边方向平行于草图 (1,0) 或 (0,1)；
    ⑤ 包围范围非退化：全部顶点的 x/y 跨度均>1e-6（二维面积非零），排除四边共线等零面积闭合链。
    全部通过 -> 以质心为内判据点取「背离质心」的外法向（与枚举方向 CW/CCW 无关）；
    失败 -> (None, 中文原因)。
    """
    if len(edges) != 4:
        return None, f"矩形确认失败：需要恰好 4 条边（当前 {len(edges)} 条）"
    for e in edges:
        if e.kind != KIND_PLANAR:
            return None, "矩形确认失败：非全部为平面（直线）边，不能按矩形套用"
    for e in edges:
        if e.start is None or e.end is None:
            return None, "无端点坐标，无法确认矩形，不得套表"
    starts = [e.start for e in edges]
    ends = [e.end for e in edges]
    dirs = [_vsub(ends[i], starts[i]) for i in range(4)]

    for i in range(4):
        if not _approx_vec(ends[i], starts[(i + 1) % 4], _CLOSURE_TOL):
            return None, "矩形确认失败：边链未闭合，端点首尾未重合"
    for d in dirs:
        if _vlen(d) <= _CLOSURE_TOL:
            return None, "矩形确认失败：存在长度过小的退化边"
    if not _approx_vec(dirs[2], _vneg(dirs[0]), _CLOSURE_TOL):
        return None, "矩形确认失败：对边不反向平行，非矩形轮廓"
    if not _approx_vec(dirs[3], _vneg(dirs[1]), _CLOSURE_TOL):
        return None, "矩形确认失败：对边不反向平行，非矩形轮廓"
    for d in dirs:
        if min(abs(d[0]), abs(d[1])) > _CLOSURE_TOL:
            return None, "矩形确认失败：边未与草图 H/V 轴对齐"

    # 包围范围非退化：x/y 跨度均须 > 容差，排除四边共线等零面积闭合链（如沿轴往返）
    xs = [p[0] for p in starts]
    ys = [p[1] for p in starts]
    if max(xs) - min(xs) <= _CLOSURE_TOL or max(ys) - min(ys) <= _CLOSURE_TOL:
        return None, "矩形确认失败：退化轮廓（二维包围范围退化为线或点，面积约为零），非矩形"

    # 外法向：质心为内判据点，取「背离质心」一侧的垂向
    cx = sum(p[0] for p in starts) / 4.0
    cy = sum(p[1] for p in starts) / 4.0
    normals: list[tuple[float, float]] = []
    for i in range(4):
        dx, dy = dirs[i]
        ux = (starts[i][0] + ends[i][0]) / 2.0 - cx
        uy = (starts[i][1] + ends[i][1]) / 2.0 - cy
        n = (dy, -dx)
        if n[0] * ux + n[1] * uy < 0:
            n = (-dy, dx)
        m = math.sqrt(n[0] * n[0] + n[1] * n[1])
        normals.append((n[0] / m, n[1] / m))
    return normals, ""


def map_2d_normal_to_3d(n2: tuple[float, float], h_axis, v_axis) -> tuple | None:
    """2D (H,V) 法向 -> 3D：normalize(n2[0]*h_axis + n2[1]*v_axis)；模长过小返回 None。"""
    vec = tuple(n2[0] * h_axis[i] + n2[1] * v_axis[i] for i in range(3))
    return _normalize(vec)


def describe_sides(outline: SketchOutline, brep_by_index: Mapping[int, str],
                   h_axis, v_axis, *, origin=None, outward: bool = True) -> list[dict]:
    """由 SketchOutline 产出候选侧面（type="side"）描述列表。

    先经 confirm_rect_outline 复确认：成功则逐边给出 3D 外法向（Pocket=outward=False 时取反）；
    失败则按边 kind 分别置 normal=None 并附中文未解析原因，绝不给出假法向。
    origin 与 edge.start 均可用时给出真实侧面上的 3D 点，否则 None。
    """
    if outline.status != STATUS_OK:
        raise GeometryQueryError(
            f"草图 outline 状态为 {outline.status}（{outline.note}），无法生成侧面描述。"
            "请确认草图几何元素读取成功，或修复草图后重新读取。")
    n2s, reason = confirm_rect_outline(outline.edges)
    faces: list[dict] = []
    for pos, edge in enumerate(outline.edges):
        if n2s is not None:
            n3 = map_2d_normal_to_3d(n2s[pos], h_axis, v_axis)
            if n3 is None:
                normal, unresolved = None, "草图 H/V 轴退化，无法把 2D 法向映射到 3D"
            else:
                normal = tuple(-c for c in n3) if not outward else n3
                unresolved = None
        elif edge.kind == KIND_PLANAR:
            normal, unresolved = None, reason
        elif edge.kind == KIND_CYLINDRICAL:
            normal, unresolved = None, "圆柱曲面无单一全局法向"
        else:
            normal, unresolved = None, "元素类型无法归类为平面/圆柱"

        origin_3d = None
        if origin is not None and edge.start is not None:
            origin_3d = tuple(origin[i] + h_axis[i] * edge.start[0] + v_axis[i] * edge.start[1]
                              for i in range(3))
        faces.append({
            "type": "side",
            "source": "feature_inference",
            "geometry_type": edge.kind,
            "normal": normal,
            "normal_unresolved": unresolved,
            "origin": origin_3d,
            "face_brep": brep_by_index[edge.index],
            "edge_index": edge.index,
            "edge_count": outline.edge_count,
            "error": None,
        })
    return faces


def describe_surfaces(outline: SketchOutline, brep_by_index: Mapping[int, str], *,
                      origin=None) -> list[dict]:
    """Shaft/Groove 用：产出候选旋转面（type="surface"）描述，S2 不分类。

    旋转面偏离轴的方向由坐标解析判定（后续任务），此处一律 geometry_type=unknown、
    normal=None、origin=None（无坐标依据不给假点）。
    """
    if outline.status != STATUS_OK:
        raise GeometryQueryError(
            f"草图 outline 状态为 {outline.status}（{outline.note}），无法生成旋转面描述。"
            "请确认草图几何元素读取成功，或修复草图后重新读取。")
    faces: list[dict] = []
    for edge in outline.edges:
        faces.append({
            "type": "surface",
            "source": "feature_inference",
            "geometry_type": KIND_UNKNOWN,
            "normal": None,
            "normal_unresolved": "旋转面类型依赖边相对旋转轴的方向，需坐标解析；S2 不分类",
            "origin": None,
            "face_brep": brep_by_index[edge.index],
            "edge_index": edge.index,
            "edge_count": outline.edge_count,
            "error": None,
        })
    return faces


def filter_faces_by_normal(faces: Iterable[dict], normal: tuple, tolerance_deg: float = 5.0, *,
                           planar_only: bool = False) -> list[dict]:
    """按目标法向 + 角度容差筛选候选面。

    校验 目标法向为有限非零 3D 向量、容差有限且位于 [0,180]；跳过 normal=None 的面
    （容差 180 也不匹配未知法向）；每面法向先查长度 3、全分量有限且非零，非法面直接
    跳过（不做 zip 截断/D 匹配、不 NaN 静默）；planar_only=True 时再跳过非 planar 面。
    与旧实现（点积累加 + 余弦阈值）一致。
    """
    if len(normal) != 3 or not all(math.isfinite(c) for c in normal):
        raise ValueError("目标法向必须是有限非零向量")
    n_len = math.sqrt(sum(c * c for c in normal))
    if n_len <= _VEC_EPS:
        raise ValueError("目标法向必须是有限非零向量")
    if not math.isfinite(tolerance_deg) or not (0.0 <= tolerance_deg <= 180.0):
        raise ValueError("容差 tolerance_deg 必须为有限值，且位于 [0, 180] 度区间")

    tgt = tuple(c / n_len for c in normal)
    cos_tol = math.cos(math.radians(tolerance_deg))
    result: list[dict] = []
    for face in faces:
        fn = face.get("normal")
        if fn is None:
            continue
        if planar_only and face.get("geometry_type") != KIND_PLANAR:
            continue
        if len(fn) != 3 or not all(math.isfinite(c) for c in fn):
            continue
        fn_len = math.sqrt(sum(c * c for c in fn))
        if fn_len <= _VEC_EPS:
            continue
        fu = tuple(c / fn_len for c in fn)
        if sum(a * b for a, b in zip(fu, tgt)) >= cos_tol:
            result.append(face)
    return result


def side_neighbor_positions(faces: Sequence[dict], edge_index: int | None) -> tuple[int, int] | None:
    """faces 中 edge_index（ordinal）相等项的列表位置 j 与循环相邻位 (prev_j, next_j)。

    按列表位置选邻（不依赖 raw_collection_index、不假设索引连续）；查无 -> None；
    单元素 -> (0,0)（调用侧以 prev_j != next_j 守卫跳过自引用）。
    纯只读：不改写 faces / edge_index / raw_collection_index / face_brep，
    不涉及任何 raw BRep 取址或替换。若同一 ordinal 多次出现，取首个匹配位置。
    """
    if edge_index is None or not faces:
        return None
    n = len(faces)
    j = None
    for i, face in enumerate(faces):
        if face.get("edge_index") == edge_index:
            j = i
            break
    if j is None:
        return None
    return ((j - 1) % n, (j + 1) % n)