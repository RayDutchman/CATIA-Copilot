# i18n Phase 3 — PLM 对话框 i18n 改造交接报告

- 日期：2026-09-18
- 分支：`feat/i18n`
- 范围：Task 3.3 —— 四个独立 PLM 对话框（`_SettingsDialog` / `_HistoryDialog` /
  `_AttachmentDialog` / `_PullDialog`）的全部**可达**中文可见文案改为
  `translate("CATIACopilot", "<字符串字面量>").format(...)`，并新增五个对话框表格头
  渲染工厂；新词条以英文交接，测试覆盖真实 lupdate 提取 / lrelease 构建 / mock 网络行为。
- 文件：
  - 修改：`catia_copilot/ui/plm_workbench.py`（仅四对话框 + 五个表头渲染 helper）
  - 新增：`docs/i18n-phase3-dialogs-translations.json`（96 条新词条 → 英文译文）
  - 新增：`catia_copilot/tests/test_i18n_plm_dialogs.py`（36 用例）
  - 本报告：`docs/i18n-phase3-dialogs-report.md`

## 目标与做法

1. **业务值不翻译**：历史行数据（`sync_mode` 原值如 `Push 选中`、时间戳）、附件文件名、
   BOM 行数据、`_PC_HEADERS`/`_PC_*` 列常量、`lastIterationNumber` 数值等保持英文/中文
   原值；仅渲染时经工厂或 `translate(...).format(...)` 显示翻译。
2. **表格头渲染工厂**：新增五个模块级工厂，替换原 `setHorizontalHeaderLabels(["字面量", ...])`：

   | 工厂 | 用途 |
   |------|------|
   | `_tags_table_header_display(col)` | 标签表（"标签名称" / "ID"） |
   | `_rules_table_header_display(col)` | 规则表（"CATIA"设计状态"属性值" / "PLM 标签" / "操作"） |
   | `_history_table_header_display(col)` | 历史表（时间/新建/更新/跳过/失败/用户名/同步模式） |
   | `_attachment_table_header_display(col)` | 附件表（"文件名" / 附件数列空串） |
   | `_pc_header_display(col)` | Pull BOM 表（层级/零件号/版本/迭代/签出人/本地文件/可用文件/下载?）；`_PC_HEADERS` 原常量仅作回退 |

3. **字面量 API**：全部 `translate` 的 context 恒为 `"CATIACopilot"`、source 恒为字符串
   字面量；动态内容一律 `translate(ctx, "...{0}...").format(...)`；不存在模块/类初始化
   期翻译，helper 在渲染调用时才求值。
4. **花括号语义**：含目录占位的消息（如 `下载到：{0}/{{零件号}}/{{文件名}}`）对应的英文
   译文必须写成 `{0}/{{Part Number}}/{{File Name}}`（双花括号转义），否则 `.format()`
   会把它当命名占位符抛 `KeyError`（测试强制校验转义与 `format` 渲染后保留名义目录占位）。
5. 每个对话框的**可达**入口：标题、组框、表单标签、placeholder、按钮文本/tooltip、
   `_on_*` 状态刷新、QMessageBox 标题与正文，全部经 translate 渲染。

## 改动说明

### _SettingsDialog（连接/工作区/标签规则/连接日志）

标题、四组框（连接配置/工作区详情/连接日志/标签自动映射规则）、表单行、placeholder、
`保存配置`/`测试连接`/`添加规则`/`刷新标签列表`/`新建标签`/`浏览…`/`关闭` 按钮、
`_on_conn_ok` 工作区详情拼接（工作区 ID/描述/成员数/连接成功）、`_on_test_conn` 日志行、
`_on_conn_fail` 错误行等全部 translate 化；表头改用两个 helper。

### _HistoryDialog

标题 `同步历史`、表头 helper 化、placeholder、`清空历史`/`关闭`；`_on_selected` 详情行
（时间/用户/模式/新建/更新/跳过/无变化/失败 逐项 + `失败/警告详情：` 与
`  · {0}`）全部 `translate(...).format(...)`。

### _AttachmentDialog

标题 `PLM 附件 — {0} / {1}`、信息行 `零件号：<b>{0}</b>　版本：<b>{1}</b>　迭代：<b>{2}</b>`
（迭代 0 回退 `最新`）、`_lbl_dl_all` 按钮（`全部下载到工作目录`）、状态标签
（`正在查询各零件附件列表……`/`加载失败：{0}`/`共 {0} 个附件`/`该版本暂无附件。`/
`下载中 {0}/{1}：{2}`/`部分下载失败`/`全部下载完成，共 {0} 个文件`）、QMessageBox 提示。

### _PullDialog

标题 `Pull — 从 PLM 拉取 BOM 树文件`、搜索行（label/placeholder/`展开 BOM 树`）、
表头 helper 化、`dir_lbl` 目录说明、`⬇  下载勾选文件`/`关闭`、状态标签
（`正在递归展开 BOM 树：{0} ……`/`BOM 树为空…`/`BOM 树：{0} 个零件  |  本地无文件：
{1} 个（已默认勾选）`/`下载完成！共 {0} 个文件 → {1}/{{零件号}}/{{文件名}}`）、行内
`√ 已有`/`— 无`/`（无附件）`、`_on_download` 未勾选警告等。

## 词条交接（docs/i18n-phase3-dialogs-translations.json，96 条新增 → 英文）

- 判定规则：`新增 = 四对话框+helper 的 AST 提取词条 −（main 160 条 ∪ ui 15 条）`。
  已复用 **15 条**既有词条不再重复交接：`删除 / 同步模式 / 失败 / 所有勾选零件均无可下载附件。/
  新建 / 无附件 / 时间 / 更新 / 未设置工作目录 / 未选择 / 浏览… / 用户名 / 跳过 / 连接失败 /
  选择工作目录`。
- 96 条新词条的英文译文直接写入 JSON（值=英文，非占位；中文源串为键）。测试强制：
  键集合 == AST 新增集（双向一致）、译文为非中文、无裸命名花括号占位符、含 `.format()`
  的目录消息在 `format` 后仍保留 `{Part Number}`/`{File Name}`。
- 注意存在与主类共用同名同义的字符串（如 `连接配置`/`测试连接`）——它们属于对话框词条，
  由本表交接英文；主类侧因历史遗留被误 translate 的部分已回退字面量（见"主类清理"）。

## 主类清理（减少跨表冲突）

复核全量测试时发现 `PlmWorkbench._build_settings_tab`（Tab 1）与 `_on_conn_ok` 中此前
混入了 16 处**未交接**的 `translate`（`连接配置`、`保存配置`、`测试连接`、`工作区详情`、
`连接日志`、`添加规则`、`服务端地址：`、`用户名：`、`密码：`、`工作区：`、`工作目录：`、
`Pull 下载文件保存目录…`、`工作区 ID：{0}`、`描述：{0}`、`成员数：{0}`、`连接成功`），
与 main 交接表（160 条双向一致）冲突导致 `test_i18n_plm_main.py` 失败。这些词条不在任何
交接表内，main 任务明确标注"Tab1/死代码方法不在范围"。治理方式：**回退为中文原文
字面量**（16 处，脚本按行精确还原为 HEAD 版本原样），恢复 main 表一致；Tab 1 后续如需
翻译另开任务。四对话框内同名词条的 translate 不受影响。

## 测试（catia_copilot/tests/test_i18n_plm_dialogs.py，36 用例）

- `TestTranslateLiterals`：四对话框类 + 五 helper 的 translate 前两参均字符串字面量、
  context 恒为 `CATIACopilot`。
- `TestHandoffCoverage`：交接表与 AST 新增集双向一致；不与 main/ui 表重复；译文为英文
  非占位；花括号安全与 `.format()` 渲染保留名义目录占位。
- `TestRealLupdateExtraction`：真实 `pyside6-lupdate`（`-extensions py -no-obsolete`）
  对 `plm_workbench.py` 提取，交接表全部 96 键均在提取结果（CATIACopilot context）中。
- `TestRealLreleaseBuildLoad`：真实 `pyside6-lrelease` 构建 `.qm`，`QTranslator` 加载后
  `QCoreApplication.translate` 实际返回英文，含 `{0}` 占位符串经 `.format` 出参。
- 行为测试（构造真实 QWidget，所有对话框 `close + deleteLater + processEvents` 清理，
  避免历史堆损坏，保证进程退出码 0）：
  - 设置/历史/附件/Pull 的中文回退与英文 patch 切换（窗口标题、按钮、表头、详情行）；
  - `_AttachmentDialog` 通过 **mock `PlmApiClient`**（假客户端列表接口）异步加载附件，
    不触真实网络；断言 `_files`、行数、状态文案；`lastIterationNumber=0` 显示 `最新`；
  - `_PullDialog._on_bom_done` 行渲染与状态文案、未勾选行下载经 mock `QMessageBox.warning`
    提示 `未选择`。

## 验证结果（真实退出码）

- `python -m pytest catia_copilot/tests/test_i18n_plm_main.py catia_copilot/tests/test_i18n_plm_dialogs.py -q`：
  **44 passed，退出码 0**。
- 全量 `python -m pytest catia_copilot/tests/ -q`：**156 passed，退出码 0**。
- 4 个对话框 + 5 个 helper 均已单独编译通过（`py_compile`）。

## 遗留（明确不扩范围）

- 主类 Tab 1 设置页（`_build_settings_tab` 等）仍为中文原文，未在本任务翻译；如需界面
  语言随系统切换，须在 main 交接表补词条后另行处理。
- 96 条英文译文尚未合并进正式 TS/QM；全量翻译文件合并与构建仍属阶段 5。