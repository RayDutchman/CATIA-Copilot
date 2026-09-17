"""i18n 基础设施测试：语言解析、QSettings 读写、翻译器安装与回退、Qt 标准按钮中文化。

说明：
- 全部 GUI 相关用例在 QT_QPA_PLATFORM=offscreen 下运行，不依赖真实窗口/显示器；
- QSettings 用例通过注入临时 Ini 文件的工厂隔离，避免污染真实注册表；
- 标准按钮中文化依赖 QApplication（QMessageBox 需要 widget 环境），故本文件统一使用 QApplication。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from catia_copilot import i18n  # noqa: E402


def _isolated_settings_factory(ini_path: Path):
    """返回构造隔离 IniFormat QSettings 的工厂，避免污染真实注册表。"""

    def factory(*_args, **_kwargs):
        return QSettings(str(ini_path), QSettings.Format.IniFormat)

    return factory


class TestResolveLanguage(unittest.TestCase):
    def test_explicit_zh(self) -> None:
        self.assertEqual(i18n.resolve_ui_language("zh_CN"), "zh_CN")

    def test_explicit_en(self) -> None:
        self.assertEqual(i18n.resolve_ui_language("en_US"), "en_US")

    @patch("catia_copilot.i18n.QLocale")
    def test_system_zh_family_maps_to_zh(self, mock_locale) -> None:
        mock_locale.system().name.return_value = "zh_TW"
        self.assertEqual(i18n.resolve_ui_language("system"), "zh_CN")

    @patch("catia_copilot.i18n.QLocale")
    def test_system_unknown_region_falls_back_to_en(self, mock_locale) -> None:
        mock_locale.system().name.return_value = "ja_JP"
        self.assertEqual(i18n.resolve_ui_language("system"), "en_US")


class TestLanguageSettings(unittest.TestCase):
    def test_default_is_system(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "app.ini"
            with patch("catia_copilot.i18n.QSettings", side_effect=_isolated_settings_factory(ini)):
                self.assertEqual(i18n.read_language(), "system")

    def test_write_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "app.ini"
            with patch("catia_copilot.i18n.QSettings", side_effect=_isolated_settings_factory(ini)):
                i18n.write_language("en_US")
                self.assertEqual(i18n.read_language(), "en_US")


class TestTranslateFallback(unittest.TestCase):
    def test_missing_source_returns_source(self) -> None:
        """未收录词条必须返回源中文，绝不抛异常、绝不返回空串。"""
        self.assertEqual(
            i18n.translate("CATIACopilot", "绝不会在 qm 中存在的词条"),
            "绝不会在 qm 中存在的词条",
        )


class _AppTestCase(unittest.TestCase):
    """共享 QApplication 实例并持有翻译器引用（防 GC）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._kept_translators = []

    def tearDown(self) -> None:
        """每个用例后卸载已安装的翻译器，避免跨用例污染（回退/切换用例不得残留）。"""
        for tr in self._kept_translators:
            for translator in tr:
                if translator is not None:
                    self.app.removeTranslator(translator)
        self._kept_translators.clear()


class TestInstallTranslators(_AppTestCase):
    def test_zh_installs_no_app_translator_but_installs_qt_base(self) -> None:
        tr = i18n.install_translators(self.app, ui_lang="zh_CN")
        self._kept_translators.append(tr)
        self.assertIsNone(tr[0], "zh_CN 是源语言，不应安装应用翻译器")
        self.assertIsNotNone(tr[1], "zh_CN 应安装 qtbase 以中文化 Qt 标准按钮")
        self.assertEqual(i18n.current_ui_language(), "zh_CN")

    def test_en_missing_qm_falls_back_to_chinese(self) -> None:
        with patch("catia_copilot.i18n._qm_path", return_value=""):
            tr = i18n.install_translators(self.app, ui_lang="en_US")
        self._kept_translators.append(tr)
        self.assertIsNone(tr[0], "qm 缺失应回退中文（不安装目标语言翻译器）")
        self.assertIsNotNone(tr[1], "回退中文时也应安装 qtbase 保证标准按钮中文化")
        # 界面实际已回退中文，语言必须记录为 zh_CN（不能误记 en_US）
        self.assertEqual(i18n.current_ui_language(), "zh_CN")

    def test_en_with_qm_translates_ui(self) -> None:
        qm = self._build_mini_qm()
        with patch("catia_copilot.i18n._qm_path", return_value=str(qm)):
            tr = i18n.install_translators(self.app, ui_lang="en_US")
        self._kept_translators.append(tr)
        self.assertIsNotNone(tr[0], "存在 qm 时应安装英语翻译器")
        try:
            self.assertEqual(
                i18n.translate("CATIACopilot", "BOM 工作台"),
                "BOM Workbench",
            )
            self.assertEqual(i18n.current_ui_language(), "en_US")
        finally:
            self.app.removeTranslator(tr[0])

    @staticmethod
    def _build_mini_qm() -> Path:
        """构造仅含一个词条的临时 qm（真实 lrelease 编译）。"""
        d = Path(tempfile.mkdtemp())
        ts = d / "mini.ts"
        ts.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<TS version="2.1" language="en_US">\n'
            '<context><name>CATIACopilot</name>\n'
            '<message><source>BOM 工作台</source><translation>BOM Workbench</translation></message>\n'
            "</context></TS>",
            encoding="utf-8",
        )
        subprocess.run(["pyside6-lrelease", str(ts)], check=True, capture_output=True)
        return d / "mini.qm"


class TestQtBaseStandardButtons(_AppTestCase):
    def test_zh_standard_buttons_are_chinese(self) -> None:
        """安装 zh_CN（含 qtbase_zh_CN）后 QMessageBox 标准按钮应显示中文。"""
        tr = i18n.install_translators(self.app, ui_lang="zh_CN")
        self._kept_translators.append(tr)
        mb = QMessageBox()
        mb.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        ok = mb.button(QMessageBox.StandardButton.Ok)
        cancel = mb.button(QMessageBox.StandardButton.Cancel)
        self.assertEqual(ok.text(), "确定")
        self.assertEqual(cancel.text(), "取消")


if __name__ == "__main__":
    unittest.main()