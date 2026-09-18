# -*- coding: utf-8 -*-
"""README 双语 i18n 测试（i18n Phase 4 — README 语言链接与内容一致性）。

覆盖目标：
1. README.md（英文）与 README.zh-CN.md（中文）均存在；
2. 两份文档首行均含双向语言链接 [English](README.md) / [简体中文](README.zh-CN.md)；
3. 版本号 / 发布日期 / 作者 / 仓库 clone 地址 / 关键命令（venv、依赖、启动、
   Nuitka 构建脚本、安装包脚本、输出目录）在双语文档中完全一致；
4. README.md 除语言链接中的"简体中文"与 VBA 宏真实属性名"材料"外，
   不得残留其他中文；
5. 两份文档对 help_dialog.py 硬编码属性名的行号引用一致，且引用行号确实
   指向包含真实属性名（物料编码…）的代码行。
"""
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EN = _ROOT / "README.md"
_ZH = _ROOT / "README.zh-CN.md"
_HELP = _ROOT / "catia_copilot" / "ui" / "help_dialog.py"

_CJK = re.compile(r"[\u4e00-\u9fff]+")


def _line_pairs(text: str) -> list[tuple[int, int]]:
    """从 README 中提取 help_dialog.py 行的“数字–数字”行号对。"""
    for line in text.splitlines():
        if "catia_copilot/ui/help_dialog.py" in line:
            return [tuple(int(x) for x in p) for p in re.findall(r"(\d+)\s*[–-]\s*(\d+)", line)]
    return []


class TestReadmeLanguageLinks(unittest.TestCase):
    """语言链接 + 版本/命令一致性 + 英文纯度 + 跨文档行号引用。"""

    def setUp(self) -> None:
        self.en = _EN.read_text(encoding="utf-8")
        self.zh = _ZH.read_text(encoding="utf-8")

    def test_both_readmes_exist(self) -> None:
        self.assertTrue(_EN.is_file(), "英文 README.md 应存在")
        self.assertTrue(_ZH.is_file(), "中文 README.zh-CN.md 应存在")

    def test_english_top_has_bidirectional_links(self) -> None:
        first = self.en.splitlines()[0]
        self.assertIn("[English](README.md)", first)
        self.assertIn("[简体中文](README.zh-CN.md)", first)

    def test_chinese_top_has_bidirectional_links(self) -> None:
        first = self.zh.splitlines()[0]
        self.assertIn("[English](README.md)", first)
        self.assertIn("[简体中文](README.zh-CN.md)", first)

    def test_version_date_author_consistent(self) -> None:
        for key in ("2.2.0", "2026-07-01", "CHEN Weibo"):
            self.assertIn(key, self.en, f"英文 README 应包含 {key}")
            self.assertIn(key, self.zh, f"中文 README 应包含 {key}")

    def test_commands_consistent(self) -> None:
        commands = (
            "git clone https://github.com/RayDutchman/CATIA-Copilot.git",
            "python -m venv .venv",
            ".venv\\Scripts\\activate",
            "pip install -r requirements.txt",
            "python main.py",
            ".\\build_nuitka.ps1",
            ".\\build_nuitka_installer.ps1",
            "..\\CATIA-Copilot-dist-nuitka\\",
        )
        for cmd in commands:
            self.assertIn(cmd, self.en, f"英文 README 应包含命令 {cmd!r}")
            self.assertIn(cmd, self.zh, f"中文 README 应包含命令 {cmd!r}")

    def test_key_sections_present(self) -> None:
        for text in (self.en, self.zh):
            self.assertIn("PRESET_USER_REF_PROPERTIES", text)
            self.assertIn("thucwb@gmail.com", text)
            self.assertIn("Nuitka", text)
            self.assertIn("Inno Setup", text)

    def test_english_readme_cjk_only_allowed_tokens(self) -> None:
        tokens = set(_CJK.findall(self.en))
        outside = tokens - {"简体中文", "材料"}
        self.assertEqual(outside, set(),
                         "英文 README 除语言链接'简体中文'与真实属性名'材料'外"
                         "不得残留中文，实际=%s" % sorted(outside))

    def test_chinese_readme_is_chinese(self) -> None:
        self.assertGreater(len(_CJK.findall(self.zh)), 50,
                           "中文 README 应以中文为主")

    def test_help_line_refs_match_source(self) -> None:
        """help_dialog.py 属性名硬编码行号引用：双语一致且行号真实有效。"""
        pairs_en = _line_pairs(self.en)
        pairs_zh = _line_pairs(self.zh)
        self.assertTrue(pairs_en, "英文 README 应包含 help_dialog.py 行号引用")
        self.assertTrue(pairs_zh, "中文 README 应包含 help_dialog.py 行号引用")
        self.assertEqual(pairs_zh, pairs_en,
                         "两份 README 对 help_dialog.py 的属性名行号引用必须一致")
        lines = _HELP.read_text(encoding="utf-8").splitlines()
        joined = "\n".join(
            lines[i - 1]
            for start, end in pairs_en
            for i in range(start, end + 1)
        )
        self.assertIn("物料编码", joined, "引用行号应指向含 物料编码 属性的代码行")
        self.assertIn("物料名称", joined, "引用行号应指向含 物料名称 属性的代码行")

    def test_help_line_refs_row_describes_hardcoding(self) -> None:
        row = next(
            (l for l in self.en.splitlines() if "catia_copilot/ui/help_dialog.py" in l),
            "",
        )
        self.assertIn("Hardcoded attribute names", row)


if __name__ == "__main__":
    unittest.main()