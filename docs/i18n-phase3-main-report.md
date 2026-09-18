# i18n Phase 3 — PLM 主工作台 i18n 改造交接报告

- 日期：2026-09-17
- 分支：`feat/i18n`
- 范围：Task 3.2 步骤 4 —— `PlmWorkbench` 主类全部**可达**中文可见文案改为
  `translate("CATIACopilot", "<字符串字面量>")`；仅渲染层经工厂翻译，业务值保持中文原值。
- 文件：
  - 修改：`catia_copilot/ui/plm_workbench.py`
  - 新增：`catia_copilot/tests/test_i18n_plm_main.py`
  - 新增：`docs/i18n-phase3-main-translations.json`（主工作台 160 义词条，当前为占位表）
  - 本报告：`docs/i18n-phase3-main-report.md`

## 目标与做法

主工作台窗口内所有面向用户的可见文案（工具栏、差异表头、状态栏、对话框提示、设置
面板、同步进度等）均改为调用 `translate("CATIACopilot", "中文字面量")`。三条原则：

1. **业务值不翻译**：差异状态 `_ST_*`、表头 `_DC_HEADERS`、同步模式 `_UPGRADE_*`、
   同步结果列名 `_SYNC_COL_DISPLAY`、`_last_sync_mode="Push 选中"`、`_detect_sync_mode`
   返回值等仍保持中文原值，仅供索引/比较与外部引用。
2. **渲染工厂翻译**：新增模块级工厂 `_st_display` / `_header_display` / `_sync_col_display`，
   在渲染时把业务值映射为随语言切换的界面文案；未知键安全回显原值。
3. **字面量 API**：全部 `translate` 调用 context 恒为 `"CATIACopilot"`，source 恒为
   字符串字面量，动态值统一 `translate(...).format(...)`，禁止对已翻译结果再拼接。

死代码方法（`_build_history_panel` / `_build_settings_tab` / `_on_pull` /
`_on_test_conn` / `_on_save_conn` / `_on_conn_ok` / `_on_conn_fail` /
`_on_refresh_tags` / `_on_tags_loaded` / `_on_create_tag` / `_on_tag_created` /
`_on_add_rule` / `_detect_sync_mode` 等）不在改造范围；其中与可达代码共用同串的
`replaceAll` 目标一并翻译（词条已在 JSON 中存在，无新增）。

## 改动说明

### 1. 模块级渲染工厂（`_fmt_kbps` 之后新增）

| 工厂 | 职责 |
|------|------|
| `_st_display(state)` | 差异状态 `_ST_*` 业务值 → 界面文案；未知键回显原值；空/None 回退 `_ST_UNKNOWN("?")` |
| `_header_display(col)` | 差异表列索引 → 表头文案；选择列(0)=`""`，附件列(12)=`"\uf0c6"`，1–11 翻译，其余返回 `""` |
| `_sync_col_display(key)` | 同步结果内部列名 → 表头文案；`_SYNC_COL_DISPLAY` 业务值不变，未知键回传原键 |

差异表头渲染改为 `[_header_display(i) for i in range(len(self._DC_HEADERS))]`；
同步结果表头（预览树）经 `_sync_col_display` 渲染。

### 2. 主类可达文案 translate 化

覆盖范围：窗口标题；工具栏按钮文本/tooltip（Push 选中 / Pull 选中 / 全选 / 全不选 /
+ 新增 PLM Part / ⚙ 同步选项 / ⚙ 设置 / ☁ 刷新 PLM 状态 / ↺ 加载工作区 / 📋 历史）；
连接/工作区/状态相关标签；差异表头工厂化；Push（`_on_sync_start`：勾选校验、未保存
文件、文件名与零件编号不一致、强制覆盖他人签出确认、进度）；Pull（`_on_pull_selected`、
`_on_pull_all_done`：无附件、下载完成、结果汇总）；新增 PLM Part（`_on_add_plm_part`：
递归展开、问询、失败、已添加）；高级选项（`_on_show_settings`：模式 radio、预设、
复选框、tooltip、提示）；扫描与状态栏（扫描进度、PLM 查询、上传速度单位前缀、
加载完成）；同步事件状态前缀；删除确认；设置面板（`_init_settings_controls`）。

### 3. replaceAll 决策

三组同串出现多处的 `translate` 化通过 `replaceAll` 一次替换（含死代码出现处，来源
词条已在 JSON 中）：`配置不完整 / 请先配置 PLM 连接信息。`、`未设置工作目录 /
请先在设置中配置工作目录。`、`工作区：{0} / 工作区：—`。首轮遗漏的 `_on_sync_start`、
`_on_pull_selected`、`_init_settings_controls` 三处已核查补齐。

## 词条表（docs/i18n-phase3-main-translations.json，160 条）

由源码 AST 提取自动生成（PlmWorkbench 类体 + 三渲染工厂的全部 `translate` source），
保证与源码**双向一致**（测试强制校验）。当前为键值同串占位表，英文译文待后续订单
填充。含多行长消息（如 CATIA 文档查找顺序设置、未保存文件、文件名与零件编号不一致、
强制覆盖确认、找到零件问询等），键含 `\n` / `\"` 字面转义，读取时以
`json.loads(Path.read_text(encoding="utf-8"))` 还原。

## 测试（catia_copilot/tests/test_i18n_plm_main.py，22 用例）

覆盖：AST 防线（主类 + 三工厂的 translate 前两参均为字符串字面量、context 恒为
`CATIACopilot`）；词条表与源码 AST 双向覆盖一致、占位表键值同串、工厂词条齐全；
业务值稳定（`_ST_*` 七值、`_STATUS_COLORS` 键集、`_DC_HEADERS` 13 列、`_UPGRADE_*`、
`_SYNC_COL_DISPLAY`、`_BOM_COL_HEADERS` 同对象）；渲染工厂中文回退 / 英文 patch /
未知键回显 / 空值回退 `?` / `\uf0c6` 附件列不变；运行时窗口标题与按钮中英文切换；
同步进度前缀 `正在同步…… ({0} / {1})  {2}` 中英文。

## 验证结果（真实退出码）

- `python -m unittest catia_copilot.tests.test_i18n_plm_main -v`：**22 passed，退出码 0**。
- 全量 `python -m unittest discover -s catia_copilot/tests -t . -v`：见执行结果。

## 遗留（明确不扩范围）

- 对话框子类（`_SettingsDialog` / `_HistoryDialog` / `_AttachmentDialog` / `_PullDialog`）
  的文案翻译不在本任务范围，后续如需可另开任务。
- 上述死代码方法的文案不处理；若某日重建这些入口，需在启用时同步补齐翻译。