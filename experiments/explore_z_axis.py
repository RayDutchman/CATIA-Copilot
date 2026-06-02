"""
零件 Z 轴 COM 获取方式探查
目标：在空 Part（或只有一个 Pad 的 Part）中，找到真正 Z 轴的 COM 引用方式，
      使其可作为 Shaft.revolute_axis。

探查方向：
  1. Part.OriginElements 有哪些属性（plane_xy/yz/zx 之外）
  2. Part.AxisSystems — 零件默认坐标系轴
  3. HybridShapeFactory 直线方向法
  4. 草图定位对话框中的"Z 轴"选项对应的 COM 路径
"""

import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import win32com.client
from pycatia.mec_mod_interfaces.part_document import PartDocument
from pycatia.in_interfaces.reference import Reference as PyRef

catia  = win32com.client.GetActiveObject("CATIA.Application")
from catia_copilot.catia.connection import wrap_application
app_py = wrap_application()

OUTPUT = os.path.join(os.path.dirname(__file__), "explore_z_axis_output.txt")
_lines = []
def log(msg=""):
    s = str(msg)
    try: print(s)
    except: print(repr(s))
    _lines.append(s)
def save():
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(f"\n>>> saved: {OUTPUT}")

log("零件 Z 轴 COM 获取方式探查")
log("=" * 60)

# ── 新建空 Part ──
app_py.documents.add("Part")
doc = catia.ActiveDocument
ppy = PartDocument(doc).part

log(f"Part name: {ppy.name}")
log("")

# ── 1. OriginElements 属性枚举 ──
log("── 1. OriginElements 属性 ──")
origin = ppy.origin_elements
log(f"  type(origin): {type(origin)}")
# 已知属性
for attr in ["plane_xy", "plane_yz", "plane_zx"]:
    try:
        val = getattr(origin, attr)
        log(f"  origin.{attr} = {val} (type={type(val).__name__})")
    except Exception as e:
        log(f"  origin.{attr} FAILED: {e}")

# 尝试轴线相关属性名
for attr in ["axis_x", "axis_y", "axis_z",
             "x_axis", "y_axis", "z_axis",
             "direction_x", "direction_y", "direction_z",
             "line_x", "line_y", "line_z"]:
    try:
        val = getattr(origin, attr)
        log(f"  origin.{attr} = {val}  ← 存在!")
    except AttributeError:
        log(f"  origin.{attr} → AttributeError（不存在）")
    except Exception as e:
        log(f"  origin.{attr} → {type(e).__name__}: {e}")

log("")

# ── 2. COM origin_elements 直接访问 ──
log("── 2. COM doc.Part.OriginElements 属性 ──")
origin_com = doc.Part.OriginElements
log(f"  type: {type(origin_com)}")
# 枚举 COM 对象的所有方法/属性
try:
    props = [p for p in dir(origin_com) if not p.startswith("_")]
    log(f"  COM 属性/方法: {props}")
except Exception as e:
    log(f"  dir() FAILED: {e}")

log("")

# ── 3. AxisSystems ──
log("── 3. Part.AxisSystems ──")
try:
    ax_sys_com = doc.Part.AxisSystems
    log(f"  AxisSystems.Count = {ax_sys_com.Count}")
    for i in range(1, ax_sys_com.Count + 1):
        ax = ax_sys_com.Item(i)
        try:    nm = ax.Name
        except: nm = "?"
        log(f"  AxisSystem[{i}] name={repr(nm)}")
        # 尝试获取轴线
        for method in ["GetXAxis", "GetYAxis", "GetZAxis",
                       "get_x_axis", "get_y_axis", "get_z_axis",
                       "XAxis", "YAxis", "ZAxis"]:
            try:
                val = getattr(ax, method)
                if callable(val):
                    result = val()
                    log(f"    {method}() = {result}")
                else:
                    log(f"    {method} = {val}")
            except Exception as e:
                pass  # 不存在的属性不打印
except Exception as e:
    log(f"  AxisSystems FAILED: {e}")

log("")

# ── 4. HybridShapeFactory 直线方向 ──
log("── 4. HybridShapeFactory 创建 Z 方向线（无 HybridBody）──")
try:
    hsf = ppy.hybrid_shape_factory
    # 尝试直接用 Direction 构造
    # add_new_direction 接受 Reference
    # 先构造两个点
    pt0 = hsf.add_new_point_coord(0, 0,   0)
    pt1 = hsf.add_new_point_coord(0, 0, 100)
    # 不加入任何 HybridBody，直接更新
    ppy.update_object(pt0)
    ppy.update_object(pt1)
    ref0 = PyRef(ppy.create_reference_from_object(pt0))
    ref1 = PyRef(ppy.create_reference_from_object(pt1))
    z_line = hsf.add_new_line_pt_pt(ref0, ref1)
    ppy.update_object(z_line)
    z_ref = ppy.create_reference_from_object(z_line)
    log(f"  z_line 创建成功（无 HybridBody）: {z_line}")
    log(f"  z_ref: {z_ref}")
    # 测试是否可作为 revolute_axis
    sk_zx = ppy.main_body.sketches.add(
        ppy.create_reference_from_object(ppy.origin_elements.plane_zx))
    f2d = sk_zx.open_edition()
    f2d.create_line(0, 5,   0, 25)
    f2d.create_line(0, 25, 40, 25)
    f2d.create_line(40, 25, 40, 5)
    f2d.create_line(40, 5,   0, 5)
    sk_zx.close_edition()
    shaft = ppy.shape_factory.add_new_shaft(sk_zx)
    shaft.revolute_axis = PyRef(z_ref) if not isinstance(z_ref, PyRef) else z_ref
    ppy.update()
    log("  *** Shaft update 成功（无 HybridBody Z 线方案）! ***")
except Exception as e:
    log(f"  无 HybridBody 方案 FAILED: {e}")

log("")

# ── 5. 尝试通过 Part.OriginElements COM 获取轴线 ──
log("── 5. 通过 Part.OriginElements（COM 只读属性）──")
try:
    # CATIA COM OriginElements 接口实际上只有三个平面
    # 但也许 .PlaneXY 对象本身有轴线子对象？
    plane_xy_com = doc.Part.OriginElements.PlaneXY
    log(f"  PlaneXY type: {type(plane_xy_com)}")
    log(f"  PlaneXY dir: {[p for p in dir(plane_xy_com) if not p.startswith('_')]}")
except Exception as e:
    log(f"  PlaneXY FAILED: {e}")

log("")
log("=" * 60)
log("探查完成。")
save()
