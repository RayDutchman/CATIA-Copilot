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
import tempfile
from datetime import datetime

from PySide6.QtCore import QSettings, QThread, Signal, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
    """查询 PLM 中所有零件的状态（version/iteration/checkOutUser），不下载文件。"""
    success = Signal(list)   # list[dict]: [{number, version, iteration, checkOutUser}, ...]
    failure = Signal(str)

    def __init__(self, base_url, login, password, workspace):
        super().__init__()
        self._base_url = base_url; self._login = login
        self._password = password; self._workspace = workspace

    def run(self):
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            parts = c.list_parts(self._workspace) or []
            self.success.emit(parts)
        except Exception as exc:
            self.failure.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────────────────────────────

class PlmWorkbench(QDialog):
    """PLM 工作台主窗口（非模态）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PLM 工作台")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)

        # 恢复窗口几何
        s = QSettings(_S_ORG, _S_WB)
        saved_geom = s.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        try:
            theme_manager.register(self)
        except Exception:
            pass

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        _emoji_font = QFont("Segoe UI Emoji"); _emoji_font.setPointSize(9)
        self._tabs.addTab(self._build_sync_tab(),     "")
        self._tabs.addTab(self._build_settings_tab(), "")
        self._tabs.setTabText(0, "🔄 同步")
        self._tabs.setTabText(1, "⚙ 设置")
        root_layout.addWidget(self._tabs)

        # 活跃后台线程句柄（防 GC）
        self._workers: list[QThread] = []

        # 已加载的 BOM 行（预览后缓存，同步时复用）
        self._bom_rows: list[dict] = []

        # 可见 BOM 行缓存（Level > 0）
        self._visible_bom_rows: list[dict] = []

        # PLM 零件缓存：PartNumber → dict
        self._plm_parts_cache: dict[str, dict] = {}

        # 同步结果映射
        self._sync_result_map: dict[str, tuple[str, str, str]] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # 通用工具
    # ─────────────────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)

    def closeEvent(self, event):
        """关闭时保存窗口几何与选项。"""
        s = QSettings(_S_ORG, _S_WB)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("chk_incremental",    self._chk_incremental.isChecked())
        s.setValue("chk_reg_product",    self._chk_reg_product.isChecked())
        s.setValue("chk_upload_catpart", self._chk_upload_catpart.isChecked())
        s.setValue("chk_upload_stp",     self._chk_upload_stp.isChecked())
        s.setValue("chk_upload_drw_file", self._chk_upload_drw_file.isChecked())
        s.setValue("chk_upload_drw_pdf",  self._chk_upload_drw_pdf.isChecked())
        super().closeEvent(event)

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
    # Tab 0 — 同步
    # ─────────────────────────────────────────────────────────────────────────

    def _build_conn_status_bar(self) -> QWidget:
        """构建顶部连接状态栏：状态指示点 + 连接信息 + 设置按钮。"""
        bar = QWidget()
        bar.setFixedHeight(32)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(6)

        _dot_font = QFont("Segoe UI Emoji")
        _dot_font.setPointSize(12)
        self._lbl_conn_dot = QLabel("●")
        self._lbl_conn_dot.setFont(_dot_font)

        self._lbl_conn_info = QLabel("未连接")

        h.addWidget(self._lbl_conn_dot)
        h.addWidget(self._lbl_conn_info)
        h.addStretch()

        _ef = QFont("Segoe UI Emoji")
        _ef.setPointSize(9)
        btn_settings = QPushButton("⚙ 设置")
        btn_settings.setFont(_ef)
        btn_settings.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        h.addWidget(btn_settings)

        self._update_conn_status_bar()
        return bar

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

    def _build_sync_tab(self) -> QWidget:
        """构建同步 Tab：状态栏 + 三面板视图 + 操作按钮 + 高级选项 + 进度区。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # A. 顶部连接状态栏
        layout.addWidget(self._build_conn_status_bar())

        # B. 主体三面板（QSplitter）
        splitter = QSplitter(Qt.Horizontal)
        _ef = QFont("Segoe UI Emoji"); _ef.setPointSize(9)

        # 左面板：CATIA 本地
        left_w = QWidget()
        v_left = QVBoxLayout(left_w)
        v_left.setContentsMargins(0, 0, 0, 0)
        v_left.setSpacing(4)

        title_row_l = QHBoxLayout()
        title_row_l.setSpacing(6)
        title_row_l.addWidget(QLabel("📁 本地 (CATIA)"))
        self._btn_load_bom = QPushButton("↺ 加载 BOM")
        self._btn_load_bom.setFont(_ef)
        self._btn_load_bom.clicked.connect(self._on_load_preview)
        self._lbl_node_count = QLabel("")
        title_row_l.addWidget(self._btn_load_bom)
        title_row_l.addWidget(self._lbl_node_count)
        title_row_l.addStretch()
        v_left.addLayout(title_row_l)

        self._tbl_local = QTableWidget(0, 3)
        self._tbl_local.setHorizontalHeaderLabels(["零件号", "术语", "本地状态"])
        self._tbl_local.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_local.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_local.horizontalHeader().setStretchLastSection(True)
        v_left.addWidget(self._tbl_local, 1)
        splitter.addWidget(left_w)

        # 中间箭头列（固定 44px）
        mid_w = QWidget()
        mid_w.setFixedWidth(44)
        v_mid = QVBoxLayout(mid_w)
        v_mid.setContentsMargins(0, 0, 0, 0)
        v_mid.setSpacing(4)
        # 占位对齐标题行高度
        _mid_placeholder = QWidget()
        _mid_placeholder.setFixedHeight(28)
        v_mid.addWidget(_mid_placeholder)
        self._tbl_arrow = QTableWidget(0, 1)
        self._tbl_arrow.horizontalHeader().hide()
        self._tbl_arrow.verticalHeader().hide()
        self._tbl_arrow.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_arrow.setSelectionMode(QAbstractItemView.NoSelection)
        self._tbl_arrow.setShowGrid(False)
        self._tbl_arrow.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        _arrow_font = QFont("Segoe UI Emoji"); _arrow_font.setPointSize(10)
        v_mid.addWidget(self._tbl_arrow, 1)
        splitter.addWidget(mid_w)

        # 右面板：PLM 系统
        right_w = QWidget()
        v_right = QVBoxLayout(right_w)
        v_right.setContentsMargins(0, 0, 0, 0)
        v_right.setSpacing(4)

        title_row_r = QHBoxLayout()
        title_row_r.setSpacing(6)
        title_row_r.addWidget(QLabel("☁ PLM 系统"))
        self._btn_refresh_plm = QPushButton("↺ 查询 PLM 状态")
        self._btn_refresh_plm.setFont(_ef)
        self._btn_refresh_plm.clicked.connect(self._on_refresh_plm_status)
        self._lbl_plm_query_status = QLabel("—")
        title_row_r.addWidget(self._btn_refresh_plm)
        title_row_r.addWidget(self._lbl_plm_query_status)
        title_row_r.addStretch()
        v_right.addLayout(title_row_r)

        self._tbl_plm = QTableWidget(0, 3)
        self._tbl_plm.setHorizontalHeaderLabels(["零件号", "PLM状态", "版本/迭代"])
        self._tbl_plm.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_plm.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_plm.horizontalHeader().setStretchLastSection(True)
        v_right.addWidget(self._tbl_plm, 1)
        splitter.addWidget(right_w)

        # 左右 1:1，中间固定
        splitter.setSizes([500, 44, 500])
        layout.addWidget(splitter, 1)

        # C. 操作按钮行
        op_row = QHBoxLayout()
        op_row.addStretch()
        self._btn_push = QPushButton("⬆  Push 至 PLM")
        self._btn_push.setFont(_ef)
        self._btn_push.setObjectName("primaryBtn")
        self._btn_push.setEnabled(False)
        self._btn_push.clicked.connect(self._on_sync_start)
        self._btn_pull = QPushButton("⬇  Pull 至本地")
        self._btn_pull.setFont(_ef)
        self._btn_pull.setEnabled(False)
        self._btn_pull.setToolTip("计划中：从 PLM 拉取文件到本地（尚未实现）")
        op_row.addWidget(self._btn_push)
        op_row.addWidget(self._btn_pull)
        op_row.addStretch()
        layout.addLayout(op_row)

        # D. 折叠高级选项
        layout.addWidget(self._build_advanced_options())

        # E. 进度条 + 状态行
        self._pgb_sync = QProgressBar()
        self._pgb_sync.setRange(0, 0)
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximumHeight(16)
        layout.addWidget(self._pgb_sync)

        status_row = QHBoxLayout()
        self._lbl_sync_status  = QLabel("就绪")
        self._lbl_sync_summary = QLabel("")
        self._lbl_sync_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_row.addWidget(self._lbl_sync_status, 1)
        status_row.addWidget(self._lbl_sync_summary, 1)
        layout.addLayout(status_row)

        # 别名：保持旧代码中对这两个控件的引用有效
        self._btn_load_preview = self._btn_load_bom
        self._btn_sync_start   = self._btn_push

        # 隐藏的 BOM 预览树（供内部同步结果追踪使用，不加入任何布局）
        self._preview_tree = _BomTreeWidget()

        # 列可见性控件（隐藏，保留逻辑供 _populate_preview_tree 使用）
        self._col_vis_widget = QWidget()
        self._col_vis_vbox   = QVBoxLayout(self._col_vis_widget)
        self._col_vis_row0   = QHBoxLayout()
        self._col_vis_row1   = QHBoxLayout()
        self._col_vis_vbox.addLayout(self._col_vis_row0)
        self._col_vis_vbox.addLayout(self._col_vis_row1)
        self._col_checkboxes: dict[str, QCheckBox] = {}
        self._build_col_visibility_row()

        return page

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1 — 设置
    # ─────────────────────────────────────────────────────────────────────────

    def _build_settings_tab(self) -> QWidget:
        """构建设置 Tab，内含三个子 Tab：连接 / 规则 / 历史。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self._settings_tabs = QTabWidget()
        self._settings_tabs.addTab(self._build_conn_subtab(),    "🔗 连接")
        self._settings_tabs.addTab(self._build_rules_subtab(),   "🏷 规则")
        self._settings_tabs.addTab(self._build_history_subtab(), "🕐 历史")
        layout.addWidget(self._settings_tabs)
        return page

    def _build_conn_subtab(self) -> QWidget:
        """子 Tab：PLM 连接配置。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        base_url, login, password, workspace = self._read_conn()

        # 配置表单
        grp_cfg = QGroupBox("PLM 连接配置")
        form = QFormLayout(grp_cfg)
        form.setSpacing(6)

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.Password)
        self._le_workspace = QLineEdit(workspace)
        self._le_base_url.setPlaceholderText("http://127.0.0.1:8001/docdoku-plm-server-rest/api")

        form.addRow("服务端地址：", self._le_base_url)
        form.addRow("用户名：",     self._le_login)
        form.addRow("密码：",       self._le_password)
        form.addRow("工作区：",     self._le_workspace)

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

        # 工作区详情
        grp_ws = QGroupBox("工作区详情")
        v_ws = QVBoxLayout(grp_ws)
        v_ws.setSpacing(4)
        self._lbl_ws_detail = QLabel("— 未获取 —")
        self._lbl_ws_detail.setWordWrap(True)
        v_ws.addWidget(self._lbl_ws_detail)
        layout.addWidget(grp_ws)

        # 连接日志（占剩余高度）
        grp_log = QGroupBox("连接日志")
        v_log = QVBoxLayout(grp_log)
        v_log.setSpacing(4)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setObjectName("logView")
        self._txt_conn_log.setPlaceholderText('— 尚未连接，点击"测试连接"验证配置 —')
        v_log.addWidget(self._txt_conn_log)
        layout.addWidget(grp_log, 1)

        return page

    def _build_rules_subtab(self) -> QWidget:
        """子 Tab：Tag 管理与自动映射规则（迁移自原 Tab3）。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 工作区现有标签
        grp_tags = QGroupBox("工作区标签")
        v_t = QVBoxLayout(grp_tags)
        v_t.setSpacing(6)

        self._tbl_plm_tags = QTableWidget(0, 2)
        self._tbl_plm_tags.setHorizontalHeaderLabels(["标签名称", "ID"])
        _hdr_tags = self._tbl_plm_tags.horizontalHeader()
        _hdr_tags.setSectionResizeMode(0, QHeaderView.Interactive)
        _hdr_tags.setSectionResizeMode(1, QHeaderView.Interactive)
        _hdr_tags.resizeSection(0, 180)
        _hdr_tags.setStretchLastSection(True)
        self._tbl_plm_tags.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_plm_tags.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_plm_tags.setMinimumHeight(120)
        v_t.addWidget(self._tbl_plm_tags)

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
        v_t.addLayout(tag_op_row)
        layout.addWidget(grp_tags)

        # 自动映射规则
        grp_rules = QGroupBox('自动打标签规则（BOM 同步 Checkin 后按"设计状态"属性值自动打 Tag）')
        v_r = QVBoxLayout(grp_rules)
        v_r.setSpacing(6)

        self._tbl_rules = QTableWidget(0, 3)
        self._tbl_rules.setHorizontalHeaderLabels(["CATIA 属性值", "PLM 标签", "操作"])
        _hdr_rules = self._tbl_rules.horizontalHeader()
        _hdr_rules.setSectionResizeMode(0, QHeaderView.Interactive)
        _hdr_rules.setSectionResizeMode(1, QHeaderView.Interactive)
        _hdr_rules.setSectionResizeMode(2, QHeaderView.Fixed)
        _hdr_rules.resizeSection(0, 160)
        _hdr_rules.resizeSection(2, 60)
        _hdr_rules.setStretchLastSection(False)
        _hdr_rules.setSectionResizeMode(1, QHeaderView.Stretch)
        self._tbl_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_rules.setMinimumHeight(120)
        v_r.addWidget(self._tbl_rules)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        lbl_add = QLabel("新增规则：")
        self._le_rule_catia = QLineEdit()
        self._le_rule_catia.setPlaceholderText('CATIA"设计状态"属性值，如：发布')
        self._cmb_rule_tag  = QComboBox()
        self._cmb_rule_tag.setEditable(True)
        self._cmb_rule_tag.setPlaceholderText("PLM Tag（可手填或从列表选）")
        btn_add_rule = QPushButton("添加")
        btn_add_rule.clicked.connect(self._on_add_rule)
        add_row.addWidget(lbl_add)
        add_row.addWidget(self._le_rule_catia, 2)
        add_row.addWidget(self._cmb_rule_tag, 2)
        add_row.addWidget(btn_add_rule)
        v_r.addLayout(add_row)

        lbl_hint = QLabel(
            "提示：规则在每次 BOM 同步完成后自动执行。"
            '若零件的"设计状态"属性值与规则匹配，则自动为该零件在 PLM 中添加对应标签。'
        )
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: palette(mid);")
        v_r.addWidget(lbl_hint)
        layout.addWidget(grp_rules)

        layout.addStretch()
        self._reload_rules_table()
        return page

    def _build_history_subtab(self) -> QWidget:
        """子 Tab：同步历史记录（迁移自原 Tab5）。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        lbl_top = QLabel("最近同步记录（最多 20 条），点击条目查看详细日志：")
        layout.addWidget(lbl_top)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧：历史列表
        left = QWidget()
        v_l = QVBoxLayout(left)
        v_l.setContentsMargins(0, 0, 0, 0)
        v_l.setSpacing(4)

        self._tbl_history = QTableWidget(0, 7)
        self._tbl_history.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        _hdr_hist = self._tbl_history.horizontalHeader()
        _hdr_hist.setStretchLastSection(True)
        _col_widths = [140, 50, 50, 50, 50, 100]
        for i, w in enumerate(_col_widths):
            _hdr_hist.setSectionResizeMode(i, QHeaderView.Interactive)
            _hdr_hist.resizeSection(i, w)
        _hdr_hist.setSectionResizeMode(6, QHeaderView.Stretch)
        self._tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_history.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl_history.currentItemChanged.connect(self._on_history_selected)
        v_l.addWidget(self._tbl_history, 1)

        btn_clear = QPushButton("清空历史")
        btn_clear.clicked.connect(self._on_clear_history)
        v_l.addWidget(btn_clear)
        splitter.addWidget(left)

        # 右侧：详细日志
        right = QWidget()
        v_r = QVBoxLayout(right)
        v_r.setContentsMargins(0, 0, 0, 0)
        v_r.setSpacing(4)
        v_r.addWidget(QLabel("详细日志："))
        self._txt_hist = QPlainTextEdit()
        self._txt_hist.setReadOnly(True)
        self._txt_hist.setObjectName("logView")
        self._txt_hist.setPlaceholderText("— 点击左侧记录查看详情 —")
        v_r.addWidget(self._txt_hist, 1)
        splitter.addWidget(right)

        splitter.setSizes([420, 540])
        layout.addWidget(splitter, 1)
        self._refresh_history_list()
        return page

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
        """触发 PLM 状态查询 Worker。"""
        base_url, login, password, workspace = self._read_conn()
        self._btn_refresh_plm.setEnabled(False)
        self._lbl_plm_query_status.setText("查询中……")
        w = _PlmStatusWorker(base_url, login, password, workspace)
        w.success.connect(self._on_plm_status_loaded)
        w.failure.connect(self._on_plm_status_error)
        self._start_worker(w)

    def _on_plm_status_loaded(self, parts: list) -> None:
        """PLM 状态查询完成：缓存结果，填充右侧表格，刷新箭头列。"""
        self._btn_refresh_plm.setEnabled(True)
        self._plm_parts_cache = {p.get("number", ""): p for p in parts}
        self._lbl_plm_query_status.setText(f"已查询 {len(parts)} 个零件")

        # 填充 _tbl_plm（与 _tbl_local 行对齐）
        visible = self._visible_bom_rows
        self._tbl_plm.setRowCount(len(visible))
        for i, row in enumerate(visible):
            pn = str(row.get("Part Number", ""))
            plm = self._plm_parts_cache.get(pn, {})
            status = "存在" if plm else "不存在"
            ver_iter = f"{plm.get('version','—')} / {plm.get('iteration','—')}" if plm else "—"
            self._tbl_plm.setItem(i, 0, QTableWidgetItem(pn))
            self._tbl_plm.setItem(i, 1, QTableWidgetItem(status))
            self._tbl_plm.setItem(i, 2, QTableWidgetItem(ver_iter))

        self._update_arrow_column()
        self._sync_table_row_heights()

        # 有 BOM 数据时启用 Push 按钮
        if visible:
            self._btn_push.setEnabled(True)

    def _on_plm_status_error(self, err: str) -> None:
        """PLM 查询失败时恢复按钮并显示错误。"""
        self._btn_refresh_plm.setEnabled(True)
        self._lbl_plm_query_status.setText(f"查询失败：{err}")

    def _update_arrow_column(self) -> None:
        """根据本地 BOM 行和 PLM 缓存，更新中间箭头列的图标与颜色。"""
        _emoji_font = QFont("Segoe UI Emoji"); _emoji_font.setPointSize(10)
        for i, row in enumerate(self._visible_bom_rows):
            pn = str(row.get("Part Number", ""))
            plm = self._plm_parts_cache.get(pn)
            if i >= self._tbl_arrow.rowCount():
                break

            if plm is None:
                icon = "🆕"
                color = QColor("#27ae60")
            else:
                local_ver = str(row.get("PLM_Version", "") or "")
                plm_ver   = str(plm.get("version", "") or "")
                if local_ver and plm_ver and local_ver != plm_ver:
                    icon  = "⬆"
                    color = QColor("#e67e22")
                else:
                    icon  = "✅"
                    color = QColor("#27ae60")

            itm = QTableWidgetItem(icon)
            itm.setTextAlignment(Qt.AlignCenter)
            itm.setFont(_emoji_font)
            itm.setForeground(color)
            self._tbl_arrow.setItem(i, 0, itm)

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
        self._update_arrow_column()

    def _on_preview_fail(self, err: str) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.close()
            self._load_progress_dlg = None
        self._btn_load_bom.setEnabled(True)
        self._lbl_node_count.setText(f"加载失败：{err}")
        self._lbl_node_count.setStyleSheet("color: red;")

    def _populate_local_table(self, rows: list) -> None:
        """将 Level>0 的 BOM 行填充到左侧本地表格，并初始化箭头/PLM 表格行数。"""
        visible = [r for r in rows if int(r.get("Level", 0)) > 0]
        self._visible_bom_rows = visible
        self._tbl_local.setRowCount(len(visible))
        for i, row in enumerate(visible):
            pn  = str(row.get("Part Number", ""))
            nom = str(row.get("Nomenclature", ""))
            self._tbl_local.setItem(i, 0, QTableWidgetItem(pn))
            self._tbl_local.setItem(i, 1, QTableWidgetItem(nom))
            self._tbl_local.setItem(i, 2, QTableWidgetItem(""))
        self._tbl_arrow.setRowCount(len(visible))
        for i in range(len(visible)):
            itm = QTableWidgetItem("—")
            itm.setTextAlignment(Qt.AlignCenter)
            self._tbl_arrow.setItem(i, 0, itm)
        self._tbl_plm.setRowCount(len(visible))
        self._sync_table_row_heights()

    def _sync_table_row_heights(self) -> None:
        """以 _tbl_local 为准，同步三个表格的行高。"""
        for i in range(self._tbl_local.rowCount()):
            h = self._tbl_local.rowHeight(i)
            if i < self._tbl_arrow.rowCount():
                self._tbl_arrow.setRowHeight(i, h)
            if i < self._tbl_plm.rowCount():
                self._tbl_plm.setRowHeight(i, h)

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
            dlg = QDialog(self)
            dlg.setWindowTitle("存在未保存的文档")
            dlg.setMinimumWidth(480)
            vbox = QVBoxLayout(dlg)
            warn_lbl = QLabel(
                "以下 CATIA 文档存在未保存问题（见各条目说明）：\n"
                "  • 从未保存到磁盘：该零件的属性与几何体完全无法上传\n"
                "  • 有未提交修改：将上传磁盘上的旧版本，本次修改不会包含在内\n\n"
                "建议先切换到 CATIA ，保存所有文件后再同步。",
                dlg,
            )
            warn_lbl.setWordWrap(True)
            vbox.addWidget(warn_lbl)
            lst = QListWidget(dlg)
            for entry in unsaved:
                lst.addItem(entry)
            lst.setMaximumHeight(120)
            vbox.addWidget(lst)
            btns = QDialogButtonBox(dlg)
            btn_cancel   = btns.addButton("取消（返回保存）", QDialogButtonBox.RejectRole)
            btn_continue = btns.addButton("忽略并继续同步", QDialogButtonBox.AcceptRole)
            btn_cancel.setDefault(True)
            btn_continue.setDefault(False)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            vbox.addWidget(btns)
            if dlg.exec() != QDialog.Accepted:
                return

        options = self._build_sync_options()
        self._btn_sync_start.setEnabled(False)
        self._btn_load_preview.setEnabled(False)

        syncable_rows = [r for r in self._bom_rows if int(r.get("Level", 0)) > 0]
        total_nodes = len(syncable_rows)
        self._pgb_sync.setMaximum(max(total_nodes, 1))
        self._pgb_sync.setValue(0)
        self._pgb_sync.setVisible(True)
        self._sync_total_nodes = total_nodes
        self._sync_done_nodes  = 0

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
        stripped = msg.strip()

        if stripped.replace("-", "").replace(" ", "") == "":
            return

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

    def _on_sync_error(self, err: str) -> None:
        self._btn_sync_start.setEnabled(True)
        self._btn_load_preview.setEnabled(True)
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximum(0)
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
