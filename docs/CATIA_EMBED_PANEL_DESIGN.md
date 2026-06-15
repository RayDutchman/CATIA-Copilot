# CATIA 嵌入面板实现总结

## 项目目标

在 CATIA V5 的每个 3D 视图窗口中嵌入一个功能面板，提供快速访问常用功能的入口，避免用户频繁切换到主窗口。

## 最终方案

### 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│ CATIA V5 主窗口 (CATDlgDocument)                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ MDIClient                                              │  │
│  │  ┌──────────────────────────────────────────────┐     │  │
│  │  │ 3D View (CATFrmNavigGraphicWindow)           │     │  │
│  │  │  ┌────────────────────────────────────────┐  │     │  │
│  │  │  │ OpenGL 渲染区域                         │  │     │  │
│  │  │  │ (CATWindowsDrawingArea)                │  │     │  │
│  │  │  └────────────────────────────────────────┘  │     │  │
│  │  └──────────────────────────────────────────────┘     │  │
│  │  ┌─────────────────────┐ ← 嵌入面板 (WS_CHILD)        │  │
│  │  │ [网格点] CATIA Copilot ▼ │   父窗口：MDIClient      │  │
│  │  └─────────────────────┘                              │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 核心技术点

#### 1. 父窗口选择：MDIClient

**问题**：最初尝试将面板作为 3D View 的子窗口，但被 OpenGL 渲染覆盖。

**解决方案**：将面板的父窗口设置为 `MDIClient`，而不是 3D View 本身。

**原因**：
- CATIA 的 3D 视图使用 OpenGL 渲染，渲染链中的任何子窗口都会被覆盖
- `MDIClient` 是 MDI 容器，所有 MDI 子窗口（包括 3D View）都是它的子窗口
- 作为 `MDIClient` 的子窗口，面板与 3D View 是平级关系，不会被 OpenGL 覆盖

#### 2. 坐标换算

**问题**：面板需要定位在 3D View 的右上角，但父窗口是 MDIClient。

**解决方案**：
```python
# 1. 获取 view 左上角在屏幕坐标系中的位置
screen_x, screen_y = win32gui.ClientToScreen(view_hwnd, (0, 0))

# 2. 转换为 MDIClient 坐标系
mdi_x, mdi_y = win32gui.ScreenToClient(mdi_hwnd, (screen_x, screen_y))

# 3. 计算面板位置（右上角）
panel_x = mdi_x + view_width - PANEL_W - anchor_dx
panel_y = mdi_y + anchor_dy
```

**关键**：不能直接使用 `GetWindowRect`，因为它返回的是屏幕坐标，需要通过 `ClientToScreen` + `ScreenToClient` 进行坐标系转换。

#### 3. 锚点系统

**设计**：面板可以吸附在 3D View 的四个角落（TR/TL/BR/BL），并记录相对于锚点的偏移量。

**优点**：
- 用户拖拽面板后，自动选择最近的角作为锚点
- View 窗口缩放时，面板保持在锚点位置，不会越界
- 位置持久化到 `QSettings`，下次启动恢复

**实现**：
```python
# 拖拽结束后，计算距离四个角的距离，选择最近的
distances = {
    "TL": (panel_x - left)**2 + (panel_y - top)**2,
    "TR": (panel_x - (right - PANEL_W))**2 + (panel_y - top)**2,
    "BL": (panel_x - left)**2 + (panel_y - (bottom - PANEL_H))**2,
    "BR": (panel_x - (right - PANEL_W))**2 + (panel_y - (bottom - PANEL_H))**2,
}
anchor = min(distances, key=distances.get)
```

#### 4. 多视图管理

**问题**：CATIA 可以同时打开多个文档，每个文档有自己的 3D 视图。

**解决方案**：
- 维护 `_panels: dict[int, int]` 映射（view_hwnd → panel_hwnd）
- 只显示 Z 序最顶层 view 的面板，其余隐藏
- 使用 `WinEventHook` 监听窗口事件，自动创建/销毁面板

**Z 序检测**：
```python
def _get_top_view(self) -> int | None:
    """获取 Z 序最顶层的 view（最后一个可见的 view）。"""
    views = self._enum_views()
    return views[-1] if views else None
```

#### 5. 面板 UI 设计

**布局**：
```
┌────────┬──────────────────────┐
│ 网格点 │ CATIA Copilot ▼      │  高度：24px
│ (拖拽) │ (下拉菜单按钮)        │  宽度：176px
└────────┴──────────────────────┘
  24px           152px
```

**拖拽区**：
- 左侧 24px 宽的区域，绘制 2×3 网格点（6 个小圆点）
- 处理 `WM_LBUTTONDOWN/MOUSEMOVE/LBUTTONUP` 实现拖拽
- 使用 `SetCapture/ReleaseCapture` 捕获鼠标

**菜单按钮**：
- 右侧 152px 宽的按钮，显示 "CATIA Copilot ▼"
- 点击弹出下拉菜单（`TrackPopupMenu`）
- 菜单包含所有主窗口功能（BOM、导出、图纸、工具等）

#### 6. 线程模型

**架构**：
```
Qt 主线程 (MainWindow)
    ↓ 创建
CATIAEmbedManager
    ↓ start()
后台线程 (CATIAEmbedThread)
    ↓ 运行
Win32 消息循环 (PumpMessages)
    ↓ 处理
面板窗口消息 (WM_COMMAND, WM_PAINT, WM_LBUTTONDOWN, ...)
    ↓ 回调
MainWindow (通过 Signal)
```

**关键**：
- Win32 消息循环在后台线程运行，不阻塞 Qt 主线程
- 回调通过 `Signal` 机制从后台线程安全派发到 Qt 主线程
- 使用 `QTimer.singleShot` 或 `Signal.emit` 进行线程间通信
- COM 调用必须在 Qt 主线程（STA）执行，不能在后台线程直接调用

详见 [DIALOG_BEHAVIOR.md](DIALOG_BEHAVIOR.md)。

## 走过的弯路

### 1. 父窗口选择

**尝试 1**：将面板作为 3D View 的子窗口
- **问题**：被 OpenGL 渲染覆盖，完全不可见
- **原因**：OpenGL 渲染会覆盖整个窗口区域，包括子窗口

**尝试 2**：将面板作为 `CATDlgFrame` 的子窗口
- **问题**：仍然被 OpenGL 覆盖
- **原因**：`CATDlgFrame` 在渲染链中，子窗口仍会被覆盖

**最终方案**：将面板作为 `MDIClient` 的子窗口
- **成功**：与 3D View 平级，不在渲染链中，不会被覆盖

### 2. 坐标换算

**尝试 1**：直接使用 `GetWindowRect` 获取 view 位置
- **问题**：返回的是屏幕坐标，不是 MDIClient 坐标
- **结果**：面板位置偏移约 53px（MDI 框架高度）

**最终方案**：`ClientToScreen` + `ScreenToClient` 进行坐标系转换
- **成功**：面板精确定位在 view 右上角

### 3. 对话框显隐逻辑

对话框的窗口行为（置顶、跟随 CATIA 最小化、几何持久化、已知问题）已独立成专题文档。

详见 [DIALOG_BEHAVIOR.md](DIALOG_BEHAVIOR.md)。

### 4. 菜单弹出

**尝试 1**：`TrackPopupMenu` 的 `hwnd` 参数传 `panel_hwnd`（`WS_CHILD` 窗口）
- **问题**：菜单无法弹出或行为异常
- **原因**：`WS_CHILD` 窗口不能作为弹出菜单的 owner

**最终方案**：`hwnd` 参数传 `self._host_hwnd`（隐藏的顶层窗口）
- **成功**：菜单正常弹出，选择后正确触发回调

## 技术难点与解决方案

### 难点 1：OpenGL 覆盖问题

**问题**：CATIA 的 3D 视图使用 OpenGL 渲染，任何子窗口都会被覆盖。

**解决方案**：
1. 不要将面板作为 3D View 的子窗口
2. 将面板作为 `MDIClient` 的子窗口，与 3D View 平级
3. 通过坐标换算将面板定位在 view 的右上角

### 难点 2：多线程通信

**问题**：Win32 消息循环在后台线程，Qt 主线程不能直接调用。

**解决方案**：
1. 使用 Qt 的 `Signal` 机制进行线程间通信
2. 回调方法 emit 信号，信号自动投递到主线程
3. 主线程的槽方法处理实际逻辑

**示例**：
```python
# 后台线程（Win32 消息循环）
def _open_bom_dialog_from_embed(self) -> None:
    view_hwnd = self._embed_manager._current_view_hwnd or 0
    self._embed_action_signal.emit("bom_edit", view_hwnd)  # 线程安全

# 主线程（Qt 事件循环）
@Slot(str, int)
def _handle_embed_action(self, action: str, view_hwnd: int) -> None:
    # 在主线程中执行
    self._do_open_bom_dialog()
```

### 难点 3：窗口生命周期管理

**问题**：
- CATIA 可以动态打开/关闭文档
- 每个文档有自己的 3D 视图
- 需要自动创建/销毁对应的面板

**解决方案**：
1. 使用 `WinEventHook` 监听 `EVENT_OBJECT_SHOW` 事件
2. 定时器（3 秒）兜底扫描新 view
3. `_update_all_panels` 中检测已销毁的 view，自动清理面板

### 难点 4：MDI 最大化延迟

**问题**：MDI 子窗口最大化时，新 view 的面板延迟 3 秒才出现。

**原因**：只依赖定时器扫描，最大化时 view 立即创建，但定时器还没触发。

**解决方案**：
- `WinEventHook` 回调中额外调用 `_scan_new_views`
- 立即检测新 view 并创建面板，消除延迟

## 代码结构

### 核心文件

```
catia_copilot/ui/
├── catia_embed.py          # 嵌入面板管理器（~1000 行）
│   ├── CATIAEmbedManager   # 主类
│   ├── _run()              # 后台线程入口
│   ├── _panel_wndproc()    # 面板窗口过程
│   ├── _show_popup_menu()  # 弹出菜单
│   └── _update_all_panels()# 刷新面板位置/显隐
└── main_window.py          # 主窗口（~1700 行）
    ├── _embed_manager      # 嵌入管理器实例
    ├── _embed_action_signal# 跨线程信号
    ├── _handle_embed_action# 信号槽
    └── _show_dialog()      # 对话框管理（添加 StaysOnTop）
```

### 关键常量

```python
# 面板尺寸
PANEL_W = 176  # 面板宽度
PANEL_H = 24   # 面板高度
DRAG_W  = 24   # 拖拽区宽度
BTN_W   = 152  # 按钮宽度

# 默认锚点
DEFAULT_ANCHOR    = "TR"  # 右上角
DEFAULT_ANCHOR_DX = -8    # 向左偏移 8px
DEFAULT_ANCHOR_DY = 4     # 向下偏移 4px

# 定时器
SCAN_VIEWS_INTERVAL = 3000  # 扫描新 view 的间隔（毫秒）
```

## 用户体验

### 优点

1. **快速访问**：无需切换到主窗口，直接在 3D 视图中访问功能
2. **位置可调**：用户可以拖拽面板到四个角落，位置持久化
3. **多文档支持**：每个 3D 视图都有自己的面板，自动管理
4. **行为一致**：无论从主窗口按钮还是嵌入面板触发，对话框行为完全一致

### 注意事项

1. **面板只在活动 view 显示**：切换到其他 view 时，面板自动隐藏/显示
2. **对话框是单例**：同一个对话框只创建一次，重复点击会激活已有对话框
3. **对话框置顶行为**：见 [DIALOG_BEHAVIOR.md](DIALOG_BEHAVIOR.md)

## 未来改进方向

### 1. 托盘化运行

**目标**：CATIA Copilot 随 CATIA 自动启动，最小化到系统托盘。

**计划**：见 `docs/plan-tray-autostart.md`

### 2. 面板样式优化

**可能改进**：
- 支持自定义面板颜色/透明度
- 支持更多锚点位置（边缘中点）
- 支持面板大小调整

### 3. 更多功能入口

**可能添加**：
- 快捷键支持
- 右键菜单集成
- 工具栏集成

## 总结

经过多次尝试和优化，最终找到了一个简洁、稳定、用户体验良好的方案：

1. **父窗口选择**：`MDIClient`（避免 OpenGL 覆盖）
2. **坐标换算**：`ClientToScreen` + `ScreenToClient`（精确定位）
3. **锚点系统**：四角吸附 + 偏移量（灵活可调）
4. **多视图管理**：Z 序检测 + WinEventHook（自动管理）
5. **线程模型**：后台线程 + Signal（线程安全）
6. **对话框行为**：见 [DIALOG_BEHAVIOR.md](DIALOG_BEHAVIOR.md)

这个方案的核心优势是**简单**：
- 代码量少（~1000 行）
- 逻辑清晰（单一职责）
- 易于维护（无复杂状态管理）
- 用户体验好（行为一致、响应快速）

---

**文档版本**：1.1  
**最后更新**：2026-05-31  
**作者**：CATIA Copilot 开发团队
