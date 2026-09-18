"""PLM 主工作台 i18n 改造测试（i18n Phase 3 Task 3.2 主类部分）。

目标：
1. PlmWorkbench 类体 + 模块级渲染工厂（_st_display / _header_display /
   _sync_col_display）内全部 translate 调用 context/source 均为字符串字面量；
2. 主工作台词条表 docs/i18n-phase3-main-translations.json 与源码 AST 提取
   的双向覆盖一致，且当前为"键值同串"占位表；
3. 差异状态 _ST_* / 表头 _DC_HEADERS / _UPGRADE_* / _SYNC_COL_DISPLAY 等
   业务中文值保持不变（渲染才翻译）；
4. 渲染工厂语言行为：默认中文回退、英文 patch 生效、未知键安全回显、
   空状态回退 _ST_UNKNOWN；
5. 运行时窗口文案（标题/按钮/同步进度前缀）随 translate 中英文切换。

说明：PlmWorkbench 为重型 QDialog，仅在 offscreen QPA 下构造；测试结束后
清理实例避免几何/选项写回影响其他用例。
"""
import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_copilot.plm import sync  # noqa: E402
from catia_copilot.ui.plm_workbench import (  # noqa: E402
    PlmWorkbench,
    _SYNC_COL_CHECKIN,
    _SYNC_COL_DISPLAY,
    _SYNC_COL_SOURCE,
    _SYNC_COL_UPDATE,
    _fmt_kbps,
    _header_display,
    _st_display,
    _sync_col_display,
)

_QAPP = QApplication.instance() or QApplication([])

_SRC = Path(__file__).resolve().parents[2] / "catia_copilot" / "ui" / "plm_workbench.py"
_TRANSLATIONS = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "i18n-phase3-main-translations.json")
    .read_text(encoding="utf-8")
)

# 模块级渲染工厂：其函数体中的 translate 属于主工作台词条
_FACTORIES = ("_st_display", "_header_display", "_sync_col_display")

# 中文字面量基线（来自测试自身对固定业务文案的记忆）
_ST_VALUES = (
    "?", "✓ 一致", "↑ 本地新", "↓ PLM新", "仅本地", "仅PLM", "⚠ 无法同步",
)
_DC_HEADERS_EXPECTED = [
    "",             # 0 选择列
    "状态",         # 1
    "零件编号",     # 2
    "版本/迭代",    # 3
    "本地版本",     # 4
    "零件名称",     # 5
    "类型",         # 6
    "作者",         # 7
    "签出者",       # 8
    "生命周期状态", # 9
    "本地修改时间", # 10
    "PLM修改时间",  # 11
    "\uf0c6",       # 12 附件列
]

_EN_MAP = {
    "PLM 工作台": "PLM Workbench",
    "⬆ Push 选中": "Push Selected",
    "⬇ Pull 选中": "Pull Selected",
    "⚙ 同步选项": "Sync Options",
    "✓ 一致": "OK",
    "状态": "Status",
    "签出来源": "Source",
    "正在同步…… ({0} / {1})  {2}": "Syncing... ({0} / {1})  {2}",
}


def _translate_collector(tree: ast.AST) -> set[str]:
    """提取 PlmWorkbench 类体 + 模块级渲染工厂内全部 translate(source) 字面量。"""
    sources: set[str] = set()

    def _grab(fn):
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "translate"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                sources.add(node.args[1].value)

    if isinstance(tree, ast.Module):
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "PlmWorkbench":
                _grab(node)
            elif isinstance(node, ast.FunctionDef) and node.name in _FACTORIES:
                _grab(node)
    else:
        _grab(tree)
    return sources


def _new_workbench() -> PlmWorkbench:
    return PlmWorkbench()


def _set_en():
    """返回英文 patch 侧效应：命中 _EN_MAP 返回英文，否则回退原文。"""
    def _en(ctx, src, *a, **k):
        return _EN_MAP.get(src, src)
    return _en


def _cleanup(wb: PlmWorkbench) -> None:
    wb.close()
    wb.deleteLater()
    _QAPP.processEvents()


class TestTranslateLiterals(unittest.TestCase):
    """AST 防线：主类 + 渲染工厂的 translate 前两参均为字符串字面量且 context 恒为 CATIACopilot。"""

    def test_calls_exist(self) -> None:
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        self.assertTrue(_translate_collector(tree), "主工作台应存在 i18n 词条")

    def test_context_is_literal_catiacopilot(self) -> None:
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        hits = 0
        for node in ast.walk(tree):
            is_main_scope = (
                (isinstance(node, ast.ClassDef) and node.name == "PlmWorkbench")
                or (isinstance(node, ast.FunctionDef) and node.name in _FACTORIES)
            )
            if not is_main_scope:
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "translate"):
                    self.assertTrue(call.args, f"第 {call.lineno} 行 translate 缺少 context")
                    ctx = call.args[0]
                    self.assertIsInstance(ctx, ast.Constant, "context 必须为字符串字面量")
                    self.assertIsInstance(ctx.value, str)
                    self.assertEqual(ctx.value, "CATIACopilot",
                                     f"第 {call.lineno} 行 translate context 应为 CATIACopilot")
                    self.assertIsInstance(call.args[1], ast.Constant,
                                          "source 必须为字符串字面量")
                    hits += 1
        self.assertGreater(hits, 0)


class TestJsonCoverage(unittest.TestCase):
    """词条表与源码 AST 提取双向覆盖一致；占位表键值同串。"""

    def test_json_matches_source_ast(self) -> None:
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        ast_sources = _translate_collector(tree)
        json_keys = set(_TRANSLATIONS)
        self.assertEqual(ast_sources, json_keys,
                         "主工作台词条表必须与源码中的 translate 源串完全一致")

    def test_json_is_placeholder_key_value_same(self) -> None:
        for key, value in _TRANSLATIONS.items():
            self.assertEqual(key, value, f"占位表 {key!r} 的键值应相同（等后续填英文）")
            self.assertTrue(key.strip(), "词条键不能为空白")

    def test_json_contains_factory_terms(self) -> None:
        for term in ("✓ 一致", "状态", "零件编号", "签出来源"):
            self.assertIn(term, _TRANSLATIONS, f"渲染工厂词条 {term!r} 缺失")


class TestStableBusinessValues(unittest.TestCase):
    """业务值保持中文原值（仅渲染经工厂翻译）。"""

    def test_st_diff_states_unchanged(self) -> None:
        self.assertEqual(
            (PlmWorkbench._ST_UNKNOWN, PlmWorkbench._ST_OK,
             PlmWorkbench._ST_LOCAL_NEW, PlmWorkbench._ST_PLM_NEW,
             PlmWorkbench._ST_LOCAL_ONLY, PlmWorkbench._ST_PLM_ONLY,
             PlmWorkbench._ST_NO_SYNC),
            _ST_VALUES,
        )

    def test_status_colors_keyed_by_states(self) -> None:
        self.assertEqual(set(PlmWorkbench._STATUS_COLORS), set(_ST_VALUES))

    def test_dc_headers_unchanged(self) -> None:
        self.assertEqual(list(PlmWorkbench._DC_HEADERS), _DC_HEADERS_EXPECTED)
        self.assertEqual(len(PlmWorkbench._DC_HEADERS), 13)

    def test_upgrade_modes_unchanged(self) -> None:
        self.assertEqual(
            (PlmWorkbench._UPGRADE_SKIP, PlmWorkbench._UPGRADE_ITER,
             PlmWorkbench._UPGRADE_VER),
            ("不推送", "+迭代", "+版本"),
        )

    def test_sync_col_display_unchanged(self) -> None:
        self.assertEqual(_SYNC_COL_DISPLAY, {
            _SYNC_COL_SOURCE: "签出来源",
            _SYNC_COL_UPDATE: "更新结果",
            _SYNC_COL_CHECKIN: "签入状态",
        })

    def test_bom_headers_alias_same_object(self) -> None:
        self.assertIs(PlmWorkbench._BOM_COL_HEADERS, PlmWorkbench._DC_HEADERS)


class TestDisplayFactories(unittest.TestCase):
    """渲染工厂：中文回退、英文 patch、未知键回显、空值回退。"""

    def test_st_display_zh_fallback(self) -> None:
        self.assertEqual(_st_display(PlmWorkbench._ST_OK), "✓ 一致")
        self.assertEqual(_st_display(PlmWorkbench._ST_LOCAL_NEW), "↑ 本地新")
        self.assertEqual(_st_display(PlmWorkbench._ST_PLM_NEW), "↓ PLM新")
        self.assertEqual(_st_display(PlmWorkbench._ST_LOCAL_ONLY), "仅本地")
        self.assertEqual(_st_display(PlmWorkbench._ST_PLM_ONLY), "仅PLM")
        self.assertEqual(_st_display(PlmWorkbench._ST_NO_SYNC), "⚠ 无法同步")

    def test_st_display_unknown_and_empty(self) -> None:
        self.assertEqual(_st_display("自定义状态"), "自定义状态")
        self.assertEqual(_st_display(""), PlmWorkbench._ST_UNKNOWN)
        self.assertEqual(_st_display(None), PlmWorkbench._ST_UNKNOWN)

    def test_st_display_english_patch(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_set_en()):
            self.assertEqual(_st_display(PlmWorkbench._ST_OK), "OK")

    def test_header_display_zh_fallback(self) -> None:
        self.assertEqual(_header_display(0), "")
        for col in range(1, 12):
            self.assertEqual(_header_display(col), _DC_HEADERS_EXPECTED[col],
                             f"列 {col} 表头中文回退不符")
        self.assertEqual(_header_display(12), "\uf0c6")
        self.assertEqual(_header_display(99), "")

    def test_header_display_english_patch(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_set_en()):
            self.assertEqual(_header_display(PlmWorkbench._DC_DIFF), "Status")
            self.assertEqual(_header_display(0), "")
            self.assertEqual(_header_display(12), "\uf0c6")

    def test_sync_col_display(self) -> None:
        self.assertEqual(_sync_col_display(_SYNC_COL_SOURCE), "签出来源")
        self.assertEqual(_sync_col_display(_SYNC_COL_UPDATE), "更新结果")
        self.assertEqual(_sync_col_display(_SYNC_COL_CHECKIN), "签入状态")
        self.assertEqual(_sync_col_display("__未知列__"), "__未知列__")
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_set_en()):
            self.assertEqual(_sync_col_display(_SYNC_COL_SOURCE), "Source")

    def test_fmt_kbps(self) -> None:
        self.assertEqual(_fmt_kbps(512.5), "512.5 KB/s")
        self.assertEqual(_fmt_kbps(1024), "1.0 MB/s")
        self.assertEqual(_fmt_kbps(2048), "2.0 MB/s")


class TestRuntimeWindowTexts(unittest.TestCase):
    """运行时窗口文案随 translate 切换中英文。"""

    def _reset_sync(self, wb: PlmWorkbench, total: int = 2) -> None:
        wb._sync_total_nodes = total
        wb._sync_done_nodes = 0
        wb._sync_seen_pns = set()
        wb._sync_result_map.clear()
        wb._pgb_sync.setRange(0, total)
        wb._pgb_sync.setValue(0)

    def test_chinese_fallback_window(self) -> None:
        wb = _new_workbench()
        try:
            self.assertEqual(wb.windowTitle(), "PLM 工作台")
            self.assertEqual(wb._btn_push.text(), "⬆ Push 选中")
            self.assertEqual(wb._btn_pull_sel.text(), "⬇ Pull 选中")
            self.assertEqual(wb._btn_adv.text(), "⚙ 同步选项")
        finally:
            _cleanup(wb)

    def test_english_patch_window(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_set_en()):
            wb = _new_workbench()
        try:
            self.assertEqual(wb.windowTitle(), "PLM Workbench")
            self.assertEqual(wb._btn_push.text(), "Push Selected")
            self.assertEqual(wb._btn_pull_sel.text(), "Pull Selected")
            self.assertEqual(wb._btn_adv.text(), "Sync Options")
        finally:
            _cleanup(wb)

    def test_sync_progress_prefix_chinese(self) -> None:
        wb = _new_workbench()
        try:
            self._reset_sync(wb, total=2)
            wb._on_sync_event(sync.SyncEvent(
                type="node_done", part_number="P-1",
                source_code=sync.CODE_SOURCE_CREATED,
            ))
            self.assertTrue(
                wb._lbl_sync_status.text().startswith("正在同步…… (1 / 2)  "),
                f"进度前缀应为中文，实际：{wb._lbl_sync_status.text()!r}",
            )
        finally:
            _cleanup(wb)

    def test_sync_progress_prefix_english(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_set_en()):
            wb = _new_workbench()
            self._reset_sync(wb, total=2)
            wb._on_sync_event(sync.SyncEvent(
                type="node_done", part_number="P-1",
                source_code=sync.CODE_SOURCE_CREATED,
            ))
        try:
            self.assertTrue(
                wb._lbl_sync_status.text().startswith("Syncing... (1 / 2)  "),
                f"进度前缀应为英文，实际：{wb._lbl_sync_status.text()!r}",
            )
        finally:
            _cleanup(wb)


if __name__ == "__main__":
    unittest.main()