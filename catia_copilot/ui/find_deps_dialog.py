"""
查找依赖项对话框。

提供：
- FindDependenciesDialog – 对指定 CATIA 文件执行依赖查找，分三组策略：

  【正向查询】顺着引用关系走：
    • find_dependencies： COM 打开目标文件， CATIA 自动级联加载所有被引用子文档
    • doc_file_links（仅 CATDrawing）：读图纸生成式视图链接，直接取被引用的零件/产品

  【反向查询】逆着引用关系溯源：
    • find_reverse_dependencies：遍历已打开的 CATProduct/CATDrawing ，
      找出哪些文档引用了目标文件（CATPart 外部引用暂不支持）

  【启发式补充】 COM 链接断开时的文件名匹配策略：
    • pn_param_open_docs/drws：在已打开文档中匹配 PartNumber 参数
    • pn_param_scan：在向上 N 级目录中按 PartNumber 参数扫描文件名
    • same_name_scan_dirs：按同名文件扫描
    • strip_prefix_scan_dirs：去前缀后按同名文件扫描
"""

import logging
import ctypes
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QApplication,
    QGroupBox, QCheckBox, QSplitter, QWidget,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView,
    QMenu, QWidgetAction,
)
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QColor, QPixmap, QFont, QBrush

from catia_copilot.catia.dependencies import (
    find_dependencies,
    find_reverse_dependencies,
    find_part_for_drawing,
    find_drawing_for_part,
)
from catia_copilot.constants import (
    DRAWING_SEARCH_STRATEGIES,
    SEARCH_MAX_LEVELS,
    PART_TO_DRAWING_STRATEGIES,
    SEARCH_MAX_LEVELS,
    BOM_THUMBNAIL_MAX_SIZE,
)
from catia_copilot.utils import read_catia_thumbnail
from catia_copilot.ui.ui_colors import get_colors
from catia_copilot.ui.theme_manager import theme_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 策略键定义
# ---------------------------------------------------------------------------

# 可执行策略搜索的文件类型
_EXT_DRAWING = ".catdrawing"
_EXTS_PART   = (".catpart", ".catproduct")

# ── 启发式补充策略（2A：图纸→零件，2B：零件/产品→图纸）────────────────────
# 后端键 → 界面显示名
_HEURISTIC_LABELS: dict[str, str] = {
    "pn_param_open_docs":     "匹配 PartNumber（已打开文档）",
    "pn_param_open_drws":     "匹配 PartNumber（已打开图纸）",
    "pn_param_scan_dirs":     "匹配 PartNumber（目录扫描→零件）",
    "pn_param_scan_drws":     "匹配 PartNumber（目录扫描→图纸）",
    "same_name_scan_dirs":    "目录扫描，同名文件",
    "strip_prefix_scan_dirs": "目录扫描，去前缀后同名文件",
}

_HEURISTIC_HINTS: dict[str, str] = {
    "pn_param_open_docs":
        "遍历已打开的零件/产品文档，找 PartNumber 参数等于图纸 PartNumber 的文件\n"
        "（仅对 CATDrawing 目标有效）",
    "pn_param_open_drws":
        "遍历已打开的 CATDrawing ，找 PartNumber 参数等于零件 PartNumber 的图纸\n"
        "（仅对 CATPart/CATProduct 目标有效）",
    "pn_param_scan_dirs":
        "向上扫描目录，找文件名等于图纸 PartNumber 参数的零件文件\n"
        "（仅对 CATDrawing 目标有效）",
    "pn_param_scan_drws":
        "向上扫描目录，找文件名等于零件 PartNumber 参数的图纸文件\n"
        "（仅对 CATPart/CATProduct 目标有效）",
    "same_name_scan_dirs":
        "向上扫描目录，找与目标文件同名的对应文件（两个方向均有效）",
    "strip_prefix_scan_dirs":
        "向上扫描目录，去掉「前缀_」或「前缀-」后再与目标文件名比较（两个方向均有效）",
}

# 启发式策略中属于 2A（图纸→零件）的后端键集合
_HEURISTIC_2A_KEYS: set[str] = {
    "pn_param_open_docs", "pn_param_scan_dirs",
    "same_name_scan_dirs", "strip_prefix_scan_dirs",
}
# 启发式策略中属于 2B（零件/产品→图纸）的后端键集合
_HEURISTIC_2B_KEYS: set[str] = {
    "pn_param_open_drws", "pn_param_scan_drws",
    "same_name_scan_dirs", "strip_prefix_scan_dirs",
}

# 表格列定义
_COL_TYPE = 0   # 来源/类型
_COL_NAME = 1   # 文件名
_COL_PATH = 2   # 完整路径
_COL_COUNT = 3

# 节标题行的 UserRole 标记（区别于真实结果行）
_ROLE_PATH    = Qt.ItemDataRole.UserRole        # 存储原始路径字符串
_ROLE_IS_HDR  = Qt.ItemDataRole.UserRole + 1   # 是否为节标题行（bool）


class FindDependenciesDialog(QDialog):
    """双向依赖查找对话框。

    • CATDrawing ：引用的文档（COM）+ 被引用零件/产品（2A）
    • CATPart/CATProduct ：引用的文档（COM）+ 被引用图纸（2B）
    • 其他格式：仅引用的文档（COM）
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查找指向的文档")
        self.setMinimumSize(750, 600)
        self.resize(900, 650)

        self._settings = QSettings("CATIACompanion", "FindDependenciesDialog")

        # 恢复窗口几何
        saved_geom = self._settings.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        # 各策略的 checkbox（固定存在，不随文件类型销毁重建；2A/2B 合并为一组）
        self._strategy_cbs: dict[str, QCheckBox] = {}

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 目标文件区域 ──────────────────────────────────────────────────────
        self._use_active_chk = QCheckBox("使用当前 CATIA 活动文档（不选择文件）")
        self._use_active_chk.toggled.connect(self._on_use_active_toggled)
        main_layout.addWidget(self._use_active_chk)

        file_row = QHBoxLayout()
        self._target_edit = QLineEdit()
        self._target_edit.setReadOnly(True)
        self._target_edit.setPlaceholderText(
            "选择一个 CATIA 文件（CATPart / CATProduct / CATDrawing）…"
        )
        last = self._settings.value("last_target", "")
        self._target_edit.setText(last)
        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.clicked.connect(self._browse_target)
        file_row.addWidget(self._target_edit)
        file_row.addWidget(self._browse_btn)
        main_layout.addLayout(file_row)

        self._file_type_label = QLabel()
        self._file_type_label.setObjectName("hintLabel")
        main_layout.addWidget(self._file_type_label)
        if last:
            self._update_file_type_label(last)

        # ── 水平 splitter：左=策略面板，右=结果表格 ───────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左侧：策略选项（固定 320px，不可滚动）
        self._strategy_panel = QWidget()
        self._strategy_panel.setFixedWidth(320)
        splitter.addWidget(self._strategy_panel)
        self._build_strategy_panel()

        # 右侧：结果表格
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        result_label = QLabel("搜索结果：")
        right_layout.addWidget(result_label)

        self._table = QTreeWidget()
        self._table.setColumnCount(_COL_COUNT)
        self._table.setHeaderLabels(["来源", "文件名", "完整路径"])
        _hdr = self._table.header()
        _hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        _hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        _hdr.resizeSection(0, 60)
        _hdr.resizeSection(1, 200)
        _hdr.setStretchLastSection(True)
        self._table.setRootIsDecorated(False)
        self._table.setIndentation(0)
        # 不使用交替行色：Qt QSS 的 branch 伪元素不支持 :alternate，
        # 开启后 branch 列背景无法同步，会出现竖条色块。
        self._table.setAlternatingRowColors(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemDoubleClicked.connect(self._open_item)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        right_layout.addWidget(self._table)

        # 结果区底部按钮
        result_btn_row = QHBoxLayout()
        self._open_all_btn = QPushButton("全部打开")
        self._open_all_btn.setEnabled(False)
        self._open_all_btn.clicked.connect(self._open_all)
        self._copy_all_btn = QPushButton("复制全部路径")
        self._copy_all_btn.setEnabled(False)
        self._copy_all_btn.clicked.connect(self._copy_all_paths)
        result_btn_row.addWidget(self._open_all_btn)
        result_btn_row.addWidget(self._copy_all_btn)
        result_btn_row.addStretch()
        right_layout.addLayout(result_btn_row)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)   # 左侧固定，不随窗口拉伸
        splitter.setStretchFactor(1, 1)   # 右侧占满剩余空间
        main_layout.addWidget(splitter, 1)

        # ── 底部按钮行 ────────────────────────────────────────────────────────
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

    # -----------------------------------------------------------------------
    # 目标文件选择
    # -----------------------------------------------------------------------

    def _on_use_active_toggled(self, use_active: bool) -> None:
        self._target_edit.setEnabled(not use_active)
        self._browse_btn.setEnabled(not use_active)
        if use_active:
            self._file_type_label.setText("将使用当前 CATIA 活动文档（搜索时自动获取）")
            self._target_edit.setPlaceholderText("将在搜索时自动获取活动文档路径…")
        else:
            self._target_edit.setPlaceholderText(
                "选择一个 CATIA 文件（CATPart / CATProduct / CATDrawing）…"
            )
            path = self._target_edit.text().strip()
            self._update_file_type_label(path) if path else self._file_type_label.setText("")

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
            self._table.clear()
            self._open_all_btn.setEnabled(False)
            self._copy_all_btn.setEnabled(False)

    def _update_file_type_label(self, path: str) -> None:
        if not path:
            self._file_type_label.setText("")
            return
        ext = Path(path).suffix.lower()
        if ext == _EXT_DRAWING:
            text = "文件类型： CATDrawing — 将查找引用的文档（COM 链接）及被引用零件/产品（2A 策略）"
        elif ext in _EXTS_PART:
            text = "文件类型： CATPart/CATProduct — 将查找引用的文档（COM 链接）及被引用图纸（2B 策略）"
        else:
            suffix = Path(path).suffix or "未知"
            text = f"文件类型：{suffix} — 仅查找引用的文档（COM 链接），不支持 2A/2B 策略"
        self._file_type_label.setText(text)

    # -----------------------------------------------------------------------
    # 解析目标路径
    # -----------------------------------------------------------------------

    def _resolve_target(self) -> str | None:
        if self._use_active_chk.isChecked():
            try:
                from catia_copilot.catia.connection import get_active_document_path
                active_path = get_active_document_path()
            except Exception as e:
                QMessageBox.warning(
                    self, "无法获取活动文档",
                    f"无法从 CATIA 获取当前活动文档路径：\n{e}\n\n请确保 CATIA 已启动且有活动文档。",
                )
                return None
            if active_path is None:
                QMessageBox.warning(
                    self, "无活动文档",
                    "CATIA 中当前没有活动文档，请先在 CATIA 中打开一个文件。",
                )
                return None
            return active_path
        target = self._target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "未选择目标文件", "请先选择一个目标 CATIA 文件。")
            return None
        if not Path(target).exists():
            QMessageBox.warning(self, "文件不存在", f"目标文件不存在：\n{target}")
            return None
        return target

    # -----------------------------------------------------------------------
    # 策略面板（固定构建，不随文件类型销毁重建）
    # -----------------------------------------------------------------------

    def _build_strategy_panel(self) -> None:
        """构建左侧策略面板，固定 320px，不可滚动。"""
        vlayout = QVBoxLayout(self._strategy_panel)
        vlayout.setSpacing(6)
        vlayout.setContentsMargins(0, 0, 4, 0)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 正向查询 ───────────────────────────────────────────────────────
        fwd_group  = QGroupBox("正向查询")
        fwd_layout = QVBoxLayout(fwd_group)
        fwd_layout.setSpacing(4)
        fwd_layout.setContentsMargins(8, 4, 8, 8)

        fwd_note = QLabel("顺着引用关系查找目标引用了谁。")
        fwd_note.setObjectName("hintLabel")
        fwd_note.setWordWrap(True)
        fwd_layout.addWidget(fwd_note)

        cb_com = QCheckBox("结构遍历（CATProduct / CATDrawing）")
        cb_com.setToolTip(
            "通过 CATIA COM 打开目标文件，按类型遍历引用结构：\n"
            "• CATProduct ：读直接子层（一级）Product.Products，\n"
            "  收集每个直接子件的文档路径\n"
            "• CATDrawing ：遍历生成式视图链接，收集关联的\n"
            "  零件/产品文档路径\n"
            "• 其他格式：退化为快照差值法，结果可能不完整"
        )
        cb_com.setChecked(self._load_cb_state("fwd_com", True))
        cb_com.stateChanged.connect(
            lambda _: self._save_cb_state("fwd_com", cb_com.isChecked())
        )
        self._strategy_cbs["fwd_com"] = cb_com
        fwd_layout.addWidget(cb_com)

        vlayout.addWidget(fwd_group)

        # ── 反向查询 ───────────────────────────────────────────────────────
        rev_group  = QGroupBox("反向查询")
        rev_layout = QVBoxLayout(rev_group)
        rev_layout.setSpacing(4)
        rev_layout.setContentsMargins(8, 4, 8, 8)

        rev_note = QLabel("逆着引用关系，找出谁引用了目标文件。")
        rev_note.setObjectName("hintLabel")
        rev_note.setWordWrap(True)
        rev_layout.addWidget(rev_note)

        cb_rev = QCheckBox("遍历已打开文档（CATProduct / CATDrawing）")
        cb_rev.setToolTip(
            "遍历已打开的文档，找出哪些文档引用了目标文件：\n"
            "• CATProduct ：读直接子层 Product.Products，\n"
            "  检查目标是否在其中\n"
            "• CATDrawing ：遍历生成式视图链接，判断是否指向目标\n"
            "• CATPart ：外部引用暂不支持（COM 接口不可达）"
        )
        cb_rev.setChecked(self._load_cb_state("rev_open_docs", True))
        cb_rev.stateChanged.connect(
            lambda _: self._save_cb_state("rev_open_docs", cb_rev.isChecked())
        )
        self._strategy_cbs["rev_open_docs"] = cb_rev
        rev_layout.addWidget(cb_rev)

        vlayout.addWidget(rev_group)

        # ── 启发式补充 ─────────────────────────────────────────────────────
        heu_group  = QGroupBox("启发式补充")
        heu_layout = QVBoxLayout(heu_group)
        heu_layout.setSpacing(4)
        heu_layout.setContentsMargins(8, 4, 8, 8)

        heu_note = QLabel("COM 链接断开时的文件名匹配策略。")
        heu_note.setObjectName("hintLabel")
        heu_note.setWordWrap(True)
        heu_layout.addWidget(heu_note)

        _HEU_ORDER = [
            "pn_param_open_docs",
            "pn_param_open_drws",
            "pn_param_scan_dirs",
            "pn_param_scan_drws",
            "same_name_scan_dirs",
            "strip_prefix_scan_dirs",
        ]
        for key in _HEU_ORDER:
            label = _HEURISTIC_LABELS.get(key, key)
            cb    = QCheckBox(label)
            cb.setChecked(self._load_cb_state(f"heu_{key}", True))
            cb.setToolTip(_HEURISTIC_HINTS.get(key, ""))
            cb.stateChanged.connect(
                lambda _, k=key: self._save_cb_state(f"heu_{k}", self._strategy_cbs[f"heu_{k}"].isChecked())
            )
            self._strategy_cbs[f"heu_{key}"] = cb
            heu_layout.addWidget(cb)

        vlayout.addWidget(heu_group)
        vlayout.addStretch()

    def _load_cb_state(self, key: str, default: bool) -> bool:
        val = self._settings.value(f"cb_{key}", None)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    def _save_cb_state(self, key: str, checked: bool) -> None:
        self._settings.setValue(f"cb_{key}", checked)

    # -----------------------------------------------------------------------
    # 搜索
    # -----------------------------------------------------------------------

    def _start_search(self) -> None:
        target = self._resolve_target()
        if not target:
            return

        # 活动文档模式下刷新文件类型提示
        self._update_file_type_label(target)

        ext = Path(target).suffix.lower()

        self._search_btn.setEnabled(False)
        self._table.clear()
        self._open_all_btn.setEnabled(False)
        self._copy_all_btn.setEnabled(False)

        # 显示临时状态行
        loading_item = QTreeWidgetItem(["", "正在搜索，请稍候…", ""])
        loading_item.setData(0, _ROLE_IS_HDR, True)
        self._table.addTopLevelItem(loading_item)
        QApplication.processEvents()

        errors: list[str] = []

        def _cb(msg: str) -> None:
            loading_item.setText(1, msg)
            QApplication.processEvents()

        # ── 正向查询：结构遍历 ────────────────────────────────────────────
        fwd_com_results: list[str] = []
        if self._strategy_cbs["fwd_com"].isChecked():
            try:
                fwd_com_results = find_dependencies(target, progress_callback=_cb)
            except Exception as e:
                logger.warning(f"find_dependencies failed: {e}")
                errors.append(f"正向查询失败：{e}")

        # ── 反向查询：遍历已打开文档 ──────────────────────────────────────
        rev_results: list[str] = []
        if self._strategy_cbs["rev_open_docs"].isChecked():
            try:
                rev_results = find_reverse_dependencies(target, progress_callback=_cb)
            except Exception as e:
                logger.warning(f"find_reverse_dependencies failed: {e}")
                errors.append(f"反向查询失败：{e}")

        # ── 启发式补充：2A（仅 CATDrawing）────────────────────────────────
        heu_2a_results: list[tuple[str, str]] = []  # (strategy_key, path)
        if ext == _EXT_DRAWING:
            enabled_2a = [
                k for k in DRAWING_SEARCH_STRATEGIES
                if self._strategy_cbs.get(f"heu_{k}", None)
                and self._strategy_cbs[f"heu_{k}"].isChecked()
            ]
            seen_2a: set[str] = set()
            for strategy in enabled_2a:
                try:
                    hits = find_part_for_drawing(
                        target,
                        strategies=[strategy],
                        max_parent_levels=SEARCH_MAX_LEVELS,
                    )
                    for h in hits:
                        if h not in seen_2a:
                            seen_2a.add(h)
                            heu_2a_results.append((strategy, h))
                except Exception as e:
                    logger.warning(f"find_part_for_drawing [{strategy}] failed: {e}")
                    errors.append(f"启发式 2A [{strategy}] 失败：{e}")

        # ── 启发式补充：2B（仅 CATPart/CATProduct）───────────────────────
        heu_2b_results: list[tuple[str, str]] = []
        if ext in _EXTS_PART:
            enabled_2b = [
                k for k in PART_TO_DRAWING_STRATEGIES
                if self._strategy_cbs.get(f"heu_{k}", None)
                and self._strategy_cbs[f"heu_{k}"].isChecked()
            ]
            seen_2b: set[str] = set()
            for strategy in enabled_2b:
                try:
                    hits = find_drawing_for_part(
                        target,
                        strategies=[strategy],
                        max_parent_levels=SEARCH_MAX_LEVELS,
                    )
                    for h in hits:
                        if h not in seen_2b:
                            seen_2b.add(h)
                            heu_2b_results.append((strategy, h))
                except Exception as e:
                    logger.warning(f"find_drawing_for_part [{strategy}] failed: {e}")
                    errors.append(f"启发式 2B [{strategy}] 失败：{e}")

        # ── 填充结果表格 ──────────────────────────────────────────────────
        self._table.clear()
        c = get_colors(theme_manager.current_mode())
        total = (len(fwd_com_results)
                 + len(rev_results)
                 + len(heu_2a_results) + len(heu_2b_results))

        # 节 1：正向查询
        if fwd_com_results:
            self._add_section_header(f"正向查询（结构遍历）— 共 {len(fwd_com_results)} 项")
            for path in fwd_com_results:
                self._add_result_row("正向", path, c.DEP_COM_FG)

        # 节 2：反向查询 — 已打开文档
        if rev_results:
            self._add_section_header(f"反向查询（已打开文档）— 共 {len(rev_results)} 项")
            for path in rev_results:
                self._add_result_row("反向", path, c.DEP_2B_FG)

        # 节 3：启发式补充 2A
        if heu_2a_results:
            self._add_section_header(f"启发式补充（图纸→零件）— 共 {len(heu_2a_results)} 项")
            for strategy_key, path in heu_2a_results:
                label = _HEURISTIC_LABELS.get(strategy_key, strategy_key)
                self._add_result_row(label, path, c.DEP_2A_FG)

        # 节 4：启发式补充 2B
        if heu_2b_results:
            self._add_section_header(f"启发式补充（零件→图纸）— 共 {len(heu_2b_results)} 项")
            for strategy_key, path in heu_2b_results:
                label = _HEURISTIC_LABELS.get(strategy_key, strategy_key)
                self._add_result_row(label, path, c.DEP_2B_FG)

        # 错误
        if errors:
            self._add_section_header("搜索期间遇到以下错误")
            for err in errors:
                item = QTreeWidgetItem(["", f"⚠ {err}", ""])
                item.setData(0, _ROLE_IS_HDR, True)
                item.setForeground(1, QBrush(c.DEP_ERROR_FG))
                self._table.addTopLevelItem(item)

        # 汇总行
        if total == 0 and not errors:
            item = QTreeWidgetItem(["", "未找到任何依赖项目。", ""])
            item.setData(0, _ROLE_IS_HDR, True)
            item.setForeground(1, QBrush(c.DEP_EMPTY_FG))
            self._table.addTopLevelItem(item)
        elif total > 0:
            item = QTreeWidgetItem(["", f"搜索完成，共找到 {total} 个结果。", ""])
            item.setData(0, _ROLE_IS_HDR, True)
            item.setForeground(1, QBrush(c.DEP_DONE_FG))
            self._table.addTopLevelItem(item)

        self._open_all_btn.setEnabled(total > 0)
        self._copy_all_btn.setEnabled(total > 0)
        self._search_btn.setEnabled(True)

    # -----------------------------------------------------------------------
    # 表格辅助
    # -----------------------------------------------------------------------

    def _add_section_header(self, text: str) -> None:
        item = QTreeWidgetItem(["", text, ""])
        item.setData(0, _ROLE_IS_HDR, True)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont()
        font.setBold(True)
        # 节标题背景/前景色：复用 ui_colors 中已定义的产品行颜色（深/浅主题均适配）
        c = get_colors(theme_manager.current_mode())
        for col in range(_COL_COUNT):
            item.setFont(col, font)
            item.setBackground(col, QBrush(c.ROW_PRODUCT_BG))
        self._table.addTopLevelItem(item)

    def _add_result_row(self, tag: str, path: str, color: QColor) -> None:
        name = Path(path).name
        item = QTreeWidgetItem([tag, name, path])
        item.setData(0, _ROLE_PATH, path)
        item.setData(0, _ROLE_IS_HDR, False)
        item.setToolTip(_COL_NAME, path)
        item.setToolTip(_COL_PATH, path)
        for col in range(_COL_COUNT):
            item.setForeground(col, QBrush(color))
        self._table.addTopLevelItem(item)

    def _iter_result_paths(self) -> list[str]:
        """遍历表格，返回所有真实结果行的路径（跳过节标题）。"""
        paths: list[str] = []
        for i in range(self._table.topLevelItemCount()):
            item = self._table.topLevelItem(i)
            if item and not item.data(0, _ROLE_IS_HDR):
                p = item.data(0, _ROLE_PATH)
                if p:
                    paths.append(p)
        return paths

    # -----------------------------------------------------------------------
    # 打开
    # -----------------------------------------------------------------------

    def _open_item(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        """双击行时在 CATIA 中打开对应文件。"""
        if item.data(0, _ROLE_IS_HDR):
            return
        path = item.data(0, _ROLE_PATH)
        if not path:
            return
        self._open_in_catia(path)

    def _open_all(self) -> None:
        paths = self._iter_result_paths()
        errors: list[str] = []
        try:
            from catia_copilot.catia.connection import open_document
            from catia_copilot.utils import bring_catia_to_foreground
            for p in paths:
                try:
                    open_document(p)
                except Exception as e:
                    errors.append(f"{Path(p).name}: {e}")
            # 全部打开后置前台一次
            try:
                bring_catia_to_foreground()
            except Exception:
                pass
        except Exception as e:
            QMessageBox.critical(self, "全部打开失败", f"无法连接 CATIA ：\n{e}")
            return
        if errors:
            QMessageBox.warning(
                self, "部分文件打开失败",
                "以下文件打开失败：\n" + "\n".join(errors),
            )

    def _open_in_catia(self, fp: str) -> None:
        try:
            from catia_copilot.catia.connection import open_document
            open_document(fp, foreground=True)
        except Exception as e:
            QMessageBox.critical(
                self, "打开失败",
                f"无法在 CATIA 中打开文件：\n{fp}\n\n{e}",
            )

    # -----------------------------------------------------------------------
    # 复制
    # -----------------------------------------------------------------------

    def _copy_all_paths(self) -> None:
        paths = self._iter_result_paths()
        if paths:
            QApplication.clipboard().setText("\n".join(paths))

    # -----------------------------------------------------------------------
    # 右键菜单（与 bom_edit_dialog 风格一致）
    # -----------------------------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None or item.data(0, _ROLE_IS_HDR):
            return
        fp = item.data(0, _ROLE_PATH) or ""
        fp_path = Path(fp) if fp else None

        # 确保右键点击行被选中
        if not item.isSelected():
            self._table.clearSelection()
            item.setSelected(True)

        menu = QMenu(self)

        # ── 缩略图 ────────────────────────────────────────────────────────
        if fp and fp_path is not None and fp_path.exists():
            img_bytes = read_catia_thumbnail(fp)
            if img_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(img_bytes) and not pixmap.isNull():
                    if (pixmap.width() > BOM_THUMBNAIL_MAX_SIZE
                            or pixmap.height() > BOM_THUMBNAIL_MAX_SIZE):
                        pixmap = pixmap.scaled(
                            BOM_THUMBNAIL_MAX_SIZE,
                            BOM_THUMBNAIL_MAX_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    thumb_label = QLabel()
                    thumb_label.setPixmap(pixmap)
                    thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    thumb_label.setContentsMargins(6, 4, 6, 4)
                    thumb_action = QWidgetAction(menu)
                    thumb_action.setDefaultWidget(thumb_label)
                    menu.addAction(thumb_action)
                    menu.addSeparator()

        # ── 打开路径 ──────────────────────────────────────────────────────
        act_open_path = menu.addAction("打开路径")
        path_available = (
            bool(fp) and fp_path is not None
            and (fp_path.exists() or fp_path.parent.exists())
        )
        act_open_path.setEnabled(path_available)

        # ── 复制路径 ──────────────────────────────────────────────────────
        act_copy_path = menu.addAction("复制路径")
        act_copy_path.setEnabled(bool(fp))

        # ── 在 CATIA 中打开 ───────────────────────────────────────────────
        act_open_catia = menu.addAction("在 CATIA 中打开")
        act_open_catia.setEnabled(bool(fp))

        action = menu.exec(self._table.viewport().mapToGlobal(pos))

        if action == act_open_path:
            self._open_path(fp)
        elif action == act_copy_path:
            QApplication.clipboard().setText(fp)
        elif action == act_open_catia:
            self._open_in_catia(fp)

    def _open_path(self, fp: str) -> None:
        """在资源管理器中打开文件所在目录并高亮选中文件。"""
        p = Path(fp)
        try:
            if p.exists():
                ctypes.windll.shell32.ShellExecuteW(
                    None, "open", "explorer.exe", f'/select,"{p}"', None, 1
                )
            elif p.parent.exists():
                ctypes.windll.shell32.ShellExecuteW(
                    None, "open", "explorer.exe", f'"{p.parent}"', None, 1
                )
        except Exception as exc:
            logger.warning(f"打开路径失败: {exc}")


    def closeEvent(self, event):  # noqa: N802
        """关闭时保存窗口几何。"""
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
