---
name: catkit
description: Use when operating CATIA V5 on Windows through Catkit, including CATPart/CATProduct/CATDrawing documents, BOM, mass properties, PDF/STEP exports, properties, templates, scripted modeling, or pending CATIA dialogs and tasks.
---

# Catkit

通过 Windows 原生 CLI 操作 CATIA V5。本文是入口索引；参数以实际安装版本的 help/schema 为准，详细规则按场景读取。

## 找到入口

CATIA 与调用 CLI 的宿主使用同一 Windows 用户、同一交互会话，优先都以普通权限运行。连接异常的 UAC 排查见 [执行与任务接续](references/execution.md)。

安装器默认添加用户 PATH，优先直接使用 `catkit`。安装后已有终端或 AI 宿主需重新打开以获取新环境；若当前进程尚未更新 PATH，或使用了 `--no-path`，可临时直接调用：

```powershell
$homeDir = if ($env:CATKIT_HOME) { $env:CATKIT_HOME } else { Join-Path $env:LOCALAPPDATA 'Catkit' }
$catkit = Join-Path $homeDir 'runtime/catkit.exe'
& $catkit --help
```

下文 `catkit ...` 也可写成 `& $catkit ...`。路径仍不存在时确认实际安装位置。

## 按需读取

命中场景时，执行前读取对应参考；同一参考已读且未变化时不重复读取。

| 场景 | 参考 |
|---|---|
| 文档定位、属性、质量、模型树、图纸、导出、模板 | [操作路由与对象边界](references/tools.md) |
| BOM 完整/层级/汇总、共享或实例字段编辑、BOM 表格导出 | [BOM 操作](references/bom.md) |
| 新建几何、组合建模、编写或修复 `build(ctx)` | [脚本建模](references/modeling.md) |
| JSON 输入输出、判断执行结果、`running/busy/waiting_for_dialog`、处理弹窗 | [执行与任务接续](references/execution.md) |

## 调用顺序

1. 按上表选场景，再查看目标命令的 `--help` 或 `schema`，不猜子命令、参数或 ctx 方法。
2. 按该命令的目标规则选择文档/文件并执行；默认值由工具决定，不增加统一预检或强制 verification。
3. 按返回内容区分完成、部分失败和等待中的任务；等待中的原操作用任务 ID 接续，不重跑。

命令发现示例：

```powershell
catkit schema
catkit schema bom export
catkit schema run
```

`schema` 路径使用**空格分隔的命令词**，不是点号路径或 Python 函数名。`status` 只查询连接，`status --diagnose` 提供诊断；业务连接入口可按需启动 CATIA。

## 能力边界

建模统一使用 `run --file`，不拆成 `part feature create` 等不存在的命令。文件编辑、对话和记忆由宿主提供；Catkit 不负责 PLM、GUI AI 会话或任意第三方 Python 包环境。目标设计中的未实现能力不能替代安装版本的 schema。
