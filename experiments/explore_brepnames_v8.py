"""
M1 B-Rep 探索脚本 v8

诊断两个核心问题：
  A. SPA GetNormal 正确调用方式
  B. Shaft 旋转轴设置策略（先 Update，再设轴，再 Update）

策略：
  A1. 先用 win32com dir() 列出 Measurable COM 方法
  A2. 尝试多种调用：GetNormal() / normal / GetArea / Area
  A3. 确认哪个 N 值对应有效面（SPA Area 成功）
  B1. add_new_shaft 不设轴，先 update，再设轴，再 update
  B2. 若仍失败，尝试用 PartDocument.axes 或 sketch_axis
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output_v8.txt")
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
log("M1 B-Rep Exploration v8")
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
# Part A: SPA 诊断  — Pad 顶面法向
# ============================================================
log("\n\n" + "=" * 65)
log("Part A: SPA Measurable API 诊断")
log("=" * 65)

try:
    doc_a, ppy_a = new_part("BRepDiag_SPA")
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
    log(f"  SPA: {spa}")

    # 用第一个 N=0 先看 COM 对象能做什么
    brep_in = f"Face:(Brp:({feat_name};0);None:())"
    ref1 = part_raw_a.CreateReferenceFromBRepName(brep_in, part_raw_a)
    dn   = ref1.DisplayName
    ref2 = part_raw_a.CreateReferenceFromBRepName(dn, part_raw_a)

    log(f"\n  N=0 DisplayName: {dn!r}")

    # 获取 Measurable COM 对象
    meas_com = spa.GetMeasurable(ref2)
    log(f"  Measurable type: {type(meas_com)}")

    # 列出 COM 对象的可用方法/属性（过滤关键词）
    keywords = ["normal", "Normal", "area", "Area", "cog", "COG", "Get", "get"]
    attrs = [a for a in dir(meas_com) if any(k in a for k in keywords)]
    log(f"  Measurable attrs with keywords: {attrs}")

    # 尝试 Area
    for method in ["Area", "area"]:
        try:
            v = getattr(meas_com, method)
            log(f"  meas.{method} = {v}")
        except Exception as ex:
            log(f"  meas.{method} -> {ex}")

    # 尝试 GetArea()
    for method in ["GetArea", "get_area"]:
        try:
            v = getattr(meas_com, method)()
            log(f"  meas.{method}() = {v}")
        except Exception as ex:
            log(f"  meas.{method}() -> {ex}")

    # 尝试 GetNormal —— 用 SAFEARRAY 方式
    # CATIA SPA GetNormal 签名: Sub GetNormal(oComponents() As Double)
    # 在 win32com 里需要传递一个可变数组或用 EarlyBound
    for method in ["GetNormal", "get_normal", "Normal", "normal"]:
        try:
            attr = getattr(meas_com, method)
            if callable(attr):
                # 尝试传入空数组
                import pythoncom
                try:
                    result = attr()
                    log(f"  meas.{method}() = {result}")
                except Exception as ex1:
                    log(f"  meas.{method}() -> {ex1}")
                    # 尝试传入预分配数组
                    try:
                        import array
                        arr = array.array('d', [0.0]*3)
                        result = attr(arr)
                        log(f"  meas.{method}(arr) = {list(arr)}")
                    except Exception as ex2:
                        log(f"  meas.{method}(arr) -> {ex2}")
            else:
                log(f"  meas.{method} (property) = {attr}")
        except Exception as ex:
            log(f"  meas.{method} -> {ex}")

    # ---- 枚举 N=0..19，只看 Area 是否成功（用 ref1 和 ref2 两种）
    log(f"\n  === 枚举 N=0..19 (Area测试) ===")
    for n in range(20):
        brep_n = f"Face:(Brp:({feat_name};{n});None:())"
        try:
            r1 = part_raw_a.CreateReferenceFromBRepName(brep_n, part_raw_a)
            dn_n = r1.DisplayName
            r2 = part_raw_a.CreateReferenceFromBRepName(dn_n, part_raw_a)
        except Exception as ex:
            log(f"    N={n}: ref fail {ex}")
            continue

        area_r1 = area_r2 = "?"
        try:
            m = spa.GetMeasurable(r1)
            area_r1 = f"{m.Area:.1f}"
        except Exception as ex:
            area_r1 = f"ERR:{ex}"
        try:
            m = spa.GetMeasurable(r2)
            area_r2 = f"{m.Area:.1f}"
        except Exception as ex:
            area_r2 = f"ERR:{ex}"

        log(f"    N={n:2d}  ref1.Area={area_r1}  ref2.Area={area_r2}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part B: Shaft 诊断  — 先 Update，再设轴
# ============================================================
log("\n\n" + "=" * 65)
log("Part B: Shaft 旋转轴策略诊断")
log("=" * 65)

try:
    doc_b, ppy_b = new_part("BRepDiag_Shaft")

    # 闭合矩形轮廓 在 ZX 平面，R=5~25, V(Z)=0~40
    sk_s = ppy_b.main_body.sketches.add(plane_ref(ppy_b, "zx"))
    f2d  = sk_s.open_edition()
    f2d.create_line(5,  0,  25, 0)
    f2d.create_line(25, 0,  25, 40)
    f2d.create_line(25, 40, 5,  40)
    f2d.create_line(5,  40, 5,  0)
    sk_s.close_edition()

    # 步骤1：add_new_shaft（不设轴）
    shaft = ppy_b.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft.name!r}")

    # 步骤2：先 Update（CATIA 默认旋转轴是 H轴）
    try:
        ppy_b.update()
        log("  Update (no axis set): OK")
    except Exception as ex:
        log(f"  Update (no axis set): FAILED -> {ex}")

    # 步骤3：建 HybridShape Z 轴线
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
        log(f"  Z-line HybridShape: FAILED -> {ex}")
        z_ref = None

    # 步骤4：设置 revolution_axis
    if z_ref:
        try:
            shaft.revolution_axis = z_ref
            log("  shaft.revolution_axis = z_ref: OK")
        except Exception as ex:
            log(f"  shaft.revolution_axis: FAILED -> {ex}")
            # 尝试 COM 直接设置
            try:
                shaft.com_object.RevolutionAxis = z_ref.com_object
                log("  shaft.com_object.RevolutionAxis: OK")
            except Exception as ex2:
                log(f"  shaft.com_object.RevolutionAxis: FAILED -> {ex2}")

    # 步骤5：Update with axis
    try:
        ppy_b.update()
        log("  Update (with axis): OK")
    except Exception as ex:
        log(f"  Update (with axis): FAILED -> {ex}")

    # 步骤6：检查 shaft COM 对象的属性
    log(f"\n  Shaft COM attrs (Revolution*):")
    shaft_attrs = [a for a in dir(shaft.com_object)
                   if "evol" in a.lower() or "axis" in a.lower() or "Angle" in a]
    log(f"    {shaft_attrs}")

    # 步骤7：尝试用 sketch 的 H 轴作为旋转轴（pycatia 文档方式）
    log("\n  Trying sketch H-axis as revolution axis...")
    try:
        # sketch 的 reference_axis 应该是 H轴
        # 在 ZX 平面，H 方向是 X（水平），V 是 Z（垂直）
        # Shaft 默认应该绕 V轴（Z）旋转——但 pycatia API 中是 H轴？
        # 检查 Sketch 的轴系
        log(f"    sketch axis_system:")
        ax = sk_s.com_object
        ax_attrs = [a for a in dir(ax) if "xis" in a or "plane" in a.lower()]
        log(f"    {ax_attrs}")
    except Exception as ex:
        log(f"    {ex}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
