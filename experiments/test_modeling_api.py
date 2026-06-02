"""
用 modeling.py 接口重跑端到端测试，验证新模块正常工作。
"""
import sys
sys.path.insert(0, ".")

from catia_copilot.catia.modeling import (
    create_part, add_sketch, draw_rect, add_pad, update_part, get_mass_props, list_features
)

def main():
    print("Step 1: create_part ...")
    part = create_part("TestModeling")
    print(f"  Part: {part.name}")

    print("Step 2: add_sketch(xy) ...")
    sk = add_sketch(part, "xy")
    print(f"  Sketch: {sk.name}")

    print("Step 3: draw_rect(0, 0, 100, 50) ...")
    draw_rect(sk, 0, 0, 100, 50)
    print("  done")

    print("Step 4: add_pad(depth=20) ...")
    pad = add_pad(part, sk, 20)
    print(f"  Pad: {pad.name}")

    print("Step 5: update_part ...")
    update_part(part)

    print("Step 6: list_features ...")
    feats = list_features(part)
    print(f"  features: {feats}")

    print("Step 7: get_mass_props ...")
    mp = get_mass_props(part)
    if mp:
        print(f"  mass={mp['mass']} kg  cog={[round(v,2) for v in mp['cog']]} mm")
    else:
        print("  未赋材料，跳过")

    print()
    print("=== modeling.py 端到端测试通过 ===")

if __name__ == "__main__":
    main()
