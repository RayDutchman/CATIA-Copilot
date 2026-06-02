"""
M1 B-Rep 探索脚本 v11

修复策略：
  A. SPA：CreateReferenceFromObject(face_item) 后再传 GetMeasurable
  B. Shaft：先 add_new_shaft（不 update），再建 Z-line，设 RevoluteAxis，再 update
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v11.txt")
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
log("M1 B-Rep Exploration v11")
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


# ============================================================
# Part A：SPA via GeometricElements + CreateReferenceFromObject
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: SPA via GeometricElements + CreateReferenceFromObject")
log("=" * 65)

top_face_ref = None  # 保存顶面 ref 用于 Part C

try:
    doc_a, ppy_a = new_part("BRepDiag_SPA_v11")
    part_raw_a = doc_a.Part

    # 建 80x80x30 Pad
    sk = ppy_a.main_body.sketches.add(plane_ref(ppy_a, "xy"))
    f2d = sk.open_edition()
    f2d.create_line(0,0, 80,0); f2d.create_line(80,0, 80,80)
    f2d.create_line(80,80, 0,80); f2d.create_line(0,80, 0,0)
    sk.close_edition()
    pad = ppy_a.shape_factory.add_new_pad(sk, 30)
    ppy_a.update()
    feat_name = pad.name
    log(f"  Pad: {feat_name!r}")

    spa = doc_a.GetWorkbench("SPAWorkbench")

    # 枚举 GeometricElements，用 CreateReferenceFromObject 获取 Reference
    ge = part_raw_a.GeometricElements
    log(f"  GeometricElements.Count: {ge.Count}")

    faces_found = []  # (name, ref, area)
    for i in range(1, ge.Count + 1):
        try:
            item = ge.Item(i)
            item_name = "?"
            try: item_name = item.Name
            except: pass

            # 关键：用 CreateReferenceFromObject 转换为 Reference
            ref = None
            try:
                ref = part_raw_a.CreateReferenceFromObject(item)
            except Exception as ex:
                log(f"  [{i:2d}] CreateReferenceFromObject failed: {ex}")
                continue

            area_str = normal_str = ""
            area_val = None
            try:
                meas = spa.GetMeasurable(ref)
                area_val = meas.Area
                area_str = f"  Area={area_val:.2f}"
            except Exception as ex:
                area_str = f"  Area_err={str(ex)[:40]}"

            # 尝试法向
            if area_val is not None:
                try:
                    import array
                    arr = array.array('d', [0.0]*3)
                    meas.GetNormal(arr)
                    normal_str = f"  normal=({arr[0]:+.3f},{arr[1]:+.3f},{arr[2]:+.3f})"
                except Exception as ex:
                    try:
                        result = meas.GetNormal()
                        normal_str = f"  normal={result}"
                    except Exception as ex2:
                        normal_str = f"  normal_err={str(ex2)[:40]}"

                faces_found.append((item_name, ref, area_val))

            log(f"  [{i:2d}] {item_name!r}{area_str}{normal_str}")
        except Exception as ex:
            log(f"  [{i:2d}] ERROR: {ex}")

    log(f"\n  Valid faces found: {len(faces_found)}")

    # 找顶面（面积最大的，或者 normal 接近 +Z）
    if faces_found:
        # 按面积降序找顶面（80x80=6400mm2）
        faces_found.sort(key=lambda x: x[2], reverse=True)
        log(f"\n  Sorted by area:")
        for nm, ref, area in faces_found:
            log(f"    {nm!r}  area={area:.2f}")

        # 顶面应该是 80x80 = 6400 mm2
        for nm, ref, area in faces_found:
            if abs(area - 6400) < 500:
                log(f"\n  Top face candidate: {nm!r}  area={area:.2f}")
                top_face_ref = ref
                break
        if top_face_ref is None:
            top_face_ref = faces_found[0][1]
            log(f"\n  Using largest face as top: {faces_found[0][0]!r}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：Shaft 正确顺序
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: Shaft 正确顺序（add先，Z-line后，再 update）")
log("=" * 65)

shaft_ok = False
try:
    doc_b, ppy_b = new_part("BRepDiag_Shaft_v11")

    # 步骤1：建草图（ZX 平面，H=Z, V=X, 全在 V>=0 侧）
    sk_s = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "zx"))
    f2d  = sk_s.open_edition()
    f2d.create_line(5,  0,  25, 0)    # 底边（在 V=0 轴上）
    f2d.create_line(25, 0,  25, 40)   # 右边
    f2d.create_line(25, 40, 5,  40)   # 顶边
    f2d.create_line(5,  40, 5,  0)    # 左边
    sk_s.close_edition()
    log("  Sketch (ZX): OK")

    # 步骤2：add_new_shaft（不 update）
    shaft = ppy_b.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft.name!r}")

    # 步骤3：建 Z-line HybridShape（只 update_object，不 update Part）
    try:
        hsf   = ppy_b.hybrid_shape_factory
        hbody = ppy_b.hybrid_bodies.add()
        pt0   = hsf.add_new_point_coord(0.0, 0.0,   0.0)
        pt1   = hsf.add_new_point_coord(0.0, 0.0, 100.0)
        hbody.append_hybrid_shape(pt0)
        hbody.append_hybrid_shape(pt1)
        ppy_b.update_object(pt0)
        ppy_b.update_object(pt1)
        z_line = hsf.add_new_line_pt_pt(
            ppy_b.create_reference_from_object(pt0),
            ppy_b.create_reference_from_object(pt1))
        hbody.append_hybrid_shape(z_line)
        ppy_b.update_object(z_line)
        z_ref = ppy_b.create_reference_from_object(z_line)
        log("  Z-line HybridShape: OK")
    except Exception as ex:
        log(f"  Z-line: FAILED -> {ex}")
        z_ref = None

    # 步骤4：用 COM 直接设置 RevoluteAxis
    if z_ref:
        try:
            shaft.com_object.RevoluteAxis = z_ref.com_object
            log("  shaft.com_object.RevoluteAxis = z_ref: OK")
        except Exception as ex:
            log(f"  RevoluteAxis COM: FAILED -> {ex}")

    # 步骤5：Part Update
    try:
        ppy_b.update()
        log("  Part Update: OK")
        shaft_ok = True
    except Exception as ex:
        log(f"  Part Update: FAILED -> {ex}")
        # 尝试 update_object(shaft)
        try:
            ppy_b.update_object(shaft)
            log("  update_object(shaft): OK")
            shaft_ok = True
        except Exception as ex2:
            log(f"  update_object(shaft): FAILED -> {ex2}")

    # 草图轴系信息
    log("\n  草图轴系 (ZX plane):")
    try:
        import array
        ax_data = array.array('d', [0.0]*12)
        sk_s.com_object.GetAbsoluteAxisData(ax_data)
        log(f"  origin={list(ax_data[0:3])}")
        log(f"  H-axis={list(ax_data[3:6])}")
        log(f"  V-axis={list(ax_data[6:9])}")
    except Exception as ex:
        log(f"  GetAbsoluteAxisData: {ex}")

    shapes = ppy_b.main_body.shapes
    feat_list = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"\n  Final features: {feat_list}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part C：在 SPA 确认的顶面上建草图
# ============================================================
log("\n\n" + "=" * 65)
log("Part C: 在顶面上建草图 + Pad")
log("=" * 65)

try:
    doc_c, ppy_c = new_part("BRepDiag_FaceSketch_v11")
    part_raw_c = doc_c.Part

    # 建基础 Pad
    sk_b = ppy_c.main_body.sketches.add(plane_ref(ppy_c, "xy"))
    f2d  = sk_b.open_edition()
    f2d.create_line(0,0, 80,0); f2d.create_line(80,0, 80,80)
    f2d.create_line(80,80, 0,80); f2d.create_line(0,80, 0,0)
    sk_b.close_edition()
    pad_b = ppy_c.shape_factory.add_new_pad(sk_b, 30)
    ppy_c.update()
    feat_name_c = pad_b.name
    log(f"  Base Pad: {feat_name_c!r}")

    spa_c = doc_c.GetWorkbench("SPAWorkbench")
    ge_c  = part_raw_c.GeometricElements

    # 找顶面（面积 ≈ 6400）
    top_ref_c = None
    for i in range(1, ge_c.Count + 1):
        try:
            item = ge_c.Item(i)
            ref  = part_raw_c.CreateReferenceFromObject(item)
            area = spa_c.GetMeasurable(ref).Area
            if abs(area - 6400) < 500:
                top_ref_c = ref
                log(f"  Top face: [{i}] area={area:.1f}")
                break
        except:
            pass

    if top_ref_c is None:
        log("  Top face NOT found via SPA")
    else:
        # 在顶面上建草图
        try:
            sk_top = ppy_c.main_body.sketches.add(top_ref_c)
            f2d_t  = sk_top.open_edition()
            f2d_t.create_line(30,30, 50,30); f2d_t.create_line(50,30, 50,50)
            f2d_t.create_line(50,50, 30,50); f2d_t.create_line(30,50, 30,30)
            sk_top.close_edition()
            pad_top = ppy_c.shape_factory.add_new_pad(sk_top, 15)
            ppy_c.update()
            shapes_c = ppy_c.main_body.shapes
            feat_list_c = [shapes_c.item(i+1).name for i in range(shapes_c.count)]
            log(f"  [OK] face-based pad: {pad_top.name!r}  features: {feat_list_c}")
        except Exception:
            log(f"  Face sketch FAILED:\n{_tb.format_exc()}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
