"""convert_dialog 的 i18n 防线测试（Phase 2 收尾）。

目标：
1. convert_dialog 内的 translate 调用，context 与 source 必须均为字符串字面量，
   禁止把变量/属性表达式当作 source（lupdate 只按字面量提取，变量会漏提取；
   且调用方 main_window 已翻译字面量，再次翻译属于双重翻译）。
2. 行为面：file_label / no_files_msg 由调用方翻译后传入，convert_dialog 必须原样
   显示，不得再次翻译（title/note 同类接口直接使用，一并守住）。
"""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from catia_copilot.ui.convert_dialog import FileConvertDialog  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

_SRC = Path(__file__).resolve().parents[2] / "catia_copilot" / "ui" / "convert_dialog.py"


def _translate_calls() -> list[ast.Call]:
    """解析 convert_dialog.py，返回全部 `translate(...)` 调用节点。"""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "translate"
    ]


class TestTranslateCallLiteral(unittest.TestCase):
    """AST 层面：translate 的前两个实参必须是字符串字面量。"""

    def test_translate_calls_exist(self) -> None:
        self.assertTrue(_translate_calls(), "convert_dialog 应存在 i18n 词条")

    def test_context_is_string_literal(self) -> None:
        for call in _translate_calls():
            if not call.args:
                self.fail("translate 缺少 context 实参")
            ctx = call.args[0]
            self.assertIsInstance(ctx, ast.Constant, "translate context 必须为字符串字面量，禁止变量")
            self.assertIsInstance(ctx.value, str)

    def test_source_is_string_literal(self) -> None:
        for call in _translate_calls():
            if len(call.args) < 2:
                self.fail("translate 缺少 source 实参")
            src = call.args[1]
            self.assertIsInstance(src, ast.Constant, "translate source 必须为字符串字面量，禁止变量/属性")
            self.assertIsInstance(src.value, str)


class TestFileLabelNoDoubleTranslate(unittest.TestCase):
    """行为面：调用方已翻译的 file_label / no_files_msg 必须原样显示。"""

    @staticmethod
    def _marker_translate(ctx: str, src: str, *args, **kwargs) -> str:
        """把 src 包装成标记串，若再次 translate 则返回值含 TR[ 前缀，可被断言捕获。"""
        return f"TR[{src}]"

    def _assert_dialog_cleanup(self, dlg: FileConvertDialog) -> None:
        dlg.close()
        dlg.deleteLater()
        _QAPP.processEvents()

    def test_file_label_displayed_as_given_not_retranslated(self) -> None:
        with patch("catia_copilot.ui.convert_dialog.translate", side_effect=self._marker_translate):
            dlg = FileConvertDialog(file_label="LABEL_ALREADY_TRANSLATED")
        try:
            texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
            self.assertIn("LABEL_ALREADY_TRANSLATED", texts, "file_label 必须原样显示")
            self.assertNotIn(
                "TR[LABEL_ALREADY_TRANSLATED]", texts, "file_label 不得再次 translate"
            )
        finally:
            self._assert_dialog_cleanup(dlg)

    def test_no_files_msg_displayed_as_given_not_retranslated(self) -> None:
        with patch("catia_copilot.ui.convert_dialog.translate", side_effect=self._marker_translate), \
                patch.object(QMessageBox, "warning") as warn:
            dlg = FileConvertDialog(file_label="L", no_files_msg="MSG_ALREADY_TRANSLATED")
            try:
                # 空文件列表 + 非活动文档模式确认：警告正文必须是原样传入的 no_files_msg
                # 注意 _confirm 必须在 mock 作用域内调用，否则弹出真实模态框阻塞
                dlg._confirm()
                self.assertTrue(warn.called, "空文件列表时应弹出未选择文件警告")
                self.assertEqual(
                    warn.call_args.args[2],
                    "MSG_ALREADY_TRANSLATED",
                    "no_files_msg 不允许再次 translate",
                )
            finally:
                self._assert_dialog_cleanup(dlg)


if __name__ == "__main__":
    unittest.main()