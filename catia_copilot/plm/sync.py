"""
CATIA BOM → DocdokuPLM 同步逻辑。

入口函数：sync_bom_to_plm()
  - 从当前活动 CATIA 文档读取完整产品结构（BOM）
  - 后序深度优先遍历（子节点先于父节点同步）
  - 两阶段执行：
      阶段一：checkout → 属性更新 → 上传附件/STP（所有节点）
      阶段二：等待所有上传了 STP 的零件转换完成，再批量 checkin
  - 返回 SyncResult（汇总创建数、跳过数、失败数）

注意：所有 CATIA COM 调用必须在主线程中完成，BOM 数据提取后
可在后台线程中执行 PLM 网络请求。本模块 sync_bom_to_plm() 负责
BOM 提取，调用方负责线程调度。
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from catia_copilot.constants import PRESET_USER_REF_PROPERTIES, PLM_BUILTIN_ATTR_COLS, BomNodeType

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
    CHECKIN       = "checkin"       # 等待转换完成后批量签入（推荐）
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

    # 同步完成后是否为每个 CATPart 上传 STEP 几何文件
    upload_step_files: bool = False

    # 同步完成后是否将顶层装配体注册为 PLM Product
    register_product: bool = False

    # Tag 自动映射规则：[{"catia_value": "发布", "plm_tag": "已归档"}, ...]
    # 根据零件"设计状态"属性值在 checkin 后自动打标签
    tag_rules: list[dict] = field(default_factory=list)

    # STP 上传后等待 CAD → OBJ 转换完成的超时时间（秒）。
    # 转换由 PLM 异步处理，必须在转换结束（零件仍 checked-out）时才写入 geometry。
    # 设为 0 表示上传后不等待（geometry 可能无法写入）。
    conversion_timeout_s: int = 120

    # 所有上传完毕后的签入策略：批量签入 或 保留签出
    after_update_policy: AfterUpdatePolicy = AfterUpdatePolicy.CHECKIN


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
    product_registered: bool = False  # 顶层装配体是否成功注册为 PLM Product

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
    "Nomenclature",   # 已作为零件名称（name 字段）写入
})

# 网络错误重试参数
_RETRY_MAX:    int  = 2
_RETRY_DELAYS: list = [1, 3]


def extract_bom(progress_callback=None) -> BomNode | None:
    """从当前活动 CATIA 文档提取 BOM 树（须在主线程调用）。"""
    from catia_copilot.catia.bom_collect import collect_bom_rows

    def _cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

    _cb("正在读取 BOM……")

    try:
        rows = collect_bom_rows(
            file_path=None,
            columns=_ALL_ATTR_COLS,
            custom_columns=_CUSTOM_COLS,
            progress_callback=lambda count: _cb(f"  已读取 {count} 个节点……"),
        )
    except Exception as exc:
        logger.error(f"BOM 提取失败：{exc}")
        _cb(f"BOM 提取失败：{exc}")
        return None

    if not rows:
        logger.warning("BOM 为空，无活动文档或文档无产品结构")
        return None

    _cb(f"BOM 读取完成，共 {len(rows)} 个节点，正在构建树……")
    return _rows_to_bom_tree(rows)


def _rows_to_bom_tree(rows: list[dict]) -> BomNode | None:
    """将平面层级行列表转换为 BomNode 树。"""
    if not rows:
        return None

    from catia_copilot.constants import SOURCE_TO_DISPLAY

    root: BomNode | None = None
    stack: list[BomNode] = []

    for row in rows:
        level = int(row.get("Level", 0))
        pn    = str(row.get("Part Number") or "").strip()
        if not pn:
            pn = str(row.get("Filename") or "UNKNOWN").strip()

        node = BomNode(part_number=pn)
        # 保存本地文件路径供附件上传使用（内部键，不写入 attrs）
        node.filepath = str(row.get("_filepath") or "").strip()
        # 保存文件类型（BomNodeType.PART / PRODUCT / COMPONENT），用于 STP 导出判断
        node.filetype = str(row.get("Type") or "").strip()
        for col in _ALL_ATTR_COLS:
            val = str(row.get(col) or "").strip()
            if col == "Source":
                val = SOURCE_TO_DISPLAY.get(val, val)
            if val:
                node.attrs[col] = val

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
) -> SyncResult:
    """将 BOM 树同步到 DocdokuPLM（不涉及 CATIA COM，可在后台线程执行）。

    两阶段执行：
      阶段一：遍历所有节点，执行 checkout → 属性更新 → 上传附件/STP，
              收集待签入票据（CheckinTicket）列表。
      阶段二：对上传了 STP 的零件轮询等待转换完成，再对所有票据批量 checkin。

    upload_step 参数保留兼容旧调用方，新调用方通过 options.upload_step_files 控制。
    """
    from catia_copilot.plm.api_client import PlmApiError

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
            upload_step_files=True,
            register_product=options.register_product,
            tag_rules=options.tag_rules,
        )

    result = SyncResult()

    def _cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

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
        _cb(f"✗ 同步中止：BOM 包含部件节点 — {names}")
        return result

    # 确保模板存在（失败不阻断同步）
    tpl_id: str | None = None
    try:
        tpl_id = client.ensure_part_template(workspace)
    except PlmApiError as exc:
        logger.warning(f"模板初始化失败（将以无模板方式继续）：{exc}")
        _cb(f"警告：模板初始化失败，将以无模板方式继续 — {exc}")

    # ── 增量同步：预加载工作区全量零件，建立属性缓存 ────────────────────────
    plm_parts_cache: dict[str, dict] = {}
    if options.incremental:
        _cb("正在拉取工作区零件列表（增量判断）……")
        try:
            raw_parts = client.list_parts(workspace)
            for p in raw_parts:
                pn = p.get("number") or p.get("partNumber") or ""
                if not pn:
                    continue
                ver = p.get("version", "A")
                last_iter = (p.get("partIterations") or [{}])[-1]
                raw_attrs = last_iter.get("instanceAttributes") or []
                attrs: dict[str, str] = {}
                for a in raw_attrs:
                    name = a.get("name") or a.get("attributeName") or ""
                    val  = str(a.get("value") or "").strip()
                    if name:
                        attrs[name] = val
                for builtin_key in ("name", "description"):
                    bval = str(p.get(builtin_key) or "").strip()
                    if bval:
                        attrs[f"__builtin_{builtin_key}"] = bval
                plm_parts_cache[pn] = {"version": ver, "attrs": attrs}
            _cb(f"已缓存 {len(plm_parts_cache)} 个已有零件（增量模式）")
        except PlmApiError as exc:
            logger.warning(f"增量缓存拉取失败，将退化为全量同步：{exc}")
            _cb(f"警告：增量缓存拉取失败，退化为全量同步 — {exc}")

    # ════════════════════════════════════════════════════════════════════
    # 阶段一：checkout + update + 上传，收集 CheckinTicket
    # ════════════════════════════════════════════════════════════════════
    _cb(_log_header())
    tickets: list[CheckinTicket] = []
    _sync_node(
        bom_root, client, workspace, tpl_id, options, result, _cb,
        plm_parts_cache=plm_parts_cache,
        tickets=tickets,
    )

    # ════════════════════════════════════════════════════════════════════
    # 阶段二：等待所有 STP 转换完成，再批量 checkin
    # ════════════════════════════════════════════════════════════════════
    keep_checkout = (
        getattr(options, "after_update_policy", AfterUpdatePolicy.CHECKIN)
        == AfterUpdatePolicy.KEEP_CHECKOUT
    )
    if tickets:
        conversion_tickets = [t for t in tickets if t.needs_conversion]
        # 保留签出时不需要等待转换（零件不会被签入，geometry 写不写无所谓）
        if conversion_tickets and not keep_checkout:
            timeout = getattr(options, "conversion_timeout_s", 120)
            _cb(f"── 等待 CAD 转换（{len(conversion_tickets)} 个零件，超时 {timeout}s）──")
            for t in conversion_tickets:
                if timeout > 0:
                    _wait_for_conversion(
                        client, workspace, t.part_number, t.version, t.iteration,
                        timeout_s=timeout, poll_interval_s=3, cb=_cb,
                        lbl=t.lbl, source=t.source,
                    )

        if keep_checkout:
            # 保留签出：不执行 checkin，仅输出终态日志行（col3="保留签出"）
            _cb(f"── 保留签出（{len(tickets)} 个零件，不执行签入）──")
            for t in tickets:
                _cb(_log_row(t.source, t.update_col or "属性已写入", "保留签出", t.lbl))
        else:
            _cb(f"── 批量签入（{len(tickets)} 个零件）──")
            for t in tickets:
                _do_checkin_ticket(t, client, workspace, options, result, _cb)

    # ── Product 注册 ──────────────────────────────────────────────────────────
    if options.register_product:
        _cb("正在注册顶层产品（Product）……")
        try:
            pn_root  = bom_root.part_number
            nom_root = (bom_root.attrs.get("Nomenclature") or "").strip() or pn_root
            prod_id  = pn_root.replace(" ", "_")
            client.create_product(workspace, prod_id, pn_root, nom_root)
            result.product_registered = True
            _cb(f"产品已注册：{prod_id}（根零件 {pn_root}）")
        except PlmApiError as exc:
            _msg = str(exc)
            if exc.status_code in (400, 409) and (
                "already exists" in _msg or "已存在" in _msg
                or "duplicate" in _msg.lower()
            ):
                result.product_registered = True
                _cb(f"产品已存在，跳过注册：{bom_root.part_number}")
            else:
                result.errors.append(f"产品注册失败：{exc}")
                _cb(f"警告：产品注册失败 — {exc}")

    return result


def _plm_call_with_retry(fn, *args, max_retries: int = _RETRY_MAX, **kwargs):
    """对网络层错误（status_code==0）自动重试，HTTP 4xx/5xx 不重试。"""
    from catia_copilot.plm.api_client import PlmApiError

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


def _get_checkout_owner(client, workspace: str, part_number: str, version: str) -> str | None:
    """查询零件当前的 checkout 持有者用户名，未签出时返回 None。"""
    try:
        import urllib.parse as _up
        ws_q  = _up.quote(workspace)
        pn_q  = _up.quote(part_number)
        r = client._request("GET", f"/workspaces/{ws_q}/parts/{pn_q}-{version}") or {}
        user = (r.get("checkOutUser") or {}).get("login")
        return str(user).strip() if user else None
    except Exception:
        return None


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
) -> tuple[str, str] | None:
    """递归同步单个 BOM 节点（阶段一：checkout + update + upload）。

    返回 (part_number, version) 或 None（失败/跳过时）。
    成功处理的节点以 CheckinTicket 追加到 tickets 列表，由调用方在阶段二统一 checkin。
    """
    from catia_copilot.plm.api_client import PlmApiError

    if plm_parts_cache is None:
        plm_parts_cache = {}
    if tickets is None:
        tickets = []

    # 1. 递归处理子节点（后序）
    child_components = []
    for child in node.children:
        ref = _sync_node(
            child, client, workspace, tpl_id, options, result, cb,
            plm_parts_cache=plm_parts_cache,
            tickets=tickets,
        )
        if ref:
            child_pn, _ver = ref
            try:
                _, latest_ver, _ = _plm_call_with_retry(
                    client._get_latest_version, workspace, child_pn
                )
            except PlmApiError:
                latest_ver = _ver
            child_components.append({"component": {"number": child_pn, "version": latest_ver}})

    pn       = node.part_number
    nom      = (node.attrs.get("Nomenclature") or "").strip()
    plm_name = nom or pn
    lbl      = _lbl(pn, nom)

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
        return _do_update_and_upload(
            node, lbl, "新建", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )
    except PlmApiError as exc:
        _msg = str(exc)
        _is_exists = (exc.status_code == 400 and (
            "already exists" in _msg
            or "已存在" in _msg
            or "不唯一" in _msg
            or "not unique" in _msg.lower()
        ))
        if _is_exists:
            part_number, version = pn, "A"
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

    # ── 已存在零件：查询 checkout 状态 ──────────────────────────────────────
    checkout_owner = _get_checkout_owner(client, workspace, part_number, version)

    if checkout_owner is None:
        # 状态：Checked In（无人签出）
        if options.existing_part_policy == ExistingPartPolicy.SKIP:
            result.skipped += 1
            result.errors.append(f"{lbl}: 跳过（策略：不更新已签入零件）")
            cb(_log_skip("跳过-已签入", lbl))
            return part_number, version

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

        return _do_update_and_upload(
            node, lbl, "签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )

    current_login = getattr(client, "_login", None)
    is_mine = (current_login is not None and checkout_owner.lower() == current_login.lower())

    if is_mine:
        # 状态：Checked Out by me
        try:
            _, _, iteration = _plm_call_with_retry(
                client._get_latest_version, workspace, part_number
            )
        except PlmApiError:
            iteration = 1
        return _do_update_and_upload(
            node, lbl, "已签出-本人", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )

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

        return _do_update_and_upload(
            node, lbl, "覆盖他人签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb, tickets,
        )


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
    import time as _time

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
    from catia_copilot.plm.api_client import PlmApiError

    # ── 属性更新 ──────────────────────────────────────────────────────────────
    attr_values = {
        k: v for k, v in node.attrs.items()
        if k not in _STRUCTURAL_COLS and v
    }

    update_ok = True
    try:
        _plm_call_with_retry(
            client.update_iteration,
            workspace, part_number, version, iteration,
            attr_values, child_components,
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

    # ── 附件 / STP 上传 ───────────────────────────────────────────────────────
    upload_col       = ""   # 供 ticket.upload_col 显示用
    needs_conversion = False

    if options.upload_step_files and node.filepath:
        import os as _os
        fp = node.filepath
        if _os.path.isfile(fp):
            try:
                client.upload_attached_file(workspace, part_number, version, iteration, fp)
                upload_col = "附件已上传"
                # col3="" → 过程行，UI 不计入 node_done
                cb(_log_row(source, "附件已上传", "", lbl))
            except Exception as _exc:
                msg = f"附件上传失败 — {_exc}"
                logger.warning(f"{lbl}: {msg}")
                result.errors.append(f"{lbl}: 原始文件上传失败 — {_exc}")
                cb(_log_row(source, "✗ 附件上传失败", "", lbl))

        # CATPart → 额外导出并上传 STP（仅 Part 类型）
        if node.filetype == BomNodeType.PART and fp.lower().endswith(".catpart") and _os.path.isfile(fp):
            try:
                import tempfile
                import pythoncom as _pcom          # noqa: PLC0415
                import win32com.client as _win32   # noqa: PLC0415
                _pcom.CoInitialize()
                try:
                    catia    = _win32.GetActiveObject("CATIA.Application")
                    fp_norm  = _os.path.normcase(_os.path.normpath(fp))
                    target_doc = None
                    for i in range(catia.Documents.Count):
                        doc = catia.Documents.Item(i + 1)
                        try:
                            doc_path = _os.path.normcase(_os.path.normpath(doc.FullName))
                            if doc_path == fp_norm:
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
                        logger.debug(f"{lbl}: STP 导出完成，准备上传")
                        client.upload_step(workspace, part_number, version, iteration, stp_path)
                        result.step_uploaded += 1
                        upload_col       = "STP 已上传"
                        needs_conversion = True
                        cb(_log_row(source, "STP 已上传", "", lbl))
                finally:
                    _pcom.CoUninitialize()
            except Exception as _exc:
                logger.warning(f"{lbl}: STP 上传失败（不影响主流程）— {_exc}")
                result.errors.append(f"{lbl}: STP 上传失败 — {_exc}")
                cb(_log_row(source, "✗ STP 上传失败", "", lbl))

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
    from catia_copilot.plm.api_client import PlmApiError

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
