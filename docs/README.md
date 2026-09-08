# CATIA Copilot 文档

本目录包含 CATIA Copilot 项目的所有技术文档。

---

## 文档索引

### 核心文档

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计：模块划分、数据流、技术栈 |
| [API.md](API.md) | API 参考：核心模块接口、参数、返回值、字段映射关系 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 开发指南：环境搭建、调试、打包、发布流程 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南：代码规范、提交规范、PR 流程 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 故障排查：常见问题、错误码、解决方案 |

### 专题文档

| 文档 | 说明 |
|------|------|
| [PLM_WORKBENCH_PLAN.md](PLM_WORKBENCH_PLAN.md) | PLM 工作台设计方案 |
| [PLM_ISSUES.md](PLM_ISSUES.md) | PLM 集成已知问题记录（PLM-01 ~ PLM-07） |
| [CATIA_COM_CONNECTION_ISSUE.md](CATIA_COM_CONNECTION_ISSUE.md) | CATIA COM 连接问题排查记录；含多进程 COM 可访问性验证结论（2026-07-03） |
| [EMBED_PANEL.md](EMBED_PANEL.md) | CATIA 3D 视图嵌入面板实现文档 |
| [DIALOG_BEHAVIOR.md](DIALOG_BEHAVIOR.md) | 对话框窗口行为（置顶、跟随最小化、已知问题） |
| [PLAN_TRAY_AUTOSTART.md](PLAN_TRAY_AUTOSTART.md) | 托盘化与开机自启计划 |
| [THEMING.md](THEMING.md) | 主题系统实现说明 |
| [DEPENDENSIES.md](DEPENDENSIES.md) | CATIA 文件依赖关系调研笔记 |
| [AI_MODELING_PLAN_AND_ROADMAP.md](AI_MODELING_PLAN_AND_ROADMAP.md) | AI 驱动建模功能架构、设计决策与路线图 |
| [MODELING_HANDOFF.md](MODELING_HANDOFF.md) | AI 建模当前状态、已知问题与实现路线图 |
| [CATIA_PART_DOCUMENT_API.md](CATIA_PART_DOCUMENT_API.md) | CATIA Part Document COM API 参考 |
| [PYCATIA_OVERVIEW.md](PYCATIA_OVERVIEW.md) | pycatia 库概览与使用说明 |
| [BREP_NAMING_REFERENCE.md](BREP_NAMING_REFERENCE.md) | CATIA B-Rep 面命名规则参考 |

---

## 文档规范

### 命名规则

- **文件名**：使用 **大写蛇形命名**（`UPPER_SNAKE_CASE.md`）
  - ✅ 正确：`API.md`、`PLM_ISSUES.md`、`CATIA_COM_CONNECTION_ISSUE.md`
  - ❌ 错误：`api.md`、`plm-issues.md`、`catia_com_connection_issue.md`
- **标题**：使用中文或英文，首字母大写
- **章节锚点**：Markdown 自动生成，无需手动维护

### 内容结构

#### 核心文档（ARCHITECTURE / API / DEVELOPMENT 等）

```markdown
# 文档标题

简短说明（1-2 句话）

---

## 章节 1

内容...

---

## 章节 2

内容...
```

#### 专题文档（PLM_ISSUES / CATIA_COM_CONNECTION_ISSUE 等）

```markdown
# 问题标题

## 问题描述

详细描述...

## 复现步骤

1. ...
2. ...

## 根本原因

分析...

## 解决方案

方案 A：...
方案 B：...

## 相关资源

- 链接 1
- 链接 2
```

### 代码块规范

- **Python 代码**：使用 ` ```python ` 标记
- **Shell 命令**：使用 ` ```bash ` 标记
- **配置文件**：使用 ` ```json ` / ` ```yaml ` / ` ```toml ` 标记
- **输出日志**：使用 ` ```text ` 或 ` ``` ` 标记

### 表格规范

- 使用 Markdown 表格语法
- 表头使用 `|` 分隔，对齐使用 `|------|`
- 复杂表格（嵌套、合并单元格）改用列表或分段说明

### 链接规范

- **内部链接**：使用相对路径（`[API 参考](API.md)`）
- **外部链接**：使用完整 URL（`[GitHub](https://github.com/...)`）
- **代码引用**：使用 `文件路径:行号` 格式（`catia_copilot/constants.py:274`）

---

## 维护指南

### 何时更新文档

| 变更类型 | 需要更新的文档 |
|---------|---------------|
| 新增模块/功能 | `ARCHITECTURE.md`、`API.md` |
| 修改公开接口 | `API.md` |
| 修改常量定义 | `API.md`（`constants` 章节） |
| 新增依赖/工具 | `DEVELOPMENT.md` |
| 发现 PLM 问题 | `PLM_ISSUES.md` |
| 修改代码规范 | `CONTRIBUTING.md` |
| 新增故障排查方案 | `TROUBLESHOOTING.md` |

### 文档审查清单

提交文档变更前，确认：

- [ ] 文件名符合大写蛇形命名规则
- [ ] 代码块使用正确的语言标记
- [ ] 表格格式正确（对齐、分隔符）
- [ ] 内部链接使用相对路径
- [ ] 代码引用包含文件路径和行号
- [ ] 中英文之间有空格（可选，推荐）
- [ ] 更新了 `docs/README.md` 索引（如果新增文档）

---

## AI 协作说明

本项目使用 AI 辅助开发，文档遵循以下约定以便 AI 理解：

### 关键信息标记

- **版本号**：`v2.2.0`（带 `v` 前缀）
- **问题编号**：`PLM-01`、`PLM-06`（大写前缀 + 连字符 + 数字）
- **文件路径**：`catia_copilot/constants.py:274`（相对路径 + 冒号 + 行号）
- **配置项**：`` `PRESET_USER_REF_PROPERTIES` ``（反引号包裹）
- **状态标记**：`✅ 已完成`、`❌ 已废弃`、`🚧 进行中`（Emoji + 中文）

### 结构化信息

- **表格**：用于对比、映射、配置项说明
- **列表**：用于步骤、清单、枚举
- **代码块**：用于示例代码、配置文件、命令行输出
- **引用块**：用于注意事项、警告、提示

### 术语一致性

| 术语 | 说明 | 不要使用 |
|------|------|---------|
| `PartNumber` | CATIA 零件编号属性 | `part_number`、`零件号` |
| `UserRefProperties` | CATIA 用户自定义属性 | `用户属性`、`自定义字段` |
| `BomNode` | BOM 树节点数据结构 | `BOM节点`、`bom_node` |
| `instanceAttributes` | PLM 实例属性字段 | `instance_attributes`、`实例属性` |
| `checkout` / `checkin` | PLM 签出/签入操作 | `check out`、`check in`、`检出`、`检入` |

---

## 快速导航

- **我想了解项目架构** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **我想调用某个 API** → [API.md](API.md)
- **我想搭建开发环境** → [DEVELOPMENT.md](DEVELOPMENT.md)
- **我想提交代码** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **我遇到了错误** → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **我想了解 PLM 集成** → [PLM_WORKBENCH_PLAN.md](PLM_WORKBENCH_PLAN.md)
- **我遇到了 PLM 问题** → [PLM_ISSUES.md](PLM_ISSUES.md)
- **我想了解 AI 建模功能** → [AI_MODELING_PLAN_AND_ROADMAP.md](AI_MODELING_PLAN_AND_ROADMAP.md)
- **我想了解 AI 建模当前状态** → [MODELING_HANDOFF.md](MODELING_HANDOFF.md)
