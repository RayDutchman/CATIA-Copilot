# CATIA-Copilot 项目 AI 指引

## 运行环境

- Shell：**PowerShell**（非 bash，非 WSL）；链式命令用 `; if ($?) { cmd2 }`
- Python：Windows 原生 3.13，命令 `python`（非 `python3`）
- 路径：`WindowsPath`，形如 `D:\foo\bar.CATPart`；`docs.Open()` 直接传 Windows 路径
- `win32com` 已安装，`GetActiveObject('CATIA.Application')` 连接 CATIA（不会启动新实例）
- COM 调用必须在 Qt 主线程（STA），不能在后台线程直接调用

---

## 项目结构速查

```
main.py                          # 入口
catia_copilot/
  ai/
    agent.py                     # AgentWorker(QThread)，流式 SSE + 多轮工具调用
    tools.py                     # 所有 CATIA 工具的 JSON Schema + 包装函数 + DEFAULT_SYSTEM_PROMPT
    config.py                    # AI 配置加载/保存，兼容 Standard-Agent-Server 格式
    session.py / session_manager.py  # 会话数据结构与持久化
  catia/
    modeling.py                  # 所有建模 API（纯函数 + ModelingContext）
    connection.py                # get_catia_v5_application()
    bom_collect/export/write.py  # BOM 采集、导出 Excel、写回 CATIA
    conversion.py                # STEP / PDF 导出
    drawing_operations.py        # 图纸新建/刷新
    template.py                  # 零件模板刷写
    dependencies.py              # 文档正/反向依赖查找
    mass_props_collect.py        # 质量特性采集
  ui/
    main_window.py               # 主窗口，Tab 结构 + _ACTION_LABELS
    ai_chat_panel.py             # AI 聊天面板
    theme_manager.py             # 主题（windows11 风格 + native.qss）
    catia_embed.py               # CATIA 3D 视图嵌入面板（Win32 子窗口）
    bom_edit_dialog.py / bom_widgets.py  # BOM 工作台
    [其余 dialog 文件各对应一个功能对话框]
  constants.py                   # APP_VERSION（版本号唯一来源）、PLM_SYNC_MAX_NODES 等
ai_config.json                   # AI 配置（已 .gitignore，不提交）
ai_config.example.json           # 可提交的配置模板
```

---

## docs 目录文件索引

> 遇到相关问题时，优先查阅对应文档，不要凭记忆猜测。

| 文件 | 内容 |
|------|------|
| `ARCHITECTURE.md` | 整体架构、模块划分、关键设计决策 |
| `AI_ARCHITECTURE_FLOW.md` | AI 建模请求全链路说明（含时序图，以"建圆筒"为例） |
| `AI_MODELING_PLAN_AND_ROADMAP.md` | AI 建模层完整计划：已完成 API 列表、待完成方向、关键技术结论 |
| `BREP_NAMING_REFERENCE.md` | CATIA B-Rep 面/边命名格式实测参考（COM 探查知识库） |
| `CATIA_COPILOT_MODULES_API.md` | 项目各核心模块的公开接口文档（bom_collect、conversion、plm 等） |
| `CATIA_PART_DOCUMENT_API.md` | win32com 访问 CATIA V5 PartDocument 各类 COM 对象的实测 API |
| `CATIA_COM_CONNECTION_ISSUE.md` | Python/win32com 连接 CATIA V5 失败的根因与修复方案 |
| `CATIA_DRAWING_PART_LINKAGE.md` | CATPart 与 CATDrawing 反向关联的 CATIA 原生查找方案 |
| `CATIA_EMBED_PANEL_DESIGN.md` | 嵌入 CATIA 3D 视图窗口的面板实现方案 |
| `BOM_DATA_MODEL_V3_PLAN.md` | BOM 数据模型 V3 设计（解决多实例属性同步问题） |
| `PLM_WORKBENCH_PLAN.md` | 独立 PLM 工作台窗口开发计划 |
| `PLM_ISSUES.md` | 与 DocdokuPLM 对接发现的服务端问题记录 |
| `DRAWING_PYTHON_SUMMARY.md` | 图纸操作从 VBScript 改写为 Python 的完成总结 |
| `DRAWING_PYTHON_TEST_GUIDE.md` | 图纸操作 Python 改写后的测试指南 |
| `PYCATIA_OVERVIEW.md` | pycatia 各模块主要类与功能摘要（重点：SAFEARRAY ByRef 封装） |
| `PYTHON_COM_TYPE_CHECK.md` | win32com 中正确检测 CATIA 文档类型的方法 |
| `THEMING.md` | 主题系统：windows11 风格 + native.qss + DWM 着色 + ChatColors/RowColors 动态颜色 |
| `DIALOG_BEHAVIOR.md` | 所有对话框的窗口行为、置顶机制、已知问题 |
| `TRAY_AUTOSTART_PLAN.md` | 程序托盘化与随 CATIA 自启动的实施计划（未实施） |
| `DEVELOPMENT.md` | 本地开发环境搭建、测试运行、打包、版本号管理 |
| `CONTRIBUTING.md` | 代码提交流程与规范 |
| `TROUBLESHOOTING.md` | 常见问题排查（连接、BOM、宏、PLM、打包） |
| `TEST_DRAWING_VALIDATION.md` | 图纸 COM 调用可行性验证测试说明 |
| `TODO_AI_TOOLS_SCHEMA_FIXES.md` | AI 工具 JSON Schema 修复清单（已完成，归档备查） |
| `TODO_MULTI_SESSION_WORKSPACE.md` | 多 Session + 工作空间功能实施规划 |
| `TEXT_TO_CAD_INSPIRATION.md` | text-to-cad 项目对本项目 AI 建模部分的参考与借鉴 |

---

## 关键约定（必读）

### 功能名称
所有功能名称集中在 `MainWindow._ACTION_LABELS`，对话框标题必须与其一致，不要硬编码。

### UI / 主题约定

- **主题跟随系统**：始终使用 `windows11` 风格 + `native.qss`，无手动切换；`ThemeManager` 是唯一入口
- **颜色动态取色**：聊天面板用 `get_chat_colors()`，表格行用 `get_colors()`，颜色从系统 QPalette 动态获取
- **字体常量集中**：所有字号/间距/尺寸常量在 `ui_layout.py` 的 `L` 单例，不要硬编码数字
- **QSS 选择器用精确 `#objectName`**：避免污染子树；不要写通用标签选择器（如 `QFrame { }`）
- **emoji/符号按钮**：统一指定 `QFont("Segoe UI Emoji")`（如 Braille 转圈动画）
- **文件写入**：`filesystem_write_file` 用正斜杠路径；超长文件用 PowerShell here-string + 临时 py 脚本

### import 规则（PyInstaller / Nuitka 打包）
所有 `import` 必须在**模块顶层**，禁止函数体内懒加载。  
唯一例外：`catia_embed.py` 中有一处循环依赖标注了 `# noqa: PLC0415`。

### 版本号
唯一来源：`catia_copilot/constants.py` 的 `APP_VERSION`。  
升版时同步更新：`pyproject.toml`、`README.md`、`docs/README.md`、`CHANGELOG.md`、`setup.iss`。

### 不要自动 push / commit
除非用户明确要求，不提交、不推送。

### 文件写入
`filesystem_write_file` 对含中文的长内容有时报 JSON 解析错误。  
备用方案：用 PowerShell `[System.IO.File]::WriteAllBytes` 写临时 Python 脚本再执行（见下方）。

```powershell
$src = @'
import pathlib
content = "\u4e2d\u6587\u5185\u5bb9"
pathlib.Path(r"D:\target\file.py").write_text(content, encoding="utf-8")
'@
$bytes = [System.Text.Encoding]::UTF8.GetBytes($src)
[System.IO.File]::WriteAllBytes("D:\tmp\gen.py", $bytes)
python "D:\tmp\gen.py"
```

---

## AI 建模层速查（modeling.py / ModelingContext）

> 详细文档：`docs/AI_MODELING_PLAN_AND_ROADMAP.md`、`docs/BREP_NAMING_REFERENCE.md`

### 已验证可用 API

```python
ctx.create_part(name)
ctx.get_active_part()
ctx.update_part(part)
ctx.save_part(part, path)
ctx.add_sketch(part, plane)               # plane="xy"/"yz"/"zx"
ctx.add_sketch_at_height(part, h, base)   # 偏移平面，用于在已有实体顶面继续建模
ctx.add_sketch_on_pad_top/bottom/side(part, pad)  # B-Rep 面直接支撑草图
ctx.draw_rect(sk, x, y, w, h)
ctx.draw_circle(sk, cx, cy, r)
ctx.draw_arc(sk, ...) / draw_line / draw_slot / draw_point
ctx.add_pad(part, sk, depth)
ctx.add_pocket(part, sk, depth)
ctx.add_shaft(part, sk, axis="z")         # axis="x"/"y"/"z"
ctx.add_groove(part, sk, axis="z")
ctx.add_hole_from_sketch(part, sk, d, depth)
ctx.add_fillet_edges(part, edge_refs, r)
ctx.add_auto_fillet(part, r, inner_r)
ctx.add_chamfer(part, edge_refs, ...)
ctx.add_rect_pattern / add_circ_pattern   # ⚠ 方向参数有 bug，暂不可用
ctx.prepare_revolute_axis(part, axis)     # 必须在 add_sketch 之前调用
ctx.list_features(part) / list_sketches(part)
ctx.get_mass_props(part)
ctx.get_pad_faces / get_pad_faces_by_normal / get_pad_face_edges
ctx.get_pocket_faces / get_pocket_face_edges / get_pocket_opening_edges
ctx.get_shaft_faces / get_shaft_face_edges
```

### 高频陷阱

**InWorkObject（IWO）**：`update_part()` 后 IWO 停在最后一个固体特征，后续 `add_sketch` / `InsertHybridShape` 会插在错误位置。操作前重置：`part.in_work_object = part.main_body`。`add_sketch_at_height` 内部已自动重置。

**旋转体轴–平面映射**：

| axis | 草图平面 | 半径方向 | 约束 |
|------|----------|---------|------|
| `"z"` | `"zx"` | H（-X） | H > 0 |
| `"y"` | `"xy"` | H（X）  | H > 0 |
| `"x"` | `"xy"` | V（Y）  | V > 0 |

`prepare_revolute_axis` 必须在 `add_sketch` **之前**调用，否则特征树顺序错误。

**B-Rep 草图支撑**：`CreateReferenceFromBRepName` 返回 `WithTemporaryBody` 引用不稳定，改用 `Selection_RSur:` 格式（`add_sketch_on_pad_top/bottom/side` 已封装）。

**COM 线程**：`AgentWorker` 在 `QThread` 运行，工具函数通过 `tool_call_requested` signal 回调主线程执行，不能在后台线程直接调用 COM。

**`application.Visible = True`**：会将最大化窗口还原为普通窗口，改用 `safe_set_visible(application)`。
