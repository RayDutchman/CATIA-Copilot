"""i18n 修复回归测试：B1 全选/全不选、B2 清空历史确认框、R1 质量特性文件过滤器。

对应 docs/i18n-final-review.md 阻塞项 B1/B2/R1，这三处曾因「裸中文可达 UI
文案」漏过 lupdate 覆盖校验（lupdate 只约束已包裹 translate 的字面量）。
本测试从三层锁死修复：

1. AST 字面量防线：
   - 六个目标字符串必须是 translate("CATIACopilot", ...) 的实参字面量；
   - 除 translate 实参外，源码中不允许再出现这些字符串的裸常量（防御回退）；
2. 行为防线（offscreen 真 QWidget）：
   - _PullDialog 全选/全不选按钮默认中文、英文 patch 后为 Select All/None；
   - PlmWorkbench._on_clear_history 弹出 QMessageBox.question，标题与正文在
     中/英 patch 下各自正确；点 Yes 才清 QSettings 记录与表格；
   - mass_props 保存/载入/追加/导出的 QFileDialog 过滤器中/英正确，且扩展名
     标记 (*.mpd)/(*.xlsx)/(*.csv) 足本不翻译（显示字符串与前台行为解耦）；
3. 守卫自身：
   - scripts/verify_translations.py 的可达 UI AST 守卫对生产文件零误报；
   - 合成样例证明该守卫既能捕获裸中文、又放过 translate 与允许清单业务值；
   - 允许清单每一条都必须真实存在于生产源码（杜绝死条目）。

质量特性文件对话框为重型 QWidget，仅在 offscreen QPA 下构造；每用例结束
close + deleteLater + processEvents 保证进程退出码 0。
"""
import ast
import importlib.util
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QMessageBox,
)

from catia_copilot.ui.mass_props_dialog import MassPropsDialog  # noqa: E402
from catia_copilot.ui.plm_workbench import (  # noqa: E402
    PlmWorkbench,
    _PullDialog,
)

_QAPP = QApplication.instance() or QApplication([])

_ROOT = Path(__file__).resolve().parents[2]
_PLM_SRC = _ROOT / "catia_copilot" / "ui" / "plm_workbench.py"
_MPD_SRC = _ROOT / "catia_copilot" / "ui" / "mass_props_dialog.py"
_VERIFY_SCRIPT = _ROOT / "scripts" / "verify_translations.py"

# B1/B2/R1 六个目标字符串的英文译文
_EN_MAP = {
    "全选": "Select All",
    "全不选": "Select None",
    "清空历史": "Clear History",
    "确定清空所有同步历史记录？此操作不可撤销。": (
        "Are you sure you want to clear all sync history? This action cannot be undone."
    ),
    "质量特性数据文件 (*.mpd)": "Mass Properties Data File (*.mpd)",
    "Excel 文件 (*.xlsx);;CSV 文件 (*.csv)": (
        "Excel File (*.xlsx);;CSV File (*.csv)"
    ),
}


def _en(ctx, src, *a, **k):
    """英文 patch 侧效应：命中目标译文返回英文，否则回退原文。"""
    return _EN_MAP.get(src, src)


def _translate_arg_sources(path: Path) -> set[str]:
    """AST 提取文件内全部 translate 调用的 source 字面量（相对路径/view_ Only 无）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sources: set[str] = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "translate"
            and len(n.args) >= 2
            and isinstance(n.args[1], ast.Constant)
            and isinstance(n.args[1].value, str)
        ):
            sources.add(n.args[1].value)
    return sources


def _bare_constant_count(path: Path, s: str) -> int:
    """统计源码中等于 s 的字符串常量个数（不含 translate 实参，纯裸字面量）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    translate_args: set[int] = set()
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            continue
        if n.func.id != "translate":
            continue
        for a in n.args:
            if isinstance(a, ast.Constant):
                translate_args.add(id(a))
    total = 0
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value == s
            and id(n) not in translate_args
        ):
            total += 1
    return total


def _cleanup(*widgets) -> None:
    for w in widgets:
        w.close()
        w.deleteLater()
    _QAPP.processEvents()


class TestRequiredLiteralsWrapped(unittest.TestCase):
    """AST 防线：六个目标字符串只出现在 translate 实参中，无裸常量。"""

    def test_pull_select_buttons_wrapped(self) -> None:
        args = _translate_arg_sources(_PLM_SRC)
        for s in ("全选", "全不选"):
            self.assertIn(s, args, f"{s} 未成为 translate 实参")
            self.assertEqual(
                _bare_constant_count(_PLM_SRC, s), 0, f"{s} 存在裸常量回退"
            )

    def test_clear_history_confirm_wrapped(self) -> None:
        args = _translate_arg_sources(_PLM_SRC)
        for s in ("清空历史", "确定清空所有同步历史记录？此操作不可撤销。"):
            self.assertIn(s, args, f"{s} 未成为 translate 实参")
            self.assertEqual(
                _bare_constant_count(_PLM_SRC, s), 0, f"{s} 存在裸常量回退"
            )

    def test_mass_props_filters_wrapped(self) -> None:
        args = _translate_arg_sources(_MPD_SRC)
        for s in (
            "质量特性数据文件 (*.mpd)",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv)",
        ):
            self.assertIn(s, args, f"{s} 未成为 translate 实参")
            self.assertEqual(
                _bare_constant_count(_MPD_SRC, s), 0, f"{s} 存在裸常量回退"
            )

    def test_mass_props_mpd_filter_used_three_times(self) -> None:
        tree = ast.parse(_MPD_SRC.read_text(encoding="utf-8"))
        count = 0
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "translate"
                and len(n.args) >= 2
                and isinstance(n.args[1], ast.Constant)
                and n.args[1].value == "质量特性数据文件 (*.mpd)"
            ):
                count += 1
        self.assertEqual(count, 3)


class TestPullDialogSelectButtons(unittest.TestCase):
    """行为防线 B1：全选/全不选按钮中英文本。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dlg = _PullDialog(
            base_url="http://plm.local", login="a", password="p",
            workspace="WS1", work_dir=self._tmp.name,
        )

    def tearDown(self) -> None:
        _cleanup(self.dlg)

    def test_zh_default(self) -> None:
        self.assertEqual(self.dlg._btn_select_all.text(), "全选")
        self.assertEqual(self.dlg._btn_select_none.text(), "全不选")

    def test_en_patch(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            dlg = _PullDialog(
                base_url="http://plm.local", login="a", password="p",
                workspace="WS1", work_dir=self._tmp.name,
            )
        try:
            self.assertEqual(dlg._btn_select_all.text(), "Select All")
            self.assertEqual(dlg._btn_select_none.text(), "Select None")
        finally:
            _cleanup(dlg)


class TestClearHistoryConfirm(unittest.TestCase):
    """行为防线 B2：确认框标题/正文中英正确；Yes 才执行清理。"""

    def setUp(self) -> None:
        self.wb = PlmWorkbench()

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_zh_question_args(self) -> None:
        with patch(
            "catia_copilot.ui.plm_workbench.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as q:
            self.wb._on_clear_history()
        q.assert_called_once()
        self.assertEqual(q.call_args.args[1], "清空历史")
        self.assertEqual(
            q.call_args.args[2], "确定清空所有同步历史记录？此操作不可撤销。"
        )

    def test_en_question_args(self) -> None:
        with patch(
            "catia_copilot.ui.plm_workbench.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as q, patch(
            "catia_copilot.ui.plm_workbench.translate", side_effect=_en
        ):
            self.wb._on_clear_history()
        q.assert_called_once()
        self.assertEqual(q.call_args.args[1], "Clear History")
        self.assertEqual(
            q.call_args.args[2],
            "Are you sure you want to clear all sync history? This action cannot be undone.",
        )

    def test_no_does_not_touch_records(self) -> None:
        with patch(
            "catia_copilot.ui.plm_workbench.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as q, patch(
            "catia_copilot.ui.plm_workbench.QSettings",
        ) as st:
            self.wb._on_clear_history()
        q.assert_called_once()
        st.assert_not_called()

    def test_yes_clears_records_and_table(self) -> None:
        fake = type("FakeSettings", (), {})()
        fake.calls = []

        def _remove(key):
            fake.calls.append(key)

        fake.remove = _remove
        with patch(
            "catia_copilot.ui.plm_workbench.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "catia_copilot.ui.plm_workbench.QSettings", return_value=fake
        ):
            self.wb._on_clear_history()
        self.assertEqual(fake.calls, ["records"])
        self.assertEqual(self.wb._tbl_history.rowCount(), 0)
        self.assertEqual(self.wb._txt_hist.toPlainText(), "")


class TestMassPropsFileDialogFilters(unittest.TestCase):
    """行为防线 R1：四个文件对话框的中/英过滤器与扩展名足本。"""

    def setUp(self) -> None:
        self.dlg = MassPropsDialog()
        self.dlg._rows = [{"Part Number": "P-ROOT", "_filepath": "D:/x/P-ROOT.CATProduct"}]

    def tearDown(self) -> None:
        _cleanup(self.dlg)

    def _capture_filter(self, fname: str, ret) -> str:
        captured = []

        def _sniff(*a, **k):
            captured.append(a[3])
            return ret

        with patch.object(QFileDialog, fname, side_effect=_sniff):
            getattr(self.dlg, self._METHOD)()
        return captured[0] if captured else self.fail(f"{fname} 未被调用")

    def test_save_filter_zh(self) -> None:
        self._METHOD = "_save_data_to_json"
        self.assertEqual(
            self._capture_filter("getSaveFileName", ("", "")),
            "质量特性数据文件 (*.mpd)",
        )

    def test_load_filter_zh(self) -> None:
        self._METHOD = "_load_data_from_json"
        self.assertEqual(
            self._capture_filter("getOpenFileName", ("", "")),
            "质量特性数据文件 (*.mpd)",
        )

    def test_append_filter_zh(self) -> None:
        self._METHOD = "_append_data_from_file"
        self.assertEqual(
            self._capture_filter("getOpenFileNames", ([], "")),
            "质量特性数据文件 (*.mpd)",
        )

    def test_export_filter_zh(self) -> None:
        self._METHOD = "_export_table"
        self.assertEqual(
            self._capture_filter("getSaveFileName", ("", "")),
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv)",
        )

    def test_filters_english_and_extension_tokens_preserved(self) -> None:
        captured = []

        def _sniff_mpd(*a, **k):
            captured.append(a[3])
            return ("", "")

        with patch.object(QFileDialog, "getSaveFileName", side_effect=_sniff_mpd), patch(
            "catia_copilot.ui.mass_props_dialog.translate", side_effect=_en
        ):
            self.dlg._save_data_to_json()
        self.assertEqual(captured[0], "Mass Properties Data File (*.mpd)")
        self.assertIn("(*.mpd)", captured[0])
        captured.clear()

        def _sniff_xlsx(*a, **k):
            captured.append(a[3])
            return ("", "")

        with patch.object(QFileDialog, "getSaveFileName", side_effect=_sniff_xlsx), patch(
            "catia_copilot.ui.mass_props_dialog.translate", side_effect=_en
        ):
            self.dlg._export_table()
        self.assertEqual(captured[0], "Excel File (*.xlsx);;CSV File (*.csv)")
        self.assertIn("(*.xlsx)", captured[0])
        self.assertIn("(*.csv)", captured[0])


def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_translations", _VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestReachableUiGuard(unittest.TestCase):
    """AST 守卫自身：对生产文件零误报、允许清单不虚设、合成样例正反拦截。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vt = _load_verify()

    def test_production_files_clean(self) -> None:
        self.assertEqual(self.vt.check_reachable_ui(verbose=False), [])

    def test_allowlist_entries_all_real(self) -> None:
        text = _MPD_SRC.read_text(encoding="utf-8")
        for v in self.vt._REACHABLE_UI_ALLOWLIST:
            self.assertIn(v, text, f"允许清单存在死条目：{v!r}")

    def test_guard_flags_bare_ui_string(self) -> None:
        src = textwrap.dedent(
            """\
            def _export_table(self):
                label = "新按钮"
                return label
            """
        )
        failures = self.vt._scan_reachable_ui_text(
            "catia_copilot/ui/mass_props_dialog.py", src
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("新按钮", failures[0])

    def test_guard_passes_translated_and_allowlisted(self) -> None:
        """translate 实参 + 允许清单（日志/业务数据）不被误报。"""
        src = textwrap.dedent(
            """\
            def _export_table(self):
                logger.error("导出失败: " + str(e))
                title = translate("CATIACopilot", "导出质量特性表格")
                return title
            """
        )
        failures = self.vt._scan_reachable_ui_text(
            "catia_copilot/ui/mass_props_dialog.py", src
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()