"""翻译目录（TS 目录表）单元测试：XML 格式 / 完整性 / 占位符 / lupdate 覆盖 / QM 加载。

与构建校验脚本（scripts/verify_translations.py）共用同一套检查函数（importlib 装载），
保证脚本与测试逻辑不漂移：

1. en_US / zh_CN TS 均为合法 XML、无空 translation、无 unfinished、占位符集合一致；
2. 两份 TS 的 source 集合双向一致；
3. 生产 18 文件清单（docs/i18n-phase5-report.md §二）真实存在；
4. 真实 pyside6-lupdate 按生产清单提取的 source 与 TS 双向一致（覆盖率）；
5. 真实 pyside6-lrelease 构建 QM 可被 QTranslator 加载并译出期望英文。

不读取 docs/i18n-phase*-translations.json 交接表：目录表校验只认 TS 与 lupdate 提取。
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT = _REPO_ROOT / "scripts" / "verify_translations.py"


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_translations", _VERIFY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


vt = _load_verify_module()

_LUPDATE = which("pyside6-lupdate")
_LRELEASE = which("pyside6-lrelease")
_SKIP_REASON = "pyside6-lupdate / pyside6-lrelease 不在 PATH 中，跳过真实提取回归"
_QAPP = QCoreApplication.instance() or QCoreApplication([])

_SAMPLE_TO_EN = {
    "确定": "OK",
    "取消": "Cancel",
    "零件编号": "Part number",
    "状态": "Status",
    "类型": "Type",
}


class TestCatalogWellFormed(unittest.TestCase):
    """TS 文件本身为合法 XML（占位符等以 XML 文本形式保存）。"""

    def test_en_and_zh_ts_are_valid_xml(self) -> None:
        for ts in (vt.TS_EN, vt.TS_ZH):
            self.assertTrue(ts.is_file(), f"缺少 TS: {ts}")
            tree = ET.parse(ts)
            contexts = {c.findtext("name"): c for c in tree.getroot().findall("context")}
            self.assertIn(vt.APP_CONTEXT, contexts, f"{ts.name} 缺少 context")

    def test_catalog_entries_source_count(self) -> None:
        en = vt.catalog_entries(vt.TS_EN)
        zh = vt.catalog_entries(vt.TS_ZH)
        self.assertGreaterEqual(len(en), 1000, "phase5 合并后词条数应不低于 1000")
        self.assertEqual(len(en), len(zh), "两份 TS 词条数应一致")


class TestCatalogIntegrity(unittest.TestCase):
    """无空词条 / unfinished / 占位符与花括号配对一致。"""

    def test_en_catalog_clean(self) -> None:
        self.assertEqual(vt.flaws_of(vt.catalog_entries(vt.TS_EN)), [])

    def test_zh_catalog_clean(self) -> None:
        self.assertEqual(vt.flaws_of(vt.catalog_entries(vt.TS_ZH)), [])

    def test_placeholder_tokens_ignore_double_brace_escapes(self) -> None:
        # {{...}} 是 .format() 字面转义，不应被当作占位符参与比较
        self.assertEqual(
            vt.placeholder_tokens("下载到 {0}/{{零件号}}/{{文件名}}"),
            {"{0}"},
        )

    def test_en_zh_source_sets_identical(self) -> None:
        en = vt.sources_of(vt.TS_EN)
        zh = vt.sources_of(vt.TS_ZH)
        self.assertEqual(en, zh, "两份 TS 的 source 集合必须双向一致")


class TestProductionManifest(unittest.TestCase):
    """docs/i18n-phase5-report.md §二 生产 18 文件清单必须全部存在。"""

    def test_all_manifest_files_exist(self) -> None:
        missing = [p for p in vt.PRODUCTION_FILES if not (_REPO_ROOT / p).is_file()]
        self.assertEqual(missing, [], "生产文件清单中的源文件必须存在")
        self.assertEqual(len(vt.PRODUCTION_FILES), 18, "生产清单应为 18 个文件")


@unittest.skipUnless(_LUPDATE, _SKIP_REASON)
class TestCatalogCoverage(unittest.TestCase):
    """真实 pyside6-lupdate 按生产清单提取 == TS source 集合（字面量覆盖）。"""

    def test_extraction_matches_both_ts(self) -> None:
        extracted = vt.lupdate_sources([_REPO_ROOT / p for p in vt.PRODUCTION_FILES])
        en, zh = vt.sources_of(vt.TS_EN), vt.sources_of(vt.TS_ZH)
        self.assertEqual(
            extracted, en,
            "lupdate 提取与 en_US.ts 不一致"
            f"（缺={sorted(en - extracted)[:5]} 多={sorted(extracted - en)[:5]}）",
        )
        self.assertEqual(en, zh, "en_US / zh_CN source 集合不一致")


@unittest.skipUnless(_LRELEASE, _SKIP_REASON)
class TestCatalogRuntime(unittest.TestCase):
    """真实 pyside6-lrelease 构建 QM 可加载并译出期望英文。"""

    def test_en_qm_loads_and_translates(self) -> None:
        qm = self._build_qm(vt.TS_EN, "en_probe")
        translator = QTranslator(_QAPP)
        self.assertTrue(translator.load(str(qm)), "en QM 应能被 QTranslator 加载")
        _QAPP.installTranslator(translator)
        try:
            for source, expected in _SAMPLE_TO_EN.items():
                self.assertEqual(
                    QCoreApplication.translate(vt.APP_CONTEXT, source),
                    expected,
                    f"en_US QM 应把 {source} 译为 {expected}",
                )
        finally:
            _QAPP.removeTranslator(translator)

    def test_zh_qm_identity(self) -> None:
        qm = self._build_qm(vt.TS_ZH, "zh_probe")
        translator = QTranslator(_QAPP)
        self.assertTrue(translator.load(str(qm)), "zh QM 应能被 QTranslator 加载")
        _QAPP.installTranslator(translator)
        try:
            self.assertEqual(
                QCoreApplication.translate(vt.APP_CONTEXT, "确定"),
                "确定",
                "zh_CN QM 应为中文 identity",
            )
        finally:
            _QAPP.removeTranslator(translator)

    def _build_qm(self, ts: Path, stem: str) -> Path:
        d = Path(tempfile.mkdtemp())
        qm = d / f"{stem}.qm"
        subprocess.run(
            ["pyside6-lrelease", str(ts), "-qm", str(qm)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        self.assertTrue(qm.exists(), "lrelease 应产出 .qm 文件")
        return qm


if __name__ == "__main__":
    unittest.main()