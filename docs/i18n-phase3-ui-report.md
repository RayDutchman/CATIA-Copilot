# i18n Phase 3 — PLM 工作台 UI 结构化事件消费交接报告

- 日期：2026-09-17
- 分支：`feat/i18n`
- 范围：仅 UI 消费端（Task 3.2）。事件生产端（Task 3.1）不改动。
- 文件：
  - 修改：`catia_copilot/ui/plm_workbench.py`
  - 新增：`catia_copilot/tests/test_i18n_plm_ui.py`
  - 新增：`docs/i18n-phase3-ui-translations.json`（Task 3.2 新增展示字面量的英文译文）
  - 本报告：`docs/i18n-phase3-ui-report.md`

## 目标与做法

PLM 工作台消费 Task 3.1 的 `SyncEvent`：结果映射、行颜色、终态计数、速度显示
全部由**结构化事件 + 稳定 code** 驱动，**不再解析中文日志文本**；`_on_sync_progress`
降级为纯日志展示。仅 PLM 工作台消费事件，不为本任务做窗口级翻译，不重写
`sync_bom_to_plm` / `SyncResult` 统计（已知旧缺陷保留，见"遗留"）。

## 改动说明

### 1. 新信号 `_SyncWorker.sync_event`

`_struct_cb` 在 `progress`（文本行）与 `upload_log`（旧接口）之后**最后**发出完整
`SyncEvent`，保证其后翻译文案与 code 级颜色覆盖旧接口写入的原始中文；
业务计数只在 `_on_sync_event` 进行，不与文本槽、`upload_log` 重复计数。

### 2. `_on_sync_progress`：只显示日志

- 去掉 `>>` / `[X]` / ` | ` 列解析、速度正则、`_sync_result_map` 更新、终态计数。
- 保留：空行忽略、纯装饰横线（`---`）忽略（纯展示过滤）、200 字符截断且避免在
  `<名称>` 中断开。
- 转换过程（summary 事件）文本经此原样显示，**不冒充结构化结果**。

### 3. 新增 `_on_sync_event`：唯一业务槽

| 事件 | 行为 |
|------|------|
| `speed_kbps`（任意事件带数值） | `_lbl_upload_speed` 显示 `_fmt_kbps(数值)`，≥1024 KB/s 自动切 MB/s；不再解析文本速度 |
| `node_done` / `node_skip` / `node_fail`（终态） | 按 `part_number`（含 `<`/`|` 完整）更新 `_sync_result_map` + code 级行颜色；按 pn 去重计数一次并推进进度条 |
| `node_progress`（过程行） | 只刷新 update 列与颜色，**不**参与终态计数 |
| `header` / `summary` | 不动结果映射 / 计数，由文本槽展示 raw 日志 |

### 4. 文案：code → 固定字面量 `translate`（模块函数）

- `_event_source_text` / `_event_update_text` / `_event_checkin_text`：以 dict 值调用
  `translate("CATIACopilot", "中文字面量")`，key 为 `sync.CODE_*` 常量。
- code 缺失或未知时**安全回退**事件原始 `source`/`update`/`checkin`，不空白、不报错。

### 5. 行颜色：`_sync_row_color_from_event`（语言无关，只读 code）

| 条件 | 颜色 |
|------|------|
| source=`failed`；update=`update-failed`/`upload-failed`/`conversion-failed`；checkin=`checkin-failed` | 红（主题 `Link`） |
| source=`skipped`/`unchanged` | 灰（主题 `Mid`） |
| source=`created` | 绿 `#27ae60` |
| source=`updated` | 蓝（主题 `Highlight`） |
| 其他/未知 | `None`（不染色） |

`_update_sync_result` / `_refresh_sync_cols_in_tree` 新增可选 `color` 参数；未传时
沿用旧文本匹配 `_sync_row_color`（BOM 重载等兼容路径）。英文展示下行颜色依旧有效。

### 6. 接口兼容

`_on_sync_progress` / `_on_upload_log` / `_update_sync_result` 签名与调用点保持可用；
真实 UI 路径仅走 `sync_event → _on_sync_event`。

## 新增译文（docs/i18n-phase3-ui-translations.json）

来源/更新/签入三列固定展示：

| 中文（translate 字面量） | 英文 |
|------|------|
| 新建 / 已更新 / 跳过 / 无变化 / 失败 | Create / Updated / Skipped / Unchanged / Failed |
| 属性已写入 / ✗ 更新失败 / 已上传 / ✗ 上传失败 | Attributes written / ✗ Update failed / Uploaded / ✗ Upload failed |
| 转换中 / 转换完成 / ✗ 转换失败 | Converting / Converted / ✗ Conversion failed |
| 已签入 / ✗ 签入失败 / 保留签出 | Checked in / ✗ Check-in failed / Checkout kept |

## 测试（catia_copilot/tests/test_i18n_plm_ui.py，27 用例）

覆盖：信号存在性；中文回退 + 英文 patch（读 JSON 恢复）；译文表完整性；相同 pn
多终态只计一次、特殊字符 pn（`<`/`|`）完整保留；update/checkin/upload/conversion
失败均红（颜色只依赖 code，英文下依旧成立）；speed 数值显示与文本槽不设速度；
文本日志不改结果/计数；未知 code 回退原文；`node_progress` 不计数；`summary`/
`header` 不动状态；close/reopen 生命周期状态复位；plm_workbench 全部 `translate`
调用前两参为字符串字面量（AST 防线）。

## 验证结果（真实退出码）

- `pytest catia_copilot/tests/test_i18n_plm_ui.py`：**27 passed，退出码 0**。
- 相关 i18n 套件（plm_events + workbenches + convert_dialog + plm_ui）：**48 passed，退出码 0**。
- 全量 `catia_copilot/tests`：用例 **112 all passed**，但解释器退出码 `-1073740940`
  （0xC0000374 堆损坏）。**该退出码为仓库既有问题**：使用 `git stash` 回退本任务全部改动
  后跑同样全量，退出码仍为 `-1073740940`；本任务新增测试单独/与 smoke 组合运行均
  退出码 0。与本任务无关。

## 遗留（明确不扩范围）

- `sync_bom_to_plm` / `SyncResult` 统计的旧缺陷（汇总计数与逐事件去重口径不一致等）
  不在本任务范围，如需修复请另开任务。
- `test_i18n_plm_ui.py` 中全量测试退出时的 C++ 堆损坏：与本次改动无关，建议单独排查
  既有 Qt/C++ 对象析构或 COM 清理顺序。
- 转换进度事件暂无稳定 code（生产端为 summary），UI 只展示 raw 日志；若后续需要
  翻译/着色，需生产端补充 `update_code`。