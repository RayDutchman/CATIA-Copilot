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
"""

from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtGui import QColor


@dataclass(frozen=True)
class RowColors:
    """一套完整的行状态颜色 + 样式表字符串，深色/浅色各一个实例。"""
    MODIFIED_FG:         QColor
    MODIFIED_COMBO_STYLE: str
    ROW_LOCKED_FG:       QColor
    ROW_NOT_FOUND_BG:    QColor
    ROW_LIGHTWEIGHT_BG:  QColor
    ROW_UNSAVED_BG:      QColor
    ROW_MEAS_FAILED_BG:  QColor
    ROW_PRODUCT_BG:      QColor
    EXCL_BG:             QColor
    EXCL_FG:             QColor
    MIRROR_BG:           QColor


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

# ── 树控件层级连接线颜色（与主题无关，保持不变）──────────────────────────────
WIDGET_LINE_COLOR = QColor("#a0aab4")
