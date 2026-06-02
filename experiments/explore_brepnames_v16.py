"""
M1 B-Rep 探索脚本 v16

目标：在 Pad 顶面建圆形草图 + 第二层 Pad，确认是否闭合轮廓问题
"""
import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v16.txt")
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

import win32com.client
from pycatia.in_interfaces.reference import Reference as PyRef

catia = win32com.client.GetActiveObject("CATIA.Application")
from catia_copilot.catia.connection import wrap_application
from pycatia.mec_mod_interfaces.part_document import PartDocument
app_py = wrap_application()

def new_part(name):
    app_py.documents.add("Part")
    doc = catia.ActiveDocument
    ppy = PartDocument(doc).part
    log(f"\n  new part: {name}")
    return doc, ppy

def plane_ref(ppy, plane):
    o = ppy.origin_elements
    return ppy.create_reference_from_object(
        {"xy": o.plane_xy, "yz": o.plane_yz, "zx": o.plane_zx}[plane])

BREP_SUFFIX = "WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR3_SP2"

def get_face_ref(raw_doc, feature_name, face_idx):
    bname = f"FSur:(Face:(Brp:({feature_name};{face_idx});None:();Cf8:());{BREP_SUFFIX})"
    ref_com = raw_doc.CreateReferenceFromBRepName(bname, raw_doc.MainBody)
    return PyRef(ref_com)


# ── 测试A：顶面圆 + Pad（验证闭合轮廓是否解决 update 失败）──
log("=" * 60)
log("Part A: 顶面圆形草图 + Pad")
log("=" * 60)
try:
    doc, ppy = new_part("BRepFace_Circle_v16")
    raw = doc.Part

    # 底层 50×50×25 Pad
    sk1 = ppy.main_body.sketches.add(plane_ref(ppy, "xy"))
    f2d = sk1.open_edition()
    f2d.create_line(0,0,50,0); f2d.create_line(50,0,50,50)
    f2d.create_line(50,50,0,50); f2d.create_line(0,50,0,0)
    sk1.close_edition()
    pad1 = ppy.shape_factory.add_new_pad(sk1, 25)
    ppy.update()
    log(f"  底层 Pad: {pad1.name!r}  (h=25)")

    # 顶面 Reference（idx=2）
    face_ref = get_face_ref(raw, pad1.name, 2)
    sk2 = ppy.main_body.sketches.add(face_ref)
    ax = sk2.get_absolute_axis_data()
    log(f"  顶面草图 origin=({ax[0]:.1f},{ax[1]:.1f},{ax[2]:.1f})")

    # 画圆（绝对闭合）
    f2d = sk2.open_edition()
    import math
    f2d.create_circle(25, 25, 10, 0, 2 * math.pi)  # 中心(25,25) 半径10（在草图局部坐标系）
    sk2.close_edition()
    log("  圆形轮廓: OK")

    pad2 = ppy.shape_factory.add_new_pad(sk2, 10)
    try:
        ppy.update()
        log(f"  第二层 Pad: {pad2.name!r} — 成功!")
    except Exception as ex:
        log(f"  第二层 Pad update 失败: {ex}")

except Exception:
    log(_tb.format_exc())


# ── 测试B：偏移平面替代方案 ──
log("\n" + "=" * 60)
log("Part B: 偏移平面草图 + Pad（作为对照/回退方案）")
log("=" * 60)
try:
    doc2, ppy2 = new_part("OffsetPlane_v16")
    raw2 = doc2.Part

    sk1 = ppy2.main_body.sketches.add(plane_ref(ppy2, "xy"))
    f2d = sk1.open_edition()
    f2d.create_line(0,0,50,0); f2d.create_line(50,0,50,50)
    f2d.create_line(50,50,0,50); f2d.create_line(0,50,0,0)
    sk1.close_edition()
    pad1 = ppy2.shape_factory.add_new_pad(sk1, 25)
    ppy2.update()
    log(f"  底层 Pad: {pad1.name!r}")

    # 建偏移平面 z=25
    hsf = ppy2.hybrid_shape_factory
    xy_ref = plane_ref(ppy2, "xy")
    off_plane = hsf.add_new_plane_offset(xy_ref, 25.0, False)
    off_plane.name = "顶面偏移平面"
    body_com = raw2.MainBody
    body_com.InsertHybridShape(off_plane.com_object)
    ppy2.update_object(off_plane)
    log(f"  偏移平面: {off_plane.name!r}")

    off_ref = ppy2.create_reference_from_object(off_plane)
    sk2 = ppy2.main_body.sketches.add(off_ref)
    ax = sk2.get_absolute_axis_data()
    log(f"  偏移平面草图 origin=({ax[0]:.1f},{ax[1]:.1f},{ax[2]:.1f})")

    f2d = sk2.open_edition()
    import math as _m
    f2d.create_circle(25, 25, 10, 0, 2 * _m.pi)
    sk2.close_edition()

    pad2 = ppy2.shape_factory.add_new_pad(sk2, 10)
    try:
        ppy2.update()
        log(f"  第二层 Pad（偏移平面）: {pad2.name!r} — 成功!")
    except Exception as ex:
        log(f"  第二层 Pad update 失败: {ex}")

except Exception:
    log(_tb.format_exc())


log("\n" + "=" * 60)
log("Done")
log("=" * 60)
save()
