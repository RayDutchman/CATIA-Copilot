# CATIA Copilot

> 一款面向工程团队的 CATIA V5 辅助工具，旨在简化日常操作、提升工作效率。

**版本：** 2.0.1 &nbsp;|&nbsp; **发布日期：** 2026-06-05 &nbsp;|&nbsp; **作者：** CHEN Weibo

> 历史版本变更说明见 [CHANGELOG.md](CHANGELOG.md)。

---

## 功能一览

### AI Copilot

| 功能 | 说明 |
|------|------|
| **AI 对话助手** | 流式对话面板，支持多会话管理、Markdown 渲染、工具调用卡片；AI 可直接操作 CATIA（读写属性、BOM、导出、建模等） |
| **多 Provider 支持** | 支持 OpenAI、Anthropic、Gemini 等多家服务商；Settings 对话框可配置 API key、base URL、模型 |
| **AI 驱动建模** | AI 生成 Python 建模脚本并动态执行，支持草图、拉伸、旋转、倒角、阵列等特征；失败自动返回 traceback 供 AI 自纠正 |

### 工作台

| 功能 | 说明 |
|------|------|
| **BOM 工作台** | 在表格中编辑 BOM 属性（零件编号、术语、定义、版本、来源及自定义用户属性），一键写回 CATIA |
| **质量特性工作台** | 遍历产品树，汇总各零件质量特性（质量/重心/转动惯量），自动按层级累加并导出 Excel；支持层次化和汇总两种 BOM 模式及多种单位切换 |
| **PLM 工作台** | 整合 PLM 连接管理、增量同步、Tag 规则、产品注册与历史记录 |

### 导出

| 功能 | 说明 |
|------|------|
| **从产品导出 BOM** | 从 CATProduct 中提取完整 BOM 信息并导出至 Excel (.xlsx) |
| **从图纸导出 PDF** | 批量将 CATDrawing 文件导出为 PDF，支持自定义文件前缀 |
| **从产品/零件导出 STP** | 批量将 CATPart 或 CATProduct 文件导出为 STEP 格式 |

### 图纸

| 功能 | 说明 |
|------|------|
| **新建图纸** | 根据 `drawing_templates` 文件夹中的 CATDrawing 模板，在 CATIA 中为当前活动零件/产品生成新图纸 |
| **刷新图纸** | 将当前活动 CATDrawing 图纸的参数与对应零件/产品同步刷新（零件编号、术语、版本及自定义属性） |

### 工具

| 功能 | 说明 |
|------|------|
| **复制字体文件到 CATIA 目录** | 将 ChangFangSong.ttf 一键复制到 CATIA TrueType 字体目录 |
| **复制 ISO.xml 到 CATIA 目录** | 将 ISO.xml 标准文件一键复制到 CATIA drafting 标准目录 |
| **刷写零件模板** | 为 CATPart 批量添加标准用户自定义属性（物料编码、物料名称等） |
| **快速装配紧固件 / 托板螺母** | 通过 VBA 宏快速批量装配紧固件或托板螺母到产品孔位，支持翻转方向 |
| **在图纸/零件间切换** | 自动判断当前活跃文档类型，双向查找并打开关联的图纸或零件/产品 |
| **查找指向的文档** | 通过 CATIA COM 查找文件的所有引用文档，支持多种查找策略 |
| **运行宏** | 自动扫描 macros 文件夹中的 `.catvbs` / `.catscript` / `.catvba` 文件，可直接运行 |

### 其他

- **CATIA 3D 视图嵌入菜单** — 在每个 3D 视图右上角嵌入功能菜单按钮，可快速访问所有功能；支持拖拽定位，菜单按工作台/导出/图纸/工具分区
- **CATIA 连接指示器**（状态栏）— 每 5 秒轮询 COM 连接状态；三色显示：绿色（已连接且功能正常）、橙色（连接异常）、红色（未连接）
- **CATIA 连接诊断** — 可查看详细诊断报告（CATIA 版本、已打开文档数、活动文档及修复建议）
- **原生主题** — 使用 Windows 系统 `windows11` 风格渲染器，外观随系统深浅色自动切换，与 CATIA V5 界面风格一致
- **gen_py 自动清理** — 启动时自动删除 `%LOCALAPPDATA%\Temp\gen_py\` 早绑定缓存，防止 COM 连接污染
- **日志窗口** — 查看操作记录与错误信息
- **帮助文档** — 内置帮助文档，在菜单「帮助 → 文档」中查看

---

## 运行环境要求

- **操作系统：** Windows 10 / 11
- **Python：** 3.10 或更高版本
- **CATIA V5 R28：** 文件导出等功能需要 CATIA 处于运行状态（通过 COM 自动化接口通信）

---

## 安装 / 开发环境搭建

```bash
# 1. 克隆仓库
git clone https://github.com/RayDutchman/CATIA-Copilot.git
cd CATIA-Copilot

# 2. 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
```

### 运行

```bash
python main.py
```

---

## 打包为 Windows 可执行文件

```bash
# 前置依赖
pip install pyinstaller

# 打包
pyinstaller build.spec

# 输出目录
# dist\CATIA Copilot\CATIA Copilot.exe
```

ISO.xml、ChangFangSong.ttf 等资源文件会由 spec 配置自动复制到输出目录。

---

## 项目结构

```
CATIA-Copilot/
├── main.py                          # 应用入口
├── catia_copilot/
│   ├── constants.py                 # 常量与配置
│   ├── logging_setup.py             # 日志初始化
│   ├── utils.py                     # 工具函数
│   ├── catia/                       # CATIA COM 自动化逻辑
│   │   ├── conversion.py            #   图纸/零件导出
│   │   ├── template.py              #   零件模板刷写
│   │   ├── bom_collect.py           #   BOM 数据采集
│   │   ├── bom_export.py            #   BOM 导出 Excel
│   │   ├── bom_write.py             #   BOM 属性写回 CATIA
│   │   ├── dependencies.py          #   依赖查找
│   │   ├── drawing_operations.py    #   图纸操作
│   │   ├── mass_props_collect.py    #   质量特性采集
│   │   └── utils.py                 #   COM 工具函数
│   └── ui/                          # PySide6 界面
│       ├── main_window.py           #   主窗口
│       ├── catia_embed.py           #   3D 视图嵌入菜单
│       ├── catia_sidebar.py         #   CATIA 吸附边栏
│       ├── convert_dialog.py        #   文件转换对话框
│       ├── export_bom_dialog.py     #   BOM 导出对话框
│       ├── bom_edit_dialog.py       #   BOM 工作台对话框
│       ├── mass_props_dialog.py     #   质量特性工作台对话框
│       ├── find_deps_dialog.py      #   查找指向的文档对话框
│       ├── plm_workbench.py         #   PLM 工作台
│       ├── help_dialog.py           #   帮助文档对话框
│       ├── theme_manager.py         #   主题管理（深色/浅色/原生）
│       ├── dark.qss / light.qss / native.qss  # 主题样式表
│       └── log_window.py            #   日志窗口
├── build.spec                       # PyInstaller 打包配置
├── requirements.txt                 # Python 依赖
├── pyproject.toml                   # 项目元数据
├── resources/                       # 图标等资源文件
├── macros/                          # 宏脚本文件夹
├── drawing_templates/               # 图纸模板文件夹
└── crack/                           # CATIA 授权文件
```

---

## 依赖

| 包 | 用途 |
|------|------|
| [PySide6](https://pypi.org/project/PySide6/) | Qt 6 GUI 框架 |
| [openpyxl](https://pypi.org/project/openpyxl/) | Excel 文件读写 |

---

## 自定义属性联动说明（`PRESET_USER_REF_PROPERTIES`）

`catia_copilot/constants.py` 中的 `PRESET_USER_REF_PROPERTIES` 列表定义了程序内置的用户自定义属性名称（物料编码、物料名称、规格型号、物料来源、数据状态、存货类别、重量、备注）。手动编辑该列表后，**Python 部分**会在重启程序后全部自动生效，**VBA 宏和文档**则需手动同步。

### ✅ 修改后自动联动（Python 层）

| 文件 | 作用 |
|------|------|
| `catia_copilot/catia/template.py` | 刷写零件模板时，按列表逐项向 CATPart 写入用户属性 |
| `catia_copilot/ui/bom_edit_dialog.py` | BOM 编辑对话框：过滤已保存可见列、构建全量列集合、渲染属性复选框、生成显示列头 |
| `catia_copilot/ui/export_bom_dialog.py` | BOM 导出对话框：构建"可用列 / 已选列"列表 |

### ⚠️ 需手动同步的地方

| 文件 | 原因 |
|------|------|
| `macros/generate_drawing.catvbs`（第 85–88 行） | VBA 宏内独立硬编码属性名数组（与 Python 列表相互独立），且包含 `PRESET_USER_REF_PROPERTIES` 中没有的 `"材料"` 字段 |
| `macros/refresh_drawing_info.catvbs`（第 119–121 行） | 同上，另一个独立的 VBA 属性名数组 |
| `catia_copilot/ui/help_dialog.py`（第 52–53、76–77 行） | 帮助窗口 HTML 文本中硬编码了属性名称列表，仅影响界面说明文字，不影响功能 |
| `README.md`（本文件）| 功能一览表中对属性名的文字描述需同步更新 |

> **总结：** 修改 `constants.py` 中的 `PRESET_USER_REF_PROPERTIES` 后，Python 程序所有读写逻辑均会在重启后自动跟随更新；唯一需要人工同步的是两个 VBA 宏文件中各自独立的属性名数组，以及帮助文档和 README 中的说明文字。

---

## 联系方式

- **开发者：** CHEN Weibo
- **邮箱：** thucwb@gmail.com

> 仅供内部使用，请勿外传。
