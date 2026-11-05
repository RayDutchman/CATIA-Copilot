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
cat5cli document open --file-path 'D:\项目\总装.CATProduct'
cat5cli bom collect --view hierarchical
cat5cli bom collect --doc '总装.CATProduct' --view full
```

采集结果提供行数据、列和目标定位信息。编辑的 node 使用返回的目标路径或 product tree 的路径，不用表格行号、显示名或猜测的 PN 代替。装配结构变化后重新查询；同 PN 不一定代表同一引用文件。

### 自定义属性自动发现

collect/export 省略 --custom-columns 时，自动枚举整个装配各主数据的 UserRefProperties，按首次发现顺序合并去重为列；三种视图一致，某行不存在该属性时留空。返回的 custom_columns 是本次自定义列清单，不包含几何参数。

CATIA COM 的参数 Name 可能为“产品名\属性组\字段名”；CLI 去掉对象路径，以字段名合并列，不修改 CATIA 参数名称。

### 两种标准描述

- `Description`：产品/零件主数据（PartMaster）的 `DescriptionRef`，对应属性窗口下方“产品 → 描述”；通过 master 编辑，共享引用联动。
- `description_inst`：部件实例的 `DescriptionInst`，对应上方“部件 → 描述”；当前可读、不可通过 bom edit 写入。
- 用户自定义“备注”是 UserRefProperties 中的另一字段，与上述两种标准描述无关。

- 显式列表只读指定属性，例如 --custom-columns '@columns.json'，文件内容为 `["物料编码", "备注"]`。
- 显式 `--custom-columns '[]'` 关闭自定义列，只使用标准列。
- 枚举失败明确报错，不把失败视为没有属性；自动发现的名称若与 Quantity 等保留字段冲突会报错，可用显式列表选择其他属性。

## 字段编辑

由宿主写 UTF-8 JSON，例如 `changes.json`：

```json
[
  {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"},
  {"node": "0.1", "scope": "instance", "column": "Instance Name", "value": "Bracket.2"}
]
```

```powershell
cat5cli schema bom edit
cat5cli bom edit --doc '总装.CATProduct' --changes '@D:\任务\changes.json'
```

- master 修改共享主数据，可能影响多个引用实例；instance 修改实例定义，目前可写列为 Instance Name。重复引用同一子装配时，其内部实例定义也可能在多个出现路径联动。
- 实例改名写入父级子装配定义，并重新沿目标出现路径回读；只有名称一致才计入 applied_count。results 中 observed_value 是实际回读值；不一致或无法回读时返回 failed，不代表已撤销写入，不应盲目重跑整批。
- 列名使用契约中的业务键，value 为字符串。自定义用户属性需在 --custom-columns 显式声明并使用 master；已有属性更新，不存在时新建字符串属性。新建不支持指定数值/长度等参数类型；只读结构列、实例描述不能冒充自定义属性写入。
- CLI 不限定公司的自定义属性名单或枚举值；公司填写规范由适用 Skill 指导。`document properties set` 的 user_defined 同样不受 GUI 预设名单约束。
- 查看 applied_count、failed_count 和逐项 results。失败项不会让成功项回滚，也不会自动重跑整批；依据结果修正必要项。
- 写回不等于保存。保存已有文件使用 `document save --file-path <文件路径>`；未保存文档首次落盘使用 `document save-as --doc <标识> --file-path <新路径>`，按各自 schema 调用。

## 导出

```powershell
cat5cli bom export --doc '总装.CATProduct' --view summary --output-file 'D:\任务\BOM.xlsx'
```

输出扩展名选择 `.xlsx` 或 `.csv`，内容与 collect 的同视图一致；使用 --columns/--custom-columns 选择列，省略 --custom-columns 时导出自动发现的全部用户属性列。覆盖策略是 --overwrite error/skip/replace；这不是 CATIA 弹窗的通用自动确认。

CLI 导出的标准表头采用英文业务键（例如 Part Number、Quantity），Source 保留采集结果中的原始代码；用户自定义列名和值保持原文。GUI 工作台自己的显示表头不受 CLI 导出格式影响。

collect/export 使用 --view，不再接受 --summarize、--no-summarize 或输入 file_path(s)。需要多文档导出时，分别选择已打开文档，不复用 GUI 当前筛选状态。

`bom write` 是按 PN 字典写回的另一接口；精确实例编辑用 bom edit，不把 PN 当作实例唯一标识。

遇到 waiting_for_dialog/running/busy，按 [执行与任务接续](execution.md) 处理原任务，不重复提交修改。
