# BOM CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 采集和导出统一使用工作台现有 BOM 数据模型，提供三种视图与独立批量编辑，并更新 Skill。

**Architecture:** 在单个 CLI worker 中复用文档解析、BOM 采集/视图和字段写回；只提取行导出层及轻量 CLI 服务，不增加 GUI 通信或常驻会话。

**Tech Stack:** 现有 Windows Python、pywin32/pycatia、openpyxl、unittest，无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-26-bom-v3-cli-design.md`

## Global Constraints

- 新增代码和公开接口用 BOM 业务名称，不加版本号或旧/新引擎开关；复用现有资产，不复制采集算法。
- collect/export 缺省即使用工作台数据模型，view=hierarchical；不保留旧采集/导出回退及 summarize 开关。
- 输入文档只用现有 --doc/--active；打开、保存、另存、关闭使用 document 命令，BOM 不重复实现生命周期。
- CLI/GUI 独立，无IPC、自动刷新、共享撤销；不改GUI行为，不加统一确认/verification。
- 保留其他工具契约；不自动提交、发布或重启服务。源码调试，最后统一构建，临时文件用完清理。

## Review Focus

- 非活动和未保存文档：不串用 ActiveDocument，不隐式打开或关闭（任务1）。
- 重复引用、同PN不同文件：目标不按显示行/裸PN误认（任务1、3）。
- 嵌入部件及复用子装配的实例定义：不能承诺修改仅影响单个出现路径（任务3）。
- 部分写入失败及PN改名：成功项保留，失败项不污染缓存/后续定位（任务3）。
- 导出与采集：同视图同列同数量，覆盖冲突不静默处理（任务2、4）。

## Task 1：复用文档目标与 BOM 数据

**Files:** 修改现有 `catia_copilot/catia/bom_collect_v3.py` 采集入口；新增 `catia_copilot/catia/bom_service.py`、`catia_copilot/tests/test_catkit_bom.py`。

**Interfaces:** 给 `collect_bom_part_masters` 增加 keyword-only `root_product=None`，传入时不再获取连接/活动文档/加载文件；原GUI调用不变。新增 `collect_bom(doc=None, active=False, view='hierarchical', columns=None, custom_columns=None, include_assemblies=False, sort_column=None, **kwargs) -> dict`。

- [ ] 写失败测试：显式非活动文档、未保存文档、无目标/歧义；传 root_product 不调用文件加载器或 ActiveDocument。
- [ ] 用 resolve_document 获取目标，传 document.Product 复用采集；按现有三种视图函数产生行。
- [ ] 建立当前请求内的节点路径映射；不暴露COM/id(product)，每行保留其代表的目标列表。无法可靠对应的目标明确报错。
- [ ] 测试 full/hierarchical/summary、重复引用、多层级、根节点与汇总数量、同PN不同文件歧义，不从测试中删除不方便的场景。
- [ ] 运行 `python -m unittest catia_copilot.tests.test_catkit_bom -v`，逐项 RED→GREEN。

## Task 2：提取行导出能力

**Files:** 修改 `catia_copilot/catia/bom_export.py`；扩展 bom_service 与 BOM 测试。

**Interfaces:** `export_bom_rows(rows, columns, output_file) -> dict`，扩展名确定 .xlsx/.csv；复用现有 `_write_sheet`/`_write_csv_file` 的列值、样式与UTF-8 BOM规则，移出闭包所需配置作为显式参数。`export_bom(output_file, doc=None, active=False, view='hierarchical', columns=None, custom_columns=None, include_assemblies=False, sort_column=None, **kwargs) -> dict`。

- [ ] 先写真实临时XLSX/CSV回归：表头、数量、Unicode、自定义列，行内容与collect同视图一致。
- [ ] 行写出helper不连接CATIA；CLI导出调用任务1采集后写出，不经过旧采集管线。
- [ ] 使用既有 file_interaction 覆盖处理，接入 --overwrite error/skip/replace；输出路径不承担打开输入文档的职责。
- [ ] 跑新导出测试与已有导出回归；GUI共享导出资产不得因提取而改变默认行为。

## Task 3：独立字段编辑与逐项结果

**Files:** 扩展 bom_service；按必要程度复用/小幅提取 `bom_write.py`、既有PN约束；扩展 BOM 测试。

**Interfaces:** `edit_bom(changes, doc=None, active=False, custom_columns=None, **kwargs) -> dict`；changes项为 node/scope/column/value，其中 value 为字符串。结果包含 success/applied_count/failed_count/results，失败计入退出码1。

- [ ] 写失败测试：主数据只写一次、实例名目标隔离、共享子装配定义联动、只读/未知字段、重复PN歧义、部分失败后继续、PN失败缓存恢复。
- [ ] 在同一个worker中完成采集、定位、写回；复用write_cell与字段映射，不调用GUI控件回调。
- [ ] schema实例写入先只声明已证实的 Instance Name；description_inst当前没有对应write_cell分支，只读，不猜COM属性来扩功能。
- [ ] 按输入顺序执行，记录成功/失败及可取得的旧新值；共享主数据按当前归属写入，嵌入部件按原语义处理。
- [ ] 成功项不回滚，失败不污染后续项；不自动保存文档，不将批次描述为事务。
- [ ] 定向测试通过后，验证原属性写回与document_scope回归。

## Task 4：接线、契约与 Skill

**Files:** tool_service.py、tool_schema.py（或新增 bom_schema.py）、cli/app.py、cli/backend.py（仅必要覆盖选项适配）、test_catkit_cli.py、test_catkit_migration.py、skills/catkit/。

- [ ] collect/export注册指向新BOM服务；edit新增命令。schema不含版本标签，统一doc/active、view、columns/custom_columns、include_assemblies/sort_column；export要求output_file。
- [ ] 删除CLI旧采集/导出分支与过时参数，不修改GUI所需旧资产；bom write及其他业务仍按原契约。
- [ ] 对collect/export取消旧行为等价断言，换成明确BOM回归；历史AST基线保留，其他工具仍逐项校验。
- [ ] 测试全部路由、目标互斥、默认hierarchical、旧开关明确报错、逐项失败退出码、help/schema无错误承诺。
- [ ] 新增 `skills/catkit/references/bom.md`，主Skill只加场景索引；说明打开→采集→编辑→按需保存/导出，不使用脚本或GUI控件绕过命令。

## Task 5：整体验证与交付

- [ ] 全量Python unittest discover和Node安装器测试通过；检查Qt-free导入、现有worker/弹窗接续回归。
- [ ] 使用自建装配夹具验证三视图、共享与实例字段、部分失败、导出及document保存。未实测场景如实记录，不操作用户既有文档。
- [ ] 审查只涉及本次BOM改动；最后一次Nuitka构建覆盖正式目录，实际EXE执行BOM闭环；清理夹具和构建中间文件。
- [ ] 更新验收记录，Skill同步到已授权的安装位置；不自动commit/push。

## 复核记录

- 已核实 document open/resolve_document/save/save-as 的职责差异，未把旧save误认为接受--doc。
- 已核实采集函数底部仍可经load_catia_file间接加载文件，故必须显式传根产品以复用文档定位，而不是只看函数体是否出现Documents.Open。
- 现有PM键以PN/宿主PN为基础；同PN不同文件不能假定天然隔离。实例名可能是共享子装配中的定义，不能承诺每个出现路径独立。
- 其余设计已获用户确认，已按以下记录实施。

## 执行结果

- [x] Task 1：采集入口复用已解析root_product，默认层级，三视图及目标信息回归通过。
- [x] Task 2：提取export_bom_rows，复用XLSX/CSV写出和既有覆盖回调协议。
- [x] Task 3：独立字段编辑、PN约束/缓存与部分失败结果完成；实例描述保持只读。
- [x] Task 4：CLI/schema接线完成，移除旧BOM参数及回退；其他19原工具AST对照通过；Skill专页已安装。
- [x] Task 5：465项Python、6项Node通过；源码重复引用场景及正式EXE的12命令专项通过，临时夹具清理。
- 最终验收及静态审查实机裁决见 `docs/superpowers/benchmarks/2026-10-26-bom-cli-acceptance.md`。
