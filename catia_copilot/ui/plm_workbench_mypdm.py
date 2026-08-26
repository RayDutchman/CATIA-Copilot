"""
myPDM 工作台主窗口。

独立非模态 QDialog，提供 myPDM 的 CAD 入口功能：
读取 CATIA 装配结构树 → myPDM BOM 匹配 → 内联编辑、签出/签入、属性推拉、文件导出上传。

使用方式：
    win = PlmWorkbench(parent)
    win.show()
    win.raise_()
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime

from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from catia_copilot.ui.bom_widgets import _RowHeightDelegate
from catia_copilot.plm.my_pdm_api_client import MyPdmApiClient
from catia_copilot.plm.api_client import PlmApiError
from catia_copilot.catia.assembly_reader import detect_catia_status, read_assembly_tree
from catia_copilot.ui.flatten_tree import flatten_tree, flatten_tree_hierarchical
from catia_copilot.ui.theme_manager import theme_manager

logger = logging.getLogger(__name__)

# ── QSettings 键 ────────────────────────────────────────────────────────────
_S_ORG     = "CATIACopilot"
_S_PLM_CFG = "PlmConfigMyPdm"   # myPDM 工作台专用配置，与其他工作台隔离
_S_WB      = "PlmWorkbenchMyPdm"

_DEFAULT_BASE_URL = "https://192.168.1.x:8443/api"
_DEFAULT_LOGIN    = ""
_DEFAULT_PASSWORD = ""

# ── CAD入口树委托 ────────────────────────────────────────────────────────────

class _CadTreeDelegate(_RowHeightDelegate):
    """CAD入口树的委托：控制行高(36px) + 限制只允许属性列编辑。"""

    def __init__(self, workbench):
        super().__init__(workbench._cad_tree if hasattr(workbench, "_cad_tree") else None)
        self._wb = workbench

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        if hint.height() < 36:
            hint.setHeight(36)
        return hint

    def createEditor(self, parent, option, index):
        wb = self._wb
        col = index.column()
        # 只允许以下列编辑：件号、版本、定义、术语、描述、自定义属性
        editable = False
        if hasattr(wb, "_CAD_COL_PN") and col == wb._CAD_COL_PN:
            editable = False  # 件号不可编辑
        elif hasattr(wb, "_CAD_COL_REV") and col == wb._CAD_COL_REV:
            editable = True
        elif hasattr(wb, "_CAD_COL_DEF") and col == wb._CAD_COL_DEF:
            editable = True
        elif hasattr(wb, "_CAD_COL_NOM") and col == wb._CAD_COL_NOM:
            editable = True
        elif hasattr(wb, "_CAD_COL_DESC") and col == wb._CAD_COL_DESC:
            editable = True
        elif hasattr(wb, "_CAD_COL_USER_START") and col >= wb._CAD_COL_USER_START and col < wb._CAD_COL_CAD_ATT:
            editable = True
        if editable:
            return super().createEditor(parent, option, index)
        return None

# ── 字段映射 ──────────────────────────────────────────────────────────────────

def _load_field_mapping() -> dict:
    """加载 CATIA-PDM 字段映射配置。"""
    import os as _os
    mapping_path = _os.path.join(_os.path.dirname(__file__), "..", "catia", "field_mapping.json")
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"builtin": {}, "properties": {}}


def _load_cad_naming() -> dict:
    """加载 CAD 附件命名前缀配置。"""
    import os as _os
    conf_path = _os.path.join(_os.path.dirname(__file__), "..", "catia", "cad_naming.conf")
    result = {"pdf_part": "DR_", "pdf_assembly": "ASY_", "stp": "MD_"}
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "CAD_PDF_PART_PREFIX":
                    result["pdf_part"] = v
                elif k == "CAD_PDF_ASSEMBLY_PREFIX":
                    result["pdf_assembly"] = v
                elif k == "CAD_STP_PREFIX":
                    result["stp"] = v
    except Exception:
        pass
    return result

# 差异状态计算：版本+迭代号相同时，比较本地 mtime 与 PLM checkInDate 的容差
# 60 秒内视为相等，避免文件系统时间精度差异导致误判
_DIFF_TIME_TOLERANCE_SEC = 60


class _ConnectWorker(QThread):
    """测试 myPDM 连接，返回当前用户信息。"""
    success = Signal(str, list, dict)
    failure = Signal(str)

    def __init__(self, base_url: str, login: str, password: str) -> None:
        super().__init__()
        self._base_url = base_url
        self._login    = login
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
                "_current_user_role": f"已登录·{user.role}",
            }
            self.success.emit(self._login, [user_info], user_info)
        except Exception as exc:
            self.failure.emit(str(exc))



class PlmWorkbench(QDialog):
    """myPDM 工作台主窗口（非模态）。

    布局：
        顶部工具栏  — 连接状态 + CAD入口 + 设置
        主体         — CAD 入口 BOM 匹配树
        底部状态栏  — 进度条 + 状态文本
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("myPDM 工作台")
        self.setMinimumSize(1100, 660)
        self.resize(1400, 800)

        s = QSettings(_S_ORG, _S_WB)
        saved_geom = s.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        try:
            theme_manager.register(self)
        except Exception:
            pass

        # 后台线程句柄
        self._workers: list[QThread] = []

        # ── 整体布局（垂直三段） ─────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # 内容区：CAD 入口页（占位，点击 CAD入口 时动态构建）
        self._cad_page = QWidget()
        self._cad_page_layout = QVBoxLayout(self._cad_page)
        self._cad_page_layout.setContentsMargins(0, 0, 0, 0)
        self._cad_page_layout.setSpacing(0)
        root.addWidget(self._cad_page, 1)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine); sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        root.addWidget(self._build_status_bar())

        # 初始化设置控件引用
        self._init_settings_controls()

    def closeEvent(self, event):
        s = QSettings(_S_ORG, _S_WB)
        s.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # 布局构建
    # ─────────────────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        """顶部工具栏：连接状态 + 设置 + CAD入口。"""
        bar = QWidget()
        bar.setFixedHeight(44)
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

        h.addStretch()

        # 设置
        btn_cfg = QPushButton("⚙ 设置")
        btn_cfg.setFont(_ef); btn_cfg.setFlat(True)
        btn_cfg.clicked.connect(self._on_show_settings)
        h.addWidget(btn_cfg)

        # CAD入口
        self._btn_cad_entry = QPushButton("🔧 CAD入口")
        self._btn_cad_entry.setFont(_ef); self._btn_cad_entry.setFlat(True)
        self._btn_cad_entry.setObjectName("primaryBtn")
        self._btn_cad_entry.setToolTip("读取 CATIA 装配结构，匹配 myPDM BOM")
        self._btn_cad_entry.clicked.connect(self._on_cad_entry)
        h.addWidget(self._btn_cad_entry)

        self._update_conn_status_bar()
        return bar

    def _build_status_bar(self) -> QWidget:
        """底部状态栏：进度条 + 状态文本 + 速度 + 汇总。"""
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

    def _update_conn_status_bar(self) -> None:
        base_url, login, _pw = self._read_conn()
        if base_url and login:
            self._lbl_conn_dot.setText("🟢")
            self._lbl_conn_dot.setStyleSheet("color: green;")
            self._lbl_conn_info.setText(f"{login}")
        else:
            self._lbl_conn_dot.setText("🔴")
            self._lbl_conn_dot.setStyleSheet("color: red;")
            self._lbl_conn_info.setText("未配置")

    # ─────────────────────────────────────────────────────────────────────────
    # 通用工具
    # ─────────────────────────────────────────────────────────────────────────

    def _read_conn(self) -> tuple[str, str, str]:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        return (
            s.value("base_url",  _DEFAULT_BASE_URL),
            s.value("login",     _DEFAULT_LOGIN),
            s.value("password",  _DEFAULT_PASSWORD),
        )

    def _save_conn(self) -> None:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())

    def _get_pdm_client(self) -> "MyPdmApiClient":
        """创建 myPDM API 客户端。"""
        _base_url, _login, _pw = self._read_conn()
        return MyPdmApiClient(_base_url)

    def _start_worker(self, worker: QThread) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def _log_to_conn(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        self._txt_conn_log.appendPlainText(f"[{ts}] [{prefix}] {msg}")

    def _on_show_settings(self) -> None:
        dlg = _SettingsDialog(self)
        dlg.exec()
        self._update_conn_status_bar()

    def _on_cad_entry(self) -> None:
        """CAD入口：CATIA 装配树 → myPDM BOM 匹配 → 内联编辑表格。"""
        base_url, login, password = self._read_conn()
        if not login or not password:
            QMessageBox.warning(self, "未登录", "请先在设置中配置 myPDM 并测试连接。")
            return

        # Step 1: 检测 CATIA
        self._log_to_conn("CAD入口：检测 CATIA……")
        status = detect_catia_status()
        if not status.get("active"):
            QMessageBox.warning(self, "CATIA 未运行", "请先启动 CATIA V5。")
            return
        if not status.get("has_document"):
            QMessageBox.warning(self, "未打开文档", "请在 CATIA 中打开一个装配体 (.CATProduct)。")
            return
        self._log_to_conn(f"CAD入口：CATIA 已连接 — {status.get('doc_name', '?')}", "ok")

        # Step 2: 读取装配结构
        self._log_to_conn("CAD入口：正在读取装配结构……")
        try:
            tree = read_assembly_tree()
        except Exception as e:
            self._log_to_conn(f"CAD入口：读取失败 — {e}", "error")
            return
        if tree is None:
            QMessageBox.warning(self, "读取失败", "无法读取装配结构，请确认当前文档为装配体。")
            return

        # Step 3: 层级树
        rows = flatten_tree_hierarchical(tree)

        # 加载字段映射
        self._cad_field_map = _load_field_mapping()

        # 将根装配体作为顶层节点
        root_row = {
            "instance_name": tree.get("instance_name", ""),
            "part_number": tree.get("part_number", ""),
            "path": tree.get("path", "0"),
            "level": 0,
            "is_assembly": tree.get("is_assembly", True),
            "quantity": 1,
            "instances": [{"matrix": tree.get("matrix"), "label": tree.get("instance_name", "")}],
            "doc_path": tree.get("doc_path", ""),
            "builtin": dict(tree.get("builtin", {})),
            "user_properties": dict(tree.get("user_properties", {})),
            "children": rows,
        }
        rows = [root_row]
        # 同时生成平铺列表用于 BOM 匹配
        flat_rows = flatten_tree(tree)
        self._log_to_conn(f"CAD入口：装配树 — {len(flat_rows)} 个零件节点", "ok")

        # Step 4: BOM 匹配（从层级树递归收集所有件号）
        self._log_to_conn("CAD入口：正在进行 myPDM BOM 匹配……")
        client = MyPdmApiClient(base_url)
        try:
            client.login(login, password)
        except PlmApiError as e:
            self._log_to_conn(f"CAD入口：myPDM 登录失败 — {e}", "error")
            return

        # 递归收集层级树中所有去重的 (code, version) 对
        seen = set()
        items = []

        def _collect_pns(tree_nodes):
            for node in tree_nodes:
                code = node.get("part_number", "").strip()
                if code and code not in seen:
                    seen.add(code)
                    version = node.get("builtin", {}).get("Revision", "")
                    items.append({"code": code, "version": version if version else None})
                children = node.get("children", [])
                if children:
                    _collect_pns(children)

        _collect_pns(rows)

        try:
            match_results = client.cad_bom_match(items)
        except PlmApiError as e:
            self._log_to_conn(f"CAD入口：BOM 匹配失败 — {e}", "error")
            return

        match_map = {}
        for r in match_results:
            match_map[r.code] = r

        matched = sum(1 for r in match_results if r.match_status == "matched")
        new_count = sum(1 for r in match_results if r.match_status == "new")
        conflict = sum(1 for r in match_results if r.match_status == "conflict")
        checked_out = sum(1 for r in match_results if r.checkout_status == "checked_out")
        self._log_to_conn(
            f"CAD入口：BOM 匹配完成 — 已匹配 {matched} / 可新建 {new_count} / 冲突 {conflict} / 已签出 {checked_out}",
            "ok",
        )

        # 存储数据
        self._cad_rows = flat_rows
        self._cad_tree_rows = rows
        self._cad_match_map = match_map
        self._cad_client = client

        # 查询附件计数
        self._cad_att_counts: dict[str, dict] = {}
        self._log_to_conn("CAD入口：查询附件计数……")
        for _pn, m in match_map.items():
            if m.match_status == "matched" and m.revision_id:
                try:
                    cad_atts = client.list_attachments(m.revision_id, "cad")
                    prod_atts = client.list_attachments(m.revision_id, "production")
                    self._cad_att_counts[_pn] = {"cad": len(cad_atts), "production": len(prod_atts)}
                except Exception:
                    self._cad_att_counts[_pn] = {"cad": 0, "production": 0}
        self._log_to_conn("CAD入口：附件计数查询完成", "ok")

        # Step 5: 构建树形页面
        self._build_cad_match_page()

        self._content_stack.setCurrentIndex(1)

    def _on_cad_back(self) -> None:
        """从 CAD入口 返回同步视图。"""
        self._content_stack.setCurrentIndex(0)

    def _build_cad_match_page(self) -> None:
        """构建 CAD入口 BOM 匹配树形页面（照抄 myPDM 列定义）。"""
        while self._cad_page_layout.count():
            item = self._cad_page_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # ── 顶部统计栏 ─────────────────────────────────────────────────────────
        summary_bar = QHBoxLayout()
        summary_bar.setContentsMargins(8, 4, 8, 4)
        summary_bar.setSpacing(12)

        matched = sum(1 for r in self._cad_match_map.values() if r.match_status == "matched")
        new_count = sum(1 for r in self._cad_match_map.values() if r.match_status == "new")
        conflict = sum(1 for r in self._cad_match_map.values() if r.match_status == "conflict")
        checked_out = sum(1 for r in self._cad_match_map.values() if r.checkout_status == "checked_out")

        counts = [
            (f"已匹配 {matched}", "#d4edda"),
            (f"可新建 {new_count}", "#fff3cd"),
            (f"冲突 {conflict}", "#f8d7da"),
            (f"已签出 {checked_out}", "#d1ecf1"),
        ]
        for text, color in counts:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"background:{color}; border-radius:3px; padding:2px 8px; font-weight:bold; font-size:11px;")
            summary_bar.addWidget(lbl)

        summary_bar.addStretch()
        for label, slot in [("🔄 重新匹配", self._on_cad_refresh), ("✅ 全部签入", self._on_cad_checkin_all), ("属性→ 批量推送", self._on_cad_push_all), ("← 返回", self._on_cad_back)]:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.clicked.connect(slot)
            summary_bar.addWidget(btn)

        self._cad_match_summary = QWidget()
        self._cad_match_summary.setLayout(summary_bar)
        self._cad_page_layout.addWidget(self._cad_match_summary)

        # ── 用户自定义属性列 ──────────────────────────────────────────────────
        self._cad_user_cols = ["存货类别", "规格型号", "物料类型", "重量(kg)"]
        self._cad_user_catia_map = {
            "存货类别": "存货类别",
            "规格型号": "规格型号",
            "物料类型": "物料类型",
            "重量(kg)": "重量",
        }

        # ── 列定义 ─────────────────────────────────────────────────────────────
        self._CAD_COL_LVL     = 0   # 层级
        self._CAD_COL_PN      = 1   # 件号
        self._CAD_COL_QTY     = 2   # 用量
        self._CAD_COL_REV     = 3   # 版本
        self._CAD_COL_DEF     = 4   # 定义
        self._CAD_COL_NOM     = 5   # 术语
        self._CAD_COL_DESC    = 6   # 描述
        self._CAD_COL_USER_START = 7
        self._CAD_COL_CAD_ATT  = 7 + len(self._cad_user_cols)
        self._CAD_COL_PROD_ATT = 8 + len(self._cad_user_cols)
        self._CAD_COL_PDM      = 9 + len(self._cad_user_cols)
        self._CAD_COL_MATCH    = 10 + len(self._cad_user_cols)
        self._CAD_COL_CO       = 11 + len(self._cad_user_cols)
        self._CAD_COL_OP       = 12 + len(self._cad_user_cols)

        headers = ["层级", "件号", "用量", "版本", "定义", "术语", "描述"]
        headers += self._cad_user_cols
        headers += ["CAD附件", "生产附件", "PDM匹配", "匹配状态", "签出状态", "操作"]

        self._cad_tree = QTreeWidget()
        self._cad_tree.setHeaderLabels(headers)
        self._cad_tree.setAnimated(True)
        self._cad_tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self._cad_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._cad_tree.customContextMenuRequested.connect(self._on_cad_tree_context_menu)
        self._cad_tree.setIndentation(16)
        self._cad_tree.setRootIsDecorated(True)
        self._cad_tree.setAlternatingRowColors(False)
        self._cad_tree.setStyleSheet("QTreeView::item { border-bottom: 1px solid #e0e0e0; }")
        # 使用委托控制行高和可编辑列
        self._cad_tree.setItemDelegate(_CadTreeDelegate(self))
        self._cad_tree.setAlternatingRowColors(False)

        hdr = self._cad_tree.header()
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(self._CAD_COL_PN, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(self._CAD_COL_PN, 160)
        hdr.setSectionResizeMode(self._CAD_COL_PDM, QHeaderView.ResizeMode.Stretch)
        for ci, w in [
            (self._CAD_COL_LVL, 60), (self._CAD_COL_QTY, 50), (self._CAD_COL_REV, 60),
            (self._CAD_COL_DEF, 80), (self._CAD_COL_NOM, 80), (self._CAD_COL_DESC, 80),
            (self._CAD_COL_CAD_ATT, 70), (self._CAD_COL_PROD_ATT, 70),
            (self._CAD_COL_PDM, 120), (self._CAD_COL_MATCH, 70),
            (self._CAD_COL_CO, 70), (self._CAD_COL_OP, 140),
        ]:
            hdr.setSectionResizeMode(ci, QHeaderView.ResizeMode.Fixed)
            hdr.resizeSection(ci, w)

        self._cad_page_layout.addWidget(self._cad_tree, 1)
        self._cad_tree.itemChanged.connect(self._on_cad_item_changed)
        self._populate_cad_tree(self._cad_tree, self._cad_tree_rows)
        self._cad_tree.expandAll()

    def _populate_cad_tree(self, tree: QTreeWidget, rows: list[dict], parent=None) -> None:
        """递归填充 BOM 树（照抄 myPDM 表格列内容）。"""
        for row in rows:
            pn = row.get("part_number", "")
            builtin = row.get("builtin", {})
            user_props = row.get("user_properties", {})
            qty = str(row.get("quantity", 1))
            match = self._cad_match_map.get(pn)

            # 件号显示
            node = QTreeWidgetItem(parent or tree)
            # 层级（dash 前缀格式：0, -1, --2, ---3）
            level = row.get("level", 0)
            level_text = "-" * level + str(level) if level > 0 else "0"
            node.setText(self._CAD_COL_LVL, level_text)
            node.setTextAlignment(self._CAD_COL_LVL, Qt.AlignCenter)
            node.setText(self._CAD_COL_PN, pn)
            node.setText(self._CAD_COL_QTY, qty)
            node.setTextAlignment(self._CAD_COL_QTY, Qt.AlignCenter)
            node.setText(self._CAD_COL_REV, builtin.get("Revision", ""))
            node.setText(self._CAD_COL_DEF, builtin.get("Definition", ""))
            node.setText(self._CAD_COL_NOM, builtin.get("Nomenclature", ""))
            node.setText(self._CAD_COL_DESC, builtin.get("Description", ""))

            # 用户自定义属性
            for ui, col_key in enumerate(self._cad_user_cols):
                catia_key = self._cad_user_catia_map.get(col_key, col_key)
                node.setText(self._CAD_COL_USER_START + ui, user_props.get(catia_key, ""))

            # CAD附件（两行：数量 + 按钮）
            att_counts = self._cad_att_counts.get(pn, {"cad": 0, "production": 0})
            cad_count = att_counts.get("cad", 0)

            cad_widget = QWidget()
            cad_layout = QVBoxLayout(cad_widget)
            cad_layout.setContentsMargins(2, 2, 2, 2)
            cad_layout.setSpacing(1)
            cad_lbl = QLabel(f"{cad_count}")
            cad_lbl.setStyleSheet("font-weight:bold; color:#2980b9; font-size:11px;")
            cad_lbl.setAlignment(Qt.AlignCenter)
            cad_layout.addWidget(cad_lbl)
            if match and match.match_status == "matched" and match.revision_id:
                btn_upload = QPushButton("上传源文件")
                btn_upload.setFixedHeight(20)
                btn_upload.setStyleSheet("font-size:9px;")
                btn_upload.clicked.connect(lambda checked, _pn=pn, _r=row: self._on_cad_upload_source(_pn, _r))
                cad_layout.addWidget(btn_upload)
            tree.setItemWidget(node, self._CAD_COL_CAD_ATT, cad_widget)

            # 生产附件（两行：数量 + PDF/STP 按钮）
            prod_count = att_counts.get("production", 0)
            prod_widget = QWidget()
            prod_layout = QVBoxLayout(prod_widget)
            prod_layout.setContentsMargins(2, 2, 2, 2)
            prod_layout.setSpacing(1)
            prod_lbl = QLabel(f"{prod_count}")
            prod_lbl.setStyleSheet("font-weight:bold; color:#e67e22; font-size:11px;")
            prod_lbl.setAlignment(Qt.AlignCenter)
            prod_layout.addWidget(prod_lbl)
            if match and match.match_status == "matched" and match.revision_id:
                btn_row = QHBoxLayout()
                btn_row.setSpacing(2)
                btn_pdf = QPushButton("PDF")
                btn_pdf.setFixedSize(36, 20)
                btn_pdf.setStyleSheet("font-size:9px;")
                btn_pdf.clicked.connect(lambda checked, _pn=pn, _r=row: self._on_cad_export_pdf(_pn, _r))
                btn_row.addWidget(btn_pdf)
                btn_stp = QPushButton("STP")
                btn_stp.setFixedSize(36, 20)
                btn_stp.setStyleSheet("font-size:9px;")
                btn_stp.clicked.connect(lambda checked, _pn=pn, _r=row: self._on_cad_export_stp(_pn, _r))
                btn_row.addWidget(btn_stp)
                btn_row.addStretch()
                prod_layout.addLayout(btn_row)
            tree.setItemWidget(node, self._CAD_COL_PROD_ATT, prod_widget)

            # PDM匹配
            pdm_text = "—"
            if match and match.match_status == "matched":
                pdm_text = f"{match.code}_{match.version}" if match.code else "—"
            node.setText(self._CAD_COL_PDM, pdm_text)

            # 匹配状态
            ms = match.match_status if match else "—"
            ms_display = {"matched": "已匹配", "new": "可新建", "conflict": "冲突", "unknown": "未知"}.get(ms, ms)
            node.setText(self._CAD_COL_MATCH, ms_display)

            # 签出状态
            cs = match.checkout_status if match and match.checkout_status else "—"
            cs_display = {"not_checked_out": "未签出", "checked_out": "已签出", "other_checked_out": "他人签出"}.get(cs, cs)
            node.setText(self._CAD_COL_CO, cs_display)

            # 操作按钮
            op_widget = QWidget()
            op_layout = QHBoxLayout(op_widget)
            op_layout.setContentsMargins(2, 1, 2, 1)
            op_layout.setSpacing(2)

            if match:
                if match.match_status == "new":
                    btn = QPushButton("创建零件")
                    btn.setFixedSize(68, 22)
                    btn.setStyleSheet("font-size:10px;")
                    btn.clicked.connect(lambda checked, r=row: self._on_cad_create_part(r))
                    op_layout.addWidget(btn)
                elif match.match_status == "matched":
                    if match.checkout_status in ("not_checked_out", None):
                        btn_co = QPushButton("签出")
                        btn_co.setFixedSize(50, 22)
                        btn_co.setStyleSheet("font-size:10px;")
                        btn_co.clicked.connect(lambda checked, _pn=pn: self._on_cad_checkout_by_pn(_pn))
                        op_layout.addWidget(btn_co)
                        btn_pull = QPushButton("属性←")
                        btn_pull.setFixedSize(52, 22)
                        btn_pull.setStyleSheet("font-size:10px;")
                        btn_pull.clicked.connect(lambda checked, _r=row, _m=match: self._on_cad_pull_attrs(_r, _m))
                        op_layout.addWidget(btn_pull)
                    elif match.checkout_status == "checked_out":
                        btn_ci = QPushButton("签入")
                        btn_ci.setFixedSize(50, 22)
                        btn_ci.setStyleSheet("font-size:10px;")
                        btn_ci.clicked.connect(lambda checked, _pn=pn: self._on_cad_checkin_by_pn(_pn))
                        op_layout.addWidget(btn_ci)
                        btn_push = QPushButton("属性→")
                        btn_push.setFixedSize(52, 22)
                        btn_push.setStyleSheet("font-size:10px;")
                        btn_push.clicked.connect(lambda checked, _r=row, _m=match: self._on_cad_push_attrs(_r, _m))
                        op_layout.addWidget(btn_push)
                        btn_undo = QPushButton("撤销")
                        btn_undo.setFixedSize(42, 22)
                        btn_undo.setStyleSheet("font-size:10px; color:#e74c3c;")
                        btn_undo.clicked.connect(lambda checked, _pn=pn: self._on_cad_undo(_pn))
                        op_layout.addWidget(btn_undo)
            op_layout.addStretch()
            tree.setItemWidget(node, self._CAD_COL_OP, op_widget)

            # 存储数据
            node.setData(self._CAD_COL_PN, Qt.UserRole, row)
            node.setData(self._CAD_COL_PN, Qt.UserRole + 1, match)
            # 启用双击编辑
            node.setFlags(node.flags() | Qt.ItemIsEditable)

            # 递归子节点
            children = row.get("children", [])
            if children:
                self._populate_cad_tree(tree, children, node)

    def _on_cad_tree_context_menu(self, pos) -> None:
        """树节点右键菜单（含属性编辑、签出/签入、创建零件等）。"""
        node = self._cad_tree.itemAt(pos)
        if not node:
            return
        row = node.data(self._CAD_COL_PN, Qt.UserRole)
        match = node.data(self._CAD_COL_PN, Qt.UserRole + 1)
        if not row:
            return
        pn = row.get("part_number", "")
        builtin = row.get("builtin", {})
        user_props = row.get("user_properties", {})

        menu = QMenu(self)

        # ── 属性编辑子菜单 ──
        edit_menu = menu.addMenu("编辑属性")
        for col_key, catia_key in [
            ("版本", "Revision"), ("定义", "Definition"),
            ("术语", "Nomenclature"), ("描述", "Description"),
        ]:
            val = builtin.get(catia_key, "")
            act = edit_menu.addAction(f"{col_key}: {val}")
            act.triggered.connect(lambda checked, _k=col_key, _ck=catia_key:
                self._on_cad_edit_property(pn, row, _k, _ck))

        for col_key in self._cad_user_cols:
            catia_key = self._cad_user_catia_map.get(col_key, col_key)
            val = user_props.get(catia_key, "")
            act = edit_menu.addAction(f"{col_key}: {val}")
            act.triggered.connect(lambda checked, _k=col_key, _ck=catia_key:
                self._on_cad_edit_property(pn, row, _k, _ck))

        menu.addSeparator()

        # ── 操作 ──
        if match:
            if match.match_status == "new":
                act_create = menu.addAction("创建零件")
                act_create.triggered.connect(lambda: self._on_cad_create_part(row))
            elif match.match_status == "matched":
                if match.checkout_status in ("not_checked_out", None):
                    act_co = menu.addAction("签出")
                    act_co.triggered.connect(lambda: self._on_cad_checkout_by_pn(pn))
                    act_pull = menu.addAction("属性← 拉取")
                    act_pull.triggered.connect(lambda: self._on_cad_pull(row, match))
                elif match.checkout_status == "checked_out":
                    act_ci = menu.addAction("签入")
                    act_ci.triggered.connect(lambda: self._on_cad_checkin_by_pn(pn))
                    act_push = menu.addAction("属性→ 推送")
                    act_push.triggered.connect(lambda: self._on_cad_push(row, match))
        menu.exec(self._cad_tree.viewport().mapToGlobal(pos))

    def _on_cad_checkout_by_pn(self, pn: str) -> None:
        match = self._cad_match_map.get(pn)
        if match and match.revision_id:
            try:
                self._cad_client.checkout(match.revision_id)
                self._log_to_conn(f"CAD入口：签出成功 — {pn}", "ok")
                self._cad_match_map[pn] = type(match)(**{**match.__dict__, "checkout_status": "checked_out"})
                self._refresh_cad_tree()
            except Exception as e:
                QMessageBox.critical(self, "签出失败", str(e))

    def _on_cad_edit_property(self, pn: str, row: dict, col_name: str, catia_key: str) -> None:
        """右键编辑 CATIA 属性并写回。"""
        builtin = row.get("builtin", {})
        user_props = row.get("user_properties", {})
        is_builtin = catia_key in ("Revision", "Definition", "Nomenclature", "Description")
        current = builtin.get(catia_key, "") if is_builtin else user_props.get(catia_key, "")

        text, ok = QInputDialog.getText(
            self, f"编辑 {col_name}", f"{pn} — {col_name}:",
            text=str(current),
        )
        if not ok:
            return

        if is_builtin:
            new_builtin = dict(builtin)
            new_builtin[catia_key] = text
            row["builtin"] = new_builtin
            from catia_copilot.ui.sync_rows import sync_rows_by_part_number
            self._cad_rows = sync_rows_by_part_number(self._cad_rows, row, catia_key, text)
            self._cad_tree_rows = sync_rows_by_part_number(self._cad_tree_rows, row, catia_key, text)
        else:
            new_user_props = dict(user_props)
            new_user_props[catia_key] = text
            row["user_properties"] = new_user_props
            from catia_copilot.ui.sync_rows import sync_rows_by_part_number
            self._cad_rows = sync_rows_by_part_number(self._cad_rows, row, catia_key, text)
            self._cad_tree_rows = sync_rows_by_part_number(self._cad_tree_rows, row, catia_key, text)

        self._refresh_cad_tree()
        self._log_to_conn(f"CAD入口：属性已编辑 — {pn}.{col_name} = {text}", "ok")

    def _on_cad_checkin_by_pn(self, pn: str) -> None:
        match = self._cad_match_map.get(pn)
        if match and match.revision_id:
            try:
                self._cad_client.checkin(match.revision_id)
                self._log_to_conn(f"CAD入口：签入成功 — {pn}", "ok")
                self._cad_match_map[pn] = type(match)(**{**match.__dict__, "checkout_status": "not_checked_out"})
                self._refresh_cad_tree()
            except Exception as e:
                QMessageBox.critical(self, "签入失败", str(e))

    def _on_cad_push(self, row: dict, match) -> None:
        """属性→ PDM。"""
        if not match.revision_id:
            return
        self._on_cad_push_attrs(row, match)

    def _on_cad_pull(self, row: dict, match) -> None:
        """属性← PDM。"""
        self._on_cad_pull_attrs(row, match)

    def _on_cad_item_changed(self, item, col: int) -> None:
        """双击编辑完成后的回调：写回 CATIA + 同步同件号实例。"""
        row = item.data(self._CAD_COL_PN, Qt.UserRole)
        if not row:
            return
        new_val = item.text(col).strip()
        pn = row.get("part_number", "")
        builtin = row.get("builtin", {})
        user_props = row.get("user_properties", {})

        col_map = {
            self._CAD_COL_REV:  ("Revision", True),
            self._CAD_COL_DEF:  ("Definition", True),
            self._CAD_COL_NOM:  ("Nomenclature", True),
            self._CAD_COL_DESC: ("Description", True),
        }
        prop_info = col_map.get(col)
        if not prop_info and col >= self._CAD_COL_USER_START and col < self._CAD_COL_CAD_ATT:
            ui = col - self._CAD_COL_USER_START
            if ui < len(self._cad_user_cols):
                catia_key = self._cad_user_catia_map.get(self._cad_user_cols[ui], self._cad_user_cols[ui])
                prop_info = (catia_key, False)

        if not prop_info:
            return

        catia_key, is_builtin = prop_info
        old_val = builtin.get(catia_key, "") if is_builtin else user_props.get(catia_key, "")
        if new_val == old_val:
            return

        if is_builtin:
            row["builtin"] = {**builtin, catia_key: new_val}
        else:
            row["user_properties"] = {**user_props, catia_key: new_val}

        from catia_copilot.ui.sync_rows import sync_rows_by_part_number
        self._cad_rows = sync_rows_by_part_number(self._cad_rows, row, catia_key, new_val)
        self._cad_tree_rows = sync_rows_by_part_number(self._cad_tree_rows, row, catia_key, new_val)

        try:
            from catia_copilot.catia.property_rw import write_property
            from catia_copilot.catia.connection import get_catia_v5_application
            app = get_catia_v5_application()
            if app.ActiveDocument and app.ActiveDocument.Product:
                path = row.get("path", "0")
                write_property(path, app.ActiveDocument.Product, catia_key, new_val)
        except Exception:
            pass

        self._log_to_conn(f"CAD入口：{pn}.{catia_key} = {new_val}", "ok")

    def _refresh_cad_tree(self) -> None:
        """刷新 CAD 树显示（保持展开状态）。"""
        self._cad_tree.clear()
        self._populate_cad_tree(self._cad_tree, self._cad_tree_rows)
        self._cad_tree.expandAll()

    def _on_cad_undo(self, pn: str) -> None:
        """撤销签出。"""
        match = self._cad_match_map.get(pn)
        if match and match.revision_id:
            try:
                self._cad_client.undocheckout(match.revision_id)
                self._log_to_conn(f"CAD入口：撤销签出成功 — {pn}", "ok")
                self._cad_match_map[pn] = type(match)(**{**match.__dict__, "checkout_status": "not_checked_out"})
                self._refresh_cad_tree()
            except Exception as e:
                QMessageBox.critical(self, "撤销失败", str(e))

    def _on_cad_push_all(self) -> None:
        """批量推送所有已签出零件的属性到 PDM。"""
        count = 0
        field_map = getattr(self, "_cad_field_map", {})
        bm = field_map.get("builtin", {})
        pm = field_map.get("properties", {})
        # 预加载自定义字段定义
        try:
            field_defs = self._cad_client.get_custom_field_definitions()
        except Exception:
            field_defs = []
        key_to_id = {}
        name_to_id = {}
        for fd in field_defs:
            fid = fd.get("id")
            if fid:
                key_to_id[fd.get("field_key", "")] = fid
                name_to_id[fd.get("name", "")] = fid

        for row in self._cad_rows:
            pn = row.get("part_number", "")
            match = self._cad_match_map.get(pn)
            if not (match and match.checkout_status == "checked_out"):
                continue
            master_id = getattr(match, "master_id", None) or getattr(match, "revision_id", None)
            if not master_id:
                continue
            builtin = row.get("builtin", {})
            user_props = row.get("user_properties", {})

            # PartMaster update
            payload = {}
            for catia_key, pdm_key in bm.items():
                val = builtin.get(catia_key, "")
                if val:
                    payload[pdm_key] = val
            if "spec" in pm.values() or "规格型号" in pm:
                spec_val = user_props.get("规格型号", "") or builtin.get("规格型号", "")
                if spec_val:
                    payload["spec"] = spec_val
            if payload:
                try:
                    self._cad_client.update_part(master_id, payload)
                except Exception:
                    pass

            # Custom field values
            custom_values = []
            for catia_key, pdm_key in pm.items():
                if pdm_key == "spec":
                    continue
                val = user_props.get(catia_key, "") or builtin.get(catia_key, "")
                if not val:
                    continue
                fid = key_to_id.get(pdm_key) or name_to_id.get(pdm_key) or key_to_id.get(catia_key) or name_to_id.get(catia_key)
                if fid:
                    custom_values.append({"field_id": fid, "value": val})
            if custom_values:
                try:
                    self._cad_client.set_custom_field_values("part", master_id, custom_values)
                except Exception:
                    pass

            count += 1

        if count:
            self._log_to_conn(f"CAD入口：批量推送完成 — {count} 个", "ok")
        else:
            QMessageBox.information(self, "批量推送", "没有需要推送的零件。")

    def _on_cad_upload_source(self, pn: str, row: dict) -> None:
        """上传 CATIA 源文件到 PDM CAD附件。"""
        match = self._cad_match_map.get(pn)
        if not match or not match.revision_id:
            return
        doc_path = row.get("doc_path", "")
        if not doc_path or not os.path.exists(doc_path):
            QMessageBox.warning(self, "上传源文件", f"找不到源文件：{doc_path}")
            return
        try:
            self._cad_client.upload_attachment(match.revision_id, doc_path, "cad", overwrite=True)
            self._log_to_conn(f"CAD入口：源文件已上传 — {pn}", "ok")
            self._cad_att_counts[pn]["cad"] = self._cad_att_counts.get(pn, {"cad":0}).get("cad", 0) + 1
            self._refresh_cad_tree()
        except Exception as e:
            QMessageBox.critical(self, "上传失败", str(e))

    def _on_cad_export_pdf(self, pn: str, row: dict) -> None:
        """导出 CATDrawing → PDF → 上传到生产附件（按命名规则）。"""
        match = self._cad_match_map.get(pn)
        if not match or not match.revision_id:
            return
        doc_path = row.get("doc_path", "")
        if not doc_path:
            QMessageBox.warning(self, "导出PDF", "找不到源文件路径。")
            return
        from catia_copilot.catia.dependencies import find_drawing_for_part
        drawing_result = find_drawing_for_part(doc_path)
        if not drawing_result:
            QMessageBox.information(self, "导出PDF", f"未找到关联工程图：{doc_path}")
            return
        # find_drawing_for_part 可能返回列表或单个字符串
        drawing_path = drawing_result[0] if isinstance(drawing_result, list) else drawing_result
        try:
            naming = _load_cad_naming()
            is_asm = row.get("is_assembly", False)
            prefix = naming["pdf_assembly"] if is_asm else naming["pdf_part"]
            version = match.version or "A"
            filename = f"{prefix}{pn}_{version}.pdf"
            output_path = os.path.join(tempfile.gettempdir(), filename)
            from catia_copilot.catia.conversion import convert_drawing_to_pdf
            output_dir = os.path.dirname(output_path) or "."
            input_stem = os.path.splitext(os.path.basename(drawing_path))[0]
            expected_file = os.path.join(output_dir, f"{input_stem}.pdf")
            pdf_path = None
            count = convert_drawing_to_pdf(
                [drawing_path], output_folder=output_dir, prefix="", suffix=""
            )
            if count > 0 and os.path.exists(expected_file):
                if expected_file != output_path:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    os.rename(expected_file, output_path)
                pdf_path = output_path
            if pdf_path:
                self._cad_client.upload_attachment(match.revision_id, pdf_path, "production", overwrite=True)
                self._log_to_conn(f"CAD入口：PDF已上传 — {filename}", "ok")
                self._cad_att_counts[pn]["production"] = self._cad_att_counts.get(pn, {"production":0}).get("production", 0) + 1
                self._refresh_cad_tree()
                try:
                    os.remove(pdf_path)
                except Exception:
                    pass
            else:
                QMessageBox.warning(self, "导出PDF", "PDF 导出失败。")
        except Exception as e:
            QMessageBox.critical(self, "导出PDF失败", str(e))

    def _on_cad_export_stp(self, pn: str, row: dict) -> None:
        """导出 STP → 上传到生产附件（按命名规则）。"""
        match = self._cad_match_map.get(pn)
        if not match or not match.revision_id:
            return
        try:
            naming = _load_cad_naming()
            prefix = naming["stp"]
            version = match.version or "A"
            filename = f"{prefix}{pn}_{version}.stp"
            output_path = os.path.join(tempfile.gettempdir(), filename)
            from catia_copilot.catia.file_exporter import export_stp
            stp_path = export_stp(row.get("path", "0"), output_path=output_path)
            if stp_path:
                self._cad_client.upload_attachment(match.revision_id, stp_path, "production", overwrite=True)
                self._log_to_conn(f"CAD入口：STP已上传 — {filename}", "ok")
                self._cad_att_counts[pn]["production"] = self._cad_att_counts.get(pn, {"production":0}).get("production", 0) + 1
                self._refresh_cad_tree()
                try:
                    os.remove(stp_path)
                except Exception:
                    pass
            else:
                QMessageBox.warning(self, "导出STP", "STP 导出失败。")
        except Exception as e:
            QMessageBox.critical(self, "导出STP失败", str(e))

    # 旧方法保留兼容
        """签入指定版本的零件。"""
        if not revision_id:
            return
        try:
            self._cad_client.checkin(revision_id)
            self._log_to_conn(f"CAD入口：签入成功", "ok")
            pn = self._cad_rows[row_idx].get("part_number", "")
            if pn in self._cad_match_map:
                m = self._cad_match_map[pn]
                self._cad_match_map[pn] = type(m)(**{**m.__dict__, "checkout_status": "not_checked_out"})
            self._refresh_cad_tree()
        except Exception as e:
            QMessageBox.critical(self, "签入失败", str(e))

    def _on_cad_create_part(self, row: dict) -> None:
        """在 myPDM 中创建零件。"""
        pn = row.get("part_number", "").strip()
        builtin = row.get("builtin", {})
        name = builtin.get("Nomenclature", pn)
        is_assembly = row.get("is_assembly", False)
        ptype = "assembly" if is_assembly else "part"
        try:
            from catia_copilot.plm.my_pdm_schemas import PartCreateRequest
            req = PartCreateRequest(code=pn, name=name, type=ptype)
            result = self._cad_client.create_part(req)
            self._log_to_conn(f"CAD入口：零件已创建 — {pn}", "ok")
            # 更新本地匹配状态
            if pn in self._cad_match_map:
                m = self._cad_match_map[pn]
                self._cad_match_map[pn] = type(m)(**{
                    **m.__dict__,
                    "match_status": "matched",
                    "revision_id": result.id,
                    "version": result.version,
                    "checkout_status": "checked_out",
                })
            self._refresh_cad_tree()
        except Exception as e:
            QMessageBox.critical(self, "创建失败", str(e))

    def _on_cad_push_attrs(self, row: dict, match) -> None:
        """属性→：按字段映射将 CATIA 属性推送到 PDM。
        
        builtin 字段 → PUT /parts/{master_id}
        自定义属性 → PUT /custom-fields/values/part/{master_id}
        """
        if not match:
            return
        pn = row.get("part_number", "")
        builtin = row.get("builtin", {})
        user_props = row.get("user_properties", {})
        field_map = getattr(self, "_cad_field_map", {})

        master_id = getattr(match, "master_id", None) or getattr(match, "revision_id", None)
        if not master_id:
            QMessageBox.warning(self, "属性→", f"缺少 PDM 零件 ID（{pn}）。")
            return

        bm = field_map.get("builtin", {})
        pm = field_map.get("properties", {})

        # ── PartMaster 更新（code, name, spec） ──
        payload = {}
        for catia_key, pdm_key in bm.items():
            val = builtin.get(catia_key, "")
            if val:
                payload[pdm_key] = val
        # spec 从属性映射中取
        if "spec" in pm.values() or "规格型号" in pm:
            spec_val = user_props.get("规格型号", "") or builtin.get("规格型号", "")
            if spec_val:
                payload["spec"] = spec_val

        push_log = []
        if payload:
            try:
                self._cad_client.update_part(master_id, payload)
                push_log.append("基础字段")
            except PlmApiError as e:
                QMessageBox.critical(self, "推送失败", f"{pn}：{e}")
                return

        # ── 自定义字段 ──
        custom_values = []
        try:
            field_defs = self._cad_client.get_custom_field_definitions()
            # 构建 field_key → field_id 映射
            key_to_id = {}
            name_to_id = {}
            for fd in field_defs:
                fid = fd.get("id")
                if fid:
                    key_to_id[fd.get("field_key", "")] = fid
                    name_to_id[fd.get("name", "")] = fid
        except Exception:
            field_defs = []
            key_to_id = {}
            name_to_id = {}

        for catia_key, pdm_key in pm.items():
            if pdm_key == "spec":
                continue  # spec 已在上面处理
            val = user_props.get(catia_key, "") or builtin.get(catia_key, "")
            if not val:
                continue
            # 优先按 field_key 匹配，其次按 name 匹配
            fid = key_to_id.get(pdm_key) or name_to_id.get(pdm_key) or key_to_id.get(catia_key) or name_to_id.get(catia_key)
            if fid:
                custom_values.append({"field_id": fid, "value": val})

        if custom_values:
            try:
                self._cad_client.set_custom_field_values("part", master_id, custom_values)
                push_log.append(f"自定义字段({len(custom_values)}个)")
            except PlmApiError as e:
                self._log_to_conn(f"CAD入口：自定义字段推送失败 {pn} — {e}", "warn")

        # ── BOM 结构同步（装配体） ──
        rev_id = getattr(match, "revision_id", None)
        if row.get("is_assembly") and row.get("children") and rev_id:
            children_data = []
            for child in row["children"]:
                child_pn = child.get("part_number", "")
                child_builtin = child.get("builtin", {})
                child_name = child_builtin.get("Nomenclature", child_pn)
                child_spec = child.get("user_properties", {}).get("规格型号", "")
                child_qty = child.get("quantity", 1)
                # 转换矩阵：CATIA 列主序 12元素 → 行主序 4×4
                child_instances = []
                for inst in child.get("instances", []):
                    m = inst.get("matrix")
                    if m and len(m) == 12:
                        m = [
                            m[0], m[3], m[6], m[9],
                            m[1], m[4], m[7], m[10],
                            m[2], m[5], m[8], m[11],
                            0.0, 0.0, 0.0, 1.0,
                        ]
                    child_instances.append({
                        "matrix": m,
                        "label": inst.get("label", ""),
                    })
                children_data.append({
                    "code": child_pn,
                    "name": child_name,
                    "spec": child_spec or None,
                    "quantity": child_qty,
                    "instances": child_instances if child_instances else [],
                })
            if children_data:
                try:
                    result = self._cad_client.cad_bom_sync(rev_id, children_data)
                    push_log.append(f"BOM({result.created}+{result.updated})")
                except PlmApiError as e:
                    self._log_to_conn(f"CAD入口：BOM同步失败 {pn} — {e}", "warn")

        if push_log:
            self._log_to_conn(f"CAD入口：属性已推送 — {pn}（{', '.join(push_log)}）", "ok")
        else:
            QMessageBox.information(self, "属性→", "未找到可推送的属性。")

    def _on_cad_pull_attrs(self, row: dict, match) -> None:
        """属性←：从 PDM 拉取属性。"""
        master_id = getattr(match, "master_id", None) or getattr(match, "revision_id", None)
        if not master_id:
            return
        try:
            part = self._cad_client.get_part(master_id)
            if part:
                field_map = getattr(self, "_cad_field_map", {})
                pm = field_map.get("properties", {})
                # 展示 PDM 侧的可拉取字段
                info_lines = []
                for catia_key, pdm_key in pm.items():
                    v = part.get(pdm_key, "")
                    if v:
                        info_lines.append(f"  {catia_key} ← {v}")
                if info_lines:
                    QMessageBox.information(self, "属性←", "PDM 属性：\n" + "\n".join(info_lines))
                else:
                    QMessageBox.information(self, "属性←", "PDM 无自定义属性数据。")
                self._log_to_conn(f"CAD入口：属性已拉取 — {match.code}", "ok")
        except Exception as e:
            QMessageBox.critical(self, "拉取失败", str(e))

    def _on_cad_refresh(self) -> None:
        """重新执行 CAD入口 流程。"""
        self._on_cad_entry()

    def _on_cad_checkin_all(self) -> None:
        """批量签入所有已签出的零件。"""
        count = 0
        for pn, match in list(self._cad_match_map.items()):
            if match.checkout_status == "checked_out" and match.revision_id:
                try:
                    self._cad_client.checkin(match.revision_id)
                    self._cad_match_map[pn] = type(match)(**{**match.__dict__, "checkout_status": "not_checked_out"})
                    count += 1
                except Exception as e:
                    self._log_to_conn(f"CAD入口：签入失败 {pn} — {e}", "warn")
        if count:
            self._log_to_conn(f"CAD入口：全部签入完成 — {count} 个", "ok")
            self._refresh_cad_tree()
        else:
            QMessageBox.information(self, "全部签入", "没有需要签入的零件。")

    def _init_settings_controls(self) -> None:
        """初始化 myPDM 连接相关控件引用。"""
        base_url, login, password = self._read_conn()

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.Password)
        self._lbl_ws_detail = QLabel("— 未获取 —")
        self._txt_conn_log  = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)

    def _on_save_conn(self):
        self._save_conn()
        self._update_conn_status_bar()

    def _on_test_conn(self):
        base_url, login, password = self._read_conn()
        if not base_url or not login:
            self._log_to_conn("请先填写服务端地址和用户名。", "warn")
            return
        self._log_to_conn("正在测试连接……")
        w = _ConnectWorker(base_url, login, password)
        w.success.connect(self._on_conn_ok)
        w.failure.connect(self._on_conn_fail)
        self._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict):
        real_name = ws_info.get("real_name", login_name)
        role = ws_info.get("role", "?")
        role_display = {"admin": "管理员", "engineer": "工程师",
                        "production": "生产", "guest": "访客"}.get(role, role)
        detail = f"用户：{real_name}（{role_display}）  |  部门：{ws_info.get('department', '—')}"
        self._lbl_ws_detail.setText(detail)
        self._log_to_conn(f"连接成功 ({login_name})", "ok")
        self._update_conn_status_bar()

    def _on_conn_fail(self, err: str):
        self._log_to_conn(f"连接失败：{err}", "error")

class _SettingsDialog(QDialog):
    """myPDM 工作台设置（连接配置）。"""

    def __init__(self, workbench: "PlmWorkbench"):
        super().__init__(workbench)
        self._wb = workbench
        self.setWindowTitle("myPDM 设置")
        self.setMinimumSize(480, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        grp_cfg = QGroupBox("连接配置")
        form = QFormLayout(grp_cfg)
        form.setSpacing(6)

        base_url, login, password = self._wb._read_conn()

        self._le_base_url = QLineEdit(base_url)
        self._le_login    = QLineEdit(login)
        self._le_password = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._le_base_url.setPlaceholderText("https://192.168.1.x:8443/api")

        form.addRow("服务端地址：", self._le_base_url)
        form.addRow("用户名：",     self._le_login)
        form.addRow("密码：",       self._le_password)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_test = QPushButton("测试连接")
        btn_save.clicked.connect(self._on_save)
        btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        form.addRow("", btn_row)
        root.addWidget(grp_cfg)

        grp_log = QGroupBox("连接日志")
        v_log = QVBoxLayout(grp_log)
        self._txt_conn_log = QPlainTextEdit()
        self._txt_conn_log.setReadOnly(True)
        self._txt_conn_log.setPlaceholderText('— 点击"测试连接"验证配置 —')
        self._txt_conn_log.setPlainText(self._wb._txt_conn_log.toPlainText())
        v_log.addWidget(self._txt_conn_log)
        root.addWidget(grp_log)

        root.addStretch()

        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        root.addLayout(close_row)

    def _on_save(self) -> None:
        """保存配置到 QSettings。"""
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())
        self._log("配置已保存。", "ok")
        self._wb._update_conn_status_bar()

    def _on_test(self) -> None:
        """测试连接。"""
        self._log("正在测试连接……", "info")
        base_url = self._le_base_url.text().strip()
        login    = self._le_login.text().strip()
        password = self._le_password.text()
        if not base_url or not login:
            self._log("请先填写服务端地址和用户名。", "warn")
            return
        w = _ConnectWorker(base_url, login, password)
        w.success.connect(lambda ln, users, ws_info: self._on_conn_ok(ln, users, ws_info))
        w.failure.connect(lambda err: self._log(f"连接失败：{err}", "error"))
        self._wb._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict) -> None:
        real_name = ws_info.get("real_name", login_name)
        role = ws_info.get("role", "?")
        role_display = {"admin": "管理员", "engineer": "工程师",
                        "production": "生产", "guest": "访客"}.get(role, role)
        detail = f"用户：{real_name}（{role_display}）  |  部门：{ws_info.get('department', '—')}"
        self._wb._lbl_ws_detail.setText(detail)
        self._log(f"连接成功 ({login_name})", "ok")
        self._wb._update_conn_status_bar()

    def _log(self, msg: str, level: str = "info") -> None:
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        line = f"[{ts}] [{prefix}] {msg}"
        self._txt_conn_log.appendPlainText(line)
        self._wb._txt_conn_log.appendPlainText(line)
