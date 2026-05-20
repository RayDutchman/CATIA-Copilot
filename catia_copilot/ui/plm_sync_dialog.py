"""
PLM 同步对话框。

在后台线程中执行 BOM 提取 + DocdokuPLM 同步，主线程保持 UI 响应。

使用方式：
    dialog = PlmSyncDialog(parent)
    dialog.exec()
"""

import logging

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

# ── PLM 连接配置默认值 ────────────────────────────────────────────────────────
# 实际使用值由 QSettings 持久化；常量仅在首次启动（无保存记录时）作为初始值。
# 注意：必须用 127.0.0.1 而非 localhost。
# Windows 将 localhost 解析为 ::1（IPv6）优先，Payara 仅监听 IPv4，
# 导致每次 TCP 连接等待 21 秒超时后才回落到 127.0.0.1，每个零件耗时 63 秒以上。
_DEFAULT_PLM_BASE_URL  = "http://127.0.0.1:8001/docdoku-plm-server-rest/api"
_DEFAULT_PLM_LOGIN     = "admin"
_DEFAULT_PLM_PASSWORD  = "password"
_DEFAULT_PLM_WORKSPACE = "Workspace_0"

_SETTINGS_ORG = "CATIACompanion"
_SETTINGS_APP = "PlmConfig"
_SETTINGS_OPT = "PlmSyncOptions"


# ── 同步选项对话框 ─────────────────────────────────────────────────────────────

class _SyncOptionsDialog(QDialog):
    """同步策略选项对话框，在每次开始同步前弹出供用户确认。

    选项持久化到 QSettings("CATIACompanion", "PlmSyncOptions")。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("同步选项")
        self.setMinimumWidth(420)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── 预设按钮 ────────────────────────────────────────────────────────
        preset_row = QHBoxLayout()
        preset_label = QLabel("快速预设：")
        btn_new    = QPushButton("新建模式")
        btn_update = QPushButton("更新模式")
        btn_new.setToolTip("只新建 Workspace 中不存在的零件，已存在零件一律跳过")
        btn_update.setToolTip("不新建，只更新已由本人签出的零件，保留签出状态")
        btn_new.clicked.connect(self._apply_preset_new)
        btn_update.clicked.connect(self._apply_preset_update)
        preset_row.addWidget(preset_label)
        preset_row.addWidget(btn_new)
        preset_row.addWidget(btn_update)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        # ── 不存在的零件 ────────────────────────────────────────────────────
        g0 = QGroupBox("Workspace 中不存在的零件")
        v0 = QVBoxLayout(g0)
        self._rb_create_new  = QRadioButton("新建（推荐）")
        self._rb_skip_new    = QRadioButton("跳过，不新建")
        bg0 = QButtonGroup(self)
        bg0.addButton(self._rb_create_new)
        bg0.addButton(self._rb_skip_new)
        v0.addWidget(self._rb_create_new)
        v0.addWidget(self._rb_skip_new)
        layout.addWidget(g0)

        # ── 已签入的零件 ────────────────────────────────────────────────────
        g1 = QGroupBox("已签入（Checked In）的零件")
        v1 = QVBoxLayout(g1)
        self._rb_skip_existing    = QRadioButton("跳过，不做更新（推荐）")
        self._rb_checkout_update  = QRadioButton("签出（Checkout）后更新属性")
        bg1 = QButtonGroup(self)
        bg1.addButton(self._rb_skip_existing)
        bg1.addButton(self._rb_checkout_update)
        v1.addWidget(self._rb_skip_existing)
        v1.addWidget(self._rb_checkout_update)
        layout.addWidget(g1)

        # ── 他人已签出的零件 ────────────────────────────────────────────────
        g3 = QGroupBox("他人已签出（Checked Out）的零件")
        v3 = QVBoxLayout(g3)
        self._rb_other_skip  = QRadioButton("跳过并记录警告（推荐）")
        self._rb_other_force = QRadioButton("强制覆盖他人签出（需管理员权限）")
        bg3 = QButtonGroup(self)
        bg3.addButton(self._rb_other_skip)
        bg3.addButton(self._rb_other_force)
        v3.addWidget(self._rb_other_skip)
        v3.addWidget(self._rb_other_force)
        layout.addWidget(g3)

        # ── 更新后处置 ──────────────────────────────────────────────────────
        g4 = QGroupBox("更新属性后")
        v4 = QVBoxLayout(g4)
        self._rb_checkin       = QRadioButton("自动签入（Check In，推荐）")
        self._rb_keep_checkout = QRadioButton("保留签出（Checked Out）状态")
        bg4 = QButtonGroup(self)
        bg4.addButton(self._rb_checkin)
        bg4.addButton(self._rb_keep_checkout)
        v4.addWidget(self._rb_checkin)
        v4.addWidget(self._rb_keep_checkout)
        layout.addWidget(g4)

        # ── 按钮 ────────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._save_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _apply_preset_new(self) -> None:
        """预设：新建模式——只新建，已存在零件全部跳过。"""
        self._rb_create_new.setChecked(True)
        self._rb_skip_existing.setChecked(True)
        self._rb_other_skip.setChecked(True)
        self._rb_checkin.setChecked(True)

    def _apply_preset_update(self) -> None:
        """预设：更新模式——不新建，只更新本人已签出的零件，保留签出。"""
        self._rb_skip_new.setChecked(True)
        self._rb_skip_existing.setChecked(True)
        self._rb_other_skip.setChecked(True)
        self._rb_keep_checkout.setChecked(True)

    def _load_settings(self) -> None:
        """从 QSettings 读取持久化选项，不存在则使用推荐默认值。"""
        s  = QSettings(_SETTINGS_ORG, _SETTINGS_OPT)
        cn = s.value("create_new_parts", "true")
        ep = s.value("existing_part_policy", "skip")
        op = s.value("other_checked_out_policy", "skip")
        au = s.value("after_update_policy", "checkin")

        self._rb_create_new.setChecked(str(cn).lower() != "false")
        self._rb_skip_new.setChecked(str(cn).lower() == "false")

        self._rb_skip_existing.setChecked(ep == "skip")
        self._rb_checkout_update.setChecked(ep != "skip")

        self._rb_other_skip.setChecked(op != "force_undo")
        self._rb_other_force.setChecked(op == "force_undo")

        self._rb_checkin.setChecked(au != "keep_checkout")
        self._rb_keep_checkout.setChecked(au == "keep_checkout")

    def _save_and_accept(self) -> None:
        """将当前选项写入 QSettings 并关闭对话框。"""
        s = QSettings(_SETTINGS_ORG, _SETTINGS_OPT)
        s.setValue("create_new_parts",
                   "false" if self._rb_skip_new.isChecked() else "true")
        s.setValue("existing_part_policy",
                   "skip" if self._rb_skip_existing.isChecked() else "checkout_update")
        s.setValue("other_checked_out_policy",
                   "force_undo" if self._rb_other_force.isChecked() else "skip")
        s.setValue("after_update_policy",
                   "keep_checkout" if self._rb_keep_checkout.isChecked() else "checkin")
        s.sync()
        self.accept()

    def to_sync_options(self):
        """将当前 UI 状态转换为 SyncOptions 实例（须在 accept() 后调用）。"""
        from catia_copilot.plm.sync import (
            AfterUpdatePolicy,
            CheckedOutByOtherPolicy,
            ExistingPartPolicy,
            OwnCheckedOutPolicy,
            SyncOptions,
        )
        return SyncOptions(
            create_new_parts=self._rb_create_new.isChecked(),
            existing_part_policy=(
                ExistingPartPolicy.SKIP
                if self._rb_skip_existing.isChecked()
                else ExistingPartPolicy.CHECKOUT_UPDATE
            ),
            own_checked_out_policy=OwnCheckedOutPolicy.UPDATE,   # 固定：始终直接更新
            other_checked_out_policy=(
                CheckedOutByOtherPolicy.FORCE_UNDO
                if self._rb_other_force.isChecked()
                else CheckedOutByOtherPolicy.SKIP
            ),
            after_update_policy=(
                AfterUpdatePolicy.KEEP_CHECKOUT
                if self._rb_keep_checkout.isChecked()
                else AfterUpdatePolicy.CHECKIN
            ),
        )


def _load_plm_config() -> dict:
    """从 QSettings 读取 PLM 连接配置，不存在则返回默认值。"""
    s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    return {
        "base_url":  s.value("base_url",  _DEFAULT_PLM_BASE_URL),
        "login":     s.value("login",     _DEFAULT_PLM_LOGIN),
        "password":  s.value("password",  _DEFAULT_PLM_PASSWORD),
        "workspace": s.value("workspace", _DEFAULT_PLM_WORKSPACE),
    }


def _save_plm_config(cfg: dict) -> None:
    """将 PLM 连接配置写入 QSettings 持久化。"""
    s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    s.setValue("base_url",  cfg["base_url"])
    s.setValue("login",     cfg["login"])
    s.setValue("password",  cfg["password"])
    s.setValue("workspace", cfg["workspace"])
    s.sync()


# ── 后台工作线程 ──────────────────────────────────────────────────────────────

class _SyncWorker(QThread):
    """在后台线程中依次完成：BOM 提取（COM，须在调用线程完成）→ PLM 同步。

    注意：CATIA COM 调用必须在创建该线程的主线程中完成，因此 BOM 提取
    在 run() 之前的 prepare() 中执行，run() 中只做纯网络操作。
    """

    # 进度日志信号（文本行）
    log_line    = Signal(str)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, cfg: dict, options=None, parent=None):
        super().__init__(parent)
        self._cfg      = cfg
        self._options  = options   # SyncOptions 实例，None 时使用默认值
        self._bom_root = None

    def prepare(self) -> bool:
        """在主线程中提取 BOM（COM 调用）。返回 False 表示失败。"""
        from catia_copilot.plm.sync import extract_bom

        self.log_line.emit("正在读取 CATIA BOM……")
        try:
            self._bom_root = extract_bom(
                progress_callback=lambda msg: self.log_line.emit(msg)
            )
        except Exception as exc:
            self.finished_err.emit(f"BOM 读取失败：{exc}")
            return False

        if self._bom_root is None:
            self.finished_err.emit("未找到活动的 CATIA 文档，请先在 CATIA 中打开 CATProduct。")
            return False

        self.log_line.emit(f"BOM 根节点：{self._bom_root.part_number}")
        return True

    def run(self) -> None:
        """后台线程：登录 PLM → 同步 BOM。"""
        from catia_copilot.plm.api_client import PlmApiClient, PlmApiError
        from catia_copilot.plm.sync import sync_bom_to_plm

        cfg = self._cfg
        self.log_line.emit(f"正在连接 PLM 服务端：{cfg['base_url']} …")
        client = PlmApiClient(cfg["base_url"])
        try:
            client.login(cfg["login"], cfg["password"])
        except PlmApiError as exc:
            self.finished_err.emit(
                f"PLM 登录失败：{exc}\n\n请确认 DocdokuPLM 服务已启动，"
                f"并检查连接配置是否正确。"
            )
            return
        self.log_line.emit("PLM 登录成功，开始同步……")

        try:
            result = sync_bom_to_plm(
                bom_root=self._bom_root,
                client=client,
                workspace=cfg["workspace"],
                options=self._options,
                upload_step=False,
                progress_callback=lambda msg: self.log_line.emit(msg),
            )
        except Exception as exc:
            self.finished_err.emit(f"同步过程中发生意外错误：{exc}")
            return

        self.finished_ok.emit(result.summary())


# ── 对话框 ────────────────────────────────────────────────────────────────────

class PlmSyncDialog(QDialog):
    """BOM → DocdokuPLM 同步对话框。"""

    # 同步开始/结束信号（供外部暂停/恢复 CATIA 连接检查定时器）
    sync_started = Signal()
    sync_done    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("同步 BOM 到 PLM")
        self.setMinimumWidth(540)
        self.setMinimumHeight(420)
        self._worker: _SyncWorker | None = None
        self._cfg = _load_plm_config()
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── 连接配置区域 ──────────────────────────────────────────────────────
        cfg_group = QGroupBox("PLM 连接配置")
        cfg_form  = QFormLayout(cfg_group)
        cfg_form.setContentsMargins(8, 8, 8, 8)

        self._edit_url       = QLineEdit(self._cfg["base_url"])
        self._edit_login     = QLineEdit(self._cfg["login"])
        self._edit_password  = QLineEdit(self._cfg["password"])
        self._edit_workspace = QLineEdit(self._cfg["workspace"])

        self._edit_password.setEchoMode(QLineEdit.Password)
        self._edit_url.setPlaceholderText("http://127.0.0.1:8001/docdoku-plm-server-rest/api")
        self._edit_workspace.setPlaceholderText("Workspace_0")

        cfg_form.addRow("服务端地址：", self._edit_url)
        cfg_form.addRow("用户名：",     self._edit_login)
        cfg_form.addRow("密码：",       self._edit_password)
        cfg_form.addRow("工作区：",     self._edit_workspace)

        self._btn_save_cfg = QPushButton("保存配置")
        self._btn_save_cfg.setFixedWidth(90)
        self._btn_save_cfg.clicked.connect(self._save_config)
        cfg_form.addRow("", self._btn_save_cfg)

        layout.addWidget(cfg_group)

        # ── 状态标签 ──────────────────────────────────────────────────────────
        self._status_label = QLabel('点击"开始同步"将当前 CATIA 产品结构同步到 DocdokuPLM。')
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # ── 不定进度条 ────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── 日志区域 ──────────────────────────────────────────────────────────
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4;"
            " font-family: Consolas, 'Courier New', monospace; font-size: 9pt;"
        )
        layout.addWidget(self._log)

        # ── 按钮行 ────────────────────────────────────────────────────────────
        self._btn_start = QPushButton("开始同步")
        self._btn_start.clicked.connect(self._start_sync)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        btn_box.addButton(self._btn_start, QDialogButtonBox.ActionRole)
        layout.addWidget(btn_box)

    # ── 配置相关槽 ────────────────────────────────────────────────────────────

    def _read_config_from_ui(self) -> dict:
        """从 UI 控件读取当前配置值。"""
        return {
            "base_url":  self._edit_url.text().strip(),
            "login":     self._edit_login.text().strip(),
            "password":  self._edit_password.text(),
            "workspace": self._edit_workspace.text().strip(),
        }

    def _save_config(self) -> None:
        """保存配置到 QSettings，并更新内存中的 _cfg。"""
        cfg = self._read_config_from_ui()
        if not cfg["base_url"]:
            QMessageBox.warning(self, "配置错误", "服务端地址不能为空。")
            return
        if not cfg["workspace"]:
            QMessageBox.warning(self, "配置错误", "工作区不能为空。")
            return
        _save_plm_config(cfg)
        self._cfg = cfg
        self._status_label.setText("配置已保存。")
        logger.info(f"PLM 配置已保存：url={cfg['base_url']}  workspace={cfg['workspace']}")

    # ── 同步相关槽 ────────────────────────────────────────────────────────────

    def _start_sync(self) -> None:
        # 同步前先用 UI 当前值（允许临时修改但未点保存的情况也能生效）
        cfg = self._read_config_from_ui()
        if not cfg["base_url"] or not cfg["workspace"]:
            QMessageBox.warning(self, "配置不完整", "请填写服务端地址和工作区后再同步。")
            return

        # 弹出同步策略选项对话框
        opts_dialog = _SyncOptionsDialog(self)
        if opts_dialog.exec() != QDialog.Accepted:
            return   # 用户取消
        options = opts_dialog.to_sync_options()

        self._btn_start.setEnabled(False)
        self._log.clear()
        self._progress.setVisible(True)
        self._status_label.setText("正在同步……")

        self._worker = _SyncWorker(cfg=cfg, options=options, parent=None)
        self._worker.log_line.connect(self._append_log)
        self._worker.finished_ok.connect(self._on_success)
        self._worker.finished_err.connect(self._on_error)

        # BOM 提取须在主线程中完成
        if not self._worker.prepare():
            self._progress.setVisible(False)
            self._btn_start.setEnabled(True)
            self._worker = None
            return

        self._worker.start()
        self.sync_started.emit()

    def _append_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)
        # 主线程 emit（BOM 提取阶段）时需要主动刷新事件循环，
        # 否则所有行会在 _start_sync 返回后才一次性渲染出来
        QApplication.processEvents()

    def _on_success(self, summary: str) -> None:
        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._status_label.setText("同步完成。")
        self._log.appendPlainText("\n" + "─" * 40)
        self._log.appendPlainText(summary)
        self.sync_done.emit()

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._status_label.setText("同步失败。")
        self.sync_done.emit()
        QMessageBox.critical(self, "PLM 同步错误", msg)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self, "同步进行中",
                "同步正在后台运行，关闭窗口后同步将继续直到当前请求完成。\n\n"
                "确定关闭窗口？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            # 断开信号，让线程继续在后台静默完成，不再更新已销毁的 UI
            self._worker.log_line.disconnect()
            self._worker.finished_ok.disconnect()
            self._worker.finished_err.disconnect()
            # 线程完成后自动删除自身（避免内存泄漏）
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker = None
        super().closeEvent(event)
