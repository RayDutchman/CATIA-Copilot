# i18n 最终独立评审报告（feat/i18n）

- 评审日期：2026-09-18
- 评审基准：分支 `feat/i18n`（基线 `97a859d`，checkpoint `5dd56ab`，含未提交工作区改动，以当前工作区为准——即构建实际使用的内容）
- 评审方式：只读。未执行任何 git 写操作；唯一产物为本文件。
- 依据文档：`docs/i18n-design.md`（规范）、`docs/i18n-progress.md`、`docs/i18n-phase1..6-*report.md`
- 验证手段：AST 全量扫中文常量分类、调用可达性分析、真实 `pyside6-lupdate`/`pyside6-lrelease` 交叉验证、PySide6 运行期文本断言测试（unittest 全量 223 通过）。

## 总体结论

Phase 1–6 的目标已基本达成：核心架构（translate 字面量封装、1002 条来源全译、Qt 翻译生命周期与回退、导出恒中文、PLM 事件化业务、构建管线接入）均为正确实现，并有测试背书。

但存在 **2 处「可达 UI 漏迁移」**（PullDialog 全选/全不选按钮、同步历史清空确认框）与 **1 类「可达 UI 漏迁移」**（质量特性文件对话框过滤器），是 spec §4.1/§4.4 意义上的真遗漏；另有若干非阻塞观察项。建议修复上述问题后再进入正式发布。详见下表。

---

## 一、阻塞 / 必改项（spec 判定：可达 UI 未迁移）

| # | 位置 | 问题 | spec 判定 | 建议 |
|---|------|------|-----------|------|
| B1 | `catia_copilot/ui/plm_workbench.py:4043-4044`（`_PullDialog._build_ui`） | `全选` / `全不选` 两按钮为裸中文字符串，未走 `translate`。Pull 对话框是可达主流程 UI，英文界面下按钮仍显示中文。 | 违反 §4.1「界面文案全部 translate」；lupdate 覆盖校验无法发现（未包裹）。 | 包一层 `translate("CATIACopilot", "全选")` / `translate(... "全不选")`（词条已存在于 TS，勿新增词表） |
| B2 | `catia_copilot/ui/plm_workbench.py:3299-3303`（`PlmWorkbench._on_clear_history`） | 清空历史确认框的标题与正文为裸中文（`清空历史` / `确定清空所有同步历史记录？此操作不可撤销。`）。本方法虽被 phase3 报告列为“死代码”，但经 **lẫnve 的 `_HistoryDialog._on_clear`（同文件 3738 行）间接调用**，实际可达。 | 违反 §4.1；phase3 报告对可达性判断错误，未被 verify 捕获。 | 标题/正文走 translate（词条需新收录并 `lupdate` 合并） |

## 二、强烈建议项（spec 判定：可达 UI 未迁移，影响轻-中）

| # | 位置 | 问题 | spec 判定 | 建议 |
|---|------|------|-----------|------|
| R1 | `catia_copilot/ui/mass_props_dialog.py:1236 / 1256 / 1283 / 2012` | 保存/载入/追加/导出四个 `QFileDialog` 的过滤器字符串为裸中文（`质量特性数据文件 (*.mpd)`、`Excel 文件 (*.xlsx);;CSV 文件 (*.csv)`）。对话框在英文界面下显示中文过滤器，且过滤提示不可读。注：此为“界面控件文案”而非“导出内容”，不受恒中文规则豁免。 | 违反 §4.1（file-dialog 过滤器属于 UI 文案） | 走 translate；过滤器为“显示字符串”，导出内容恒中文不受影响 |

## 三、非阻塞 / 观察项（已知取舍或低风险，建议列入后续）

| # | 位置 | 说明 | 评级 |
|---|------|------|------|
| O1 | `catia_copilot/ui/plm_workbench.py:408`、`catia_copilot/plm/sync.py:1747-1748` | Worker 构建的错误字符串（`：BOM 提取失败…`、`签入失败(code)…`）为中文，经 `_on_sync_done` 弹窗与 `_HistoryDialog` 详情原样显示于英文界面。错误文本属业务输出而非 UI 标签，但却是“面向用户可见”。 | LOW（建议后续将错误文本改用 code 映射渲染） |
| O2 | `catia_copilot/ui/plm_workbench.py:2053`（`_last_sync_mode = "Push 选中"`） | 作为持久化业务值写入历史，`_HistoryDialog` 详情「模式：」行在英文界面显示中文原值。phase3 报告已标明此为“业务值保持中文”的有意决策。 | LOW（已记录取舍，保持） |
| O3 | `catia_copilot/plm/sync.py:1762,1790` | `checkin_code` 由 `col3 == "已签入"` 文本比较推导（1790 的迭代写回分支同样依赖）。当前引擎自吐自判，功能自洽；但若未来将 `已签入` 字面量改译，签入判定将静默失效。建议以布尔量（`ticket.update_ok` 同级）替代文本比较。 | LOW（脆耦合，非当前 bug） |
| O4 | `catia_copilot/ui/plm_workbench.py:2628 / 2567`（`_build_settings_tab` / `_build_history_panel`）及 2399-2421、2587、2644-2783 等 | 大量含裸中文 UI 的死代码。当前无任何调用点，不可达，不影响运行；但 `verify_translations.py` 无法预防其被后续启用（未包裹字符串不在 lupdate 覆盖范围）。 | LOW（技术债；建议后续删除或补 AST 守卫） |
| O5 | `catia_copilot/ui/session_config_dialog.py:272` | `text.startswith("使用全局默认")` 在英文界面下失效。实际路径已由 `currentData()` 恒等（`""`）兜底，startswith 仅是模型项 `itemData` 缺失时的兜底分支，触发概率极低。 | LOW（已确认非阻塞） |
| O6 | `catia_copilot/ui/mass_props_dialog.py:2449-2451`（`(对称件)` / `(虚拟)`）、2085（`不统一`）、2159/2244（`总计 (根产品)`） | 属数据值/导出内容：导出恒中文合规，但英文界面表格单元格内会显示中文后缀（镜像行零件号）。非 UI 控件文案，属数据层展示。 | INFO（如需英文界面完整中文数据展示，另行决策） |
| O7 | `catia_copilot/constants.py:22-49` `ABOUT_TEXT`、`:304` `SOURCE_FROM_DISPLAY` | 旧中文常量与反查字典仍保留：`ABOUT_TEXT` 在主窗口已不引用（实际用 `build_about_text()`）；`SOURCE_FROM_DISPLAY` 仅作写入端文本→值防御（`bom_write.py:53`），store 为 raw 值时不触发。 | INFO（死代码/防御性代码，可清可留） |
| O8 | `resources/i18n/*.qm` 为跟踪文件 | 仓库中 .qm 可能与 .ts 轻微滞后；构建管线（两 ps1）会预编译重建 .qm，CI 强制 verify。建议将 .qm 视作构建产物，避免手工同步。 | INFO（流程建议） |

---

## 四、符合结论（spec 对照核验）

### 1. TS 目录质量（spec §2/§5）
- en_US 与 zh_CN 均 **1002 条 message、unfinished=0**、en/zh source 集合完全一致（0/0）、占位符集合失配 0。
- en_US 全量 1002 条译为真实英文（含中文的 translation = 0；抽样翻译质量良好，含 `{0}` 复刻）。
- 真实 `pyside6-lupdate` 对 18 个生产文件提取 = 1002，与 TS 双向差集 0/0；所有 `translate()` 调用 context/source 均为字符串字面量（唯一例外是 i18n.py 的封装函数自身，正确）。

### 2. Qt 翻译生命周期与回退（spec §4.11/§4.12）
- `main.py:42` 在创建 `MainWindow` 前安装翻译器，`app_translator`/`qt_translator` 引用保持至 `app.exec()` 结束（`main.py:42-49`）。
- `i18n.py` 回退链完整：目标 qm 缺失/加载失败 → warning + 安装 `qtbase_zh_CN.qm`，界面回退中文；`_current_ui_lang` 仅在实际生效后才记录。
- zh 语种安装 qtbase 中文化标准按钮；打包侧由两构建脚本 prebuild 定位并 postbuild 拷贝 `qtbase_zh_CN.qm`（兼容 `_internal\` 与旧根布局），CI 中 lrelease 重建 .qm。

### 3. Source raw / 撤销 / 导出恒中文（spec §4.3/§4.10）
- 编辑：`bom_edit_dialog_v3.py:136` combo `itemData` 存 raw `"0"/"1"/"2"`，`source_display` 仅渲染；`_read_source_raw`（1322）读 currentData → store raw。
- 撤销/重做：`_push_undo` 记录 store 原值（1382-1385），回写 raw，界面经 `source_display` 重渲染 —— 语言无关。
- 导出：`bom_edit_dialog_v3.py:3001` 用 `SOURCE_TO_DISPLAY` 恒定中文；P1-6 交付的 export 测试（`test_export_chinese.py`）通过。

### 4. PLM 事件化 / 颜色 / 去重（spec §4.5-4.8）
- `sync.py` 通过 `_makecb` 双通道下发：文本通道（进度展示）与结构化 `SyncEvent`（携带语言无关的 `*_code`）。
- UI 端 `_on_sync_event`（3006）以 code 驱动：速度数值（`_fmt_kbps`）、PN 级去重计数（终态事件每 PN 一次）、文案映射 `_event_*_text`；颜色 `_sync_row_color_from_event`（180）code 判定，文本后备 `_sync_row_color`（147）仅在事件缺 code 时使用。
- 测试背书：`test_english_row_still_colored_by_code`、`test_terminal_looking_lines_do_not_touch_results`、`test_same_pn_terminal_events_count_once`、`test_text_log_does_not_set_speed` 等均通过。

### 5. UI 迁移覆盖（spec §4.1）
- 16 个 UI 相关文件经 AST 全量扫描（含非 translate 调用的中文常量，含 keyword 参数）：除上表 B1/B2/R1 外，其余中文字符串均为注释/docstring/日志/LLM 上下文提示词/数据值/导出内容，非可达 UI 文案。
- 已逐模块核实：主窗口（含 About、UAC 复制确认、图纸/模板/导出页）、嵌入面板菜单、BOM 编辑、质量特性、导出/转换/依赖模板对话框、AI 面板/设置/会话侧栏（`_provider_type_label`/`_cred_placeholder_display` 全走翻译）、PLM 主界面与 `_SettingsDialog`/`_HistoryDialog`/`_PullDialog`/`_AttachmentDialog`（除 B1）、帮助（`_HELP_HTML_EN` 全量英文版 + 中文版）。

### 6. verify 脚本与构建（spec §2.4/§6、用户关注点）
- `scripts/verify_translations.py`（233 行）：XML 合法性、无 unfinished、花括号配对、占位符集合一致、en/zh source 双向一致、**真实 lupdate 对 18 文件清单的双向覆盖** —— 此次实跑输出 1002/0/0，退出码 0。
- 局限（已确认）：lupdate 覆盖只约束“已包裹的字面量”，**无法发现未包裹的可达 UI 字符串** —— 这正是 B1/B2/R1 能漏过的根因。建议增加一道 AST 守卫（扫描含中文常量且不在 translate 调用的可达 UI 字符串），即可在 CI 前拦截此类回归。
- `build_nuitka.ps1` / `build_nuitka_installer.ps1`：prebuild = verify + lrelease 重建 .qm + qtbase 定位（`python -c "import PySide6…"`，非硬编码）；postbuild = qtbase_zh_CN.qm 拷入产物。`setup.iss [Languages]` 中英双语。`release.yml` 跑 verify + 单测 + 禁目录检查 + 安装器构建。
- 设计文档“tools/verify_translations.py”与实际 `scripts/` 路径存在表述不一致（文档笔误，无功能影响）。

### 7. 测试（用户关注“不能以 import 当完成”）
- unittest 全量 **223 通过（30.9s）**，其中 i18n 子集 132 用例全绿；覆盖：运行期 zh/en 文本断言、real lupdate/lrelease、`FakePlmClient` 事件生产端、撤销/导出恒中文、回退与 qtbase、占位符一致性等。
- 本次全量运行未复现 Qt 原生退出异常（offscreen 平台 + unittest discover 干净退出）。

---

## 五、结论

- **架构与覆盖判定：通过** —— 1002 全译、生命周期/回退、Source 恒中文、PLM 事件化、构建管线均合规且有测试与真实 lupdate/lrelease 背书。
- **可达 UI 完整性判定：基本通过，存在 2 处必须修复 + 1 类建议修复**（B1、B2、R1），全部为“漏包 translate”而非“码表缺失”。
- 建议：修复 B1/B2/R1 后按 `scripts/verify_translations.py` 重新合并新词条并回归 223 测试，即可进入发布。