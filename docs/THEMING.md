# 主题系统实现说明

## 文件结构

```
catia_copilot/ui/
├── theme_manager.py          # 主题管理核心逻辑（单例 ThemeManager + theme_signal）
├── native.qss                # 项目最小 QSS 覆盖（仅日志字体、CATIA 状态色、布局容器清零）
├── ui_layout.py              # 所有布局与字体常量（L 单例），QSS 占位符值来源
└── ui_colors.py              # 行状态颜色令牌（RowColors）+ 聊天面板颜色令牌（ChatColors）
```

---

## 三层实现

### 第一层：Windows11 风格（QStyle）

`theme_manager.py` 在 `register()` 时调用 `app.setStyle("windows11")`（若 Qt 版本不支持则回退 `"windowsvista"`）。

windows11 风格的特性：
- **原生深色/浅色模式**：自动读取系统 `QPalette`，字体、圆角、颜色均由 Windows 系统主题决定
- **无边框容器**：窗口、面板、GroupBox 等控件的边框和圆角由系统接管
- **无需手动构建调色板**：不再需要旧架构中的 dark.qss / light.qss 手动配色

### 第二层：native.qss（项目最小覆盖）

`native.qss` 只写项目专属控件的最小覆盖，其余控件全部留空由 windows11 风格渲染器接管。

覆盖的控件：

| 控件 | 说明 |
|------|------|
| `QPushButton` / `QTabBar::tab` | 文字字号（占位符替换） |
| `QLabel#sectionLabel` | 节标题：字号 + 粗体 + 透明背景 |
| `QLabel#hintLabel` | 提示标签：字号 + 透明背景 |
| `QLabel#catiaStatusLabel` | CATIA 连接状态：用 `[catiaConnected="true"]` 属性选择器着色 |
| `QPlainTextEdit#logView` | 日志面板：等宽字体 |
| `QWidget#ChatArea` / `#ToolbarWrapper` | 聊天区/工具栏：用 `palette(base)` 背景，与侧边栏的 `palette(window)` 形成分隔 |
| `QSplitter#InputSplitter::handle` | 输入区分隔线：`palette(mid)` 背景 |

QSS 占位符（由 `theme_manager._apply()` 替换，值来自 `ui_layout.L`）：

| 占位符 | 来源 | 用途 |
|--------|------|------|
| `@mono_font_family` | `L.MONO_FONT_FAMILY` | 等宽字体族 |
| `@mono_font_size_pt` | `L.MONO_FONT_SIZE_PT` | 等宽字体字号 |
| `@label_font_size_pt` | `L.LABEL_FONT_SIZE_PT` | 节标题字号 |
| `@hint_font_size_pt` | `L.HINT_FONT_SIZE_PT` | 提示标签字号 |
| `@status_font_size_pt` | `L.STATUS_FONT_SIZE_PT` | 状态标签字号 |
| `@button_font_size_pt` | `L.BUTTON_FONT_SIZE_PT` | 按钮文字字号 |
| `@tab_font_size_pt` | `L.TAB_FONT_SIZE_PT` | Tab 标签字号 |

### 第三层：Windows DWM 标题栏着色

对话框有系统原生标题栏，QSS 无法控制其颜色。通过 `ctypes` 调用 Windows DWM API 解决：

| API 属性 | 效果 |
|---------|------|
| `DWMWA_CAPTION_COLOR`（attr=35，Win11 专属） | 精确设置标题栏颜色（深色 `#2b2b2b` / 浅色 `#f3f3f3`） |

**不设置 attr=20/19**（`DWMWA_USE_IMMERSIVE_DARK_MODE`），因为 windows11 风格已自动处理深/浅切换，重复设置会产生 "Unable to set light window border" 警告。

`_DwmEventFilter` 挂载在 `QApplication` 上，捕获所有顶层窗口的 `Show` 事件，确保新打开的对话框自动获得正确的标题栏颜色。

---

## 主题切换流程

```
系统深色/浅色切换
    │
    └── QGuiApplication.styleHints().colorSchemeChanged
            │
            └── theme_manager._apply()
                    ├── app.setStyle("windows11")          ← 恢复风格（确保一致）
                    ├── 读取 native.qss
                    ├── 替换 @xxx 占位符（值来自 ui_layout.L）
                    ├── app.setStyleSheet(qss)             ← 全局生效
                    ├── theme_signal.theme_changed.emit()   ← 通知业务逻辑
                    └── _apply_dwm_caption_color()          ← 更新标题栏颜色
```

**不再提供手动主题切换**：主题始终跟随 Windows 系统设置，无 toggle() 方法。

---

## 动态颜色系统

### ui_colors.py — 行状态颜色（RowColors）

用于表格/BOM 树控件的行背景和文字着色。通过 `get_colors(mode)` 获取，`mode` 为 `"dark"` 或 `"light"`。

```python
from catia_copilot.ui.ui_colors import get_colors
c = get_colors(theme_manager.current_mode())
item.setBackground(ci, c.ROW_NOT_FOUND_BG)
```

### ui_colors.py — 聊天面板颜色（ChatColors）

用于 AI 聊天面板的气泡、侧边栏、分隔线等。通过 `get_chat_colors(mode)` 获取，每次调用都从当前系统 `QPalette` 动态取色，自动跟随深色/浅色切换。

```python
from catia_copilot.ui.ui_colors import get_chat_colors
c = get_chat_colors(theme_manager.current_mode())
widget.setStyleSheet(f"background: {c.ai_bg}; color: {c.ai_fg};")
```

ChatColors 包含：用户气泡（`user_bg/fg`）、AI 气泡（`ai_bg/fg/border`）、工具卡片（`tool_bg/fg/border`）、侧边栏（`sidebar_bg/fg/sel/hover`）、分隔线（`divider`）、Splitter handle（`handle_bg/hover/fg/line`）。

---

## 字体与尺寸常量（ui_layout.py）

所有间距、尺寸、字体大小集中在 `_Layout` 数据类（单例 `L`），方便调整 UI 外观而无需改动逻辑代码。

字号层级：
- `SMALL_FONT_SIZE` = 11px：辅助文字（提示、进度、状态标签）
- `NORMAL_FONT_SIZE` = 13px：正文（消息气泡、会话标题）
- `LARGE_FONT_SIZE` = 15px：大图标按钮（emoji）

QSS pt 字号（通过占位符替换到 native.qss）：
- `MONO_FONT_SIZE_PT` = "9pt"：等宽字体（日志、工具结果）
- `LABEL_FONT_SIZE_PT` / `HINT_FONT_SIZE_PT` / `STATUS_FONT_SIZE_PT` = "10pt"
- `BUTTON_FONT_SIZE_PT` / `TAB_FONT_SIZE_PT` = "10pt"

---

## 注意事项

- 主窗口使用自绘标题栏（若启用），DWM 着色对其无视觉效果，但调用无害
- `native.qss` 缺失时 QSS 为空字符串，样式完全降级为 windows11 风格默认值，程序仍可正常启动
- DWM API 调用在非 Windows 平台或调用失败时静默跳过
- emoji/符号按钮统一指定 `QFont("Segoe UI Emoji")`，确保 Braille 图标（如 `_TypingIndicatorWidget` 的转圈动画）正常显示
