# i18n Phase 4 — AI 附属对话框改造报告

- 日期：2026-09-18
- 分支：`feat/i18n`
- 状态：完成（未提交 commit / push）

## 范围

本次仅修改：

| 文件 | 动作 |
|------|------|
| `catia_copilot/ui/session_config_dialog.py` | 会话设置对话框源码文案改造（`translate("CATIACopilot", 字面量).format(...)`） |
| `catia_copilot/ui/model_state_dialog.py` | AI 建模状态面板源码文案改造 |
| `catia_copilot/ui/log_window.py` | 日志窗口源码文案改造 |
| `docs/i18n-phase4-ai-dialogs-translations.json` | 新增交接表（29 条） |
| `catia_copilot/tests/test_i18n_ai_dialogs.py` | 新增测试（17 用例） |
| `docs/i18n-progress.md` | 进度标记（Phase 4 — AI 附属对话框完成） |

未触碰：`ai/tools.py`（schema 名称/描述、`DEFAULT_SYSTEM_PROMPT`）、`ai_chat_panel.py`
（上轮已改）、用户会话历史与配置（API Key/模型 id/配置 key）、TS/QM 构建与合并、
`README*`、任何 git 操作。禁止项：未改 TS、未派 agent、无 commit/push/stash/reset。

## 改造概览

### 1. `session_config_dialog.py`（20 个唯一 source，16 新）

- 窗口标题 `会话设置`、会话名标题 `会话：{0}`、模型表单 `模型：` / `Temperature：` 及
  温度说明、上下文消息数表单与说明、工作空间占位/行标签与提示、`浏览…` / `清除` /
  `重置所有字段为默认值` / `清空本会话消息记录…` 按钮及其 tooltip、清空二次确认对话框
  （标题 + `确定要清空会话「{0}」的所有对话历史吗？\n此操作不可撤销。`）、温度滑块
  `未设置` 标签、`选择工作空间目录`。
- 默认模型下拉项：显示文本 `使用全局默认（{0}）` 随界面语言翻译，但
  `_apply_and_accept()` 中对手动输入文本的判定 `text.startswith("使用全局默认")`
  **保持中文原值不变**（业务判断不改，仅显示翻译）。
- 复用既有词条：`会话设置`、`清空消息记录`、`选择工作空间目录`、`浏览…`
  （既有表已收录，其中 `浏览…` 既有值为中文占位，测试侧补英文 patch）。

### 2. `model_state_dialog.py`（14 个唯一 source，13 新）

- 面板各标签/消息翻译：标题 `模型状态`、`特征树`、`质量属性`、`质量：`、`重心：`、
  `步骤日志`、`关闭`（既有）、失败/零件名/无数据三种标题
  `模型状态 — 失败` / `模型状态 — {0}` / `模型状态 — 无数据`、`无特征`、根节点
  `零件几何体`、`—（未赋材料或无数据）`、`— 无步骤记录 —`。
- **业务判断原值保持**：`set_state` 状态字段（`part_name` / `features` 元素 /
  `step` 名）为 CATIA/脚本业务数据，一律原样展示；`status == "ok"` 判定保持不翻译，
  `✓` / `✗` 仅作显示映射；`mass_kg` / `cog_mm` 数值与单位格式原样。交接表不含
  `part_name / features / steps / status / success / mass_kg / cog_mm` 等业务标识（测试强制）。
- 决策记录：根节点 `零件几何体` 为硬编码显示标签（非状态数据），译为 `Part Body`；
  子节点特征名（如 `Pad.1`、`拉伸`）保持原值。

### 3. `log_window.py`（3 个唯一 source，均既有表已收录，0 新）

- `打开日志文件` 按钮与错误对话框（标题 `无法打开日志文件`、正文
  `无法打开日志文件：\n{0}\n\n{1}`）接入 translate；三条词条均已在阶段 1/2 交接表
  收录（`Open Log File` / `Unable to Open Log File` 等），不重复交接。
- 不翻译：窗口标题 `CATIA Copilot 1.4.1 – Log`（本身为英文文案，含过时版本号，未改）；
  日志路径标签 `Log: {LOG_FILE}` 前缀已是英文、路径为数据，保持原样。

## 交接表

`docs/i18n-phase4-ai-dialogs-translations.json`：29 条 = 三文件 AST 提取的 37 个唯一
source − 既有表 8 条（`会话设置`/`清空消息记录`/`选择工作空间目录`/`浏览…`/`关闭`/
`打开日志文件`/`无法打开日志文件`/`无法打开日志文件：\n{0}\n\n{1}`）。全部英文译文；
含 `{N}` 的 source 保证译文占位符集合一致（键/值统一按源文本生成，测试强制）；无
`{N:.x}` 格式说明符；`{0}` / `{1}` 双占位符条目（清空确认、无法打开）无格式说明。

## 测试

`catia_copilot/tests/test_i18n_ai_dialogs.py`，17 用例：

1. AST 防线：三文件全部 `translate` 的 context/source 为字符串字面量、context 恒为
   `CATIACopilot`、source 含非 ASCII；各文件关键词条存在性。
2. 交接表：与三文件 AST 新 source 双向一致；译文非空、≠ source、占位符集合一致、无
   格式说明符、无中文残留；业务值隔离（状态字段/配置 key）；`status == "ok"` 判定与
   `使用全局默认` 判定保持中文原值（源码断言）。
3. 真实 `pyside6-lupdate`：三文件合并提取命中全部 29 条。
4. 真实 `pyside6-lrelease` + `QTranslator`：抽样 5 条（含占位符、含换行、含 em dash）
   实际返回英文。
5. 行为（offscreen）：
   - `SessionConfigDialog` 中英切换（标题/会话名/默认模型下拉项/占位符/按钮）；`ai_config`
     `load` / `list_model_ids` / `get_default_model_id` 全 mock，不读真实
     `%APPDATA%` 配置；`_apply_and_accept` 业务写回回归（默认模型 → `""`、温度 → `None`、
     上下文数、工作空间）。
   - `ModelStateDialog` 中英切换；`ModelStateDialog._settings` patch 到临时 ini，构建与
     关闭（`done → _save_geometry`）均不写真实注册表；业务数据（特征名/步骤名/质量数值）
     保持原值；`✓` 符号映射保持不变。所有用例 `close + deleteLater + processEvents`
     清理（带 `WA_DeleteOnClose`，清理容忍 `RuntimeError`）。
   - `LogWindow` 中英切换；窗口标题/`Log:` 前缀不变；win32 下 mock `os.startfile` 抛错
     捕获 `QMessageBox.warning` 的英文标题与正文。
   - 导入期 `Path.home()` 重定向至临时目录，避免 `log_window → logging_setup` 在导入时
     创建/写入真实 `~/CATIA_Copilot/logs`；临时目录 `atexit` 清理。

## 验证

```
$env:PYTHONIOENCODING="utf-8"; $env:QT_QPA_PLATFORM="offscreen"
python -m unittest catia_copilot.tests.test_i18n_ai_dialogs    → 17 tests OK，退出码 0
python -m unittest discover -s catia_copilot/tests -t .        → 213 tests OK（= 上轮 196 + 本次 17），退出码 0
```

全程无真实 COM / 网络 / CATIA 调用，无真实持久化污染（配置、会话、日志、注册表均
mock / 沙箱 / 临时目录）。

## 后续待办（不在本次范围）

- 全部交接表（phase1/2/3/4）尚未合并进正式 TS/QM —— 阶段 5 构建编译统一处理（本次未改 TS）。
- `log_window` 窗口标题含过时版本号 `1.4.1`，未在本次处理。