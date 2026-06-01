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

    树状连接线由 :meth:`drawBranches` 自绘（1px 点状虚线，相位对齐），
    不依赖 QSS branch 规则，避免 windows11 风格下 CSS border 与原生箭头错位。
    展开/折叠箭头仍由系统风格的 PE_IndicatorBranch 原生绘制。
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
        """绘制 Windows 注册表编辑器风格点状连接线。

        先调用 super() 让系统风格画展开/折叠箭头，再叠加虚线连接线。
        windows11/windowsvista 风格在有全局 QSS 时不自绘竖线/横线，需要手动补。
        其他风格（windows 经典、Fusion 等）自带连接线，直接交由 super() 处理。

        虚线采用 1px 实点 + 1px 间隔，通过 setDashOffset(rect.top() % 2)
        根据行的绝对 y 坐标对齐相位，确保相邻行的竖线点阵在视觉上连续。
        """
        # 先让系统风格画原生箭头（PE_IndicatorBranch）
        super().drawBranches(painter, rect, index)

        # 只有 windows11/windowsvista 风格需要手动补虚线；
        # 其他风格（windows 经典、Fusion 等）自带连接线，不叠加避免双线。
        try:
            from catia_copilot.ui.theme_manager import _STYLE_NAME
            _need_overlay = _STYLE_NAME in ("windows11", "windowsvista")
        except Exception:
            _need_overlay = True   # 导入失败时保守地画线
        if not _need_overlay:
            return

        indent = self.indentation()
        model  = self.model()

        # 从当前节点向上遍历到根节点，记录各层祖先是否还有下一个兄弟节点
        has_next: list[bool] = []
        tmp = index
        while True:
            par = tmp.parent()
            cnt = model.rowCount(par) if par.isValid() else model.rowCount()
            has_next.append(tmp.row() < cnt - 1)
            if not par.isValid():
                break
            tmp = par
        has_next.reverse()  # has_next[0] 对应最顶层祖先，has_next[-1] 对应当前节点

        depth = len(has_next) - 1
        if depth == 0:
            # 顶层节点不画连接线
            return

        mid_y = (rect.top() + rect.bottom()) // 2

        # 从 ui_colors 取当前主题的连接线颜色
        try:
            from catia_copilot.ui.theme_manager import theme_manager
            from catia_copilot.ui.ui_colors import get_colors
            line_color = get_colors(theme_manager.current_mode()).WIDGET_LINE_COLOR
        except Exception:
            line_color = QColor("#808080")

        pen = QPen(line_color, 1, Qt.PenStyle.SolidLine)
        pen.setDashPattern([1.0, 1.0])   # 1px 实点 + 1px 间隔
        pen.setDashOffset(rect.top() % 2)  # 根据行绝对 y 坐标对齐相位，确保竖线连续
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(pen)

        # 祖先层：如果该层祖先还有后续兄弟，画贯穿整行的竖线
        for d in range(depth - 1):
            if has_next[d + 1]:
                x = rect.left() + d * indent + indent // 2
                painter.drawLine(x, rect.top(), x, rect.bottom())

        # 当前层：T 型（有后续兄弟）或 L 型（最后一个子节点）
        x     = rect.left() + (depth - 1) * indent + indent // 2
        x_end = rect.left() + depth * indent  # 横线终点 = 当前层内容区左边缘
        if has_next[-1]:
            # T 型：贯穿整行的竖线
            painter.drawLine(x, rect.top(), x, rect.bottom())
        else:
            # L 型：只画上半段竖线（转角）
            painter.drawLine(x, rect.top(), x, mid_y)
        # 横线：从竖线延伸到内容区左边缘
        painter.drawLine(x, mid_y, x_end, mid_y)

        painter.restore()
