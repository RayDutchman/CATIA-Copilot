# i18n Phase 3 — PLM 结构化事件生产端交接报告

- 日期：2026-09-17
- 分支：`feat/i18n`
- 范围：仅事件生产端（Task 3.1）。UI/TS 未改动（下一 worker 接 Task 3.2）。
- 文件：
  - 修改：`catia_copilot/plm/sync.py`
  - 新增：`catia_copilot/tests/test_i18n_plm_events.py`
  - 本报告：`docs/i18n-phase3-events-report.md`

## 目标与做法

所有影响 UI 结果/进度的状态均通过 `SyncEvent` 携带**固定 code** 与**明确 PN**
（`part_number` 完整、含 `<`/`|` 不截断），不再要求 UI 解析中文日志；
现有文本回调保留，输出与旧版**逐字一致**（`_emit_text` 优先原样输出生产端
预生成的 `_text_line`），UI 文本解析在 Task 3.2 完成前仍可正常工作。

## 接口契约（供下一 worker：UI 消费与计数）

### SyncEvent 字段

既有字段不变，新增三个可选 code 字段（默认 `None`，向后兼容）+ 一个私有文本字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `header` / `node_start` / `node_progress` / `node_done` / `node_skip` / `node_fail` / `summary` |
| `part_number` | str | 明确、完整的零件号（含 `<`,`|` 不截断）；终态事件必填 |
| `source` | str | 签出来源显示文案（"新建"/"签出"/"已签出-本人"/"覆盖他人签出"/跳过原因…） |
| `update` | str | 更新结果显示文案（"属性已写入"/"✗ 更新失败"/上传/转换过程文案…） |
| `checkin` | str | 签入状态显示文案（"已签入"/"✗ 签入失败"/"保留签出"/"" ） |
| `message` | str | 原始日志消息（文本行） |
| `speed_kbps` | float\|None | 上传成功过程事件的真实速度（KB/s） |
| `source_code` | str\|None | 来源类别，见下表 |
| `update_code` | str\|None | 更新/上传/转换结果，见下表 |
| `checkin_code` | str\|None | 签入结果，见下表 |
| `_text_line` | str | **私有**，仅供文本兼容层，结构化消费者忽略 |

### code 列表（全部 ASCII，语言无关）

`source_code`：

| code | 含义（source 文案） |
|------|------|
| `created` | 新建成功（新建） |
| `updated` | 已存在并更新（签出/已签出-本人/覆盖他人签出） |
| `skipped` | 跳过类终态（跳过-不新建 / 跳过-被@xxx / 撤销失败-@xxx） |
| `unchanged` | 增量无变化跳过（无变化-跳过） |
| `failed` | 失败类终态（创建失败/签出失败/撤销后签出失败） |

`update_code`：

| code | 含义 |
|------|------|
| `written` | 属性已写入（终态） |
| `update-failed` | ✗ 更新失败（终态） |
| `uploaded` | 附件/文件上传成功（node_progress） |
| `upload-failed` | 上传失败（node_progress） |
| `converting` / `converted` / `conversion-failed` | 转换中 / 完成 / 失败或超时（node_progress，预留） |

`checkin_code`：

| code | 含义 |
|------|------|
| `checked-in` | 已签入 |
| `checkin-failed` | ✗ 签入失败 |
| `retained` | 保留签出（after_update_policy=KEEP_CHECKOUT） |

集中映射：`sync._SOURCE_CODE_MAP` + `_source_code_for(source)`。
⚠️ 硬约束：**UI 不得通过翻译 `source`/`update`/`checkin` 文本反推状态**，必须读 code。

### 每 PN 终态计数语义（done 计数）

- `node_done` / `node_skip` / `node_fail` 为终态事件，**每 PN 至多一次**：
  - create / 本人签出 / 覆盖他人签出 / 他人签出跳过 均写入 `uploaded_pns` 去重，
    同一 PN 跨层级二次出现不再产生终态事件（现有业务去重保持一致）。
  - 成功路径的终态 `node_done` 由阶段二签入（或保留签出）一次性发出：每个
    `CheckinTicket` 恰好一条，无中途重复。
- `node_progress`（上传/转换过程行）**不参与 done 计数**。
  - 上传成功会产生一条 `node_progress`（`update_code="uploaded"`），
    其 `speed_kbps` 为数值型真实速度；相同行仍以旧格式顺发一条 summary 文本（速度行）。
- ⚠️ 例外（现有业务语义，未改动）：**创建失败 / 签出失败 / 增量无变化 / 不新建 等
  路径不注册 `uploaded_pns`**，同一 PN 在树中多次出现时会多次发终态事件，
  与现有 `result.failed/unchanged/skipped` 计数逐次累加的行为一致。若 UI 需要严格
  "每 PN 一次"，需在 Task 3.2 消费侧按 `part_number` 去重（建议如此）。
- 计数仍来自 `SyncResult` 整数（created/updated/skipped/failed/unchanged），语言无关。

## 验证记录（命令退出码）

Windows 原生、`QT_QPA_PLATFORM=offscreen`：

1. `python -m unittest catia_copilot.tests.test_i18n_plm_events -v` → **exit 0**（8 passed）
   - 覆盖：code ASCII 断言；create/本人签出/他人跳过 去重；update/checkin 失败 code；
     PN 含 `<` 与 `|`；文本+结构化双通道（`_makecb` 直测 + 真实 sync 集成）。
2. `python -m unittest discover -s catia_copilot/tests -t .` → **exit 0**（85 tests OK，含原 77）

## 对下一 worker（Task 3.2 UI 消费）的建议

- 接收 `progress_callback_structured` 事件，按 `type` + `part_number` + 三码渲染；
- 进度计数按 `part_number` 对终态事件去重（兼容上述例外路径）；
- 终态 `node_done` 的 `update`/`checkin` 中文文案可按 `update_code`/`checkin_code`
  翻译，不再解析 `">>"`、`"[X]"`、`" | "`、表头、`lbl.split("<")`；
- 删除/降级 `_on_sync_progress` 的文本解析路径时，注意本报告"每 PN 终态例外"。