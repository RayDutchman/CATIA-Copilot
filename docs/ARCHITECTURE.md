# 架构说明

本文档描述 CATIA Copilot 的整体架构、模块划分与关键设计决策。

---

## 整体架构

CATIA Copilot 是一个运行在 Windows 上的 Python 桌面应用，通过 COM 自动化接口与 CATIA V5 通信，并通过 HTTP REST API 与 PLM 服务器同步数据。

```
┌──────────────────────────────────────────────────────┐
│                   PySide6 UI 层                      │
│  main_window / 各 Dialog / theme_manager / style.qss │
└─────────────────────┬────────────────────────────────┘
                      │ 调用
┌─────────────────────▼────────────────────────────────┐
│                  业务逻辑层                           │
│   catia/          plm/           utils.py            │
│   conversion      client         constants           │
│   bom_collect     sync                               │
│   bom_export      models                             │
│   bom_write                                          │
│   template                                           │
└──────────┬──────────────────────┬────────────────────┘
           │ win32com.client      │ requests / httpx
┌──────────▼──────────┐  ┌───────▼───────────────────┐
│    CATIA V5 (COM)   │  │   PLM REST API 服务器      │
└─────────────────────┘  └───────────────────────────┘
```

---

## 目录结构

```
CATIA-Copilot/
├── main.py                          # 应用入口，初始化 QApplication、主窗口
├── catia_copilot/
│   ├── constants.py                 # 全局常量（版本号、列定义、属性名列表等）
│   ├── logging_setup.py             # 日志初始化（文件 + 控制台 handler）
│   ├── utils.py                     # 通用工具函数
│   ├── catia/                       # CATIA COM 自动化逻辑
│   │   ├── conversion.py            #   图纸/零件批量导出（PDF/STP）
│   │   ├── template.py              #   零件模板属性刷写
│   │   ├── bom_collect.py           #   从 CATIA 采集 BOM 数据
│   │   ├── bom_export.py            #   BOM 导出至 Excel
│   │   └── bom_write.py             #   BOM 属性写回 CATIA
│   ├── plm/                         # PLM 服务器集成
│   │   ├── client.py                #   REST API 客户端（requests）
│   │   ├── sync.py                  #   BOM 树同步逻辑（checkout/update/checkin）
│   │   └── models.py                #   数据模型（Pydantic / dataclass）
│   └── ui/                          # PySide6 界面
│       ├── main_window.py           #   主窗口（无边框 Fluent 风格）
│       ├── theme_manager.py         #   主题管理（深色/浅色，QSettings 持久化）
│       ├── convert_dialog.py        #   文件转换对话框
│       ├── export_bom_dialog.py     #   BOM 导出对话框
│       ├── bom_edit_dialog.py       #   BOM 属性编辑对话框
│       ├── mass_props_dialog.py     #   质量特性统计对话框
│       ├── find_deps_dialog.py      #   依赖查找对话框（开发中）
│       ├── help_dialog.py           #   帮助文档对话框
│       ├── log_window.py            #   日志窗口
│       └── native.qss               #   QSS 样式表（由 theme_manager 动态注入）
├── macros/                          # CATIA VBA 宏文件
│   ├── fastener_assembly.txt        #   紧固件装配宏主模块
│   ├── fastener_assembly_userforms.txt  # 紧固件装配 FlipForm UI
│   ├── nut_plate_assembly.txt       #   托板螺母装配宏主模块
│   ├── nut_plate_assembly_userforms.txt # 托板螺母装配 FlipForm UI
│   ├── generate_drawing.catvbs      #   新建图纸宏
│   ├── refresh_drawing_info.catvbs  #   刷新图纸参数宏
│   └── hide_wireframe.catvbs        #   隐藏线框宏
├── tests/                           # pytest 测试
├── docs/                            # 项目文档
├── drawing_templates/               # CATDrawing 模板文件
├── build.spec                       # PyInstaller 打包配置
├── pyproject.toml                   # 项目元数据与版本号
└── requirements.txt                 # Python 依赖
```

---

## 关键模块说明

### `constants.py`

存放所有全局常量：
- `APP_VERSION`：版本号，`build.spec` 通过正则解析此值自动同步打包输出目录名
- `PRESET_USER_REF_PROPERTIES`：用户自定义属性名列表，驱动 BOM 编辑/导出/刷写的全部属性逻辑
- BOM 列定义（显示名、宽度、是否可隐藏、编辑顺序等）

修改 `PRESET_USER_REF_PROPERTIES` 后，Python 层所有读写逻辑自动跟随，VBA 宏中的属性名数组需手动同步（详见 README）。

### `catia/bom_collect.py`

通过 `win32com.client` 递归遍历 CATIA 产品树，采集每个节点的内置属性（`PartNumber`、`Nomenclature`、`Definition`、`Revision`、`Source`、`Description`）以及用户自定义属性（`PRESET_USER_REF_PROPERTIES`）。

### `plm/sync.py`

BOM 树同步的核心逻辑：
1. 按深度优先遍历 BOM 节点
2. 通过 `POST /parts` 探测零件是否已存在（绕开 PLM 服务端 GET 接口的 NPE bug）
3. 已存在的零件执行 `checkout → update attributes → checkin` 流程
4. 新建零件直接 POST 创建
5. 同步策略由 `SyncOptions` / `ExistingPartPolicy` 枚举控制

### `ui/theme_manager.py`

- 通过 `QSettings` 持久化用户偏好
- 发出 `theme_changed` 信号，各对话框订阅后动态更新行着色等无法由 QSS 覆盖的样式

### VBA 宏（`macros/`）

`.catvba` 文件是 OLE2 二进制容器，无法用文本工具生成，只能在 CATIA VBA IDE 内手动粘贴。`.txt` 文件是对应的可读源码，用 `[1/3]`、`[2/3]`、`[3/3]` 分隔多个模块。

装配宏中的 `SelectElement3`（CATIA R33 专用）允许 UI 交互穿透——FlipForm 的撤销/停止按钮在等待用户选择时仍可响应。filter 参数必须使用 `Variant` 类型数组，不能用强类型 `String` 数组（会触发 Type mismatch 错误 13）。

---

## COM 连接管理

- 启动时自动删除 `%LOCALAPPDATA%\Temp\gen_py\` 早绑定缓存，防止 `EnsureDispatch` 残留污染
- 状态栏每 5 秒轮询连接状态，三色指示（绿/橙/红）
- 橙色表示 COM 连接存在但功能异常（如早绑定缓存未清干净），可通过帮助菜单的诊断工具查看详情

---

## 设计决策记录

| 决策 | 原因 |
|------|------|
| 不引入 `structlog` / Sentry | 项目规模不需要，标准库 `logging` 已够用 |
| PLM 存在性判断改用 `POST` 探测 | PLM 服务端 `GET /parts/{pn}-{ver}` 存在全局 NPE bug（PLM-06），POST 创建成功=不存在，400"不唯一"=已存在 |
| VBA 宏源码用 `.txt` 存储 | `.catvba` 是二进制，Git 无法 diff；`.txt` 便于代码审查和版本管理 |
| `SelectElement3` 替代 `SelectElement2` | CATIA R33 中 `SelectElement2` 等待期间 UI 完全冻结（包括 FlipForm 所有按钮），`SelectElement3` 允许 UI 穿透 |
