# Catkit 目标命令补全实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐目标设计中的文档、零件查询、装配与图纸命令，复用项目已有资产，保持脚本为唯一建模入口。

**Architecture:** 保留现有 CLI 注册、schema、工具服务和 detached worker。使用已有文档身份工具扩展显式目标解析；新增业务函数只做对象解析和现有操作的薄适配，不重建连接、任务或建模框架。

**Tech Stack:** Windows Python、pycatia/pywin32、unittest、现有 Nuitka 与 Node 安装器，不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-23-catia-cli-skills-design.md`

## Global Constraints

- 在当前隔离 worktree 工作；保留已有未提交修改，不自动 commit/push。
- 原 21 个工具的参数、默认值和业务结果保持兼容；新增命令不套业务信封。
- 建模仅通过 `run --file`；不添加独立 body/feature/sketch 命令。
- `product export step` 仅整装配或文件集，无 `--node` 实例导出。
- SaveAs 保留源文件；Close 调用原生关闭并使用已有弹窗接续，不自动丢弃修改。
- 开发验证使用源码；最后统一构建一次并覆盖正式 runtime，测试临时目录结束即清理。
- 未查证的 COM 属性不得据名称猜测；查项目调用、已安装包装器/类型库或官方文档，必要时只读探查。

## Review Focus

1. 未保存文档、同名文档、无活动文档：返回可解析身份或明确歧义，不选错对象（任务 1）。
2. 显式指定非活动文档：不能读取、更新或保存另一个文档（任务 1–3）。
3. Close/SaveAs 的取消与弹窗：不重复执行，不删源文件，不误报成功（任务 2）。
4. MPD 合并与重测的单位/坐标系：保持既有数据语义，不宣称自动坐标变换（任务 4）。
5. 批量导出失败、图纸链接不可读取：保留逐项错误，不将未知关联伪装为空关联（任务 3、5）。

## 文件职责

- `catia_copilot/catia/document_scope.py`：保留 DocumentIdentity、describe_document、matches_document，扩展查找与类型检查。
- 新建 `catia_copilot/catia/inspection_service.py`：新增查询、文档操作和质量适配函数，避免继续膨胀旧工具服务。
- 新建 `catia_copilot/catia/inspection_schema.py`：新增操作契约；`tool_schema.py` 合并导出，旧契约不改。
- `catia_copilot/catia/tool_service.py`：将新增函数接入 tools_map，保持旧函数体及迁移校验。
- `catia_copilot/cli/app.py`：添加命令映射、目标参数互斥与批处理别名。
- `catia_copilot/catia/assembly_reader.py`：允许传入文档，复用现有树读取，不改变 GUI 默认活动文档语义。
- `catia_copilot/tests/test_catkit_inspection.py`：新增操作行为回归；既有 CLI、文档身份、导出测试按需扩展。

## Task 1：文档身份、显式目标与查询

**Interfaces:** `resolve_document(app, doc=None, active=False, expected_type=None)` 返回原始 COM 文档；基于已有 identity key/name/path 匹配，全量匹配后报告歧义。`current_document()`、`document_info(doc=None, active=False)` 返回 JSON 可序列化字典。

- [ ] 先补失败测试：未保存文档可定位；同名返回候选；空文档集合报错；`--doc/--active` 互斥；显式 B 文档不触及活动 A。
- [ ] 运行 `python -m unittest catia_copilot.tests.test_document_scope catia_copilot.tests.test_catkit_inspection -v` 确认失败原因。
- [ ] 扩展 document_scope，复用 `document.get_document_type`；身份属性不可读时不伪造路径。
- [ ] 接入 `document current/info`，结果包含身份、类型、活动状态及可读取的保存状态；查询不激活文档。
- [ ] 新增命令使用可选 `--doc/--active`；已有路径参数命令保持原约定，显式目标不得静默忽略。
- [ ] 跑文档身份、新操作、CLI 回归，确认旧迁移契约仍通过。

## Task 2：文档激活、另存和关闭

**Interfaces:** `activate_document(doc=None, active=False)`；`save_document_as(file_path, doc=None, active=False)`；`close_document(doc=None, active=False)`。成功返回 `success` 和目标身份/路径；取消返回明确失败结果。

- [ ] 测试指定 B 时仅调用 B 的 Activate/SaveAs/Close；另存不删除源文件；COM 异常透传为操作失败。
- [ ] 查验 `document.rename_document` 中 SaveAs 主体与副作用，复用安全部分；禁止引入其可选删除源文件行为。
- [ ] 核实 Document.Activate/SaveAs/Close 接口后实现薄适配，复用既有任务等待和弹窗响应。
- [ ] 注册 `document activate/save-as/close` 和 schema，另存路径使用 Windows 原生形式。
- [ ] 运行新操作与 task/dialog 回归；实机仅使用本轮新建夹具验证关闭/取消/另存。

## Task 3：零件与图纸查询

**Interfaces:** `part_info(doc=None, active=False)`、`part_tree(doc=None, active=False)`、`update_part_document(doc=None, active=False)`、`part_mass(doc=None, active=False)`、`drawing_info(doc=None, active=False)`。

- [ ] 核对 modeling 的 `list_features/list_sketches/update_part/get_mass_props` 及文档绑定方式，查验 Body/HybridBody/草图集合和 Drawing Sheets/Views 链接接口。
- [ ] 写失败测试：类型不符、空集合、多 Body、嵌套几何图形集、查询异常不得冒充完整空树、显式目标不串文档。
- [ ] `part info` 返回身份和根对象信息；`part tree` 按已证实集合返回类型/名称/children，未覆盖分支明确标记，不称为完整读取成功。
- [ ] 更新与质量复用 modeling API；若内部仍读 ActiveDocument，最小修正目标绑定并增加回归，不用静默切换文档补救。
- [ ] `drawing info` 返回页名称/数量及视图关联模型；关联来自已证实 COM 链接，不用文件名启发式冒充；不可读项记录错误。
- [ ] 注册对应 CLI/schema；运行新操作与 modeling 相关回归。

## Task 4：装配查询与质量数据处理

**Interfaces:** `read_assembly_tree(catia_app=None, document=None)` 保持原 GUI 调用兼容；`product_info(doc=None, active=False)`、`product_tree(doc=None, active=False, node=None)`；`merge_mass_files(files, output_file, overwrite='error')`；`remeasure_product_part(file_path, part_number='', read_mode='all')`。

- [ ] 对照现有 mass_props GUI 调用核实 `load_rows/merge_rows/save_rows/remeasure_part_mass_props` 的输入输出，不重写汇总算法。
- [ ] 写失败测试：指定文档树读取；重复引用实例保留；节点不存在；MPD 序列化往返；合并结果与原函数一致；未打开零件重测明确失败。
- [ ] 给 assembly_reader 增加可选 document 参数；树节点路径复用现有 `0.0` 零基索引约定，注明随树变化失效，不承诺永久 ID。
- [ ] `product info` 返回根信息，`product tree --node` 仅查询子树；不用于导出。
- [ ] merge 输入多个 MPD，沿用同一根坐标系前提与 SI 内部数据，输出 MPD；不添加坐标变换或自动写回 CATIA。
- [ ] remeasure 沿用已有“已打开引用零件的保持测量重读”，保留原单位/字段，不伪称强制 Analyze 或自动修改 MPD。
- [ ] 注册命令并在 help/schema 明确上述语义；运行质量与装配现有回归。

## Task 5：整装配导出与契约集成

**Interfaces:** `export_product_step(file_paths, output_folder=None, prefix='MD_', suffix='', ...)` 适配现有 STEP 导出函数；精确参数取已有 schema，不创造新的第二套导出策略。

- [ ] 先验证 `convert_part_to_step` 对 CATProduct 的既有支持，复用打开文档所有权及 overwrite 回调。
- [ ] 测试 CATProduct 文件集、复用已打开文档、只关闭本次打开文档、覆盖策略及混合成功/失败统计。
- [ ] 注册 `product export step`；`--files` 作为现有 `file_paths` 的别名，不接受 `--node`，禁止冲突输入静默覆盖。
- [ ] 为新增命令补齐 help/schema；旧业务命令如果没有显式目标参数，契约清晰区分，不宣称全局通用。
- [ ] 更新 `skills/catkit/references/tools.md` 及验收记录；目标文档只在实际决策变化时调整，不写进度。

## Task 6：回归、实机证据与交付

- [ ] 执行 `python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -q`（QT_QPA_PLATFORM=offscreen），以及 `node --test packages/catkit/test/installer.cjs`。
- [ ] 使用新建夹具通过源码 CLI 验证新命令；记录许可、样本或环境阻塞，未测项不计通过；不操作原用户文档。
- [ ] 无 Python 的独立 Windows 环境若不可用，明确保留该验收缺口，不在本机卸载 Python。
- [ ] 最后运行一次 `python scripts/build_catkit.py`，验证正式 EXE 的 help/schema/version 和新命令发现。
- [ ] 删除本轮夹具与临时文件，保留仓库内验收摘要；核对固定安装和 dist 无多版本/中间目录残留。

## 计划自检

- 命令缺项全部对应任务 1–5；脚本建模、旧工具及 task/dialog 复用，不重复实现。
- `product export step --node` 按用户决定排除；树的 --node 仅是只读定位。
- COM 查证安排在对应实现前；查证失败时记录确切缺口，不用 guessed API 或 mock 通过冒充实现完成。
- 执行方式待用户选择；本计划不授权自动提交、发布或关闭用户既有 CATIA 文档。
