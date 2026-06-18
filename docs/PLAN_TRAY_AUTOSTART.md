# 托盘化 & 随 CATIA 自动启用 — 实施计划

> 状态：**已规划，尚未实施**
> 前置条件：嵌入 CATIA 3D 视图的工作完成后再执行

---

## 目标

- 程序打包为 exe 后开机自启，常驻系统托盘
- 检测到 CATIA V5 启动时，自动激活 3D 视图嵌入面板
- CATIA 关闭后，嵌入面板自动停止，程序继续在托盘等待
- 点主窗口 ✕ 不退出，改为最小化到托盘；只有托盘菜单"退出"才真正退出

---

## 前置说明

| 项目 | 说明 |
|---|---|
| exe 路径 | 随版本变化（如 `CATIA Copilot 1.8.0.exe`），开机自启注册表值用 `sys.executable` 动态获取，不硬编码版本号 |
| QSettings 组织名 | 统一用 `"CATIACopilot"`，与现有对话框用的 `"CATIACopilot"` 区分 |
| `broken` 状态处理 | CATIA 进程存在但 COM 不通时为 `broken`；embed 依赖 win32 窗口句柄而非 COM，理论上可工作；**暂定只在 `connected` 时自动激活**，待后续确认 |
| build.spec | `QSystemTrayIcon` 属于 `QtWidgets`，已被打包，**不需要改 spec** |

---

## 改动 1 — `catia_copilot/utils.py`

新增两个函数，放在文件末尾：

```python
def get_autostart() -> bool:
    """读取当前用户是否已设置开机自启（HKCU\...\Run）。"""

def set_autostart(enabled: bool) -> None:
    """写入或删除开机自启注册表项。
    - 注册表键名：HKCU\Software\Microsoft\Windows\CurrentVersion\Run
    - 值名称：固定为 "CATIA Copilot"
    - 值内容：sys.executable（打包后即 exe 路径）
    - 只需普通用户权限，无需管理员
    """
```

---

## 改动 2 — 新建 `catia_copilot/ui/tray_icon.py`

封装 `QSystemTrayIcon`，**只负责 UI，不持有业务逻辑**。

### 信号

| 信号 | 触发时机 |
|---|---|
| `show_window_requested` | 双击托盘图标 / 菜单"显示主窗口" |
| `quit_requested` | 菜单"退出" |
| `open_bom_edit_requested` | 菜单"BOM 属性补全" |
| `open_bom_export_requested` | 菜单"BOM 导出" |
| `open_mass_props_requested` | 菜单"质量特性" |
| `embed_toggle_requested(bool)` | 菜单"嵌入 3D 视图面板"勾选/取消 |
| `autostart_toggle_requested(bool)` | 菜单"开机自动启动"勾选/取消 |

### 右键菜单结构

```
显示主窗口
──────────────────
BOM 属性补全
BOM 导出
质量特性
──────────────────
[✓] 嵌入 3D 视图面板      ← checkable，由外部调用 set_embed_checked() 同步
──────────────────
[✓] 开机自动启动           ← checkable，由外部调用 set_autostart_checked() 同步
──────────────────
退出
```

### 对外方法

```python
def set_embed_checked(checked: bool) -> None
def set_autostart_checked(checked: bool) -> None
```

---

## 改动 3 — `catia_copilot/ui/main_window.py`

### 3a. `__init__` 新增

```python
self._settings = QSettings("CATIACopilot", "MainWindow")
self._last_catia_status: str = ""   # 用于检测连接状态变化
self._tray = TrayIcon(self)
self._setup_tray()
self._load_settings()
# 末尾：根据上次保存的 window/visible 决定是否显示主窗口
if self._settings.value("window/visible", False, type=bool):
    self.show()
# 否则只显示托盘图标，不显示主窗口（默认行为）
```

### 3b. 新增 `_setup_tray()`

连接 `TrayIcon` 所有信号到对应槽，调用 `self._tray.show()`。

### 3c. 修改 `_update_connection_status()`

在现有三态更新逻辑之后追加（**只在状态发生变化时触发**）：

```python
if status != self._last_catia_status:
    self._last_catia_status = status
    if status == "connected" and not self._embed_manager.is_active:
        self._start_embed_silent()
    elif status == "disconnected" and self._embed_manager.is_active:
        self._stop_embed_silent()
```

### 3d. 新增 `_start_embed_silent()` / `_stop_embed_silent()`

与 `_toggle_embed` 逻辑相同，但**不更新状态栏**（避免每次 CATIA 重连都弹提示），只更新按钮文字和托盘菜单勾选状态。

### 3e. 修改 `closeEvent`

```python
def closeEvent(self, event):
    event.ignore()          # 拦截关闭，不退出
    self.hide()
    self._tray.showMessage(
        "CATIA Copilot",
        "程序仍在后台运行，双击托盘图标可重新打开",
        QSystemTrayIcon.MessageIcon.Information,
        2000,
    )
```

### 3f. 新增 `_quit_app()`

**唯一真正退出的入口**，由托盘菜单"退出"触发：

```python
def _quit_app(self):
    self._embed_manager.stop()
    self._save_settings()
    QApplication.quit()
```

### 3g. 新增 `_load_settings()` / `_save_settings()`

持久化项：

| key | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `window/visible` | bool | `False` | 上次关闭时主窗口是否可见 |
| `embed/auto_enabled` | bool | `True` | 检测到 CATIA 时是否自动激活嵌入面板 |

`autostart` 状态直接从注册表读取，不单独存 QSettings。

### 3h. 新增"设置"区域（在"视图"区域下方）

```
[✓] 开机自动启动
[✓] 检测到 CATIA 时自动嵌入面板
```

两个 `QCheckBox`，与托盘菜单对应项**双向同步**。

---

## 改动 4 — `main.py`

```python
# 新增：设置组织名，QSettings 需要
app.setOrganizationName("CATIACopilot")

# 去掉 window.show()
# 改为：MainWindow.__init__ 末尾根据 settings 决定是否显示
window = MainWindow()
# 不调用 window.show()
```

---

## 执行顺序

```
1. catia_copilot/utils.py      → 新增 get_autostart / set_autostart
2. catia_copilot/ui/tray_icon.py  → 新建，纯 UI，无业务依赖
3. catia_copilot/ui/main_window.py → 接入托盘、自动激活、持久化
4. main.py                     → 最后调整，依赖 main_window 行为稳定
```

---

## 涉及文件汇总

| 文件 | 改动性质 | 估计行数 |
|---|---|---|
| `catia_copilot/utils.py` | 修改（新增函数） | +30 行 |
| `catia_copilot/ui/tray_icon.py` | **新建** | ~100 行 |
| `catia_copilot/ui/main_window.py` | 修改 | +120 行 |
| `main.py` | 修改 | ±5 行 |
| `build.spec` | **不改** | — |
| `catia_copilot/ui/catia_embed.py` | **不改** | — |
