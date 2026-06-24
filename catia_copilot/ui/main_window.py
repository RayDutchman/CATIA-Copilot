"""
主应用程序窗口模块。

提供：
- MainWindow – 带有分组按钮 UI 和菜单栏的主 QMainWindow。
"""

import ctypes
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

import win32con
import win32gui
from PySide6.QtCore import (
    QByteArray,
    QPoint,
    QSettings,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from catia_copilot.catia.connection import get_catia_v5_application, open_document
from catia_copilot.catia.conversion import convert_drawing_to_pdf, convert_part_to_step
from catia_copilot.catia.dependencies import (
    find_drawing_for_part,
    find_part_for_drawing,
)
from catia_copilot.catia.drawing_operations import generate_drawing, refresh_drawing
from catia_copilot.catia.macro import CATIA_COPILOT_MODULES
from catia_copilot.catia.macro import run_macro as _catia_run_macro
from catia_copilot.catia.part_from_template import create_part_from_template
from catia_copilot.catia.template import apply_part_template
from catia_copilot.constants import (
    ABOUT_TEXT,
    AI_TAB_LABEL,
    APP_NAME,
    CRACK_DIR_PATH,
    FONT_FILE_PATH,
    ISO_XML_FILE_PATH,
    MAIN_WINDOW_DEFAULT_HEIGHT,
    MAIN_WINDOW_DEFAULT_WIDTH,
)
from catia_copilot.logging_setup import LOG_FILE, log_signal_emitter
from catia_copilot.ui.ai_chat_panel import AIChatPanel
from catia_copilot.ui.bom_edit_dialog import BomEditDialog
from catia_copilot.ui.bom_edit_dialog_v2 import BomEditDialogV2
from catia_copilot.ui.bom_edit_dialog_v3 import BomEditDialogV3
from catia_copilot.ui.catia_embed import (
    DEFAULT_ANCHOR,
    DEFAULT_ANCHOR_DX,
    DEFAULT_ANCHOR_DY,
    CATIAEmbedManager,
)
from catia_copilot.ui.catia_sidebar import CATIASidebarManager
from catia_copilot.ui.convert_dialog import FileConvertDialog
from catia_copilot.ui.export_bom_dialog import ExportBomDialog
from catia_copilot.ui.find_deps_dialog import FindDependenciesDialog
from catia_copilot.ui.help_dialog import HelpDialog
from catia_copilot.ui.mass_props_dialog import MassPropsDialog
from catia_copilot.ui.plm_sync_dialog import PlmSyncDialog
from catia_copilot.ui.plm_workbench import PlmWorkbench
from catia_copilot.ui.template_dialog import TemplateDialog
from catia_copilot.ui.theme_manager import theme_manager
from catia_copilot.utils import (
    check_catia_connection,
    detect_catia_root,
    diagnose_catia_connection,
    resource_path,
)

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """主应用程序窗口。"""

    # 嵌入面板回调信号（从后台线程 emit，自动投递到主线程）
    # 参数：(action_key, view_hwnd)
    _embed_action_signal = Signal(str, int)

    # 快速运行宏支持 CATScript（.catvbs / .catscript）和 VBA（.catvba）文件。
    _MACRO_EXTENSIONS: frozenset[str] = frozenset({".catvbs", ".catscript", ".catvba"})

    # 功能动作的显示名称，嵌入菜单和主菜单按钮共用，避免硬编码不一致
    _ACTION_LABELS: dict[str, str] = {
        "bom_edit":        "BOM 工作台",
        "bom_export":      "从产品导出 BOM",
        "mass_props":      "质量特性工作台",
        "plm_workbench":   "PLM 工作台",
        "export_pdf":      "从图纸导出 PDF",
        "export_stp":      "从产品/零件导出 STP",
        "drawing_new":     "新建图纸 (Python)",
        "drawing_refresh": "刷新图纸 (Python)",
        "stamp_template":  "刷写零件模板",
        "fastener_asm":    "快速装配紧固件",
        "nut_plate_asm":   "快速装配托板螺母",
        "open_related":    "在图纸/零件间切换",
        "find_deps":       "查找指向的文档",
        "run_macro":       "运行宏…",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(MAIN_WINDOW_DEFAULT_WIDTH, MAIN_WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(600, 600)

        # 连接嵌入面板回调信号到槽（必须在 _embed_manager 创建前）
        self._embed_action_signal.connect(self._handle_embed_action)

        self._build_ui()
        self._build_connection_indicator()
        self.statusBar().showMessage("就绪")

        # 应用主题 QSS（全局，对话框等顶层窗口均跟随）
        theme_manager.register(self)

        # CATIA 吸附边栏管理器（默认关闭，用户在"≡"页手动开启）
        self._sidebar_manager = CATIASidebarManager(self)
        self._sidebar_manager.sidebar_mode_changed.connect(self._on_sidebar_mode_changed)

        # CATIA 3D 视图嵌入管理器（默认关闭）
        # 从 QSettings 读取上次保存的面板位置，首次使用时取模块默认值
        _embed_settings = QSettings("CATIACopilot", "EmbedPanel")
        _anchor    = _embed_settings.value("position/anchor",    DEFAULT_ANCHOR,    type=str)
        _anchor_dx = _embed_settings.value("position/anchor_dx", DEFAULT_ANCHOR_DX, type=int)
        _anchor_dy = _embed_settings.value("position/anchor_dy", DEFAULT_ANCHOR_DY, type=int)
        self._embed_manager = CATIAEmbedManager(
            callbacks={
                "bom_edit":        self._open_bom_dialog_from_embed,
                "bom_export":      self._open_export_bom_from_embed,
                "mass_props":      self._open_mass_props_from_embed,
                "plm_sync":        self._open_plm_sync_from_embed,
                "plm_workbench":   self._open_plm_workbench_from_embed,
                "export_pdf":      self._open_export_pdf_from_embed,
                "export_stp":      self._open_export_stp_from_embed,
                "drawing_new":     self._open_drawing_new_from_embed,
                "drawing_refresh": self._open_drawing_refresh_from_embed,
                "stamp_template":  self._open_stamp_template_from_embed,
                "fastener_asm":    self._open_fastener_asm_from_embed,
                "nut_plate_asm":   self._open_nut_plate_asm_from_embed,
                "open_related":    self._open_related_from_embed,
                "find_deps":       self._open_find_deps_from_embed,
                "run_macro":       self._open_run_macro_from_embed,
                "run_macro_file":  self._run_macro_file_from_embed,
                "close":           self._close_embed_from_panel,
            },
            anchor=_anchor,
            anchor_dx=_anchor_dx,
            anchor_dy=_anchor_dy,
            position_changed_callback=self._on_embed_position_changed,
        )

        # 恢复嵌入面板的上次启用状态（_build_ui 已完成，按钮已存在）
        if _embed_settings.value("active", False, type=bool):
            self._toggle_embed()

        # 读取对话框置顶偏好（默认开启），初始化按钮状态
        _mw_settings = QSettings("CATIACopilot", "MainWindow")
        self._dlg_topmost: bool = _mw_settings.value("dlg_topmost", True, type=bool)
        self._btn_dlg_topmost.setChecked(self._dlg_topmost)

    # ── CATIA 连接状态指示器 ──────────────────────────────────────────────

    def _build_connection_indicator(self) -> None:
        """在状态栏右侧添加 CATIA 连接状态指示标签，并启动定时轮询。"""
        self._catia_status_label = QLabel()
        self._catia_status_label.setObjectName("catiaStatusLabel")
        self._catia_status_label.setToolTip(
            "CATIA V5 COM 连接状态（每 5 秒自动刷新）\n"
            "橙色表示 COM 对象可获取但功能测试失败，\n"
            "可通过菜单「帮助 -> CATIA 连接诊断」查看详情"
        )
        self.statusBar().addPermanentWidget(self._catia_status_label)

        # 立即检测一次，再每 5 秒轮询一次
        self._update_connection_status()
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(5000)
        self._connection_timer.timeout.connect(self._update_connection_status)
        self._connection_timer.start()

    def _update_connection_status(self) -> None:
        """轮询 CATIA 连接状态并更新指示标签的文字和样式。"""
        status = check_catia_connection()
        if status == "connected":
            self._catia_status_label.setText("● CATIA 已连接")
            self._catia_status_label.setProperty("catiaConnected", "true")
        elif status == "broken":
            self._catia_status_label.setText("⚠ CATIA 连接异常")
            self._catia_status_label.setProperty("catiaConnected", "broken")
        else:
            self._catia_status_label.setText("● CATIA 未连接")
            self._catia_status_label.setProperty("catiaConnected", "false")
        # 强制重新应用 QSS（动态属性变化后需要刷新样式）
        self._catia_status_label.style().unpolish(self._catia_status_label)
        self._catia_status_label.style().polish(self._catia_status_label)

    def _show_catia_diagnostics(self) -> None:
        """运行 CATIA COM 详细诊断并以对话框形式呈现结果。"""
        info = diagnose_catia_connection()
        status = info["status"]
        is_elevated = bool(info.get("is_elevated"))
        catia_running = bool(info.get("catia_process_running"))

        status_text = {
            "connected":    "✅ 已连接（功能测试通过）",
            "broken":       "⚠️ 连接异常",
            "disconnected": "❌ 未连接",
        }.get(status, status)

        elevated_text = "是（管理员）" if is_elevated else "否（普通用户）"
        process_text  = "运行中" if catia_running else "未检测到"

        lines = [
            f"<b>连接状态：</b>{status_text}",
            f"<b>本程序权限：</b>{elevated_text}",
            f"<b>CNEXT.exe 进程：</b>{process_text}",
        ]

        # ── 已连接：显示连接细节 ─────────────────────────────────────────
        if status == "connected":
            if info["app_name"]:
                lines.append(f"<b>应用名称：</b>{info['app_name']}")
            if info.get("is_v5") is not None:
                lines.append(
                    "<b>产品类型：</b>CATIA V5 ✅"
                    if info["is_v5"]
                    else "<b>产品类型：</b>3DEXPERIENCE ⚠️"
                )
            if info["doc_count"] is not None:
                lines.append(f"<b>已打开文档数：</b>{info['doc_count']}")
            if info["active_doc"]:
                lines.append(f"<b>当前活动文档：</b>{info['active_doc']}")
            else:
                lines.append("<b>当前活动文档：</b>（无）")

        # ── 连接异常：区分权限不匹配方向 ────────────────────────────────
        elif status == "broken" and catia_running:
            if is_elevated:
                # 本程序管理员，CATIA 普通用户
                lines += [
                    "",
                    "<b>根本原因：</b>本程序以<b>管理员</b>权限运行， CATIA 以<b>普通用户</b>"
                    "权限运行。 Windows UAC 隔离机制导致管理员进程无法看到普通用户进程注册的"
                    " ROT 对象。",
                    "<b>解决方案：</b>以<b>普通用户身份（不提权）</b>直接运行本程序。",
                ]
            else:
                lines += [
                    "",
                    "<b>根本原因：</b>CATIA 进程存在，但所有 COM 连接方式均失败。"
                    "最常见原因： CATIA 以<b>管理员</b>权限运行，而本程序以<b>普通用户</b>"
                    "权限运行（UAC ROT 隔离）。",
                    "<b>解决方案：</b>将 CATIA 改为<b>普通用户</b>权限运行（取消「以管理员身份运行」），"
                    "使两侧权限级别一致。",
                ]

        # ── 未连接 ───────────────────────────────────────────────────────
        elif status == "disconnected":
            lines += [
                "",
                "<b>原因：</b>未检测到运行中的 CATIA V5 进程。",
                "<b>建议：</b>请先启动 CATIA V5，再重试。",
            ]

        html = "<br/>".join(lines)

        msg = QMessageBox(self)
        msg.setWindowTitle("CATIA 连接诊断")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(html)
        msg.exec()

    @staticmethod
    def _detect_crack_version_subdir(catia_root: str) -> str | None:
        """从 CATIA 安装路径末尾（如 B28、B33）推断 crack 子目录名（如 R28、R33）。

        例：``C:\\Program Files\\Dassault Systemes\\B33`` → ``"R33"``
        若路径末尾不符合 ``B\\d+`` 格式，则返回 None。
        """
        name = Path(catia_root).name.upper()
        m = re.match(r"^B(\d+)$", name)
        if m:
            return f"R{m.group(1)}"
        return None

    def _run_copy_elevated(self, operations: list[tuple[Path, Path]]) -> bool:
        """以管理员权限批量复制文件（ShellExecuteExW + WaitForSingleObject）。

        写入临时批处理文件，通过 UAC「runas」动词以管理员身份静默执行，
        同步等待完成后清理临时文件。

        :param operations: ``[(src_path, dest_path), ...]`` 复制操作列表。
        :returns: ``True`` 表示 UAC 提权已接受并等待完成；
                  ``False`` 表示用户取消 UAC 或系统调用失败。
                  返回 True **不保证**文件一定写入成功，调用方需自行验证目标文件。
        """

        if not operations:
            return True

        # 构建批处理文件内容（若目标目录不存在则先创建）
        lines = ["@echo off"]
        for src, dest in operations:
            parent = str(dest.parent)
            lines.append(f'if not exist "{parent}" mkdir "{parent}"')
            lines.append(f'copy /Y "{src}" "{dest}"')
        bat_content = "\r\n".join(lines) + "\r\n"

        # 写入临时 .bat 文件（CATIA 路径通常为 ASCII，GBK 可安全表示）
        bat_path = ""
        try:
            fd, bat_path = tempfile.mkstemp(suffix=".bat")
            with os.fdopen(fd, "w", encoding="gbk", errors="replace") as f:
                f.write(bat_content)
        except Exception as exc:
            logger.warning(f"创建临时批处理文件失败：{exc}")
            return False

        # SHELLEXECUTEINFOW 结构体（Windows SDK 定义）
        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_HIDE = 0

        class _SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize",         wintypes.DWORD),
                ("fMask",          wintypes.ULONG),
                ("hwnd",           wintypes.HWND),
                ("lpVerb",         wintypes.LPCWSTR),
                ("lpFile",         wintypes.LPCWSTR),
                ("lpParameters",   wintypes.LPCWSTR),
                ("lpDirectory",    wintypes.LPCWSTR),
                ("nShow",          ctypes.c_int),
                ("hInstApp",       wintypes.HINSTANCE),
                ("lpIDList",       ctypes.c_void_p),
                ("lpClass",        wintypes.LPCWSTR),
                ("hkeyClass",      wintypes.HKEY),
                ("dwHotKey",       wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),   # union hIcon/hMonitor
                ("hProcess",       wintypes.HANDLE),
            ]

        sei = _SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f'/c "{bat_path}"'
        sei.lpDirectory = None
        sei.nShow = SW_HIDE

        accepted = False
        try:
            ok = bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)))
            if ok and sei.hProcess:
                # 等待 cmd.exe 执行完毕（最多 60 秒）
                WAIT_TIMEOUT = 0x00000102
                ret = ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 60000)
                ctypes.windll.kernel32.CloseHandle(sei.hProcess)
                if ret == WAIT_TIMEOUT:
                    logger.warning("提权复制操作等待超时（60 秒），请手动确认结果。")
                accepted = True
            else:
                logger.info("ShellExecuteExW 返回失败，用户可能取消了 UAC 提权。")
        except Exception as exc:
            logger.warning(f"ShellExecuteExW 调用异常：{exc}")
        finally:
            try:
                if bat_path:
                    os.unlink(bat_path)
            except Exception:
                pass

        return accepted

    # ── 无边框 UI 构建入口 ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        """构建主窗口 UI 结构（TabWidget 分页 + 状态栏）。"""
        # ── Tab 分页内容区 ──────────────────────────────────────────────────
        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("mainTabWidget")  # 专属样式：Tab 标题 padding + 按钮高度
        self._tab_widget.addTab(self._build_workbench_page(), "工作台")  # 0
        self._tab_widget.addTab(self._build_export_page(),    "导出")    # 1
        self._tab_widget.addTab(self._build_drawing_page(),   "模板")    # 2
        self._tab_widget.addTab(self._build_tools_page(),     "工具")    # 3

        # AI 助手 Tab
        self._ai_chat_panel = AIChatPanel()
        self._tab_widget.addTab(self._ai_chat_panel, AI_TAB_LABEL)       # 4

        self._tab_widget.addTab(self._build_more_page(),      "≡")       # 5

        # ── 嵌入式日志面板（位于 Tab 下方、状态栏上方）─────────────────────
        self._log_panel = self._build_log_panel()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tab_widget, stretch=1)
        layout.addWidget(self._log_panel)
        self.setCentralWidget(central)

        # 隐藏默认菜单栏
        self.menuBar().hide()

    def _build_log_panel(self) -> QWidget:
        """构建嵌入在主窗口底部的日志面板（默认隐藏）。
        
        布局策略：
        - panel 无固定高度，由 logView 的 maximumHeight 控制上限（≈10行）
        - content_stack 有 stretch=1，窗口空间不足时优先压缩 logView
        """
        panel = QWidget()
        panel.setObjectName("logPanel")
        panel.setVisible(False)
        # 面板最小高度：底部工具栏 + 至少1行日志
        panel.setMinimumHeight(60)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setObjectName("logView")
        # 用 QSS 中指定的字体（9pt Consolas）直接计算行高，
        # 避免在 QSS 应用前调用 widget.fontMetrics() 拿到错误的默认字体。
        fm = QFontMetrics(QFont("Consolas", 9))
        line_h = fm.lineSpacing()
        # 最多显示 10 行 + 少量内边距；最小保证 3 行可见
        self._log_text.setMaximumHeight(line_h * 10 + 12)
        self._log_text.setMinimumHeight(line_h * 3 + 8)
        layout.addWidget(self._log_text)

        # 底部工具栏：打开日志文件按钮 + 路径标签
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        open_btn = QPushButton("打开日志文件")
        # Fixed 策略：按钮只占 sizeHint 宽度，不会因布局拉伸而变大
        open_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        open_btn.clicked.connect(self._open_log_file)
        path_label = QLabel(f"Log: {LOG_FILE}")
        path_label.setObjectName("logPathLabel")
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bottom.addWidget(open_btn)
        bottom.addWidget(path_label, stretch=1)
        layout.addLayout(bottom)

        # 连接日志信号
        log_signal_emitter.message_logged.connect(self._append_log)

        return panel

    def _append_log(self, message: str) -> None:
        """将日志消息追加到嵌入式日志面板并滚动到底部。"""
        self._log_text.appendPlainText(message)
        self._log_text.verticalScrollBar().setValue(
            self._log_text.verticalScrollBar().maximum()
        )

    def _open_log_file(self) -> None:
        """在系统默认编辑器中打开日志文件。"""
        try:
            if sys.platform == "win32":
                os.startfile(str(LOG_FILE))
            else:
                subprocess.Popen(
                    ["xdg-open", str(LOG_FILE)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            QMessageBox.warning(
                self, "无法打开日志文件",
                f"无法打开日志文件：\n{LOG_FILE}\n\n{e}",
            )

    # ── 更多功能菜单 ───────────────────────────────────────────────────────

    def _toggle_log_from_menu(self) -> None:
        """切换嵌入式日志面板的显示 / 隐藏状态。"""
        self._log_panel.setVisible(not self._log_panel.isVisible())

    # ── 分页构建辅助 ───────────────────────────────────────────────────────

    @staticmethod
    def _make_page(content_widget: QWidget) -> QWidget:
        """将内容控件包裹在带内边距的滚动区域中，返回页面 QWidget。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content_widget)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll)
        return page

    @staticmethod
    def _make_section_label(text: str) -> QLabel:
        """创建带样式的节标题标签（用于页面内分区）。"""
        label = QLabel(text.upper())
        label.setObjectName("sectionLabel")
        return label

    # ── 导出页面 ───────────────────────────────────────────────────────────

    def _build_workbench_page(self) -> QWidget:
        """构建"工作台"功能页。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        layout.addWidget(self._make_section_label("工作台"))

        btn_bom_edit = QPushButton(self._ACTION_LABELS["bom_edit"])
        btn_bom_edit.setToolTip("在表格中编辑 BOM 属性并写回 CATIA")
        btn_bom_edit.clicked.connect(self._open_bom_edit_dialog)

        btn_bom_edit_v2 = QPushButton("BOM 工作台 V2（即时写回）")
        btn_bom_edit_v2.setToolTip("编辑即时写回 CATIA 的新版 BOM 工作台（V2）")
        btn_bom_edit_v2.clicked.connect(self._open_bom_edit_dialog_v2)

        btn_bom_edit_v3 = QPushButton("BOM 工作台 V3（part_master 架构）")
        btn_bom_edit_v3.setToolTip("V3 架构：part_master/instance 分离，同文件多实例天然共享属性")
        btn_bom_edit_v3.clicked.connect(self._open_bom_edit_dialog_v3)

        btn_mass_props = QPushButton(self._ACTION_LABELS["mass_props"])
        btn_mass_props.setToolTip(
            "遍历产品树，读取零件质量/重心/转动惯量，计算产品总质量特性并导出"
        )
        btn_mass_props.clicked.connect(self._open_mass_props_dialog)

        btn_plm_workbench = QPushButton(self._ACTION_LABELS["plm_workbench"])
        btn_plm_workbench.setToolTip(
            "打开 PLM 工作台——整合连接管理、增量同步、Tag 规则、产品注册与历史记录"
        )
        btn_plm_workbench.clicked.connect(self._open_plm_workbench)

        for btn in (btn_bom_edit, btn_bom_edit_v2, btn_bom_edit_v3, btn_mass_props, btn_plm_workbench):
            layout.addWidget(btn)

        layout.addStretch()
        return self._make_page(body)

    # ── 导出页面 ───────────────────────────────────────────────────────────

    def _build_export_page(self) -> QWidget:
        """构建"导出"功能页。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        layout.addWidget(self._make_section_label("导出"))

        btn_bom_export = QPushButton(self._ACTION_LABELS["bom_export"])
        btn_bom_export.setToolTip("从 CATProduct 导出 BOM 到 Excel 文件")
        btn_bom_export.clicked.connect(self._open_export_bom_dialog)

        btn_drawing = QPushButton(self._ACTION_LABELS["export_pdf"])
        btn_drawing.setToolTip("将 CATDrawing 文件批量导出为 PDF")
        btn_drawing.clicked.connect(self._open_convert_drawing_dialog)

        btn_part = QPushButton(self._ACTION_LABELS["export_stp"])
        btn_part.setToolTip("将 CATPart 或 CATProduct 文件批量导出为 STEP")
        btn_part.clicked.connect(self._open_convert_part_dialog)

        for btn in (btn_bom_export, btn_drawing, btn_part):
            layout.addWidget(btn)

        layout.addStretch()
        return self._make_page(body)

    # ── 图纸页面 ───────────────────────────────────────────────────────────

    def _build_drawing_page(self) -> QWidget:
        """构建"模板"功能页。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        # ── 零件模板 ────────────────────────────────────────────────────────
        layout.addWidget(self._make_section_label("零件模板"))

        btn_part_from_tpl = QPushButton("从模板新建零件")
        btn_part_from_tpl.setToolTip(
            "以当前活动 CATPart 为模板，通过 NewFrom 创建新零件\n"
            "（保留参数、关系、几何图形集结构、公式、发布等所有知识工程内容）"
        )
        btn_part_from_tpl.clicked.connect(self._open_part_from_template_dialog)
        layout.addWidget(btn_part_from_tpl)

        # ── 工程图纸 (Python 实现) ───────────────────────────────────────────
        layout.addWidget(self._make_section_label("工程图纸 (Python 实现)"))

        btn_new_py = QPushButton(self._ACTION_LABELS["drawing_new"])
        btn_new_py.setToolTip("从 CATPart/CATProduct 生成 CATDrawing 图纸 - Python 实现版本")
        btn_new_py.clicked.connect(self._open_generate_drawing_dialog_python)

        btn_refresh_py = QPushButton(self._ACTION_LABELS["drawing_refresh"])
        btn_refresh_py.setToolTip("刷新当前活动图纸的参数信息（从对应零件/产品同步属性）- Python 实现版本")
        btn_refresh_py.clicked.connect(self._open_refresh_drawing_dialog_python)

        for btn in (btn_new_py, btn_refresh_py):
            layout.addWidget(btn)

        # VBScript 实现版本（旧，用于对比测试）
        layout.addWidget(self._make_section_label("工程图纸 (VBScript 宏)"))

        btn_new = QPushButton("新建图纸 (VBScript)")
        btn_new.setToolTip("从 CATPart/CATProduct 生成 CATDrawing 图纸 - VBScript 宏版本")
        btn_new.clicked.connect(self._open_generate_drawing_dialog)

        btn_refresh = QPushButton("刷新图纸 (VBScript)")
        btn_refresh.setToolTip("刷新当前活动图纸的参数信息（从对应零件/产品同步属性）- VBScript 宏版本")
        btn_refresh.clicked.connect(self._open_refresh_drawing_dialog)

        for btn in (btn_new, btn_refresh):
            layout.addWidget(btn)

        layout.addStretch()
        return self._make_page(body)

    # ── 工具页面 ───────────────────────────────────────────────────────────

    def _build_tools_page(self) -> QWidget:
        """构建"工具"功能页。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        # ── CATIA 资源 ────────────────────────────────────────────────
        layout.addWidget(self._make_section_label("CATIA 资源"))

        btn_font = QPushButton("复制字体文件到 CATIA 目录")
        btn_font.setToolTip("将 Changfangsong.ttf 复制到 CATIA 字体目录")
        btn_font.clicked.connect(self._copy_font_to_catia)

        btn_iso = QPushButton("复制 ISO.xml 到 CATIA 目录")
        btn_iso.setToolTip("将 ISO.xml 复制到 CATIA 标准目录")
        btn_iso.clicked.connect(self._copy_iso_to_catia)

        btn_crack = QPushButton("Crack")
        btn_crack.setToolTip("将 crack 文件夹中的文件复制到 CATIA bin 目录")
        btn_crack.clicked.connect(self._crack)

        for btn in (btn_font, btn_iso, btn_crack):
            layout.addWidget(btn)

        layout.addSpacing(4)

        # ── 功能 ──────────────────────────────────────────────────────
        layout.addWidget(self._make_section_label("功能"))

        btn_stamp = QPushButton(self._ACTION_LABELS["stamp_template"])
        btn_stamp.setToolTip("为选中的 CATPart 添加标准用户自定义属性")
        btn_stamp.clicked.connect(self._open_stamp_part_template_dialog)
        layout.addWidget(btn_stamp)

        # 快速装配：两个按钮并排
        asm_row = QHBoxLayout()
        asm_row.setSpacing(6)
        btn_fastener = QPushButton(self._ACTION_LABELS["fastener_asm"])
        btn_fastener.setToolTip("在产品中连续放置紧固件实例")
        btn_fastener.clicked.connect(self._open_fastener_assembly_dialog)
        btn_nut = QPushButton(self._ACTION_LABELS["nut_plate_asm"])
        btn_nut.setToolTip("在产品中连续放置托板螺母实例")
        btn_nut.clicked.connect(self._open_nut_plate_assembly_dialog)
        asm_row.addWidget(btn_fastener)
        asm_row.addWidget(btn_nut)
        layout.addLayout(asm_row)

        btn_open_related = QPushButton(self._ACTION_LABELS["open_related"])
        btn_open_related.setToolTip(
            "自动判断当前活跃文档类型：\n"
            "• CATPart / CATProduct → 查找对应 CATDrawing\n"
            "• CATDrawing → 查找对应 CATPart / CATProduct"
        )
        btn_open_related.clicked.connect(self._open_related_file_for_active_doc)
        layout.addWidget(btn_open_related)

        btn_deps = QPushButton(self._ACTION_LABELS["find_deps"])
        btn_deps.setToolTip("通过 CATIA COM 查找文件的所有引用文档")
        btn_deps.clicked.connect(self._open_find_dependencies_dialog)
        layout.addWidget(btn_deps)

        # 「运行宏…」按钮，点击后弹出 QMenu 列出宏文件
        self._btn_run_macro = QPushButton(self._ACTION_LABELS["run_macro"])
        self._btn_run_macro.setToolTip("选择并运行一个宏文件")
        self._btn_run_macro.clicked.connect(lambda: self._show_macro_menu())
        layout.addWidget(self._btn_run_macro)

        btn_macro_folder = QPushButton("打开宏文件夹")
        btn_macro_folder.setToolTip("在资源管理器中打开 macros 目录")
        btn_macro_folder.clicked.connect(self._open_macros_folder)
        layout.addWidget(btn_macro_folder)

        layout.addStretch()
        return self._make_page(body)

    # ── 更多页面 ───────────────────────────────────────────────────────────

    def _build_more_page(self) -> QWidget:
        """构建"≡"功能页（视图、帮助）。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        # ── 视图 ──────────────────────────────────────────────────────
        layout.addWidget(self._make_section_label("视图"))

        btn_log = QPushButton("显示 / 隐藏日志")
        btn_log.setToolTip("切换底部嵌入式日志面板")
        btn_log.clicked.connect(self._toggle_log_from_menu)
        layout.addWidget(btn_log)

        self._btn_embed = QPushButton("嵌入 3D 视图按钮")
        self._btn_embed.setCheckable(True)
        self._btn_embed.setObjectName("toggleButton")
        self._btn_embed.setToolTip(
            "开启后，在 CATIA V5 每个 3D 视图右上角显示功能菜单按钮\n"
            "点击按钮可快速访问 BOM 、导出、图纸、工具等功能\n"
            "（需要 CATIA V5 正在运行）"
        )
        self._btn_embed.clicked.connect(self._toggle_embed)
        layout.addWidget(self._btn_embed)

        self._btn_dlg_topmost = QPushButton("对话框置顶")
        self._btn_dlg_topmost.setCheckable(True)
        self._btn_dlg_topmost.setObjectName("toggleButton")
        self._btn_dlg_topmost.setToolTip(
            "开启：功能对话框始终浮于其他窗口之上\n"
            "关闭：对话框与普通窗口平级，CATIA 弹窗可正常显示在前台"
        )
        self._btn_dlg_topmost.clicked.connect(self._toggle_dlg_topmost)
        layout.addWidget(self._btn_dlg_topmost)

        btn_diag = QPushButton("CATIA 连接诊断")
        btn_diag.setToolTip("显示 CATIA COM 连接的详细诊断信息")
        btn_diag.clicked.connect(self._show_catia_diagnostics)
        layout.addWidget(btn_diag)

        # 主题切换：在系统深色/浅色之间切换（通过 QGuiApplication.styleHints）
        _mode_label = {"dark": "切换到浅色", "light": "切换到深色"}
        self._btn_theme = QPushButton(_mode_label.get(theme_manager.current_mode(), "切换主题"))
        self._btn_theme.setToolTip("在系统深色/浅色主题之间切换")
        self._btn_theme.clicked.connect(self._toggle_theme)
        layout.addWidget(self._btn_theme)

        layout.addSpacing(4)

        # ── 帮助 ──────────────────────────────────────────────────────
        layout.addWidget(self._make_section_label("帮助"))

        btn_help = QPushButton("文档")
        btn_help.clicked.connect(self._show_help)
        layout.addWidget(btn_help)

        btn_about = QPushButton(f"关于 {APP_NAME}")
        btn_about.clicked.connect(self._show_about)
        layout.addWidget(btn_about)

        btn_template = QPushButton("模板对话框")
        btn_template.setToolTip("打开空对话框模板（用于测试）")
        btn_template.clicked.connect(self._open_template_dialog)
        layout.addWidget(btn_template)

        layout.addStretch()
        return self._make_page(body)

    def _show_macro_menu(self, pos: QPoint | None = None) -> None:
        """在指定位置或「运行宏…」按钮下方弹出宏文件菜单。

        对 catia_copilot.catvba 展开为子菜单，列出 CATIA_COPILOT_MODULES 中的模块；
        其他宏文件直接作为一级菜单项，点击后走兼容轮询运行。

        :param pos: 菜单弹出位置（全局坐标），None 时使用主窗口按钮位置
        """
        macros_dir = self._macros_dir()
        macro_files: list[Path] = []
        if macros_dir.is_dir():
            macro_files = sorted(
                f for f in macros_dir.iterdir()
                if f.is_file() and f.suffix.lower() in self._MACRO_EXTENSIONS
            )

        menu = QMenu(self)
        if macro_files:
            for mp in macro_files:
                if mp.name.lower() == "catia_copilot.catvba":
                    # 展开为子菜单，每项对应一个已注册模块
                    submenu = QMenu(mp.name, menu)
                    for key, mod_name in CATIA_COPILOT_MODULES.items():
                        action = submenu.addAction(mod_name)
                        action.setToolTip(f"{mp.name} → {mod_name}")
                        action.triggered.connect(
                            lambda checked=False, p=mp, m=mod_name:
                                self._run_macro(p, module_name=m)
                        )
                    menu.addMenu(submenu)
                else:
                    action = menu.addAction(mp.name)
                    action.setToolTip(str(mp))
                    action.triggered.connect(lambda checked=False, p=mp: self._run_macro(p))
        else:
            empty = menu.addAction("（未找到宏文件）")
            empty.setEnabled(False)

        if pos is None:
            pos = self._btn_run_macro.mapToGlobal(self._btn_run_macro.rect().bottomLeft())
        menu.exec(pos)

    def _toggle_log_window(self, checked: bool) -> None:
        """切换日志窗口的显示/隐藏状态（保留，供外部调用）。"""
        if checked:
            self._log_window.show()
            self._log_window.raise_()
        else:
            self._log_window.hide()

    def _show_about(self) -> None:
        """显示关于对话框。"""
        QMessageBox.about(self, f"About {APP_NAME}", ABOUT_TEXT)

    def _toggle_theme(self) -> None:
        """在系统深色/浅色主题之间切换，并更新按钮文字。"""
        hints = QGuiApplication.styleHints()
        if theme_manager.current_mode() == "dark":
            hints.setColorScheme(Qt.ColorScheme.Light)
            self._btn_theme.setText("切换到深色")
        else:
            hints.setColorScheme(Qt.ColorScheme.Dark)
            self._btn_theme.setText("切换到浅色")

    def _show_help(self) -> None:
        """显示帮助文档对话框。"""
        self._show_dialog("_dlg_help", lambda: HelpDialog(self))

    def _open_template_dialog(self) -> None:
        """打开模板对话框。"""
        self._show_dialog("_dlg_template", lambda: TemplateDialog(self))

    # ── 非模态对话框管理 ──────────────────────────────────────────────────

    def _show_dialog(self, attr: str, factory: Callable[[], QDialog]) -> None:
        """以非模态方式打开对话框，若已存在则将其置于前台。

        所有对话框均为独立顶级窗口，在任务栏有独立条目。
        是否置顶由 self._dlg_topmost 控制（用户可在 ≡ 页切换）。

        :param attr: 用于在 MainWindow 上缓存对话框实例的属性名。
        :param factory: 无参可调用对象，返回新的 QDialog 实例。
        """
        dlg = getattr(self, attr, None)
        if dlg is None:
            dlg = factory()

            base_flags = (
                Qt.WindowType.Window
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.WindowMaximizeButtonHint
                | Qt.WindowType.WindowMinimizeButtonHint
            )
            if self._dlg_topmost:
                base_flags |= Qt.WindowType.WindowStaysOnTopHint

            dlg.setParent(None, base_flags)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dlg.destroyed.connect(lambda _=None, a=attr: self._on_dialog_destroyed(a))
            setattr(self, attr, dlg)

            # setParent(None) 会重建原生窗口并重置位置，在此之后重新恢复几何
            if hasattr(dlg, "_settings"):
                saved = dlg._settings.value("geometry")
                if isinstance(saved, QByteArray) and not saved.isEmpty():
                    dlg.restoreGeometry(saved)

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

        # 置顶时启动 CATIA 状态监听（跟随最小化/还原），不置顶时不监听
        if self._dlg_topmost:
            self._start_catia_monitor()

    def _on_dialog_destroyed(self, attr: str) -> None:
        """对话框被销毁时的回调，清理引用和隐藏记录。"""
        setattr(self, attr, None)
        if hasattr(self, "_hidden_dialogs") and attr in self._hidden_dialogs:
            self._hidden_dialogs.discard(attr)

    def _toggle_dlg_topmost(self) -> None:
        """切换对话框置顶开关，立即对所有已开对话框生效，并持久化。"""
        self._dlg_topmost = self._btn_dlg_topmost.isChecked()
        QSettings("CATIACopilot", "MainWindow").setValue("dlg_topmost", self._dlg_topmost)
        for attr, value in vars(self).items():
            if attr.startswith("_dlg_") and isinstance(value, (QDialog, QMainWindow)):
                self._apply_topmost(value)
        # 置顶时启动监听，不置顶时停止监听（并还原被隐藏的对话框）
        if self._dlg_topmost:
            self._start_catia_monitor()
        else:
            self._stop_catia_monitor()
        msg = "对话框已设为置顶" if self._dlg_topmost else "对话框已取消置顶"
        self.statusBar().showMessage(msg, 3000)

    def _apply_topmost(self, dlg) -> None:
        """用 Win32 API 直接修改窗口的 WS_EX_TOPMOST 标志，无需 show()，无闪烁。

        HWND_TOPMOST   (-1)：设置为置顶（WS_EX_TOPMOST）
        HWND_NOTOPMOST (-2)：取消置顶
        """
        try:
            hwnd = int(dlg.winId())
            SWP_NOMOVE    = 0x0002
            SWP_NOSIZE    = 0x0001
            SWP_NOACTIVATE = 0x0010
            insert_after = win32con.HWND_TOPMOST if self._dlg_topmost else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            logger.debug(f"_apply_topmost: {e}")

    def _start_catia_monitor(self) -> None:
        """启动 CATIA 窗口状态监听定时器（仅置顶模式下使用）。"""
        if hasattr(self, "_catia_monitor_timer") and self._catia_monitor_timer.isActive():
            return
        self._catia_monitor_timer = QTimer(self)
        self._catia_monitor_timer.timeout.connect(self._check_catia_state)
        self._catia_monitor_timer.start(500)
        self._catia_was_minimized = False
        self._hidden_dialogs: set[str] = set()
        self._dialog_geometries: dict[str, bytes] = {}

    def _stop_catia_monitor(self) -> None:
        """停止 CATIA 窗口状态监听定时器，并还原所有被隐藏的对话框。"""
        if hasattr(self, "_catia_monitor_timer") and self._catia_monitor_timer.isActive():
            self._catia_monitor_timer.stop()
        # 还原所有因监听而隐藏的对话框
        for attr in list(getattr(self, "_hidden_dialogs", [])):
            value = getattr(self, attr, None)
            if value is not None and isinstance(value, (QDialog, QMainWindow)):
                geom = getattr(self, "_dialog_geometries", {}).get(attr)
                value.show()
                if geom:
                            value.restoreGeometry(QByteArray(geom))
        self._hidden_dialogs = set()
        self._dialog_geometries = {}

    def _check_catia_state(self) -> None:
        """检查 CATIA 窗口状态，同步对话框的显示/隐藏（仅置顶模式下运行）。"""
        catia_hwnd = self._get_catia_hwnd()
        if not catia_hwnd:
            return
        is_minimized = win32gui.IsIconic(catia_hwnd)
        if is_minimized != self._catia_was_minimized:
            self._catia_was_minimized = is_minimized
            if is_minimized:
                self._hidden_dialogs.clear()
                self._dialog_geometries.clear()
                for attr, value in vars(self).items():
                    if attr.startswith("_dlg_") and isinstance(value, (QDialog, QMainWindow)) and value.isVisible():
                        self._dialog_geometries[attr] = bytes(value.saveGeometry())
                        value.hide()
                        self._hidden_dialogs.add(attr)
            else:
                for attr in list(self._hidden_dialogs):
                    value = getattr(self, attr, None)
                    if value is not None and isinstance(value, (QDialog, QMainWindow)):
                        value.show()
                        geom = self._dialog_geometries.get(attr)
                        if geom:
                            value.restoreGeometry(QByteArray(geom))
                self._hidden_dialogs.clear()
                self._dialog_geometries.clear()

    def _get_catia_hwnd(self) -> int:
        """查找 CATIA V5 主窗口句柄，未找到返回 0。"""
        catia_hwnd = 0
        def _find(hwnd: int, _) -> bool:
            nonlocal catia_hwnd
            try:
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).startswith("CATIA V5"):
                    catia_hwnd = hwnd
                    return False
            except Exception:
                pass
            return True
        try:
            win32gui.EnumWindows(_find, None)
        except Exception:
            pass
        return catia_hwnd

    def _set_catia_as_owner(self, dlg) -> None:
        """已废弃：Win32 Owner 机制在已显示窗口上修改 GWLP_HWNDPARENT 属于未定义行为，
        会导致多对话框场景下崩溃。保留方法签名避免调用方报错，实际不执行任何操作。"""
        pass

    # ── CATIA 吸附边栏 ────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        """切换 CATIA 吸附边栏模式的开关。"""
        if self._sidebar_manager.is_active or self._sidebar_manager._timer.isActive():
            self._sidebar_manager.stop()
            self.statusBar().showMessage("已关闭 CATIA 吸附模式", 3000)
        else:
            self._sidebar_manager.start()
            self.statusBar().showMessage("已开启 CATIA 吸附模式，等待 CATIA V5 窗口…", 3000)

    def _on_sidebar_mode_changed(self, active: bool) -> None:
        """吸附状态改变时更新状态栏提示。"""
        if active:
            self.statusBar().showMessage("✔ 已吸附到 CATIA V5 右侧", 4000)
        else:
            self.statusBar().showMessage("CATIA 未检测到，等待中…", 3000)

    # ── CATIA 3D 视图嵌入 ─────────────────────────────────────────────────

    def _toggle_embed(self) -> None:
        """切换 CATIA 3D 视图嵌入面板的开关。"""
        _s = QSettings("CATIACopilot", "EmbedPanel")
        if self._embed_manager.is_active:
            self._embed_manager.stop()
            self._btn_embed.setChecked(False)
            self.statusBar().showMessage("已关闭 3D 视图嵌入模式", 3000)
            _s.setValue("active", False)
        else:
            ok = self._embed_manager.start()
            if ok:
                self._btn_embed.setChecked(True)
                self.statusBar().showMessage("✔ 已在 CATIA 3D 视图中嵌入菜单面板", 4000)
                _s.setValue("active", True)
            else:
                self._btn_embed.setChecked(False)
                self.statusBar().showMessage("未检测到 CATIA V5，请先启动 CATIA", 4000)

    # 以下四个方法均在 win32 后台线程中被调用，
    # 通过 QTimer.singleShot(0, ...) 安全派发到 Qt 主线程。

    def _open_bom_dialog_from_embed(self) -> None:
        """嵌入面板菜单 → BOM 属性补全。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("bom_edit", view_hwnd)

    def _open_export_bom_from_embed(self) -> None:
        """嵌入面板菜单 → BOM 导出。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("bom_export", view_hwnd)

    def _open_mass_props_from_embed(self) -> None:
        """嵌入面板菜单 → 质量特性。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("mass_props", view_hwnd)

    def _close_embed_from_panel(self) -> None:
        """嵌入面板菜单 → 关闭面板（同步更新主窗口按钮文字）。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("close", view_hwnd)

    def _open_plm_sync_from_embed(self) -> None:
        """嵌入面板菜单 → 同步 BOM 到 PLM 。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("plm_sync", view_hwnd)

    def _open_plm_workbench_from_embed(self) -> None:
        """嵌入面板菜单 → PLM 工作台。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("plm_workbench", view_hwnd)

    def _open_export_pdf_from_embed(self) -> None:
        """嵌入面板菜单 → CATDrawing → PDF 。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("export_pdf", view_hwnd)

    def _open_export_stp_from_embed(self) -> None:
        """嵌入面板菜单 → CATPart/CATProduct → STP 。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("export_stp", view_hwnd)

    def _open_drawing_new_from_embed(self) -> None:
        """嵌入面板菜单 → 新建图纸。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("drawing_new", view_hwnd)

    def _open_drawing_refresh_from_embed(self) -> None:
        """嵌入面板菜单 → 刷新图纸。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("drawing_refresh", view_hwnd)

    def _open_stamp_template_from_embed(self) -> None:
        """嵌入面板菜单 → 刷写零件模板。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("stamp_template", view_hwnd)

    def _open_fastener_asm_from_embed(self) -> None:
        """嵌入面板菜单 → 快速装配紧固件。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("fastener_asm", view_hwnd)

    def _open_nut_plate_asm_from_embed(self) -> None:
        """嵌入面板菜单 → 快速装配托板螺母。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("nut_plate_asm", view_hwnd)

    def _open_related_from_embed(self) -> None:
        """嵌入面板菜单 → 打开关联图纸/零件。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("open_related", view_hwnd)

    def _open_find_deps_from_embed(self) -> None:
        """嵌入面板菜单 → 查找所有依赖项。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("find_deps", view_hwnd)

    def _open_run_macro_from_embed(self) -> None:
        """嵌入面板菜单 → 运行宏…。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("run_macro", view_hwnd)

    def _run_macro_file_from_embed(self) -> None:
        """嵌入面板菜单 → 直接运行指定宏文件（从后台线程回调，派发到主线程）。"""
        view_hwnd = self._embed_manager._current_view_hwnd or 0
        self._embed_action_signal.emit("run_macro_file", view_hwnd)

    @Slot(str, int)
    def _handle_embed_action(self, action: str, view_hwnd: int) -> None:
        """处理嵌入面板回调信号（在主线程中执行）。
        
        Parameters
        ----------
        action : str
            动作 key（如 "bom_edit"）
        view_hwnd : int
            触发该动作的 MDI 子窗口句柄（0 表示从主窗口按钮触发）
        """
        action_map = {
            "bom_edit":        self._do_open_bom_dialog,
            "bom_export":      self._do_open_export_bom,
            "mass_props":      self._do_open_mass_props,
            "close":           self._do_close_embed,
            "plm_sync":        self._do_open_plm_sync,
            "plm_workbench":   self._do_open_plm_workbench,
            "export_pdf":      self._do_open_export_pdf,
            "export_stp":      self._do_open_export_stp,
            "drawing_new":     self._do_open_drawing_new,
            "drawing_refresh": self._do_open_drawing_refresh,
            "stamp_template":  self._do_open_stamp_template,
            "fastener_asm":    self._do_open_fastener_asm,
            "nut_plate_asm":   self._do_open_nut_plate_asm,
            "open_related":    self._do_open_related,
            "find_deps":       self._do_open_find_deps,
            "run_macro":       self._do_open_run_macro,
            "run_macro_file":  self._do_run_macro_file,
        }
        handler = action_map.get(action)
        if handler:
            handler()
        else:
            logger.warning("未知的嵌入面板动作: %s", action)

    @Slot()
    def _do_open_bom_dialog(self) -> None:
        """在主线程打开 BomEditDialog。"""
        self._open_bom_edit_dialog()

    @Slot()
    def _do_open_export_bom(self) -> None:
        """在主线程打开 ExportBomDialog。"""
        self._open_export_bom_dialog()

    @Slot()
    def _do_open_mass_props(self) -> None:
        """在主线程打开 MassPropsDialog。"""
        self._open_mass_props_dialog()

    @Slot()
    def _do_close_embed(self) -> None:
        """在主线程停止嵌入管理器并更新按钮状态。"""
        self._embed_manager.stop()
        self._btn_embed.setChecked(False)
        QSettings("CATIACopilot", "EmbedPanel").setValue("active", False)
        self.statusBar().showMessage("已关闭 3D 视图嵌入模式", 3000)

    @Slot()
    def _do_open_plm_sync(self) -> None:
        """在主线程打开 PLM 同步对话框。"""
        self._open_plm_sync_dialog()

    @Slot()
    def _do_open_plm_workbench(self) -> None:
        """在主线程打开 PLM 工作台。"""
        self._open_plm_workbench()

    @Slot()
    def _do_open_export_pdf(self) -> None:
        """在主线程打开 CATDrawing → PDF 对话框。"""
        self._open_convert_drawing_dialog()

    @Slot()
    def _do_open_export_stp(self) -> None:
        """在主线程打开 CATPart/CATProduct → STP 对话框。"""
        self._open_convert_part_dialog()

    @Slot()
    def _do_open_drawing_new(self) -> None:
        """在主线程打开新建图纸对话框。"""
        self._open_generate_drawing_dialog_python()

    @Slot()
    def _do_open_drawing_refresh(self) -> None:
        """在主线程打开刷新图纸对话框。"""
        self._open_refresh_drawing_dialog_python()

    @Slot()
    def _do_open_stamp_template(self) -> None:
        """在主线程打开刷写零件模板对话框。"""
        self._open_stamp_part_template_dialog()

    @Slot()
    def _do_open_fastener_asm(self) -> None:
        """在主线程运行快速装配紧固件宏。"""
        self._open_fastener_assembly_dialog()

    @Slot()
    def _do_open_nut_plate_asm(self) -> None:
        """在主线程运行快速装配托板螺母宏。"""
        self._open_nut_plate_assembly_dialog()

    @Slot()
    def _do_open_related(self) -> None:
        """在主线程打开关联图纸/零件。"""
        self._open_related_file_for_active_doc()

    @Slot()
    def _do_open_find_deps(self) -> None:
        """在主线程打开查找所有依赖项对话框。"""
        self._open_find_dependencies_dialog()

    @Slot()
    def _do_open_run_macro(self) -> None:
        """在主线程弹出运行宏菜单。"""
        self._show_macro_menu()

    @Slot()
    def _do_run_macro_file(self) -> None:
        """在主线程直接运行嵌入面板选中的宏文件。"""
        macro_path_str = getattr(self._embed_manager, "_current_macro_path", None)
        if not macro_path_str:
            logger.warning("_do_run_macro_file: 未找到宏文件路径")
            return
        module_name = getattr(self._embed_manager, "_current_macro_module", None)
        self._run_macro(Path(macro_path_str), module_name=module_name)

    def _on_embed_position_changed(self, anchor: str, dx: int, dy: int) -> None:
        """
        嵌入面板位置变化后的回调（在 win32 后台线程中调用）。
        通过 QTimer.singleShot 派发到主线程写入 QSettings。
        """
        QTimer.singleShot(
            0,
            lambda: self._save_embed_position(anchor, dx, dy),
        )

    def _save_embed_position(self, anchor: str, dx: int, dy: int) -> None:
        """在主线程将面板位置持久化到 QSettings。"""
        s = QSettings("CATIACopilot", "EmbedPanel")
        s.setValue("position/anchor",    anchor)
        s.setValue("position/anchor_dx", dx)
        s.setValue("position/anchor_dy", dy)
        s.sync()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """用户手动调整宽度时同步记录，供吸附模式复用。"""
        super().resizeEvent(event)
        if not self._sidebar_manager.is_active:
            # 非吸附状态下记住用户设置的宽度
            self._sidebar_manager._sidebar_width = self.width()

    def closeEvent(self, event) -> None:  # noqa: N802
        """主窗口关闭时，清理所有资源：对话框、嵌入面板、吸附边栏、定时器。

        由于子窗口通过 ``setParent(None, ...)`` 清除了 Qt 父引用（以获得独立
        的任务栏条目），Qt 的默认父子关闭机制对其无效，需在此手动关闭。
        所有子窗口均设有 ``WA_DeleteOnClose``，close() 会触发其销毁和清理。
        """
        # 停止嵌入面板
        if hasattr(self, "_embed_manager"):
            self._embed_manager.stop()

        # 停止吸附边栏
        if hasattr(self, "_sidebar_manager"):
            self._sidebar_manager.stop()

        # 停止 CATIA 状态监听定时器
        self._stop_catia_monitor()

        # 停止 AI Agent（如果正在运行）
        if hasattr(self, "_ai_chat_panel"):
            self._ai_chat_panel.stop_agent()

        # 关闭所有对话框和子窗口（包括 QDialog 和 QMainWindow）
        for attr, value in list(vars(self).items()):
            if attr.startswith("_dlg_") and isinstance(value, (QDialog, QMainWindow)):
                value.close()

        super().closeEvent(event)

    # ── 宏辅助方法 ────────────────────────────────────────────────────────

    def _macros_dir(self) -> Path:
        """返回宏文件夹路径。"""
        return resource_path("macros")

    def _open_macros_folder(self) -> None:
        macros_dir = self._macros_dir()
        macros_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(macros_dir))
            else:
                subprocess.Popen(
                    ["xdg-open", str(macros_dir)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            QMessageBox.warning(
                self, "无法打开文件夹", f"无法打开宏文件夹：\n{macros_dir}\n\n{e}"
            )

    def _run_macro(
        self,
        macro_path: Path,
        module_name: str | None = None,
        params: list | None = None,
    ) -> None:
        """运行宏文件（UI 包装层）。

        文件不存在时弹警告；执行失败时弹错误对话框。
        具体执行逻辑见 catia_copilot.catia.macro.run_macro。
        """
        if not macro_path.exists():
            QMessageBox.warning(self, "文件不存在", f"宏文件不存在：\n{macro_path}")
            return
        try:
            _catia_run_macro(macro_path, module_name=module_name, params=params)
        except Exception as e:
            logger.error(f"宏执行失败 {macro_path.name}: {e}")
            QMessageBox.critical(
                self, "宏执行失败",
                f"运行宏时出错：\n{macro_path.name}\n\n{e}",
            )

    # ── Dialog launchers ───────────────────────────────────────────────────

    def _open_convert_part_dialog(self) -> None:
        self._show_dialog("_dlg_convert_part", lambda: FileConvertDialog(
            parent=self,
            title="从产品/零件导出 STP",
            file_label="已选 CATPart/CATProduct 文件:",
            file_filter="*.CATPart *.CATProduct (*.CATPart *.CATProduct);;All Files (*)",
            no_files_msg="请至少选择一个 CATPart 或 CATProduct 文件。",
            conversion_fn=convert_part_to_step,
            settings_key="CATPart",
            show_prefix_option=True,
            prefix="MD_",
            note="暂时留空",
        ))

    def _open_convert_drawing_dialog(self) -> None:
        self._show_dialog("_dlg_convert_drawing", lambda: FileConvertDialog(
            parent=self,
            title="从图纸导出 PDF",
            file_label="已选 CATDrawing 文件:",
            file_filter="*.CATDrawing (*.CATDrawing);;All Files (*)",
            no_files_msg="请至少选择一个 CATDrawing 文件。",
            conversion_fn=convert_drawing_to_pdf,
            settings_key="CATDrawing",
            show_prefix_option=True,
            prefix="DR_",
            show_update_option=True,
            note=(
                "如果用于导出的 CATDrawing 有多页，请将 CATIA 设置为"
                "\u201c将多页文档保存在单向量文件中\u201d"
                "（工具->选项->常规->兼容性->图形格式->导出（另存为））"
            ),
        ))

    def _open_export_bom_dialog(self) -> None:
        self._show_dialog("_dlg_export_bom", lambda: ExportBomDialog(self))

    def _open_bom_edit_dialog(self) -> None:
        self._show_dialog("_dlg_bom_edit", lambda: BomEditDialog(self))

    @Slot()
    def _open_bom_edit_dialog_v2(self) -> None:
        self._show_dialog("_dlg_bom_edit_v2", lambda: BomEditDialogV2(self))

    @Slot()
    def _open_bom_edit_dialog_v3(self) -> None:
        self._show_dialog("_dlg_bom_edit_v3", lambda: BomEditDialogV3(self))

    def _open_mass_props_dialog(self) -> None:
        self._show_dialog("_dlg_mass_props", lambda: MassPropsDialog(self))

    def _open_plm_sync_dialog(self) -> None:
        def factory():
            dlg = PlmSyncDialog(self)
            # 同步运行期间暂停 CATIA 连接检查，避免 COM 调用持有 GIL 阻塞 Worker 线程
            dlg.sync_started.connect(self._connection_timer.stop)
            dlg.sync_done.connect(self._connection_timer.start)
            return dlg
        self._show_dialog("_dlg_plm_sync", factory)

    def _open_plm_workbench(self) -> None:
        """打开 PLM 工作台（非模态独立窗口，单例）。"""
        self._show_dialog("_dlg_plm_workbench", lambda: PlmWorkbench(self))

    def _open_stamp_part_template_dialog(self) -> None:
        self._show_dialog("_dlg_stamp_template", lambda: FileConvertDialog(
            parent=self,
            title="刷写零件模板",
            file_label="已选 CATPart 文件:",
            file_filter="*.CATPart (*.CATPart);;All Files (*)",
            no_files_msg="请至少选择一个 CATPart 文件。",
            conversion_fn=apply_part_template,
            settings_key="StampPartTemplate",
            show_active_doc_option=True,
        ))

    def _open_find_dependencies_dialog(self) -> None:
        self._show_dialog("_dlg_find_deps", lambda: FindDependenciesDialog(self))

    # ------------------------------------------------------------------
    # 图纸 ↔ 零件/产品 互相查找（单入口，自动判断活跃文档类型）
    # ------------------------------------------------------------------

    def _open_related_file_for_active_doc(self) -> None:
        """根据当前活跃文档类型，自动查找并打开关联文件。

        - CATPart / CATProduct → 启发式查找对应 CATDrawing （文件名/PartNumber 匹配）
        - CATDrawing → 正向查询（COM 视图链接）+ 启发式查找对应 CATPart / CATProduct
        - 其他格式 → 提示不支持
        """

        # 1. 获取当前活跃文档
        try:
            app       = get_catia_v5_application()
            full_name = app.ActiveDocument.FullName
        except Exception as e:
            QMessageBox.warning(self, "无法访问 CATIA", f"无法获取当前活跃文档：\n{e}")
            return

        ext = full_name.lower().endswith

        # 2. 按类型分支
        if ext((".catpart", ".catproduct")):
            candidates: list[str] = []

            # 启发式：文件名/PartNumber 匹配
            try:
                heu = find_drawing_for_part(full_name)
                for p in heu:
                    if p not in candidates:
                        candidates.append(p)
            except Exception:
                pass

            empty_msg  = f"未能找到对应的 CATDrawing 。\n\n零件：{Path(full_name).name}"
            pick_title = "选择要打开的图纸"

        elif ext(".catdrawing",):
            candidates = []

            # doc_file_links 策略已包含在 find_part_for_drawing 中（COM 视图链接优先）
            try:
                heu = find_part_for_drawing(full_name)
                for p in heu:
                    if p not in candidates:
                        candidates.append(p)
            except Exception:
                pass

            empty_msg  = (
                f"未能找到对应的 CATPart 或 CATProduct 。\n\n图纸：{Path(full_name).name}"
            )
            pick_title = "选择要打开的零件/产品"

        else:
            QMessageBox.information(
                self, "不支持的文档类型",
                f"当前活跃文档不是 CATPart / CATProduct / CATDrawing ：\n{full_name}",
            )
            return

        # 3. 无结果
        if not candidates:
            QMessageBox.information(self, "未找到关联文件", empty_msg)
            return

        # 4. 单结果直接打开；多结果弹选择框
        chosen = self._pick_one_file(candidates, pick_title)
        if chosen:
            try:
                open_document(chosen, foreground=True)
            except Exception as e:
                QMessageBox.critical(
                    self, "打开文件失败",
                    f"无法在 CATIA 中打开文件：\n{chosen}\n\n错误：{e}",
                )

    def _pick_one_file(self, paths: list[str], title: str) -> str | None:
        """若只有一个候选直接返回；否则弹出列表对话框让用户选择。

        用户取消时返回 None。
        """
        if len(paths) == 1:
            return paths[0]

        dlg  = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(560, 300)
        vlay = QVBoxLayout(dlg)

        hint = QLabel(f"找到 {len(paths)} 个候选文件，请选择一个：")
        vlay.addWidget(hint)

        lst = QListWidget()
        for p in paths:
            item = QListWidgetItem(p)
            item.setToolTip(p)
            lst.addItem(item)
        lst.setCurrentRow(0)
        vlay.addWidget(lst)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vlay.addWidget(btns)

        # 双击也确认
        lst.itemDoubleClicked.connect(lambda _: dlg.accept())

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        selected = lst.currentItem()
        return selected.text() if selected else None

    def _open_fastener_assembly_dialog(self) -> None:
        """运行 catia_copilot.catvba 中的 fastener_assembly 模块。"""
        catvba_path = self._macros_dir() / "catia_copilot.catvba"
        if not catvba_path.exists():
            QMessageBox.warning(
                self, "宏文件未找到",
                f"未找到 VBA 宏文件：\n{catvba_path}\n\n"
                "请将 catia_copilot.catvba 放入 macros 文件夹后重试。",
            )
            return
        self._run_macro(catvba_path, module_name=CATIA_COPILOT_MODULES["fastener_assembly"])

    def _open_nut_plate_assembly_dialog(self) -> None:
        """运行 catia_copilot.catvba 中的 nut_plate_assembly 模块。"""
        catvba_path = self._macros_dir() / "catia_copilot.catvba"
        if not catvba_path.exists():
            QMessageBox.warning(
                self, "宏文件未找到",
                f"未找到 VBA 宏文件：\n{catvba_path}\n\n"
                "请将 catia_copilot.catvba 放入 macros 文件夹后重试。",
            )
            return
        self._run_macro(catvba_path, module_name=CATIA_COPILOT_MODULES["nut_plate_assembly"])

    # ── Drawing generation ─────────────────────────────────────────────────

    def _drawing_templates_dir(self) -> Path:
        return resource_path("drawing_templates")

    def _open_generate_drawing_dialog(self) -> None:
        templates_dir = self._drawing_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)

        templates = sorted(templates_dir.glob("*.CATDrawing"))
        if not templates:
            QMessageBox.warning(
                self, "未找到模板",
                f"在以下目录中未找到任何 CATDrawing 模板文件：\n{templates_dir}\n\n"
                "请将 *.CATDrawing 模板放入该文件夹后重试。",
            )
            return

        name, ok = QInputDialog.getItem(
            self,
            "选择图纸模板",
            "请选择一个 CATDrawing 模板：",
            [t.name for t in templates],
            0,
            False,
        )
        if not ok:
            return

        template_path = templates_dir / name

        # 优先使用同名的 .catvbs 脚本；若不存在则提示用户
        catvbs_path = self._macros_dir() / "generate_drawing.catvbs"
        if not catvbs_path.exists():
            QMessageBox.warning(
                self, "宏文件未找到",
                f"未找到 CATScript 宏文件：\n{catvbs_path}\n\n"
                "请将 generate_drawing.catvbs 放入 macros 文件夹后重试。",
            )
            return
        self._run_macro(catvbs_path, params=[str(template_path)])

    def _open_refresh_drawing_dialog(self) -> None:
        """刷新当前活动图纸的参数信息（通过 refresh_drawing_info.catvbs 宏）。"""
        catvbs_path = self._macros_dir() / "refresh_drawing_info.catvbs"
        if not catvbs_path.exists():
            QMessageBox.warning(
                self, "宏文件未找到",
                f"未找到 CATScript 宏文件：\n{catvbs_path}\n\n"
                "请将 refresh_drawing_info.catvbs 放入 macros 文件夹后重试。",
            )
            return
        self._run_macro(catvbs_path)

    # ── Part from template ──────────────────────────────────────────────────

    def _part_templates_dir(self) -> Path:
        return resource_path("part_templates")

    def _open_part_from_template_dialog(self) -> None:
        """从模板新建零件 — 从 part_templates 目录选择 CATPart 模板，NewFrom 创建新零件。"""
        templates_dir = self._part_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)

        templates = sorted(templates_dir.glob("*.CATPart"))
        if not templates:
            QMessageBox.warning(
                self, "未找到模板",
                f"在以下目录中未找到任何 CATPart 模板文件：\n{templates_dir}\n\n"
                "请将 *.CATPart 模板放入该文件夹后重试。",
            )
            return

        name, ok = QInputDialog.getItem(
            self,
            "选择零件模板",
            "请选择一个 CATPart 模板：",
            [t.name for t in templates],
            0,
            False,
        )
        if not ok:
            return

        template_path = templates_dir / name

        def input_callback(title: str, default: str) -> tuple[str, bool]:
            text, ok = QInputDialog.getText(
                self,
                "输入新零件号",
                f"请输入新零件的 PartNumber\n（留空则自动使用：{default}）：",
                text="",
            )
            return (text, ok)

        try:
            result = create_part_from_template(
                template_path=str(template_path),
                input_callback=input_callback,
            )

            if result["success"]:
                QMessageBox.information(self, "新建零件成功", result["message"])
            else:
                QMessageBox.critical(self, "新建零件失败", result["message"])

        except Exception as e:
            QMessageBox.critical(self, "新建零件失败", f"发生错误：\n{e}")

    # ── Drawing generation (Python implementation) ──────────────────────────

    def _open_generate_drawing_dialog_python(self) -> None:
        """新建图纸 - Python 实现版本"""
        
        templates_dir = self._drawing_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)

        templates = sorted(templates_dir.glob("*.CATDrawing"))
        if not templates:
            QMessageBox.warning(
                self, "未找到模板",
                f"在以下目录中未找到任何 CATDrawing 模板文件：\n{templates_dir}\n\n"
                "请将 *.CATDrawing 模板放入该文件夹后重试。",
            )
            return

        name, ok = QInputDialog.getItem(
            self,
            "选择图纸模板",
            "请选择一个 CATDrawing 模板：",
            [t.name for t in templates],
            0,
            False,
        )
        if not ok:
            return

        template_path = templates_dir / name

        # 定义输入回调函数（当属性不存在时弹窗询问用户）
        def input_callback(prop_name: str, part_number: str) -> tuple[str, bool]:
            text, ok = QInputDialog.getText(
                self,
                f"补充缺失属性 - {prop_name}",
                f'零件 "{part_number}" 中未找到用户自定义属性 "{prop_name}"。\n'
                f'请输入该属性的值（留空则以空值写入图纸）：',
                text="",
            )
            return (text, ok)

        # 调用 Python 实现的生成图纸函数
        try:
            result = generate_drawing(
                template_path=str(template_path),
                input_callback=input_callback,
            )

            if result["success"]:
                # 弹出 SaveAs 对话框，预填建议文件名
                suggested_name = result.get("suggested_name", "")
                save_path, ok = QFileDialog.getSaveFileName(
                    self,
                    "另存为",
                    suggested_name,
                    "CATDrawing (*.CATDrawing)",
                )
                if ok and save_path:
                    try:
                        result["drawing_doc"].SaveAs(save_path)
                    except Exception as e:
                        QMessageBox.critical(self, "保存图纸失败", f"SaveAs 失败：\n{e}")
            else:
                QMessageBox.critical(self, "生成图纸失败", result["message"])

        except Exception as e:
            QMessageBox.critical(
                self, "生成图纸失败",
                f"发生错误：\n{e}"
            )

    def _open_refresh_drawing_dialog_python(self) -> None:
        """刷新图纸 - Python 实现版本"""
        
        # 定义输入回调函数（当属性不存在时弹窗询问用户）
        def input_callback(prop_name: str, part_number: str) -> tuple[str, bool]:
            text, ok = QInputDialog.getText(
                self,
                f"补充缺失属性 - {prop_name}",
                f'零件 "{part_number}" 中未找到用户自定义属性 "{prop_name}"。\n'
                f'请输入该属性的值（留空则以空值写入图纸）：',
                text="",
            )
            return (text, ok)

        # 调用 Python 实现的刷新图纸函数
        try:
            result = refresh_drawing(input_callback=input_callback)
            
            if result["success"]:
                # 显示同步日志
                log_msg = "\n".join(result["details"])
                QMessageBox.information(self, "同步日志", log_msg)
            else:
                QMessageBox.critical(self, "刷新图纸失败", result["message"])
                
        except Exception as e:
            QMessageBox.critical(
                self, "刷新图纸失败",
                f"发生错误：\n{e}"
            )

    # ── CATIA resource file helpers ────────────────────────────────────────

    def _copy_font_to_catia(self) -> None:
        self._copy_file_to_catia(
            file_name=FONT_FILE_PATH,
            relative_dest=Path("win_b64") / "resources" / "fonts" / "TrueType",
        )

    def _copy_iso_to_catia(self) -> None:
        self._copy_file_to_catia(
            file_name=ISO_XML_FILE_PATH,
            relative_dest=Path("win_b64") / "resources" / "standard" / "drafting",
        )

    def _copy_file_to_catia(self, file_name: str, relative_dest: Path) -> None:
        src_file = resource_path(file_name)
        base_name = Path(file_name).name
        if not src_file.exists():
            QMessageBox.warning(
                self, "文件未找到",
                f"在工作目录中找不到 '{base_name}'：\n{src_file.parent}",
            )
            return

        catia_root = detect_catia_root()
        if catia_root:
            reply = QMessageBox.question(
                self, "检测到 CATIA 安装",
                f"检测到 CATIA 安装路径：\n{catia_root}\n\n是否使用该目录？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                catia_root = None

        if not catia_root:
            catia_root = QFileDialog.getExistingDirectory(
                self,
                "选择 CATIA 安装目录（例如 C:\\Program Files\\Dassault Systemes\\B28）",
                "",
            )
            if not catia_root:
                return

        dest_dir = Path(catia_root) / relative_dest
        if not dest_dir.exists():
            reply = QMessageBox.question(
                self, "文件夹未找到",
                f"目标文件夹不存在：\n{dest_dir}\n\n是否要创建该文件夹？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                dest_dir.mkdir(parents=True, exist_ok=True)
            else:
                return

        dest_file = dest_dir / base_name
        try:
            shutil.copy2(str(src_file), str(dest_file))
            QMessageBox.information(
                self, "成功",
                f"'{base_name}' 已成功复制到：\n{dest_file}",
            )
        except PermissionError:
            reply = QMessageBox.question(
                self, "权限不足",
                f"无法直接复制文件（权限不足）。\n\n"
                f"目标路径：\n{dest_file}\n\n"
                f"是否通过 UAC 提权以管理员身份重试？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                accepted = self._run_copy_elevated([(src_file, dest_file)])
                if accepted:
                    if dest_file.exists():
                        QMessageBox.information(
                            self, "成功",
                            f"'{base_name}' 已成功复制到：\n{dest_file}",
                        )
                    else:
                        QMessageBox.warning(
                            self, "结果未知",
                            f"提权复制已执行，但无法确认文件是否成功写入。\n"
                            f"请手动确认：\n{dest_file}",
                        )
                else:
                    QMessageBox.information(self, "已取消", "用户取消了 UAC 提权，文件未复制。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生意外错误：\n{e}")

    def _crack(self) -> None:
        base_src_dir = resource_path(CRACK_DIR_PATH)
        if not base_src_dir.exists() or not base_src_dir.is_dir():
            QMessageBox.warning(
                self, "文件夹未找到",
                f"找不到 'crack' 文件夹：\n{base_src_dir.parent}",
            )
            return

        catia_root = detect_catia_root()
        if catia_root:
            reply = QMessageBox.question(
                self, "检测到 CATIA 安装",
                f"检测到 CATIA 安装路径：\n{catia_root}\n\n是否使用该目录？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                catia_root = None

        if not catia_root:
            catia_root = QFileDialog.getExistingDirectory(
                self,
                "选择 CATIA 安装目录（例如 C:\\Program Files\\Dassault Systemes\\B28）",
                "",
            )
            if not catia_root:
                return

        # 按版本推断专属子目录（如安装路径末尾为 B28 → crack/R28）
        version_subdir = self._detect_crack_version_subdir(catia_root)
        src_dir = base_src_dir
        if version_subdir:
            versioned_dir = base_src_dir / version_subdir
            if versioned_dir.is_dir():
                src_dir = versioned_dir
                logger.info(f"使用版本专属 crack 目录：{src_dir}")
            else:
                reply = QMessageBox.question(
                    self, "找不到版本专属目录",
                    f"未找到版本专属 crack 子目录：\n{versioned_dir}\n\n"
                    f"是否改用通用 crack 根目录中的文件？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.No:
                    return
                # src_dir 保持为 base_src_dir（通用目录）

        dest_dir = Path(catia_root) / "win_b64" / "code" / "bin"
        if not dest_dir.exists():
            QMessageBox.critical(
                self, "文件夹未找到",
                f"目标文件夹不存在：\n{dest_dir}\n\n请检查您的 CATIA 安装。",
            )
            return

        files = [f for f in src_dir.iterdir() if f.is_file()]
        if not files:
            QMessageBox.warning(
                self, "文件夹为空",
                f"crack 源目录中没有文件：\n{src_dir}",
            )
            return

        try:
            copied: list[str] = []
            for src_file in files:
                dest_file = dest_dir / src_file.name
                shutil.copy2(str(src_file), str(dest_file))
                copied.append(src_file.name)
                logger.info(f"  Copied: {src_file.name} -> {dest_file}")
            QMessageBox.information(
                self, "成功",
                f"已成功复制 {len(copied)} 个文件到：\n{dest_dir}\n\n"
                + "\n".join(copied),
            )
        except PermissionError:
            reply = QMessageBox.question(
                self, "权限不足",
                f"无法直接复制文件（权限不足）。\n\n"
                f"目标路径：\n{dest_dir}\n\n"
                f"是否通过 UAC 提权以管理员身份重试？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                ops = [(f, dest_dir / f.name) for f in files]
                accepted = self._run_copy_elevated(ops)
                if accepted:
                    success_count = sum(1 for _, dst in ops if dst.exists())
                    if success_count == len(ops):
                        QMessageBox.information(
                            self, "成功",
                            f"已成功复制 {success_count} 个文件到：\n{dest_dir}\n\n"
                            + "\n".join(f.name for f in files),
                        )
                    else:
                        QMessageBox.warning(
                            self, "部分完成",
                            f"提权复制已执行，但仅确认 {success_count}/{len(ops)} 个文件写入成功。\n"
                            f"目标路径：\n{dest_dir}\n\n请手动确认复制结果。",
                        )
                else:
                    QMessageBox.information(self, "已取消", "用户取消了 UAC 提权，文件未复制。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生意外错误：\n{e}")
