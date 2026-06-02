"""
M1 B-Rep 探索脚本 v12

核心策略（放弃 SPA）：
  A. 对 GeometricElements 中的面，用 sketches.add + get_absolute_axis_data
     计算法向 = H×V，识别顶面、底面、侧面
  B. 用 set_absolute_axis_data 定位草图（指定 origin/H/V 方向）
  C. 在顶面建第二个 Pad，验证 face-based sketch 完整流程
  D. 同时验证 Shaft 正确方案（revolute_axis + Z-line）
"""

import sys, os, traceback as _tb, math
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v12.txt")
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
log("M1 B-Rep Exploration v12")
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

def cross(a, b):
    """计算叉积 a×b"""
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )

def fmt_v(v):
    return f"({v[0]:+.3f},{v[1]:+.3f},{v[2]:+.3f})"


# ============================================================
# Part A：面识别 + 定位草图
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: 面识别 via sketches.add + get_absolute_axis_data")
log("=" * 65)

face_info = {}  # 供后续使用

try:
    doc_a, ppy_a = new_part("BRepDiag_FaceId")
    part_raw_a = doc_a.Part

    # 建 80×80×30 的 Pad
    sk_base = ppy_a.main_body.sketches.add(plane_ref(ppy_a, "xy"))
    f2d = sk_base.open_edition()
    f2d.create_line(0,0, 80,0); f2d.create_line(80,0, 80,80)
    f2d.create_line(80,80, 0,80); f2d.create_line(0,80, 0,0)
    sk_base.close_edition()
    pad = ppy_a.shape_factory.add_new_pad(sk_base, 30)
    ppy_a.update()
    log(f"  Pad: {pad.name!r}")

    ge = part_raw_a.GeometricElements
    log(f"  GeometricElements.Count: {ge.Count}")
    log("\n  尝试对每个 GE 项建草图，读取轴系：")

    valid_faces = []  # [(idx, name, ref, origin, H, V, normal)]

    for i in range(1, ge.Count + 1):
        try:
            item = ge.Item(i)
            item_name = "?"
            try: item_name = item.Name
            except: pass

            ref = part_raw_a.CreateReferenceFromObject(item)

            # 尝试建草图
            try:
                sk_test = ppy_a.main_body.sketches.add(ref)
            except Exception as ex:
                log(f"  [{i:2d}] {item_name!r}  sketches.add -> {str(ex)[:50]}")
                continue

            # 读取轴系
            try:
                ax = sk_test.get_absolute_axis_data()
                origin = ax[0:3]
                h_axis = ax[3:6]
                v_axis = ax[6:9]
                normal = cross(h_axis, v_axis)
                # 归一化法向
                mag = math.sqrt(sum(x*x for x in normal))
                if mag > 1e-9:
                    normal = tuple(x/mag for x in normal)
                valid_faces.append((i, item_name, ref, origin, h_axis, v_axis, normal))
                log(f"  [{i:2d}] {item_name!r}  origin={fmt_v(origin)}"
                    f"  H={fmt_v(h_axis)}  V={fmt_v(v_axis)}  normal={fmt_v(normal)}")
            except Exception as ex:
                log(f"  [{i:2d}] {item_name!r}  get_absolute_axis_data -> {ex}")

        except Exception as ex:
            log(f"  [{i:2d}] ERROR: {ex}")

    log(f"\n  Valid face sketches: {len(valid_faces)}")

    # 识别各面（按法向）
    top_ref = bot_ref = None
    for idx, name, ref, origin, h, v, normal in valid_faces:
        nz = normal[2]
        if abs(nz - 1.0) < 0.01:
            label = "TOP (+Z)"
            top_ref = ref
        elif abs(nz + 1.0) < 0.01:
            label = "BOTTOM (-Z)"
            bot_ref = ref
        else:
            label = "SIDE"
        log(f"  [{idx:2d}] {name!r}  -> {label}  origin_Z={origin[2]:.1f}")

    face_info['top'] = top_ref
    face_info['bottom'] = bot_ref

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：在顶面建定位草图 → 第二个 Pad
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: 顶面定位草图 + 第二个 Pad")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_FaceSketch_v12")
    part_raw_b = doc_b.Part

    # 建基础 Pad
    sk_base = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "xy"))
    f2d = sk_base.open_edition()
    f2d.create_line(0,0, 80,0); f2d.create_line(80,0, 80,80)
    f2d.create_line(80,80, 0,80); f2d.create_line(0,80, 0,0)
    sk_base.close_edition()
    pad_b = ppy_b.shape_factory.add_new_pad(sk_base, 30)
    ppy_b.update()
    log(f"  Base Pad: {pad_b.name!r}")

    # 找顶面
    ge_b = part_raw_b.GeometricElements
    top_ref_b = None
    for i in range(1, ge_b.Count + 1):
        try:
            item = ge_b.Item(i)
            ref = part_raw_b.CreateReferenceFromObject(item)
            sk_t = ppy_b.main_body.sketches.add(ref)
            ax = sk_t.get_absolute_axis_data()
            h = ax[3:6]; v = ax[6:9]
            normal = cross(h, v)
            mag = math.sqrt(sum(x*x for x in normal))
            if mag > 1e-9:
                normal = tuple(x/mag for x in normal)
            if abs(normal[2] - 1.0) < 0.01:
                origin = ax[0:3]
                log(f"  Top face found: [{i}] origin_Z={origin[2]:.1f}  normal={fmt_v(normal)}")
                top_ref_b = ref
                # 在顶面建定位草图（指定原点=面中心(40,40,30), H=X, V=Y）
                sk_t.set_absolute_axis_data((
                    40.0, 40.0, 30.0,   # origin = 面中心
                    1.0,  0.0,  0.0,    # H = X 方向
                    0.0,  1.0,  0.0,    # V = Y 方向
                ))
                log("  set_absolute_axis_data(origin=face_center, H=X, V=Y): OK")
                break
        except:
            pass

    if top_ref_b is None:
        log("  Top face NOT found")
    else:
        # 在顶面草图上画 20×20 矩形，中心坐标系原点在面中心(40,40,30)
        try:
            sk_top = ppy_b.main_body.sketches.add(top_ref_b)
            # 定位草图：origin=(40,40,30), H=X, V=Y
            sk_top.set_absolute_axis_data((
                40.0, 40.0, 30.0,
                1.0, 0.0, 0.0,
                0.0, 1.0, 0.0,
            ))
            f2d_t = sk_top.open_edition()
            # 画 20×20 矩形（草图局部坐标，原点=面中心）
            f2d_t.create_line(-10,-10, 10,-10)
            f2d_t.create_line( 10,-10, 10, 10)
            f2d_t.create_line( 10, 10,-10, 10)
            f2d_t.create_line(-10, 10,-10,-10)
            sk_top.close_edition()
            pad_top = ppy_b.shape_factory.add_new_pad(sk_top, 15)
            ppy_b.update()
            shapes = ppy_b.main_body.shapes
            feat_list = [shapes.item(i+1).name for i in range(shapes.count)]
            log(f"  [OK] face pad: {pad_top.name!r}  features: {feat_list}")
        except Exception:
            log(f"  Face pad FAILED:\n{_tb.format_exc()}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part C：Shaft + Groove 完整验证
# ============================================================
log("\n\n" + "=" * 65)
log("Part C: Shaft + Groove 完整验证")
log("=" * 65)

try:
    doc_c, ppy_c = new_part("BRepDiag_Shaft_v12")

    # ZX 平面草图（旋转体轮廓）
    sk_s = ppy_c.main_body.sketches.add(plane_ref(ppy_c, "zx"))
    f2d = sk_s.open_edition()
    f2d.create_line(5,  0,  25, 0)
    f2d.create_line(25, 0,  25, 40)
    f2d.create_line(25, 40, 5,  40)
    f2d.create_line(5,  40, 5,  0)
    sk_s.close_edition()

    shaft = ppy_c.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft.name!r}")

    # 建 Z 轴线
    hsf   = ppy_c.hybrid_shape_factory
    hbody = ppy_c.hybrid_bodies.add()
    pt0   = hsf.add_new_point_coord(0.0, 0.0,   0.0)
    pt1   = hsf.add_new_point_coord(0.0, 0.0, 100.0)
    hbody.append_hybrid_shape(pt0); hbody.append_hybrid_shape(pt1)
    ppy_c.update_object(pt0); ppy_c.update_object(pt1)
    z_line = hsf.add_new_line_pt_pt(
        ppy_c.create_reference_from_object(pt0),
        ppy_c.create_reference_from_object(pt1))
    hbody.append_hybrid_shape(z_line)
    ppy_c.update_object(z_line)
    z_ref = ppy_c.create_reference_from_object(z_line)

    # 设置旋转轴（正确属性名：revolute_axis）
    shaft.revolute_axis = z_ref
    log("  shaft.revolute_axis = z_ref: OK")

    ppy_c.update()
    log("  Shaft Update: OK")

    # Groove（环形槽）
    sk_g = ppy_c.main_body.sketches.add(plane_ref(ppy_c, "zx"))
    f2d = sk_g.open_edition()
    f2d.create_line(22, 15, 25, 15)
    f2d.create_line(25, 15, 25, 20)
    f2d.create_line(25, 20, 22, 20)
    f2d.create_line(22, 20, 22, 15)
    sk_g.close_edition()

    groove = ppy_c.shape_factory.add_new_groove(sk_g)
    log(f"  add_new_groove: {groove.name!r}")
    groove.revolute_axis = z_ref
    log("  groove.revolute_axis = z_ref: OK")

    ppy_c.update()
    log("  Groove Update: OK")

    shapes = ppy_c.main_body.shapes
    feat_list = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"  Final features: {feat_list}")

    # 验证 Shaft 面识别
    log("\n  Shaft 面识别（法向）：")
    ge_c = doc_c.Part.GeometricElements
    for i in range(1, ge_c.Count + 1):
        try:
            item = ge_c.Item(i)
            item_name = "?"
            try: item_name = item.Name
            except: pass
            ref = doc_c.Part.CreateReferenceFromObject(item)
            sk_t = ppy_c.main_body.sketches.add(ref)
            ax = sk_t.get_absolute_axis_data()
            h = ax[3:6]; v = ax[6:9]
            normal = cross(h, v)
            mag = math.sqrt(sum(x*x for x in normal))
            if mag > 1e-9:
                normal = tuple(x/mag for x in normal)
            origin = ax[0:3]
            log(f"    [{i:2d}] {item_name!r}  origin={fmt_v(origin)}  normal={fmt_v(normal)}")
        except:
            pass

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
