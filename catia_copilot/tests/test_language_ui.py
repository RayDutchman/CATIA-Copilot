"""主窗口「设置」区（界面语言切换）测试。

覆盖：
- cmbUILang 下拉存在且覆盖 system / zh_CN / en_US，首版无 cmbExportLang；
- 选择 English 并点击页脚「保存设置」后 i18n.read_language() == en_US；
- 已有语言设置回填到下拉框。

隔离：
- i18n 与 main_window 的 QSettings 都注入临时 Ini 文件，避免污染真实注册表；
- check_catia_connection 被替换，避免测试环境触发真实 COM 探测。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton  # noqa: E402

from catia_copilot import i18n  # noqa: E402
from catia_copilot.ui.main_window import MainWindow  # noqa: E402


def _isolated_settings_factory(ini_path: Path):
    return lambda *_a, **_k: QSettings(str(ini_path), QSettings.Format.IniFormat)


class TestLanguageUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        ini = Path(self._tmp.name) / "app.ini"
        self._patch_settings = patch(
            "catia_copilot.ui.main_window.QSettings",
            _isolated_settings_factory(ini),
        )
        self._patch_i18n_settings = patch(
            "catia_copilot.i18n.QSettings",
            _isolated_settings_factory(ini),
        )
        self._patch_conn = patch(
            "catia_copilot.ui.main_window.check_catia_connection",
            return_value="disconnected",
        )
        self._patch_settings.start()
        self._patch_i18n_settings.start()
        self._patch_conn.start()

    def tearDown(self) -> None:
        self._patch_conn.stop()
        self._patch_i18n_settings.stop()
        self._patch_settings.stop()
        self._tmp.cleanup()

    def test_combo_present_and_save_en(self) -> None:
        w = MainWindow()
        w.show()
        try:
            boxes = {c.objectName(): c for c in w.findChildren(QComboBox)}
            self.assertIn("cmbUILang", boxes)
            self.assertNotIn("cmbExportLang", boxes)  # 首版无导出语言选择
            cmb = boxes["cmbUILang"]
            cmb.setCurrentIndex(cmb.findData("en_US"))
            with patch("catia_copilot.ui.main_window.QMessageBox.information"):
                btn = w.findChild(QPushButton, "btnSaveLang")
                self.assertIsNotNone(btn, "缺少保存语言按钮 btnSaveLang")
                btn.click()
            self.assertEqual(i18n.read_language(), "en_US")
        finally:
            w.close()

    def test_combo_covers_all_languages(self) -> None:
        w = MainWindow()
        w.show()
        try:
            cmb = w.findChild(QComboBox, "cmbUILang")
            self.assertIsNotNone(cmb)
            self.assertEqual(
                {cmb.itemData(i) for i in range(cmb.count())},
                {"system", "zh_CN", "en_US"},
            )
        finally:
            w.close()

    def test_combo_prefilled_from_settings(self) -> None:
        i18n.write_language("zh_CN")
        w = MainWindow()
        w.show()
        try:
            cmb = w.findChild(QComboBox, "cmbUILang")
            self.assertEqual(cmb.currentData(), "zh_CN")
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()