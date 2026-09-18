"""PLM 对话框 i18n 改造测试（i18n Phase 3 Task 3.3）。

覆盖目标：
1. 四个对话框（_SettingsDialog / _HistoryDialog / _AttachmentDialog /
   _PullDialog）及其表格头渲染 helper 内全部 translate 调用 context/source
   均为字符串字面量且 context 恒为 CATIACopilot（AST 防线）；
2. 交接表 docs/i18n-phase3-dialogs-translations.json 的键 == 源码 AST 提取的
   对话框词条与（main + ui）交接表之差，即"仅交接新增词条，双向覆盖一致"；
   且所有译文为非中文英文（非占位）。
3. 真实提取：pyside6-lupdate 对 plm_workbench.py 提取，交接表全部键均在
   提取结果中；
4. 真实构建：pyside6-lrelease 构建剪切后的 .ts → .qm，经 QTranslator 加载后
   translate 实际返回英文（验证 .format 占位符在真实流水线可译）；
5. 行为：默认中文回退、英文 patch 生效；_AttachmentDialog 通过 mock
   PlmApiClient（假网络）构造并异步加载附件列表，不触真实网络；
   _PullDialog._on_bom_done 填充表格与状态文案；未勾选行下载触发
   QMessageBox.warning（mock 防弹窗）。

说明：对话框为重型 QWidget，仅在 offscreen QPA 下构造；每用例结束
close + deleteLater + processEvents 提前完成 C++ 侧析构，保证进程退出码 0。
"""
import ast
import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
)

from catia_copilot.ui.plm_workbench import (  # noqa: E402
    PlmWorkbench,
    _AttachmentDialog,
    _HistoryDialog,
    _PullDialog,
    _SettingsDialog,
    _PC_LOCAL,
)

_QAPP = QApplication.instance() or QApplication([])

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "catia_copilot" / "ui" / "plm_workbench.py"
_MAIN_TRANSLATIONS = json.loads(
    (_ROOT / "docs" / "i18n-phase3-main-translations.json").read_text(encoding="utf-8")
)
_UI_TRANSLATIONS = json.loads(
    (_ROOT / "docs" / "i18n-phase3-ui-translations.json").read_text(encoding="utf-8")
)
_DIALOG_TRANSLATIONS = json.loads(
    (_ROOT / "docs" / "i18n-phase3-dialogs-translations.json").read_text(encoding="utf-8")
)

_DIALOG_CLASSES = ("_SettingsDialog", "_HistoryDialog", "_AttachmentDialog", "_PullDialog")
_DIALOG_HELPERS = (
    "_tags_table_header_display",
    "_rules_table_header_display",
    "_history_table_header_display",
    "_attachment_table_header_display",
    "_pc_header_display",
)


def _dialog_translate_sources() -> set[str]:
    """AST 提取四个对话框类 + 对话框表格头渲染 helper 内全部 translate source 字面量。"""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    sources: set[str] = set()
    for node in tree.body:
        if (isinstance(node, ast.ClassDef) and node.name in _DIALOG_CLASSES) or (
            isinstance(node, ast.FunctionDef) and node.name in _DIALOG_HELPERS
        ):
            for n in ast.walk(node):
                if (isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "translate"
                        and len(n.args) >= 2
                        and isinstance(n.args[1], ast.Constant)
                        and isinstance(n.args[1].value, str)):
                    sources.add(n.args[1].value)
    return sources


def _en(ctx, src, *a, **k):
    """英文 patch 侧效应：对话框交接表命中返回英文，否则回退原文。"""
    return _DIALOG_TRANSLATIONS.get(src, src)


def _brace_ok(value: str) -> bool:
    """译文花括号安全：仅允许 {N} 数字占位符与 {{配对大括号}} 转义，禁裸命名占位符。"""
    i, n = 0, len(value)
    while i < n:
        if value[i] != "{":
            i += 1
            continue
        j = value.find("}", i + 1)
        if j == -1:
            return False
        inner = value[i + 1:j]
        if inner == "":
            return False
        if inner[0] == "{":  # {{...}} 转义，要求成对闭合
            if j + 1 >= n or value[j + 1] != "}":
                return False
            i = j + 2
            continue
        if inner.isdigit():  # {0}/{1} 数字占位符
            i = j + 1
            continue
        return False
    return True


def _cleanup(*widgets) -> None:
    for w in widgets:
        if w is None:
            continue
        w.close()
        w.deleteLater()
    _QAPP.processEvents()


def _new_workbench() -> PlmWorkbench:
    return PlmWorkbench()


def _new_settings_dialog() -> tuple[_SettingsDialog, PlmWorkbench]:
    wb = _new_workbench()
    return _SettingsDialog(wb), wb


def _new_history_dialog() -> tuple[_HistoryDialog, PlmWorkbench]:
    wb = _new_workbench()
    return _HistoryDialog(wb), wb


def _new_attachment_dialog(work_dir: str) -> _AttachmentDialog:
    return _AttachmentDialog(
        base_url="http://plm.local",
        login="alice",
        password="pw",
        workspace="WS1",
        part_number="P-ATT",
        version="A",
        plm_data={"lastIterationNumber": 2},
        work_dir=work_dir,
    )


class _FakePlmClient:
    """假 PLM 客户端：登录/附件列表不触网络，返回固定文件列表。"""

    files = ["a.CATPart", "b.stp"]

    def __init__(self, *a, **k):
        pass

    def login(self, *a, **k):
        pass

    def list_part_attachments(self, *a, **k):
        return self.files


class TestTranslateLiterals(unittest.TestCase):
    """AST 防线：四个对话框 + helper 的 translate 前两参均为字符串字面量、context 恒为 CATIACopilot。"""

    def _dialog_nodes(self) -> list:
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        return [
            node for node in tree.body
            if (isinstance(node, ast.ClassDef) and node.name in _DIALOG_CLASSES)
            or (isinstance(node, ast.FunctionDef) and node.name in _DIALOG_HELPERS)
        ]

    def test_dialog_translate_calls_exist(self) -> None:
        self.assertTrue(_dialog_translate_sources(), "四个对话框应存在 i18n 词条")

    def test_context_source_are_literals(self) -> None:
        for node in self._dialog_nodes():
            for n in ast.walk(node):
                if not (isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "translate"):
                    continue
                if not n.args:
                    self.fail(f"第 {n.lineno} 行 translate 缺少 context 实参")
                ctx = n.args[0]
                self.assertIsInstance(ctx, ast.Constant,
                                      f"第 {n.lineno} 行 context 必须为字符串字面量")
                self.assertEqual(ctx.value, "CATIACopilot",
                                 f"第 {n.lineno} 行 context 应为 CATIACopilot")
                self.assertIsInstance(n.args[1], ast.Constant,
                                      f"第 {n.lineno} 行 source 必须为字符串字面量")


class TestHandoffCoverage(unittest.TestCase):
    """交接表 = 对话框词条 - 已有（main+ui）词条，双向覆盖一致；译文为英文非占位。"""

    def setUp(self) -> None:
        self.dialog_sources = _dialog_translate_sources()
        self.existing = self.dialog_sources & (set(_MAIN_TRANSLATIONS) | set(_UI_TRANSLATIONS))
        self.new_sources = self.dialog_sources - self.existing

    def test_dialog_json_matches_new_sources(self) -> None:
        self.assertEqual(set(_DIALOG_TRANSLATIONS), self.new_sources,
                         "对话框交接表必须等于新增词条集（相对 main+ui 表）")

    def test_existing_terms_not_in_dialog_json(self) -> None:
        self.assertEqual(set(_DIALOG_TRANSLATIONS) & self.existing, set(),
                         "已收录词条不应重复交接")

    def test_dialog_json_values_are_english(self) -> None:
        import re as _re
        cjk = _re.compile(r"[\u4e00-\u9fff]")
        for key, value in _DIALOG_TRANSLATIONS.items():
            self.assertTrue(value, f"{key!r} 译文不可为空")
            self.assertNotEqual(value, key, f"{key!r} 不应为占位键值同串")
            self.assertFalse(cjk.search(value), f"{key!r} 译文必须为英文，实际：{value!r}")
            self.assertTrue(_brace_ok(value),
                            f"{key!r} 译文花括号必须为 {{N}} 数字占位符或 {{配对大括号}} 转义，"
                            f"禁止裸命名占位符（会被 .format() 当作字段）：{value!r}")

    def test_brace_transfer_to_format_safe(self) -> None:
        """新词条中走 .format() 链路的，其译文在 format 后应能保留名义目录占位符。"""
        # 这三条在源码中均以 translate(...).format(work_dir) 调用
        for key in (
            "下载到：{0}/{{零件号}}/{{文件名}}",
            "下载完成！共 {0} 个文件 → {1}/{{零件号}}/{{文件名}}",
            "已下载 {0} 个文件\n保存位置：{1}/{{零件号}}/{{文件名}}",
        ):
            rendered = _DIALOG_TRANSLATIONS[key].format("C:/tmp", "C:/dest")
            self.assertIn("{Part Number}", rendered, f"{key!r} 渲染后应保留 {{Part Number}}")
            self.assertIn("{File Name}", rendered, f"{key!r} 渲染后应保留 {{File Name}}")


class TestRealLupdateExtraction(unittest.TestCase):
    """真实 pyside6-lupdate：交接表全部新词条必须从 plm_workbench.py 提取到。"""

    _LUPDATE = which("pyside6-lupdate")

    @unittest.skipUnless(_LUPDATE, "pyside6-lupdate 不在 PATH 中，跳过真实提取回归")
    def test_plm_workbench_extracts_all_dialog_terms(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ts = Path(d) / "out.ts"
            subprocess.run(
                [self._LUPDATE, "-extensions", "py", "-no-obsolete",
                 str(_SRC), "-ts", str(ts)],
                check=True, capture_output=True, timeout=120,
            )
            self.assertTrue(ts.exists(), "lupdate 应产出 .ts 文件")
            tree = ET.parse(ts)
            contexts = {
                ctx.findtext("name"): ctx
                for ctx in tree.getroot().findall("context")
            }
            self.assertIn("CATIACopilot", contexts, "提取结果必须含 CATIACopilot context")
            extracted = {
                m.find("source").text
                for m in contexts["CATIACopilot"].findall("message")
                if m.find("source") is not None
            }
            missing = set(_DIALOG_TRANSLATIONS) - extracted
            self.assertEqual(missing, set(),
                             "交接表词条必须能被 lupdate 提取（缺失=%s）"
                             % sorted(missing))


class TestRealLreleaseBuildLoad(unittest.TestCase):
    """真实 pyside6-lrelease 构建 qm 并加载，translate 实际返回英文（占位符 {0} 保留）。"""

    _LRELEASE = which("pyside6-lrelease")

    @unittest.skipUnless(_LRELEASE, "pyside6-lrelease 不在 PATH 中，跳过真实构建回归")
    def test_build_qm_and_translate_returns_english(self) -> None:
        ts_body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE TS><TS version="2.1" language="en_US">\n'
            "<context>\n<name>CATIACopilot</name>\n"
            "<message><source>连接配置</source><translation>Connection Settings</translation></message>\n"
            "<message><source>关闭</source><translation>Close</translation></message>\n"
            "<message><source>共 {0} 个附件</source>"
            "<translation>{0} attachments in total</translation></message>\n"
            "</context>\n</TS>\n"
        )
        with tempfile.TemporaryDirectory() as d:
            ts = Path(d) / "probe.ts"
            ts.write_text(ts_body, encoding="utf-8")
            qm = Path(d) / "probe.qm"
            subprocess.run(
                [self._LRELEASE, str(ts), "-qm", str(qm)],
                check=True, capture_output=True, timeout=60,
            )
            self.assertTrue(qm.exists(), "lrelease 应产出 .qm 文件")
            translator = QTranslator()
            self.assertTrue(translator.load(str(qm)), "QTranslator 应能加载构建产物")
            app = QApplication.instance()
            app.installTranslator(translator)
            try:
                self.assertEqual(
                    QCoreApplication.translate("CATIACopilot", "连接配置"),
                    "Connection Settings",
                )
                self.assertEqual(QCoreApplication.translate("CATIACopilot", "关闭"), "Close")
                self.assertEqual(
                    QCoreApplication.translate("CATIACopilot", "共 {0} 个附件").format(5),
                    "5 attachments in total",
                )
            finally:
                app.removeTranslator(translator)


class TestSettingsDialogTexts(unittest.TestCase):
    """设置对话框：中文回退 + 英文 patch 切换。"""

    def setUp(self) -> None:
        self.dlg, self.wb = _new_settings_dialog()

    def tearDown(self) -> None:
        _cleanup(self.dlg, self.wb)

    def test_zh_fallback(self) -> None:
        self.assertEqual(self.dlg.windowTitle(), "PLM 设置")
        titles = [w.text() for w in self.dlg.findChildren(QPushButton)]
        self.assertIn("保存配置", titles)
        self.assertIn("测试连接", titles)
        self.assertIn("浏览…", titles)

    def test_english_patch(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            dlg, wb = _new_settings_dialog()
        try:
            self.assertEqual(dlg.windowTitle(), "PLM Settings")
            titles = [w.text() for w in dlg.findChildren(QPushButton)]
            self.assertIn("Save Settings", titles)
            self.assertIn("Test Connection", titles)
        finally:
            _cleanup(dlg, wb)


class TestHistoryDialogTexts(unittest.TestCase):
    """历史对话框：窗口标题/表头/详情行中文回退 + 英文切换。"""

    def setUp(self) -> None:
        self.dlg, self.wb = _new_history_dialog()

    def tearDown(self) -> None:
        _cleanup(self.dlg, self.wb)

    def _selected(self, data: dict, patch_en: bool = False) -> str:
        item = QTableWidgetItem("t")
        item.setData(0x0100, data)  # Qt.ItemDataRole.UserRole
        if patch_en:
            with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
                self.dlg._on_selected(item, None)
        else:
            self.dlg._on_selected(item, None)
        return self.dlg._txt.toPlainText()

    def test_window_and_headers_zh(self) -> None:
        self.assertEqual(self.dlg.windowTitle(), "同步历史")
        headers = [self.dlg._tbl.horizontalHeaderItem(i).text() for i in range(7)]
        self.assertEqual(headers, ["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])

    def test_window_headers_english(self) -> None:
        # 历史表头词条（时间/用户名等）复用 main 交接表，本任务不改英文；此处验证
        # 表头渲染确实经 translate 输出（用局部英文表探测渲染链路生效）。
        local_en = {"时间": "Time", "用户名": "User Name", "同步历史": "Sync History"}
        with patch("catia_copilot.ui.plm_workbench.translate",
                   side_effect=lambda ctx, src, *a, **k: local_en.get(src, src)):
            dlg, wb = _new_history_dialog()
        try:
            self.assertEqual(dlg.windowTitle(), "Sync History")
            headers = [dlg._tbl.horizontalHeaderItem(i).text() for i in range(7)]
            self.assertEqual(headers[0], "Time")
            self.assertEqual(headers[5], "User Name")
        finally:
            _cleanup(dlg, wb)

    def test_detail_zh(self) -> None:
        text = self._selected({
            "time": "2026-09-18", "username": "alice", "sync_mode": "Push 选中",
            "created": 1, "updated": 2, "skipped": 0, "unchanged": 3, "failed": 0,
            "errors": ["<P-1> 已存在"],
        })
        for line in ("时间：2026-09-18", "用户：alice", "模式：Push 选中",
                     "新建：1", "更新：2", "无变化：3", "失败/警告详情：", "· <P-1> 已存在"):
            self.assertIn(line, text)

    def test_detail_english(self) -> None:
        text = self._selected({
            "time": "2026-09-18", "username": "alice", "sync_mode": "Push 选中",
            "created": 1, "updated": 0, "skipped": 0, "unchanged": 0, "failed": 0,
            "errors": [],
        }, patch_en=True)
        self.assertIn("Time: 2026-09-18", text)
        self.assertIn("User: alice", text)
        self.assertIn("Mode: Push 选中", text)
        # 模式值是历史记录中的业务原值，不翻译
        self.assertIn("Created: 1", text)


class TestAttachmentDialogTexts(unittest.TestCase):
    """附件对话框：mock 网络加载附件列表，标题/表格头/状态文案中英切换。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with patch("catia_copilot.plm.api_client.PlmApiClient", _FakePlmClient):
            self.dlg = _new_attachment_dialog(self._tmp.name)
            _QAPP.processEvents()  # 触发 QTimer.singleShot(0, _load_attachments)

    def tearDown(self) -> None:
        _cleanup(self.dlg)

    def test_window_title_zh(self) -> None:
        self.assertEqual(self.dlg.windowTitle(), "PLM 附件 — P-ATT / A")

    def test_attachment_list_loaded_via_fake_client(self) -> None:
        self.assertEqual(self.dlg._files, ["a.CATPart", "b.stp"], "附件应来自 mock 客户端")
        self.assertEqual(self.dlg._lst.rowCount(), 2)
        self.assertEqual(self.dlg._lbl_status.text(), "共 2 个附件")

    def test_info_label_zh(self) -> None:
        labels = self.dlg.findChildren(QLabel)
        info = next(l.text() for l in labels if l.text().startswith("零件号："))
        self.assertIn("版本：<b>A</b>", info)
        self.assertIn("迭代：<b>2</b>", info)

    def test_info_label_english_and_latest(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            with patch("catia_copilot.plm.api_client.PlmApiClient", _FakePlmClient):
                dlg = _AttachmentDialog(
                    base_url="http://plm.local", login="a", password="p",
                    workspace="WS1", part_number="P-ATT", version="A",
                    plm_data={"lastIterationNumber": None},  # 0 → “最新”
                    work_dir=self._tmp.name,
                )
                _QAPP.processEvents()
        try:
            self.assertEqual(dlg.windowTitle(), "PLM Attachments - P-ATT / A")
            labels = dlg.findChildren(QLabel)
            info = next(l.text() for l in labels if l.text().startswith("Part Number:"))
            self.assertIn("Iteration: <b>Latest</b>", info)
            self.assertEqual(dlg._lbl_status.text(), "2 attachments in total")
        finally:
            _cleanup(dlg)


class TestPullDialogTexts(unittest.TestCase):
    """Pull 对话框：标题/表头、_on_bom_done 行渲染与状态、未勾选下载警告。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dlg = _PullDialog(
            base_url="http://plm.local", login="a", password="p",
            workspace="WS1", work_dir=self._tmp.name,
        )

    def tearDown(self) -> None:
        _cleanup(self.dlg)

    def test_window_title_zh_and_english(self) -> None:
        self.assertEqual(self.dlg.windowTitle(), "Pull — 从 PLM 拉取 BOM 树文件")
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            dlg = _PullDialog(
                base_url="http://plm.local", login="a", password="p",
                workspace="WS1", work_dir=self._tmp.name,
            )
        try:
            self.assertEqual(dlg.windowTitle(), "Pull - Retrieve BOM Tree Files from PLM")
            self.assertEqual(dlg._btn_download.text(), "Download Checked Files")
        finally:
            _cleanup(dlg)

    def test_bom_done_populates_rows_zh(self) -> None:
        rows = [
            {"part_number": "P1", "version": "A", "iteration": "1", "name": "Nm",
             "check_out_user": None, "depth": 0, "_files": []},
            {"part_number": "P2", "version": "B", "iteration": "2", "name": "Nm2",
             "check_out_user": "bob", "depth": 1, "_files": []},
        ]
        self.dlg._on_bom_done(rows)
        self.assertEqual(self.dlg._tbl_bom.rowCount(), 2)
        self.assertEqual(self.dlg._tbl_bom.item(0, _PC_LOCAL).text(), "— 无")
        self.assertTrue(self.dlg._lbl_status.text().startswith("BOM 树：2 个零件"))

    def test_bom_done_status_english(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            self.dlg._on_bom_done([
                {"part_number": "P1", "version": "A", "iteration": "1", "name": "Nm",
                 "check_out_user": None, "depth": 0, "_files": []},
            ])
        self.assertTrue(
            self.dlg._lbl_status.text().startswith("BOM tree: 1 parts"),
            f"英文状态文案，实际：{self.dlg._lbl_status.text()!r}",
        )

    def test_download_without_selection_warns(self) -> None:
        with patch("catia_copilot.ui.plm_workbench.QMessageBox.warning") as warn:
            self.dlg._on_download()
        warn.assert_called_once()
        args = warn.call_args[0]
        self.assertEqual(args[1], "未选择")


if __name__ == "__main__":
    unittest.main()