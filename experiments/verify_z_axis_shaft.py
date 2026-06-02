"""
验证 Part.FindObjectByName('Z 轴') 可用于 Shaft.revolute_axis
新建 Part → ZX 平面定位草图 → add_new_shaft → FindObjectByName('Z 轴') → update
"""
import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import win32com.client
from pycatia.mec_mod_interfaces.part_document import PartDocument
from pycatia.in_interfaces.reference import Reference as PyRef
from catia_copilot.catia.connection import wrap_application

catia  = win32com.client.GetActiveObject("CATIA.Application")
app_py = wrap_application()

# ── 新建 Part ──
app_py.documents.add("Part")
doc  = catia.ActiveDocument
ppy  = PartDocument(doc).part

# ── 定位草图：ZX 平面，显式设定 H=-X, V=Z ──
# 支撑面：ZX 平面
plane_zx = ppy.origin_elements.plane_zx
plane_ref = ppy.create_reference_from_object(plane_zx)
sk = ppy.main_body.sketches.add(plane_ref)

# 定位：原点 (0,0,0)，H 方向 = -X = (-1,0,0)，V 方向 = Z = (0,0,1)
sk.set_absolute_axis_data((
    0.0, 0.0, 0.0,   # origin
   -1.0, 0.0, 0.0,   # H = -X
    0.0, 0.0, 1.0,   # V = Z
))

# ── 画旋转轮廓（在 H > 0 侧，即 X < 0 侧；V 方向为高度）
# 矩形：H=5~25（对应 X=-5~-25），V=0~40（对应 Z=0~40）
f2d = sk.open_edition()
f2d.create_line( 5,  0,  25,  0)
f2d.create_line(25,  0,  25, 40)
f2d.create_line(25, 40,   5, 40)
f2d.create_line( 5, 40,   5,  0)
sk.close_edition()

# ── add_new_shaft（不立即 update）──
shaft = ppy.shape_factory.add_new_shaft(sk)

# ── 用 MainBody.HybridShapes.Item("Z 轴") 获取 Z 轴 Reference ──
print("尝试 body.HybridShapes.Item('Z 轴') ...")
try:
    z_obj = doc.Part.MainBody.HybridShapes.Item("Z 轴")
    z_ref_com = doc.Part.CreateReferenceFromObject(z_obj)
    print(f"  z_ref_com.DisplayName = {z_ref_com.DisplayName}")

    shaft.revolute_axis = PyRef(z_ref_com)
    print(f"  revolute_axis 设置成功")

    ppy.update()
    print(f"  update() 成功！旋转体: {shaft.name}")

    check = shaft.revolute_axis
    print(f"  验证 revolute_axis.DisplayName = {check.DisplayName}")

except Exception as e:
    import traceback
    print(f"  FAILED: {e}")
    traceback.print_exc()

print("\n完成。")
