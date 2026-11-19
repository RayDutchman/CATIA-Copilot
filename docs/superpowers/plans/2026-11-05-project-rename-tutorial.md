# 项目改名教程与脚本工具支持实施计划

> 已被后续用户决定取代：用户要求 cat5cli 自身提供组件引用替换、图纸生成源重绑两个通用工具，而非用 `run --file` 承载项目 COM 脚本。教程直接交付，不再要求单独审阅本计划。下文仅保留此前方案记录，不作为当前实施指令。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付同事在目标电脑使用 cat5cli 完成分批项目改名的教程，验证所需通用脚本能力。

**Architecture:** 复用现有 `run --file`、文档、图纸导出及任务接续接口。项目规则、路径映射和分批顺序由任务脚本负责；一级子装配分批处理，零级总装最后单独处理。不增加项目迁移框架。

**Tech Stack:** Windows、CATIA V5、现有 cat5cli standalone、Python 标准库与已随包提供的 COM 支持。

**Spec:** 用户确认的本轮范围；技术依据为 `docs/superpowers/benchmarks/2026-11-05-filesystem-rename-relink-probe.md`。

## Global Constraints

- 本机仅验证自建夹具；真实项目由同事在目标电脑操作。
- 只有零级总装约 20GB，一级子装配正常可加载；不因此扩展内存调度系统。
- 不新增一键迁移命令、数据库、自动重启或多机调度。
- 最新替换前缀是 V01/VL01 → VE01；VT28 不自动加入。
- 保留共享件、供应商排除、装配链接和目录结构；不改写 CAD 二进制。
- CLI 生成消息使用英文；教程使用中文；不新增依赖。
- 既有 beta.2、GUI 构建脚本、模板等未提交变更保留；不自动 commit、安装或发布。

## Review Focus

- 一个共享文件被多个一级装配使用：全局文件映射唯一，文件内容只修改一次，每条父引用分别检查。
- 改名后 ReferenceProduct 已不可读：以改名前保存的父文档/节点/旧路径恢复定位。
- 断言失败或弹窗等待：不重跑整批，先读取原 task 结果和业务报告。
- 同名映射碰撞及父目录改名：同时检查多对一、已有目标、路径传播与供应商路径变化。
- 运行器的 success 不是迁移验收：脚本明确断言并落盘报告；普通脚本不使用几何建模状态代替链接验证。

## Task 1：明确现有脚本入口支持范围

**Files:**
- Read: `catia_copilot/catia/tool_service.py`
- Read: `catia_copilot/catia/modeling_contract.py`
- Read: `skills/cat5cli/references/modeling.md`
- Read: `skills/cat5cli/references/execution.md`
- Read: `catia_copilot/tests/catia_manual/cat5cli_rename_reference_probe.py`

**Interfaces:** 消费 `run --file` 的 `def build(ctx):`；产出教程中的真实运行前提和结果处理方式。

- [ ] 核对 schema、脚本导入、活动文档要求、脚本返回值处理和 worker 输出通道。
- [ ] 记录当前约束：执行入口先检查活动文档；`module.build(ctx)` 返回值目前未被消费；报告应写显式绝对路径，不能依赖脚本相对目录或 print 作为最终 JSON。
- [ ] 在 beta.2 候选 EXE 中验证脚本可导入实际需要的标准库及 win32com，执行自建 Product/CATDrawing 操作；使用独立任务目录，不替换正式安装。
- [ ] 若出现阻断教程的运行器缺陷，先给出最小回归和局部修复方案，不顺带新增项目命令或 ctx API。

## Task 2：编写目标电脑操作教程

**Files:**
- Create: `docs/CAT5CLI_PROJECT_RENAME_GUIDE.md`

**Interfaces:** 消费现有命令 schema 和试验中验证过的 COM 方法；产出分阶段输入、操作与验收说明，不承诺一键执行。

- [ ] 写明版本：beta.1 已知跨机 typelib 缺陷，beta.2 仍为候选；目标电脑先验证版本/schema/连接，不能把尚未发布的包写成可公开下载。
- [ ] 给出工作副本、全局路径清单及字段规则的 JSON 示例；展示 V01/VL01 多对一冲突、供应商内容排除与路径变化的区别。
- [ ] 说明先保存完整引用清单再进行文件系统改名；映射以文件身份去重，节点记录保留父文档和出现路径。文件夹层级不当作装配依赖层级。
- [ ] 说明一级子装配分批、内部自底向上、共享件只处理一次；零级总装最后单独打开、替换直接引用并保存，不从总装重复遍历修改全机。
- [ ] 给出可由宿主改写的短脚本片段：显式路径配置、ReplaceComponent、每个出现路径读回、普通 Save、报告写盘与异常上抛。说明不能直接执行会创建/删除夹具的试验脚本来迁移真实项目。
- [ ] 写明图纸生成源重绑、文本/图框字段明确选择、更新保存后按现有 PDF 命令重导；复杂尺寸关联、上下文外链和柔性装配尚未验证。
- [ ] 写明 running/waiting_for_dialog 的原任务接续，以及完成批次核对报告后再处理下一批；多 CATIA 实例当前不支持可靠 PID 绑定，不写虚构参数。
- [ ] 给出冷重开检查：路径、件号、实例名、位置、约束、发布、图纸来源；PDF 实际显示内容另行检查。

## Task 3：Skill 路由与交付验证

**Files:**
- Modify: `skills/cat5cli/SKILL.md`
- Create: `skills/cat5cli/references/reference-relink.md`
- Modify if necessary: `skills/cat5cli/references/modeling.md`
- Create: `docs/superpowers/benchmarks/2026-11-05-project-rename-guide-validation.md`

**Interfaces:** Skill 内参考自包含，不链接到安装包中不存在的仓库相对路径；详细人工教程位于 docs。

- [ ] 添加脚本重连场景入口，列出已验证 API、清单前置条件、共享件和任务接续规则；区分通用脚本与 ctx 几何建模接口。
- [ ] 校验全部示例命令对照真实 schema，Skill 链接可解析，文档不引入不存在的 PID/项目命令。
- [ ] 用自建夹具覆盖共享件、断链恢复、图纸源、普通保存及冷重开；报告记录使用的候选 EXE 和验证范围，检查自己的临时文件与文档已清理。
- [ ] 若仅改文档，运行文档链接/schema 检查；若修改 Python 行为，运行相应回归及完整 unittest。
- [ ] 交付教程、工具支持结论和目标电脑尚需确认项；发布/提交另等授权。
