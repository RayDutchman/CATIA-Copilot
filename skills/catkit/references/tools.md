# 工具路由

完整参数以 `catkit schema <下表命令路径>` 为准，原参数下划线在 CLI 中写为短横线。

| 操作 | 命令路径 |
|---|---|
| 连接状态 / 详细诊断 | `status` / `status --diagnose` |
| 打开 / 列表 / 保存 | `document open` / `document list` / `document save` |
| 读取 / 写入标准及用户属性 | `document properties get` / `document properties set` |
| 正向 / 反向依赖 | `document dependencies list` / `document dependencies reverse` |
| 图纸找零件 / 零件找图纸 | `document find part` / `document find drawing` |
| BOM 采集 / 导出 / 写回 | `bom collect` / `bom export` / `bom write` |
| 装配质量采集 | `product mass collect` |
| 新建 / 刷新图纸 | `drawing create` / `drawing refresh` |
| PDF / STEP 导出 | `drawing export pdf` / `part export step` |
| 零件模板 | `part template apply` |

PDF 默认前缀 `DR_`，STEP 默认前缀 `MD_`；不需要时明确传空字符串。
例如先由宿主文件工具写 `D:/任务/drawings.json`，内容 `["D:/A.CATDrawing"]`，再调用：

```powershell
catkit drawing export pdf --file-paths '@D:/任务/drawings.json' --update-before-export
```

碰到原先需要弹窗选择的导出覆盖冲突，可用 `--overwrite replace` 或 `--overwrite skip` 表达处理方式。
业务值（PartNumber、属性键、文件路径）不因为 UI 语言而翻译。
