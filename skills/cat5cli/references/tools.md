# 工具路由

完整参数以 `cat5cli schema <下表命令路径>` 为准，原参数下划线在 CLI 中写为短横线。

| 操作 | 命令路径 |
|---|---|
| 连接状态 / 详细诊断 | `status` / `status --diagnose` |
| 打开 / 列表 / 保存 | `document open` / `document list` / `document save` |
| 当前 / 详情 / 激活 / 另存 / 关闭 | `document current` / `document info` / `document activate` / `document save-as` / `document close` |
| 读取 / 写入标准及用户属性 | `document properties get` / `document properties set` |
| 正向 / 反向依赖 | `document dependencies list` / `document dependencies reverse` |
| 图纸找零件 / 零件找图纸 | `document find part` / `document find drawing` |
| BOM 采集 / 编辑 / 导出 / 按 PN 写回 | `bom collect` / `bom edit` / `bom export` / `bom write`；先读 [BOM 操作](bom.md) |
| 装配质量采集 | `product mass collect` |
| 装配详情 / 实例树 | `product info` / `product tree` |
| MPD 离线合并 / 保持测量重读 | `product mass merge` / `product mass remeasure` |
| 零件详情 / 树 / 更新 / 质量 | `part info` / `part tree` / `part update` / `part mass` |
| 图纸页及视图生成源 | `drawing info` |
| 新建 / 刷新图纸 | `drawing create` / `drawing refresh` |
| PDF / STEP 导出 | `drawing export pdf` / `part export step` |
| 整装配 STEP 导出 | `product export step`（无 `--node` 实例导出） |
| 零件模板 | `part template apply` |

PDF 默认前缀 `DR_`，STEP 默认前缀 `MD_`；不需要时明确传空字符串。
例如先由宿主文件工具写 `D:/任务/drawings.json`，内容 `["D:/A.CATDrawing"]`，再调用：

```powershell
cat5cli drawing export pdf --file-paths '@D:/任务/drawings.json' --update-before-export
```

碰到原先需要弹窗选择的导出覆盖冲突，可用 `--overwrite replace` 或 `--overwrite skip` 表达处理方式。
业务值（PartNumber、属性键、文件路径）不因为 UI 语言而翻译。

## 新增查询及操作的目标

- 新增对象命令接受 `--doc <key/路径/无歧义名称>` 或 `--active`，二者互斥；省略则使用活动文档。原工具仍按各自 schema 的路径参数调用。
- `document current/info` 的 `saved` 是 CATIA 的“自上次保存后无修改”标记，不保证文件曾写入磁盘；`full_name` 在未保存时可能仅为文档名。
- `document save-as --file-path <路径>` 保留原文件；`document close` 不自动丢弃修改，出现 CATIA 提示按任务接续处理。
- `part tree` 返回 Body、特征、草图、几何图形集及有序几何图形集，不展开特征参数、布尔吸收体；读取失败返回 `complete:false/errors` 和非零退出码，不把部分结果当完整树。
- `product tree --node 0.0` 只读指定子树；节点路径随树变化失效，操作前重新查询。
- `drawing info` 通过各视图 `GenerativeBehavior.Document` 读取生成源文档，不枚举全部底层几何链接；读取失败明确返回 errors。
- `part mass` 和 `product mass remeasure` 单位为 kg、mm、kg·mm²；后者只重读已打开零件的保持测量，不等于强制 Analyze，也不写回 MPD。
- `product mass merge --files @inputs.json --output-file merged.mpd` 复用现有 MPD 合并算法，要求输入同根坐标系，不做额外坐标变换；支持 `--overwrite error|skip|replace`。
- `product export step --files @products.json` 导出整份 CATProduct；文件集与 `--doc/--active` 互斥，结果包含逐文件状态与失败/跳过数量。文件数组的 `--files` 是 `--file-paths` 别名，不可重复指定。
