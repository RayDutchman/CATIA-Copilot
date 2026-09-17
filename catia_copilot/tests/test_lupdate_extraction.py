"""真实 pyside6-lupdate 提取回归测试。

固化解锁的设计文档 §2.4 提取形式：
- 显式 `-extensions py`（目录模式实测提取 0 词条）；
- 显式枚举真实源码文件、不枚举 tests/（lupdate 没有 `-exclude` 选项，
  排除测试词条只能靠不把测试目录列入参数）；
- 用 subprocess 列表参数，避免 shell 引号/转义问题。
"""
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_LUPDATE = which("pyside6-lupdate")
_SKIP_REASON = "pyside6-lupdate 不在 PATH 中，跳过真实提取回归"

# 显式源文件枚举清单（与 docs/i18n-design.md §2.4 生产命令一致，phase2 全量）
_PROBE_SOURCES = [
    "catia_copilot/i18n.py",
    "catia_copilot/constants.py",
    "catia_copilot/ui/main_window.py",
    "catia_copilot/ui/catia_embed.py",
    "catia_copilot/ui/bom_edit_dialog_v3.py",
    "catia_copilot/ui/bom_file_rename_dialog.py",
    "catia_copilot/ui/convert_dialog.py",
    "catia_copilot/ui/export_bom_dialog.py",
    "catia_copilot/ui/find_deps_dialog.py",
    "catia_copilot/ui/mass_props_dialog.py",
    "catia_copilot/ui/template_dialog.py",
]


@unittest.skipUnless(_LUPDATE, _SKIP_REASON)
class TestLupdateExtraction(unittest.TestCase):
    """真实调用 pyside6-lupdate 验证提取契约。"""

    def _run_lupdate(self, root: Path, sources: list[Path]) -> Path:
        """在临时目录中运行 lupdate，返回生成的 .ts 路径。"""
        ts = root / "out.ts"
        subprocess.run(
            [_LUPDATE, "-extensions", "py", "-no-obsolete", *map(str, sources), "-ts", str(ts)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        self.assertTrue(ts.exists(), "lupdate 应产出 .ts 文件")
        return ts

    def _sources_in(self, ts: Path) -> list[str]:
        """解析 .ts，返回 CATIACopilot context 下全部 source 文本。"""
        tree = ET.parse(ts)
        contexts = {
            ctx.findtext("name"): ctx
            for ctx in tree.getroot().findall("context")
        }
        self.assertIn("CATIACopilot", contexts, "提取结果必须含 CATIACopilot context")
        return [
            m.find("source").text
            for m in contexts["CATIACopilot"].findall("message")
            if m.find("source") is not None
        ]

    def test_extracts_explicit_sources_and_skips_unlisted_dir(self) -> None:
        """显式枚举的源码词条被提取，未枚举的 tests 目录词条不被误提取。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "probe.py"
            src.write_text(
                "from catia_copilot.i18n import translate\n"
                'LABELS = { "probe": translate("CATIACopilot", "探测被提取 {0}") }\n',
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "unrelated.py").write_text(
                "from catia_copilot.i18n import translate\n"
                'X = translate("CATIACopilot", "测试目录不应被提取 {0}")\n',
                encoding="utf-8",
            )

            ts = self._run_lupdate(root, [src])
            sources = self._sources_in(ts)
            self.assertIn("探测被提取 {0}", sources, "显式源文件中的词条应被提取")
            self.assertNotIn(
                "测试目录不应被提取 {0}", sources, "未枚举的 tests 目录不得被自动提取"
            )

    def test_production_source_list_yields_all_entries(self) -> None:
        """对真实源码树按生产文件清单提取：仅在边界外缺失时意会到，不比对具体数量。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            missing = []
            for rel in _PROBE_SOURCES:
                p = Path(rel)
                if not p.exists():
                    missing.append(rel)
            self.assertEqual(missing, [], "生产提取清单中的源文件必须存在")
            ts = self._run_lupdate(root, [Path(rel) for rel in _PROBE_SOURCES])
            sources = self._sources_in(ts)
            self.assertTrue(sources, f"生产清单提取不应为空（{len(sources)} 条）")

    def test_extraction_matches_handoff_json(self) -> None:
        """生产清单（phase2 全量 11 文件）提取结果须与 docs/i18n-phase2-translations.json 键双向一致。

        该断言正是为捕获「_T 别名包裹 translate、lupdate 漏提取」这类回归而生：
        新增加 UI 词条时若不更新交接清单，或将词条包进 lupdate 不识别的别名，
        两侧键集合即出现差异导致本用例失败。
        """
        import json

        repo_root = Path(__file__).resolve().parents[2]
        handoff = json.loads(
            (repo_root / "docs" / "i18n-phase2-translations.json").read_text(
                encoding="utf-8"
            )
        )
        expected = set(handoff.keys())
        expected.discard("_说明")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ts = self._run_lupdate(root, [Path(rel) for rel in _PROBE_SOURCES])
            extracted = set(self._sources_in(ts))

        self.assertEqual(
            extracted,
            expected,
            "提取词条与交接清单键不一致（缺失=%s 多余=%s）"
            % (sorted(expected - extracted), sorted(extracted - expected)),
        )


if __name__ == "__main__":
    unittest.main()