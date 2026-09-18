#!/usr/bin/env python3
"""CATIA Copilot 翻译目录构建前校验脚本。

构建流水线（build_nuitka*.ps1 / GitHub Actions）在 Nuitka 编译前调用本脚本，
校验 ``resources/i18n/*.ts`` 可直接交付给 ``pyside6-lrelease``：

- XML 格式合法（ElementTree 解析；占位符等以 XML 文本形式保存，非法实体即失败）；
- 无空 ``<translation>``、无 ``type="unfinished"``；
- 每条 source/translation 的花括号配对与占位符集合一致
  （``{{...}}`` 为 ``.format()`` 字面转义，不计入占位符集合）；
- zh_CN 与 en_US 的 source 集合双向一致；
- 真实 ``pyside6-lupdate`` 按生产 18 文件清单（见 docs/i18n-phase5-report.md §二）
  提取的 source 集合与两份 TS 双向一致（生产 translate 字面量覆盖）；
- AST 可达 UI 守卫：扫描已知 UI 作用域（Pull/历史/设置/附件对话框、清空确认框、
  质量特性四个文件对话框）内未走 translate 的中文字符串字面量，拦截 B1/B2/R1
  这类「裸中文可达 UI 文案」回归（lupdate 只覆盖已包裹字面量，此处补齐盲区）；
  已知业务值/数据值/日志文本在允许清单内，不被误报。

不依赖 docs/i18n-phase*-translations.json 交接表（本项目不允许运行期字典方案）。

退出码：0 = 全部通过；1 = 存在任一校验失败项（构建流水线据此中止）。
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# ── 路径（基于脚本位置解析仓库根，调用方不依赖 cwd）────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = REPO_ROOT / "resources" / "i18n"
TS_EN = I18N_DIR / "catia_copilot_en_US.ts"
TS_ZH = I18N_DIR / "catia_copilot_zh_CN.ts"

# i18n.py 中固定的翻译 context（lupdate 按此 key 提取/合并）
APP_CONTEXT = "CATIACopilot"

# 生产 18 文件清单（docs/i18n-phase5-report.md §二「真实 lupdate 提取」）：
# main.py + catia_copilot/constants.py、i18n.py 及 ui/ 下 15 个生产文件；
# 排除 tests / _archive-mypdm。清单内新出现的 translate 字面量必须已进 TS，
# 否则"覆盖"校验失败，提示先跑 lupdate 合并。
PRODUCTION_FILES: list[str] = [
    "main.py",
    "catia_copilot/constants.py",
    "catia_copilot/i18n.py",
    "catia_copilot/ui/main_window.py",
    "catia_copilot/ui/catia_embed.py",
    "catia_copilot/ui/bom_edit_dialog_v3.py",
    "catia_copilot/ui/bom_file_rename_dialog.py",
    "catia_copilot/ui/convert_dialog.py",
    "catia_copilot/ui/export_bom_dialog.py",
    "catia_copilot/ui/find_deps_dialog.py",
    "catia_copilot/ui/mass_props_dialog.py",
    "catia_copilot/ui/template_dialog.py",
    "catia_copilot/ui/plm_workbench.py",
    "catia_copilot/ui/ai_chat_panel.py",
    "catia_copilot/ui/help_dialog.py",
    "catia_copilot/ui/session_config_dialog.py",
    "catia_copilot/ui/model_state_dialog.py",
    "catia_copilot/ui/log_window.py",
]

LUPDATE = shutil.which("pyside6-lupdate")


def placeholder_tokens(text: str) -> set[str]:
    """提取单花括号占位符 token 集合（``{0}`` / ``{name}``）。

    ``{{...}}`` 是 ``.format()`` 的字面转义（phase3-dialogs 表头模板即用此写
    法避免 ``KeyError``），先剥除再提取，避免把转义字面量误判为占位符。
    """
    stripped = re.sub(r"\{\{[^{}]*\}\}", "", text)
    return set(re.findall(r"\{[^{}]*\}", stripped))


def catalog_entries(ts_path: Path) -> list[dict]:
    """解析 .ts 并返回 APP_CONTEXT 下每条词条字段。

    XML 解析失败（非法实体/标签损坏）直接抛出 ParseError，即"XML 文本格式"不合格。
    """
    tree = ET.parse(ts_path)
    root = tree.getroot()
    contexts = {c.findtext("name"): c for c in root.findall("context")}
    ctx = contexts.get(APP_CONTEXT)
    if ctx is None:
        raise ValueError(f"{ts_path.name}: 缺少 {APP_CONTEXT} context")
    entries = []
    for m in ctx.findall("message"):
        src_el = m.find("source")
        tr_el = m.find("translation")
        source = src_el.text if src_el is not None and src_el.text is not None else ""
        trans = tr_el.text if tr_el is not None and tr_el.text is not None else ""
        entries.append(
            {
                "source": source,
                "translation": trans,
                "missing_source": src_el is None or not source,
                "unfinished": tr_el is not None and tr_el.get("type") == "unfinished",
            }
        )
    return entries


def flaws_of(entries: list[dict]) -> list[str]:
    """按空词条 / unfinished / 花括号配对 / 占位符一致性逐条检查。"""
    flaws = []
    for i, e in enumerate(entries, start=1):
        src, tr = e["source"], e["translation"]
        if e["missing_source"]:
            flaws.append(f"第 {i} 条缺少 source")
        if e["unfinished"]:
            flaws.append(f"第 {i} 条 translation 标记 unfinished: {src[:40]}")
        if not tr.strip():
            flaws.append(f"第 {i} 条 translation 为空: {src[:40]}")
        if src.count("{") != src.count("}") or tr.count("{") != tr.count("}"):
            flaws.append(f"第 {i} 条花括号不配对: {src[:40]}")
        elif placeholder_tokens(src) != placeholder_tokens(tr):
            flaws.append(f"第 {i} 条占位符集合不一致: {src[:40]}")
    return flaws


def sources_of(ts_path: Path) -> set[str]:
    """返回 APP_CONTEXT 下全部 source 文本集合。"""
    return {e["source"] for e in catalog_entries(ts_path)}


def lupdate_sources(sources: list[Path]) -> set[str]:
    """用真实 pyside6-lupdate 对给定文件清单提取 source 集合（临时 TS，不落盘）。"""
    if LUPDATE is None:
        raise RuntimeError("pyside6-lupdate 不在 PATH 中，无法做字面量覆盖校验")
    with tempfile.TemporaryDirectory() as d:
        tmp_ts = Path(d) / "probe.ts"
        proc = subprocess.run(
            [
                LUPDATE,
                "-extensions",
                "py",
                "-no-obsolete",
                *(str(p) for p in sources),
                "-ts",
                str(tmp_ts),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"pyside6-lupdate 失败（退出码 {proc.returncode}）:\n{proc.stderr}"
            )
        if not tmp_ts.exists():
            raise RuntimeError("pyside6-lupdate 未产出 .ts")
        return sources_of(tmp_ts)


# ── 可达 UI 裸中文 AST 守卫 ────────────────────────────────────────────────
# 背景：lupdate 覆盖校验只约束「已包裹 translate 的字面量」，无法发现漏包裹的
# 可达 UI 字符串（正是 B1/B2/R1 漏过的根因）。本守卫对已知 UI 作用域做 AST 扫描：
# 出现在这些作用域内、又不在 translate 参数或允许清单中的中文字符串字面量即报错，
# 在 CI 前拦截同类回归；业务值/数据值/日志文本列入允许清单，避免误报。

_REACHABLE_UI_FILES = [
    "catia_copilot/ui/plm_workbench.py",
    "catia_copilot/ui/mass_props_dialog.py",
]

# plm_workbench 中受守卫作用域：4 个对话框类 + 清空历史确认框方法 + 表头渲染 helper
_PLM_DIALOG_CLASSES = (
    "_SettingsDialog",
    "_HistoryDialog",
    "_AttachmentDialog",
    "_PullDialog",
)
_PLM_GUARD_METHODS = frozenset({"_on_clear_history"})
_PLM_GUARD_HELPERS = frozenset({
    "_pc_header_display",
    "_history_table_header_display",
    "_attachment_table_header_display",
    "_tags_table_header_display",
    "_rules_table_header_display",
})

# mass_props_dialog 中 4 个文件对话框方法（保存/载入/追加/导出）
_MASS_PROPS_GUARD_METHODS = frozenset({
    "_save_data_to_json",
    "_load_data_from_json",
    "_append_data_from_file",
    "_export_table",
})

# 已知业务值/数据值/日志文本（非可达 UI 控件文案），命中不报错
_REACHABLE_UI_ALLOWLIST = frozenset({
    # 质量特性默认文件名（数据值）
    "惯量汇总",
    "_惯量汇总",
    # 质量特性文件对话框方法内的日志文本（不面向 UI 展示）
    "保存质量特性数据失败: ",
    "载入质量特性数据失败: ",
    "追加质量特性数据失败 (",
    "导出失败: ",
})

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _reachable_ui_scopes(tree: ast.Module, rel: str) -> list[tuple[str, ast.AST]]:
    """返回守卫作用域（(作用域名, AST 节点)）；类内方法按 类名.方法名 命名。"""
    scopes: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
            node.name in _PLM_GUARD_HELPERS or node.name in _MASS_PROPS_GUARD_METHODS
        ):
            scopes.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            if node.name in _PLM_DIALOG_CLASSES:
                scopes.append((node.name, node))
            elif node.name in ("PlmWorkbench", "MassPropsDialog"):
                for m in node.body:
                    if not isinstance(m, ast.FunctionDef):
                        continue
                    if node.name == "PlmWorkbench" and m.name in _PLM_GUARD_METHODS:
                        scopes.append((f"{node.name}.{m.name}", m))
                    elif node.name == "MassPropsDialog" and m.name in _MASS_PROPS_GUARD_METHODS:
                        scopes.append((f"{node.name}.{m.name}", m))
    return scopes


def _scope_allowed_literals(scope: ast.AST) -> set[str]:
    """收集作用域内 translate 实参字面量与 docstring 字面量（这些中文不计违规）。"""
    allowed: set[str] = set()
    for n in ast.walk(scope):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "translate":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    allowed.add(a.value)
        if (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        ):
            allowed.add(n.value.value)
    return allowed


def _scan_reachable_ui_text(rel: str, text: str) -> list[str]:
    """对单文件源码文本做 AST 扫描，返回裸中文 UI 文案失败列表（可独立单测）。"""
    failures: list[str] = []
    tree = ast.parse(text)
    for scope_name, scope in _reachable_ui_scopes(tree, rel):
        allowed = _scope_allowed_literals(scope)
        for n in ast.walk(scope):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
                continue
            if not _CJK_RE.search(n.value):
                continue
            if n.value in allowed or n.value in _REACHABLE_UI_ALLOWLIST:
                continue
            failures.append(
                f"{rel} [{scope_name}] 第 {n.lineno} 行裸中文 UI 字面量未走 translate:"
                f" {n.value[:40]!r}"
            )
    return failures


def check_reachable_ui(verbose: bool = True) -> list[str]:
    """AST 扫描已知 UI 作用域，返回「裸中文可达 UI 文案」失败列表。"""
    failures: list[str] = []
    for rel in _REACHABLE_UI_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            failures.append(f"可达 UI 守卫缺少目标文件: {rel}")
            continue
        failures.extend(_scan_reachable_ui_text(rel, path.read_text(encoding="utf-8")))
    if verbose and failures:
        print("[AST 可达 UI 守卫] 发现漏迁移裸中文文案：")
        for f in failures[:20]:
            print(f"  ✗ {f}")
    return failures


def check_catalog(ts_path: Path, verbose: bool = True) -> list[str]:
    """校验单个 TS，返回失败列表（打印统计与明细）。"""
    if not ts_path.is_file():
        return [f"缺少翻译文件: {ts_path}"]
    try:
        entries = catalog_entries(ts_path)
    except (ET.ParseError, ValueError) as exc:
        return [str(exc)]
    flaws = flaws_of(entries)
    if verbose:
        print(
            f"[{ts_path.name}] 词条 {len(entries)} / 空 {sum(not e['translation'].strip() for e in entries)}"
            f" / unfinished {sum(e['unfinished'] for e in entries)}"
            f" / 结构或占位符问题 {len(flaws)}"
        )
        for f in flaws:
            print(f"  ✗ {f}")
    return flaws


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-lupdate",
        action="store_true",
        help="跳过真实 pyside6-lupdate 字面量覆盖校验（本地开发用，构建不传此参数）",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    print("── CATIA Copilot 翻译目录校验 ──")
    for ts in (TS_EN, TS_ZH):
        failures.extend(check_catalog(ts))

    # 可达 UI 裸中文 AST 守卫（lupdate 覆盖校验的互补盲区）
    failures.extend(check_reachable_ui())

    # en / zh source 集合双向一致
    if TS_EN.is_file() and TS_ZH.is_file():
        en_src, zh_src = sources_of(TS_EN), sources_of(TS_ZH)
        if en_src != zh_src:
            failures.append(
                "en_US / zh_CN source 集合不一致"
                f"（仅 en={sorted(en_src - zh_src)[:5]}，仅 zh={sorted(zh_src - en_src)[:5]}）"
            )

    # 生产 translate 字面量覆盖（18 文件清单 → 真实 lupdate）
    missing_files = [str(p) for p in PRODUCTION_FILES if not (REPO_ROOT / p).is_file()]
    if missing_files:
        failures.append(f"生产文件清单存在缺失: {missing_files}")
    elif not args.skip_lupdate:
        try:
            extracted = lupdate_sources([REPO_ROOT / p for p in PRODUCTION_FILES])
        except RuntimeError as exc:
            failures.append(str(exc))
        else:
            en_src = sources_of(TS_EN) if TS_EN.is_file() else set()
            missing = sorted(en_src - extracted)
            extra = sorted(extracted - en_src)
            print(
                f"[lupdate] {len(extracted)} 个 source；TS 缺少 {len(missing)}"
                f" / TS 多余 {len(extra)}"
            )
            for s in missing[:5]:
                failures.append(f"lupdate 提取到但 TS 未收录: {s[:60]}")
            if extra:
                # lupdate 对错误标注的类进行提取（如测试文件混入生产清单），应清理
                failures.append(f"TS 收录但 lupdate 未提取 {len(extra)} 条（多余）: {extra[:5]}")

    if failures:
        print(f"\n校验失败：共 {len(failures)} 项")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\n校验通过：TS 完整 / 占位符一致 / lupdate 覆盖双向一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())