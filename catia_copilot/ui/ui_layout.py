"""UI 布局与字体常量。

所有间距、尺寸、字体大小集中在此文件，方便调整 UI 外观而无需改动逻辑代码。
颜色令牌在 ui_colors.py 中管理（ChatColors / RowColors）。

用法：
    from catia_copilot.ui.ui_layout import L
    bar.setFixedHeight(L.TOOLBAR_HEIGHT)
    layout.setContentsMargins(*L.TOOLBAR_MARGINS)
    app.setStyleSheet(f"QLabel {{ font-size: {L.NORMAL_FONT_SIZE}px; }}")

────────────────────────────────────────────────────────────────────────────────
布局层级说明（从外到内）：

  AIChatPanel
  └── AISplitter (QSplitter)
      ├── SessionSidebar          ← 左侧边栏
      └── chat_area (QWidget)     ← 右主区，margins=(0,0,0,0)，spacing=0
          ├── toolbar (QWidget)   ← 顶部工具栏
          ├── ChatScrollArea      ← 聊天消息滚动区
          │   └── _ChatContainer
          │       └── _chat_layout (QVBoxLayout)
          │           ├── UserMessageWidget   ← 用户消息气泡
          │           ├── AIMessageWidget     ← AI 消息气泡
          │           └── ToolCallWidget      ← 工具调用卡片
          └── input_area (QWidget)  ← 底部输入区

注意：
  - chat_area 的 layout margins=(0,0,0,0)，所有左右对齐由各子区域自身控制。
  - ChatScrollArea 用 QSS 覆盖 windowsvista 风格的 border，viewport 无偏移。
  - ToolCallWidget 是 QFrame，其 layout margins 控制卡片内容的内边距；
    卡片本身距聊天区域左边缘由 _chat_layout 的 CHAT_MARGINS.left 统一控制，
    与 AIMessageWidget、UserMessageWidget 的左边缘对齐逻辑相同。
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class _Layout:

    # ══════════════════════════════════════════════════════════════════════════
    # 字体
    # ══════════════════════════════════════════════════════════════════════════
    # 调整这里即可全局改变对应场景的字体/字号，无需逐处修改。
    # 单位说明：
    #   *_PT  字符串，用于 QSS（"9pt"），跟随系统 DPI 缩放
    #   其余  整数 px，用于 setStyleSheet 的 font-size 或 QFont.setPixelSize

    # ── 等宽字体（日志面板、工具调用结果等）────────────────────────────────
    MONO_FONT_FAMILY:        str = '"Consolas", "Cascadia Code", "NSimSun", monospace'
    MONO_FONT_SIZE_PT:       str = "9pt"

    # ── 通用字号层级（px，用于 setStyleSheet / QFont.setPixelSize）──────────
    SMALL_FONT_SIZE:         int = 11   # 辅助文字：提示、进度、状态标签
    NORMAL_FONT_SIZE:        int = 13   # 正文：消息气泡、会话标题、普通标签
    LARGE_FONT_SIZE:         int = 15   # 大图标按钮（emoji）

    # ── QSS pt 字号（用于 native.qss 占位符替换）────────────────────────────
    LABEL_FONT_SIZE_PT:      str = "8pt"    # 节标题（sectionLabel）
    HINT_FONT_SIZE_PT:       str = "9pt"    # 提示标签（hintLabel）
    STATUS_FONT_SIZE_PT:     str = "9pt"    # CATIA 连接状态标签（catiaStatusLabel）
    BUTTON_FONT_SIZE_PT:     str = "9pt"    # 按钮文字（QPushButton）
    TAB_FONT_SIZE_PT:        str = "9pt"    # Tab 标签文字（QTabBar::tab）

    # ── 详细表格（BOM / 质量属性等 QTreeWidget）─────────────────────────────
    TABLE_ROW_HEIGHT:        int = 24   # 统一行高（px），通过 delegate.sizeHint 控制
    TABLE_FONT_SIZE_PT:      str = "9pt"    # 表格正文字号（pt，通过 QSS 设置）
    TABLE_MONO_FONT_FAMILY:  str = '"Consolas", "Cascadia Code", "NSimSun", monospace'
    # 表格字体族：None 表示跟随系统默认（推荐），设为字符串则强制指定
    TABLE_FONT_FAMILY:       str | None = None

    # ══════════════════════════════════════════════════════════════════════════
    # 布局
    # ══════════════════════════════════════════════════════════════════════════

    # ── Splitter ──────────────────────────────────────────────────────────────
    # AISplitter 分隔侧边栏与右主区
    SPLITTER_HANDLE_WIDTH:   int   = 10    # 拖动 handle 的宽度（px）
    SIDEBAR_DEFAULT_WIDTH:   int   = 220   # 侧边栏展开时的默认宽度（px）

    # ── 侧边栏 ────────────────────────────────────────────────────────────────
    SIDEBAR_HEADER_HEIGHT:   int   = 40    # 顶部标题栏高度（px）
    SIDEBAR_HEADER_MARGINS:  tuple = (8, 2, 8, 2)   # 标题栏 layout margins (left,top,right,bottom)
    SIDEBAR_HEADER_SPACING:  int   = 4     # 标题栏内控件间距（px）
    SIDEBAR_DIVIDER_HEIGHT:  int   = 1     # 标题栏与列表之间分隔线高度（px）
    SIDEBAR_LIST_SPACING:    int   = 1     # 会话列表项之间的间距（px）
    SIDEBAR_BOTTOM_HEIGHT:   int   = 52    # 底部"新对话"按钮区高度（px）
    SIDEBAR_BOTTOM_MARGINS:  tuple = (8, 0, 8, 2)   # 底部区域 layout margins
    SIDEBAR_NEW_BTN_HEIGHT:  int   = 30    # "新对话"按钮高度（px）

    # ── 工具栏（toolbar） ─────────────────────────────────────────────────────
    # toolbar 是 chat_area 顶部的一行，包含会话标题、模型选择、设置按钮
    TOOLBAR_HEIGHT:          int   = 40    # toolbar 固定高度（px）
    TOOLBAR_DIVIDER_HEIGHT:  int   = 1     # toolbar 底部分隔线高度（px）
    TOOLBAR_MARGINS:         tuple = (8, 2, 8, 2)   # toolbar layout margins (left,top,right,bottom)
    TOOLBAR_SPACING:         int   = 4     # toolbar 内控件间距（px）
    SESSION_TITLE_WIDTH:     int   = 120   # 会话名 QLabel 固定宽度（px），超出用省略号
    TITLE_FONT_SIZE:         int   = 13    # 会话名字体大小（px）= NORMAL_FONT_SIZE
    ICON_BTN_SIZE:           tuple = (28, 28)   # 铅笔/齿轮图标按钮尺寸 (width, height)
    ICON_BTN_FONT_SIZE:      int   = 15    # 图标按钮字体大小（px）= LARGE_FONT_SIZE
    ICON_BTN_RADIUS:         int   = 4     # 图标按钮 hover 圆角半径（px）
    MODEL_COMBO_MIN_CHARS:   int   = 16    # 模型下拉框最小字符宽度（字符数）
    MODEL_COMBO_MAX_WIDTH:   int   = 200   # 模型下拉框最大宽度（px）

    # ── 聊天消息区（_chat_layout） ────────────────────────────────────────────
    # _chat_layout 是 _ChatContainer 内的 QVBoxLayout，直接包含所有消息 widget。
    # 其 margins 决定所有消息 widget 距 ChatScrollArea 可视区域边缘的距离。
    # left=8/right=8：所有消息 widget（含工具卡片）统一距左右边缘 8px，
    # 与输入框的 INPUT_MARGINS.left=8 对齐，视觉上形成一致的内边距。
    # 各消息 widget 自身的 left/right margins 在此基础上叠加，
    # 用于实现靠左/靠右的气泡效果（AI 靠左、用户靠右）。
    CHAT_MARGINS:            tuple = (8, 8, 8, 8)   # (left=8, top=8, right=8, bottom=8)
    CHAT_SPACING:            int   = 6     # 相邻消息 widget 之间的垂直间距（px）

    # ── 用户消息气泡（UserMessageWidget） ────────────────────────────────────
    # UserMessageWidget 是 QFrame(NoFrame)，内含一个 QHBoxLayout。
    # layout margins 在 CHAT_MARGINS 基础上叠加，决定气泡在 widget 内的位置。
    # left=40 留出大量左侧空白，使气泡视觉上靠右对齐。
    # right=0：右边缘已由 CHAT_MARGINS.right=8 提供，此处不再叠加。
    USER_MSG_MARGINS:        tuple = (40, 4, 0, 4)  # (left=40大留白, top=4, right=0, bottom=4)
    USER_MSG_FONT_SIZE:      int   = 13    # 用户消息字体大小（px）= NORMAL_FONT_SIZE
    USER_MSG_PADDING:        str   = "8px 12px"  # QLabel QSS padding（上下 8px，左右 12px）
    USER_MSG_RADIUS:         int   = 8     # 气泡圆角半径（px）

    # ── AI 消息气泡（AIMessageWidget） ───────────────────────────────────────
    # AIMessageWidget 是 QFrame(NoFrame)，内含一个 QHBoxLayout。
    # layout margins 在 CHAT_MARGINS 基础上叠加，决定气泡在 widget 内的位置。
    # left=0：左边缘已由 CHAT_MARGINS.left=8 提供，此处不再叠加。
    # right=40 留出大量右侧空白，使气泡视觉上靠左对齐。
    AI_MSG_MARGINS:          tuple = (0, 4, 40, 4)  # (left=0, top=4, right=40大留白, bottom=4)
    AI_MSG_FONT_SIZE:        int   = 13    # AI 消息字体大小（px）= NORMAL_FONT_SIZE
    AI_MSG_PADDING_V:        int   = 6     # QTextBrowser QSS 上下 padding（px），影响高度计算
    AI_MSG_PADDING_H:        int   = 10    # QTextBrowser QSS 左右 padding（px）
    AI_MSG_RADIUS:           int   = 8     # 气泡圆角半径（px）
    AI_MSG_MIN_HEIGHT:       int   = 40    # _AutoHeightBrowser 最小高度（px）

    # ── 工具调用卡片（ToolCallWidget） ───────────────────────────────────────
    # ToolCallWidget 是 QFrame(StyledPanel)，有圆角边框背景。
    # Qt 没有直接的 widget 外边距 API，通过在 _insert_widget 里包一层
    # QWidget wrapper 并设置 layout margins 来实现卡片外框到聊天区域的间距。
    #
    # TOOL_CARD_OUTER_MARGINS：卡片外框到聊天区域边缘的外边距（wrapper layout margins）。
    #   right=40 与 AI 气泡的 right 留白对齐，使卡片不撑满整行。
    TOOL_CARD_OUTER_MARGINS: tuple = (0, 2, 40, 2)  # (left=0, top=2, right=40与AI气泡对齐, bottom=2)
    #
    # TOOL_CARD_MARGINS：卡片内部 QVBoxLayout 的 margins，
    #   控制卡片边框到内部内容（标题行、展开内容）的内边距。
    TOOL_CARD_MARGINS:       tuple = (4, 4, 4, 4)   # (left=4, top=4, right=4, bottom=4)
    TOOL_CARD_SPACING:       int   = 2     # 标题行与展开内容之间的间距（px）
    TOOL_CARD_HEADER_SPACING: int  = 4     # 标题行内：折叠箭头与标题文字之间的间距（px）
    TOOL_CARD_TOGGLE_SIZE:   tuple = (16, 16)   # 折叠/展开箭头按钮尺寸 (width, height)
    TOOL_CARD_RADIUS:        int   = 8     # 卡片圆角半径（px）
    #
    # TOOL_CARD_CONTENT_MARGINS：展开后内容区域（_content_widget）的 layout margins，
    #   left=0：内容与标题行左对齐（无额外缩进）。
    TOOL_CARD_CONTENT_MARGINS: tuple = (0, 2, 0, 2)  # (left=0, top=2, right=0, bottom=2)
    TOOL_CARD_CONTENT_SPACING: int = 2    # 内容区域内各子 widget 之间的间距（px）
    TOOL_CARD_RESULT_MAX_HEIGHT: int = 200  # 结果文本框最大高度（px），超出后出现滚动条
    TOOL_CARD_TITLE_FONT_SIZE: int = 12    # 标题行字体大小（px）
    TOOL_CARD_PROGRESS_FONT_SIZE: int = 11 # 进度提示文字字体大小（px）= SMALL_FONT_SIZE
    TOOL_CARD_RESULT_FONT_SIZE: int = 11   # 结果文本框字体大小（px）= SMALL_FONT_SIZE

    # ── 输入区（input_area） ──────────────────────────────────────────────────
    # input_area 是 chat_area 底部的一行，包含文本输入框和发送按钮。
    # left=8 使输入框左边缘与 AI 消息气泡左边缘对齐（AI_MSG_MARGINS.left=8）。
    INPUT_MARGINS:           tuple = (8, 8, 8, 6)   # (left=8, top=8, right=8, bottom=6)
    INPUT_SPACING:           int   = 6     # 输入框与发送按钮之间的间距（px）
    INPUT_BOX_HEIGHT:        int   = 72    # 输入框默认高度（px），用户可通过 splitter 调整
    SEND_BTN_SIZE:           tuple = (60, 60)   # 发送按钮尺寸 (width, height)

    # ── Splitter handle 绘制 ──────────────────────────────────────────────────
    HANDLE_BTN_HEIGHT:       int   = 40    # handle 中央箭头按钮的可点击区域高度（px）


# 全局单例，直接 from catia_copilot.ui.ui_layout import L 使用
L = _Layout()
