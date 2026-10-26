# BOM 操作

CLI 独立操作 CATIA，复用工作台的 BOM 业务能力；GUI 不参与命令执行，也不会自动刷新。先查目标命令的 help/schema。

## 目标与视图

collect、edit、export 通过 `--doc <已打开文档标识>` 或 `--active` 选择目标，省略则使用活动文档。打开文件用 `document open`；这些 BOM 命令不另行打开、关闭或保存输入文档。

| view | 用途 |
|---|---|
| `hierarchical`（默认） | 同一父节点下相同主数据合并，保留层级及数量 |
| `full` | 按实例展开，便于定位具体目标 |
| `summary` | 全树数量汇总；默认不包含装配件，--include-assemblies 会包含装配件及根装配 |

```powershell
catkit document open --file-path 'D:\项目\总装.CATProduct'
catkit bom collect --view hierarchical
catkit bom collect --doc '总装.CATProduct' --view full
```

采集结果提供行数据、列和目标定位信息。编辑的 node 使用返回的目标路径或 product tree 的路径，不用表格行号、显示名或猜测的 PN 代替。装配结构变化后重新查询；同 PN 不一定代表同一引用文件。

## 字段编辑

由宿主写 UTF-8 JSON，例如 `changes.json`：

```json
[
  {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"},
  {"node": "0.1", "scope": "instance", "column": "Instance Name", "value": "Bracket.2"}
]
```

```powershell
catkit schema bom edit
catkit bom edit --doc '总装.CATProduct' --changes '@D:\任务\changes.json'
```

- master 修改共享主数据，可能影响多个引用实例；instance 修改实例定义，目前可写列为 Instance Name。重复引用同一子装配时，其内部实例定义也可能在多个出现路径联动。
- 列名使用契约中的业务键，value 为字符串。自定义用户属性需在 --custom-columns 声明；只读结构列、实例描述不能冒充自定义属性写入。
- 查看 applied_count、failed_count 和逐项 results。失败项不会让成功项回滚，也不会自动重跑整批；依据结果修正必要项。
- 写回不等于保存。保存已有文件使用 `document save --file-path <文件路径>`；未保存文档首次落盘使用 `document save-as --doc <标识> --file-path <新路径>`，按各自 schema 调用。

## 导出

```powershell
catkit bom export --doc '总装.CATProduct' --view summary --output-file 'D:\任务\BOM.xlsx'
```

输出扩展名选择 `.xlsx` 或 `.csv`，内容与 collect 的同视图一致；使用 --columns/--custom-columns 选择列。覆盖策略是 --overwrite error/skip/replace；这不是 CATIA 弹窗的通用自动确认。

collect/export 使用 --view，不再接受 --summarize、--no-summarize 或输入 file_path(s)。需要多文档导出时，分别选择已打开文档，不复用 GUI 当前筛选状态。

`bom write` 是按 PN 字典写回的另一接口；精确实例编辑用 bom edit，不把 PN 当作实例唯一标识。

遇到 waiting_for_dialog/running/busy，按 [执行与任务接续](execution.md) 处理原任务，不重复提交修改。
