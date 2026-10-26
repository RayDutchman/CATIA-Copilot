# BOM 独立 CLI 接口设计

## 1. 产品边界

CLI 与 GUI 独立运行，复用 V3 BOM 业务能力。CLI 直接操作 CATIA，不经 GUI 控件执行，不建立 IPC、自动刷新或共享撤销。GUI 打开时，用户按需刷新；既有 GUI 撤销问题单独处理。

本次目标为 V3 的完整/层级/汇总视图、字段编辑和导出。不引入常驻 COM 服务、跨调用 COM 会话或离线 BOM 快照协议，也不迁移文件移动等外围交互。

## 2. 命令与默认行为

| 命令 | 设计 |
|---|---|
| `bom collect [--view <view>]` | 统一使用 V3，输出列数据和可序列化目标定位信息 |
| `bom export [--view <view>]` | 统一从本次 V3 采集生成指定视图，导出 XLSX/CSV |
| `bom edit --changes @changes.json` | 新增批量字段编辑，逐项返回结果 |
| `bom write` | 保留原按 PN 批量写回接口，不把它冒充实例级编辑 |

- collect/export 只使用 V3，不设置旧实现回退或兼容分发；未传 view 时默认 hierarchical。
- view 取 full、hierarchical、summary；不再用 summarize 开关选择模式。include_assemblies 默认 false，只影响 summary。
- 默认层级视图及汇总不包含装配件，与 V3 工作台无用户偏好时的默认行为一致（bom_edit_dialog_v3.py:203–210、308–313）；CLI 不读取 GUI 的本机 QSettings。
- 列及自定义属性沿用 columns/custom_columns；视图名称使用英文枚举，业务字段名不翻译。
- collect/export/edit 统一复用现有 `--doc` / `--active` 定位已打开的文档，两者互斥；省略使用活动文档。BOM 不提供额外的文件打开、关闭、保存流程，也不沿用旧 file_path/file_paths 作为隐式打开入口。
- 文件打开由 `document open` 负责；现有 `resolve_document` 可定位未保存和非活动文档。普通保存用 `document save`，未保存文档首次落盘用 `document save-as`。不要假设旧 save 命令也接受 --doc，按其现有 schema 调用。
- 采集入口新增可选的已解析根产品参数，绕过其原有 ActiveDocument/文件加载分支；GUI 现有调用不改。导出输出路径是产物位置，与输入文档生命周期分开。
- 新增命令、模块、函数、schema 和使用说明采用 BOM 业务名称，不引入版本标签或按版本分支的选择项；现有采集资产复用而不复制实现。

## 3. V3 数据与目标定位

- 同一 worker 内调用 collect_bom_part_masters，保留 COM 引用和内部索引仅供本次操作使用。
- 用 iter_full_rows、iter_hierarchical_rows、flatten_bom_to_summary 生成视图，不重写聚合算法。
- 对外移除 `_product`、`_inst_key` 等运行时字段；不返回裸 COM 对象、id(product) 或把 pm_key 宣称为持久ID。
- 每行提供其代表的实例目标列表，包含与 product tree 一致的 node 路径及可读取的名称/引用文档信息。层级/汇总行可能对应多个目标，不能默认任选实例写入。
- node 是当前装配结构中的零基索引路径，结构变化后须重新查询。edit 在同次采集/解析中建立 node 与当前 COM 实例的对应关系，不拿旧进程对象ID匹配新对象。
- 同一引用零件的共享属性修改会影响其所有实例；实例名写入所选实例的定义。若其所属装配文件被重复引用，定义变更可能在多个路径联动，不能承诺只改变屏幕上的一个出现位置。
- 现有主数据键采用 PN/宿主 PN，并非文件唯一标识；同 PN 不同引用文件若不能唯一对应，必须明确报告歧义，不能静默把其中一个文件当作所有实例的目标。

## 4. 字段编辑契约

建议 changes 为有序数组，例如：

```json
[
  {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"},
  {"node": "0.1", "scope": "instance", "column": "Instance Name", "value": "Bracket.2"}
]
```

- scope 显式区分 master/instance；支持列来自已有字段常量和 write_cell 规则，schema 明确列出限制。实例描述须核实真实写入映射后才列为支持，不能根据行字典字段名推测 API。
- master 通过节点解析其主数据；一次变更只写该共享主数据一次，不按显示行重复写所有引用实例。不同变更按输入顺序执行。
- 复用只读字段限制、Source 原始值/显示值转换、自定义用户属性规则及 PN 改名约束。
- PN 改名需保持本次上下文的主数据和实例引用一致；COM 写入失败必须恢复本次预改缓存，不能把失败的新 PN 带入后续项。
- 同一请求内，相同主数据字段的重复相同赋值可合并；冲突赋值须按明确的有序变更语义处理并如实记录，不把视图重复行当成多份独立主数据。

### 部分失败

- 默认逐项执行，某项失败后继续处理可独立定位的后续项；每项记录 node、scope、column、status、错误及可取得的旧/新值。
- 批次返回 applied_count、failed_count 和 results；存在失败时 success=false、退出码1。被拒绝/不支持/无法定位的项不能静默跳过成成功。
- 已成功的修改保留，不宣称事务回滚，也不自动重跑整批；宿主根据逐项结果决定后续操作。
- CLI 不自动保存文档；沿用现有 document save。写回成功与落盘保存是两个状态。
- 不增加统一确认或强制 verification；CATIA 弹窗仍复用原 task/dialog 接续。

## 5. 导出边界

现有 export_bom_to_excel 自行调用旧采集，不能直接作为 V3 行导出。应提取其 rows→XLSX/CSV 写出部分，复用格式、列和样式；CLI 导出入口始终接收 V3 视图数据，不保留旧采集分支。

V3 导出使用所选视图产生的行，与 collect 的同视图语义一致；不是按 GUI 当前筛选或隐藏行导出。列配置显式来自命令，而不是读取 GUI 的 QSettings。

## 6. 最小实现落点

- 新增 Qt-free BOM CLI 服务：负责 V3 采集、目标映射、JSON清洗和编辑结果；复用 bom_collect_v3、bom_write、document_scope。
- tool_schema/app 更新 collect/export 的 V3 参数及结果契约并注册 edit。collect/export 的旧行为等价断言改为 V3 行为回归，其余旧工具的迁移对照保留；不篡改历史 AST 基线来掩盖语义变化。
- bom_export 提取行写出 helper，不把旧采集路径硬塞进 V3。
- Skill 增加 BOM 专用参考：视图选择、共享/实例属性、目标定位、部分成功、写回与保存；具体参数以运行时 schema 为准。
- CLI worker 生命周期、Job兼容和弹窗接续保持复用；不引入对 OpenCode 或特定 PowerShell 版本的依赖。

## 7. 验证要求

- collect/export 缺省即调用 V3，默认 hierarchical；full/summary 显式选择正确，不存在旧采集回退，旧 summarize 开关不能被静默忽略。
- bom write 和其他未调整工具维持各自既有契约。
- 三视图在重复引用、同 PN 不同引用、嵌入部件及多层装配上的语义一致；导出与采集同视图对齐。
- JSON不含COM/内部ID；节点失效、字段不支持及类型错误明确失败。
- 显式非活动目标不串文档；共享属性不重复写，实例字段不误改其他实例。
- 批次部分成功、PN失败后的缓存恢复、成功但未保存的语义均有回归。
- 实机只使用新建夹具，验证采集→编辑→读取→导出→保存；最后统一构建一次正式 EXE，清理测试产物。

## 8. 实施依据

用户已确认独立 CLI、统一采集实现、默认层级视图及其余编辑设计；文档操作复用现有接口，新增代码不引入版本标签。实施计划据此展开，不再加入旧采集/导出兼容分支。GUI行为不随本设计改变。
