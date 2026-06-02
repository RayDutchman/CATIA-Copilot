"""
M1 B-Rep 探索脚本 v13

修复：
  A. CreateReferenceFromObject 返回的 COM 对象需包装成 pycatia Reference
  B. Groove：先建好两个草图（Shaft + Groove），再依次 add/set_axis/update
  C. 确认 sketches.add(face_ref) + get_absolute_axis_data 面识别
"""

import sys, os, traceback as _tb, math
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v13.txt")
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
log("M1 B-Rep Exploration v13")
log("=" * 65)

import win32com.client
from pycatia.in_interfaces.reference import Reference as PyRef

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

def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def normalize(v):
    mag = math.sqrt(sum(x*x for x in v))
    return tuple(x/mag for x in v) if mag > 1e-9 else v

def fmt_v(v):
    return f"({v[0]:+.3f},{v[1]:+.3f},{v[2]:+.3f})"

def make_z_line(ppy):
    """建 HybridShape Z 轴线，返回 pycatia Reference"""
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


# ============================================================
# Part A：面识别（修复 Reference 包装）
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: 面识别 — sketches.add + get_absolute_axis_data")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepDiag_FaceId_v13")
    part_raw_a = doc_a.Part

    # 建 80×80×30 Pad
    sk_base = ppy_a.main_body.sketches.add(plane_ref(ppy_a, "xy"))
    f2d = sk_base.open_edition()
    f2d.create_line(0,0,80,0); f2d.create_line(80,0,80,80)
    f2d.create_line(80,80,0,80); f2d.create_line(0,80,0,0)
    sk_base.close_edition()
    pad = ppy_a.shape_factory.add_new_pad(sk_base, 30)
    ppy_a.update()
    log(f"  Pad: {pad.name!r}")

    ge = part_raw_a.GeometricElements
    log(f"  GeometricElements.Count: {ge.Count}")

    valid_faces = []

    for i in range(1, ge.Count + 1):
        item_name = "?"
        try:
            item = ge.Item(i)
            try: item_name = item.Name
            except: pass

            # 关键修复：用 PyRef 包装 COM Reference
            ref_com = part_raw_a.CreateReferenceFromObject(item)
            ref_py  = PyRef(ref_com)

            # 建草图
            sk_test = ppy_a.main_body.sketches.add(ref_py)

            # 读取轴系
            ax = sk_test.get_absolute_axis_data()
            origin = ax[0:3]
            h = ax[3:6]; v = ax[6:9]
            normal = normalize(cross(h, v))
            valid_faces.append((i, item_name, ref_py, origin, h, v, normal))
            log(f"  [{i:2d}] {item_name!r}  origin={fmt_v(origin)}  normal={fmt_v(normal)}")

        except Exception as ex:
            log(f"  [{i:2d}] {item_name!r}  -> {str(ex)[:60]}")

    log(f"\n  Valid: {len(valid_faces)}")
    for idx, name, ref, origin, h, v, normal in valid_faces:
        nz = normal[2]
        if abs(nz - 1.0) < 0.01:   label = "TOP"
        elif abs(nz + 1.0) < 0.01: label = "BOTTOM"
        else:                       label = f"SIDE  nx={normal[0]:+.2f} ny={normal[1]:+.2f}"
        log(f"  [{idx:2d}] {name!r}  {label}  origin_Z={origin[2]:.1f}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：顶面定位草图 → 第二个 Pad
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: 顶面定位草图 + 第二个 Pad")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_FaceSketch_v13")
    part_raw_b = doc_b.Part

    sk_base = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "xy"))
    f2d = sk_base.open_edition()
    f2d.create_line(0,0,80,0); f2d.create_line(80,0,80,80)
    f2d.create_line(80,80,0,80); f2d.create_line(0,80,0,0)
    sk_base.close_edition()
    pad_b = ppy_b.shape_factory.add_new_pad(sk_base, 30)
    ppy_b.update()
    log(f"  Base Pad: {pad_b.name!r}")

    ge_b = part_raw_b.GeometricElements
    top_ref = None

    for i in range(1, ge_b.Count + 1):
        try:
            item = ge_b.Item(i)
            ref_py = PyRef(part_raw_b.CreateReferenceFromObject(item))
            sk_t = ppy_b.main_body.sketches.add(ref_py)
            ax = sk_t.get_absolute_axis_data()
            normal = normalize(cross(ax[3:6], ax[6:9]))
            if abs(normal[2] - 1.0) < 0.01:
                origin = ax[0:3]
                log(f"  Top face: [{i}] origin={fmt_v(origin)}")
                top_ref = ref_py
                break
        except:
            pass

    if top_ref is None:
        log("  Top face NOT found")
    else:
        # 建定位草图：在顶面，原点=(40,40,30), H=X, V=Y
        sk_top = ppy_b.main_body.sketches.add(top_ref)
        sk_top.set_absolute_axis_data((
            40.0, 40.0, 30.0,   # origin
            1.0,  0.0,  0.0,    # H = X
            0.0,  1.0,  0.0,    # V = Y
        ))
        log("  定位草图 set_absolute_axis_data: OK")

        f2d_t = sk_top.open_edition()
        f2d_t.create_line(-10,-10, 10,-10)
        f2d_t.create_line( 10,-10, 10, 10)
        f2d_t.create_line( 10, 10,-10, 10)
        f2d_t.create_line(-10, 10,-10,-10)
        sk_top.close_edition()

        try:
            pad_top = ppy_b.shape_factory.add_new_pad(sk_top, 15)
            ppy_b.update()
            shapes = ppy_b.main_body.shapes
            feats = [shapes.item(j+1).name for j in range(shapes.count)]
            log(f"  [OK] 顶面 Pad: {pad_top.name!r}  features: {feats}")
        except Exception:
            log(f"  顶面 Pad FAILED:\n{_tb.format_exc()}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part C：Shaft + Groove（正确顺序）
# ============================================================
log("\n\n" + "=" * 65)
log("Part C: Shaft + Groove 正确顺序")
log("=" * 65)

try:
    doc_c, ppy_c = new_part("BRepDiag_ShaftGroove_v13")

    # Shaft 草图（ZX，R=5~25，H=0~40）
    sk_s = ppy_c.main_body.sketches.add(plane_ref(ppy_c, "zx"))
    f2d = sk_s.open_edition()
    f2d.create_line(5,0,25,0); f2d.create_line(25,0,25,40)
    f2d.create_line(25,40,5,40); f2d.create_line(5,40,5,0)
    sk_s.close_edition()

    shaft = ppy_c.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft.name!r}")

    z_ref_c = make_z_line(ppy_c)
    shaft.revolute_axis = z_ref_c
    ppy_c.update()
    log("  Shaft Update: OK")

    # Groove 草图（ZX，环形槽 R=22~25，H=15~20）
    sk_g = ppy_c.main_body.sketches.add(plane_ref(ppy_c, "zx"))
    f2d = sk_g.open_edition()
    f2d.create_line(22,15,25,15); f2d.create_line(25,15,25,20)
    f2d.create_line(25,20,22,20); f2d.create_line(22,20,22,15)
    sk_g.close_edition()

    try:
        groove = ppy_c.shape_factory.add_new_groove(sk_g)
        log(f"  add_new_groove: {groove.name!r}")
        groove.revolute_axis = z_ref_c
        ppy_c.update()
        log("  Groove Update: OK")
    except Exception as ex:
        log(f"  Groove FAILED: {ex}")
        # 尝试重新建 Z-line（Shaft update 后 ref 可能失效）
        try:
            log("  重建 Z-line...")
            z_ref_c2 = make_z_line(ppy_c)
            groove2 = ppy_c.shape_factory.add_new_groove(sk_g)
            groove2.revolute_axis = z_ref_c2
            ppy_c.update()
            log("  Groove (z2) Update: OK")
        except Exception as ex2:
            log(f"  Groove retry FAILED: {ex2}")

    shapes = ppy_c.main_body.shapes
    feats = [shapes.item(j+1).name for j in range(shapes.count)]
    log(f"  Final features: {feats}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
