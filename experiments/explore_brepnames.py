"""
M1 B-Rep 名称探索脚本 v7

基于 v6 的关键发现：
  - CreateReferenceFromBRepName(name, part) 对中文特征名有效，但 DisplayName 显示的是
    英文内部名称: FSur:(Face:(Brp:(Pad.1;N);None:();Cf8:());WithTemporaryBody;...)
  - SPA 和 sketches.add 需要用 DisplayName 格式的 ref（英文 Pad.1，含 Cf8/WithTemporaryBody）
  - Shaft 改回闭合轮廓（4线矩形），HybridShape Z 轴在 update 前设置
  - AddNewShaft 在开放轮廓上失败，闭合轮廓应该可以（v5 测试过）

目标：
  1. 识别 Pad.1 的 6 个真实面（用 SPA 筛选）+ 法向量
  2. 用顶面（法向 Z=+1）建顶面草图验证 add_sketch_on_face
  3. Shaft + Groove 的 B-Rep 名称
"""

import sys, os, traceback as _tb
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUT = os.path.join(os.path.dirname(__file__), "brep_output.txt")
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

# ============================================================
log("=" * 65)
log("M1 B-Rep Exploration v7")
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

# ============================================================
# 辅助
# ============================================================
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

def features_now(ppy):
    try:
        s = ppy.main_body.shapes
        return [s.item(i+1).name for i in range(s.count)]
    except Exception: return []

def get_spa(doc_com):
    try: return doc_com.GetWorkbench("SPAWorkbench")
    except Exception: return None

def make_z_axis_ref(ppy):
    """HybridShapeFactory 建 Z 方向线段，返回 Reference"""
    try:
        hsf   = ppy.hybrid_shape_factory
        hbody = ppy.hybrid_bodies.add()
        pt0   = hsf.add_new_point_coord(0.0, 0.0,   0.0)
        pt1   = hsf.add_new_point_coord(0.0, 0.0, 100.0)
        hbody.append_hybrid_shape(pt0)
        hbody.append_hybrid_shape(pt1)
        ppy.update_object(pt0)
        ppy.update_object(pt1)
        z_line = hsf.add_new_line_pt_pt(
            ppy.create_reference_from_object(pt0),
            ppy.create_reference_from_object(pt1))
        hbody.append_hybrid_shape(z_line)
        ppy.update_object(z_line)
        ref = ppy.create_reference_from_object(z_line)
        log("    [OK] Z-axis HybridShape line created")
        return ref
    except Exception as ex:
        log(f"    make_z_axis_ref failed: {ex}")
        return None

# ============================================================
# B-Rep 枚举：用 DisplayName 构造 SPA 可用的 ref
# ============================================================
def enumerate_valid_faces(doc_com, ppy, feat_names, spa=None):
    """
    对每个特征名穷举 Face N=0..19，
    用 CreateReferenceFromBRepName(name, part) 获取 ref 和 DisplayName，
    再用 DisplayName 重新构造 ref 给 SPA 测量（获取真实法向 + COG）。
    返回 {feat: [{n, brep_input, display_name, ref_dn, normal, cog}]}
    """
    part_raw = doc_com.Part
    results  = {}

    for feat in feat_names:
        log(f"\n  --- Feature [{feat}] ---")
        found = []

        for n in range(0, 20):
            brep_in = f"Face:(Brp:({feat};{n});None:())"

            # 1. 构造 ref（用中文特征名）
            ref1 = None
            try: ref1 = part_raw.CreateReferenceFromBRepName(brep_in, part_raw)
            except Exception: continue

            # 2. 读取 DisplayName（=英文内部格式）
            dn = None
            for attr in ("DisplayName", "display_name", "Name"):
                try:
                    v = getattr(ref1, attr)
                    if v: dn = v; break
                except Exception: pass
            if not dn:
                continue

            # 3. 用 DisplayName 重新构造 ref 给 SPA
            ref2 = None
            try: ref2 = part_raw.CreateReferenceFromBRepName(dn, part_raw)
            except Exception:
                ref2 = ref1  # 回退到原 ref

            # 4. SPA 测量
            normal_str = cog_str = area_str = ""
            is_valid_face = False
            if spa:
                try:
                    meas = spa.GetMeasurable(ref2)
                    n3   = meas.GetNormal()
                    normal_str = f"  normal=({n3[0]:+.3f},{n3[1]:+.3f},{n3[2]:+.3f})"
                    is_valid_face = True
                except Exception as ex:
                    normal_str = f"  SPA_normal_fail={ex}"
                try:
                    meas2 = spa.GetMeasurable(ref2)
                    c3    = meas2.GetCOG()
                    cog_str = f"  COG=({c3[0]:.1f},{c3[1]:.1f},{c3[2]:.1f})"
                except Exception: pass
                try:
                    meas3 = spa.GetMeasurable(ref2)
                    area  = meas3.Area
                    area_str = f"  area={area:.2f}mm2"
                    is_valid_face = True  # area成功则是有效面
                except Exception: pass

            log(f"    N={n:2d} {'[REAL]' if is_valid_face else '[????]'}"
                f"  input={brep_in!r}"
                f"\n           dn={dn!r}"
                f"{normal_str}{cog_str}{area_str}")

            found.append({
                "n": n, "brep_in": brep_in, "dn": dn,
                "ref1": ref1, "ref2": ref2,
                "normal": normal_str, "cog": cog_str,
                "is_valid": is_valid_face,
            })

        valid_count = sum(1 for x in found if x["is_valid"])
        log(f"\n    Total found: {len(found)}, SPA-confirmed valid: {valid_count}")
        results[feat] = found

    return results


# ============================================================
# Part 1: BRepTest_Prismatic  (Pad + Pocket)
# ============================================================
log("\n\n" + "=" * 65)
log("Part 1: BRepTest_Prismatic  (Pad + Pocket)")
log("=" * 65)

brep_results1 = {}
try:
    doc1, ppy1 = new_part("BRepTest_Prismatic")

    sk1 = ppy1.main_body.sketches.add(plane_ref(ppy1, "xy"))
    f2d = sk1.open_edition()
    f2d.create_line(0,  0,   100, 0);  f2d.create_line(100,0,  100, 60)
    f2d.create_line(100,60,  0,  60);  f2d.create_line(0,  60, 0,   0)
    sk1.close_edition()
    pad1 = ppy1.shape_factory.add_new_pad(sk1, 20)
    ppy1.update()
    feats1 = features_now(ppy1)
    log(f"  Pad: {pad1.name}  features: {feats1}")

    sk2 = ppy1.main_body.sketches.add(plane_ref(ppy1, "xy"))
    f2d = sk2.open_edition()
    f2d.create_line(30, 15, 70, 15); f2d.create_line(70, 15, 70, 45)
    f2d.create_line(70, 45, 30, 45); f2d.create_line(30, 45, 30, 15)
    sk2.close_edition()
    pkt1 = ppy1.shape_factory.add_new_pocket(sk2, 10)
    ppy1.update()
    feats1 = features_now(ppy1)
    log(f"  Pocket: {pkt1.name}  features: {feats1}")

    spa1 = get_spa(doc1)
    log(f"  SPAWorkbench: {'available' if spa1 else 'NOT available'}")
    brep_results1 = enumerate_valid_faces(doc1, ppy1, feats1, spa1)

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part 2: BRepTest_Revolute  (Shaft + Groove)
#   闭合轮廓 + HybridShape Z 轴
# ============================================================
log("\n\n" + "=" * 65)
log("Part 2: BRepTest_Revolute  (Shaft + Groove)")
log("=" * 65)

brep_results2 = {}
try:
    doc2, ppy2 = new_part("BRepTest_Revolute")

    # Shaft：闭合轮廓 R=5..25, H=0..40（矩形，全在 H>0 侧）
    log("\n  [Shaft] ZX closed profile R=5~25 H=40")
    sk_s = ppy2.main_body.sketches.add(plane_ref(ppy2, "zx"))
    f2d  = sk_s.open_edition()
    f2d.create_line(5,  0,  25, 0)
    f2d.create_line(25, 0,  25, 40)
    f2d.create_line(25, 40, 5,  40)
    f2d.create_line(5,  40, 5,  0)
    sk_s.close_edition()

    shaft1 = ppy2.shape_factory.add_new_shaft(sk_s)
    log(f"  add_new_shaft: {shaft1.name}")

    # 建 Z 轴 HybridShape 并设 revolution_axis
    z_ref = make_z_axis_ref(ppy2)
    if z_ref:
        try:
            shaft1.revolution_axis = z_ref
            log("  shaft.revolution_axis = Z-line  OK")
        except Exception as ex:
            log(f"  shaft.revolution_axis failed: {ex}")

    try:
        ppy2.update()
        feats2 = features_now(ppy2)
        log(f"  Shaft Update OK  features: {feats2}")
    except Exception as ex:
        log(f"  Shaft Update FAILED: {ex}")
        feats2 = features_now(ppy2)

    # Groove：环形槽
    log("\n  [Groove] ZX closed profile R=22~25 Z=15~20")
    sk_g = ppy2.main_body.sketches.add(plane_ref(ppy2, "zx"))
    f2d  = sk_g.open_edition()
    f2d.create_line(22, 15, 25, 15); f2d.create_line(25, 15, 25, 20)
    f2d.create_line(25, 20, 22, 20); f2d.create_line(22, 20, 22, 15)
    sk_g.close_edition()

    try:
        groove1 = ppy2.shape_factory.add_new_groove(sk_g)
        log(f"  add_new_groove: {groove1.name}")
        if z_ref:
            try:
                groove1.revolution_axis = z_ref
                log("  groove.revolution_axis = Z-line  OK")
            except Exception as ex:
                log(f"  groove.revolution_axis failed: {ex}")
        ppy2.update()
        feats2 = features_now(ppy2)
        log(f"  Groove Update OK  features: {feats2}")
    except Exception as ex:
        log(f"  Groove failed: {ex}")
        feats2 = features_now(ppy2)

    spa2 = get_spa(doc2)
    feats2 = features_now(ppy2)
    log(f"  Final features: {feats2}")
    brep_results2 = enumerate_valid_faces(doc2, ppy2, feats2, spa2)

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
# Part 3: BRepTest_FaceSketch
#   用 SPA 确认有效面后，在顶面建草图
# ============================================================
log("\n\n" + "=" * 65)
log("Part 3: BRepTest_FaceSketch  (SPA-confirmed top face -> sketch)")
log("=" * 65)

try:
    doc3, ppy3 = new_part("BRepTest_FaceSketch")
    part_raw3  = doc3.Part

    sk_b = ppy3.main_body.sketches.add(plane_ref(ppy3, "xy"))
    f2d  = sk_b.open_edition()
    f2d.create_line(0,0, 80,0); f2d.create_line(80,0, 80,80)
    f2d.create_line(80,80, 0,80); f2d.create_line(0,80, 0,0)
    sk_b.close_edition()
    pad_b = ppy3.shape_factory.add_new_pad(sk_b, 30)
    ppy3.update()
    feats3 = features_now(ppy3)
    log(f"  Base Pad: {pad_b.name}  features: {feats3}")

    spa3 = get_spa(doc3)
    r3   = enumerate_valid_faces(doc3, ppy3, feats3, spa3)

    # 找顶面 ref：法向 Z=+1 的面（ref2）
    top_info = None
    feat3_name = feats3[0]
    for finfo in r3.get(feat3_name, []):
        if not finfo["is_valid"]: continue
        if "+1.000" in finfo["normal"] or ("+1.00" in finfo["normal"]
                                            and "+0.000" in finfo["normal"]
                                            and finfo["normal"].count("+0.000") == 2):
            top_info = finfo
            log(f"\n  Top face confirmed (Z normal=+1): N={finfo['n']}  {finfo['brep_in']!r}")
            break

    if top_info is None and r3.get(feat3_name):
        # SPA 不可用时，按面积排序找最大面（顶面 80x80=6400mm2）
        valid_faces = [x for x in r3.get(feat3_name, []) if x["is_valid"]]
        log(f"\n  Cannot identify top by normal, {len(valid_faces)} valid faces found")
        log("  Trying each valid face for sketch (will check if Pad extrudes upward)...")
        for finfo in valid_faces:
            ref_try = finfo["ref2"]
            try:
                sk_t = ppy3.main_body.sketches.add(ref_try)
                f2d_t = sk_t.open_edition()
                f2d_t.create_line(30,30, 50,30); f2d_t.create_line(50,30, 50,50)
                f2d_t.create_line(50,50, 30,50); f2d_t.create_line(30,50, 30,30)
                sk_t.close_edition()
                pad_t = ppy3.shape_factory.add_new_pad(sk_t, 15)
                ppy3.update()
                log(f"  [OK] sketch on N={finfo['n']} -> {pad_t.name}  features: {features_now(ppy3)}")
                top_info = finfo
                break
            except Exception as ex:
                try: ppy3.update()
                except Exception: pass
    elif top_info:
        # 直接用 ref2 建草图
        log(f"\n  Building sketch on top face (ref2)...")
        try:
            sk_top = ppy3.main_body.sketches.add(top_info["ref2"])
            f2d_t  = sk_top.open_edition()
            f2d_t.create_line(30,30, 50,30); f2d_t.create_line(50,30, 50,50)
            f2d_t.create_line(50,50, 30,50); f2d_t.create_line(30,50, 30,30)
            sk_top.close_edition()
            pad_top = ppy3.shape_factory.add_new_pad(sk_top, 15)
            ppy3.update()
            log(f"  [OK] top face pad: {pad_top.name}  features: {features_now(ppy3)}")
        except Exception:
            log(f"  top face sketch FAILED:\n{_tb.format_exc()}")
            # 尝试用 ref1（短格式）
            log("  Retrying with ref1 (short format)...")
            try:
                sk_top2 = ppy3.main_body.sketches.add(top_info["ref1"])
                f2d_t2  = sk_top2.open_edition()
                f2d_t2.create_line(30,30, 50,30); f2d_t2.create_line(50,30, 50,50)
                f2d_t2.create_line(50,50, 30,50); f2d_t2.create_line(30,50, 30,30)
                sk_top2.close_edition()
                pad_top2 = ppy3.shape_factory.add_new_pad(sk_top2, 15)
                ppy3.update()
                log(f"  [OK] ref1 face pad: {pad_top2.name}  features: {features_now(ppy3)}")
            except Exception:
                log(f"  ref1 also FAILED:\n{_tb.format_exc()}")

except Exception:
    log(f"\n  [ERROR]:\n{_tb.format_exc()}")


# ============================================================
log("\n\n" + "=" * 65)
log("Done")
log("=" * 65)
save()
