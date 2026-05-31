"""AI 聊天面板布局常量。

所有间距、尺寸、字体大小集中在此文件，方便调整 UI 外观而无需改动逻辑代码。
颜色令牌在 ui_colors.py 中管理（ChatColors / RowColors）。

用法：
    from catia_copilot.ui.ui_layout import L
    bar.setFixedHeight(L.TOOLBAR_HEIGHT)
    layout.setContentsMargins(*L.TOOLBAR_MARGINS)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class _Layout:
    # ── Splitter ──────────────────────────────────────────────────────────────
    SPLITTER_HANDLE_WIDTH:   int   = 10    # handle 宽度（px）
    SIDEBAR_DEFAULT_WIDTH:   int   = 220   # 侧边栏展开时的默认宽度（px）

    # ── 侧边栏 ────────────────────────────────────────────────────────────────
    SIDEBAR_HEADER_HEIGHT:   int   = 36    # 顶部标题栏高度
    SIDEBAR_HEADER_MARGINS:  Tuple = (0, 0, 0, 0)   # (left, top, right, bottom)
    SIDEBAR_HEADER_SPACING:  int   = 4
    SIDEBAR_DIVIDER_HEIGHT:  int   = 1     # 分隔线高度
    SIDEBAR_LIST_SPACING:    int   = 1     # 列表项间距
    SIDEBAR_BOTTOM_HEIGHT:   int   = 36    # 底部按钮区高度
    SIDEBAR_BOTTOM_MARGINS:  Tuple = (0, 0, 0, 0)
    SIDEBAR_NEW_BTN_HEIGHT:  int   = 32    # "新对话"按钮高度

    # ── 工具栏 ────────────────────────────────────────────────────────────────
    TOOLBAR_HEIGHT:          int   = 36
    TOOLBAR_MARGINS:         Tuple = (0, 0, 0, 0)
    TOOLBAR_SPACING:         int   = 4
    SESSION_TITLE_WIDTH:     int   = 160   # 会话名标签固定宽度
    TITLE_FONT_SIZE:         int   = 13
    ICON_BTN_SIZE:           Tuple = (28, 28)   # 铅笔/齿轮图标按钮尺寸
    ICON_BTN_FONT_SIZE:      int   = 15
    ICON_BTN_RADIUS:         int   = 4
    MODEL_COMBO_MIN_CHARS:   int   = 16    # 模型下拉框最小字符宽度
    MODEL_COMBO_MAX_WIDTH:   int   = 200   # 模型下拉框最大宽度（px）

    # ── 聊天消息区 ────────────────────────────────────────────────────────────
    CHAT_MARGINS:            Tuple = (0, 8, 0, 8)
    CHAT_SPACING:            int   = 6     # 消息气泡之间的间距

    # ── 用户消息气泡 ──────────────────────────────────────────────────────────
    USER_MSG_MARGINS:        Tuple = (40, 4, 8, 4)   # 左侧留白大，气泡靠右
    USER_MSG_FONT_SIZE:      int   = 13
    USER_MSG_PADDING:        str   = "8px 12px"
    USER_MSG_RADIUS:         int   = 8

    # ── AI 消息气泡 ───────────────────────────────────────────────────────────
    AI_MSG_MARGINS:          Tuple = (8, 4, 40, 4)   # 右侧留白大，气泡靠左
    AI_MSG_FONT_SIZE:        int   = 13
    AI_MSG_PADDING_V:        int   = 6    # QTextBrowser 上下 padding（px）
    AI_MSG_PADDING_H:        int   = 10   # QTextBrowser 左右 padding（px）
    AI_MSG_RADIUS:           int   = 8
    AI_MSG_MIN_HEIGHT:       int   = 40   # QTextBrowser 最小高度

    # ── 工具调用卡片 ──────────────────────────────────────────────────────────
    TOOL_CARD_MARGINS:       Tuple = (8, 4, 8, 4)
    TOOL_CARD_SPACING:       int   = 2
    TOOL_CARD_HEADER_SPACING: int  = 4
    TOOL_CARD_TOGGLE_SIZE:   Tuple = (16, 16)   # 折叠箭头按钮尺寸
    TOOL_CARD_CONTENT_MARGINS: Tuple = (20, 2, 0, 2)
    TOOL_CARD_CONTENT_SPACING: int = 2
    TOOL_CARD_RESULT_MAX_HEIGHT: int = 200   # 结果展示区最大高度
    TOOL_CARD_TITLE_FONT_SIZE: int = 12
    TOOL_CARD_PROGRESS_FONT_SIZE: int = 11
    TOOL_CARD_RESULT_FONT_SIZE: int = 11
    TOOL_CARD_RADIUS:        int   = 6

    # ── 输入区 ────────────────────────────────────────────────────────────────
    INPUT_MARGINS:           Tuple = (8, 4, 8, 4)
    INPUT_SPACING:           int   = 6
    INPUT_BOX_HEIGHT:        int   = 72    # 输入框固定高度
    SEND_BTN_SIZE:           Tuple = (60, 60)   # 发送按钮尺寸

    # ── Splitter handle 绘制 ──────────────────────────────────────────────────
    HANDLE_BTN_HEIGHT:       int   = 40    # handle 上箭头按钮区域高度


# 全局单例，直接 from catia_copilot.ui.ui_layout import L 使用
L = _Layout()
