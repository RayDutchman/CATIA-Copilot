"""
M1 B-Rep 探索脚本 v9

核心验证：
  A. CreateReferenceFromBRepName 第二参数：Part COM 对象 vs Reference COM 对象
     → SPA Area 是否成功
  B. Shaft 草图坐标系：ZX 平面 H=Z/V=X vs H=X/V=Z
     + RevoluteAxis 正确赋值方式
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v9.txt")
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
log("M1 B-Rep Exploration v9")
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
# Part A：SPA 第二参数修复测试
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: CreateReferenceFromBRepName 第二参数测试")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepDiag_SPA_v9")
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

    # 获取 Part 的 Reference
    try:
        part_ref_py = ppy_a.create_reference_from_object(ppy_a.part)
        part_ref_com = part_ref_py.com_object
        log(f"  Part Reference: {part_ref_com}")
    except Exception as ex:
        log(f"  Part Reference failed: {ex}")
        part_ref_com = None

    log("\n  --- 枚举 N=0..9 with 3 ref variants ---")
    for n in range(10):
        brep_in = f"Face:(Brp:({feat_name};{n});None:())"

        # 变体1：原始方式（part_raw 作为第二参数）
        a1 = "?"
        try:
            r = part_raw_a.CreateReferenceFromBRepName(brep_in, part_raw_a)
            a1 = f"{spa.GetMeasurable(r).Area:.1f}"
        except Exception as ex:
            a1 = f"E:{str(ex)[:40]}"

        # 变体2：用 Part Reference COM 对象
        a2 = "?"
        if part_ref_com:
            try:
                r = part_raw_a.CreateReferenceFromBRepName(brep_in, part_ref_com)
                a2 = f"{spa.GetMeasurable(r).Area:.1f}"
            except Exception as ex:
                a2 = f"E:{str(ex)[:40]}"

        # 变体3：用 DisplayName + Part Reference
        a3 = "?"
        try:
            r1 = part_raw_a.CreateReferenceFromBRepName(brep_in, part_raw_a)
            dn = r1.DisplayName
            if part_ref_com:
                r3 = part_raw_a.CreateReferenceFromBRepName(dn, part_ref_com)
            else:
                r3 = part_raw_a.CreateReferenceFromBRepName(dn, part_raw_a)
            a3 = f"{spa.GetMeasurable(r3).Area:.1f}"
        except Exception as ex:
            a3 = f"E:{str(ex)[:40]}"

        log(f"  N={n:2d}  v1={a1}  v2={a2}  v3={a3}")

    # 试试 pycatia 的 create_reference_from_b_rep_name
    log("\n  --- pycatia create_reference_from_b_rep_name ---")
    try:
        brep_in = f"Face:(Brp:({feat_name};1);None:())"
        # 先获取 pad 的 reference
        pad_ref = ppy_a.create_reference_from_object(pad)
        r_py = ppy_a.create_reference_from_b_rep_name(brep_in, pad_ref)
        log(f"  pycatia ref: {r_py}")
        area_py = spa.GetMeasurable(r_py.com_object).Area
        log(f"  SPA Area via pycatia ref: {area_py}")
    except Exception as ex:
        log(f"  pycatia create_reference_from_b_rep_name: {_tb.format_exc()}")

    # 试试直接从 Part.GeometricElements 获取面
    log("\n  --- Part.GeometricElements ---")
    try:
        ge = part_raw_a.GeometricElements
        log(f"  GeometricElements count: {ge.Count}")
        for i in range(1, min(ge.Count+1, 5)):
            item = ge.Item(i)
            log(f"    item({i}): {item}")
    except Exception as ex:
        log(f"  GeometricElements: {ex}")

    # 试试 MainBody.Shapes.Item(1) 的面
    log("\n  --- 通过 Body.Faces 或 Shape 访问实体面 ---")
    try:
        body_com = part_raw_a.MainBody
        log(f"  MainBody attrs (face/surf): {[a for a in dir(body_com) if 'ace' in a or 'urf' in a or 'Face' in a]}")
    except Exception as ex:
        log(f"  MainBody: {ex}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B：Shaft 草图坐标测试
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: Shaft 草图方向测试")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_Shaft_v9")

    # 测试1：ZX 平面，H=Z, V=X
    # Shaft 旋转轴 = H 轴 = Z 轴（V=0 线）
    # 轮廓：H(Z)=5..25, V(X)=0..40，底边 V=0 触 H 轴
    log("\n  [Test1] ZX 平面, H(Z)=5~25, V(X)=0~40, 底边 V=0")
    try:
        sk1 = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "zx"))
        f2d = sk1.open_edition()
        f2d.create_line(5,  0,  25, 0)
        f2d.create_line(25, 0,  25, 40)
        f2d.create_line(25, 40, 5,  40)
        f2d.create_line(5,  40, 5,  0)
        sk1.close_edition()
        shaft1 = ppy_b.shape_factory.add_new_shaft(sk1)
        log(f"  add_new_shaft: {shaft1.name!r}")
        ppy_b.update()
        log("  Update: OK")
    except Exception as ex:
        log(f"  FAILED: {ex}")

    # 测试2：ZX 平面，H=0..25, V=0..40（起点从 H=0）
    # 底边和左边都通过 V=0 轴
    log("\n  [Test2] ZX 平面, H=0~25, V=0~40, 左边 H=0 在轴上")
    try:
        sk2 = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "zx"))
        f2d = sk2.open_edition()
        f2d.create_line(0,  0,  25, 0)
        f2d.create_line(25, 0,  25, 40)
        f2d.create_line(25, 40, 0,  40)
        f2d.create_line(0,  40, 0,  0)
        sk2.close_edition()
        shaft2 = ppy_b.shape_factory.add_new_shaft(sk2)
        log(f"  add_new_shaft: {shaft2.name!r}")
        ppy_b.update()
        log("  Update: OK")
    except Exception as ex:
        log(f"  FAILED: {ex}")

    # 测试3：YZ 平面（换个平面试试）
    log("\n  [Test3] YZ 平面, H=0~25, V=0~40")
    try:
        sk3 = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "yz"))
        f2d = sk3.open_edition()
        f2d.create_line(0,  0,  25, 0)
        f2d.create_line(25, 0,  25, 40)
        f2d.create_line(25, 40, 0,  40)
        f2d.create_line(0,  40, 0,  0)
        sk3.close_edition()
        shaft3 = ppy_b.shape_factory.add_new_shaft(sk3)
        log(f"  add_new_shaft: {shaft3.name!r}")
        ppy_b.update()
        log("  Update: OK")
    except Exception as ex:
        log(f"  FAILED: {ex}")

    # 测试4：XY 平面
    log("\n  [Test4] XY 平面, H=0~25, V=0~40")
    try:
        sk4 = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "xy"))
        f2d = sk4.open_edition()
        f2d.create_line(0,  0,  25, 0)
        f2d.create_line(25, 0,  25, 40)
        f2d.create_line(25, 40, 0,  40)
        f2d.create_line(0,  40, 0,  0)
        sk4.close_edition()
        shaft4 = ppy_b.shape_factory.add_new_shaft(sk4)
        log(f"  add_new_shaft: {shaft4.name!r}")
        ppy_b.update()
        log("  Update: OK")
    except Exception as ex:
        log(f"  FAILED: {ex}")

    # 输出特征列表
    shapes = ppy_b.main_body.shapes
    feat_list = [shapes.item(i+1).name for i in range(shapes.count)]
    log(f"\n  Final features: {feat_list}")

    # 对成功的 Shaft 测试 SPA（用 Part Reference 方式）
    part_raw_b = doc_b.Part
    spa_b = doc_b.GetWorkbench("SPAWorkbench")
    for feat in feat_list:
        if not ("旋转" in feat or "Shaft" in feat or "shaft" in feat.lower()):
            continue
        log(f"\n  SPA test on {feat!r}:")
        for n in range(8):
            brep_in = f"Face:(Brp:({feat};{n});None:())"
            try:
                r = part_raw_b.CreateReferenceFromBRepName(brep_in, part_raw_b)
                dn = r.DisplayName
                area = spa_b.GetMeasurable(r).Area
                log(f"    N={n:2d} Area={area:.1f}  dn={dn!r}")
            except Exception as ex:
                pass  # 静默跳过无效面

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
