"""集中定义 UI 行状态颜色令牌，支持深色 / 浅色双主题。

颜色值均使用 ``"#rrggbb"`` 十六进制字符串，VS Code 内置调色板会在
悬停时自动激活，方便直接在此文件中调色。

用法：
  from catia_copilot.ui.ui_colors import get_colors
  c = get_colors(theme_manager.current_mode())  # "dark" 或 "light"
  item.setBackground(ci, c.ROW_NOT_FOUND_BG)

配色语义 & 触发条件速查：

  常量名               触发 flag / 条件
  ──────────────────── ─────────────────────────────────────────
  MODIFIED_FG          字段已在 UI 中修改但尚未写回 CATIA
  ROW_LOCKED_FG        行处于锁定状态，叠加于行背景色之上
  ROW_NOT_FOUND_BG     row_data["_not_found"] = True
  ROW_LIGHTWEIGHT_BG   row_data["_unreadable"] = True（轻量化模式）
  ROW_UNSAVED_BG       row_data["_no_file"] = True（未保存到磁盘）
  ROW_MEAS_FAILED_BG   row_data["_meas_failed"] = True（仅 mass_props_dialog）
  ROW_PRODUCT_BG       row_data["Type"] in ("产品","部件")（仅 mass_props_dialog）
  EXCL_BG              row_data["_excluded"] = True
  EXCL_FG              同上，与 EXCL_BG 同时生效
  MIRROR_BG            row_data["_is_mirror"] = True（仅 mass_props_dialog）
  DEP_COM_FG           依赖查找结果行：引用的文档（COM 链接）
  DEP_2A_FG            依赖查找结果行：被引用零件/产品（2A 策略）
  DEP_2B_FG            依赖查找结果行：被引用图纸（2B 策略）
  DEP_ERROR_FG         依赖查找：搜索错误提示文字
  DEP_EMPTY_FG         依赖查找：无结果提示文字
  DEP_DONE_FG          依赖查找：搜索完成汇总文字
  WIDGET_LINE_COLOR    树控件层级连接线颜色
"""

from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtGui import QColor


@dataclass(frozen=True)
class RowColors:
    """一套完整的行状态颜色 + 样式表字符串，深色/浅色各一个实例。"""
    MODIFIED_FG:          QColor
    MODIFIED_COMBO_STYLE: str
    ROW_LOCKED_FG:        QColor
    ROW_NOT_FOUND_BG:     QColor
    ROW_LIGHTWEIGHT_BG:   QColor
    ROW_UNSAVED_BG:       QColor
    ROW_MEAS_FAILED_BG:   QColor
    ROW_PRODUCT_BG:       QColor
    EXCL_BG:              QColor
    EXCL_FG:              QColor
    MIRROR_BG:            QColor
    DEP_COM_FG:           QColor
    DEP_2A_FG:            QColor
    DEP_2B_FG:            QColor
    DEP_ERROR_FG:         QColor
    DEP_EMPTY_FG:         QColor
    DEP_DONE_FG:          QColor
    WIDGET_LINE_COLOR:    QColor


# ── 浅色主题（默认）──────────────────────────────────────────────────────────
_LIGHT = RowColors(
    MODIFIED_FG          = QColor("#c05800"),   # 深橙：已修改字段文字
    MODIFIED_COMBO_STYLE = "QComboBox { font-weight: bold; color: #c05800; }",
    ROW_LOCKED_FG        = QColor("#909090"),   # 中灰：锁定行文字
    ROW_NOT_FOUND_BG     = QColor("#ffcccc"),   # 粉红：_not_found
    ROW_LIGHTWEIGHT_BG   = QColor("#ebebeb"),   # 浅灰：_unreadable（轻量化）
    ROW_UNSAVED_BG       = QColor("#fff9c4"),   # 浅黄：_no_file（未保存）
    ROW_MEAS_FAILED_BG   = QColor("#ffe0b3"),   # 浅橙：_meas_failed（测量失败）
    ROW_PRODUCT_BG       = QColor("#dde8f5"),   # 浅蓝灰：产品/部件汇总行
    EXCL_BG              = QColor("#d8d4f0"),   # 薰衣草：_excluded（已排除）
    EXCL_FG              = QColor("#5858a0"),   # 深紫：_excluded 前景
    MIRROR_BG            = QColor("#c8e4ff"),   # 浅蓝：_is_mirror（对称件）
    DEP_COM_FG           = QColor("#1565C0"),   # 深蓝：引用的文档（COM 链接）
    DEP_2A_FG            = QColor("#2E7D32"),   # 深绿：被引用零件/产品（2A）
    DEP_2B_FG            = QColor("#6A1B9A"),   # 深紫：被引用图纸（2B）
    DEP_ERROR_FG         = QColor("#B71C1C"),   # 深红：搜索错误
    DEP_EMPTY_FG         = QColor("#777777"),   # 中灰：无结果提示
    DEP_DONE_FG          = QColor("#0277BD"),   # 深青蓝：搜索完成（与 DEP_2A_FG 区分）
    WIDGET_LINE_COLOR    = QColor("#b0bec5"),   # 蓝灰：树控件层级连接线
)

# ── 深色主题（柔和暗色调，避免在深色背景上过于刺眼）────────────────────────
_DARK = RowColors(
    MODIFIED_FG          = QColor("#ff9040"),   # 亮橙：深色背景下可读
    MODIFIED_COMBO_STYLE = "QComboBox { font-weight: bold; color: #ff9040; }",
    ROW_LOCKED_FG        = QColor("#767676"),   # 中灰：锁定行文字
    ROW_NOT_FOUND_BG     = QColor("#5a2020"),   # 暗红：_not_found
    ROW_LIGHTWEIGHT_BG   = QColor("#383838"),   # 深灰：_unreadable（轻量化）
    ROW_UNSAVED_BG       = QColor("#484510"),   # 暗黄：_no_file（未保存）
    ROW_MEAS_FAILED_BG   = QColor("#4a3010"),   # 暗琥珀：_meas_failed（测量失败）
    ROW_PRODUCT_BG       = QColor("#1e3050"),   # 深蓝：产品/部件汇总行
    EXCL_BG              = QColor("#28254a"),   # 深薰衣草：_excluded（已排除）
    EXCL_FG              = QColor("#a8a8e0"),   # 浅紫：_excluded 前景（深色背景可读）
    MIRROR_BG            = QColor("#1a3550"),   # 深蓝：_is_mirror（对称件）
    DEP_COM_FG           = QColor("#64b5f6"),   # 亮蓝：引用的文档（COM 链接）
    DEP_2A_FG            = QColor("#81c784"),   # 亮绿：被引用零件/产品（2A）
    DEP_2B_FG            = QColor("#ce93d8"),   # 亮紫：被引用图纸（2B）
    DEP_ERROR_FG         = QColor("#ef9a9a"),   # 亮红：搜索错误
    DEP_EMPTY_FG         = QColor("#909090"),   # 中灰：无结果提示
    DEP_DONE_FG          = QColor("#4dd0e1"),   # 亮青：搜索完成（与 DEP_2A_FG 区分）
    WIDGET_LINE_COLOR    = QColor("#4a5568"),   # 暗蓝灰：树控件层级连接线
)


def get_colors(mode: str) -> RowColors:
    """根据主题模式返回对应的行状态颜色集。

    :param mode: "dark" 或 "light"（其他值等同于 "light"）
    """
    return _DARK if mode == "dark" else _LIGHT


# ── 向后兼容：保留模块级常量（均为浅色主题值）────────────────────────────────
# 旧代码可继续直接导入这些常量；新代码请改用 get_colors()。
MODIFIED_FG          = _LIGHT.MODIFIED_FG
MODIFIED_COMBO_STYLE = _LIGHT.MODIFIED_COMBO_STYLE
ROW_LOCKED_FG        = _LIGHT.ROW_LOCKED_FG
ROW_NOT_FOUND_BG     = _LIGHT.ROW_NOT_FOUND_BG
ROW_LIGHTWEIGHT_BG   = _LIGHT.ROW_LIGHTWEIGHT_BG
ROW_UNSAVED_BG       = _LIGHT.ROW_UNSAVED_BG
ROW_MEAS_FAILED_BG   = _LIGHT.ROW_MEAS_FAILED_BG
ROW_PRODUCT_BG       = _LIGHT.ROW_PRODUCT_BG
EXCL_BG              = _LIGHT.EXCL_BG
EXCL_FG              = _LIGHT.EXCL_FG
MIRROR_BG            = _LIGHT.MIRROR_BG

# ── 树控件层级连接线颜色（向后兼容，取浅色主题值）────────────────────────────
WIDGET_LINE_COLOR = _LIGHT.WIDGET_LINE_COLOR


# ===========================================================================
# AI 聊天面板颜色令牌
# ===========================================================================

@dataclass(frozen=True)
class ChatColors:
    """AI 聊天面板的颜色令牌，深色/浅色各一个实例。

    颜色值均为 ``"#rrggbb"`` 字符串，供 QSS / QPainter 直接使用。
    """
    # 用户消息气泡
    user_bg:       str
    user_fg:       str
    # AI 消息气泡
    ai_bg:         str
    ai_fg:         str
    ai_border:     str   # 气泡细边框，增强与背景的区分
    # 工具调用卡片
    tool_bg:       str
    tool_fg:       str
    tool_border:   str
    progress_fg:   str
    # 侧边栏
    sidebar_bg:    str
    sidebar_fg:    str
    sidebar_sel:   str
    sidebar_hover: str
    # 分隔线（侧边栏标题/底部、toolbar 底部）
    divider:       str
    # Splitter handle
    handle_bg:     str   # handle 整体背景（与侧边栏融合）
    handle_hover:  str   # 箭头区域 hover 背景
    handle_fg:     str   # 箭头文字颜色


_CHAT_DARK = ChatColors(
    user_bg       = "#1e3a5f",
    user_fg       = "#e8f0fe",
    ai_bg         = "#1e2d3d",   # 比 qdarkstyle 背景(#19232D)亮，带蓝调，有明显区分
    ai_fg         = "#dce8f0",
    ai_border     = "#2e4a62",   # 细边框，进一步勾勒气泡边界
    tool_bg       = "#1a2a1a",
    tool_fg       = "#a0d0a0",
    tool_border   = "#3a5a3a",
    progress_fg   = "#808080",
    sidebar_bg    = "#19232D",   # 与 qdarkstyle 主背景一致，消除色调冲突
    sidebar_fg    = "#DFE1E2",   # qdarkstyle 标准文字色
    sidebar_sel   = "#346792",   # qdarkstyle 选中色
    sidebar_hover = "#37414F",   # qdarkstyle hover 色
    divider       = "#37414F",   # qdarkstyle 边框/分隔色
    handle_bg     = "#293544",   # qdarkstyle 次背景
    handle_hover  = "#37414F",
    handle_fg     = "#9DA9B5",   # qdarkstyle 次要文字色
)

_CHAT_LIGHT = ChatColors(
    user_bg       = "#dce8ff",
    user_fg       = "#1a1a2e",
    ai_bg         = "#eef2f7",   # 带蓝调的浅灰，在白色背景上有明显区分
    ai_fg         = "#1a1a1a",
    ai_border     = "#c8d8e8",   # 浅蓝灰细边框
    tool_bg       = "#f0fff0",
    tool_fg       = "#2e7d32",
    tool_border   = "#a5d6a7",
    progress_fg   = "#757575",
    sidebar_bg    = "#FAFAFA",   # 与 qdarkstyle light 主背景一致
    sidebar_fg    = "#19232D",   # qdarkstyle light 标准文字色
    sidebar_sel   = "#DAEDFF",   # qdarkstyle light 选中色
    sidebar_hover = "#D2D5D8",   # qdarkstyle light hover 色
    divider       = "#C0C4C8",   # qdarkstyle light 边框/分隔色
    handle_bg     = "#F0F2F3",   # qdarkstyle light 次背景
    handle_hover  = "#C0C4C8",
    handle_fg     = "#555555",
)


def get_chat_colors(mode: str) -> ChatColors:
    """根据主题模式返回 AI 聊天面板颜色集。

    :param mode: "dark" 或 "light"（其他值等同于 "light"）
    """
    return _CHAT_DARK if mode == "dark" else _CHAT_LIGHT
