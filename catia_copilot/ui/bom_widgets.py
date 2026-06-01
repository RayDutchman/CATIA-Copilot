"""BOM 树控件：自定义委托与 QTreeWidget 封装。"""

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QStyledItemDelegate
from PySide6.QtCore import Qt, QSize, QModelIndex, QRect
from PySide6.QtGui import QPainter, QPen, QColor

from catia_copilot.constants import BOM_READONLY_COLUMNS
from catia_copilot.ui.ui_layout import L

# 自定义 UserRole 用于 QTreeWidgetItem：标记行为锁定（不可读/未找到）
_ITEM_LOCKED_ROLE: int = Qt.ItemDataRole.UserRole + 1


class _RowHeightDelegate(QStyledItemDelegate):
    """仅负责固定行高的基础委托。

    QSS 的 ``QTreeWidget::item { min-height }`` 规则会触发样式引擎接管 item
    背景，导致 ``BackgroundRole`` 被 QSS 继承背景色覆盖。
    改用此 delegate 的 :meth:`sizeHint` 设置行高，完全绕开 QSS 对 item 背景
    的干扰，使 ``setBackground`` 能在任何状态下正常生效。
    """

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        if hint.height() < L.TABLE_ROW_HEIGHT:
            hint.setHeight(L.TABLE_ROW_HEIGHT)
        return hint


class _BomSortItem(QTreeWidgetItem):
    """QTreeWidgetItem，排序时对纯数字列执行数值比较，其余列保持字符串比较。

    Qt 内置的 QTreeWidgetItem.__lt__ 始终按文本字符串排序，导致 "#" 行号列和
    "数量" 列出现 "10" < "2" 的问题。重写 __lt__ 以先尝试浮点数转换，失败时
    回退到字符串比较，兼容文本列。
    """

    def __lt__(self, other: "QTreeWidgetItem") -> bool:  # type: ignore[override]
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        a = self.text(col)
        b = other.text(col)
        try:
            return float(a) < float(b)
        except (ValueError, TypeError):
            return a < b


class _BomTreeDelegate(QStyledItemDelegate):
    """BOM QTreeWidget 的逐列只读强制委托。

    QTreeWidgetItem 的 flags 是按行设置的；此委托对内部名称属于
    :data:`~catia_copilot.constants.BOM_READONLY_COLUMNS` 的列，
    以及被标记为锁定的行（文件未找到/不可读）从 :meth:`createEditor`
    返回 ``None``，从而阻止编辑。
    """

    def __init__(self, cols_fn, tree: QTreeWidget) -> None:
        super().__init__(tree)
        self._cols_fn = cols_fn  # callable: () -> list[str]

    def createEditor(self, parent, option, index):
        tree = self.parent()
        item = tree.itemFromIndex(index)
        if item is not None and item.data(0, _ITEM_LOCKED_ROLE):
            return None
        col_name = self._cols_fn()[index.column()]
        if col_name in BOM_READONLY_COLUMNS:
            return None
        return super().createEditor(parent, option, index)

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        if hint.height() < L.TABLE_ROW_HEIGHT:
            hint.setHeight(L.TABLE_ROW_HEIGHT)
        return hint


class _BomTreeWidget(QTreeWidget):
    """BOM 用 QTreeWidget 封装。

    树状连接线由 :meth:`drawBranches` 自绘（1px 虚线），不依赖 QSS branch 规则，
    避免 windows11 风格下 CSS border-left 与原生箭头错位的问题。
    展开/折叠箭头仍由 windows11 风格的 PE_IndicatorBranch 原生绘制。
    构造时自动安装 :class:`_RowHeightDelegate` 以保证行高，无需 QSS ``::item`` 规则。
    子类或外部代码可通过 :meth:`setItemDelegate` 替换为更专用的委托——替换后行高
    由新委托的 :meth:`sizeHint` 负责（见 :class:`_BomTreeDelegate`）。
    行高由 ``L.TABLE_ROW_HEIGHT`` 统一控制（ui_layout.py）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 安装默认行高委托；setItemDelegate 会替换它，新委托需自行保证行高
        self.setItemDelegate(_RowHeightDelegate(self))

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:
        """先画虚线连接线（底层），再让风格画箭头（顶层），避免线浮在箭头上。

        windows11/windowsvista 风格在有全局 QSS 时不自绘竖线/横线，需要手动补。
        其他风格（windows 经典、Fusion、macOS 等）自带连接线，直接交由 super() 处理。

        坐标系：rect 覆盖从 x=0 到当前层缩进终点的整个 branch 区域，
        宽度 = (depth+1) * indent，高度 = 行高。
        横线从竖线延伸到 rect.right()（文字左边缘），竖线在箭头左侧。
        """
        # 只有 windows11/windowsvista 风格需要手动补虚线；
        # 其他风格（windows 经典、Fusion 等）自带连接线，不叠加避免双线。
        # 用 theme_manager._STYLE_NAME 而非运行时查询 objectName()，
        # 因为 QWidget.style().objectName() 在未单独设置 style 时可能返回空。
        try:
            from catia_copilot.ui.theme_manager import _STYLE_NAME
            _need_overlay = _STYLE_NAME in ("windows11", "windowsvista")
        except Exception:
            _need_overlay = True   # 导入失败时保守地画线
        if not _need_overlay:
            super().drawBranches(painter, rect, index)
            return

        item = self.itemFromIndex(index)
        if item is None:
            super().drawBranches(painter, rect, index)
            return

        # 计算当前 item 的深度（root 的子节点 depth=1）
        depth = 0
        p = item.parent()
        while p is not None:
            depth += 1
            p = p.parent()

        if depth == 0:
            # root 节点本身不画连接线，只画箭头
            super().drawBranches(painter, rect, index)
            return

        indent = self.indentation()

        # 从 ui_colors 取当前主题的连接线颜色
        try:
            from catia_copilot.ui.theme_manager import theme_manager
            from catia_copilot.ui.ui_colors import get_colors
            line_color = get_colors(theme_manager.current_mode()).WIDGET_LINE_COLOR
        except Exception:
            line_color = QColor("#808080")

        pen = QPen(line_color, 1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([1, 1])   # 1px 实点 + 1px 间隔，与 Windows 注册表编辑器一致
        pen.setDashOffset(0)
        painter.save()
        painter.setPen(pen)

        row_h = rect.height()
        mid_y = rect.top() + row_h // 2

        # 竖线 x 坐标：箭头左侧，与箭头中心 (rect.right() - indent//2) 左移 indent//2
        # 即 rect.right() - indent，再左移 1px 视觉居中
        def layer_x(d: int) -> int:
            return rect.left() + d * indent - 1

        cur_x = layer_x(depth)

        # ── 当前层：竖线 + 横线 ──────────────────────────────────────────
        parent_item = item.parent()
        idx_in_parent = parent_item.indexOfChild(item) if parent_item else 0
        has_next_sibling = (
            parent_item is not None
            and idx_in_parent < parent_item.childCount() - 1
        )

        # 竖线：从顶部到行中央（连接上方兄弟/父节点）
        painter.drawLine(cur_x, rect.top(), cur_x, mid_y)
        # 竖线：从行中央到底部（如果还有后续兄弟）
        if has_next_sibling:
            painter.drawLine(cur_x, mid_y, cur_x, rect.bottom())
        # 横线：从竖线延伸到文字左边缘（rect.right()）
        painter.drawLine(cur_x, mid_y, rect.right(), mid_y)

        # ── 祖先层：只画竖线（该祖先还有后续兄弟时）────────────────────
        anc       = item.parent()
        anc_depth = depth - 1
        while anc is not None and anc.parent() is not None:
            anc_parent = anc.parent()
            anc_idx    = anc_parent.indexOfChild(anc)
            if anc_idx < anc_parent.childCount() - 1:
                # 该祖先还有后续兄弟，需要画贯穿本行的竖线
                anc_x = layer_x(anc_depth)
                painter.drawLine(anc_x, rect.top(), anc_x, rect.bottom())
            anc       = anc.parent()
            anc_depth -= 1

        painter.restore()

        # 最后让风格画箭头（顶层），覆盖在虚线上
        super().drawBranches(painter, rect, index)
