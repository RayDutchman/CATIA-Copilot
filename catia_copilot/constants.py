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
APP_VERSION = "1.8.0"
APP_DATE    = "2026-05-21"
APP_AUTHOR  = "CHEN Weibo"
APP_CONTACT = "thucwb@gmail.com"

ABOUT_TEXT = f"""{APP_NAME} v{APP_VERSION}

一款面向工程团队的 CATIA V5 效率工具。

主要功能：
  • CATDrawing 批量导出 PDF
  • CATPart / CATProduct 批量导出 STEP
  • CATProduct BOM 导出到 Excel
  • BOM 属性在线编辑与回写 CATIA
  • 重量、重心、转动惯量统计（质量特性汇总）
  • 新建图纸（从模板生成 CATDrawing）
  • 刷新图纸（同步零件属性到图纸参数）
  • CATIA 宏脚本快捷运行
  • 紧固件快速装配（VBA 宏批量装配）
  • 托板螺母快速装配（VBA 宏批量装配）
  • 零件模板刷写（添加标准用户自定义属性）
  • 字体文件 / ISO.xml 标准文件一键部署
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

# Relative path to the QSS stylesheet (used by main.py entry point)
STYLESHEET_RELATIVE_PATH = "catia_copilot/ui/style.qss"

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
# "物料编码", "材料", "重量" 这三个属性在新建图纸和刷新图纸的宏也会用到，修改时请注
# 意保持一致
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
    "Description":  20,
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
# "Description" 是 PLM 创建零件时的描述字段，其余四项与 BOM_HIDEABLE_COLUMNS 一致
PLM_BUILTIN_ATTR_COLS: list[str] = ["Nomenclature", "Definition", "Revision", "Source", "Description"]

# ---------------------------------------------------------------------------
# PLM 同步：单次同步最大节点数（硬限制，超出则禁止同步）
# ---------------------------------------------------------------------------

PLM_SYNC_MAX_NODES: int = 50

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
# CATDrawing 查找策略优先级（给图纸 → 找对应的 CATPart/CATProduct）
# ---------------------------------------------------------------------------
# find_part_for_drawing() 支持多种策略，按此列表顺序尝试，找到即返回。
# 可调整顺序或注释掉某项以禁用对应策略。
# 支持的策略键：
#   "pn_param_open_docs"   – 读图纸 Parameters["PartNumber"]，在已打开文档中匹配
#                            doc.Product.PartNumber == 该值的零件/产品（优先级最高）
#   "pn_param_scan_dirs"   – 读图纸 Parameters["PartNumber"]，在向上 N 级目录范围内
#                            查找文件名（stem）== 该值的 .CATPart/.CATProduct
#   "same_name_scan_dirs"  – 用图纸文件名 stem 在向上 N 级目录范围内找同名零件文件
#   "strip_prefix_scan_dirs" – 同上，但先 strip 图纸文件名中"前缀_"/"前缀-"前缀再匹配
#   "doc_file_links"       – 通过 COM 读取图纸的 FileLinks（被指向的文档列表），
#                            过滤出 .CATPart/.CATProduct（兜底，结果直接来自 CATIA 内部链接）
# ---------------------------------------------------------------------------

DRAWING_SEARCH_STRATEGIES: list[str] = [
    "pn_param_open_docs",
    "pn_param_scan_dirs",
    "same_name_scan_dirs",
    "strip_prefix_scan_dirs",
    "doc_file_links",
]

# 向上查找父目录的最大层级数
# 被 "pn_param_scan_dirs" / "same_name_scan_dirs" / "strip_prefix_scan_dirs" 共用
DRAWING_SEARCH_MAX_LEVELS: int = 2

# ---------------------------------------------------------------------------
# CATPart/CATProduct 查找策略优先级（给零件/产品 → 找对应的 CATDrawing）
# ---------------------------------------------------------------------------
# find_drawing_for_part() 支持多种策略，按此列表顺序尝试，找到即返回。
# 可调整顺序或注释掉某项以禁用对应策略。
# 支持的策略键：
#   "pn_param_open_drws"     – 遍历已打开 CATDrawing，找 Parameters["PartNumber"]
#                              == 零件 doc.Product.PartNumber 的图纸（优先级最高）
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

# 向上查找父目录的最大层级数（给零件找图纸，与 DRAWING_SEARCH_MAX_LEVELS 独立可调）
PART_TO_DRAWING_MAX_LEVELS: int = 2
