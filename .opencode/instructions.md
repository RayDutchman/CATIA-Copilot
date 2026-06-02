# CATIA-Copilot 项目 AI Agent 指引

本文件记录项目关键设计决策，供同项目不同会话的 AI Agent 参考，避免重复踩坑。

---

## 0. 运行环境（重要）

**本项目完全运行在 Windows 原生环境（PowerShell），与 WSL 无关。**

- 当前 shell：**PowerShell**（非 bash，非 WSL）
- 运行时：Windows Python 3.13（`C:\Users\Chen Weibo\AppData\Local\Programs\Python\Python313\python.exe`）
- 命令行调用 Python：`python`（不是 `python3`）
- 路径类型：`WindowsPath`，形如 `D:\foo\bar\baz.CATPart`
- 项目路径：`D:\CATIA_Related\CATIA-Copilot`
- 文件路径比对用 `d.FullName == file_path` 精确匹配；扩展名过滤用 `.lower()` 大小写不敏感
- `docs.Open(file_path)` 直接传原始 Windows 路径字符串，不做转换
- `win32com` 已安装，可通过 `GetActiveObject('CATIA.Application')` 操作 CATIA COM 对象
- PowerShell 不支持 `&&`，链式命令用 `; if ($?) { cmd2 }` 或分开写

---

## 1. 项目概览

**版本**：1.9.0（2026-05-30）  
**入口**：`main.py`  
**主窗口**：`catia_copilot/ui/main_window.py`（`MainWindow` 类）

### Tab 结构（顺序固定）

| Tab | 内容 |
|-----|------|
| 工作台 | BOM 工作台、质量特性工作台、PLM 工作台 |
| 导出 | 从产品导出 BOM、从图纸导出 PDF、从产品/零件导出 STP |
| 图纸 | 新建图纸 (Python)、刷新图纸 (Python)、新建/刷新图纸 (VBScript) |
| 工具 | CATIA 资源 + 功能（刷写模板、快速装配、切换图纸/零件、查找文档、运行宏） |
| ≡ | 视图（日志、嵌入按钮、诊断、主题）+ 帮助 |
| AI 助手 | AI 聊天面板（`catia_copilot/ui/ai_chat_panel.py`） |

### 功能名称统一（`_ACTION_LABELS`）

所有功能名称集中在 `MainWindow._ACTION_LABELS` 字典，嵌入菜单和主菜单按钮共用，**不要硬编码功能名称**：

```python
_ACTION_LABELS = {
    "bom_edit":        "BOM 工作台",
    "bom_export":      "从产品导出 BOM",
    "mass_props":      "质量特性工作台",
    "plm_workbench":   "PLM 工作台",
    "export_pdf":      "从图纸导出 PDF",
    "export_stp":      "从产品/零件导出 STP",
    "drawing_new":     "新建图纸 (Python)",
    "drawing_refresh": "刷新图纸 (Python)",
    "stamp_template":  "刷写零件模板",
    "fastener_asm":    "快速装配紧固件",
    "nut_plate_asm":   "快速装配托板螺母",
    "open_related":    "在图纸/零件间切换",
    "find_deps":       "查找指向的文档",
    "run_macro":       "运行宏…",
}
```

对话框标题必须与对应的 `_ACTION_LABELS` 值一致。

---

## 2. 主题系统（ThemeManager）

**文件**：`catia_copilot/ui/theme_manager.py`，`native.qss`（分支 `feat/native-theme`，已替换 qdarkstyle）

**架构**：

```
QApplication.setStyle("windows11")          ← Qt Windows 11 原生风格
        + native.qss（占位符替换后）          ← 项目最小覆盖（仅日志字体、状态颜色等专属规则）
                    ↓
        QApplication.setStyleSheet(qss)
```

- **不再使用 qdarkstyle**，不再有 `dark.qss` / `light.qss`
- `windows11` 风格完全由系统 QPalette 驱动，深/浅色自动跟随 Windows 系统设置
- 回退：Qt < 6.7 时用 `windowsvista`（`_STYLE_NAME` 模块常量，在 `theme_manager.py` 顶层定义）

**颜色**：
- 所有聊天气泡、树状线、分隔线等颜色集中在 `ui_colors.py` 的 `get_chat_colors(mode)` / `get_colors(mode)`
- `get_colors(mode).WIDGET_LINE_COLOR`：BOM 树连接线颜色（浅色 `#b0bec5` / 深色 `#4a5568`）

**DWM 标题栏颜色**：
- `_apply_dwm_caption_color(dark)` 通过 `DWMWA_CAPTION_COLOR`（attr=35，Win11 专属）设置精确颜色
- 只做 attr=35，**不做** attr=20/19（交由 `windows11` 风格自管），避免产生 `Unable to set light window border` 警告
- `_DwmEventFilter` 监听 `QEvent.Show`，为后续打开的对话框补充标题栏颜色

**native.qss 占位符**（由 `_apply()` 在运行时替换为 `L.*` 常量）：

| 占位符 | 来源 |
|--------|------|
| `@mono_font_family` | `L.MONO_FONT_FAMILY` |
| `@mono_font_size_pt` | `L.MONO_FONT_SIZE_PT` |
| `@label_font_size_pt` | `L.LABEL_FONT_SIZE_PT` |
| `@hint_font_size_pt` | `L.HINT_FONT_SIZE_PT` |
| `@status_font_size_pt` | `L.STATUS_FONT_SIZE_PT` |
| `@button_font_size_pt` | `L.BUTTON_FONT_SIZE_PT` |
| `@tab_font_size_pt` | `L.TAB_FONT_SIZE_PT` |

**约束**：
- `native.qss` 只写项目专属规则，**不重写** `windows11` 风格的通用控件样式
- QSS 选择器用精确 `#objectName` 避免污染子树（如 `QTextBrowser#AIBubble`）
- 主题切换订阅：`from catia_copilot.ui.theme_manager import theme_signal; theme_signal.theme_changed.connect(slot)`
- `theme_manager.current_mode()` 返回 `"dark"` 或 `"light"`，无手动切换接口（完全跟随系统）

---

## 3. 对话框管理（`_show_dialog`）

所有非模态对话框通过 `MainWindow._show_dialog(attr, factory)` 统一管理：

- `setParent(None, Window | WindowStaysOnTopHint | ...)` — 独立顶级窗口，浮于所有窗口之上
- `WA_DeleteOnClose` — 关闭时销毁，`destroyed` 信号清理引用
- `setParent` 之后立即 `restoreGeometry`（`setParent` 会重建原生窗口并重置位置）
- 对话框跟随 CATIA 最小化/还原：500ms 定时器检测 `IsIconic(catia_hwnd)`，最小化前 `saveGeometry()` 保存运行时几何，还原后 `restoreGeometry()` 精确恢复

**新建对话框的标准模板**：见 `catia_copilot/ui/template_dialog.py`，包含：
- `_settings = QSettings("CATIACompanion", "XxxDialog")`
- `restoreGeometry` 在 `__init__` 末尾（会被 `_show_dialog` 的 `setParent` 覆盖，但 `_show_dialog` 会再次恢复）
- `closeEvent` 保存 `saveGeometry()`
- 不要调用 `theme_manager.register(self)`（只有主窗口才调用）

---

## 4. CATIA 3D 视图嵌入面板

**文件**：`catia_copilot/ui/catia_embed.py`（`CATIAEmbedManager` 类）

- Win32 原生子窗口，嵌入 CATIA MDI 区域（父窗口是 MDIClient）
- 后台线程运行 Win32 消息循环，通过 `_embed_action_signal = Signal(str, int)` 跨线程派发到主线程
- 菜单文字从 `MainWindow._ACTION_LABELS` 读取，与主菜单保持一致
- 菜单按工作台/导出/图纸/工具分区，分区间用分隔线
- 宏文件选中后通过 `run_macro_file` 回调 emit Signal，在主线程调用 `_run_macro()`，**不能在后台线程直接调用 COM**
- 嵌入按钮颜色硬编码（GDI 绘制），不受 Qt 主题影响

---

## 5. QTreeWidget 定制（BOM 树）

**文件**：`catia_copilot/ui/bom_widgets.py`

| 类 | 职责 |
|----|------|
| `_RowHeightDelegate` | 通过 `sizeHint()` 保证 24px 行高，不干涉背景绘制 |
| `_BomSortItem` | 数字列数值排序，避免 "10" < "2" |
| `_BomTreeDelegate` | 逐列只读控制 + 锁定行禁编辑，含 `sizeHint()` |
| `_BomTreeWidget` | 构造时安装 `_RowHeightDelegate`；替换委托后新委托需自带 `sizeHint()` |

**规则**：`setItemDelegate()` 替换默认委托时，新委托**必须**重写 `sizeHint()` 返回 ≥ 24px，否则行高退回 Qt 默认值（~17px）。

**为什么不用 `QTreeWidget::item { min-height }` QSS**：该规则会触发 Qt 样式引擎接管 item 背景绘制，导致 `setBackground()` 的 `BackgroundRole` 被覆盖，特殊行着色失效。

**`setAlternatingRowColors(True)` 已全部启用**（`feat/native-theme` 分支）：
所有使用 `_BomTreeWidget` 的对话框（`bom_edit_dialog`、`plm_workbench`、`mass_props_dialog`、`find_deps_dialog`）均已开启交替行色。
旧注释"禁用是因为 branch 区域出现竖条色块"**不成立**：该问题源于用 QSS `QTreeView::branch` 规则着色时 `:alternate` 伪元素不受支持。
当前实现完全用 `drawBranches()` 自绘连接线，不依赖 QSS branch 规则，因此交替行色与连接线互不干扰，可以安全开启。

---

## 6. CATIA COM 注意事项

- `application.Visible = True` 会触发 CATIA 内部 `ShowWindow(SW_SHOW)`，将最大化窗口还原为普通窗口。改用 `safe_set_visible(application)`（`catia_copilot/catia/utils.py`）
- `bring_catia_to_foreground()` 只在窗口最小化时才 `SW_RESTORE`（`IsIconic` 检查），不影响最大化状态
- COM 调用必须在 Qt 主线程（STA）中执行，不能在 Win32 后台线程直接调用
- `get_catia_v5_application()` 只连接已运行的 CATIA（`GetActiveObject`），不会启动新实例

---

## 7. 列宽策略

所有 `QHeaderView` 统一用 **Interactive + 初始宽度 + `setStretchLastSection(True)`**：

```python
hdr.setSectionResizeMode(i, QHeaderView.Interactive)
hdr.resizeSection(i, initial_px)
hdr.setStretchLastSection(True)
```

不用 `ResizeToContents`（数据量大时性能差）或纯 `Stretch`（禁止用户拖拽）。

---

## 8. 其他约定

- `PLM_SYNC_MAX_NODES = 100`（`catia_copilot/constants.py`）
- "使用活动文档" checkbox 在 `_build_ui` **完成后**再 `setChecked(True)`，避免控件未就绪时触发 `toggled`
- `convert_dialog._toggle_file_section` 用 `setEnabled` 而非 `setVisible`，避免窗口尺寸骤变
- 中文与专有名词（BOM / CATIA / CATPart / CATProduct / CATDrawing / COM / Excel 等）之间统一加空格
- 提交信息遵循 Conventional Commits 格式（`feat:` / `fix:` / `refactor:` / `chore:` / `style:` 等）
- **不要自动 push 到远端**，除非用户明确要求

---

## 9. AI 模块（feature/ai-agent 分支）

### 文件结构

```
catia_copilot/ai/
├── __init__.py
├── config.py      # 配置加载/保存、多 provider 路由、模型列表拉取
├── agent.py       # AgentWorker(QThread)，流式 SSE + 多轮工具调用循环
└── tools.py       # 14 个 CATIA 工具的 JSON Schema + 包装函数
catia_copilot/ui/ai_chat_panel.py  # 聊天面板 UI
```

### 配置文件

- `ai_config.json`：项目根目录，**已加入 .gitignore，不提交**
- `ai_config.example.json`：可提交的模板，直接照搬自 `Termux-Agent-Server/models_config.example.json`
- 格式与 `Standard-Agent-Server/models_config.json` **完全相同**，可直接照搬

### 参考项目

- **参考**：`D:\OpenCode_Workspace\Standard-Agent-Server`（server.py 是 agent.py 的参考实现）
- **不再参考**：`Termux-Agent-Server`（已被 Standard-Agent-Server 取代）

### COM 线程安全（方案 B）

```
AgentWorker(后台线程)
  ↓ emit tool_call_requested(name, args, id)
主线程 执行工具函数（COM 安全）
  ↓ worker.receive_tool_result(result)
 AgentWorker 通过 threading.Event 接收结果
```

### 工具包装规则

- `progress_signal` 参数由 `AIChatPanel._execute_tool_in_main_thread` 注入，不来自 LLM
- 返回值统一为 JSON 字符串，过滤掉 COM 对象和内部键（`_filepath`、`_placement` 等）
- `generate_drawing` / `refresh_drawing` 的 `input_callback` 由包装层从 `property_values` 参数构造

---

## 11. 建模层（`modeling.py` / `ModelingContext`）关键结论

> **AI Agent 行为要求**：每次对话中产生新的建模层结论（COM 行为、API 约束、已验证结论、已关闭/开放问题），
> 必须主动更新本章节，保持与最新代码同步，避免后续会话重复踩坑。

---

### 11.1 文件位置

| 文件 | 职责 |
|------|------|
| `catia_copilot/catia/modeling.py` | 所有建模 API（纯函数 + `ModelingContext` 类） |
| `catia_copilot/ai/tools.py` | `tool_run_modeling_script` + `DEFAULT_SYSTEM_PROMPT` |
| `docs/BREP_NAMING_REFERENCE.md` | COM 探查知识库（供人类查阅） |
| `experiments/explore_brepnames_v*.py` | 历史探查脚本 |

---

### 11.2 InWorkObject 机制（关键！）

**结论**：`part.update()` / `ctx.update_part()` 执行后，CATIA 会将 InWorkObject（IWO）指针停在
**最后一个固体特征**（如 `凸台.1`），而非 PartBody 根节点。

**影响**：此后调用 `InsertHybridShape` / `sketches.add` / `add_new_pad`，
新特征会被插入到 IWO 当前位置之后，导致特征树顺序倒序（Pad → Sketch → Plane）。

**修复原则**：在任何需要"追加到末尾"的批量插入操作之前，先重置 IWO：

```python
part.in_work_object = part.main_body  # 重置为 PartBody 根节点，后续插入追加到末尾
```

`add_sketch_at_height` 已在内部自动执行此重置，调用方无需手动处理。

**用户补充**：用户通常使用"自动更新模式"，且不手动调整 IWO，
因此 IWO 始终等于特征树最后一个特征。只要代码中不污染 IWO（或在操作前重置），
时间顺序正确的调用即可保证树顺序正确。

---

### 11.3 旋转体 / 环形槽：轴–平面–约束映射

| axis | 草图平面 | 旋转轴方向 | 半径方向 | 约束 |
|------|----------|-----------|---------|------|
| `"z"` | `"zx"` | V（Z 轴） | H（-X）| H > 0 |
| `"y"` | `"xy"` | V（Y 轴） | H（X） | H > 0 |
| `"x"` | `"xy"` | H（X 轴） | V（Y） | V > 0 |

`draw_rect(sk, x, y, w, h)` 中：x=H 起点，y=V 起点，w=H 方向宽度，h=V 方向高度。

旧版 System Prompt 中 `axis="y" → plane="yz"` 是错误的，已修正。

---

### 11.4 旋转轴线创建（`prepare_revolute_axis` / `_get_named_axis_ref`）

- 必须在 `add_sketch` **之前**调用 `ctx.prepare_revolute_axis(part, axis)`，
  否则轴线节点出现在草图节点之后，树顺序错误。
- 轴线使用 `HybridShapeFactory.AddNewLinePtPt` 创建，
  仅 `body.InsertHybridShape(line)` 插入线对象，点自动成为线的子节点。
- `_get_named_axis_ref(part_doc_com, axis)` 按 x/y/z dispatch 到对应的 `_get_x/y/z_axis_ref`，
  已在 `modeling.py` 中定义（曾因遗漏导致 `NameError`，已修复）。

---

### 11.5 `InsertHybridShape` vs `AppendHybridShape`

| 方法 | 适用对象 | 说明 |
|------|----------|------|
| `body_com.InsertHybridShape(shape.com_object)` | `PartBody`（固体体） | 向固体体直接插入线/面等混合形状 |
| `hybrid_body.AppendHybridShape(shape)` | `HybridBody`（几何图形集） | 向几何图形集追加，传 pycatia 对象 |

- `AppendHybridShape` 不存在于 PartBody，只存在于 HybridBody。
- `InsertHybridShape` 需传 COM 对象（`.com_object`），不能传 pycatia 包装对象。

---

### 11.6 在已有实体上继续建模（P2 回退方案）

**背景**：`CreateReferenceFromBRepName` 可以定位 B-Rep 面（idx=1 底面，idx=2 顶面），
但以此为草图支撑调用 `add_new_pad` 后 `update()` 必然失败（`WithTemporaryBody` 面引用不稳定）。

**当前可用方案**：偏移平面（稳定可靠）：

```python
sk = ctx.add_sketch_at_height(part, height=20, base_plane="xy")
# 等价于：在 z=20 处创建偏移平面，在其上建草图
```

- 内部流程：`add_new_plane_offset` → `InsertHybridShape` → `sketches.add`
- 特征树顺序：偏移平面 → 草图 → Pad（正确）
- 缺点：偏移平面无法随底层 Pad 高度参数联动（无关联性），对参数化建模有限制。

**已知 BRep 名格式**（备查，暂不用于 Pad 支撑）：

```
FSur:(Face:(Brp:(<feature_name>;<face_idx>);None:();Cf8:());
WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR3_SP2)
```

中文 CATIA 的特征名（`凸台.1`）和英文名（`Pad.1`）均可用于 `CreateReferenceFromBRepName`。

---

### 11.7 `Selection.Isolate`（已确认不可用）

- `Selection.Isolate` 不在 CATIA V5 COM IDispatch 表，Python 晚绑定调用报 `DISP_E_UNKNOWNNAME`。
- `GSMIsolate` 同样失败。
- **暂无 Python 调用路径**，隔离功能需通过 GUI 手动操作。

---

### 11.8 已验证可用的 API 列表

```python
ctx.create_part(name)
ctx.get_active_part()
ctx.update_part(part)
ctx.save_part(part, path)
ctx.add_sketch(part, plane)               # plane="xy"/"yz"/"zx"
ctx.add_sketch_at_height(part, h, base)   # 偏移平面草图，用于已有实体上继续建模
ctx.draw_rect(sk, x, y, w, h)
ctx.draw_circle(sk, cx, cy, r)
ctx.draw_point(sk, x, y)
ctx.add_pad(part, sk, depth)
ctx.add_pocket(part, sk, depth)           # 仅支持基准面草图
ctx.add_shaft(part, sk, axis="z")         # axis="x"/"y"/"z" 均已验证
ctx.add_groove(part, sk, axis="z")
ctx.add_hole_from_sketch(part, sk, d, depth)
ctx.prepare_revolute_axis(part, axis)     # 必须在 add_sketch 之前调用
ctx.list_features(part)
ctx.list_sketches(part)
ctx.get_mass_props(part)
```

---

## 10. 在 Windows PowerShell 下写入长文件的正确方法

`filesystem_write_file` 和 `write` 工具对含中文或特殊字符的长内容会报 JSON 解析错误。

**正确方法**：

```powershell
# 1. 用 WriteAllBytes 把 Python 脚本内容写到临时文件
$src = @'
# Python 脚本内容（中文用 \u 转义）
import pathlib
DEST = r"D:\path\to\target.py"
content = "\u4e2d\u6587内容..."
pathlib.Path(DEST).write_text(content, encoding="utf-8")
'@
$bytes = [System.Text.Encoding]::UTF8.GetBytes($src)
[System.IO.File]::WriteAllBytes("D:\tmp\gen_xxx.py", $bytes)

# 2. 执行脚本
python "D:\tmp\gen_xxx.py"
```

**要点**：
- Python 脚本中的中文必须用 `\u` 转义（如 `\u914d\u7f6e`）
- 临时脚本目录：`D:\tmp`（已存在）
- `filesystem_edit_file` 工具可正常工作，适合小段内容的替换
