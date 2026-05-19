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
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
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

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg      = cfg
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

        self._btn_start.setEnabled(False)
        self._log.clear()
        self._progress.setVisible(True)
        self._status_label.setText("正在同步……")

        # parent=None：不让 dialog 管理 worker 的生命周期，
        # 避免 dialog 关闭时 Qt 析构正在运行的 QThread 导致崩溃
        self._worker = _SyncWorker(cfg=cfg, parent=None)
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
