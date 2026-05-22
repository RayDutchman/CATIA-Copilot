"""
查找依赖项对话框（重构版）。

提供：
- FindDependenciesDialog – 对指定 CATIA 文件执行双向依赖查找：
    • "依赖谁"：通过 COM 打开文件，收集所有被引用文档（find_dependencies）
    • "被谁依赖"（2A 图纸→零件）：对 CATDrawing 文件，用多策略查找对应零件
    • "被谁依赖"（2B 零件→图纸）：对 CATPart/CATProduct 文件，用多策略查找对应图纸

用户可通过 checkbox 启用/禁用各搜索策略。
结果列表中双击或点击"打开"按钮可在 CATIA 中打开对应文件。
"""

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QApplication,
    QGroupBox, QCheckBox, QSplitter, QWidget, QScrollArea, QDialogButtonBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QColor

from catia_copilot.catia.dependencies import (
    find_dependencies,
    find_part_for_drawing,
    find_drawing_for_part,
)
from catia_copilot.constants import (
    DRAWING_SEARCH_STRATEGIES,
    DRAWING_SEARCH_MAX_LEVELS,
    PART_TO_DRAWING_STRATEGIES,
    PART_TO_DRAWING_MAX_LEVELS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 策略的中文显示名（用于 checkbox 标签 和 结果标注）
# ---------------------------------------------------------------------------

_DRAWING_STRATEGY_LABELS: dict[str, str] = {
    "pn_param_open_docs":     "已打开文档匹配 PartNumber 参数",
    "pn_param_scan_dirs":     "目录扫描，文件名匹配 PartNumber 参数",
    "same_name_scan_dirs":    "目录扫描，同名零件文件",
    "strip_prefix_scan_dirs": "目录扫描，去前缀后同名零件文件",
    "doc_file_links":         "COM 视图链接（兜底，图纸须已打开）",
}

_PART_STRATEGY_LABELS: dict[str, str] = {
    "pn_param_open_drws":     "已打开图纸匹配 PartNumber 参数",
    "pn_param_scan_drws":     "目录扫描，文件名匹配 PartNumber",
    "same_name_scan_dirs":    "目录扫描，同名图纸文件",
    "strip_prefix_scan_dirs": "目录扫描，去前缀后同名图纸文件",
}

# ---------------------------------------------------------------------------
# 结果条目颜色
# ---------------------------------------------------------------------------
_COLOR_COM_LINK  = QColor("#1565C0")   # 深蓝：COM 链接（find_dependencies）
_COLOR_DRW_STRAT = QColor("#2E7D32")   # 深绿：2A 图纸→零件策略
_COLOR_PART_STRAT= QColor("#6A1B9A")   # 深紫：2B 零件→图纸策略
_COLOR_NONE      = QColor("#555555")   # 灰色：无颜色区分时


class FindDependenciesDialog(QDialog):
    """双向依赖查找对话框。

    • 对 CATDrawing：执行 find_dependencies（依赖谁）+ find_part_for_drawing（被谁依赖-2A）
    • 对 CATPart / CATProduct：执行 find_dependencies（依赖谁）+ find_drawing_for_part（被谁依赖-2B）
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查找所有依赖项目")
        self.setMinimumSize(700, 560)
        self.resize(820, 640)

        self._settings = QSettings("CATIACompanion", "FindDependenciesDialog")

        # 各策略的 checkbox 字典，在 _rebuild_strategy_panel 中填充
        self._drawing_cbs: dict[str, QCheckBox] = {}  # 2A 策略
        self._part_cbs:    dict[str, QCheckBox] = {}  # 2B 策略

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 目标文件行 ──────────────────────────────────────────────────────
        file_group = QGroupBox("目标文件")
        file_vlayout = QVBoxLayout(file_group)
        file_vlayout.setSpacing(6)

        self._file_type_label = QLabel()
        self._file_type_label.setStyleSheet("color: gray; font-size: 11px;")

        target_row = QHBoxLayout()
        self._target_edit = QLineEdit()
        self._target_edit.setReadOnly(True)
        self._target_edit.setPlaceholderText("选择目标 CATIA 文件（CATPart / CATProduct / CATDrawing）…")
        last = self._settings.value("last_target", "")
        self._target_edit.setText(last)
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(64)
        browse_btn.clicked.connect(self._browse_target)
        target_row.addWidget(self._target_edit)
        target_row.addWidget(browse_btn)

        file_vlayout.addLayout(target_row)
        file_vlayout.addWidget(self._file_type_label)
        main_layout.addWidget(file_group)

        # ── 水平 splitter：左=策略面板，右=结果列表 ──────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左侧：策略选项
        self._strategy_scroll = QScrollArea()
        self._strategy_scroll.setWidgetResizable(True)
        self._strategy_scroll.setMinimumWidth(230)
        self._strategy_scroll.setMaximumWidth(320)
        splitter.addWidget(self._strategy_scroll)

        # 右侧：结果列表
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        result_label = QLabel("搜索结果（双击打开文件）：")
        right_layout.addWidget(result_label)

        self._result_list = QListWidget()
        self._result_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._result_list.itemDoubleClicked.connect(self._open_selected)
        right_layout.addWidget(self._result_list)

        # 结果区底部按钮行
        result_btn_row = QHBoxLayout()
        self._open_btn = QPushButton("打开选中文件")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_selected)
        copy_btn = QPushButton("复制路径")
        copy_btn.clicked.connect(self._copy_selected_path)
        copy_all_btn = QPushButton("复制全部")
        copy_all_btn.clicked.connect(self._copy_all_paths)
        result_btn_row.addWidget(self._open_btn)
        result_btn_row.addWidget(copy_btn)
        result_btn_row.addWidget(copy_all_btn)
        result_btn_row.addStretch()
        right_layout.addLayout(result_btn_row)

        splitter.addWidget(right_widget)
        splitter.setSizes([260, 540])
        main_layout.addWidget(splitter, 1)

        # ── 底部按钮行 ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._search_btn = QPushButton("开始搜索")
        self._search_btn.setDefault(True)
        self._search_btn.clicked.connect(self._start_search)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._search_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        main_layout.addLayout(btn_row)

        # 初始化策略面板 & 文件类型标签
        self._result_list.itemSelectionChanged.connect(
            lambda: self._open_btn.setEnabled(bool(self._result_list.selectedItems()))
        )
        self._rebuild_strategy_panel()
        if last:
            self._update_file_type_label(last)

    # -----------------------------------------------------------------------
    # 目标文件选择
    # -----------------------------------------------------------------------

    def _browse_target(self) -> None:
        last      = self._settings.value("last_target", "")
        start_dir = str(Path(last).parent) if last else ""
        file, _   = QFileDialog.getOpenFileName(
            self, "选择目标 CATIA 文件", start_dir,
            "CATIA 文件 (*.CATPart *.CATProduct *.CATDrawing);;所有文件 (*)",
        )
        if file:
            self._target_edit.setText(file)
            self._settings.setValue("last_target", file)
            self._update_file_type_label(file)
            self._rebuild_strategy_panel()
            self._result_list.clear()
            self._open_btn.setEnabled(False)

    def _update_file_type_label(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        if ext == ".catdrawing":
            text = '文件类型：CATDrawing（将查找"依赖谁" + "图纸对应零件"）'
        elif ext in (".catpart", ".catproduct"):
            text = '文件类型：CATPart / CATProduct（将查找"依赖谁" + "零件对应图纸"）'
        else:
            text = "文件类型：未知"
        self._file_type_label.setText(text)

    # -----------------------------------------------------------------------
    # 策略面板（根据文件类型动态显示 2A 或 2B 策略）
    # -----------------------------------------------------------------------

    def _rebuild_strategy_panel(self) -> None:
        """根据当前目标文件扩展名重建左侧策略 checkbox 面板。"""
        target = self._target_edit.text().strip()
        ext    = Path(target).suffix.lower() if target else ""

        container = QWidget()
        vlayout   = QVBoxLayout(container)
        vlayout.setSpacing(6)
        vlayout.setContentsMargins(8, 8, 8, 8)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 层面 1：find_dependencies（始终显示，不可关闭）
        dep_group = QGroupBox("依赖谁（COM 文档链接）")
        dep_layout = QVBoxLayout(dep_group)
        dep_layout.setSpacing(2)
        note = QLabel("通过 CATIA COM 打开文件，\n收集所有被引用文档。\n（固定启用，无法禁用）")
        note.setStyleSheet("color: #1565C0; font-size: 11px;")
        note.setWordWrap(True)
        dep_layout.addWidget(note)
        vlayout.addWidget(dep_group)

        self._drawing_cbs.clear()
        self._part_cbs.clear()

        if ext == ".catdrawing":
            # 2A：图纸 → 零件
            strat_group = QGroupBox("被谁依赖 2A\n（图纸 → 零件/产品）")
            strat_layout = QVBoxLayout(strat_group)
            strat_layout.setSpacing(3)
            for key in DRAWING_SEARCH_STRATEGIES:
                label = _DRAWING_STRATEGY_LABELS.get(key, key)
                cb    = QCheckBox(label)
                cb.setChecked(self._load_cb_state(f"drw_{key}", True))
                cb.stateChanged.connect(lambda _, k=key: self._save_cb_state(f"drw_{k}", self._drawing_cbs[k].isChecked()))
                self._drawing_cbs[key] = cb
                strat_layout.addWidget(cb)
            vlayout.addWidget(strat_group)

        elif ext in (".catpart", ".catproduct"):
            # 2B：零件 → 图纸
            strat_group = QGroupBox("被谁依赖 2B\n（零件/产品 → 图纸）")
            strat_layout = QVBoxLayout(strat_group)
            strat_layout.setSpacing(3)
            for key in PART_TO_DRAWING_STRATEGIES:
                label = _PART_STRATEGY_LABELS.get(key, key)
                cb    = QCheckBox(label)
                cb.setChecked(self._load_cb_state(f"part_{key}", True))
                cb.stateChanged.connect(lambda _, k=key: self._save_cb_state(f"part_{k}", self._part_cbs[k].isChecked()))
                self._part_cbs[key] = cb
                strat_layout.addWidget(cb)
            vlayout.addWidget(strat_group)

        else:
            hint = QLabel("请先选择目标文件，\n策略选项将根据文件类型自动显示。")
            hint.setStyleSheet("color: gray; font-size: 11px;")
            hint.setWordWrap(True)
            vlayout.addWidget(hint)

        vlayout.addStretch()
        self._strategy_scroll.setWidget(container)

    def _load_cb_state(self, key: str, default: bool) -> bool:
        val = self._settings.value(f"cb_{key}", None)
        if val is None:
            return default
        # QSettings 可能返回字符串 "true"/"false" 或 bool
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    def _save_cb_state(self, key: str, checked: bool) -> None:
        self._settings.setValue(f"cb_{key}", checked)

    # -----------------------------------------------------------------------
    # 搜索
    # -----------------------------------------------------------------------

    def _start_search(self) -> None:
        target = self._target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "未选择目标文件", "请先选择一个目标 CATIA 文件。")
            return
        if not Path(target).exists():
            QMessageBox.warning(self, "文件不存在", f"目标文件不存在：\n{target}")
            return

        self._search_btn.setEnabled(False)
        self._result_list.clear()
        self._open_btn.setEnabled(False)
        self._add_info_item("正在搜索，请稍候…")
        QApplication.processEvents()

        ext = Path(target).suffix.lower()
        errors: list[str] = []

        # ── 层面 1：find_dependencies ──────────────────────────────────────
        dep_results: list[str] = []
        try:
            def _prog(msg: str) -> None:
                self._result_list.clear()
                self._add_info_item(msg)
                QApplication.processEvents()
            dep_results = find_dependencies(target, progress_callback=_prog)
        except Exception as e:
            logger.warning(f"find_dependencies failed: {e}")
            errors.append(f"依赖谁（COM 链接）失败：{e}")

        # ── 层面 2A 或 2B ──────────────────────────────────────────────────
        reverse_results: list[tuple[str, str]] = []  # (strategy_key, path)

        if ext == ".catdrawing":
            enabled = [k for k, cb in self._drawing_cbs.items() if cb.isChecked()]
            seen: set[str] = set()
            for strategy in enabled:
                try:
                    hits = find_part_for_drawing(
                        target,
                        strategies=[strategy],
                        max_parent_levels=DRAWING_SEARCH_MAX_LEVELS,
                    )
                    for h in hits:
                        if h not in seen:
                            seen.add(h)
                            reverse_results.append((strategy, h))
                except Exception as e:
                    logger.warning(f"find_part_for_drawing [{strategy}] failed: {e}")
                    errors.append(f"策略 [{_DRAWING_STRATEGY_LABELS.get(strategy, strategy)}] 失败：{e}")

        elif ext in (".catpart", ".catproduct"):
            enabled = [k for k, cb in self._part_cbs.items() if cb.isChecked()]
            seen = set()
            for strategy in enabled:
                try:
                    hits = find_drawing_for_part(
                        target,
                        strategies=[strategy],
                        max_parent_levels=PART_TO_DRAWING_MAX_LEVELS,
                    )
                    for h in hits:
                        if h not in seen:
                            seen.add(h)
                            reverse_results.append((strategy, h))
                except Exception as e:
                    logger.warning(f"find_drawing_for_part [{strategy}] failed: {e}")
                    errors.append(f"策略 [{_PART_STRATEGY_LABELS.get(strategy, strategy)}] 失败：{e}")

        # ── 填充结果列表 ───────────────────────────────────────────────────
        self._result_list.clear()

        total = len(dep_results) + len(reverse_results)

        if dep_results:
            self._add_section_header(f"── 依赖谁（COM 文档链接）共 {len(dep_results)} 个 ──")
            for path in dep_results:
                self._add_result_item("[COM链接]", path, _COLOR_COM_LINK)

        if reverse_results:
            if ext == ".catdrawing":
                section = f"── 图纸对应零件/产品 共 {len(reverse_results)} 个 ──"
            else:
                section = f"── 零件/产品对应图纸 共 {len(reverse_results)} 个 ──"
            self._add_section_header(section)
            for strategy_key, path in reverse_results:
                if ext == ".catdrawing":
                    label = _DRAWING_STRATEGY_LABELS.get(strategy_key, strategy_key)
                    color = _COLOR_DRW_STRAT
                else:
                    label = _PART_STRATEGY_LABELS.get(strategy_key, strategy_key)
                    color = _COLOR_PART_STRAT
                self._add_result_item(f"[{label}]", path, color)

        if errors:
            self._add_section_header("── 搜索期间遇到以下错误 ──")
            for err in errors:
                self._add_info_item(f"⚠ {err}", color=QColor("#B71C1C"))

        if total == 0 and not errors:
            self._add_info_item("未找到任何依赖项目。", color=QColor("#555555"))
        elif total > 0:
            self._add_info_item(f"搜索完成，共找到 {total} 个结果。", color=QColor("#2E7D32"))

        self._search_btn.setEnabled(True)

    # -----------------------------------------------------------------------
    # 结果列表辅助
    # -----------------------------------------------------------------------

    def _add_section_header(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # 不可选
        item.setForeground(QColor("#424242"))
        font = item.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() - 1)
        item.setFont(font)
        self._result_list.addItem(item)

    def _add_result_item(self, tag: str, path: str, color: QColor) -> None:
        display = f"{tag}  {path}"
        item    = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, path)   # 存储原始路径
        item.setForeground(color)
        item.setToolTip(path)
        self._result_list.addItem(item)

    def _add_info_item(self, text: str, color: QColor | None = None) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        if color:
            item.setForeground(color)
        self._result_list.addItem(item)

    # -----------------------------------------------------------------------
    # 打开文件
    # -----------------------------------------------------------------------

    def _open_selected(self, _item: QListWidgetItem | None = None) -> None:
        """打开列表中当前选中的文件（通过父窗口的 _open_catia_file）。"""
        items = self._result_list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # 调用父窗口（main_window）的 _open_catia_file 方法
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_catia_file"):
            try:
                parent._open_catia_file(path)
            except Exception as e:
                QMessageBox.critical(self, "打开失败", f"无法在 CATIA 中打开文件：\n{path}\n\n{e}")
        else:
            QMessageBox.information(self, "文件路径", path)

    # -----------------------------------------------------------------------
    # 复制
    # -----------------------------------------------------------------------

    def _copy_selected_path(self) -> None:
        items = self._result_list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if path:
            QApplication.clipboard().setText(path)

    def _copy_all_paths(self) -> None:
        paths: list[str] = []
        for i in range(self._result_list.count()):
            item = self._result_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            if path:
                paths.append(path)
        if paths:
            QApplication.clipboard().setText("\n".join(paths))


