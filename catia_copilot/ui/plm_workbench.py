"""
PLM 工作台主窗口。

独立非模态 QDialog，通过 QTabWidget 整合所有 PLM 对接功能：
  Tab 1 - 连接：配置与测试 PLM 服务端连接
  Tab 2 - 同步： BOM 预览 + 增量同步（附件上传可选）
  Tab 3 - 标签：Tag 管理与自动映射规则
  Tab 4 - 产品：查看与管理 PLM Product 配置
  Tab 5 - 历史：最近 20 次同步记录

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
    QSpinBox,
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
    PLM_MEMBER_TABLE_COLUMNS,
    BomNodeType,
)
from catia_copilot.plm.api_client import PlmApiClient
from catia_copilot.plm.my_pdm_api_client import MyPdmApiClient, MyPdmApiError
from catia_copilot.plm.sync import (
    _rows_to_bom_tree as rows_to_bom_tree,
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

_DEFAULT_BASE_URL  = "https://192.168.1.x:8443/api"
_DEFAULT_LOGIN     = ""
_DEFAULT_PASSWORD  = ""
_DEFAULT_WORKSPACE = ""

_MAX_HISTORY = 20

# BOM 预览树：必须始终显示的固定列（不提供 checkbox）
_PREVIEW_FIXED_COLS: list[str] = ["Level", "Type", "Part Number", "Quantity"]

# BOM 预览树：默认勾选的可选列（Filename + 可隐藏列的子集）
_PREVIEW_DEFAULT_COLS = ["Filename", "Nomenclature"]

# 同步结果内部列名（不在 BOM_EDIT_COLUMN_ORDER 里，同步后追加显示）
_SYNC_COL_SOURCE  = "_sync_source"   # 签出来源：新建 / 签出 / 已签出-本人 / 撤销后签出
_SYNC_COL_UPDATE  = "_sync_update"   # 更新结果：属性已写入 / 附件已上传 / STP 已上传 / ✗ …
_SYNC_COL_CHECKIN = "_sync_checkin"  # 签入状态：已签入 / 保留签出 / ✗ 签入失败
# 兼容旧代码中偶有引用的别名（指向对应新列，勿删）
_SYNC_COL_OP     = _SYNC_COL_SOURCE
_SYNC_COL_STATUS = _SYNC_COL_CHECKIN
_SYNC_COL_DISPLAY = {
    _SYNC_COL_SOURCE:  "签出来源",
    _SYNC_COL_UPDATE:  "更新结果",
    _SYNC_COL_CHECKIN: "签入状态",
}
# 三列按此顺序插入 Level 之后
_SYNC_COLS_ORDERED = [_SYNC_COL_SOURCE, _SYNC_COL_UPDATE, _SYNC_COL_CHECKIN]


def _sync_row_color(source: str, update: str = "", checkin: str = ""):
    """根据同步三列内容返回对应的 QColor，无法匹配时返回 None。

    颜色语义：
      绿色  #27ae60 — 新建成功
      蓝色  #2980b9 — 已有零件更新成功（签出、属性写入、签入均属此类）
      灰色  #7f8c8d — 跳过 / 无变化
      红色  #e74c3c — 任何失败

    source  = _SYNC_COL_SOURCE：新建 / 已签出-本人 / 签出 / 撤销后签出 / 跳过 / 失败
    update  = _SYNC_COL_UPDATE ：属性已写入 / 附件已上传 / STP 已上传 / ✗ …
    checkin = _SYNC_COL_CHECKIN：已签入 / 保留签出 / ✗ 签入失败
    """
    combined = source + update + checkin

    # ── 失败（最优先）────────────────────────────────────────────────────────
    if "失败" in combined or "✗" in combined:
        return QColor("#e74c3c")

    # ── 跳过 / 无变化 ────────────────────────────────────────────────────────
    if "跳过" in combined or "无变化" in combined:
        return QColor("#7f8c8d")

    # ── 新建 ─────────────────────────────────────────────────────────────────
    if "新建" in source:
        return QColor("#27ae60")

    # ── 更新成功：签出类操作 / 属性写入 / 签入 ──────────────────────────────
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
    """测试 myPDM 连接并获取用户信息。"""
    success = Signal(str, list, dict)
    failure = Signal(str)

    def __init__(self, base_url, login, password):
        super().__init__()
        self._base_url = base_url
        self._login = login
        self._password = password

    def run(self):
        try:
            c = MyPdmApiClient(self._base_url)
            c.login(self._login, self._password)
            user = c.current_user
            if user is None:
                self.failure.emit("登录成功但获取用户信息失败")
                return
            user_info = {
                "id": user.id,
                "username": user.username,
                "real_name": user.real_name,
                "role": user.role,
                "department": user.department or "",
                "phone": user.phone or "",
                "status": user.status,
            }
            users = [user_info]
            self.success.emit(self._login, users, user_info)
        except MyPdmApiError as exc:
            self.failure.emit(str(exc))
        except Exception as exc:
            self.failure.emit(f"连接失败：{exc}")


class _BomPreviewWorker(QThread):
    """从 CATIA 提取 BOM 行数据（不含 PLM 操作）。"""
    success  = Signal(list)   # list[dict] rows
    failure  = Signal(str)
    progress = Signal(int)    # 已收集节点数，用于进度条

    def run(self):
        # QThread 工作线程需手动初始化 COM（STA 模式），否则 win32com 调用会抛
        # "CoInitialize has not been called"
        pythoncom.CoInitialize()
        try:
            # 构造需要读取的列：标准列 + 预设自定义属性列
            all_cols = list(dict.fromkeys(
                BOM_EDIT_COLUMN_ORDER
                + [c for c in PRESET_USER_REF_PROPERTIES if c not in BOM_EDIT_COLUMN_ORDER]
            ))
            custom_cols = [c for c in all_cols if c in PRESET_USER_REF_PROPERTIES]
            rows = collect_bom_rows_archive(
                None,           # file_path=None：使用当前活动 CATIA 文档
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
    """执行 BOM 同步 + 可选附件上传。"""
    progress   = Signal(str)
    upload_log = Signal(str, str, str, str)  # (pn, source, update, checkin)
    sync_done  = Signal(object)         # SyncResult（避免与 QThread.finished 内置信号同名冲突）
    error      = Signal(str)

    def __init__(self, base_url, login, password, workspace, options, rows):
        super().__init__()
        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._options   = options
        self._rows      = rows          # 已预览的 BOM 行，直接复用，不再二次提取

    def run(self):
        try:
            self.progress.emit("正在构建 BOM 树……")
            bom_root = rows_to_bom_tree(self._rows)
            if bom_root is None:
                self.error.emit("BOM 树构建失败，请先刷新预览")
                return

            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)

            result = sync_bom_to_plm(
                bom_root, c, self._workspace,
                options=self._options,
                progress_callback=lambda m: self.progress.emit(m),
            )

            # 注：附件上传（CATPart / STP）已移入 sync.py 的 _do_update_and_checkin，
            # 在 checkin 前执行，确保零件处于 checked-out 状态。

            self.sync_done.emit(result)
        except Exception as exc:
            logger.exception("PLM 同步后台线程异常")
            self.error.emit(str(exc))

    def _upload_attachments(self, client, rows, result):
        """已废弃：附件上传逻辑已迁移至 sync.py _do_update_and_checkin，
        在 checkin 前执行以确保零件处于 checked-out 状态。此方法保留以兼容旧引用。"""

    def _export_stp(self, catpart_path: str, pn: str) -> str | None:
        """已废弃： STP 导出已迁移至 sync.py _do_update_and_checkin。此方法保留以兼容旧引用。"""
        return None


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
    success = Signal(str)   # tag label
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
            # DocdokuPLM: POST /workspaces/{ws}/tags  body: {"label": "..."}
            c.post(f"/workspaces/{self._workspace}/tags", {"label": self._label})
            self.success.emit(self._label)
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

        # 恢复窗口几何（位置和尺寸）
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
        self._tabs.addTab(self._build_conn_tab(),     "连接")
        self._tabs.addTab(self._build_sync_tab(),     "同步")
        self._tabs.addTab(self._build_tags_tab(),     "标签")
        self._tabs.addTab(self._build_products_tab(), "产品")
        self._tabs.addTab(self._build_history_tab(),  "历史")
        root_layout.addWidget(self._tabs)

        # 活跃后台线程句柄（防 GC）
        self._workers: list[QThread] = []

        # 已加载的 BOM 行（预览后缓存，同步时复用）
        self._bom_rows: list[dict] = []
        self._pdm_client: MyPdmApiClient | None = None

        # 同步结果映射：Part Number → (操作, 状态)，同步进行中实时更新
        self._sync_result_map: dict[str, tuple[str, str, str]] = {}  # pn → (source, update, checkin)

    # ─────────────────────────────────────────────────────────────────────────
    # 通用工具
    # ─────────────────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 窗口首次显示后，把右侧"连接日志"高度对齐到左侧"PLM 连接配置"
        if hasattr(self, "_grp_cfg") and hasattr(self, "_grp_conn_log"):
            self._grp_conn_log.setFixedHeight(self._grp_cfg.sizeHint().height())

    def closeEvent(self, event):  # noqa: N802
        """关闭时保存窗口几何（位置和尺寸）。"""
        s = QSettings(_S_ORG, _S_WB)
        s.setValue("geometry", self.saveGeometry())
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
        """向连接 Tab 的状态日志区追加一行带时间戳的消息。"""
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        self._txt_conn_log.appendPlainText(f"[{ts}] [{prefix}] {msg}")
        # 滚动到底部
        sb = self._txt_conn_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1 — 连接
    # ─────────────────────────────────────────────────────────────────────────

    def _build_conn_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        base_url, login, password, workspace = self._read_conn()

        # ── 上半：左右水平布局（配置表单 | 连接日志） ─────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        # 左：配置表单
        grp_cfg = QGroupBox("myPDM 连接配置")
        grp_cfg.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
        self._le_workspace.hide()  # myPDM 不需要 workspace 字段
        # form.addRow("工作区：",     self._le_workspace)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_test = QPushButton("测试连接")
        btn_login = QPushButton("登录")
        self._btn_goto_sync = QPushButton("→ 前往同步")
        self._btn_goto_sync.setToolTip("保存配置并切换到同步 Tab")
        _arrow_font = QFont("Segoe UI Emoji")
        _arrow_font.setPointSize(9)
        self._btn_goto_sync.setFont(_arrow_font)
        btn_save.clicked.connect(self._on_save_conn)
        btn_test.clicked.connect(self._on_test_conn)
        btn_login.clicked.connect(self._on_login_conn)
        self._btn_goto_sync.clicked.connect(self._on_goto_sync)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addWidget(btn_login)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_goto_sync)
        form.addRow("", btn_row)
        top_row.addWidget(grp_cfg, stretch=1)
        # 保存引用，showEvent 时同步右侧高度
        self._grp_cfg    = grp_cfg

        # 右：连接日志
        grp_status = QGroupBox("连接日志")
        grp_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v_st = QVBoxLayout(grp_status)
        v_st.setSpacing(4)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setObjectName("logView")
        self._txt_conn_log.setPlaceholderText('— 尚未连接，点击"测试连接"验证配置 —')
        v_st.addWidget(self._txt_conn_log)
        top_row.addWidget(grp_status, stretch=1)
        self._grp_conn_log = grp_status

        # 用一个固定高度的容器包住左右，防止窗口缩小时被压缩
        top_widget = QWidget()
        top_widget.setLayout(top_row)
        top_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(top_widget)

        # ── 下半：用户信息 ──────────────────────────────────────────────
        grp_user = QGroupBox("用户信息")
        grp_user.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v_user = QVBoxLayout(grp_user)
        v_user.setSpacing(4)
        self._lbl_user_info = QLabel("— 尚未登录 —")
        self._lbl_user_info.setWordWrap(True)
        v_user.addWidget(self._lbl_user_info)
        layout.addWidget(grp_user)

        # ── 下半：连接日志（填满剩余高度） ─────────────────────────────────
        grp_log = QGroupBox("连接日志")
        v_log = QVBoxLayout(grp_log)
        v_log.setSpacing(4)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setObjectName("logView")
        self._txt_conn_log.setPlaceholderText('— 点击"登录"连接到 myPDM 后端 —')
        v_log.addWidget(self._txt_conn_log)
        layout.addWidget(grp_log, stretch=1)

        return page

    def _on_save_conn(self):
        self._save_conn()
        self._log_to_conn("配置已保存", "info")

    def _on_goto_sync(self):
        self._save_conn()
        self._tabs.setCurrentIndex(1)

    def _on_test_conn(self) -> None:
        """测试连接（仅检查后端是否可达）。"""
        base_url = self._le_base_url.text().strip()
        if not base_url:
            QMessageBox.warning(self, "配置不完整", "请输入服务端地址。")
            return
        self._log_to_conn(f"正在测试连接: {base_url} ...")
        try:
            client = MyPdmApiClient(base_url)
            if client.health():
                self._log_to_conn("连接测试成功：后端可达", "ok")
            else:
                self._log_to_conn("连接测试失败：后端无响应", "error")
        except Exception as exc:
            self._log_to_conn(f"连接测试异常：{exc}", "error")

    def _on_login_conn(self) -> None:
        """执行 myPDM 登录。"""
        base_url = self._le_base_url.text().strip()
        login = self._le_login.text().strip()
        password = self._le_password.text()

        if not base_url or not login or not password:
            QMessageBox.warning(self, "配置不完整", "请填写服务端地址、用户名和密码。")
            return

        self._log_to_conn(f"正在连接到 myPDM: {base_url} ...")
        self._save_conn()

        self._pdm_client = MyPdmApiClient(base_url)
        self._pdm_client.set_reauth_callback(self._on_reauth_required)

        worker = _ConnectWorker(base_url, login, password)
        worker.success.connect(self._on_conn_login_success)
        worker.failure.connect(self._on_conn_login_failure)
        self._start_worker(worker)

    def _on_conn_login_success(self, login: str, users: list, user_info: dict) -> None:
        """登录成功回调。"""
        real_name = user_info.get("real_name", login)
        role = user_info.get("role", "?")
        dept = user_info.get("department", "")

        role_display = {
            "admin": "管理员",
            "engineer": "工程师",
            "production": "生产",
            "guest": "访客",
        }.get(role, role)

        info_text = (
            f"姓名：{real_name}\n"
            f"用户名：{user_info.get('username', login)}\n"
            f"角色：{role_display}\n"
            f"部门：{dept}\n"
        )

        key_perms = ["parts:create", "parts:checkout", "parts:checkin", "attachments:upload"]
        perms_text = "\n权限摘要：\n"
        if self._pdm_client:
            for perm in key_perms:
                if self._pdm_client.can(perm):
                    perms_text += f"  ✓ {perm}\n"
                else:
                    perms_text += f"  ✗ {perm}\n"

        self._lbl_user_info.setText(info_text + perms_text)
        self._btn_goto_sync.setEnabled(True)
        self._log_to_conn(
            f"登录成功：{real_name}（{role_display}）", "ok"
        )

    def _on_conn_login_failure(self, error_msg: str) -> None:
        """登录失败回调。"""
        self._log_to_conn(f"登录失败：{error_msg}", "error")
        self._btn_goto_sync.setEnabled(False)

    def _on_reauth_required(self) -> None:
        """JWT 过期回调，提示用户重新登录。"""
        self._log_to_conn("认证已过期，请重新登录", "warn")
        self._btn_goto_sync.setEnabled(False)
        self._lbl_user_info.setText("— 认证已过期，请重新登录 —")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 2 — 同步
    # ─────────────────────────────────────────────────────────────────────────

    def _build_sync_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 同步选项 GroupBox ──────────────────────────────────────────────
        grp_opt = QGroupBox("同步选项")
        opt_layout = QVBoxLayout(grp_opt)
        opt_layout.setSpacing(4)

        form = QFormLayout()
        form.setSpacing(5)
        form.setContentsMargins(0, 0, 0, 0)

        # 每行单选用独立 QButtonGroup，防止跨行互相影响
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
        opt_layout.addLayout(form)

        # 分隔线
        sep = QWidget(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: palette(mid);")
        opt_layout.addWidget(sep)

        # 复选框选项 — 行一：基础选项
        chk_row1 = QHBoxLayout()
        chk_row1.setSpacing(20)
        self._chk_incremental  = QCheckBox("增量同步（跳过属性无变化的零件）")
        self._chk_reg_product  = QCheckBox("注册顶层产品为产品配置（PLM Product）")
        self._chk_incremental.setChecked(True)
        chk_row1.addWidget(self._chk_incremental)
        chk_row1.addWidget(self._chk_reg_product)
        chk_row1.addStretch()
        opt_layout.addLayout(chk_row1)

        # 复选框选项 — 行二：上传选项（4 个独立开关）
        chk_row2 = QHBoxLayout()
        chk_row2.setSpacing(16)
        self._chk_upload_catpart  = QCheckBox("上传 CATIA 文件")
        self._chk_upload_stp      = QCheckBox("上传 STP 几何文件")
        self._chk_upload_drw_pdf  = QCheckBox("上传图纸 PDF")
        self._chk_upload_drw_file = QCheckBox("上传图纸原文件")
        self._chk_upload_catpart.setToolTip("将 CATPart / CATProduct 原始文件作为附件上传到 PLM")
        self._chk_upload_stp.setToolTip(
            "将 CATPart 导出为 STP 几何文件并上传； PLM 将异步转换为 OBJ 以供三维预览。\n"
            "勾选后可设置转换等待超时时间。"
        )
        self._chk_upload_drw_pdf.setToolTip(
            "将对应的 CATDrawing 图纸转换为 PDF 后上传。\n"
            "⚠ 图纸文件定位功能待实现（TODO-01），当前找不到图纸时静默跳过。"
        )
        self._chk_upload_drw_file.setToolTip(
            "将对应的 CATDrawing 原文件作为附件上传到 PLM 。\n"
            "⚠ 图纸文件定位功能待实现（TODO-01），当前找不到图纸时静默跳过。"
        )
        chk_row2.addWidget(self._chk_upload_catpart)
        chk_row2.addWidget(self._chk_upload_stp)
        chk_row2.addWidget(self._chk_upload_drw_pdf)
        chk_row2.addWidget(self._chk_upload_drw_file)
        chk_row2.addStretch()
        opt_layout.addLayout(chk_row2)

        # STP 转换等待超时（仅上传附件时有意义）
        conv_row = QHBoxLayout()
        conv_row.setSpacing(8)
        lbl_conv = QLabel("STP 转换等待超时（秒，0=不等待）：")
        self._spn_conversion_timeout = QSpinBox()
        self._spn_conversion_timeout.setRange(0, 600)
        self._spn_conversion_timeout.setValue(120)
        self._spn_conversion_timeout.setSingleStep(30)
        self._spn_conversion_timeout.setFixedWidth(80)
        self._spn_conversion_timeout.setToolTip(
            "上传 STP 文件后， PLM 会异步转换为 OBJ 以供三维预览。\n"
            "此处设置等待转换完成的最长时间（秒）。\n"
            "若转换未完成就 Check-in，geometry 将被服务端丢弃，前端显示'无转换'。\n"
            "建议保持默认值 120 秒；若转换服务较慢可适当增大。\n"
            "设为 0 则上传后立即 Check-in（不等待，恢复旧行为）。"
        )
        # 仅在"上传 STP"勾选时启用
        self._spn_conversion_timeout.setEnabled(self._chk_upload_stp.isChecked())
        self._chk_upload_stp.toggled.connect(self._spn_conversion_timeout.setEnabled)
        conv_row.addWidget(lbl_conv)
        conv_row.addWidget(self._spn_conversion_timeout)
        conv_row.addStretch()
        opt_layout.addLayout(conv_row)

        # 分隔线
        sep2 = QWidget(); sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: palette(mid);")
        opt_layout.addWidget(sep2)

        # 预设按钮（在 GroupBox 内底部）
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
        opt_layout.addLayout(preset_row)

        layout.addWidget(grp_opt)

        # ── BOM 预览 / 同步结果 ────────────────────────────────────────────
        grp_preview = QGroupBox("BOM 预览 / 同步结果")
        v_prev = QVBoxLayout(grp_preview)
        v_prev.setSpacing(4)

        # 工具栏行：加载按钮 + 列可见性 + 节点计数 + 开始同步
        prev_toolbar = QHBoxLayout()
        self._btn_load_preview = QPushButton("从 CATIA 加载 BOM")
        self._btn_load_preview.clicked.connect(self._on_load_preview)
        self._lbl_node_count   = QLabel("")
        self._btn_sync_start   = QPushButton("开始同步")
        self._btn_sync_start.setEnabled(False)
        self._btn_sync_start.clicked.connect(self._on_sync_start)
        prev_toolbar.addWidget(self._btn_load_preview)
        prev_toolbar.addWidget(self._lbl_node_count)
        prev_toolbar.addStretch()
        prev_toolbar.addWidget(self._btn_sync_start)
        v_prev.addLayout(prev_toolbar)

        # 列可见性区域（两行，与 bom_edit_dialog 保持一致）
        self._col_vis_widget = QWidget()
        self._col_vis_vbox = QVBoxLayout(self._col_vis_widget)
        self._col_vis_vbox.setContentsMargins(8, 4, 8, 4)
        self._col_vis_vbox.setSpacing(6)
        # row0/row1 在 _build_col_visibility_row 中动态填充，先占位
        self._col_vis_row0 = QHBoxLayout()
        self._col_vis_row1 = QHBoxLayout()
        self._col_vis_row0.setSpacing(12)
        self._col_vis_row1.setSpacing(12)
        self._col_vis_vbox.addLayout(self._col_vis_row0)
        self._col_vis_vbox.addLayout(self._col_vis_row1)
        self._col_checkboxes: dict[str, QCheckBox] = {}
        self._build_col_visibility_row()
        v_prev.addWidget(self._col_vis_widget)

        # 只读 BOM 预览树（使用共享的 _BomTreeWidget，带树状连接线）
        self._preview_tree = _BomTreeWidget()
        self._preview_tree.setUniformRowHeights(True)
        self._preview_tree.setRootIsDecorated(True)
        # 不使用交替行色：Qt QSS 的 branch 伪元素不支持 :alternate，
        # 开启后 branch 列背景无法同步，会出现竖条色块。
        self._preview_tree.setAlternatingRowColors(True)
        self._preview_tree.setIndentation(16)
        # 行高由 _BomTreeWidget 内置的 _RowHeightDelegate.sizeHint() 保证，无需 setStyleSheet
        self._preview_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._preview_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        hdr = self._preview_tree.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setFixedHeight(L.TABLE_ROW_HEIGHT)
        v_prev.addWidget(self._preview_tree, 1)

        # 进度 & 状态（嵌入 GroupBox 底部）
        self._pgb_sync = QProgressBar()
        self._pgb_sync.setRange(0, 0)
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximumHeight(6)
        v_prev.addWidget(self._pgb_sync)

        # 状态行：左侧进度文字，右侧同步摘要
        status_row = QHBoxLayout()
        self._lbl_sync_status  = QLabel("就绪")
        self._lbl_sync_summary = QLabel("")
        self._lbl_sync_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_row.addWidget(self._lbl_sync_status, 1)
        status_row.addWidget(self._lbl_sync_summary, 1)
        v_prev.addLayout(status_row)

        layout.addWidget(grp_preview, 1)

        return page

    # ── 列可见性 ────────────────────────────────────────────────────────────

    def _preview_visible_cols(self) -> list[str]:
        """返回预览树当前应显示的列（固定列 + 用户勾选的可选列 + 同步结果列），按 BOM_EDIT_COLUMN_ORDER 排序。"""
        s = QSettings(_S_ORG, _S_WB)
        saved = s.value("preview_optional_cols", _PREVIEW_DEFAULT_COLS)
        if isinstance(saved, str):
            saved = [saved]
        # 过滤非法值（不含固定列，固定列另行处理）
        all_optional = (
            set(BOM_EDIT_COLUMN_ORDER) | set(PRESET_USER_REF_PROPERTIES)
        ) - set(_PREVIEW_FIXED_COLS)
        optional = [c for c in saved if c in all_optional]

        # 按 BOM_EDIT_COLUMN_ORDER 顺序排列：固定列先，然后可选列（保持原顺序）
        order = BOM_EDIT_COLUMN_ORDER + [
            c for c in PRESET_USER_REF_PROPERTIES if c not in BOM_EDIT_COLUMN_ORDER
        ]
        result: list[str] = []
        for c in order:
            if c in _PREVIEW_FIXED_COLS or c in optional:
                result.append(c)
        # 补充 PRESET_USER_REF_PROPERTIES 中不在 order 里的（理论上不会有）
        for c in optional:
            if c not in result:
                result.append(c)
        # 同步结果列固定紧跟在 Level 之后（始终显示，未同步时显示 "—"）
        for i, col in enumerate(_SYNC_COLS_ORDERED):
            result.insert(1 + i, col)
        return result

    def _save_preview_cols(self, optional_cols: list[str]) -> None:
        """只保存可选列（固定列不需要存储）。"""
        QSettings(_S_ORG, _S_WB).setValue("preview_optional_cols", optional_cols)

    def _build_col_visibility_row(self) -> None:
        """构建列可见性区域（严格参考 bom_edit_dialog）。

        第一行：
          - "显示列：" 标签
          - 固定列（Level/Type/Part Number/Quantity）— 灰色只读标签，始终显示
          - 分隔符 "|"
          - Filename checkbox
          - "显示完整路径" checkbox（与 bom_edit_dialog 一致，暂不实装，置灰）
          - BOM_HIDEABLE_COLUMNS 的各 checkbox
        第二行：
          - "自定义属性：" 标签
          - PRESET_USER_REF_PROPERTIES 的各 checkbox
        """
        # 清空两行
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

        # ── 第一行 ────────────────────────────────────────────────────────────
        self._col_vis_row0.addWidget(QLabel("显示列："))

        # 固定列：灰色只读标签
        for col in _PREVIEW_FIXED_COLS:
            display = BOM_COLUMN_DISPLAY_NAMES.get(col, col)
            lbl = QLabel(display)
            lbl.setStyleSheet("color: gray;")
            lbl.setToolTip("此列始终显示，不可隐藏")
            self._col_vis_row0.addWidget(lbl)

        # 分隔
        sep = QLabel("|")
        sep.setStyleSheet("color: #aaa;")
        self._col_vis_row0.addWidget(sep)

        # Filename checkbox（与 bom_edit_dialog 一致）
        fn_cb = QCheckBox(BOM_COLUMN_DISPLAY_NAMES.get("Filename", "Filename"))
        fn_cb.setChecked("Filename" in visible_optional)
        fn_cb.toggled.connect(self._on_col_vis_changed)
        fn_cb.setProperty("col_name", "Filename")
        self._col_checkboxes["Filename"] = fn_cb
        self._col_vis_row0.addWidget(fn_cb)

        # 可隐藏标准列（BOM_HIDEABLE_COLUMNS）
        for col in BOM_HIDEABLE_COLUMNS:
            display = BOM_COLUMN_DISPLAY_NAMES.get(col, col)
            cb = QCheckBox(display)
            cb.setChecked(col in visible_optional)
            cb.toggled.connect(self._on_col_vis_changed)
            cb.setProperty("col_name", col)
            self._col_checkboxes[col] = cb
            self._col_vis_row0.addWidget(cb)

        self._col_vis_row0.addStretch()

        # ── 第二行：预设用户自定义属性 ────────────────────────────────────────
        self._col_vis_row1.addWidget(QLabel("自定义属性："))
        for col in PRESET_USER_REF_PROPERTIES:
            if col in self._col_checkboxes:
                continue
            display = BOM_COLUMN_DISPLAY_NAMES.get(col, col)
            cb = QCheckBox(display)
            cb.setChecked(col in visible_optional)
            cb.toggled.connect(self._on_col_vis_changed)
            cb.setProperty("col_name", col)
            self._col_checkboxes[col] = cb
            self._col_vis_row1.addWidget(cb)
        self._col_vis_row1.addStretch()

    def _on_col_vis_changed(self) -> None:
        optional = [col for col, cb in self._col_checkboxes.items() if cb.isChecked()]
        self._save_preview_cols(optional)
        if self._bom_rows:
            self._populate_preview_tree(self._bom_rows)

    # ── BOM 预览加载 ────────────────────────────────────────────────────────

    def _on_load_preview(self) -> None:
        self._btn_load_preview.setEnabled(False)
        self._btn_sync_start.setEnabled(False)
        self._lbl_node_count.setText("正在加载……")
        self._preview_tree.clear()
        self._bom_rows = []
        self._sync_result_map.clear()   # 重新加载 BOM 时清空同步状态列

        # 进度对话框（不定模式，实时显示已读节点数）
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
        """BOM 加载进度：实时更新已读节点数。"""
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.setLabelText(f"正在从 CATIA 读取 BOM……  已读取 {count} 个节点")
            dlg.repaint()

    def _on_preview_loaded(self, rows: list) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.close()
            self._load_progress_dlg = None
        self._btn_load_preview.setEnabled(True)
        self._bom_rows = rows
        n = len(rows)

        # 检查异常行：未保存(_no_file)、断链接(_not_found)、轻量化(_unreadable)
        bad_unsaved   = [r for r in rows if r.get("_no_file")]
        bad_not_found = [r for r in rows if r.get("_not_found")]
        bad_unreadable= [r for r in rows if r.get("_unreadable")]

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
            self._btn_sync_start.setEnabled(False)
        elif n > PLM_SYNC_MAX_NODES:
            self._lbl_node_count.setText(
                f"共 {n} 个节点（超出上限 {PLM_SYNC_MAX_NODES}，禁止同步）"
            )
            self._lbl_node_count.setStyleSheet("color: red;")
            self._btn_sync_start.setEnabled(False)
        else:
            self._lbl_node_count.setText(f"共 {n} 个节点（上限 {PLM_SYNC_MAX_NODES}）")
            self._lbl_node_count.setStyleSheet("")
            self._btn_sync_start.setEnabled(True)

        self._populate_preview_tree(rows)

    def _on_preview_fail(self, err: str) -> None:
        dlg = getattr(self, "_load_progress_dlg", None)
        if dlg:
            dlg.close()
            self._load_progress_dlg = None
        self._btn_load_preview.setEnabled(True)
        self._lbl_node_count.setText(f"加载失败：{err}")
        self._lbl_node_count.setStyleSheet("color: red;")

    def _populate_preview_tree(self, rows: list) -> None:
        """将 BOM 行数据填充到只读预览树控件。"""
        vis_cols = self._preview_visible_cols()  # Level 始终是第一列

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
                    continue  # 同步结果列单独处理
                val = str(row.get(col_name, ""))
                item.setText(col_idx, val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            # 写入同步结果列（按纯 Part Number 匹配）
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
        # 自适应列宽
        for i in range(len(vis_cols)):
            self._preview_tree.resizeColumnToContents(i)

    # ── 同步选项预设 ─────────────────────────────────────────────────────────

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

    # ── 构建 SyncOptions ─────────────────────────────────────────────────────

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
            conversion_timeout_s=self._spn_conversion_timeout.value(),
        )

    # ── 同步执行 ─────────────────────────────────────────────────────────────

    def _on_sync_start(self) -> None:
        if not self._bom_rows:
            QMessageBox.warning(self, "无 BOM 数据", '请先点击"从 CATIA 加载 BOM"。')
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
            QMessageBox.warning(self, "配置不完整", '请先在"连接"页配置并保存 PLM 连接信息。')
            return

        # ── 前置校验：BOM 中不允许存在"部件"节点 ─────────────────────────────
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
            btn_cancel.setDefault(True)   # 默认聚焦"取消"，防止误触
            btn_continue.setDefault(False)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            vbox.addWidget(btns)
            if dlg.exec() != QDialog.Accepted:
                return  # 用户选择取消

        options = self._build_sync_options()
        self._btn_sync_start.setEnabled(False)
        self._btn_load_preview.setEnabled(False)

        # 进度条：只计 level>0 的节点（根节点 Product 不产生终态日志行）
        syncable_rows = [r for r in self._bom_rows if int(r.get("Level", 0)) > 0]
        total_nodes = len(syncable_rows)
        self._pgb_sync.setMaximum(max(total_nodes, 1))
        self._pgb_sync.setValue(0)
        self._pgb_sync.setVisible(True)
        self._sync_total_nodes  = total_nodes
        self._sync_done_nodes   = 0

        self._lbl_sync_status.setText(f"正在同步…… (0 / {total_nodes})")
        self._sync_result_map.clear()
        self._lbl_sync_summary.setText("")

        # 保存本次同步的登录名和模式（供历史记录使用）
        self._last_sync_login = login
        self._last_sync_mode  = self._detect_sync_mode()

        w = _SyncWorker(base_url, login, password, workspace, options, list(self._bom_rows))
        w.progress.connect(self._on_sync_progress)
        w.upload_log.connect(self._on_upload_log)
        w.sync_done.connect(self._on_sync_done)
        w.error.connect(self._on_sync_error)
        self._start_worker(w)

    def _detect_sync_mode(self) -> str:
        """根据当前选项推断同步模式名称。"""
        create = self._rb_create_yes.isChecked()
        update = self._rb_exist_checkout.isChecked()
        incremental = self._chk_incremental.isChecked()
        if create and not update and not incremental:
            return "新建模式"
        if not create and update and incremental:
            return "更新模式"
        return "自定义模式"

    def _on_sync_progress(self, msg: str) -> None:
        """解析 sync.py 的结构化日志行，更新状态标签和 _sync_result_map。

        格式（来自 sync._log_row / _log_skip / _log_fail）：
          终态行：  {col1} | {col2} | {col3} | {lbl}      col3 非空
          过程行：  {col1} | {col2} |         | {lbl}      col3 为空（附件上传、转换进度等）
          跳过行：  >>  {reason} | {lbl}
          失败行：  [X] {reason} | {lbl}
          表头行（_log_header）：col1="签出来源" — 跳过，不写结果

        node_done 规则：
          - 终态行（col3 非空）→ node_done=True，推进进度条
          - 过程行（col3 为空） → 仅实时刷新树单元格，不推进进度条
          - 跳过/失败行        → node_done=True，推进进度条
        """
        stripped = msg.strip()

        # 跳过分隔线（全是 '-'）
        if stripped.replace("-", "").replace(" ", "") == "":
            return

        node_done = False

        if stripped.startswith(">>"):
            inner = stripped[2:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                self._update_sync_result(lbl, reason, "", "")
                node_done = True
        elif stripped.startswith("[X]"):
            inner = stripped[3:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                self._update_sync_result(lbl, reason, "", "")
                node_done = True
        elif " | " in stripped:
            parts = [p.strip() for p in stripped.split(" | ")]
            if len(parts) >= 4:
                col1 = parts[0]
                col2 = parts[1]
                col3 = parts[2]
                lbl  = parts[-1]
                # 过滤表头行
                if col1 not in ("签出来源",):
                    if col3:
                        # col3 非空 → 终态行，写入结果并推进进度
                        self._update_sync_result(lbl, col1, col2, col3)
                        node_done = True
                    else:
                        # col3 为空 → 过程行（附件/STP 上传进度、转换中…、转换完成等）
                        # 仅实时刷新树中该零件的 _sync_update 列，不计 node_done
                        pn = lbl.split("<")[0].strip()
                        # 保留已有的 source/checkin，只更新 update 列
                        existing = self._sync_result_map.get(pn, ("", "", ""))
                        self._sync_result_map[pn] = (existing[0] or col1, col2, existing[2])
                        self._refresh_sync_cols_in_tree(
                            pn,
                            existing[0] or col1,
                            col2,
                            existing[2],
                        )

        # 推进进度条
        if node_done:
            self._sync_done_nodes = getattr(self, "_sync_done_nodes", 0) + 1
            total = getattr(self, "_sync_total_nodes", 0)
            self._pgb_sync.setValue(min(self._sync_done_nodes, total))
            self._lbl_sync_status.setText(
                f"正在同步…… ({self._sync_done_nodes} / {total})  {stripped[:60]}"
            )
        else:
            self._lbl_sync_status.setText(stripped)

    def _update_sync_result(self, lbl: str, source: str, update: str, checkin: str) -> None:
        """写入/更新 _sync_result_map，并刷新预览树中该零件的行。

        lbl 格式为 "pn<nom>" 或纯 "pn"（来自 sync._lbl），key 统一用纯 pn。
        """
        pn = lbl.split("<")[0].strip()
        self._sync_result_map[pn] = (source, update, checkin)
        self._refresh_sync_cols_in_tree(pn, source, update, checkin)

    def _refresh_sync_cols_in_tree(self, pn: str, source: str, update: str, checkin: str) -> None:
        """在已渲染的树中找到 Part Number == pn 的节点，更新同步结果三列。"""
        vis_cols = self._preview_visible_cols()
        # 如果列数与树不符，整体重建
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

        # 自适应同步结果列宽
        for idx in (sync_src_idx, sync_upd_idx, sync_chk_idx):
            if idx >= 0:
                self._preview_tree.resizeColumnToContents(idx)

    def _on_upload_log(self, pn: str, source: str, update: str, checkin: str = "") -> None:
        """接收附件上传进度，更新同步结果列（附件上传完成后用新值覆盖旧值）。"""
        self._update_sync_result(pn, source, update, checkin)

    def _on_sync_done(self, result) -> None:
        self._btn_sync_start.setEnabled(True)
        self._btn_load_preview.setEnabled(True)
        # 进度条推满再隐藏
        self._pgb_sync.setValue(self._pgb_sync.maximum())
        self._pgb_sync.setVisible(False)
        self._pgb_sync.setMaximum(0)   # 重置为不定模式，供下次使用前再设定
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
        self._pgb_sync.setMaximum(0)   # 重置为不定模式
        self._lbl_sync_status.setText(f"同步失败：{err}")
        QMessageBox.critical(self, "同步失败", err)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 3 — 标签
    # ─────────────────────────────────────────────────────────────────────────

    def _build_tags_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── 工作区现有标签 ──────────────────────────────────────────────────
        grp_tags = QGroupBox("工作区标签")
        v_t = QVBoxLayout(grp_tags)
        v_t.setSpacing(6)

        # 标签表格（比列表展示更多信息）
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

        # 操作行：刷新 + 新建标签
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

        # ── 自动映射规则 ────────────────────────────────────────────────────
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
        # 让第 1 列（PLM 标签）自动撑满剩余空间
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

        # 规则说明
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
        # 同步更新规则下拉框
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
    # Tab 4 — 产品
    # ─────────────────────────────────────────────────────────────────────────

    def _build_products_tab(self) -> QWidget:
        """构建「产品」Tab：查看与管理 PLM 中的 Product 配置。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── 产品列表区 ────────────────────────────────────────────────────────
        grp_list = QGroupBox("PLM 产品列表")
        v_l = QVBoxLayout(grp_list)
        v_l.setSpacing(6)

        # 说明文字
        lbl_hint = QLabel(
            "Product 是 DocdokuPLM 中的产品配置根节点，绑定到某个零件版本的 BOM 结构视图。"
        )
        lbl_hint.setWordWrap(True)
        v_l.addWidget(lbl_hint)

        # 产品表格
        self._tbl_products = QTableWidget(0, 4)
        self._tbl_products.setHorizontalHeaderLabels(["产品 ID", "根零件号", "版本", "说明"])
        _hdr_prod = self._tbl_products.horizontalHeader()
        _hdr_prod.setSectionResizeMode(0, QHeaderView.Interactive)
        _hdr_prod.setSectionResizeMode(1, QHeaderView.Interactive)
        _hdr_prod.setSectionResizeMode(2, QHeaderView.Interactive)
        _hdr_prod.setSectionResizeMode(3, QHeaderView.Stretch)
        _hdr_prod.resizeSection(0, 160)
        _hdr_prod.resizeSection(1, 160)
        _hdr_prod.resizeSection(2, 60)
        self._tbl_products.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_products.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl_products.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl_products.setMinimumHeight(160)
        v_l.addWidget(self._tbl_products, 1)

        # 操作行：刷新 + 删除
        op_row = QHBoxLayout()
        op_row.setSpacing(8)
        btn_refresh_prod = QPushButton("刷新列表")
        btn_refresh_prod.clicked.connect(self._on_refresh_products)
        btn_del_prod = QPushButton("删除选中")
        btn_del_prod.clicked.connect(self._on_delete_product)
        op_row.addWidget(btn_refresh_prod)
        op_row.addStretch()
        op_row.addWidget(btn_del_prod)
        v_l.addLayout(op_row)
        layout.addWidget(grp_list)

        # ── 新建产品区（内联展开） ─────────────────────────────────────────────
        grp_create = QGroupBox("新建产品")
        grp_create.setCheckable(True)
        grp_create.setChecked(False)   # 默认折叠
        v_c = QVBoxLayout(grp_create)
        v_c.setSpacing(8)

        form_create = QFormLayout()
        form_create.setSpacing(6)

        self._le_prod_id = QLineEdit()
        self._le_prod_id.setPlaceholderText("如：MyAssembly_Prod")
        form_create.addRow("产品 ID：", self._le_prod_id)

        self._le_prod_pn = QLineEdit()
        self._le_prod_pn.setPlaceholderText("顶层 CATProduct 的 Part Number")
        form_create.addRow("根零件号：", self._le_prod_pn)

        self._le_prod_desc = QLineEdit()
        self._le_prod_desc.setPlaceholderText("（可选）产品说明")
        form_create.addRow("说明：", self._le_prod_desc)

        v_c.addLayout(form_create)

        # 从当前 BOM 根节点自动填入按钮
        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        btn_fill_from_bom = QPushButton("从当前 BOM 自动填入")
        btn_fill_from_bom.setToolTip("将同步页已加载 BOM 的根节点 Part Number 自动填入「根零件号」")
        btn_fill_from_bom.clicked.connect(self._on_fill_product_from_bom)
        self._lbl_prod_fill_hint = QLabel("")
        self._lbl_prod_fill_hint.setStyleSheet("color: gray;")
        hint_row.addWidget(btn_fill_from_bom)
        hint_row.addWidget(self._lbl_prod_fill_hint, 1)
        v_c.addLayout(hint_row)

        btn_confirm_create = QPushButton("确认新建")
        btn_confirm_create.clicked.connect(self._on_create_product)
        v_c.addWidget(btn_confirm_create)

        layout.addWidget(grp_create)
        layout.addStretch()
        return page

    # ── 产品页事件处理 ────────────────────────────────────────────────────────

    def _make_prod_client(self) -> tuple["PlmApiClient | None", str]:
        """构建 PlmApiClient；失败时返回 (None, 错误信息)。"""
        base_url, login, password, workspace = self._read_conn()
        if not base_url or not login:
            return None, "请先在「连接」Tab 配置并保存 PLM 地址和用户名"
        try:
            client = PlmApiClient(base_url)
            client.login(login, password)
            return client, workspace
        except Exception as exc:
            return None, f"登录失败：{exc}"

    def _on_refresh_products(self) -> None:
        """刷新产品列表。"""
        client, workspace = self._make_prod_client()
        if client is None:
            QMessageBox.warning(self, "PLM 产品", workspace)  # workspace 此时为错误信息
            return
        self._lbl_prod_fill_hint.setText("")

        class _Worker(QThread):
            done  = Signal(list)
            error = Signal(str)
            def __init__(self, c, ws):
                super().__init__()
                self._c, self._ws = c, ws
            def run(self):
                try:
                    self.done.emit(self._c.list_products(self._ws))
                except Exception as exc:
                    self.error.emit(str(exc))

        w = _Worker(client, workspace)
        w.done.connect(self._populate_products_table)
        w.error.connect(lambda msg: QMessageBox.warning(self, "PLM 产品", f"获取产品列表失败：{msg}"))
        self._start_worker(w)

    def _populate_products_table(self, products: list[dict]) -> None:
        """将产品列表填充到表格。"""
        self._tbl_products.setRowCount(0)
        for prod in products:
            row = self._tbl_products.rowCount()
            self._tbl_products.insertRow(row)
            prod_id  = prod.get("id", "")
            din      = prod.get("designItemNumber", "")
            div      = prod.get("designItemVersion", "A")
            desc     = prod.get("description", "")
            for col, val in enumerate([prod_id, din, div, desc]):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, prod)
                self._tbl_products.setItem(row, col, item)

    def _on_delete_product(self) -> None:
        """删除选中的产品（二次确认）。"""
        row = self._tbl_products.currentRow()
        if row < 0:
            QMessageBox.information(self, "PLM 产品", "请先选中要删除的产品。")
            return
        item = self._tbl_products.item(row, 0)
        if item is None:
            return
        prod = item.data(Qt.UserRole) or {}
        prod_id = prod.get("id", self._tbl_products.item(row, 0).text())

        if QMessageBox.question(
            self, "删除产品",
            f"确定删除产品「{prod_id}」？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        client, workspace = self._make_prod_client()
        if client is None:
            QMessageBox.warning(self, "PLM 产品", workspace)
            return

        class _DelWorker(QThread):
            done  = Signal()
            error = Signal(str)
            def __init__(self, c, ws, pid):
                super().__init__()
                self._c, self._ws, self._pid = c, ws, pid
            def run(self):
                try:
                    import urllib.parse
                    ws = urllib.parse.quote(self._ws)
                    pid = urllib.parse.quote(self._pid)
                    self._c._request("DELETE", f"/workspaces/{ws}/products/{pid}")
                    self.done.emit()
                except Exception as exc:
                    self.error.emit(str(exc))

        w = _DelWorker(client, workspace, prod_id)
        w.done.connect(lambda: (
            QMessageBox.information(self, "PLM 产品", f"产品「{prod_id}」已删除。"),
            self._on_refresh_products(),
        ))
        w.error.connect(lambda msg: QMessageBox.warning(self, "PLM 产品", f"删除失败：{msg}"))
        self._start_worker(w)

    def _on_fill_product_from_bom(self) -> None:
        """从同步页已加载的 BOM 根节点自动填入「根零件号」和「产品 ID」。"""
        if not self._bom_rows:
            self._lbl_prod_fill_hint.setText("同步页尚未加载 BOM，请先在「同步」Tab 读取 BOM。")
            return
        # 找层级最高的节点（level 最小）作为根节点
        root_row = min(self._bom_rows, key=lambda r: r.get("Level", 0))
        pn  = str(root_row.get("PartNumber", "")).strip()
        nom = str(root_row.get("Nomenclature", "")).strip()
        if pn:
            self._le_prod_pn.setText(pn)
            # 产品 ID 默认为 PartNumber + "_Prod"，用户可自行修改
            self._le_prod_id.setText(f"{pn}_Prod")
        if nom and not self._le_prod_desc.text():
            self._le_prod_desc.setText(nom)
        self._lbl_prod_fill_hint.setText(f"已填入根节点：{pn}")

    def _on_create_product(self) -> None:
        """新建 PLM 产品。"""
        prod_id  = self._le_prod_id.text().strip()
        pn       = self._le_prod_pn.text().strip()
        desc     = self._le_prod_desc.text().strip()

        if not prod_id:
            QMessageBox.warning(self, "新建产品", "请填写「产品 ID」。")
            return
        if not pn:
            QMessageBox.warning(self, "新建产品", "请填写「根零件号」。")
            return

        client, workspace = self._make_prod_client()
        if client is None:
            QMessageBox.warning(self, "新建产品", workspace)
            return

        class _CreateWorker(QThread):
            done  = Signal(dict)
            error = Signal(str)
            def __init__(self, c, ws, pid, design_pn, d):
                super().__init__()
                self._c, self._ws = c, ws
                self._pid, self._design_pn, self._d = pid, design_pn, d
            def run(self):
                try:
                    result = self._c.create_product(self._ws, self._pid, self._design_pn, self._d)
                    self.done.emit(result)
                except Exception as exc:
                    self.error.emit(str(exc))

        w = _CreateWorker(client, workspace, prod_id, pn, desc)
        w.done.connect(lambda _: (
            QMessageBox.information(self, "新建产品", f"产品「{prod_id}」已成功创建。"),
            self._on_refresh_products(),
            self._le_prod_id.clear(),
            self._le_prod_pn.clear(),
            self._le_prod_desc.clear(),
        ))
        w.error.connect(lambda msg: QMessageBox.warning(self, "新建产品", f"创建失败：{msg}"))
        self._start_worker(w)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 5 — 历史
    # ─────────────────────────────────────────────────────────────────────────

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        lbl_top = QLabel("最近同步记录（最多 20 条），点击条目查看详细日志：")
        layout.addWidget(lbl_top)

        splitter = QSplitter(Qt.Horizontal)

        # ── 左侧：历史列表 ────────────────────────────────────────────────
        left = QWidget()
        v_l = QVBoxLayout(left)
        v_l.setContentsMargins(0, 0, 0, 0)
        v_l.setSpacing(4)

        # 历史表格（比列表展示更多信息）
        self._tbl_history = QTableWidget(0, 7)
        self._tbl_history.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        _hdr_hist = self._tbl_history.horizontalHeader()
        _hdr_hist.setStretchLastSection(True)
        # 时间列宽稍宽，数字列紧凑，最后「同步模式」列 stretch 填满
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

        # ── 右侧：详细日志 ────────────────────────────────────────────────
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

        # 左:右 = 45:55，左侧列表宽度合理
        splitter.setSizes([420, 540])
        layout.addWidget(splitter, 1)
        self._refresh_history_list()
        return page

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
                # 失败数非零时红色高亮
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
