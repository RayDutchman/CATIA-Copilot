"""
M1 B-Rep 探索脚本 v15

目标：找到中文 CATIA V5 中获取实体顶面 Reference 的正确方式
方法：
  A. 枚举 Body faces via win32com 的 ShapeTopology
  B. 尝试 CreateReferenceFromBRepName 不同面索引
  C. 从 pad.com_object 获取输出面
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v15.txt")
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
log("M1 B-Rep Exploration v15")
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


# ── 辅助：新建 Part ──────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────
# Part A：通过 CreateReferenceFromBRepName 尝试不同面索引
# ────────────────────────────────────────────────────────────
log("\n\n" + "=" * 65)
log("Part A: CreateReferenceFromBRepName — 面索引穷举")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepFace_v15")
    raw_a = doc_a.Part

    # 建 60×60×30 Pad
    sk = ppy_a.main_body.sketches.add(plane_ref(ppy_a, "xy"))
    f2d = sk.open_edition()
    f2d.create_line(0, 0, 60, 0); f2d.create_line(60, 0, 60, 60)
    f2d.create_line(60, 60, 0, 60); f2d.create_line(0, 60, 0, 0)
    sk.close_edition()
    pad = ppy_a.shape_factory.add_new_pad(sk, 30)
    ppy_a.update()
    pad_name = pad.name
    log(f"  Pad name: {pad_name!r}")

    # 尝试 BRep 名称格式（CATIA V5 内部名通常用英文 feature 名）
    # 格式：FSur:(Face:(Brp:(<pad_name>;<face_idx>);None:();Cf8:());...)
    suffix = "WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR3_SP2"
    obj_com = raw_a.MainBody  # 作为 context object

    valid = []
    for idx in range(1, 10):
        for name_used in [pad_name, "Pad.1"]:  # 中文名和英文名都试
            bname = f"FSur:(Face:(Brp:({name_used};{idx});None:();Cf8:());{suffix})"
            try:
                ref_com = raw_a.CreateReferenceFromBRepName(bname, obj_com)
                ref_py  = PyRef(ref_com)
                # 尝试建草图验证
                sk_test = ppy_a.main_body.sketches.add(ref_py)
                ax = sk_test.get_absolute_axis_data()
                origin_z = ax[2]
                log(f"  [OK] idx={idx} name={name_used!r}  origin_z={origin_z:.1f}  -> {bname[:60]}")
                valid.append((idx, name_used, origin_z, ref_py))
            except Exception as ex:
                log(f"  [fail] idx={idx} name={name_used!r}  {str(ex)[:50]}")

    log(f"\n  Valid refs: {len(valid)}")
    for idx, nm, oz, _ in valid:
        label = "顶面" if abs(oz - 30) < 1 else ("底面" if abs(oz) < 1 else f"z={oz:.1f}")
        log(f"    idx={idx} name={nm!r} origin_z={oz:.1f}  ({label})")

except Exception:
    log(_tb.format_exc())


# ────────────────────────────────────────────────────────────
# Part B：若 A 找到顶面，在顶面画草图并 Pad
# ────────────────────────────────────────────────────────────
log("\n\n" + "=" * 65)
log("Part B: 顶面草图 + 第二层 Pad（验证可行性）")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepFace_TopPad_v15")
    raw_b = doc_b.Part

    # 底层 50×50×20 Pad
    sk_base = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "xy"))
    f2d = sk_base.open_edition()
    f2d.create_line(0, 0, 50, 0); f2d.create_line(50, 0, 50, 50)
    f2d.create_line(50, 50, 0, 50); f2d.create_line(0, 50, 0, 0)
    sk_base.close_edition()
    pad1 = ppy_b.shape_factory.add_new_pad(sk_base, 20)
    ppy_b.update()
    log(f"  底层 Pad: {pad1.name!r}")

    # 构造顶面 Reference（idx=2 通常是顶面）
    pad1_name = pad1.name
    suffix = "WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR3_SP2"

    face_ref = None
    for idx in range(1, 10):
        for nm in [pad1_name, "Pad.1"]:
            bname = f"FSur:(Face:(Brp:({nm};{idx});None:();Cf8:());{suffix})"
            try:
                ref_com = raw_b.CreateReferenceFromBRepName(bname, raw_b.MainBody)
                ref_py  = PyRef(ref_com)
                sk_test = ppy_b.main_body.sketches.add(ref_py)
                ax = sk_test.get_absolute_axis_data()
                origin_z = ax[2]
                if abs(origin_z - 20) < 1:  # 顶面 z=20
                    face_ref = ref_py
                    log(f"  找到顶面: idx={idx} name={nm!r} origin_z={origin_z:.1f}")
                    break
            except Exception:
                pass
        if face_ref:
            break

    if face_ref is None:
        log("  [FAIL] 未找到顶面 Reference")
    else:
        # 在顶面画草图
        sk_top = ppy_b.main_body.sketches.add(face_ref)
        f2d = sk_top.open_edition()
        f2d.create_line(10, 10, 40, 10); f2d.create_line(40, 10, 40, 40)
        f2d.create_line(40, 40, 10, 40); f2d.create_line(10, 40, 10, 10)
        sk_top.close_edition()
        log("  顶面草图绘制: OK")

        pad2 = ppy_b.shape_factory.add_new_pad(sk_top, 15)
        try:
            ppy_b.update()
            log(f"  第二层 Pad: {pad2.name!r} — 成功!")
        except Exception as ex:
            log(f"  第二层 Pad update 失败: {ex}")

except Exception:
    log(_tb.format_exc())


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
