"""
CATIA BOM → DocdokuPLM 同步逻辑。

入口函数：sync_bom_to_plm()
  - 从当前活动 CATIA 文档读取完整产品结构（BOM）
  - 后序深度优先遍历（子节点先于父节点同步）
  - 每个节点按 SyncOptions 指定的策略处理 checkout / update / checkin
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

logger = logging.getLogger(__name__)


# ── 同步选项 ──────────────────────────────────────────────────────────────────

class ExistingPartPolicy(Enum):
    """已存在零件（Checked In 状态）的处理策略。"""
    SKIP          = "skip"           # 跳过，不做任何更新
    CHECKOUT_UPDATE = "checkout_update"  # Checkout 后更新属性（推荐）


class CheckedOutByOtherPolicy(Enum):
    """零件已被他人 Checkout 时的处理策略。"""
    SKIP          = "skip"           # 跳过并记录警告（不计入 failed）
    FORCE_UNDO    = "force_undo"     # 强制撤销他人签出后更新（需管理员权限）


class OwnCheckedOutPolicy(Enum):
    """零件已由当前用户 Checkout（未签入）时的处理策略。"""
    UPDATE        = "update"         # 直接更新（利用现有签出）
    SKIP          = "skip"           # 跳过并计入失败统计


class AfterUpdatePolicy(Enum):
    """更新属性后的处理策略。"""
    CHECKIN       = "checkin"        # 自动 Check In（推荐）
    KEEP_CHECKOUT = "keep_checkout"  # 保留 Checked Out 状态


@dataclass
class SyncOptions:
    """同步策略选项，由 UI 对话框收集后传入 sync_bom_to_plm()。"""
    # 已存在零件（Checked In）的处理方式
    existing_part_policy: ExistingPartPolicy = ExistingPartPolicy.CHECKOUT_UPDATE

    # Workspace 中不存在的零件是否新建
    create_new_parts: bool = True

    # 我自己已 Checkout 但未 Checkin 的零件：是否继续更新？
    own_checked_out_policy: OwnCheckedOutPolicy = OwnCheckedOutPolicy.UPDATE

    # 他人已 Checkout 的零件：强制撤销还是跳过？
    other_checked_out_policy: CheckedOutByOtherPolicy = CheckedOutByOtherPolicy.SKIP

    # 更新完成后是否自动 Check In
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


@dataclass
class SyncResult:
    """同步操作汇总结果。"""
    created: int = 0
    updated: int = 0   # 已存在，成功更新
    skipped: int = 0   # 按策略跳过（不计入失败）
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed

    def summary(self) -> str:
        lines = [
            f"同步完成：共 {self.total} 个节点",
            f"  ✓ 新建：{self.created}",
            f"  ✓ 已更新：{self.updated}",
            f"  → 已跳过：{self.skipped}",
            f"  ✗ 失败：{self.failed}",
        ]
        if self.errors:
            lines.append("\n失败 / 警告详情：")
            for e in self.errors[:10]:
                lines.append(f"  · {e}")
            if len(self.errors) > 10:
                lines.append(f"  … 共 {len(self.errors)} 条")
        return "\n".join(lines)


# ── BOM 提取（CATIA COM，须在主线程调用） ──────────────────────────────────────

_BUILTIN_ATTR_COLS: list[str] = ["Nomenclature", "Definition", "Revision", "Source", "Description"]
_CUSTOM_COLS:       list[str] = ["零件类型", "设计状态", "材料", "重量", "物料编码", "存货类别", "规格型号", "备注"]
_ALL_ATTR_COLS:     list[str] = _BUILTIN_ATTR_COLS + _CUSTOM_COLS

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
    """三列 + 零件标识，列间用 ' | ' 分隔，按显示宽度对齐。"""
    return f"  {_ljust(col1,_W1)} | {_ljust(col2,_W2)} | {_ljust(col3,_W3)} | {lbl}"


# 跳过/失败行前缀均为 4 个 ASCII 字符（">>  " / "[X] "）
# 普通行 lbl 前显示宽 = 2 + W1 + 3 + W2 + 3 + W3 + 3 = W1+W2+W3+11
# 跳过行 lbl 前显示宽 = 2 + 4(">>  ") + reason_dw + 3(" | ") = reason_dw + 9
# => reason_dw = W1+W2+W3+2
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
    """将 BOM 树同步到 DocdokuPLM（不涉及 CATIA COM，可在后台线程执行）。"""
    from catia_copilot.plm.api_client import PlmApiError

    if options is None:
        options = SyncOptions()

    result = SyncResult()

    def _cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.debug(msg)

    # 确保模板存在（失败不阻断同步）
    tpl_id: str | None = None
    try:
        tpl_id = client.ensure_part_template(workspace)
    except PlmApiError as exc:
        logger.warning(f"模板初始化失败（将以无模板方式继续）：{exc}")
        _cb(f"警告：模板初始化失败，将以无模板方式继续 — {exc}")

    _cb(_log_header())
    _sync_node(bom_root, client, workspace, tpl_id, options, result, _cb)
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
    """查询零件当前的 checkout 持有者用户名，未签出时返回 None。

    通过 GET /parts/{pn}-{ver} 响应体中的 checkOutUser 字段判断。
    若服务端不返回该字段，则返回 None（按未签出处理）。
    """
    ws = urllib.parse.quote(workspace) if False else workspace  # 已在调用前 quote，此处直接用
    try:
        from catia_copilot.plm.api_client import PlmApiError
        import urllib.parse as _up
        ws_q  = _up.quote(workspace)
        pn_q  = _up.quote(part_number)
        result = client._request("GET", f"/workspaces/{ws_q}/parts/{pn_q}-{version}") or {}
        # checkOutUser 是嵌套对象 {"login": ..., ...}，未签出时为 null
        # 文档确认无顶级 checkOutLogin 字段
        user = (result.get("checkOutUser") or {}).get("login")
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
) -> tuple[str, str] | None:
    """递归同步单个 BOM 节点，返回 (part_number, version) 或 None（失败时）。

    零件状态机：
        新建  → 服务端自动处于 WIP（已由创建者 checkout）→ update → [checkin]
        已存在 Checked In    → 按 existing_part_policy 决定是否 checkout
        已存在 Checked Out by me    → 按 own_checked_out_policy 决定是否 update
        已存在 Checked Out by other → 按 other_checked_out_policy 决定是否强制撤销
    """
    from catia_copilot.plm.api_client import PlmApiError

    # 1. 递归处理子节点（后序），并取各子节点 PLM 最新版本
    child_components = []
    for child in node.children:
        ref = _sync_node(child, client, workspace, tpl_id, options, result, cb)
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
    plm_name = node.attrs.get("Nomenclature") or pn
    lbl      = _lbl(pn, plm_name)

    # 2. 判断零件是否已存在
    existing: tuple | None = None   # (part_number, version, iteration) 或 None
    try:
        pn_r, ver_r, iter_r = _plm_call_with_retry(
            client._get_latest_version, workspace, pn
        )
        existing = (pn_r, ver_r, iter_r)
    except PlmApiError as exc:
        if exc.status_code not in (404, 400, 0):
            result.failed += 1
            if exc.status_code == 500:
                # 服务端已知 bug：checkOutUser 为 null 时 isCheckoutByAnotherUser 抛 NPE
                msg = "PLM 服务端内部错误(500)，跳过此零件"
            else:
                msg = f"查询版本失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg} — {exc}")
            cb(_log_fail(msg, lbl))
            return None
        # 404/400 → 零件不存在，existing 保持 None

    # 3. 不存在时：按策略决定是否新建
    is_new = False
    if existing is None:
        if not options.create_new_parts:
            result.skipped += 1
            cb(_log_skip("跳过-不新建", lbl))
            return None
        # 新建
        try:
            part_number, version = _plm_call_with_retry(
                client.create_part,
                workspace, pn, plm_name,
                node.attrs.get("Description", ""),
                tpl_id,
            )
            is_new = True
            result.created += 1
        except PlmApiError as exc:
            result.failed += 1
            msg = f"创建失败({exc.status_code})"
            result.errors.append(f"{lbl}: {msg} — {exc}")
            cb(_log_fail(msg, lbl))
            return None
        try:
            _, _, iteration = _plm_call_with_retry(
                client._get_latest_version, workspace, part_number
            )
        except PlmApiError:
            iteration = 1
        return _do_update_and_checkin(
            node, lbl, "新建", client, workspace, part_number, version, iteration,
            child_components, options, result, cb,
        )

    # 4. 零件已存在
    part_number, version, _ = existing

    # ── 已存在零件：查询 checkout 状态 ──────────────────────────────────────
    checkout_owner = _get_checkout_owner(client, workspace, part_number, version)

    if checkout_owner is None:
        # 状态：Checked In（无人签出）
        if options.existing_part_policy == ExistingPartPolicy.SKIP:
            result.skipped += 1
            result.errors.append(f"{lbl}: 跳过（策略：不更新已签入零件）")
            cb(_log_skip("跳过-已签入", lbl))
            return part_number, version

        # CHECKOUT_UPDATE：尝试签出
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

        return _do_update_and_checkin(
            node, lbl, "签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb,
        )

    # 判断是否为当前登录用户
    current_login = getattr(client, "_login", None)
    is_mine = (current_login is not None and checkout_owner.lower() == current_login.lower())

    if is_mine:
        # 状态：Checked Out by me — 始终直接更新
        try:
            _, _, iteration = _plm_call_with_retry(
                client._get_latest_version, workspace, part_number
            )
        except PlmApiError:
            iteration = 1
        return _do_update_and_checkin(
            node, lbl, "已签出-本人", client, workspace, part_number, version, iteration,
            child_components, options, result, cb,
        )

    else:
        # 状态：Checked Out by other
        if options.other_checked_out_policy == CheckedOutByOtherPolicy.SKIP:
            result.skipped += 1
            result.errors.append(f"{lbl}: 已被 {checkout_owner} 签出，已跳过")
            cb(_log_skip(f"跳过-被@{checkout_owner}", lbl))
            return part_number, version

        # FORCE_UNDO：尝试强制撤销他人签出
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

        # 撤销成功后重新签出
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

        return _do_update_and_checkin(
            node, lbl, "覆盖他人签出", client, workspace, part_number, version, iteration,
            child_components, options, result, cb,
        )


def _do_update_and_checkin(
    node: BomNode,
    lbl: str,
    source: str,      # 列1文本：签出来源，如 "新建"/"签出"/"已签出-本人"/"撤销后签出"
    client,
    workspace: str,
    part_number: str,
    version: str,
    iteration: int,
    child_components: list,
    options: SyncOptions,
    result: SyncResult,
    cb,
) -> tuple[str, str]:
    """执行属性更新，并按策略决定是否签入，最终输出 1 条对齐日志。

    日志格式（三列 + 零件标识）：
        [签出来源]  [更新结果]  [签入状态]  零件编号<零件名>
    """
    from catia_copilot.plm.api_client import PlmApiError

    # 属性：跳过结构性列
    attr_values = {
        k: v for k, v in node.attrs.items()
        if k not in _STRUCTURAL_COLS and v
    }

    # 列2：更新结果
    update_ok = True
    try:
        _plm_call_with_retry(
            client.update_iteration,
            workspace, part_number, version, iteration,
            attr_values, child_components,
        )
        result.updated += 1
        col2 = "属性已写入"
    except PlmApiError as exc:
        update_ok = False
        col2 = "✗ 更新失败"
        msg = f"属性更新失败({exc.status_code}) — {exc}"
        logger.warning(f"{lbl}: {msg}")
        result.errors.append(f"{lbl}: {msg}")

    # 列3：签入状态
    if options.after_update_policy == AfterUpdatePolicy.CHECKIN:
        try:
            _plm_call_with_retry(
                client.checkin_part, workspace, part_number, version
            )
            col3 = "已签入"
        except PlmApiError as exc:
            col3 = "✗ 签入失败"
            msg = f"签入失败({exc.status_code}) — {exc}"
            logger.warning(f"{lbl}: {msg}")
            result.errors.append(f"{lbl}: {msg}")
    else:
        col3 = "保留签出"

    cb(_log_row(source, col2, col3, lbl))

    # 统计修正：update 失败时回滚 updated +1
    if not update_ok and result.updated > 0:
        result.updated -= 1
        result.failed += 1

    return part_number, version
