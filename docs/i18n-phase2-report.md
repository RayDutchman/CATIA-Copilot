# i18n Phase 2 实施报告（含独立审查修复）

本报告记录 `docs/i18n-implementation-plan.md` Phase 2 的落地情况：10 个生产文件（6 个专属运行文件 +
`constants.py` + `find_deps_dialog.py` + `mass_props_dialog.py` + `main_window.py` 授权点）全部改为运行时
`translate("CATIACopilot", …)`，导出路径保持恒中文，并完成独立审查提出的 4 项修复。英文译文 JSON 覆盖
全量 657 词条，与真实 `pyside6-lupdate` 提取结果双向一致。

## 一、已实施内容

| 文件 | 状态 | 改动要点 | 缺失词条（本次新增英文） |
|------|------|----------|--------------------------|
| `catia_copilot/constants.py` | 修改 | 新增 `build_about_text()` 运行时工厂：逐行 `QCoreApplication.translate` 字面量、features 列表附加 `  • ` 前缀、`divider = "─" * 41`、`"开发者    {0}".format(APP_AUTHOR)` 等；保留旧 `ABOUT_TEXT` 供兼容 | 53 |
| `catia_copilot/ui/main_window.py` | 修改（仅授权点） | import 增加 `build_about_text`；L1046 `QMessageBox.about(…, build_about_text())`（其余行未动） | 196 |
| `catia_copilot/ui/bom_edit_dialog_v3.py` | 不修改源码 | 仅将 160 条缺失词条的英文译文补入 phase2 JSON | 160 |
| `catia_copilot/ui/mass_props_dialog.py` | 修改 | 全量 UI 文案运行时翻译（窗口标题、数据来源区、滤波/placeholder、测量状态 tooltip 4 类、排除/临时行、加载/追加/计算/导出/保存/刷新全部消息与进度）；`_MIRROR_TOOLTIP` 由模块级常量改为 `_mirror_tooltip()` 运行时函数（审查项）；新增模块级 `_EXPORT_COLUMN_CHINESE` 与类方法 `_export_column_header()`（恒中文表头 + 单位后缀），Excel/CSV 导出表头改用该函数 | 156 |
| `catia_copilot/ui/find_deps_dialog.py` | 修改 | 窗口标题、目标文件选择、结果区按钮、正向/反向/启发式分组框与提示、搜索结果节标题/汇总/错误、打开/复制/右键菜单；`_HEURISTIC_LABELS`/`_HEURISTIC_HINTS` 模块级 dict 改为 `_heuristic_label()`/`_heuristic_hint()` 运行时函数（审查项，未知键回退） | 73 |
| `catia_copilot/ui/export_bom_dialog.py` | 修改 | 窗口标题、数据来源组/单选、输出文件夹、BOM 类型与汇总选项、排序列、输出格式、导出列；`_make_col_item` 文案走 `bom_column_display` 工厂 | 46 |
| `catia_copilot/ui/convert_dialog.py` | 修改 | 使用活动文档、文件列表按钮、输出文件夹/前缀后缀、更新图纸后再输出、确认/取消、进度与完成消息 | 31 |
| `catia_copilot/ui/bom_file_rename_dialog.py` | 修改 | 另存为窗口、表单标签、浏览/确认/取消按钮、非法字符/路径类消息 | 17 |
| `catia_copilot/ui/catia_embed.py` | 只需 lupdate 枚举 | 17 条缺失词条译文已入 JSON | 17 |
| `catia_copilot/ui/template_dialog.py` | 修改 | 模板窗口标题、内容、确定/取消按钮 | 4 |
| `catia_copilot/i18n.py` | 不修改 | 无缺失词条 | 0 |
| `catia_copilot/tests/test_mass_props_i18n.py` | 修改 | 替换「无 translator 假测试」为 `TestExportKeepsChineseUnderEnTranslator`：用真实 `pyside6-lrelease` 编译临时 en_US qm + 安装 QTranslator，断言 CSV 导出仍恒中文、Excel 表头为恒中文 + 单位后缀 | — |
| `catia_copilot/tests/test_i18n_workbenches.py` | 修改 | 断言改用 `_heuristic_hint`/`_heuristic_label`，补充未知键回退用例 | — |
| `catia_copilot/tests/test_lupdate_extraction.py` | 修改 | `_PROBE_SOURCES` 扩为 phase2 全量 11 文件；`test_extraction_matches_handoff_json` 比对源改为 `docs/i18n-phase2-translations.json` | — |
| `docs/i18n-phase2-translations.json` | 新建/更新 | phase1 200 键 + phase2 458 条英文译文，共 657 键 | 458 |
| `docs/en_phase2_extra.json` | 新建 | 458 条中文→英文译文源文件（value 统一用弯引号，杜绝 ASCII 引号转义问题） | 458 |

## 二、恒中文保留（导出/数据路径，不做运行时翻译）

- `mass_props_dialog.py`：`_export_column_header()` 表头在 en_US 环境下仍为中文 + 当前单位后缀
  （如 `密度 (kg/m³)`、`重量 (g)`、`Ixx ({inertia_unit})`、`重心 X (mm)`）；Excel/CSV 导出内容保持
  「不统一」（密度单位漂移行）、「总计 (根产品)」汇总行、镜像行后缀 `(对称件)` / `(虚拟)`。
- `logger.*` 调试 f-string 全部保留（非 UI）。
- `find_deps_dialog.py`：须为 2A/2B 的关键字（`pn_param_*` 等）、行内 `⚠` 前缀保留。

## 三、实现说明与决策

1. **调用点保留中文字面量**：所有 UI 字符串以 `translate("CATIACopilot", "中文")` 包裹，
   动态内容一律 `.format(...)` 占位，不做 `translate` 结果拼接。
2. **运行时函数替代模块级 translate**（审查项①③）：`_HEURISTIC_LABELS`/`_HEURISTIC_HINTS` 模块级 dict
   与 `_MIRROR_TOOLTIP` 模块级常量在 import 时即固化语言（模块级作用域捕获当前 translator），改为
   `_heuristic_label(key)` / `_heuristic_hint(key)` / `_mirror_tooltip()` 运行时函数，每个 key 在函数内
   独立 translate 字面量，lupdate 仍按字面量提取。
3. **导出表头恒中文**（审查项②）：新增 `_EXPORT_COLUMN_CHINESE`（Level→层级、Type→类型、Filename→文件名、
   Part Number→零件编号、Instance Name→实例名、Nomenclature→术语（中文名称）、Revision→版本、
   Quantity→数量、Status→状态）与 `_export_column_header()`；Density/Weight/_INERTIA/CogX~Z 追加单位后缀，
   Excel（`_do_export`）与 CSV（`_do_export_csv`）表头统一走该函数。Excel 列宽估算仍使用 UI 列名（仅影响
   列宽，不改变导出内容），特意保留最小 diff。
4. **About 对话框**（审查项④）：`build_about_text()` 工厂在调用时构造文本，而非模块导入时固化；
   `ABOUT_TEXT` 保留；`main_window.py` 仅 L1046 一处调用改工厂。
5. **译文 JSON 生成管线**：AST 提取 11 文件全部 `translate("CATIACopilot", …)` 中文字面量
   union=657；phase1 覆盖 199 词条；missing=458。`en_phase2_extra.json` 逐条校验：
   占位符 `{0}`/`{1}` 一致、value 无 ASCII 双引号、无重复键、无多余键；合并 phase1 后输出 657 键。

## 四、词条覆盖与 lupdate 对账

```
AST 提取全量 union : 657
phase1 已覆盖        : 199
phase2 新增英文      : 458（constants 53 / v3 160 / rename 17 / convert 31 / export_bom 46 /
                       find_deps 73 / mass_props 156 / template 4 / main_window 196 /
                       catia_embed 17，去重后 458）
v3 独有词条         : 120（审查口径 122，按实际统计取 120）
phase2 JSON 键数     : 657

真实 lupdate：pyside6-lupdate -extensions py -no-obsolete <11 文件> -ts out.ts
  → 提取 657 条 == phase2 JSON 657 键，0 缺失 / 0 多余（test_lupdate_extraction 断言此双向一致）
```

## 五、验证结果

### 单元测试

统一执行 `python -X utf8 -m pytest catia_copilot\tests -p no:cacheprovider`，收集 **72 用例**：

| 测试文件 | 用例数 | 结果 |
|----------|--------|------|
| test_action_labels.py | 3 | 通过 |
| test_bom_edit_dialog_i18n.py | 14 | 通过 |
| test_constants_i18n.py | 12 | 通过 |
| test_export_chinese.py | 5 | 通过 |
| test_i18n.py | 11 | 通过 |
| test_i18n_workbenches.py | 8 | 通过 |
| test_language_ui.py | 3 | 通过 |
| test_lupdate_extraction.py | 3 | 通过 |
| test_mass_props_i18n.py | 9 | 通过 |
| test_smoke.py | 4 | 通过 |
| **合计** | **72** | **全部断言通过** |

> 说明：
> - 根目录 `tests/` 下 `embed_test.py`、`test_drawing_com_operations.py` 为历史乱码损坏文件（SyntaxError），
>   收集时排除；统一指定 `catia_copilot\tests`。
> - `test_bom_edit_dialog_i18n.py`（14 用例，被测源本次未改动）在进程**退出**阶段出现
>   `STATUS_HEAP_CORRUPTION (0xC0000374)`，所有断言通过；其余 9 个测试文件均 `EXIT=0`。属既有
>   Qt 无 GUI 环境析构问题，非本次改动引入。

### 编译检查

```
python -X utf8 -m py_compile catia_copilot/constants.py catia_copilot/ui/main_window.py \
  catia_copilot/ui/mass_props_dialog.py catia_copilot/ui/find_deps_dialog.py  → 通过
```

### 真实 en_US 导出回归（test_mass_props_i18n）

`TestExportKeepsChineseUnderEnTranslator` 用真实 `pyside6-lrelease` 编译临时 en_US qm 并安装
QTranslator（首断言 translator 生效，`零件编号`→`Part No.`）：CSV 导出保持中文（无 Part No./不统一/
总计 (根产品)），Excel 表头 = `["零件编号","类型","密度 (kg/m³)","重量 (g)","状态"]`。

## 六、待办

1. 将 `docs/i18n-phase2-translations.json`（657 词条）作为 lrelease 的 TS 翻译源，完成翻译合并与
   `en_US.qm` 编译接入现有 TS 建置流程；多行 `\n` 与占位符需与 source 保持逐字一致。
2. `en_US` 界面在 CATIA 真实环境完整走查（人工）。
3. 清理/归档中间产物：`docs/en_phase2_extra.json`（译文源）、`docs/_phase2_missing.json`（缺失清单）。