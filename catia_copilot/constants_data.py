"""纯业务常量；GUI 通过 constants 重导出，CLI 直接使用此模块。"""

import re

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

TYPE_DISPLAY_NAMES: dict = {
    BomNodeType.PART:      "零件",
    BomNodeType.PRODUCT:   "产品",
    BomNodeType.COMPONENT: "部件",
    BomNodeType.MIRROR:    "对称件",
}

MAIN_WINDOW_DEFAULT_WIDTH  = 560

MAIN_WINDOW_DEFAULT_HEIGHT = 520

FONT_FILE_PATH    = "resources/ChangFangSong.ttf"

ISO_XML_FILE_PATH = "resources/ISO.xml"

CRACK_DIR_PATH    = "crack"

APP_ICON_PATH     = "resources/icon.ico"

PRESET_USER_REF_PROPERTIES: list[str] = [
    "零件类型", "设计状态", "材料", "重量",
    "物料编码", "存货类别", "规格型号", "备注",
    # PLM 同步状态字段，由程序在 Push checkin 成功后和 Pull 后自动写入，工程师不手动维护
    "PLM_Version", "PLM_Iteration",
]

PRESET_USER_REF_PROPERTY_OPTIONS: dict[str, list[str]] = {
    "设计状态": ["草稿", "冻结", "发布", "废弃"],
    "存货类别": ["物料-复材件", "物料-金属件", "物料-标准件", "物料-非标件",
                "物料-钣金件", "物料-塑胶件", "物料-橡胶件", "物料-电子件",
                "物料-泡沫", "物料-软包", "物料-辅材", "物料-组件",
                "物料-虚拟件", "半成品-组件", "成品-整机"],
    # PLM 版本字母序，与 DocdokuPLM version 字段对应
    "PLM_Version": ["A", "B", "C", "D", "E", "F", "G", "H"],
}

DOC_EXT_TYPE_MAP: dict[str, str] = {
    ".catpart":    "PartDocument",
    ".catproduct": "ProductDocument",
    ".catdrawing": "DrawingDocument",
}

CATIA_VISUALIZATION_MODE: int = 1

CATIA_DESIGN_MODE:        int = 2

CATIA_MACRO_LIBRARY_DIR: int = 1

CATIA_MACRO_LIBRARY_VBA: int = 2

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

BOM_ALL_COLUMNS: list[str] = [
    "Level", "Type", "Part Number", "Nomenclature",
    "Definition", "Revision", "Source", "Description", "Quantity",
]

BOM_DEFAULT_COLUMNS: list[str] = [
    "Level", "Type", "Part Number", "Nomenclature",
    "Definition", "Revision", "Source", "Description", "Quantity",
]

FILENAME_NOT_FOUND: str = "未检索到"

FILENAME_UNSAVED: str = "未保存"

BOM_ROW_NUMBER_COLUMN: str = "#"

BOM_INSTANCE_NAME_COLUMN: str = "Instance Name"

BOM_READONLY_COLUMNS: frozenset[str] = frozenset({"#", "Level", "Type", "Filename", "Filepath", "Quantity"})

BOM_HIDEABLE_COLUMNS: list[str] = ["Nomenclature", "Revision", "Definition", "Source", "Description"]

BOM_EDIT_COLUMN_ORDER: list[str] = [
    "Level", "Type", "Filename", "Part Number", "Quantity",
    "Nomenclature", "Revision", "Definition", "Source", "Description",
]

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
    "description_inst": "实例描述",
}

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

SOURCE_TO_DISPLAY: dict[str, str]  = {"0": "未知", "1": "自制", "2": "外购"}

SOURCE_FROM_DISPLAY: dict[str, str] = {"未知": "0", "自制": "1", "外购": "2"}

SOURCE_OPTIONS: list[str]           = ["未知", "自制", "外购"]

BOM_THUMBNAIL_MAX_SIZE: int = 130

MASS_PROPS_COLUMNS: list[str] = [
    "Level", "Type", "Filename", "Part Number", "Instance Name", "Nomenclature", "Revision",
    "Density", "Weight", "CogX", "CogY", "CogZ",
    "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz",
]

MASS_PROPS_COLUMN_DISPLAY_NAMES: dict[str, str] = {
    "#":             "#",
    "Level":         "层级",
    "Type":          "类型",
    "Filename":      "文件名",
    "Part Number":   "零件编号",
    "Instance Name": "实例名",
    "Nomenclature":  "术语（中文名称）",
    "Revision":      "版本",
    "Quantity":      "数量",
    "Status":        "状态",
    "Density":       "密度 (kg/m³)",
    "Weight":        "重量 (kg)",
    "CogX":          "重心 X (mm)",
    "CogY":          "重心 Y (mm)",
    "CogZ":          "重心 Z (mm)",
    "Ixx":           "Ixx (kg·mm²)",
    "Iyy":           "Iyy (kg·mm²)",
    "Izz":           "Izz (kg·mm²)",
    "Ixy":           "Ixy (kg·mm²)",
    "Ixz":           "Ixz (kg·mm²)",
    "Iyz":           "Iyz (kg·mm²)",
}

MASS_PROPS_READONLY_COLUMNS: frozenset[str] = frozenset({
    "#", "Level", "Type", "Filename", "Part Number", "Instance Name",
    "Nomenclature", "Revision", "Quantity",
    "CogX", "CogY", "CogZ",
    "Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz",
})

MASS_PROPS_HIDEABLE_COLUMNS: tuple[str, ...] = (
    "Filename", "Part Number", "Instance Name", "Nomenclature", "Revision",
)

PART_NUMBER_VALID_PATTERN: re.Pattern = re.compile(
    r'^[^\x00-\x1f\x7f-\U0010ffff\\/:*?"<>|]*$'
)

PLM_BUILTIN_ATTR_COLS: list[str] = ["Nomenclature", "Definition", "Revision", "Source", "Description"]

PLM_SYNC_MAX_NODES: int = 100

PLM_MEMBER_TABLE_COLUMNS: list[tuple[str, str, str]] = [
    ("login",       "登录名",    "contents"),
    ("name",        "姓名",      "stretch"),
    ("email",       "邮箱",      "stretch"),
    ("language",    "语言",      "fixed:60"),
    ("workspaceId", "工作区",    "contents"),
]

MAX_INERTIA_INDEX: int = 20

DRAWING_SYNC_STANDARD_PARAMS: list[str] = ["PartNumber", "Nomenclature", "Revision"]

DRAWING_SYNC_USER_PROPS: list[str] = ["物料编码", "材料", "重量"]

DRAWING_FILENAME_PREFIX: str = "DR_"

DRAWING_SEARCH_STRATEGIES: list[str] = [
    "pn_param_open_docs",
    "pn_param_scan_dirs",
    "same_name_scan_dirs",
    "strip_prefix_scan_dirs",
    "doc_file_links",
]

SEARCH_MAX_LEVELS: int = 2

PART_TO_DRAWING_STRATEGIES: list[str] = [
    "pn_param_open_drws",
    "pn_param_scan_drws",
    "same_name_scan_dirs",
    "strip_prefix_scan_dirs",
    "doc_file_links",
]
