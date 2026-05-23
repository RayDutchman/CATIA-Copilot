"""BOM 树控件：自定义委托与 QTreeWidget 封装。"""

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QStyledItemDelegate
from PySide6.QtCore import Qt, QSize, QEvent, QObject

from catia_copilot.constants import BOM_READONLY_COLUMNS

# 自定义 UserRole 用于 QTreeWidgetItem：标记行为锁定（不可读/未找到）
_ITEM_LOCKED_ROLE: int = Qt.ItemDataRole.UserRole + 1

_ROW_HEIGHT = 24  # 统一行高（像素），替代 QTreeWidget::item { min-height } QSS 规则


class _RowHeightDelegate(QStyledItemDelegate):
    """仅负责固定行高的基础委托。

    QSS 的 ``QTreeWidget::item { min-height }`` 规则会触发样式引擎接管 item
    背景，导致 ``BackgroundRole`` 被 QSS 继承背景色覆盖。
    改用此 delegate 的 :meth:`sizeHint` 设置行高，完全绕开 QSS 对 item 背景
    的干扰，使 ``setBackground`` 能在任何状态下正常生效。
    """

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        if hint.height() < _ROW_HEIGHT:
            hint.setHeight(_ROW_HEIGHT)
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
        if hint.height() < _ROW_HEIGHT:
            hint.setHeight(_ROW_HEIGHT)
        return hint


class _ViewportLeaveFilter(QObject):
    """拦截 viewport 的 Leave 事件：若鼠标仍在 viewport 范围内（仅进入子 widget），
    则吃掉该事件，避免 QAbstractItemView 清除已选行的 active 高亮。
    """

    def __init__(self, viewport: QObject) -> None:
        super().__init__(viewport)
        self._viewport = viewport

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._viewport and event.type() == QEvent.Type.Leave:
            # 判断鼠标全局位置是否仍在 viewport 矩形内
            from PySide6.QtGui import QCursor
            vp = self._viewport
            if vp.rect().contains(vp.mapFromGlobal(QCursor.pos())):
                return True  # 吃掉事件：鼠标只是进入了子控件，未真正离开
        return False


class _BomTreeWidget(QTreeWidget):
    """BOM 用 QTreeWidget 封装。

    树状连接线、branch 区域背景、hover/selected 效果完全交由 qdarkstyle QSS 处理。
    构造时自动安装 :class:`_RowHeightDelegate` 以保证行高，无需 QSS ``::item`` 规则。
    子类或外部代码可通过 :meth:`setItemDelegate` 替换为更专用的委托——替换后行高
    由新委托的 :meth:`sizeHint` 负责（见 :class:`_BomTreeDelegate`）。

    通过 :class:`_ViewportLeaveFilter` 拦截 viewport Leave 事件：鼠标移入嵌入的
    QComboBox 等子控件时，viewport 会收到 Leave，导致已选行 active 高亮消失。
    过滤器检测鼠标是否仍在 viewport 范围内，若是则吃掉 Leave 事件。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 安装默认行高委托；setItemDelegate 会替换它，新委托需自行保证行高
        self.setItemDelegate(_RowHeightDelegate(self))
        # 安装 viewport Leave 过滤器，防止子控件 hover 时已选行高亮消失
        vp = self.viewport()
        self._leave_filter = _ViewportLeaveFilter(vp)
        vp.installEventFilter(self._leave_filter)
