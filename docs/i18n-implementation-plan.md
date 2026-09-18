# CATIA Copilot 国际化（i18n）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务执行本计划。步骤用 checkbox（`- [ ]`）追踪。
>
> **提交纪律（用户明确要求）**：本计划**不自动 commit / push / 建 release**。各任务末尾的 "Commit" 步骤为**手动建议项**，仅列出将执行的命令，由用户明确批准后才执行。

**Goal:** 在不改动业务常量与交付数据的前提下，为 CATIA Copilot 增加简体中文 / 英文 / 跟随系统的界面语言能力，全程走 PySide6 `QTranslator` + `.ts/.qm`；首版导出内容恒为中文（不随界面语言变化）。

**Architecture:** 中文为源文本，全部 UI 文案经 `catia_copilot/i18n.py` 的 `translate(context, source)`（内部 `QCoreApplication.translate`）运行时翻译；翻译器在 `main.py` 中 `QApplication` 之后、`MainWindow` 之前安装并保持引用；界面语言存 `QSettings('CATIACopilot','Application')`，`system/zh_CN/en_US`，切换重启生效。首版**不新增导出语言机制**（不建 `export_i18n` 模块、不加导出语言设置与下拉框），导出表头/Sheet 名保持现有中文字面量工厂值，仅用回归断言锁定英文 UI 下导出仍为中文。先做 PLM 事件结构化解耦再翻译 PLM 界面，避免破坏现有关键字文本解析。

**Tech Stack:** Python 3.13（仅需 >=3.10）、PySide6>=6.5.0、`pyside6-lupdate`/`pyside6-lrelease`（官方工具链，随 PySide6 安装）、Python 标准库 `unittest`（无 pytest）、Nuitka + Inno Setup + GitHub Actions 构建流水线。

## Global Constraints

（以下为项目级硬约束，每个任务的实现均必须满足，不再逐条重复。）

1. **翻译框架**：只用 PySide6 `QTranslator` + `.ts/.qm`。禁用 PyQt、禁止自建运行时语言字典，禁止 import 期执行翻译。
2. **编码规范**：
   - 统一调用形式：`QCoreApplication.translate("CATIACopilot", "中文字面量")` 或 `translate("CATIACopilot", "中文字面量")`（`catia_copilot.i18n.translate` 透传前者）。`context` 与 `source` 都必须是**字符串字面量**（不得变量/常量/动态值），`source` 必须是中文，使 `pyside6-lupdate` 能定位词条。
   - 该调用形式能被 lupdate 正确提取（context/source/comment 不错位）在 Task 1.6 用命令与断言验证后固化；**不把有限试验推广为「仅支持一种形式」**，也不断言 `self.tr` 等便捷形式不可用。
   - 动态内容用 `translate("CATIACopilot", "共 {0} 项，跳过 {1} 项").format(n, m)` 占位符，占位符数量中英一致（`{0}` `{1}`…）。
   - 禁止句子拼接（多个 `translate` 结果 `+`）、禁止把变量塞进 `source`。
3. **源文本为中文**；ui 层 context 统一 `"CATIACopilot"`（首版不新增导出层 context）。
4. **翻译器生命周期**：`install_translators` 返回的 `QTranslator`（或 None）必须在 `main()` 内保持引用直到 `app.exec()` 返回（否则 GC 后翻译失效）。
5. **回退链**：qm 缺失/加载失败 → `logging.warning` + 界面回退中文；任意词条缺失 → `translate` 返回源中文；系统区域不支持（非 zh）→ `en_US`。
6. **设置键**：`QSettings("CATIACopilot","Application")`，仅 `language` ∈ `system|zh_CN|en_US`（默认 `system`）；首版**无** `export_language`（导出语言选择属后续版本）。
7. **不翻译清单（business invariants，值永不改变）**：COM 属性名（`PartNumber/Nomenclature/Revision/Definition/Source/Description`）、真实 UserRefProperties 中文名与其选项值（物料编码、设计状态、草稿/冻结/发布/废弃…）、`PLM_Version/PLM_Iteration`、文件名与目录名、QSettings key、宏模块名 `MODULES`、AI 工具 schema 名称、产品名 `CATIA Copilot`、`BomNodeType` 键、BOM 列内部 key、`SOURCE_*` 的 `'0'/'1'/'2'`。
8. **Source 值**：存储值恒为 `'0'/'1'/'2'`；下拉 `addItem(显示, itemData)`，itemData 固定 `'0'/'1'/'2'`，回写读 `currentData()`；**禁止文本反查**（删除 `bom_edit_dialog_v3.py:1289` 内联 `{"未知":"0","自制":"1","外购":"2"}.get(text, text)`）。英文直译：`0=Unknown`、`1=Made in-house`、`2=Purchased`（用户确认，非官方 UI 术语）。
9. **哨兵不变量**：`FILENAME_NOT_FOUND="未检索到"`、`FILENAME_UNSAVED="未保存"`（constants.py）是业务值，被 `bom_collect*`/`mass_props_collect` 写入、被 `bom_edit_dialog_v3` 相等比对——**常量值永不改变**；仅渲染层显示翻译文本，行数据/比对仍用常量。
10. **PLM 顺序**：必须先做 `sync.py` 结构化状态码 + `plm_workbench.py` 消费 `SyncEvent`，**之后**才允许翻译 PLM 界面词条；SYNC 相关日志/分隔符/表头在解耦前不允许改字面量（`plm_workbench.py:2804-2826` 依赖 `" | "`、`"<"`、`">>"`、`"[X]"` 与中文表头解析文本）。
11. **导出恒中文**：首版导出内容恒为中文，不新增 `export_i18n` 模块、不装导出翻译器、不加导出语言下拉框；BOM/质量特性导出表头与 Sheet 名保持现有**中文字面量工厂值**；英文界面下导出仍中文，以回归测试锁定（Task 2.4）。
12. **AI**：面板界面文案翻译；会话历史里用户输入原样；`ai/tools.py` 的 schema 名称/描述**不改**。
13. **测试**：测试放 `catia_copilot/tests/`（`tests/__init__.py` 必须存在，`python -m unittest discover` 才可发现该包），标准库 `unittest`；运行：`python -m unittest discover -s catia_copilot/tests -t . -v`；GUI 无关测试不实例化 `QApplication`（用 `QCoreApplication`/mock），需要事件循环时用 `QT_QPA_PLATFORM=offscreen python -m unittest ...`。**import 通过 ≠ 功能可用**，评审以真实断言为准。
14. **构建**：`resources/` 整目录已随打包（Nuitka `--include-data-dir=resources=resources`、PyInstaller data specs 均已在用，主程序图标/字体同机制验证正常），qm 放 `resources/i18n/`，**无需新增打包配置**；不臆断 resource_path 故障。
15. **文档语言**：`README.md` 改英文、新建 `README.zh-CN.md` 承载原中文，双向链接，版本号/安装命令中英一致。
16. **git 忽略**：`.gitignore:81` 的 `tests/` 会匹配任意层级，`catia_copilot/tests/` 因此被忽略——需改为 `tests/` 前加锚定（改 `tests/` 所在行为根锚定形式）或追加否定；本计划采用「将第 81 行的 `tests/` 改为 `/tests/`（同时追加 `*.qm` 忽略）」，见 Task 1.1。
17. **文件所有权**：`constants.py`、`main.py`、`resources/i18n/*.ts`、构建脚本、`release.yml` 仅**主 agent** 修改；其余窗口文件可派并发子 agent，**每子 agent 独占文件边界**；阶段内各 agent 完成后由主 agent 统一 `pyside6-lupdate` 合并 TS；每阶段独立 review gate。

---

## Phase 1 — 基础设施与主窗口试点

### Task 1.1: 测试基座与 git 忽略修正

**Files:**
- Modify: `.gitignore:81`（`tests/` → `/tests/`，追加 `*.qm` 忽略规则）
- Create: `catia_copilot/tests/__init__.py`（空文件）
- Create: `catia_copilot/tests/smoke_test.py`

**Interfaces:**
- Consumes: none（本任务不依赖翻译基础设施）。
- Produces: 可被 `unittest discover` 发现并运行的测试目录；`python -m unittest discover -s catia_copilot/tests -t . -v` 全绿。

**步骤：**

- [ ] **Step 1: 确认当前忽略行为**

```powershell
git check-ignore catia_copilot/tests/__init__.py
```
Expected: 返回该路径（`tests/` 规则匹配任意层级，证实需修正）。

- [ ] **Step 2: 修改 `.gitignore`**

把第 81 行 `tests/` 改为 `/tests/`（仅根目录测试被忽略），并在文件末尾追加：

```
# 国际化编译产物（构建流程用 pyside6-lrelease 生成）
*.qm
```

- [ ] **Step 3: 复核忽略行为**

```powershell
git check-ignore catia_copilot/tests/__init__.py; git check-ignore tests/ # 期望根 tests/ 仍被忽略
```
Expected: 第一次无输出（不再被忽略），第二次输出 `tests/`。

- [ ] **Step 4: 写冒烟测试**

`catia_copilot/tests/smoke_test.py`：

```python
import unittest


class SmokeTest(unittest.TestCase):
    def test_trivial(self):
        self.assertEqual(1 + 1, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: 运行验证**

```powershell
python -m unittest discover -s catia_copilot/tests -t . -v
```
Expected: `Ran 1 test ... OK`。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add .gitignore catia_copilot/tests/
git commit -m "chore: 修正测试目录忽略规则并建立 unittest 基座"
```

---

### Task 1.2: 核心翻译基础设施 `catia_copilot/i18n.py`

**Files:**
- Create: `catia_copilot/i18n.py`
- Test: `catia_copilot/tests/test_i18n.py`

**Interfaces:**
- Produces:
  - `APP_CTX: str = "CATIACopilot"`
  - `LANG_SYSTEM = "system"`、`LANG_ZH_CN = "zh_CN"`、`LANG_EN_US = "en_US"`
  - `def translate(context: str, source: str, disambiguation: str = "", n: int = -1) -> str` — 内部 `QCoreApplication.translate`。**函数名必须叫 `translate`，context 与 source 必须为调用点字面量**（lupdate 提取要求）。
  - `def resolve_ui_language(setting: str) -> str` — `system/zh_CN/en_US → zh_CN | en_US`（system 下非 zh 区域回退 en_US）。
  - `def read_language() -> str` / `def write_language(value: str) -> None` — 读/写 `QSettings`（仅界面语言，无导出语言键）。
  - `def install_translators(app, ui_lang: str | None = None) -> QTranslator | None` — 按 `resolve_ui_language(ui_lang or read_language())` 安装**单个**界面翻译器；`zh_CN` 不装；qm 失败 → `logging.warning` 并回退中文；返回 translator（或 None），调用方必须保存引用。**不安装导出翻译器**。
  - `def current_ui_language() -> str` — 记录实际生效的界面语言。

**步骤：**

- [ ] **Step 1: 写失败测试** `catia_copilot/tests/test_i18n.py`

```python
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication

from catia_copilot import i18n


class TestResolveLanguage(unittest.TestCase):
    def test_explicit_zh(self):
        self.assertEqual(i18n.resolve_ui_language("zh_CN"), "zh_CN")

    def test_explicit_en(self):
        self.assertEqual(i18n.resolve_ui_language("en_US"), "en_US")

    @patch("catia_copilot.i18n.QLocale")
    def test_system_zh_family(self, mock_locale):
        mock_locale.system().name.return_value = "zh_TW"
        self.assertEqual(i18n.resolve_ui_language("system"), "zh_CN")

    @patch("catia_copilot.i18n.QLocale")
    def test_system_unknown_fallback_en(self, mock_locale):
        mock_locale.system().name.return_value = "ja_JP"
        self.assertEqual(i18n.resolve_ui_language("system"), "en_US")


class TestTranslateFallback(unittest.TestCase):
    """翻译缺失时必须返回源中文，绝不抛异常。"""

    def test_missing_source_returns_source(self):
        app = QCoreApplication.instance()
        result = i18n.translate("CATIACopilot", "绝不会在 qm 中存在的词条")
        self.assertEqual(result, "绝不会在 qm 中存在的词条")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest catia_copilot.tests.test_i18n -v
```
Expected: `ModuleNotFoundError: No module named 'catia_copilot.i18n'`。

- [ ] **Step 3: 实现 `catia_copilot/i18n.py`**

```python
"""CATIA Copilot 国际化基础设施。

编码规范（提取形式以 Task 1.6 命令+断言验证后固化）：
- 采用形式：
      from catia_copilot.i18n import translate
      translate("CATIACopilot", "中文字面量")
  context 与 source 均须为调用点字符串字面量；禁止用变量/常量当 context 或 source。
- 不把有限探测推广为「唯一形式」；自定义便捷形式须经 Task 1.6 提取验证后再采纳。
- 禁止句子拼接与变量塞入 source。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication, QLocale, QSettings, QTranslator

logger = logging.getLogger(__name__)

APP_CTX = "CATIACopilot"

LANG_SYSTEM = "system"
LANG_ZH_CN = "zh_CN"
LANG_EN_US = "en_US"

_ORG = "CATIACopilot"
_APP = "Application"

_current_ui_lang: str = LANG_ZH_CN


def translate(context: str, source: str, disambiguation: str = "", n: int = -1) -> str:
    """UI 文案统一入口（首版导出恒中文，不走本入口）。source 必须是中文字面量。"""
    return QCoreApplication.translate(context, source, disambiguation, n)


def current_ui_language() -> str:
    """返回 install_translators 后实际生效的界面语言。"""
    return _current_ui_lang


def resolve_ui_language(setting: str) -> str:
    """把用户设置解析为实际界面语言。system 下非中文区域一律回退 en_US。"""
    if setting == LANG_ZH_CN:
        return LANG_ZH_CN
    if setting == LANG_EN_US:
        return LANG_EN_US
    name = QLocale.system().name()  # 例如 zh_CN / en_US / ja_JP
    if name.split("_")[0].lower() == "zh":
        return LANG_ZH_CN
    return LANG_EN_US


def read_language() -> str:
    return QSettings(_ORG, _APP).value("language", LANG_SYSTEM, type=str)


def write_language(value: str) -> None:
    QSettings(_ORG, _APP).setValue("language", value)
    QSettings(_ORG, _APP).sync()


def _qm_path(lang: str) -> str:
    """返回对应语言 qm 的绝对路径；缺文件返回空串。"""
    if lang == LANG_ZH_CN:
        return ""
    from catia_copilot.utils import resource_path

    p = resource_path(f"resources/i18n/catia_copilot_{lang}.qm")
    import os

    return p if os.path.isfile(p) else ""


def install_translators(app, ui_lang: str | None = None):
    """按设置安装界面语言翻译器（只装 UI，不装导出翻译器）。

    返回需保持引用的 QTranslator 或 None。
    - zh_CN：源语言，不安装。
    - 其他：加载 qm 失败时仅告警并回退中文，不中断启动。
    """
    global _current_ui_lang
    lang = resolve_ui_language(ui_lang or read_language())
    _current_ui_lang = lang
    if lang == LANG_ZH_CN:
        return None
    qm = _qm_path(lang)
    if not qm:
        logger.warning("未找到翻译文件，界面回退中文: resources/i18n/catia_copilot_%s.qm", lang)
        return None
    translator = QTranslator(app)
    if not translator.load(qm):
        logger.warning("翻译文件加载失败，界面回退中文: %s", qm)
        return None
    app.installTranslator(translator)
    return translator
```

（说明：`resolve_ui_language` 对 `read_language()` 的返回值本身做了 `system` 兜底；`install_translators` 的 `ui_lang` 参数供测试注入。`translate` 调用点必须使用 `"CATIACopilot"` 字面量，**不能引用 `APP_CTX` 常量名**——lupdate 按字面量提取，常量引用会漏提取。）

- [ ] **Step 4: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_i18n -v
```
Expected: `Ran 5 tests ... OK`（TestResolveLanguage 4 例 + TestTranslateFallback 1 例）。

- [ ] **Step 5: 验证 lupdate 提取形式（一次性探测；正式命令+断言验证并入 Task 1.6）**

```powershell
$probe = "$env:TEMP\opencode\i18n_probe\probe_final.py"
# 内容：from catia_copilot.i18n import translate; translate("CATIACopilot", "探测词条")
pyside6-lupdate $probe -ts "$env:TEMP\opencode\i18n_probe\probe_final.ts" -no-obsolete
Get-Content "$env:TEMP\opencode\i18n_probe\probe_final.ts"
```
Expected: `<context><name>CATIACopilot</name>` 内出现 `<source>探测词条</source>`。

> 注意：不要把本次有限探测总结为「lupdate 只支持某一种形式 / `self.tr` 不可用」。执行阶段（Task 1.6）必须用命令对**真实源码树**再验证，并以断言固化规范。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add catia_copilot/i18n.py catia_copilot/tests/test_i18n.py
git commit -m "feat: 新增 QTranslator 翻译基础设施与语言设置读写"
```

---

### Task 1.3: `main.py` 安装翻译器

**Files:**
- Modify: `main.py`（`app = QApplication(sys.argv)` 之后、`window = MainWindow(...)` 之前插入）
- Test: `catia_copilot/tests/test_i18n.py` 追加

**Interfaces:**
- Consumes: `i18n.install_translators(app)`、`i18n.translate("CATIACopilot", "...")`。
- Produces: 进程级翻译器生效；`QSettings` 语言回读。

**步骤：**

- [ ] **Step 1: 追加失败测试** `test_i18n.py`

```python
class TestInstallTranslatorsBehavior(unittest.TestCase):
    def test_zh_installs_nothing(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        tr = i18n.install_translators(app, ui_lang="zh_CN")
        self.assertIsNone(tr)

    def test_en_missing_qm_falls_back_to_chinese(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        with patch("catia_copilot.i18n._qm_path", return_value=""):
            tr = i18n.install_translators(app, ui_lang="en_US")
        self.assertIsNone(tr)
        self.assertEqual(i18n.current_ui_language(), "en_US")  # 解析结果仍记录为英文
```

- [ ] **Step 2: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_i18n -v
```
Expected: `Ran 7 tests ... OK`。

- [ ] **Step 3: 修改 `main.py`**

在 `app = QApplication(sys.argv)` 后、`window = MainWindow(...)` 前加入：

```python
from catia_copilot import i18n  # 若已 import 则跳过此行

# “跟随系统 / 简体中文 / English” 语言支持：翻译器必须在 MainWindow 之前安装
_i18n_translator = i18n.install_translators(app)
```

保持 `_i18n_translator` 为 `main()`/`if __name__` 块内**存活至程序退出**的局部变量（`app.exec()` 之后仍引用）。若 `main.py` 有 `if __name__ == "__main__":` 结构，将变量绑定在模块级或 `main()` 内部均可；**不得**仅临时赋值即丢弃。

- [ ] **Step 4: 静态验证**

```powershell
python -c "import ast,io; t=ast.parse(open('main.py',encoding='utf-8').read()); print('parse ok')"
```
Expected: `parse ok`（导入检查由后续入口测试覆盖，不导入主模块以免运行污染）。

- [ ] **Step 5: 入口冒烟（头less 运行真实 main，失败允许凭据/文件缺失）**

```powershell
$env:QT_QPA_PLATFORM="offscreen"; $env:LANG_CONFIG_FOR_TEST=""; timeout 15 python main.py 2>&1 | Select-Object -First 5
```
Expected: 无 `QTranslator`/`i18n` 相关异常（若因缺 CATIA 环境退出属正常，只要求翻译安装段无抛错）。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add main.py
git commit -m "feat: 在 MainWindow 之前安装界面语言翻译器"
```

---

### Task 1.4: 主窗口试点 —— `_ACTION_LABELS` 运行时化

**Files:**
- Modify: `catia_copilot/ui/main_window.py`（类属性 `_ACTION_LABELS` 改为随运行时语言求值）
- Test: `catia_copilot/tests/test_action_labels.py`

**Interfaces:**
- Consumes: `from catia_copilot.i18n import translate`。
- Produces: `MainWindow.action_labels()` 静态方法（替代类属性 `_ACTION_LABELS`，函数体内直接 `translate("CATIACopilot", "...")` **字面量调用**），词条集合、key、顺序不变。

**步骤：**

- [ ] **Step 1: 写失败测试**

`catia_copilot/tests/test_action_labels.py`：

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication

from catia_copilot import i18n
from catia_copilot.ui.main_window import MainWindow


class TestActionLabels(unittest.TestCase):
    KEY_SUBSET = {  # _ACTION_LABELS 的词条应与现状一致（仅抽查关键键）
        "bom_edit", "bom_export", "mass_props", "drawing_export",
        "run_macro", "commit_all", "open_workspace", "open_community",
    }

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_keys_unchanged(self):
        labels = MainWindow.action_labels()
        self.assertTrue(self.KEY_SUBSET.issubset(set(labels.keys())))

    def test_chinese_default(self):
        i18n.install_translators(self.app, ui_lang="zh_CN")
        labels = MainWindow.action_labels()
        self.assertEqual(labels["bom_edit"], "BOM 工作台")  # 以实际源中文为准

    def test_english_when_translator_active(self):
        # 构造最小 qm：至少覆盖 “BOM 工作台” 词条
        import tempfile, subprocess, pathlib
        d = pathlib.Path(tempfile.mkdtemp())
        ts = d / "en.ts"
        ts.write_text(
            """<?xml version="1.0" encoding="utf-8"?>
<TS version="2.1" language="en_US">
<context><name>CATIACopilot</name>
<message><source>BOM 工作台</source><translation>BOM Workbench</translation></message>
</context></TS>""", encoding="utf-8"
        )
        subprocess.run(["pyside6-lrelease", str(ts)], check=True, capture_output=True)
        from PySide6.QtCore import QTranslator
        t = QTranslator(self.app)
        ok = t.load(str(d / "en.qm"))
        self.assertTrue(ok, "测试 qm 生成失败")
        # 直接安装（绕过 i18n 以隔离本用例）
        self.app.installTranslator(t)
        try:
            labels = MainWindow.action_labels()
            self.assertEqual(labels["bom_edit"], "BOM Workbench")
        finally:
            self.app.removeTranslator(t)


if __name__ == "__main__":
    unittest.main()
```

（词条内容以 main_window.py 现状为准；若源中文不同则同步修改断言。）

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest catia_copilot.tests.test_action_labels -v
```
Expected: `AttributeError: ... _ACTION_LABELS` 或断言失败（取决于当前实现是类属性还是方法）。

- [ ] **Step 3: 重构为 `action_labels()`**

保留 key 与顺序，把**类属性改为静态方法**，函数体内直接写 `translate` **字面量调用**（固定 label 工厂，使 lupdate 可见）：

```python
@staticmethod
def action_labels() -> dict:
    """动作显示名，随当前界面语言动态求值（嵌入菜单与主窗口共用）。"""
    return {
        "bom_edit": translate("CATIACopilot", "BOM 工作台"),
        "bom_export": translate("CATIACopilot", "从产品导出 BOM"),
        # …… 其余键保持现状，全部改为 translate("CATIACopilot", "原中文字面量")
    }
```

`catia_embed.py` 中的嵌入菜单弹出处（~L916-929）改为调用 `MainWindow.action_labels()`（**函数级懒加载，弹出时取运行时文案**）。**二选一**：若 embed 仍以 `MainWindow.action_labels()` 读取，主窗口按钮文字、≡页、嵌入菜单三处共用同一函数，保证运行时一致。

- [ ] **Step 4: 更新所有 `_ACTION_LABELS` 引用点**

```powershell
rg -n "_ACTION_LABELS" catia_copilot/ui/main_window.py catia_copilot/ui/catia_embed.py
```
逐个把类属性访问替换为 `MainWindow.action_labels().get(key, ...)` 或 `self.action_labels()`；**词条 key 与默认值不得改变**。

- [ ] **Step 5: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_action_labels -v
```
Expected: `Ran 3 tests ... OK`（english 用例 `BOM Workbench` 通过说明运行时语言生效）。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add catia_copilot/ui/main_window.py catia_copilot/ui/catia_embed.py catia_copilot/tests/test_action_labels.py
git commit -m "feat: 主窗口动作菜单文案运行时化（支持界面语言切换）"
```

---

### Task 1.5: 主窗口「设置」区（界面语言切换）

**Files:**
- Modify: `catia_copilot/ui/main_window.py`（`_build_more_page` 区域新增「设置」节）
- Test: `catia_copilot/tests/test_language_ui.py`

**Interfaces:**
- Consumes: `i18n.read_language()/write_language()`。
- Produces: `MainWindow` 上「界面语言」`QComboBox` + 「保存设置」按钮 + 重启提示。**首版不提供导出语言选择。**

**步骤：**

- [ ] **Step 1: 写失败测试**

`catia_copilot/tests/test_language_ui.py`：

```python
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox

from catia_copilot import i18n
from catia_copilot.ui.main_window import MainWindow


class TestLanguageUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_present_and_save(self):
        w = MainWindow()
        w.show()
        try:
            boxes = {c.objectName(): c for c in w.findChildren(QComboBox)}
            self.assertIn("cmbUILang", boxes)
            self.assertNotIn("cmbExportLang", boxes)  # 首版无导出语言选择
            boxes["cmbUILang"].setCurrentText("English")
            with patch("catia_copilot.ui.main_window.QMessageBox.information"):
                # 触发“保存”按钮
                btn = next(b for b in w.findChildren(type(w)) if b.objectName() == "btnSaveLang")
                btn.click()
            self.assertEqual(i18n.read_language(), "en_US")
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
```

（控件 objectName 由本任务实现时**固定命名**：`cmbUILang`、`btnSaveLang`，后续任务不得改名。）

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest catia_copilot.tests.test_language_ui -v
```
Expected: `AssertionError / AttributeError`（控件不存在）。

- [ ] **Step 3: 在 `_build_more_page` 增加「设置」节**

在 ≡ 页末追加一个新 section，包含（中文注释表示原文案，翻译词条在 .ts 生成阶段统一）：

```python
from catia_copilot.i18n import translate  # 模块顶部已导入则复用

# —— 设置：界面语言（其余代码复用现有 section 样板） ——
sec = QWidget()
lay = QVBoxLayout(sec)
lay.addWidget(QLabel(translate("CATIACopilot", "设置")))

row_ui = QHBoxLayout()
row_ui.addWidget(QLabel(translate("CATIACopilot", "界面语言")))
self.cmbUILang = QComboBox()
self.cmbUILang.addItem(translate("CATIACopilot", "跟随系统"), "system")
self.cmbUILang.addItem(translate("CATIACopilot", "简体中文"), "zh_CN")
self.cmbUILang.addItem("English", "en_US")  # English 词条为源中审后仍有英文标签；实际以 ts 译文为准
row_ui.addWidget(self.cmbUILang)
lay.addLayout(row_ui)

self.btnSaveLang = QPushButton(translate("CATIACopilot", "保存设置（重启后生效）"))
self.btnSaveLang.clicked.connect(self._save_lang_settings)
lay.addWidget(self.btnSaveLang)
```

`_save_lang_settings`：

```python
def _save_lang_settings(self):
    i18n.write_language(self.cmbUILang.currentData())
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information(
        self, translate("CATIACopilot", "提示"),
        translate("CATIACopilot", "语言设置已保存，重启程序后生效。"))
```

在 `_build_more_page` 初始化末尾按当前设置回填下拉框（`read_language()` 对应 itemData）。

- [ ] **Step 4: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_language_ui -v
```
Expected: `Ran 1 test ... OK`。

- [ ] **Step 5: Commit（手动，待用户批准）**

```bash
git add catia_copilot/ui/main_window.py catia_copilot/tests/test_language_ui.py
git commit -m "feat: ≡页新增界面语言设置（重启生效）"
```

---

### Task 1.6: 提取形式命令+断言验证，生成首个 TS 词条全集并英译（主 agent 独占）

**Files:**
- Create: `resources/i18n/catia_copilot_zh_CN.ts`、`resources/i18n/catia_copilot_en_US.ts`
- Modify: `.gitignore`（已含 `*.qm`，无需改）

**Interfaces:**
- Produces:
  - **提取形式验证**：对任务内实际源码树跑 `pyside6-lupdate`，用断言核对 context 正确（`CATIACopilot`）、source 非空、无错位；形式不合规的词条列入清单，退回对应任务修正再合并。
  - 第一批词条（Task 1.4/1.5 引入的 action labels + 设置节 + 主窗口已迁移文案）的 zh/en 双 TS；en_US 无 `type="unfinished"`。

**步骤：**

- [ ] **Step 1: 提取词条**

```powershell
pyside6-lupdate -ts resources/i18n/catia_copilot_zh_CN.ts resources/i18n/catia_copilot_en_US.ts -no-obsolete main.py catia_copilot
```
Expected: `Found N source text(s)`；两个文件同步生成，`en_US.ts` 每条 `<translation type="unfinished">`。

- [ ] **Step 2: 提取形式断言验证（命令+断言，固化规范）**

写入临时检查脚本（仅 ASCII，避免 PowerShell 5.1 编码问题）并运行：

```powershell
$dir = "$env:TEMP\opencode\i18n_probe"
$script = @"
import xml.etree.ElementTree as ET, sys
root = ET.parse(r"resources/i18n/catia_copilot_zh_CN.ts").getroot()
bad = []
for ctx in root.findall("context"):
    name = ctx.findtext("name") or ""
    for msg in ctx.findall("message"):
        src = msg.findtext("source") or ""
        if name == "" or src == "":
            bad.append((name, src))
        if msg.findtext("comment") and msg.findtext("comment") == src:
            bad.append((name, src))
if bad:
    print("EXTRACTION FAIL:", bad); sys.exit(1)
print("EXTRACTION OK: %d contexts, %d messages" % (len(root.findall("context")), len(root.findall(".//message"))))
"@
[System.IO.File]::WriteAllText("$dir\check_extraction.py", $script)
python "$dir\check_extraction.py"
```
Expected: `EXTRACTION OK: ...`。若失败，说明有词条未按统一字面量形式调用，退回相关任务修正后再进入翻译。

> 说明：此处以**实际源码树**验证为准（不以 Task 1.2 一次性探针为最终结论）；`self.tr`/自定义便捷函数等未经验证的形式**不得**作为规范。

- [ ] **Step 3: 英文翻译**（在 Linguist 中或直接编辑 `en_US.ts` XML）

逐条为 `en_US.ts` 的每个 `<message>` 填写 `<translation>` 并去掉 `type="unfinished"`。**属性名、文件名、`CATIA Copilot`、`Source`、`Unknown/Made in-house/Purchased`、`DisplayName` 等不译；主窗口标题 `CATIA Copilot` 保留。**

- [ ] **Step 4: 用占位符一致性自检**

```powershell
rg -n "unfinished" resources/i18n/catia_copilot_en_US.ts
```
Expected: 无输出（全部已译）。

- [ ] **Step 5: 编译 qm 并完成 Task 1.4 的英文端到端**

```powershell
pyside6-lrelease resources/i18n/catia_copilot_en_US.ts
```
Expected: `Generated N translation(s) (N finished ...)`；qm 落在 `resources/i18n/catia_copilot_en_US.qm`。

- [ ] **Step 6: 回归整个测试套件**

```powershell
python -m unittest discover -s catia_copilot/tests -t . -v
```
Expected: 全部通过（此时真实 qm 存在，`test_english_when_translator_active` 可用真实 qm 替换临时 qm 后仍通过）。

- [ ] **Step 7: Commit（手动，待用户批准）**

```bash
git add resources/i18n/*.ts catia_copilot/ui/
git commit -m "feat(i18n): 生成首批 zh/en 词条并编译 en_US.qm"
```

---

### Phase 1 Review Gate

- [ ] 无 CATIA 环境下 `unittest` 全绿；`rg -n "unfinished" resources/i18n/catia_copilot_en_US.ts` 无输出。
- [ ] `action_labels()` 三处（主窗口按钮、≡页、嵌入菜单）语言一致；设置保存后 `QSettings` 值与测试断言一致。
- [ ] `main.py` 翻译器安装在 MainWindow 之前且引用存活；入口冒烟无翻译异常。
- 评审人：主 agent + 用户确认后进入 Phase 2。

---

## Phase 2 — 业务 key 解耦与各窗口迁移

### Task 2.1: `constants.py` 显示映射解耦（主 agent 独占）

**Files:**
- Modify: `catia_copilot/constants.py`
- Test: `catia_copilot/tests/test_constants_i18n.py`

**Interfaces:**
- Consumes: `i18n.translate("CATIACopilot", "...")`。
- Produces: 移除 `TYPE_DISPLAY_NAMES`、`BOM_COLUMN_DISPLAY_NAMES`、`MASS_PROPS_COLUMN_DISPLAY_NAMES`、`SOURCE_TO_DISPLAY`、`SOURCE_FROM_DISPLAY`、`ABOUT_TEXT`、`AI_TAB_LABEL` 的**导入期中文构建**；改为函数或在各自渲染点用 `translate`（保持既有消费方 import 与调用方式，若消费方已迁移则删除死代码）。

**步骤：**

- [ ] **Step 1: 盘点消费点**

```powershell
rg -n "TYPE_DISPLAY_NAMES|BOM_COLUMN_DISPLAY_NAMES|MASS_PROPS_COLUMN_DISPLAY_NAMES|SOURCE_TO_DISPLAY|SOURCE_FROM_DISPLAY|SOURCE_OPTIONS|ABOUT_TEXT|AI_TAB_LABEL" catia_copilot --glob "!i18n.py"
```
记录每个消费点文件:行。

- [ ] **Step 2: 确认边界**

- `BomNodeType`、`BOM_READONLY_COLUMNS`、`BOM_COLUMN_MIN_WIDTHS`、`BOM_EDIT_DELETED_ROW_PAD_TOP`、`PRESET_USER_REF_PROPERTIES`、`PRESET_USER_REF_PROPERTY_OPTIONS`、`PLM_MEMBER_TABLE_COLUMNS`、`FILENAME_NOT_FOUND`、`FILENAME_UNSAVED`、`BOM_TABLE_DEFAULT_WIDTH`、`CATIA_COPILOT_MODULES`、column key 常量、`SOURCE_*` 的 `'0'/'1'/'2'` **保留原样不译不改**。
- 仅显示用字面量（`"物料编码"`、`"设计状态"`、`"草稿/冻结/发布/废弃"` 等 USER_PROP 的值）**若被写入 CATIA 则不改**；确认哪些仅 UI 展示。

- [x] **Step 3: 提供运行时等价物**（在 `constants.py` 内或就近新增函数，**不得在 import 期执行 translate**；采用固定 label 工厂，函数体内逐 key 直接写 `translate("CATIACopilot", "原中文字面量")` **字面量调用**，使 lupdate 可提取）：

```python
# 删除原 TYPE_DISPLAY_NAMES 等导入期中文字典；提供：
def type_display(key: str) -> str:
    names = {
        "sketch": translate("CATIACopilot", "草图"),
        "partbody": translate("CATIACopilot", "零件几何体"),
        # …… 全部 key 与现状一致
    }
    return names.get(key, key)
```

> ⚠️ 不要建「中文→英文」双字典按 `current_ui_language()` 切换，也不要动态 key 查表翻译——那样 lupdate 无法提取词条；工厂函数体内必须是**直接字面量**。

同理 `BOM_COLUMN_DISPLAY_NAMES` → `bom_column_display(key)`（保留「完整路径」特例逻辑，见 bom_edit_dialog_v3 `_header_labels`）；`ABOUT_TEXT` → `build_about_text()`（内用 `translate("CATIACopilot", "...").format(APP_NAME, APP_VERSION, ...)`）；`AI_TAB_LABEL` → 主窗口 Tab 插入/更新处直接 `translate("CATIACopilot", "AI 助手")`。`SOURCE_FROM_DISPLAY` **删除**（反查不可靠），改由 `currentData()` 回写（见 Task 2.2）。

- [ ] **Step 4: 逐消费点改造**

```powershell
rg -n "SOURCE_FROM_DISPLAY|SOURCE_TO_DISPLAY" catia_copilot/ui/bom_edit_dialog_v3.py
```
对每个引用：展示走 `translate`，反查删除，回写走 itemData。`ABOUT_TEXT` 改为 `build_about_text()`（内用 `translate(...).format(APP_NAME, version, ...)`），帮助对话框与关于弹窗改用该函数。

- [x] **Step 5: 回归测试**（新增/更新断言）

`catia_copilot/tests/test_constants_i18n.py`：

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication

from catia_copilot import i18n
import catia_copilot.constants as C


class TestConstantsInvariants(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])

    def test_sentinels_never_change(self):
        self.assertEqual(C.FILENAME_NOT_FOUND, "未检索到")
        self.assertEqual(C.FILENAME_UNSAVED, "未保存")

    def test_source_values_constant(self):
        self.assertEqual((C.SOURCE_MAKE, C.SOURCE_BUY), ("1", "2"))

    def test_about_text_builds_runtime(self):
        text = C.build_about_text()   # 若改为函数则断言调用成功且含产品名
        self.assertIn("CATIA Copilot", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_constants_i18n -v
```
Expected: `Ran 3 tests ... OK`（`SOURCE_MAKE/SOURCE_BUY` 若不存在则调整断言为 `"0"/"1"/"2"` 满足）。

- [ ] **Step 7: Commit（手动，待用户批准）**

```bash
git add catia_copilot/constants.py catia_copilot/tests/test_constants_i18n.py
git commit -m "refactor(i18n): constants 显示映射解耦为运行时翻译，哨兵与存储值不变"
```

---

### Task 2.2: `bom_edit_dialog_v3.py` 迁移（含 Source itemData 与哨兵渲染）

**Files:**
- Modify: `catia_copilot/ui/bom_edit_dialog_v3.py`
- Test: `catia_copilot/tests/test_bom_edit_dialog_i18n.py`

**Interfaces:**
- Consumes: `i18n.translate("CATIACopilot", "...")`；`bom_collect*`/`bom_write` 输入输出不变。
- Produces: 树/表显示文本随语言；行数据与 `_rows` 仍为业务常量；Source 回写为 `'0'/'1'/'2'`。

**步骤：**

- [ ] **Step 1: 写哨兵与反查的失败测试**

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from catia_copilot import i18n
from catia_copilot.ui.bom_edit_dialog_v3 import BomEditDialog


class TestBomDialogSourceAndSentinel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_combo_writeback_is_itemdata(self):
        dlg = BomEditDialog(None)
        combo = dlg.source_combo  # 本任务固定暴露的 QComboBox 属性（或等价定位方式）
        combo.setCurrentIndex(2)          # 第三项 = “外购”
        self.assertEqual(combo.currentData(), "2")   # 回写用数据而非文本
        self.assertEqual(combo.itemText(combo.currentIndex()), i18n.translate("CATIACopilot", "外购"))

    def test_sentinel_rows_keep_constant(self):
        # 通过行数据构建验证：树项显示译文、_rows 仍存常量
        dlg = BomEditDialog(None)
        rows = dlg.insert_row_for_test(FILENAME_UNSAVED)  # 若公开入口不便则改用内部方法白盒验证
        self.assertIn(FILENAME_UNSAVED, "".join(rows))    # 业务值保持


if __name__ == "__main__":
    unittest.main()
```

（若 `BomEditDialog` 构造需要 BOM 环境，则将 `__init__` 参数 mock 或改为直接测内部 `_build_source_combo`/哨兵渲染函数，具体以源码为准；断言目标不变：**写回走 itemData、行数据走常量**。）

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest catia_copilot.tests.test_bom_edit_dialog_i18n -v
```
Expected: 断言失败（当前文本反查 / 哨兵直接进行数据）。

- [ ] **Step 3: Source 下拉改造**

- 统一一个构建函数 `_build_source_combo()`：三个 itemData 固定 `'0','1','2'`，显示用 `translate("CATIACopilot", "未知"/"自制"/"外购")`。
- 初始化渲染（~L973）用该函数；进度/回显（~L3421）用 itemData；**删除 ~L1289 的 `{"未知":"0",...}.get(text,text)`**。
- 表列「Source」的既有排序/过滤若按文本进行，改为按字段值；`rg -n "未知|自制|外购" catia_copilot/ui/bom_edit_dialog_v3.py` 逐个确认是显示还是逻辑。

- [ ] **Step 4: 哨兵渲染**

- 单元格/树项显示用 `translate("CATIACopilot", "未保存"/"未检索到")`，但 `_rows`、`bom_write`、`bom_collect` 写入/比对值仍用 `FILENAME_UNSAVED`/`FILENAME_NOT_FOUND` 常量。
- 检查 ~L1029-1042、~L1069、~L1098、~L1174、~L2557 处：凡是**比对**用常量，凡是**显示**用 translate，核对无混淆。

- [x] **Step 5: 窗口其余文案**（右键「填充」菜单列名插值已改用运行时 `bom_column_display({0})`，静态 `BOM_COLUMN_DISPLAY_NAMES` 残留已消除）

`rg -n "[\u4e00-\u9fff]" catia_copilot/ui/bom_edit_dialog_v3.py` 列出全部中文；除业务值与类名外，按钮/标题/提示/列显头/右键菜单/校验消息全部改 `translate("CATIACopilot", "...")`；**不可变内容**（属性名 demo `PartNumber`、真实 UserProp 名）外提常量仍不译。

- [x] **Step 6: 回归测试 + 冒烟**

```powershell
python -m unittest catia_copilot.tests.test_bom_edit_dialog_i18n -v
python -m unittest discover -s catia_copilot/tests -t . -v
```
Expected: 全绿。

- [ ] **Step 7: Commit（手动，待用户批准）**

```bash
git add catia_copilot/ui/bom_edit_dialog_v3.py catia_copilot/tests/test_bom_edit_dialog_i18n.py
git commit -m "feat(i18n): BOM V3 对话框文案翻译，Source 回写改 itemData，哨兵仅渲染翻译"
```

---

### Task 2.3: `mass_props_dialog.py` 迁移

**Files:**
- Modify: `catia_copilot/ui/mass_props_dialog.py`
- Test: `catia_copilot/tests/test_mass_props_i18n.py`

**Interfaces:**
- Consumes: `i18n.translate("CATIACopilot", ...)`。
- Produces: 列显头/状态/单位/消息随界面语言；**导出 Excel 表头保持现有中文字面量工厂值（首版导出恒中文，不接界面翻译）**。

**步骤：**

- [ ] **Step 1: 列出全部中文，分类（显示 / 业务）**

```powershell
rg -n "[\u4e00-\u9fff]" catia_copilot/ui/mass_props_dialog.py
```

- [x] **Step 2: 界面文案翻译**（镜像行 ` (对称件)`/`(虚拟)` 显示层随语言，数据层保持中文）

质量特性列显头（来自 `MASS_PROPS_COLUMN_DISPLAY_NAMES` 的展示位置）在渲染处 `translate`；状态行（`计算质量特性中…` 等）翻译；单位/换算标签保留数值。**列内部 key 与 `bom_collect` 写入值不变。**

- [ ] **Step 3: 导出表头恒中文**

导出分支**不接入界面翻译**：`mass_props_dialog.py` 导出段的表头/Sheet 名保持现有中文字面量工厂值原样；英文界面下导出仍中文，由 Task 2.4 回归断言锁定。

- [x] **Step 4: 测试**（`TestMirrorRowDisplayLayer`：zh 回退中文 / en 显示翻译与数据层不变；导出测试镜像行恒中文）

```python
def test_columns_translate_and_keys_stable(self):
    # 断言列对象 key 不变、显示随语言
```

- [ ] **Step 5: 运行与提交（手动）**

```powershell
python -m unittest catia_copilot.tests.test_mass_props_i18n -v
```
全绿后：
```bash
git add catia_copilot/ui/mass_props_dialog.py catia_copilot/tests/test_mass_props_i18n.py
git commit -m "feat(i18n): 质量特性对话框翻译，导出表头保持中文字面量"
```

---

### Task 2.4: 导出恒中文回归测试（首版不新增导出语言机制）

**Files:**
- Create: `catia_copilot/tests/test_export_chinese.py`（回归锁定，不新增模块）
- Modify: 无（导出文案不改动；若 Task 2.3/2.1 误接入界面翻译则回滚）

**Interfaces:**
- Consumes: `bom_export` 表头/Sheet 名工厂、`mass_props_dialog.py` 导出段表头工厂。
- Produces: 断言**英文界面下导出表头与 Sheet 名仍为中文**、且不依赖界面翻译器（回归测试）。

**步骤：**

- [ ] **Step 1: 写回归测试**

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication

from catia_copilot import i18n


class TestExportStaysChinese(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _header_values(self):
        # 按实际导出函数/常量取表头与 Sheet 名（bom_export / mass_props 导出段）
        raise NotImplementedError  # 由实现时依据源码填真实取值方式

    def test_export_headers_chinese_even_with_en_ui(self):
        i18n.install_translators(self.app, ui_lang="en_US")
        headers = self._header_values()
        for h in headers:
            self.assertTrue(any("\u4e00" <= c <= "\u9fff" for c in h),
                            f"导出表头在英文界面下必须仍为中文: {h}")


if __name__ == "__main__":
    unittest.main()
```

（首版导出恒中文：无导出语言设置、无导出语言下拉框、无 `export_i18n` 模块。表头/Sheet 名保持现有中文字面量工厂值，本测试锁定「英文 UI 下导出仍中文」。）

- [x] **Step 2: 运行确认通过**（导出恒中文由 `test_mass_props_i18n.TestExportKeepsChineseUnderEnTranslator` 覆盖，含镜像行数据，实时 en 翻译器下导出仍中文）

```powershell
python -m unittest catia_copilot.tests.test_export_chinese -v
python -m unittest discover -s catia_copilot/tests -t . -v
```
Expected: 全绿。

- [ ] **Step 3: Commit（手动，待用户批准）**

```bash
git add catia_copilot/tests/test_export_chinese.py
git commit -m "test(i18n): 锁定英文界面下导出表头/Sheet 名仍为中文"
```

---

### Task 2.5: 工具对话框批量迁移（并发子 agent，各占文件）

**Files:**
- Modify: `catia_copilot/ui/convert_dialog.py`、`find_deps_dialog.py`、`bom_file_rename_dialog.py`、`template_dialog.py`、`model_state_dialog.py`、`session_config_dialog.py`、`log_window.py`
- Test: 每文件配套 `catia_copilot/tests/test_<模块>_i18n.py`

**Interfaces:**
- Consumes: `from catia_copilot.i18n import translate`。
- Produces: 各对话框全部界面文案翻译；业务串（文件名、宏名、属性名、配置 key、真实 UserProp 值与选项）原样保留。

**步骤（对每个对话框文件，子 agent 执行同一规程）：**

- [ ] **Step 1: 中文全量盘点并分类**

```powershell
rg -n "[\u4e00-\u9fff]" catia_copilot/ui/<目标文件>.py
```
逐条标注：界面文案 / 业务值（不译）/ 配置 key（不译）/ 文件名（不译）。

- [x] **Step 2: 界面文案替换**（`session_config_dialog` 默认模型判定已改 `== itemText(0)`，不再依赖中文前缀）

除业务值外所有 `QPushButton/QLabel/QGroupBox/QToolTip/消息框/联动警告` 的文本改为 `translate("CATIACopilot", "原中文字面量")`；**动态内容用 `.format()` 占位符**保持占位符数量中英一致。

- [ ] **Step 3: 保留不译清单**（每个文件单独核对）

`bom_file_rename_dialog`：文件名模板、`SV_*.CATPART`；`convert_dialog`：文件扩展名、宏选择列表；`model_state_dialog`：属性名与选项值（真实 CATIA 值）；`session_config_dialog`：模块名、`QSettings` key、宏模块名；`template_dialog`：模板项值；`log_window`：日志文本（首轮不译，仅窗口装饰文案可译）。

- [x] **Step 4: 测试**（`test_i18n_ai_dialogs.TestSessionConfigDialogTexts` 新增默认模型与自定义模型写回行为断言）

- 对无事件循环依赖的纯文案函数做直译断言（镜像 `test_i18n` 模式）。
- 有 UI 的对话框：`translation` 检查 `findChildren(QLabel/QPushButton)` 文本不再含中文（业务值字段除外），或断言其等于对应 `translate` 结果。

示例（`test_<模块>_i18n.py` 通用模板，函数名/断言按实际替换）：

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from catia_copilot import i18n


class TestMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_no_hardcoded_chinese_in_labels(self):
        # 导入目标对话框类并实例化，遍历 QLabel/QPushButton 断言
        #   业务值白名单（文件名/属性名/选项值）除外
        pass  # 由该文件具体断言替换
```

- [x] **Step 5: 运行**

```powershell
python -m unittest catia_copilot.tests.test_<模块>_i18n -v
```
Expected: 全绿。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add catia_copilot/ui/<目标文件>.py catia_copilot/tests/test_<模块>_i18n.py
git commit -m "feat(i18n): <模块> 对话框文案翻译"
```

---

### Phase 2 Consolidation（主 agent 独占）

- [ ] **合并 TS**：
```powershell
pyside6-lupdate -ts resources/i18n/catia_copilot_zh_CN.ts resources/i18n/catia_copilot_en_US.ts -no-obsolete main.py catia_copilot
```
- [ ] 英译新词条；`rg -n "unfinished" resources/i18n/catia_copilot_en_US.ts` → 无输出。
- [ ] `pyside6-lrelease resources/i18n/catia_copilot_en_US.ts`。
- [ ] 全测试套件 + `rg -n "TYPE_DISPLAY_NAMES|SOURCE_FROM_DISPLAY" catia_copilot` 确认无残留引用。

### Phase 2 Review Gate

- [ ] 哨兵常量不变（测试断言）；Source 反查零残留；英文 UI 下导出表头/Sheet 名仍中文（Task 2.4 回归测试）。
- [ ] 各窗口英文布局无截断抽查（手动）。
- [ ] 评审后进入 Phase 3。

---

## Phase 3 — PLM 事件解耦与翻译

### Task 3.1: `plm/sync.py` 结构化状态码

**Files:**
- Modify: `catia_copilot/plm/sync.py`
- Test: `catia_copilot/tests/test_sync_codes.py`

**Interfaces:**
- Produces（新增，**不破坏既有字段/函数**）：
  - `SyncEvent` 新增字段 `source_code/update_code/checkin_code: str | None`（取值如 `"created"|"updated"|"skipped"|"failed"`、`"written"|"uploaded"|"failed"` 等，**全 ASCII、语言无关**）。
  - `FILTERS` 的每项新增/保留稳定 `code` 字段（`"new"|"modified"|"unchanged"|"failed"`…），现有中文 `"新建"/"修改"/"跳过"/"失败"...` 字段作为**纯展示**，UI 不再依赖。
  - `SyncResult` 保持整数计数（created/updated/skipped/failed/unchanged）不变。

**步骤：**

- [ ] **Step 1: 盘点现状**

```powershell
rg -n "class SyncEvent|class SyncResult|FILTERS|SyncMsg|_makecb|def _sync|checkin|created|updated|skipped|failed|unchanged" catia_copilot/plm/sync.py
```

- [ ] **Step 2: 写失败测试（断言新 code 字段存在且纯 ASCII）**

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catia_copilot.plm import sync
from catia_copilot.plm.sync import SyncEvent


class TestSyncCodes(unittest.TestCase):
    def test_event_codes_ascii(self):
        e = SyncEvent(
            sync_msgid="x", source="", source_code="created",
            update="", update_code="written", checkin="", checkin_code=None,
            message="", speed_kbps=0, type="",
        )
        for c in (e.source_code, e.update_code):
            self.assertTrue(c.isascii(), "状态码必须语言无关 ASCII")

    def test_filters_have_stable_codes(self):
        codes = {f.code for f in sync.FILTERS}
        self.assertTrue(codes.issuperset({"new", "modified", "unchanged", "failed"}))

    def test_counts_are_ints(self):
        r = sync.SyncResult()
        self.assertIsInstance(r.created + r.updated + r.failed, int)


if __name__ == "__main__":
    unittest.main()
```

（`SyncEvent` 字段签名以现有为准，新增可选字段、保持向后兼容。）

- [ ] **Step 3: 实现**

- `SyncEvent` 增加 `source_code/update_code/checkin_code` 可选字段（默认 `None`）。
- 事件产生处（各 `_do_*`/上传/签入回调）同步填 code；`message` 中文字段保留做展示，但**新增的 code 为 UI 结构化来源**。
- `FILTERS` 每项加稳定 `code`；现有 `name`（中文）保留仅展示。

- [ ] **Step 4: 运行确认通过**

```powershell
python -m unittest catia_copilot.tests.test_sync_codes -v
```
Expected: 全绿。

- [ ] **Step 5: Commit（手动，待用户批准）**

```bash
git add catia_copilot/plm/sync.py catia_copilot/tests/test_sync_codes.py
git commit -m "feat(i18n): SyncEvent/FILTERS 增加语言无关状态码，为 PLM 翻译铺路"
```

---

### Task 3.2: `plm_workbench.py` 消费 `SyncEvent` 并翻译界面

**Files:**
- Modify: `catia_copilot/ui/plm_workbench.py`
- Test: `catia_copilot/tests/test_plm_workbench_i18n.py`

**Interfaces:**
- Consumes: `sync.SyncEvent(source_code/update_code/checkin_code)`、`sync.FILTERS[].code`、`i18n.translate("CATIACopilot", ...)`。
- Produces: 进度表/过滤器的状态来源改为事件 code（**不解析 `">>"`、`"[X]"`、`" | "`、表头文本**）；计数来自 `SyncResult` 整数。

**步骤：**

- [ ] **Step 1: 记录当前解析点**

```powershell
rg -n ">> |\[X\]| \| |>|<|签出来源|签入来源" catia_copilot/ui/plm_workbench.py catia_copilot/plm/sync.py
```
确认 ~L2804-2826 与 `_on_sync_progress` 的文本解析逻辑。

- [ ] **Step 2: 写失败测试（解析零依赖）**

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catia_copilot.plm import sync
from catia_copilot.ui.plm_workbench import PlmWorkbench


class TestPlmConsumesEvents(unittest.TestCase):
    def test_status_from_code_not_text(self):
        wb = PlmWorkbench(None)
        wb._handle_sync_event(SyncEvent(
            sync_msgid="", source="", source_code="created",
            update="", update_code=None, checkin="", checkin_code=None,
            message="", speed_kbps=0, type="sync",
        ))
        row = wb._table.rowCount(wb._table.topLevelItemCount() - 1) if ... # 按实际 API
        self.assertEqual(wb._status_of_last_row(), "新建")  # 断言：由 code 映射而来
        # 且不依赖任何包含 “>>”/“[X]” 的解析

    def test_counts_language_independent(self):
        r = sync.SyncResult(created=2, failed=1)
        # 由 UI 渲染方法消费：断言计数部分不因界面语言变化
```

（接口名按 plm_workbench 实际私有方法校准；目标：**状态由 `code` 映射为 `translate` 显示**。）

- [ ] **Step 3: 解耦**

- `_on_sync_progress`/进度表填充改为直接读 `SyncEvent` 的 `source_code/update_code/checkin_code` → 查 FILTERS code → `translate("CATIACopilot", "原中文字面量")`。
- 文本解析路径（`_makecb` / `SyncMsg(*lbl.split(...))`）**删除或在日志只读路径保留但不再喂给 UI**；中文表头（`签出来源` 等）在 UI 层不再被解析依赖后方可翻译。
- 保留 `SyncMeta`/日志兼容：SYNC 日志输出的中文字面量**本阶段不翻译**（开发日志）。

- [ ] **Step 4: 其余界面文案翻译**

工具按钮、过滤器标签、状态栏、列显头、消息框 → `translate`；列 key 与计数整数不动。

- [ ] **Step 5: 运行**

```powershell
python -m unittest catia_copilot.tests.test_plm_workbench_i18n -v
python -m unittest discover -s catia_copilot/tests -t . -v
```
Expected: 全绿。

- [ ] **Step 6: Commit（手动，待用户批准）**

```bash
git add catia_copilot/ui/plm_workbench.py catia_copilot/tests/test_plm_workbench_i18n.py
git commit -m "feat(i18n): PLM 工作台改为消费结构化事件码并翻译界面"
```

---

### Phase 3 Consolidation（主 agent 独占）

- [ ] `pyside6-lupdate -ts resources/i18n/catia_copilot_zh_CN.ts resources/i18n/catia_copilot_en_US.ts -no-obsolete main.py catia_copilot`
- [ ] 英译新词条；`rg -n "unfinished" resources/i18n/catia_copilot_en_US.ts` → 无输出；`pyside6-lrelease`。
- [ ] 全测试套件全绿。

### Phase 3 Review Gate

- [ ] `plm_workbench` 英文界面下进度/状态/计数正确且与中文一致（无 CATIA 用 mock 事件驱动测试验证）。
- [ ] SYNC 日志与分隔符未在本阶段被改字面量（`git diff resources/` 仅 i18n，`git diff catia_copilot/plm/` 仅 code 字段新增，无日志文本改动）。

---

## Phase 4 — 帮助 / AI / README

### Task 4.1: `help_dialog.py` 翻译

**Files:**
- Modify: `catia_copilot/ui/help_dialog.py`
- Test: `catia_copilot/tests/test_help_i18n.py`

**步骤：**

- [ ] **Step 1: 盘点**

```powershell
rg -n "[\u4e00-\u9fff]" catia_copilot/ui/help_dialog.py
```

- [ ] **Step 2: 翻译界面**

标题、正文、快捷键说明、目录翻译；**真实属性名（如 `PartNumber`、`物料编码` 的示例值）、ISO 标准名、`CATIA Copilot` 不译**。正文较长建议拆为 `translate("CATIACopilot", "段落")` 按句。

- [ ] **Step 3: 关于弹窗统一用 `build_about_text()`**（Task 2.1 产物）。

- [ ] **Step 4: 测试**

```python
def test_about_and_help_language_follow(self):
    # UI 语言 zh → 断言含中文引导；en → 断言英文（用真实 qm）
```

- [ ] **Step 5: 运行与提交（手动）**

---

### Task 4.2: `ai_chat_panel.py` 界面翻译

**Files:**
- Modify: `catia_copilot/ui/ai_chat_panel.py`
- Test: `catia_copilot/tests/test_ai_chat_i18n.py`

**步骤：**

- [ ] **Step 1: 盘点**

```powershell
rg -n "[\u4e00-\u9fff]" catia_copilot/ui/ai_chat_panel.py
```
标注：界面文案（译）/ 会话历史（不译）/ tool schema 名（不译）/ 用户输入原样（不译）。

- [ ] **Step 2: 翻译界面控件文案**

按钮、占位提示、状态标签 → `translate("CATIACopilot", ...)`；**不触碰 `ai/tools.py` 的 schema 名称与描述、聊天记录里用户原文**。

- [ ] **Step 3: 保持历史机制**（`history`/`messages` 文件里的原始中文不因切换语言重写；如需展示语言跟随，仅渲染层转换，回写仍存原始文本）。

- [ ] **Step 4: 测试**

```python
def test_schema_names_unchanged(self):
    from catia_copilot.ai import tools
    names = [t["function"]["name"] for t in tools.TOOLS]  # 以实际入口为准
    self.assertTrue(all(not any('\u4e00' <= c <= '\u9fff' for c in n) for n in names))
```

- [ ] **Step 5: 运行与提交（手动）**

---

### Task 4.3: README 中英文档

**Files:**
- Create: `README.zh-CN.md`
- Modify: `README.md`（改英文）
- Modify: `docs/README.md`（语言入口说明）

**步骤：**

- [ ] **Step 1: 迁移原中文内容**

`git mv README.md README.zh-CN.md`，保留全部现有中文章节。

- [ ] **Step 2: 写英文 `README.md`**

- 顶部双向链接：
  - `README.md` 起首：`[English](./README.md) | [简体中文](./README.zh-CN.md)`
  - `README.zh-CN.md` 起首：`[简体中文](./README.zh-CN.md) | [English](./README.md)`
- 章节与中文**一一对应**：简介、功能、安装、启动、打包（构建/发布）、依赖、配置、常见问题、联系方式；命令/路径/版本号 `2.2.0`（或当前 `constants.py` 值）与中文一致。

- [ ] **Step 3: `docs/README.md` 增加语言入口**

加入：文档使用说明 + 两个 README 链接。

- [ ] **Step 4: 一致性检查**

```powershell
rg -n "2\.2\.0|python 3\.10|PySide6" README.md README.zh-CN.md
```
对照两端版本号/依赖/命令一致。

- [ ] **Step 5: Commit（手动，待用户批准）**

```bash
git add README.md README.zh-CN.md docs/README.md
git commit -m "docs: README 英文化并新增中文版，双向链接"
```

---

### Phase 4 Consolidation（主 agent 独占）

- [ ] 合并 TS + 英译新词条 + `rg -n unfinished` 无输出 + `pyside6-lrelease`。
- [ ] 全测试套件全绿；`python main.py` 冒烟。

### Phase 4 Review Gate

- [ ] help / AI 面板英文界面正常；历史原样；schema 名未变；README 双语文档一致。

---

## Phase 5 — 构建流水线编译 qm 与校验

### Task 5.1: `tools/verify_translations.py`

**Files:**
- Create: `tools/verify_translations.py`

**Interfaces:**
  - CLI：`python tools/verify_translations.py resources/i18n/catia_copilot_en_US.ts`
  - 返回码：`0` 全部词条已译且占位符一致；`1` 存在 `type="unfinished"` 或占位符数量不一致。
  - 供 `build_nuitka_installer.ps1` 与 `release.yml` 调用。

**步骤：**

- [ ] **Step 1: 实现**

```python
"""校验 en_US.ts：无 unfinished 词条，且 source/translation 占位符数量一致。"""
import re
import sys
import xml.etree.ElementTree as ET


def main(argv):
    if len(argv) != 2:
        print("用法: python tools/verify_translations.py <xxx_en_US.ts>", file=sys.stderr)
        return 2
    root = ET.parse(argv[1]).getroot()
    errors = []
    total = 0
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            tr_el = msg.find("translation")
            if tr_el is None or tr_el.attrib.get("type") == "obsolete":
                continue
            total += 1
            tr_text = (tr_el.text or "").strip()
            if tr_el.attrib.get("type") == "unfinished" or not tr_text:
                errors.append((msg.findtext("source") or "", "unfinished"))
                continue
            src = msg.findtext("source") or ""
            if src.count("{") != tr_text.count("{") or src.count("}") != tr_text.count("}"):
                errors.append((src, "占位符数量不一致"))
    if errors:
        for src, why in errors[:50]:
            print(f"  [{why}] {src}")
        print(f"FAIL: {len(errors)}/{total} 个词条不合格", file=sys.stderr)
        return 1
    print(f"OK: {total} 个词条已翻译且占位符一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 2: 运行验证（含失败样例）**

```powershell
python tools/verify_translations.py resources/i18n/catia_copilot_en_US.ts
```
Expected: `OK: N 个词条…`（N>0）。

构造一个含 `unfinished` 的临时 ts 验证返回 1：
```powershell
# 复制一份并改一个 translation 为 type="unfinished"，再运行 → 期望报 FAIL 与返回码 1
```

- [ ] **Step 3: Commit（手动，待用户批准）**

```bash
git add tools/verify_translations.py
git commit -m "feat(i18n): 新增翻译完整性校验脚本（无 unfinished + 占位符一致）"
```

---

### Task 5.2: `build_nuitka_installer.ps1` 集成翻译构建

**Files:**
- Modify: `build_nuitka_installer.ps1`

**步骤：**

- [ ] **Step 1: 在 Nuitka 编译前加入编译与校验**

```powershell
# 在现有 build 步骤（如资源拷贝/Nuitka 调用）之前插入：
Write-Host "== 编译翻译 == "
pyside6-lrelease -verbose resources\i18n\catia_copilot_en_US.ts
if ($LASTEXITCODE -ne 0) { throw "lrelease 失败" }
python tools\verify_translations.py resources\i18n\catia_copilot_en_US.ts
if ($LASTEXITCODE -ne 0) { throw "翻译校验失败" }
```

- [ ] **Step 2: 确认资源包含**

- 现有脚本是否用 `--include-data-dir=resources=resources`：若没有但 `resources/` 已由其他方式处理，则**保持现状，只新增 lrelease + verify 步骤**；若存在则无需修改。用 `rg -n "resources|include-data|data dir" build_nuitka_installer.ps1` 确认，不臆断改动。

- [ ] **Step 3: 冒烟（可选、需本机 Nuitka）**

```powershell
powershell -ExecutionPolicy Bypass -File build_nuitka_installer.ps1  # 至少跑到 lrelease/verify 通过
```

- [ ] **Step 4: Commit（手动，待用户批准）**

---

### Task 5.3: `.github/workflows/release.yml` 增加测试与翻译步骤

**Files:**
- Modify: `.github/workflows/release.yml`

**步骤：**

- [ ] **Step 1: 定位构建 job 的资源准备段**

```powershell
rg -n "requirements|compileall|innosetup|build_nuitka|pip install" .github/workflows/release.yml
```

- [ ] **Step 2: 在 compileall 后、构建前加入**

```yaml
      - name: 编译并校验翻译
        run: |
          pyside6-lrelease -verbose resources/i18n/catia_copilot_en_US.ts
          python tools/verify_translations.py resources/i18n/catia_copilot_en_US.ts

      - name: 无 CATIA 单元测试
        shell: bash
        env:
          QT_QPA_PLATFORM: offscreen
        run: python -m unittest discover -s catia_copilot/tests -t . -v
```

（`pyside6` 可执行文件在 PySide6 依赖已安装后位于 Scripts，release.yml 已安装 requirements.txt 故可用。）

- [ ] **Step 3: 冒烟**

完整 workflow 依赖真实仓库推送，本机验证 limit 到 lrelease/verify/unittest 三条命令可直接执行即可。

- [ ] **Step 4: Commit（手动，待用户批准）**

---

### Task 5.4: `setup.iss` 中文/英文双语安装包

**Files:**
- Modify: `setup.iss`

**步骤：**

- [ ] **Step 1: 盘点现有语言与硬编码中文**

```powershell
rg -n "Language|Messages|Tasks|Run|Name: \"|Description:|AppName|AppVerName" setup.iss
```

- [ ] **Step 2: 增加语言节点**

```ini
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english";            MessagesFile: "compiler:Default.isl"
```

- [ ] **Step 3: 抽出硬编码中文为 CustomMessages**

把 Tasks/带 Description 的中文移到 `[CustomMessages]`（`chinesesimplified`/`english` 各组），`[Tasks]` 引用 `{cm:MyTaskName}`；`[Run]` 描述同上。安装程序标题 `AppVerName` 用自定义消息以随语言。

- [ ] **Step 4: 验证**

```powershell
# 用 Inno 命令行编译验证（本机已装或经 choco 装）
ISCC.exe setup.iss
```
Expected: 成功产出 `Output/CATIA-Copilot-Setup-2.2.0.exe`（或现有命名）。

- [ ] **Step 5: Commit（手动，待用户批准）**

---

### Phase 5 Review Gate

- [ ] 本机跑通 `pyside6-lrelease` + `verify_translations.py` + 三语 `unittest`；`ISCC.exe setup.iss` 编译成功且安装器含中英语言选择。
- [ ] Nuitka 出包后确认 `resources/i18n/catia_copilot_en_US.qm` 被包含且运行期可加载（实测加载日志无 warning）。

---

## Phase 6 — 无 CATIA 自动化测试与实机验收

### Task 6.1: 聚拢关键不变量测试套件

**Files:**
- Create: `catia_copilot/tests/test_acceptance_invariants.py`

**步骤：**

- [ ] **Step 1: 实现收敛测试**（聚合各任务已分散断言 + 未覆盖关键点）

```python
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication

from catia_copilot import i18n
import catia_copilot.constants as C


class TestAcceptanceInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_placeholder_translation_integrity(self):
        # 对所有含占位符词条：中文源与英文译文占位符数量一致（复用 verify 脚本逻辑于内存）
        import xml.etree.ElementTree as ET
        from pathlib import Path
        root = ET.parse(Path("resources/i18n/catia_copilot_en_US.ts")).getroot()
        for msg in root.iter("message"):
            tr = msg.find("translation")
            if tr is None or tr.get("type") in ("unfinished", "obsolete"):
                continue
            src, t = msg.findtext("source") or "", tr.text or ""
            self.assertEqual(src.count("{"), t.count("{"), f"占位符不一致: {src}")

    def _export_header_values(self):
        # 按实际导出工厂/模块取表头与 Sheet 名（bom_export / mass_props 导出段）
        raise NotImplementedError  # 由实现时依据源码填真实取值方式

    def test_export_never_follows_ui(self):
        # 英文界面下导出表头/Sheet 名仍为中文（首版导出恒中文）
        i18n.install_translators(self.app, ui_lang="en_US")
        for h in self._export_header_values():
            self.assertTrue(any("\u4e00" <= c <= "\u9fff" for c in h), f"导出表头必须为中文: {h}")

    def test_business_values_never_localized(self):
        self.assertEqual(C.FILENAME_NOT_FOUND, "未检索到")
        self.assertEqual(C.FILENAME_UNSAVED, "未保存")
        for k in C.PRESET_USER_REF_PROPERTIES:  # 若存在则该 API 保持
            self.assertNotIn("\u4e00" <= k <= "\u9fff", ())  # 仅示意：真实校验在下方
```

（按实际 API 将业务值断言写实；目标：**一组只要仓库不变就更不该动的测试**。）

- [ ] **Step 2: 全量回归**

```powershell
python -m unittest discover -s catia_copilot/tests -t . -v
python tools/verify_translations.py resources/i18n/catia_copilot_en_US.ts
```
Expected: 全绿。

- [ ] **Step 3: Commit（手动，待用户批准）**

---

### Task 6.2: 实机安装验收清单（人工执行）

- [ ] 清空用户 `QSettings`（或删除 `HKCU\Software\CATIACopilot`），启动 → 全中文。
- [ ] 系统区域为非中文 + `language=system` → 英文界面。
- [ ] 设置 `language=en_US` 重启 → 主窗口/嵌入菜单/BOM V3/质量特性/导出/PLM/AI/帮助全英文。
- [ ] 英文 UI 下导出 BOM/质量特性 Excel → 表头/Sheet 名**仍中文**（首版无导出语言切换；列 key/业务值一致）。
- [ ] Source 列英文显示，写入 CATIA 后读回仍为原 `'0'/'1'/'2'`；`未知` 等单元格显示正确回退。
- [ ] FILENAME 未保存/未检索到 单元格英文显示，但 BOM 写回与比对业务不受影响（保存后重开一致）。
- [ ] PLM 同步：进度/状态/计数在中文与英文界面下正确且一致（同一批文件）。
- [ ] 移除 `resources/i18n/catia_copilot_en_US.qm` → 启动回退中文 + 日志 warning，不崩溃；放回后恢复正常。
- [ ] 安装包（中英双语安装器）在本机完成安装、首次运行无误。
- [ ] 打包后（Nuitka 单目录）qm 能加载（无 warning）；快捷方式/宏菜单正常。

---

## 收尾（主 agent 独占，不做自动提交/发布）

- [ ] `pyside6-lupdate -ts resources/i18n/catia_copilot_zh_CN.ts resources/i18n/catia_copilot_en_US.ts -no-obsolete main.py catia_copilot`
- [ ] `rg -n "unfinished" resources/i18n/catia_copilot_en_US.ts` → 无输出；`pyside6-lrelease`。
- [ ] 全测试套件全绿；`python tools/verify_translations.py ...` 通过。
- [ ] 向用户汇报：创建文件清单、阶段摘要、发现的关键冲突（tests/ 父匹配、恒不加 text 反查、PLM 解析依赖、首版导出恒中文、哨兵不变量、`translate("ctx","字面量")` 提取约束等）。
- [ ] 不创建 release、不 push、不 commit（除用户明确批准的手动提交外）。