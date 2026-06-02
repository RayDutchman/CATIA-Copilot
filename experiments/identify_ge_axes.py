"""
GE 直线项 Z 轴识别测试（修复版）
目标：确认 GeometricElements 中哪个 直线.* 项是 Z 轴，
      可以直接用作 Shaft.revolute_axis，无需建 HybridBody。

修复说明：
  原版使用 doc_base 的 GE 引用传给不同 Part 的 Shaft，
  CATIA 不允许跨文档引用，导致全部失败。
  修复版在同一个 Part 内完成：
    1. 建 Pad（触发 GE 填充）
    2. 对每个 GE 直线项，建 ZX 草图 + add_new_shaft，用同 Part 的 GE 项测试
    3. update() 后检查是否成功，然后 undo 或关闭重建
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

OUTPUT = os.path.join(os.path.dirname(__file__), "ge_axes_output.txt")
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

log("GE 直线项 Z 轴识别（修复版）")
log("=" * 50)

def new_part(name):
    app_py.documents.add("Part")
    doc = catia.ActiveDocument
    ppy = PartDocument(doc).part
    try: ppy.part.PartNumber = name
    except: pass
    return doc, ppy

def plane_ref(ppy, plane):
    o = ppy.origin_elements
    return ppy.create_reference_from_object(
        {"xy": o.plane_xy, "yz": o.plane_yz, "zx": o.plane_zx}[plane])

# ── 步骤1：先建基础 Part 了解 GE 结构 ──
log("步骤1：建含 Pad 的 Part，读取 GE 项")
doc_info, ppy_info = new_part("GE_Info")
sk0 = ppy_info.main_body.sketches.add(plane_ref(ppy_info, "xy"))
f2d = sk0.open_edition()
f2d.create_line(0,0,50,0); f2d.create_line(50,0,50,50)
f2d.create_line(50,50,0,50); f2d.create_line(0,50,0,0)
sk0.close_edition()
ppy_info.shape_factory.add_new_pad(sk0, 20)
ppy_info.update()

ge_info = doc_info.Part.GeometricElements
log(f"GE.Count = {ge_info.Count}")
for i in range(1, ge_info.Count + 1):
    try:    nm = ge_info.Item(i).Name
    except: nm = "?"
    log(f"  GE[{i:2d}] name={repr(nm)}")

catia.ActiveDocument.Close()  # 关闭信息 Part

log("")
log("步骤2：逐 GE 索引测试 Shaft revolute_axis（同 Part 内）")
log("=" * 50)

# 每次测试：新建 Part → Pad（填充 GE）→ 建 Shaft → 用 GE[idx] → update
# 在同一个 Part 内，Pad 在前，Shaft 在后

for idx in range(1, 17):  # 测试索引 1~16（GE 固定项范围）
    try:
        app_py.documents.add("Part")
        doc_t = catia.ActiveDocument
        ppy_t = PartDocument(doc_t).part

        # 建 Pad（让 GE 有内容）
        sk_pad = ppy_t.main_body.sketches.add(plane_ref(ppy_t, "xy"))
        f2d = sk_pad.open_edition()
        f2d.create_line(0,0,50,0); f2d.create_line(50,0,50,50)
        f2d.create_line(50,50,0,50); f2d.create_line(0,50,0,0)
        sk_pad.close_edition()
        ppy_t.shape_factory.add_new_pad(sk_pad, 20)
        ppy_t.update()

        # 检查 GE 是否有该索引
        ge_t = doc_t.Part.GeometricElements
        if idx > ge_t.Count:
            log(f"  GE[{idx:2d}]  索引超出范围（GE.Count={ge_t.Count}）")
            catia.ActiveDocument.Close()
            continue

        ge_item = ge_t.Item(idx)
        try:    item_name = ge_item.Name
        except: item_name = "?"

        # 建 ZX 平面草图（旋转体轮廓，V≥0 侧）
        sk_s = ppy_t.main_body.sketches.add(plane_ref(ppy_t, "zx"))
        f2d  = sk_s.open_edition()
        # ZX 平面：H=Z，V=X；轮廓在 V>0（即 X>0）侧
        f2d.create_line(0, 5,  0, 25)   # H=Z, V=X
        f2d.create_line(0, 25, 40, 25)
        f2d.create_line(40, 25, 40, 5)
        f2d.create_line(40, 5,  0, 5)
        sk_s.close_edition()

        # 建 Shaft（不立即 update）
        shaft = ppy_t.shape_factory.add_new_shaft(sk_s)

        # 用同 Part 的 GE[idx] 作为 revolute_axis
        # ge_t / ge_item 来自 doc_t.Part（COM），直接用 COM CreateReferenceFromObject
        item_ref = PyRef(doc_t.Part.CreateReferenceFromObject(ge_item))
        shaft.revolute_axis = item_ref

        # update
        ppy_t.update()
        log(f"  GE[{idx:2d}] '{item_name}'  → Update OK  ← 候选旋转轴!")

    except Exception as ex:
        err = str(ex)
        try:    item_name_err = doc_t.Part.GeometricElements.Item(idx).Name if idx <= doc_t.Part.GeometricElements.Count else "?"
        except: item_name_err = "?"
        short = err[:80].replace("\n", " ")
        log(f"  GE[{idx:2d}] '{item_name_err}'  FAILED: {short}")

    # 关闭测试 Part（不保存）
    try:
        catia.ActiveDocument.Close()
    except:
        pass

log("")
log("=" * 50)
log("测试完成。Update OK 的 GE 索引即为可用旋转轴。")
save()
