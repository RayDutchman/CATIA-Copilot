"""
Application-wide constants for CATIA Copilot.

All magic strings, column definitions, and configuration values are kept here
so they can be imported by any module without circular-dependency risk.
"""

import re

# ---------------------------------------------------------------------------
# Application info
# ---------------------------------------------------------------------------

APP_NAME    = "CATIA Copilot"
APP_VERSION = "2.0.1"
APP_DATE    = "2026-06-05"
APP_AUTHOR  = "CHEN Weibo"
APP_CONTACT = "thucwb@gmail.com"

ABOUT_TEXT = f"""{APP_NAME} v{APP_VERSION}

一款面向工程团队的 CATIA V5 效率工具。

主要功能：
  • 从图纸导出 PDF（CATDrawing 批量导出）
  • 从产品/零件导出 STP（CATPart / CATProduct 批量导出）
  • 从产品导出 BOM（导出至 Excel）
  • BOM 工作台（在线编辑属性并写回 CATIA）
  • 质量特性工作台（质量/重心/转动惯量统计与汇总）
  • PLM 工作台（连接管理、增量同步、Tag 规则）
  • 新建图纸 / 刷新图纸（从模板生成或同步 CATDrawing）
  • 刷写零件模板（添加标准用户自定义属性）
  • 紧固件 / 托板螺母快速装配（VBA 宏批量装配）
  • 在图纸/零件间切换（自动查找关联文档）
  • 查找指向的文档（COM 依赖分析）
  • 运行宏（快捷运行 .catvbs / .catscript / .catvba）
  • 字体文件 / ISO.xml 标准文件一键部署
  • CATIA 3D 视图嵌入菜单（快速访问所有功能）
  • COM 连接诊断（自动检测连接状态与异常）

─────────────────────────────────────────
开发者    {APP_AUTHOR}
联系方式  {APP_CONTACT}
发布日期  {APP_DATE}
─────────────────────────────────────────

\u00a9 2026 {APP_AUTHOR}. 仅供内部使用。"""

# ---------------------------------------------------------------------------
# BOM 节点类型常量
# ---------------------------------------------------------------------------
# row["Type"] 的存储值统一使用英文 key，显示名通过 TYPE_DISPLAY_NAMES 转换。
# 避免直接在业务逻辑中硬编码中文字符串（language-agnostic）。

class BomNodeType:
    """BOM 节点类型的英文 key 常量。"""
    PART       = "Part"        # 零件（.CATPart 叶节点）
    PRODUCT    = "Product"     # 产品（.CATProduct 独立子装配）
    COMPONENT  = "Component"   # 部件（嵌入式子装配，无独立文件）
    MIRROR     = "Mirror"      # 对称件（mass_props_dialog 虚拟行）

    # 所有"装配"类型（非叶节点），用于过滤 / 聚合判断
    ASSEMBLY_TYPES: frozenset = frozenset({PRODUCT, COMPONENT})
    # 所有"叶节点"类型（需要测量质量特性 / 上传 STP 等）
    LEAF_TYPES: frozenset = frozenset({PART, MIRROR})

# 显示名映射：英文 key → 界面显示中文
TYPE_DISPLAY_NAMES: dict = {
    BomNodeType.PART:      "零件",
    BomNodeType.PRODUCT:   "产品",
    BomNodeType.COMPONENT: "部件",
    BomNodeType.MIRROR:    "对称件",
}

# ---------------------------------------------------------------------------
# Default window geometry
# ---------------------------------------------------------------------------

MAIN_WINDOW_DEFAULT_WIDTH  = 560
MAIN_WINDOW_DEFAULT_HEIGHT = 520

# ---------------------------------------------------------------------------
# Resource file paths (relative to project root / frozen executable directory)
# ---------------------------------------------------------------------------

FONT_FILE_PATH    = "resources/ChangFangSong.ttf"
ISO_XML_FILE_PATH = "resources/ISO.xml"
CRACK_DIR_PATH    = "crack"
APP_ICON_PATH     = "resources/icon.ico"

# ---------------------------------------------------------------------------
# Preset user-defined reference properties
# (used both for CATPart template stamping and as BOM preset custom columns)
# 图纸同步所用的默认属性子集见 DRAWING_SYNC_USER_PROPS
# ---------------------------------------------------------------------------

PRESET_USER_REF_PROPERTIES: list[str] = [
    "零件类型", "设计状态", "材料", "重量",
    "物料编码", "存货类别", "规格型号", "备注",
]

# ---------------------------------------------------------------------------
# User-defined property dropdown options
#
# 在此字典中为任意预设用户自定义属性指定可选值列表，该属性在"BOM属性补全"对话框中
# 将自动渲染为下拉框（QComboBox）而非自由文本输入框。
# 字典键必须是 PRESET_USER_REF_PROPERTIES 或用户添加的自定义列中的属性名称。
# 若不希望某属性使用下拉框，只需不在此处添加该属性（或将其删除）即可。
# ---------------------------------------------------------------------------

PRESET_USER_REF_PROPERTY_OPTIONS: dict[str, list[str]] = {
    "设计状态": ["草稿", "冻结", "发布", "废弃"],
    "存货类别": ["物料-复材件", "物料-金属件", "物料-标准件", "物料-非标件",
                "物料-钣金件", "物料-塑胶件", "物料-橡胶件", "物料-电子件",
                "物料-泡沫", "物料-软包", "物料-辅材", "物料-组件",
                "物料-虚拟件", "半成品-组件", "成品-整机"],
}

# ---------------------------------------------------------------------------
# CATIA 文档类型映射
#
# 文件后缀（小写）→ 文档类型字符串，与 VBScript TypeName() 返回值一致。
# 供 get_document_type()、tool_get_open_documents 等所有需要判断文档类型的
# 地方统一使用，避免各处各自定义 _EXT_TYPE 字典。
# ---------------------------------------------------------------------------

DOC_EXT_TYPE_MAP: dict[str, str] = {
    ".catpart":    "PartDocument",
    ".catproduct": "ProductDocument",
    ".catdrawing": "DrawingDocument",
}

# ---------------------------------------------------------------------------
# CATIA COM 工作模式 / 宏库类型常量
#
# ApplyWorkMode / GetWorkMode 对应 CATWorkModeType 枚举：
#   catWorkModeVisualization = 1
#   catWorkModeDesign        = 2
#
# SystemService.ExecuteScript 的 iLibraryType 参数：
#   1 = 目录模式（CATScript / .catvbs / .catscript，iLibraryName 为目录路径）
#   2 = VBA 项目文件模式（.catvba，iLibraryName 为文件完整路径）
# ---------------------------------------------------------------------------

CATIA_VISUALIZATION_MODE: int = 1  # catWorkModeVisualization
CATIA_DESIGN_MODE:        int = 2  # catWorkModeDesign

CATIA_MACRO_LIBRARY_DIR: int = 1   # ExecuteScript iLibraryType: 目录（CATScript）
CATIA_MACRO_LIBRARY_VBA: int = 2   # ExecuteScript iLibraryType: VBA 项目文件（.catvba）

# ---------------------------------------------------------------------------
# CATIA COM 属性映射
#
# 将 BOM 列名（用户可见的显示名）映射到 win32com Product 对象的 COM 属性名。
# 这两个 map 是 bom_collect / bom_write / document 模块的共同数据源，
# 集中在此处维护，避免各模块各自定义导致不一致。
#
# PRODUCT_ATTR_READ_MAP  — 可读属性（含 Description，通过 DescriptionRef 读写）
# PRODUCT_ATTR_WRITE_MAP — 可写属性（含 Description，通过 DescriptionRef 写入）
# ---------------------------------------------------------------------------

PRODUCT_ATTR_READ_MAP: dict[str, str] = {
    "Part Number":  "PartNumber",
    "Nomenclature": "Nomenclature",
    "Revision":     "Revision",
    "Definition":   "Definition",
    "Source":       "Source",
    "Description":  "DescriptionRef",   # 引用产品的描述字段，通过 DescriptionRef 读写
}

PRODUCT_ATTR_WRITE_MAP: dict[str, str] = {
    "Part Number":  "PartNumber",
    "Nomenclature": "Nomenclature",
    "Revision":     "Revision",
    "Definition":   "Definition",
    "Source":       "Source",
    "Description":  "DescriptionRef",   # 经实测可写，通过 DescriptionRef 赋值
}

# ---------------------------------------------------------------------------
# BOM standard columns
# ---------------------------------------------------------------------------

BOM_ALL_COLUMNS: list[str] = [
    "Level", "Type", "Part Number", "Nomenclature",
    "Definition", "Revision", "Source", "Description", "Quantity",
]

BOM_DEFAULT_COLUMNS: list[str] = [
    "Level", "Type", "Part Number", "Nomenclature",
    "Definition", "Revision", "Source", "Description", "Quantity",
]

# ---------------------------------------------------------------------------
# BOM edit / display constants
# ---------------------------------------------------------------------------

# Sentinel value displayed in the Filename cell when a product's backing file
# cannot be resolved via COM (the product is "not found").
FILENAME_NOT_FOUND: str = "未检索到"

# Sentinel value displayed in the Filename cell when a product has a backing
# file path in CATIA memory but the file has never been saved to disk.
FILENAME_UNSAVED: str = "未保存"

# Sentinel internal column name for the row-number column (always first, read-only)
BOM_ROW_NUMBER_COLUMN: str = "#"

# Column name for per-instance name in "完整 BOM" mode (maps to product.Name)
BOM_INSTANCE_NAME_COLUMN: str = "Instance Name"

# Columns that are structural / derived – shown read-only in the edit table
BOM_READONLY_COLUMNS: frozenset[str] = frozenset({"#", "Level", "Type", "Filename", "Filepath", "Quantity"})

# Standard BOM columns that can be hidden in the edit dialog
# These are properties that users might not need to see/edit
BOM_HIDEABLE_COLUMNS: list[str] = ["Nomenclature", "Revision", "Definition", "Source", "Description"]

# Column order used in the BOM edit dialog (internal names)
BOM_EDIT_COLUMN_ORDER: list[str] = [
    "Level", "Type", "Filename", "Part Number", "Quantity",
    "Nomenclature", "Revision", "Definition", "Source", "Description",
]

# Internal column name → Chinese display name
BOM_COLUMN_DISPLAY_NAMES: dict[str, str] = {
    "#":            "#",
    "Level":        "层级",
    "Type":         "类型",
    "Filename":     "文件名",
    "Filepath":     "完整路径",
    "Part Number":  "零件编号",
    "Nomenclature": "术语（中文名称）",
    "Definition":   "定义",
    "Revision":     "版本",
    "Source":       "源",
    "Description":  "描述",
    "Quantity":     "数量",
    "Instance Name": "实例名",
}

# Minimum column widths (Excel character units) for standard BOM columns
BOM_COLUMN_MIN_WIDTHS: dict[str, int] = {
    "Level":        6,
    "Type":         10,
    "Filename":     30,
    "Part Number":  20,
    "Nomenclature": 20,
    "Definition":   20,
    "Revision":     10,
    "Source":       8,
    "Description":  40,
    "Quantity":     8,
}

# Source field: CATIA integer string ↔ Chinese display label
SOURCE_TO_DISPLAY: dict[str, str]  = {"0": "未知", "1": "自制", "2": "外购"}
SOURCE_FROM_DISPLAY: dict[str, str] = {"未知": "0", "自制": "1", "外购": "2"}
SOURCE_OPTIONS: list[str]           = ["未知", "自制", "外购"]

# ---------------------------------------------------------------------------
# BOM thumbnail display
# ---------------------------------------------------------------------------

# Maximum width and height (pixels) for the thumbnail shown in the BOM
# right-click context menu.  Images larger than this are scaled down
# proportionally; images smaller than this are shown at their original size.
BOM_THUMBNAIL_MAX_SIZE: int = 130

# ---------------------------------------------------------------------------
# Mass properties columns
# ---------------------------------------------------------------------------

MASS_PROPS_COLUMNS: list[str] = [
    "Level", "Type", "Filename", "Part Number", "Nomenclature", "Revision",
    "Density", "Weight", "CogX", "CogY", "CogZ",
    "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz",
]

MASS_PROPS_COLUMN_DISPLAY_NAMES: dict[str, str] = {
    "#":            "#",
    "Level":        "层级",
    "Type":         "类型",
    "Filename":     "文件名",
    "Part Number":  "零件编号",
    "Nomenclature": "术语（中文名称）",
    "Revision":     "版本",
    "Quantity":     "数量",
    "Status":       "状态",
    "Density":      "密度 (kg/m³)",
    "Weight":       "重量 (kg)",
    "CogX":         "重心 X (mm)",
    "CogY":         "重心 Y (mm)",
    "CogZ":         "重心 Z (mm)",
    "Ixx":          "Ixx (kg·mm²)",
    "Iyy":          "Iyy (kg·mm²)",
    "Izz":          "Izz (kg·mm²)",
    "Ixy":          "Ixy (kg·mm²)",
    "Ixz":          "Ixz (kg·mm²)",
    "Iyz":          "Iyz (kg·mm²)",
}

# Columns that are read-only in the mass properties dialog
# (only "Weight" and "Density" with valid data are editable for part rows;
#  density with value -1.0 is additionally locked in the delegate)
MASS_PROPS_READONLY_COLUMNS: frozenset[str] = frozenset({
    "#", "Level", "Type", "Filename", "Part Number",
    "Nomenclature", "Revision", "Quantity",
    "CogX", "CogY", "CogZ",
    "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz",
})

# Columns in the mass properties dialog that can be hidden by the user
MASS_PROPS_HIDEABLE_COLUMNS: tuple[str, ...] = (
    "Filename", "Part Number", "Nomenclature", "Revision",
)

# ---------------------------------------------------------------------------
# Part Number validation
# ---------------------------------------------------------------------------

# Rejects control characters, non-ASCII characters, and Windows filename-
# forbidden characters  \ / : * ? " < > |
PART_NUMBER_VALID_PATTERN: re.Pattern = re.compile(
    r'^[^\x00-\x1f\x7f-\U0010ffff\\/:*?"<>|]*$'
)

# ---------------------------------------------------------------------------
# PLM 同步相关列定义
# ---------------------------------------------------------------------------

# DocdokuPLM 内置属性列（对应 PartRevision 的标准字段，作为 instanceAttributes 上传）
PLM_BUILTIN_ATTR_COLS: list[str] = ["Nomenclature", "Definition", "Revision", "Source", "Description"]

# ---------------------------------------------------------------------------
# PLM 同步：单次同步最大节点数（硬限制，超出则禁止同步）
# ---------------------------------------------------------------------------

PLM_SYNC_MAX_NODES: int = 100

# ---------------------------------------------------------------------------
# PLM 工作台：连接 Tab 成员列表列定义
# 每项：(字段键, 列标题, 拉伸模式)
# 拉伸模式："stretch" = Stretch，"contents" = ResizeToContents，"fixed:N" = Fixed N px
# 如需增删列或调整顺序，只改这里即可。
# ---------------------------------------------------------------------------

PLM_MEMBER_TABLE_COLUMNS: list[tuple[str, str, str]] = [
    ("login",       "登录名",    "contents"),
    ("name",        "姓名",      "stretch"),
    ("email",       "邮箱",      "stretch"),
    ("language",    "语言",      "fixed:60"),
    ("workspaceId", "工作区",    "contents"),
]

# ---------------------------------------------------------------------------
# 可调参数：惯量包络体编号上限
# ---------------------------------------------------------------------------

# 每个零件最多读取"惯量包络体.1"到"惯量包络体.MAX_INERTIA_INDEX"的保持测量。
# 编号不要求连续；所有编号在此范围内存在的测量均会被读取并在零件级汇总。
MAX_INERTIA_INDEX: int = 20

# ---------------------------------------------------------------------------
# CATDrawing 参数同步
#
# DRAWING_SYNC_STANDARD_PARAMS  — 从零件 Product 标准属性同步到图纸的参数名列表
#                                  对应 Product.PartNumber / Nomenclature / Revision
# DRAWING_SYNC_USER_PROPS       — 默认从零件用户自定义属性同步到图纸的属性名列表
#                                  可在调用 sync_to_drawing_parameters() 时通过
#                                  property_names 参数覆盖
# 注意：图纸中若不存在对应参数，同步时会跳过（不自动新建，不报错）。
# ---------------------------------------------------------------------------

DRAWING_SYNC_STANDARD_PARAMS: list[str] = ["PartNumber", "Nomenclature", "Revision"]

DRAWING_SYNC_USER_PROPS: list[str] = ["物料编码", "材料", "重量"]

# ---------------------------------------------------------------------------
# CATDrawing 启发式查找策略（给图纸 → 找对应的 CATPart/CATProduct）
# ---------------------------------------------------------------------------
# find_part_for_drawing() 支持多种策略，按此列表顺序尝试。
# 注意：doc_file_links 已移至正向查询（图纸视图链接），不再属于启发式策略。
# 支持的策略键：
#   "pn_param_open_docs"     – 读图纸 Parameters["PartNumber"]，在已打开文档中匹配
#                              doc.Product.PartNumber == 该值（需 CATIA 运行）
#   "pn_param_scan_dirs"     – 读图纸 Parameters["PartNumber"]，在向上 N 级目录范围内
#                              查找文件名（stem）== 该值的 .CATPart/.CATProduct
#   "same_name_scan_dirs"    – 用图纸文件名 stem 在向上 N 级目录范围内找同名零件文件
#   "strip_prefix_scan_dirs" – 同上，但先 strip 图纸文件名中"前缀_"/"前缀-"前缀再匹配
# ---------------------------------------------------------------------------

DRAWING_SEARCH_STRATEGIES: list[str] = [
    "pn_param_open_docs",
    "pn_param_scan_dirs",
    "same_name_scan_dirs",
    "strip_prefix_scan_dirs",
    "doc_file_links",
]

# 向上查找父目录的最大层级数
SEARCH_MAX_LEVELS: int = 2

# ---------------------------------------------------------------------------
# CATPart/CATProduct 启发式查找策略（给零件/产品 → 找对应的 CATDrawing）
# ---------------------------------------------------------------------------
# find_drawing_for_part() 支持多种策略，按此列表顺序尝试。
# 注意：doc_file_links 已移至反向查询（遍历已打开图纸反查），不再属于启发式策略。
# 支持的策略键：
#   "pn_param_open_drws"     – 遍历已打开 CATDrawing，找 Parameters["PartNumber"]
#                              == 零件 doc.Product.PartNumber 的图纸（需 CATIA 运行）
#   "pn_param_scan_drws"     – 在向上 N 级目录中找文件名（stem）== 零件
#                              doc.Product.PartNumber 的 .CATDrawing
#   "same_name_scan_dirs"    – 在向上 N 级目录中找文件名（stem）== 零件 stem 的 .CATDrawing
#   "strip_prefix_scan_dirs" – 同上，但对图纸文件名先 strip "前缀_"/"前缀-" 再与零件 stem 比较
# ---------------------------------------------------------------------------

PART_TO_DRAWING_STRATEGIES: list[str] = [
    "pn_param_open_drws",
    "pn_param_scan_drws",
    "same_name_scan_dirs",
    "strip_prefix_scan_dirs",
    "doc_file_links",
]

# ---------------------------------------------------------------------------
# AI Agent 相关常量
# ---------------------------------------------------------------------------

# 配置文件名（保存在项目根目录，已加入 .gitignore）
AI_CONFIG_FILENAME = "ai_config.json"

# 默认 API 地址（OpenAI 兼容接口）
AI_DEFAULT_API_BASE = "https://api.openai.com/v1"

# 默认模型
AI_DEFAULT_MODEL = "gpt-4o"

# 单次对话最多工具调用轮数（防止死循环）
AI_MAX_TOOL_ROUNDS = 20

# 聊天面板 Tab 标签
AI_TAB_LABEL = "AI 助手"

# 会话存储目录名（项目根目录下，已加入 .gitignore）
AI_SESSIONS_DIR = "ai_sessions"

# 全局长期记忆文件名（项目根目录下，已加入 .gitignore）
AI_MEMORY_FILENAME = "memory.md"

# 发给 LLM 的最近消息数上限（system 消息不计入，0 = 不限制）
AI_MAX_CONTEXT_MESSAGES = 100
