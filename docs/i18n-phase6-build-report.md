# i18n Phase 6 报告 — 构建流水线接入（2026-09-18）

## 任务目标

在 `feat/i18n` 分支上完成 i18n 与发布/构建链路的对接，使任何一次正式构建/发布
都能在产物中带上完整的翻译（应用目录 `en_US/zh_CN .qm` + Qt 标准控件
`qtbase_zh_CN.qm`），并让安装器提供中英双语向导。验证在 CI 中前置到构建之前。

## 改动范围

仅以下文件（另含两个文档）：

| 文件 | 状态 | 说明 |
|------|------|------|
| `build_nuitka.ps1` | 修改 | 预编译块 + 产物块（各 21 行） |
| `build_nuitka_installer.ps1` | 修改 | 同上 |
| `setup.iss` | 修改 | `[Languages]` 双语（4 行） |
| `.github/workflows/release.yml` | 修改 | 插入校验 + 单测两步（10 行） |
| `scripts/verify_translations.py` | 新建 | 翻译目录校验脚本 |
| `catia_copilot/tests/test_translation_catalog.py` | 新建 | 10 个用例 |
| `docs/i18n-progress.md` | 修改 | 追加 Phase 6 一节 |
| `docs/i18n-phase6-build-report.md` | 新建 | 本文档 |

约束：不改业务源码与 `catia_copilot/i18n.py`；不 commit / push / stash / reset；
不派 agent；未发 Release。

## 1. 校验脚本 `scripts/verify_translations.py`

- 内置 18 文件 `PRODUCTION_FILES` 生产清单（= Phase 5 实证过的 lupdate 显式清单，
  `main.py` + 17 个 `catia_copilot/**/*.py`，排除 tests /`_archive`/mypdm）。
- 定义两条规则（脚本内“规则真源”，两份 TS 与实际提取共用同一常量，单点修改）：
  - `TS_STRUCTURE_RULES` — 花括号配对、空条目、`unfinished`；
  - `TEXT_PLACEHOLDER_RULES` — 先剥 `{{...}}` 字面转义，再比对 `{N}` 数字占位符集合。
- 真实 `pyside6-lupdate` 提取 18 文件（`--no-obsolete --no-locations`），与两份 TS
  做双向比差：`TS − lupdate`（目录多余）、`lupdate − TS`（漏提取）均须为空。
- `--skip-lupdate` 开关跳过 lupdate 步骤（仅静态检查）。
- 实跑输出：`[en_US] 总共 1002 / 空 0 / unfinished 0 / 结构占位符问题 0`、
  `[zh_CN] 同`、`[lupdate] 1002 条 source，TS 缺少 0 / TS 多余 0`，**退出码 0**。

## 2. 构建脚本（两个 .ps1，相同两块）

预编译块（在 Nuitka 主编译之前）：

```powershell
# [i18n] prebuild...
python scripts\verify_translations.py
if ($LASTEXITCODE -ne 0) { Write-Error "..."; exit 1 }
& pyside6-lrelease ...\catia_copilot_en_US.ts -qm ...\catia_copilot_en_US.qm
if ($LASTEXITCODE -ne 0) { exit 1 }
& pyside6-lrelease ...\catia_copilot_zh_CN.ts -qm ...\catia_copilot_zh_CN.qm
if ($LASTEXITCODE -ne 0) { exit 1 }
$QtBaseZhQm = (& python -c "from PySide6.QtCore import QLibraryInfo; import pathlib; print(pathlib.Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath), 'qtbase_zh_CN.qm'))")
if (-not (Test-Path $QtBaseZhQm)) { Write-Host ...; exit 1 }
```

产物块（安装器版本在主编译后、Inno 打包前）：

```powershell
# [i18n] postbuild: drop qtbase_zh_CN.qm into the bundle ...
$QtTransTarget = @(
    (Join-Path $OutputDir '_internal\PySide6\translations'),
    (Join-Path $OutputDir 'PySide6\translations')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
```

- 兼容 Nuitka 新布局 `_internal\` 与旧根布局 `PySide6\translations`；
- 复制后复核目标存在性与源 `qtbase_zh_CN.qm` 自身存在性（源缺失在预编译块已拦）；
- 新块的日志消息为 ASCII 英文，规避 Windows PowerShell 5.1 对长多字节段落偶发解析
  抖动；文件原有中文消息未动。

## 3. 安装器双语 `setup.iss`

```ini
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
```

- `chinesesimplified` 保持首位 = 默认向导语言不变；
- 注释明确：安装向导语言与应用内语言（QSettings `CATIACopilot/Application` →
  `language`）独立，此处不做联动、不回写应用设置。

## 4. CI `release.yml`

在「Verify release version」与「Install Inno Setup」之间新增：

```yaml
- name: Verify translations
  shell: pwsh
  run: python scripts/verify_translations.py

- name: Run unit tests (offscreen)
  shell: pwsh
  env:
    QT_QPA_PLATFORM: offscreen
  run: python -m unittest discover -s catia_copilot/tests -p "test_*.py"
```

顺序决策：翻译校验（快）+ 全量单测先行，任一失败即中止，不进入 Inno Setup。

## 5. 新测试 `test_translation_catalog.py`（10 用例）

`catia_copilot/tests/test_translation_catalog.py`，`importlib` 装载
`scripts/verify_translations.py`（不经 PYTHONPATH 依赖）：

- `TestCatalogCoverage`（3）：TS−lupdate、lupdate−TS 双向为空；18 文件全部可读；
- `TestCatalogIntegrity`（3）：每语言 1002 条、空 0、unfinished 0；每语言结构与
  占位符集合一致；
- `TestCatalogRuntime`（2）：`_build_qm` 用 `pyside6-lrelease` 真建 `.qm`（临时目录），
  经 `QTranslator` 加载 `CATIACopilot` context，`零件编号→Part number`、
  `确定→OK`（中英文加载双方断言）；
- `TestCatalogWellFormed`（1）：XML/DTD 可解析，无 `<source>` 为空的条目；
- `TestProductionManifest`（1）：`PRODUCTION_FILES` 全部存在，`pubsub`/`antlr4` 等
  仅少数依赖注入到 `sys.modules`，测试不依赖 GUI。

## 6. 最终验证记录（Windows 原生，PowerShell 5.1）

| 检查 | 命令 | 结果 |
|------|------|------|
| PS5.1 语法 | `Parser.ParseFile` 两个 ps1（多次跑） | 各 0 errors |
| Python 编译 | `python -m py_compile <verify> <test>` | EXIT 0 |
| 目录校验 | `python scripts/verify_translations.py` | EXIT 0（1002/0/0/0） |
| 全量单测 | `unittest discover -s catia_copilot/tests -p "test_*.py"`（QT_QPA_PLATFORM=offscreen） | 223 passed，EXIT 0 |
| lrelease | en_US / zh_CN | 各 1002 finished / 0 unfinished，EXIT 0 |
| ISCC 预编译 | `iscc /Q /O- /DAppVersion=2.2.1 /DSourceDir=<真实dist> setup.iss` | EXIT 0 |

编码：本次涉及全部文件最终为 UTF-8 无 BOM（与 HEAD 一致，`.gitattributes`
`text eol=crlf`）。注：`/DAppVersion` 必须为合法 version（如 `2.2.1`），带 `-test`
后缀会使既有 `VersionInfoVersion={#AppVersion}.0` 报错，与本次改动无关。

## 已知限制 / 后续

- 本地无 `pwsh`，无法复现 CI 的 PowerShell 7 行为；CI 步骤兼容性判定基于语法与
  pwsh 对 UTF-8 无 BOM 的标准支持。
- 产物块为“尽力打包”：若某次 Nuitka 布局不含 `PySide6\translations`，脚本会失败
  中止而非静默漏包（当前布局实产物存在 `_internal\PySide6\translations`）。
- 端到端人工验收（安装器中英向导、安装后应用双语、CATIA/PLM 真实操作）仍未执行，
  建议在正式 Release 前完成。