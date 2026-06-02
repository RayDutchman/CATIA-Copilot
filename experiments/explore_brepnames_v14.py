"""
M1 B-Rep 探索脚本 v14

关键策略调整：
  A. 顶面建草图 = sketches.add(xy_plane) + set_absolute_axis_data(Z=height)
     不需要 B-Rep 面 Reference
  B. Groove：单独测试（不含 Shaft），再测试 Shaft + Groove 组合
"""

import sys, os, traceback as _tb, math
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v14.txt")
_lines = []
def log(msg=""):
    s = str(msg)
    try: print(s)
    except Exception: print(repr(s))
    _lines.append(s)
def save():
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(f"\n>>> saved: {OUTPUT}")

log("=" * 65)
log("M1 B-Rep Exploration v14")
log("=" * 65)

import win32com.client
try:
    catia = win32com.client.GetActiveObject("CATIA.Application")
    log("[OK] CATIA connected")
except Exception as e:
    log(f"[FAIL] {e}"); save(); sys.exit(1)

try:
    from catia_copilot.catia.connection import wrap_application
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    app_py = wrap_application()
    log("[OK] pycatia ready")
except Exception as e:
    log(f"[FAIL] pycatia: {e}"); save(); sys.exit(1)


def new_part(name):
    app_py.documents.add("Part")
    doc = catia.ActiveDocument
    ppy = PartDocument(doc).part
    try: ppy.part.PartNumber = name
    except Exception: pass
    log(f"\n  new part: {name}")
    return doc, ppy

def plane_ref(ppy, plane):
    o = ppy.origin_elements
    return ppy.create_reference_from_object(
        {"xy": o.plane_xy, "yz": o.plane_yz, "zx": o.plane_zx}[plane])

def make_z_line(ppy):
    hsf   = ppy.hybrid_shape_factory
    hbody = ppy.hybrid_bodies.add()
    pt0   = hsf.add_new_point_coord(0.0, 0.0,   0.0)
    pt1   = hsf.add_new_point_coord(0.0, 0.0, 100.0)
    hbody.append_hybrid_shape(pt0); hbody.append_hybrid_shape(pt1)
    ppy.update_object(pt0); ppy.update_object(pt1)
    z_line = hsf.add_new_line_pt_pt(
        ppy.create_reference_from_object(pt0),
        ppy.create_reference_from_object(pt1))
    hbody.append_hybrid_shape(z_line)
    ppy.update_object(z_line)
    return ppy.create_reference_from_object(z_line)

def add_rect_sketch(ppy, plane, x0, y0, x1, y1):
    """在指定平面上建矩形草图，返回 sketch"""
    sk = ppy.main_body.sketches.add(plane_ref(ppy, plane))
    f2d = sk.open_edition()
    f2d.create_line(x0,y0,x1,y0); f2d.create_line(x1,y0,x1,y1)
    f2d.create_line(x1,y1,x0,y1); f2d.create_line(x0,y1,x0,y0)
    sk.close_edition()
    return sk


# ============================================================
# Part A：定位草图在指定 Z 高度建 Pad
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: set_absolute_axis_data 定位草图在 Z=30 建 Pad")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepDiag_PositionedSketch")

    # 建 80×80×30 基础 Pad
    sk1 = add_rect_sketch(ppy_a, "xy", 0, 0, 80, 80)
    pad1 = ppy_a.shape_factory.add_new_pad(sk1, 30)
    ppy_a.update()
    log(f"  Base Pad: {pad1.name!r}")

    # 在 Z=30 处建定位草图（支撑面 = XY 平面，但原点移到 Z=30）
    sk2 = ppy_a.main_body.sketches.add(plane_ref(ppy_a, "xy"))
    sk2.set_absolute_axis_data((
        40.0, 40.0, 30.0,   # origin = Pad 顶面中心
        1.0, 0.0, 0.0,      # H = X
        0.0, 1.0, 0.0,      # V = Y
    ))
    log("  定位草图 set_absolute_axis_data OK")

    # 检查草图坐标系
    ax = sk2.get_absolute_axis_data()
    log(f"  验证: origin={list(ax[0:3])}  H={list(ax[3:6])}  V={list(ax[6:9])}")

    f2d2 = sk2.open_edition()
    f2d2.create_line(-10,-10, 10,-10)
    f2d2.create_line( 10,-10, 10, 10)
    f2d2.create_line( 10, 10,-10, 10)
    f2d2.create_line(-10, 10,-10,-10)
    sk2.close_edition()

    pad2 = ppy_a.shape_factory.add_new_pad(sk2, 15)
    ppy_a.update()
    shapes = ppy_a.main_body.shapes
    feats = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"  [OK] 顶面 Pad: {pad2.name!r}  features: {feats}")
    log(f"  Total features: {shapes.count}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：Groove 单独测试（无 Shaft）
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: Groove 单独测试（无 Shaft）")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_GrooveOnly")

    # 先建一个 Pad 作为基础实体（Groove 需要有实体才能切）
    # 80×80×40 的 Pad
    sk_b = add_rect_sketch(ppy_b, "xy", -30, -30, 30, 30)
    pad_b = ppy_b.shape_factory.add_new_pad(sk_b, 40)
    ppy_b.update()
    log(f"  Base Pad: {pad_b.name!r}")

    # Groove 草图（ZX 平面，环形轮廓 R=22~25，Z=15~20）
    sk_g = add_rect_sketch(ppy_b, "zx", 22, 15, 25, 20)

    z_ref_b = make_z_line(ppy_b)
    try:
        groove = ppy_b.shape_factory.add_new_groove(sk_g)
        log(f"  add_new_groove: {groove.name!r}")
        groove.revolute_axis = z_ref_b
        ppy_b.update()
        log("  Groove Update: OK")
    except Exception as ex:
        log(f"  Groove FAILED: {ex}")

    shapes = ppy_b.main_body.shapes
    feats = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"  Final features: {feats}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part C：Shaft + Groove 完整测试
# ============================================================
log("\n\n" + "=" * 65)
log("Part C: Shaft + Groove 完整（分离 Z-line）")
log("=" * 65)

try:
    doc_c, ppy_c = new_part("BRepDiag_ShaftGroove_v14")

    # Shaft
    sk_s = add_rect_sketch(ppy_c, "zx", 5, 0, 25, 40)
    shaft = ppy_c.shape_factory.add_new_shaft(sk_s)
    z_ref1 = make_z_line(ppy_c)
    shaft.revolute_axis = z_ref1
    ppy_c.update()
    log(f"  Shaft Update: OK  ({shaft.name!r})")

    # Groove：新建 Z-line（Shaft update 后，重新建参考轴）
    sk_g = add_rect_sketch(ppy_c, "zx", 22, 15, 25, 20)
    z_ref2 = make_z_line(ppy_c)

    try:
        groove = ppy_c.shape_factory.add_new_groove(sk_g)
        log(f"  add_new_groove: {groove.name!r}")
        groove.revolute_axis = z_ref2
        ppy_c.update()
        log("  Groove Update: OK")
    except Exception as ex:
        log(f"  Groove FAILED: {ex}")

    shapes = ppy_c.main_body.shapes
    feats = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"  Final features: {feats}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
