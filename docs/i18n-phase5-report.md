# i18n Phase 5 — 全量翻译目录合并收尾报告

- 日期：2026-09-18
- 分支：`feat/i18n`
- 状态：完成（未提交 commit / push）

## 范围

本次仅修改：

| 文件 | 动作 |
|------|------|
| `resources/i18n/catia_copilot_en_US.ts` | 由 199 条扩充至 1002 条，803 条待译英文全部填充 |
| `resources/i18n/catia_copilot_zh_CN.ts` | 由 199 条扩充至 1002 条，803 条 identity 填充 |
| `resources/i18n/catia_copilot_en_US.qm` / `..._zh_CN.qm` | 重新生成（`*.qm` 被 .gitignore 忽略） |
| `docs/i18n-progress.md` | 进度标记（Phase 5 完成） |
| `docs/i18n-phase5-report.md` | 本次报告 |

未触碰：任何业务源码、`README*`、既有 8 个 `docs/i18n-phase*-translations.json`
交接表、任何 git 操作（未 commit / push / stash / reset）。

## 一、交接表合并

读取全部 8 个 `docs/i18n-phase*-translations.json`：

| 交接表 | 词条 |
|--------|------|
| phase1 | 200 |
| phase2 | 657 |
| phase3-dialogs | 96 |
| phase3-main | 160（占位表，全部键值同串，英文待本次填充） |
| phase3-ui | 15 |
| phase4-ai | 57 |
| phase4-ai-dialogs | 29 |
| phase4-help | 1 |
| **去重后唯一 source** | **1003**（含元数据键 `_说明`，已剔除 → 实际 1002） |

跨表冲突 11 条，解析如下（未覆盖 phase1 既有「就绪→Ready」）：

`文件名→File Name`、`版本→Version`、`就绪→Ready`、`浏览…→Browse…`、
`状态→Status`、`类型→Type`、`零件编号→Part number`、`新建→Create`、
`跳过→Skipped`、`失败→Failed`、`保留签出→Checkout kept`。

已核对与既存测试断言一致（`test_i18n_plm_main`：`状态→Status`、`零件号` 等；
`test_mass_props_i18n`：`类型→Type`；`test_i18n_ai_dialogs`：`浏览…→Browse…`；
`test_i18n_plm_ui`：Create / Checked in 等）。

## 二、真实 lupdate 提取

显式 18 文件生产清单（`main.py` + `catia_copilot/constants.py`、`i18n.py` 及
`ui/` 下 15 个文件；排除 tests / `_archive` / mypdm，`main.py` 实测 0 个
`translate()` 调用），命令：

```
pyside6-lupdate -extensions py -no-obsolete <18 文件> -ts resources/i18n/catia_copilot_{en_US,zh_CN}.ts
```

两次运行结果（exit 0）：

```
Updating 'resources/i18n/catia_copilot_en_US.ts'...
    Found 1002 source text(s) (803 new and 199 already existing)
Updating 'resources/i18n/catia_copilot_zh_CN.ts'...
    Found 1002 source text(s) (803 new and 199 already existing)
```

对同一清单向临时空 TS 再跑一次提取（独立对比基准）：同样 1002 source。
与交接合并集做集合差：**TS−merged = 0 缺少；merged−TS = 0 多余**。

## 三、TS 填充

- **en_US.ts**：803 条 `type="unfinished"` 全部填充英文。来源：
  - phase1/2/3-dialogs/3-ui/4-ai/4-ai-dialogs/4-help 的既有英文译文（非占位）；
  - 11 条冲突解析值；
  - phase3-main 占位表中 **151 条手工翻译**（占位符 `{N}` 与 source 全量一致）；
  - **故意保持原值 10 条**：`CATIA V5 ✅`、`3DEXPERIENCE ⚠️`（品牌识别）、
    `Ixx/Ixy/Ixz/Iyy/Iyz/Izz (kg·mm²)`（6 个惯量列布局）、
    `*.CATProduct (*.CATProduct);;All Files (*)`（QFileDialog 过滤器）、
    `{0} ({1})`（通用格式包装）。
- **zh_CN.ts**：803 条全部 identity 填充（translation = source）并去除
  `type="unfinished"`。

填充后两组均：**total = 1002，unfinished = 0，空 translation = 0**。

## 四、lrelease 编译 QM

```
pyside6-lrelease resources/i18n/catia_copilot_en_US.ts -qm ...en_US.qm   → exit 0
pyside6-lrelease resources/i18n/catia_copilot_zh_CN.ts -qm ...zh_CN.qm   → exit 0
```

两个 QM 均：`Generated 1002 translation(s) (1002 finished and 0 unfinished)`。

## 五、验证

| 项目 | 结果 |
|------|------|
| 正式 TS 与 lupdate 提取源集合 | 0 缺少 / 0 多余 |
| 全部 source/translation `{`/`}` 计数一致 | 0 不一致 |
| `QTranslator` 加载 en_US.qm（context `CATIACopilot`） | `取消→Cancel`、`确定→OK`、`状态→Status`、`类型→Type`、`零件编号→Part number`、`浏览…→Browse…`、`文件名→File Name`、`版本→Version`、`就绪→Ready`、`保留签出→Checkout kept`、`新建→Create`、`跳过→Skipped`、`失败→Failed` 等全部英文命中 |
| 长句/富文本英文命中 | `以下文件有未保存的修改，请先保存后再同步：\n\n{0}` → `The following files have unsaved changes. Save them before syncing:\n\n{0}`；`同步完成，共 {0} 条警告/错误：\n` → `Sync complete; {0} warning(s)/error(s) in total:\n`；`零件号：<b>{0}</b>　版本：<b>{1}</b>　迭代：<b>{2}</b>` → `Part Number: <b>{0}</b>  Version: <b>{1}</b>  Iteration: <b>{2}</b>` |
| 未收录词回退 | `这个字符串绝不在目录表中XYZ` → 原样返回（中文 source 回退） |
| zh_CN.qm | 加载成功，`状态` → `状态`（中文 identity） |
| 回归测试 | i18n / lupdate / mass_props 相关 **78 passed** |

### 已知环境问题（与本次改动无关）

pytest 以 `-k "i18n or lupdate or mass_props"` 跑整个 tests 目录时，全部用例 PASSED
之后的进程退出阶段偶现原生崩溃（exit `-1073740940` = `0xC0000409`），faulthandler
无法捕获栈。已用 phase1 基线 TS（199 条）在相同命令下复现同样崩溃，判定为
PySide6 在 Python 3.13 下的进程退出/GC 环境问题，与 TS/QM 内容无关。显式指定
5 个相关测试文件单跑退出码 0 且输出完整摘要。

## 六、未完成事项

- Task 5.1 后半：构建流水线「自动校验/编译 QM」脚本尚未加入构建系统。
- Task 5.3：安装器双语（inno 脚本 / 安装界面语言打包）。
- 安装包 / CATIA / PLM 真实操作验收尚未执行。