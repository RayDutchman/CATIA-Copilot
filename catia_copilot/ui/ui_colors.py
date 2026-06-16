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

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class RowColors:
    """一套完整的行状态颜色，深色/浅色各一个实例。"""
    MODIFIED_FG:          QColor
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

def _pal_hex(role: QPalette.ColorRole, group: QPalette.ColorGroup = QPalette.ColorGroup.Active) -> str:
    """从当前应用 QPalette 取色，返回 '#rrggbb' 字符串。
    若 QApplication 尚未创建则返回空字符串（启动期间不会被调用）。
    """
    app = QApplication.instance()
    if app is None:
        return "#000000"
    return app.palette().color(group, role).name()


def _blend_hex(c1: str, c2: str, t: float) -> str:
    """在两个 '#rrggbb' 颜色之间线性插值，t=0 返回 c1，t=1 返回 c2。"""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass(frozen=True)
class ChatColors:
    """AI 聊天面板的颜色令牌。

    颜色值均为 ``"#rrggbb"`` 字符串，供 QSS / QPainter 直接使用。
    通过 get_chat_colors() 获取，每次调用都从当前系统 QPalette 动态取色，
    自动跟随 Windows 深色/浅色主题切换。
    """
    # 用户消息气泡
    user_bg:       str
    user_fg:       str
    # AI 消息气泡
    ai_bg:         str
    ai_fg:         str
    ai_border:     str
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
    # 分隔线
    divider:       str
    # Splitter handle
    handle_bg:     str
    handle_hover:  str
    handle_fg:     str
    handle_line:   str   # handle 两侧竖线颜色（Midlight，比 divider 更亮）


def get_chat_colors(mode: str) -> ChatColors:
    """从当前系统 QPalette 动态构建 AI 聊天面板颜色集，跟随系统主题。

    :param mode: "dark" 或 "light"（由 theme_manager.current_mode() 提供）
    """
    # 从系统 QPalette 取基础色
    window_bg   = _pal_hex(QPalette.ColorRole.Window)           # 窗口背景
    window_text = _pal_hex(QPalette.ColorRole.WindowText)       # 窗口文字
    base_bg     = _pal_hex(QPalette.ColorRole.Base)             # 输入框/列表背景
    highlight   = _pal_hex(QPalette.ColorRole.Highlight)        # 选中色
    mid         = _pal_hex(QPalette.ColorRole.Mid)              # 中间色（边框/分隔线）
    button_bg   = _pal_hex(QPalette.ColorRole.Button)           # 按钮背景
    mid_light   = _pal_hex(QPalette.ColorRole.Midlight)         # 浅中间色

    # 用户气泡：用 Highlight 色系（选中蓝），混入背景使其柔和
    user_bg = _blend_hex(highlight, window_bg, 0.6)
    user_fg = window_text

    # AI 气泡：用 Base 色（比 Window 略亮/暗），加细边框
    ai_bg     = base_bg
    ai_fg     = window_text
    ai_border = mid

    # 工具卡片：用 Button 色（比 Window 略有区分）
    tool_bg     = button_bg
    tool_fg     = window_text
    tool_border = mid
    progress_fg = _pal_hex(QPalette.ColorRole.PlaceholderText)

    # 侧边栏：与窗口背景一致，选中用 Highlight
    sidebar_bg    = window_bg
    sidebar_fg    = window_text
    sidebar_sel   = highlight
    sidebar_hover = mid_light

    # 分隔线 / handle：用 Mid 色
    divider      = mid
    handle_bg    = window_bg
    handle_hover = mid_light
    handle_fg    = _pal_hex(QPalette.ColorRole.ButtonText)
    # handle 两侧竖线：用 Midlight，比 Mid 更亮，在深/浅两侧背景上都清晰可见
    handle_line  = mid_light

    return ChatColors(
        user_bg=user_bg, user_fg=user_fg,
        ai_bg=ai_bg, ai_fg=ai_fg, ai_border=ai_border,
        tool_bg=tool_bg, tool_fg=tool_fg, tool_border=tool_border,
        progress_fg=progress_fg,
        sidebar_bg=sidebar_bg, sidebar_fg=sidebar_fg,
        sidebar_sel=sidebar_sel, sidebar_hover=sidebar_hover,
        divider=divider,
        handle_bg=handle_bg, handle_hover=handle_hover,
        handle_fg=handle_fg, handle_line=handle_line,
    )

