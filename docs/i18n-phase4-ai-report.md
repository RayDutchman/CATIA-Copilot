# i18n Phase 4 — AI 聊天面板改造报告

- 日期：2026-09-18
- 分支：`feat/i18n`
- 状态：完成（未提交 commit / push）

## 范围

本次仅修改：

| 文件 | 动作 |
|------|------|
| `catia_copilot/ui/ai_chat_panel.py` | 源码文案改造（AI 可及 UI 文案 `translate("CATIACopilot", 字面量).format(...)`） |
| `docs/i18n-phase4-ai-translations.json` | 新增交接表（57 条） |
| `catia_copilot/tests/test_i18n_ai.py` | 新增测试（17 用例） |
| `docs/i18n-progress.md` | 进度标记（Phase 4 — AI 完成） |

未触碰：`ai/tools.py`（schema 名称/描述、`DEFAULT_SYSTEM_PROMPT`）、
`ui/session_config_dialog.py`、用户会话历史与配置（API Key/模型 id/配置 key）、
TS/QM 构建与合并、`README*`、任何 git 操作。

## 改造概览

### 1. 显示名 helper

`_provider_type_label(ptype)`：provider 类型 key → 界面显示名，函数体内固定字面量
translate（避免 import 期翻译）。可翻译中文名：`Ollama（本地）`、`OpenAI / 兼容`、
`讯飞星火`、`自定义端点`。纯英文名原样返回：Anthropic / OpenRouter / DeepSeek /
AWS Bedrock / Google Vertex AI / GitHub Copilot（避免“翻译 == source”边界问题）。

`_cred_placeholder_display(ph)`：三条中文凭证占位随界面语言翻译：
`控制台 > HTTP 服务接口认证信息 > APIPassword`、`留空则使用本机 ADC`、
`（无需认证时留空）`；其余（`API Base URL` 等英文占位）原样返回。

### 2. 覆盖点（62 个唯一 source，其中 5 条既有表已收录）

- AISettingsDialog：窗口标题 `AI 助手设置`、配置文件提示、凭证/模型分组框、测试
  连接/从 API 获取/获取中/正在拉取模型列表/已获取 N 个模型/未获取到模型/请先勾选
  模型/未找到 API Base URL、运行时参数（最大工具调用轮数/请求超时及左右说明、` 秒`
  后缀、温度说明），Provider 增删对话框与确认删除消息，测试结果消息
  `✔ 通过  {0}s  {1} tokens  回复：「{2}」` / `✖ 失败  {0}s：{1}`。
- SessionSidebar：标题 `会话列表`、`新对话`、右键菜单（重命名/会话设置…/设置工作
  空间/清空消息记录/删除）、重命名对话框、工作空间目录选择、删除确认、会话项 tooltip
  （`ID: {0}\n创建：{1}\n工作空间：{2}`、`不限制`）。
- AIChatPanel 工具栏/输入/错误：`重命名会话`、`会话设置`、模型状态说明、`⚙ 全局设
  置` 及 tooltip、输入占位、`发送`/`⏹ 停止`/`停止中…`、`会话设置（…）\n当前工作
  空间：{0}`、`工作空间：{0}`、`错误：{0}`、模型下拉分组 `▶ 显示名`。
- 打字指示器：`{0}  执行工具：{1}{2}` / `{0}  生成回复中…` / `{0}  AI 思考中…`。

### 3. 有意的例外（决策记录）

保留为中文（直接进入 LLM 上下文/提示词，非界面文案，翻译会破坏语义）：
- `DEFAULT_SYSTEM_PROMPT`（ai/tools.py，未改）
- `## 长期记忆` memory 注入
- `未知工具：`、`工具参数 JSON 解析失败：`、`路径无效：`、`路径超出工作空间限制：…`

不翻译：
- usage 标签 `↑N ↓N  ∑N`（无中文）
- provider 表单字段英文文案（`API Base URL` / `API Key` / `Model` / `Region` /
  `Temperature:` 等）
- `▲ 展开` / `▼ 收起` 折叠按钮、`…` 测试中初始态、emoji（✏ ⚙ 📊 ⏹ ✔ ✖ ⚙）
- 模型名 / 会话名 / token 计数等业务值
- 注释与 logger 文本（非用户可见）

### 4. 接管既有词条

5 个 source 已存在于既往交接表，本次不重复交接，仅补测试侧英文 patch：
`测试连接`（阶段 3 dialogs）、`全选`/`全不选`/`删除`（阶段 3 main，当前为占位）、
`确认删除`（阶段 2）。

## 交接表

`docs/i18n-phase4-ai-translations.json`：57 条 = AI 面板 AST 提取的 62 个唯一
source − 既有表 5 条。全部英文译文；含 `{N}` 的 source 保证译文占位符集合一致；
无 `{N:.x}` 格式说明符（数值先 `f"{...:.1f}"` 预格式化）。也校验交接表 source 不含
Anthropic / OpenRouter / DeepSeek / Vertex AI / GitHub Copilot / api_base /
api_key / aws_region 等业务值。

## 测试

`catia_copilot/tests/test_i18n_ai.py`，17 用例：

1. AST 防线：全部 `translate` 的 context/source 为字符串字面量、context 恒为
   `CATIACopilot`、source 含中文；关键词条存在。
2. 交接表：与 AST 新 source 双向一致；译文非空、≠ source、占位符集合一致、无格式
   说明符、无中文残留；与业务值隔离。
3. 真实 `pyside6-lupdate`：从 `ai_chat_panel.py` 提取命中全部 57 条。
4. 真实 `pyside6-lrelease` + `QTranslator`：抽样词条实际返回英文。
5. 行为（offscreen）：`AISettingsDialog` / `SessionSidebar` /
   `_TypingIndicatorWidget` / `AIChatPanel` 中英文案切换；会话列表 tooltip 中英；
   会话目录沙箱化（patch `session_manager._SESSIONS_DIR`/`_INDEX_PATH` 至临时目录），
   不触碰真实 `%APPDATA%\CATIA Copilot` 数据；全部 `close + deleteLater +
   processEvents` 清理。

## 验证

```
$env:PYTHONIOENCODING="utf-8"; $env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s catia_copilot/tests -t .
196 tests OK（= 上一里程碑 179 + 本次 17），退出码 0
```

## 后续待办（不在本次范围）

- `ui/session_config_dialog.py`（会话设置对话框，AIChatPanel 子对话框）的中文文案
  （`会话设置`、`工作空间：`、`清空消息记录`、`选择工作空间目录` 等）未翻译，需另行
  任务。
- 全部交接表（phase1/2/3/4）尚未合并进正式 TS/QM —— 阶段 5 构建编译统一处理。
- 阶段 3 PLM 主类 Tab 1 设置页遗留未翻译（历史记录，见阶段 3 报告）。