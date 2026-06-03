"""
尝试通过 Selection.Search 找到 Z 轴 Reference，
以及 Part.FindObjectByName / CreateReferenceFromBRepName 等方法。

前提：CATIA 里有一个含旋转体且旋转轴已设为 Z 轴的 Part，保持活动。
"""
import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import win32com.client
from pycatia.in_interfaces.reference import Reference as PyRef

catia = win32com.client.GetActiveObject("CATIA.Application")
doc   = catia.ActiveDocument
part  = doc.Part

def sep(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)

# ── 1. Selection.Search ──────────────────────────────
sep("1. Selection.Search('Name=Z 轴,all')")
try:
    sel = doc.Selection
    sel.Clear()
    sel.Search("Name=Z 轴,all")
    print(f"  找到 {sel.Count} 个结果")
    for i in range(1, sel.Count + 1):
        item = sel.Item2(i)
        try:    ref = item.Reference
        except: ref = None
        try:    val = item.Value
        except: val = None
        print(f"  [{i}] Value={val}  ref={ref}")
        if ref is not None:
            print(f"       ref.DisplayName={ref.DisplayName}")
            # 尝试把这个 ref 当作 revolute_axis
            # （先打印，不直接赋值）
except Exception as e:
    print(f"  FAILED: {e}")

# ── 2. Part.FindObjectByName ──────────────────────────
sep("2. Part.FindObjectByName('Z 轴')")
try:
    obj = part.FindObjectByName("Z 轴")
    print(f"  找到: {obj}")
    print(f"  Name={obj.Name}")
    try:    print(f"  DisplayName={obj.DisplayName}")
    except: pass
    ref = part.CreateReferenceFromObject(obj)
    print(f"  ref.DisplayName={ref.DisplayName}")
except Exception as e:
    print(f"  FAILED: {e}")

# ── 3. CreateReferenceFromBRepName 穷举已知格式 ──────
sep("3. CreateReferenceFromBRepName 穷举")
candidates = [
    "Z 轴",
    "RSur:(Face:(Brp:(Z Axis;None:());None:();Cf8:()))",
    "REdge:(Brp:(Z Axis;None:());None:())",
    "Edge:(Brp:(Z Axis);None:())",
    "Z Axis",
]
for name in candidates:
    try:
        ref = part.CreateReferenceFromBRepName(name, part)
        dn  = ref.DisplayName
        print(f"  '{name}' → DisplayName={dn}  ← 成功!")
    except Exception as e:
        short = str(e)[:60]
        print(f"  '{name}' → FAILED: {short}")

# ── 4. 读取已设好的 revolute_axis，用它建新 Part 里的 Shaft ──
sep("4. 直接用当前 Part 的 Z 轴 ref 赋给新 Shaft")
try:
    shapes = part.MainBody.Shapes
    shaft_com = None
    for i in range(1, shapes.Count + 1):
        sh = shapes.Item(i)
        if sh.IsA("Shaft") or "旋转" in sh.Name or "Shaft" in sh.Name:
            shaft_com = sh
            break
    if shaft_com is None:
        print("  未找到旋转体特征")
    else:
        z_ref = shaft_com.RevoluteAxis
        print(f"  当前 revolute_axis.DisplayName = {z_ref.DisplayName}")
        # 尝试重新赋值（验证该 ref 可复用）
        shaft_com.RevoluteAxis = z_ref
        part.Update()
        print("  重新赋值 + Update 成功 ← 说明 ref 对象可复用!")
        # 现在尝试从不同入口重新构造相同 ref
        # 方法：把 ref 传入 ComposeWith(part_ref)
        try:
            part_ref = part.CreateReferenceFromObject(part)
            composed = z_ref.ComposeWith(part_ref)
            print(f"  ComposeWith(part_ref).DisplayName = {composed.DisplayName}")
        except Exception as e2:
            print(f"  ComposeWith FAILED: {e2}")
except Exception as e:
    print(f"  FAILED: {e}")

# ── 5. 通过草图 V 轴（纵向）构造 ──────────────────────
sep("5. 用 ZX 平面草图的 GE[3](纵向=Z方向) 作为 revolute_axis")
try:
    ge = doc.Part.GeometricElements
    print(f"  GE.Count = {ge.Count}")
    for i in range(1, ge.Count + 1):
        try:    nm = ge.Item(i).Name
        except: nm = "?"
        print(f"  GE[{i}] = {nm}")
    # 找 纵向（V轴=Z）
    for i in range(1, ge.Count + 1):
        try:
            item = ge.Item(i)
            if item.Name == "纵向":
                print(f"  找到 GE[{i}] = '纵向'，尝试作为 revolute_axis ...")
                ref = PyRef(part.CreateReferenceFromObject(item))
                # 找旋转体
                shapes = part.MainBody.Shapes
                for j in range(1, shapes.Count + 1):
                    sh = shapes.Item(j)
                    try:
                        sh.RevoluteAxis = ref
                        part.Update()
                        dn = sh.RevoluteAxis.DisplayName
                        print(f"  GE[{i}]('纵向') → Update OK, DisplayName={dn}")
                    except Exception as e2:
                        print(f"  GE[{i}]('纵向') → Update FAILED: {e2}")
                break
        except: pass
except Exception as e:
    print(f"  FAILED: {e}")

print("\n完成。")
