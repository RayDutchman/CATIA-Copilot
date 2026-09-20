# -*- coding: utf-8 -*-
"""geometry_faces.py 纯几何分类模块（S2 Task1）单元测试：无 COM、纯数据。

S2 Task2 追加 modeling.py 接线测试：只读源码文本做 AST 受控加载，绝不 import modeling.py
（其模块顶层 import pycatia/COM 包装，无 COM 环境直接 import 不可行）。
"""
import ast
import math
import pathlib
import logging
import unittest
from dataclasses import replace
from types import SimpleNamespace

import catia_copilot.catia.geometry_faces as gf

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODELING_SRC = _ROOT / "catia_copilot" / "catia" / "modeling.py"


def _extract_func(name):
    """从 modeling.py AST 抽取顶层 FunctionDef（绝不 import modeling.py）。"""
    tree = ast.parse(_MODELING_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"modeling.py 未定义 {name}")


def _exec_func(node, ns):
    """把单个 FunctionDef 编入注入命名空间并返回函数对象。"""
    mod = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, "<modeling.py>", "exec"), ns)   # noqa: S102 - 受控 AST 源码文本
    return ns[node.name]


def _edge(gtype, kind, index, raw=None, start=None, end=None):
    """构造单条 SketchEdgeClass（raw 缺省取 index，便于原始/ordinal 分离测试）。"""
    return gf.SketchEdgeClass(
        index=index,
        raw_collection_index=raw if raw is not None else index,
        geometric_type=gtype,
        kind=kind,
        start=start,
        end=end,
    )


def _line_edge(index, start=None, end=None, raw=None):
    return _edge(gf.GT_LINE, gf.KIND_PLANAR, index, raw=raw, start=start, end=end)


def _rect_edges(corners, *, index_base=1, raw_base=1):
    """按拐点序列构造闭合矩形链边：corners[i] -> corners[i+1]（末边闭合回 corners[0]）。"""
    pts = list(corners) + [corners[0]]
    return [
        _line_edge(index_base + i, start=pts[i], end=pts[i + 1], raw=raw_base + i)
        for i in range(len(corners))
    ]


def _outline(edges, *, kind=gf.OUTLINE_RECT, note=""):
    return gf.SketchOutline(kind=kind, edges=tuple(edges),
                            edge_count=len(edges), status=gf.STATUS_OK, note=note)


_CORNERS = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
_H = (1.0, 0.0, 0.0)
_V = (0.0, 1.0, 0.0)
_BREP = {i: f"BRep;{i}" for i in range(1, 5)}


class TestConstants(unittest.TestCase):
    def test_geometric_type_enums(self):
        self.assertEqual(gf.GT_UNKNOWN, 0)
        self.assertEqual(gf.GT_AXIS, 1)
        self.assertEqual(gf.GT_POINT, 2)
        self.assertEqual(gf.GT_LINE, 3)
        self.assertEqual(gf.GT_CONTROL_POINT, 4)
        self.assertEqual(gf.GT_CIRC_ARC, 5)
        self.assertEqual(gf.GT_ELLIPSE, 8)
        self.assertEqual(gf.GT_SPLINE, 9)

    def test_edge_type_filter_excludes_non_edges(self):
        self.assertEqual(gf._ELEMENT_GEOMETRIC_TYPES,
                         {gf.GT_LINE, gf.GT_CIRC_ARC, gf.GT_ELLIPSE, gf.GT_SPLINE})
        self.assertNotIn(gf.GT_AXIS, gf._ELEMENT_GEOMETRIC_TYPES)
        self.assertNotIn(gf.GT_POINT, gf._ELEMENT_GEOMETRIC_TYPES)
        self.assertNotIn(gf.GT_CONTROL_POINT, gf._ELEMENT_GEOMETRIC_TYPES)

    def test_kind_and_status_constants(self):
        self.assertEqual(gf.KIND_PLANAR, "planar")
        self.assertEqual(gf.KIND_CYLINDRICAL, "cylindrical")
        self.assertEqual(gf.KIND_UNKNOWN, "unknown")
        self.assertEqual(gf.OUTLINE_RECT, "rect_like")
        self.assertEqual(gf.OUTLINE_CIRCULAR, "circular")
        self.assertEqual(gf.OUTLINE_SLOT, "slot_like")
        self.assertEqual(gf.OUTLINE_MIXED, "mixed")
        self.assertEqual(gf.OUTLINE_UNKNOWN, "unknown")
        self.assertEqual(gf.STATUS_OK, "ok")
        self.assertEqual(gf.STATUS_READ_ERROR, "read_error")


class TestClassifyElementKind(unittest.TestCase):
    def test_line_to_planar(self):
        self.assertEqual(gf.classify_element_kind(gf.GT_LINE), gf.KIND_PLANAR)

    def test_circle_arc_to_cylindrical(self):
        self.assertEqual(gf.classify_element_kind(gf.GT_CIRC_ARC), gf.KIND_CYLINDRICAL)

    def test_non_edge_types_unknown(self):
        for gt in (gf.GT_UNKNOWN, gf.GT_AXIS, gf.GT_POINT,
                   gf.GT_CONTROL_POINT, gf.GT_ELLIPSE, gf.GT_SPLINE):
            self.assertEqual(gf.classify_element_kind(gt), gf.KIND_UNKNOWN, gt)

    def test_unknown_values_unknown(self):
        self.assertEqual(gf.classify_element_kind(99), gf.KIND_UNKNOWN)
        self.assertEqual(gf.classify_element_kind(-1), gf.KIND_UNKNOWN)


class TestSketchOutline(unittest.TestCase):
    def test_four_lines_rect_like(self):
        o = gf.sketch_outline_from_element_types([3, 3, 3, 3])
        self.assertEqual(o.status, gf.STATUS_OK)
        self.assertEqual(o.kind, gf.OUTLINE_RECT)
        self.assertEqual(o.edge_count, 4)
        self.assertEqual([e.index for e in o.edges], [1, 2, 3, 4])
        self.assertEqual([e.raw_collection_index for e in o.edges], [1, 2, 3, 4])
        for e in o.edges:
            self.assertEqual(e.kind, gf.KIND_PLANAR)
            self.assertEqual(e.geometric_type, gf.GT_LINE)
            self.assertIsNone(e.start)
            self.assertIsNone(e.end)

    def test_single_circle_circular(self):
        o = gf.sketch_outline_from_element_types([5])
        self.assertEqual(o.kind, gf.OUTLINE_CIRCULAR)
        self.assertEqual(o.edge_count, 1)
        self.assertEqual(o.edges[0].kind, gf.KIND_CYLINDRICAL)

    def test_two_lines_two_arcs_slot_like(self):
        o = gf.sketch_outline_from_element_types([3, 3, 5, 5])
        self.assertEqual(o.kind, gf.OUTLINE_SLOT)
        self.assertEqual(o.edge_count, 4)

    def test_mixed(self):
        o = gf.sketch_outline_from_element_types([3, 5, 8])
        self.assertEqual(o.kind, gf.OUTLINE_MIXED)
        self.assertEqual(o.edge_count, 3)

    def test_control_point_not_edge(self):
        o = gf.sketch_outline_from_element_types([4])
        self.assertEqual(o.kind, gf.OUTLINE_UNKNOWN)
        self.assertEqual(o.edge_count, 0)
        self.assertEqual(o.edges, ())
        self.assertEqual(o.status, gf.STATUS_OK)

    def test_sparse_collection_ordinal_and_raw(self):
        o = gf.sketch_outline_from_element_types([1, 3, 2, 3, 3, 3])
        self.assertEqual(o.kind, gf.OUTLINE_RECT)
        self.assertEqual(o.edge_count, 4)
        self.assertEqual([e.index for e in o.edges], [1, 2, 3, 4])
        self.assertEqual([e.raw_collection_index for e in o.edges], [2, 4, 5, 6])

    def test_empty_unknown(self):
        o = gf.sketch_outline_from_element_types([])
        self.assertEqual(o.kind, gf.OUTLINE_UNKNOWN)
        self.assertEqual(o.edge_count, 0)
        self.assertEqual(o.edges, ())


class TestReadErrorOutlineState(unittest.TestCase):
    def test_read_error_constructor_fields(self):
        o = gf.read_error_outline("boom")
        self.assertEqual(o.status, gf.STATUS_READ_ERROR)
        self.assertEqual(o.kind, gf.OUTLINE_UNKNOWN)
        self.assertEqual(o.edges, ())
        self.assertIsNone(o.edge_count)
        self.assertEqual(o.note, "boom")

    def test_sketch_outline_never_read_error(self):
        self.assertEqual(gf.sketch_outline_from_element_types([3, 3, 3, 3]).status,
                         gf.STATUS_OK)
        self.assertEqual(gf.sketch_outline_from_element_types([4]).status, gf.STATUS_OK)


class TestConfirmRectOutline(unittest.TestCase):
    def test_draw_rect_order(self):
        edges = _rect_edges(_CORNERS)
        n2s, reason = gf.confirm_rect_outline(edges)
        self.assertIsNotNone(n2s)
        self.assertEqual(reason, "")
        # 边链顺序即 (x,y)-(x+w,y)-(x+w,y+h)-(x,y+h)-闭合：
        # 外法向按枚举顺序 (0,-1),(1,0),(0,1),(-1,0)
        self.assertEqual(n2s, [(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)])

    def test_reversed_draw_order_same_outward_set(self):
        corners = [(0.0, 0.0), (0.0, 5.0), (10.0, 5.0), (10.0, 0.0)]   # CW
        n2s, reason = gf.confirm_rect_outline(_rect_edges(corners))
        self.assertIsNotNone(n2s)
        self.assertEqual({tuple(n) for n in n2s},
                         {(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)})
        # 每边法向必须"背离质心"（向外），与枚举方向无关
        cx = sum(x for x, _ in corners) / 4.0
        cy = sum(y for _, y in corners) / 4.0
        for i, n in enumerate(n2s):
            mx = (corners[i][0] + corners[(i + 1) % 4][0]) / 2.0
            my = (corners[i][1] + corners[(i + 1) % 4][1]) / 2.0
            self.assertGreater(n[0] * (mx - cx) + n[1] * (my - cy), 0.0)

    def test_missing_endpoint(self):
        edges = _rect_edges(_CORNERS)
        edges[1] = _line_edge(2, start=None, end=(10.0, 5.0))
        n2s, reason = gf.confirm_rect_outline(edges)
        self.assertIsNone(n2s)
        self.assertIn("端点", reason)

    def test_not_closed(self):
        edges = _rect_edges(_CORNERS)
        edges[2] = _line_edge(3, start=(10.0, 5.0), end=(9.0, 5.0))
        n2s, reason = gf.confirm_rect_outline(edges)
        self.assertIsNone(n2s)
        self.assertIn("未闭合", reason)

    def test_closure_beyond_tolerance_fails(self):
        edges = _rect_edges(_CORNERS)
        # 仅移动 e0 终点，e1 起点保持原样：端点首尾错位 1e-4 > 容差
        edges[0] = _line_edge(1, start=(0.0, 0.0), end=(10.0 + 1e-4, 0.0))
        n2s, reason = gf.confirm_rect_outline(edges)
        self.assertIsNone(n2s)
        self.assertIn("未闭合", reason)

    def test_degenerate_zero_height(self):
        corners = [(0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
        n2s, reason = gf.confirm_rect_outline(_rect_edges(corners))
        self.assertIsNone(n2s)
        self.assertIn("退化", reason)

    def test_not_axis_aligned_45deg(self):
        corners = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0), (1.0, -1.0)]
        n2s, reason = gf.confirm_rect_outline(_rect_edges(corners))
        self.assertIsNone(n2s)
        self.assertIn("轴对齐", reason)

    def test_collinear_zero_area_rejected(self):
        # 四条非零共线线段首尾闭合（沿 x 轴往返），对边反向平行且轴对齐，
        # 但二维包围范围退化为一条线（面积为零），不得误判为矩形
        corners = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (10.0, 0.0)]
        n2s, reason = gf.confirm_rect_outline(_rect_edges(corners))
        self.assertIsNone(n2s)
        self.assertIn("退化", reason)
        self.assertIn("面积", reason)

    def test_wrong_edge_count(self):
        n2s, reason = gf.confirm_rect_outline(_rect_edges(_CORNERS)[:3])
        self.assertIsNone(n2s)
        self.assertIn("4", reason)

    def test_non_planar_edge_rejected(self):
        edges = [i for i in _rect_edges(_CORNERS)]
        edges[1] = _edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 2,
                         start=(10.0, 0.0), end=(10.0, 5.0))
        n2s, reason = gf.confirm_rect_outline(edges)
        self.assertIsNone(n2s)
        self.assertIn("平面", reason)

    def test_noise_within_tolerance_confirmed(self):
        corners = [(x + 1e-9 * (i + 1), y - 1e-9 * (i + 2))
                   for i, (x, y) in enumerate(_CORNERS)]
        n2s, reason = gf.confirm_rect_outline(_rect_edges(corners))
        self.assertIsNotNone(n2s)
        self.assertEqual(reason, "")


class TestMap2DTo3D(unittest.TestCase):
    def test_hv_basis_mapping(self):
        self.assertEqual(gf.map_2d_normal_to_3d((0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                         (0.0, -1.0, 0.0))
        self.assertEqual(gf.map_2d_normal_to_3d((1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                         (1.0, 0.0, 0.0))
        self.assertEqual(gf.map_2d_normal_to_3d((0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
                         (0.0, 0.0, 1.0))
        self.assertEqual(gf.map_2d_normal_to_3d((1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                         (0.0, 1.0, 0.0))

    def test_zero_vector_returns_none(self):
        self.assertIsNone(gf.map_2d_normal_to_3d((0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))

    def test_cancelling_axes_returns_none(self):
        self.assertIsNone(gf.map_2d_normal_to_3d((1.0, 1.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)))


class TestDescribeSides(unittest.TestCase):
    def test_rect_sides_normals(self):
        o = _outline(_rect_edges(_CORNERS))
        faces = gf.describe_sides(o, _BREP, _H, _V)
        self.assertEqual([f["type"] for f in faces], ["side"] * 4)
        self.assertEqual([f["normal"] for f in faces],
                         [(0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)])
        self.assertEqual([f["geometry_type"] for f in faces], [gf.KIND_PLANAR] * 4)
        self.assertTrue(all(f["normal_unresolved"] is None for f in faces))
        self.assertEqual([f["edge_index"] for f in faces], [1, 2, 3, 4])
        self.assertEqual([f["edge_count"] for f in faces], [4, 4, 4, 4])
        self.assertTrue(all(f["error"] is None for f in faces))

    def test_rect_outward_false_reverses(self):
        o = _outline(_rect_edges(_CORNERS))
        faces = gf.describe_sides(o, _BREP, _H, _V, outward=False)
        self.assertEqual([f["normal"] for f in faces],
                         [(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)])

    def test_side_origin_real_vs_none(self):
        o = _outline(_rect_edges(_CORNERS))
        faces = gf.describe_sides(o, _BREP, _H, _V, origin=(1.0, 2.0, 3.0))
        self.assertEqual([f["origin"] for f in faces],
                         [(1.0, 2.0, 3.0), (11.0, 2.0, 3.0), (11.0, 7.0, 3.0), (1.0, 7.0, 3.0)])
        faces_no_origin = gf.describe_sides(o, _BREP, _H, _V)
        self.assertTrue(all(f["origin"] is None for f in faces_no_origin))

    def test_missing_endpoint_normal_none(self):
        edges = _rect_edges(_CORNERS)
        edges[1] = _line_edge(2, start=None, end=(10.0, 5.0))
        o = _outline(edges)
        faces = gf.describe_sides(o, _BREP, _H, _V)
        self.assertTrue(all(f["normal"] is None for f in faces))
        for f in faces:
            self.assertIsNotNone(f["normal_unresolved"])
            self.assertIn("端点", f["normal_unresolved"])

    def test_circular_cylindrical_none(self):
        edges = [_edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 1)]
        o = _outline(edges, kind=gf.OUTLINE_CIRCULAR)
        faces = gf.describe_sides(o, {1: "C;1"}, _H, _V, origin=(0.0, 0.0, 0.0))
        self.assertEqual(len(faces), 1)
        f = faces[0]
        self.assertEqual(f["type"], "side")
        self.assertEqual(f["geometry_type"], gf.KIND_CYLINDRICAL)
        self.assertIsNone(f["normal"])
        self.assertIn("圆柱", f["normal_unresolved"])
        self.assertEqual(f["edge_count"], 1)
        self.assertIsNone(f["origin"], "无端点坐标时必须返回 None，不给假点")

    def test_slot_sides_unresolved(self):
        edges = [
            _line_edge(1), _line_edge(2),
            _edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 3),
            _edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 4),
        ]
        o = _outline(edges, kind=gf.OUTLINE_SLOT)
        faces = gf.describe_sides(o, _BREP, _H, _V)
        self.assertEqual(len(faces), 4)
        self.assertTrue(all(f["normal"] is None for f in faces))
        self.assertTrue(all(f["normal"] != (0.0, 0.0, 0.0) for f in faces))
        for f in faces[:2]:
            self.assertIsNotNone(f["normal_unresolved"], "planar 侧面必须带未解析原因")
        for f in faces[2:]:
            self.assertIn("圆柱", f["normal_unresolved"])

    def test_mixed_unknown_kind_fields(self):
        edges = [
            _line_edge(1), _line_edge(4),
            _edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 2),
            _edge(gf.GT_SPLINE, gf.KIND_UNKNOWN, 3),
        ]
        o = _outline(edges, kind=gf.OUTLINE_MIXED)
        faces = gf.describe_sides(o, _BREP, _H, _V)
        spline_face = next(f for f in faces if f["edge_index"] == 3)
        self.assertEqual(spline_face["geometry_type"], gf.KIND_UNKNOWN)
        self.assertIsNone(spline_face["normal"])
        self.assertIn("无法归类", spline_face["normal_unresolved"])
        arc_face = next(f for f in faces if f["edge_index"] == 2)
        self.assertIn("圆柱", arc_face["normal_unresolved"])

    def test_brep_passthrough_by_ordinal_index(self):
        edges = _rect_edges(_CORNERS, raw_base=5)   # raw 位移 5..8，ordinal 仍 1..4
        o = _outline(edges)
        faces = gf.describe_sides(o, _BREP, _H, _V)
        self.assertEqual([f["face_brep"] for f in faces], [f"BRep;{i}" for i in (1, 2, 3, 4)])
        # 若按 raw 索引取 BRep 会 KeyError，证明按 ordinal 键命中
        raw_only = {5: "X;5", 6: "X;6", 7: "X;7", 8: "X;8"}
        with self.assertRaises(KeyError):
            gf.describe_sides(o, raw_only, _H, _V)

    def test_face_source_feature_inference(self):
        o = _outline(_rect_edges(_CORNERS))
        faces = gf.describe_sides(o, _BREP, _H, _V)
        self.assertTrue(all(f["source"] == "feature_inference" for f in faces))
        circle = _outline([_edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 1)],
                          kind=gf.OUTLINE_CIRCULAR)
        self.assertTrue(all(f["source"] == "feature_inference"
                            for f in gf.describe_sides(circle, {1: "C;1"}, _H, _V)))

    def test_read_error_raises_geometry_query_error(self):
        o = gf.read_error_outline("boom")
        with self.assertRaises(gf.GeometryQueryError) as cm:
            gf.describe_sides(o, {}, _H, _V)
        self.assertIn("boom", str(cm.exception))


class TestDescribeSurfaces(unittest.TestCase):
    SURF_BREP = {1: "S;1", 2: "S;2", 3: "S;3"}

    def test_surfaces_fields(self):
        edges = [_line_edge(1), _edge(gf.GT_CIRC_ARC, gf.KIND_CYLINDRICAL, 2),
                 _edge(gf.GT_SPLINE, gf.KIND_UNKNOWN, 3)]
        o = _outline(edges, kind=gf.OUTLINE_MIXED)
        faces = gf.describe_surfaces(o, self.SURF_BREP)
        self.assertEqual(len(faces), 3)
        for f in faces:
            self.assertEqual(f["type"], "surface")
            self.assertEqual(f["source"], "feature_inference")
            self.assertEqual(f["geometry_type"], gf.KIND_UNKNOWN)
            self.assertIsNone(f["normal"])
            self.assertIn("旋转面", f["normal_unresolved"])
            self.assertIsNone(f["origin"])
            self.assertIsNone(f["error"])
            self.assertEqual(f["edge_count"], 3)
        self.assertEqual([f["face_brep"] for f in faces], ["S;1", "S;2", "S;3"])
        self.assertEqual([f["edge_index"] for f in faces], [1, 2, 3])

    def test_brep_by_ordinal_index(self):
        edges = _rect_edges(_CORNERS, raw_base=5)
        o = _outline(edges)
        faces = gf.describe_surfaces(o, _BREP, origin=(0.0, 0.0, 0.0))
        self.assertEqual([f["face_brep"] for f in faces], [f"BRep;{i}" for i in (1, 2, 3, 4)])
        self.assertTrue(all(f["origin"] is None for f in faces))

    def test_read_error_raises_geometry_query_error(self):
        o = gf.read_error_outline("nope")
        with self.assertRaises(gf.GeometryQueryError) as cm:
            gf.describe_surfaces(o, {}, origin=None)
        self.assertIn("nope", str(cm.exception))


class TestFilterFacesByNormal(unittest.TestCase):
    def test_target_normal_must_be_finite_nonzero(self):
        for bad in ((1.0, 2.0), (1.0, 2.0, float("nan")), (0.0, 0.0, 0.0), (1, 2, 3, 4)):
            with self.assertRaises(ValueError):
                gf.filter_faces_by_normal([], bad)

    def test_tolerance_range_validation(self):
        for tol in (-1.0, 181.0, float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                gf.filter_faces_by_normal([], (1.0, 0.0, 0.0), tolerance_deg=tol)

    def test_zero_tolerance_exact_match(self):
        faces = [
            {"normal": (1.0, 0.0, 0.0)},
            {"normal": (0.6, 0.8, 0.0)},
            {"normal": (0.0, 1.0, 0.0)},
        ]
        out = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=0.0)
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], faces[0])

    def test_cosine_threshold_boundary(self):
        faces = [
            {"normal": (1.0, 0.0, 0.0)},
            {"normal": (0.6, 0.8, 0.0)},   # 与 (1,0,0) 夹角 ≈53.13°
            {"normal": (0.0, 1.0, 0.0)},   # 90°
        ]
        out5 = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=5.0)
        self.assertEqual([f["normal"] for f in out5], [(1.0, 0.0, 0.0)])
        out60 = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=60.0)
        self.assertEqual([f["normal"] for f in out60],
                         [(1.0, 0.0, 0.0), (0.6, 0.8, 0.0)])
        out45 = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=45.0)
        self.assertEqual(len(out45), 1, "53.13° > 45° 容差应被排除")
        self.assertEqual(gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=180.0),
                         faces)

    def test_none_normal_skipped_even_at_180(self):
        faces = [
            {"normal": None, "tag": "none"},
            {"normal": (1.0, 0.0, 0.0), "tag": "h"},
            {"normal": (0.0, 0.0, 1.0), "tag": "z"},
        ]
        out = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=180.0)
        self.assertEqual({f["tag"] for f in out}, {"h", "z"})
        self.assertNotIn(faces[0], out)

    def test_invalid_face_normal_skipped(self):
        faces = [
            {"normal": (1.0, 0.0), "tag": "len2"},               # 长度 2：旧实现 zip 截断可被误匹配
            {"normal": (1.0, float("nan"), 0.0), "tag": "nan"},  # 含 NaN：非法法向应跳过而非 NaN 静默
            {"normal": (0.0, 0.0, 0.0), "tag": "zero"},          # 零向量：非法法向应跳过
            {"normal": (1.0, 0.0, 0.0), "tag": "h"},
        ]
        out = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), tolerance_deg=0.0)
        self.assertEqual({f["tag"] for f in out}, {"h"})
        self.assertNotIn(faces[0], out)
        self.assertNotIn(faces[1], out)
        self.assertNotIn(faces[2], out)

    def test_planar_only_skips_non_planar(self):
        faces = [
            {"normal": (1.0, 0.0, 0.0), "geometry_type": gf.KIND_PLANAR, "tag": "p"},
            {"normal": (1.0, 0.0, 0.0), "geometry_type": gf.KIND_CYLINDRICAL, "tag": "c"},
            {"normal": (1.0, 0.0, 0.0), "tag": "no-type"},
        ]
        out = gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0), planar_only=True)
        self.assertEqual({f["tag"] for f in out}, {"p"})
        self.assertEqual(len(gf.filter_faces_by_normal(faces, (1.0, 0.0, 0.0))), 3)


class TestSideNeighborPositions(unittest.TestCase):
    @staticmethod
    def _faces(indices, raw_base=None):
        return [{"edge_index": i,
                 "raw_collection_index": i if raw_base is None else raw_base + j}
                for j, i in enumerate(indices)]

    def test_cyclic_neighbors_by_list_position(self):
        faces = self._faces([1, 2, 3, 4])
        self.assertEqual(gf.side_neighbor_positions(faces, 1), (3, 1))
        self.assertEqual(gf.side_neighbor_positions(faces, 2), (0, 2))
        self.assertEqual(gf.side_neighbor_positions(faces, 3), (1, 3))
        self.assertEqual(gf.side_neighbor_positions(faces, 4), (2, 0))

    def test_raw_shift_still_list_position(self):
        faces = self._faces([1, 2, 3, 4], raw_base=5)   # raw=[5,6,7,8]
        self.assertEqual(gf.side_neighbor_positions(faces, 2), (0, 2))
        self.assertEqual(gf.side_neighbor_positions(faces, 4), (2, 0))

    def test_absent_or_none_returns_none(self):
        faces = self._faces([1, 2, 3, 4])
        self.assertIsNone(gf.side_neighbor_positions(faces, 7))
        self.assertIsNone(gf.side_neighbor_positions(faces, None))
        self.assertIsNone(gf.side_neighbor_positions([], 1))

    def test_single_element_returns_00(self):
        faces = self._faces([1])
        self.assertEqual(gf.side_neighbor_positions(faces, 1), (0, 0))
        self.assertIsNone(gf.side_neighbor_positions(faces, 2))

    def test_neighbors_never_self_when_n_ge_2(self):
        faces = self._faces([1, 2, 3, 4])
        for idx in (1, 2, 3, 4):
            j = idx - 1
            prev_j, next_j = gf.side_neighbor_positions(faces, idx)
            self.assertNotEqual(prev_j, j)
            self.assertNotEqual(next_j, j)


# ---------------------------------------------------------------------------
# S2 Task2：modeling.py 接线（AST 受控加载，绝不 import modeling.py）
# ---------------------------------------------------------------------------

class TestNoFallback4InModelingSource(unittest.TestCase):
    """静态守卫：防 fallback=4（默认矩形）回归。"""

    def test_modeling_src_has_no_default_rectangle_fallback(self):
        src = _MODELING_SRC.read_text(encoding="utf-8")
        self.assertNotIn("默认矩形", src, "几何未知时不得 fallback=4（默认矩形）")
        self.assertNotIn("return 4", src)   # 覆盖 _get_sk_edge_count_from_sketch 与 _pad_geometry 的旧兜底


class _FakeGEItem:
    """带 GeometricType 的原始 GE 假件。"""

    def __init__(self, gtype):
        self.GeometricType = gtype


class _FakeGE:
    """草图 GeometricElements 假件：Count + Item(i)（1-based）。"""

    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class _RaisingGE:
    """每次 Item 都抛 COM 风格异常的 GeometricElements 假件。"""

    Count = 1

    def Item(self, i):
        raise RuntimeError("COM 读取异常: GeometricElements.Item failed (0x80020009)")


class _FakeSketch:
    """带 GeometricElements 的 sketch_com 假件。"""

    def __init__(self, ge):
        self.GeometricElements = ge


class TestModelingSketchOutlineWiring(unittest.TestCase):
    """AST 受控加载 modeling._get_sketch_outline，验证对 geometry_faces 的接线。"""

    @staticmethod
    def _load(edge_stub=None):
        ns = {
            "SketchOutline": gf.SketchOutline,
            "STATUS_OK": gf.STATUS_OK,
            "OUTLINE_UNKNOWN": gf.OUTLINE_UNKNOWN,
            "read_error_outline": gf.read_error_outline,
            "sketch_outline_from_element_types": gf.sketch_outline_from_element_types,
            "_edge_endpoints": edge_stub,
            "replace": replace,
        }
        return _exec_func(_extract_func("_get_sketch_outline"), ns)

    def test_read_error_wires_read_error_outline(self):
        fn = self._load()
        out = fn(_FakeSketch(_RaisingGE()))
        self.assertEqual(out.status, gf.STATUS_READ_ERROR)
        self.assertIn("COM 读取异常", out.note)
        self.assertIsNone(out.edge_count)
        self.assertEqual(out.kind, gf.OUTLINE_UNKNOWN)
        self.assertEqual(out.edges, ())

    def test_ok_path_fills_endpoints_via_edge_stub(self):
        def stub(sketch_com, raw_i):
            return (float(raw_i), 0.0, float(raw_i), 1.0)

        fn = self._load(stub)
        items = [_FakeGEItem(2), _FakeGEItem(3), _FakeGEItem(1),
                 _FakeGEItem(3), _FakeGEItem(3), _FakeGEItem(3)]  # 点/线/轴/线/线/线
        out = fn(_FakeSketch(_FakeGE(items)))
        self.assertEqual(out.status, gf.STATUS_OK)
        self.assertEqual(out.kind, gf.OUTLINE_RECT)
        self.assertEqual(out.edge_count, 4)
        self.assertEqual([e.index for e in out.edges], [1, 2, 3, 4])
        self.assertEqual([e.raw_collection_index for e in out.edges], [2, 4, 5, 6])
        self.assertEqual(out.edges[0].start, (2.0, 0.0))
        self.assertEqual(out.edges[0].end, (2.0, 1.0))


class _AxisSketchFake:
    def __init__(self):
        self.values = None

    def set_absolute_axis_data(self, values):
        self.values = tuple(values)


class TestBasePlaneSketchAxisWiring(unittest.TestCase):
    """基准面草图必须复现 CATIA 手工定位草图的 H/V 方向。"""

    @staticmethod
    def _load():
        tree = ast.parse(_MODELING_SRC.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        ns = {"_BASE_PLANE_AXIS_DATA": {
            "xy": (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
            "yz": (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            "zx": (0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        }}
        mod = ast.Module(body=[functions["_set_base_plane_sketch_axis"]], type_ignores=[])
        ast.fix_missing_locations(mod)
        exec(compile(mod, "<modeling.py>", "exec"), ns)  # noqa: S102 - 受控 AST
        return ns["_set_base_plane_sketch_axis"]

    def test_zx_matches_manual_positioned_sketch(self):
        sketch = _AxisSketchFake()
        self._load()(sketch, "zx")
        self.assertEqual(sketch.values[3:6], (-1.0, 0.0, 0.0))
        self.assertEqual(sketch.values[6:9], (0.0, 0.0, 1.0))

    def test_all_base_plane_mappings(self):
        expected = {
            "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "yz": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            "zx": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        }
        fn = self._load()
        for plane, (h, v) in expected.items():
            with self.subTest(plane=plane):
                sketch = _AxisSketchFake()
                fn(sketch, plane)
                self.assertEqual(sketch.values[3:6], h)
                self.assertEqual(sketch.values[6:9], v)

    def test_unsupported_plane_is_rejected(self):
        with self.assertRaises(ValueError):
            self._load()(_AxisSketchFake(), "bad")

    def test_base_plane_creators_apply_positioning(self):
        tree = ast.parse(_MODELING_SRC.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in ("add_sketch", "add_sketch_at_height"):
            calls = [node for node in ast.walk(functions[name])
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name)
                     and node.func.id == "_set_base_plane_sketch_axis"]
            self.assertEqual(len(calls), 1, f"{name} 必须应用基准面草图定位")


class _FakeCurve2D:
    """注入的 _PyCurve2D 替身基类：方法默认抛错，子类按路径覆写。"""

    def __init__(self, com_object):
        self.com_object = com_object

    def get_end_points(self):
        raise NotImplementedError

    @property
    def start_point(self):
        raise NotImplementedError

    @property
    def end_point(self):
        raise NotImplementedError


class TestModelingEdgeEndpoints(unittest.TestCase):
    """AST 受控加载 modeling._edge_endpoints：路径 A / B / 双双失败→None 决策。"""

    @staticmethod
    def _load(fake_curve):
        ns = {"_PyCurve2D": fake_curve, "math": math}
        return _exec_func(_extract_func("_edge_endpoints"), ns)

    def test_path_a_get_end_points_direct(self):
        class _A(_FakeCurve2D):
            def get_end_points(self):
                return (1.0, 2.0, 3.0, 4.0)

        fn = self._load(_A)
        got = fn(_FakeSketch(_FakeGE([_FakeGEItem(3)])), 1)
        self.assertEqual(got, (1.0, 2.0, 3.0, 4.0))

    def test_path_b_start_end_fallback(self):
        class _Pt:
            def __init__(self, c):
                self._c = c

            def get_coordinates(self):
                return self._c

        class _B(_FakeCurve2D):
            def get_end_points(self):
                raise RuntimeError("路径 A 失败")

            @property
            def start_point(self):
                return _Pt((5.0, 6.0))

            @property
            def end_point(self):
                return _Pt((7.0, 8.0))

        fn = self._load(_B)
        got = fn(_FakeSketch(_FakeGE([_FakeGEItem(3)])), 1)
        self.assertEqual(got, (5.0, 6.0, 7.0, 8.0))

    def test_both_paths_fail_returns_none(self):
        class _Fail(_FakeCurve2D):
            def get_end_points(self):
                raise RuntimeError("路径 A 失败")

            @property
            def start_point(self):
                raise RuntimeError("路径 B 失败")

        fn = self._load(_Fail)
        self.assertIsNone(fn(_FakeSketch(_FakeGE([_FakeGEItem(3)])), 1))

    def test_malformed_endpoints_do_not_escape(self):
        for value in ((1.0, 2.0), (0, 0, float("nan"), 1)):
            class Bad(_FakeCurve2D):
                def get_end_points(self):
                    return value
            fn = self._load(Bad)
            self.assertIsNone(fn(_FakeSketch(_FakeGE([_FakeGEItem(3)])), 1))


class TestModelingFaceQueryWiring(unittest.TestCase):
    """执行真实查询函数体，COM 边界仅替换为已经采集的几何数据。"""

    def _load(self, outline):
        geo = dict(outline=outline, en_pad="Pad.1", en_pocket="Pocket.1",
                   en_shaft="Shaft.1", en_sk="Sketch.1", h_axis=_H, v_axis=_V,
                   normal=(0, 0, 1), sk_origin=(0, 0, 0),
                   top_origin=(0, 0, 20), bottom_origin=(0, 0, 0))
        ns = {name: getattr(gf, name) for name in dir(gf) if not name.startswith("__")}
        ns.update(logger=logging.getLogger(__name__), _pad_geometry=lambda _: geo,
                  _pocket_geometry=lambda _: geo, _shaft_geometry=lambda _: geo,
                  _brep_face_top=lambda name: f"{name}:top",
                  _brep_face_bottom=lambda name: f"{name}:bottom",
                  _brep_face_side=lambda name, sk, index: f"{name}:{sk}:{index}",
                  make_pad_edge_ref=lambda part, feat, a, b: (a, b),
                  _make_feature_edge_ref=lambda part, feat, a, b: (a, b))
        for name in ("get_pad_faces", "get_pocket_faces", "get_shaft_faces",
                     "get_pad_faces_by_normal", "get_pad_face_edges",
                     "get_pocket_face_edges", "get_shaft_face_edges"):
            _exec_func(_extract_func(name), ns)
        return ns

    def test_rectangle_queries_and_edges(self):
        ns = self._load(_outline(_rect_edges(_CORNERS, raw_base=5)))
        feature = SimpleNamespace(name="test")
        pad = ns["get_pad_faces"](None, feature)
        pocket = ns["get_pocket_faces"](None, feature)
        self.assertEqual((len(pad), len(pocket)), (6, 5))
        for p, q in zip(pad[2:], pocket[1:]):
            self.assertEqual(q["normal"], tuple(-c for c in p["normal"]))
        self.assertEqual(len(ns["get_pad_face_edges"](None, feature, pad[0])), 4)
        self.assertEqual(len(ns["get_pocket_face_edges"](None, feature, pocket[0])), 4)

    def test_circle_filter_never_selects_curved_side(self):
        ns = self._load(gf.sketch_outline_from_element_types([1, 2, 5]))
        feature = SimpleNamespace(name="circle")
        faces = ns["get_pad_faces"](None, feature)
        self.assertEqual(len(faces), 3)
        self.assertEqual(faces[-1]["geometry_type"], "cylindrical")
        self.assertEqual(faces[-1]["face_brep"], "Pad.1:Sketch.1:1")
        self.assertIsNone(faces[-1]["normal"])
        self.assertEqual(len(ns["get_pad_faces_by_normal"](None, feature, (0, 0, 1), 180)), 2)

    def test_shaft_edges_run_without_stale_variable(self):
        ns = self._load(_outline(_rect_edges(_CORNERS)))
        feature = SimpleNamespace(name="shaft")
        faces = ns["get_shaft_faces"](None, feature)
        self.assertTrue(all(f["normal"] is None for f in faces))
        edges = ns["get_shaft_face_edges"](None, feature, faces[0])
        self.assertEqual(edges, [(faces[0]["face_brep"], faces[3]["face_brep"]),
                                 (faces[0]["face_brep"], faces[1]["face_brep"])])

    def test_read_error_stops_all_three_queries(self):
        ns = self._load(gf.read_error_outline("GE failed"))
        for name in ("get_pad_faces", "get_pocket_faces", "get_shaft_faces"):
            with self.subTest(name=name), self.assertRaisesRegex(gf.GeometryQueryError, "GE failed"):
                ns[name](None, SimpleNamespace(name="broken"))


if __name__ == "__main__":
    unittest.main()
