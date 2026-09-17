# i18n Phase 1 实施报告

本报告记录 `docs/i18n-implementation-plan.md` Phase 1 的落地情况、对计划的偏差修复、
测试结果、main.py 接线建议与待办事项。

## 一、已实施内容

| 文件 | 状态 | 改动要点 |
|------|------|----------|
| `.gitignore` | 修改 | `tests/` → `/tests/`（仅忽略仓库根目录，避免误屏蔽包内测试目录）；追加 `*.qm` 忽略规则（翻译二进制不入库） |
| `catia_copilot/tests/__init__.py` | 新建 | 空包标记，使 `unittest discover -s catia_copilot/tests` 可导入 |
| `catia_copilot/tests/test_smoke.py` | 新建 | 4 个包级冒烟：`APP_NAME`/`APP_VERSION`/`resource_path` 解析 |
| `catia_copilot/i18n.py` | 新建 | 翻译基础设施，见下 |
| `catia_copilot/tests/test_i18n.py` | 新建 | 11 个用例：解析/设置读写/回退/翻译加载/标准按钮 |
| `catia_copilot/tests/test_action_labels.py` | 新建 | 3 个用例：14 键与中文默认、`connection_states()` 键集、en_US 翻译生效 |
| `catia_copilot/tests/test_lupdate_extraction.py` | 新建 | 3 个用例：真实 `pyside6-lupdate` 提取回归，含与交接清单键双向一致断言（审查整改） |
| `docs/i18n-phase1-translations.json` | 新建 | 一次性译文交接清单（中文源→英文，含占位符与 `<b>`/多行拼接串），供主 agent 合并 TS |
| `catia_copilot/tests/test_language_ui.py` | 新建 | 3 个用例：设置区控件、保存跳转、回填 |
| `catia_copilot/ui/main_window.py` | 修改 | `action_labels()`、新增 `connection_states()`；主窗口全部发行可达 UI 文案（tab 标题/状态栏/连接指示/完整诊断对话框/各 tab 节标题与 tooltip/全部对话框启动器与消息）改为运行时 `translate`；`_build_more_page` 新增「设置」节 |
| `catia_copilot/ui/catia_embed.py` | 修改 | Win32 菜单全部文字改为运行时 `action_labels()` + `translate(...)` 回退；`catia_copilot.catvba` 模块名/宏文件名不翻译 |

### i18n.py 能力

- `translate(context, source)`：`QCoreApplication.translate` 包装，缺翻译回退中文源文本。
- `read_language()` / `write_language()`：`QSettings("CATIACopilot", "Application")`、键 `language`，
  默认 `"system"`；三者取值 `system | zh_CN | en_US`。
- `resolve_ui_language()`：先 `langOverride`，再按系统语言族决定；`system` 下非简体中文（`zh*` 之外的
  所有语言）一律落到 `en_US`；`zh_CN` 之下 `en_US`。
- `install_translators(app)`：**返回 `tuple[QTranslator | None, QTranslator | None]`（应用翻译器, Qt 基础翻译器）**。
  - `zh_CN`：不装应用翻译器（中文即源文本），额外安装 Qt 自带的 `qtbase_zh_CN.qm`，使
    `QMessageBox`/`QDialog` 标准按钮（确定/取消/保存等）中文化。
  - `en_US`：加载 `resources/i18n/catia_copilot_en_US.qm`；文件缺失或加载失败仅 `warning`，回退
    `_current_ui_lang = "zh_CN"` 并仍安装 `qtbase_zh_CN.qm`（标准按钮保持中文化）；仅成功加载目标
    qm 才记录 `en_US`。不崩溃。范围扩大后经审查整改，见 §二 9。
  - `system`：按系统语言同 `zh_CN`/`en_US` 处理。
  - 两个翻译器返回值需由调用方持有引用（应用翻译器），否则会被垃圾回收导致翻译失效。

## 二、对计划的疑点修复（与计划的偏差）

1. **`install_translators` 返回单翻译器不足**：单个 `QTranslator | None` 无法同时覆盖应用文案与
   Qt 标准按钮文案，已改为返回二元组。
2. **假测试**：计划 smoke 示例 `1+1=2` 属无意义断言，按用户要求改为真实行为测试
   （常量值、资源路径解析）。
3. **`findChildren(type(w))` 写法错误**：`findChildren` 不接受类实例实时参数化搜索目标按钮，测试改用
   `findChild(QPushButton, "btnSaveLang")` / `findChild(QComboBox, "cmbUILang")`。
4. **计划 KEY_SUBSET 含虚构键**：`open_workspace`/`commit_all`/`drawing_export` 在 `_ACTION_LABELS` 中
   并不存在，测试按实际的 14 个键断言，保证 key 集合稳定。
5. **`smoke_test.py` 命名不匹配 discover 默认 `test*.py`**：实测 `unittest discover` 会遗漏该文件
   （Ran 0 tests），已改名 `test_smoke.py`。
6. **`pyside6-lupdate` 目录模式不生效**：实测 `<lupdate> catia_copilot -ts …` 提取 0 词条；必须显式
   `-extensions py` 才递归扫描。文档中留作生产提取命令提示。
7. **`git check-ignore tests/` 返回非忽略**：空目录参数为 git 边界行为；以实际文件验证
   `tests/foo.txt` 命中 `/tests/`，`catia_copilot/tests/__init__.py` 不再被忽略，规则语义正确。
8. **测试隔离增强**：`test_language_ui` 将 `main_window` 与 `i18n` 的 `QSettings` 都注入临时 Ini，
   不污染用户注册表；`check_catia_connection` 打桩避免 COM 探测；`test_i18n` 使用
   `QApplication`（非计划的 `QCoreApplication`），因为标准按钮文本断言需要 widget 环境。
9. **审查整改：`install_translators` 回退语义缺陷**：原实现 en_US qm 缺失时不记录语言也不装 qtbase，
   会「中英混排」。现统一为：缺失/加载失败 → `_current_ui_lang = "zh_CN"` 且仍装 `qtbase_zh_CN.qm`；
   仅成功加载目标 qm 才记录 `en_US`。测试断言 `tr[0] is None`、`tr[1] is not None`、
   `current_ui_language() == "zh_CN"`。
10. **审查整改：主窗口仅部分文案可翻译**：Phase 1 完工时只覆盖动作标签与设置区；现补齐状态栏、
    tab 标题、连接状态指示器、完整 CATIA 连接诊断对话框、各 tab 节标题与 tooltip、全部对话框
    启动器与消息（含 `_copy_file_to_catia`、`_crack`、宏执行、生成/刷新图纸等）。
11. **审查整改：`AI_TAB_LABEL` 常量无法翻译**：tab 文案改为调用点 `translate("CATIACopilot", "AI 助手")`，
    `_on_tab_changed` 同步取当前语言；`ABOUT_TEXT` 保留（constants.py 属主 agent / Phase 2）。
12. **审查整改：lupdate 提取形式无回归保障**：新增 `test_lupdate_extraction.py`，用临时文件调用真实
    lupdate，固化「显式 `-extensions py` + 显式枚举真实源码文件、不枚举 tests/」的提取形式
    （lupdate 无 `-exclude` 选项）。
13. **审查整改：设计文档提取命令含 tests 路径**：`docs/i18n-design.md` §2.4 修正为显式枚举
    `main.py` / `catia_copilot/i18n.py` / `catia_copilot/ui/main_window.py` / `catia_copilot/ui/catia_embed.py`。

## 三、验证结果

### 单元测试

```
python -m unittest discover -s catia_copilot/tests -t . -v
Ran 24 tests ... OK
```

- test_smoke 4 · test_i18n 11 · test_action_labels 3 · test_language_ui 3 · test_lupdate_extraction 3 —— 全部通过。
- en_US 翻译与 Qt 标准按钮用例使用 `pyside6-lrelease` 动态编译 mini.qm（临时目录），不依赖预置 qm。

### lupdate 提取验证

```
pyside6-lupdate -extensions py catia_copilot -ts <tmp>/i18n_phase1_verify.ts
Found 22 source text(s)
```

22 条中 1 条为 `test_i18n.py` 的测试词条（「绝不会在 qm 中存在的词条」，验证缺翻译回退链），
**生产词条 21 条**全部位于 `<context><name>CATIACopilot</name></context>` 下：

| # | 中文源文本 | # | 中文源文本 |
|---|-----------|---|-----------|
| 1 | BOM 工作台 | 12 | 快速装配托板螺母 |
| 2 | 从产品导出 BOM | 13 | 在图纸/零件间切换 |
| 3 | 质量特性工作台 | 14 | 查找指向的文档 |
| 4 | PLM 工作台 (DocDoku) | 15 | 运行宏… |
| 5 | 从图纸导出 PDF | 16 | 设置 |
| 6 | 从产品/零件导出 STP | 17 | 界面语言 |
| 7 | 新建图纸 (Python) | 18 | 跟随系统 |
| 8 | 刷新图纸 (Python) | 19 | 简体中文 |
| 9 | 刷写零件模板 | 20 | 保存设置（重启后生效） |
| 10 | 快速装配紧固件 | 21 | 提示 / 语言设置已保存，重启程序后生效。 |
| 11 | 在图纸/零件间切换 | | |

> 注：原文案有重复，去重后实际 source 21 条；`English` 选项文案保持原文不参与翻译。

**审查整改后**（按设计文档 §2.4 显式文件清单重新提取）：

```
pyside6-lupdate -extensions py -no-obsolete main.py catia_copilot/i18n.py \
                catia_copilot/ui/main_window.py catia_copilot/ui/catia_embed.py \
                -ts <tmp>/i18n_phase1_review.ts
Found 199 source text(s)
```

199 条全部位于 `<context><name>CATIACopilot</name></context>` 下；tests/ 因不枚举而不再混入。
该提取形式由 `test_lupdate_extraction.py`（真实 lupdate + 临时目录 + ET 断言）回归固化。

核验中另发现并修复：连接诊断对话框曾用本地别名 `_T = translate` 包裹词条，lupdate 不识别 `_T`，
导致 20 条诊断文案漏提取；改为直接 `translate(...)` 后 199 条与 `docs/i18n-phase1-translations.json`
键完全一一对应（双向交集 0 缺失 0 多余）。

## 四、main.py 接线建议（主 agent 执行）

在 `app = QApplication(sys.argv)` 之后、`MainWindow()` 之前安装翻译器，并保持引用：

```python
from catia_copilot import i18n

def main() -> None:
    ...
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # ── i18n：安装界面语言翻译器（restart 后生效），必须持有返回引用 ──
    _app_tr, _qt_tr = i18n.install_translators(app)   # 局部引用即防止 GC
    window = MainWindow()
    ...
```

- `system`/`zh_CN`（简体系统）下不加载应用翻译器，中文源文本即默认界面；
- `en_US` 或非简体系统且设置 `en_US` 时加载 `resources/i18n/catia_copilot_en_US.qm`；
- Qt 基础翻译器在 `zh_CN`/简体系统下加载 `qtbase_zh_CN.qm`，标准按钮显示中文。

## 五、启动方式与首版边界

- 语言选择只含「跟随系统 / 简体中文 / English」，保存后**重启生效**；
- **首版不含导出语言选择**（计划 Task 1.5 的 cmbExportLang 未实现，属设备语言相关字幕），
  相关测试断言其不出现；
- `part_templates/` 用户文档未触碰。

## 六、Concerns / 遗留

1. `catia_copilot/tests/test_language_ui.py` 会实例化 `MainWindow()`；若运行机
   `QSettings("CATIACopilot","EmbedPanel")` 的 `active=True` 且 CATIA 正在运行，`_toggle_embed`
   可能触发嵌入启动（测试环境已打桩 `check_catia_connection`，实际影响极小）。测试通过后
   `CATIASidebarManager` 随窗口关闭清理。
2. `python -m unittest discover -t .` 在 `tests/` 目录（遗留同名目录若存在）会误扫描旧文件；
   当前 `catia_copilot/tests` 为唯一测试包。
3. ~~生产 TS 提取建议仅扫描真实模块，或对目录扫描后剔除 `tests/` 条目~~ —— 已由审查整改解决：
   设计文档 §2.4 与 `test_lupdate_extraction.py` 固化为「显式枚举真实源码文件、不枚举 tests/」。
   lupdate 无 `-exclude`，目录扫描后剔除不可行，只能靠不枚举。
4. 本阶段未修改 `main.py`，接线由主 agent 合并；
   `resources/i18n/catia_copilot_en_US.qm` 已由集成 worker 从交接词条 TS 编译生成并实机验证
   （见 §八），缺失时程序自动回退中文。
5. 本阶段仅覆盖主窗口动作标签与设置区，未覆盖主窗口标题、工具栏、其余对话框与
   AIChatPanel 等，属 Phase 2+ 范围。

## 七、结论

Phase 1 代码与测试已完成并通过：翻译基础设施、重启生效的语言设置、en_US 翻译加载、
Qt 标准按钮中文化、Win32 菜单共享运行时文案。`main.py` 接线等待主 agent 合并；
正式 TS/qm 交付物已由集成 worker 生成并通过真实生产 qm 校验（见 §八）。

## 八、TS/QM 集成交付（交接词条合并）

由集成 worker 将 `docs/i18n-phase1-translations.json` 的 **199 条交接词条**合并为原生 TS
（一次性工具脚本位于系统临时目录、未入库；仅产出 TS/qm，**不产生运行时 JSON 字典**）。

### 交付文件

| 文件 | 说明 |
|------|------|
| `resources/i18n/catia_copilot_en_US.ts` | source=中文源字面量、translation=交接英文，context 全部为 `CATIACopilot` |
| `resources/i18n/catia_copilot_zh_CN.ts` | 词条全集基准：translation=中文 identity（与代码同步用） |
| `resources/i18n/catia_copilot_en_US.qm` | `pyside6-lrelease` 编译产物（`*.qm` 已被 .gitignore 忽略） |
| `resources/i18n/catia_copilot_zh_CN.qm` | 同上 |

- 两个 TS 均为 `<TS version="2.1" language=… sourcelanguage="zh_CN">`，各 **199 个 `<message>`**，
  上下文 `<name>CATIACopilot</name>`；UTF-8 编码、无 BOM。
- XML 实体经 `xml.sax.saxutils.escape`（`&` `<` `>`）转义；含 `<b>` 的 html 诊断词条在 qm 中还原为
  非转义字面量（富文本语义保留）；`{0}/{1}…` 占位符原样保留。
- 生成脚本做三重校验：199 计数、占位符集合源/译完全一致、TS XML 解析往返与交接清单逐条一致。

### lrelease 结果

```
pyside6-lrelease resources/i18n/catia_copilot_en_US.ts resources/i18n/catia_copilot_zh_CN.ts
Updating '...catia_copilot_en_US.qm'...    Generated 199 translation(s) (199 finished and 0 unfinished)
Updating '...catia_copilot_zh_CN.qm'...    Generated 199 translation(s) (199 finished and 0 unfinished)
```

### 真实生产 qm 校验（临时脚本 + offscreen，不依赖预置 mini.qm）

- **en 安装**：`i18n.install_translators(app, ui_lang="en_US")` 真实加载生产
  `resources/i18n/catia_copilot_en_US.qm`（不 patch `_qm_path`）；`current_ui_language()=="en_US"`；
  `translate("CATIACopilot", "BOM 工作台")=="BOM Workbench"`（与交接清单一致）。
- **全量比对**：en qm 下 199 条逐条 `translate` 结果 == 交接清单英文值，0 缺失 0 差异；
  199 条译文占位符数量与源全部一致；`<b>管理员</b>` 词条译文 `<b>Administrator</b>`，实体往返完整。
- **缺词回退**：`translate("CATIACopilot", "绝不会在 qm 中存在的词条")` 返回源中文。
- **zh 标准按钮**：zh_CN 安装不装应用翻译器（`tr[0] is None`）而安装 `qtbase_zh_CN.qm`；
  QMessageBox OK/Cancel 按钮文本 `确定`/`取消`；中文词条保持 identity。
- **zh_CN.qm 独立加载**：199 条全部 identity 一致。
- QTranslator 引用全程由校验脚本持有（列表持有防 GC）；显式传 `ui_lang=` 避免触发
  `read_language()`，并以「替换 `i18n.QSettings` 为抛错守卫」证明 i18n 层零 QSettings 实例化，
  **未写入任何真实 QSettings**。

### 交付状态

14 项断言全部通过。`main.py` 接线（安装并持有 `install_translators` 返回引用）仍由主 agent 完成；
qm 为构建产物已忽略入库，缺失时程序照常回退中文。