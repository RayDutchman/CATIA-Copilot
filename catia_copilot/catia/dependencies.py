"""
CATIA 依赖项查找器。

提供：
- find_dependencies()          – 正向查询：打开目标文件，遍历其引用结构，收集所有子文档路径
- find_reverse_dependencies()  – 反向查询：遍历已打开文档，找出哪些文档引用了目标文件
- find_part_for_drawing()      – 给定 CATDrawing ，启发式查找对应的 CATPart/CATProduct （2A）
- find_drawing_for_part()      – 给定 CATPart/CATProduct ，启发式查找对应的 CATDrawing （2B）

正向查询策略（find_dependencies）：
  CATProduct  – 通过 COM 打开后，只读直接子层（一级）的 Product.Products，
                收集每个直接子件的 ReferenceProduct.Parent.FullName
  CATDrawing  – 通过 COM 打开后，遍历生成式视图 GenerativeBehavior.Document，
                收集关联的零件/产品文档路径（与 doc_file_links 策略一致）
  其他格式    – 退化为快照差值法（打开前后 Documents 集合的新增项），
                结果可能不完整

反向查询策略（find_reverse_dependencies）：
  对每个已打开文档调用 find_dependencies，取其直接依赖列表，
  检查目标路径是否在其中。相当于对所有已打开文档各做一次正向查询，
  找出哪些文档直接引用了目标文件。
  典型用途：想删除某个零件前，先确认哪些已打开的装配体/图纸直接用到了它。

启发式补充策略（DRAWING_SEARCH_STRATEGIES / PART_TO_DRAWING_STRATEGIES）：
  pn_param_open_docs     – 读图纸 Parameters["PartNumber"]，在已打开文档中匹配
                           doc.Product.PartNumber == 该值
  pn_param_open_drws     – 遍历已打开 CATDrawing ，找 Parameters["PartNumber"]
                           == 零件 doc.Product.PartNumber 的图纸
  pn_param_scan_dirs     – 读图纸 Parameters["PartNumber"]，在向上 N 级目录中找
                           文件名（stem）== 该值的 .CATPart/.CATProduct
  pn_param_scan_drws     – 在向上 N 级目录中找文件名（stem）== 零件
                           doc.Product.PartNumber 的 .CATDrawing
  same_name_scan_dirs    – 用文件名 stem 在向上 N 级目录中找同名对应文件
  strip_prefix_scan_dirs – 同上，但先 strip 文件名前缀再匹配
"""

import logging
import re
from collections.abc import Callable
from pathlib import Path

from catia_copilot.constants import (
    DRAWING_SEARCH_STRATEGIES,
    SEARCH_MAX_LEVELS,
    PART_TO_DRAWING_STRATEGIES,
)
from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.utils import open_catia_file

logger = logging.getLogger(__name__)

# CATIA 零件/产品文件扩展名
_PART_EXTS = (".CATPart", ".CATProduct")


def find_dependencies(
    target_path: str,
    progress_callback: Callable[[str], None] | None = None,
    activate: bool = True,
) -> list[str]:
    """返回 *target_path* 所引用的全部子文档路径（去重、保序）。

    根据目标文件类型采用不同策略：

    - **CATProduct**：通过 COM 打开后，遍历 ``Product.Products`` 直接子层，
      收集每个子件的 ``ReferenceProduct.Parent.FullName``。
    - **CATDrawing**：通过 COM 打开后，遍历所有生成式视图的
      ``GenerativeBehavior.Document``，收集关联的零件/产品文档路径。
    - **其他格式**：不支持，直接返回空列表。

    参数
    ----------
    target_path:
        任意 CATIA 文档的绝对路径。
    progress_callback:
        可选的 ``callable(str)``，在搜索运行时使用状态消息调用。
    activate:
        为 ``True``（默认）时，若文档已打开则调用 ``Activate()`` 切换到前台，
        若未打开则调用 ``documents.Open()``。
        为 ``False`` 时，只在当前已打开的文档中查找目标文档，不激活、不打开；
        若目标文档未打开则直接返回空列表。适合在反向遍历场景中使用，
        避免批量查询时频繁切换前台文档。

    注意
    ----
    - 结果中不包含目标文件本身。
    - 路径比对使用小写，以兼容 Windows 文件系统大小写不敏感的特性。
    """

    target_lower = target_path.lower()
    ext_lower    = Path(target_path).suffix.lower()

    application = get_catia_v5_application()
    application.Visible = True
    documents   = application.Documents

    if activate:
        logger.info(f"find_dependencies: opening target {target_path}")
        if progress_callback:
            progress_callback("正在打开文件，请稍候…")
        open_catia_file(documents, target_path)  # 已打开则 Activate，未打开则 Open
    else:
        # 不激活：检查文档是否已打开，未打开则无法读取依赖，直接返回空列表
        is_open = any(
            documents.Item(i).FullName.lower() == target_lower
            for i in range(1, documents.Count + 1)
        )
        if not is_open:
            logger.debug(f"find_dependencies(activate=False): 文档未打开，跳过：{Path(target_path).name}")
            return []
        logger.debug(f"find_dependencies(activate=False): 文档已打开，读取依赖：{Path(target_path).name}")

    # ── CATProduct：只读直接子层（一级）─────────────────────────────
    if ext_lower == ".catproduct":
        if progress_callback:
            progress_callback("正在读取产品直接子件…")
        # 找到目标文档对象
        target_doc = None
        for i in range(1, documents.Count + 1):
            try:
                d = documents.Item(i)
                if d.FullName.lower() == target_lower:
                    target_doc = d
                    break
            except Exception:
                continue
        if target_doc is None:
            logger.warning("find_dependencies: 无法在 Documents 中定位目标 CATProduct")
            return []

        results: list[str] = []
        try:
            prods = target_doc.Product.Products
            for j in range(1, prods.Count + 1):
                try:
                    child    = prods.Item(j)
                    ref_name = child.ReferenceProduct.Parent.FullName
                    ref_lower = ref_name.lower()
                    if ref_lower != target_lower:
                        results.append(ref_name)
                except Exception as e:
                    logger.debug(f"find_dependencies: 子件 {j} 读取失败：{e}")
        except Exception as e:
            logger.debug(f"find_dependencies: Product.Products 访问失败：{e}")

        result = list(dict.fromkeys(results))  # 去重保序
        logger.info(
            f"find_dependencies (CATProduct): {len(result)} direct child(ren) for "
            f"{Path(target_path).name}"
        )
        return result

    # ── CATDrawing：遍历生成式视图链接 ────────────────────────────────
    elif ext_lower == ".catdrawing":
        if progress_callback:
            progress_callback("正在读取图纸视图链接…")
        results = _find_parts_via_file_links(target_path)
        logger.info(
            f"find_dependencies (CATDrawing): {len(results)} dep(s) for "
            f"{Path(target_path).name}"
        )
        return results

    # ── 其他格式：不支持 ──────────────────────────────────────────────
    else:
        logger.debug(
            f"find_dependencies: 不支持的文档格式，返回空列表：{Path(target_path).name}"
        )
        return []

def find_reverse_dependencies(
    target_path: str,
    progress_callback: Callable[[str], None] | None = None,
) -> list[str]:
    """反向查询：遍历当前已打开的文档，返回直接依赖了 *target_path* 的文档路径列表。

    对每个已打开文档调用 :func:`find_dependencies` 获取其直接依赖列表，
    检查目标路径是否在其中：
    - CATProduct ：``find_dependencies`` 返回其直接子层零件/产品路径列表
    - CATDrawing ：``find_dependencies`` 返回其视图链接的零件/产品路径列表
    - CATPart 及其他格式：无可用 COM 接口，跳过不查询

    典型用途：想删除某个零件前，先查哪些已打开文档直接引用了它。

    结果去重（保序），不包含目标文件本身。

    参数
    ----------
    target_path:
        被查询文件的绝对路径。
    progress_callback:
        可选的 ``callable(str)``，在遍历时传递进度信息。
    """

    application    = get_catia_v5_application()
    documents      = application.Documents
    target_lower   = target_path.lower()
    results: list[str] = []

    # 只有这两种格式的 find_dependencies 能返回有效依赖信息
    # CATPart 无可用 COM 接口读取引用，直接跳过
    _QUERYABLE_EXTS = (".catproduct", ".catdrawing")

    total = documents.Count
    for i in range(1, total + 1):
        try:
            doc       = documents.Item(i)
            full_name = doc.FullName
            fn_lower  = full_name.lower()

            # 跳过目标文件本身
            if fn_lower == target_lower:
                continue

            # 跳过系统库、材料库等非用户文档（.CATfct / .catalog / .cgr 等）
            if not fn_lower.endswith(_QUERYABLE_EXTS):
                logger.debug(f"find_reverse_dependencies: 跳过非查询格式：{Path(full_name).name}")
                continue

            if progress_callback:
                progress_callback(f"反向查询 {i}/{total}：{Path(full_name).name}")

            # 调用 find_dependencies 取该文档的直接依赖列表，检查 target 是否在其中
            # activate=False：不切换前台，仅读取已打开文档的依赖
            deps = find_dependencies(full_name, activate=False)
            if any(d.lower() == target_lower for d in deps):
                results.append(full_name)

        except Exception as e:
            logger.debug(f"find_reverse_dependencies: 文档 {i} 读取失败：{e}")

    result = list(dict.fromkeys(results))  # 去重保序
    logger.info(
        f"find_reverse_dependencies: {len(result)} referencing doc(s) for "
        f"{Path(target_path).name}"
    )
    return result


def find_part_for_drawing(
    drawing_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = SEARCH_MAX_LEVELS,
) -> list[str]:
    """给定图纸路径，返回所有匹配的 CATPart/CATProduct 文件路径列表。

    所有策略的结果拼接后去重（保序：优先级高的策略排在前面）。
    未找到任何结果时返回空列表。

    参数
    ----------
    drawing_path:
        ``.CATDrawing`` 的绝对路径。
    strategies:
        策略键列表；None 时使用 :data:`~catia_copilot.constants.DRAWING_SEARCH_STRATEGIES`。
    max_parent_levels:
        目录向上搜索的最大层级数（含图纸所在目录为第 0 层）。
    """
    if strategies is None:
        strategies = DRAWING_SEARCH_STRATEGIES

    drawing = Path(drawing_path)

    # 尝试读取图纸自定义参数 PartNumber（方法 1/2 的公共前置步骤，只做一次 COM 调用）
    pn_value: str | None = _read_drawing_pn_param(drawing_path)

    combined: list[str] = []
    for strategy in strategies:
        hits = _part_strategy(strategy, drawing_path, drawing, pn_value, max_parent_levels)
        for h in hits:
            logger.debug(f"find_part_for_drawing [{strategy}]: {h}")
        combined.extend(hits)

    result = list(dict.fromkeys(combined))  # 全局去重，保序
    logger.info(
        f"find_part_for_drawing: {len(result)} candidate(s) for {drawing.name}"
    )
    return result


def _read_drawing_pn_param(drawing_path: str) -> str | None:
    """尝试通过 COM 读取图纸的自定义参数 'PartNumber'。

    图纸必须已在 CATIA 中打开；若未打开或无该参数则返回 None。
    不会主动打开文件，无副作用。
    """
    try:
        application = get_catia_v5_application()
        documents   = application.Documents
        drawing_path_lower = drawing_path.lower()
        for i in range(1, documents.Count + 1):
            try:
                doc = documents.Item(i)
                if doc.FullName.lower() != drawing_path_lower:
                    continue
                return doc.Parameters.Item("PartNumber").Value
            except Exception:
                return None  # 找到了图纸文档但读不到参数
    except Exception:
        pass
    return None


def _part_strategy(
    strategy: str,
    drawing_path: str,
    drawing: Path,
    pn_value: str | None,
    max_parent_levels: int,
) -> list[str]:
    """执行单条查找策略，返回所有命中的 CATPart/CATProduct 路径列表。"""

    if strategy == "pn_param_open_docs":
        # 方法 1：读图纸 Parameters["PartNumber"]，在已打开文档中匹配
        # doc.Product.PartNumber == pn_value 的零件/产品
        if not pn_value:
            return []
        try:
            hit = _find_part_in_open_docs(pn_value)
            return [hit] if hit else []
        except Exception as e:
            logger.debug(f"pn_param_open_docs strategy failed: {e}")
            return []

    elif strategy == "pn_param_scan_dirs":
        # 方法 2：读图纸 Parameters["PartNumber"]，在向上 N 级目录中找
        # 文件名（stem）== pn_value 的 .CATPart/.CATProduct
        if not pn_value:
            return []
        return _find_part_by_stem_in_dirs(drawing, pn_value.strip(), max_parent_levels)

    elif strategy == "same_name_scan_dirs":
        # 方法 3：用图纸文件名 stem 在向上 N 级目录中找同名零件文件
        return _find_part_by_stem_in_dirs(drawing, drawing.stem, max_parent_levels)

    elif strategy == "strip_prefix_scan_dirs":
        # 方法 4：strip 图纸文件名前缀后，在向上 N 级目录中找同名零件文件
        stripped = _strip_drawing_prefix(drawing.stem)
        if not stripped:
            return []
        return _find_part_by_stem_in_dirs(drawing, stripped, max_parent_levels)

    elif strategy == "doc_file_links":
        # 方法 5：通过 COM 读取图纸生成式视图的 GenerativeBehavior.Document，
        # 过滤出 .CATPart/.CATProduct。图纸须已在 CATIA 中打开。
        try:
            return _find_parts_via_file_links(drawing_path)
        except Exception as e:
            logger.debug(f"doc_file_links strategy failed: {e}")
            return []

    else:
        logger.warning(f"find_part_for_drawing: 未知策略 '{strategy}'，已跳过。")
        return []


# ---------------------------------------------------------------------------
# 启发式 2A：给定 CATDrawing，查找对应的 CATPart / CATProduct
# ---------------------------------------------------------------------------

def _find_part_in_open_docs(pn_value: str) -> str | None:
    """方法 1 实现：在已打开文档中找 Product.PartNumber == pn_value 的零件/产品。

    通过 COM 直接读取 doc.Product.PartNumber（不使用 pycatia）。
    只检查已打开文档，无副作用。
    """

    application = get_catia_v5_application()
    documents   = application.Documents
    pn_value    = pn_value.strip()

    for i in range(1, documents.Count + 1):
        try:
            doc       = documents.Item(i)
            full_name = doc.FullName
            # 只考虑零件/产品文档（大小写不敏感扩展名判断）
            if not full_name.lower().endswith(tuple(e.lower() for e in _PART_EXTS)):
                continue
            try:
                doc_pn = doc.Product.PartNumber
            except Exception:
                continue
            if doc_pn and doc_pn.strip() == pn_value:
                return full_name
        except Exception:
            continue

    return None


def _find_parts_via_file_links(drawing_path: str) -> list[str]:
    """方法 5 实现：遍历图纸所有生成式视图的 GenerativeBehavior.Document，
    收集被指向的零件/产品文档路径。

    图纸须已在 CATIA 中打开，否则返回空列表。
    每个视图通过 view.GenerativeBehavior.Document 得到 Product 对象，
    再经 product.ReferenceProduct.Parent.FullName 取到文档绝对路径。
    过滤出 .CATPart / .CATProduct ，结果去重（保序）后返回。
    """

    application = get_catia_v5_application()
    documents   = application.Documents

    # 找到图纸的 COM 文档对象（不主动打开，大小写不敏感路径比对）
    drawing_path_lower = drawing_path.lower()
    doc = None
    for i in range(1, documents.Count + 1):
        try:
            d = documents.Item(i)
            if d.FullName.lower() == drawing_path_lower:
                doc = d
                break
        except Exception:
            continue

    if doc is None:
        logger.debug(f"doc_file_links: 图纸未在 CATIA 中打开：{Path(drawing_path).name}")
        return []

    results: list[str] = []
    try:
        root = doc.DrawingRoot
        for si in range(1, root.Sheets.Count + 1):
            sheet = root.Sheets.Item(si)
            for vi in range(1, sheet.Views.Count + 1):
                view = sheet.Views.Item(vi)
                try:
                    if not view.IsGenerative:
                        continue
                except Exception:
                    continue
                try:
                    product   = view.GenerativeBehavior.Document
                    full_name = product.ReferenceProduct.Parent.FullName
                    if full_name.lower().endswith(tuple(e.lower() for e in _PART_EXTS)):
                        results.append(full_name)
                except Exception:
                    # 视图无关联文档（CATIA 正常情况），静默跳过
                    continue
    except Exception as e:
        logger.debug(f"doc_file_links: 遍历图纸结构失败：{e}")

    # 去重（保序）
    return list(dict.fromkeys(results))


# ---------------------------------------------------------------------------
# 公共辅助：目录向上 N 层祖先 + rglob 搜索
# ---------------------------------------------------------------------------

# Windows 路径 parts 示例：('D:\\', 'foo') = 2，('D:\\',) = 1
# 设为 2：只阻止爬到盘符本身（'D:\\'），允许最浅到 D:\foo 这一级
_MIN_SEARCH_ROOT_DEPTH: int = 2


def _get_ancestor_dir(start_dir: Path, max_parent_levels: int) -> Path:
    """返回 start_dir 向上最多 max_parent_levels 层的祖先目录。

    层 0 = start_dir，层 1 = start_dir.parent，…
    安全限制：祖先目录的路径深度（parts 数量）不得少于 _MIN_SEARCH_ROOT_DEPTH，
    防止在文件层级较浅时爬到盘符/盘根并触发全盘 rglob。
    """
    current = start_dir
    for _ in range(max_parent_levels):
        parent = current.parent
        if parent == current:                          # 已到文件系统根目录
            break
        if len(parent.parts) < _MIN_SEARCH_ROOT_DEPTH:
            break                                      # 再往上会进入盘根，停止
        current = parent
    return current


def _find_part_by_stem_in_dirs(
    drawing: Path,
    stem: str,
    max_parent_levels: int,
) -> list[str]:
    """在图纸向上 max_parent_levels 层的祖先目录整棵子树中查找文件名（stem）== stem 的零件文件。

    返回所有命中路径的列表，按路径深度由浅到深排序（越靠近祖先目录越优先）。
    同深度内优先返回 .CATPart ，其次 .CATProduct 。
    """
    root = _get_ancestor_dir(drawing.parent, max_parent_levels)
    candidates: list[Path] = []
    for ext in _PART_EXTS:
        candidates.extend(root.rglob(f"{stem}{ext}"))

    # 按路径深度排序，深度相同时 .CATPart 在前
    ext_order = {ext: i for i, ext in enumerate(_PART_EXTS)}
    candidates.sort(key=lambda p: (len(p.parts), ext_order.get(p.suffix, 99)))

    # 去重（保序），直接用 str(p)——运行在 Windows，路径已是绝对路径
    return list(dict.fromkeys(str(p) for p in candidates if p.exists()))


# ---------------------------------------------------------------------------
# 方法 4 辅助：strip 图纸文件名前缀
# ---------------------------------------------------------------------------

# 匹配"非空前缀 + 第一个 _ 或 - + 剩余部分"
# 前缀为空（文件名以 _ 或 - 开头）时不匹配，视为无前缀，不处理。
_PREFIX_SEP_RE = re.compile(r'^.+?[_\-](.+)$')


def _strip_drawing_prefix(drawing_stem: str) -> str | None:
    """Strip 掉图纸文件名最左侧的"前缀_"或"前缀-"，返回剩余部分。

    若文件名中不含 _ 或 -，或前缀为空（以分隔符开头），则返回 None。

    示例：
      "DRW_PartA"   → "PartA"
      "ASM-Sub_001" → "Sub_001"   （只去最左侧前缀）
      "A_B_C"       → "B_C"
      "PartA"       → None
    """
    m = _PREFIX_SEP_RE.match(drawing_stem)
    return m.group(1) if m else None


# ===========================================================================
# 启发式 2B：给定 CATPart/CATProduct，查找对应的 CATDrawing
# ===========================================================================

def find_drawing_for_part(
    part_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = SEARCH_MAX_LEVELS,
) -> list[str]:
    """给定零件/产品路径，返回所有匹配的 CATDrawing 文件路径列表。

    所有策略的结果拼接后去重（保序：优先级高的策略排在前面）。
    未找到任何结果时返回空列表。

    参数
    ----------
    part_path:
        ``.CATPart`` 或 ``.CATProduct`` 的绝对路径。
    strategies:
        策略键列表；None 时使用 :data:`~catia_copilot.constants.PART_TO_DRAWING_STRATEGIES`。
    max_parent_levels:
        目录向上搜索的最大层级数（含零件所在目录为第 0 层）。
    """
    if strategies is None:
        strategies = PART_TO_DRAWING_STRATEGIES

    part = Path(part_path)

    # 预先读取零件的 Product.PartNumber（方法 1/2 需要，只做一次 COM 调用）
    part_pn: str | None = _read_part_pn(part_path)

    combined: list[str] = []
    for strategy in strategies:
        hits = _drawing_strategy(strategy, part_path, part, part_pn, max_parent_levels)
        for h in hits:
            logger.debug(f"find_drawing_for_part [{strategy}]: {h}")
        combined.extend(hits)

    result = list(dict.fromkeys(combined))  # 全局去重，保序
    logger.info(
        f"find_drawing_for_part: {len(result)} candidate(s) for {part.name}"
    )
    return result


def _read_part_pn(part_path: str) -> str | None:
    """通过 COM 读取零件/产品文档的原生 Product.PartNumber。

    零件必须已在 CATIA 中打开；若未打开或读取失败则返回 None。不会主动打开文件。
    """
    try:
        application = get_catia_v5_application()
        documents   = application.Documents
        part_path_lower = part_path.lower()
        for i in range(1, documents.Count + 1):
            try:
                doc = documents.Item(i)
                if doc.FullName.lower() != part_path_lower:
                    continue
                return doc.Product.PartNumber
            except Exception:
                return None
    except Exception:
        pass
    return None


def _drawing_strategy(
    strategy: str,
    part_path: str,
    part: Path,
    part_pn: str | None,
    max_parent_levels: int,
) -> list[str]:
    """执行单条给零件找图纸的策略，返回所有命中的 CATDrawing 路径列表。"""

    if strategy == "pn_param_open_drws":
        # 方法 1：遍历已打开 CATDrawing，找 Parameters["PartNumber"] == 零件 PartNumber 的图纸
        if not part_pn:
            return []
        try:
            hit = _find_drawing_by_part_pn(part_pn.strip())
            return [hit] if hit else []
        except Exception as e:
            logger.debug(f"pn_param_open_drws strategy failed: {e}")
            return []

    elif strategy == "pn_param_scan_drws":
        # 方法 2：在向上 N 级目录中找文件名（stem）== 零件 Product.PartNumber 的 .CATDrawing
        if not part_pn:
            return []
        try:
            return _find_drawing_by_stem_in_dirs(part, part_pn.strip(), max_parent_levels)
        except Exception as e:
            logger.debug(f"pn_param_scan_drws strategy failed: {e}")
            return []

    elif strategy == "same_name_scan_dirs":
        # 方法 3：在向上 N 级目录中找文件名（stem）== 零件 stem 的 .CATDrawing
        return _find_drawing_by_stem_in_dirs(part, part.stem, max_parent_levels)

    elif strategy == "strip_prefix_scan_dirs":
        # 方法 4：扫描向上 N 级目录内的 .CATDrawing，strip 图纸文件名前缀后与零件 stem 比较
        return _find_drawing_strip_prefix_in_dirs(part, part.stem, max_parent_levels)

    elif strategy == "doc_file_links":
        # 方法 5：遍历已打开的 CATDrawing，反查其生成式视图链接是否指向该零件/产品
        try:
            return _find_drawings_via_part_link(part_path)
        except Exception as e:
            logger.debug(f"doc_file_links strategy failed: {e}")
            return []

    else:
        logger.warning(f"find_drawing_for_part: 未知策略 '{strategy}'，已跳过。")
        return []


# ---------------------------------------------------------------------------
# 层面 2B 策略实现
# ---------------------------------------------------------------------------

def _find_drawing_by_part_pn(part_pn: str) -> str | None:
    """方法 1 实现：在已打开 CATDrawing 中找 Parameters["PartNumber"] == part_pn 的图纸。

    只检查已打开文档，无副作用。Parameters["PartNumber"] 可能不存在，跳过即可。
    """

    application = get_catia_v5_application()
    documents   = application.Documents

    for i in range(1, documents.Count + 1):
        try:
            doc       = documents.Item(i)
            full_name = doc.FullName
            if not full_name.lower().endswith(".catdrawing"):
                continue
            try:
                pn_value = doc.Parameters.Item("PartNumber").Value
            except Exception:
                continue  # 该图纸无 PartNumber 参数，跳过
            if pn_value and pn_value.strip() == part_pn:
                return full_name
        except Exception:
            continue

    return None


def _find_drawing_by_stem_in_dirs(
    part: Path,
    stem: str,
    max_parent_levels: int,
) -> list[str]:
    """在零件向上 max_parent_levels 层的祖先目录整棵子树中找 stem 相同的 .CATDrawing 。

    返回所有命中路径列表，按路径深度由浅到深排序（越靠近祖先目录越优先）。
    """
    root = _get_ancestor_dir(part.parent, max_parent_levels)
    candidates = list(root.rglob(f"{stem}.CATDrawing"))
    candidates.sort(key=lambda p: len(p.parts))

    # 去重（保序），直接用 str(p)——运行在 Windows，路径已是绝对路径
    return list(dict.fromkeys(str(p) for p in candidates if p.exists()))


def _find_drawing_strip_prefix_in_dirs(
    part: Path,
    part_stem: str,
    max_parent_levels: int,
) -> list[str]:
    """在零件向上 max_parent_levels 层的祖先目录整棵子树中扫描 .CATDrawing ，
    strip 图纸文件名前缀后与 part_stem 比较。

    返回所有命中路径列表，按路径深度由浅到深排序（越靠近祖先目录越优先）。
    """
    root = _get_ancestor_dir(part.parent, max_parent_levels)
    candidates: list[Path] = []

    for drw in root.rglob("*.CATDrawing"):
        stripped = _strip_drawing_prefix(drw.stem)
        if stripped == part_stem:
            candidates.append(drw)

    candidates.sort(key=lambda p: len(p.parts))

    # 去重（保序），直接用 str(p)——运行在 Windows，路径已是绝对路径
    return list(dict.fromkeys(str(p) for p in candidates if p.exists()))


def _find_drawings_via_part_link(part_path: str) -> list[str]:
    """方法 5（2B）实现：遍历已打开的 CATDrawing ，反查其生成式视图是否链接到 part_path。

    对每张已打开图纸调用 _find_parts_via_file_links()，若结果中包含目标零件/产品路径，
    则将该图纸加入返回列表。
    图纸须已在 CATIA 中打开；若无已打开图纸则返回空列表。
    """

    application   = get_catia_v5_application()
    documents     = application.Documents
    part_lower    = part_path.lower()
    results: list[str] = []

    for i in range(1, documents.Count + 1):
        try:
            doc       = documents.Item(i)
            full_name = doc.FullName
            if not full_name.lower().endswith(".catdrawing"):
                continue
            # 该图纸的所有视图链接指向的零件列表
            linked_parts = _find_parts_via_file_links(full_name)
            if any(p.lower() == part_lower for p in linked_parts):
                results.append(full_name)
        except Exception:
            continue

    return list(dict.fromkeys(results))
