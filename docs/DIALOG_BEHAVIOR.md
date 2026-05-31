# 对话框窗口行为说明

本文件记录 CATIA Copilot 所有功能对话框的窗口行为设计、已知问题和演进历史。

---

## 1. 当前行为

### 对话框管理入口

所有非模态对话框通过 `MainWindow._show_dialog(attr, factory)` 统一管理：

- `setParent(None, Window | ...)` — 独立顶级窗口，在任务栏有独立条目
- `WA_DeleteOnClose` — 关闭时销毁，`destroyed` 信号清理引用
- `setParent` 之后立即 `restoreGeometry`（`setParent` 会重建原生窗口并重置位置）

### 置顶行为（可切换）

对话框是否置顶由用户在 **≡ → 视图 → 「对话框置顶」** Toggle 按钮控制，偏好持久化到 `QSettings("CATIACopilot", "MainWindow", "dlg_topmost")`，默认开启。

| 状态 | 行为 |
|------|------|
| 开启（默认） | 对话框浮于所有窗口之上（`WS_EX_TOPMOST`） |
| 关闭 | 对话框与普通窗口平级，CATIA 弹窗可正常显示在前台 |

切换时通过 Win32 `SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST)` 直接修改 `WS_EX_TOPMOST` 标志，无需 `show()` 重建窗口，无闪烁，立即对所有已开对话框生效。

### 跟随 CATIA 最小化/还原

500ms 定时器检测 `IsIconic(catia_hwnd)`：

- CATIA 最小化时：`hide()` 前调用 `saveGeometry()` 保存运行时几何，记录到 `_hidden_dialogs`
- CATIA 还原时：`show()` 后从 `_dialog_geometries` 恢复几何（包含用户运行时调整的位置/尺寸）

主窗口完全独立，不跟随 CATIA 最小化/还原。

### 几何持久化

- `closeEvent` 写入 `_settings.saveGeometry()`（持久化到磁盘）
- `_show_dialog` 在 `setParent(None)` 之后重新 `restoreGeometry`（防止 `setParent` 重建原生窗口后位置丢失）
- CATIA 最小化/还原时用 `_dialog_geometries` 字典保存/恢复运行时几何（不写磁盘）

---

## 2. 跨线程通信

Win32 消息循环在后台线程运行，不能直接调用 Qt 主线程的 UI 方法。通过 `Signal` 机制安全派发：

```python
# 后台线程（Win32 消息循环）
def _open_bom_dialog_from_embed(self) -> None:
    view_hwnd = self._embed_manager._current_view_hwnd or 0
    self._embed_action_signal.emit("bom_edit", view_hwnd)  # 线程安全

# 主线程（Qt 事件循环）
@Slot(str, int)
def _handle_embed_action(self, action: str, view_hwnd: int) -> None:
    self._do_open_bom_dialog()
```

**COM 调用必须在 Qt 主线程（STA）执行**，不能在 Win32 后台线程直接调用 CATIA COM 对象。

---

## 3. 已知问题

### 对话框置顶时 CATIA 弹窗被遮挡（死锁）

**现象**：对话框置顶开启时，CATIA 弹出的确认弹窗（如「激活其他文档保存操作，要继续吗？」）被我们的对话框遮挡，用户无法点击，程序陷入死锁。

**根本原因**：我们通过 COM 调用 CATIA 的 `doc.SaveAs()` 时，CATIA 内部弹出确认对话框。COM 调用是同步阻塞的——我们的 Qt 主线程在等待 COM 调用返回，而 CATIA 的 COM 调用在等待用户响应它的弹窗。两个 `WS_EX_TOPMOST` 窗口争抢 Z-order，CATIA 弹窗被压在下面，用户无法点击，形成死锁。

**触发路径**：BOM 工作台 → 另存为 → `rename_document()` → `target_doc.SaveAs(new_fp)`（同步 COM 调用）→ CATIA 弹出确认弹窗

**用户自己在 CATIA 里操作不会触发**：用户直接在 CATIA 里操作时，CATIA 的弹窗是在 CATIA 的 UI 线程上弹出的，不涉及 COM 调用，我们的线程没有在等待任何东西，所以不阻塞。

**当前缓解方案**：用户可以关闭「对话框置顶」开关，此时 CATIA 弹窗可以正常显示在前台。

**根本解决方案**（未实施）：将 `rename_document()` 等同步 COM 调用移到后台线程执行（需要 `pythoncom.CoInitialize()` 初始化 COM STA），Qt 主线程保持响应。改动量较大，暂时接受现状。

---

## 4. 演进历史

### 尝试 1：对话框绑定到 MDI 子窗口

- **问题**：对话框被自动最小化（Windows 认为切换到了另一个应用）；实现复杂，需要维护 `_view_dialogs` 映射
- **放弃**：用户体验差，实现复杂

### 尝试 2：对话框作为 MainWindow 的子窗口 + `WindowStaysOnTopHint`

- **问题**：对话框始终浮在 CATIA 之前；主窗口隐藏时对话框也隐藏
- **放弃**：用户体验差

### 尝试 3：Win32 Owner 机制（`SetWindowLongPtrW(GWLP_HWNDPARENT)`）

- **目标**：对话框只浮于 CATIA 之上，不浮于其他软件之上
- **问题**：Win32 文档明确规定不能对已创建窗口用此方法修改 Owner，在多对话框场景下必然崩溃
- **放弃**：技术方案不可行

### 当前方案：`WindowStaysOnTopHint` + 用户可切换开关

- 默认置顶，用户可在 ≡ 页关闭
- 切换时用 `SetWindowPos` 直接修改 `WS_EX_TOPMOST`，无闪烁
- 偏好持久化

---

**文档版本**：1.1  
**最后更新**：2026-05-31  
**作者**：CATIA Copilot 开发团队
