# 更新日志

本文件记录 CATIA Copilot 各版本的主要变更。

---

## [2.2.0] — 2026-07-01

### 新增

- **AI 建模完整工具链**：AI Agent 可通过 `build(ctx)` 脚本自主完成复杂零件建模（PR #70）。
- **几何查询 API（方向 B）**：`get_pad_faces` / `get_pad_faces_by_normal` / `get_pad_face_edges` 等，支持按法向筛选面、获取面所有边引用，纯从草图坐标系推导，不依赖 SPA。
- **BRep 边引用扩展（方向 A）**：Pocket（底楞/侧楞/开口楞）、Shaft（旋转体相邻面交线）、圆柱 Pad 侧面格式已验证并封装。
- **自动圆角 `add_auto_fillet`**：等价于 CATIA GUI「自动圆角」，无需逐边指定，一键对所有适合的边施加圆角。
- **System Prompt 参数化约束**：要求 AI 将所有关键尺寸声明为 `build(ctx)` 开头具名变量，禁止硬编码数字。
- **ModelStateDialog**：📊 按钮弹出独立窗口，展示当前零件特征树、质量重心、步骤日志，每次建模后自动刷新。
- **多模型提供者支持**（`feat/multi-provider`）：支持 OpenAI / Anthropic / OpenRouter / DeepSeek / Ollama / 讯飞星火 / AWS Bedrock / Google Vertex AI / GitHub Copilot / 自定义端点 共 10 种 LLM 后端。

### 修复

- **`create_part` 命名无效**：`part.part.PartNumber`（`CATIAPart` 无此属性）→ `Product.PartNumber`，新增 `nomenclature` 参数写入 `Product.Nomenclature`。
- **`add_hole_from_sketch` 签名错误**：COM 方法仅接受 `(sketch, depth)` 两个参数，孔径通过 `hole.diameter.value` 单独设置。
- **`add_shaft` / `add_groove` 轴线树顺序**：先建轴线再建特征，避免树中轴线出现在 Shaft/Groove 之后。
- **`_pad_geometry` 草图边数穷举越界**：改用 `GeometricElements` 计数（排除坐标轴和点），不再用 BRep 枚举试探。

### 已有 API 现状

| 分类 | 已实现 | 暂不可用 |
|------|--------|----------|
| 草图 | 6 种平面（`xy`/`yz`/`zx` + BRep 顶/底/侧面） | — |
| 草图绘图 | `draw_rect` / `draw_circle` / `draw_arc` / `draw_line` / `draw_slot` / `draw_point` | — |
| 实体特征 | `add_pad` / `add_pocket` / `add_shaft` / `add_groove` / `add_hole_from_sketch` | Rib / Loft / Stiffener |
| 修饰 | `add_fillet_edges` / `add_auto_fillet` / `add_chamfer` | Shell / Draft / Thread |
| 变换 | — | Mirror |
| 阵列 | — | `add_rect_pattern` / `add_circ_pattern`（方向参数 bug） |
| 查询 | `list_features` / `list_sketches` / `get_mass_props` / 几何查询全套 | — |

---

## [2.1.0] — 2026-06-06

### 新增

- **BOM 多实例同步（V2 对话框）**：修改任意实例的属性（PartNumber、Nomenclature 等）后，同 PartNumber 的所有其他实例（完整 BOM 模式）及不同父节点下的同零件汇总行（层级 BOM 模式）均自动同步更新界面，无需刷新。
- **BOM V2 `PlmWorkbench` 改为 `QDialog`**：移除中间 `QWidget` 壳，原生支持 ESC 关闭，与其他对话框风格一致。
- **质量属性对话框 — "刷新质量特性"右键菜单**：
  - 完整 BOM 模式：新增"刷新质量特性（子树范围）"，通过缓存 `_product` COM 引用直接重测选中子树内所有零件的质量特性（含 mat4 重读）及子树外同 PN 兄弟实例（复用测量结果，保留各自 mat4），按当前面板选择的 Analyze / 惯量包络体方式执行。
  - 汇总 BOM 模式：新增"刷新质量特性"，仅重测当前行对应零件，不涉及 mat4。
  - 两种模式均通过 `_product` COM 引用直接测量，无需文件已保存到磁盘。
- **质量属性对话框 — 实例名（Instance Name）列**：完整 BOM 模式下显示每个节点的 `product.Name`（实例名）；汇总 BOM 模式下强制隐藏（多实例合并后无意义）。
- **质量属性对话框 — 对称件实例名标识**：对称件的 Instance Name 列显示为"原件实例名（对称件）"，直接指明是哪个实例的对称件。
- **质量属性对话框 — "完整 BOM" 改名**：原"层级 BOM"按钮及相关文案统一改为"完整 BOM"，与 BOM 工作台术语一致。
- **`bom_collect.py` 同步改进**：引入 `_com_unk()` / `_inst_to_product` / `_inst_to_items` / `_ref_to_insts` / `_inst_to_ref_unk` 索引体系，以 `id(product)` 为实例 key，以 PartNumber 为同零件识别 key，实现 O(1) 兄弟行查找。删除已无用的 `_product_extras` 字段。

### 修复

- **BOM V2 PN 修改时兄弟行不联动**：修改 PartNumber 时，`_canonical_data` 已更新为新值，但 `_ref_to_insts` key 仍为旧 PN 导致查找失败；修复为用旧 PN 完成同步后再迁移 key。
- **BOM V2 PN 冲突检查误拦同零件实例**：同文件多实例的 PartNumber 相同，被冲突检查误判为冲突；修复为跳过同 `_inst_to_ref_unk` 的所有兄弟实例。
- **BOM V2 `_auto_rename_instance_names` 使用旧 PN**：自动改名时从 `_full_rows` 读取 PN，未查 `_canonical_data`；修复为优先读取规范数据。
- **质量属性对话框"重新读取质量特性"不区分数据来源**：原菜单项始终用 `keep_inertia` 路径；已删除，统一由新"刷新质量特性"替代，按当前面板设置执行。

### 杂项

- `collect_bom_rows_archive` 与 `bom_collect.py f003fda` 版本对齐：删除 `extras` 收集逻辑，row dict 去除 `_product` / `_reference_product` 字段，保持作为对比测试基准的独立性。
- `mass_props_collect.py`：row dict 新增 `"_product"` 字段供子树刷新使用；`_SERIALIZE_SKIP` 增加 `"_product"`，保持 `.mpd` 文件向前兼容。
- `mass_props_collect.py`：row dict 新增 `"Instance Name"` 字段（`product.Name`）。

---

## [2.0.1] — 2026-06-05

### 新增 — BOM 工作台 V2（即时写回版）

- **`bom_edit_dialog_v2.py`**：全新 BOM 工作台 V2，每次单元格编辑后立即通过缓存 COM 引用写回 CATIA，无需点击"应用"按钮批量提交。
- **完整 BOM 模式**：新增"完整 BOM"单选按钮，每个装配实例单独一行；含可编辑"实例名"列，直接写回 `product.Name`。
- **`collect_bom_rows_full()`**：逐实例遍历产品树，经 `ReferenceProduct.Products.Item(i)` 取得可写实例引用（修复 `instance.Products.Item(i)` 代理对象 `.Name` setter 静默 no-op 的根因）。
- **`build_hierarchical_rows()`**：纯 Python 后处理，将完整行重组为层级视图，切换显示模式无需重新遍历 CATIA。
- **`write_cell()`**：通过缓存 COM 引用单格直接写入，含实例名分支。
- **右键菜单新增"自动修改实例名（子树范围）"**：对选中节点的子树按 `PartNumber.n` 规则递归重命名实例名。
- **右键菜单新增"自动修改文件名（子树范围）"**：对选中节点子树中文件名与零件编号不符的文件批量另存为改名；替代原工具栏"按零件编号修改文件名"按钮。

### 修复

- **实例名列切换后不消失**：从"完整 BOM"切换到"层级 BOM"时，实例名列仍然显示的问题——为"层级 BOM"单选按钮补充 `_on_hierarchical_bom_toggled` handler，正确重置 `_full_bom = False` 并重建列。
- **`_on_bom_type_changed` 逻辑简化**：各 BOM 模式的单选按钮均只在 `checked=True` 时处理，消除旧代码中 `False` 分支互相干扰的问题。

### 样式

- BOM 类型选择器顺序调整："完整 BOM"移至最前（完整 BOM → 层级 BOM → 汇总 BOM）。

---

## [2.0.0] — 2026-06-04

### 新增 — AI Copilot 助手

- **AI 对话面板**：新增独立 AI Tab，支持流式 SSE 输出、Markdown 渲染、工具调用卡片展示；对话历史持久化，重启后自动恢复。
- **多会话管理**：左侧会话列表，支持新建 / 删除 / 重命名会话；工作区沙盒限制（AI 只能操作项目内文件）。
- **多 Provider 支持**：支持 OpenAI、Anthropic、Gemini 等多家服务商；Settings 对话框可配置 API key、base URL、模型；一键刷新可用模型列表。
- **CATIA 工具集（24 个）**：AI 可直接操作 CATIA —— 读写文档属性（零件编号、术语、版本、来源、描述、自定义 `UserRefProperties`）、BOM 采集/写回、导出 PDF/STP、查找依赖、执行宏、读写文件系统、运行建模脚本等。
- **默认系统提示**：内置工具使用指引（文档属性 vs. BOM 写回的适用场景、建模规范、错误自纠正策略）。
- **AI 对话界面优化**：统一气泡颜色（`QPalette` 动态读取）、工具调用卡片布局、分隔线 2px 统一、`AISplitter` 可拖动手柄。

### 新增 — AI 驱动建模（Phase 1）

- **建模层 `catia/modeling.py`**：封装 pycatia CATPart API —— `create_part` / `add_sketch` / `draw_rect` / `draw_circle` / `add_pad` / `add_pocket` / `add_hole_from_sketch` / `add_edge_fillet` / `add_chamfer` / `add_rect_pattern` / `add_circ_pattern` / `list_features` / `get_mass_props` 等。
- **`run_modeling_script` 工具**：AI 生成 Python 建模脚本 → 通过 `importlib` 动态执行 `build()` 函数 → 返回零件名 + 特征列表 + 质量特性；失败时返回完整 traceback 供 AI 自纠正。
- **`ModelingContext`**：逐步执行上下文，结构化反馈每一步的执行状态。
- **质量特性新增 `source=analyze` 模式**：通过 pycatia 直接调用 CATIA 质量分析（原只支持 COM 读取缓存值）。
- **质量特性内部单位切换**：内部长度单位从 m 统一切换至 mm，避免换算误差。

### 新增 — BOM 编辑增强

- **首行内容填充**：右键菜单「首行内容填充」——多行选中时，将最上方所选行的值批量填入其余选中行；支持文本列与下拉列。
- **序列填充**：右键菜单「序列填充」——对话框驱动，支持数字递增和字母（A–ZZ）递增，可设前缀/后缀（带 `QSettings` 持久化）、3 行实时预览；自动去重重复 PN、冲突检查。

### 新增 — 对话框置顶开关

- 主界面新增「置顶」开关按钮；不置顶时停止监听 CATIA 最小化状态，降低后台 CPU 占用。

### 重构 — 主题系统完全重写

- **统一切换为 Windows 原生主题**：移除 qdarkstyle 和深色/浅色手动主题；始终使用 Qt `windows11` 风格渲染器 + DWM 系统配色；`native.qss` 只保留项目专属控件样式。
- **`ui_colors.py`**：`ChatColors` 改为 `get_chat_colors()` 动态函数，在调用时从系统 `QPalette` 读取颜色，随系统深浅色自动切换。
- **移除主题切换按钮**：主窗口不再有主题切换入口；深浅色跟随系统。
- **DWM 标题栏**：跟随系统深浅色配置，消除 DWM 深色边框警告。
- 从 `requirements.txt` 移除 `qdarkstyle` 依赖。

### 重构 — COM 层下沉

- **`catia/connection.py`**：新增 `get_active_document_path()` 和 `open_document()`，统一 6 处调用点；调用方不再需要直接持有 CATIA `Application` 对象。
- **`catia/document.py`**：将 `rename_document` / `save_as_document` COM 逻辑从 UI 层下沉；集中文档/节点类型判断逻辑（`get_document_type`）；集中 `_READABLE_ATTRS` / `_WRITABLE_ATTRS` / `_SOURCE_TO_DISPLAY` 常量。
- **`constants.py`**：集中 COM 属性映射表（`PRODUCT_ATTR_READ_MAP`、`PRODUCT_ATTR_WRITE_MAP`）。
- **`utils.py`**：合并 `catia/utils.py`，统一 `open_catia_file` / `bring_catia_to_foreground` 入口。

### 重构 — UI 布局常量集中化

- **`ui_layout.L`**：新增字体常量块（`MONO_FONT_FAMILY`、`MONO/SMALL/NORMAL/LARGE_FONT_SIZE_PT`、`TABLE_ROW_HEIGHT`、`TABLE/LABEL/HINT/STATUS_FONT_SIZE_PT`、`BUTTON_FONT_SIZE_PT`、`TAB_FONT_SIZE_PT`）；全库所有硬编码尺寸替换为 `L.*` 引用。

### 重构 — 对话框行为简化

- **彻底移除对话框跟随 CATIA 最小化/还原逻辑**：删除 `_start_catia_monitor`、`_check_catia_state`、`_hidden_dialogs`、`_dialog_geometries` 及相关回调，主窗口减少约 83 行；对话框完全由用户自主管理。

### 重构 — 全库消除懒加载

- **所有模块提升到顶层导入**：全面扫描并将 `ai/`、`catia/`、`plm/`、`ui/`、`utils.py` 中所有函数体内的懒加载 `import` 提升至模块顶层，确保 PyInstaller / Nuitka 静态分析可正确检测所有依赖，无需 `hiddenimports` / `collect_all` 绕过。
- **唯一保留的懒加载**：`ui/catia_embed.py` 中 `from catia_copilot.ui.main_window import MainWindow` 因循环依赖须保持懒加载（已加 `# noqa: PLC0415` 注释说明）。
- **Bug 修正**：`plm/sync.py` 中 `catia_utils` 模块引用修正为 `catia_copilot.catia.connection`。
- **死代码清理**：`utils.py` 移除空 `try/except ImportError` 块；`catia/dependencies.py` 去除重复常量 `SEARCH_MAX_LEVELS`。

### 新增 — Nuitka 构建支持

- **`build_nuitka.ps1`**：新增 Nuitka 等价打包脚本，动态读取 `constants.py` 中的 `APP_VERSION`，自动处理 pywin32 DLL 收集（通过 `--include-package` 避免手动 `--include-data-files` 引起的 DLL 冲突）。
- **`build.ps1`**：改为 `python -m PyInstaller` 调用，消除 PATH 依赖。
- **`build.spec`**：移除 `collect_all('pycatia')` 和多余 `hiddenimports`；pywin32 DLL 改为双路径搜索（系统目录 + 用户目录）。

### 修复

- `Description`（`DescriptionRef`）属性可写，修正为不加入只读集合。
- `QToolButton` 全部替换为 `QPushButton`，统一按钮行为和样式。
- 树控件分支线：通过 `drawBranches` override + `native.qss` dotted border 恢复点线分支；修复坐标偏移、箭头下方叠加、相位对齐等细节问题。
- 所有树控件启用交替行颜色（`setAlternatingRowColors`）。

---

## [1.9.0] — 2026-05-30

### 新增 — CATIA 3D 视图嵌入面板

- **嵌入菜单按钮**：在 CATIA V5 每个 3D 视图右上角嵌入 Win32 原生功能菜单按钮，可快速访问所有功能；支持拖拽定位、锚点吸附（TR/TL/BR/BL）、位置持久化（`QSettings`）。
- **菜单分区**：嵌入菜单按工作台 / 导出 / 图纸 / 工具分区，与主菜单结构完全一致；菜单文字从 `_ACTION_LABELS` 读取，不硬编码。
- **Toggle 按钮**：嵌入 3D 视图按钮改为可选中的 Toggle 按钮，选中时蓝色高亮；启用状态持久化，重启后自动恢复。
- **宏子菜单**：嵌入菜单中「运行宏」改为子菜单，直接列出宏文件，点击即运行；通过 Signal 派发到 Qt 主线程执行，不在 Win32 后台线程直接调用 COM。

### 新增 — 图纸功能 Python 改写

- **新建图纸 (Python)**：新增 `drawing_operations.py` 核心模块，实现 `generate_drawing()` 从 CATPart/CATProduct 生成新图纸；解决 `win32com CDispatch` 类型检测问题（`get_document_type()`）。
- **刷新图纸 (Python)**：实现 `refresh_drawing()` 将图纸参数与对应零件/产品同步（零件编号、术语、版本及自定义属性）；保留 VBScript 版本用于对比。

### 新增 — 主题

- **原生主题（CATIA 风格）**：新增第三套主题，使用 Qt `windows` 风格渲染器，外观与 CATIA V5 界面一致；`native.qss` 只保留项目专属控件样式，其余全部由 Windows 经典风格渲染器接管。
- **主题切换下拉菜单**：主题切换按钮改为下拉菜单（深色 / 浅色 / 原生），带勾选状态；`toggle()` 改为三态循环，新增 `set_theme(name)` 方法。

### 新增 — 对话框行为

- **对话框跟随 CATIA 最小化/还原**：500ms 定时器检测 `IsIconic(catia_hwnd)`，CATIA 最小化时所有对话框自动隐藏（`hide()` 前 `saveGeometry()` 保存运行时几何），还原时精确恢复位置和尺寸。
- **主窗口独立**：主窗口不跟随 CATIA 最小化，完全由用户自主管理。
- **空对话框模板**：新增 `template_dialog.py`，包含几何持久化、主题跟随等标准行为，作为新建对话框的参考模板。

### 新增 — 功能

- **查找指向的文档**（原「查找所有依赖项」）：重构为支持多种查找策略的双向依赖分析对话框；支持 COM 结构遍历、doc_file_links、启发式文件名匹配、反向遍历已打开文档等策略；策略可通过 checkbox 独立开关。
- **在图纸/零件间切换**（原「打开当前文档的关联图纸/零件」）：将原来的两个按钮合并为单按钮，自动判断当前活跃文档类型，双向查找关联文档。

### 新增 — PLM 工作台

- **两阶段同步**：拆分上传选项（STP 几何文件、CATDrawing 附件），完善 BOM 采集与位置同步逻辑；批量 checkin、转换等待 UI 实时反馈、进度计数修复。
- **Cloudflare 公网访问修复**：修复公网经 Cloudflare 时 `urllib` 被 403 拦截的问题，添加 UA 头绕过。
- **PLM-08 记录**：记录 Cloudflare UA 403 问题及修复方案（`docs/PLM_ISSUES.md`）。

### 重构 — 主菜单结构

- Tab 重命名与重组：工作台 / 导出 / 图纸 / 工具 / ≡（原 导出 / BOM / 图纸 / 工具 / ≡）
- 工作台 Tab：BOM 工作台、质量特性工作台、PLM 工作台
- 导出 Tab：从产品导出 BOM（从 BOM Tab 移入）、从图纸导出 PDF、从产品/零件导出 STP
- 工具 Tab：合并原「零件与装配」和「分析」section 为「功能」section；运行宏从 ≡ 移入
- 新增 `_ACTION_LABELS` 类变量字典，嵌入菜单和主菜单按钮共用，消除硬编码不一致

### 重构 — 功能命名统一

- 所有对话框标题与主菜单按钮名称完全一致：BOM 工作台、质量特性工作台、从产品导出 BOM、从图纸导出 PDF、从产品/零件导出 STP、查找指向的文档
- 中文与专有名词（BOM / CATIA / CATPart / CATProduct / CATDrawing / COM / Excel 等）之间统一加空格

### 修复 — CATIA COM

- 修复 `application.Visible = True` 导致 CATIA 最大化窗口变为普通窗口的问题：新增 `safe_set_visible()` 函数，设置前保存窗口状态，设置后恢复最大化。
- 修复 `bring_catia_to_foreground` 的 `SW_RESTORE` 问题：添加 `IsIconic` 检查，只在最小化时才恢复。
- 提取 `open_catia_file()` 和 `bring_catia_to_foreground()` 到 `catia/utils.py`，三处打开文件逻辑统一调用；修复 `Path.resolve()` 在 Windows 环境下的路径污染问题。

### 修复 — UI

- 修复对话框几何持久化：`setParent(None)` 重建原生窗口后重新调用 `restoreGeometry`；CATIA 最小化前用 `saveGeometry()` 保存运行时几何，还原时精确恢复。
- 修复所有对话框缺少窗口几何持久化的问题（`ExportBomDialog`、`FindDependenciesDialog`、`HelpDialog`、`PlmSyncDialog`、`PlmWorkbench`、`FileConvertDialog`）。
- 修复主窗口「运行宏」按钮菜单消失的问题：`clicked` 信号的 `bool` 参数被误传给 `pos` 参数，改用 `lambda` 隔离。
- 修复 QSS 主界面 Tab 标题 padding 和功能按钮高度（padding 调整为 13px，min-height 调整为 30px）。
- 修复 `QComboBox` 设置 `NoFocus` 防止抢走树焦点导致已选行高亮消失。
- 修复 BOM 树 branch hover-on-selected 颜色割裂；统一三处表头行高为 `_ROW_HEIGHT`。
- 修复 hover 其他行时选中行着色消失：覆盖 `item:selected:!active` 规则保持选中色稳定。
- 修复查找依赖项路径大小写比对、`PartNumber` 参数查找大小写比对；消除 `doc_file_links` 正常视图的误导性日志。

### 修复 — 宏

- `fastener_assembly.catvba` / `nut_plate_assembly.catvba`：更新 VBA 宏文件（`.catvba` 二进制格式），移除旧的 `.txt` 用户窗体文本文件。
- `revert SelectElement3 → SelectElement2`：回退宏中的 SelectElement 版本，修复兼容性问题。

### 工程 — 构建

- `build.spec` 新增 `pywin32_system32` DLL 动态打包（`pywintypes` / `pythoncom`），缺失时所有 COM 操作会崩溃。
- `build.spec` 新增 `qdarkstyle` 的两个 rc 模块到 `hiddenimports`，缺失时深色/浅色主题图标显示为空白。
- `build.spec` 版本号通过正则解析 `constants.py` 自动同步，无需手动维护。
- 新增 `.gitattributes`，统一行尾符为 LF，解决跨平台编辑器导致的虚假 diff 问题。

---

## [1.8.0] — 2026-05-21

### 新增 — BOM

- **Description（描述）列**：新增 CATIA 内置属性 `Description` 支持，与 `Nomenclature`/`Definition`/`Revision`/`Source` 并列；`bom_collect.py` 通过 `description_reference` 属性采集，`constants.py` 同步补全列定义、显示名、宽度、可隐藏列、编辑列顺序。

### 新增 — PLM 同步

- **同步策略枚举**：新增 `SyncOptions`/`ExistingPartPolicy` 策略枚举，`_sync_node` 重构为先查询再按策略处理；日志改为四列等宽对齐表格。
- **预设选项对话框**：同步前新增 `_SyncOptionsDialog`（预设 + 4 个选项组）。
- **属性写入流程**：已存在零件同步时加入 `checkout → update → checkin` 完整流程，修复属性写入失败问题。
- **存在性判断改为 POST 探测**：绕开 PLM 服务端 `GET /parts/{pn}-{ver}` 全局 NPE bug（PLM-06），改用 `POST /parts` 探测：成功=新建，`400"不唯一"`=已存在。
- **FORCE_UNDO 策略灰显**：`undocheckout` 不支持撤销他人签出且 `iter=1` 时无法撤销（PLM-07），UI 灰显该选项，退化为 SKIP；旧配置强制忽略。
- **PLM API 集成测试**：新增 33 个用例覆盖连通性、创建幂等性、空格编码、404/500 行为、属性更新、嵌套 BOM、撤销签出、并发等场景。

### 新增 — 主题与 UI

- **Fluent Design 无边框窗口**：主窗口改为无边框，新增自定义标题栏（Tab 切换、主题按钮、更多菜单、拖拽移动、双击最大化）。
- **深色/浅色主题**：新增完整双套 QSS，`QSettings` 持久化偏好，运行时动态切换；所有对话框订阅主题信号自动跟随。
- **DWM 标题栏着色**：通过 Win32 API 同步着色系统标题栏（如有）。
- **SVG 图标单选/复选框**：单选框、复选框改用 SVG 图标渲染，深/浅主题各一套。
- **QSS 常量化**：控件圆角、按钮高度、输入框高度、指示器尺寸、日志字体等提取为 `theme_manager.py` 常量，双主题统一占位符注入。
- **行着色动态切换**：`bom_edit_dialog`/`mass_props_dialog` 订阅主题信号，切换主题时遍历现有行重设背景/前景色，不重建树。

### 修复 — UI

- 修复中文路径下"打开路径"异常：改用 `ShellExecuteW`（Unicode 宽字符 API），彻底解决 OEM 代码页乱码及 PowerShell 环境 `Explorer.ps1` 误解析问题。
- 修复 HiDPI 下无边框窗口八方向 resize 区域检测偏移：改用 `QCursor.pos() + mapFromGlobal()` 精确命中测试。
- 修复箭头按钮图标被裁剪：`setFixedSize(36,32)` + 内边距。
- 修复 CATIA 已可见时仍调用 `application.visible = True` 导致窗口位置跳回的问题：改为 `if not application.visible` 条件赋值。

### 修复 — PLM

- 修复 `checkOutUser` 读取方式：改用 `(checkOutUser or {}).get('login')` 防御性读取，删除不存在的 `checkOutLogin` fallback。
- 修复 `_get_latest_version` 对 500+NPE 响应体的处理：改为 `continue` 跳过而非 raise，保证已存在零件的迭代号查询不中断。
- 修复新建零件时 `updated` 计数被多计的问题：`source=="新建"` 时不再重复计入 `updated`。
- 修复 COM 连接检测被安全软件拦截 `tasklist` 时指示器持续显示未连接的问题。

### 工程 — 构建

- **`build.spec` 版本号自动同步**：不再硬编码版本字符串，改为正则解析 `constants.py` 中的 `APP_VERSION`，打包输出目录名自动跟随版本更新。

---

## [1.7.0] — 2026-05-11

### 新增 — BOM 属性补全

- **脏字段高亮与撤销/重做**：已修改的单元格以橙色粗体显示，可通过 Ctrl+Z / Ctrl+Y 最多撤销/重做 10 步，撤销/重做按钮显示方向箭头图标；悬停时显示原值提示。
- **关闭确认弹窗**：关闭对话框时若存在未保存的修改，弹出确认对话框防止误丢数据。
- **搜索栏**：在表格上方新增搜索框（Ctrl+F 快捷键），可实时按任意列内容过滤行。
- **状态栏**：底部显示当前可见行数及已修改字段数。
- **表头点击排序**：点击任意列标题即可切换升/降序排序，替换原下拉框。
- **窗口几何持久化**：退出时自动保存、重启后恢复对话框的尺寸与位置。
- **快捷键**：Ctrl+S 保存并写回 CATIA，Ctrl+Z / Ctrl+Y 撤销/重做。
- **右键复制单元格**：右键菜单新增"复制单元格内容"选项。
- **导出成功弹窗**：导出完成后弹窗新增"打开文件"和"打开所在文件夹"按钮。
- **默认导出文件名**：自动以根产品零件编号加 `_BOM` 作为默认导出文件名。

### 新增 — 质量特性统计

- **表头点击排序**：点击列标题即可升/降序排序，替换原排序列下拉框。
- **搜索/过滤框**：新增搜索框（Ctrl+F 快捷键）过滤表格行，与 BOM 编辑对话框一致。
- **惯量单位 QComboBox**：将惯量单位选择从 4 个单选按钮改为紧凑下拉框（QComboBox）。
- **标题栏脏标记**：编辑重量或密度后，标题栏显示 `*` 提示有未保存修改；保存或载入后自动清除。
- **导出成功弹窗**：导出完成后弹窗新增"打开文件"和"打开所在文件夹"按钮，与 BOM 编辑对话框保持一致。
- **窗口几何持久化**：退出时自动保存、重启后恢复对话框的尺寸与位置。
- **列标题工具提示**：各列标题新增悬停提示说明。
- **对称件行排除于汇总 BOM**：汇总 BOM 模式中不再展示对称件行；"导出表格"始终以层级 BOM 内容导出。

### 修复 — 质量特性统计

- 修复对称件行的 `None` 语义：当源行无质量数据时，对称件行的重量、重心、惯量、密度均正确显示空值或破折号，不再错误填充 0 或非空内容。
- 修复对称件密度显示与源行不一致的问题（"不统一" / 空值均已同步）。
- 修复 `_mirror_src_type` 回退逻辑使用 `or` 导致空字符串被误判为缺失的问题（改用显式 `None` 判断）。
- 修复同一源行可重复添加对称件的问题（现已防止重复添加）。
- 修复删除对称件后无法为同一源行重新添加对称件的问题。
- 修复使用说明标签高度硬编码导致三行文字被截断的问题。

### 修复 — BOM 属性补全

- 修复 `#` 列和数量列按字符串排序而非数值排序的问题。
- 修复将字段还原为原始值后脏标记未正确清除的问题（文本字段与下拉框字段均已修复）。

### 修复 — 宏（hide_wireframe.catvbs）

- 修复产品场景下线框隐藏逻辑的多处问题：正确使用 `SelectedElement.Value.VisProperties` 读取每个元素的可见性；修正 `catVisNoShow`（showVal=1）的判断逻辑；仅对 ProductDocument 节点执行 InWorkObject 切换，跳过 PartDocument 节点。

---

## [1.6.0] — 2026-05-08

### 新增

- **对称件（镜像件）支持**：在层级 BOM 模式下，可通过右键菜单为任意零件行添加对称件。对称件类型显示为"对称件"，其重量、密度和惯量数据随原零件自动同步更新。
- **质量特性表格多选批量操作**：支持在表格中按住 Shift / Ctrl 进行多行选择，并通过右键菜单对所选行批量执行"删除"、"切换参与计算"和"重新读取质量特性"操作。
- **汇总 BOM 按"数量"排序**：汇总 BOM 模式新增按数量（Quantity）排序选项，支持升/降序。
- **汇总结果面板重设计**：汇总结果区改为三列 + 白色数值框布局，新增**重心主惯量矩**（M1 / M2 / M3）和**主轴**（A1–A3 × x/y/z）显示，以及底部对齐与统一数值框宽度。
- **帮助文档新增惯量测量参数说明**：在"CATIA 前提条件"章节增加了惯量包络体最小测量参数的说明与示意图。

### 修复

- 修复重量输入时允许输入 0 及负数的问题（现拒绝 ≤ 0 的输入）。
- 消除惯量数值中 IEEE 754 负零（−0）的显示问题。
- 修复嵌套产品中对称件所在的父节点汇总重心（_root_mp）未及时更新的问题。
- 修复 `rollup_mass_properties` 未将对称件计入汇总计算的问题。
- 修复载入已保存数据时镜像行密度同步及多实例惯量更新的问题。
- 修复右键"重新读取质量特性"时删除行不弹出确认对话框的问题。

---

## [1.5.0] — 2026-04-30

### 新增

- **重量、重心、惯量统计**：全新功能，遍历 CATProduct 产品树，从 CATIA 惯量包络体保持测量参数中读取每个零件的质量、重心坐标及转动惯量，在根产品坐标系下按层级汇总，并支持导出至 Excel；支持层级 BOM 和汇总 BOM 两种展示模式、多种单位切换（g/kg、mm/m、g·mm²/kg·m² 等）、密度编辑等。
- **三态 CATIA 连接指示器**：状态栏 COM 连接指示器升级为三态：绿色（已连接且功能正常）、橙色（连接异常，如 gen_py 缓存污染）、红色（未连接）。
- **CATIA 连接诊断按钮**：在帮助菜单新增"CATIA 连接诊断"入口，可查看 CATIA 版本、已打开文档数及修复建议的详细诊断报告。
- **帮助文档**：内置完整中文帮助文档（帮助 → 文档），详细说明所有功能使用方法、前提条件及常见问题。

---

> 仅供内部使用，请勿外传。
