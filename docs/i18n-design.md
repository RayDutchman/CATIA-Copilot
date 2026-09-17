# CATIA Copilot 国际化（i18n）设计文档

> 状态：设计定稿，实施见 `docs/i18n-implementation-plan.md`
> 范围版本：APP_VERSION 2.2.0（Nuitka + Inno Setup 发行版）

## 1. 目标与非目标

### 目标

- 首版支持 **简体中文 / English / 跟随系统** 三种界面语言，切换后 **重启生效**。
- 全部运行时文案走 Qt 原生 `QTranslator` + `.ts/.qm`；**不使用 PyQt，不自建 JSON 翻译字典**。
- 中文作为**源文本**：未翻译、翻译缺失、qm 加载失败时自动回退中文并写日志。
- 首版**导出内容恒为中文**：BOM/质量特性导出表头与 Sheet 名不受界面语言影响；导出语言独立选择属后续版本，本期不实现。
- 当前发行版可达 UI 全覆盖：主窗口 + Win32 嵌入菜单、BOM V3、质量特性、导出/图纸/工具、DocDokuPLM、AI 界面、帮助。
- 构建流水线（Nuitka / Inno / GitHub Workflow）自动编译并校验 `.qm`。

### 非目标（首轮不翻译）

- `catia_copilot/_archive/` 旧版对话框（已无 UI 出口）。
- `plm_workbench_mypdm.py`（myPDM 工作台，当前发行版无 UI 入口）；仅保证随编译不破损。
- 开发/诊断日志文本（`logging` 输出）默认保持中文；日志不是交付文案，**翻译任何日志前必须先解耦其被 UI 文本解析的事实**（见 §4.6）。
- AI 会话历史、用户输入的原始提示词、工具 schema 名称、宏模块名、真实用户属性名、文件名、配置 key。

## 2. 总体架构

### 2.1 技术选型

| 项 | 决策 | 理由 |
|---|---|---|
| 翻译框架 | PySide6 `QCoreApplication.translate(context, source)` + `QTranslator` | 项目已用 PySide6≥6.5；无新依赖 |
| 源文本 | 中文（代码中的中文字面量即 source） | 现有代码即中文；缺翻译自动回退中文 |
| qm 编译 | `pyside6-lupdate` / `pyside6-lrelease` | 随 PySide6 安装附带的官方工具链；提取形式必须在实施阶段按计划 Task 1.6 用命令+断言验证 |
| 设置存储 | `QSettings("CATIACopilot", "Application")` | 与现有 EmbedPanel/MainWindow 设置同簇 |

### 2.2 语言与回退链

`QSettings` 键（首版仅有界面语言）：

- `language`（界面）：`"system" | "zh_CN" | "en_US"`，默认 `"system"`。导出语言选择不在首版范围。

界面实际语言解析 `resolve_ui_language()`：

1. `zh_CN` → 简体中文（**源语言，不安装翻译器**）。
2. `en_US` → 英文（安装 `en_US.qm`）。
3. `system` → `QLocale.system().name()`：
   - 语言族为 `zh` → 简体中文；
   - 其余系统区域 → **英文（en_US）**（不支持的区域统一回退英文）。

回落链：自定义 `en_US` → qm 缺失/加载失败 → **中文**，并 `logging.warning`。任何时刻缺词条 → `translate()` 返回源文本（中文）。

### 2.3 运行时组件

#### `catia_copilot/i18n.py`（新建，UI 文案统一入口）

- 常量：`APP_CTX = "CATIACopilot"`（UI 统一 context，部署时定义并作为约定）、三个语言常量。
- `translate(context, source, ...)`：透传 `QCoreApplication.translate`。**context 与 source 均须为字面量**；source 必须是中文字面量，使 `pyside6-lupdate` 能定位词条（提取形式在执行阶段验证，见 §4.2 与计划 Task 1.6）。
- `resolve_ui_language(setting)` / `read_language()` / `write_language()`。
- `install_translators(app, ui_lang=None) -> QTranslator | None`：**只安装界面语言的一个翻译器**，不接收导出语言参数、也不安装导出翻译器。`ui_lang` 缺省时按 `read_language()` 解析；`zh_CN` 不装；qm 缺失/加载失败 → 跳过 + warning（即回退中文）。
- `current_ui_language()`：返回启动时解析出的实际界面语言（`zh_CN`/`en_US`），供后续版本导出独立机制与界面逻辑查询。
- **翻译器生命周期 = 进程生命周期**：必须在 `QApplication` 之后创建成功并保持引用到 `app.exec()` 返回，否则被 GC 后翻译立即失效。

> 模块内可定义**源文本字面量常量**（集中的 label 常量表），但只要调用 `translate` 的地方使用变量而非字面量，lupdate 就提取不到——因此约定：**翻译调用点必须直接写中文字面量**；如需共享同一源字面量，使用固定 label 工厂函数（内部直接调用 `translate("CATIACopilot", "...")`，见 §4.2）而非跨函数传值。

#### 导出文案（首版不新增模块）

导出内容与界面语言**完全解耦**：不新增 `export_i18n` 模块，不安装导出翻译器，不新增导出语言设置。BOM/质量特性导出表头与 Sheet 名保持现有**中文字面量工厂值**（`catia/bom_export.py`、`mass_props_dialog.py` 导出段），确保英文界面下导出仍为中文且不随界面翻译器变化。以回归测试锁定（见 §4.8）。

### 2.4 翻译文件布局与生成工作流

```
resources/i18n/
  catia_copilot_zh_CN.ts   # 源文本即中文（词条全集基准，与代码保持同步）
  catia_copilot_en_US.ts   # source=中文，translation=英文
  catia_copilot_en_US.qm   # 构建期由 pyside6-lrelease 生成，不入 git（.gitignore *.qm）
```

- `.qm` 随 `resources/` 整目录由打包脚本携带（Nuitka `--include-data-dir=resources=resources`、PyInstaller datas 均已覆盖，且主程序图标/字体同机制运行期验证正常），**无需新增打包配置**。
- 运行时路径：`resource_path("resources/i18n/catia_copilot_en_US.qm")`，复用现有 `utils.resource_path()`。
- 更新流程（**主 agent 独占**，阶段内各子 agent 完成后统一合并，避免并行写冲突）：

```bash
# 注意：lupdate 没有 -exclude 选项，且实测目录模式（... catia_copilot）不带 -extensions py 时提取 0 词条。
# 因此生产提取必须「显式 -extensions py」并「显式枚举真实源码文件」，tests/ 只能靠不枚举来排除：
pyside6-lupdate -extensions py -no-obsolete main.py \
                catia_copilot/i18n.py \
                catia_copilot/ui/main_window.py \
                catia_copilot/ui/catia_embed.py \
                -ts resources/i18n/catia_copilot_zh_CN.ts \
                   resources/i18n/catia_copilot_en_US.ts
# en_US.ts 由主 agent/译者在 Linguist 或直接编辑中补英文，并通过
python tools/verify_translations.py resources/i18n/catia_copilot_en_US.ts
pyside6-lrelease resources/i18n/catia_copilot_en_US.ts
```

> 该提取形式（显式 `-extensions py` + 显式源文件枚举，排除 tests/）已有真实回归测试固化：
> `catia_copilot/tests/test_lupdate_extraction.py` 用临时文件调用真实 `pyside6-lupdate`，断言
> context 为 `CATIACopilot`、显式源词条被提取、未枚举的 tests 目录词条不被误提取。

已实机验证该工具链：lupdate 按 context 提取、lrelease 产出 qm、`QTranslator.load` + `installTranslator` 后 `QCoreApplication.translate` 返回英文、未收录词条返回源中文。**该语句为一次性探测记录**；执行阶段（计划 Task 1.6）必须再次用命令与断言验证，不得把本次有限试验推广为「lupdate 只支持某一种调用形式」的结论。

### 2.5 main.py 集成点

在 `QApplication` 创建成功之后、`MainWindow()` 实例化之前安装翻译器（`main.py` 现状：第 38 行 `app = QApplication(sys.argv)`，第 46 行 `window = MainWindow()`），顺序不可颠倒（`QCoreApplication.translate` 依赖已存在的 app 与已安装 translator）。

### 2.6 语言切换 UI 与存储

主窗口无菜单栏（`menuBar().hide()`），语言选择放在「≡」页（`_build_more_page`）新增「设置」小节：

- 界面语言 `QComboBox`：跟随系统 / 简体中文（中文）/ English（英文）——选项文案双语，保证未重启前任何语言下都可辨认。
- 切换即写 `QSettings('CATIACopilot','Application')`，弹出提示「重启后生效」（文案经 `translate`）。
- **首版不提供导出语言选择**（导出恒中文，见 §4.8）。

## 3. 翻译范围地图（当前发行版可达 UI）

| 模块 | 内容 | 归属阶段 |
|---|---|---|
| `ui/main_window.py` | `_ACTION_LABELS`、Tab 标题、≡页按钮/设置、状态栏连接状态、诊断对话框、宏菜单「未找到宏文件」 | 1 |
| `ui/catia_embed.py` | Win32 `AppendMenuW` 菜单文字（与主窗口共享运行时文案） | 1 |
| `ui/bom_edit_dialog_v3.py` | 列显头、Source 下拉、对话框标题/按钮/消息、提示标语、右键菜单 | 2 |
| `ui/mass_props_dialog.py` | 列显头、状态、单位换算标签、消息；导出表头保持中文字面量（不接入界面翻译） | 2 |
| `ui/export_bom_dialog.py` + `catia/bom_export.py` | 对话框文案；Excel 表头/Sheet 名保持中文字面量（首版导出恒中文） | 2 |
| `ui/convert_dialog.py` `find_deps_dialog.py` `bom_file_rename_dialog.py` `template_dialog.py` `model_state_dialog.py` `session_config_dialog.py` `log_window.py` | 各自对话框文案 | 2 |
| `plm/sync.py` + `ui/plm_workbench.py` | 先结构化事件/状态码（3a），再翻译界面（3b） | 3 |
| `ui/help_dialog.py` | 帮助正文（属性名等专有名词不译） | 4 |
| `ui/ai_chat_panel.py` | 界面控件文案；历史原样、工具 schema 不变 | 4 |
| `README.md` / `README.zh-CN.md` / `docs/README.md` | 文档中英 | 4 |
| `.github/workflows/release.yml` `build_nuitka_installer.ps1` `setup.iss` | 构建/安装包语言 | 5 |

不随语言改变的**业务常量**（保留中文原值，只做渲染翻译，见 §4.5/§4.10）：

- `FILENAME_NOT_FOUND="未检索到"`、`FILENAME_UNSAVED="未保存"`（`catia_copilot/constants.py`）
- `PRESET_USER_REF_PROPERTIES` 及选项值（物料编码、设计状态…真实用户属性名）
- `PLM_Version`/`PLM_Iteration`、`PRESET_USER_REF_PROPERTY_OPTIONS` 内「草稿/冻结/发布/废弃」等**属性可选项值**（写入 CATIA 的真实值，不译）
- `BomNodeType` 键、BOM 列内部键（`Part Number` 等）、`SOURCE_*` 的 '0'/'1'/'2'
- AI 工具 schema 名称、`CATIA_COPILOT_MODULES` 宏模块名、配置 key、文件名/目录名
- 主窗口标题 `CATIA Copilot`（产品名）

## 4. 关键决策与冲突化解

### 4.1 源文本即中文，动态文案不落翻译器

**调用形式约定**（与实施计划任务一致）：

- 统一使用明确字面量的调用：`translate("CATIACopilot", "中文")`（`from catia_copilot.i18n import translate`）或 `QCoreApplication.translate("CATIACopilot", "中文")`——**context 与 source 均须为字面量**。
- 不必强制"唯一形式"：执行阶段（计划 Task 1.6）用命令与断言验证所采用的调用形式可被 `pyside6-lupdate` 提取；未验证的自定义封装（如 `tr(f"...")` 单参便捷函数）不得作为规范写入。
- **禁止**：把变量、`f-string`、拼接表达式作为 `translate` 的 `source` 传入。
- **允许**：`translate("CATIACopilot", "同步完成：共 {0} 个节点，跳过 {1} 个").format(total, skipped)` —— 源字面量含 `{0}` 占位符，翻译保留相同数量占位符（`tools/verify_translations.py` 校验一致性）。
- **禁止**：把多个 `translate(...)` 结果 `+` 拼接成句子（各语言语序不同）；整句为一个 `translate(...)`。
- 模块内可定义源文本常量表，但**翻译调用点必须直接写中文字面量**（不得跨模块传变量），以保证 lupdate 可见。

### 4.2 固定 context 与提取规则

- UI 统一 context `"CATIACopilot"`；不再按类名逐类建 context，避免 .ts 碎片化与漏配。
- **提取契约**：`translate` 的 `source` 必须是**调用点源代码中的中文字面量**（或含 `{0}` 占位符的整句），context 必须是字符串字面量。模块中可以定义 source 字面量常量便于集中审阅，但调用点必须以字面量形式书写，**不得把常量/变量传给 `translate` 作 source**（lupdate 只提取调用点字面量；该行为以执行阶段命令+断言验证为准）。
- **固定 label 工厂**：同一模式的一组文案（如 `_ACTION_LABELS`、列显头工厂）用固定函数承载，函数体内**直接写 `translate("CATIACopilot", "...")` 字面量调用**，使 lupdate 可见并返回当前语言文案。
- **不把有限试验绝对化**：仅凭一次探测不得断言「lupdate 只支持某一种形式 / self.tr 不可用」；所采用的调用形式必须在执行阶段（计划 Task 1.6）用命令与断言验证后固定为规范。
- 动态组装：若某字符串必须动态组装，先重构为固定字面量 + 占位符。英文 UI 布局长度：英文译文加长（如「Term（Chinese name）」）不得导致控件截断——列宽/按钮需按内容自适应，验收清单含英文布局检查。

### 4.3 `constants.py` 显示映射与业务 key 解耦

现在 `constants.py` 在**导入期**即构建含中文的显示字典：
`TYPE_DISPLAY_NAMES`、`BOM_COLUMN_DISPLAY_NAMES`、`MASS_PROPS_COLUMN_DISPLAY_NAMES`、`SOURCE_TO_DISPLAY`/`SOURCE_FROM_DISPLAY`/`SOURCE_OPTIONS`、`ABOUT_TEXT`、`AI_TAB_LABEL`。

处理原则：

- **结构性常量保留**（列 key、`BOM_READONLY_COLUMNS`、`BOM_COLUMN_MIN_WIDTHS`、`PLM_MEMBER_TABLE_COLUMNS` 的三元组等）。
- **显示映射删除或改为函数**：消费方在渲染处调用 `translate("CATIACopilot", ...)`；杜绝"模块导入时翻译"（导入早于翻译器安装即取到中文，且永不刷新）。
  - `TYPE_DISPLAY_NAMES` → `type_display(key)` 辅助函数（函数体内直接 `translate("CATIACopilot", ...)`）。
  - `BOM_COLUMN_DISPLAY_NAMES` → 列显头函数 `bom_column_display(key)`（含「完整路径」特例逻辑，见 bom_edit_dialog_v3 `_header_labels`）。
  - `SOURCE_TO_DISPLAY/OPTIONS` → `src_label(store)`，连接 role/项构建用 `('0','1','2')` 常量列表（见 4.4）。
  - `SOURCE_FROM_DISPLAY` **删除**——国际化后文本反查不可靠，一律以 combo `itemData` 回写。
  - `ABOUT_TEXT` → `build_about_text()` 函数，内部 `translate("CATIACopilot", "...").format(APP_NAME, APP_VERSION, ...)`。
  - `AI_TAB_LABEL` → 主窗口 Tab 插入/更新处直接 `translate("CATIACopilot", "AI 助手")`。

### 4.4 Source 0/1/2 值不变，combo itemData 固定

- CATIA `Source` 存储值与业务键永远是 `'0'/'1'/'2'`——**值不变**。
- 下拉项：`addItem(display, itemData)`，`itemData` **固定** `'0'/'1'/'2'`；回写读取 `combo.currentData()`，**不做文本反查**。
- 删除 `bom_edit_dialog_v3.py` 第 1289 行内联 `{"未知":"0","自制":"1","外购":"2"}.get(text, text)`。
- 英文直译（用户已确认，非自称官方 UI 用词）：`0→Unknown`、`1→Made in-house`、`2→Purchased`。
- 三处触点：初始化渲染（~L973-976）、编辑提交（~L1289）、进度/回显（~L3421），统一走同一构建函数。

### 4.5 FILENAME_NOT_FOUND / UNSAVED 哨兵

- 哨兵常量**保留中文原值**，是业务不变量：被 `bom_collect`/`bom_collect_v3`/`mass_props_collect` 写入行数据、被 `bom_edit_dialog_v3`（~L1069）等做相等比对。
- 国际化**只影响渲染层**：单元格/树项文本按 `translate("CATIACopilot", "未检索到"/"未保存")` 显示；行数据（`_rows`）与比对逻辑仍用常量。
- 契约：任何把「显示文本」当作业务值进行相等/写入/比较的代码，必须先改读行数据。测试 `test_sentinel_invariance.py` 固化该不变量。

### 4.6 PLM：先结构化，后翻译（禁止直接替换日志）

现状事实（已核对源码）：

- `plm/sync.py`：`SyncEvent` 已存在（`type/source/update/checkin/message/speed_kbps`），但其 `source/update/checkin` 字段**直接装中文显示串**（"新建"/"属性已写入"/"✗ 更新失败"/"已签入"...）；又有文本路径 `_log_header/_log_row/_log_skip/_log_fail` 用「 |>| 」分隔与 `>>`/`[X]` 前缀格式化。
- `ui/plm_workbench.py` `_on_sync_progress` **通过解析文本**重建状态：匹配 `">>"`、`"[X]"`、`" | "`、表头 `"签出来源"`、`lbl.split("<")` 提取零件号（~L2798-2836）。

因此**第一序是解耦**：UI 改直接消费 `SyncEvent`（`sync.py` 的 `_makecb` 已支持结构化回调），文本路径仅作日志兼容；同时给 SyncEvent 增加**语言无关的状态码**字段。完成且验证解析不再依赖中文分隔符/表头后，才允许翻译界面词条。任何对 sync/plm 日志的"顺手翻译"都会破坏解析（进度条、状态列、计数），列为硬约束。

PLM 计数（created/updated/skipped/failed/unchanged）来自 `SyncResult` 整数，**天然语言无关**，翻译只作用于渲染，不作用于统计。

### 4.7 Win32 嵌入菜单共享运行时文案

- `catia_embed.py` 在弹出时以函数级懒加载读 `MainWindow._ACTION_LABELS`（~L916-929），`AppendMenuW` 逐项填文本。
- 方案：把 `_ACTION_LABELS` 类属性改为 `MainWindow.action_labels()` 静态方法（内部直接 `translate("CATIACopilot", ...)` 字面量调用），嵌入菜单弹出时取运行时文案；主窗口按钮文字、嵌入菜单、`_ACTION_LABELS` 三处从此**同一个函数**，消除硬编码漂移。`AppendMenuW` 本身不跟随 Qt 翻译事件，但语言切换重启生效，天然一致。
- 宏子菜单显示的 `CATIA_COPILOT_MODULES` 模块名与宏文件名是标识符，不译。

### 4.8 导出文案恒中文（首版不实现导出语言切换）

- **首版不新增导出语言机制**：不建 `export_i18n` 模块、不建导出翻译器、不新增 `export_language` 设置、不加导出语言下拉框——导出语言独立选择属后续版本。
- BOM 导出表头/Sheet 名（`catia/bom_export.py` + `export_bom_dialog.py` 预览与列选择）、质量特性导出表头（mass_props_dialog 导出段）**维持现有中文字面量工厂值**，不接入界面翻译器，保证英文界面下导出仍为中文且不随界面翻译器变化。
- **回归测试锁定**：英文界面（安装 `en_US` 翻译器）下执行导出表头/Sheet 名构造，断言输出仍为中文字面量（不因界面翻译器变化）。

### 4.9 AI 界面

- AI 面板**界面文案**（按钮/标签/提示）随界面语言翻译；`ai_chat_panel.py` 内大量中文控件文案在阶段 4 迁移。
- **AI 回复语言**可跟随当前会话语言设置（现有行为，不强制改动）；**用户提示词历史原样保存/回显**，不因 UI 语言改写。
- 工具 schema 的名称/描述（`ai/tools.py` 等）**保持不变**——它们是供给 LLM 的接口契约。

### 4.10 不翻译清单（硬约束）

COM 属性名（`PartNumber`/`Nomenclature`/`Revision`/`Definition`/`Source`/`Description`）、真实用户参考属性名与可选值（物料编码、设计状态、草稿/冻结/发布/废弃…）、`PLM_Version`/`PLM_Iteration`、文件/目录名、配置 key（`dlg_topmost`、`custom_columns`…）、`QSettings` 组织/应用名、宏模块名、AI 工具 schema、产品名 `CATIA Copilot`。列展示例外：BOM 列 key 本身是英文（如 `Part Number`），其显示名可译（如 `零件编号`→`Part Number`），但列内部键与写入 CATIA 的值恒不变。

### 4.11 日志策略

`logging` 输出（`utils.py`、`catia/*` 等开发日志）首轮保持中文不翻译；`log_window`/嵌入式日志面板是日志查看，非交付文案。PLM/同步日志**拆除解析依赖之前**禁止改字面量（§4.6）。

### 4.12 安装包与 README

- `setup.iss` 现仅 `chinesesimplified` 一种语言，Tasks/Run 描述硬编码中文。阶段 5：`[Languages]` 增加 `en`（`compiler:Languages\English.isl`），`[CustomMessages]` 提供中英任务文案，`[Run]/[Tasks]` 使用按语言分派的标准消息。
- 文档：`README.md` 改为**英文**（功能/安装/打包/依赖/联系方式全量），新建 `README.zh-CN.md` 承载现有中文内容；两者顶部互相链接；版本号/功能/安装命令保持**一致**并同源维护（版本号只改 `constants.py`）。`docs/README.md` 增加两语言入口说明。

## 5. 阶段划分与文件所有权

| 阶段 | 内容 | 主要执行者 | 独占权 |
|---|---|---|---|
| 1 | 基础设施 + 主窗口试点 | 主 agent（基础设施/入口）；catia_embed 可交子 agent | 主 agent 独占 `main.py`、`i18n.py` 初稿、TS 合并 |
| 2 | 业务 key 解耦 + 各窗口迁移 | 每窗口一个子 agent（独立文件边界） | 主 agent 独占 `constants.py` 解耦调用点收口、TS 合并 |
| 3 | PLM 事件解耦 + 翻译 | 子 agent（sync.py / plm_workbench.py 各自文件） | 主 agent 协调 sync→ui 接口契约、TS 合并 |
| 4 | 帮助 / AI / README | 子 agent 并行（help、ai_chat、README 互不依赖） | 主 agent 独占 TS 合并与 README 版本号一致性检查 |
| 5 | Nuitka / Inno / GitHub Workflow 编译 qm 与校验 | 主 agent | 主 agent 独占构建脚本与 workflow |
| 6 | 无 CATIA 自动测试 + 实机验收 | 主 agent 汇总 + 测试子 agent 补充用例 | 主 agent 独占最终断言/验收清单 |

**并行写冲突规则**：子 agent 各改各的文件；`resources/i18n/*.ts`、`constants.py`、`main.py`、构建脚本、workflow 仅主 agent 修改；每阶段结束 `pyside6-lupdate` 合并 TS、`verify_translations.py` 校验、阶段独立 review。**「import 成功 ≠ 功能可用」**：阶段 review 必须跑相应自动化断言与验收清单，不以 import 冒充分功能测试。

## 6. 风险登记表

| 风险 | 等级 | 缓解 |
|---|---|---|
| 哨兵/中文值被当作业务状态比对 | 高 | 渲染翻译 + 常量比对双层契约；测试固化 |
| PLM 文本解析依赖中文分隔符/表头 | 高 | 先结构化事件/状态码，后翻译；独立任务与测试 |
| 导出随英文 UI 变英文 | 高 | 导出文案不接入界面翻译器（保持中文字面量工厂值）+ 英文导出仍中文回归测试 |
| 翻译器 GC 后翻译失效 | 中 | main() 保持引用 + 实测断言 |
| qm 缺失/损坏导致界面回退 | 中 | 加载失败 warning + 源中文回退；构建期校验 |
| 翻译加载早于 app/翻译器安装 | 中 | 入口顺序固定，测试断言 |
| 英文长文案导致控件截断 | 低~中 | 布局自适应列宽 + 英文验收清单 |
| .ts 未跟上代码 | 低~中 | verify_translations 强制 unfinished=0 + 占位符一致 |
| 并行写 TS/constants 冲突 | 低 | 所有权矩阵 + 主 agent 独占 TS 合并 |

> 刻意不做之事：不臆断 `resource_path()` 存在 Qt 资源故障（该机制已由图标/字体在两种打包下验证）；不重写成熟业务模块（`bom_collect`、`bom_write`、`sync` 核心同步逻辑等），只做接口层/渲染层最小改动。

## 7. 验收标准

1. `python main.py`（无 qm、无设置）→ 全中文，日志记录翻译回退（如适用）。
2. `QSettings` 切 `en_US` 重启 → 主窗口/嵌入菜单/BOM V3/质量特性/导出对话框/PLM/AI/帮助全英文；切换 `zh_CN`/系统中文 → 中文。
3. 系统区域非中文（`system`）→ 英文界面。
4. 英文界面下 BOM / 质量特性导出 Excel 表头与 Sheet 名仍为中文（回归测试锁定；首版无导出语言切换）。
5. Source 下拉显示翻译但写回 `'0'/'1'/'2'` 不变；哨兵显示翻译而业务比对不破。
6. PLM 同步进度/状态/计数中文界面下正确、英文界面下同样正确且计数一致（语言无关）。
7. `pyside6-lrelease` 之后移除/损坏 qm → 回退中文 + warning 日志，不崩溃。
8. `python -m unittest discover -s catia_copilot/tests -t . -v` 全绿（无 CATIA），含提取形式回归用例。
9. 打包后（Nuitka + Inno，含英文语言的安装器）实机安装，逐窗口核对。