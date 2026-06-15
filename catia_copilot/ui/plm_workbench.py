"""
PLM 工作台主窗口（重构版）。

独立非模态 QDialog，通过 QTabWidget 整合所有 PLM 对接功能：
  Tab 0 - 同步：CATIA 本地 ↔ PLM 三面板对比 + 增量同步
  Tab 1 - 设置：连接配置 / 标签规则 / 同步历史（内嵌三子 Tab）

使用方式：
    win = PlmWorkbench(parent)
    win.show()
    win.raise_()
"""

from __future__ import annotations

import json
import logging
import os
import pythoncom
from datetime import datetime

from PySide6.QtCore import QSettings, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QProgressDialog
)

from catia_copilot.ui.bom_widgets import _BomTreeWidget
from catia_copilot.ui.ui_colors import get_colors as _get_colors
from catia_copilot.ui.theme_manager import theme_manager
from catia_copilot.ui.ui_layout import L

from catia_copilot.catia.bom_collect import collect_bom_rows_archive, check_unsaved_docs
from catia_copilot.constants import (
    BOM_COLUMN_DISPLAY_NAMES,
    BOM_EDIT_COLUMN_ORDER,
    BOM_HIDEABLE_COLUMNS,
    PRESET_USER_REF_PROPERTIES,
    PLM_SYNC_MAX_NODES,
    BomNodeType,
)
from catia_copilot.plm.api_client import PlmApiClient
from catia_copilot.plm.sync import (
    extract_bom_v3,
    sync_bom_to_plm,
    AfterUpdatePolicy,
    CheckedOutByOtherPolicy,
    ExistingPartPolicy,
    OwnCheckedOutPolicy,
    SyncOptions,
)

logger = logging.getLogger(__name__)

# ── QSettings 键（与 PlmSyncDialog 共用 PlmConfig，保持配置互通） ─────────────
_S_ORG       = "CATIACompanion"
_S_PLM_CFG   = "PlmConfig"
_S_TAG_RULES = "PlmTagRules"
_S_HISTORY   = "PlmSyncHistory"
_S_WB        = "PlmWorkbench"        # 工作台专用（列可见性等）

_DEFAULT_BASE_URL  = "http://127.0.0.1:8001/docdoku-plm-server-rest/api"
_DEFAULT_LOGIN     = "admin"
_DEFAULT_PASSWORD  = "password"
_DEFAULT_WORKSPACE = "Workspace_0"

_MAX_HISTORY = 20

# BOM 预览树：必须始终显示的固定列（不提供 checkbox）
_PREVIEW_FIXED_COLS: list[str] = ["Level", "Type", "Part Number", "Quantity"]

# BOM 预览树：默认勾选的可选列（Filename + 可隐藏列的子集）
_PREVIEW_DEFAULT_COLS = ["Filename", "Nomenclature"]

# 同步结果内部列名
_SYNC_COL_SOURCE  = "_sync_source"
_SYNC_COL_UPDATE  = "_sync_update"
_SYNC_COL_CHECKIN = "_sync_checkin"
# 兼容旧代码中偶有引用的别名
_SYNC_COL_OP     = _SYNC_COL_SOURCE
_SYNC_COL_STATUS = _SYNC_COL_CHECKIN
_SYNC_COL_DISPLAY = {
    _SYNC_COL_SOURCE:  "签出来源",
    _SYNC_COL_UPDATE:  "更新结果",
    _SYNC_COL_CHECKIN: "签入状态",
}
_SYNC_COLS_ORDERED = [_SYNC_COL_SOURCE, _SYNC_COL_UPDATE, _SYNC_COL_CHECKIN]


def _sync_row_color(source: str, update: str = "", checkin: str = ""):
    """根据同步三列内容返回对应的 QColor，无法匹配时返回 None。

    颜色语义：
      绿色  #27ae60 — 新建成功
      蓝色  #2980b9 — 已有零件更新成功（签出、属性写入、签入均属此类）
      灰色  #7f8c8d — 跳过 / 无变化
      红色  #e74c3c — 任何失败
    """
    combined = source + update + checkin
    if "失败" in combined or "✗" in combined:
        return QColor("#e74c3c")
    if "跳过" in combined or "无变化" in combined:
        return QColor("#7f8c8d")
    if "新建" in source:
        return QColor("#27ae60")
    if (
        "签出" in source
        or "更新" in source
        or "已写入" in update
        or "已上传" in update
        or "已签入" in checkin
        or "保留签出" in checkin
        or "✓" in combined
    ):
        return QColor("#2980b9")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 后台工作线程
# ─────────────────────────────────────────────────────────────────────────────

class _ConnectWorker(QThread):
    """测试连接，拉取工作区信息与用户列表。"""
    success = Signal(str, list, dict)
    failure = Signal(str)

    def __init__(self, base_url, login, password, workspace):
        super().__init__()
        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace

    def run(self):
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            users = c.list_users(self._workspace) or []
            ws_info: dict = {}
            try:
                all_ws = c._request("GET", "/workspaces") or {}
                admin_ids = {w.get("id") for w in (all_ws.get("administratedWorkspaces") or [])}
                for w in (all_ws.get("allWorkspaces") or []) + (all_ws.get("administratedWorkspaces") or []):
                    if w.get("id") == self._workspace:
                        ws_info.update(w)
                        break
                ws_info["_current_user_role"] = "管理员" if self._workspace in admin_ids else "普通成员"
            except Exception as e:
                ws_info["_current_user_role"] = f"未知（{e}）"
            self.success.emit(self._login, users, ws_info)
        except Exception as exc:
            self.failure.emit(str(exc))


class _BomPreviewWorker(QThread):
    """从 CATIA 提取 BOM 行数据（不含 PLM 操作）。"""
    success  = Signal(list)
    failure  = Signal(str)
    progress = Signal(int)

    def run(self):
        pythoncom.CoInitialize()
        try:
            all_cols = list(dict.fromkeys(
                BOM_EDIT_COLUMN_ORDER
                + [c for c in PRESET_USER_REF_PROPERTIES if c not in BOM_EDIT_COLUMN_ORDER]
            ))
            custom_cols = [c for c in all_cols if c in PRESET_USER_REF_PROPERTIES]
            rows = collect_bom_rows_archive(
                None,
                all_cols,
                custom_cols,
                progress_callback=lambda n: self.progress.emit(n),
            )
            self.success.emit(rows or [])
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


class _SyncWorker(QThread):
    """执行 BOM 同步（含 v3 路径 COM 遍历 + PLM 网络操作）。"""
    progress   = Signal(str)
    upload_log = Signal(str, str, str, str)
    sync_done  = Signal(object)
    error      = Signal(str)

    def __init__(self, base_url, login, password, workspace, options):
        super().__init__()
        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._options   = options

    def run(self):
        pythoncom.CoInitialize()
        try:
            self.progress.emit("正在从 CATIA 提取 BOM（含位置信息）……")
            bom_root = extract_bom_v3(
                progress_callback=lambda m: self.progress.emit(m),
            )
            if bom_root is None:
                self.error.emit(
                    "BOM 提取失败：请确认 CATIA 已启动、有活动文档，且文档包含产品结构。"
                )
                return
            self.progress.emit("BOM 提取完成，正在连接 PLM……")
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            result = sync_bom_to_plm(
                bom_root, c, self._workspace,
                options=self._options,
                progress_callback=lambda m: self.progress.emit(m),
            )
            self.sync_done.emit(result)
        except Exception as exc:
            logger.exception("PLM 同步后台线程异常")
            self.error.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


class _TagsWorker(QThread):
    """拉取工作区 Tag 列表。"""
    success = Signal(list)
    failure = Signal(str)

    def __init__(self, base_url, login, password, workspace):
        super().__init__()
        self._base_url = base_url; self._login = login
        self._password = password; self._workspace = workspace

    def run(self):
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            self.success.emit(c.list_tags(self._workspace) or [])
        except Exception as exc:
            self.failure.emit(str(exc))


class _CreateTagWorker(QThread):
    """在工作区创建新 Tag。"""
    success = Signal(str)
    failure = Signal(str)

    def __init__(self, base_url, login, password, workspace, label):
        super().__init__()
        self._base_url = base_url; self._login = login
        self._password = password; self._workspace = workspace
        self._label = label

    def run(self):
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            c.post(f"/workspaces/{self._workspace}/tags", {"label": self._label})
            self.success.emit(self._label)
        except Exception as exc:
            self.failure.emit(str(exc))


class _PlmStatusWorker(QThread):
    """按本地 BOM 零件号列表精确查询 PLM 状态，不下载文件。

    改进：不再 list_parts 全量拉取，而是用 search_parts 按零件号逐个精确查，
    只返回本地 BOM 中实际存在的零件信息，避免全量拉取的性能开销。
    """
    success  = Signal(list)   # list[dict]: [{number, version, lastIterationNumber, checkOutUser}, ...]
    failure  = Signal(str)
    progress = Signal(int, int)  # (done, total) 查询进度

    def __init__(self, base_url, login, password, workspace, part_numbers: list[str]):
        super().__init__()
        self._base_url      = base_url
        self._login         = login
        self._password      = password
        self._workspace     = workspace
        self._part_numbers  = [pn for pn in part_numbers if pn]  # 过滤空值

    def run(self):
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)

            results: list[dict] = []
            total = len(self._part_numbers)

            if total == 0:
                self.success.emit([])
                return

            # 按零件号精确查询，每次 search_parts 传 number= 做精确匹配
            # DocdokuPLM 的 number 参数支持完整匹配，size=1 取第一条
            for i, pn in enumerate(self._part_numbers, 1):
                try:
                    hits = c.search_parts(
                        self._workspace,
                        number=pn,
                        fetch_head_only=True,
                        size=1,
                    )
                    # search_parts 按 number 精确查，取第一条且编号完全吻合的
                    for hit in (hits or []):
                        if str(hit.get("number", "")) == pn:
                            results.append(hit)
                            break
                except Exception:
                    # 单个零件查询失败不中断整体
                    pass
                self.progress.emit(i, total)

            self.success.emit(results)
        except Exception as exc:
            self.failure.emit(str(exc))


class _PullWorker(QThread):
    """执行 Pull 操作的后台线程，支持三种模式：

    MODE_SEARCH    — 按零件号搜索 PLM（search_parts）
    MODE_BOM       — 递归拼装 Part BOM 树（get_part_components_flat）
    MODE_DOWNLOAD  — 批量下载附件，每个零件保存到独立子目录

    信号：
      search_done(list)                  — 搜索完成，返回 PartRevisionDTO 列表
      bom_done(list)                     — BOM 树展开完成，返回扁平行列表
      file_progress(str, int, int, float)— (filename, downloaded, total, speed_bps)
      file_done(str, str)                — (filename, dest_path) 单文件完成
      all_done(int)                      — 全部下载完成，参数为已下载文件总数
      failure(str)                       — 失败
    """
    search_done   = Signal(list)
    bom_done      = Signal(list)
    file_progress = Signal(str, int, int, float)
    file_done     = Signal(str, str)
    all_done      = Signal(int)
    failure       = Signal(str)

    MODE_SEARCH   = "search"
    MODE_BOM      = "bom"
    MODE_DOWNLOAD = "download"

    def __init__(self, base_url, login, password, workspace):
        super().__init__()
        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._mode      = self.MODE_SEARCH
        # 搜索参数
        self._search_number = ""
        # BOM 展开参数
        self._bom_number  = ""
        self._bom_version = "A"
        # 下载参数：list of (part_number, version, iteration, filename)
        self._dl_items: list[tuple[str, str, str, str]] = []
        self._dl_base_dir = ""   # 基础目录，每个零件在此目录下建子目录

    def set_search(self, number: str) -> None:
        self._mode = self.MODE_SEARCH
        self._search_number = number

    def set_bom(self, number: str, version: str = "A") -> None:
        self._mode        = self.MODE_BOM
        self._bom_number  = number
        self._bom_version = version

    def set_download(
        self,
        items: list[tuple[str, str, str, str]],
        base_dir: str,
    ) -> None:
        """设置批量下载参数。

        items: list of (part_number, version, iteration, filename)
               来自 BOM 表格勾选行，每行可能有多个文件
        base_dir: 基础目录，每个零件的文件下载到 base_dir/{part_number}/
        """
        self._mode        = self.MODE_DOWNLOAD
        self._dl_items    = items
        self._dl_base_dir = base_dir

    def run(self):
        import os as _os
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)

            if self._mode == self.MODE_SEARCH:
                results = c.search_parts(
                    self._workspace,
                    number=self._search_number,
                    fetch_head_only=True,
                    size=100,
                )
                self.search_done.emit(results or [])

            elif self._mode == self.MODE_BOM:
                rows = c.get_part_components_flat(
                    self._workspace,
                    self._bom_number,
                    self._bom_version,
                )
                self.bom_done.emit(rows or [])

            elif self._mode == self.MODE_DOWNLOAD:
                total_done = 0
                for pn, ver, itr, fname in self._dl_items:
                    # 每个零件保存到独立子目录，避免同名文件冲突
                    part_dir = _os.path.join(self._dl_base_dir, pn)
                    _os.makedirs(part_dir, exist_ok=True)
                    dest_path = _os.path.join(part_dir, fname)

                    def _progress(dl, total, speed, _fn=fname):
                        self.file_progress.emit(_fn, dl, total, speed)

                    c.download_attached_file(
                        self._workspace, pn, ver, itr, fname,
                        dest_path,
                        progress_cb=_progress,
                    )
                    self.file_done.emit(fname, dest_path)
                    total_done += 1
                self.all_done.emit(total_done)

        except Exception as exc:
            self.failure.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────────────────────────────

class PlmWorkbench(QDialog):
    """PLM 工作台主窗口（非模态）。

    布局：
        顶部工具栏  — 连接状态 + 加载BOM + 刷新PLM + Push/Pull + 设置/历史按钮
        主体分栏   — 左: PLM Part 树  |  右: 差异对比表
        底部状态栏  — 进度条 + 状态文本 + 速度
    """

    # ── 差异对比表列常量 ───────────────────────────────────────────────────────
    _DC_DEPTH  = 0   # 层级缩进
    _DC_PN     = 1   # 零件号
    _DC_NOM    = 2   # 术语
    _DC_STATUS = 3   # 差异状态
    _DC_LOC_V  = 4   # 本地版本/迭代
    _DC_PLM_V  = 5   # PLM 版本/迭代
    _DC_COUT   = 6   # 签出人
    _DC_SEL    = 7   # 选择 checkbox
    _DC_HEADERS = ["层级", "零件号", "术语", "状态", "本地版本", "PLM版本", "签出人", "选择"]

    # 差异状态值及颜色
    _ST_UNKNOWN  = "?"          # 未查询
    _ST_OK       = "✓ 一致"
    _ST_PUSH     = "↑ Push"     # 本地新
    _ST_PULL     = "↓ Pull"     # PLM 新
    _ST_NEW_LOC  = "仅本地"
    _ST_NEW_PLM  = "仅PLM"
    _ST_CONFLICT = "! 冲突"

    _STATUS_COLORS = {
        _ST_UNKNOWN:  "#7f8c8d",
        _ST_OK:       "#27ae60",
        _ST_PUSH:     "#e67e22",
        _ST_PULL:     "#2980b9",
        _ST_NEW_LOC:  "#8e44ad",
        _ST_NEW_PLM:  "#16a085",
        _ST_CONFLICT: "#e74c3c",
    }

    # 旧版兼容别名（业务逻辑方法仍引用这些列常量）
    _COL_PN        = 1   # _DC_PN
    _COL_NOM       = 2   # _DC_NOM
    _COL_STATUS    = 3   # _DC_STATUS
    _COL_LOC_VER   = 4   # _DC_LOC_V（显示"A/3"格式）
    _COL_LOC_ITER  = 4   # 同上（合并显示）
    _COL_PLM_VER   = 5   # _DC_PLM_V
    _COL_PLM_ITER  = 5   # 同上（合并显示）
    _COL_PLM_USER  = 6   # _DC_COUT
    _COL_PUSH      = 7   # _DC_SEL
    _COL_UPGRADE   = 7   # 同上（合并 checkbox）

    _BOM_COL_HEADERS = _DC_HEADERS  # 兼容旧引用

    _UPGRADE_SKIP = "不推送"
    _UPGRADE_ITER = "+迭代"
    _UPGRADE_VER  = "+版本"

    # QSettings 键
    _S_ORG     = _S_ORG
    _S_PLM_CFG = _S_PLM_CFG
    _S_WB      = _S_WB

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PLM 工作台")
        self.setMinimumSize(1000, 620)
        self.resize(1280, 750)

        s = QSettings(_S_ORG, _S_WB)
        saved_geom = s.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        try:
            theme_manager.register(self)
        except Exception:
            pass

        # 数据缓存
        self._bom_rows: list[dict] = []
        self._visible_bom_rows: list[dict] = []
        self._plm_parts_cache: dict[str, dict] = {}
        self._sync_result_map: dict[str, tuple[str, str, str]] = {}
        self._workers: list[QThread] = []

        # 同步进度跟踪
        self._sync_total_nodes = 0
        self._sync_done_nodes  = 0
        self._sync_seen_pns: set = set()
        self._sync_push_map: dict = {}
        self._last_sync_login = ""
        self._last_sync_mode  = ""

        # ── 整体布局（垂直三段：顶栏 + 主体 + 底栏） ─────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        root.addWidget(self._build_main_body(), 1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        root.addWidget(self._build_status_bar())

        # 高级选项区（折叠，放在底栏上方）
        self._adv_widget = self._build_advanced_options()
        self._adv_widget.setVisible(False)
        root.insertWidget(root.count() - 1, self._adv_widget)

        # 兼容旧代码引用
        self._tbl_bom         = self._tbl_diff
        self._tbl_local       = self._tbl_diff
        self._tbl_arrow       = QTableWidget()
        self._tbl_plm         = QTableWidget()
        self._btn_load_preview = self._btn_load_bom
        self._btn_sync_start   = self._btn_push
        self._lbl_sync_status  = self._lbl_status
        self._lbl_sync_summary = self._lbl_summary
        self._lbl_upload_speed = self._lbl_speed
        self._pgb_sync         = self._pgb

        # 隐藏的 BOM 树（供内部同步结果追踪）
        self._preview_tree = _BomTreeWidget()
        self._col_vis_widget = QWidget()
        self._col_vis_vbox   = QVBoxLayout(self._col_vis_widget)
        self._col_vis_row0   = QHBoxLayout()
        self._col_vis_row1   = QHBoxLayout()
        self._col_vis_vbox.addLayout(self._col_vis_row0)
        self._col_vis_vbox.addLayout(self._col_vis_row1)
        self._col_checkboxes: dict[str, QCheckBox] = {}
        self._build_col_visibility_row()

        # 初始化设置面板中需要的控件（供旧业务逻辑方法使用）
        self._init_settings_controls()

        # 加载历史
        self._refresh_history_list()

    def closeEvent(self, event):
        s = QSettings(_S_ORG, _S_WB)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("chk_incremental",    self._chk_incremental.isChecked())
        s.setValue("chk_reg_product",    self._chk_reg_product.isChecked())
        s.setValue("chk_upload_catpart", self._chk_upload_catpart.isChecked())
        s.setValue("chk_upload_stp",     self._chk_upload_stp.isChecked())
        s.setValue("chk_upload_drw_file", self._chk_upload_drw_file.isChecked())
        s.setValue("chk_upload_drw_pdf",  self._chk_upload_drw_pdf.isChecked())
        super().closeEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # 布局构建
    # ─────────────────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        """顶部工具栏（一行）：连接状态 + 操作按钮 + 设置/历史入口。"""
        bar = QWidget()
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(6)

        _ef = QFont("Segoe UI Emoji"); _ef.setPointSize(9)

        # 连接状态
        dot_font = QFont("Segoe UI Emoji"); dot_font.setPointSize(11)
        self._lbl_conn_dot  = QLabel("●")
        self._lbl_conn_dot.setFont(dot_font)
        self._lbl_conn_info = QLabel("未配置")
        h.addWidget(self._lbl_conn_dot)
        h.addWidget(self._lbl_conn_info)

        sep = QFrame(); sep.setFrameShape(QFrame.VLine); sep.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep)

        # 加载 BOM 按钮
        self._btn_load_bom = QPushButton("↺ 加载 BOM")
        self._btn_load_bom.setFont(_ef)
        self._btn_load_bom.setToolTip("从 CATIA 当前活动文档读取 BOM")
        self._btn_load_bom.clicked.connect(self._on_load_preview)
        h.addWidget(self._btn_load_bom)

        # 刷新 PLM 状态
        self._btn_refresh_plm = QPushButton("☁ 刷新 PLM 状态")
        self._btn_refresh_plm.setFont(_ef)
        self._btn_refresh_plm.setToolTip("按本地 BOM 零件号逐一查询 PLM 状态")
        self._btn_refresh_plm.clicked.connect(self._on_refresh_plm_status)
        h.addWidget(self._btn_refresh_plm)

        # 节点/查询进度标签
        self._lbl_node_count      = QLabel("")
        self._lbl_plm_query_status = QLabel("")
        h.addWidget(self._lbl_node_count)
        h.addWidget(self._lbl_plm_query_status)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine); sep2.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep2)

        # Push / Pull
        self._btn_push = QPushButton("⬆ Push 选中项")
        self._btn_push.setFont(_ef)
        self._btn_push.setObjectName("primaryBtn")
        self._btn_push.setEnabled(False)
        self._btn_push.setToolTip("将勾选零件推送到 PLM")
        self._btn_push.clicked.connect(self._on_sync_start)
        h.addWidget(self._btn_push)

        self._btn_pull_sel = QPushButton("⬇ Pull 选中项")
        self._btn_pull_sel.setFont(_ef)
        self._btn_pull_sel.setEnabled(False)
        self._btn_pull_sel.setToolTip("从 PLM 拉取勾选零件的文件到本地")
        self._btn_pull_sel.clicked.connect(self._on_pull_selected)
        h.addWidget(self._btn_pull_sel)

        self._btn_pull = QPushButton("⬇ Pull BOM 树")
        self._btn_pull.setFont(_ef)
        self._btn_pull.setToolTip("输入根零件号，拉取整棵 BOM 树的文件")
        self._btn_pull.clicked.connect(self._on_pull)
        h.addWidget(self._btn_pull)

        h.addStretch()

        # 高级选项切换
        self._btn_adv = QPushButton("▶ 高级")
        self._btn_adv.setFont(_ef)
        self._btn_adv.setFlat(True)
        self._btn_adv.setCheckable(True)
        self._btn_adv.setChecked(False)
        self._btn_adv.clicked.connect(self._toggle_adv)
        h.addWidget(self._btn_adv)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.VLine); sep3.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep3)

        # 历史
        btn_hist = QPushButton("📋 历史")
        btn_hist.setFont(_ef)
        btn_hist.setFlat(True)
        btn_hist.setToolTip("查看同步历史记录")
        btn_hist.clicked.connect(self._on_show_history)
        h.addWidget(btn_hist)

        # 设置
        btn_cfg = QPushButton("⚙ 设置")
        btn_cfg.setFont(_ef)
        btn_cfg.setFlat(True)
        btn_cfg.setToolTip("PLM 连接配置与标签规则")
        btn_cfg.clicked.connect(self._on_show_settings)
        h.addWidget(btn_cfg)

        self._update_conn_status_bar()
        return bar

    def _build_main_body(self) -> QWidget:
        """主体分栏：左侧 PLM Part 树 | 右侧差异对比表。"""
        from PySide6.QtWidgets import QTreeWidget
        self._splitter = QSplitter(Qt.Horizontal)

        # ── 左侧：PLM Part 树 ─────────────────────────────────────────────────
        left_w = QWidget()
        left_v = QVBoxLayout(left_w)
        left_v.setContentsMargins(4, 4, 4, 4)
        left_v.setSpacing(4)

        _ef = QFont("Segoe UI Emoji"); _ef.setPointSize(9)

        # 搜索框（输入根零件号后点击展开）
        search_row = QHBoxLayout()
        self._le_plm_root = QLineEdit()
        self._le_plm_root.setPlaceholderText("输入根零件号…")
        self._le_plm_root.returnPressed.connect(self._on_load_plm_tree)
        btn_load_tree = QPushButton("展开")
        btn_load_tree.setFont(_ef)
        btn_load_tree.setFixedWidth(50)
        btn_load_tree.clicked.connect(self._on_load_plm_tree)
        search_row.addWidget(self._le_plm_root, 1)
        search_row.addWidget(btn_load_tree)
        left_v.addLayout(search_row)

        # PLM Part 树
        self._tree_plm = QTreeWidget()
        self._tree_plm.setHeaderLabels(["零件号 / 名称", "版本", "签出人"])
        _th = self._tree_plm.header()
        _th.setStretchLastSection(False)
        _th.setSectionResizeMode(0, QHeaderView.Stretch)
        _th.setSectionResizeMode(1, QHeaderView.Fixed)
        _th.setSectionResizeMode(2, QHeaderView.Interactive)
        _th.resizeSection(1, 50)
        _th.resizeSection(2, 80)
        self._tree_plm.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tree_plm.itemClicked.connect(self._on_plm_tree_item_clicked)
        left_v.addWidget(self._tree_plm, 1)

        # PLM 树状态标签
        self._lbl_tree_status = QLabel("— 输入零件号后点击「展开」—")
        self._lbl_tree_status.setStyleSheet("color: palette(mid);")
        self._lbl_tree_status.setWordWrap(True)
        left_v.addWidget(self._lbl_tree_status)

        self._splitter.addWidget(left_w)

        # ── 右侧：差异对比表 ──────────────────────────────────────────────────
        right_w = QWidget()
        right_v = QVBoxLayout(right_w)
        right_v.setContentsMargins(4, 4, 4, 4)
        right_v.setSpacing(4)

        # 差异表
        self._tbl_diff = QTableWidget(0, len(self._DC_HEADERS))
        self._tbl_diff.setHorizontalHeaderLabels(self._DC_HEADERS)
        self._tbl_diff.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_diff.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_diff.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tbl_diff.setAlternatingRowColors(True)
        dh = self._tbl_diff.horizontalHeader()
        dh.setSectionResizeMode(self._DC_DEPTH,  QHeaderView.Fixed)
        dh.setSectionResizeMode(self._DC_PN,     QHeaderView.Interactive)
        dh.setSectionResizeMode(self._DC_NOM,    QHeaderView.Interactive)
        dh.setSectionResizeMode(self._DC_STATUS, QHeaderView.Fixed)
        dh.setSectionResizeMode(self._DC_LOC_V,  QHeaderView.Interactive)
        dh.setSectionResizeMode(self._DC_PLM_V,  QHeaderView.Interactive)
        dh.setSectionResizeMode(self._DC_COUT,   QHeaderView.Interactive)
        dh.setSectionResizeMode(self._DC_SEL,    QHeaderView.Fixed)
        dh.resizeSection(self._DC_DEPTH,  36)
        dh.resizeSection(self._DC_PN,     180)
        dh.resizeSection(self._DC_NOM,    130)
        dh.resizeSection(self._DC_STATUS,  76)
        dh.resizeSection(self._DC_LOC_V,   90)
        dh.resizeSection(self._DC_PLM_V,   90)
        dh.resizeSection(self._DC_COUT,   100)
        dh.resizeSection(self._DC_SEL,     44)
        dh.setStretchLastSection(False)
        # 让「术语」列自动拉伸填充剩余空间
        dh.setSectionResizeMode(self._DC_NOM, QHeaderView.Stretch)
        right_v.addWidget(self._tbl_diff, 1)

        # 右侧差异表下方：本地 BOM 加载按钮行
        local_row = QHBoxLayout()
        local_row.setSpacing(6)
        lbl_local = QLabel("本地 BOM：")
        local_row.addWidget(lbl_local)
        local_row.addStretch()
        # 全选 / 全不选
        btn_sel_all  = QPushButton("全选")
        btn_sel_none = QPushButton("全不选")
        btn_sel_all.setFixedWidth(50)
        btn_sel_none.setFixedWidth(60)
        btn_sel_all.clicked.connect(lambda: self._set_diff_checked(True))
        btn_sel_none.clicked.connect(lambda: self._set_diff_checked(False))
        local_row.addWidget(btn_sel_all)
        local_row.addWidget(btn_sel_none)
        right_v.addLayout(local_row)

        self._splitter.addWidget(right_w)
        self._splitter.setSizes([260, 800])
        self._splitter.setHandleWidth(4)

        return self._splitter

    def _build_status_bar(self) -> QWidget:
        """底部状态栏：进度条 + 状态文本 + 速度。"""
        bar = QWidget()
        bar.setFixedHeight(44)
        v = QVBoxLayout(bar)
        v.setContentsMargins(8, 2, 8, 2)
        v.setSpacing(2)

        self._pgb = QProgressBar()
        self._pgb.setRange(0, 0)
        self._pgb.setMaximumHeight(10)
        self._pgb.setVisible(False)
        v.addWidget(self._pgb)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._lbl_status  = QLabel("就绪")
        self._lbl_speed   = QLabel("")
        self._lbl_summary = QLabel("")
        self._lbl_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._lbl_status, 1)
        row.addWidget(self._lbl_speed)
        row.addWidget(self._lbl_summary, 1)
        v.addLayout(row)

        return bar

    def _toggle_adv(self, checked: bool) -> None:
        self._adv_widget.setVisible(checked)
        self._btn_adv.setText("▼ 高级" if checked else "▶ 高级")

    def _set_diff_checked(self, checked: bool) -> None:
        for i in range(self._tbl_diff.rowCount()):
            w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isEnabled():
                    chk.setChecked(checked)

    def _on_show_settings(self) -> None:
        """弹出设置对话框。"""
        dlg = _SettingsDialog(self)
        dlg.exec()
        # 设置保存后更新状态栏
        self._update_conn_status_bar()

    def _on_show_history(self) -> None:
        """弹出历史对话框。"""
        dlg = _HistoryDialog(self)
        dlg.exec()

    def _on_load_plm_tree(self) -> None:
        """从左侧搜索框加载 PLM Part 树。"""
        pn = self._le_plm_root.text().strip()
        if not pn:
            return
        base_url, login, password, workspace = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", '请先在设置中配置 PLM 连接信息。')
            return
        self._lbl_tree_status.setText(f"正在展开 {pn} 的 BOM 树……")
        self._tree_plm.clear()

        w = _PullWorker(base_url, login, password, workspace)
        # 先搜索确认版本，再展开树
        w.search_done.connect(self._on_plm_tree_search_done)
        w.bom_done.connect(self._on_plm_tree_loaded)
        w.failure.connect(lambda err: self._lbl_tree_status.setText(f"失败：{err}"))
        w.set_search(number=pn)
        self._start_worker(w)

    def _on_plm_tree_search_done(self, parts: list) -> None:
        if not parts:
            self._lbl_tree_status.setText("未找到零件。")
            return
        p = parts[0]
        pn  = str(p.get("number", ""))
        ver = str(p.get("version", "") or "A")
        base_url, login, password, workspace = self._read_conn()
        w = _PullWorker(base_url, login, password, workspace)
        w.bom_done.connect(self._on_plm_tree_loaded)
        w.failure.connect(lambda err: self._lbl_tree_status.setText(f"失败：{err}"))
        w.set_bom(pn, ver)
        self._start_worker(w)

    def _on_plm_tree_loaded(self, rows: list) -> None:
        """BOM 树展开完成，填充左侧 PLM 树控件。"""
        self._tree_plm.clear()
        if not rows:
            self._lbl_tree_status.setText("BOM 树为空。")
            return

        # 构建零件号 → QTreeWidgetItem 映射，以便按 parent_pn 挂载
        item_map: dict[str, QTreeWidgetItem] = {}
        root_items: list[QTreeWidgetItem] = []

        for row in rows:
            pn    = str(row.get("part_number", ""))
            ver   = str(row.get("version", ""))
            name  = str(row.get("name", ""))
            cout  = str(row.get("check_out_user", "") or "")
            depth = int(row.get("depth", 0))
            parent_pn = row.get("parent_pn")

            it = QTreeWidgetItem([f"{pn}  {name}".strip(), ver, cout])
            it.setData(0, Qt.UserRole, row)
            if cout:
                it.setForeground(2, QColor("#e74c3c"))

            item_map[pn] = it
            if depth == 0 or parent_pn not in item_map:
                root_items.append(it)
            else:
                item_map[parent_pn].addChild(it)

        for it in root_items:
            self._tree_plm.addTopLevelItem(it)
        self._tree_plm.expandAll()
        self._lbl_tree_status.setText(f"共 {len(rows)} 个节点")

    def _on_plm_tree_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        """点击 PLM 树节点 → 把该节点零件号填入本地 BOM 的快捷入口（暂留空）。"""
        pass

    def _on_pull_selected(self) -> None:
        """Pull 勾选行对应的 PLM 零件文件。"""
        base_url, login, password, workspace = self._read_conn()
        s_plm = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = s_plm.value("work_dir", "")
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", '请先在设置中配置 PLM 连接信息。')
            return
        if not work_dir:
            QMessageBox.warning(self, "未设置工作目录", '请先在设置中设置工作目录。')
            return

        # 收集勾选行中有 PLM 版本信息的行
        checked: list[tuple[str, str, str]] = []  # (pn, ver, itr)
        for i in range(self._tbl_diff.rowCount()):
            chk_w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if not chk_w:
                continue
            chk = chk_w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            pn_item  = self._tbl_diff.item(i, self._DC_PN)
            plm_item = self._tbl_diff.item(i, self._DC_PLM_V)
            if not pn_item or not plm_item:
                continue
            pn      = str(pn_item.data(Qt.UserRole) or pn_item.text()).strip()
            plm_val = plm_item.text().strip()  # 格式 "A/3"
            if not pn or not plm_val or plm_val == "—":
                continue
            parts = plm_val.split("/")
            ver = parts[0].strip() if len(parts) >= 1 else "A"
            itr = parts[1].strip() if len(parts) >= 2 else "0"
            checked.append((pn, ver, itr))

        if not checked:
            QMessageBox.information(self, "未选择", "请先勾选有 PLM 版本信息的行。")
            return

        # 使用已有的 _PullDialog 但以预填的方式打开
        # 简化实现：直接触发下载，不弹对话框
        import os as _os
        try:
            c = PlmApiClient(base_url)
            c.login(login, password)
        except Exception as exc:
            QMessageBox.critical(self, "连接失败", str(exc))
            return

        dl_items: list[tuple[str, str, str, str]] = []
        for pn, ver, itr in checked:
            try:
                files = c.list_part_attachments(workspace, pn, ver, itr)
            except Exception:
                files = []
            for fname in files:
                dl_items.append((pn, ver, itr, fname))

        if not dl_items:
            QMessageBox.information(self, "无附件", "所有勾选零件均无可下载附件。")
            return

        self._pgb.setMaximum(len(dl_items))
        self._pgb.setValue(0)
        self._pgb.setVisible(True)
        self._lbl_status.setText(f"开始下载 {len(dl_items)} 个文件……")

        w = _PullWorker(base_url, login, password, workspace)
        w.file_progress.connect(lambda fn, dl, tot, spd: self._lbl_speed.setText(f"{spd/1024:.1f} KB/s"))
        w.file_done.connect(lambda fn, dest: self._pgb.setValue(self._pgb.value() + 1))
        w.all_done.connect(lambda n: (
            self._pgb.setVisible(False),
            self._lbl_status.setText(f"Pull 完成：{n} 个文件"),
            self._lbl_speed.setText(""),
        ))
        w.failure.connect(lambda err: (
            self._pgb.setVisible(False),
            QMessageBox.critical(self, "Pull 失败", err),
        ))
        w.set_download(dl_items, work_dir)
        self._start_worker(w)

    def _init_settings_controls(self) -> None:
        """初始化设置相关控件（业务逻辑方法依赖这些 self._le_* 引用）。

        实际 UI 在 _SettingsDialog 中展示，此处创建对应的 QLineEdit 等控件
        作为数据存储，业务逻辑方法通过这些引用读写。
        """
        base_url, login, password, workspace = self._read_conn()
        s_plm = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = s_plm.value("work_dir", "")

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.Password)
        self._le_workspace = QLineEdit(workspace)
        self._le_work_dir  = QLineEdit(work_dir)
        self._btn_browse_work_dir = QPushButton("浏览…")
        self._btn_browse_work_dir.clicked.connect(self._on_browse_work_dir)

        self._lbl_ws_detail = QLabel("— 未获取 —")
        self._txt_conn_log  = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)

        # 标签规则相关
        self._tbl_plm_tags  = QTableWidget(0, 2)
        self._le_new_tag    = QLineEdit()
        self._tbl_rules     = QTableWidget(0, 3)
        self._le_rule_catia = QLineEdit()
        self._cmb_rule_tag  = QComboBox()
        self._cmb_rule_tag.setEditable(True)

        # 高级同步选项（来自 _build_advanced_options，在此占位以保证 closeEvent 中的引用有效）
        s_wb = QSettings(_S_ORG, _S_WB)
        self._rb_exist_skip   = QRadioButton()
        self._rb_exist_update = QRadioButton()
        self._rb_exist_update.setChecked(True)
        self._rb_create_yes   = QRadioButton(); self._rb_create_yes.setChecked(True)
        self._rb_create_no    = QRadioButton()
        self._rb_after_checkin = QRadioButton(); self._rb_after_checkin.setChecked(True)
        self._rb_after_keep    = QRadioButton()
        self._chk_incremental    = QCheckBox(); self._chk_incremental.setChecked(s_wb.value("chk_incremental", True, bool))
        self._chk_reg_product    = QCheckBox(); self._chk_reg_product.setChecked(s_wb.value("chk_reg_product", False, bool))
        self._chk_upload_catpart = QCheckBox(); self._chk_upload_catpart.setChecked(s_wb.value("chk_upload_catpart", False, bool))
        self._chk_upload_stp     = QCheckBox(); self._chk_upload_stp.setChecked(s_wb.value("chk_upload_stp", False, bool))
        self._chk_upload_drw_file = QCheckBox(); self._chk_upload_drw_file.setChecked(s_wb.value("chk_upload_drw_file", False, bool))
        self._chk_upload_drw_pdf  = QCheckBox(); self._chk_upload_drw_pdf.setChecked(s_wb.value("chk_upload_drw_pdf", False, bool))

        # 历史相关（_refresh_history_list 依赖）
        self._tbl_history = QTableWidget(0, 7)
        self._tbl_history.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        self._txt_hist    = QPlainTextEdit()
        self._txt_hist.setReadOnly(True)

        self._reload_rules_table()

    # ─────────────────────────────────────────────────────────────────────────
    # 连接状态栏更新
    # ─────────────────────────────────────────────────────────────────────────

    def _build_conn_status_bar(self) -> QWidget:
        """兼容旧调用，返回空 widget（工具栏已集成状态显示）。"""
        return QWidget()

    def _read_conn(self) -> tuple[str, str, str, str]:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        return (
            s.value("base_url",  _DEFAULT_BASE_URL),
            s.value("login",     _DEFAULT_LOGIN),
            s.value("password",  _DEFAULT_PASSWORD),
            s.value("workspace", _DEFAULT_WORKSPACE),
        )

    def _save_conn(self) -> None:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())
        s.setValue("workspace", self._le_workspace.text().strip())
        s.setValue("work_dir",  self._le_work_dir.text().strip())

    def _start_worker(self, worker: QThread) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def _log_to_conn(self, msg: str, level: str = "info") -> None:
        """向连接日志区追加一行带时间戳的消息。"""
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        self._txt_conn_log.appendPlainText(f"[{ts}] [{prefix}] {msg}")
        sb = self._txt_conn_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 0 — 同步（旧方法保留供调用兼容）
    # ─────────────────────────────────────────────────────────────────────────

    def _update_conn_status_bar(self) -> None:
        """更新顶部状态栏显示（从 QSettings 读取配置）。"""
        base_url, login, _pw, workspace = self._read_conn()
        if base_url and login:
            self._lbl_conn_dot.setText("🟢")
            self._lbl_conn_dot.setStyleSheet("color: green;")
            self._lbl_conn_info.setText(f"{login} @ {workspace}")
        else:
            self._lbl_conn_dot.setText("🔴")
            self._lbl_conn_dot.setStyleSheet("color: red;")
            self._lbl_conn_info.setText("未配置")

    def _build_advanced_options(self) -> QWidget:
        """构建折叠式高级同步选项区域。"""
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        self._adv_toggle_btn = QPushButton("▶ 高级选项")
        self._adv_toggle_btn.setFlat(True)
        self._adv_toggle_btn.clicked.connect(self._toggle_advanced)
        v.addWidget(self._adv_toggle_btn)

        self._adv_widget = QWidget()
        adv_layout = QVBoxLayout(self._adv_widget)
        adv_layout.setContentsMargins(8, 4, 8, 4)
        adv_layout.setSpacing(4)

        form = QFormLayout()
        form.setSpacing(5)
        form.setContentsMargins(0, 0, 0, 0)

        def _radio_row(*labels) -> tuple[list[QRadioButton], QButtonGroup, QHBoxLayout]:
            btns = [QRadioButton(lbl) for lbl in labels]
            grp  = QButtonGroup()
            row  = QHBoxLayout()
            row.setSpacing(16)
            for b in btns:
                grp.addButton(b)
                row.addWidget(b)
            row.addStretch()
            btns[0].setChecked(True)
            return btns, grp, row

        (self._rb_create_yes, self._rb_create_no), self._bg_create, row_create = \
            _radio_row("新建", "跳过")
        (self._rb_exist_checkout, self._rb_exist_skip), self._bg_exist, row_exist = \
            _radio_row("签出后更新", "跳过")
        (self._rb_other_skip, self._rb_other_force), self._bg_other, row_other = \
            _radio_row("跳过", "强制覆盖（暂不可用）")
        self._rb_other_force.setEnabled(False)
        (self._rb_after_checkin, self._rb_after_keep), self._bg_after, row_after = \
            _radio_row("自动签入", "保留签出")

        form.addRow("不存在的零件：",  row_create)
        form.addRow("已签入的零件：",  row_exist)
        form.addRow("他人已签出：",    row_other)
        form.addRow("更新后操作：",    row_after)
        adv_layout.addLayout(form)

        sep = QWidget(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: palette(mid);")
        adv_layout.addWidget(sep)

        # 复选框行一
        chk_row1 = QHBoxLayout()
        chk_row1.setSpacing(20)
        self._chk_incremental  = QCheckBox("增量同步（跳过属性无变化的零件）")
        self._chk_reg_product  = QCheckBox("注册顶层产品为产品配置（PLM Product）")
        self._chk_reg_product.setEnabled(False)
        self._chk_reg_product.setToolTip(
            "当前不可用：POST /products 接口返回 403。\n"
            "该操作要求的权限级别高于工作区管理员角色，需联系 PLM 供应商确认权限配置。"
        )
        self._chk_incremental.setChecked(True)
        chk_row1.addWidget(self._chk_incremental)
        chk_row1.addWidget(self._chk_reg_product)
        chk_row1.addStretch()
        adv_layout.addLayout(chk_row1)

        # 复选框行二
        chk_row2 = QHBoxLayout()
        chk_row2.setSpacing(16)
        self._chk_upload_catpart  = QCheckBox("上传 CATIA 文件")
        self._chk_upload_stp      = QCheckBox("上传 STP 几何文件")
        self._chk_upload_drw_file = QCheckBox("上传图纸原文件")
        self._chk_upload_drw_pdf  = QCheckBox("上传图纸 PDF")
        self._chk_upload_catpart.setChecked(True)
        self._chk_upload_stp.setChecked(True)
        self._chk_upload_drw_file.setChecked(True)
        self._chk_upload_drw_pdf.setChecked(True)
        self._chk_upload_catpart.setToolTip("将 CATPart / CATProduct 原始文件作为附件上传到 PLM")
        self._chk_upload_stp.setToolTip(
            "将 CATPart 导出为 STP 几何文件并上传；PLM 将异步转换为 OBJ 以供三维预览。"
        )
        self._chk_upload_drw_file.setToolTip(
            "将对应的 CATDrawing 原文件作为附件上传到 PLM 。\n"
            "⚠ 图纸文件定位功能待实现（TODO-01），当前找不到图纸时静默跳过。"
        )
        self._chk_upload_drw_pdf.setToolTip(
            "将对应的 CATDrawing 图纸转换为 PDF 后上传。\n"
            "⚠ 图纸文件定位功能待实现（TODO-01），当前找不到图纸时静默跳过。"
        )
        chk_row2.addWidget(self._chk_upload_catpart)
        chk_row2.addWidget(self._chk_upload_stp)
        chk_row2.addWidget(self._chk_upload_drw_file)
        chk_row2.addWidget(self._chk_upload_drw_pdf)
        chk_row2.addStretch()

        # 恢复 QSettings
        _sw = QSettings(_S_ORG, _S_WB)
        def _chk_val(key, default=True):
            v = _sw.value(key)
            return default if v is None else str(v).lower() not in ("false", "0")
        self._chk_incremental.setChecked(_chk_val("chk_incremental", True))
        self._chk_reg_product.setChecked(_chk_val("chk_reg_product", False))
        self._chk_upload_catpart.setChecked(_chk_val("chk_upload_catpart", True))
        self._chk_upload_stp.setChecked(_chk_val("chk_upload_stp", True))
        self._chk_upload_drw_file.setChecked(_chk_val("chk_upload_drw_file", True))
        self._chk_upload_drw_pdf.setChecked(_chk_val("chk_upload_drw_pdf", True))
        adv_layout.addLayout(chk_row2)

        sep2 = QWidget(); sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: palette(mid);")
        adv_layout.addWidget(sep2)

        # 预设按钮
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        lbl_preset = QLabel("预设：")
        btn_preset_new    = QPushButton("新建模式")
        btn_preset_update = QPushButton("更新模式")
        btn_preset_new.setToolTip("新建所有不存在的零件，跳过已有零件，不增量")
        btn_preset_update.setToolTip("仅更新已有零件（签出后更新），不新建，开启增量")
        btn_preset_new.clicked.connect(self._apply_preset_new)
        btn_preset_update.clicked.connect(self._apply_preset_update)
        preset_row.addWidget(lbl_preset)
        preset_row.addWidget(btn_preset_new)
        preset_row.addWidget(btn_preset_update)
        preset_row.addStretch()
        adv_layout.addLayout(preset_row)

        self._adv_widget.setVisible(False)
        v.addWidget(self._adv_widget)
        return container

    def _toggle_advanced(self) -> None:
        """切换高级选项展开/折叠状态。"""
        visible = self._adv_widget.isVisible()
        self._adv_widget.setVisible(not visible)
        self._adv_toggle_btn.setText("▼ 高级选项" if not visible else "▶ 高级选项")

    # BOM 合并表格的列索引常量
    _COL_PN        = 0   # 零件号
    _COL_NOM       = 1   # 术语（Nomenclature）
    _COL_LOC_VER   = 2   # 本地 PLM_Version
    _COL_LOC_ITER  = 3   # 本地 PLM_Iteration
    _COL_STATUS    = 4   # 状态：?/New/OK/Push/!
    _COL_PLM_VER   = 5   # PLM 版本
    _COL_PLM_ITER  = 6   # PLM 迭代号
    _COL_PLM_USER  = 7   # PLM 签出人
    _COL_PUSH      = 8   # 推送? (checkbox)
    _COL_UPGRADE   = 9   # 升级方式（下拉：不推送/+迭代/+版本）
    _BOM_COL_HEADERS = ["零件号", "术语", "本地版本", "本地迭代", "状态", "PLM版本", "PLM迭代", "签出人", "推送?", "升级方式"]
    _UPGRADE_SKIP   = "不推送"
    _UPGRADE_ITER   = "+迭代"
    _UPGRADE_VER    = "+版本"

    def _build_history_panel(self) -> QWidget:
        """构建同步 Tab 底部的历史记录折叠面板。"""
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        self._hist_toggle_btn = QPushButton("▶ 同步历史")
        self._hist_toggle_btn.setFlat(True)
        self._hist_toggle_btn.clicked.connect(self._toggle_history_panel)
        v.addWidget(self._hist_toggle_btn)

        self._hist_widget = QWidget()
        self._hist_widget.setFixedHeight(180)
        h_layout = QHBoxLayout(self._hist_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        # 左：历史列表
        self._tbl_history = QTableWidget(0, 7)
        self._tbl_history.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        _hdr_hist = self._tbl_history.horizontalHeader()
        _hdr_hist.setStretchLastSection(True)
        for i, w in enumerate([140, 45, 45, 45, 45, 100]):
            _hdr_hist.setSectionResizeMode(i, QHeaderView.Interactive)
            _hdr_hist.resizeSection(i, w)
        _hdr_hist.setSectionResizeMode(6, QHeaderView.Stretch)
        self._tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_history.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl_history.currentItemChanged.connect(self._on_history_selected)
        h_layout.addWidget(self._tbl_history, 3)

        # 右：详情文本 + 清空按钮
        right_v = QVBoxLayout()
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(4)
        self._txt_hist = QPlainTextEdit()
        self._txt_hist.setReadOnly(True)
        self._txt_hist.setObjectName("logView")
        self._txt_hist.setPlaceholderText("— 点击左侧记录查看详情 —")
        right_v.addWidget(self._txt_hist, 1)
        btn_clear = QPushButton("清空历史")
        btn_clear.setFixedHeight(24)
        btn_clear.clicked.connect(self._on_clear_history)
        right_v.addWidget(btn_clear)
        h_layout.addLayout(right_v, 2)

        self._hist_widget.setVisible(False)
        v.addWidget(self._hist_widget)
        return container

    def _toggle_history_panel(self) -> None:
        visible = self._hist_widget.isVisible()
        self._hist_widget.setVisible(not visible)
        self._hist_toggle_btn.setText("▼ 同步历史" if not visible else "▶ 同步历史")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1 — 设置
    # ─────────────────────────────────────────────────────────────────────────

    def _build_settings_tab(self) -> QWidget:
        """构建设置 Tab：垂直滚动页，QGroupBox 堆叠。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        base_url, login, password, workspace = self._read_conn()
        s_plm = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = s_plm.value("work_dir", "")

        # ── 连接配置 ──────────────────────────────────────────────────────────
        grp_cfg = QGroupBox("连接配置")
        form = QFormLayout(grp_cfg)
        form.setSpacing(6)

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.Password)
        self._le_workspace = QLineEdit(workspace)
        self._le_base_url.setPlaceholderText("http://127.0.0.1:8001/docdoku-plm-server-rest/api")

        # 工作目录（Pull 下载到此处）
        work_dir_row = QHBoxLayout()
        self._le_work_dir = QLineEdit(work_dir)
        self._le_work_dir.setPlaceholderText("Pull 下载文件保存目录…")
        self._btn_browse_work_dir = QPushButton("浏览…")
        self._btn_browse_work_dir.setFixedWidth(60)
        self._btn_browse_work_dir.clicked.connect(self._on_browse_work_dir)
        work_dir_row.addWidget(self._le_work_dir)
        work_dir_row.addWidget(self._btn_browse_work_dir)

        form.addRow("服务端地址：", self._le_base_url)
        form.addRow("用户名：",     self._le_login)
        form.addRow("密码：",       self._le_password)
        form.addRow("工作区：",     self._le_workspace)
        form.addRow("工作目录：",   work_dir_row)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_test = QPushButton("测试连接")
        btn_save.clicked.connect(self._on_save_conn)
        btn_test.clicked.connect(self._on_test_conn)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        form.addRow("", btn_row)
        layout.addWidget(grp_cfg)

        # ── 工作区详情 ────────────────────────────────────────────────────────
        grp_ws = QGroupBox("工作区详情")
        v_ws = QVBoxLayout(grp_ws)
        v_ws.setSpacing(4)
        self._lbl_ws_detail = QLabel("— 未获取 —")
        self._lbl_ws_detail.setWordWrap(True)
        v_ws.addWidget(self._lbl_ws_detail)
        layout.addWidget(grp_ws)

        # ── 标签规则（可折叠） ────────────────────────────────────────────────
        grp_rules_outer = QWidget()
        v_ro = QVBoxLayout(grp_rules_outer)
        v_ro.setContentsMargins(0, 0, 0, 0)
        v_ro.setSpacing(2)
        self._rules_toggle_btn = QPushButton("▶ 标签规则")
        self._rules_toggle_btn.setFlat(True)
        self._rules_toggle_btn.clicked.connect(self._toggle_rules_panel)
        v_ro.addWidget(self._rules_toggle_btn)

        self._rules_widget = QGroupBox()
        v_rw = QVBoxLayout(self._rules_widget)
        v_rw.setSpacing(6)

        # 工作区现有标签子区
        lbl_tags_title = QLabel("工作区标签：")
        v_rw.addWidget(lbl_tags_title)

        self._tbl_plm_tags = QTableWidget(0, 2)
        self._tbl_plm_tags.setHorizontalHeaderLabels(["标签名称", "ID"])
        _hdr_tags = self._tbl_plm_tags.horizontalHeader()
        _hdr_tags.setSectionResizeMode(0, QHeaderView.Interactive)
        _hdr_tags.resizeSection(0, 180)
        _hdr_tags.setStretchLastSection(True)
        self._tbl_plm_tags.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_plm_tags.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_plm_tags.setMinimumHeight(100)
        self._tbl_plm_tags.setMaximumHeight(160)
        v_rw.addWidget(self._tbl_plm_tags)

        tag_op_row = QHBoxLayout()
        tag_op_row.setSpacing(8)
        btn_refresh = QPushButton("刷新标签列表")
        btn_refresh.clicked.connect(self._on_refresh_tags)
        self._le_new_tag = QLineEdit()
        self._le_new_tag.setPlaceholderText("输入新标签名称…")
        btn_create_tag = QPushButton("新建标签")
        btn_create_tag.clicked.connect(self._on_create_tag)
        tag_op_row.addWidget(btn_refresh)
        tag_op_row.addStretch()
        tag_op_row.addWidget(self._le_new_tag)
        tag_op_row.addWidget(btn_create_tag)
        v_rw.addLayout(tag_op_row)

        # 自动映射规则子区
        sep = QWidget(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: palette(mid);")
        v_rw.addWidget(sep)
        v_rw.addWidget(QLabel('自动打标签规则（Checkin 后按"设计状态"属性值自动打 Tag）：'))

        self._tbl_rules = QTableWidget(0, 3)
        self._tbl_rules.setHorizontalHeaderLabels(["CATIA 属性值", "PLM 标签", "操作"])
        _hdr_rules = self._tbl_rules.horizontalHeader()
        _hdr_rules.setSectionResizeMode(0, QHeaderView.Interactive)
        _hdr_rules.setSectionResizeMode(1, QHeaderView.Stretch)
        _hdr_rules.setSectionResizeMode(2, QHeaderView.Fixed)
        _hdr_rules.resizeSection(0, 160)
        _hdr_rules.resizeSection(2, 60)
        _hdr_rules.setStretchLastSection(False)
        self._tbl_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_rules.setMinimumHeight(100)
        self._tbl_rules.setMaximumHeight(160)
        v_rw.addWidget(self._tbl_rules)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._le_rule_catia = QLineEdit()
        self._le_rule_catia.setPlaceholderText('CATIA"设计状态"属性值，如：发布')
        self._cmb_rule_tag  = QComboBox()
        self._cmb_rule_tag.setEditable(True)
        self._cmb_rule_tag.setPlaceholderText("PLM Tag（可手填或从列表选）")
        btn_add_rule = QPushButton("添加规则")
        btn_add_rule.clicked.connect(self._on_add_rule)
        add_row.addWidget(self._le_rule_catia, 2)
        add_row.addWidget(self._cmb_rule_tag, 2)
        add_row.addWidget(btn_add_rule)
        v_rw.addLayout(add_row)

        self._rules_widget.setVisible(False)
        v_ro.addWidget(self._rules_widget)
        layout.addWidget(grp_rules_outer)

        # ── 连接日志 ──────────────────────────────────────────────────────────
        grp_log = QGroupBox("连接日志")
        v_log = QVBoxLayout(grp_log)
        v_log.setSpacing(4)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setObjectName("logView")
        self._txt_conn_log.setFixedHeight(150)
        self._txt_conn_log.setPlaceholderText('— 尚未连接，点击"测试连接"验证配置 —')
        v_log.addWidget(self._txt_conn_log)
        layout.addWidget(grp_log)

        layout.addStretch()
        self._reload_rules_table()
        scroll.setWidget(page)
        return scroll

    def _toggle_rules_panel(self) -> None:
        visible = self._rules_widget.isVisible()
        self._rules_widget.setVisible(not visible)
        self._rules_toggle_btn.setText("▼ 标签规则" if not visible else "▶ 标签规则")

    def _on_browse_work_dir(self) -> None:
        """弹出文件夹选择对话框，更新工作目录输入框。"""
        current = self._le_work_dir.text().strip() or ""
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", current)
        if path:
            self._le_work_dir.setText(path)

    # ─────────────────────────────────────────────────────────────────────────
    # 连接 Tab 事件处理
    # ─────────────────────────────────────────────────────────────────────────

    def _on_save_conn(self):
        self._save_conn()
        self._log_to_conn("配置已保存", "info")
        self._update_conn_status_bar()

    def _on_test_conn(self):
        self._save_conn()
        base_url, login, password, workspace = self._read_conn()
        self._log_to_conn(f"正在连接 {base_url} …", "info")
        self._lbl_ws_detail.setText("— 获取中 —")
        w = _ConnectWorker(base_url, login, password, workspace)
        w.success.connect(self._on_conn_ok)
        w.failure.connect(self._on_conn_fail)
        self._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict):
        _, _, _, ws = self._read_conn()
        self._log_to_conn(
            f"连接成功  用户：{login_name}  工作区：{ws}  成员数：{len(users)}", "ok"
        )
        desc    = ws_info.get("description") or "—"
        role    = ws_info.get("_current_user_role") or "—"
        enabled = "是" if ws_info.get("enabled", True) else "否"
        self._lbl_ws_detail.setText(
            f"工作区：{ws}    描述：{desc}    我的身份：{role}    已启用：{enabled}"
        )
        self._update_conn_status_bar()

    def _on_conn_fail(self, err: str):
        self._log_to_conn(f"连接失败：{err}", "error")
        self._lbl_ws_detail.setText("— 连接失败 —")

    # ─────────────────────────────────────────────────────────────────────────
    # PLM 状态查询
    # ─────────────────────────────────────────────────────────────────────────

    def _on_refresh_plm_status(self) -> None:
        """触发 PLM 状态查询 Worker（按本地 BOM 零件号精确查询）。"""
        base_url, login, password, workspace = self._read_conn()
        if not self._visible_bom_rows:
            self._lbl_plm_query_status.setText("请先加载 BOM")
            return
        # 取本地 BOM 中所有零件号（去重，保序）
        pns: list[str] = []
        seen: set[str] = set()
        for row in self._visible_bom_rows:
            pn = str(row.get("Part Number", "")).strip()
            if pn and pn not in seen:
                pns.append(pn)
                seen.add(pn)
        self._btn_refresh_plm.setEnabled(False)
        self._lbl_plm_query_status.setText(f"查询中…… (0/{len(pns)})")
        w = _PlmStatusWorker(base_url, login, password, workspace, pns)
        w.success.connect(self._on_plm_status_loaded)
        w.failure.connect(self._on_plm_status_error)
        w.progress.connect(
            lambda done, total: self._lbl_plm_query_status.setText(f"查询中…… ({done}/{total})")
        )
        self._start_worker(w)

    def _on_plm_status_loaded(self, parts: list) -> None:
        """PLM 状态查询完成：缓存结果，更新合并表格中的 PLM 相关列。"""
        self._btn_refresh_plm.setEnabled(True)
        self._plm_parts_cache = {p.get("number", ""): p for p in parts}
        self._lbl_plm_query_status.setText(f"已查询 {len(parts)} 个零件")

        self._update_status_col()

        # 有 BOM 数据且无异常时才启用 Push 按钮
        visible = self._visible_bom_rows
        has_errors = any(
            r.get("_no_file") or r.get("_not_found") or r.get("_unreadable")
            for r in visible
        )
        if visible and not has_errors:
            self._btn_push.setEnabled(True)

    def _on_plm_status_error(self, err: str) -> None:
        """PLM 查询失败时恢复按钮并显示错误。"""
        self._btn_refresh_plm.setEnabled(True)
        self._lbl_plm_query_status.setText(f"查询失败：{err}")

    def _update_arrow_column(self) -> None:
        """兼容旧调用，转发给 _update_status_col。"""
        self._update_status_col()

    def _update_status_col(self) -> None:
        """根据本地 BOM 行和 PLM 缓存，更新合并表格中的状态列和 PLM 相关列。

        状态枚举：
          ?    — PLM 状态未查询（灰色）
          New  — PLM 中不存在该零件（绿色，默认勾选推送）
          OK   — 本地版本与 PLM 一致（绿色，默认不勾推送）
          Push — 本地版本比 PLM 新（橙色，默认勾选推送）
          !    — PLM 版本比本地新，落后（红色，禁止推送）
        """
        for i, row in enumerate(self._visible_bom_rows):
            if i >= self._tbl_bom.rowCount():
                break
            pn       = str(row.get("Part Number", ""))
            plm      = self._plm_parts_cache.get(pn) if self._plm_parts_cache else None

            if plm is None and not self._plm_parts_cache:
                status = self._ST_UNKNOWN
                color  = QColor(self._STATUS_COLORS[self._ST_UNKNOWN])
                default_push = False
                plm_val  = ""
                plm_user = ""
                push_enabled = True
            elif plm is None:
                status = self._ST_NEW_LOC
                color  = QColor(self._STATUS_COLORS[self._ST_NEW_LOC])
                default_push = True
                plm_val  = "—"
                plm_user = ""
                push_enabled = True
            else:
                plm_ver  = str(plm.get("version", "") or "")
                plm_iter = str(plm.get("lastIterationNumber", "") or "")
                plm_user = str(plm.get("checkOutUser", "") or "")
                plm_val  = f"{plm_ver}/{plm_iter}" if plm_ver else ""
                local_ver = str(row.get("PLM_Version", "") or "")

                if local_ver and plm_ver:
                    if local_ver == plm_ver:
                        status = self._ST_OK
                        color  = QColor(self._STATUS_COLORS[self._ST_OK])
                        default_push = False
                        push_enabled = True
                    elif local_ver > plm_ver:
                        status = self._ST_PUSH
                        color  = QColor(self._STATUS_COLORS[self._ST_PUSH])
                        default_push = True
                        push_enabled = True
                    else:
                        status = self._ST_PULL
                        color  = QColor(self._STATUS_COLORS[self._ST_PULL])
                        default_push = False
                        push_enabled = False
                else:
                    status = self._ST_OK
                    color  = QColor(self._STATUS_COLORS[self._ST_OK])
                    default_push = False
                    push_enabled = True

            # 写入状态列
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(color)
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            self._tbl_bom.setItem(i, self._DC_STATUS, status_item)

            # 写入 PLM 版本/迭代合并列
            plm_item = QTableWidgetItem(plm_val)
            plm_item.setFlags(plm_item.flags() & ~Qt.ItemIsEditable)
            self._tbl_bom.setItem(i, self._DC_PLM_V, plm_item)

            # 写入签出人
            cout_item = QTableWidgetItem(plm_user)
            cout_item.setFlags(cout_item.flags() & ~Qt.ItemIsEditable)
            if plm_user:
                cout_item.setForeground(QColor("#e74c3c"))
            self._tbl_bom.setItem(i, self._DC_COUT, cout_item)

            # Push 按钮使能
            self._btn_push.setEnabled(True)
            self._btn_pull_sel.setEnabled(True)

            # 写入选择 checkbox
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(default_push)
            chk.setEnabled(push_enabled)
            chk_layout.addWidget(chk)
            self._tbl_bom.setCellWidget(i, self._DC_SEL, chk_widget)

    # ─────────────────────────────────────────────────────────────────────────
    # BOM 加载（本地）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_load_preview(self) -> None:
        self._btn_load_bom.setEnabled(False)
        self._btn_push.setEnabled(False)
        self._lbl_node_count.setText("正在加载……")
        self._preview_tree.clear()
        self._bom_rows = []
        self._sync_result_map.clear()

        self._load_progress_dlg = QProgressDialog("正在从 CATIA 读取 BOM……", None, 0, 0, self)
        self._load_progress_dlg.setWindowTitle("加载 BOM")
        self._load_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._load_progress_dlg.setMinimumDuration(300)
        self._load_progress_dlg.setValue(0)

        w = _BomPreviewWorker()
        w.progress.connect(self._on_load_bom_progress)
        w.success.connect(self._on_preview_loaded)
        w.failure.connect(self._on_preview_fail)
        self._start_worker(w)

    def _on_load_bom_progress(self, count: int) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.setLabelText(f"正在从 CATIA 读取 BOM……  已读取 {count} 个节点")
            dlg.repaint()

    def _on_preview_loaded(self, rows: list) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.close()
            self._load_progress_dlg = None
        self._btn_load_bom.setEnabled(True)
        self._bom_rows = rows
        n = len(rows)

        bad_unsaved    = [r for r in rows if r.get("_no_file")]
        bad_not_found  = [r for r in rows if r.get("_not_found")]
        bad_unreadable = [r for r in rows if r.get("_unreadable")]

        if bad_unsaved or bad_not_found or bad_unreadable:
            problems = []
            if bad_unsaved:
                problems.append(f"{len(bad_unsaved)} 个零件未保存到磁盘")
            if bad_not_found:
                problems.append(f"{len(bad_not_found)} 个零件文件断链/找不到")
            if bad_unreadable:
                problems.append(f"{len(bad_unreadable)} 个零件处于轻量化状态")
            self._lbl_node_count.setText(
                f"共 {n} 个节点 — 存在异常，禁止同步：" + "；".join(problems)
            )
            self._lbl_node_count.setStyleSheet("color: red;")
            self._btn_push.setEnabled(False)
        elif n > PLM_SYNC_MAX_NODES:
            self._lbl_node_count.setText(
                f"共 {n} 个节点（超出上限 {PLM_SYNC_MAX_NODES}，禁止同步）"
            )
            self._lbl_node_count.setStyleSheet("color: red;")
            self._btn_push.setEnabled(False)
        else:
            self._lbl_node_count.setText(f"共 {n} 个节点（上限 {PLM_SYNC_MAX_NODES}）")
            self._lbl_node_count.setStyleSheet("")
            self._btn_push.setEnabled(True)

        self._populate_preview_tree(rows)
        self._populate_local_table(rows)
        self._update_status_col()

    def _on_preview_fail(self, err: str) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.close()
            self._load_progress_dlg = None
        self._btn_load_bom.setEnabled(True)
        self._lbl_node_count.setText(f"加载失败：{err}")
        self._lbl_node_count.setStyleSheet("color: red;")

    def _populate_local_table(self, rows: list) -> None:
        """将 BOM 行填充到差异对比表（本地列），包含根产品（Level=0）。"""
        visible = rows
        self._visible_bom_rows = visible
        self._tbl_bom.setRowCount(len(visible))
        for i, row in enumerate(visible):
            pn       = str(row.get("Part Number", ""))
            nom      = str(row.get("Nomenclature", ""))
            depth    = int(row.get("Level", 0))
            loc_ver  = str(row.get("PLM_Version", "") or "")
            loc_iter = str(row.get("PLM_Iteration", "") or "")
            loc_val  = f"{loc_ver}/{loc_iter}" if loc_ver else ""

            # 层级缩进文本
            indent = "  " * depth

            def _item(val: str, user_data=None) -> QTableWidgetItem:
                it = QTableWidgetItem(val)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if user_data is not None:
                    it.setData(Qt.UserRole, user_data)
                return it

            self._tbl_bom.setItem(i, self._DC_DEPTH,  _item(str(depth)))
            self._tbl_bom.setItem(i, self._DC_PN,     _item(f"{indent}{pn}", pn))
            self._tbl_bom.setItem(i, self._DC_NOM,    _item(nom))
            self._tbl_bom.setItem(i, self._DC_STATUS, _item(self._ST_UNKNOWN))
            self._tbl_bom.item(i, self._DC_STATUS).setTextAlignment(Qt.AlignCenter)
            self._tbl_bom.item(i, self._DC_STATUS).setForeground(QColor(self._STATUS_COLORS[self._ST_UNKNOWN]))
            self._tbl_bom.setItem(i, self._DC_LOC_V,  _item(loc_val))
            self._tbl_bom.setItem(i, self._DC_PLM_V,  _item(""))
            self._tbl_bom.setItem(i, self._DC_COUT,   _item(""))
            # 选择 checkbox 初始化（未查询 PLM 前禁用）
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(False)
            chk.setEnabled(False)
            chk_layout.addWidget(chk)
            self._tbl_bom.setCellWidget(i, self._DC_SEL, chk_widget)

    def _sync_table_row_heights(self) -> None:
        """兼容旧调用，现单表格无需同步行高。"""
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # 列可见性（内部 BOM 树使用，UI 不显示）
    # ─────────────────────────────────────────────────────────────────────────

    def _preview_visible_cols(self) -> list[str]:
        s = QSettings(_S_ORG, _S_WB)
        saved = s.value("preview_optional_cols", _PREVIEW_DEFAULT_COLS)
        if isinstance(saved, str):
            saved = [saved]
        all_optional = (
            set(BOM_EDIT_COLUMN_ORDER) | set(PRESET_USER_REF_PROPERTIES)
        ) - set(_PREVIEW_FIXED_COLS)
        optional = [c for c in saved if c in all_optional]
        order = BOM_EDIT_COLUMN_ORDER + [
            c for c in PRESET_USER_REF_PROPERTIES if c not in BOM_EDIT_COLUMN_ORDER
        ]
        result: list[str] = []
        for c in order:
            if c in _PREVIEW_FIXED_COLS or c in optional:
                result.append(c)
        for c in optional:
            if c not in result:
                result.append(c)
        for i, col in enumerate(_SYNC_COLS_ORDERED):
            result.insert(1 + i, col)
        return result

    def _save_preview_cols(self, optional_cols: list[str]) -> None:
        QSettings(_S_ORG, _S_WB).setValue("preview_optional_cols", optional_cols)

    def _build_col_visibility_row(self) -> None:
        """构建列可见性控件（不显示在 UI，仅供内部树使用）。"""
        for layout in (self._col_vis_row0, self._col_vis_row1):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        self._col_checkboxes.clear()

        s = QSettings(_S_ORG, _S_WB)
        saved = s.value("preview_optional_cols", _PREVIEW_DEFAULT_COLS)
        if isinstance(saved, str):
            saved = [saved]
        visible_optional = set(saved)

        fn_cb = QCheckBox(BOM_COLUMN_DISPLAY_NAMES.get("Filename", "Filename"))
        fn_cb.setChecked("Filename" in visible_optional)
        fn_cb.toggled.connect(self._on_col_vis_changed)
        fn_cb.setProperty("col_name", "Filename")
        self._col_checkboxes["Filename"] = fn_cb

        for col in BOM_HIDEABLE_COLUMNS:
            cb = QCheckBox(BOM_COLUMN_DISPLAY_NAMES.get(col, col))
            cb.setChecked(col in visible_optional)
            cb.toggled.connect(self._on_col_vis_changed)
            cb.setProperty("col_name", col)
            self._col_checkboxes[col] = cb

        for col in PRESET_USER_REF_PROPERTIES:
            if col in self._col_checkboxes:
                continue
            cb = QCheckBox(BOM_COLUMN_DISPLAY_NAMES.get(col, col))
            cb.setChecked(col in visible_optional)
            cb.toggled.connect(self._on_col_vis_changed)
            cb.setProperty("col_name", col)
            self._col_checkboxes[col] = cb

    def _on_col_vis_changed(self) -> None:
        optional = [col for col, cb in self._col_checkboxes.items() if cb.isChecked()]
        self._save_preview_cols(optional)
        if self._bom_rows:
            self._populate_preview_tree(self._bom_rows)

    def _populate_preview_tree(self, rows: list) -> None:
        """将 BOM 行数据填充到内部隐藏预览树控件（供同步结果追踪使用）。"""
        vis_cols = self._preview_visible_cols()
        self._preview_tree.clear()
        self._preview_tree.setColumnCount(len(vis_cols))
        headers = [_SYNC_COL_DISPLAY.get(c, BOM_COLUMN_DISPLAY_NAMES.get(c, c)) for c in vis_cols]
        self._preview_tree.setHeaderLabels(headers)
        self._preview_tree.setRootIsDecorated(True)

        has_sync = bool(self._sync_result_map)
        sync_src_idx = vis_cols.index(_SYNC_COL_SOURCE)  if _SYNC_COL_SOURCE  in vis_cols else -1
        sync_upd_idx = vis_cols.index(_SYNC_COL_UPDATE)  if _SYNC_COL_UPDATE  in vis_cols else -1
        sync_chk_idx = vis_cols.index(_SYNC_COL_CHECKIN) if _SYNC_COL_CHECKIN in vis_cols else -1

        parent_stack: list[tuple[int, QTreeWidgetItem | None]] = [(-1, None)]

        for row in rows:
            level = int(row.get("Level", 0))
            while len(parent_stack) > 1 and parent_stack[-1][0] >= level:
                parent_stack.pop()

            parent_item = parent_stack[-1][1]
            item = QTreeWidgetItem()
            if parent_item is None:
                self._preview_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            parent_stack.append((level, item))

            for col_idx, col_name in enumerate(vis_cols):
                if col_name in (_SYNC_COL_SOURCE, _SYNC_COL_UPDATE, _SYNC_COL_CHECKIN):
                    continue
                val = str(row.get(col_name, ""))
                item.setText(col_idx, val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            pn = str(row.get("Part Number", ""))
            if has_sync and pn:
                src, upd, chk = self._sync_result_map.get(pn, ("", "", ""))
            else:
                src, upd, chk = "", "", ""
            color = _sync_row_color(src, upd, chk)
            for col_idx, text in [
                (sync_src_idx, src  or "—"),
                (sync_upd_idx, upd  or "—"),
                (sync_chk_idx, chk  or "—"),
            ]:
                if col_idx < 0:
                    continue
                item.setText(col_idx, text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if color:
                    item.setForeground(col_idx, color)

        self._preview_tree.expandAll()
        for i in range(len(vis_cols)):
            self._preview_tree.resizeColumnToContents(i)

    # ─────────────────────────────────────────────────────────────────────────
    # 同步选项预设
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_preset_new(self) -> None:
        self._rb_create_yes.setChecked(True)
        self._rb_exist_skip.setChecked(True)
        self._rb_other_skip.setChecked(True)
        self._rb_after_checkin.setChecked(True)
        self._chk_incremental.setChecked(False)

    def _apply_preset_update(self) -> None:
        self._rb_create_no.setChecked(True)
        self._rb_exist_checkout.setChecked(True)
        self._rb_other_skip.setChecked(True)
        self._rb_after_checkin.setChecked(True)
        self._chk_incremental.setChecked(True)

    def _build_sync_options(self):
        return SyncOptions(
            existing_part_policy=(
                ExistingPartPolicy.SKIP
                if self._rb_exist_skip.isChecked()
                else ExistingPartPolicy.CHECKOUT_UPDATE
            ),
            create_new_parts=self._rb_create_yes.isChecked(),
            own_checked_out_policy=OwnCheckedOutPolicy.UPDATE,
            other_checked_out_policy=CheckedOutByOtherPolicy.SKIP,
            after_update_policy=(
                AfterUpdatePolicy.KEEP_CHECKOUT
                if self._rb_after_keep.isChecked()
                else AfterUpdatePolicy.CHECKIN
            ),
            incremental=self._chk_incremental.isChecked(),
            upload_catpart_file=self._chk_upload_catpart.isChecked(),
            upload_step_file=self._chk_upload_stp.isChecked(),
            upload_drawing_pdf=self._chk_upload_drw_pdf.isChecked(),
            upload_drawing_file=self._chk_upload_drw_file.isChecked(),
            register_product=self._chk_reg_product.isChecked(),
            tag_rules=self._load_tag_rules(),
        )

    def _detect_sync_mode(self) -> str:
        create = self._rb_create_yes.isChecked()
        update = self._rb_exist_checkout.isChecked()
        incremental = self._chk_incremental.isChecked()
        if create and not update and not incremental:
            return "新建模式"
        if not create and update and incremental:
            return "更新模式"
        return "自定义模式"

    # ─────────────────────────────────────────────────────────────────────────
    # 同步执行
    # ─────────────────────────────────────────────────────────────────────────

    def _on_sync_start(self) -> None:
        if not self._bom_rows:
            QMessageBox.warning(self, "无 BOM 数据", '请先点击"↺ 加载 BOM"。')
            return
        if len(self._bom_rows) > PLM_SYNC_MAX_NODES:
            QMessageBox.critical(
                self, "节点数超限",
                f"当前 BOM 共 {len(self._bom_rows)} 个节点，"
                f"超出最大限制 {PLM_SYNC_MAX_NODES}，无法同步。",
            )
            return

        base_url, login, password, workspace = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", '请先在"设置"页配置并保存 PLM 连接信息。')
            return

        component_rows = [
            r for r in self._bom_rows
            if r.get("Type") == BomNodeType.COMPONENT
        ]
        if component_rows:
            names = "、".join(
                str(r.get("Part Number") or r.get("Filename") or "?")
                for r in component_rows[:5]
            )
            if len(component_rows) > 5:
                names += f" 等共 {len(component_rows)} 个"
            QMessageBox.critical(
                self, "BOM 包含部件，无法同步",
                f"当前 BOM 包含以下\u201c部件\u201d节点，无法同步：\n\n{names}\n\n"
                "部件是 CATIA 的嵌入式子装配，没有独立文件，不对应 PLM 零件实体。\n"
                "请在 CATIA 中将其转换为独立产品（CATProduct）后重新读取 BOM 。",
            )
            return

        try:
            unsaved = check_unsaved_docs(self._bom_rows)
        except Exception as exc:
            logger.warning(f"未保存文档检查失败，跳过：{exc}")
            unsaved = []

        if unsaved:
            # 强制阻止：所有未保存状态一律不允许继续同步
            dlg = QDialog(self)
            dlg.setWindowTitle("存在未保存的文档 — 同步已阻止")
            dlg.setMinimumWidth(500)
            vbox = QVBoxLayout(dlg)
            warn_lbl = QLabel(
                "以下 CATIA 文档存在未保存问题，同步已阻止：\n\n"
                "  • 从未保存到磁盘：该零件的属性与几何体完全无法上传\n"
                "  • 有未提交修改：上传内容与当前编辑状态不一致\n\n"
                "请切换到 CATIA，保存所有文件后重新点击同步。",
                dlg,
            )
            warn_lbl.setWordWrap(True)
            vbox.addWidget(warn_lbl)
            lst = QListWidget(dlg)
            for entry in unsaved:
                lst.addItem(entry)
            lst.setMaximumHeight(120)
            vbox.addWidget(lst)
            btns = QDialogButtonBox(QDialogButtonBox.Ok, dlg)
            btns.accepted.connect(dlg.accept)
            vbox.addWidget(btns)
            dlg.exec()
            return  # 无论用户点什么，强制返回

        options = self._build_sync_options()

        # 从表格读取用户选择的推送行（选择 checkbox 列）
        push_map: dict[str, str] = {}  # {pn: "+迭代"}
        for i, row in enumerate(self._visible_bom_rows):
            chk_widget = self._tbl_bom.cellWidget(i, self._DC_SEL)
            if chk_widget is None:
                continue
            chk = chk_widget.findChild(QCheckBox)
            if chk is None or not chk.isChecked():
                continue
            pn = str(row.get("Part Number", "")).strip()
            if pn:
                push_map[pn] = self._UPGRADE_ITER  # 默认 +迭代

        if not push_map:
            QMessageBox.information(
                self, "未选择推送零件",
                '没有勾选任何零件。\n\n请在表格中勾选"推送?"列，或先点"☁ 查询 PLM 状态"再勾选。',
            )
            return

        # 将勾选列表注入 SyncOptions，sync.py 的 _sync_node 会按此过滤
        options.part_upgrade_map = push_map

        self._btn_sync_start.setEnabled(False)
        self._btn_load_preview.setEnabled(False)

        total_nodes = len(push_map)
        self._pgb_sync.setMaximum(max(total_nodes, 1))
        self._pgb_sync.setValue(0)
        self._pgb_sync.setVisible(True)
        self._sync_total_nodes = total_nodes
        self._sync_done_nodes  = 0
        self._sync_seen_pns    = set()   # 每次 Push 重置，防止第二次进度不推进

        self._lbl_sync_status.setText(f"正在同步…… (0 / {total_nodes})")
        self._sync_result_map.clear()
        self._lbl_sync_summary.setText("")

        self._last_sync_login = login
        self._last_sync_mode  = self._detect_sync_mode()

        w = _SyncWorker(base_url, login, password, workspace, options)
        w.progress.connect(self._on_sync_progress)
        w.upload_log.connect(self._on_upload_log)
        w.sync_done.connect(self._on_sync_done)
        w.error.connect(self._on_sync_error)
        self._start_worker(w)

    def _on_sync_progress(self, msg: str) -> None:
        """解析 sync.py 的结构化日志行，更新状态标签和 _sync_result_map。"""
        import re as _re
        stripped = msg.strip()

        if stripped.replace("-", "").replace(" ", "") == "":
            return

        # 解析上传速度（格式：xx.x KB/s 或 xxx KB/s）
        _speed_m = _re.search(r'(\d+(?:\.\d+)?)\s*KB/s', stripped, _re.IGNORECASE)
        if _speed_m:
            self._lbl_upload_speed.setText(f"{_speed_m.group(1)} KB/s")

        extracted_lbl: str | None = None
        is_terminal = False

        if stripped.startswith(">>"):
            inner = stripped[2:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                self._update_sync_result(lbl, reason, "", "")
                extracted_lbl = lbl
                is_terminal = True
        elif stripped.startswith("[X]"):
            inner = stripped[3:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                self._update_sync_result(lbl, reason, "", "")
                extracted_lbl = lbl
                is_terminal = True
        elif " | " in stripped:
            parts = [p.strip() for p in stripped.split(" | ")]
            if len(parts) >= 4:
                col1 = parts[0]
                col2 = parts[1]
                col3 = parts[2]
                lbl  = parts[-1]
                if col1 not in ("签出来源",):
                    extracted_lbl = lbl
                    if col3:
                        self._update_sync_result(lbl, col1, col2, col3)
                        is_terminal = True
                    else:
                        pn = lbl.split("<")[0].strip()
                        existing = self._sync_result_map.get(pn, ("", "", ""))
                        self._sync_result_map[pn] = (existing[0] or col1, col2, existing[2])
                        self._refresh_sync_cols_in_tree(
                            pn, existing[0] or col1, col2, existing[2],
                        )

        total = getattr(self, "_sync_total_nodes", 0)
        if extracted_lbl:
            pn = extracted_lbl.split("<")[0].strip()
            seen = getattr(self, "_sync_seen_pns", set())
            if pn not in seen:
                seen.add(pn)
                self._sync_seen_pns = seen
                self._sync_done_nodes = getattr(self, "_sync_done_nodes", 0) + 1
                self._pgb_sync.setValue(min(self._sync_done_nodes, total))

        done = getattr(self, "_sync_done_nodes", 0)
        if done or is_terminal:
            self._lbl_sync_status.setText(
                f"正在同步…… ({done} / {total})  {stripped[:60]}"
            )
        else:
            self._lbl_sync_status.setText(stripped)

    def _update_sync_result(self, lbl: str, source: str, update: str, checkin: str) -> None:
        pn = lbl.split("<")[0].strip()
        self._sync_result_map[pn] = (source, update, checkin)
        self._refresh_sync_cols_in_tree(pn, source, update, checkin)

    def _refresh_sync_cols_in_tree(self, pn: str, source: str, update: str, checkin: str) -> None:
        """在隐藏的预览树中更新同步结果列（供内部追踪使用）。"""
        vis_cols = self._preview_visible_cols()
        if self._preview_tree.columnCount() != len(vis_cols):
            self._populate_preview_tree(self._bom_rows)
            return

        sync_src_idx = vis_cols.index(_SYNC_COL_SOURCE)  if _SYNC_COL_SOURCE  in vis_cols else -1
        sync_upd_idx = vis_cols.index(_SYNC_COL_UPDATE)  if _SYNC_COL_UPDATE  in vis_cols else -1
        sync_chk_idx = vis_cols.index(_SYNC_COL_CHECKIN) if _SYNC_COL_CHECKIN in vis_cols else -1
        pn_col_idx   = vis_cols.index("Part Number")     if "Part Number"      in vis_cols else -1
        if pn_col_idx < 0:
            return

        color = _sync_row_color(source, update, checkin)

        def _walk(item: QTreeWidgetItem) -> None:
            if item.text(pn_col_idx) == pn:
                for col_idx, text in [
                    (sync_src_idx, source  or "—"),
                    (sync_upd_idx, update  or "—"),
                    (sync_chk_idx, checkin or "—"),
                ]:
                    if col_idx < 0:
                        continue
                    item.setText(col_idx, text)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if color:
                        item.setForeground(col_idx, color)
            for i in range(item.childCount()):
                _walk(item.child(i))

        for i in range(self._preview_tree.topLevelItemCount()):
            _walk(self._preview_tree.topLevelItem(i))

        for idx in (sync_src_idx, sync_upd_idx, sync_chk_idx):
            if idx >= 0:
                self._preview_tree.resizeColumnToContents(idx)

    def _on_upload_log(self, pn: str, source: str, update: str, checkin: str = "") -> None:
        self._update_sync_result(pn, source, update, checkin)

    def _on_sync_done(self, result) -> None:
        self._btn_sync_start.setEnabled(True)
        self._btn_load_preview.setEnabled(True)
        self._pgb_sync.setValue(self._pgb_sync.maximum())
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximum(0)
        self._lbl_sync_status.setText("同步完成")
        self._lbl_upload_speed.setText("")
        parts = [
            f"新建 {result.created}",
            f"更新 {result.updated}",
            f"跳过 {result.skipped}",
            f"无变化 {result.unchanged}",
            f"失败 {result.failed}",
        ]
        if result.step_uploaded:
            parts.append(f"STP↑{result.step_uploaded}")
        if result.product_registered:
            parts.append("★产品已注册")
        self._lbl_sync_summary.setText("  ".join(parts))
        self._save_history(
            result,
            user=getattr(self, "_last_sync_login", ""),
            mode=getattr(self, "_last_sync_mode", "自定义模式"),
        )
        self._refresh_history_list()
        # 有新建零件时，PLM_Version=A / PLM_Iteration=1 已在签入后写回 CATIA 文件；
        # 重新加载 BOM 以便表格立即反映最新属性值。
        if result.created > 0:
            QTimer.singleShot(500, self._on_load_preview)

    def _on_sync_error(self, err: str) -> None:
        self._btn_sync_start.setEnabled(True)
        self._btn_load_preview.setEnabled(True)
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximum(0)
        self._lbl_upload_speed.setText("")
        self._lbl_sync_status.setText(f"同步失败：{err}")
        QMessageBox.critical(self, "同步失败", err)

    # ─────────────────────────────────────────────────────────────────────────
    # 标签（Tag）方法
    # ─────────────────────────────────────────────────────────────────────────

    def _on_refresh_tags(self) -> None:
        base_url, login, password, workspace = self._read_conn()
        w = _TagsWorker(base_url, login, password, workspace)
        w.success.connect(self._on_tags_loaded)
        w.failure.connect(lambda e: QMessageBox.warning(self, "标签获取失败", e))
        self._start_worker(w)

    def _on_tags_loaded(self, tags: list) -> None:
        self._tbl_plm_tags.setRowCount(0)
        names = []
        for t in tags:
            name = t.get("label") or t.get("id") or str(t)
            tid  = str(t.get("id") or "")
            row = self._tbl_plm_tags.rowCount()
            self._tbl_plm_tags.insertRow(row)
            self._tbl_plm_tags.setItem(row, 0, QTableWidgetItem(name))
            self._tbl_plm_tags.setItem(row, 1, QTableWidgetItem(tid))
            names.append(name)
        self._cmb_rule_tag.clear()
        self._cmb_rule_tag.addItems(names)

    def _on_create_tag(self) -> None:
        label = self._le_new_tag.text().strip()
        if not label:
            QMessageBox.warning(self, "输入为空", "请输入标签名称。")
            return
        base_url, login, password, workspace = self._read_conn()
        w = _CreateTagWorker(base_url, login, password, workspace, label)
        w.success.connect(self._on_tag_created)
        w.failure.connect(lambda e: QMessageBox.warning(self, "创建标签失败", e))
        self._start_worker(w)

    def _on_tag_created(self, label: str) -> None:
        self._le_new_tag.clear()
        QMessageBox.information(self, "创建成功", f'标签"{label}"已创建。')
        self._on_refresh_tags()

    def _load_tag_rules(self) -> list[dict]:
        raw = QSettings(_S_ORG, _S_TAG_RULES).value("rules", "[]")
        try:
            return json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            return []

    def _save_tag_rules(self, rules: list[dict]) -> None:
        QSettings(_S_ORG, _S_TAG_RULES).setValue("rules", json.dumps(rules, ensure_ascii=False))

    def _reload_rules_table(self) -> None:
        self._tbl_rules.setRowCount(0)
        for rule in self._load_tag_rules():
            self._add_rule_row(rule.get("catia_value", ""), rule.get("plm_tag", ""))

    def _add_rule_row(self, catia_val: str, plm_tag: str) -> None:
        row = self._tbl_rules.rowCount()
        self._tbl_rules.insertRow(row)
        self._tbl_rules.setItem(row, 0, QTableWidgetItem(catia_val))
        self._tbl_rules.setItem(row, 1, QTableWidgetItem(plm_tag))
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.clicked.connect(lambda: self._on_delete_rule(btn_del))
        self._tbl_rules.setCellWidget(row, 2, btn_del)

    def _on_add_rule(self) -> None:
        catia_val = self._le_rule_catia.text().strip()
        plm_tag   = self._cmb_rule_tag.currentText().strip()
        if not catia_val or not plm_tag:
            QMessageBox.warning(self, "输入不完整", "请填写 CATIA 属性值并选择或输入 PLM 标签。")
            return
        rules = self._load_tag_rules()
        if any(r["catia_value"] == catia_val and r["plm_tag"] == plm_tag for r in rules):
            QMessageBox.information(self, "重复", "该规则已存在。")
            return
        rules.append({"catia_value": catia_val, "plm_tag": plm_tag})
        self._save_tag_rules(rules)
        self._add_rule_row(catia_val, plm_tag)
        self._le_rule_catia.clear()

    def _on_delete_rule(self, btn: QPushButton) -> None:
        for row in range(self._tbl_rules.rowCount()):
            if self._tbl_rules.cellWidget(row, 2) is btn:
                cv = self._tbl_rules.item(row, 0).text()
                pt = self._tbl_rules.item(row, 1).text()
                rules = [r for r in self._load_tag_rules()
                         if not (r["catia_value"] == cv and r["plm_tag"] == pt)]
                self._save_tag_rules(rules)
                self._tbl_rules.removeRow(row)
                break

    # ─────────────────────────────────────────────────────────────────────────
    # 历史记录方法
    # ─────────────────────────────────────────────────────────────────────────

    def _save_history(self, result, user: str = "", mode: str = "") -> None:
        s = QSettings(_S_ORG, _S_HISTORY)
        raw = s.value("records", "[]")
        try:
            records = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            records = []
        records.insert(0, {
            "time":               datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created":            result.created,
            "updated":            result.updated,
            "skipped":            result.skipped,
            "unchanged":          result.unchanged,
            "failed":             result.failed,
            "step_uploaded":      result.step_uploaded,
            "product_registered": result.product_registered,
            "errors":             result.errors[:50],
            "username":           user,
            "sync_mode":          mode,
        })
        s.setValue("records", json.dumps(records[:_MAX_HISTORY], ensure_ascii=False))

    def _refresh_history_list(self) -> None:
        if not hasattr(self, "_tbl_history"):
            return
        s = QSettings(_S_ORG, _S_HISTORY)
        raw = s.value("records", "[]")
        try:
            records = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            records = []
        self._tbl_history.setRowCount(0)
        for rec in records:
            row = self._tbl_history.rowCount()
            self._tbl_history.insertRow(row)
            for col, key in enumerate(["time", "created", "updated", "skipped", "failed", "username", "sync_mode"]):
                val = str(rec.get(key, 0) if key not in ("username", "sync_mode", "time") else rec.get(key, ""))
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, rec)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if key == "failed" and int(rec.get("failed", 0)) > 0:
                    item.setForeground(QColor("#e74c3c"))
                self._tbl_history.setItem(row, col, item)
        self._tbl_history.resizeColumnsToContents()

    def _on_history_selected(self, current, _prev) -> None:
        if current is None:
            return
        rec = current.data(Qt.UserRole)
        if not rec:
            return
        lines = [
            f"时间：{rec.get('time','')}",
            f"用户：{rec.get('username','—')}",
            f"模式：{rec.get('sync_mode','—')}",
            f"新建：{rec.get('created',0)}",
            f"更新：{rec.get('updated',0)}",
            f"跳过：{rec.get('skipped',0)}",
            f"无变化：{rec.get('unchanged',0)}",
            f"失败：{rec.get('failed',0)}",
            f"STP 上传：{rec.get('step_uploaded',0)}",
            f"产品注册：{'是' if rec.get('product_registered') else '否'}",
        ]
        errors = rec.get("errors", [])
        if errors:
            lines.append("")
            lines.append("失败/警告详情：")
            lines += [f"  · {e}" for e in errors]
        self._txt_hist.setPlainText("\n".join(lines))

    def _on_clear_history(self) -> None:
        if QMessageBox.question(
            self, "清空历史",
            "确定清空所有同步历史记录？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        QSettings(_S_ORG, _S_HISTORY).remove("records")
        self._tbl_history.setRowCount(0)
        self._txt_hist.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # Pull（从 PLM 拉取文件到本地）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pull(self) -> None:
        """弹出 Pull 对话框。"""
        base_url, login, password, workspace = self._read_conn()
        s_plm    = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = s_plm.value("work_dir", "")
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", '请先在"设置"页配置并保存 PLM 连接信息。')
            return
        if not work_dir:
            QMessageBox.warning(
                self, "未设置工作目录",
                '请先在"设置"页设置工作目录，Pull 文件将保存到该目录。',
            )
            return
        dlg = _PullDialog(base_url, login, password, workspace, work_dir, parent=self)
        dlg.exec()



# ─────────────────────────────────────────────────────────────────────────────
# 设置对话框
# ─────────────────────────────────────────────────────────────────────────────

class _SettingsDialog(QDialog):
    """PLM 工作台设置（连接配置 + 标签规则）。

    保存后通过 QSettings 持久化，调用方可读取最新配置。
    """

    def __init__(self, workbench: "PlmWorkbench"):
        super().__init__(workbench)
        self._wb = workbench
        self.setWindowTitle("PLM 设置")
        self.setMinimumSize(640, 560)
        self.resize(720, 640)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 连接配置 ──────────────────────────────────────────────────────────
        grp_cfg = QGroupBox("连接配置")
        form = QFormLayout(grp_cfg)
        form.setSpacing(6)

        # 从 workbench 的对应 QLineEdit 同步当前值
        wb = self._wb
        self._le_base_url  = QLineEdit(wb._le_base_url.text())
        self._le_login     = QLineEdit(wb._le_login.text())
        self._le_password  = QLineEdit(wb._le_password.text())
        self._le_password.setEchoMode(QLineEdit.Password)
        self._le_workspace = QLineEdit(wb._le_workspace.text())
        self._le_work_dir  = QLineEdit(wb._le_work_dir.text())
        self._le_work_dir.setPlaceholderText("Pull 下载文件保存目录…")

        work_dir_row = QHBoxLayout()
        btn_browse = QPushButton("浏览…"); btn_browse.setFixedWidth(60)
        btn_browse.clicked.connect(self._on_browse)
        work_dir_row.addWidget(self._le_work_dir)
        work_dir_row.addWidget(btn_browse)

        form.addRow("服务端地址：", self._le_base_url)
        form.addRow("用户名：",     self._le_login)
        form.addRow("密码：",       self._le_password)
        form.addRow("工作区：",     self._le_workspace)
        form.addRow("工作目录：",   work_dir_row)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_test = QPushButton("测试连接")
        btn_save.clicked.connect(self._on_save)
        btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        form.addRow("", btn_row)
        layout.addWidget(grp_cfg)

        # ── 工作区详情 ────────────────────────────────────────────────────────
        grp_ws = QGroupBox("工作区详情")
        v_ws = QVBoxLayout(grp_ws)
        self._lbl_ws = QLabel(wb._lbl_ws_detail.text())
        self._lbl_ws.setWordWrap(True)
        v_ws.addWidget(self._lbl_ws)
        layout.addWidget(grp_ws)

        # ── 标签规则 ──────────────────────────────────────────────────────────
        grp_rules = QGroupBox("标签自动映射规则")
        v_r = QVBoxLayout(grp_rules)
        v_r.setSpacing(6)

        v_r.addWidget(QLabel("工作区标签："))
        self._tbl_plm_tags = QTableWidget(0, 2)
        self._tbl_plm_tags.setHorizontalHeaderLabels(["标签名称", "ID"])
        _ht = self._tbl_plm_tags.horizontalHeader()
        _ht.setSectionResizeMode(0, QHeaderView.Interactive)
        _ht.resizeSection(0, 180)
        _ht.setStretchLastSection(True)
        self._tbl_plm_tags.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_plm_tags.setMinimumHeight(80)
        self._tbl_plm_tags.setMaximumHeight(130)
        v_r.addWidget(self._tbl_plm_tags)

        tag_op = QHBoxLayout()
        btn_refresh_tags = QPushButton("刷新标签列表")
        btn_refresh_tags.clicked.connect(self._on_refresh_tags)
        self._le_new_tag = QLineEdit(); self._le_new_tag.setPlaceholderText("新标签名称…")
        btn_create_tag   = QPushButton("新建标签")
        btn_create_tag.clicked.connect(self._on_create_tag)
        tag_op.addWidget(btn_refresh_tags)
        tag_op.addStretch()
        tag_op.addWidget(self._le_new_tag)
        tag_op.addWidget(btn_create_tag)
        v_r.addLayout(tag_op)

        sep = QWidget(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: palette(mid);")
        v_r.addWidget(sep)
        v_r.addWidget(QLabel('规则（Checkin 后按"设计状态"属性值自动打 Tag）：'))

        self._tbl_rules = QTableWidget(0, 3)
        self._tbl_rules.setHorizontalHeaderLabels(["CATIA 属性值", "PLM 标签", "操作"])
        _hr = self._tbl_rules.horizontalHeader()
        _hr.setSectionResizeMode(0, QHeaderView.Interactive)
        _hr.setSectionResizeMode(1, QHeaderView.Stretch)
        _hr.setSectionResizeMode(2, QHeaderView.Fixed)
        _hr.resizeSection(0, 160); _hr.resizeSection(2, 60)
        self._tbl_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_rules.setMinimumHeight(80)
        self._tbl_rules.setMaximumHeight(130)
        v_r.addWidget(self._tbl_rules)

        add_row = QHBoxLayout()
        self._le_rule_catia = QLineEdit(); self._le_rule_catia.setPlaceholderText('CATIA"设计状态"属性值')
        self._cmb_rule_tag  = QComboBox(); self._cmb_rule_tag.setEditable(True)
        self._cmb_rule_tag.setPlaceholderText("PLM Tag")
        btn_add_rule = QPushButton("添加规则")
        btn_add_rule.clicked.connect(self._on_add_rule)
        add_row.addWidget(self._le_rule_catia, 2)
        add_row.addWidget(self._cmb_rule_tag, 2)
        add_row.addWidget(btn_add_rule)
        v_r.addLayout(add_row)
        layout.addWidget(grp_rules)

        # ── 连接日志 ──────────────────────────────────────────────────────────
        grp_log = QGroupBox("连接日志")
        v_log = QVBoxLayout(grp_log)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setFixedHeight(120)
        self._txt_conn_log.setPlaceholderText('— 点击"测试连接"验证配置 —')
        # 同步 workbench 已有日志
        self._txt_conn_log.setPlainText(wb._txt_conn_log.toPlainText())
        v_log.addWidget(self._txt_conn_log)
        layout.addWidget(grp_log)

        layout.addStretch()
        self._reload_rules()

        scroll.setWidget(page)
        root.addWidget(scroll, 1)

        # 关闭按钮
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        root.addLayout(close_row)
        root.setContentsMargins(0, 0, 8, 8)

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作目录",
                                                 self._le_work_dir.text())
        if path:
            self._le_work_dir.setText(path)

    def _on_save(self) -> None:
        """保存配置到 QSettings，并同步到 workbench 的引用控件。"""
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())
        s.setValue("workspace", self._le_workspace.text().strip())
        s.setValue("work_dir",  self._le_work_dir.text().strip())
        # 同步到 workbench 引用控件
        wb = self._wb
        wb._le_base_url.setText(self._le_base_url.text())
        wb._le_login.setText(self._le_login.text())
        wb._le_password.setText(self._le_password.text())
        wb._le_workspace.setText(self._le_workspace.text())
        wb._le_work_dir.setText(self._le_work_dir.text())
        self._log("配置已保存。", "ok")

    def _on_test(self) -> None:
        """测试连接。"""
        self._log("正在测试连接……", "info")
        base_url  = self._le_base_url.text().strip()
        login     = self._le_login.text().strip()
        password  = self._le_password.text()
        workspace = self._le_workspace.text().strip()
        if not base_url or not login:
            self._log("请先填写服务端地址和用户名。", "warn")
            return
        w = _ConnectWorker(base_url, login, password, workspace)
        w.conn_ok.connect(lambda ln, users, ws_info: self._on_conn_ok(ln, users, ws_info))
        w.conn_fail.connect(lambda err: self._log(f"连接失败：{err}", "error"))
        # 借用 workbench 的 _start_worker 管理线程
        self._wb._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict) -> None:
        info_parts = []
        if ws_info.get("id"):
            info_parts.append(f"工作区 ID：{ws_info['id']}")
        if ws_info.get("description"):
            info_parts.append(f"描述：{ws_info['description']}")
        if isinstance(users, list):
            info_parts.append(f"成员数：{len(users)}")
        detail = "  |  ".join(info_parts) or "连接成功"
        self._lbl_ws.setText(detail)
        self._wb._lbl_ws_detail.setText(detail)
        self._log(f"连接成功 ({login_name})", "ok")
        self._wb._update_conn_status_bar()

    def _on_refresh_tags(self) -> None:
        self._wb._on_refresh_tags()
        # 同步标签数据到对话框的表格
        self._tbl_plm_tags.setRowCount(0)
        src = self._wb._tbl_plm_tags
        for row in range(src.rowCount()):
            self._tbl_plm_tags.insertRow(row)
            for col in range(2):
                item = src.item(row, col)
                self._tbl_plm_tags.setItem(row, col, QTableWidgetItem(item.text() if item else ""))

    def _on_create_tag(self) -> None:
        # 先把 le_new_tag 值同步到 workbench，再触发
        self._wb._le_new_tag.setText(self._le_new_tag.text())
        self._wb._on_create_tag()

    def _on_add_rule(self) -> None:
        self._wb._le_rule_catia.setText(self._le_rule_catia.text())
        idx = self._wb._cmb_rule_tag.findText(self._cmb_rule_tag.currentText())
        if idx < 0:
            self._wb._cmb_rule_tag.addItem(self._cmb_rule_tag.currentText())
            self._wb._cmb_rule_tag.setCurrentText(self._cmb_rule_tag.currentText())
        else:
            self._wb._cmb_rule_tag.setCurrentIndex(idx)
        self._wb._on_add_rule()
        self._reload_rules()

    def _reload_rules(self) -> None:
        self._wb._reload_rules_table()
        src = self._wb._tbl_rules
        self._tbl_rules.setRowCount(0)
        for row in range(src.rowCount()):
            self._tbl_rules.insertRow(row)
            for col in range(2):
                item = src.item(row, col)
                self._tbl_rules.setItem(row, col, QTableWidgetItem(item.text() if item else ""))
            # 删除按钮
            btn_del = QPushButton("删除")
            btn_del.setFixedWidth(52)
            btn_del.clicked.connect(lambda _, r=row: self._on_delete_rule(r))
            self._tbl_rules.setCellWidget(row, 2, btn_del)

    def _on_delete_rule(self, row: int) -> None:
        src = self._wb._tbl_rules
        if row < src.rowCount():
            btn = src.cellWidget(row, 2)
            if btn:
                self._wb._on_delete_rule(btn)
        self._reload_rules()

    def _log(self, msg: str, level: str = "info") -> None:
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        line = f"[{ts}] [{prefix}] {msg}"
        self._txt_conn_log.appendPlainText(line)
        self._wb._txt_conn_log.appendPlainText(line)


# ─────────────────────────────────────────────────────────────────────────────
# 历史对话框
# ─────────────────────────────────────────────────────────────────────────────

class _HistoryDialog(QDialog):
    """同步历史查看对话框。"""

    def __init__(self, workbench: "PlmWorkbench"):
        super().__init__(workbench)
        self._wb = workbench
        self.setWindowTitle("同步历史")
        self.setMinimumSize(700, 420)
        self.resize(820, 500)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)

        # 左：历史列表
        left_w = QWidget()
        left_v = QVBoxLayout(left_w)
        left_v.setContentsMargins(0, 0, 0, 0)
        self._tbl = QTableWidget(0, 7)
        self._tbl.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        _hdr = self._tbl.horizontalHeader()
        _hdr.setStretchLastSection(True)
        for i, w in enumerate([140, 45, 45, 45, 45, 100]):
            _hdr.setSectionResizeMode(i, QHeaderView.Interactive)
            _hdr.resizeSection(i, w)
        _hdr.setSectionResizeMode(6, QHeaderView.Stretch)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.currentItemChanged.connect(self._on_selected)
        left_v.addWidget(self._tbl, 1)
        splitter.addWidget(left_w)

        # 右：详情 + 清空
        right_w = QWidget()
        right_v = QVBoxLayout(right_w)
        right_v.setContentsMargins(0, 0, 0, 0)
        self._txt = QPlainTextEdit()
        self._txt.setReadOnly(True)
        self._txt.setPlaceholderText("— 点击左侧记录查看详情 —")
        right_v.addWidget(self._txt, 1)
        btn_clear = QPushButton("清空历史")
        btn_clear.setFixedHeight(24)
        btn_clear.clicked.connect(self._on_clear)
        right_v.addWidget(btn_clear)
        splitter.addWidget(right_w)
        splitter.setSizes([460, 280])
        root.addWidget(splitter, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        root.addLayout(close_row)

    def _load(self) -> None:
        """从 workbench 的 _tbl_history 复制数据。"""
        src = self._wb._tbl_history
        self._tbl.setRowCount(0)
        for row in range(src.rowCount()):
            self._tbl.insertRow(row)
            for col in range(7):
                item = src.item(row, col)
                self._tbl.setItem(row, col, QTableWidgetItem(item.text() if item else ""))
            # 复制 UserRole 数据
            src_item = src.item(row, 0)
            dst_item = self._tbl.item(row, 0)
            if src_item and dst_item:
                dst_item.setData(Qt.UserRole, src_item.data(Qt.UserRole))

    def _on_selected(self, current, _prev) -> None:
        if not current:
            return
        data = current.data(Qt.UserRole)
        if not data:
            return
        lines = []
        lines.append(f"时间：{data.get('ts', '')}")
        lines.append(f"用户：{data.get('user', '')}")
        lines.append(f"模式：{data.get('mode', '')}")
        lines.append(f"结果：新建 {data.get('created',0)}  更新 {data.get('updated',0)}  "
                     f"跳过 {data.get('skipped',0)}  失败 {data.get('failed',0)}")
        detail = data.get("detail", "")
        if detail:
            lines.append("")
            lines.append(detail)
        self._txt.setPlainText("\n".join(lines))

    def _on_clear(self) -> None:
        self._wb._on_clear_history()
        self._tbl.setRowCount(0)
        self._txt.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Pull 对话框
# ─────────────────────────────────────────────────────────────────────────────

# BOM 对比表格列常量
_PC_DEPTH   = 0   # 层级缩进（只用于视觉，值为 depth 数字）
_PC_PN      = 1   # 零件号
_PC_VER     = 2   # PLM 版本
_PC_ITER    = 3   # PLM 迭代
_PC_COUT    = 4   # 签出人
_PC_LOCAL   = 5   # 本地文件状态
_PC_FILES   = 6   # 可下载文件列表（内部存储，不显示）
_PC_PULL    = 7   # 下载? checkbox
_PC_HEADERS = ["层级", "零件号", "版本", "迭代", "签出人", "本地文件", "可用文件", "下载?"]


class _PullDialog(QDialog):
    """Pull 对话框：输入根零件号 → 递归展开 Part BOM 树 → 对比本地文件 → 批量下载。

    下载策略：
      - 每个零件的附件保存到 work_dir/{part_number}/ 子目录
      - 已有同名文件则直接覆盖（用户可通过状态列判断是否需要更新）
    """

    def __init__(self, base_url: str, login: str, password: str,
                 workspace: str, work_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pull — 从 PLM 拉取 BOM 树文件")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)

        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._work_dir  = work_dir
        self._worker: _PullWorker | None = None
        # BOM 行缓存：每行 dict 来自 get_part_components_flat，追加了 files 列表
        self._bom_rows: list[dict] = []
        self._dl_total = 0
        self._dl_done  = 0

        self._build_ui()

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        _ef = QFont("Segoe UI Emoji"); _ef.setPointSize(9)

        # ── 顶部：搜索行 ──────────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(QLabel("根零件号："))
        self._le_search = QLineEdit()
        self._le_search.setPlaceholderText("输入顶层零件号，回车或点击展开 BOM 树")
        self._le_search.returnPressed.connect(self._on_expand_bom)
        self._btn_expand = QPushButton("展开 BOM 树")
        self._btn_expand.setFont(_ef)
        self._btn_expand.clicked.connect(self._on_expand_bom)

        # 全选 / 全不选 按钮
        self._btn_select_all  = QPushButton("全选")
        self._btn_select_none = QPushButton("全不选")
        self._btn_select_all.setEnabled(False)
        self._btn_select_none.setEnabled(False)
        self._btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        self._btn_select_none.clicked.connect(lambda: self._set_all_checked(False))

        top_row.addWidget(self._le_search, 1)
        top_row.addWidget(self._btn_expand)
        top_row.addWidget(self._btn_select_all)
        top_row.addWidget(self._btn_select_none)
        layout.addLayout(top_row)

        # ── BOM 对比表格 ──────────────────────────────────────────────────────
        self._tbl_bom = QTableWidget(0, len(_PC_HEADERS))
        self._tbl_bom.setHorizontalHeaderLabels(_PC_HEADERS)
        hdr = self._tbl_bom.horizontalHeader()
        hdr.setSectionResizeMode(_PC_DEPTH,  QHeaderView.Fixed)
        hdr.setSectionResizeMode(_PC_PN,     QHeaderView.Interactive)
        hdr.setSectionResizeMode(_PC_VER,    QHeaderView.Fixed)
        hdr.setSectionResizeMode(_PC_ITER,   QHeaderView.Fixed)
        hdr.setSectionResizeMode(_PC_COUT,   QHeaderView.Interactive)
        hdr.setSectionResizeMode(_PC_LOCAL,  QHeaderView.Interactive)
        hdr.setSectionResizeMode(_PC_FILES,  QHeaderView.Interactive)
        hdr.setSectionResizeMode(_PC_PULL,   QHeaderView.Fixed)
        hdr.resizeSection(_PC_DEPTH,  40)
        hdr.resizeSection(_PC_PN,     200)
        hdr.resizeSection(_PC_VER,    60)
        hdr.resizeSection(_PC_ITER,   50)
        hdr.resizeSection(_PC_COUT,   100)
        hdr.resizeSection(_PC_LOCAL,  140)
        hdr.resizeSection(_PC_FILES,  200)
        hdr.resizeSection(_PC_PULL,   55)
        self._tbl_bom.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_bom.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_bom.setAlternatingRowColors(True)
        layout.addWidget(self._tbl_bom, 1)

        # ── 下载目录说明 ───────────────────────────────────────────────────────
        dir_lbl = QLabel(f"下载到：{self._work_dir}/{{零件号}}/{{文件名}}")
        dir_lbl.setStyleSheet("color: palette(mid);")
        layout.addWidget(dir_lbl)

        # ── 进度区 ────────────────────────────────────────────────────────────
        self._pgb_dl = QProgressBar()
        self._pgb_dl.setMaximumHeight(16)
        self._pgb_dl.setVisible(False)
        layout.addWidget(self._pgb_dl)

        prog_row = QHBoxLayout()
        self._lbl_status = QLabel("")
        self._lbl_speed  = QLabel("")
        self._lbl_speed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prog_row.addWidget(self._lbl_status, 1)
        prog_row.addWidget(self._lbl_speed)
        layout.addLayout(prog_row)

        # ── 按钮行 ────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_download = QPushButton("⬇  下载勾选文件")
        self._btn_download.setFont(_ef)
        self._btn_download.setEnabled(False)
        self._btn_download.clicked.connect(self._on_download)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_download)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _make_worker(self) -> _PullWorker:
        w = _PullWorker(self._base_url, self._login, self._password, self._workspace)
        w.search_done.connect(self._on_search_done)
        w.bom_done.connect(self._on_bom_done)
        w.file_progress.connect(self._on_file_progress)
        w.file_done.connect(self._on_file_done)
        w.all_done.connect(self._on_all_done)
        w.failure.connect(self._on_failure)
        return w

    def _local_files_for(self, part_number: str) -> set[str]:
        """返回 work_dir/{part_number}/ 下已有的文件名集合（小写）。"""
        import os as _os
        part_dir = _os.path.join(self._work_dir, part_number)
        if not _os.path.isdir(part_dir):
            return set()
        return {f.lower() for f in _os.listdir(part_dir)
                if _os.path.isfile(_os.path.join(part_dir, f))}

    def _set_all_checked(self, checked: bool) -> None:
        """批量勾选或取消所有行的下载 checkbox。"""
        for i in range(self._tbl_bom.rowCount()):
            w = self._tbl_bom.cellWidget(i, _PC_PULL)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isEnabled():
                    chk.setChecked(checked)

    # ── 步骤 1：展开 BOM 树 ────────────────────────────────────────────────────

    def _on_expand_bom(self) -> None:
        pn = self._le_search.text().strip()
        if not pn:
            return
        self._btn_expand.setEnabled(False)
        self._btn_select_all.setEnabled(False)
        self._btn_select_none.setEnabled(False)
        self._btn_download.setEnabled(False)
        self._tbl_bom.setRowCount(0)
        self._bom_rows = []
        self._lbl_status.setText(f"正在递归展开 BOM 树：{pn} ……")
        w = self._make_worker()
        # 搜索先确认零件号 + 版本
        w.set_search(number=pn)
        self._worker = w
        w.start()

    def _on_search_done(self, parts: list) -> None:
        """搜索完成后取第一个结果展开 BOM 树。"""
        self._btn_expand.setEnabled(True)
        if not parts:
            self._lbl_status.setText("未找到零件，请检查零件号。")
            return
        # 取最新版本（第一条结果）
        p = parts[0]
        pn  = str(p.get("number", ""))
        ver = str(p.get("version", "") or "A")
        self._lbl_status.setText(f"找到 {len(parts)} 个结果，正在展开 {pn}-{ver} 的 BOM 树……")
        w = self._make_worker()
        w.set_bom(pn, ver)
        self._worker = w
        w.start()

    def _on_bom_done(self, rows: list) -> None:
        """BOM 树展开完成，填充对比表格。"""
        import os as _os
        self._btn_expand.setEnabled(True)
        self._btn_select_all.setEnabled(True)
        self._btn_select_none.setEnabled(True)
        self._bom_rows = rows

        if not rows:
            self._lbl_status.setText("BOM 树为空，可能该零件没有子件。")
            return

        self._tbl_bom.setRowCount(0)
        total_files = 0

        for row_data in rows:
            pn        = str(row_data.get("part_number", ""))
            ver       = str(row_data.get("version", ""))
            itr       = str(row_data.get("iteration", ""))
            name      = str(row_data.get("name", ""))
            cout_user = str(row_data.get("check_out_user", "") or "")
            depth     = int(row_data.get("depth", 0))

            # 查询该零件的附件列表（同步，已在 Worker 中完成 BOM 展开，这里直接用缓存数据）
            # 注意：附件列表在 BOM 展开时没有并行拉取，需要在 _on_bom_done 后异步补全
            # 此版本先用 row_data 中已有数据（BOM 展开时不含附件），下载时实时查询
            files: list[str] = row_data.get("_files", [])

            # 本地文件状态
            local_set  = self._local_files_for(pn)
            local_text = "√ 已有" if local_set else "— 无"

            # 层级缩进文本
            indent = "  " * depth + ("└ " if depth > 0 else "")
            depth_text = str(depth)

            row_idx = self._tbl_bom.rowCount()
            self._tbl_bom.insertRow(row_idx)

            def _item(val: str, user_data=None) -> QTableWidgetItem:
                it = QTableWidgetItem(val)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if user_data is not None:
                    it.setData(Qt.UserRole, user_data)
                return it

            self._tbl_bom.setItem(row_idx, _PC_DEPTH,  _item(depth_text))
            self._tbl_bom.setItem(row_idx, _PC_PN,     _item(f"{indent}{pn}  {name}".strip(), pn))
            self._tbl_bom.setItem(row_idx, _PC_VER,    _item(ver))
            self._tbl_bom.setItem(row_idx, _PC_ITER,   _item(itr))

            # 签出人：非空则标红
            cout_item = _item(cout_user)
            if cout_user:
                cout_item.setForeground(QColor("#e74c3c"))
            self._tbl_bom.setItem(row_idx, _PC_COUT, cout_item)

            # 本地文件状态：已有标绿，无则灰色
            local_item = _item(local_text)
            if local_set:
                local_item.setForeground(QColor("#27ae60"))
            else:
                local_item.setForeground(QColor("#7f8c8d"))
            self._tbl_bom.setItem(row_idx, _PC_LOCAL, local_item)

            # 文件列表列（占位，实际下载时动态查询）
            self._tbl_bom.setItem(row_idx, _PC_FILES, _item("（下载时实时查询）"))

            # 下载? checkbox：默认勾选本地没有文件的行
            chk_w = QWidget()
            chk_l = QHBoxLayout(chk_w)
            chk_l.setContentsMargins(0, 0, 0, 0)
            chk_l.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(not bool(local_set))
            chk_l.addWidget(chk)
            self._tbl_bom.setCellWidget(row_idx, _PC_PULL, chk_w)

            total_files += 1

        checked = sum(
            1 for i in range(self._tbl_bom.rowCount())
            if (w := self._tbl_bom.cellWidget(i, _PC_PULL)) and
               (c := w.findChild(QCheckBox)) and c.isChecked()
        )
        self._lbl_status.setText(
            f"BOM 树：{len(rows)} 个零件  |  本地无文件：{checked} 个（已默认勾选）"
        )
        self._btn_download.setEnabled(True)

    # ── 步骤 2：批量下载 ──────────────────────────────────────────────────────

    def _on_download(self) -> None:
        """收集勾选行，为每行动态查询附件列表，然后批量下载。"""
        # 收集勾选行的基本信息
        checked_rows: list[tuple[int, str, str, str]] = []  # (row_idx, pn, ver, itr)
        for i in range(self._tbl_bom.rowCount()):
            w = self._tbl_bom.cellWidget(i, _PC_PULL)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            pn_item = self._tbl_bom.item(i, _PC_PN)
            if not pn_item:
                continue
            pn  = str(pn_item.data(Qt.UserRole) or pn_item.text()).strip()
            ver = str((self._tbl_bom.item(i, _PC_VER) or QTableWidgetItem("")).text())
            itr = str((self._tbl_bom.item(i, _PC_ITER) or QTableWidgetItem("")).text())
            if pn:
                checked_rows.append((i, pn, ver, itr))

        if not checked_rows:
            QMessageBox.warning(self, "未选择", "请至少勾选一个零件行。")
            return

        # 在当前线程中逐零件查询附件（数量通常不多，可接受）
        # 为避免 UI 卡顿，这里启动一个"预查询+下载"组合 Worker
        # 实现方式：先同步查询所有附件（用主线程的 PlmApiClient），收集 items，再下载
        self._btn_download.setEnabled(False)
        self._btn_expand.setEnabled(False)
        self._lbl_status.setText("正在查询各零件附件列表……")

        import os as _os
        try:
            from catia_copilot.plm.api_client import PlmApiClient
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
        except Exception as exc:
            QMessageBox.critical(self, "连接失败", str(exc))
            self._btn_download.setEnabled(True)
            self._btn_expand.setEnabled(True)
            return

        # (part_number, version, iteration, filename) 四元组列表
        dl_items: list[tuple[str, str, str, str]] = []
        for row_idx, pn, ver, itr in checked_rows:
            try:
                files = c.list_part_attachments(self._workspace, pn, ver, itr)
            except Exception:
                files = []
            # 更新表格文件列显示
            files_text = ", ".join(files) if files else "（无附件）"
            file_item = QTableWidgetItem(files_text)
            file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
            self._tbl_bom.setItem(row_idx, _PC_FILES, file_item)
            for fname in files:
                dl_items.append((pn, ver, itr, fname))

        if not dl_items:
            QMessageBox.information(self, "无附件", "所有勾选零件均无可下载附件。")
            self._btn_download.setEnabled(True)
            self._btn_expand.setEnabled(True)
            return

        self._dl_total = len(dl_items)
        self._dl_done  = 0
        self._pgb_dl.setMaximum(self._dl_total)
        self._pgb_dl.setValue(0)
        self._pgb_dl.setVisible(True)
        self._lbl_status.setText(f"开始下载…… (0 / {self._dl_total} 个文件)")

        _os.makedirs(self._work_dir, exist_ok=True)
        w = self._make_worker()
        w.set_download(dl_items, self._work_dir)
        self._worker = w
        w.start()

    def _on_file_progress(self, fname: str, dl: int, total: int, speed: float) -> None:
        mb_dl    = dl    / 1048576
        mb_total = total / 1048576 if total > 0 else 0
        kb_s     = speed / 1024
        current  = self._dl_done + 1
        if total > 0:
            self._lbl_status.setText(
                f"({current}/{self._dl_total})  {fname}  {mb_dl:.1f}/{mb_total:.1f} MB"
            )
        else:
            self._lbl_status.setText(
                f"({current}/{self._dl_total})  {fname}  {mb_dl:.1f} MB"
            )
        self._lbl_speed.setText(f"{kb_s:.1f} KB/s")

    def _on_file_done(self, fname: str, dest: str) -> None:
        self._dl_done += 1
        self._pgb_dl.setValue(self._dl_done)

    def _on_all_done(self, total: int) -> None:
        self._pgb_dl.setVisible(False)
        self._btn_download.setEnabled(True)
        self._btn_expand.setEnabled(True)
        self._lbl_speed.setText("")
        self._lbl_status.setText(
            f"下载完成！共 {total} 个文件 → {self._work_dir}/{{零件号}}/{{文件名}}"
        )
        # 刷新本地文件状态列
        for i in range(self._tbl_bom.rowCount()):
            pn_item = self._tbl_bom.item(i, _PC_PN)
            if not pn_item:
                continue
            pn = str(pn_item.data(Qt.UserRole) or "")
            if not pn:
                continue
            local_set = self._local_files_for(pn)
            local_text = "√ 已有" if local_set else "— 无"
            local_item = QTableWidgetItem(local_text)
            local_item.setFlags(local_item.flags() & ~Qt.ItemIsEditable)
            local_item.setForeground(QColor("#27ae60") if local_set else QColor("#7f8c8d"))
            self._tbl_bom.setItem(i, _PC_LOCAL, local_item)
        QMessageBox.information(
            self, "下载完成",
            f"已下载 {total} 个文件\n保存位置：{self._work_dir}/{{零件号}}/{{文件名}}",
        )

    def _on_failure(self, err: str) -> None:
        self._btn_expand.setEnabled(True)
        self._btn_download.setEnabled(True)
        self._pgb_dl.setVisible(False)
        self._lbl_speed.setText("")
        self._lbl_status.setText(f"失败：{err}")
        QMessageBox.critical(self, "操作失败", err)
