# i18n Phase 4 — 帮助文档与 README 双语化交接报告

- 日期：2026-09-18
- 分支：`feat/i18n`
- 范围：`catia_copilot/ui/help_dialog.py`（帮助对话框按语言资源化 + 固定字面量
  translate）、README.md（英文）与 README.zh-CN.md（中文）双重校验；含测试与
  交接表文档。**不涉及 AI / TS / 构建脚本**；未 commit / push / stash / reset。

## 目标与方式

1. **帮助文档按语言走资源**（i18n-design 4.4 既定方向）：`_HELP_HTML`（中文源文档）
   原样保留；新增 `_HELP_HTML_EN`（英文全量译版，同在 `help_dialog.py` 内，不新增
   资源文件）。`_help_html()` 按 `current_ui_language()` 选择：`en_US` → 英文版，
   其余一律回退中文源文档。
2. **固定字面量走 translate**：窗口标题
   `translate("CATIACopilot", "{0} — 帮助文档").format(APP_NAME)`、关闭按钮
   `translate("CATIACopilot", "关闭")`，均为字符串字面量、context 恒为 CATIACopilot，
   未把 translate 塞入 HTML f-string。
3. **不翻译真实标识**：`analyze.*` API、`ReferenceProduct.Parent.Product`、
   `Position.GetComponents()`、`CATIA.Application` / `CNEXT.exe` / `3DEXPERIENCE` /
   `ProgID` / `CLSID` / `ROT`、参数名 `惯量包络体.N` / `质量` / `密度` / `Gx` /
   `IoxG…IyzG`、自定义属性名（物料编码…备注）、文件与路径名
   （`ChangFangSong.ttf` / `ISO.xml` / `drawing_templates` / `macros` / `.mpd` /
   `.catvbs` / `.catscript` / `.catvba`）在英文版中一律原样保留。
4. **保持帮助结构**：CN/EN 两份 HTML 经断言完全一致——`<h2>`×1、`<h3>`×5、
   `<h4>`×15、`<table`×15、`<ul>`×16、`<ol>`×2、`<img>`×1；
   `<img src="inertia_keep_params.png" style="max-width: 480px">` 路径与样式一致。
5. **README 双语**：README.md 为英文（运行/安装/发行/联系说明与中文一致），
   README.zh-CN.md 承载中文内容；两份首行均有双向语言链接
   `[English](README.md)` / `[简体中文](README.zh-CN.md)`；版本 2.2.0、
   发布日期 2026-07-01、作者、clone 地址与全部命令（venv / pip / main.py /
   build_nuitka.ps1 / build_nuitka_installer.ps1 / 输出目录）一致。
   更新两份 README 中 `help_dialog.py` 属性名硬编码行号引用为 76–77、590–591。

## 文件清单

- 修改：`catia_copilot/ui/help_dialog.py`（导入 i18n；新增 `_HELP_HTML_EN` 与
  `_help_html()`；标题/关闭按钮走 translate）。
- 修改：`README.md`、`README.zh-CN.md`（help_dialog 属性名行号引用 52–53/76–77 →
  76–77/590–591，与改动后文件一致）。
- 新增：`docs/i18n-phase4-help-translations.json`——1 条新词条：
  `"{0} — 帮助文档"` → `"{0} - Help Documentation"`；`"关闭" → "Close"` 已收录于
  阶段 2/3 交接表，不重复交接。
- 新增测试：`catia_copilot/tests/test_help_i18n.py`（13 用例）、
  `catia_copilot/tests/test_readme_i18n.py`（10 用例）。

## 测试覆盖（全部真实断言）

### test_help_i18n.py —— 13 用例

- AST 防线：translate context/source 均为字面量、context 恒为 CATIACopilot；
  `"{0} — 帮助文档"` 与 `关闭` 必在。
- 资源选择：`current_ui_language()`=zh_CN/fr_FR → 中文源文档，=en_US → 英文版。
- 结构一致：h2/h3/h4/table/ul/ol/img 计数 CN==EN；图片路径与 style 原样。
- 不翻译校验：`analyze.*` / 参数名 / 文件名等 26 个真实标识均在英文版中；
  去掉标签后英文版中文 token 仅限白名单（惯量包络体/质量/密度/物料编码/物料名称/
  规格型号/物料来源/数据状态/存货类别/重量/备注）。
- 真实 lupdate：pyside6-lupdate 从 help_dialog.py 提取到交接表全部词条。
- 真实 lrelease：构建临时 .ts → .qm，QTranslator 加载后
  `translate("CATIACopilot", "{0} — 帮助文档").format("CATIA Copilot")`
  == `"CATIA Copilot - Help Documentation"`，`关闭` == `"Close"`。
- 行为：offscreen 构造 HelpDialog——中文回退标题 `CATIA Copilot — 帮助文档`、
  按钮 `关闭`、正文含 `运行环境要求`；英文 patch 下标题
  `CATIA Copilot - Help Documentation`、按钮 `Close`、正文含 Overview/FAQ。

### test_readme_i18n.py —— 10 用例

- README.md / README.zh-CN.md 均存在；两份首行双向语言链接。
- 版本/日期/作者/命令（clone、venv、activate、pip、main.py、Nuitka 脚本、
  输出目录）双语完全一致；PRESET 段与联系方式在。
- README.md 中文 token 仅 `简体中文` 与真实属性名 `材料`；zh 版以中文为主。
- `help_dialog.py` 属性名行号引用：双语一致，(76–77, 590–591) 指向的行确实含
  `物料编码` / `物料名称`；该行描述"硬编码属性名"。

## 验证记录（Windows 原生，offscreen）

- `python -m unittest catia_copilot.tests.test_help_i18n catia_copilot.tests.test_readme_i18n`：
  23 tests OK，退出码 0。
- `python -m unittest discover -s catia_copilot/tests -p "test_*.py"`：**179 tests OK**，
  退出码 0（阶段 3 末 156 + 本次 23）。
- `python -m pytest catia_copilot/tests/ -q -p no:cacheprovider`：**179 passed**，
  退出码 0。

## 边界与未完成项

- 帮助窗口自身无搜索框/目录导航控件（现状仅 QTextBrowser + 关闭按钮），
  "英文至少覆盖窗口标题、关闭与正文目录结构"已按现状落地；全文中文/英文正文
  均以资源整段切换，未做逐句翻译。
- `docs/README.md` 中英版不在本次「只改」清单内，未处理。
- 阶段 5（TS/QM 合并构建、安装器双语）与阶段 4 AI 部分尚未开始。