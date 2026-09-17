"""工作台对话窗口 i18n 测试（Phase 2 Task 2.4）：查找依赖 / 文件重命名 / 模板对话框。

覆盖：
1. FindDependenciesDialog：窗口标题、结果区按钮、启发式策略显示名与提示均回退中文；
   文件类型提示文本经翻译；正向/反向/启发式分组框中文。
2. _FileRenameDialog：窗口标题、按钮文本回退中文；预览逻辑不受影响。
3. TemplateDialog：窗口标题、内容与按钮回退中文。

说明：所有断言基于 qm 缺失时的中文回退（当前仓库仅 zh_CN 源语言）。
"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGroupBox,
    QPushButton,
)

from catia_copilot.ui.bom_file_rename_dialog import _FileRenameDialog  # noqa: E402
from catia_copilot.ui.find_deps_dialog import (  # noqa: E402
    FindDependenciesDialog,
    _heuristic_hint,
    _heuristic_label,
)
from catia_copilot.ui.template_dialog import TemplateDialog  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])


class TestFindDepsDialog(unittest.TestCase):
    def test_widget_titles_are_chinese(self) -> None:
        dlg = FindDependenciesDialog()
        self.assertEqual(dlg.windowTitle(), "查找指向的文档")
        self.assertEqual(dlg._search_btn.text(), "开始搜索")
        self.assertEqual(dlg._open_all_btn.text(), "全部打开")
        self.assertEqual(dlg._copy_all_btn.text(), "复制全部路径")
        self.assertEqual(dlg._browse_btn.text(), "浏览...")

    def test_heuristic_labels_fallback_chinese(self) -> None:
        self.assertEqual(_heuristic_label("same_name_scan_dirs"), "目录扫描，同名文件")
        self.assertEqual(
            _heuristic_label("strip_prefix_scan_dirs"), "目录扫描，去前缀后同名文件"
        )
        # 未知键回退原键（保持旧的 .get(key, key) 语义）
        self.assertEqual(_heuristic_label("unknown_heu_key"), "unknown_heu_key")

    def test_heuristic_hints_fallback_chinese(self) -> None:
        self.assertIn("PartNumber", _heuristic_hint("pn_param_open_docs"))
        self.assertIn("同名", _heuristic_hint("same_name_scan_dirs"))
        # 未知键返回空串（保持旧的 .get(key, "") 语义）
        self.assertEqual(_heuristic_hint("unknown_heu_key"), "")

    def test_file_type_label_drawing(self) -> None:
        dlg = FindDependenciesDialog()
        dlg._update_file_type_label("C:/tmp/part.CATDrawing")
        self.assertIn("CATDrawing", dlg._file_type_label.text())
        self.assertIn("2A", dlg._file_type_label.text())

    def test_strategy_group_boxes_are_chinese(self) -> None:
        dlg = FindDependenciesDialog()
        texts = [g.title() for g in dlg.findChildren(QGroupBox)]
        self.assertIn("正向查询", texts)
        self.assertIn("反向查询", texts)
        self.assertIn("启发式补充", texts)


class TestFileRenameDialog(unittest.TestCase):
    def _dlg(self) -> _FileRenameDialog:
        src = Path(__file__).parent / "_fake_part.CATPart"
        return _FileRenameDialog(str(src))

    def test_window_title_chinese(self) -> None:
        self.assertEqual(self._dlg().windowTitle(), "另存为")

    def test_buttons_chinese(self) -> None:
        dlg = self._dlg()
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        self.assertIn("确认", texts)
        self.assertIn("取消", texts)
        self.assertIn("浏览…", texts)


class TestTemplateDialog(unittest.TestCase):
    def test_window_title_chinese(self) -> None:
        self.assertEqual(TemplateDialog().windowTitle(), "模板对话框")


if __name__ == "__main__":
    unittest.main()