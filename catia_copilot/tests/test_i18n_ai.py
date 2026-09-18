# -*- coding: utf-8 -*-
"""AI 聊天面板 i18n 改造测试（i18n Phase 4 — AI）。

覆盖目标：
1. AST 防线：ai_chat_panel.py 内全部 translate 调用 context/source 均为字符串字面量、
   context 恒为 CATIACopilot；
2. 交接表：docs/i18n-phase4-ai-translations.json 词条 == AST 提取的新 source
   （扣除既有交接表已收录词条）；译文非空、不与 source 相同、{N} 占位符集合一致、
   不得含格式说明符 {N:.x}、译文不得残留中文；
3. 真实 lupdate 提取：pyside6-lupdate 能从 ai_chat_panel.py 提取到交接表全部词条；
4. 真实 lrelease 构建 + QTranslator 加载：抽样词条经真实流水线译英；
5. 行为（offscreen）：AISettingsDialog / SessionSidebar / _TypingIndicatorWidget /
   AIChatPanel 中文回退与英文 patch 下文案切换；会话目录沙箱化为临时目录，
   避免触碰真实用户 %APPDATA% 会话数据。
"""
import ast
import json
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from shutil import which
from unittest.mock import patch

os = __import__("os")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QGroupBox, QLabel, QPushButton, QTextEdit,
)

import catia_copilot.ui.ai_chat_panel as ap  # noqa: E402
import catia_copilot.ai.session_manager as sm_mod  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "catia_copilot" / "ui" / "ai_chat_panel.py"
_AI_TRANSLATIONS = json.loads(
    (_ROOT / "docs" / "i18n-phase4-ai-translations.json").read_text(encoding="utf-8")
)

# phase4-ai 交接表以外的既有交接表合并（phase1/2/3/help），用于"新 source"归口
_EXISTING = {}
for _f in (_ROOT / "docs").glob("*.json"):
    if "phase4-ai" in _f.name:
        continue
    try:
        _d = json.loads(_f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(_d, dict):
        _EXISTING.update(_d)

_CJK = re.compile(r"[\u4e00-\u9fff]+")
# 既有表中部分词条当前为中文占位（未译），英文 patch 需本地补充
_EN_EXTRA = {
    "测试连接": "Test Connection",
    "全选": "Select All",
    "全不选": "Deselect All",
    "删除": "Delete",
    "确认删除": "Confirm Delete",
}


def _en(ctx, src, *a, **k):
    """英文 patch：先查既有占位补充，再查 phase4-ai 交接表，其余回退原文。"""
    return _EN_EXTRA.get(src, _AI_TRANSLATIONS.get(src, src))


def _placeholders(text: str) -> set:
    """提取 {N} 占位符序号集合（不含格式说明符场景）。"""
    return set(re.findall(r"\{(\d+)\}", text))


def _cleanup(*widgets) -> None:
    """close + deleteLater + processEvents 提前完成 C++ 侧析构（防 0xC0000374）。"""
    for w in widgets:
        if w is None:
            continue
        w.close()
        w.deleteLater()
    _QAPP.processEvents()


@contextmanager
def _sandboxed_sessions():
    """把 session_manager 的会话目录/索引重定向到临时目录，隔离真实用户数据。"""
    with tempfile.TemporaryDirectory() as d:
        with patch.object(sm_mod, "_SESSIONS_DIR", Path(d)), \
             patch.object(sm_mod, "_INDEX_PATH", Path(d) / "index.json"):
            yield Path(d)


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
        self.assertTrue(calls, "ai_chat_panel 应存在 i18n 词条")
        for n in calls:
            self.assertIsInstance(n.args[0], ast.Constant,
                                  f"第 {n.lineno} 行 context 必须为字符串字面量")
            self.assertEqual(n.args[0].value, "CATIACopilot",
                             f"第 {n.lineno} 行 context 应为 CATIACopilot")
            self.assertIsInstance(n.args[1], ast.Constant,
                                  f"第 {n.lineno} 行 source 必须为字符串字面量")
            self.assertTrue(any(ord(ch) > 0x2FFF for ch in n.args[1].value),
                            f"第 {n.lineno} 行 source 应为中文源文案")

    def test_required_terms_present(self) -> None:
        sources = {n.args[1].value for n in self._translate_calls()}
        for term in ("发送", "AI 助手设置", "新对话", "删除会话", "会话列表"):
            self.assertIn(term, sources)


class TestJsonHandoffTable(unittest.TestCase):
    """交接表：与 AST 提取的新 source 完全一致，内容质量约束。"""

    def _new_sources(self):
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        sources = {
            n.args[1].value for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "translate"
            and len(n.args) >= 2
            and isinstance(n.args[1], ast.Constant)
        }
        return sources - set(_EXISTING)

    def test_table_matches_new_sources(self) -> None:
        new = self._new_sources()
        tbl = set(_AI_TRANSLATIONS)
        self.assertEqual(tbl - new, set(),
                         "交接表存在代码中未使用/未统计的词条")
        self.assertEqual(new - tbl, set(),
                         "代码新增 source 未进交接表")

    def test_translations_quality(self) -> None:
        for src, en in _AI_TRANSLATIONS.items():
            self.assertTrue(en.strip(), f"{src!r} 译文不得为空")
            self.assertNotEqual(en, src, f"{src!r} 译文不得与 source 相同")
            if "{" in src:
                self.assertIn("0", _placeholders(src), f"{src!r} 应使用 {{0}} 占位符")
                # {N} 占位符集合必须一致；不得出现 {N:.x} 格式说明符（交由 format 处理）
                self.assertEqual(_placeholders(en), _placeholders(src),
                                 f"{src!r} 译文占位符集合不一致")
                self.assertNotRegex(src, r"\{[0-9]+:\.", f"{src!r} source 不得含格式说明符")
            self.assertEqual(_CJK.findall(en), [], f"{src!r} 英文译文不得含中文")

    def test_isolates_business_values(self) -> None:
        """不翻译 provider 纯英文名/配置 key：Anthropic、OpenRouter 等不得出现在交接表。"""
        src = "".join(_AI_TRANSLATIONS)
        for token in ("Anthropic", "OpenRouter", "DeepSeek", "Vertex AI",
                      "GitHub Copilot", "api_base", "api_key", "aws_region"):
            self.assertNotIn(token, src, f"业务值 {token!r} 不得出现在交接 source 中")


class TestRealLupdateExtraction(unittest.TestCase):
    """真实 pyside6-lupdate：交接表全部词条必须从 ai_chat_panel.py 提取到。"""

    _LUPDATE = which("pyside6-lupdate")

    @unittest.skipUnless(_LUPDATE, "pyside6-lupdate 不在 PATH 中，跳过真实提取回归")
    def test_ai_chat_panel_extracts_all_terms(self) -> None:
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
            missing = set(_AI_TRANSLATIONS) - extracted
            self.assertEqual(missing, set(),
                             "AI 交接表词条必须能被 lupdate 提取（缺失=%s）" % sorted(missing))


class TestRealLreleaseBuildLoad(unittest.TestCase):
    """真实 pyside6-lrelease 构建 qm 并加载：AI 词条 translate 实际返回英文。"""

    _LRELEASE = which("pyside6-lrelease")

    @unittest.skipUnless(_LRELEASE, "pyside6-lrelease 不在 PATH 中，跳过真实构建回归")
    def test_sample_terms_translate_via_real_qm(self) -> None:
        samples = [("发送", "Send"), ("AI 助手设置", "AI Assistant Settings"),
                   ("{0}  AI 思考中…", "{0}  AI is thinking…")]
        ts_body = '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS><TS version="2.1" language="en_US">\n'
        ts_body += "<context>\n<name>CATIACopilot</name>\n"
        for src, en in samples:
            ts_body += f"<message><source>{src}</source><translation>{en}</translation></message>\n"
        ts_body += "</context>\n</TS>\n"
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
                for src, en in samples:
                    self.assertEqual(
                        QCoreApplication.translate("CATIACopilot", src).format("◦"),
                        en.replace("{0}", "◦"),
                    )
            finally:
                _QAPP.removeTranslator(translator)


class TestAISettingsDialogTexts(unittest.TestCase):
    """行为：offscreen 构造 AISettingsDialog，文案中英切换。"""

    def test_zh_fallback(self) -> None:
        dlg = ap.AISettingsDialog()
        try:
            self.assertEqual(dlg.windowTitle(), "AI 助手设置")
            groups = [g.title() for g in dlg.findChildren(QGroupBox)]
            self.assertEqual(groups, ["凭证", "模型"])
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            self.assertIn("测试连接", buttons)
            self.assertIn("从 API 获取", buttons)
            for lab in dlg.findChildren(QLabel):
                if "最确定" in lab.text():
                    self.assertIn("0=最确定", lab.text())
                    break
            else:
                self.fail("应存在温度说明 label（0=最确定）")
        finally:
            _cleanup(dlg)

    def test_english_live(self) -> None:
        with patch.object(ap, "translate", side_effect=_en):
            dlg = ap.AISettingsDialog()
        try:
            self.assertEqual(dlg.windowTitle(), "AI Assistant Settings")
            groups = [g.title() for g in dlg.findChildren(QGroupBox)]
            self.assertEqual(groups, ["Credentials", "Models"])
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            self.assertIn("Test Connection", buttons)
            self.assertIn("Fetch from API", buttons)
            labels = [lab.text() for lab in dlg.findChildren(QLabel)]
            self.assertTrue(any("0=deterministic" in t for t in labels),
                            "温度说明应译为英文")
            self.assertTrue(any("Max tool call rounds:" in t for t in labels),
                            "最大工具调用轮数行标签应译为英文")
        finally:
            _cleanup(dlg)


class TestSessionSidebarTexts(unittest.TestCase):
    """行为：offscreen 构造 SessionSidebar，会话目录沙箱化，文案中英切换。"""

    def test_zh_fallback(self) -> None:
        with _sandboxed_sessions():
            sb = ap.SessionSidebar(sm_mod.SessionManager())
        try:
            labels = [lab.text() for lab in sb.findChildren(QLabel)]
            self.assertIn("会话列表", labels)
            btn = [b.text() for b in sb.findChildren(QPushButton)]
            self.assertIn("新对话", btn)
        finally:
            _cleanup(sb)

    def test_english_live(self) -> None:
        with patch.object(ap, "translate", side_effect=_en):
            with _sandboxed_sessions():
                sb = ap.SessionSidebar(sm_mod.SessionManager())
        try:
            labels = [lab.text() for lab in sb.findChildren(QLabel)]
            self.assertIn("Sessions", labels)
            btn = [b.text() for b in sb.findChildren(QPushButton)]
            self.assertIn("New Chat", btn)
        finally:
            _cleanup(sb)

    def test_item_tooltip_zh_and_en(self) -> None:
        with _sandboxed_sessions() as d:
            (d / "index.json").write_text(
                json.dumps([{"session_id": "s1", "name": "测试会话",
                             "created_at": "2026-01-01", "workspace": None}]),
                encoding="utf-8",
            )
            sb = ap.SessionSidebar(sm_mod.SessionManager())
            sb.refresh()
            item = sb._list.item(0)
            self.assertEqual(item.text(), "测试会话")
            self.assertEqual(
                item.toolTip(),
                "ID: s1\n创建：2026-01-01\n工作空间：不限制",
            )
            with patch.object(ap, "translate", side_effect=_en):
                sb.refresh()
                item = sb._list.item(0)
                self.assertEqual(item.text(), "测试会话")
                self.assertEqual(
                    item.toolTip(),
                    "ID: s1\nCreated: 2026-01-01\nWorkspace: Unrestricted",
                )
            _cleanup(sb)


class TestTypingIndicatorTexts(unittest.TestCase):
    """行为：_TypingIndicatorWidget 各状态文案中英切换。"""

    def _make(self, state, detail=None):
        w = ap._TypingIndicatorWidget()
        w.set_state(state, detail=detail)
        return w

    def test_zh_fallback(self) -> None:
        for state, expect in (("thinking", "AI 思考中"), ("gen", "生成回复中"),
                              ("tool", "执行工具：get_open_documents")):
            w = self._make(state, detail="get_open_documents")
            try:
                self.assertIn(expect, w._label.text(), f"{state} 中文文案")
            finally:
                w.stop_animation()
                _cleanup(w)

    def test_english_live(self) -> None:
        with patch.object(ap, "translate", side_effect=_en):
            w = self._make("tool", detail="get_open_documents")
        try:
            self.assertIn("Running tool: get_open_documents", w._label.text())
        finally:
            w.stop_animation()
            _cleanup(w)


class TestAIChatPanelTexts(unittest.TestCase):
    """行为：offscreen 构造 AIChatPanel（会话目录沙箱化），工具栏文案中英切换。"""

    def test_zh_fallback(self) -> None:
        with _sandboxed_sessions():
            panel = ap.AIChatPanel()
        try:
            self.assertEqual(panel._send_btn.text(), "发送")
            self.assertEqual(
                panel._input_box.placeholderText(),
                "输入消息... (Ctrl+Enter 发送，Enter 换行)",
            )
            sidebar_btns = [b.text() for b in panel._sidebar.findChildren(QPushButton)]
            self.assertIn("新对话", sidebar_btns)
            self.assertEqual(panel._settings_btn.text(), "⚙ 全局设置")
            tips = [b.toolTip() for b in panel.findChildren(QPushButton)]
            self.assertTrue(any("模型状态 — 查看当前零件" in t for t in tips),
                            "模型状态按钮 tooltip 应为中文")
        finally:
            _cleanup(panel)

    def test_english_live(self) -> None:
        with patch.object(ap, "translate", side_effect=_en):
            with _sandboxed_sessions():
                panel = ap.AIChatPanel()
        try:
            self.assertEqual(panel._send_btn.text(), "Send")
            self.assertIn("Type a message...", panel._input_box.placeholderText())
            sidebar_btns = [b.text() for b in panel._sidebar.findChildren(QPushButton)]
            self.assertIn("New Chat", sidebar_btns)
            self.assertEqual(panel._settings_btn.text(), "⚙ Global Settings")
            tips = [b.toolTip() for b in panel.findChildren(QPushButton)]
            self.assertTrue(any("Model State - view the current part" in t for t in tips),
                            "模型状态按钮 tooltip 应为英文")
        finally:
            _cleanup(panel)

    def test_session_cfg_tooltip_zh_and_en(self) -> None:
        with _sandboxed_sessions():
            panel = ap.AIChatPanel()
            panel._update_session_title_tooltip()
            self.assertIn("会话设置（模型、上下文长度、工作空间）", panel._session_cfg_btn.toolTip())
            self.assertIn("当前工作空间：不限制", panel._session_cfg_btn.toolTip())
            with patch.object(ap, "translate", side_effect=_en):
                panel._update_session_title_tooltip()
                self.assertIn("Session settings (model, context length, workspace)",
                              panel._session_cfg_btn.toolTip())
                self.assertIn("Current workspace: Unrestricted", panel._session_cfg_btn.toolTip())
            _cleanup(panel)


if __name__ == "__main__":
    unittest.main()