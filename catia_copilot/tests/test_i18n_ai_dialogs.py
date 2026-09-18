# -*- coding: utf-8 -*-
"""AI 可达的附属 UI i18n 改造测试（i18n Phase 4 — AI 对话框）。

覆盖对象：ui/session_config_dialog.py、ui/model_state_dialog.py、ui/log_window.py。

覆盖目标：
1. AST 防线：三个文件内全部 translate 调用 context/source 均为字符串字面量、
   context 恒为 CATIACopilot、source 含非 ASCII；
2. 交接表：docs/i18n-phase4-ai-dialogs-translations.json 词条 == AST 提取的新 source
   （扣除既有交接表已收录词条）；译文非空、≠ source、{N} 占位符集合一致、
   不得含格式说明符 {N:.x}、译文不得残留中文；
3. 业务值隔离：model_state 状态字段（part_name / features / step / status / mass_kg /
   cog_mm / success）与 session_config 配置 key（temperature / max_context_messages /
   workspace / userData）不得进交接表；“ok” 状态判定与 “使用全局默认” 判定保持中文
   原值（仅显示映射）；
4. 真实 lupdate 提取：三个源文件能提取到交接表全部词条；
5. 真实 lrelease 构建 + QTranslator 加载：抽样词条经真实流水线译英；
6. 行为（offscreen）：SessionConfigDialog / ModelStateDialog / LogWindow 中英文本切换；
   ModelStateDialog 的 QSettings 持久化重定向到临时 ini（不写真实注册表）；
   SessionConfigDialog 的 ai_config 读取 mock（不读真实 %APPDATA% ai_config.json）；
   log_window 导入期 Path.home 重定向到临时目录（不创建/写真实 ~/CATIA_Copilot/logs）。
"""
import ast
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from pathlib import Path
from shutil import which
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# log_window → logging_setup 在导入期就创建 ~/CATIA_Copilot/logs 与日志文件，
# 先把 Path.home() 重定向到临时目录再导入，避免真实持久化污染，导入后立即恢复。
_TMP_HOME = Path(tempfile.mkdtemp(prefix="i18n_ai_dialogs_home_"))
atexit.register(shutil.rmtree, _TMP_HOME, ignore_errors=True)
_ORIG_PATH_HOME = Path.home
Path.home = classmethod(lambda cls: _TMP_HOME)

from PySide6.QtCore import QCoreApplication, QSettings, QTranslator  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton,
)

from catia_copilot.ai import config as ai_config  # noqa: E402
from catia_copilot.ai.session import ChatSession  # noqa: E402
from catia_copilot.ui import log_window as lw  # noqa: E402

Path.home = _ORIG_PATH_HOME

from catia_copilot.ui import model_state_dialog as msd  # noqa: E402
from catia_copilot.ui import session_config_dialog as sd  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

_ROOT = Path(__file__).resolve().parents[2]
_FILES = {
    "session_config": _ROOT / "catia_copilot" / "ui" / "session_config_dialog.py",
    "model_state": _ROOT / "catia_copilot" / "ui" / "model_state_dialog.py",
    "log_window": _ROOT / "catia_copilot" / "ui" / "log_window.py",
}
_HANDOFF = json.loads(
    (_ROOT / "docs" / "i18n-phase4-ai-dialogs-translations.json").read_text(
        encoding="utf-8"
    )
)

# 既有交接表合并（phase1/2/3/4-ai/4-help），用于"新 source"归口
_EXISTING = {}
for _f in (_ROOT / "docs").glob("*.json"):
    if "phase4-ai-dialogs" in _f.name:
        continue
    try:
        _d = json.loads(_f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(_d, dict):
        _EXISTING.update(_d)

_CJK = re.compile(r"[\u4e00-\u9fff]+")
# 既有表中个别词条为中文占位（未译），英文行为 patch 需本地补充
_EN_EXTRA = {
    "浏览…": "Browse…",
}


def _en(ctx, src, *a, **k):
    """英文 patch：先查本地补充，再查本次交接表，再退回既有表，否则回退原文。"""
    v = _EN_EXTRA.get(src)
    if v is not None:
        return v
    v = _HANDOFF.get(src)
    if v is not None:
        return v
    return _EXISTING.get(src, src)


def _placeholders(text: str) -> set:
    """提取 {N} 占位符序号集合（不含格式说明符场景）。"""
    return set(re.findall(r"\{(\d+)\}", text))


def _translate_sources(path: Path) -> set:
    """AST 提取指定文件全部 translate() 的 source 字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.args[1].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "translate"
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
    }


def _cleanup(*widgets) -> None:
    """close + deleteLater + processEvents 提前完成 C++ 侧析构（防 0xC0000374）。

    ModelStateDialog 带 WA_DeleteOnClose，close 可能已走删除路径，
    deleteLater 需容忍 RuntimeError（C++ 对象已销毁）。
    """
    for w in widgets:
        if w is None:
            continue
        try:
            w.close()
        except RuntimeError:
            pass
        try:
            w.deleteLater()
        except RuntimeError:
            pass
    _QAPP.processEvents()


@contextmanager
def _temp_settings(settings_class, d):
    """ModelStateDialog 的 QSettings 重定向到临时 ini，避免污染真实注册表。"""
    qs = QSettings(str(Path(d) / "msd.ini"), QSettings.Format.IniFormat)
    with patch.object(settings_class, "_settings", return_value=qs):
        yield qs


@contextmanager
def _session_config_env(en: bool = False):
    """mock ai_config 读取，避免读真实 %APPDATA% 配置；en=True 时另 patch translate。"""
    ctxs = [
        patch.object(ai_config, "load", return_value={}),
        patch.object(ai_config, "list_model_ids", return_value=[]),
        patch.object(ai_config, "get_default_model_id", return_value="gpt-4o"),
    ]
    if en:
        ctxs.append(patch.object(sd, "translate", side_effect=_en))
    with ExitStack() as st:
        for c in ctxs:
            st.enter_context(c)
        yield


class TestTranslateLiterals(unittest.TestCase):
    """AST 防线：translate 前两参均为字符串字面量、context 恒为 CATIACopilot。"""

    def _calls(self, path: Path) -> list:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "translate"
        ]

    def test_sources_are_literals(self) -> None:
        for name, path in _FILES.items():
            calls = self._calls(path)
            self.assertTrue(calls, f"{name} 应存在 i18n 词条")
            for n in calls:
                self.assertIsInstance(
                    n.args[0], ast.Constant,
                    f"{name}:{n.lineno} context 必须为字符串字面量",
                )
                self.assertEqual(
                    n.args[0].value, "CATIACopilot",
                    f"{name}:{n.lineno} context 应为 CATIACopilot",
                )
                self.assertIsInstance(
                    n.args[1], ast.Constant,
                    f"{name}:{n.lineno} source 必须为字符串字面量",
                )
                self.assertTrue(
                    any(ord(ch) > 0x2FFF for ch in n.args[1].value),
                    f"{name}:{n.lineno} source 应含非 ASCII 源文案",
                )

    def test_required_terms_present(self) -> None:
        for name, required in self._required().items():
            sources = _translate_sources(_FILES[name])
            missing = required - sources
            self.assertEqual(missing, set(), f"{name} 缺关键词条: {sorted(missing)}")

    @staticmethod
    def _required() -> dict:
        return {
            "session_config": {
                "会话：{0}",
                "使用全局默认（{0}）",
                "未设置",
                "重置所有字段为默认值",
                "清空本会话消息记录…",
                "确定要清空会话「{0}」的所有对话历史吗？\n此操作不可撤销。",
            },
            "model_state": {
                "模型状态",
                "模型状态 — 失败",
                "零件几何体",
                "无特征",
                "—（未赋材料或无数据）",
                "— 无步骤记录 —",
                "质量：",
            },
            "log_window": {
                "打开日志文件",
                "无法打开日志文件",
                "无法打开日志文件：\n{0}\n\n{1}",
            },
        }


class TestJsonHandoffTable(unittest.TestCase):
    """交接表：与三文件 AST 提取的新 source 完全一致，内容质量约束。"""

    def _new_sources(self) -> set:
        new = set()
        for path in _FILES.values():
            new |= _translate_sources(path)
        return new - set(_EXISTING)

    def test_table_matches_new_sources(self) -> None:
        new = self._new_sources()
        tbl = set(_HANDOFF)
        self.assertEqual(tbl - new, set(), "交接表存在代码中未使用/未统计的词条")
        self.assertEqual(new - tbl, set(), "代码新增 source 未进交接表")

    def test_translations_quality(self) -> None:
        for src, en in _HANDOFF.items():
            self.assertTrue(en.strip(), f"{src!r} 译文不得为空")
            self.assertNotEqual(en, src, f"{src!r} 译文不得与 source 相同")
            if "{" in src:
                self.assertIn("0", _placeholders(src),
                              f"{src!r} 应使用 {0} 占位符")
                self.assertEqual(_placeholders(en), _placeholders(src),
                                 f"{src!r} 译文占位符集合不一致")
                self.assertNotRegex(src, r"\{[0-9]+:\.", f"{src!r} 不得含格式说明符")
            self.assertEqual(_CJK.findall(en), [], f"{src!r} 英文译文不得含中文")

    def test_isolates_business_values(self) -> None:
        """状态字段/配置 key 等业务值不得出现在交接表。"""
        text = "".join(_HANDOFF.keys()) + "".join(_HANDOFF.values())
        for token in ("part_name", "features", "steps", "status", "success",
                      "mass_kg", "cog_mm", "temperature", "max_context_messages",
                      "workspace", "userData", "ChatSession"):
            self.assertNotIn(token, text, f"业务值 {token!r} 不得出现在交接表")

    def test_model_state_business_judgment_preserved(self) -> None:
        """“ok” 状态判定供业务判断，不得翻译；✓/✗ 仅作显示映射。"""
        code = _FILES["model_state"].read_text(encoding="utf-8")
        self.assertIn('status == "ok"', code,
                      "model_state 的 ok 状态判定必须保持原值")
        joined = "".join(_HANDOFF.keys()) + "".join(_HANDOFF.values())
        self.assertNotIn("ok", re.sub(r"[\u4e00-\u9fff]+", "", joined),
                         "“ok” 判定值不得进入交接表")
        # 显示映射（非翻译）仍在：symbol 由 status 判定产生
        self.assertIn('symbol = "\u2713" if status == "ok" else "\u2717"', code,
                      "✓/✗ 符号映射不应被改动")

    def test_session_config_default_judgment_not_chinese(self) -> None:
        """_apply_and_accept 对“使用全局默认”的判定与下拉第 0 项显示文本比较（随语言）。"""
        code = _FILES["session_config"].read_text(encoding="utf-8")
        self.assertIn("itemText(0)", code,
                      "默认模型判定必须与下拉第 0 项显示文本比较")
        self.assertNotIn('startswith("使用全局默认")', code,
                         "判定不得再依赖中文字面量")


class TestRealLupdateExtraction(unittest.TestCase):
    """真实 pyside6-lupdate：交接表全部词条必须从三个源文件提取到。"""

    _LUPDATE = which("pyside6-lupdate")

    @unittest.skipUnless(_LUPDATE, "pyside6-lupdate 不在 PATH 中，跳过真实提取回归")
    def test_three_files_extract_all_terms(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ts = Path(d) / "out.ts"
            subprocess.run(
                [self._LUPDATE, "-extensions", "py", "-no-obsolete",
                 *[str(p) for p in _FILES.values()], "-ts", str(ts)],
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
            missing = set(_HANDOFF) - extracted
            self.assertEqual(missing, set(),
                             "交接表词条必须能被 lupdate 提取（缺失=%s）" % sorted(missing))


class TestRealLreleaseBuildLoad(unittest.TestCase):
    """真实 pyside6-lrelease 构建 qm 并加载：抽样词条 translate 实际返回英文。"""

    _LRELEASE = which("pyside6-lrelease")
    # 覆盖会话配置 / 建模状态 / 日志窗口三类文案（含占位符与换行）
    _SAMPLES = [
        ("使用全局默认（{0}）", "Use global default ({0})"),
        ("模型状态 — 失败", "Model State - Failed"),
        ("—（未赋材料或无数据）", "— (no material assigned or no data)"),
        ("确定要清空会话「{0}」的所有对话历史吗？\n此操作不可撤销。",
         "Are you sure you want to clear all chat history for session \"{0}\"?\n"
         "This action cannot be undone."),
        ("无法打开日志文件：\n{0}\n\n{1}",
         "Unable to open the log file:\n{0}\n\n{1}"),
    ]

    @unittest.skipUnless(_LRELEASE, "pyside6-lrelease 不在 PATH 中，跳过真实构建回归")
    def test_sample_terms_translate_via_real_qm(self) -> None:
        ts_body = '<?xml version="1.0" encoding="utf-8"?>\n'
        ts_body += '<!DOCTYPE TS><TS version="2.1" language="en_US">\n'
        ts_body += "<context>\n<name>CATIACopilot</name>\n"
        for src, en in self._SAMPLES:
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
                for src, en in self._SAMPLES:
                    self.assertEqual(
                        QCoreApplication.translate("CATIACopilot", src), en,
                        f"{src!r} 经真实流水线应返回英文",
                    )
            finally:
                _QAPP.removeTranslator(translator)


class TestSessionConfigDialogTexts(unittest.TestCase):
    """行为：offscreen 构造 SessionConfigDialog，文案中英切换 + 业务写回保持。"""

    def test_zh_fallback(self) -> None:
        with _session_config_env():
            sess = ChatSession(session_id="s1", name="测试会话")
            dlg = sd.SessionConfigDialog(sess)
        try:
            self.assertEqual(dlg.windowTitle(), "会话设置")
            labels = [lab.text() for lab in dlg.findChildren(QLabel)]
            self.assertIn("会话：测试会话", labels)
            self.assertIn("未设置", labels)
            self.assertEqual(dlg._model_combo.itemText(0), "使用全局默认（gpt-4o）")
            self.assertEqual(dlg._ws_edit.placeholderText(),
                             "留空 = 不限制（可访问任意路径）")
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            for t in ("重置所有字段为默认值", "清空本会话消息记录…", "清除", "浏览…"):
                self.assertIn(t, buttons, f"中文按钮 {t!r} 应存在")
        finally:
            _cleanup(dlg)

    def test_english_live(self) -> None:
        with _session_config_env(en=True):
            sess = ChatSession(session_id="s1", name="Test Session")
            dlg = sd.SessionConfigDialog(sess)
        try:
            self.assertEqual(dlg.windowTitle(), "Session Settings")
            labels = [lab.text() for lab in dlg.findChildren(QLabel)]
            self.assertIn("Session: Test Session", labels)
            self.assertIn("Unset", labels)
            self.assertEqual(dlg._model_combo.itemText(0), "Use global default (gpt-4o)")
            self.assertEqual(dlg._ws_edit.placeholderText(),
                             "Leave empty = unrestricted (can access any path)")
            buttons = [b.text() for b in dlg.findChildren(QPushButton)]
            for t in ("Reset all fields to defaults", "Clear this session's chat history…",
                      "Clear", "Browse…"):
                self.assertIn(t, buttons, f"英文按钮 {t!r} 应存在")
        finally:
            _cleanup(dlg)

    def test_apply_writes_business_values(self) -> None:
        """_apply_and_accept 写回业务值不受翻译影响（默认模型 → ""、None 温度等）。"""
        with _session_config_env():
            sess = ChatSession(session_id="s1", name="测试会话")
            dlg = sd.SessionConfigDialog(sess)
        try:
            dlg._model_combo.setCurrentIndex(0)
            dlg._temp_slider.setValue(0)
            dlg._ctx_spin.setValue(42)
            dlg._ws_edit.setText("D:/ws")
            dlg._apply_and_accept()
            self.assertEqual(sess.model, "")
            self.assertIsNone(sess.config["temperature"])
            self.assertEqual(sess.config["max_context_messages"], 42)
            self.assertEqual(sess.workspace, "D:/ws")
        finally:
            _cleanup(dlg)

    def test_apply_typed_global_default_text_maps_to_empty(self) -> None:
        """手动输入与第 0 项（使用全局默认）显示文本一致 → 写回全局默认（""）。

        中英文各跑一遍：判定必须与随语言的 itemText(0) 比较，而非中文字面量。
        """
        for en, sid in ((False, "s1"), (True, "s2")):
            with _session_config_env(en=en):
                sess = ChatSession(session_id=sid,
                                   name="Test Session" if en else "测试会话")
                dlg = sd.SessionConfigDialog(sess)
            try:
                combo = dlg._model_combo
                combo.setCurrentIndex(-1)  # 取消选择 → currentData() 为 None（手工输入态）
                combo.setEditText(combo.itemText(0))
                dlg._apply_and_accept()
                self.assertEqual(sess.model, "",
                                 f"en={en}：等于第 0 项文本应写回全局默认")
            finally:
                _cleanup(dlg)

    def test_apply_typed_custom_model_id_written_back(self) -> None:
        """英文界面手动输入自定义模型 ID → 原样写回（不再按中文前缀过滤）。"""
        with _session_config_env(en=True):
            sess = ChatSession(session_id="s3", name="Test Session")
            dlg = sd.SessionConfigDialog(sess)
        try:
            combo = dlg._model_combo
            combo.setCurrentIndex(-1)
            combo.setEditText("claude-sonnet-4")
            dlg._apply_and_accept()
            self.assertEqual(sess.model, "claude-sonnet-4")
        finally:
            _cleanup(dlg)


class TestModelStateDialogTexts(unittest.TestCase):
    """行为：offscreen 构造 ModelStateDialog，QSettings 重定向临时 ini。"""

    def test_zh_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as d, _temp_settings(msd.ModelStateDialog, d):
            dlg = msd.ModelStateDialog()
            try:
                labels = [lab.text() for lab in dlg.findChildren(QLabel)]
                for t in ("模型状态", "特征树", "质量属性", "质量：", "重心：", "步骤日志"):
                    self.assertIn(t, labels, f"中文 label {t!r} 应存在")
                buttons = [b.text() for b in dlg.findChildren(QPushButton)]
                self.assertIn("关闭", buttons, "中文关闭按钮应存在")

                dlg.set_state({"success": False})
                self.assertEqual(dlg._title_label.text(), "模型状态 — 失败")

                dlg.set_state({
                    "success": True, "part_name": "测试零件",
                    "features": ["Pad.1", "拉伸"],
                    "mass_kg": 1.2, "cog_mm": [1.0, 2.0, 3.0],
                    "steps": [{"step": "创建凸台", "status": "ok"},
                              {"step": "失败步骤", "status": "failed"}],
                })
                self.assertEqual(dlg._title_label.text(), "模型状态 — 测试零件")
                root = dlg._feat_tree.topLevelItem(0)
                self.assertEqual(root.text(0), "零件几何体")
                self.assertEqual(root.child(0).text(0), "Pad.1")
                self.assertEqual(root.child(1).text(0), "拉伸")
                self.assertEqual(dlg._mass_value.text(), "1.200 kg")
                self.assertEqual(dlg._cog_value.text(), "(1.0, 2.0, 3.0) mm")
                self.assertEqual(dlg._steps_log.toPlainText(),
                                 "[01] \u2713 创建凸台\n[02] \u2717 失败步骤")

                dlg.set_state({"success": True})
                self.assertEqual(dlg._title_label.text(), "模型状态 — 无数据")
                self.assertEqual(dlg._feat_tree.topLevelItem(0).text(0), "无特征")
                self.assertEqual(dlg._mass_value.text(), "—（未赋材料或无数据）")
                self.assertEqual(dlg._steps_log.toPlainText(), "— 无步骤记录 —")
            finally:
                _cleanup(dlg)

    def test_english_live(self) -> None:
        with patch.object(msd, "translate", side_effect=_en), \
             tempfile.TemporaryDirectory() as d, \
             _temp_settings(msd.ModelStateDialog, d):
            dlg = msd.ModelStateDialog()
            try:
                self.assertEqual(dlg._title_label.text(), "Model State")
                dlg.set_state({"success": False})
                self.assertEqual(dlg._title_label.text(), "Model State - Failed")

                dlg.set_state({
                    "success": True, "part_name": "Part001",
                    "features": ["Pad.1"], "steps": [{"step": "创建凸台", "status": "ok"}],
                })
                self.assertEqual(dlg._title_label.text(), "Model State - Part001")
                root = dlg._feat_tree.topLevelItem(0)
                self.assertEqual(root.text(0), "Part Body")
                self.assertEqual(root.child(0).text(0), "Pad.1")
                self.assertEqual(dlg._steps_log.toPlainText(), "[01] \u2713 创建凸台")
                self.assertEqual(dlg._mass_value.text(),
                                 "— (no material assigned or no data)")

                dlg.set_state({"steps": []})
                self.assertEqual(dlg._steps_log.toPlainText(), "— No step records —")
            finally:
                _cleanup(dlg)


class TestLogWindowTexts(unittest.TestCase):
    """行为：offscreen 构造 LogWindow，按钮文案中英切换；标题为英文不参与翻译。"""

    def test_zh_fallback(self) -> None:
        w = lw.LogWindow()
        try:
            self.assertEqual(w.windowTitle(), "CATIA Copilot 1.4.1 – Log")
            buttons = [b.text() for b in w.findChildren(QPushButton)]
            self.assertIn("打开日志文件", buttons)
            labels = [lab.text() for lab in w.findChildren(QLabel)]
            self.assertTrue(any(t.startswith("Log: ") for t in labels),
                            "日志路径标签保留 Log: 前缀")
        finally:
            _cleanup(w)

    def test_english_live(self) -> None:
        with patch.object(lw, "translate", side_effect=_en):
            w = lw.LogWindow()
        try:
            self.assertEqual(w.windowTitle(), "CATIA Copilot 1.4.1 – Log")
            buttons = [b.text() for b in w.findChildren(QPushButton)]
            self.assertIn("Open Log File", buttons)
            labels = [lab.text() for lab in w.findChildren(QLabel)]
            self.assertTrue(any(t.startswith("Log: ") for t in labels),
                            "英文界面日志路径标签前缀不变")
        finally:
            _cleanup(w)

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 走 os.startfile 路径")
    def test_english_error_dialog(self) -> None:
        captured = {}
        with patch.object(lw, "translate", side_effect=_en), \
             patch.object(lw.os, "startfile", side_effect=OSError("boom")), \
             patch.object(lw.QMessageBox, "warning",
                          side_effect=lambda parent, title, text: captured.update(
                              title=title, text=text)):
            w = lw.LogWindow()
            try:
                w._open_log_file()
            finally:
                _cleanup(w)
        self.assertEqual(captured["title"], "Unable to Open Log File")
        self.assertIn("Unable to open the log file:", captured["text"])
        self.assertIn(str(lw.LOG_FILE), captured["text"])
        self.assertIn("boom", captured["text"])


if __name__ == "__main__":
    unittest.main()