# -*- coding: utf-8 -*-
"""S2 几何查询 CATIA 手动基准。

只在用户已经启动 CATIA、且确认允许新建未保存试验零件时运行。
脚本不会保存、关闭或删除任何 CATIA 文档。
此文件不是 CI 测试；CI 不会导入本目录。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

# 直接以文件路径运行时，Python 默认只把本目录加入 sys.path。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.modeling import ModelingContext, _edge_endpoints

_RUN_TAG = ""


def _part_name(base: str) -> str:
    return f"{base}_{_RUN_TAG}" if _RUN_TAG else base


def _face_summary(face: dict) -> dict:
    """只保留可序列化的几何查询字段。"""
    return {
        key: face.get(key)
        for key in (
            "type", "source", "geometry_type", "normal", "normal_unresolved",
            "origin", "face_brep", "edge_index", "edge_count", "error",
        )
    }


def _write_case(out_dir: Path, case_name: str, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{case_name}] JSON: {path}")


def _require(condition, message: str) -> None:
    """验收失败必须返回非零退出码，不能只看脚本是否抛 COM 异常。"""
    if not condition:
        raise AssertionError(message)


def _near_vector(actual, expected) -> bool:
    return actual is not None and len(actual) == len(expected) and all(
        math.isclose(a, b, abs_tol=1e-6) for a, b in zip(actual, expected)
    )


def _validate_faces(name: str, faces: list[dict]) -> None:
    expected_counts = {"B1": 6, "B2": 3, "B3": 6, "B4": 5, "B5": 4, "B6": 4}
    case = name[:2]
    _require(len(faces) == expected_counts[case], f"候选面数错误: {len(faces)}")
    _require(all(f["source"] == "feature_inference" for f in faces), "来源字段缺失")
    if case in ("B5", "B6"):
        _require(all(f["geometry_type"] == "unknown" and f["normal"] is None
                     and f["origin"] is None for f in faces), "旋转面伪造几何属性")
        return
    sides = [f for f in faces if f["type"] == "side"]
    if case in ("B1", "B4"):
        normals = [(0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)]
        if case == "B4":
            normals = [tuple(-v for v in n) for n in normals]
        _require(all(f["geometry_type"] == "planar" and _near_vector(f["normal"], n)
                     for f, n in zip(sides, normals)), "矩形侧面法向未读取或方向错误")
    else:
        kinds = [f["geometry_type"] for f in sides]
        _require(kinds.count("cylindrical") == (1 if case == "B2" else 2), "圆弧分类错误")
        _require(kinds.count("planar") == (0 if case == "B2" else 2), "直线分类错误")
        _require(all(f["normal"] is None for f in sides), "曲面/混合轮廓伪造固定法向")


def _run_case(name: str, builder, out_dir: Path) -> str:
    ctx = ModelingContext()
    payload = {"case": name, "status": "FAIL"}
    try:
        part, feature, faces = builder(ctx)
        opening_pad = None
        if isinstance(feature, tuple):
            feature, opening_pad = feature
        payload.update(features_before=ctx.list_features(part),
                       faces=[_face_summary(face) for face in faces])
        sketch = feature.sketch
        ge = sketch.com_object.GeometricElements
        payload["sketch_axes"] = sketch.get_absolute_axis_data()
        payload["elements"] = [
            dict(raw_index=i, name=ge.Item(i).Name, geometric_type=ge.Item(i).GeometricType,
                 endpoints=_edge_endpoints(sketch.com_object, i)
                 if ge.Item(i).GeometricType == 3 else None)
            for i in range(1, ge.Count + 1)
        ]
        _validate_faces(name, faces)
        analyze = get_catia_v5_application().ActiveDocument.Product.Analyze
        before = float(analyze.Volume)
        payload["volume_before"] = before
        _require(math.isfinite(before) and before > 0, "实体体积无效")
        # 质量/重心读取独立于候选面推导，用于检查旋转轴实际所在方向。
        if name.startswith(("B5", "B6")):
            mass = ctx.get_mass_props(part)
            payload["mass_props"] = mass
            if name.startswith("B6"):
                _require(mass is not None and _near_vector(mass["cog"], (0, 40, 0)),
                         "Y 轴旋转体的实际重心未沿 Y 轴")
            else:
                payload["axis_observation"] = (
                    "ZX 草图 H=-X、V=+Z 的轴向语义以用户实机复核为准；"
                    "此处仅记录 CATIA 返回的质量重心，不用猜测值判定失败。"
                )
        edges = []
        radius = 3.0
        if name.startswith(("B1", "B2")):
            top = ctx.get_pad_faces_by_normal(part, feature, (0, 0, 1))[0]
            edges = ctx.get_pad_face_edges(part, feature, top)
        elif name.startswith("B4"):
            edges = ctx.get_pocket_opening_edges(part, feature, opening_pad)
            radius = 2.0
        elif name.startswith("B5"):
            edges = ctx.get_shaft_face_edges(part, feature, faces[0])
            radius = 1.0
        if edges:
            payload["fillet_edge_count"] = len(edges)
            ctx.add_fillet_edges(part, edges, radius)
            ctx.update_part(part)
            after = float(analyze.Volume)
            payload["volume_after_fillet"] = after
            _require(after > 0 and not math.isclose(before, after, rel_tol=1e-8),
                     "圆角更新后体积没有变化")
        payload.update(status="PASS", features_after=ctx.list_features(part))
        _write_case(out_dir, name, payload)
        print(f"[{name}] PASS: faces={len(faces)}")
        return "PASS"
    except Exception as exc:
        payload.update(error=str(exc), traceback=traceback.format_exc(), steps=ctx.steps)
        _write_case(out_dir, name, payload)
        print(f"[{name}] FAIL: {exc}")
        return "FAIL"


def _rect_pad(ctx: ModelingContext, name: str):
    part = ctx.create_part(_part_name(name))
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sketch, 0, 0, 100.0, 60.0)
    pad = ctx.add_pad(part, sketch, 20.0)
    ctx.update_part(part)
    faces = ctx.get_pad_faces(part, pad)
    return part, pad, faces


def _circle_pad(ctx: ModelingContext, name: str):
    part = ctx.create_part(_part_name(name))
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_circle(sketch, 50.0, 30.0, 20.0)
    pad = ctx.add_pad(part, sketch, 20.0)
    ctx.update_part(part)
    return part, pad, ctx.get_pad_faces(part, pad)


def _slot_pad(ctx: ModelingContext, name: str):
    part = ctx.create_part(_part_name(name))
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_slot(sketch, 25.0, 30.0, 75.0, 30.0, 15.0)
    pad = ctx.add_pad(part, sketch, 20.0)
    ctx.update_part(part)
    return part, pad, ctx.get_pad_faces(part, pad)


def _pocket(ctx: ModelingContext, name: str):
    part = ctx.create_part(_part_name(name))
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sketch, 0, 0, 100.0, 60.0)
    pad = ctx.add_pad(part, sketch, 20.0)
    ctx.update_part(part)
    cut_sketch = ctx.add_sketch_on_pad_top(part, pad)
    ctx.draw_rect(cut_sketch, 20.0, 10.0, 60.0, 40.0)
    pocket = ctx.add_pocket(part, cut_sketch, 10.0)
    ctx.update_part(part)
    faces = ctx.get_pocket_faces(part, pocket)
    return part, (pocket, pad), faces


def _shaft(ctx: ModelingContext, name: str, axis: str = "z"):
    part = ctx.create_part(_part_name(name))
    ctx.prepare_revolute_axis(part, axis)
    plane = {"x": "xy", "y": "xy", "z": "zx"}[axis]
    sketch = ctx.add_sketch(part, plane)
    ctx.draw_rect(sketch, 25.0, 0, 25.0, 80.0)
    shaft = ctx.add_shaft(part, sketch, axis=axis)
    ctx.update_part(part)
    return part, shaft, ctx.get_shaft_faces(part, shaft)


def main(argv: list[str] | None = None) -> int:
    global _RUN_TAG
    parser = argparse.ArgumentParser(description="S2 CATIA 几何查询手动基准")
    parser.add_argument("--b6", action="store_true", help="只构造 axis=y 样本，供人工核对轴向")
    parser.add_argument("--fail-check", action="store_true", help="无 CATIA 时验证失败退出码")
    parser.add_argument("--case", choices=["B1", "B2", "B3", "B4", "B5", "B6"],
                        help="只运行一个样本，避免失败时重建所有零件")
    parser.add_argument(
        "--outdir",
        default=str(Path(tempfile.gettempdir()) / "catia_s2_smoke"),
        help="JSON 输出目录（默认在 TEMP 下）",
    )
    args = parser.parse_args(argv)
    _RUN_TAG = datetime.now().strftime("%H%M%S%f")

    if args.fail_check:
        print("FAIL: intentional failure check")
        return 1

    try:
        get_catia_v5_application()
    except Exception as exc:
        print(f"BLOCKED: CATIA 未连接，未创建任何文档：{exc}")
        return 2

    out_dir = Path(args.outdir) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    cases = (
        [("B6_axis_y", lambda ctx: _shaft(ctx, "S2_B6_AxisY", "y"))]
        if args.b6 or args.case == "B6"
        else [
            ("B1_rect_pad", lambda ctx: _rect_pad(ctx, "S2_B1_RectPad")),
            ("B2_circle_pad", lambda ctx: _circle_pad(ctx, "S2_B2_CirclePad")),
            ("B3_slot_pad", lambda ctx: _slot_pad(ctx, "S2_B3_SlotPad")),
            ("B4_pocket", lambda ctx: _pocket(ctx, "S2_B4_Pocket")),
            ("B5_shaft", lambda ctx: _shaft(ctx, "S2_B5_Shaft")),
        ]
    )
    if args.case:
        cases = [(name, builder) for name, builder in cases if name.startswith(args.case)]
    results = [_run_case(name, builder, out_dir) for name, builder in cases]
    print(f"SUMMARY: PASS={results.count('PASS')} FAIL={results.count('FAIL')} BLOCKED=0")
    print("提示：试验零件未保存、未关闭、未删除，请按需在 CATIA 中手动处理。")
    return 1 if "FAIL" in results else 0


if __name__ == "__main__":
    sys.exit(main())
