"""BOM 树控件：自定义委托与 QTreeWidget 封装。"""

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QWidget
from PySide6.QtCore import Qt, QSize, QModelIndex, QRect, QPersistentModelIndex
from PySide6.QtGui import QPainter, QPen, QColor

from catia_copilot.constants import BOM_READONLY_COLUMNS
from catia_copilot.ui.theme_manager import _STYLE_NAME, theme_manager
from catia_copilot.ui.ui_colors import get_colors
from catia_copilot.ui.ui_layout import L

# 自定义 UserRole 用于 QTreeWidgetItem：标记行为锁定（不可读/未找到）
_ITEM_LOCKED_ROLE: int = Qt.ItemDataRole.UserRole + 1


class _RowHeightDelegate(QStyledItemDelegate):
    """行高保证 + 列 0 溢出防护的基础委托。

    所有 BOM/质量属性树的专用委托均应继承此类，以共享以下行为：

    **行高**：QSS 的 ``QTreeWidget::item { min-height }`` 规则会触发样式引擎接管
    item 背景，导致 ``BackgroundRole`` 被 QSS 继承背景色覆盖。改用此委托的
    :meth:`sizeHint` 设置行高，完全绕开 QSS 对 item 背景的干扰，使 ``setBackground``
    能在任何状态下正常生效。

    **列 0 防溢出**：:meth:`paint` 对列 0 始终把画家裁剪到列宽范围内。
    Qt 传给委托的 ``option.rect`` 左边界已含缩进偏移，右边界恒等于列右缘减一；
    理论上文字不应溢出，但 windows11/windowsvista 风格下样式实现可能在
    ``option.rect`` 之外绘制，因此用 ``setClipRect`` 兜底。
    缩进量超出列宽时（``option.rect.left() >= col_right``）直接跳过绘制。
    """

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        if hint.height() < L.TABLE_ROW_HEIGHT:
            hint.setHeight(L.TABLE_ROW_HEIGHT)
        return hint

    def paint(self, painter: QPainter, option, index: QModelIndex | QPersistentModelIndex) -> None:  # type: ignore[override]
        """列 0：裁剪到列宽，防止缩进过深时内容溢出到相邻列。"""
        if index.column() == 0:
            tree = self.parent()
            assert isinstance(tree, QTreeWidget)
            col_right = tree.columnViewportPosition(0) + tree.columnWidth(0)
            if option.rect.left() >= col_right:
                # 缩进已超出列宽，无空间绘制
                return
            # option.rect.right() 恒等于 col_right - 1，理论上不会溢出；
            # 但部分风格实现会在 rect 外绘制，setClipRect 作为兜底保障。
            painter.save()
            painter.setClipRect(
                QRect(option.rect.left(), option.rect.top(),
                      col_right - option.rect.left(), option.rect.height())
            )
            super().paint(painter, option, index)
            painter.restore()
            return
        super().paint(painter, option, index)


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


class _BomTreeDelegate(_RowHeightDelegate):
    """BOM QTreeWidget 的逐列只读强制委托。

    继承 :class:`_RowHeightDelegate`，获得行高保证与列 0 防溢出能力。
    自身只负责 :meth:`createEditor`：对 :data:`~catia_copilot.constants.BOM_READONLY_COLUMNS`
    中的列，以及被标记为锁定的行（文件未找到/不可读）返回 ``None``，阻止编辑。
    """

    def __init__(self, cols_fn, tree: QTreeWidget) -> None:
        super().__init__(tree)
        self._cols_fn = cols_fn  # callable: () -> list[str]

    def createEditor(self, parent, option, index) -> QWidget | None:  # type: ignore[override]
        tree = self.parent()
        assert isinstance(tree, QTreeWidget)
        item = tree.itemFromIndex(index)
        if item is not None and item.data(0, _ITEM_LOCKED_ROLE):
            return None
        col_name = self._cols_fn()[index.column()]
        if col_name in BOM_READONLY_COLUMNS:
            return None
        return super().createEditor(parent, option, index)


class _BomTreeWidget(QTreeWidget):
    """BOM 用 QTreeWidget 封装。

    **为何自绘树状连接线**：Windows 11 / Windows Vista 风格在应用全局 QSS 时，
    原生的 ``PE_IndicatorBranch`` 竖线/横线会消失或与展开箭头错位。
    :meth:`drawBranches` 在 ``super()`` 画完原生箭头后，为这两种风格额外叠加
    1 px 点状虚线（其他风格自带连接线，不重复叠加）。

    **防溢出**：两处绘制均把画家裁剪到列 0 右边缘。Qt 传入的 branch ``rect``
    宽度 = ``indent * depth``，深度较大时可能超出列宽；不加裁剪则连接线/箭头
    会溢入相邻列。

    构造时自动安装 :class:`_RowHeightDelegate` 以保证行高，无需 QSS ``::item``
    规则。子类或外部代码可通过 :meth:`setItemDelegate` 替换为更专用的委托——
    替换后行高由新委托的 :meth:`sizeHint` 负责。
    行高由 ``L.TABLE_ROW_HEIGHT`` 统一控制（ui_layout.py）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 安装默认行高委托；setItemDelegate 会替换它，新委托需自行保证行高
        self.setItemDelegate(_RowHeightDelegate(self))

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex | QPersistentModelIndex) -> None:  # type: ignore[override]
        """绘制 Windows 注册表编辑器风格点状连接线。

        先调用 super() 让系统风格画展开/折叠箭头，再叠加虚线连接线。
        windows11/windowsvista 风格在有全局 QSS 时不自绘竖线/横线，需要手动补。
        其他风格（windows 经典、Fusion 等）自带连接线，直接交由 super() 处理。

        虚线采用 1px 实点 + 1px 间隔，通过 setDashOffset(rect.top() % 2)
        根据行的绝对 y 坐标对齐相位，确保相邻行的竖线点阵在视觉上连续。

        ``rect`` 由 Qt 按 ``indent * depth`` 计算，深度较大时宽度可能超出列宽；
        两处 ``setClipRect`` 均使用列 0 实际右边缘而非 ``rect.right()`` 作为上限。
        """
        col_right = self.columnViewportPosition(0) + self.columnWidth(0)
        clip_w    = max(0, col_right - rect.left())

        # 先让系统风格画原生箭头（PE_IndicatorBranch）
        painter.save()
        painter.setClipRect(QRect(rect.left(), rect.top(), clip_w, rect.height()))
        super().drawBranches(painter, rect, index)
        painter.restore()

        # 只有 windows11/windowsvista 风格需要手动补虚线；
        # 其他风格（windows 经典、Fusion 等）自带连接线，不叠加避免双线。
        try:
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
            line_color = get_colors(theme_manager.current_mode()).WIDGET_LINE_COLOR
        except Exception:
            line_color = QColor("#808080")

        pen = QPen(line_color, 1, Qt.PenStyle.SolidLine)
        pen.setDashPattern([1.0, 1.0])   # 1px 实点 + 1px 间隔
        pen.setDashOffset(rect.top() % 2)  # 根据行绝对 y 坐标对齐相位，确保竖线连续
        painter.save()
        painter.setClipRect(QRect(rect.left(), rect.top(), clip_w, rect.height()))
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
