# CATIA 工具 CLI + Skills 设计

更新：2026-10-24。本文定义产品目标、接口组织与能力边界，不表示所列能力已经实现。实施顺序、实现差异与验收结果分别记录在计划及验收文档中。

**目标：让外部 AI 宿主通过 Skill 和 Windows CLI 使用 CATIA，GUI 专注非 AI 功能。** 保留已有 21 个 CATIA 工具的参数、默认值与业务语义；建模统一通过脚本执行，替代逐项建模命令，不追加统一门禁、预检、强制验证或确认流程。

## 1. 产品与技术决定

- 命令名 **`catkit`**（CATIA Toolkit）；npm 包名及可用性发布前核实。
- Python + pycatia/pywin32；固定 **Nuitka standalone** 构建 Windows x64 ZIP，保留控制台，无需用户安装 Python。
- 同仓库新增 CLI，复用现有 CATIA 操作代码；GUI 保留非 AI 功能，移除内置 AI 界面与运行代码。
- 外部宿主负责模型、对话和工具调度，CLI 不嵌入 AI 运行时或 GUI 桥接层。
- CATIA 操作、构建与运行环境均为 Windows 原生。
- 借鉴 Lark：按对象分组、命令自描述、Skill 按需参考、npm 薄启动器；不把整个 API 手册塞进上下文。

## 2. 代码组织

`catia_copilot/cli/` 负责命令注册、参数解析及 JSON 输出；`catia_copilot/catia/` 复用和补充实际操作。
GUI 与 CLI 只复用有需要的部分代码，不另造“共享核心”框架；CLI 不导入 Qt 或内置 AI 模块。
操作契约与脚本执行逻辑属于 CATIA 业务层；文件覆盖等交互由 CLI 参数表达，GUI 自己负责交互。
业务 worker 在单线程中初始化 COM；前台负责等待和观察 CATIA 弹窗，结果写 stdout，日志与进度不混入结果。
`skills/catkit/` 负责调用指导及按需参考；npm 包仅负责 Windows 工具与 Skill 的安装、版本配套和启动。

## 3. 目标命令树

以下定义目标能力与命令分组，不逐项标注开发状态。`a | b` 表示同级独立子命令；缩进拼接成完整命令。
每个组与叶子均支持 `--help`；根 `schema <命令路径>` 查询具体参数、单位、对象类型和结果格式。

```text
catkit
├─ --help / --version                  # 总入口帮助、版本
├─ status [--diagnose]                # 连接状态；--diagnose 查询详细诊断
├─ schema [<command-path>]             # 全部命令索引或指定命令契约
├─ run --file <script.py>              # 执行 build(ctx)，记录步骤与结果
├─ task status | wait --task <id>      # 查询或等待同一后台任务
├─ dialog respond --task <id>          # --button <id> 或 --control <id> --text <text>
├─ document                           # CATPart / CATProduct / CATDrawing 文档
│  ├─ list | current | info            # 已打开文档、当前活动文档、指定文档详情
│  ├─ open | activate                 # 打开文件、激活指定文档
│  ├─ save | save-as | close          # 保存、另存、关闭；文件改名通过另存表达
│  ├─ properties
│  │  ├─ get                         # 标准属性与用户属性
│  │  └─ set                         # 修改 PartNumber、Nomenclature 等元数据
│  ├─ dependencies list | reverse    # 正向文件依赖与反向依赖
│  └─ find part | drawing            # 按路径查关联零件或图纸
├─ part                               # CATPart 零件
│  ├─ info | tree | update | mass    # 零件信息、完整模型树、更新、质量/重心
│  ├─ template apply                 # 对指定文档或文件集应用零件模板
│  └─ export step                    # 导出指定零件或文件集
├─ product                            # CATProduct 装配文档
│  ├─ info | tree                    # 装配节点与引用零件信息
│  ├─ mass
│  │  ├─ collect                     # 装配质量特性采集与汇总
│  │  ├─ merge                       # 合并已有质量数据文件，不需要 CATIA
│  │  └─ remeasure                   # 重新测量指定引用零件
│  └─ export step                    # 整装配文档或文件集导出，不提供 --node
├─ drawing                            # CATDrawing 图纸
│  ├─ info                           # 页信息及关联模型
│  ├─ create                         # 从源模型与模板生成图纸
│  ├─ refresh                        # 刷新图纸及属性
│  └─ export pdf                     # 指定图纸或文件集导出
└─ bom                                # 装配 BOM 操作，不等同于模型树
   ├─ collect                        # 采集明细/汇总，输出结构化数据
   ├─ export                         # 从装配或已采集数据导出 Excel/CSV
   └─ write                          # 将属性数据写回对应 CATIA 对象
```

`run --file` 是统一建模入口：新建零件、草图绘制、特征创建和建模内的几何查询在 `build(ctx)` 中组合，不另设 `part new/body/feature/sketch` 命令族。通用文件操作和聊天记忆由宿主提供。
文档文件名由 open/save-as 的路径表达，PartNumber 等业务名称由 properties set 修改，避免含糊的 document rename。
源文档和保存状态由文档查询提供，不设置独立 BOM 前置检查流程。阵列与装配约束不在本文目标范围；PLM 保留在 GUI。
建模 API 以 `modeling_contract.py` 的能力目录为准，由 Skill 按需提供方法签名与边界；不把模块中存在函数等同于脚本已可调用或实机已可用。
Body 管理、既有特征参数修改、草图编辑等扩展若纳入目标，应补充 `ModelingContext` API 与契约，通过同一脚本入口使用，不再扩展逐项 CLI。脚本入口本身不构成这些能力已存在的保证。

## 4. 当前对象与显式目标

- 文档操作保留已有工具的默认目标行为（通常为活动文档）及路径参数；新增对象操作用 `--doc <id>` 显式指定，`--active` 明示活动文档，两者互斥。
- `document list/current` 返回文档标识、名称、路径、类型和活动状态；支持未保存文档，名称歧义返回候选。
- `current` 是只读查询，`activate` 才改变 CATIA 当前对象；CLI 不保存隐式的跨命令“当前目标”。
- `part`、`product`、`drawing` 分别校验 CATPart、CATProduct、CATDrawing；不按活动窗口猜对象类型。
- 脚本内的 Part、Body、特征及草图对象遵循上下文 API 的归属约束；不增加一套独立 CLI 建模对象选择参数。
- 模型树查询用于理解结构，区分显示名称、对象类型与所属文档；不得把显示名或临时几何引用当成永久稳定标识。
- 装配节点用 `--node <instance-path>`；实例路径不等于引用 CATPart，编辑子零件须定位其文档。
- 脚本新建零件不要求已有目标；`drawing create` 用 `--source-doc <id>` 指定源模型，而非目标图纸。
- 文件批处理用 `--files ...`，与 `--doc/--active` 互斥；批量导出仍需 CATIA，不是离线几何转换。
- `run` 保留可选 `--target-document-id` 与脚本自身的对象选择能力，不强制指定目标；结果列明实际操作文档。

## 5. 参数、输出与调用示例

命令契约统一生成 argparse 帮助与 schema；参数名称、类型、单位、默认值和结果定义以契约为准，不在实施计划另建参数规范。
已有参数使用短横线 CLI 拼写并保留默认值；对象和数组接受 JSON 或 `@UTF-8文件`，PowerShell 优先使用文件。新增命令遵循相同规则。
文件导出保留各工具的输出路径参数，覆盖策略为 `--overwrite error|skip|replace`；CATIA 自身弹窗通过任务接续处理。
保存与关闭沿用原操作语义；原先依赖 GUI 交互的参数按命令逐项适配，不追加统一门禁。
业务完成时直接返回操作结果，保留已有工具结构，不另套统一业务信封；stdout 单 JSON、stderr 日志。
退出码：0 成功或非终态任务，1 执行失败（含对象不存在或歧义），2 参数错误，3 CATIA 不可用；退出码 0 时仍须检查任务状态。
结果保留原工具的步骤、状态及错误信息；已有可选 verification 仍为可选，不要求所有写命令额外回读。help/version 输出文本，schema 输出 JSON。

```powershell
catkit status
catkit document current
catkit part tree --doc <document-id>
catkit schema run
catkit run --help
catkit run --target-document-id <document-id> --file D:/CATIA_Scripts/edit.py
catkit task wait --task <task-id>
catkit dialog respond --task <task-id> --button <button-id>
```

## 6. 任务与弹窗接续

- 普通业务命令自动启动独立 worker，无需宿主事先开启监控；前台退出不取消原操作。
- 遇弹窗返回 `waiting_for_dialog`、`task_id`、文字和控件标识；等待超时返回 `running`，不重复执行业务。
- `dialog respond` 响应后接续同一任务，`task wait` 等待并读取原结果；输入和按钮均按控件标识操作。
- 用 pywinauto Win32 观察 CATIA 弹窗，不依赖被阻塞的 COM；响应前校验窗口归属及快照，扫描失败仍保留任务 ID。
- 存在未完成操作时返回 `busy` 和已有任务 ID；多个 CATIA 实例无法确定归属时不猜测、不点击。
- CATIA 连接和按需启动复用已有连接入口；状态查询本身不启动 CATIA，不自动确认未知弹窗。

## 7. 分发与磁盘布局

- 正式程序固定为 `%LOCALAPPDATA%/Catkit/runtime/catkit.exe`，连同 standalone 依赖整体替换；`CATKIT_HOME` 可覆盖根目录。
- 构建验证通过后更新正式安装；重复安装不积累版本目录，文件占用时明确失败，不另建副本。
- `dist/catkit` 保留最新 ZIP 与校验文件；成功后清理编译中间目录与重复产物，安装临时目录结束即清理。
- Skill 随工具配套分发，支持指定宿主安装位置；任务记录与运行文件分离，清理不得破坏未完成任务。

## 8. 验收标准

- 离线：全命令解析、help/schema、目标与父级校验、JSON/退出码、业务错误路径、Nuitka 产物启动。
- 实机：按命令树测试文档/零件/装配/图纸/BOM；通过 run 的完整脚本验证草图与多类特征组合，不只测试 Pad。
- 重点场景：两个文档切换、未保存零件、同名特征、脚本失败后的部分修改、生成后更新、批量导出与写回；新增脚本 API 的编辑能力须有对应验收。
- GUI：AI 入口与运行代码已移除，BOM/PLM/文档/图纸等非 AI 功能回归通过。
- 验证弹窗接续、前台退出后 worker 存活、业务仅执行一次、扫描异常保留任务 ID，以及重复安装清除旧文件。
- 提供 Windows ZIP、配套 Skill 和安装命令；独立 AI 仅凭 Skill、help/schema 与 EXE 完成业务闭环，验收证据单独归档。
