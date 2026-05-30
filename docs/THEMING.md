# 自定义外观实现说明

## 文件结构

```
catia_copilot/ui/
├── theme_manager.py          # 主题管理核心逻辑
├── dark.qss                  # 深色主题样式表
├── light.qss                 # 浅色主题样式表
├── check_white.svg           # 复选框勾号图标（白色）
├── radio_checked.svg         # 单选框选中图标（蓝色外环 + 中心点）
├── radio_unchecked_dark.svg  # 单选框未选中图标（灰色圆环，深色主题；浅色主题走原生渲染）
└── title_bar.py              # 自绘标题栏组件
```

---

## 三层实现

### 第一层：无边框窗口 + 自绘标题栏

主窗口设置了 `Qt.FramelessWindowHint`，移除系统标题栏，由 `TitleBar` 组件负责绘制关闭、最大化、最小化按钮。这是 Fluent Design 风格的结构基础，与 QSS 无关。

### 第二层：QSS 全局样式表

`theme_manager.py` 在启动时调用 `QApplication.setStyleSheet(qss)`，对整个进程的所有窗口（包括后续打开的对话框）全局生效。

`dark.qss` / `light.qss` 覆盖的控件：

| 类别 | 控件 |
|------|------|
| 布局 | 窗口/面板背景、GroupBox 圆角卡片 |
| 交互 | QPushButton、QLineEdit、QComboBox |
| 数据 | QTableWidget、QHeaderView、QProgressBar |
| 选择 | QRadioButton（SVG 图标）、QCheckBox（SVG 图标） |
| 其他 | QStatusBar、日志框、标题栏区域、滚动条 |

SVG 图标通过占位符机制注入 QSS：

```python
# _apply() 中
qss = (DARK_QSS if mode == "dark" else LIGHT_QSS) \
    .replace("@check_icon", str(_UI_DIR / "check_white.svg")) \
    .replace("@radio_checked_icon", str(_UI_DIR / "radio_checked.svg"))
```

QSS 文件在模块导入时一次性读取并缓存，文件缺失时回退空字符串（样式降级为系统原生，程序仍可正常启动）。

### 第三层：Windows DWM 标题栏着色

对话框有系统原生标题栏，QSS 无法控制其颜色。通过 `ctypes` 调用 Windows DWM API 解决：

| Windows 版本 | API 属性 | 效果 |
|-------------|---------|------|
| Win11 / Win10 21H1+ | `DWMWA_CAPTION_COLOR`（属性 35） | 精确设置标题栏颜色（深色 `#2b2b2b` / 浅色 `#ffffff`） |
| Win10 旧版 | `DWMWA_USE_IMMERSIVE_DARK_MODE`（属性 20 / 19） | 切换深/浅色系统标题栏 |

`_DwmEventFilter` 挂载在 `QApplication` 上，捕获所有顶层窗口的 `Show` 事件，确保新打开的对话框自动获得正确的标题栏颜色。

---

## 主题切换流程

```
theme_manager.toggle()
    │
    ├── 写入 QSettings("CATIACopilot", "theme")  ← 持久化，下次启动自动恢复
    │
    └── _apply()
            ├── 读取 dark.qss 或 light.qss
            ├── 替换 @check_icon / @radio_checked_icon 为绝对路径
            ├── app.setStyleSheet(qss)              ← 全局生效
            ├── theme_signal.theme_changed.emit()   ← 通知业务逻辑（如表格行着色）
            └── _apply_dwm_dark_mode()              ← 更新所有已打开窗口的标题栏
```

系统主题变化时（用户在 Windows 设置里切换深/浅色），仅在无手动偏好时自动跟随。

---

## 注意事项

- 主窗口使用自绘标题栏，DWM 着色对其无视觉效果（无系统标题栏），但调用无害
- 单选框和复选框的指示器尺寸均为 16×16px，全状态统一使用 1px border，保证 checked/unchecked 视觉大小一致
- `check_white.svg` 和 `radio_checked.svg` 缺失时图标消失，但控件仍可正常使用（不崩溃）
- DWM API 调用在非 Windows 平台或调用失败时静默跳过
