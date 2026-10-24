---
name: catkit
description: Use when working with CATIA V5 documents, BOM, mass properties, drawing PDF or STEP exports, document properties, templates, or Python modeling scripts on Windows.
---

# Catkit

通过 Windows 原生 `catkit` 调用 CATIA；开发目录可用 `python -m catia_copilot.cli` 替代。
CLI 沿用已有工具的参数、默认活动文档及连接行为；使用 `connection.py` 连接入口的业务命令在 CATIA 未运行时可自动启动，`status` 本身仅查询连接状态。

## 查询命令

先按场景查 help/schema；只读对应参考，不需要读全部建模接口。

```powershell
catkit --help
catkit schema bom export
catkit schema drawing export pdf
catkit run --help
```

`schema` 路径用**空格分隔的 CLI 命令词**，不是 `bom.export` 或 Python 函数名。
不带路径的 `catkit schema` 列出命令映射；连接诊断用 `catkit status --diagnose`。

| 场景 | 按需参考 |
|---|---|
| 文档、属性、BOM、质量、导出、模板 | [tools.md](references/tools.md) |
| 创建几何或执行组合建模脚本 | [modeling.md](references/modeling.md) |

## 参数与结果

- CLI 文件参数使用 Windows 路径；不要给 COM 传 `/mnt/d/...`。建模脚本的 `ctx.save_part` 直接交给 CATIA SaveAs，使用原生反斜杠路径，如 `r"C:\任务\part.CATPart"`；不要依赖 SaveAs 接受正斜杠。
- 列表和对象参数接受 JSON 或 `@文件`。PowerShell 5.1 传含双引号的 JSON 容易变形，优先用 UTF-8 JSON 文件。
- 缺省参数沿用工具默认值；例如 BOM 的 `file_path=null` 是当前活动文档，导出 BOM 的 `[null]` 同样如此。
- stdout 是原工具结果 JSON，stderr 是日志/进度。检查退出码及 `error`、`success`、失败数量；不要仅凭命令返回就声称成功。
- `verification` 是可选结果验收，不额外要求用户提供。运行失败可能已有部分修改，依据返回步骤分析。

## 弹窗与原任务接续

正常执行业务命令即可，**不需要提前启动后台命令**。遇到 CATIA 弹窗会返回 `waiting_for_dialog`、`task_id`、提示文字和按钮/输入框 id；原操作仍在独立进程中等待。

```powershell
catkit dialog respond --task <task_id> --button <返回的按钮id>
catkit dialog respond --task <task_id> --control <返回的输入框id> --text "输入内容"
catkit task status --task <task_id>
catkit task wait --task <task_id> --timeout 30
```

响应按钮后命令继续等待原任务，可能返回最终结果或下一个弹窗。填写输入框后，使用新快照中的按钮 id 提交。
无弹窗但等待超过 30 秒时返回 `running`，用 `task wait` 接着取结果。**不要重复执行原业务命令**，否则可能重复创建或写入。
已有未完成任务时，新业务命令返回 `busy`、原任务 id 和 `executed:false`；先处理该任务，不把新命令当作已执行。
`worker_lost` 表示调用进程已退出且没有结果，不表示 CATIA 操作被撤销。弹窗文字决定选择什么按钮，不按“确定/是”一律点击。
检测由 Win32 进行，业务 worker 沿用既有连接策略。

## 示例

当前文档导出汇总 BOM CSV，无需构造带引号的路径 JSON：

```powershell
catkit bom export --file-paths '[null]' --summarize --output-format csv
```

已安装发布包不需要 Python；GUI 不再承担 AI 会话、模型配置或工具循环。
