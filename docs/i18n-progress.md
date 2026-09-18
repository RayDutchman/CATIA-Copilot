# i18n 实施进度

## 阶段性检查点（2026-09-17）

- 分支：`feat/i18n`，起点提交 `97a859d`。
- 开始实施时工作区并非全干净：已有 `part_templates/Part_Template.md` 修改、未跟踪的中文模板对比文档及已批准的两份 i18n 计划。模板文档不纳入 i18n 提交。
- 本次为阶段性检查点，不代表 i18n 全量可用；不推送、不创建 Release。

## 已实现

1. PySide6 QTranslator 基础设施、启动接线、语言设置（system/zh_CN/en_US，重启生效）、Qt 标准控件中文翻译和加载失败回退。
2. 主窗口与嵌入菜单的运行时文案工厂；阶段 1 TS 各 199 条，QM 为忽略的编译产物。
3. BOM V3、质量特性、导出及工具对话框的文案标记；Source 使用 itemData 保存 0/1/2，撤销重做保持原值。
4. 质量特性实际 Excel/CSV 导出路径与界面翻译隔离，英文界面仍导出中文表头。
5. 阶段 2 英文译文交接清单共 657 条（包含阶段 1），已通过真实 lupdate 提取的双向覆盖检查。

## 验证及未完成项

- 主 agent 在本次检查点运行：`python -m unittest discover -s catia_copilot/tests -t . -v`，设置 `QT_QPA_PLATFORM=offscreen`；72 测试通过，进程退出码 0。
- 子 agent 此前报告的单跑 BOM 测试退出阶段 `0xC0000374` 崩溃：已在 pytest 模式下稳定复现，判定为 Qt 对象生命周期问题并经清理修复（见下节验证记录），不再视为未解决。
- 阶段 2 的 657 条尚未统一合并至正式 TS/QM，当前生产翻译目录仍只有阶段 1 的 199 条。
- 提交前 diff 复核发现的 `convert_dialog.py` 对调用方传入的 `file_label` / `_no_files_msg` 二次 translate 问题：已修复为调用方翻译字面量、被调用方直接显示，并补提取约束断言（见下节）。
- 阶段 3 PLM：仅完成调查与任务拆解，尚未修改 `plm/sync.py` / `ui/plm_workbench.py`。
- 阶段 4 AI 已在 2026-09-18 完成；帮助与 README 已于 2026-09-18 完成（见下文 i18n Phase 4 一节）；
  AI 附属对话框（会话设置 / 模型状态 / 日志窗口）已于 2026-09-18 完成（见文末）。
- 阶段 5 翻译目录已合并收尾（见文末「i18n Phase 5」一节）；构建自动编译 QM 流水线与安装器双语尚未开始。
- 安装包/CATIA/PLM 真实操作验收尚未执行。

## 收尾修复（2026-09-17 下午）

1. **convert_dialog 双重翻译**：去掉对 `file_label` / `self._no_files_msg` 的二次 translate
   （`main_window.py` 调用方已翻译字面量；`title`/`note` 同类接口本就直接使用，未动）。
   新增 `catia_copilot/tests/test_convert_dialog_i18n.py`：
   - AST 断言：`convert_dialog.py` 内所有 `translate()` 的 context/source 实参必须为字符串字面量，禁止变量/属性表达式；
   - 行为断言：mock translate 后 `file_label` 必须原样显示、空文件列表警告必须原样使用 `no_files_msg`。
2. **BOM i18n 测试退出崩溃 0xC0000374**：在 pytest 模式下稳定复现（unittest 模式不崩）。
   最小化验证确认为 Qt 对象生命周期问题——`BomEditDialogV3` 构造的大量 Qt 顶层对象
   （QShortcut/QSettings/控件图等）拖到进程退出期由 GC 销毁，触发堆损坏；
   显式 `close + deleteLater + processEvents` 提前完成 C++ 侧析构可消除。
   修复：引入 `_BomI18nTestCase` 基类统一管理用例创建的 dialog/combo，tearDown 显式清理；
   未用 skip / os._exit 掩盖。

验证记录（Windows 原生、QT_QPA_PLATFORM=offscreen）：
- `python -m unittest catia_copilot.tests.test_bom_edit_dialog_i18n`：连续 3 个独立进程退出码均 0；
- `python -m pytest catia_copilot/tests/test_bom_edit_dialog_i18n.py -q`：修复前退出码 -1073740940（0xC0000374）→ 修复后 0（14 passed）；
- `python -m unittest discover -s catia_copilot/tests -p "test_*.py"`：77 tests OK，退出码 0。

## 后续派发粒度

- 不再将整个阶段交给一个实施 agent。
- 单任务限定一个窗口，或一个可独立验证的数据链路；跨文件契约先固定再派发。
- PLM 拆为：事件生产端稳定 code → UI 消费与计数 → 窗口文案；每步独立审查。
- 翻译目录合并、测试退出问题、英文 README、帮助资源、AI UI、构建校验分别派发。
- 所有任务以文件形式交接报告，返回摘要；达到约定范围后开启新的 agent 上下文。

## i18n Phase 3 Task 3.3 — PLM 四对话框（2026-09-18 完成）

- **范围**：`catia_copilot/ui/plm_workbench.py` 内 `_SettingsDialog` / `_HistoryDialog` /
  `_AttachmentDialog` / `_PullDialog` 四个独立对话框的全部可达中文文案 →
  `translate("CATIACopilot", "字面量").format(...)`；新增五对话框表头渲染工厂
  （`_tags_table_header_display` / `_rules_table_header_display` /
  `_history_table_header_display` / `_attachment_table_header_display` /
  `_pc_header_display`）。业务值（历史 `sync_mode`、附件文件名、BOM 行数据、`_PC_HEADERS`
  列常量等）保持原值不翻译。
- **交接**：新增 `docs/i18n-phase3-dialogs-translations.json`，96 条新词条（= 对话框
  AST 提取 − main 160 条 − ui 15 条）均以英文译文交接；复用 15 条既有词条不重复交接；
  含 `.format()` 的目录消息英文译文用 `{{Part Number}}` 双花括号转义，防止
  `KeyError`（测试强制）。
- **测试**：新增 `catia_copilot/tests/test_i18n_plm_dialogs.py`（36 用例）：AST 字面量
  防线、交接表双向覆盖/英文性/花括号安全、**真实** `pyside6-lupdate` 提取全键命中、
  **真实** `pyside6-lrelease` 构建 `.qm` 经 `QTranslator` 加载出英文、行为测试（mock
  `PlmApiClient` 假网络加载附件、mock `QMessageBox`、offscreen 构造 + close/deleteLater/
  processEvents 清理保证退出码 0）。
- **主类清理**：复核发现 `_build_settings_tab` / `_on_conn_ok` 遗留 16 处未交接 translate
  （不属于任何交接表，破坏 `test_i18n_plm_main` 主表一致性），已按 HEAD 原样回退为中文
  字面量；四对话框内同名词条不受影响。主类 Tab 1 设置页整体仍未翻译，留待后续任务。
- **验证**：`pytest catia_copilot/tests/ -q` **156 passed，退出码 0**。
- 报告：`docs/i18n-phase3-dialogs-report.md`。未提交 commit/push。

## i18n Phase 4 — 帮助与 README（2026-09-18 完成）

- **范围**：`catia_copilot/ui/help_dialog.py` + `README.md` / `README.zh-CN.md`；
  不涉及 AI / TS / 构建脚本，未 commit / push / stash / reset。
- **帮助按语言走资源**：`_HELP_HTML`（中文源文档）原样保留，新增 `_HELP_HTML_EN`
  （英文全量译版，同文件内不新增资源），`_help_html()` 依 `current_ui_language()` 选择
  （`en_US` → 英文，其余回退中文）。窗口标题与关闭按钮走固定字面量 translate：
  `translate("CATIACopilot", "{0} — 帮助文档").format(APP_NAME)`、
  `translate("CATIACopilot", "关闭")`。CATIA 真实标识（`analyze.*`、
  `惯量包络体.N`、`质量`、`密度`、`ChangFangSong.ttf`、`ISO.xml`、`.mpd`、
  `.catvbs`、自定义属性名物料编码等）英文版一律原样保留；CN/EN 结构计数完全一致
  （h2=1 / h3=5 / h4=15 / table=15 / ul=16 / ol=2 / img=1，含图片路径与样式）。
- **交接**：新增 `docs/i18n-phase4-help-translations.json`，1 条新词条
  `"{0} — 帮助文档" → "{0} - Help Documentation"`；`关闭`→`Close` 已收录于阶段 2/3
  交接表，不重复交接。
- **README**：README.md 英文、README.zh-CN.md 中文在各自文件内的运行/安装/发行/
  联系内容一致，两份首行均含双向语言链接；更新 help_dialog 属性名行号引用为
  76–77、590–591。
- **测试**：新增 `test_help_i18n.py`（13 用例）与 `test_readme_i18n.py`（10 用例）：
  AST 字面量防线、CN/EN 结构一致、英文版残留中文 token 白名单、26 个真实标识保留、
  真实 `pyside6-lupdate` 提取命中交接表、真实 `pyside6-lrelease` 构建 `.qm` 经
  `QTranslator` 输出英文标题/Close、offscreen 行为（zh 回退 / en_US 切换）、README
  双语一致性（版本 2.2.0 / 日期 / 作者 / 命令）与跨文档行号引用有效性。
- **验证**：`python -m unittest discover -s catia_copilot/tests -p "test_*.py"` 与
  `python -m pytest catia_copilot/tests/ -q -p no:cacheprovider` 均为
  **179 passed（= 阶段 3 156 + 本次 23），退出码 0**。
- 报告：`docs/i18n-phase4-help-readme-report.md`。未提交 commit/push。

## i18n Phase 4 — AI 聊天面板（2026-09-18 完成）

- **范围**：`catia_copilot/ui/ai_chat_panel.py`（含同文件内的 `AISettingsDialog`/
  `SessionSidebar`/`_TypingIndicatorWidget`）；不涉及 `ai/tools.py` 的 schema 名称/
  描述、`session_config_dialog.py`、用户会话历史、API Key/模型 id/配置 key、TS/QM
  构建，未 commit / push / stash / reset。
- **显示名与占位 helper**：新增模块级 `_provider_type_label(ptype)` 与
  `_cred_placeholder_display(ph)`——在函数体内固定字面量 translate（避免 import 期
  翻译），纯英文 provider 名（Anthropic/OpenRouter/DeepSeek/AWS Bedrock/Google
  Vertex AI/GitHub Copilot）与纯英文占位（`API Base URL` 等）原样返回，
  `Ollama（本地）`/`OpenAI / 兼容`/`讯飞星火`/`自定义端点` 及三条中文凭证占位随
  界面语言翻译。
- **保留为中文（面向 LLM / 提示词，非 UI 文案）**：`DEFAULT_SYSTEM_PROMPT` 系统
  提示词（ai/tools.py 内，未动）、`## 长期记忆` memory 注入、工具结果错误
  `未知工具：` / `工具参数 JSON 解析失败：` / `路径无效：` / `路径超出工作空间限制`
  等——这些字符串直接进入对话上下文而非界面，英文界面下保持中文以免破坏提示词语义
  （报告已记录决策）。
- **不翻译**：usage 标签 `↑… ↓…  ∑…`、provider 表单字段文案
  （`API Base URL`/`API Key`/`Model`/`Region`/`Temperature:` 等）、`▲ 展开`/`▼ 收起`
  折叠按钮、`…` 测试初始态、emoji 图标、模型/会话名等业务值。
- **格式安全**：数值类消息（`✔ 通过  {0}s  {1} tokens…`、`✖ 失败  {0}s：{1}`）
  先 `f"{elapsed:.1f}"` 预格式化再 `.format()`，source/译文不含 `{N:.x}` 格式说明符
  （测试强制）。
- **交接**：新增 `docs/i18n-phase4-ai-translations.json`，57 条（= AI 面板 AST 提取
  的 62 个唯一 source − 既有交接表已收录的 测试连接/全选/全不选/删除/确认删除 5 条），
  全部英文译文；`{N}` 占位符集合与 source 一致。
- **测试**：新增 `catia_copilot/tests/test_i18n_ai.py`（17 用例）：AST 字面量防线、
  交接表双向覆盖/占位符一致/译文无中文/业务值隔离、真实 `pyside6-lupdate` 提取全键
  命中、真实 `pyside6-lrelease` 构建 `.qm` 经 `QTranslator` 输出英文、offscreen 行为
  （AISettingsDialog / SessionSidebar / _TypingIndicatorWidget / AIChatPanel 中英
  切换），会话目录用 patch 沙箱化到临时目录，避免读写真实 `%APPDATA%` 会话数据。
- **验证**：`python -m unittest discover -s catia_copilot/tests -t .` 为
  **196 passed（= 上轮 179 + AI 17），退出码 0**。
- 报告：`docs/i18n-phase4-ai-report.md`。未提交 commit/push。

## i18n Phase 4 — AI 附属对话框（2026-09-18 完成）

- **范围**：`ui/session_config_dialog.py` / `ui/model_state_dialog.py` / `ui/log_window.py`
  三个 AI 可达附属 UI；不涉及 `ai/tools.py`（schema/提示词）、`ai_chat_panel.py`、
  用户会话历史与配置、TS/QM 构建，未 commit / push / stash / reset、未改 TS、未派 agent。
- **业务判断原值保持**：`session_config_dialog` 默认模型下拉显示文本随语言翻译，但
  `_apply_and_accept` 的 `text.startswith("使用全局默认")` 判定保持中文原值；
  `model_state_dialog` 状态字段（`part_name`/`features`/`step` 名）为业务数据原样展示，
  `status == "ok"` 判定不翻译，`✓`/`✗` 仅显示映射；`mass_kg`/`cog_mm` 数值与单位原样。
- **不翻译**：`log_window` 窗口标题 `CATIA Copilot 1.4.1 – Log`（英文文案，含过时版本号）、
  日志路径标签 `Log: {LOG_FILE}` 前缀；`model_state` 的 `—` 空值占位符。决策记录：
  `model_state` 根节点 `零件几何体` 为硬编码显示标签 → `Part Body`（子节点特征名保持原值）。
- **交接**：新增 `docs/i18n-phase4-ai-dialogs-translations.json`，29 条新词条
  （= 三文件 AST 提取 37 个唯一 source − 既有表 8：`会话设置`/`清空消息记录`/
  `选择工作空间目录`/`浏览…`/`关闭`/`打开日志文件`/`无法打开日志文件`/
  `无法打开日志文件：\n{0}\n\n{1}`），全部英文译文，占位符集合与 source 一致。
- **测试**：新增 `catia_copilot/tests/test_i18n_ai_dialogs.py`（17 用例）：AST 字面量
  防线、交接表双向覆盖/占位符一致/业务值隔离/判定原值保持、真实 `pyside6-lupdate`
  三文件合并提取全键命中、真实 `pyside6-lrelease` 构建 `.qm` 经 `QTranslator` 输出英文、
  offscreen 行为（三对话框中英切换 + `_apply_and_accept` 业务写回归）；`ai_config` 读取
  mock、`ModelStateDialog._settings` 重定向临时 ini（防注册表污染）、`log_window` 导入期
  `Path.home` 重定向临时目录（防真实 `~/CATIA_Copilot/logs` 污染），全部 close/deleteLater/
  processEvents 清理，正常销毁。
- **验证**：`python -m unittest discover -s catia_copilot/tests -t .` 为
  **213 passed（= 上轮 196 + 本次 17），退出码 0**；无真实 COM/网络/持久化污染。
- 报告：`docs/i18n-phase4-ai-dialogs-report.md`。未提交 commit/push。

## i18n Phase 5 — 全量翻译目录合并收尾（2026-09-18 完成）

- **范围与约束**：仅修改 `resources/i18n/*.ts/.qm`（TS 可提交，`*.qm` gitignore）、
  `docs/i18n-progress.md` 与本次新建的 `docs/i18n-phase5-report.md`；不改业务源码、
  不 commit / push / stash / reset、不派 agent。
- **交接表合并**：读取全部 8 个 `docs/i18n-phase*-translations.json`
  （phase1=200、phase2=657、phase3-dialogs=96、phase3-main=160（占位表，全部键值
  同串）、phase3-ui=15、phase4-ai=57、phase4-ai-dialogs=29、phase4-help=1），
  去重后 **1003 个唯一 source**，其中 `_说明` 元数据键已剔除；跨表冲突 11 条，已按
  PLM 表头/业务语境结合既有测试断言解析（如 `状态→Status`、`类型→Type`、
  `零件编号→Part number`、`浏览…→Browse…`），未覆盖 phase1 既有「就绪→Ready」。
- **真实 lupdate 提取**：显式 18 文件生产清单（main.py + 17 个 `catia_copilot/**/*.py`，
  排除 tests /`_archive`/mypdm），命令
  `pyside6-lupdate -extensions py -no-obsolete <18 文件> -ts <ts>`，
  en_US 与 zh_CN 各成功一次（exit 0）：**1002 source texts（803 new + 199 already
  existing）**，与交接表合并集严格相等（TS−merged=0 缺少，merged−TS=0 多余）。
- **填充**：en_US.ts 803 条待译全部填充英文，含 phase3-main 占位表中 151 条手工翻译
  （占位符 `{N}` 与 source 全量一致），故意保持原值 10 条（`CATIA V5 ✅`、`3DEXPERIENCE ⚠️`、
  `Ixx/Ixy/Ixz (kg·mm²)` 等 8 个包含 & 5 个单位词条、`*.CATProduct (*.CATProduct);;All Files (*)`
  QFileDialog 过滤器、`{0} ({1})` 通用格式包装）；zh_CN.ts 803 条全部 identity 填充并去除
  `type="unfinished"`。两组均 **0 unfinished / 0 空（各 1002 条）**。
- **lrelease**：`pyside6-lrelease` 分别生成 en_US/zh_CN `.qm`（exit 0），各
  **1002 finished / 0 unfinished**。
- **验证**：
  - TS 与 lupdate 提取源集合：**0 缺少 / 0 多余**；全部 source/translation 的 `{`/`}` 计数一致；
  - 真实 `QTranslator` 加载 en_US.qm（context `CATIACopilot`）：`取消→Cancel`、`确定→OK`、
    `状态→Status`、`类型→Type`、`零件编号→Part number`、`浏览…→Browse…`、`版本→Version`、
    `就绪→Ready` 及长句/富文本词条（含 `\n`/`<b>`/`　`）均正确英文；未收录词回退中文 source；
    zh_CN.qm 加载返回中文 identity；
  - 回归：i18n/lupdate/mass_props 相关测试 **78 passed**（含 lupdate 提取、QTranslator 断言、
    中英窗口文本、表头/业务数值稳定）。
  - 已知环境问题：pytest 按 `-k "i18n or lupdate or mass_props"` 跑全 tests 目录时，全部用例
    PASSED 之后的进程退出阶段偶现原生崩溃（0xC0000409），用 phase1 基线 TS 复现同样崩溃，
    判定为 PySide6 在 Python 3.13 下的 teardown 问题，与本次 TS 改动无关；显式指定 5 个测试
    文件单跑退出码 0。
- 报告：`docs/i18n-phase5-report.md`。未提交 commit/push。

## i18n Phase 6 — 构建流水线接入（2026-09-18 完成）

- **范围与约束**：仅修改 `build_nuitka.ps1` / `build_nuitka_installer.ps1` /
  `setup.iss` / `.github/workflows/release.yml` 四个构建相关文件，新建
  `scripts/verify_translations.py`、`catia_copilot/tests/test_translation_catalog.py`
  与 `docs/i18n-phase6-build-report.md`，更新本文档；不改业务源码与 `i18n.py`，
  不 commit / push / stash / reset、不派 agent。
- **校验脚本** `scripts/verify_translations.py`（内置 18 文件 `PRODUCTION_FILES`
  生产清单 = Phase 5 实证清单，占位符规则先剥 `{{...}}` 字面转义再比 `{N}` 集合）：
  对 en_US / zh_CN 两份 TS 检查词条完整性、空 / unfinished、花括号与占位符一致；
  再经真实 `pyside6-lupdate` 提取 18 文件与 TS 双向比差。实跑退出码 0：
  `1002 / 1002 条，空 0，unfinished 0，lupdate 缺少 0 / 多余 0`。
- **构建脚本**（两个 ps1 相同两块）：
  - 预编译块：先跑 `verify_translations.py`（非 0 即中止）→ `pyside6-lrelease`
    重编 en_US / zh_CN 两个 `.qm`（非 0 即中止）→ 以 `python -c` 定位 PySide6
    自带 `qtbase_zh_CN.qm`（缺失即 exit 1）；
  - 产物块：在产物目录探测 `_internal\PySide6\translations` 或 `PySide6\translations`
    （兼容 standalone / onedir 新旧布局）后 `Copy-Item -Force` 复制
    `qtbase_zh_CN.qm`（均缺即 exit 1）。
- **安装器双语**：`setup.iss` 的 `[Languages]` 保留 `chinesesimplified`
  （ChineseSimplified.isl）放首位维持默认，新增 `english`（`compiler:Default.isl`）；
  注释明确安装向导语言与应用内 `QSettings` 语言彼此独立、不做联动。
- **CI**：`release.yml` 在「Verify release version」与「Install Inno Setup」之间插入
  「Verify translations」（`python scripts/verify_translations.py`）与
  「Run unit tests (offscreen)」（`QT_QPA_PLATFORM=offscreen` 下
  `python -m unittest discover -s catia_copilot/tests -p "test_*.py"`）。
- **验证**（Windows 10/11 原生、PS 5.1）：
  - `Parser.ParseFile` 两 ps1 连续多次均 0 错误；
  - `python -m py_compile` + `verify_translations.py` 退出码 0；
  - 全套单测 `python -m unittest discover -s catia_copilot/tests -p "test_*.py"`
    **223 passed = 上轮 213 + 新增 10（test_translation_catalog），退出码 0**；
  - `pyside6-lrelease` en_US / zh_CN 各 1002 finished / 0 unfinished，退出码 0；
  - `iscc /Q /O- /DAppVersion=… /DSourceDir=… setup.iss` 预编译退出码 0。
- **编码约定**：仓库文本统一 UTF-8 无 BOM + CRLF；新建 ps1 块的日志消息采用 ASCII
  英文以规避 Windows PowerShell 5.1 对长多字节段的解析抖动（CI 用 pwsh 不受影响），
  原有中文消息原样保留。
- 报告：`docs/i18n-phase6-build-report.md`。未提交 commit/push、未发 Release。

## 最终收尾（2026-09-18）

- 阶段 3 PLM 主工作台、四个附属对话框、结构化事件生产/消费已完成。
- 阶段 4 帮助、AI 主面板、AI 附属窗口、英文 README / 中文 README 已完成。
- 阶段 5 翻译目录已合并：en_US/zh_CN 各 1002 条，0 空译文、0 unfinished、占位符一致。
- 阶段 6 构建接入已完成：Nuitka 构建前校验和 lrelease、Qt 基础翻译复制、Inno Setup 中英安装语言、GitHub Actions tag 发布。
- 最终审查遗漏已修复：Pull 全选按钮、历史清空确认框、质量特性文件过滤器均已纳入翻译；可达 UI 裸中文 AST 守卫已加入。
- 阶段测试最终结果：全量 `unittest discover` **242 tests OK，退出码 0**；主包语法与关键模块导入通过。
- 仍需真实环境验收：CATIA COM、DocDoku PLM、Nuitka 安装包在目标机器上的启动和运行；pytest/PySide6 Python 3.13 的退出阶段原生崩溃曾出现过，标准 unittest 全量退出码已为 0。

## i18n 收尾修复（2026-09-18 下午）

可达 UI 复核遗留三处显示层问题修复；数据层/业务判定不变，未 commit / push / stash / reset。

1. **mass_props 镜像行显示层**（`mass_props_dialog.py` `_make_item`）：镜像 PN / 实例名 ` (对称件)` 后缀与虚拟文件名 `(虚拟)` 改渲染时段 `translate`，数据行（`_rows` 与导出内容）恒中文。
2. **bom 右键「填充」菜单列名插值**（`bom_edit_dialog_v3.py`：3271）：`_fill_col_display` 由静态 `BOM_COLUMN_DISPLAY_NAMES` 残留改用运行时 `bom_column_display(fill_col_name)`，英文界面下填充菜单不再残留中文列名；`_export_header` 仍走既有映射（导出表头恒中文，不变）。
3. **session_config 默认模型判定**（`session_config_dialog.py` `_apply_and_accept`）：`text.startswith("使用全局默认")` 中文前缀依赖 → `text == self._model_combo.itemText(0).strip()`，显示文本随语言翻译仍判定正确。
- **测试**：`test_mass_props_i18n` 新增 `TestMirrorRowDisplayLayer`（zh 回退 / en 显示翻译且数据层不变）并在导出断言加入镜像行恒中文；`test_bom_edit_dialog_i18n` 新增 `TestFillMenuUsesRuntimeDisplay`（源码 AST 守卫 + FakeMenu 行为，zh=首行内容填充（零件编号）/ en=Fill from first row (Part number)）；`test_i18n_ai_dialogs` 判定断言改 `itemText(0)` + 新增默认/自定义模型行为测试。
- **TS/QM**：`pyside6-lupdate` 18 文件合并新增 2 source（1002→**1007 条**），en_US 补 ` (Mirror)`/`(Virtual)`、zh_CN identity，两侧 0 unfinished / 0 空；`pyside6-lrelease` 各 1007 finished / 0 unfinished。
- **交接清单同步**：`docs/i18n-phase2-translations.json` +2 键（保持 `test_lupdate_extraction` 双向一致）；`docs/en_phase2_extra.json` +2 键。
- **验证**：全量 `python -m unittest discover -s catia_copilot/tests -t .` **249 tests OK，退出码 0**；`python scripts/verify_translations.py` **退出码 0**（TS/Lupdate 双向 0 缺 / 0 余）。
