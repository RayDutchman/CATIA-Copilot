"""
CATIA BOM → DocdokuPLM 同步逻辑。

入口函数：sync_bom_to_plm()
  - 从当前活动 CATIA 文档读取完整产品结构（BOM）
  - 后序深度优先遍历（子节点先于父节点同步）
  - 两阶段执行：
      阶段一：checkout → 属性更新 → 上传附件/STP（所有节点）
      阶段二：批量 checkin（PLM 端已支持先签入再异步转换，无需等待转换完成）
  - 返回 SyncResult（汇总创建数、跳过数、失败数）

注意：所有 CATIA COM 调用必须在主线程中完成，BOM 数据提取后
可在后台线程中执行 PLM 网络请求。本模块 sync_bom_to_plm() 负责
BOM 提取，调用方负责线程调度。
"""

import logging
import os as _os
import pathlib as _pl
import tempfile
import time
import time as _time
import urllib.parse as _up
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path as _Path
from typing import Any

import pythoncom as _pcom
import win32com.client as _win32

from catia_copilot.catia.bom_collect_v3 import (
    CollectConfig,
    MatrixCollectConfig,
    collect_bom_part_masters,
)
from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.catia.conversion import convert_drawing_to_pdf
from catia_copilot.catia.dependencies import find_drawing_for_part
from catia_copilot.catia.document import get_bom_node_type, set_document_properties
from catia_copilot.constants import (
    PLM_BUILTIN_ATTR_COLS,
    PRESET_USER_REF_PROPERTIES,
    SOURCE_TO_DISPLAY,
    BomNodeType,
)
from catia_copilot.plm.api_client import PlmApiError

logger = logging.getLogger(__name__)


# ── 同步选项 ──────────────────────────────────────────────────────────────────

class ExistingPartPolicy(Enum):
    """已存在零件（Checked In 状态）的处理策略。"""
    SKIP            = "skip"             # 跳过，不做任何更新
    CHECKOUT_UPDATE = "checkout_update"  # Checkout 后更新属性（推荐）


class CheckedOutByOtherPolicy(Enum):
    """零件已被他人 Checkout 时的处理策略。"""
    SKIP       = "skip"        # 跳过并记录警告（不计入 failed）
    FORCE_UNDO = "force_undo"  # 尝试撤销他人签出（PLM-07：当前版本实际无效，会降级为 SKIP）


class OwnCheckedOutPolicy(Enum):
    """零件已由当前用户 Checkout（未签入）时的处理策略。"""
    UPDATE = "update"  # 直接更新（利用现有签出）
    SKIP   = "skip"    # 跳过并计入失败统计


class AfterUpdatePolicy(Enum):
    """所有零件上传完毕后的签入策略。"""
    CHECKIN       = "checkin"       # 所有节点更新完毕后批量签入（推荐）
    KEEP_CHECKOUT = "keep_checkout" # 保留签出状态，不执行签入


@dataclass
class SyncOptions:
    """同步策略选项，由 UI 收集后传入 sync_bom_to_plm()。"""
    # 已存在零件（Checked In）的处理方式
    existing_part_policy: ExistingPartPolicy = ExistingPartPolicy.CHECKOUT_UPDATE

    # Workspace 中不存在的零件是否新建
    create_new_parts: bool = True

    # 我自己已 Checkout 但未 Checkin 的零件：是否继续更新？
    own_checked_out_policy: OwnCheckedOutPolicy = OwnCheckedOutPolicy.UPDATE

    # 他人已 Checkout 的零件：强制撤销还是跳过？
    other_checked_out_policy: CheckedOutByOtherPolicy = CheckedOutByOtherPolicy.SKIP

    # ── 新增选项（PLM 工作台扩展） ────────────────────────────────────────────

    # 增量同步：仅同步属性有变化的零件（False=全量强制同步）
    incremental: bool = True

    # ── 上传选项（各项独立，可组合勾选）─────────────────────────────────────

    # 是否将 CATPart / CATProduct 原始文件作为附件上传到 PLM
    upload_catpart_file: bool = False

    # 是否将 CATPart 导出为 STP 几何文件并上传（仅 Part 类型节点；触发 PLM 异步转换）
    upload_step_file: bool = False

    # 是否将对应 CATDrawing 转换为 PDF 后上传
    # 注：CATDrawing 文件定位逻辑待实现（见 docs/PLM_WORKBENCH_PLAN.md TODO-01），
    #     当前若找不到图纸文件则静默跳过，不报错。
    upload_drawing_pdf: bool = False

    # 是否将对应 CATDrawing 原文件作为附件上传
    # 同上，定位逻辑待实现。
    upload_drawing_file: bool = False

    # 同步完成后是否将顶层产品注册为 PLM Product
    register_product: bool = False

    # Tag 自动映射规则：[{"catia_value": "发布", "plm_tag": "已归档"}, ...]
    # 根据零件"设计状态"属性值在 checkin 后自动打标签
    tag_rules: list[dict] = field(default_factory=list)

    # STP 上传后等待 CAD → OBJ 转换完成的超时时间（秒）。
    # PLM 端已支持先签入再异步转换，该字段当前不再使用（保留供历史兼容）。
    conversion_timeout_s: int = 0

    # 所有上传完毕后的签入策略：批量签入 或 保留签出
    after_update_policy: AfterUpdatePolicy = AfterUpdatePolicy.CHECKIN

    # 用户在 UI 中选择要推送的零件及升级方式。
    # key = part_number，value = "+迭代"（当前唯一有效值）
    # 空 dict 表示不过滤（全量同步，向后兼容）
    part_upgrade_map: dict = field(default_factory=dict)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class BomNode:
    """BOM 树中的一个节点（对应 CATIA 零件或组件）。"""
    part_number: str
    # 所有属性统一存入 attrs，键名与 CATIA 列名一致：
    #   内置属性使用英文列名（Nomenclature / Definition / Revision / Source / Description）
    #   自定义属性使用 UserRefProperty 键名（中文，如"材料"/"重量"等）
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["BomNode"] = field(default_factory=list)
    _catia_ref: Any = field(default=None, repr=False)
    # 本地文件完整路径（来自 bom_collect 的 _filepath 键），用于附件上传
    filepath: str = field(default="", repr=False)
    # 文件类型（来自 row["Type"]）：BomNodeType.PART / PRODUCT / COMPONENT / ""
    filetype: str = field(default="", repr=False)
    # 该节点在其父节点坐标系中的各实例局部变换矩阵列表（行主序 4×4，平移单位 mm）。
    # 列表长度 = 该零件在父节点中的实例数（qty）。None 表示对应实例位置读取失败。
    # 根节点此字段为空列表。
    instances: list = field(default_factory=list, repr=False)


@dataclass
class CheckinTicket:
    """阶段一（上传）完成后，记录待签入零件的必要信息。

    由 _do_update_and_upload() 返回，汇总后在阶段二统一等待转换、批量 checkin。
    """
    part_number: str
    version: str
    iteration: int
    lbl: str           # 日志标识，格式 "pn<nom>" 或 "pn"
    source: str        # 签出来源，用于最终日志的 col1
    update_col: str    # 属性更新结果，用于最终日志的 col2（"属性已写入" / "✗ 更新失败"）
    upload_col: str    # 附件/STP 上传结果，用于状态显示（"STP 已上传" / "附件已上传" / ""）
    needs_conversion: bool  # 是否上传了 STP、需要等待转换
    node: BomNode      # 保留节点引用，用于 tag 映射
    update_ok: bool    # 属性更新是否成功，用于统计修正


@dataclass
class SyncResult:
    """同步操作汇总结果。"""
    created: int = 0
    updated: int = 0        # 已存在，成功更新
    skipped: int = 0        # 按策略跳过（不计入失败）
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    # ── 扩展字段 ────────────────────────────────────────────────────────────
    unchanged: int = 0           # 增量判断：属性无变化，主动跳过
    step_uploaded: int = 0       # 成功上传 STEP 文件的零件数
    product_registered: bool = False  # 顶层产品是否成功注册为 PLM Product

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed + self.unchanged

    def summary(self) -> str:
        lines = [
            f"同步完成：共 {self.total} 个节点",
            f"  ✓ 新建：{self.created}",
            f"  ✓ 已更新：{self.updated}",
            f"  → 已跳过：{self.skipped}",
        ]
        if self.unchanged:
            lines.append(f"  → 无变化：{self.unchanged}")
        lines.append(f"  ✗ 失败：{self.failed}")
        if self.step_uploaded:
            lines.append(f"  ↑ STEP 已上传：{self.step_uploaded}")
        if self.product_registered:
            lines.append("  ★ 产品已注册")
        if self.errors:
            lines.append("\n失败 / 警告详情：")
            for e in self.errors[:10]:
                lines.append(f"  · {e}")
            if len(self.errors) > 10:
                lines.append(f"  … 共 {len(self.errors)} 条")
        return "\n".join(lines)


# ── 结构化同步事件（供 UI 进度解析使用，替代脆弱的文本解析） ────────────────────

@dataclass
class SyncEvent:
    """结构化同步进度事件。

    type: 事件类型
      - "header": 表头
      - "node_start": 开始处理节点
      - "node_progress": 节点处理中（上传进度、转换进度）
      - "node_done": 节点处理完成（终态）
      - "node_skip": 节点跳过
      - "node_fail": 节点失败
      - "summary": 同步汇总
    part_number: 零件编号
    source: 签出来源（如 "新建"、"签出"、"已签出-本人"、"覆盖他人签出"）
    update: 更新结果（如 "属性已写入"、"STP 已上传"、"✗ 更新失败"）
    checkin: 签入状态（如 "已签入"、"保留签出"、""）
    message: 原始日志消息（兼容旧代码）
    speed_kbps: 上传速度 KB/s（可选）
    """
    type: str
    part_number: str = ""
    source: str = ""
    update: str = ""
    checkin: str = ""
    message: str = ""
    speed_kbps: float | None = None


def _emit_text(cb, event: SyncEvent) -> None:
    """将结构化事件转换为文本日志（兼容旧回调）。"""
    if event.type == "header":
        cb(_log_header())
    elif event.type == "node_start":
        cb(_log_row(event.source, "", "", event.part_number))
    elif event.type == "node_progress":
        if event.speed_kbps is not None:
            cb(f"{event.message}  {event.speed_kbps:.1f} KB/s")
        else:
            cb(event.message)
    elif event.type == "node_done":
        cb(_log_row(event.source, event.update, event.checkin, event.part_number))
    elif event.type == "node_skip":
        cb(_log_skip(event.source, event.part_number))
    elif event.type == "node_fail":
        cb(_log_fail(event.source, event.part_number))
    elif event.type == "summary":
        cb(event.message)
    else:
        cb(event.message)


def _makecb(textcb, structcb):
    """创建同时支持文本和结构化回调的包装器。

    返回的 cb 接受 str 或 SyncEvent：
    - 传入 str 时自动包装为 SyncEvent(type="summary", message=str)，
      保持向后兼容（调用方无需改动）。
    - 传入 SyncEvent 时直接分发，供结构化消费方使用。
    """
    def cb(event: "str | SyncEvent") -> None:  # type: ignore[misc]
        if isinstance(event, str):
            event = SyncEvent(type="summary", message=event)
        if textcb:
            _emit_text(textcb, event)
        if structcb:
            structcb(event)
    return cb


# ── BOM 提取（CATIA COM，须在主线程调用） ──────────────────────────────────────

# 从 constants 引入，避免重复定义：
#   PLM_BUILTIN_ATTR_COLS  — DocdokuPLM 内置属性列（Nomenclature/Definition/Revision/Source/Description）
#   PRESET_USER_REF_PROPERTIES — 用户自定义属性列（零件类型/设计状态/材料/重量…）
_BUILTIN_ATTR_COLS = PLM_BUILTIN_ATTR_COLS
_CUSTOM_COLS       = PRESET_USER_REF_PROPERTIES
_ALL_ATTR_COLS: list[str] = _BUILTIN_ATTR_COLS + _CUSTOM_COLS

# PLM instanceAttributes 时跳过的列
_STRUCTURAL_COLS: frozenset[str] = frozenset({
    "Level", "Type", "Filename", "Filepath", "Part Number", "Quantity",
    "Nomenclature",    # 已作为零件名称（name 字段）写入
    "PLM_Version",     # PLM 对齐属性，由 PLM 系统管理，签入后写回 CATIA，不上传到 PLM
    "PLM_Iteration",   # PLM 对齐属性，由 PLM 系统管理，签入后写回 CATIA，不上传到 PLM
})

# 网络错误重试参数
_RETRY_MAX:    int  = 2
_RETRY_DELAYS: list = [1, 3]


# ── 位置读取辅助（PLM 同步专用，平移单位保留 mm）──────────────────────────────

def _sync_position_to_mat4(product) -> list[list[float]]:
    """从 CATIA Product 对象读取局部变换矩阵，平移单位保留 mm（不转换为 m）。

    CATIA Position.GetComponents 列主序 12 元素：
      arr[0..2]=X轴向, arr[3..5]=Y轴向, arr[6..8]=Z轴向, arr[9..11]=平移(mm)
    组装为行主序 4×4 矩阵后返回。
    若读取失败则返回单位矩阵。
    """
    try:
        arr = list(product.Position.GetComponents())
        if arr and len(arr) >= 12:
            return [
                [arr[0], arr[3], arr[6], arr[9] ],
                [arr[1], arr[4], arr[7], arr[10]],
                [arr[2], arr[5], arr[8], arr[11]],
                [0.0,    0.0,    0.0,    1.0    ],
            ]
    except Exception:
        pass
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def collect_bom_for_sync(progress_callback=None) -> list[dict] | None:
    """遍历当前活动 CATIA 文档，返回逐实例行列表（不去重）。

    与 bom_collect.collect_bom_rows() 的区别：
      - 同一 pn 在同一父节点下有多个实例时，每个实例单独输出一行
      - 每行包含 _local_mat4（4×4 列表，平移单位 mm）表示该实例相对父节点的局部变换
      - 不折叠 Quantity，不共享行（Quantity 字段始终为 1）

    返回 None 表示 CATIA 连接失败；返回空列表表示文档无产品结构。
    """
    def cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

    try:
        app = get_catia_v5_application()
    except Exception as exc:
        logger.error(f"collect_bom_for_sync: 无法连接 CATIA: {exc}")
        return None

    try:
        root_product = app.ActiveDocument.Product
    except Exception as exc:
        logger.error(f"collect_bom_for_sync: 无活动文档或文档无 Product: {exc}")
        return None

    rows: list[dict] = []

    def _traverse(product, level: int, parent_filepath: str) -> None:
        """递归遍历，每个实例输出一行（不去重）。"""
        # ── Part Number ──────────────────────────────────────────────────────
        try:
            pn = product.PartNumber
        except Exception:
            try:
                pn = product.ReferenceProduct.PartNumber
            except Exception:
                n  = getattr(product, "Name", "UNKNOWN")
                pn = n.rsplit(".", 1)[0] if "." in n else n

        # ── 文件路径 ─────────────────────────────────────────────────────────
        try:
            filepath = product.ReferenceProduct.Parent.FullName
        except Exception:
            filepath = ""

        # ── 节点类型 ─────────────────────────────────────────────────────────
        node_type = get_bom_node_type(product, parent_filepath, filepath=filepath)

        # ── 属性（Nomenclature / Revision / Source 等）───────────────────────
        nomenclature = ""
        revision     = ""
        source       = ""
        try:
            nomenclature = str(product.GetItem("Nomenclature") or "")
        except Exception:
            pass
        try:
            revision = str(product.GetItem("Revision") or "")
        except Exception:
            pass
        try:
            source = str(product.GetItem("Source") or "")
        except Exception:
            pass

        # ── 文件名 ───────────────────────────────────────────────────────────
        if filepath:
            filename = _Path(filepath).name
        else:
            filename = ""

        # ── 局部变换矩阵（相对父节点，平移单位 mm）───────────────────────────
        local_mat4 = _sync_position_to_mat4(product)

        row: dict = {
            "Level":        level,
            "Type":         node_type,
            "Part Number":  pn,
            "Filename":     filename,
            "Filepath":     filepath,
            "Quantity":     1,
            "Nomenclature": nomenclature,
            "Revision":     revision,
            "Source":       source,
            "_filepath":    filepath,
            "_local_mat4":  local_mat4,
        }
        rows.append(row)
        cb(f"  已读取 {len(rows)} 个实例……")

        # ── 递归子节点（每个实例单独递归，不去重）───────────────────────────
        try:
            count = product.Products.Count
            for i in range(1, count + 1):
                try:
                    child = product.Products.Item(i)
                    _traverse(child, level + 1, parent_filepath=filepath)
                except Exception as e:
                    logger.debug(f"遍历子节点 {i} 失败: {e}")
        except Exception:
            pass

    _traverse(root_product, level=0, parent_filepath="")
    return rows


def extract_bom(progress_callback=None) -> BomNode | None:
    """从当前活动 CATIA 文档提取 BOM 树（PLM 同步专用，须在主线程调用）。

    使用 collect_bom_for_sync() 逐实例遍历（不去重），
    _rows_to_bom_tree() 在构建树时按 pn 聚合同级同 pn 的多个实例，
    使 BomNode.instances 包含所有实例的局部变换矩阵。
    """
    def cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

    cb("正在读取 BOM……")

    rows = collect_bom_for_sync(progress_callback=progress_callback)

    if rows is None:
        cb("BOM 提取失败：无法连接 CATIA 或无活动文档")
        return None

    if not rows:
        logger.warning("BOM 为空，无活动文档或文档无产品结构")
        return None

    cb(f"BOM 读取完成，共 {len(rows)} 个实例行，正在构建树……")
    return _rows_to_bom_tree(rows)


def extract_bom_v3(progress_callback=None, file_path: str | None = None,
                   depth: int = -1) -> "BomNode | None":
    """从指定文件（或当前活动文档）提取 BOM 树（v3 路径，含位置信息）。

    Args:
        progress_callback: 文本进度回调 fn(msg: str)
        file_path: 要提取的 CATIA 文件完整路径（CATPart / CATProduct）。
                   None 表示使用当前活动文档（原有行为）。
        depth:     BomNode 树的最大层数。-1 表示完整递归（默认）；
                   1 表示只构建根节点 + 直接子节点（子节点 children 为空），
                   用于按文件主键 sync 时避免重复处理下级文件。

    使用 collect_bom_part_masters（bom_collect_v3）遍历产品树，
    CollectConfig(placement=MatrixCollectConfig(enabled=True)) 确保每个子实例
    的局部变换矩阵（4×4 行主序，mm）被读取并存入 inst_info["placement"]。

    随后通过 _part_masters_to_bom_tree 将 part_masters 字典树转为 BomNode 树，
    同一父节点下相同 pm_key 的多个实例的 placement 矩阵聚合到同一 BomNode.instances。

    须在主线程或已 CoInitialize 的后台线程中调用（win32com + pycatia 均需要）。
    返回 None 表示 CATIA 连接失败、文件不存在或产品结构为空。
    """
    def cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

    label = _os.path.basename(file_path) if file_path else "活动文档"
    cb(f"正在读取 BOM（v3，含位置信息）：{label}……")

    cols        = list(dict.fromkeys(_ALL_ATTR_COLS))   # 去重保序
    custom_cols = _CUSTOM_COLS

    v3_config = CollectConfig(
        placement=MatrixCollectConfig(enabled=True),
    )

    try:
        root_pm_key, part_masters, _ = collect_bom_part_masters(
            file_path,      # None 使用活动文档，非 None 按路径打开
            cols,
            custom_cols,
            progress_callback=lambda n: cb(f"  已读取 {n} 个节点……"),
            config=v3_config,
            max_depth=depth,   # 透传深度限制，避免 ApplyWorkMode 触发轻量化子节点加载
        )
    except Exception as exc:
        cb(f"BOM 提取失败：{exc}")
        logger.error("extract_bom_v3 失败（%s）：%s", label, exc)
        return None

    n_pm = len(part_masters)
    if n_pm == 0:
        logger.warning("extract_bom_v3: part_masters 为空（%s）", label)
        return None

    cb(f"BOM 读取完成，共 {n_pm} 个 PartMaster，正在构建同步树……")
    result = _part_masters_to_bom_tree(root_pm_key, part_masters, max_depth=depth)
    if result is None:
        cb("BOM 树构建失败（根节点未找到）")
    return result


def _part_masters_to_bom_tree(
    root_pm_key: str,
    part_masters: dict[str, dict],
    max_depth: int = -1,
) -> "BomNode | None":
    """将 bom_collect_v3 的 part_masters 字典树转换为 BomNode 树。

    遍历规则：
    - 递归从 root_pm_key 出发，深度优先构建子树。
    - part_master["instances"] 是文件视角的直接子实例列表（唯一一份，所有引用共享）。
      同一 parent_pm 下，相同 child_pm_key 的多个 inst_info 聚合为一个 BomNode：
        - 按 instances 中首次出现的顺序保持 CATIA 树顺序
        - 每个 inst_info["placement"] 依次追加到 BomNode.instances（None 跳过）
    - ancestors 集合防止 Component 循环引用导致无限递归。

    max_depth:
        -1（默认）：完整递归，构建完整子树。
        N >= 0：只递归到第 N 层（根节点为第 0 层）。子节点在 max_depth 层时
                仍建立 BomNode（含 attrs/filepath/instances），但其 children 为空。
                用于按文件主键 sync 时，每个文件只需一层子节点（placement 信息）。

    attrs 映射：
    - 遍历 _ALL_ATTR_COLS（PLM_BUILTIN_ATTR_COLS + PRESET_USER_REF_PROPERTIES）
    - Source 字段：part_master["source"] 存原始值 "0"/"1"/"2"，经 SOURCE_TO_DISPLAY 转换后写入
    - 空字符串不写入 attrs（与 _rows_to_bom_tree 行为一致）
    """
    if root_pm_key not in part_masters:
        logger.error("_part_masters_to_bom_tree: root_pm_key=%r 不在 part_masters 中", root_pm_key)
        return None

    def _walk(pm_key: str, ancestors: frozenset, cur_depth: int) -> "BomNode":
        pm   = part_masters[pm_key]
        node = BomNode(part_number=pm.get("part_number", pm_key))
        node.filepath = pm.get("filepath", "")
        node.filetype = pm.get("type", "")

        # attrs：遍历 PLM 需要的所有属性列
        for col in _ALL_ATTR_COLS:
            # part_master 内键名通过 _COL_TO_PM_KEY 映射（标准列），自定义列直接用列名
            pm_key_for_col = _COL_TO_PM_KEY_SYNC.get(col, col)
            val = str(pm.get(pm_key_for_col, "") or "").strip()
            if col == "Source":
                val = SOURCE_TO_DISPLAY.get(val, val)
            if val:
                node.attrs[col] = val

        # max_depth 限制：已到最大深度时不再向下递归子节点
        if max_depth != -1 and cur_depth >= max_depth:
            return node

        # 子节点：按 child_pm_key 分组，聚合 placement，保持首次出现顺序
        seen_child_keys: dict[str, BomNode] = {}   # child_pm_key → 已建 BomNode
        new_ancestors = ancestors | {pm_key}

        for inst_info in pm.get("instances", []):
            child_pm_key = inst_info["pm_key"]

            if child_pm_key in seen_child_keys:
                # 同一 child_pm_key 的第 2..N 个实例：只追加 placement
                child_node = seen_child_keys[child_pm_key]
            else:
                # 首次遇到：递归构建子节点（防循环）
                if child_pm_key in new_ancestors:
                    logger.warning(
                        "_part_masters_to_bom_tree: 检测到循环引用 %r，跳过", child_pm_key
                    )
                    continue
                if child_pm_key not in part_masters:
                    logger.warning(
                        "_part_masters_to_bom_tree: child_pm_key=%r 不在 part_masters 中，跳过",
                        child_pm_key,
                    )
                    continue
                child_node = _walk(child_pm_key, new_ancestors, cur_depth + 1)
                seen_child_keys[child_pm_key] = child_node
                node.children.append(child_node)

            # 追加该实例的局部变换矩阵（None 跳过，表示未启用 placement 收集）
            placement = inst_info.get("placement")
            if placement is not None:
                child_node.instances.append(placement)

        return node

    return _walk(root_pm_key, frozenset(), 0)


# part_master dict key → BOM 列名的反向映射（仅 _part_masters_to_bom_tree 内部使用）
_COL_TO_PM_KEY_SYNC: dict[str, str] = {
    "Nomenclature": "nomenclature",
    "Revision":     "revision",
    "Definition":   "definition",
    "Source":       "source",
    "Description":  "description",
}


def _rows_to_bom_tree(rows: list[dict]) -> BomNode | None:
    """将平面层级行列表转换为 BomNode 树。

    支持两种输入格式：
      1. collect_bom_rows() 输出（去重，含 Quantity）：用于 BOM 预览展示。
         此格式不含位置信息，instances 列表为空。
      2. collect_bom_for_sync() 输出（不去重，含 _local_mat4）：用于 PLM 同步。
         此格式每行代表一个实例，构建树时按 pn 聚合同级同 pn 的多个实例，
         将各实例的 local_mat4 合并到父节点对应 child entry 的 instances 列表。
    """
    if not rows:
        return None

    root: BomNode | None = None
    # stack[i] 存储 level i 的当前节点
    stack: list[BomNode] = []
    # 去重表：(level, parent_pn, child_pn) → BomNode
    # 同级同父下相同 pn 的多个实例行聚合为同一个 BomNode，instances 追加
    _seen: dict[tuple, BomNode] = {}

    for row in rows:
        level = int(row.get("Level", 0))
        pn    = str(row.get("Part Number") or "").strip()
        if not pn:
            pn = str(row.get("Filename") or "UNKNOWN").strip()

        local_mat4 = row.get("_local_mat4")  # collect_bom_for_sync 格式才有此字段

        parent_pn = stack[level - 1].part_number if level > 0 and len(stack) >= level else ""
        dedup_key = (level, parent_pn, pn)

        if dedup_key in _seen:
            # 同级同父已有此 pn 的节点 → 追加实例位置，不新建节点
            existing = _seen[dedup_key]
            existing.instances.append(local_mat4)
            # stack 更新为该节点，以便其子节点能正确挂载
            while len(stack) > level:
                stack.pop()
            if len(stack) == level:
                stack.append(existing)
            else:
                stack[level] = existing
            continue

        node = BomNode(part_number=pn)
        node.filepath = str(row.get("_filepath") or "").strip()
        node.filetype = str(row.get("Type") or "").strip()
        # 首个实例的 local_mat4（后续同 pn 实例通过 dedup_key 追加）
        if local_mat4 is not None:
            node.instances = [local_mat4]
        for col in _ALL_ATTR_COLS:
            val = str(row.get(col) or "").strip()
            if col == "Source":
                val = SOURCE_TO_DISPLAY.get(val, val)
            if val:
                node.attrs[col] = val

        _seen[dedup_key] = node

        if level == 0:
            root  = node
            stack = [node]
        else:
            while len(stack) > level:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            stack.append(node)

    return root


# ── 日志辅助 ──────────────────────────────────────────────────────────────────


def _dw(s: str) -> int:
    """返回字符串的终端显示宽度（ASCII=1，CJK=2）。"""
    w = 0
    for ch in s:
        cp = ord(ch)
        if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0x303E or
                0x3040 <= cp <= 0x33FF or 0x3400 <= cp <= 0x4DBF or
                0x4E00 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7FF or
                0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE6F or
                0xFF01 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6):
            w += 2
        else:
            w += 1
    return w


def _ljust(s: str, width: int) -> str:
    """按显示宽度右填充空格（正确处理中文）。"""
    return s + " " * max(width - _dw(s), 0)


# 各列显示宽度（= 最宽内容显示宽 + 1 间距）
_W1 = 13   # 列1：最宽"覆盖他人签出"dw=12，+1
_W2 = 11   # 列2：最宽"属性已写入"dw=10，+1
_W3 = 9    # 列3：最宽"保留签出"dw=8，+1


def _lbl(part_number: str, name: str | None) -> str:
    """有名称时返回 '编号<名称>'，无名称时只返回编号。"""
    n = (name or "").strip()
    if n:
        return f"{part_number}<{n}>"
    return part_number


def _log_row(col1: str, col2: str, col3: str, lbl: str) -> str:
    """三列 + 零件标识，列间用 ' | ' 分隔，按显示宽度对齐。

    col3 为空字符串表示中间过程行（附件上传进度、转换进度等），
    UI 解析时据此区分"终态行"与"过程行"，避免多次触发 node_done 计数。
    """
    return f"  {_ljust(col1,_W1)} | {_ljust(col2,_W2)} | {_ljust(col3,_W3)} | {lbl}"


# 跳过/失败行前缀均为 4 个 ASCII 字符（">>  " / "[X] "）
_W_REASON = _W1 + _W2 + _W3 + 2


def _log_skip(reason: str, lbl: str) -> str:
    """跳过行。"""
    return f"  >>  {_ljust(reason, _W_REASON)} | {lbl}"


def _log_fail(reason: str, lbl: str) -> str:
    """失败行。"""
    return f"  [X] {_ljust(reason, _W_REASON)} | {lbl}"


def _log_header() -> str:
    """返回日志表头和分隔线（两行，用 \\n 连接）。"""
    h1, h2, h3, h4 = "签出来源", "更新结果", "签入状态", "零件标识"
    header = f"  {_ljust(h1,_W1)} | {_ljust(h2,_W2)} | {_ljust(h3,_W3)} | {h4}"
    sep_w  = 2 + _W1 + 3 + _W2 + 3 + _W3 + 3 + 4
    sep    = "  " + "-" * (sep_w - 2)
    return f"{header}\n{sep}"


# ── PLM 同步（可在后台线程调用） ──────────────────────────────────────────────

def sync_bom_to_plm(
    bom_root: BomNode,
    client,
    workspace: str,
    options: SyncOptions | None = None,
    upload_step: bool = False,
    progress_callback=None,
    progress_callback_structured=None,
    shared_uploaded_pns: "dict[str, tuple] | None" = None,
) -> SyncResult:
    """将 BOM 树同步到 DocdokuPLM（不涉及 CATIA COM，可在后台线程执行）。

    两阶段执行：
      阶段一：遍历所有节点，执行 checkout → 属性更新 → 上传附件/STP，
              收集待签入票据（CheckinTicket）列表。
      阶段二：对上传了 STP 的零件轮询等待转换完成，再对所有票据批量 checkin。

    upload_step 参数保留兼容旧调用方，新调用方通过 options.upload_step_file 控制。

    progress_callback: 旧式文本回调 fn(msg: str)
    progress_callback_structured: 新式结构化回调 fn(event: SyncEvent)
    shared_uploaded_pns: 跨文件共享的已处理 pn 去重表 dict[pn -> (pn, version)]。
                         按文件主键逐个调用本函数时传入，避免同一 pn 被重复 sync。
                         None 表示单次调用（原有行为），内部创建局部去重表。
    """
    if options is None:
        options = SyncOptions()

    # 兼容旧 upload_step 位置参数
    if upload_step:
        options = SyncOptions(
            existing_part_policy=options.existing_part_policy,
            create_new_parts=options.create_new_parts,
            own_checked_out_policy=options.own_checked_out_policy,
            other_checked_out_policy=options.other_checked_out_policy,
            incremental=options.incremental,
            upload_step_file=True,
            register_product=options.register_product,
            tag_rules=options.tag_rules,
        )

    result = SyncResult()

    # 创建同时支持文本和结构化的回调包装器
    cb = _makecb(progress_callback, progress_callback_structured)

    # ── 前置校验：BOM 中不允许存在"部件"节点 ──────────────────────────────────
    def _find_components(node: BomNode) -> list[str]:
        """递归收集所有 filetype == COMPONENT 节点的 part_number。"""
        found = []
        if node.filetype == BomNodeType.COMPONENT:
            found.append(node.part_number)
        for child in node.children:
            found.extend(_find_components(child))
        return found

    component_pns = _find_components(bom_root)
    if component_pns:
        names = "、".join(component_pns[:5])
        if len(component_pns) > 5:
            names += f" 等共 {len(component_pns)} 个"
        msg = (
            f"BOM 中包含\u201c部件\u201d节点（{names}），无法同步。\n\n"
            "部件是 CATIA 的嵌入式子装配，没有独立文件，不对应 PLM 零件实体。\n"
            "请在 CATIA 中将其转换为独立产品（CATProduct）后重新读取 BOM。"
        )
        result.errors.append(msg)
        cb(f"✗ 同步中止：BOM 包含部件节点 — {names}")
        return result

    # 确保模板存在（失败不阻断同步）
    tpl_id: str | None = None
    try:
        tpl_id = client.ensure_part_template(workspace)
    except PlmApiError as exc:
        logger.warning(f"模板初始化失败（将以无模板方式继续）：{exc}")
        cb(f"警告：模板初始化失败，将以无模板方式继续 — {exc}")

    # ── 增量同步：预加载工作区全量零件，建立属性缓存 ────────────────────────
    plm_parts_cache: dict[str, dict] = {}
    if options.incremental:
        cb("正在拉取工作区零件列表（增量判断）……")
        try:
            raw_parts = client.list_parts(workspace)
            cb(f"工作区共 {len(raw_parts)} 个零件，逐一拉取详情中……")
            for idx, p in enumerate(raw_parts, 1):
                pn = p.get("number") or p.get("partNumber") or ""
                if not pn:
                    continue
                # list_parts 端点不返回 partIterations 完整数据，
                # 改用 get_part_head() 逐个获取完整对象（含属性），
                # 确保增量属性比较有效，避免退化成全量更新。
                try:
                    detail = client.get_part_head(workspace, pn)
                except PlmApiError:
                    # 单个零件拉取失败时跳过，不影响其他零件
                    logger.warning(f"增量缓存：拉取 {pn} 详情失败，跳过")
                    continue
                ver      = detail.get("version", "A")
                last_iter = (detail.get("partIterations") or [{}])[-1]
                raw_attrs = last_iter.get("instanceAttributes") or []
                attrs: dict[str, str] = {}
                for a in raw_attrs:
                    name = a.get("name") or a.get("attributeName") or ""
                    val  = str(a.get("value") or "").strip()
                    if name:
                        attrs[name] = val
                for builtin_key in ("name", "description"):
                    bval = str(detail.get(builtin_key) or "").strip()
                    if bval:
                        attrs[f"__builtin_{builtin_key}"] = bval
                plm_parts_cache[pn] = {"version": ver, "attrs": attrs}
                if idx % 50 == 0:
                    cb(f"  已缓存 {idx}/{len(raw_parts)} 个零件……")
            cb(f"已缓存 {len(plm_parts_cache)} 个已有零件（增量模式）")
        except PlmApiError as exc:
            logger.warning(f"增量缓存拉取失败，将退化为全量同步：{exc}")
            cb(f"警告：增量缓存拉取失败，退化为全量同步 — {exc}")

    # ════════════════════════════════════════════════════════════════════
    # 阶段一：checkout + update + 上传，收集 CheckinTicket
    # ════════════════════════════════════════════════════════════════════

    tickets: list[CheckinTicket] = []
    try:
        _sync_node(
            bom_root, client, workspace, tpl_id, options, result, cb,
            plm_parts_cache=plm_parts_cache,
            tickets=tickets,
            uploaded_pns=shared_uploaded_pns if shared_uploaded_pns is not None else {},
        )
    except Exception as exc:
        # 阶段一中途抛异常：记录错误，但必须进入阶段二对已生成的 tickets 执行 checkin，
        # 避免已签出的零件因异常跳过 checkin 而永久残留签出状态。
        logger.error("sync_bom_to_plm 阶段一异常（将尝试对已完成票据执行 checkin）：%s", exc)
        result.errors.append(f"阶段一异常（{exc}）— 已尝试对已完成票据签入")

    # ════════════════════════════════════════════════════════════════════
    # 阶段二：批量 checkin（PLM 端已支持先签入再异步转换，无需等待）
    # ════════════════════════════════════════════════════════════════════
    keep_checkout = (
        getattr(options, "after_update_policy", AfterUpdatePolicy.CHECKIN)
        == AfterUpdatePolicy.KEEP_CHECKOUT
    )
    if tickets:
        if keep_checkout:
            # 保留签出：不执行 checkin，输出终态日志行并写回 CATIA 属性
            cb(f"── 保留签出（{len(tickets)} 个零件，不执行签入）──")
            for t in tickets:
                cb(_log_row(t.source, t.update_col or "属性已写入", "保留签出", t.lbl))
                # checkout 时 PLM 已创建新 iteration，此处写回本地文件
                if t.node.filepath and _os.path.isfile(t.node.filepath):
                    _write_plm_attrs_to_catia(t.node.filepath, t.version, t.iteration)
        else:
            cb(f"── 批量签入（{len(tickets)} 个零件）──")
            for t in tickets:
                _do_checkin_ticket(t, client, workspace, options, result, cb)

    # ── Product 注册 ──────────────────────────────────────────────────────────
    if options.register_product:
        cb("正在注册顶层产品（Product）……")
        try:
            pn_root  = bom_root.part_number
            nom_root = (bom_root.attrs.get("Nomenclature") or "").strip() or pn_root
            prod_id  = pn_root.replace(" ", "_")
            client.create_product(workspace, prod_id, pn_root, nom_root)
            result.product_registered = True
            cb(f"产品已注册：{prod_id}（根零件 {pn_root}）")
        except PlmApiError as exc:
            _msg = str(exc)
            if exc.status_code in (400, 409) and (
                "already exists" in _msg or "已存在" in _msg
                or "duplicate" in _msg.lower()
            ):
                result.product_registered = True
                cb(f"产品已存在，跳过注册：{bom_root.part_number}")
            else:
                result.errors.append(f"产品注册失败：{exc}")
                cb(f"警告：产品注册失败 — {exc}")

    return result


def _plm_call_with_retry(fn, *args, max_retries: int = _RETRY_MAX, **kwargs):
    """对网络层错误（status_code==0）自动重试，HTTP 4xx/5xx 不重试。"""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except PlmApiError as exc:
            if exc.status_code != 0:
                raise
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                logger.warning(f"网络错误，{delay}s 后重试（{attempt + 1}/{max_retries}）：{exc}")
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _get_checkout_owner(client, workspace: str, part_number: str) -> tuple[str | None, str, int]:
    """查询零件当前最新版本的 checkout 持有者用户名及最新迭代号。

    返回 (checkout_owner, version, iteration)：
      - checkout_owner: 签出人 login，未签出时为 None
      - version: 找到的最新版本（如 "A"），用于后续 checkout/checkin 调用
      - iteration: lastIterationNumber，已签出时直接使用，省去额外的 get_latest_version 调用

    内部使用 get_part_head() 动态找到实际最新版本，不再依赖调用方传入版本号，
    避免了零件已升版时版本硬编码为 "A" 的 Bug。
    """
    try:
        r    = client.get_part_head(workspace, part_number)
        ver  = r.get("version", "A")
        user = (r.get("checkOutUser") or {}).get("login")
        itr  = int((r.get("partIterations") or [{}])[-1].get("iteration", 1))
        return (str(user).strip() if user else None), ver, itr
    except Exception:
        return None, "A", 1


def _find_drawing_for_part(filepath: str) -> str | None:
    """从零件文件路径查找对应的 CATDrawing 文件路径。

    委托给 dependencies.find_drawing_for_part()，取优先级最高的第一个结果。
    同步场景不需要用户选择，若有多个候选则记录日志后取第一个。
    未找到时返回 None。
    """
    candidates = find_drawing_for_part(filepath)
    if not candidates:
        return None
    if len(candidates) > 1:
        logging.getLogger(__name__).info(
            f"_find_drawing_for_part: 找到 {len(candidates)} 个候选图纸，取第一个：{candidates[0]}"
        )
    return candidates[0]


def _instances_to_cad_instances(instances: list) -> list[dict]:
    """将 BomNode.instances 中的 4×4 变换矩阵列表转换为 PLM cadInstances JSON 格式。

    PLM 服务端 RotationMatrix(double[]) 构造函数按**列主序**读入传入数组：
        values[0..2] = 第一列 (col 0)，values[3..5] = 第二列 (col 1)，values[6..8] = 第三列 (col 2)
    即 m00=v[0], m10=v[1], m20=v[2], m01=v[3], m11=v[4], m21=v[5], m02=v[6], m12=v[7], m22=v[8]

    因此必须发**列主序**数组，才能让 PLM 存入与 CATIA 一致的旋转矩阵：
        发 [mat[0][0], mat[1][0], mat[2][0],   ← 第一列
             mat[0][1], mat[1][1], mat[2][1],   ← 第二列
             mat[0][2], mat[1][2], mat[2][2]]    ← 第三列

    如果发行主序数组，RotationMatrix 构造时会把矩阵转置，导致旋转方向错误。
    平移 tx/ty/tz 直接取 mat[i][3]，不受此影响。

    参考：
        RotationMatrix.java:45-57  — 构造函数列主序赋值
        RotationMatrix.java:132-134 — getValues() 行主序输出（两者合计等于一次转置）
        PartResource.java:892-904  — createComponents() 直接把 matrix[] 传给 RotationMatrix()
    """
    result = []
    for mat in instances:
        if mat is None:
            continue
        try:
            entry = {
                "rotationType": "MATRIX",
                # 列主序 9 元素数组：PLM RotationMatrix 构造函数按此顺序读入
                # values[0..2]=第一列, values[3..5]=第二列, values[6..8]=第三列
                "matrix": [
                    mat[0][0], mat[1][0], mat[2][0],   # col 0
                    mat[0][1], mat[1][1], mat[2][1],   # col 1
                    mat[0][2], mat[1][2], mat[2][2],   # col 2
                ],
                "tx": mat[0][3],
                "ty": mat[1][3],
                "tz": mat[2][3],
            }
            result.append(entry)
        except Exception as e:
            logger.debug(f"_instances_to_cad_instances: 跳过无效矩阵 {e}")
    return result


def _sync_node(
    node: BomNode,
    client,
    workspace: str,
    tpl_id: str | None,
    options: SyncOptions,
    result: SyncResult,
    cb,
    plm_parts_cache: dict | None = None,
    tickets: list | None = None,
    uploaded_pns: dict | None = None,
) -> tuple[str, str] | None:
    """递归同步单个 BOM 节点（阶段一：checkout + update + upload）。

    返回 (part_number, version) 或 None（失败/跳过时）。
    成功处理的节点以 CheckinTicket 追加到 tickets 列表，由调用方在阶段二统一 checkin。

    uploaded_pns: dict[pn -> (part_number, version)]
        跨层级去重表——同一 Part Number 无论出现在多少个父节点下，只上传一次。
        后续出现时直接返回缓存的引用，但父节点仍会用正确的 cadInstances 写入装配关系。
    """
    if plm_parts_cache is None:
        plm_parts_cache = {}
    if tickets is None:
        tickets = []
    if uploaded_pns is None:
        uploaded_pns = {}

    # 1. 递归处理子节点（后序）
    child_components = []
    for child in node.children:
        ref = _sync_node(
            child, client, workspace, tpl_id, options, result, cb,
            plm_parts_cache=plm_parts_cache,
            tickets=tickets,
            uploaded_pns=uploaded_pns,
        )
        if ref:
            child_pn, _ver = ref
            try:
                _, latest_ver, _ = _plm_call_with_retry(
                    client.get_latest_version, workspace, child_pn
                )
            except PlmApiError:
                latest_ver = _ver

            # 从该子节点的 instances 列表构造 cadInstances（局部变换矩阵→PLM 格式）
            cad_instances = _instances_to_cad_instances(child.instances)
            # amount 为该子零件在父装配中的实例数量；不设置时服务端 Java int 默认为 0，
            # 导致前端结构树无法展开，因此必须显式写入。
            comp_entry: dict = {
                "component": {"number": child_pn, "version": latest_ver},
                "amount": len(child.instances) if child.instances else 1,
            }
            if cad_instances:
                comp_entry["cadInstances"] = cad_instances
            child_components.append(comp_entry)

    pn       = node.part_number
    nom      = (node.attrs.get("Nomenclature") or "").strip()
    plm_name = nom or pn
    lbl      = _lbl(pn, nom)

    # 去重：同一 Part Number 跨层级只执行一次 checkout/update/upload
    # 后续出现时直接返回已缓存的 (part_number, version)，跳过上传
    # 注：child_components 仍以正确的 cadInstances 写入，装配关系不丢失
    if pn in uploaded_pns:
        logger.debug(f"{lbl}: 已处理过（跨层级去重），跳过上传，返回缓存引用")
        return uploaded_pns[pn]

    # 用户勾选过滤：part_upgrade_map 非空时，只 sync map 中的零件。
    # 不在 map 中的零件（子节点）仍需查 PLM 最新 version，
    # 供父节点构建 child_components（装配关系），但不做 checkout/update/upload。
    if options.part_upgrade_map and pn not in options.part_upgrade_map:
        try:
            _, latest_ver, _ = _plm_call_with_retry(
                client.get_latest_version, workspace, pn
            )
            return (pn, latest_ver)
        except PlmApiError:
            # 子零件不存在于 PLM → 跳过，不触发自动创建空壳
            return None

    # 2. 用 POST /parts 探测零件是否存在，同时完成新建
    try:
        part_number, version, iteration = _plm_call_with_retry(
            client.create_part,
            workspace, pn, plm_name,
            node.attrs.get("Description", ""),
            tpl_id,
        )
        # 新建成功，服务端自动 checkout
        if not options.create_new_parts:
            # 不新建模式：立即 checkin 再删除，然后跳过
            try:
                _plm_call_with_retry(client.checkin_part, workspace, part_number, version)
                _plm_call_with_retry(client.delete_part, workspace, part_number, version)
            except PlmApiError:
                pass
            result.skipped += 1
            cb(_log_skip("跳过-不新建", lbl))
            return None
        result.created += 1
        _ref = _do_update_and_upload(
            node, lbl, "新建", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )
        uploaded_pns[pn] = _ref
        return _ref
    except PlmApiError as exc:
        _msg = str(exc)
        _is_exists = (exc.status_code == 400 and (
            "already exists" in _msg
            or "已存在" in _msg
            or "不唯一" in _msg
            or "not unique" in _msg.lower()
            or "may not be unique" in _msg.lower()  # 服务端 CreationException 消息
        ))
        if _is_exists:
            part_number = pn
            # version 将由下方 _get_checkout_owner() 通过 get_part_head() 动态获取，
            # 此处设兜底值，若 _get_checkout_owner 异常时使用
            version = "A"
        else:
            result.failed += 1
            msg = f"创建失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg} — {exc}")
            cb(_log_fail(msg, lbl))
            return None

    # 3. 零件已存在

    # ── 增量判断：若缓存中有此零件，对比属性，完全一致则跳过 ───────────────
    if options.incremental and pn in plm_parts_cache:
        cached_attrs = plm_parts_cache[pn].get("attrs", {})
        node_attrs = {
            k: v for k, v in node.attrs.items()
            if k not in _STRUCTURAL_COLS and v
        }
        plm_attrs = {
            k: v for k, v in cached_attrs.items()
            if not k.startswith("__builtin_") and v
        }
        if node_attrs == plm_attrs and not child_components:
            result.unchanged += 1
            cb(_log_skip("无变化-跳过", lbl))
            cached_ver = plm_parts_cache[pn].get("version", version)
            return part_number, cached_ver

    # ── 已存在零件：查询 checkout 状态（同时获取实际最新版本和迭代号） ──────────
    checkout_owner, version, head_iteration = _get_checkout_owner(client, workspace, part_number)

    if checkout_owner is None:
        # 状态：Checked In（无人签出）— 签出后更新属性+附件
        try:
            iteration = _plm_call_with_retry(
                client.checkout_part, workspace, part_number, version
            )
        except PlmApiError as exc:
            result.failed += 1
            msg = f"签出失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg} — {exc}")
            cb(_log_fail(msg, lbl))
            return part_number, version

        _ref = _do_update_and_upload(
            node, lbl, "签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )
        uploaded_pns[pn] = _ref
        return _ref

    current_login = getattr(client, "_login", None)
    is_mine = (current_login is not None and checkout_owner.lower() == current_login.lower())

    if is_mine:
        # 状态：Checked Out by me — 直接使用 _get_checkout_owner 已返回的迭代号，无需再查
        _ref = _do_update_and_upload(
            node, lbl, "已签出-本人", client, workspace, part_number, version, head_iteration,
            child_components, options, result, cb, tickets,
        )
        uploaded_pns[pn] = _ref
        return _ref

    else:
        # 状态：Checked Out by other
        if options.other_checked_out_policy == CheckedOutByOtherPolicy.SKIP:
            result.skipped += 1
            result.errors.append(f"{lbl}: 已被 {checkout_owner} 签出，已跳过")
            cb(_log_skip(f"跳过-被@{checkout_owner}", lbl))
            return part_number, version

        try:
            _plm_call_with_retry(
                client.force_undo_checkout, workspace, part_number, version
            )
        except PlmApiError as exc:
            result.skipped += 1
            msg = f"撤销失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg}（权限不足，锁定者：{checkout_owner}）— {exc}")
            cb(_log_skip(f"撤销失败-@{checkout_owner}", lbl))
            return part_number, version

        try:
            iteration = _plm_call_with_retry(
                client.checkout_part, workspace, part_number, version
            )
        except PlmApiError as exc:
            result.failed += 1
            msg = f"撤销后签出失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg} — {exc}")
            cb(_log_fail(msg, lbl))
            return part_number, version

        _ref = _do_update_and_upload(
            node, lbl, "覆盖他人签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )
        uploaded_pns[pn] = _ref
        return _ref


def _wait_for_conversion(
    client,
    workspace: str,
    part_number: str,
    version: str,
    iteration: int,
    timeout_s: int,
    poll_interval_s: int,
    cb,
    lbl: str,
    source: str,
) -> bool:
    """轮询等待 PLM CAD → OBJ 转换完成，返回是否成功。

    PLM 转换是异步的（Kafka 任务队列）：
      pending=True  → 转换任务排队或进行中
      pending=False, succeed=True  → 转换成功，geometry 已写入
      pending=False, succeed=False → 转换失败或尚未开始

    转换结果回调在写入 geometry 前会再次检查 isCheckedOut()。
    因此零件必须保持 checked-out 状态直到转换完成。

    本函数通过发送 col3="" 的过程行（_log_row）向 UI 推送进度；
    UI 侧通过 col3 是否为空区分过程行与终态行，不将其计入 node_done。
    """
    deadline = _time.monotonic() + timeout_s
    elapsed  = 0
    interval = poll_interval_s

    while _time.monotonic() < deadline:
        try:
            status = client.get_conversion_status(workspace, part_number, version, iteration)
        except Exception as _exc:
            logger.warning(f"{lbl}: 查询转换状态失败（继续等待）— {_exc}")
            _time.sleep(interval)
            elapsed += interval
            continue

        pending = status.get("pending", False)
        succeed = status.get("succeed", False)

        if not pending:
            if succeed:
                logger.info(f"{lbl}: CAD 转换成功（{elapsed}s）")
                # col3="" 表示过程行，UI 不计入 node_done
                cb(_log_row(source, "转换完成", "", lbl))
                return True
            else:
                # pending=False, succeed=False：转换失败或记录尚未创建
                # 刚上传时可能 conversion 记录尚未建立，等一个周期再判断
                if elapsed == 0:
                    _time.sleep(interval)
                    elapsed += interval
                    continue
                logger.warning(f"{lbl}: CAD 转换失败（succeed=false，已等待 {elapsed}s）")
                cb(_log_row(source, "✗ 转换失败", "", lbl))
                return False

        # col3="" 过程行：实时刷新树单元格但不计 node_done
        cb(_log_row(source, f"转换中…({elapsed}s)", "", lbl))
        logger.debug(f"{lbl}: 等待转换 {elapsed}s / {timeout_s}s")
        _time.sleep(interval)
        elapsed += interval

    logger.warning(f"{lbl}: 等待 CAD 转换超时（{timeout_s}s）")
    cb(_log_row(source, f"✗ 转换超时({timeout_s}s)", "", lbl))
    return False


def _do_update_and_upload(
    node: BomNode,
    lbl: str,
    source: str,
    client,
    workspace: str,
    part_number: str,
    version: str,
    iteration: int,
    child_components: list,
    options: SyncOptions,
    result: SyncResult,
    cb,
    tickets: list,
) -> tuple[str, str]:
    """阶段一：执行属性更新 + 附件/STP 上传，生成 CheckinTicket 追加到 tickets。

    本函数不执行 checkin，不输出终态日志行。
    终态日志（col1/col2/col3 均非空）由阶段二的 _do_checkin_ticket() 输出。

    中间过程行（附件已上传、STP 已上传、转换进度）通过 col3="" 的 _log_row 输出，
    UI 解析时据此识别为过程行，不触发 node_done 计数。
    """
    # ── 属性更新 ──────────────────────────────────────────────────────────────
    attr_values = {
        k: v for k, v in node.attrs.items()
        if k not in _STRUCTURAL_COLS and v
    }

    # child_components 为空列表时传 None，让 PLM 保留现有装配关系而不是清空。
    # PLM 端：components=null → 跳过更新；components=[] → 清空所有子组件。
    components_to_send = child_components if child_components else None

    update_ok = True
    try:
        _plm_call_with_retry(
            client.update_iteration,
            workspace, part_number, version, iteration,
            attr_values, components_to_send,
        )
        if source != "新建":
            result.updated += 1
        update_col = "属性已写入"
    except PlmApiError as exc:
        update_ok = False
        update_col = "✗ 更新失败"
        msg = f"属性更新失败({exc.status_code}) — {exc}"
        logger.warning(f"{lbl}: {msg}")
        result.errors.append(f"{lbl}: {msg}")

    # ── 文件上传（各项独立，由 SyncOptions 控制）────────────────────────────
    upload_col       = ""   # 供 ticket.upload_col 显示用（取最后一次非空上传结果）
    needs_conversion = False
    fp = node.filepath

    # 1. 上传原始 CATIA 文件（CATPart / CATProduct）
    if options.upload_catpart_file and fp and _os.path.isfile(fp):
        try:
            _t0 = _time.time()
            client.upload_attached_file(workspace, part_number, version, iteration, fp)
            _elapsed = _time.time() - _t0
            upload_col = "CATIA文件已上传"
            cb(_log_row(source, "CATIA文件已上传", "", lbl))
            if _elapsed > 0:
                _fsize_kb = _os.path.getsize(fp) / 1024
                if _fsize_kb > 0:
                    cb(f"  {upload_col} ({_fsize_kb/_elapsed:.1f} KB/s)")
        except Exception as _exc:
            logger.warning(f"{lbl}: CATIA 文件上传失败 — {_exc}")
            result.errors.append(f"{lbl}: CATIA 文件上传失败 — {_exc}")
            cb(_log_row(source, "✗ CATIA文件上传失败", "", lbl))

    # 2. 导出并上传 STP 几何文件（仅 Part 类型；触发 PLM 异步 CAD 转换）
    if (options.upload_step_file
            and fp and node.filetype == BomNodeType.PART
            and fp.lower().endswith(".catpart")
            and _os.path.isfile(fp)):
        try:
            _pcom.CoInitialize()
            try:
                catia   = _win32.GetActiveObject("CATIA.Application")
                fp_norm = _os.path.normcase(_os.path.normpath(fp))
                target_doc = None
                for i in range(catia.Documents.Count):
                    doc = catia.Documents.Item(i + 1)
                    try:
                        if _os.path.normcase(_os.path.normpath(doc.FullName)) == fp_norm:
                            target_doc = doc
                            break
                    except Exception:
                        continue
                if target_doc is None:
                    raise RuntimeError(f"CATIA 中未找到已打开的文档：{_os.path.basename(fp)}")
                with tempfile.TemporaryDirectory() as tmpdir:
                    stp_name = _os.path.splitext(_os.path.basename(fp))[0] + ".stp"
                    stp_path = _os.path.join(tmpdir, stp_name)
                    logger.debug(f"{lbl}: 开始导出 STP → {stp_path}")
                    target_doc.ExportData(stp_path, "stp")
                    if not _os.path.isfile(stp_path):
                        raise FileNotFoundError(f"ExportData 未生成文件：{stp_path}")
                    _t0 = _time.time()
                    client.upload_step(workspace, part_number, version, iteration, stp_path)
                    _elapsed = _time.time() - _t0
                    result.step_uploaded += 1
                    upload_col       = "STP已上传"
                    needs_conversion = True
                    cb(_log_row(source, "STP已上传", "", lbl))
                    if _elapsed > 0:
                        _fsize_kb = _os.path.getsize(stp_path) / 1024
                        if _fsize_kb > 0:
                            cb(f"  {upload_col} ({_fsize_kb/_elapsed:.1f} KB/s)")
            finally:
                _pcom.CoUninitialize()
        except Exception as _exc:
            logger.warning(f"{lbl}: STP 上传失败（不影响主流程）— {_exc}")
            result.errors.append(f"{lbl}: STP 上传失败 — {_exc}")
            cb(_log_row(source, "✗ STP上传失败", "", lbl))

    # 3. 将对应 CATDrawing 转换为 PDF 并上传
    #    按 constants.DRAWING_SEARCH_STRATEGIES 策略查找 CATDrawing
    if options.upload_drawing_pdf and fp:
        drawing_path = _find_drawing_for_part(fp)
        if drawing_path and _os.path.isfile(drawing_path):
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    converted = convert_drawing_to_pdf(
                        file_paths=[drawing_path],
                        output_folder=tmpdir,
                        prefix="",
                        suffix="",
                    )
                    if converted:
                        # convert_drawing_to_pdf 输出文件名与源文件同名，扩展名 .pdf
                        pdf_path = str(_pl.Path(tmpdir) / (_pl.Path(drawing_path).stem + ".pdf"))
                        if _os.path.isfile(pdf_path):
                            _t0 = _time.time()
                            client.upload_attached_file(
                                workspace, part_number, version, iteration, pdf_path
                            )
                            _elapsed = _time.time() - _t0
                            upload_col = "图纸PDF已上传"
                            cb(_log_row(source, "图纸PDF已上传", "", lbl))
                            if _elapsed > 0:
                                _fsize_kb = _os.path.getsize(pdf_path) / 1024
                                if _fsize_kb > 0:
                                    cb(f"  {upload_col} ({_fsize_kb/_elapsed:.1f} KB/s)")
                        else:
                            raise FileNotFoundError(f"PDF 文件未生成：{pdf_path}")
                    else:
                        raise RuntimeError("convert_drawing_to_pdf 返回 0（转换失败）")
            except Exception as _exc:
                logger.warning(f"{lbl}: 图纸 PDF 上传失败 — {_exc}")
                result.errors.append(f"{lbl}: 图纸 PDF 上传失败 — {_exc}")
                cb(_log_row(source, "✗ 图纸PDF上传失败", "", lbl))
        elif drawing_path is None:
            logger.debug(f"{lbl}: 未找到对应 CATDrawing，跳过 PDF 上传")

    # 4. 上传 CATDrawing 原文件
    if options.upload_drawing_file and fp:
        drawing_path = _find_drawing_for_part(fp)
        if drawing_path and _os.path.isfile(drawing_path):
            try:
                _t0 = _time.time()
                client.upload_attached_file(
                    workspace, part_number, version, iteration, drawing_path
                )
                _elapsed = _time.time() - _t0
                upload_col = "图纸文件已上传"
                cb(_log_row(source, "图纸文件已上传", "", lbl))
                if _elapsed > 0:
                    _fsize_kb = _os.path.getsize(drawing_path) / 1024
                    if _fsize_kb > 0:
                        cb(f"  {upload_col} ({_fsize_kb/_elapsed:.1f} KB/s)")
            except Exception as _exc:
                logger.warning(f"{lbl}: CATDrawing 上传失败 — {_exc}")
                result.errors.append(f"{lbl}: CATDrawing 上传失败 — {_exc}")
                cb(_log_row(source, "✗ 图纸文件上传失败", "", lbl))
        elif drawing_path is None:
            logger.debug(f"{lbl}: 未找到对应 CATDrawing，跳过文件上传（TODO-01）")

    # ── 生成 CheckinTicket ────────────────────────────────────────────────────
    ticket = CheckinTicket(
        part_number      = part_number,
        version          = version,
        iteration        = iteration,
        lbl              = lbl,
        source           = source,
        update_col       = update_col,
        upload_col       = upload_col,
        needs_conversion = needs_conversion,
        node             = node,
        update_ok        = update_ok,
    )
    tickets.append(ticket)

    # 统计修正：update 失败在阶段一就记录
    if not update_ok:
        if source != "新建" and result.updated > 0:
            result.updated -= 1
        result.failed += 1

    return part_number, version


def _do_checkin_ticket(
    ticket: CheckinTicket,
    client,
    workspace: str,
    options: SyncOptions,
    result: SyncResult,
    cb,
) -> None:
    """阶段二：对单个 CheckinTicket 执行 checkin，输出终态日志行。

    终态行格式：col1=source，col2=update_col，col3=已签入/✗ 签入失败
    这是每个节点唯一的终态行，UI 据此触发一次 node_done 计数。
    """
    try:
        _plm_call_with_retry(
            client.checkin_part, workspace, ticket.part_number, ticket.version
        )
        col3 = "已签入"
    except PlmApiError as exc:
        col3 = "✗ 签入失败"
        msg  = f"签入失败({exc.status_code}) — {exc}"
        logger.warning(f"{ticket.lbl}: {msg}")
        result.errors.append(f"{ticket.lbl}: {msg}")

    # 终态行：col1/col2/col3 均非空，UI 触发一次 node_done
    cb(_log_row(ticket.source, ticket.update_col, col3, ticket.lbl))

    # ── 签入成功后：将 PLM_Version / PLM_Iteration 写回 CATIA 文件 ────────────
    # ticket.iteration 是 checkout 时 PLM 返回的新迭代号；checkin 只是锁定该迭代，
    # 不会再改变编号，直接使用即可。
    if col3 == "已签入" and ticket.node.filepath and _os.path.isfile(ticket.node.filepath):
        _write_plm_attrs_to_catia(
            ticket.node.filepath,
            ticket.version,
            ticket.iteration,
        )

    # ── Tag 自动映射（checkin 后执行，不影响主流程） ─────────────────────────
    if ticket.update_ok and options.tag_rules:
        design_state = (ticket.node.attrs.get("设计状态") or "").strip()
        if design_state:
            matched_tags = [
                rule["plm_tag"]
                for rule in options.tag_rules
                if rule.get("catia_value") == design_state and rule.get("plm_tag")
            ]
            if matched_tags:
                try:
                    _plm_call_with_retry(
                        client.update_part_tags,
                        workspace, ticket.part_number, ticket.version, matched_tags,
                    )
                    logger.debug(f"PLM Tag 写入：{ticket.part_number} → {matched_tags}")
                except PlmApiError as exc:
                    logger.warning(f"Tag 写入失败（不影响同步）：{ticket.lbl} — {exc}")
                    result.errors.append(f"{ticket.lbl}: Tag 写入失败 — {exc}")


def _write_plm_attrs_to_catia(
    filepath: str,
    plm_version: str,
    plm_iteration: int,
) -> None:
    """将 PLM_Version 和 PLM_Iteration 写回 CATIA 文件的用户自定义属性。

    在后台线程中调用（已由调用方保证 CoInitialize），通过 COM 操作 CATIA 文档。
    若 CATIA 未运行或文件未打开，则静默跳过，只记录 debug 日志。
    """
    try:
        result = set_document_properties(
            filepath,
            user_defined={
                "PLM_Version": str(plm_version),
                "PLM_Iteration": str(int(plm_iteration)),
            },
            save=True,
        )
        logger.info(
            f"PLM 属性已写回 CATIA：{_os.path.basename(filepath)} "
            f"→ PLM_Version={plm_version}, PLM_Iteration={plm_iteration}"
        )
        if result.get("skipped"):
            logger.warning(f"PLM 属性写回：部分属性跳过 {result['skipped']}（{filepath}）")
    except FileNotFoundError:
        logger.warning(f"PLM 属性写回跳过（文档未在 CATIA 中打开）：{filepath}")
    except RuntimeError as exc:
        logger.warning(f"PLM 属性写回跳过（{exc}）：{filepath}")
    except Exception as exc:
        logger.warning(f"PLM 属性写回失败（不影响同步）：{filepath} — {exc}")
