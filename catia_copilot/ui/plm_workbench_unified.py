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
from datetime import datetime
from functools import partial
from typing import cast

import pythoncom
import shiboken6
from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from catia_copilot.constants import (
    BOM_COLUMN_DISPLAY_NAMES,
    BOM_EDIT_COLUMN_ORDER,
    BOM_HIDEABLE_COLUMNS,
    PRESET_USER_REF_PROPERTIES,
)
from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
from catia_copilot.plm.my_pdm_api_client import MyPdmApiClient, MyPdmApiError
from catia_copilot.catia.assembly_reader import detect_catia_status, read_assembly_tree
from catia_copilot.ui.flatten_tree import flatten_tree, flatten_tree_hierarchical
import json as _json
from catia_copilot.plm.sync import (
    AfterUpdatePolicy,
    CheckedOutByOtherPolicy,
    ExistingPartPolicy,
    OwnCheckedOutPolicy,
    SyncOptions,
    SyncResult,
    extract_bom_v3,
    sync_bom_to_plm,
)
from catia_copilot.ui.bom_widgets import _BomTreeWidget
from catia_copilot.ui.theme_manager import theme_manager

logger = logging.getLogger(__name__)

# ── QSettings 键（与 PlmSyncDialog 共用 PlmConfig，保持配置互通） ─────────────
_S_ORG       = "CATIACopilot"
_S_PLM_CFG   = "PlmConfigUnified"   # 独立配置，与 DocDoku 工作台隔离
_S_TAG_RULES = "PlmTagRulesUnified"
_S_HISTORY   = "PlmSyncHistoryUnified"
_S_WB        = "PlmWorkbenchUnified"

# 后端类型：plm-unified / mypdm。存于 PlmConfigUnified.backend
_BACKEND_UNIFIED = "plm-unified"
_BACKEND_MYPDM   = "mypdm"

_DEFAULT_BASE_URL  = "http://127.0.0.1:8010"
_DEFAULT_LOGIN     = "admin"
_DEFAULT_PASSWORD  = "password"
_DEFAULT_WORKSPACE = "Workspace_0"

_MAX_HISTORY = 20

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

# 差异状态计算：版本+迭代号相同时，比较本地 mtime 与 PLM checkInDate 的容差
# 60 秒内视为相等，避免文件系统时间精度差异导致误判
_DIFF_TIME_TOLERANCE_SEC = 60


def _app_palette() -> "QPalette | None":
    """安全获取当前 QApplication 的 QPalette。

    QApplication.instance() 返回 QCoreApplication | None，Pylance 不认识
    QCoreApplication 上的 .palette()，需通过 cast 告知类型检查器实际是
    QApplication 实例，从而消除 reportAttributeAccessIssue 误报。
    """
    app = QApplication.instance()
    if app is None:
        return None
    return cast(QApplication, app).palette()


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


def _sync_row_color(source: str, update: str = "", checkin: str = "") -> QColor | None:
    """根据同步三列内容返回对应的 QColor，无法匹配时返回 None。

    使用主题感知颜色：根据当前 QPalette 自动适配深色/浅色模式。

    颜色语义：
      绿色  — 新建成功
      蓝色  — 已有零件更新成功（签出、属性写入、签入均属此类）
      灰色  — 跳过 / 无变化
      红色  — 任何失败
    """
    palette = _app_palette()

    combined = source + update + checkin
    if "失败" in combined or "✗" in combined:
        return palette.color(palette.ColorRole.Link) if palette else QColor("#e74c3c")
    if "跳过" in combined or "无变化" in combined:
        return palette.color(palette.ColorRole.Mid) if palette else QColor("#7f8c8d")
    if "新建" in source:
        return QColor("#27ae60")  # 绿色在深/浅色下都可读
    if (
        "签出" in source
        or "更新" in source
        or "已写入" in update
        or "已上传" in update
        or "已签入" in checkin
        or "保留签出" in checkin
        or "✓" in combined
    ):
        return palette.color(palette.ColorRole.Highlight) if palette else QColor("#2980b9")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 后台工作线程
# ─────────────────────────────────────────────────────────────────────────────

class _ConnectWorker(QThread):
    """测试连接，支持 plm-unified 和 myPDM 双后端。"""
    success = Signal(str, list, dict)
    failure = Signal(str)

    def __init__(self, base_url: str, login: str, password: str, workspace: str, backend: str = _BACKEND_UNIFIED) -> None:
        super().__init__()
        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._backend   = backend

    def run(self):
        try:
            if self._backend == _BACKEND_MYPDM:
                self._run_mypdm()
            else:
                self._run_unified()
        except Exception as exc:
            self.failure.emit(str(exc))

    def _run_mypdm(self):
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

    def _run_unified(self):
        c = PlmApiClient(self._base_url)
        c.login(self._login, self._password)
        try:
            users = c.list_users(self._workspace) or []
        except Exception:
            users = []
        ws_info: dict = {
            "id":   self._workspace,
            "name": self._workspace,
            "_current_user_role": "已登录（plm-unified）",
        }
        self.success.emit(self._login, users, ws_info)



class _SyncWorker(QThread):
    """执行 Push 同步（按文件主键，逐文件提取 BOM 树后同步到 PLM）。"""
    progress   = Signal(str)
    upload_log = Signal(str, str, str, str)
    sync_done  = Signal(object)
    error      = Signal(str)

    def __init__(self, base_url: str, login: str, password: str, workspace: str,
                 options: SyncOptions, push_rows: list[dict]) -> None:
        super().__init__()
        self._base_url   = base_url
        self._login      = login
        self._password   = password
        self._workspace  = workspace
        self._options    = options
        self._push_rows  = push_rows  # 每项含 "pn"、"local"(LocalPartInfo)

    def run(self) -> None:
        pythoncom.CoInitialize()
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)

            merged = SyncResult()
            # 跨文件共享的已处理 pn 去重表，防止同一零件被重复 sync
            shared_uploaded_pns: dict[str, tuple] = {}

            for row in self._push_rows:
                local = row.get("local")
                if local is None:
                    continue

                # depth=1：只读根节点 + 直接子节点（含 placement），不递归子树，
                # 避免先 sync 根产品时把所有子零件都处理一遍，后续子零件 push_row 重复 sync
                bom_root = extract_bom_v3(
                    progress_callback=lambda m: self.progress.emit(m),
                    file_path=local.filepath,
                    depth=1,
                )
                if bom_root is None:
                    merged.errors.append(
                        f"{local.part_number}：BOM 提取失败，已跳过（文件：{local.filepath}）"
                    )
                    continue

                def _struct_cb(event):
                    if event.message:
                        self.progress.emit(event.message)
                    if event.type == "node_done" and event.part_number:
                        self.upload_log.emit(
                            event.part_number,
                            event.source,
                            event.update,
                            event.checkin,
                        )

                sub = sync_bom_to_plm(
                    bom_root, c, self._workspace,
                    options=self._options,
                    progress_callback=lambda m: self.progress.emit(m),
                    progress_callback_structured=_struct_cb,
                    shared_uploaded_pns=shared_uploaded_pns,
                )
                merged.created   += sub.created
                merged.updated   += sub.updated
                merged.skipped   += sub.skipped
                merged.unchanged += sub.unchanged
                merged.errors.extend(sub.errors)

            self.sync_done.emit(merged)
        except Exception as exc:
            logger.exception("PLM 同步后台线程异常")
            self.error.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


class _TagsWorker(QThread):
    """拉取工作区 Tag 列表。"""
    success = Signal(list)
    failure = Signal(str)

    def __init__(self, base_url: str, login: str, password: str, workspace: str) -> None:
        super().__init__()
        self._base_url = base_url
        self._login = login
        self._password = password
        self._workspace = workspace

    def run(self) -> None:
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            self.success.emit(c.list_tags(self._workspace) or [])
        except Exception as exc:
            logger.exception("_TagsWorker 运行异常")
            self.failure.emit(str(exc))


class _CreateTagWorker(QThread):
    """在工作区创建新 Tag。"""
    success = Signal(str)
    failure = Signal(str)

    def __init__(self, base_url: str, login: str, password: str, workspace: str, label: str) -> None:
        super().__init__()
        self._base_url = base_url
        self._login = login
        self._password = password
        self._workspace = workspace
        self._label = label

    def run(self) -> None:
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            c._request("POST", f"/workspaces/{self._workspace}/tags", {"label": self._label})
            self.success.emit(self._label)
        except Exception as exc:
            logger.exception("_CreateTagWorker 运行异常")
            self.failure.emit(str(exc))


class _PlmStatusWorker(QThread):
    """按零件号列表逐个查询 PLM 状态，返回 {pn: summary_dict} 字典。

    summary_dict 字段（来自 api_client.extract_part_summary）：
        number / version / lastIterationNumber / name /
        checkOutUser / modificationDate / authorLogin / lifecycleState / tags
    不存在的零件号对应值为 None。
    """
    done     = Signal(dict)   # {pn: summary_dict | None}
    failure  = Signal(str)
    progress = Signal(int, int)  # (done, total)

    # 兼容旧信号名
    success  = done

    def __init__(self, base_url: str, login: str, password: str,
                 workspace: str, part_numbers: list[str]) -> None:
        super().__init__()
        self._base_url     = base_url
        self._login        = login
        self._password     = password
        self._workspace    = workspace
        self._part_numbers = [pn for pn in part_numbers if pn]

    def run(self) -> None:
        try:
            c = PlmApiClient(self._base_url)
            c.login(self._login, self._password)
            result = c.search_parts_summary(
                self._workspace,
                self._part_numbers,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
            self.done.emit(result)
        except Exception as exc:
            logger.exception("_PlmStatusWorker 运行异常")
            self.failure.emit(str(exc))


class _WorkspaceScanWorker(QThread):
    """后台扫描工作区根目录，通过 CATIA COM 读取每个文件的属性。

    信号：
        progress(done, total, filename) — 扫描进度
        scan_done(list[LocalPartInfo])  — 扫描完成
        failure(str)                    — 失败
    """
    progress  = Signal(int, int, str)
    scan_done = Signal(list)
    failure   = Signal(str)

    def __init__(self, work_dir: str) -> None:
        super().__init__()
        self._work_dir = work_dir

    def run(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            from catia_copilot.plm.workspace_scanner import scan_workspace
            results = scan_workspace(
                self._work_dir,
                catia_app=None,  # 内部自动调用 get_catia_v5_application()
                progress_callback=lambda done, total, fn: self.progress.emit(done, total, fn),
            )
            self.scan_done.emit(results)
        except Exception as exc:
            logger.exception("_WorkspaceScanWorker 运行异常")
            self.failure.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


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

    def __init__(self, base_url: str, login: str, password: str, workspace: str) -> None:
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
        # 预查询附件参数（用于避免主线程阻塞）
        self._prequery_items: list[tuple[int, str, str, str]] = []  # (row_idx, pn, ver, itr)

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
        mod_dates: "dict[str, str] | None" = None,
    ) -> None:
        """设置批量下载参数。

        items:     list of (part_number, version, iteration, filename)
        base_dir:  基础目录，主键文件平铺到根目录，其他附件放 base_dir/{pn}/
        mod_dates: {pn: modificationDate UTC str}，下载主键文件后设置其 mtime，
                   避免 mtime 被刷新为下载时间而误判为"本地新"。
        """
        self._mode        = self.MODE_DOWNLOAD
        self._dl_items    = items
        self._dl_base_dir = base_dir
        self._mod_dates   = mod_dates or {}

    def set_prequery_attachments(
        self,
        items: list[tuple[int, str, str, str]],
    ) -> None:
        """设置预查询附件参数（在 Worker 线程中并行查询附件列表）。

        items: list of (row_idx, part_number, version, iteration)
        """
        self._mode           = "prequery"
        self._prequery_items = items

    def run(self) -> None:
        pythoncom.CoInitialize()
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
                    ext = os.path.splitext(fname)[1].lower()

                    # STP/STEP 文件不下载（仅用于 PLM CAD 预览，本地已有源文件）
                    if ext in (".stp", ".step"):
                        continue

                    # 主键文件（CATPart/CATProduct）平铺到 work_dir 根目录，
                    # 保证 CATIA 打开 CATProduct 时能在同目录找到引用的子文件。
                    # 其他附件（PDF、DXF 等）放 base_dir/{pn}/ 子目录。
                    is_primary = ext in (".catpart", ".catproduct")
                    if is_primary:
                        dest_path = os.path.join(self._dl_base_dir, fname)
                    else:
                        part_dir  = os.path.join(self._dl_base_dir, pn)
                        os.makedirs(part_dir, exist_ok=True)
                        dest_path = os.path.join(part_dir, fname)

                    # 使用 functools.partial 避免闭包捕获问题（top-level import）
                    progress_cb = partial(self.file_progress.emit, fname)

                    c.download_attached_file(
                        self._workspace, pn, ver, int(itr), fname,
                        dest_path,
                        progress_cb=progress_cb,
                    )

                    # 主键文件：将 mtime 回拨到 PLM 的修改时间，避免误判为"本地新"
                    if is_primary and pn in self._mod_dates and self._mod_dates[pn]:
                        try:
                            from datetime import datetime, timezone as _tz
                            raw = self._mod_dates[pn]
                            plm_dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                                tzinfo=_tz.utc
                            )
                            ts = plm_dt.timestamp()
                            os.utime(dest_path, (ts, ts))
                        except Exception:
                            pass  # 设置 mtime 失败不影响下载本身

                    self.file_done.emit(fname, dest_path)
                    total_done += 1
                self.all_done.emit(total_done)

            elif self._mode == "prequery":
                # 在后台线程中查询附件列表，避免阻塞主线程
                prequery_results: list[tuple[int, list[str]]] = []
                for row_idx, pn, ver, itr in self._prequery_items:
                    try:
                        files = c.list_part_attachments(self._workspace, pn, ver, itr)
                    except Exception:
                        files = []
                    prequery_results.append((row_idx, files))
                self.bom_done.emit(prequery_results)

        except Exception as exc:
            logger.exception("_PullWorker 运行异常")
            self.failure.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


# ─────────────────────────────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────────────────────────────

class PlmWorkbench(QDialog):
    """PLM 工作台主窗口（非模态）。

    布局：
        顶部工具栏  — 工作区路径 + 连接状态 + 所有操作按钮
        主体         — 全幅差异对比表（17列）
        底部状态栏  — 进度条 + 状态文本 + 速度
    """

    # ── 差异对比表列常量 ──────────────────────────────────────────────────
    _DC_SEL    = 0   # 选择 checkbox（表头实现全选）
    _DC_DIFF   = 1   # 差异状态（合并显示 _DC_WARN 的警告信息）
    _DC_PN     = 2   # 零件编号（前缀含 eye/pencil + cube/cubes FontAwesome 4.7 字体，仅在 PLM 数据存在时显示）
    _DC_VER    = 3   # 版本/迭代（PLM，合并显示）
    _DC_LVER   = 4   # 本地版本/迭代（本地 PLM_Version / PLM_Iteration，合并显示）
    _DC_NAME   = 5   # 零件名称
    _DC_TYPE   = 6   # 类型
    _DC_AUTHOR = 7   # 作者
    _DC_COUT   = 8   # 签出者
    _DC_LCST   = 9   # 生命周期状态（PartRevisionDTO.status：WIP/RELEASED/OBSOLETE）
    _DC_LMTIME = 10  # 本地修改时间
    _DC_PMTIME = 11  # PLM 修改时间
    _DC_FILES  = 12  # 文件（附件按钮）

    _DC_HEADERS = [
        "",             # 0  — 选择 checkbox
        "状态",         # 1  — 差异状态（含警告信息）
        "零件编号",     # 2  — 含状态/类型图标
        "版本/迭代",    # 3  — PLM 版本/迭代
        "本地版本",     # 4  — 本地版本
        "零件名称",     # 5
        "类型",         # 6
        "作者",         # 7
        "签出者",       # 8
        "生命周期状态", # 9 — PartRevisionDTO.status：WIP / RELEASED / OBSOLETE
        "本地修改时间", # 10
        "PLM修改时间",  # 11
        "\uf0c6",       # 12 — 回形针图标（FontAwesome paperclip）
    ]

        # 差异状态
    _ST_UNKNOWN   = "?"
    _ST_OK        = "✓ 一致"
    _ST_LOCAL_NEW = "↑ 本地新"
    _ST_PLM_NEW   = "↓ PLM新"
    _ST_LOCAL_ONLY = "仅本地"
    _ST_PLM_ONLY   = "仅PLM"
    _ST_NO_SYNC    = "⚠ 无法同步"

    _STATUS_COLORS = {
        _ST_UNKNOWN:    "#7f8c8d",
        _ST_OK:         "#27ae60",
        _ST_LOCAL_NEW:  "#e67e22",
        _ST_PLM_NEW:    "#2980b9",
        _ST_LOCAL_ONLY: "#8e44ad",
        _ST_PLM_ONLY:   "#16a085",
        _ST_NO_SYNC:    "#e74c3c",
    }

    # 兼容旧版业务逻辑方法引用的列常量（映射到新 _DC_* 值）
    _COL_PN        = 2   # _DC_PN
    _COL_NOM       = 5   # _DC_NAME
    _COL_STATUS    = 1   # _DC_DIFF
    _COL_LOC_VER   = 4   # _DC_LVER（合并 _DC_LITER）
    _COL_LOC_ITER  = 4   # _DC_LVER（原 _DC_LITER，现合并至 _DC_LVER）
    _COL_PLM_VER   = 3   # _DC_VER（合并 _DC_ITER）
    _COL_PLM_ITER  = 3   # _DC_VER（原 _DC_ITER，现合并至 _DC_VER）
    _COL_PLM_USER  = 8   # _DC_COUT
    _COL_PUSH      = 0   # _DC_SEL
    _COL_UPGRADE   = 0   # _DC_SEL
    _BOM_COL_HEADERS = _DC_HEADERS
    _UPGRADE_SKIP  = "不推送"
    _UPGRADE_ITER  = "+迭代"
    _UPGRADE_VER   = "+版本"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PLM 工作台")
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

        # ── 注册 FontAwesome 4.7 字体（仅注册一次，用于 PN 列图标）────────────
        from PySide6.QtGui import QFontDatabase
        _fa_ttf = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "resources", "fontawesome-webfont.ttf"
        )
        if QFontDatabase.addApplicationFont(_fa_ttf) < 0:
            logger.warning("FontAwesome 字体注册失败：%s", _fa_ttf)

        # ── 数据缓存 ──────────────────────────────────────────────────────────
        # 本地工作区扫描结果
        self._local_parts: list = []            # list[LocalPartInfo]
        # PLM parts 缓存：{pn: summary_dict}
        self._plm_cache: dict[str, dict] = {}
        # 差异表行数据（与表格行一一对应）
        self._diff_rows: list[dict] = []        # {pn, local, plm, status}
        # 后台线程句柄
        self._workers: list[QThread] = []
        # 同步进度跟踪
        self._sync_total_nodes = 0
        self._sync_done_nodes  = 0
        self._sync_seen_pns: set = set()
        self._sync_push_map: dict = {}
        self._sync_just_pushed: set[str] = set()  # 刚 Push 成功的 PN，自动刷新时绕过时间比较
        self._catia_search_order_warned = False   # session 级：已提醒过 CATIA 文档查找顺序设置
        self._last_sync_login = ""
        self._last_sync_mode  = ""
        # 进度辅助（兼容旧逻辑）
        self._bom_rows: list[dict] = []
        self._visible_bom_rows: list[dict] = []
        self._plm_parts_cache: dict[str, dict] = {}
        self._sync_result_map: dict[str, tuple[str, str, str]] = {}

        # ── 整体布局（垂直三段） ─────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # 同步选项（折叠，插在工具栏和表格之间）
        self._adv_widget = self._build_advanced_options()
        self._adv_widget.setVisible(False)
        root.addWidget(self._adv_widget)

        root.addWidget(self._build_diff_table(), 1)
        # 内容区：QStackedWidget 切换 diff 表 / CAD入口 表
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._build_diff_table())
        # CAD入口页先放占位，点击时动态构建
        self._cad_page = QWidget()
        self._cad_page_layout = QVBoxLayout(self._cad_page)
        self._cad_page_layout.setContentsMargins(0, 0, 0, 0)
        self._cad_page_layout.setSpacing(0)
        self._content_stack.addWidget(self._cad_page)
        root.addWidget(self._content_stack, 1)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine); sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        root.addWidget(self._build_status_bar())

        # ── 兼容旧代码引用 ────────────────────────────────────────────────────
        self._tbl_bom         = self._tbl_diff
        self._tbl_local       = self._tbl_diff
        self._tbl_arrow       = QTableWidget()
        self._tbl_plm         = QTableWidget()
        self._btn_load_preview = self._btn_load_ws
        self._btn_sync_start   = self._btn_push
        self._lbl_sync_status  = self._lbl_status
        self._lbl_sync_summary = self._lbl_summary
        self._lbl_upload_speed = self._lbl_speed
        self._pgb_sync         = self._pgb
        self._lbl_node_count   = QLabel("")
        self._lbl_plm_query_status = QLabel("")

        # 隐藏的 BOM 树（内部同步追踪）
        self._preview_tree = _BomTreeWidget()
        self._col_vis_widget = QWidget()
        self._col_vis_vbox   = QVBoxLayout(self._col_vis_widget)
        self._col_vis_row0   = QHBoxLayout()
        self._col_vis_row1   = QHBoxLayout()
        self._col_vis_vbox.addLayout(self._col_vis_row0)
        self._col_vis_vbox.addLayout(self._col_vis_row1)
        self._col_checkboxes: dict[str, QCheckBox] = {}
        self._build_col_visibility_row()

        # 初始化设置控件引用（供旧业务方法使用）
        self._init_settings_controls()

        # 加载历史记录
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
        """顶部工具栏：工作区路径 + 连接状态 + 全部操作按钮。"""
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

        sep0 = QFrame(); sep0.setFrameShape(QFrame.VLine); sep0.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep0)

        # 工作区路径显示
        self._lbl_work_dir = QLabel("工作区：—")
        self._lbl_work_dir.setStyleSheet("color: palette(mid);")
        h.addWidget(self._lbl_work_dir)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.VLine); sep1.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep1)

        # 加载工作区
        self._btn_load_ws = QPushButton("↺ 加载工作区")
        self._btn_load_ws.setFont(_ef)
        self._btn_load_ws.setToolTip("扫描工作目录中的 CATPart/CATProduct 文件并通过 CATIA COM 读取属性")
        self._btn_load_ws.clicked.connect(self._on_load_workspace)
        h.addWidget(self._btn_load_ws)

        # 刷新 PLM 状态
        self._btn_refresh_plm = QPushButton("☁ 刷新 PLM 状态")
        self._btn_refresh_plm.setFont(_ef)
        self._btn_refresh_plm.setToolTip("按工作区零件号列表查询 PLM，更新缓存和差异列")
        self._btn_refresh_plm.clicked.connect(self._on_refresh_plm_status)
        h.addWidget(self._btn_refresh_plm)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine); sep2.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep2)

        # Push
        self._btn_push = QPushButton("⬆ Push 选中")
        self._btn_push.setFont(_ef)
        self._btn_push.setObjectName("primaryBtn")
        self._btn_push.setEnabled(False)
        self._btn_push.setToolTip("将勾选零件推送到 PLM")
        self._btn_push.clicked.connect(self._on_sync_start)
        h.addWidget(self._btn_push)

        # Pull
        self._btn_pull_sel = QPushButton("⬇ Pull 选中")
        self._btn_pull_sel.setFont(_ef)
        self._btn_pull_sel.setEnabled(False)
        self._btn_pull_sel.setToolTip("从 PLM 下载勾选零件的文件到工作目录")
        self._btn_pull_sel.clicked.connect(self._on_pull_selected)
        h.addWidget(self._btn_pull_sel)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.VLine); sep3.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep3)

        # 全选 / 全不选
        btn_sel_all  = QPushButton("全选")
        btn_sel_none = QPushButton("全不选")
        btn_sel_all.setFont(_ef); btn_sel_none.setFont(_ef)
        btn_sel_all.setFixedWidth(48); btn_sel_none.setFixedWidth(60)
        btn_sel_all.clicked.connect(lambda: self._set_diff_checked(True))
        btn_sel_none.clicked.connect(lambda: self._set_diff_checked(False))
        h.addWidget(btn_sel_all)
        h.addWidget(btn_sel_none)

        # 新增 PLM Part
        btn_add_plm = QPushButton("+ 新增 PLM Part")
        btn_add_plm.setFont(_ef)
        btn_add_plm.setToolTip("手动输入零件号，追加到本地 PLM 缓存（适用于工作目录为空的情形）")
        btn_add_plm.clicked.connect(self._on_add_plm_part)
        h.addWidget(btn_add_plm)

        h.addStretch()

        # 高级选项（点击切换面板显示/隐藏）
        self._btn_adv = QPushButton("⚙ 同步选项")
        self._btn_adv.setFont(_ef); self._btn_adv.setFlat(True)
        self._btn_adv.setCheckable(False)
        self._btn_adv.setToolTip("显示/隐藏同步选项")
        self._btn_adv.clicked.connect(self._toggle_adv)
        h.addWidget(self._btn_adv)

        sep4 = QFrame(); sep4.setFrameShape(QFrame.VLine); sep4.setFrameShadow(QFrame.Sunken)
        h.addWidget(sep4)

        # 历史
        btn_hist = QPushButton("📋 历史")
        btn_hist.setFont(_ef); btn_hist.setFlat(True)
        btn_hist.clicked.connect(self._on_show_history)
        h.addWidget(btn_hist)

        # 设置
        btn_cfg = QPushButton("⚙ 设置")
        btn_cfg.setFont(_ef); btn_cfg.setFlat(True)
        btn_cfg.clicked.connect(self._on_show_settings)
        h.addWidget(btn_cfg)

        # CAD入口（仅 myPDM 后端可见）
        self._btn_cad_entry = QPushButton("🔧 CAD入口")
        self._btn_cad_entry.setFont(_ef); self._btn_cad_entry.setFlat(True)
        self._btn_cad_entry.setObjectName("primaryBtn")
        self._btn_cad_entry.setToolTip("读取 CATIA 装配结构，匹配 myPDM BOM")
        self._btn_cad_entry.clicked.connect(self._on_cad_entry)
        self._btn_cad_entry.setVisible(False)
        h.addWidget(self._btn_cad_entry)

        self._update_conn_status_bar()
        return bar

    def _build_diff_table(self) -> QWidget:
        """构建主体差异对比表（13 列）。"""
        self._tbl_diff = QTableWidget(0, len(self._DC_HEADERS))
        self._tbl_diff.setHorizontalHeaderLabels(self._DC_HEADERS)
        self._tbl_diff.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_diff.setSelectionMode(QAbstractItemView.NoSelection)
        self._tbl_diff.setFocusPolicy(Qt.NoFocus)
        self._tbl_diff.setStyleSheet(
            "QTableWidget { selection-background-color: transparent; }"
            "QTableWidget::item:hover { background-color: transparent; }"
            "QTableWidget::item:selected { background-color: transparent; color: inherit; }"
        )
        self._tbl_diff.setAlternatingRowColors(True)
        self._tbl_diff.verticalHeader().setDefaultSectionSize(28)
        self._tbl_diff.verticalHeader().setVisible(False)
        # self._tbl_diff.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        hdr = self._tbl_diff.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setStretchLastSection(False)
        # 固定列宽（不可调整）
        fixed_cols = {
            self._DC_SEL:   30,
            self._DC_DIFF:  70,
            self._DC_VER:   70,
            self._DC_LVER:  70,
            self._DC_TYPE:  80,
            self._DC_AUTHOR: 80,
            self._DC_COUT:  80,
            self._DC_LCST:  80,
            self._DC_FILES: 30,
            self._DC_LMTIME: 140,
            self._DC_PMTIME: 140
        }
        for col, w in fixed_cols.items():
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            hdr.resizeSection(col, w)

        # 交互列宽（用户可拖拽调整）
        interactive_cols = {
            self._DC_PN:    200,
            self._DC_NAME:  200
        }
        for col, w in interactive_cols.items():
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            hdr.resizeSection(col, w)
        strectch_cols = {
        }
        for col, w in strectch_cols.items():
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            hdr.resizeSection(col, w)
        
        # 居中对齐的列表头
        for _cc in (self._DC_SEL, self._DC_DIFF, self._DC_VER, self._DC_LVER,
                    self._DC_TYPE, self._DC_AUTHOR, self._DC_COUT, self._DC_LCST,
                    self._DC_LMTIME, self._DC_PMTIME, self._DC_FILES):
            _hi = self._tbl_diff.horizontalHeaderItem(_cc)
            if _hi:
                _hi.setTextAlignment(Qt.AlignCenter)

        # 附件列表头用 FontAwesome 字体
        _fhdr = self._tbl_diff.horizontalHeaderItem(self._DC_FILES)
        if _fhdr:
            _ffa = QFont("FontAwesome"); _ffa.setPointSize(11)
            _fhdr.setFont(_ffa)
            _fhdr.setTextAlignment(Qt.AlignCenter)

        return self._tbl_diff

    def _on_header_select_all(self, checked: bool) -> None:
        """全选/全不选所有行的选择 checkbox。"""
        for i in range(self._tbl_diff.rowCount()):
            w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isEnabled():
                    chk.setChecked(checked)

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

    def _toggle_adv(self, *_) -> None:
        """切换同步选项面板的显示/隐藏。"""
        self._adv_widget.setVisible(not self._adv_widget.isVisible())

    def _set_diff_checked(self, checked: bool) -> None:
        """批量勾选/取消所有行的选择 checkbox（col 0）。"""
        for i in range(self._tbl_diff.rowCount()):
            w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isEnabled():
                    chk.setChecked(checked)

    # ── 兼容旧代码的 conn status bar ─────────────────────────────────────────

    def _build_conn_status_bar(self) -> QWidget:
        """兼容旧调用，返回空 widget（状态已集成到工具栏）。"""
        return QWidget()

    def _update_conn_status_bar(self) -> None:
        base_url, login, _pw, workspace, backend = self._read_conn()
        if base_url and login:
            self._lbl_conn_dot.setText("🟢")
            self._lbl_conn_dot.setStyleSheet("color: green;")
            backend_label = "myPDM" if backend == _BACKEND_MYPDM else "plm-unified"
            self._lbl_conn_info.setText(f"{login} @ {workspace or backend_label}")
        else:
            self._lbl_conn_dot.setText("🔴")
            self._lbl_conn_dot.setStyleSheet("color: red;")
            self._lbl_conn_info.setText("未配置")
        # CAD入口按钮仅 myPDM 后端可见
        self._btn_cad_entry.setVisible(backend == _BACKEND_MYPDM)

    # ─────────────────────────────────────────────────────────────────────────
    # 通用工具
    # ─────────────────────────────────────────────────────────────────────────

    def _read_conn(self) -> tuple[str, str, str, str, str]:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        backend = str(s.value("backend", _BACKEND_UNIFIED))
        if backend not in (_BACKEND_UNIFIED, _BACKEND_MYPDM):
            backend = _BACKEND_UNIFIED
        return (
            s.value("base_url",  _DEFAULT_BASE_URL),
            s.value("login",     _DEFAULT_LOGIN),
            s.value("password",  _DEFAULT_PASSWORD),
            s.value("workspace", _DEFAULT_WORKSPACE),
            backend,
        )

    def _save_conn(self) -> None:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())
        s.setValue("workspace", self._le_workspace.text().strip())
        s.setValue("work_dir",  self._le_work_dir.text().strip())
        # 保存后端类型
        backend = self._backend_combo.currentText() if hasattr(self, "_backend_combo") else _BACKEND_UNIFIED
        backend_key = _BACKEND_MYPDM if backend == "myPDM" else _BACKEND_UNIFIED
        s.setValue("backend", backend_key)

    def _get_pdm_client(self) -> "MyPdmApiClient | PlmApiClient":
        """根据配置的后端类型创建对应的 API 客户端。"""
        _base_url, _login, _pw, _ws, backend = self._read_conn()
        if backend == _BACKEND_MYPDM:
            return MyPdmApiClient(_base_url)
        return PlmApiClient(_base_url)

    def _is_mypdm_backend(self) -> bool:
        """当前是否为 myPDM 后端。"""
        _, _, _, _, backend = self._read_conn()
        return backend == _BACKEND_MYPDM

    def _start_worker(self, worker: QThread) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def _log_to_conn(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "INFO", "ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(level, "INFO")
        self._txt_conn_log.appendPlainText(f"[{ts}] [{prefix}] {msg}")

    def _get_work_dir(self) -> str:
        s = QSettings(_S_ORG, _S_PLM_CFG)
        return s.value("work_dir", "")

    def _on_show_settings(self) -> None:
        # 记录打开前的关键配置，用于检测变更
        s_before = QSettings(_S_ORG, _S_PLM_CFG)
        _before = (
            s_before.value("base_url",  ""),
            s_before.value("workspace", ""),
            s_before.value("work_dir",  ""),
        )

        dlg = _SettingsDialog(self)
        dlg.exec()
        self._update_conn_status_bar()

        # 更新工作区路径显示
        wd = self._get_work_dir()
        self._lbl_work_dir.setText(f"工作区：{wd}" if wd else "工作区：—")

    def _on_cad_entry(self) -> None:
        """CAD入口：CATIA 装配树 → myPDM BOM 匹配 → 内联编辑表格。"""
        base_url, login, password, _ws, backend = self._read_conn()
        if backend != _BACKEND_MYPDM:
            return
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
        except MyPdmApiError as e:
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
        except MyPdmApiError as e:
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
        self._cad_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._cad_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._cad_tree.customContextMenuRequested.connect(self._on_cad_tree_context_menu)
        self._cad_tree.setIndentation(16)
        self._cad_tree.setRootIsDecorated(True)
        self._cad_tree.setStyleSheet("QTreeView::item { min-height: 44px; padding: 4px 0; border-bottom: 1px solid #e0e0e0; }")
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

            # 递归子节点
            children = row.get("children", [])
            if children:
                self._populate_cad_tree(tree, children, node)

    def _on_cad_tree_context_menu(self, pos) -> None:
        """树节点右键菜单。"""
        node = self._cad_tree.itemAt(pos)
        if not node:
            return
        row = node.data(self._CAD_COL_PN, Qt.UserRole)
        match = node.data(self._CAD_COL_PN, Qt.UserRole + 1)
        if not row:
            return
        pn = row.get("part_number", "")

        menu = QMenu(self)
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
        for row in self._cad_rows:
            pn = row.get("part_number", "")
            match = self._cad_match_map.get(pn)
            if match and match.checkout_status == "checked_out" and match.revision_id:
                builtin = row.get("builtin", {})
                user_props = row.get("user_properties", {})
                payload = {}
                for catia_key, pdm_key in bm.items():
                    val = builtin.get(catia_key, "")
                    if val:
                        payload[pdm_key] = val
                for catia_key, pdm_key in pm.items():
                    val = user_props.get(catia_key, "") or builtin.get(catia_key, "")
                    if val:
                        payload[pdm_key] = val
                if not payload:
                    continue
                try:
                    self._cad_client.update_part(match.revision_id, payload)
                    count += 1
                except Exception as e:
                    self._log_to_conn(f"CAD入口：推送失败 {pn} — {e}", "warn")
        if count:
            self._log_to_conn(f"CAD入口：批量推送完成 — {count} 个", "ok")
        else:
            QMessageBox.information(self, "批量推送", "没有可推送的零件（需要已签出状态且有待推送属性）。")

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
        """导出 CATDrawing → PDF → 上传到生产附件。"""
        match = self._cad_match_map.get(pn)
        if not match or not match.revision_id:
            return
        doc_path = row.get("doc_path", "")
        if not doc_path:
            QMessageBox.warning(self, "导出PDF", "找不到源文件路径。")
            return
        # 查找关联的 CATDrawing
        from catia_copilot.catia.dependencies import find_drawing_for_part
        drawing_path = find_drawing_for_part(doc_path)
        if not drawing_path:
            QMessageBox.information(self, "导出PDF", f"未找到关联工程图：{doc_path}")
            return
        try:
            from catia_copilot.catia.file_exporter import export_pdf
            pdf_path = export_pdf(drawing_path)
            if pdf_path:
                self._cad_client.upload_attachment(match.revision_id, pdf_path, "production", overwrite=True)
                self._log_to_conn(f"CAD入口：PDF已上传 — {pn}", "ok")
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
        """导出 STP → 上传到生产附件。"""
        match = self._cad_match_map.get(pn)
        if not match or not match.revision_id:
            return
        try:
            from catia_copilot.catia.file_exporter import export_stp
            stp_path = export_stp(row.get("path", "0"))
            if stp_path:
                self._cad_client.upload_attachment(match.revision_id, stp_path, "production", overwrite=True)
                self._log_to_conn(f"CAD入口：STP已上传 — {pn}", "ok")
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
        """属性→：按字段映射将 CATIA 属性推送到 PDM。"""
        if not match or not match.revision_id:
            return
        pn = row.get("part_number", "")
        builtin = row.get("builtin", {})
        user_props = row.get("user_properties", {})
        field_map = getattr(self, "_cad_field_map", {})

        # 按映射构建 payload
        payload: dict = {}
        bm = field_map.get("builtin", {})
        pm = field_map.get("properties", {})

        # 内置属性映射
        for catia_key, pdm_key in bm.items():
            val = builtin.get(catia_key, "")
            if val:
                payload[pdm_key] = val

        # 用户属性映射
        for catia_key, pdm_key in pm.items():
            # 先查用户属性，再查内置属性
            val = user_props.get(catia_key, "") or builtin.get(catia_key, "")
            if val:
                payload[pdm_key] = val

        if not payload:
            QMessageBox.information(self, "属性→", "未找到可推送的属性。")
            return
        try:
            self._cad_client.update_part(match.revision_id, payload)
            self._log_to_conn(f"CAD入口：属性已推送 — {pn} ({list(payload.keys())})", "ok")
        except Exception as e:
            QMessageBox.critical(self, "推送失败", str(e))

    def _on_cad_pull_attrs(self, row: dict, match) -> None:
        """属性←：从 PDM 拉取属性。"""
        if not match.revision_id:
            return
        try:
            part = self._cad_client.get_part(match.revision_id)
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

        # 检测关键配置是否变更（服务器地址、PLM工作区、本地工作目录）
        s_after = QSettings(_S_ORG, _S_PLM_CFG)
        _after = (
            s_after.value("base_url",  ""),
            s_after.value("workspace", ""),
            s_after.value("work_dir",  ""),
        )
        if _after != _before:
            # 配置已变更：清空差异表和缓存数据，避免显示过期内容
            self._tbl_diff.setRowCount(0)
            self._diff_rows = []
            self._local_parts = []
            self._plm_cache = {}
            self._plm_parts_cache = {}
            self._sync_just_pushed.clear()
            changed_fields = []
            if _after[0] != _before[0]:
                changed_fields.append("服务器地址")
            if _after[1] != _before[1]:
                changed_fields.append("PLM 工作区")
            if _after[2] != _before[2]:
                changed_fields.append("本地工作目录")
            self._lbl_status.setText(
                f"配置已变更（{'/'.join(changed_fields)}），请重新加载工作区。"
            )

    def _on_show_history(self) -> None:
        dlg = _HistoryDialog(self)
        dlg.exec()

    def _init_settings_controls(self) -> None:
        """初始化设置相关控件引用（供旧业务方法使用）。"""
        base_url, login, password, workspace, backend = self._read_conn()
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
        self._tbl_plm_tags  = QTableWidget(0, 2)
        self._le_new_tag    = QLineEdit()
        self._tbl_rules     = QTableWidget(0, 3)
        self._le_rule_catia = QLineEdit()
        self._cmb_rule_tag  = QComboBox(); self._cmb_rule_tag.setEditable(True)
        # 后端选择下拉框（仅在设置弹窗中可见）
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["plm-unified", "myPDM"])
        if backend == _BACKEND_MYPDM:
            self._backend_combo.setCurrentText("myPDM")
        else:
            self._backend_combo.setCurrentText("plm-unified")

        # RadioButton / CheckBox 由 _build_advanced_options 创建并赋值到同名属性
        # 这里不再重复创建，避免孤儿控件覆盖可见控件
        # 仅保留 QSettings 键名引用，供 closeEvent 使用

        self._tbl_history = QTableWidget(0, 7)
        self._tbl_history.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        self._txt_hist = QPlainTextEdit(); self._txt_hist.setReadOnly(True)

        self._reload_rules_table()

        # 工作区路径显示初始化
        wd = work_dir
        self._lbl_work_dir.setText(f"工作区：{wd}" if wd else "工作区：—")

    # ─────────────────────────────────────────────────────────────────────────
    # 加载工作区
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _check_catia_doc_locator_order() -> bool:
        """检查 CATIA 文档查找顺序设置：File_CurrentDir 是否在 File_StorageName 之前。

        读取 %AppData%\\DassaultSystemes\\CATSettings\\SymbolicLinks.CATSettings
        中的 DocLocators 字段（分号分隔的 locator:enabled 列表）。

        返回 True 表示顺序正确（无需提醒），False 表示顺序不对或文件不可读。
        """
        import re as _re
        try:
            import os as _os, pathlib as _pl
            f = _pl.Path(_os.environ['APPDATA']) / 'DassaultSystemes' / 'CATSettings' / 'SymbolicLinks.CATSettings'
            data = f.read_bytes()
            m = _re.search(rb'File_FeatCatalog:[^\x00\x80\x9f]+', data)
            if not m:
                return False
            locators = m.group().decode('ascii', errors='ignore').rstrip('"').split(';')
            names = [item.split(':')[0] for item in locators]
            if 'File_CurrentDir' in names and 'File_StorageName' in names:
                return names.index('File_CurrentDir') < names.index('File_StorageName')
            return False
        except Exception:
            return False

    def _on_load_workspace(self) -> None:
        """扫描工作目录，通过 CATIA COM 读取每个文件的属性，填充差异表。"""
        if self._is_mypdm_backend():
            QMessageBox.information(self, "myPDM", "myPDM 后端请使用 '🔧 CAD入口' 功能。")
            return
        # 检查 CATIA 文档查找顺序设置（每次启动后仅提醒一次，设置正确则静默通过）
        if not self._catia_search_order_warned:
            self._catia_search_order_warned = True
            if not self._check_catia_doc_locator_order():
                ret = QMessageBox.warning(
                    self, "CATIA 设置需要调整",
                    "检测到 CATIA 文档查找顺序设置不正确。\n\n"
                    "当前 CATIA 打开 CATProduct 时会优先按内部存储的绝对路径查找子件，\n"
                    "可能导致找到的文件非工作目录中的文件。\n\n"
                    "请前往 CATIA：\n"
                    "  工具 > 选项 > 常规 > 文档\n"
                    "  在「已链接的文档本地化」列表中，\n"
                    "  将「指向文档的文件夹」移到「链接文件夹」之前。\n\n"
                    "调整后重新启动 CATIA，使修改生效。",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                )
                if ret == QMessageBox.StandardButton.Cancel:
                    self._catia_search_order_warned = False  # 取消则下次仍提醒
                    return

        work_dir = self._get_work_dir()
        if not work_dir:
            QMessageBox.warning(self, "未设置工作目录", '请先在"设置"中配置工作目录。')
            return

        import os
        if not os.path.isdir(work_dir):
            QMessageBox.warning(self, "工作目录不存在", f"路径不存在：\n{work_dir}")
            return

        self._btn_load_ws.setEnabled(False)
        self._pgb.setRange(0, 0)
        self._pgb.setVisible(True)
        self._lbl_status.setText("正在扫描工作区……")

        w = _WorkspaceScanWorker(work_dir)
        w.progress.connect(self._on_scan_progress)
        w.scan_done.connect(self._on_scan_done)
        w.failure.connect(self._on_scan_fail)
        self._start_worker(w)

    def _on_scan_progress(self, done: int, total: int, filename: str) -> None:
        if total > 0:
            self._pgb.setRange(0, total)
            self._pgb.setValue(done)
        msg = f"正在读取 ({done}/{total})：{filename}" if filename else f"扫描完成 {done} 个文件"
        self._lbl_status.setText(msg)

    def _on_scan_done(self, local_parts: list) -> None:
        """扫描完成：缓存结果，加载本地 PLM 缓存，合并后填充表格。"""
        self._btn_load_ws.setEnabled(True)
        self._pgb.setVisible(False)
        self._local_parts = local_parts

        # 从本地缓存文件读取已知 PLM 数据
        work_dir = self._get_work_dir()
        try:
            from catia_copilot.plm.workspace_scanner import load_plm_cache
            self._plm_cache = load_plm_cache(work_dir)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"读取 PLM 缓存失败：{exc}")
            self._plm_cache = {}

        # 同步到旧兼容缓存
        self._plm_parts_cache = {pn: d for pn, d in self._plm_cache.items() if d}

        self._populate_diff_table()
        n_local = len(local_parts)
        n_plm   = len(self._plm_cache)
        self._lbl_status.setText(f"工作区已加载：{n_local} 个本地文件，{n_plm} 条 PLM 缓存记录")
        # 加载完成后自动刷新 PLM 状态
        QTimer.singleShot(500, self._on_refresh_plm_status)
        self._btn_push.setEnabled(True)
        self._btn_pull_sel.setEnabled(True)

    def _on_scan_fail(self, err: str) -> None:
        self._btn_load_ws.setEnabled(True)
        self._pgb.setVisible(False)
        self._lbl_status.setText(f"扫描失败：{err}")
        # 扫描失败时清空表格，避免显示上次的过期数据
        self._tbl_diff.setRowCount(0)
        self._diff_rows = []
        self._local_parts = []
        QMessageBox.critical(self, "扫描工作区失败", err)

    # ─────────────────────────────────────────────────────────────────────────
    # 刷新 PLM 状态
    # ─────────────────────────────────────────────────────────────────────────

    def _on_refresh_plm_status(self) -> None:
        """按本地文件零件号列表查询 PLM，更新缓存和差异列。"""
        if self._is_mypdm_backend():
            return
        # 互斥：已有 PLM 状态刷新 worker 在运行时，直接返回（避免并发写 _plm_cache）
        if getattr(self, "_plm_status_running", False):
            return

        base_url, login, password, workspace, _be = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", '请先在"设置"中配置 PLM 连接信息。')
            return

        # 收集所有零件号（本地文件 + 已有缓存中的）
        pns_from_local = [info.part_number for info in self._local_parts if info.part_number]
        pns_from_cache = list(self._plm_cache.keys())
        all_pns = list(dict.fromkeys(pns_from_local + pns_from_cache))  # 去重保序

        if not all_pns:
            QMessageBox.information(self, "无零件", "请先加载工作区或新增 PLM Part。")
            return

        self._plm_status_running = True
        self._btn_refresh_plm.setEnabled(False)
        self._pgb.setRange(0, len(all_pns))
        self._pgb.setValue(0)
        self._pgb.setVisible(True)
        self._lbl_status.setText(f"正在查询 PLM 状态（0/{len(all_pns)}）……")

        w = _PlmStatusWorker(base_url, login, password, workspace, all_pns)
        w.progress.connect(self._on_plm_status_progress)
        w.done.connect(self._on_plm_status_done)
        w.failure.connect(self._on_plm_status_fail)
        self._start_worker(w)

    def _on_plm_status_progress(self, done: int, total: int) -> None:
        self._pgb.setValue(done)
        self._lbl_status.setText(f"查询 PLM 状态……（{done}/{total}）")

    def _on_plm_status_done(self, result: dict) -> None:
        """PLM 查询完成：合并缓存，刷新表格差异列。"""
        self._plm_status_running = False
        self._btn_refresh_plm.setEnabled(True)
        self._pgb.setVisible(False)

        # 合并到 PLM 缓存并持久化
        work_dir = self._get_work_dir()
        if work_dir:
            try:
                from catia_copilot.plm.workspace_scanner import merge_plm_cache
                self._plm_cache = merge_plm_cache(work_dir, result)
            except Exception:
                self._plm_cache.update(result)
        else:
            self._plm_cache.update(result)

        self._plm_parts_cache = {pn: d for pn, d in self._plm_cache.items() if d}

        # 更新本地已推送零件的本地版本/迭代信息（CATIA 文件已由 _write_plm_attrs_to_catia 更新）
        if self._sync_just_pushed:
            for info in self._local_parts:
                if info.part_number in self._sync_just_pushed:
                    plm_data = self._plm_cache.get(info.part_number)
                    if plm_data:
                        info.plm_version   = str(plm_data.get("version", info.plm_version))
                        info.plm_iteration = int(plm_data.get("lastIterationNumber", info.plm_iteration))
                    try:
                        info.mtime = datetime.fromtimestamp(os.path.getmtime(info.filepath))
                    except Exception:
                        pass

        # 重新填充表格（含新 PLM 数据）
        self._populate_diff_table()

        found    = sum(1 for v in result.values() if v is not None)
        not_found = len(result) - found
        self._lbl_status.setText(
            f"PLM 状态已刷新：找到 {found} 个，未找到 {not_found} 个"
        )

    def _on_plm_status_fail(self, err: str) -> None:
        self._plm_status_running = False
        self._btn_refresh_plm.setEnabled(True)
        self._pgb.setVisible(False)
        self._lbl_status.setText(f"PLM 查询失败：{err}")
        QMessageBox.critical(self, "PLM 查询失败", err)

    # 兼容旧方法引用
    def _on_plm_status_loaded(self, parts: list) -> None:
        pass

    def _on_plm_status_error(self, err: str) -> None:
        self._on_plm_status_fail(err)

    # ─────────────────────────────────────────────────────────────────────────
    # 填充差异对比表
    # ─────────────────────────────────────────────────────────────────────────

    def _populate_diff_table(self) -> None:
        """根据 _local_parts 和 _plm_cache 构建差异行并填充表格。"""

        from catia_copilot.plm.workspace_scanner import LocalPartInfo

        self._tbl_diff.setRowCount(0)
        self._diff_rows = []

        # 构建 pn → local_info 映射（同一 pn 可能有多个文件，取第一个）
        local_map: dict[str, LocalPartInfo] = {}
        for info in self._local_parts:
            if info.part_number and info.part_number not in local_map:
                local_map[info.part_number] = info

        # 汇总所有 pn（本地 + PLM 缓存）
        all_pns: list[str] = []
        seen: set[str] = set()
        for info in self._local_parts:
            if info.part_number and info.part_number not in seen:
                all_pns.append(info.part_number)
                seen.add(info.part_number)
        for pn in self._plm_cache:
            if pn not in seen:
                all_pns.append(pn)
                seen.add(pn)

        self._tbl_diff.setRowCount(len(all_pns))

        for row_idx, pn in enumerate(all_pns):
            local = local_map.get(pn)
            plm   = self._plm_cache.get(pn)

            # ── 计算差异状态 ──────────────────────────────────────────────────
            status = self._compute_diff_status(local, plm)
            # 刚 Push 完成的零件跳过时间比较（Phase 1 的 modificationDate
            # 与 Phase 2 的本地保存时间差可能远大于容差）
            if pn in self._sync_just_pushed:
                status = self._ST_OK

            # ── 计算警告条件 ──────────────────────────────────────────────────
            warn = False
            if local:
                warn = (
                    local.no_file or
                    not local.is_saved or
                    not local.part_number
                )
                if not local.is_readable:
                    warn = True  # COM 读取失败（软警告）
            # 他人签出也警告（Push 时阻止）
            if plm:
                cout = str(plm.get("checkOutUser") or "")
                _, current_login, _, _, _ = self._read_conn()
                if cout and cout != current_login:
                    warn = True

            # ── 文件类型判断 ──────────────────────────────────────────────────
            ftype = local.file_type if local else (plm.get("type", "") if plm else "")
            is_product = "Product" in ftype

            # ── Push/Pull 可用性 ─────────────────────────────────────────────
            # 不再硬性排除 _ST_PLM_NEW，调试时可强制 Push（_on_sync_start 会弹确认）
            can_push = (local is not None and
                        not local.no_file and
                        local.is_saved and
                        bool(local.part_number))
            can_pull = (plm is not None)

            # ── PLM 版本信息 ─────────────────────────────────────────────────
            ver = str(plm.get("version", "") or "") if plm else ""
            itr = str(plm.get("lastIterationNumber", "") or "") if plm else ""

            # ── 辅助函数 ─────────────────────────────────────────────────────
            def _item(val: str, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
                it = QTableWidgetItem(str(val))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setTextAlignment(align)
                return it

            # ── 写入各列 ─────────────────────────────────────────────────────

            # col 0: 选择 checkbox（让 checkbox 使用自然 sizeHint，避免 Windows 11 风格裁剪）
            chk_w = QWidget(); chk_l = QHBoxLayout(chk_w)
            chk_l.setContentsMargins(0, 0, 0, 0); chk_l.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setEnabled(can_push or can_pull)
            chk.setChecked(status in (
                self._ST_LOCAL_NEW, self._ST_LOCAL_ONLY,
                self._ST_PLM_NEW, self._ST_PLM_ONLY,
            ))
            chk_l.addWidget(chk)
            self._tbl_diff.setCellWidget(row_idx, self._DC_SEL, chk_w)

            # col 1: 差异状态
            st_text = status
            st_item = _item(st_text, Qt.AlignCenter)
            color = self._STATUS_COLORS.get(status, "#7f8c8d")
            st_item.setForeground(QColor(color))
            if warn:
                tips = []
                if local:
                    if local.no_file: tips.append("文件从未保存到磁盘")
                    if not local.is_saved: tips.append("文件有未保存的修改")
                    if not local.is_readable: tips.append("COM 读取部分失败，属性可能不完整（不影响 Push）")
                    if not local.part_number: tips.append("零件号为空")
                if status == self._ST_PLM_NEW: tips.append("PLM 版本更新，建议先 Pull")
                if tips:
                    st_item.setToolTip("\n".join(tips))
            self._tbl_diff.setItem(row_idx, self._DC_DIFF, st_item)

            # col 2: 零件编号（前缀含 FontAwesome 4.7 字体：eye/pencil + cube/cubes）
            checkout_user = str(plm.get("checkOutUser") or "") if plm else ""
            is_checked_out = bool(checkout_user)
            pn_widget = self._make_pn_cell_widget(pn, is_product, bool(plm), is_checked_out, checkout_user)
            self._tbl_diff.setCellWidget(row_idx, self._DC_PN, pn_widget)

            # col 3: 版本/迭代（PLM）
            ver_text = f"{ver} / {itr}" if ver and itr else (ver or itr or "")
            self._tbl_diff.setItem(row_idx, self._DC_VER, _item(ver_text, Qt.AlignCenter))

            # col 4: 本地版本/迭代
            loc_ver = str(local.plm_version or "") if local else ""
            loc_iter_int = int(local.plm_iteration or 0) if local else 0
            loc_itr = str(loc_iter_int) if loc_iter_int else ""
            lver_text = f"{loc_ver} / {loc_itr}" if loc_ver and loc_itr else (loc_ver or loc_itr or "")
            lver_item = _item(lver_text, Qt.AlignCenter)
            if (ver and loc_ver and loc_ver != ver) or (itr and loc_itr and loc_itr != itr):
                lver_item.setForeground(QColor("#e67e22"))
            self._tbl_diff.setItem(row_idx, self._DC_LVER, lver_item)

            # col 5: 零件名称
            name = (plm.get("name") or "") if plm else (local.nomenclature if local else "")
            self._tbl_diff.setItem(row_idx, self._DC_NAME, _item(str(name)))

            # col 6: 类型
            self._tbl_diff.setItem(row_idx, self._DC_TYPE, _item(ftype, Qt.AlignCenter))

            # col 7: 作者
            author = str(plm.get("authorLogin") or "") if plm else ""
            self._tbl_diff.setItem(row_idx, self._DC_AUTHOR, _item(author, Qt.AlignCenter))

            # col 8: 签出者（他人=橙色，本人=普通）
            cout_str = checkout_user
            cout_item = _item(cout_str, Qt.AlignCenter)
            if cout_str:
                _, _cur_login, _, _ = self._read_conn()
                if cout_str != _cur_login:
                    cout_item.setForeground(QColor("#e67e22"))  # 他人签出：橙色
            self._tbl_diff.setItem(row_idx, self._DC_COUT, cout_item)

            # col 9: 生命周期状态
            lc_state = str(plm.get("lifecycleState") or "") if plm else ""
            self._tbl_diff.setItem(row_idx, self._DC_LCST, _item(lc_state, Qt.AlignCenter))

            # col 10: 本地修改时间
            lmtime = local.mtime.strftime("%Y-%m-%d %H:%M:%S") if local else "—"
            self._tbl_diff.setItem(row_idx, self._DC_LMTIME, _item(lmtime, Qt.AlignCenter))

            # col 11: PLM 修改时间
            pmtime_raw = str(plm.get("modificationDate") or "") if plm else ""
            pmtime = self._format_plm_date(pmtime_raw) if pmtime_raw else "—"
            self._tbl_diff.setItem(row_idx, self._DC_PMTIME, _item(pmtime, Qt.AlignCenter))

            # col 12: 附件图标（FontAwesome paperclip，与 PN 列图标样式一致）
            if plm:
                lbl_files = QLabel("\uf0c6")
                _fa_f = QFont("FontAwesome"); _fa_f.setPointSize(11)
                lbl_files.setFont(_fa_f)
                lbl_files.setStyleSheet("color: #4C566A;")
                lbl_files.setAlignment(Qt.AlignCenter)
                lbl_files.setToolTip("查看 PLM 附件")
                lbl_files.setCursor(Qt.PointingHandCursor)
                lbl_files.mousePressEvent = lambda e, p=pn, v=ver, pi=plm: self._on_show_attachments(p, v, pi)
                w = QWidget(); l = QHBoxLayout(w)
                l.setContentsMargins(0, 0, 0, 0); l.setAlignment(Qt.AlignCenter)
                l.addWidget(lbl_files)
                self._tbl_diff.setCellWidget(row_idx, self._DC_FILES, w)
            else:
                self._tbl_diff.setItem(row_idx, self._DC_FILES, _item(""))

            # 记录差异行数据
            self._diff_rows.append({
                "pn":     pn,
                "local":  local,
                "plm":    plm,
                "status": status,
                "row":    row_idx,
            })

        # 清空刚 Push 标记，后续刷新走正常时间比较
        if self._sync_just_pushed:
            self._sync_just_pushed.clear()

        # 同步旧兼容字段
        self._visible_bom_rows = [{"Part Number": r["pn"]} for r in self._diff_rows]
        self._bom_rows = self._visible_bom_rows


    def _make_pn_cell_widget(
        self,
        part_number: str,
        is_product: bool,
        has_plm_data: bool,
        is_checked_out: bool,
        checkout_user: str,
    ) -> "QWidget":
        """构建零件编号单元格小窗口：FontAwesome 字体图标 + 加粗文字。

        图标规则：
          eye(\\uf06e)/pencil(\\uf040) — 仅 has_plm_data=True 时显示
          cubes(\\uf1b3)/cube(\\uf1b2) — 始终显示（来源为本地文件类型）

        使用 FontAwesome 4.7 字体，零依赖，无 SVG 截断问题。
        """
        from PySide6.QtGui import QFont as _QF
        from PySide6.QtWidgets import QLabel as _QL

        ICON_PT    = 11           # 字体图标磅值
        ICON_COLOR = "#4C566A"   # 中性灰

        # FontAwesome 4.7 codepoints
        FA_EYE    = "\uf06e"
        FA_PENCIL = "\uf040"
        FA_CUBE   = "\uf1b2"
        FA_CUBES  = "\uf1b3"

        _fa_font = _QF("FontAwesome", ICON_PT)

        def _icon_lbl(char: str, tip: str, visible: bool = True) -> _QL:
            lbl = _QL(char if visible else "")
            lbl.setFont(_fa_font)
            lbl.setStyleSheet(f"color: {ICON_COLOR};")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedWidth(18)   # 固定宽，确保两列图标对齐
            if tip:
                lbl.setToolTip(tip)
            return lbl

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 2, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 图标1：eye / pencil（PLM 状态）
        if has_plm_data:
            ch1  = FA_PENCIL if is_checked_out else FA_EYE
            tip1 = f"已签出（{checkout_user}）" if is_checked_out else "未签出（已签入）"
            layout.addWidget(_icon_lbl(ch1, tip1))
        else:
            layout.addWidget(_icon_lbl("", "", visible=False))  # 占位，保持列对齐

        # 图标2：cube / cubes（本地文件类型）
        ch2  = FA_CUBES if is_product else FA_CUBE
        tip2 = "装配体 (CATProduct)" if is_product else "零件 (CATPart)"
        layout.addWidget(_icon_lbl(ch2, tip2))

        # 零件编号文字（默认颜色，加粗）
        lbl_pn = _QL(part_number)
        _f = lbl_pn.font(); _f.setBold(True); lbl_pn.setFont(_f)
        lbl_pn.setToolTip(part_number)
        layout.addWidget(lbl_pn, 1)

        return container

    @staticmethod
    def _compute_diff_status(local, plm) -> str:
        """计算差异状态。"""
        from datetime import datetime as _dt, timezone as _tz

        if local is None and plm is None:
            return PlmWorkbench._ST_UNKNOWN
        if local is None:
            return PlmWorkbench._ST_PLM_ONLY
        if plm is None:
            return PlmWorkbench._ST_LOCAL_ONLY

        # 两侧都有：比较版本/迭代/时间
        loc_ver  = str(local.plm_version or "")
        plm_ver  = str(plm.get("version") or "")
        loc_iter = int(local.plm_iteration or 0)
        plm_iter = int(plm.get("lastIterationNumber") or 0)

        # 硬性阻止
        if not local.is_saved or local.no_file or not local.is_readable:
            return PlmWorkbench._ST_NO_SYNC

        if loc_ver and plm_ver:
            if (loc_ver, loc_iter) > (plm_ver, plm_iter):
                return PlmWorkbench._ST_LOCAL_NEW
            elif (loc_ver, loc_iter) < (plm_ver, plm_iter):
                return PlmWorkbench._ST_PLM_NEW
            # 版本迭代相同，比修改时间（带容差）
            # PLM 时间是 UTC，local.mtime 是文件系统本地时间，必须统一时区再比较
            pmtime_raw = str(plm.get("modificationDate") or "")
            plm_mtime = None
            if pmtime_raw:
                try:
                    plm_mtime = _dt.strptime(pmtime_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                        tzinfo=_tz.utc
                    )
                except Exception:
                    pass
            if plm_mtime:
                # local.mtime 转为带时区的本地时间（利用系统时区）
                local_mtime = local.mtime
                if local_mtime.tzinfo is None:
                    local_mtime = local_mtime.astimezone()
                # 截断到秒，消除文件系统亚秒精度与 PLM 秒精度之间的误差
                local_utc = local_mtime.astimezone(_tz.utc).replace(microsecond=0)
                diff_sec = abs((local_utc - plm_mtime).total_seconds())
                if diff_sec <= _DIFF_TIME_TOLERANCE_SEC:
                    return PlmWorkbench._ST_OK
                if local_utc > plm_mtime:
                    return PlmWorkbench._ST_LOCAL_NEW
                else:
                    return PlmWorkbench._ST_PLM_NEW
            return PlmWorkbench._ST_OK

        # 缺少版本信息（未同步过）：
        # - plm_ver 也为空 → 仅本地
        # - plm_ver 非空但 loc_ver 为空 → PLM 有版本但本地从未同步，应提示 PLM 有更新可 Pull
        if not plm_ver:
            return PlmWorkbench._ST_LOCAL_ONLY
        return PlmWorkbench._ST_PLM_NEW

    @staticmethod
    def _format_plm_date(raw: str) -> str:
        """将 PLM 返回的 UTC ISO 日期字符串转换为系统本地时间并格式化。"""
        if not raw:
            return "—"
        try:
            from datetime import datetime, timezone
            s = raw.strip()
            dt_utc = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            # astimezone() 无参数 → 系统本地时区
            dt_local = dt_utc.astimezone()
            return dt_local.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return raw[:19].replace("T", " ") if len(raw) >= 19 else raw

    # ─────────────────────────────────────────────────────────────────────────
    # 附件弹窗
    # ─────────────────────────────────────────────────────────────────────────

    def _on_show_attachments(self, pn: str, version: str, plm_data: dict) -> None:
        """弹出零件附件详情窗口。"""
        base_url, login, password, workspace, _be = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", "请先配置 PLM 连接信息。")
            return
        dlg = _AttachmentDialog(
            base_url, login, password, workspace,
            pn, version, plm_data,
            work_dir=self._get_work_dir(),
            parent=self,
        )
        dlg.exec()

    # ─────────────────────────────────────────────────────────────────────────
    # Push（同步到 PLM）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_sync_start(self) -> None:
        """读取勾选行，执行 Push 到 PLM。"""
        if self._is_mypdm_backend():
            QMessageBox.information(self, "myPDM", "myPDM 后端请使用 '🔧 CAD入口' 功能。Push 功能后续版本支持。")
            return
        base_url, login, password, workspace, _be = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", "请先配置 PLM 连接信息。")
            return

        # 收集勾选行
        push_rows = []
        for row_data in self._diff_rows:
            i = row_data["row"]
            w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            local = row_data.get("local")
            if not local:
                continue
            push_rows.append(row_data)

        if not push_rows:
            QMessageBox.information(self, "未选择", "请先勾选要 Push 的零件行。")
            return

        # ── 实时查询 PLM 最新状态（替代缓存数据） ─────────────────────────────
        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            c = PlmApiClient(base_url)
            c.login(login, password)
            pns_to_check = [r["pn"] for r in push_rows]
            fresh_data = c.search_parts_summary(workspace, pns_to_check)
            has_recheck = False
            for row_data in push_rows:
                pn = row_data["pn"]
                if pn not in fresh_data or not fresh_data[pn]:
                    continue
                fresh_plm = fresh_data[pn]
                old_plm = row_data.get("plm")
                if (old_plm is None
                    or old_plm.get("version") != fresh_plm.get("version")
                    or old_plm.get("lastIterationNumber") != fresh_plm.get("lastIterationNumber")):
                    row_data["plm"] = fresh_plm
                    row_data["status"] = self._compute_diff_status(
                        row_data.get("local"), fresh_plm
                    )
                    has_recheck = True
            if has_recheck:
                self._plm_cache.update({pn: fresh_data[pn]
                                        for pn in fresh_data if fresh_data[pn]})
        except Exception:
            pass  # 实时查询失败则使用缓存数据，不阻断 Push

        # 实时刷新 push_rows 里各文件的保存状态（COM 查询，避免用户在 CATIA 里保存后不重扫仍报未保存）
        try:
            from catia_copilot.catia.connection import get_catia
            _catia = get_catia()
            if _catia is not None:
                _open_docs = {}
                for _d in _catia.Documents:
                    try:
                        _open_docs[_d.FullName.lower()] = _d
                    except Exception:
                        pass
                for _row in push_rows:
                    _local = _row.get("local")
                    if _local and _local.filepath:
                        _doc = _open_docs.get(_local.filepath.lower())
                        if _doc is not None:
                            try:
                                _local.is_saved = not bool(_doc.Modified)
                            except Exception:
                                pass
        except Exception:
            pass  # COM 不可用时降级用缓存値

        # 检查未保存
        unsaved = [r["pn"] for r in push_rows
                   if r["local"] and not r["local"].is_saved]
        if unsaved:
            msg = "\n".join(unsaved[:10])
            if len(unsaved) > 10:
                msg += f"\n…等共 {len(unsaved)} 个"
            QMessageBox.critical(
                self, "存在未保存文件",
                f"以下文件有未保存的修改，请先保存后再同步：\n\n{msg}",
            )
            return

        # 校验文件名 stem == 零件编号（Pull 后 CATIA 按文件名在同目录找引用，必须一致）
        invalid_names = []
        for r in push_rows:
            local = r.get("local")
            if local and local.filepath:
                stem = os.path.splitext(os.path.basename(local.filepath))[0]
                if stem != local.part_number:
                    invalid_names.append(
                        f"{local.part_number}  ←  文件：{os.path.basename(local.filepath)}"
                    )
        if invalid_names:
            msg = "\n".join(invalid_names[:10])
            if len(invalid_names) > 10:
                msg += f"\n…等共 {len(invalid_names)} 个"
            QMessageBox.critical(
                self, "文件名与零件编号不一致",
                f"以下文件的文件名（不含扩展名）与零件编号不一致：\n\n{msg}\n\n"
                "Push 后 Pull 时 CATIA 将无法在同目录找到引用的子文件。\n"
                "请在 CATIA 中另存为正确文件名后再 Push。",
            )
            return

        # PLM_NEW 行警告（PLM 有更新版本，覆盖推送会丢失 PLM 侧修改）
        plm_newer = [r["pn"] for r in push_rows if r.get("status") == self._ST_PLM_NEW]
        if plm_newer:
            msg = "\n".join(plm_newer[:10])
            if len(plm_newer) > 10:
                msg += f"\n…等共 {len(plm_newer)} 个"
            ret = QMessageBox.warning(
                self, "PLM 有更新版本",
                f"以下零件在 PLM 中有更新版本，强制推送会覆盖 PLM 侧的修改：\n\n{msg}\n\n确认继续？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if ret != QMessageBox.Yes:
                return

        options = self._build_sync_options()
        push_map = {r["pn"]: self._UPGRADE_ITER for r in push_rows}
        options.part_upgrade_map = push_map
        self._sync_push_map = push_map
        self._sync_just_pushed = set(push_map.keys())

        self._btn_push.setEnabled(False)
        self._btn_load_ws.setEnabled(False)
        total_nodes = len(push_map)
        self._pgb.setRange(0, total_nodes)
        self._pgb.setValue(0)
        self._pgb.setVisible(True)
        self._sync_total_nodes = total_nodes
        self._sync_done_nodes  = 0
        self._sync_seen_pns    = set()
        self._lbl_status.setText(f"正在同步…… (0/{total_nodes})")
        self._sync_result_map.clear()
        self._lbl_summary.setText("")
        self._last_sync_login = login
        self._last_sync_mode  = "Push 选中"

        w = _SyncWorker(base_url, login, password, workspace, options, push_rows)
        w.progress.connect(self._on_sync_progress)
        w.upload_log.connect(self._on_upload_log)
        w.sync_done.connect(self._on_sync_done)
        w.error.connect(self._on_sync_error)
        self._start_worker(w)

    def _on_sync_done(self, result) -> None:
        self._btn_push.setEnabled(True)
        self._btn_load_ws.setEnabled(True)
        self._pgb.setValue(self._pgb.maximum())
        self._pgb.setVisible(False)
        self._lbl_status.setText("同步完成")
        self._lbl_speed.setText("")
        parts = [
            f"新建 {result.created}", f"更新 {result.updated}",
            f"跳过 {result.skipped}", f"无变化 {result.unchanged}",
            f"失败 {result.failed}",
        ]
        self._lbl_summary.setText("  ".join(parts))
        # 有异常时弹窗展示详情
        if result.errors:
            msg_lines = [f"同步完成，共 {len(result.errors)} 条警告/错误：\n"]
            for e in result.errors[:20]:
                msg_lines.append(f"  · {e}")
            if len(result.errors) > 20:
                msg_lines.append(f"  …等共 {len(result.errors)} 条")
            QMessageBox.warning(self, "同步结果", "\n".join(msg_lines))
        self._save_history(result, user=self._last_sync_login, mode=self._last_sync_mode)
        self._refresh_history_list()
        # 同步完成后自动刷新 PLM 状态
        if result.created > 0 or result.updated > 0:
            QTimer.singleShot(800, self._on_refresh_plm_status)

    def _on_sync_error(self, err: str) -> None:
        self._btn_push.setEnabled(True)
        self._btn_load_ws.setEnabled(True)
        self._pgb.setVisible(False)
        self._lbl_speed.setText("")
        self._lbl_status.setText(f"同步失败：{err}")
        # Push 失败时清空 _sync_just_pushed，避免差异表伪装成"一致"
        self._sync_just_pushed.clear()
        QMessageBox.critical(self, "同步失败", err)

    # ─────────────────────────────────────────────────────────────────────────
    # Pull（从 PLM 拉取文件）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pull(self) -> None:
        """弹出 Pull 对话框（BOM 树模式）。"""
        base_url, login, password, workspace, _be = self._read_conn()
        work_dir = self._get_work_dir()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", "请先配置 PLM 连接信息。")
            return
        if not work_dir:
            QMessageBox.warning(self, "未设置工作目录", "请先在设置中配置工作目录。")
            return
        dlg = _PullDialog(base_url, login, password, workspace, work_dir, parent=self)
        dlg.exec()

    def _on_pull_selected(self) -> None:
        """Pull 勾选行对应的 PLM 零件文件到工作目录。"""
        if self._is_mypdm_backend():
            QMessageBox.information(self, "myPDM", "myPDM 后端请使用 '🔧 CAD入口' 功能。Pull 功能后续版本支持。")
            return
        base_url, login, password, workspace, _be = self._read_conn()
        work_dir = self._get_work_dir()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", "请先配置 PLM 连接信息。")
            return
        if not work_dir:
            QMessageBox.warning(self, "未设置工作目录", "请先在设置中配置工作目录。")
            return

        checked = []
        for row_data in self._diff_rows:
            i = row_data["row"]
            w = self._tbl_diff.cellWidget(i, self._DC_SEL)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            plm = row_data.get("plm")
            if not plm:
                continue
            pn  = row_data["pn"]
            ver = str(plm.get("version") or "A")
            itr = str(plm.get("lastIterationNumber") or "0")
            checked.append((pn, ver, itr))

        if not checked:
            QMessageBox.information(self, "未选择", "请先勾选有 PLM 版本信息的行。")
            return

        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            c = PlmApiClient(base_url)
            c.login(login, password)
        except Exception as exc:
            QMessageBox.critical(self, "连接失败", str(exc))
            return

        # ── 实时查询 PLM 最新版本（替代缓存数据） ──────────────────────────────
        try:
            pns_to_refresh = list(set(pn for pn, _, _ in checked))
            fresh_data = c.search_parts_summary(workspace, pns_to_refresh)
            updated_checked = []
            for pn, ver, itr in checked:
                if pn in fresh_data and fresh_data[pn]:
                    fresh = fresh_data[pn]
                    ver = str(fresh.get("version") or ver)
                    itr = str(fresh.get("lastIterationNumber") or itr)
                updated_checked.append((pn, ver, itr))
            checked = updated_checked
            self._plm_cache.update({pn: fresh_data[pn]
                                    for pn in fresh_data if fresh_data[pn]})
        except Exception:
            pass  # 实时查询失败则使用缓存数据

        self._pulled_pns = set(pn for pn, _, _ in checked)

        dl_items: list[tuple[str, str, str, str]] = []
        # mod_dates：pn → PLM modificationDate，下载后设置文件 mtime 避免误判"本地新"
        mod_dates: dict[str, str] = {
            pn: str(self._plm_cache.get(pn, {}).get("modificationDate") or "")
            for pn, _, _ in checked
        }
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

        self._pgb.setRange(0, len(dl_items))
        self._pgb.setValue(0)
        self._pgb.setVisible(True)
        self._lbl_status.setText(f"开始下载 {len(dl_items)} 个文件……")

        w = _PullWorker(base_url, login, password, workspace)
        w.file_progress.connect(lambda fn, dl, tot, spd: self._lbl_speed.setText(f"{spd/1024/1024:.1f} MB/s" if spd >= 1048576 else f"{spd/1024:.1f} KB/s"))
        w.file_done.connect(lambda fn, dest: self._pgb.setValue(self._pgb.value() + 1))
        w.all_done.connect(self._on_pull_all_done)
        w.failure.connect(lambda err: (
            self._pgb.__class__.setVisible(self._pgb, False),
            QMessageBox.critical(self, "Pull 失败", err),
        ))
        w.set_download(dl_items, work_dir, mod_dates=mod_dates)
        self._start_worker(w)

    def _on_pull_all_done(self, n: int) -> None:
        """Pull 全部完成后：更新本地文件信息 + 重新扫描工作区（含新增文件）+ 刷新 PLM 状态。"""
        self._pgb.__class__.setVisible(self._pgb, False)
        self._lbl_status.setText(f"Pull 完成：{n} 个文件，正在重新扫描工作区……")
        self._lbl_speed.setText("")
        # 更新已有条目的 mtime（快速路径，无需 COM）
        for info in self._local_parts:
            if info.part_number in self._pulled_pns and os.path.isfile(info.filepath):
                try:
                    info.mtime = datetime.fromtimestamp(os.path.getmtime(info.filepath))
                except Exception:
                    pass
        # 重新扫描工作区：Pull 可能新增了本地没有的文件（PLM_ONLY → 有本地文件），
        # 只有重新扫描才能让这些新文件出现在差异表中。
        # _on_scan_done 末尾会自动触发 _on_refresh_plm_status，无需在此重复调用。
        self._on_load_workspace()

    # ─────────────────────────────────────────────────────────────────────────
    # 新增 PLM Part（手动）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_add_plm_part(self) -> None:
        """弹窗手动输入零件号，查询 PLM 并递归展开子孙，批量加入本地缓存。

        行为：
        - PLM 中存在该零件 → 递归获取其完整 BOM 子树，把所有节点都加入缓存"""
        if self._is_mypdm_backend():
            QMessageBox.information(self, "myPDM", "myPDM 后端暂不支持此操作，请使用 '🔧 CAD入口' 功能。")
            return
        from PySide6.QtWidgets import QInputDialog
        pn, ok = QInputDialog.getText(self, "新增 PLM Part", "输入零件号（Part Number）：")
        if not ok or not pn.strip():
            return
        pn = pn.strip()

        base_url, login, password, workspace, _be = self._read_conn()
        if not base_url or not login:
            QMessageBox.warning(self, "配置不完整", "请先配置 PLM 连接信息，以便查询该零件。")
            return

        self._lbl_status.setText(f"正在查询 PLM：{pn}……")
        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            c = PlmApiClient(base_url)
            c.login(login, password)

            # ── 1. 确认零件存在（精确路径查询，避免全文搜索漏查）────────────────
            try:
                root_detail = c.get_part_head(workspace, pn)
            except Exception:
                root_detail = None
            if not root_detail:
                QMessageBox.warning(
                    self, "PLM 中不存在",
                    f"在 PLM 工作区中未找到零件号：{pn}\n\n请确认零件号是否正确。",
                )
                self._lbl_status.setText("未找到零件，操作已取消")
                return

            root_ver = str(root_detail.get("version") or "A")

            # ── 2. 询问是否递归展开子孙 ───────────────────────────────────────
            reply = QMessageBox.question(
                self, "新增 PLM Part",
                f"找到零件：{pn}（版本 {root_ver}）\n\n"
                "是否递归展开其所有子孙零件并一并加入缓存？\n"
                "（对于单个零件点「否」，对于装配体点「是」）",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Cancel:
                self._lbl_status.setText("操作已取消")
                return

            # ── 3. 收集所有需要查询的零件号 ─────────────────────────────────
            pns_to_query: list[str] = [pn]

            if reply == QMessageBox.Yes:
                # 递归获取 BOM 子树（仅取零件号，不需要附件）
                self._lbl_status.setText(f"正在递归展开 {pn} 的子孙……")
                try:
                    bom_rows = c.get_part_components_flat(workspace, pn, root_ver)
                    child_pns = [
                        str(r.get("part_number", ""))
                        for r in bom_rows
                        if r.get("part_number") and r.get("depth", 0) > 0
                    ]
                    pns_to_query += child_pns
                    pns_to_query = list(dict.fromkeys(pns_to_query))  # 去重保序
                except Exception as exc:
                    QMessageBox.warning(
                        self, "递归展开失败",
                        f"展开子树时出错（将只添加根零件）：\n{exc}",
                    )

            # ── 4. 批量查询摘要并写入缓存 ─────────────────────────────────────
            self._lbl_status.setText(f"正在查询 {len(pns_to_query)} 个零件的 PLM 详情……")
            self._pgb.setRange(0, len(pns_to_query))
            self._pgb.setValue(0)
            self._pgb.setVisible(True)

            added = 0
            for idx, qpn in enumerate(pns_to_query):
                try:
                    detail = c.get_part_head(workspace, qpn)
                    self._plm_cache[qpn] = c.extract_part_summary(detail)
                    added += 1
                except Exception:
                    pass  # 单个子零件查询失败不中断整体
                self._pgb.setValue(idx + 1)

            self._pgb.setVisible(False)

            # ── 5. 持久化并刷新表格 ───────────────────────────────────────────
            work_dir = self._get_work_dir()
            if work_dir:
                from catia_copilot.plm.workspace_scanner import merge_plm_cache
                # 合并新查询的零件到缓存文件，确保不覆盖现有缓存
                added_parts = {qpn: self._plm_cache[qpn]
                               for qpn in pns_to_query if qpn in self._plm_cache}
                self._plm_cache = merge_plm_cache(work_dir, added_parts)

            self._plm_parts_cache = {k: v for k, v in self._plm_cache.items() if v}
            self._populate_diff_table()
            self._lbl_status.setText(
                f"已添加 {added} 个零件（共查询 {len(pns_to_query)} 个）"
            )

        except Exception as exc:
            self._pgb.setVisible(False)
            self._lbl_status.setText(f"查询失败：{exc}")
            QMessageBox.warning(self, "查询失败", str(exc))

    # ─────────────────────────────────────────────────────────────────────────
    # 兼容旧业务逻辑方法（_on_load_preview / _populate_local_table 等）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_load_preview(self) -> None:
        """兼容旧调用，等价于加载工作区。"""
        self._on_load_workspace()

    def _populate_local_table(self, rows: list) -> None:
        """兼容旧调用，实际由 _populate_diff_table 取代。"""
        pass

    def _update_status_col(self) -> None:
        """兼容旧调用，实际由 _populate_diff_table 取代。"""
        pass

    def _sync_table_row_heights(self) -> None:
        pass

    def _update_arrow_column(self) -> None:
        pass

    def _on_load_bom_progress(self, count: int) -> None:
        pass

    def _on_preview_loaded(self, rows: list) -> None:
        pass

    def _on_preview_fail(self, err: str) -> None:
        self._lbl_status.setText(f"加载失败：{err}")

    def _on_browse_work_dir(self) -> None:
        current = self._le_work_dir.text().strip()
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", current)
        if path:
            self._le_work_dir.setText(path)

    def _on_save_conn(self):
        self._save_conn()
        self._update_conn_status_bar()
        wd = self._get_work_dir()
        self._lbl_work_dir.setText(f"工作区：{wd}" if wd else "工作区：—")

    def _on_test_conn(self):
        base_url, login, password, workspace, backend = self._read_conn()
        if not base_url or not login:
            self._log_to_conn("请先填写服务端地址和用户名。", "warn")
            return
        self._log_to_conn("正在测试连接……")
        w = _ConnectWorker(base_url, login, password, workspace, backend)
        w.success.connect(self._on_conn_ok)
        w.failure.connect(self._on_conn_fail)
        self._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict):
        info_parts = []
        if ws_info.get("id"):
            info_parts.append(f"工作区 ID：{ws_info['id']}")
        if ws_info.get("description"):
            info_parts.append(f"描述：{ws_info['description']}")
        if isinstance(users, list):
            info_parts.append(f"成员数：{len(users)}")
        detail = "  |  ".join(info_parts) or "连接成功"
        self._lbl_ws_detail.setText(detail)
        self._log_to_conn(f"连接成功 ({login_name})", "ok")
        self._update_conn_status_bar()

    def _on_conn_fail(self, err: str):
        self._log_to_conn(f"连接失败：{err}", "error")

    def _build_advanced_options(self) -> QWidget:
        """构建同步选项区域（三区水平排列：radio+预设 | checkbox | 他人签出）。"""
        from PySide6.QtWidgets import QGridLayout

        self._adv_content = QWidget()
        outer = QHBoxLayout(self._adv_content)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(8)

        _lbl_style = "color: palette(mid);"

        def _make_radio_grp(*labels) -> tuple[list[QRadioButton], QButtonGroup]:
            btns = [QRadioButton(lbl) for lbl in labels]
            grp  = QButtonGroup()
            for b in btns: grp.addButton(b)
            btns[0].setChecked(True)
            return btns, grp

        # ── 区1：3 组 radio（不存在/已签入/推送后）+ 预设按钮 ─────────────────
        grid1 = QGridLayout(); grid1.setSpacing(4); grid1.setContentsMargins(0,0,0,0)
        grid1.setColumnStretch(1, 0); grid1.setColumnStretch(2, 0); grid1.setColumnStretch(3, 1)

        (self._rb_create_yes, self._rb_create_no),     self._bg_create = _make_radio_grp("新建",     "跳过")
        self._rb_exist_checkout = QRadioButton("签出更新")
        self._rb_exist_checkout.setChecked(True)
        self._bg_exist = QButtonGroup()
        self._bg_exist.addButton(self._rb_exist_checkout)
        (self._rb_after_checkin, self._rb_after_keep),  self._bg_after  = _make_radio_grp("自动签入", "保留签出")

        radio_rows1 = [
            ("不存在：", self._rb_create_yes,    self._rb_create_no),
            ("已签入：", self._rb_exist_checkout, None),
            ("推送后：", self._rb_after_checkin,  self._rb_after_keep),
        ]
        for r, (lbl_txt, rb1, rb2) in enumerate(radio_rows1):
            lbl = QLabel(lbl_txt); lbl.setStyleSheet(_lbl_style)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid1.addWidget(lbl, r, 0)
            grid1.addWidget(rb1, r, 1)
            if rb2 is not None:
                grid1.addWidget(rb2, r, 2)

        # 预设按钮竖向放在第3列（与 radio 行对齐）
        btn_preset_new    = QPushButton("新建模式")
        btn_preset_update = QPushButton("更新模式")
        btn_preset_new.setToolTip("新建所有不存在的零件，跳过已有零件，不增量")
        btn_preset_update.setToolTip("仅更新已有零件（签出后更新），不新建，开启增量")
        btn_preset_new.clicked.connect(self._apply_preset_new)
        btn_preset_update.clicked.connect(self._apply_preset_update)
        grid1.addWidget(btn_preset_new,    0, 3)
        grid1.addWidget(btn_preset_update, 1, 3)

        zone1 = QWidget(); zone1.setLayout(grid1)
        outer.addWidget(zone1)

        # 竖分隔线
        def _vsep():
            s = QFrame(); s.setFrameShape(QFrame.VLine); s.setFrameShadow(QFrame.Sunken)
            return s
        outer.addWidget(_vsep())

        # ── 区2：6 个 checkbox，每行 2 个，共 3 行 ────────────────────────────
        grid2 = QGridLayout(); grid2.setSpacing(4); grid2.setContentsMargins(8,0,8,0)
        grid2.setColumnStretch(1, 1); grid2.setColumnStretch(3, 1)

        self._chk_incremental     = QCheckBox("增量同步")
        self._chk_reg_product     = QCheckBox("注册顶层产品")
        self._chk_upload_catpart  = QCheckBox("上传 CATIA 文件")
        self._chk_upload_stp      = QCheckBox("上传 STP 文件")
        self._chk_upload_drw_file = QCheckBox("上传图纸文件")
        self._chk_upload_drw_pdf  = QCheckBox("上传 PDF")
        self._chk_reg_product.setEnabled(False)
        self._chk_reg_product.setToolTip(
            "当前不可用：POST /products 接口返回 403。\n"
            "该操作要求的权限级别高于工作区管理员角色，需联系 PLM 供应商确认权限配置。"
        )
        self._chk_incremental.setToolTip("增量同步：跳过属性无变化的零件")
        self._chk_upload_catpart.setToolTip("上传 CATPart / CATProduct 原始文件")
        self._chk_upload_stp.setToolTip("上传 STP 几何文件（PLM 转 OBJ 供三维预览）")
        self._chk_upload_drw_file.setToolTip("上传 CATDrawing 原文件")
        self._chk_upload_drw_pdf.setToolTip("上传图纸 PDF")

        # 每行2个：(行, 列偏移, checkbox)
        chk_layout = [
            (0, 0, self._chk_incremental),
            (0, 1, self._chk_reg_product),
            (1, 0, self._chk_upload_catpart),
            (1, 1, self._chk_upload_stp),
            (2, 0, self._chk_upload_drw_file),
            (2, 1, self._chk_upload_drw_pdf),
        ]
        for r, c, chk in chk_layout:
            grid2.addWidget(chk, r, c)

        zone2 = QWidget(); zone2.setLayout(grid2)
        outer.addWidget(zone2)

        outer.addWidget(_vsep())

        # ── 区3：他人签出选项（未完全实现，标注说明）────────────────────────
        grid3 = QGridLayout(); grid3.setSpacing(4); grid3.setContentsMargins(8,0,0,0)

        (self._rb_other_skip, self._rb_other_force), self._bg_other = _make_radio_grp("跳过", "强制")
        self._rb_other_force.setEnabled(False)
        self._rb_other_force.setToolTip("强制覆盖他人签出（尚未实现）")

        lbl_other = QLabel("他人签出："); lbl_other.setStyleSheet(_lbl_style)
        lbl_other.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid3.addWidget(lbl_other,            0, 0)
        grid3.addWidget(self._rb_other_skip,  0, 1)
        grid3.addWidget(self._rb_other_force, 0, 2)

        zone3 = QWidget(); zone3.setLayout(grid3)
        outer.addWidget(zone3)

        outer.addStretch()

        # 恢复 QSettings 并连接 toggled 信号保存状态
        _sw = QSettings(_S_ORG, _S_WB)
        def _chk_val(key, default=True):
            v = _sw.value(key)
            return default if v is None else str(v).lower() not in ("false", "0")
        def _save_chk(key: str):
            return lambda checked: _sw.setValue(key, checked)

        self._chk_incremental.setChecked(_chk_val("chk_incremental", True))
        self._chk_incremental.toggled.connect(_save_chk("chk_incremental"))
        self._chk_reg_product.setChecked(False)  # 功能未实现，强制不勾选
        self._chk_upload_catpart.setChecked(_chk_val("chk_upload_catpart", True))
        self._chk_upload_catpart.toggled.connect(_save_chk("chk_upload_catpart"))
        self._chk_upload_stp.setChecked(_chk_val("chk_upload_stp", True))
        self._chk_upload_stp.toggled.connect(_save_chk("chk_upload_stp"))
        self._chk_upload_drw_file.setChecked(_chk_val("chk_upload_drw_file", True))
        self._chk_upload_drw_file.toggled.connect(_save_chk("chk_upload_drw_file"))
        self._chk_upload_drw_pdf.setChecked(_chk_val("chk_upload_drw_pdf", True))
        self._chk_upload_drw_pdf.toggled.connect(_save_chk("chk_upload_drw_pdf"))

        return self._adv_content

    # 升级方式枚举（供 _build_sync_options 等使用）
    UPGRADE_SKIP = "不推送"
    UPGRADE_ITER = "+迭代"
    UPGRADE_VER  = "+版本"

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
            _hdr_hist.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            _hdr_hist.resizeSection(i, w)
        _hdr_hist.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._tbl_history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl_history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        base_url, login, password, workspace, _be = self._read_conn()
        s_plm = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = s_plm.value("work_dir", "")

        # ── 连接配置 ──────────────────────────────────────────────────────────
        grp_cfg = QGroupBox("连接配置")
        form = QFormLayout(grp_cfg)
        form.setSpacing(6)

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._le_workspace = QLineEdit(workspace)
        self._le_base_url.setPlaceholderText("http://127.0.0.1:8001/docdoku-plm-server-rest/api")

        # 工作目录（Pull 下载到此处）
        work_dir_row = QHBoxLayout()
        self._le_work_dir = QLineEdit(str(work_dir))
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
        _hdr_tags.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _hdr_tags.resizeSection(0, 180)
        _hdr_tags.setStretchLastSection(True)
        self._tbl_plm_tags.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl_plm_tags.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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
        _hdr_rules.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _hdr_rules.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        _hdr_rules.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        _hdr_rules.resizeSection(0, 160)
        _hdr_rules.resizeSection(2, 60)
        _hdr_rules.setStretchLastSection(False)
        self._tbl_rules.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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

    def _preview_visible_cols(self) -> list[str]:
        s = QSettings(_S_ORG, _S_WB)
        saved = s.value("preview_optional_cols", _PREVIEW_DEFAULT_COLS)
        if isinstance(saved, str):
            saved = [saved]
        saved_list: list = saved if isinstance(saved, list) else []
        all_optional = (
            set(BOM_EDIT_COLUMN_ORDER) | set(PRESET_USER_REF_PROPERTIES)
        ) - set(_PREVIEW_FIXED_COLS)
        optional = [c for c in saved_list if c in all_optional]
        order = BOM_EDIT_COLUMN_ORDER + [
            c for c in PRESET_USER_REF_PROPERTIES if c not in BOM_EDIT_COLUMN_ORDER
        ]
        result: list[str] = []
        # 先添加固定列
        for c in _PREVIEW_FIXED_COLS:
            result.append(c)
        # 再按顺序添加可选列
        for c in order:
            if c in optional and c not in result:
                result.append(c)
        # 最后添加同步列（在固定列之后，可选列之前）
        # 找到插入位置：固定列之后
        insert_pos = len(_PREVIEW_FIXED_COLS)
        for i, col in enumerate(_SYNC_COLS_ORDERED):
            result.insert(insert_pos + i, col)
        return result

    def _save_preview_cols(self, optional_cols: list[str]) -> None:
        QSettings(_S_ORG, _S_WB).setValue("preview_optional_cols", optional_cols)

    def _build_col_visibility_row(self) -> None:
        """构建列可见性控件（不显示在 UI，仅供内部树使用）。"""
        for layout in (self._col_vis_row0, self._col_vis_row1):
            while layout.count():
                item = layout.takeAt(0)
                if item is not None:
                    w = item.widget()
                    if w is not None:
                        w.deleteLater()
        self._col_checkboxes.clear()

        s = QSettings(_S_ORG, _S_WB)
        saved = s.value("preview_optional_cols", _PREVIEW_DEFAULT_COLS)
        if isinstance(saved, str):
            saved = [saved]
        visible_optional: set = set(saved) if isinstance(saved, (list, set)) else set()

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
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

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
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
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
            existing_part_policy=ExistingPartPolicy.CHECKOUT_UPDATE,
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

    def _on_sync_progress(self, msg: str) -> None:
        """解析 sync.py 的结构化日志行，更新状态标签和 _sync_result_map。"""
        import re as _re
        stripped = msg.strip()

        if stripped.replace("-", "").replace(" ", "") == "":
            return

        # 解析上传速度（格式：xx.x KB/s 或 xxx KB/s）
        _speed_m = _re.search(r'(\d+(?:\.\d+)?)\s*(KB|MB)/s', stripped, _re.IGNORECASE)
        if _speed_m:
            self._lbl_upload_speed.setText(f"{_speed_m.group(1)} {_speed_m.group(2)}/s")

        extracted_pn: str | None = None
        is_terminal = False

        if stripped.startswith(">>"):
            inner = stripped[2:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                pn = lbl.split("<")[0].strip()
                self._update_sync_result(pn, reason, "", "")
                extracted_pn = pn
                is_terminal = True
        elif stripped.startswith("[X]"):
            inner = stripped[3:].strip()
            idx = inner.rfind(" | ")
            if idx >= 0:
                reason = inner[:idx].strip()
                lbl    = inner[idx + 3:].strip()
                pn = lbl.split("<")[0].strip()
                self._update_sync_result(pn, reason, "", "")
                extracted_pn = pn
                is_terminal = True
        elif " | " in stripped:
            parts = [p.strip() for p in stripped.split(" | ")]
            if len(parts) >= 4:
                col1 = parts[0]
                col2 = parts[1]
                col3 = parts[2]
                lbl  = parts[-1]
                if col1 not in ("签出来源",):
                    pn = lbl.split("<")[0].strip()
                    extracted_pn = pn
                    if col3:
                        self._update_sync_result(pn, col1, col2, col3)
                        is_terminal = True
                    else:
                        existing = self._sync_result_map.get(pn, ("", "", ""))
                        self._sync_result_map[pn] = (existing[0] or col1, col2, existing[2])
                        self._refresh_sync_cols_in_tree(
                            pn, existing[0] or col1, col2, existing[2],
                        )

        total = getattr(self, "_sync_total_nodes", 0)
        if extracted_pn:
            seen = getattr(self, "_sync_seen_pns", set())
            if extracted_pn not in seen:
                seen.add(extracted_pn)
                self._sync_seen_pns = seen
                self._sync_done_nodes = getattr(self, "_sync_done_nodes", 0) + 1
                self._pgb_sync.setValue(min(self._sync_done_nodes, total))

        done = getattr(self, "_sync_done_nodes", 0)
        if done or is_terminal:
            # 截断过长文本，但避免在 <名称> 中间切断
            _show = stripped[:200]
            _lt = _show.rfind("<")
            if _lt >= 0 and ">" not in _show[_lt:]:
                _show = _show[:_lt]
            self._lbl_sync_status.setText(
                f"正在同步…… ({done} / {total})  {_show}"
            )
        else:
            self._lbl_sync_status.setText(stripped)

    def _update_sync_result(self, pn: str, source: str, update: str, checkin: str) -> None:
        """更新同步结果映射并刷新预览树。

        直接使用 pn 作为键，避免从 lbl 字符串解析。
        """
        self._sync_result_map[pn] = (source, update, checkin)
        self._refresh_sync_cols_in_tree(pn, source, update, checkin)

    def _refresh_sync_cols_in_tree(self, pn: str, source: str, update: str, checkin: str) -> None:
        """在隐藏的预览树中更新同步结果列（供内部追踪使用）。

        使用 pn->item 映射字典实现 O(1) 查找，避免全树遍历。
        """
        vis_cols = self._preview_visible_cols()
        if self._preview_tree.columnCount() != len(vis_cols):
            self._populate_preview_tree(self._bom_rows)
            return

        sync_src_idx = vis_cols.index(_SYNC_COL_SOURCE)  if _SYNC_COL_SOURCE  in vis_cols else -1
        sync_upd_idx = vis_cols.index(_SYNC_COL_UPDATE)  if _SYNC_COL_UPDATE  in vis_cols else -1
        sync_chk_idx = vis_cols.index(_SYNC_COL_CHECKIN) if _SYNC_COL_CHECKIN in vis_cols else -1
        if sync_src_idx < 0 and sync_upd_idx < 0 and sync_chk_idx < 0:
            return

        # 延迟构建 pn->item 映射
        if not hasattr(self, "_preview_pn_item_map") or self._preview_tree.columnCount() != len(vis_cols):
            self._preview_pn_item_map: dict[str, QTreeWidgetItem] = {}
            self._build_preview_pn_map(self._preview_tree.invisibleRootItem(), vis_cols)

        item = self._preview_pn_item_map.get(pn)
        if item is None:
            return
        # 同步进行中若 BOM 被重新加载，C++ 侧对象可能已销毁
        if not shiboken6.isValid(item):
            self._preview_pn_item_map.pop(pn, None)
            return

        color = _sync_row_color(source, update, checkin)
        for col_idx, text in [
            (sync_src_idx, source  or "—"),
            (sync_upd_idx, update  or "—"),
            (sync_chk_idx, checkin or "—"),
        ]:
            if col_idx < 0:
                continue
            item.setText(col_idx, text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if color:
                item.setForeground(col_idx, color)

        for idx in (sync_src_idx, sync_upd_idx, sync_chk_idx):
            if idx >= 0:
                self._preview_tree.resizeColumnToContents(idx)

    def _build_preview_pn_map(self, parent_item: QTreeWidgetItem, vis_cols: list[str]) -> None:
        """递归构建 pn -> QTreeWidgetItem 映射。"""
        pn_col_idx = vis_cols.index("Part Number") if "Part Number" in vis_cols else -1
        if pn_col_idx < 0:
            return
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            pn = child.text(pn_col_idx).strip()
            if pn:
                self._preview_pn_item_map[pn] = child
            self._build_preview_pn_map(child, vis_cols)

    def _on_upload_log(self, pn: str, source: str, update: str, checkin: str = "") -> None:
        self._update_sync_result(pn, source, update, checkin)

    def _on_refresh_tags(self) -> None:
        base_url, login, password, workspace, _be = self._read_conn()
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
        base_url, login, password, workspace, _be = self._read_conn()
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

    def _on_delete_rule(self, btn: QWidget) -> None:
        for row in range(self._tbl_rules.rowCount()):
            if self._tbl_rules.cellWidget(row, 2) is btn:
                _item0 = self._tbl_rules.item(row, 0)
                _item1 = self._tbl_rules.item(row, 1)
                if _item0 is None or _item1 is None:
                    break
                cv = _item0.text()
                pt = _item1.text()
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
                item.setData(Qt.ItemDataRole.UserRole, rec)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if key == "failed" and int(rec.get("failed", 0)) > 0:
                    # 失败数显示为红色（链接色，主题感知）
                    palette = _app_palette()
                    red_color = palette.color(palette.ColorRole.Link) if palette else QColor("#e74c3c")
                    item.setForeground(red_color)
                self._tbl_history.setItem(row, col, item)
        self._tbl_history.resizeColumnsToContents()

    def _on_history_selected(self, current, _prev) -> None:
        if current is None:
            return
        rec = current.data(Qt.ItemDataRole.UserRole)
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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        QSettings(_S_ORG, _S_HISTORY).remove("records")
        self._tbl_history.setRowCount(0)
        self._txt_hist.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # Pull（从 PLM 拉取文件到本地）
    # ─────────────────────────────────────────────────────────────────────────

class _SettingsDialog(QDialog):
    """PLM 工作台设置（连接配置 + 标签规则）。

    支持 plm-unified 和 myPDM 双后端，通过下拉框切换。
    保存后通过 QSettings 持久化，调用方可读取最新配置。
    """

    def __init__(self, workbench: "PlmWorkbench"):
        super().__init__(workbench)
        self._wb = workbench
        self.setWindowTitle("PLM 设置")
        self.setMinimumSize(640, 580)
        self.resize(720, 660)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 后端选择 ────────────────────────────────────────────────────────────
        grp_backend = QGroupBox("后端类型")
        backend_row = QHBoxLayout(grp_backend)
        self._cmb_backend = QComboBox()
        self._cmb_backend.addItem("plm-unified (FastAPI)", _BACKEND_UNIFIED)
        self._cmb_backend.addItem("myPDM (JWT)", _BACKEND_MYPDM)
        self._cmb_backend.currentIndexChanged.connect(self._on_backend_changed)
        backend_row.addWidget(QLabel("选择后端："))
        backend_row.addWidget(self._cmb_backend, stretch=1)
        layout.addWidget(grp_backend)

        # ── 连接配置 ──────────────────────────────────────────────────────────
        grp_cfg = QGroupBox("连接配置")
        self._form_cfg = QFormLayout(grp_cfg)
        self._form_cfg.setSpacing(6)

        # 初始值直接从 QSettings 读取，不依赖 workbench 上的控件引用
        base_url, login, password, workspace, backend = self._wb._read_conn()
        s_plm    = QSettings(_S_ORG, _S_PLM_CFG)
        work_dir = str(s_plm.value("work_dir", ""))

        self._le_base_url  = QLineEdit(base_url)
        self._le_login     = QLineEdit(login)
        self._le_password  = QLineEdit(password)
        self._le_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._le_workspace = QLineEdit(workspace)
        self._le_work_dir  = QLineEdit(work_dir)
        self._le_work_dir.setPlaceholderText("Pull 下载文件保存目录…")

        work_dir_row = QHBoxLayout()
        btn_browse = QPushButton("浏览…"); btn_browse.setFixedWidth(60)
        btn_browse.clicked.connect(self._on_browse)
        work_dir_row.addWidget(self._le_work_dir)
        work_dir_row.addWidget(btn_browse)

        # 后端选择字段标签
        self._lbl_login      = QLabel("用户名：")
        self._lbl_password   = QLabel("密码：")
        self._lbl_workspace  = QLabel("工作区：")
        self._lbl_work_dir   = QLabel("工作目录：")

        form = self._form_cfg
        form.addRow("服务端地址：", self._le_base_url)
        form.addRow(self._lbl_login,    self._le_login)
        form.addRow(self._lbl_password, self._le_password)
        form.addRow(self._lbl_workspace, self._le_workspace)
        form.addRow(self._lbl_work_dir,  work_dir_row)

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
        grp_ws = QGroupBox("连接信息")
        v_ws = QVBoxLayout(grp_ws)
        self._lbl_ws = QLabel(self._wb._lbl_ws_detail.text())
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
        _ht.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _ht.resizeSection(0, 180)
        _ht.setStretchLastSection(True)
        self._tbl_plm_tags.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
        _hr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _hr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        _hr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        _hr.resizeSection(0, 160); _hr.resizeSection(2, 60)
        self._tbl_rules.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
        self._txt_conn_log.setPlainText(self._wb._txt_conn_log.toPlainText())
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

        # 初始化后端选择
        backend = self._wb._read_conn()[4]
        idx = self._cmb_backend.findData(backend)
        if idx >= 0:
            self._cmb_backend.setCurrentIndex(idx)
        self._on_backend_changed()

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作目录",
                                                 self._le_work_dir.text())
        if path:
            self._le_work_dir.setText(path)

    def _on_backend_changed(self) -> None:
        """后端切换时，显示/隐藏工作区和工作目录字段（myPDM 不需要）。"""
        backend = self._cmb_backend.currentData()
        show_ws = backend != _BACKEND_MYPDM
        self._lbl_workspace.setVisible(show_ws)
        self._le_workspace.setVisible(show_ws)
        self._lbl_work_dir.setVisible(show_ws)
        self._le_work_dir.setVisible(show_ws)
        # 设置后端默认地址
        if backend == _BACKEND_MYPDM and not self._le_base_url.text().strip():
            self._le_base_url.setText("https://192.168.1.x:8443/api")
            self._le_base_url.setPlaceholderText("https://192.168.1.x:8443/api")
        elif backend == _BACKEND_UNIFIED and self._le_base_url.text() == "https://192.168.1.x:8443/api":
            self._le_base_url.setText("http://127.0.0.1:8010")
            self._le_base_url.setPlaceholderText("http://127.0.0.1:8010")

    def _on_save(self) -> None:
        """保存配置到 QSettings。"""
        s = QSettings(_S_ORG, _S_PLM_CFG)
        s.setValue("base_url",  self._le_base_url.text().strip())
        s.setValue("login",     self._le_login.text().strip())
        s.setValue("password",  self._le_password.text())
        s.setValue("workspace", self._le_workspace.text().strip())
        s.setValue("work_dir",  self._le_work_dir.text().strip())
        # 保存后端类型
        backend_data = self._cmb_backend.currentData()
        s.setValue("backend", backend_data if backend_data else _BACKEND_UNIFIED)
        # 同步 workbench 的控件引用
        if hasattr(self._wb, "_backend_combo") and self._wb._backend_combo:
            self._wb._backend_combo.setCurrentText(self._cmb_backend.currentText())
        if hasattr(self._wb, "_le_work_dir") and self._wb._le_work_dir:
            self._wb._le_work_dir.setText(self._le_work_dir.text())
        self._log("配置已保存。", "ok")
        self._wb._update_conn_status_bar()

    def _on_test(self) -> None:
        """测试连接。"""
        self._log("正在测试连接……", "info")
        base_url  = self._le_base_url.text().strip()
        login     = self._le_login.text().strip()
        password  = self._le_password.text()
        workspace = self._le_workspace.text().strip()
        backend   = self._cmb_backend.currentData() or _BACKEND_UNIFIED
        if not base_url or not login:
            self._log("请先填写服务端地址和用户名。", "warn")
            return
        w = _ConnectWorker(base_url, login, password, workspace, backend)
        w.success.connect(lambda ln, users, ws_info: self._on_conn_ok(ln, users, ws_info))
        w.failure.connect(lambda err: self._log(f"连接失败：{err}", "error"))
        self._wb._start_worker(w)

    def _on_conn_ok(self, login_name: str, users: list, ws_info: dict) -> None:
        backend = self._cmb_backend.currentData()
        if backend == _BACKEND_MYPDM:
            real_name = ws_info.get("real_name", login_name)
            role = ws_info.get("role", "?")
            role_display = {"admin": "管理员", "engineer": "工程师",
                            "production": "生产", "guest": "访客"}.get(role, role)
            detail = f"用户：{real_name}（{role_display}）  |  部门：{ws_info.get('department', '—')}"
        else:
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

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：历史列表
        left_w = QWidget()
        left_v = QVBoxLayout(left_w)
        left_v.setContentsMargins(0, 0, 0, 0)
        self._tbl = QTableWidget(0, 7)
        self._tbl.setHorizontalHeaderLabels(["时间", "新建", "更新", "跳过", "失败", "用户名", "同步模式"])
        self._tbl.setAlternatingRowColors(True)
        self._tbl.verticalHeader().setDefaultSectionSize(28)
        self._tbl.verticalHeader().setVisible(False)
        _hdr = self._tbl.horizontalHeader()
        _hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _hdr.setStretchLastSection(True)
        for i, w in enumerate([140, 45, 45, 45, 45, 100]):
            _hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            _hdr.resizeSection(i, w)
        _hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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
                dst_item.setData(Qt.ItemDataRole.UserRole, src_item.data(Qt.ItemDataRole.UserRole))

    def _on_selected(self, current, _prev) -> None:
        if not current:
            return
        data = current.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        lines = [
            f"时间：{data.get('time', '')}",
            f"用户：{data.get('username', '—')}",
            f"模式：{data.get('sync_mode', '—')}",
            f"新建：{data.get('created', 0)}",
            f"更新：{data.get('updated', 0)}",
            f"跳过：{data.get('skipped', 0)}",
            f"无变化：{data.get('unchanged', 0)}",
            f"失败：{data.get('failed', 0)}",
        ]
        errors = data.get("errors", [])
        if errors:
            lines.append("")
            lines.append("失败/警告详情：")
            lines += [f"  · {e}" for e in errors]
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


# ─────────────────────────────────────────────────────────────────────────────
# 附件查看对话框
# ─────────────────────────────────────────────────────────────────────────────

class _AttachmentDialog(QDialog):
    """查看并下载某零件迭代的 PLM 附件列表。"""

    _FA_DOWNLOAD = "\uf019"   # FontAwesome 4.7 download icon

    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        workspace: str,
        part_number: str,
        version: str,
        plm_data: dict,
        work_dir: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"PLM 附件 — {part_number} / {version}")
        self.setMinimumSize(580, 380)
        self.resize(660, 450)

        self._base_url  = base_url
        self._login     = login
        self._password  = password
        self._workspace = workspace
        self._pn        = part_number
        self._version   = version
        self._work_dir  = work_dir
        # 从 plm_data 中取迭代号，0 表示最新迭代
        self._iteration = int(plm_data.get("lastIterationNumber") or 0)
        self._files: list[str] = []

        self._build_ui()
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(0, self._load_attachments)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 标题信息行
        info_lbl = QLabel(
            f"零件号：<b>{self._pn}</b>　版本：<b>{self._version}</b>　"
            f"迭代：<b>{self._iteration or '最新'}</b>"
        )
        info_lbl.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info_lbl)

        # 附件列表：文件名列 + 下载图标列
        self._lst = QTableWidget(0, 2)
        self._lst.setHorizontalHeaderLabels(["文件名", ""])
        self._lst.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._lst.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._lst.horizontalHeader().resizeSection(1, 36)
        self._lst.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._lst.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lst.setAlternatingRowColors(True)
        self._lst.verticalHeader().setDefaultSectionSize(28)
        self._lst.verticalHeader().setVisible(False)
        root.addWidget(self._lst, 1)

        # 状态行 + 全部下载图标 + 关闭按钮
        bottom = QHBoxLayout()
        self._lbl_status = QLabel("正在加载……")
        self._lbl_status.setStyleSheet("color: palette(mid);")
        bottom.addWidget(self._lbl_status, 1)

        # 全部下载：FontAwesome download 图标标签
        _fa_f = QFont("FontAwesome"); _fa_f.setPointSize(14)
        self._lbl_dl_all = QLabel(self._FA_DOWNLOAD)
        self._lbl_dl_all.setFont(_fa_f)
        self._lbl_dl_all.setStyleSheet("color: palette(mid);")
        self._lbl_dl_all.setToolTip("全部下载到工作目录")
        self._lbl_dl_all.setCursor(Qt.PointingHandCursor)
        self._lbl_dl_all.mousePressEvent = lambda e: self._on_download_all()
        bottom.addWidget(self._lbl_dl_all)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ── 辅助：构建行内下载图标 widget ─────────────────────────────────────────

    def _make_dl_icon(self, filename: str, sub_type: str) -> QWidget:
        """返回居中放置 FontAwesome download 图标的 QWidget，点击触发下载。"""
        _fa_f = QFont("FontAwesome"); _fa_f.setPointSize(11)
        lbl = QLabel(self._FA_DOWNLOAD)
        lbl.setFont(_fa_f)
        lbl.setStyleSheet("color: #4C566A;")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setToolTip(f"下载 {filename}")
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.mousePressEvent = lambda e, f=filename, s=sub_type: self._on_download_one(f, s)
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        return w

    # ── 加载附件列表 ──────────────────────────────────────────────────────────

    def _load_attachments(self) -> None:
        """查询 PLM 附件列表并填充表格（文件数量少，同步请求即可）。"""
        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            client = PlmApiClient(self._base_url)
            client.login(self._login, self._password)
            self._files = client.list_part_attachments(
                self._workspace, self._pn, self._version, self._iteration
            )
        except Exception as exc:
            self._lbl_status.setText(f"加载失败：{exc}")
            return

        self._lst.setRowCount(0)
        if not self._files:
            self._lbl_status.setText("该版本暂无附件。")
            return

        for fname in self._files:
            row = self._lst.rowCount()
            self._lst.insertRow(row)
            self._lst.setItem(row, 0, QTableWidgetItem(fname))
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            sub_type = "nativecad" if ext in ("stp", "step", "igs", "iges", "obj", "stl") else "attachedfiles"
            self._lst.setCellWidget(row, 1, self._make_dl_icon(fname, sub_type))

        self._lbl_status.setText(f"共 {len(self._files)} 个附件")
        if self._work_dir:
            self._lbl_dl_all.setStyleSheet("color: #4C566A;")
        else:
            self._lbl_dl_all.setToolTip("请先配置工作目录")

    # ── 下载 ─────────────────────────────────────────────────────────────────

    def _resolve_dest(self, filename: str) -> str:
        """主键文件平铺到 work_dir，其余放 work_dir/{pn}/ 子目录。"""
        import os
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("catpart", "catproduct"):
            return os.path.join(self._work_dir, filename)
        return os.path.join(self._work_dir, self._pn, filename)

    def _on_download_one(self, filename: str, sub_type: str) -> None:
        if not self._work_dir:
            QMessageBox.warning(self, "未设置工作目录", "请先在设置中配置本地工作目录。")
            return
        dest = self._resolve_dest(filename)
        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            client = PlmApiClient(self._base_url)
            client.login(self._login, self._password)
            client.download_attached_file(
                self._workspace, self._pn, self._version,
                self._iteration, filename, dest, sub_type=sub_type,
            )
            self._lbl_status.setText(f"已下载：{filename}")
        except Exception as exc:
            QMessageBox.critical(self, "下载失败", str(exc))

    def _on_download_all(self) -> None:
        if not self._work_dir:
            QMessageBox.warning(self, "未设置工作目录", "请先在设置中配置本地工作目录。")
            return
        if not self._files:
            return
        errors = []
        try:
            from catia_copilot.plm.unified_client import UnifiedPlmClient as PlmApiClient
            client = PlmApiClient(self._base_url)
            client.login(self._login, self._password)
        except Exception as exc:
            QMessageBox.critical(self, "连接失败", str(exc))
            return

        self._lbl_dl_all.setStyleSheet("color: palette(mid);")
        for i, fname in enumerate(self._files):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            sub_type = "nativecad" if ext in ("stp", "step", "igs", "iges", "obj", "stl") else "attachedfiles"
            dest = self._resolve_dest(fname)
            self._lbl_status.setText(f"下载中 {i+1}/{len(self._files)}：{fname}")
            QApplication.processEvents()
            try:
                client.download_attached_file(
                    self._workspace, self._pn, self._version,
                    self._iteration, fname, dest, sub_type=sub_type,
                )
            except Exception as exc:
                errors.append(f"{fname}：{exc}")

        self._lbl_dl_all.setStyleSheet("color: #4C566A;")
        if errors:
            QMessageBox.warning(self, "部分下载失败", "\n".join(errors))
            self._lbl_status.setText(f"完成，{len(errors)} 个失败")
        else:
            self._lbl_status.setText(f"全部下载完成，共 {len(self._files)} 个文件")


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
        hdr.setSectionResizeMode(_PC_DEPTH,  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_PC_PN,     QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_PC_VER,    QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_PC_ITER,   QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_PC_COUT,   QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_PC_LOCAL,  QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_PC_FILES,  QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_PC_PULL,   QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(_PC_DEPTH,  40)
        hdr.resizeSection(_PC_PN,     200)
        hdr.resizeSection(_PC_VER,    60)
        hdr.resizeSection(_PC_ITER,   50)
        hdr.resizeSection(_PC_COUT,   100)
        hdr.resizeSection(_PC_LOCAL,  140)
        hdr.resizeSection(_PC_FILES,  200)
        hdr.resizeSection(_PC_PULL,   55)
        self._tbl_bom.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl_bom.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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
        self._lbl_speed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        w.bom_done.connect(self._on_bom_done_or_prequery)
        w.file_progress.connect(self._on_file_progress)
        w.file_done.connect(self._on_file_done)
        w.all_done.connect(self._on_all_done)
        w.failure.connect(self._on_failure)
        return w

    def _on_bom_done_or_prequery(self, result: list) -> None:
        """统一处理 BOM 树展开完成和预查询附件完成。"""
        # 如果是预查询结果（list of (row_idx, files)）
        if result and isinstance(result[0], tuple) and len(result[0]) == 2 and isinstance(result[0][0], int):
            self._on_prequery_done(result)
        else:
            self._on_bom_done(result)

    def _local_files_for(self, part_number: str) -> set[str]:
        """返回 work_dir/{part_number}/ 下已有的文件名集合（小写）。"""
        import os as os
        part_dir = os.path.join(self._work_dir, part_number)
        if not os.path.isdir(part_dir):
            return set()
        return {f.lower() for f in os.listdir(part_dir)
                if os.path.isfile(os.path.join(part_dir, f))}

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
        import os as os
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
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if user_data is not None:
                    it.setData(Qt.ItemDataRole.UserRole, user_data)
                return it

            self._tbl_bom.setItem(row_idx, _PC_DEPTH,  _item(depth_text))
            self._tbl_bom.setItem(row_idx, _PC_PN,     _item(f"{indent}{pn}  {name}".strip(), pn))
            self._tbl_bom.setItem(row_idx, _PC_VER,    _item(ver))
            self._tbl_bom.setItem(row_idx, _PC_ITER,   _item(itr))

            # 签出人：非空则标红（链接色，主题感知）
            cout_item = _item(cout_user)
            if cout_user:
                palette = _app_palette()
                red_color = palette.color(palette.ColorRole.Link) if palette else QColor("#e74c3c")
                cout_item.setForeground(red_color)
            self._tbl_bom.setItem(row_idx, _PC_COUT, cout_item)

            # 本地文件状态：已有标绿，无则灰色（主题感知）
            local_item = _item(local_text)
            palette = _app_palette()
            if local_set:
                green_color = QColor("#27ae60")  # 绿色通用
                local_item.setForeground(green_color)
            else:
                mid_color = palette.color(palette.ColorRole.Mid) if palette else QColor("#7f8c8d")
                local_item.setForeground(mid_color)
            self._tbl_bom.setItem(row_idx, _PC_LOCAL, local_item)

            # 文件列表列（占位，实际下载时动态查询）
            self._tbl_bom.setItem(row_idx, _PC_FILES, _item("（下载时实时查询）"))

            # 下载? checkbox：默认勾选本地没有文件的行
            chk_w = QWidget()
            chk_l = QHBoxLayout(chk_w)
            chk_l.setContentsMargins(0, 0, 0, 0)
            chk_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        """收集勾选行，在后台线程查询附件列表，然后批量下载。"""
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
            pn  = str(pn_item.data(Qt.ItemDataRole.UserRole) or pn_item.text()).strip()
            ver = str((self._tbl_bom.item(i, _PC_VER) or QTableWidgetItem("")).text())
            itr = str((self._tbl_bom.item(i, _PC_ITER) or QTableWidgetItem("")).text())
            if pn:
                checked_rows.append((i, pn, ver, itr))

        if not checked_rows:
            QMessageBox.warning(self, "未选择", "请至少勾选一个零件行。")
            return

        # 使用 Worker 线程预查询附件列表，避免阻塞主线程
        self._btn_download.setEnabled(False)
        self._btn_expand.setEnabled(False)
        self._lbl_status.setText("正在查询各零件附件列表……")

        w = self._make_worker()
        w.set_prequery_attachments(checked_rows)
        self._worker = w
        w.start()

    def _on_prequery_done(self, prequery_results: list) -> None:
        """预查询附件完成，收集下载项并开始下载。"""
        import os as os

        # (part_number, version, iteration, filename) 四元组列表
        dl_items: list[tuple[str, str, str, str]] = []
        # mod_dates：从 _bom_rows 按 pn 取 PLM modificationDate，下载后设置文件 mtime
        mod_dates: dict[str, str] = {
            row.get("part_number", ""): str(row.get("modification_date") or "")
            for row in self._bom_rows
            if row.get("part_number")
        }
        for row_idx, files in prequery_results:
            # 更新表格文件列显示
            files_text = ", ".join(files) if files else "（无附件）"
            file_item = QTableWidgetItem(files_text)
            file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._tbl_bom.setItem(row_idx, _PC_FILES, file_item)
            # 获取该行的 pn, ver, itr
            pn_item = self._tbl_bom.item(row_idx, _PC_PN)
            if not pn_item:
                continue
            pn  = str(pn_item.data(Qt.ItemDataRole.UserRole) or pn_item.text()).strip()
            ver = str((self._tbl_bom.item(row_idx, _PC_VER) or QTableWidgetItem("")).text())
            itr = str((self._tbl_bom.item(row_idx, _PC_ITER) or QTableWidgetItem("")).text())
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

        os.makedirs(self._work_dir, exist_ok=True)
        w = self._make_worker()
        w.set_download(dl_items, self._work_dir, mod_dates=mod_dates)
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
        self._lbl_speed.setText(f"{kb_s/1024:.1f} MB/s" if kb_s >= 1024 else f"{kb_s:.1f} KB/s")

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
            pn = str(pn_item.data(Qt.ItemDataRole.UserRole) or "")
            if not pn:
                continue
            local_set = self._local_files_for(pn)
            local_text = "√ 已有" if local_set else "— 无"
            local_item = QTableWidgetItem(local_text)
            local_item.setFlags(local_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            palette = _app_palette()
            if local_set:
                green_color = QColor("#27ae60")  # 绿色通用
                local_item.setForeground(green_color)
            else:
                mid_color = palette.color(palette.ColorRole.Mid) if palette else QColor("#7f8c8d")
                local_item.setForeground(mid_color)
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
