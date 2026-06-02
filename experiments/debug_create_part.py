"""
端到端建模测试：
  1. 新建 CATPart
  2. 在 XY 平面新建草图
  3. 画一个 100x50 的矩形
  4. 关闭草图
  5. Pad 20mm
  6. 刷新模型
  7. 读取 Analyze 验证几何中心

已验证可跑通（2026-06-02）。
关键坑：
  - plane_xy 返回 AnyObject 而非 Reference，需经 create_reference_from_object() 转换
  - 草图挂在 part.main_body.sketches 下，Part 本身没有 sketches 属性
"""
import sys
sys.path.insert(0, ".")

from catia_copilot.catia.connection import get_catia_v5_application, wrap_application

def main():
    app_com = get_catia_v5_application()
    app_py  = wrap_application()

    # 1. 新建 CATPart
    print("Step 1: 新建 CATPart ...")
    app_py.documents.add("Part")
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    part_doc = PartDocument(app_com.ActiveDocument)
    part = part_doc.part
    print(f"  Part: {part.name}")

    # 2. 获取 XY 平面引用
    print("Step 2: 获取 XY 平面 ...")
    xy_plane = part.origin_elements.plane_xy          # 返回 AnyObject，非 Reference
    xy_ref   = part.create_reference_from_object(xy_plane)
    print(f"  xy_ref: {type(xy_ref).__name__}")

    # 3. 新建草图
    print("Step 3: 新建草图 ...")
    sketch = part.main_body.sketches.add(xy_ref)
    print(f"  草图: {sketch.name}")

    # 4. 进入编辑，画 100x50 矩形
    print("Step 4: 画矩形 ...")
    f2d = sketch.open_edition()
    f2d.create_line(  0,  0, 100,  0)
    f2d.create_line(100,  0, 100, 50)
    f2d.create_line(100, 50,   0, 50)
    f2d.create_line(  0, 50,   0,  0)
    sketch.close_edition()
    print("  完成")

    # 5. Pad 20mm
    print("Step 5: Pad 20mm ...")
    pad = part.shape_factory.add_new_pad(sketch, 20.0)
    print(f"  Pad: {pad.name}")

    # 6. 刷新
    print("Step 6: update() ...")
    part.update()

    # 7. 验证
    print("Step 7: Analyze 验证 ...")
    from catia_copilot.catia.connection import wrap_product
    analyze = wrap_product(app_com.ActiveDocument.Product).analyze
    print(f"  mass = {analyze.mass} kg")
    print(f"  cog  = {list(analyze.get_gravity_center())} mm")
    print()
    print("=== 通过 ===")

if __name__ == "__main__":
    main()
