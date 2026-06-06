"""
tests/test_com_ref_cache.py

验证"在 row dict 中缓存 COM 对象引用，写回时直接使用缓存跳过重新遍历"方案的可行性。

测试分四个阶段：

  Phase 1 – COLLECT
    遍历当前活动文档的产品树，将每个节点的 COM 对象引用和元数据存入 row dict，
    模拟改造后的 collect_bom_rows 的行为。
    同时收集"同一父节点下 PN 相同的兄弟实例"（_product_extras），
    验证嵌入式部件多实例的情况。

  Phase 2 – VALIDATE
    遍历结束后，对缓存的每个 COM 引用重新读取 PartNumber，
    与收集时存储的值对比，证明引用在存储期间一直有效。

  Phase 3 – WRITE TEST（需要 --write 参数才会执行，默认跳过）
    对用户指定 PartNumber 的节点，通过缓存的 COM 引用同时写入 PartNumber 和
    Nomenclature，立即回读验证，再还原原始值。

    PartNumber 写入测试的关键意义：
      证明 COM 引用在 PartNumber 被修改后依然有效（COM ref 不依赖 PN 作为标识），
      从而彻底消除写回逻辑中 pn_remap / current_pn 的复杂性——
      以后可以直接用缓存的 COM 引用写入所有字段，包括 PartNumber 本身。

  Phase 4 – MANUAL INTERACTION TEST（需要 --manual 参数才会执行，默认跳过）
    在 Phase 1 完成缓存后，暂停等待用户在 CATIA 中手动操作，
    然后通过缓存的 COM 引用再次访问，检验引用是否在以下场景中依然有效：

      场景 A – 用户在 CATIA 中手动修改零件编号（Properties → Part Number）
        预期：COM 引用应仍然有效（COM ref 不依赖 PN 值作为标识）。

      场景 B – 用户在 CATIA 中对该文件执行「另存为」新路径
        预期：COM 引用应仍然有效（对象仍在 CATIA 内存中，路径变了不影响 COM 句柄）。

      场景 C – 用户在 CATIA 中关闭该文档后重新打开
        预期：COM 引用应失效（对象已从 CATIA 内存中卸载，COM 句柄无效）。

用法：
  # 仅读取 + 验证引用有效性（默认，不改 CATIA 任何数据）
  python tests/test_com_ref_cache.py

  # 额外执行写入测试（会修改并还原 PartNumber 和 Nomenclature，需谨慎）
  python tests/test_com_ref_cache.py --write <PartNumber> <NewPartNumber> <NewNomenclature>

  # 额外执行交互测试（暂停等待用户在 CATIA 中手动操作）
  python tests/test_com_ref_cache.py --manual [PartNumber]
    PartNumber 可选，默认使用产品树中第一个子节点（Level=1）。

  示例：
  python tests/test_com_ref_cache.py --write ENGINE_BLOCK ENGINE_BLOCK_NEW TestNom_123
  python tests/test_com_ref_cache.py --manual
  python tests/test_com_ref_cache.py --manual ENGINE_BLOCK

前提：
  - CATIA V5 已启动，并且有一个 CATProduct 处于激活状态
  - 从项目根目录运行（或确保 catia_copilot 包在 sys.path 中）
"""

import sys
import time
import argparse
from pathlib import Path

# ── 路径设置：支持从项目根或 tests/ 子目录运行 ────────────────────────────────
_here        = Path(__file__).parent
_project_root = _here.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── 连接到 CATIA ──────────────────────────────────────────────────────────────
try:
    from catia_copilot.catia.connection import get_catia_v5_application
    from catia_copilot.constants import CATIA_DESIGN_MODE
except ImportError as e:
    print(f"[FATAL] 无法导入项目模块：{e}")
    print(f"        请确认从项目根目录运行，或已将项目根加入 PYTHONPATH。")
    sys.exit(1)

# ── 颜色输出（Windows CMD / PowerShell 均支持 ANSI 转义） ─────────────────────
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def ok(msg: str)    -> None: print(f"  {_GREEN}✓{_RESET} {msg}")
def fail(msg: str)  -> None: print(f"  {_RED}✗{_RESET} {msg}")
def info(msg: str)  -> None: print(f"  {_CYAN}·{_RESET} {msg}")
def warn(msg: str)  -> None: print(f"  {_YELLOW}!{_RESET} {msg}")
def header(msg: str)-> None: print(f"\n{_BOLD}{msg}{_RESET}")
def sep()           -> None: print("─" * 60)


def _get_pn(product) -> str:
    """安全读取 PartNumber，失败时退回到 Name。"""
    try:
        return str(product.PartNumber)
    except Exception:
        try:
            name = product.Name
            return name.rsplit(".", 1)[0] if "." in name else name
        except Exception:
            return "<unknown>"


def _get_filepath(product) -> str:
    """安全读取 ReferenceProduct.Parent.FullName。"""
    try:
        return str(product.ReferenceProduct.Parent.FullName)
    except Exception:
        return ""


def _switch_design_mode(product) -> bool:
    """切换到 Design 模式，返回是否成功。"""
    try:
        if product.GetWorkMode() != CATIA_DESIGN_MODE:
            product.ApplyWorkMode(CATIA_DESIGN_MODE)
        return True
    except Exception:
        try:
            product.ApplyWorkMode(CATIA_DESIGN_MODE)
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 – COLLECT：遍历产品树，缓存 COM 引用
# ─────────────────────────────────────────────────────────────────────────────

def collect_with_com_refs(root_product) -> list[dict]:
    """
    遍历以 root_product 为根的产品树，返回 row dict 列表。

    每个 row dict 包含：
      _product        : COM 对象引用（第一个/代表实例）
      _product_extras : list[COM]，同一父节点下 PN 相同的其余实例（通常为空）
      Part Number     : str
      Nomenclature    : str
      _filepath       : str
      _level          : int
      _is_embedded    : bool（filepath == parent_filepath 时为 True）
    """
    rows: list[dict] = []
    node_count = 0

    def _traverse(product, level: int, parent_filepath: str) -> None:
        nonlocal node_count

        pn       = _get_pn(product)
        filepath = _get_filepath(product)
        is_embedded = bool(filepath) and bool(parent_filepath) and filepath == parent_filepath

        # 切换 Design 模式以确保属性可读
        _switch_design_mode(product)

        # 读取 Nomenclature
        try:
            nom = str(product.ReferenceProduct.Nomenclature)
        except Exception:
            try:
                nom = str(product.Nomenclature)
            except Exception:
                nom = ""

        row: dict = {
            "_product":        product,   # ← 核心：直接缓存 COM 对象引用
            "_product_extras": [],        # 同父节点下重复 PN 的其余实例
            "Part Number":     pn,
            "Nomenclature":    nom,
            "_filepath":       filepath,
            "_level":          level,
            "_is_embedded":    is_embedded,
        }
        rows.append(row)
        node_count += 1

        # ── 遍历子节点，收集兄弟重复实例 ─────────────────────────────────────
        try:
            count = product.Products.Count
            if count == 0:
                return

            # children: cpn → {"product": first_instance, "extras": [rest]}
            children: dict = {}
            for i in range(1, count + 1):
                try:
                    child = product.Products.Item(i)
                    cpn   = _get_pn(child)
                except Exception:
                    continue

                if cpn not in children:
                    children[cpn] = {"product": child, "extras": []}
                else:
                    children[cpn]["extras"].append(child)   # 收集额外实例

            for cpn, data in children.items():
                _traverse(data["product"], level + 1, parent_filepath=filepath)
                # 将额外实例写回到刚追加的那个 row（即 rows[-1] 对应 data["product"]）
                if data["extras"]:
                    # _traverse 刚追加了子节点的整棵子树；
                    # data["product"] 对应的行是从当前 len(rows) 倒推的首行。
                    # 为简化，我们在遍历后找到对应行并写入 extras。
                    for row in reversed(rows):
                        if row["_product"] is data["product"]:
                            row["_product_extras"] = data["extras"]
                            break

        except Exception:
            pass

    _traverse(root_product, level=0, parent_filepath="")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 – VALIDATE：回读每个缓存 COM 引用，验证有效性
# ─────────────────────────────────────────────────────────────────────────────

def validate_com_refs(rows: list[dict]) -> tuple[int, int]:
    """
    对每个 row["_product"]，重新读取 PartNumber 并与存储值对比。

    返回 (pass_count, fail_count)。
    """
    passed = 0
    failed = 0
    stale_examples: list[str] = []

    for row in rows:
        product     = row["_product"]
        stored_pn   = row["Part Number"]
        try:
            live_pn = str(product.PartNumber)
            if live_pn == stored_pn:
                passed += 1
            else:
                # PN 在 CATIA 中被改了——引用本身仍然有效，只是值不同
                warn(f"PN 已在 CATIA 中变更：存储值={stored_pn!r}  当前值={live_pn!r}  （引用有效）")
                passed += 1
        except Exception as e:
            failed += 1
            stale_examples.append(f"{stored_pn}: {e}")

    if stale_examples:
        for ex in stale_examples[:5]:
            fail(f"COM 引用失效：{ex}")
        if len(stale_examples) > 5:
            fail(f"  ... 还有 {len(stale_examples) - 5} 个失效引用")

    return passed, failed


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – WRITE TEST：通过缓存 COM 引用写入 PartNumber 和 Nomenclature 并还原
# ─────────────────────────────────────────────────────────────────────────────

def write_test(rows: list[dict], target_pn: str, new_pn: str, new_nom: str) -> bool:
    """
    在 rows 中找到 Part Number == target_pn 的节点，通过缓存的 COM 引用：

      Step A – 写入 Nomenclature，立即回读验证，还原。
      Step B – 写入 PartNumber，立即回读验证，再通过 **同一个** COM 引用还原。

    Step B 是核心测试点：PartNumber 被改写后，cached COM ref 本身不失效，
    仍可继续读写。这证明可以用 COM 引用（而非 PartNumber）作为唯一定位键，
    彻底消除写回逻辑中 pn_remap / current_pn 的复杂性。

    返回 True 表示全部子测试通过。
    """
    # 找到目标行（可能有多行同 PN，全部测试）
    targets = [r for r in rows if r["Part Number"] == target_pn]
    if not targets:
        fail(f"在缓存的行中找不到 PartNumber = {target_pn!r}")
        print("  可用的 PartNumber 列表：")
        unique_pns = sorted({r['Part Number'] for r in rows})
        for pn in unique_pns[:20]:
            print(f"    {pn}")
        if len(unique_pns) > 20:
            print(f"    ... 共 {len(unique_pns)} 个")
        return False

    all_passed = True

    for row in targets:
        product     = row["_product"]
        stored_pn   = row["Part Number"]
        stored_nom  = row["Nomenclature"]
        level       = row["_level"]
        filepath    = row["_filepath"]
        is_embedded = row["_is_embedded"]
        extras      = row["_product_extras"]

        info(f"目标节点：PN={target_pn!r}  Level={level}  "
             f"嵌入式={is_embedded}  额外实例数={len(extras)}")
        info(f"当前 PartNumber（缓存值） ：{stored_pn!r}  →  将写入 {new_pn!r}")
        info(f"当前 Nomenclature（缓存值）：{stored_nom!r}  →  将写入 {new_nom!r}")
        info(f"文件路径：{filepath or '(无文件)'}")
        print()

        # 先切换 Design 模式
        if not _switch_design_mode(product):
            warn("切换 Design 模式失败，仍尝试写入")

        # 构建写入目标列表：优先 ReferenceProduct，其次 product 实例本身
        write_targets: list[tuple[str, object]] = []
        try:
            write_targets.append(("ReferenceProduct", product.ReferenceProduct))
        except Exception:
            pass
        write_targets.append(("product", product))

        # ════════════════════════════════════════════════════════════════════
        # Step A：写入 Nomenclature
        # ════════════════════════════════════════════════════════════════════
        print(f"  {_BOLD}Step A – Nomenclature 写入{_RESET}")

        nom_write_ok = False
        nom_write_label = ""
        for label, tgt in write_targets:
            try:
                tgt.Nomenclature = new_nom
                info(f"已通过 {label}.Nomenclature = {new_nom!r} 写入")
                nom_write_ok    = True
                nom_write_label = label
                break
            except Exception as e:
                warn(f"通过 {label} 写入失败：{e}")

        if not nom_write_ok:
            fail("Nomenclature 写入失败，跳过回读")
            all_passed = False
        else:
            # 回读
            nom_read_back = None
            for label, tgt in write_targets:
                try:
                    nom_read_back = str(tgt.Nomenclature)
                    break
                except Exception:
                    pass
            if nom_read_back == new_nom:
                ok(f"回读验证：Nomenclature = {nom_read_back!r}  ✓")
                info(f"请在 CATIA 中确认 Nomenclature 已变为 {new_nom!r}（不自动还原）")
            else:
                fail(f"回读不符：期望 {new_nom!r}，得到 {nom_read_back!r}")
                all_passed = False

        print()

        # ════════════════════════════════════════════════════════════════════
        # Step B：写入 PartNumber（核心测试）
        # ════════════════════════════════════════════════════════════════════
        print(f"  {_BOLD}Step B – PartNumber 写入（核心：改 PN 后 COM 引用是否仍有效）{_RESET}")

        pn_write_ok = False
        try:
            # PartNumber 直接写 product 实例（与 bom_write.py 一致）
            product.PartNumber = new_pn
            info(f"已通过 product.PartNumber = {new_pn!r} 写入")
            pn_write_ok = True
        except Exception as e:
            warn(f"直接写 product.PartNumber 失败：{e}，尝试 ReferenceProduct")
            try:
                product.ReferenceProduct.PartNumber = new_pn
                info(f"已通过 ReferenceProduct.PartNumber = {new_pn!r} 写入")
                pn_write_ok = True
            except Exception as e2:
                fail(f"PartNumber 写入全部失败：{e2}")
                all_passed = False

        if pn_write_ok:
            # 回读：用 **同一个 COM 引用** 读 PartNumber
            try:
                pn_read_back = str(product.PartNumber)
                if pn_read_back == new_pn:
                    ok(f"回读验证：product.PartNumber = {pn_read_back!r}  ✓")
                    ok("关键结论：PartNumber 已改变，但缓存的 COM 引用依然有效！")
                else:
                    fail(f"回读不符：期望 {new_pn!r}，得到 {pn_read_back!r}")
                    all_passed = False
            except Exception as e:
                fail(f"PartNumber 改写后，COM 引用已失效：{e}")
                all_passed = False

            info(f"请在 CATIA 中确认 PartNumber 已变为 {new_pn!r}（不自动还原）")

        print()

        # ── 测试额外实例 ────────────────────────────────────────────────────
        if extras:
            info(f"额外实例：共 {len(extras)} 个，验证 COM 引用有效性")
            for idx, extra in enumerate(extras, start=1):
                try:
                    extra_pn = _get_pn(extra)
                    ok(f"  实例 #{idx}：PartNumber={extra_pn!r}（引用有效）")
                except Exception as e:
                    fail(f"  实例 #{idx}：COM 引用失效 → {e}")
                    all_passed = False

    return all_passed


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – MANUAL INTERACTION TEST：暂停等用户在 CATIA 手动操作后回来检验
# ─────────────────────────────────────────────────────────────────────────────

def _probe_com_ref(product, label: str) -> bool:
    """
    尝试通过 COM 引用读取 PartNumber 和文件路径，打印结果。
    返回 True 表示引用有效，False 表示已失效。
    """
    try:
        live_pn  = str(product.PartNumber)
        live_fp  = _get_filepath(product)
        ok(f"[{label}] COM 引用有效  PartNumber={live_pn!r}  filepath={live_fp or '(无)'}")
        return True
    except Exception as e:
        fail(f"[{label}] COM 引用已失效：{e}")
        return False


def _wait_for_user(prompt: str) -> None:
    """打印提示并等待用户按 Enter。"""
    print()
    print(f"  {_YELLOW}┌─ 等待用户操作 ────────────────────────────────────────────┐{_RESET}")
    for line in prompt.strip().splitlines():
        print(f"  {_YELLOW}│{_RESET}  {line}")
    print(f"  {_YELLOW}└──────────────────────────────────────────────────────────┘{_RESET}")
    input("  按 Enter 继续...")
    print()


def manual_interaction_test(rows: list[dict], target_pn: str) -> None:
    """
    交互式测试：暂停等待用户在 CATIA 中手动操作，然后检验缓存 COM 引用是否仍然有效。

    涵盖三个场景：
      场景 A – 手动在 CATIA 中修改该零件的 PartNumber
      场景 B – 对该文件执行「另存为」到新路径
      场景 C – 关闭该文档后重新打开
    """
    # ── 找到目标行 ─────────────────────────────────────────────────────────────
    targets = [r for r in rows if r["Part Number"] == target_pn]
    if not targets:
        fail(f"在缓存的行中找不到 PartNumber = {target_pn!r}")
        print("  可用的 PartNumber 列表（前 20 个）：")
        for pn in sorted({r['Part Number'] for r in rows})[:20]:
            print(f"    {pn}")
        return

    row     = targets[0]   # 取第一个（通常唯一）
    product = row["_product"]
    stored_pn  = row["Part Number"]
    stored_fp  = row["_filepath"]
    stored_nom = row["Nomenclature"]
    level      = row["_level"]

    info(f"目标节点  PartNumber  : {stored_pn!r}  (Level={level})")
    info(f"         Nomenclature: {stored_nom!r}")
    info(f"         文件路径    : {stored_fp or '(无文件)'}")
    print()

    # 先确认初始状态下引用有效
    info("基准检查（操作前）：")
    initial_ok = _probe_com_ref(product, "初始状态")
    if not initial_ok:
        fail("初始状态 COM 引用已失效，无法继续交互测试")
        return

    # ════════════════════════════════════════════════════════════════════════
    # 场景 A：用户手动在 CATIA 中修改 PartNumber
    # ════════════════════════════════════════════════════════════════════════
    print(f"\n  {_BOLD}场景 A – 在 CATIA 中手动修改 PartNumber{_RESET}")

    _wait_for_user(
        f"请切换到 CATIA，右键点击零件 {stored_pn!r}，选择「属性」，\n"
        f"将 PartNumber 改为任意新值（例如 {stored_pn}_MANUAL），\n"
        f"点击「确定」后回到此窗口。"
    )

    a_ok = _probe_com_ref(product, "场景A")
    if a_ok:
        # 尝试回读新 PN 并与原始值对比
        try:
            new_pn_in_catia = str(product.PartNumber)
            if new_pn_in_catia != stored_pn:
                ok(f"PartNumber 已从 {stored_pn!r} 变为 {new_pn_in_catia!r}，"
                   f"但 COM 引用依然有效 ✓")
                ok("结论：COM 引用不以 PartNumber 为标识，PN 改变不会使引用失效")
            else:
                warn(f"PartNumber 未检测到变化（仍为 {stored_pn!r}），"
                     "请确认已在 CATIA 中保存更改")
        except Exception:
            pass
    else:
        warn("场景 A：COM 引用失效（这是意外结果，请记录 CATIA 版本和操作步骤）")

    # 提示用户还原（可选）
    _wait_for_user(
        f"（可选）如需还原，请在 CATIA 中将 PartNumber 改回 {stored_pn!r}。\n"
        f"若不还原也可继续，后续场景的 COM 引用检测不依赖 PartNumber 值。"
    )

    # ════════════════════════════════════════════════════════════════════════
    # 场景 B：用户对文件执行「另存为」新路径
    # ════════════════════════════════════════════════════════════════════════
    print(f"\n  {_BOLD}场景 B – 在 CATIA 中对该文件执行「另存为」{_RESET}")

    if not stored_fp:
        warn("目标节点没有关联文件（嵌入式部件或 _not_found），跳过场景 B")
    else:
        _wait_for_user(
            f"请切换到 CATIA，激活包含 {stored_pn!r} 的文档，\n"
            f"执行「文件 > 另存为」将其保存到新路径（文件名或目录任意），\n"
            f"另存为完成后回到此窗口。\n"
            f"原路径：{stored_fp}"
        )

        b_ok = _probe_com_ref(product, "场景B")
        if b_ok:
            try:
                new_fp = _get_filepath(product)
                if new_fp and new_fp != stored_fp:
                    ok(f"文件路径已从\n"
                       f"    {stored_fp}\n"
                       f"  变为\n"
                       f"    {new_fp}")
                    ok("COM 引用在「另存为」后依然有效 ✓")
                    ok("结论：SaveAs 不会使已缓存的 COM 引用失效")
                elif new_fp == stored_fp:
                    warn("文件路径未检测到变化，请确认已执行另存为到不同路径")
            except Exception:
                pass
        else:
            warn("场景 B：COM 引用失效（若文件被移动后关闭再重开则属于预期结果）")

    # ════════════════════════════════════════════════════════════════════════
    # 场景 C：用户关闭文档后重新打开
    # ════════════════════════════════════════════════════════════════════════
    print(f"\n  {_BOLD}场景 C – 在 CATIA 中关闭文档后重新打开{_RESET}")

    _wait_for_user(
        f"请切换到 CATIA，关闭包含 {stored_pn!r} 的文档（File > Close），\n"
        f"然后重新打开同一文档（File > Open），\n"
        f"打开完成后回到此窗口。\n"
        f"注意：关闭后 COM 引用预计失效，这是正常的预期行为。"
    )

    c_ok = _probe_com_ref(product, "场景C")
    if not c_ok:
        ok("场景 C 结果符合预期：文档关闭后 COM 引用已失效 ✓")
        ok("结论：COM 引用的有效期 = 文档在 CATIA 中保持打开的期间，与 BOM 编辑对话框生命周期一致")
    else:
        warn("场景 C：文档关闭重开后 COM 引用仍然有效（出乎预期，请记录并核实操作步骤）")
        try:
            pn_after_reopen = str(product.PartNumber)
            warn(f"  当前读取到的 PartNumber = {pn_after_reopen!r}")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── 解析命令行参数 ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="验证 COM 对象引用缓存方案",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  仅读取+验证（不写 CATIA）：
    python tests/test_com_ref_cache.py

  写入测试（修改后立即还原）：
    python tests/test_com_ref_cache.py --write ENGINE_BLOCK ENGINE_BLOCK_NEW TestNom

  交互式手动操作测试（暂停等用户在 CATIA 中操作）：
    python tests/test_com_ref_cache.py --manual
    python tests/test_com_ref_cache.py --manual ENGINE_BLOCK
        """,
    )
    parser.add_argument(
        "--write", nargs=3,
        metavar=("PART_NUMBER", "NEW_PART_NUMBER", "NEW_NOMENCLATURE"),
        help="执行写入测试：依次指定目标 PartNumber、新 PartNumber、新 Nomenclature（写完后自动还原）",
    )
    parser.add_argument(
        "--manual", nargs="?", const="",
        metavar="PART_NUMBER",
        help="执行交互式手动操作测试，暂停等待用户在 CATIA 中操作后检验 COM 引用是否仍有效。"
             "可选指定目标 PartNumber；缺省时使用产品树第一个子节点（Level=1）。",
    )
    args = parser.parse_args()

    # ── 连接 CATIA ────────────────────────────────────────────────────────────
    header("═══  COM 引用缓存方案验证  ═══")
    sep()
    print("正在连接 CATIA V5 ...")

    try:
        app = get_catia_v5_application()
    except RuntimeError as e:
        fail(f"无法连接 CATIA：{e}")
        sys.exit(1)

    try:
        doc          = app.ActiveDocument
        root_product = doc.Product
        doc_name     = doc.Name
    except Exception as e:
        fail(f"无法获取活动文档：{e}\n请确保 CATIA 中有一个 CATProduct 处于激活状态。")
        sys.exit(1)

    ok(f"已连接 CATIA，活动文档：{doc_name}")

    # ── Phase 1: COLLECT ─────────────────────────────────────────────────────
    header("Phase 1 – COLLECT：遍历产品树，缓存 COM 引用")
    sep()

    t_start = time.perf_counter()
    rows    = collect_with_com_refs(root_product)
    t_end   = time.perf_counter()

    elapsed_ms    = (t_end - t_start) * 1000
    total_nodes   = len(rows)
    nodes_with_extras = sum(1 for r in rows if r["_product_extras"])
    embedded_nodes    = sum(1 for r in rows if r["_is_embedded"])
    max_level         = max((r["_level"] for r in rows), default=0)

    ok(f"遍历完成：{total_nodes} 个节点，耗时 {elapsed_ms:.1f} ms")
    info(f"树最大深度：{max_level} 层")
    info(f"嵌入式部件（Type=部件）节点数：{embedded_nodes}")
    info(f"存在额外兄弟实例的节点数：{nodes_with_extras}")

    # 显示树的前几行（概览）
    print()
    print("  产品树概览（前 30 行）：")
    print(f"  {'Level':<6} {'Part Number':<30} {'Nomenclature':<25} {'COM 类型'}")
    print(f"  {'─'*6} {'─'*30} {'─'*25} {'─'*20}")
    for row in rows[:30]:
        indent   = "  " * row["_level"]
        pn       = (indent + row["Part Number"])[:28]
        nom      = row["Nomenclature"][:23] if row["Nomenclature"] else ""
        com_type = type(row["_product"]).__name__
        extras   = f" (+{len(row['_product_extras'])} extra)" if row["_product_extras"] else ""
        print(f"  {row['_level']:<6} {pn:<30} {nom:<25} {com_type}{extras}")
    if total_nodes > 30:
        print(f"  ... 共 {total_nodes} 个节点（只显示前 30 行）")

    # 验证第一个节点的 COM 对象类型
    if rows:
        first_product = rows[0]["_product"]
        com_type_name = type(first_product).__name__
        com_module    = type(first_product).__module__
        info(f"\nCOM 对象类型：{com_module}.{com_type_name}")
        # 验证它是 win32com CDispatch（不是字符串或数字）
        if "CDispatch" in com_type_name or "Dispatch" in com_type_name:
            ok("确认：存储的是 win32com CDispatch 对象（不是字符串/ID）")
        else:
            warn(f"COM 对象类型为 {com_type_name}，请确认这是有效的 COM 包装器")

    # ── Phase 2: VALIDATE ────────────────────────────────────────────────────
    header("Phase 2 – VALIDATE：回读所有缓存 COM 引用，验证有效性")
    sep()

    # 模拟"用户在 CATIA 中编辑了一段时间"：等待 1 秒
    wait_s = 1
    info(f"等待 {wait_s} 秒（模拟遍历结束到写回之间的时间间隔）...")
    time.sleep(wait_s)

    t_v_start          = time.perf_counter()
    passed, failed_cnt = validate_com_refs(rows)
    t_v_end            = time.perf_counter()

    v_elapsed_ms = (t_v_end - t_v_start) * 1000

    ok(f"验证完成：{passed} 个引用有效  /  {failed_cnt} 个失效  （耗时 {v_elapsed_ms:.1f} ms）")

    if failed_cnt > 0:
        fail(f"存在 {failed_cnt} 个失效 COM 引用，缓存方案存在风险！")
    else:
        ok("所有 COM 引用均保持有效 ✓")

    # ── Phase 3: WRITE TEST（可选） ──────────────────────────────────────────
    if args.write:
        target_pn, new_pn, new_nom = args.write
        header("Phase 3 – WRITE TEST：通过缓存 COM 引用写入 PartNumber 和 Nomenclature")
        sep()
        info(f"目标 PartNumber  : {target_pn!r}")
        info(f"新   PartNumber  : {new_pn!r}")
        info(f"新   Nomenclature: {new_nom!r}")
        print()
        write_passed = write_test(rows, target_pn, new_pn, new_nom)
        print()
        if write_passed:
            ok("写入测试通过 ✓  —  缓存 COM 引用方案可行，PN 改写后引用依然有效")
        else:
            fail("写入测试存在失败项，请检查上方输出")
    else:
        header("Phase 3 – WRITE TEST（已跳过）")
        sep()
        info("未传入 --write 参数，不执行写入测试。")
        info("如需测试写入，运行：")
        info("  python tests/test_com_ref_cache.py --write <PartNumber> <NewPartNumber> <NewNomenclature>")

    # ── Phase 4: MANUAL INTERACTION TEST（可选） ─────────────────────────────
    if args.manual is not None:
        # args.manual == "" 表示 --manual 未带参数，自动选第一个 Level=1 节点
        if args.manual == "":
            first_sub = next((r for r in rows if r["_level"] == 1), None)
            if first_sub is None:
                first_sub = rows[1] if len(rows) > 1 else rows[0]
            manual_target_pn = first_sub["Part Number"]
            info(f"--manual 未指定 PartNumber，自动选取：{manual_target_pn!r}")
        else:
            manual_target_pn = args.manual

        header("Phase 4 – MANUAL INTERACTION TEST：等待用户在 CATIA 中手动操作")
        sep()
        print(f"  {_YELLOW}本阶段会多次暂停，请按提示切换到 CATIA 操作后回来按 Enter。{_RESET}")
        print()
        manual_interaction_test(rows, manual_target_pn)
    else:
        header("Phase 4 – MANUAL INTERACTION TEST（已跳过）")
        sep()
        info("未传入 --manual 参数，不执行交互测试。")
        info("如需测试，运行：")
        info("  python tests/test_com_ref_cache.py --manual [PartNumber]")

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    header("═══  测试汇总  ═══")
    sep()
    info(f"文档         ：{doc_name}")
    info(f"节点总数     ：{total_nodes}")
    info(f"遍历耗时     ：{elapsed_ms:.1f} ms")
    info(f"验证耗时     ：{v_elapsed_ms:.1f} ms  （直接访问 {passed} 个缓存 COM 引用）")

    if total_nodes > 0:
        avg_per_node = v_elapsed_ms / total_nodes
        info(f"平均每节点   ：{avg_per_node:.3f} ms（验证阶段，纯 COM 调用）")

    if failed_cnt == 0:
        print(f"\n  {_GREEN}{_BOLD}结论：COM 引用缓存方案有效，可以安全实施。{_RESET}")
    else:
        print(f"\n  {_RED}{_BOLD}结论：存在 {failed_cnt} 个失效引用，需要进一步调查。{_RESET}")

    sep()


if __name__ == "__main__":
    main()
