"""PLM 工作台 UI 结构化事件消费测试（i18n Phase 3 Task 3.2）。

目标：
1. 来源/更新/签入三列展示由稳定 code 映射固定字面量 translate（默认中文回退；
   读 docs/i18n-phase3-ui-translations.json 恢复英文）；
2. 终态 node_done / node_skip / node_fail 按 pn 去重计数一次；pn 含 "<"/"|"
   特殊字符时完整保留，不做字符串截断；
3. 更新/签入失败（update-failed / checkin-failed 等 code）行颜色为红，且颜色
   判定只依赖 code、不解析中文文案（语言无关）；
4. 速度 speed_kbps 为数值型真实速度，直接格式化显示，不解析文本；
5. 文本日志（_on_sync_progress）只改状态栏纯展示，绝不动结果映射/计数；
6. 未知 code 安全回退事件原始文案，不因 code 扩展而空白；
7. 全部 translate 调用 context/source 必须是字符串字面量（AST 防线）。

说明：PlmWorkbench 为重型 QDialog，仅在 offscreen QPA 下构造；closeEvent 会
写入 QSettings（几何/选项），测试结束后清理实例。
"""
import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_copilot.plm import sync  # noqa: E402
from catia_copilot.ui.plm_workbench import (  # noqa: E402
    PlmWorkbench,
    _SyncWorker,
    _event_checkin_text,
    _event_source_text,
    _event_update_text,
    _fmt_kbps,
    _sync_row_color_from_event,
)

_QAPP = QApplication.instance() or QApplication([])

_TRANSLATIONS = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "i18n-phase3-ui-translations.json")
    .read_text(encoding="utf-8")
)


def _event(type_: str, **kw) -> "sync.SyncEvent":
    """构造最小 SyncEvent（未给出的字段走默认值）。"""
    return sync.SyncEvent(type=type_, **kw)


def _new_workbench() -> PlmWorkbench:
    return PlmWorkbench()


def _reset_sync(wb: PlmWorkbench, total: int = 3) -> None:
    """把工作台同步状态复位到一次全新 Push 开始的形态。"""
    wb._sync_total_nodes = total
    wb._sync_done_nodes = 0
    wb._sync_seen_pns = set()
    wb._sync_result_map.clear()
    wb._pgb_sync.setRange(0, total)
    wb._pgb_sync.setValue(0)


def _cleanup(wb: PlmWorkbench) -> None:
    wb.close()
    wb.deleteLater()
    _QAPP.processEvents()


class TestPlmWorkbenchUiStructure(unittest.TestCase):
    """信号与业务入口的存在性 / 快速结构检查。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_sync_event_signal_exists(self) -> None:
        self.assertTrue(hasattr(_SyncWorker, "sync_event"),
                        "_SyncWorker 必须暴露 sync_event 结构化事件信号")


class TestDisplayTexts(unittest.TestCase):
    """来源/更新/签入展示：默认中文回退 + 英文 patch。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_source_text_zh_fallback(self) -> None:
        ev = _event("node_done",
                    source_code=sync.CODE_SOURCE_CREATED,
                    source="新建")
        self.assertEqual(_event_source_text(ev), "新建")

    def test_complete_row_zh_fallback(self) -> None:
        ev = _event(
            "node_done", part_number="P-1",
            source_code=sync.CODE_SOURCE_UPDATED,
            update_code=sync.CODE_UPDATE_WRITTEN,
            checkin_code=sync.CODE_CHECKIN_CHECKED_IN,
        )
        self.wb._on_sync_event(ev)
        self.assertEqual(
            self.wb._sync_result_map["P-1"], ("已更新", "属性已写入", "已签入")
        )

    def test_complete_row_english(self) -> None:
        def _en(ctx, src, *a, **k):
            return _TRANSLATIONS.get(src, src)

        ev = _event(
            "node_done", part_number="P-1",
            source_code=sync.CODE_SOURCE_CREATED,
            update_code=sync.CODE_UPDATE_WRITTEN,
            checkin_code=sync.CODE_CHECKIN_CHECKED_IN,
        )
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            self.wb._on_sync_event(ev)
        self.assertEqual(
            self.wb._sync_result_map["P-1"],
            ("Create", "Attributes written", "Checked in"),
        )

    def test_english_translations_json_has_all_displays(self) -> None:
        """英文译文表必须覆盖全部新加入的固定展示字面量。"""
        key_codes = [
            sync.CODE_SOURCE_CREATED, sync.CODE_SOURCE_UPDATED,
            sync.CODE_SOURCE_SKIPPED, sync.CODE_SOURCE_UNCHANGED,
            sync.CODE_SOURCE_FAILED,
            sync.CODE_UPDATE_WRITTEN, sync.CODE_UPDATE_UPDATE_FAILED,
            sync.CODE_UPDATE_UPLOADED, sync.CODE_UPDATE_UPLOAD_FAILED,
            sync.CODE_UPDATE_CONVERTING, sync.CODE_UPDATE_CONVERTED,
            sync.CODE_UPDATE_CONVERSION_FAILED,
            sync.CODE_CHECKIN_CHECKED_IN, sync.CODE_CHECKIN_CHECKIN_FAILED,
            sync.CODE_CHECKIN_RETAINED,
        ]
        # 中文字面量来自测试自身对固定文案的记忆，直接与 JSON 键比对
        zh_literals = {
            sync.CODE_SOURCE_CREATED: "新建",
            sync.CODE_SOURCE_UPDATED: "已更新",
            sync.CODE_SOURCE_SKIPPED: "跳过",
            sync.CODE_SOURCE_UNCHANGED: "无变化",
            sync.CODE_SOURCE_FAILED: "失败",
            sync.CODE_UPDATE_WRITTEN: "属性已写入",
            sync.CODE_UPDATE_UPDATE_FAILED: "✗ 更新失败",
            sync.CODE_UPDATE_UPLOADED: "已上传",
            sync.CODE_UPDATE_UPLOAD_FAILED: "✗ 上传失败",
            sync.CODE_UPDATE_CONVERTING: "转换中",
            sync.CODE_UPDATE_CONVERTED: "转换完成",
            sync.CODE_UPDATE_CONVERSION_FAILED: "✗ 转换失败",
            sync.CODE_CHECKIN_CHECKED_IN: "已签入",
            sync.CODE_CHECKIN_CHECKIN_FAILED: "✗ 签入失败",
            sync.CODE_CHECKIN_RETAINED: "保留签出",
        }
        for code in key_codes:
            zh = zh_literals[code]
            self.assertIn(zh, _TRANSLATIONS, f"译文表缺失 {zh!r}")
            self.assertTrue(_TRANSLATIONS[zh], f"{zh!r} 译文不可为空")

    def test_unknown_code_falls_back_to_raw(self) -> None:
        ev = _event(
            "node_done", part_number="P-1",
            source="自定义来源", update="自定义更新", checkin="",
        )
        self.wb._on_sync_event(ev)
        self.assertEqual(
            self.wb._sync_result_map["P-1"], ("自定义来源", "自定义更新", "")
        )
        self.assertEqual(self.wb._sync_done_nodes, 1, "终态未知 code 仍应计数一次")


class TestCounting(unittest.TestCase):
    """终态按 pn 去重计数；特殊字符 pn 完整保留。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb, total=2)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_same_pn_terminal_events_count_once(self) -> None:
        done = _event("node_done", part_number="P-1",
                      source_code=sync.CODE_SOURCE_CREATED)
        skip = _event("node_skip", part_number="P-1",
                      source_code=sync.CODE_SOURCE_SKIPPED)
        fail = _event("node_fail", part_number="P-1",
                      source_code=sync.CODE_SOURCE_FAILED)
        for ev in (done, skip, fail):
            self.wb._on_sync_event(ev)
        self.assertEqual(self.wb._sync_done_nodes, 1)
        self.assertEqual(self.wb._sync_seen_pns, {"P-1"})
        # 末次终态写入 map，颜色由 code 决定
        self.assertEqual(self.wb._sync_result_map["P-1"][0], "失败")

    def test_pn_with_special_chars_kept(self) -> None:
        pn = "ABC<DISK|HDD/2"
        ev = _event("node_done", part_number=pn,
                    source_code=sync.CODE_SOURCE_CREATED)
        self.wb._on_sync_event(ev)
        self.assertIn(pn, self.wb._sync_result_map)
        self.assertEqual(self.wb._sync_seen_pns, {pn})
        self.assertEqual(self.wb._sync_done_nodes, 1)

    def test_progress_events_do_not_count(self) -> None:
        ev = _event("node_progress", part_number="P-1",
                    update_code=sync.CODE_UPDATE_UPLOADED)
        self.wb._on_sync_event(ev)
        self.assertEqual(self.wb._sync_done_nodes, 0)
        self.assertEqual(self.wb._sync_seen_pns, set())
        self.assertEqual(self.wb._sync_result_map["P-1"][1], "已上传")

    def test_summary_header_do_not_touch_state(self) -> None:
        for ev in (_event("summary", message="  转换中…… (STP)"),
                   _event("header", message="=== 同步开始 ===")):
            self.wb._on_sync_event(ev)
        self.assertEqual(self.wb._sync_result_map, {})
        self.assertEqual(self.wb._sync_done_nodes, 0)


class TestResultColors(unittest.TestCase):
    """行颜色只依赖稳定 code，不解析中文文案（语言无关）。"""

    @staticmethod
    def _red() -> QColor:
        palette = QApplication.instance().palette()
        return palette.color(QPalette.ColorRole.Link) if palette else QColor("#e74c3c")

    @staticmethod
    def _gray() -> QColor:
        palette = QApplication.instance().palette()
        return palette.color(QPalette.ColorRole.Mid) if palette else QColor("#7f8c8d")

    def test_update_failed_is_red(self) -> None:
        for upd_code in (
            sync.CODE_UPDATE_UPDATE_FAILED,
            sync.CODE_UPDATE_UPLOAD_FAILED,
            sync.CODE_UPDATE_CONVERSION_FAILED,
        ):
            ev = _event("node_done", part_number="P-1", update_code=upd_code)
            self.assertEqual(_sync_row_color_from_event(ev), self._red(),
                             f"{upd_code} 应判红（code 而非文案）")

    def test_checkin_failed_is_red(self) -> None:
        ev = _event("node_done", part_number="P-1",
                    checkin_code=sync.CODE_CHECKIN_CHECKIN_FAILED)
        self.assertEqual(_sync_row_color_from_event(ev), self._red())

    def test_source_failed_is_red(self) -> None:
        ev = _event("node_done", part_number="P-1",
                    source_code=sync.CODE_SOURCE_FAILED)
        self.assertEqual(_sync_row_color_from_event(ev), self._red())

    def test_success_codes_green_blue_gray(self) -> None:
        created = _event("node_done", part_number="P-1",
                         source_code=sync.CODE_SOURCE_CREATED)
        self.assertEqual(_sync_row_color_from_event(created), QColor("#27ae60"))
        skipped = _event("node_done", part_number="P-1",
                         source_code=sync.CODE_SOURCE_SKIPPED)
        gray = self._gray()
        self.assertEqual(_sync_row_color_from_event(skipped), gray)
        self.assertNotEqual(gray, self._red())
        self.assertNotEqual(gray, QColor("#27ae60"))

    def test_unknown_code_no_color(self) -> None:
        self.assertIsNone(_sync_row_color_from_event(_event("summary")))

    def test_progress_color_reads_code(self) -> None:
        ev = _event("node_progress", part_number="P-1",
                    update_code=sync.CODE_UPDATE_UPLOAD_FAILED)
        self.assertEqual(_sync_row_color_from_event(ev), self._red())


class TestSpeed(unittest.TestCase):
    """speed_kbps 为数值型真实速度，直接格式化显示。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_fmt_kbps(self) -> None:
        self.assertEqual(_fmt_kbps(512.5), "512.5 KB/s")
        self.assertEqual(_fmt_kbps(2048), "2.0 MB/s")
        self.assertEqual(_fmt_kbps(1024), "1.0 MB/s")

    def test_speed_label_updated_from_numeric(self) -> None:
        ev = _event("node_progress", part_number="P-1",
                    speed_kbps=2048, update_code=sync.CODE_UPDATE_UPLOADED)
        self.wb._on_sync_event(ev)
        self.assertEqual(self.wb._lbl_upload_speed.text(), "2.0 MB/s")
        ev2 = _event("node_progress", part_number="P-1",
                     speed_kbps=512.5, update_code=sync.CODE_UPDATE_UPLOADED)
        self.wb._on_sync_event(ev2)
        self.assertEqual(self.wb._lbl_upload_speed.text(), "512.5 KB/s")

    def test_text_log_does_not_set_speed(self) -> None:
        # 文本回调不再负责速度解析（速度必须来自事件数值字段）
        self.wb._on_sync_progress("  P-1  (123.4 KB/s)")
        self.assertEqual(self.wb._lbl_upload_speed.text(), "")


class TestTextLogsDoNotDriveBusiness(unittest.TestCase):
    """文本日志只做状态栏纯展示，不得改变结果映射/计数。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_terminal_looking_lines_do_not_touch_results(self) -> None:
        for line in (
            ">> P-1 | 跳过-不新建  <P-1>",
            "[X] P-2 | 失败  <P-2>",
            "P-3 | 属性已写入 | ___ | 已签入  <P-3>",
        ):
            self.wb._on_sync_progress(line)
        self.assertEqual(self.wb._sync_result_map, {})
        self.assertEqual(self.wb._sync_done_nodes, 0)
        self.assertEqual(self.wb._sync_seen_pns, set())

    def test_progress_shows_raw_text(self) -> None:
        self.wb._on_sync_progress("  P-1  转换中…… (STP) | 上传中 ")
        self.assertEqual(self.wb._lbl_sync_status.text(), "P-1  转换中…… (STP) | 上传中")

    def test_empty_line_ignored(self) -> None:
        before = self.wb._lbl_sync_status.text()
        self.wb._on_sync_progress("")
        self.wb._on_sync_progress("   ")
        self.wb._on_sync_progress("---")
        self.assertEqual(self.wb._lbl_sync_status.text(), before,
                         "空行/纯装饰横线不应改动状态栏")


class TestLifecycle(unittest.TestCase):
    """关闭/重开工作台后同步状态复位，重复开启互不污染。"""

    def test_close_reopen_resets_sync_state(self) -> None:
        wb1 = _new_workbench()
        _reset_sync(wb1, total=1)
        wb1._on_sync_event(_event("node_done", part_number="P-LIFE",
                                  source_code=sync.CODE_SOURCE_CREATED))
        self.assertEqual(wb1._sync_done_nodes, 1)
        self.assertEqual(wb1._sync_result_map, {"P-LIFE": ("新建", "", "")})
        _cleanup(wb1)

        wb2 = _new_workbench()
        try:
            self.assertEqual(wb2._sync_done_nodes, 0)
            self.assertEqual(wb2._sync_result_map, {})
            _reset_sync(wb2, total=1)
            wb2._on_sync_event(_event("node_done", part_number="P-LIFE2",
                                      source_code=sync.CODE_SOURCE_CREATED))
            self.assertEqual(wb2._sync_done_nodes, 1)
        finally:
            _cleanup(wb2)


class TestNoTextColorDependency(unittest.TestCase):
    """英文展示下颜色依旧生效（颜色必须来自 code 而非中文关键词）。"""

    def setUp(self) -> None:
        self.wb = _new_workbench()
        _reset_sync(self.wb)

    def tearDown(self) -> None:
        _cleanup(self.wb)

    def test_english_row_still_colored_by_code(self) -> None:
        def _en(ctx, src, *a, **k):
            return _TRANSLATIONS.get(src, src)

        palette = QApplication.instance().palette()
        red = palette.color(QPalette.ColorRole.Link) if palette else QColor("#e74c3c")
        ev = _event("node_done", part_number="P-1",
                    update_code=sync.CODE_UPDATE_UPDATE_FAILED)
        with patch("catia_copilot.ui.plm_workbench.translate", side_effect=_en):
            self.wb._on_sync_event(ev)
        self.assertEqual(self.wb._sync_result_map["P-1"][1], "✗ Update failed")
        self.assertEqual(_sync_row_color_from_event(ev), red)


class TestTranslateCallLiteral(unittest.TestCase):
    """AST 防线：plm_workbench.py 内全部 translate 调用前两参均为字符串字面量。"""

    _SRC = Path(__file__).resolve().parents[2] / "catia_copilot" / "ui" / "plm_workbench.py"

    @classmethod
    def _translate_calls(cls) -> list[ast.Call]:
        tree = ast.parse(cls._SRC.read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "translate"
        ]

    def test_translate_calls_exist(self) -> None:
        self.assertTrue(self._translate_calls(), "plm_workbench 应存在 i18n 词条")

    def test_context_is_string_literal(self) -> None:
        for call in self._translate_calls():
            if not call.args:
                self.fail(f"第 {call.lineno} 行 translate 缺少 context 实参")
            ctx = call.args[0]
            self.assertIsInstance(ctx, ast.Constant,
                                  "translate context 必须为字符串字面量，禁止变量")
            self.assertIsInstance(ctx.value, str)

    def test_source_is_string_literal(self) -> None:
        for call in self._translate_calls():
            if len(call.args) < 2:
                self.fail(f"第 {call.lineno} 行 translate 缺少 source 实参")
            src = call.args[1]
            self.assertIsInstance(src, ast.Constant,
                                  "translate source 必须为字符串字面量，禁止变量/属性")
            self.assertIsInstance(src.value, str)


if __name__ == "__main__":
    unittest.main()