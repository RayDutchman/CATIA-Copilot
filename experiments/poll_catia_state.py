"""
CATIA COM 状态轮询 & Diff 工具
用途：轮询活动 Part 的所有可读 COM 属性，操作前后 diff 定位 Z 轴引用路径。

使用方式：
  1. 启动脚本，会每 3 秒打印一次快照
  2. 在 CATIA 里手动操作（如设置旋转轴）
  3. 脚本自动比较相邻快照的 diff
  4. Ctrl+C 退出，最终结果写入 poll_output.txt
"""

import sys, os, time, json, copy
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import win32com.client
from pycatia.mec_mod_interfaces.part_document import PartDocument
from pycatia.in_interfaces.reference import Reference as PyRef

catia = win32com.client.GetActiveObject("CATIA.Application")

OUTPUT = os.path.join(os.path.dirname(__file__), "poll_output.txt")
_all_lines = []

def log(msg=""):
    s = str(msg)
    try: print(s)
    except: print(repr(s))
    _all_lines.append(s)

def save():
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_all_lines))

# ─── 快照函数 ───────────────────────────────────────────────

def safe(fn):
    try:    return fn()
    except Exception as e: return f"ERR:{e}"

def snap_ref(ref_com):
    """把一个 COM Reference 对象转成可比较的字典"""
    if ref_com is None:
        return None
    d = {}
    # 已知属性
    for attr in ["Name", "DisplayName", "InternalName",
                 "GenericNaming", "Label", "ReferenceableType"]:
        try:    d[attr] = getattr(ref_com, attr)
        except: pass
    # PyRef 包装后尝试
    try:
        pr = PyRef(ref_com)
        for attr in ["display_name", "internal_name", "name"]:
            try:    d[f"py_{attr}"] = getattr(pr, attr)
            except: pass
    except: pass
    # 全量 dir() 枚举（首次出现时才有意义）
    try:
        all_attrs = [a for a in dir(ref_com) if not a.startswith("_")]
        d["_dir"] = all_attrs
        # 对每个非下划线属性尝试读值
        for attr in all_attrs:
            if attr in d:
                continue
            try:
                val = getattr(ref_com, attr)
                if callable(val):
                    continue  # 跳过方法
                d[f"_prop_{attr}"] = str(val)[:120]
            except:
                pass
    except: pass
    return d

def snap_shape(shape_com):
    """单个 Shape 特征的快照"""
    d = {"Name": safe(lambda: shape_com.Name),
         "Type": safe(lambda: shape_com.IsA("CATIAShape"))}
    # 旋转体/环形槽的 RevoluteAxis
    for attr in ["RevoluteAxis", "revolute_axis"]:
        try:
            ra = getattr(shape_com, attr)
            if ra is not None:
                d[f"revolute_axis"] = snap_ref(ra)
        except: pass
    # Pad/Pocket 的 depth
    for attr in ["FirstLimit", "Offset"]:
        try:
            lim = getattr(shape_com, attr)
            d[attr] = safe(lambda: lim.Dimension.Value)
        except: pass
    return d

def snapshot():
    """对活动文档做一次完整快照，返回可 JSON 序列化的字典"""
    snap = {}
    try:
        doc = catia.ActiveDocument
        snap["doc_name"] = safe(lambda: doc.Name)

        part_com = doc.Part

        # ── GeometricElements ──
        ge = part_com.GeometricElements
        ge_items = {}
        for i in range(1, ge.Count + 1):
            item = ge.Item(i)
            nm = safe(lambda: item.Name)
            # 尝试 CreateReferenceFromObject 并读 DisplayName
            try:
                ref = part_com.CreateReferenceFromObject(item)
                dn  = safe(lambda: ref.DisplayName)
            except:
                dn = "no-ref"
            ge_items[str(i)] = {"name": nm, "ref_display": dn}
        snap["GE"] = ge_items

        # ── OriginElements ──
        oe = part_com.OriginElements
        snap["OriginElements"] = {
            "PlaneXY": snap_ref(safe(lambda: oe.PlaneXY)),
            "PlaneYZ": snap_ref(safe(lambda: oe.PlaneYZ)),
            "PlaneZX": snap_ref(safe(lambda: oe.PlaneZX)),
        }

        # ── AxisSystems ──
        ax_sys = part_com.AxisSystems
        axis_systems = {}
        for i in range(1, ax_sys.Count + 1):
            ax = ax_sys.Item(i)
            entry = {"Name": safe(lambda: ax.Name)}
            # 尝试获取轴/原点
            for m in ["GetOrigin", "GetXAxis", "GetYAxis", "GetZAxis"]:
                try:
                    vals = list(getattr(ax, m)())
                    entry[m] = vals
                except: pass
            # 尝试 Ref 属性
            for m in ["OriginPoint", "XAxisDirection", "YAxisDirection",
                      "XAxisPoint", "YAxisPoint", "ZAxisPoint"]:
                try:
                    val = getattr(ax, m)
                    entry[m] = snap_ref(val)
                except: pass
            axis_systems[str(i)] = entry
        snap["AxisSystems"] = axis_systems

        # ── Shapes（MainBody）──
        try:
            shapes_com = part_com.MainBody.Shapes
            shapes = {}
            for i in range(1, shapes_com.Count + 1):
                sh = shapes_com.Item(i)
                nm = safe(lambda: sh.Name)
                entry = {"Name": nm}
                # revolve_axis / revolute_axis
                for attr in ["RevoluteAxis"]:
                    try:
                        ra = getattr(sh, attr)
                        entry["RevoluteAxis"] = snap_ref(ra)
                        # 尝试更多信息
                        if ra is not None:
                            entry["RevoluteAxis_type"] = safe(lambda: type(ra).__name__)
                            for sub in ["InternalName", "DisplayName", "Name"]:
                                try:
                                    entry[f"RevoluteAxis.{sub}"] = getattr(ra, sub)
                                except: pass
                    except: pass
                shapes[str(i)] = entry
            snap["Shapes"] = shapes
        except Exception as e:
            snap["Shapes"] = f"ERR:{e}"

        # ── HybridBodies ──
        try:
            hbs = part_com.HybridBodies
            hb_snap = {}
            for i in range(1, hbs.Count + 1):
                hb = hbs.Item(i)
                nm = safe(lambda: hb.Name)
                # HybridShapes 内的项
                try:
                    hss = hb.HybridShapes
                    items = [safe(lambda: hss.Item(j).Name) for j in range(1, hss.Count+1)]
                except:
                    items = []
                hb_snap[str(i)] = {"Name": nm, "HybridShapes": items}
            snap["HybridBodies"] = hb_snap
        except Exception as e:
            snap["HybridBodies"] = f"ERR:{e}"

        # ── Sketches：列出所有草图名 + 其绝对轴数据 ──
        try:
            sketches_com = part_com.MainBody.Sketches
            sk_snap = {}
            for i in range(1, sketches_com.Count + 1):
                sk = sketches_com.Item(i)
                nm = safe(lambda: sk.Name)
                try:
                    ax_data = list(sk.GetAbsoluteAxisData())
                except:
                    ax_data = "ERR"
                sk_snap[str(i)] = {"Name": nm, "AbsoluteAxisData": ax_data}
            snap["Sketches"] = sk_snap
        except Exception as e:
            snap["Sketches"] = f"ERR:{e}"

    except Exception as e:
        snap["_top_error"] = str(e)

    return snap

# ─── Diff 函数 ─────────────────────────────────────────────

def flatten(d, prefix=""):
    """把嵌套 dict 展平为 {path: value}"""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list):
        out[prefix] = repr(d)
    else:
        out[prefix] = repr(d)
    return out

def diff_snaps(a, b):
    fa, fb = flatten(a), flatten(b)
    keys = sorted(set(fa) | set(fb))
    lines = []
    for k in keys:
        va, vb = fa.get(k, "<missing>"), fb.get(k, "<missing>")
        if va != vb:
            lines.append(f"  CHANGED  {k}")
            lines.append(f"    before: {va}")
            lines.append(f"    after:  {vb}")
    return lines

# ─── 主循环 ─────────────────────────────────────────────────

INTERVAL = 3  # 秒
prev_snap = None
snap_count = 0

log("=" * 60)
log("CATIA COM 状态轮询启动")
log(f"间隔: {INTERVAL}s | Ctrl+C 退出")
log("=" * 60)

try:
    while True:
        snap_count += 1
        ts = time.strftime("%H:%M:%S")
        cur = snapshot()

        log(f"\n── 快照 #{snap_count}  {ts} ──")
        # 打印关键摘要
        log(f"  doc: {cur.get('doc_name','?')}")
        shapes = cur.get("Shapes", {})
        if isinstance(shapes, dict):
            for k, v in shapes.items():
                ra = v.get('RevoluteAxis')
                log(f"  Shape[{k}] {v.get('Name','?')}  RevoluteAxis={ra}")
                if isinstance(ra, dict):
                    for rk, rv in ra.items():
                        if not rk.startswith("_"):
                            log(f"    {rk}: {rv}")
        ge = cur.get("GE", {})
        if isinstance(ge, dict):
            for k, v in ge.items():
                log(f"  GE[{k}] {v.get('name','?')}  ref={v.get('ref_display','?')}")

        # Diff
        if prev_snap is not None:
            diffs = diff_snaps(prev_snap, cur)
            if diffs:
                log(f"\n  *** DIFF（相比快照 #{snap_count-1}）***")
                for d in diffs:
                    log(d)
            else:
                log("  （无变化）")

        prev_snap = copy.deepcopy(cur)
        save()
        time.sleep(INTERVAL)

except KeyboardInterrupt:
    log("\n\n轮询已停止。")
    save()
    print(f"\n结果已保存至: {OUTPUT}")
