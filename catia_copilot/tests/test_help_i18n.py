# -*- coding: utf-8 -*-
"""帮助对话框 i18n 改造测试（i18n Phase 4 — Help）。

覆盖目标：
1. AST 防线：help_dialog.py 内全部 translate 调用 context/source 均为字符串字面量
   且 context 恒为 CATIACopilot；
2. 按语言资源：current_ui_language()=zh_CN 返回中文源文档、=en_US 返回英文全量
   译版；两份资源 h2/h3/h4/table/ul/ol/img 结构计数完全一致（保持帮助结构），
   <img src="inertia_keep_params.png" style="max-width: 480px"> 图片路径与样式一致；
3. 不翻译真实标识：analyze 代码、惯量包络体.N/质量/密度/Gx/IoxG… 参数名、
   ChangFangSong.ttf/ISO.xml/macros/drawing_templates/.mpd/.catvbs/CATIA.Application
   /CNEXT.exe/3DEXPERIENCE 等原样保留；除白名单中文 token 外英文版不得残留其他中文；
4. 真实 lupdate 提取：pyside6-lupdate 从 help_dialog.py 提取到交接表全部词条；
5. 真实 lrelease 构建 + QTranslator 加载：窗口标题与关闭按钮经真实流水线译英；
6. 行为：offscreen 构造 HelpDialog，中文回退标题/关闭/正文中文，英文 patch 下
   标题/关闭/正文英文。
"""
import ast
import json
import os
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QTextBrowser  # noqa: E402

from catia_copilot.ui import help_dialog  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "catia_copilot" / "ui" / "help_dialog.py"
_HELP_TRANSLATIONS = json.loads(
    (_ROOT / "docs" / "i18n-phase4-help-translations.json").read_text(encoding="utf-8")
)

# 英文帮助允许保留的中文 token：CATIA 真实参数名 / 程序写回 CATIA 的自定义属性名
_CN_ALLOWLIST = {
    "惯量包络体", "质量", "密度",
    "物料编码", "物料名称", "规格型号", "物料来源", "数据状态", "存货类别", "重量", "备注",
}

_CJK = re.compile(r"[\u4e00-\u9fff]+")


def _en(ctx, src, *a, **k):
    """英文 patch：帮助交接表命中返回英文译文；"关闭"已收录于既有交接表，
    本地按既有译文补充；其余回退原文。"""
    _extra = {"关闭": "Close"}
    return _extra.get(src, _HELP_TRANSLATIONS.get(src, src))


def _cleanup(*widgets) -> None:
    """close + deleteLater + processEvents 提前完成 C++ 侧析构（防 0xC0000374）。"""
    for w in widgets:
        if w is None:
            continue
        w.close()
        w.deleteLater()
    _QAPP.processEvents()


def _new_dialog() -> "help_dialog.HelpDialog":
    return help_dialog.HelpDialog()


class TestTranslateLiterals(unittest.TestCase):
    """AST 防线：translate 前两参均为字符串字面量、context 恒为 CATIACopilot。"""

    def _translate_calls(self) -> list:
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "translate"
        ]

    def test_translate_sources_are_literals(self) -> None:
        calls = self._translate_calls()
        self.assertTrue(calls, "help_dialog 应存在 i18n 词条")
        for n in calls:
            self.assertIsInstance(n.args[0], ast.Constant,
                                  f"第 {n.lineno} 行 context 必须为字符串字面量")
            self.assertEqual(n.args[0].value, "CATIACopilot",
                             f"第 {n.lineno} 行 context 应为 CATIACopilot")
            self.assertIsInstance(n.args[1], ast.Constant,
                                  f"第 {n.lineno} 行 source 必须为字符串字面量")

    def test_required_terms_present(self) -> None:
        sources = {n.args[1].value for n in self._translate_calls()}
        self.assertIn("{0} — 帮助文档", sources)
        self.assertIn("关闭", sources)


class TestHtmlResources(unittest.TestCase):
    """按语言资源：中文源文档 / 英文全量译版，结构与图片路径一致。"""

    def test_both_html_resources_present(self) -> None:
        text = _SRC.read_text(encoding="utf-8")
        self.assertIn("_HELP_HTML = f", text)
        self.assertIn("_HELP_HTML_EN = f", text)

    def test_structure_counts_match(self) -> None:
        for tag in ("<h2>", "<h3>", "<h4>", "<table", "<ul>", "<ol>", "<img"):
            self.assertEqual(
                help_dialog._HELP_HTML.count(tag),
                help_dialog._HELP_HTML_EN.count(tag),
                f"{tag} 数量 CN/EN 应一致（保持帮助结构）",
            )

    def test_image_path_and_style_preserved(self) -> None:
        for html in (help_dialog._HELP_HTML, help_dialog._HELP_HTML_EN):
            self.assertIn('src="inertia_keep_params.png"', html)
            self.assertIn('style="max-width: 480px"', html)

    def test_en_keeps_catia_identifiers(self) -> None:
        en = help_dialog._HELP_HTML_EN
        for token in (
            "analyze.mass", "analyze.get_gravity_center()", "analyze.get_inertia()",
            "analyze.volume", "ReferenceProduct.Parent.Product", "Position.GetComponents()",
            "CATIA.Application", "CNEXT.exe", "3DEXPERIENCE", "ProgID", "CLSID", "ROT",
            "惯量包络体.1", "惯量包络体.N", "惯量包络体.x",
            "ChangFangSong.ttf", "ISO.xml", "drawing_templates", "macros",
            ".mpd", ".catvbs", ".catscript", ".catvba", "CATPart", "CATProduct",
            "CATDrawing", "PartNumber", "Nomenclature", "Revision",
        ):
            self.assertIn(token, en, f"真实标识 {token!r} 必须在英文帮助中原样保留")

    def test_en_has_no_untracked_cjk(self) -> None:
        plain = re.sub(r"<[^>]+>", "", help_dialog._HELP_HTML_EN)
        tokens = set(_CJK.findall(plain))
        outside = tokens - _CN_ALLOWLIST
        self.assertEqual(outside, set(),
                         "英文帮助除真实属性名外不得残留其他中文，实际=%s" % sorted(outside))

    def test_zh_source_injected_version(self) -> None:
        self.assertIn("帮助文档", help_dialog._HELP_HTML)

    def test_selector_respects_ui_language(self) -> None:
        with patch.object(help_dialog, "current_ui_language", return_value="zh_CN"):
            self.assertIs(help_dialog._help_html(), help_dialog._HELP_HTML)
        with patch.object(help_dialog, "current_ui_language", return_value="en_US"):
            self.assertIs(help_dialog._help_html(), help_dialog._HELP_HTML_EN)
        with patch.object(help_dialog, "current_ui_language", return_value="fr_FR"):
            self.assertIs(help_dialog._help_html(), help_dialog._HELP_HTML)


class TestRealLupdateExtraction(unittest.TestCase):
    """真实 pyside6-lupdate：帮助交接表全部词条必须从 help_dialog.py 提取到。"""

    _LUPDATE = which("pyside6-lupdate")

    @unittest.skipUnless(_LUPDATE, "pyside6-lupdate 不在 PATH 中，跳过真实提取回归")
    def test_help_dialog_extracts_all_terms(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ts = Path(d) / "out.ts"
            subprocess.run(
                [self._LUPDATE, "-extensions", "py", "-no-obsolete",
                 str(_SRC), "-ts", str(ts)],
                check=True, capture_output=True, timeout=120,
            )
            self.assertTrue(ts.exists(), "lupdate 应产出 .ts 文件")
            tree = ET.parse(ts)
            contexts = {c.findtext("name"): c for c in tree.getroot().findall("context")}
            self.assertIn("CATIACopilot", contexts, "提取结果必须含 CATIACopilot context")
            extracted = {
                m.find("source").text
                for m in contexts["CATIACopilot"].findall("message")
                if m.find("source") is not None
            }
            missing = set(_HELP_TRANSLATIONS) - extracted
            self.assertEqual(missing, set(),
                             "帮助交接表词条必须能被 lupdate 提取（缺失=%s）" % sorted(missing))


class TestRealLreleaseBuildLoad(unittest.TestCase):
    """真实 pyside6-lrelease 构建 qm 并加载：help 词条 translate 实际返回英文。"""

    _LRELEASE = which("pyside6-lrelease")

    @unittest.skipUnless(_LRELEASE, "pyside6-lrelease 不在 PATH 中，跳过真实构建回归")
    def test_title_and_close_translate_via_real_qm(self) -> None:
        src = "{0} — 帮助文档"
        translation = _HELP_TRANSLATIONS[src]
        ts_body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE TS><TS version="2.1" language="en_US">\n'
            "<context>\n<name>CATIACopilot</name>\n"
            f"<message><source>{src}</source><translation>{translation}</translation></message>\n"
            "<message><source>关闭</source><translation>Close</translation></message>\n"
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
            _QAPP.installTranslator(translator)
            try:
                self.assertEqual(
                    QCoreApplication.translate("CATIACopilot", src).format("CATIA Copilot"),
                    "CATIA Copilot - Help Documentation",
                )
                self.assertEqual(
                    QCoreApplication.translate("CATIACopilot", "关闭"),
                    "Close",
                )
            finally:
                _QAPP.removeTranslator(translator)


class TestHelpDialogTexts(unittest.TestCase):
    """行为：offscreen 构造 HelpDialog，标题/关闭按钮/正文语言切换。"""

    def test_zh_fallback(self) -> None:
        dlg = _new_dialog()
        try:
            self.assertEqual(dlg.windowTitle(), "CATIA Copilot — 帮助文档")
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            self.assertIn("关闭", buttons)
            browser = dlg.findChild(QTextBrowser)
            text = browser.toPlainText()
            self.assertIn("帮助文档", text)
            self.assertIn("运行环境要求", text)
            self.assertIn("常见问题", text)
        finally:
            _cleanup(dlg)

    def test_english_live(self) -> None:
        with patch.object(help_dialog, "current_ui_language", return_value="en_US"):
            with patch.object(help_dialog, "translate", side_effect=_en):
                dlg = _new_dialog()
        try:
            self.assertEqual(dlg.windowTitle(), "CATIA Copilot - Help Documentation")
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            self.assertIn("Close", buttons)
            browser = dlg.findChild(QTextBrowser)
            text = browser.toPlainText()
            self.assertIn("Help Documentation", text)
            self.assertIn("Overview", text)
            self.assertIn("Requirements", text)
            self.assertIn("FAQ", text)
        finally:
            _cleanup(dlg)


if __name__ == "__main__":
    unittest.main()