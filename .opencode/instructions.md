# CATIA-Copilot 项目 AI Agent 指引

本文件记录项目关键设计决策，供同项目不同会话的 AI Agent 参考，避免重复踩坑。

---

## 0. 运行环境（重要）

**本项目完全运行在 Windows 上，与 WSL 无关。**

- 运行时：Windows Python 3.13（`C:\Users\Chen Weibo\AppData\Local\Programs\Python\Python313\python.exe`）
- 路径类型：`WindowsPath`，形如 `D:\foo\bar\baz.CATPart`
- WSL 中编辑代码时看到的 `/mnt/d/...` 是映射路径，**不是运行时路径，不可传给任何运行时 API**
- **不要**对 Windows 路径调用 WSL 的 `Path.resolve()`，会得到错误的 `/mnt/d/...` 格式
- 文件路径比对用 `d.FullName == file_path` 精确匹配；扩展名过滤用 `.lower()` 大小写不敏感
- `docs.Open(file_path)` 直接传原始 Windows 路径字符串，不做转换
- `win32com` 已安装，可通过 `GetActiveObject('CATIA.Application')` 操作 CATIA COM 对象

在 WSL bash 中验证/探查 Windows 运行时状态时，用以下方式调用 Windows Python：

```bash
WIN_PYTHON="/mnt/c/Users/Chen Weibo/AppData/Local/Programs/Python/Python313/python.exe"
"$WIN_PYTHON" -c "import win32com.client; ..."
```

---

## 1. 主题系统（ThemeManager）

**文件**：`catia_copilot/ui/theme_manager.py`，`dark.qss` / `light.qss`

**架构**：qdarkstyle 基础层 + 项目 overlay 拼接后一次性 `setStyleSheet`：

```
qdarkstyle.load_stylesheet(qt_api="pyside6", palette=...)  ← 基础层（通用控件深/浅色）
                    +
        dark.qss / light.qss                               ← 项目 overlay（只写项目专属部分）
                    ↓
        QApplication.setStyleSheet(base + overlay)
```

`load_stylesheet()` 调用同时注册 Qt 资源（`:/qss_icons/...`），使 qdarkstyle 自带的树状线图片生效。

**约束**：
- overlay 只写项目特有 widget（标题栏、日志面板、treeCombo 等），**不重写** qdarkstyle 已有的通用规则
- 深色基调：`#19232D` 背景 / `#DFE1E2` 前景 / hover `#37414F` / selected `#346792`
- 浅色基调：`#FAFAFA` 背景 / `#19232D` 前景 / hover `#D2D5D8` / selected `#9FCBFF`
- 主题切换订阅：`from catia_copilot.ui.theme_manager import theme_signal; theme_signal.theme_changed.connect(slot)`

---

## 2. QTreeWidget 定制（BOM 树）

**文件**：`catia_copilot/ui/bom_widgets.py`

| 类 | 职责 |
|----|------|
| `_RowHeightDelegate` | 通过 `sizeHint()` 保证 24px 行高，不干涉背景绘制 |
| `_BomSortItem` | 数字列数值排序，避免 "10" < "2" |
| `_BomTreeDelegate` | 逐列只读控制 + 锁定行禁编辑，含 `sizeHint()` |
| `_BomTreeWidget` | 构造时安装 `_RowHeightDelegate`；替换委托后新委托需自带 `sizeHint()` |
| `_MassPropsDelegate`（`mass_props_dialog.py`）| 质量属性专用委托，含 `sizeHint()` |

**规则**：`setItemDelegate()` 替换默认委托时，新委托**必须**重写 `sizeHint()` 返回 ≥ 24px，否则行高退回 Qt 默认值（~17px）。

**为什么不用 `QTreeWidget::item { min-height }` QSS**：该规则会触发 Qt 样式引擎接管 item 背景绘制，导致 `setBackground()` 的 `BackgroundRole` 被覆盖，特殊行着色失效。

**为什么全部禁用 `setAlternatingRowColors(True)`**：`QTreeView:branch` 伪元素不支持 `:alternate`，开启后 branch 区域出现竖条色块，且奇偶行选中色不一致。所有 QTreeWidget（`bom_edit_dialog`、`mass_props_dialog`、`plm_workbench._preview_tree`、`find_deps_dialog`）均设为 `False`。

**树状连接线**：由 qdarkstyle 基础层处理，项目不自绘、不在 overlay 中覆盖 branch 规则。

**特殊行着色**：`QTreeWidgetItem.setBackground(col, QBrush(QColor(...)))`，hover/selected 状态下 qdarkstyle 高亮色覆盖自定义色（可接受）。

---

## 3. 列宽策略

所有 `QHeaderView` 统一用 **Interactive + 初始宽度 + `setStretchLastSection(True)`**：

```python
hdr.setSectionResizeMode(i, QHeaderView.Interactive)
hdr.resizeSection(i, initial_px)
hdr.setStretchLastSection(True)
```

不用 `ResizeToContents`（数据量大时性能差）或纯 `Stretch`（禁止用户拖拽）。

---

## 4. 其他约定

- `PLM_SYNC_MAX_NODES = 100`（`catia_copilot/constants.py`）
- "使用活动文档" checkbox 在 `_build_ui` **完成后**再 `setChecked(True)`，避免控件未就绪时触发 `toggled`
- `convert_dialog._toggle_file_section` 用 `setEnabled` 而非 `setVisible`，避免窗口尺寸骤变
- 宏文件列表：主窗口用单按钮 `"运行宏…"` + `QMenu` 弹出
