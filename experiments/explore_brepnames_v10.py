"""
M1 B-Rep 探索脚本 v10

修复两个核心问题：
  A. SPA：通过 Part.GeometricElements 直接访问面的 Reference
  B. Shaft：直接用 COM 属性 RevoluteAxis（不经过 pycatia setter）
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v10.txt")
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
log("M1 B-Rep Exploration v10")
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
# Part A：通过 GeometricElements 访问面
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: GeometricElements 访问面 + SPA")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepDiag_GE")
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

    # 枚举 GeometricElements
    ge = part_raw_a.GeometricElements
    log(f"  GeometricElements.Count: {ge.Count}")
    log("\n  --- 逐项检查 ---")
    for i in range(1, ge.Count + 1):
        try:
            item = ge.Item(i)
            item_type = type(item).__name__
            item_name = "?"
            try: item_name = item.Name
            except: pass
            # 尝试 SPA
            area_str = ""
            try:
                area = spa.GetMeasurable(item).Area
                area_str = f"  Area={area:.2f}"
            except Exception as ex:
                area_str = f"  Area_err={str(ex)[:50]}"
            # 尝试获取 DisplayName / Reference
            dn_str = ""
            try:
                ref = part_raw_a.CreateReferenceFromObject(item)
                dn = ref.DisplayName
                dn_str = f"\n        dn={dn!r}"
            except Exception as ex:
                try:
                    dn = item.DisplayName
                    dn_str = f"\n        dn={dn!r}"
                except:
                    dn_str = ""
            log(f"  [{i:2d}] type={item_type} name={item_name!r}{area_str}{dn_str}")
        except Exception as ex:
            log(f"  [{i:2d}] ERROR: {ex}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：Shaft 用 COM RevoluteAxis 直接赋值
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: Shaft + COM RevoluteAxis 直接赋值")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_Shaft_v10")

    # 建 HybridShape Z 轴线（在建草图前）
    log("  建 Z 轴 HybridShape line...")
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
    log("  Z-line: OK")

    # ZX 平面草图，H(Z)=5~25, V(X)=0~40
    sk_s = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "zx"))
    f2d  = sk_s.open_edition()
    f2d.create_line(5,  0,  25, 0)
    f2d.create_line(25, 0,  25, 40)
    f2d.create_line(25, 40, 5,  40)
    f2d.create_line(5,  40, 5,  0)
    sk_s.close_edition()

    shaft = ppy_b.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft.name!r}")

    # 直接用 COM RevoluteAxis（不通过 pycatia setter）
    try:
        shaft.com_object.RevoluteAxis = z_ref.com_object
        log("  shaft.com_object.RevoluteAxis = z_ref: OK")
    except Exception as ex:
        log(f"  RevoluteAxis COM set: FAILED -> {ex}")

    # pycatia setter 也试一下
    try:
        shaft.revolution_axis = z_ref
        log("  shaft.revolution_axis = z_ref (pycatia): OK")
    except Exception as ex:
        log(f"  revolution_axis (pycatia): {ex}")

    # Update
    try:
        ppy_b.update()
        log("  Part Update: OK")
    except Exception as ex:
        log(f"  Part Update: FAILED -> {ex}")

    # 检查 shaft.com_object 其他与轴相关的属性值
    try:
        rev_ax = shaft.com_object.RevoluteAxis
        log(f"  After set, RevoluteAxis = {rev_ax}")
    except Exception as ex:
        log(f"  Read RevoluteAxis: {ex}")

    # 检查草图的 AbsoluteAxis
    log("\n  草图 AbsoluteAxis 检查:")
    try:
        ax_data = [0.0]*18
        sk_s.com_object.GetAbsoluteAxisData(ax_data)
        log(f"  origin={ax_data[0:3]}")
        log(f"  H-axis={ax_data[3:6]}")
        log(f"  V-axis={ax_data[6:9]}")
        log(f"  normal={ax_data[9:12]}")
    except Exception as ex:
        log(f"  GetAbsoluteAxisData: {ex}")

    # 如果 Update 失败，试试不设轴但换 angle
    log("\n  --- 不设轴，直接 update_object(shaft) 而不是 part.update ---")
    try:
        ppy_b.update_object(shaft)
        log("  update_object(shaft): OK")
    except Exception as ex:
        log(f"  update_object(shaft): FAILED -> {ex}")

    # 再试 update part
    try:
        ppy_b.update()
        log("  Part Update after update_object: OK")
    except Exception as ex:
        log(f"  Part Update: FAILED -> {ex}")

    # 列出特征
    shapes = ppy_b.main_body.shapes
    feat_list = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"\n  Final features: {feat_list}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
