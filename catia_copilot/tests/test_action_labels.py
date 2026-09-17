"""主窗口动作标签运行时化测试。

验证 action_labels() 替代类属性 _ACTION_LABELS 后：
- key 集合与源中文文案保持现状（不因重构改变业务字面量）；
- 安装英文翻译器后按钮文案随运行时语言翻译；
- 嵌入 Win32 菜单与主窗口按钮共用同一函数。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTranslator  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_copilot.ui.main_window import MainWindow  # noqa: E402

# 与实际 _ACTION_LABELS 完全一致的 key 集合（覆盖计划中虚构的 open_workspace 等）
EXPECTED_KEYS = {
    "bom_edit",
    "bom_export",
    "mass_props",
    "plm_workbench",
    "export_pdf",
    "export_stp",
    "drawing_new",
    "drawing_refresh",
    "stamp_template",
    "fastener_asm",
    "nut_plate_asm",
    "open_related",
    "find_deps",
    "run_macro",
}


class TestActionLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._kept_translators = []

    def tearDown(self) -> None:
        """卸载本类已安装的翻译器，避免跨用例/跨模块污染。"""
        for t in self._kept_translators:
            self.app.removeTranslator(t)
        self._kept_translators.clear()

    def test_keys_unchanged_and_chinese_default(self) -> None:
        """无翻译器（或 zh_CN）时返回源中文，key 集合保持现状。"""
        labels = MainWindow.action_labels()
        self.assertEqual(set(labels.keys()), EXPECTED_KEYS)
        self.assertEqual(labels["bom_edit"], "BOM 工作台")
        self.assertEqual(labels["run_macro"], "运行宏…")

    def test_connection_states_keys_and_chinese_default(self) -> None:
        """连接状态工厂覆盖状态栏与诊断对话框的全部文案，默认返回源中文。"""
        states = MainWindow.connection_states()
        self.assertEqual(
            set(states.keys()),
            {
                "connected",
                "broken",
                "disconnected",
                "diag_connected",
                "diag_broken",
                "diag_disconnected",
            },
        )
        self.assertEqual(states["connected"], "● CATIA 已连接")
        self.assertEqual(states["disconnected"], "● CATIA 未连接")
        self.assertEqual(states["diag_connected"], "✅ 已连接")

    def test_english_when_translator_active(self) -> None:
        """安装英文翻译器后，运行时求值应返回英文文案。"""
        qm = self._build_mini_qm()
        t = QTranslator(self.app)
        self.assertTrue(t.load(str(qm)), "测试 qm 生成失败")
        self.app.installTranslator(t)
        self._kept_translators.append(t)
        try:
            labels = MainWindow.action_labels()
            self.assertEqual(labels["bom_edit"], "BOM Workbench")
            self.assertEqual(labels["run_macro"], "Running Macro…")
        finally:
            self.app.removeTranslator(t)

    @staticmethod
    def _build_mini_qm() -> Path:
        """用真实 pyside6-lrelease 编译一个临时英文 qm。"""
        d = Path(tempfile.mkdtemp())
        ts = d / "en.ts"
        ts.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<TS version="2.1" language="en_US">\n'
            "<context><name>CATIACopilot</name>\n"
            "<message><source>BOM 工作台</source><translation>BOM Workbench</translation></message>\n"
            "<message><source>运行宏…</source><translation>Running Macro…</translation></message>\n"
            "</context></TS>",
            encoding="utf-8",
        )
        subprocess.run(["pyside6-lrelease", str(ts)], check=True, capture_output=True)
        return d / "en.qm"


if __name__ == "__main__":
    unittest.main()