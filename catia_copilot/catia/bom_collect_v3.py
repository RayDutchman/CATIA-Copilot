"""
BOM 数据收集 V3 模块（part_master / instance 分离架构）。

设计原则：
- part_master 是装配树的节点，instances 存该 part_master 内部的直接子实例列表。
- 同一 pm_key 只建一份 part_master，所有引用共享同一份 instances。
  （CATIA 中 product.ReferenceProduct.Products 是文件视角，
   通过 Product3.1 或 Product3.2 导航到的 Part1.x COM 对象底层相同，
   修改任意一个的 Name 全部同步。）
- inst_info["product"] 持有 COM 引用防止 GC，inst_info["instance_name"] 是唯一真相。
- inst_key_to_info 提供 O(1) 反向索引。

pm_key 规则：
- 独立文件节点（Part / Product）：pm_key = part_number
- 嵌入部件（Component，filepath == parent_filepath）：
    pm_key = f"{part_number}:{host_file_pn}"
    host_file_pn 是宿主独立文件的 PartNumber（即包含该 Component 的 .CATProduct 的 pn）。
  同一宿主文件内 PartNumber 唯一（CATIA 约束），因此 pm_key 全局唯一。
  多层嵌套 Component 共享同一宿主文件，host_file_pn 透传不变，不会链式叠加。
  分隔符使用 ":" （冒号），CATIA PartNumber 不允许包含 ":"（Windows 文件名禁用字符），绝无歧义。

数据结构：

    part_master = {
        "part_number":  str,   # CATIA PartNumber 属性值（显示、写回用）
        "pm_key":      str,   # part_masters 字典的唯一 key（查找用）
        "nomenclature": str,
        "revision":     str,
        "definition":   str,
        "source":       str,   # CATIA 原始值 "0"/"1"/"2"
        "description":  str,
        # + 用户自定义列（dict 中额外 key）
        "type":         str,   # BomNodeType 英文 key
        "filename":     str,
        "filepath":     str,
        "_not_found":   bool,
        "_no_file":     bool,
        "_unreadable":  bool,
        "instances": [         # 该 part_master 内部的直接子实例列表（文件视角，唯一一份）
            {
                "inst_key":      int,    # id(product)，任取一个 Python wrapper 的 id
                "pn":            str,    # 子节点的 CATIA PartNumber（显示用）
                "pm_key":       str,    # 子节点的 pm_key（查 part_masters 用）
                "instance_name": str,    # product.Name，实例名（每个实例在父装配中唯一）
                "description_inst": str, # product.DescriptionInst，实例级描述（每个实例独立，可为空）
                "product":       object, # COM 引用，防 GC、写回实例名用
                "placement":     None,   # 4×4 变换矩阵（mass props 用）
            },
            ...
        ]
    }

提供：
- collect_bom_part_masters()  – 遍历 CATIA 产品树，返回
                                (root_pm_key, part_masters, inst_key_to_info)
- iter_full_rows()            – 完整 BOM（每实例一行）
- iter_hierarchical_rows()    – 层级 BOM（同父节点同 pm_key 合并）
- flatten_bom_to_summary()    – 汇总 BOM（复用 bom_collect）
- get_part_master_attr()      – O(1) 读属性
- set_part_master_attr()      – O(1) 写属性
- rename_part_master()        – 统一处理 PN 改名
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from catia_copilot.constants import (
    FILENAME_NOT_FOUND,
    FILENAME_UNSAVED,
    BomNodeType,
    PRODUCT_ATTR_READ_MAP,
    CATIA_DESIGN_MODE,
)
from catia_copilot.catia.bom_collect import flatten_bom_to_summary  # noqa: F401
from catia_copilot.catia.connection import get_catia_v5_application, wrap_product
from catia_copilot.catia.document import get_bom_node_type

logger = logging.getLogger(__name__)

# 列名 → part_master dict key 映射（标准列）
_COL_TO_PM_KEY: dict[str, str] = {
    "Part Number":  "part_number",
    "Nomenclature": "nomenclature",
    "Revision":     "revision",
    "Definition":   "definition",
    "Source":       "source",
    "Description":  "description",
    "Type":         "type",
    "Filename":     "filename",
    "Filepath":     "filepath",
}

_WRITABLE_COLS:    frozenset[str] = frozenset(
    {"Part Number", "Nomenclature", "Revision", "Definition", "Source", "Description"}
)
_READONLY_PM_COLS: frozenset[str] = frozenset({"Type", "Filename", "Filepath"})


# ---------------------------------------------------------------------------
# 收集配置（嵌套 dataclass，每项独立可控，扩展时只加字段不改调用签名）
# ---------------------------------------------------------------------------

@dataclass
class MatrixCollectConfig:
    """局部变换矩阵（placement）收集配置。

    placement 含义：子实例相对直接父节点的 4×4 行主序变换矩阵，平移单位 mm。
    格式与 _local_position_to_mat4() 返回值一致：
        [[ R[0][0]  R[0][1]  R[0][2]  Tx ],
         [ R[1][0]  R[1][1]  R[1][2]  Ty ],
         [ R[2][0]  R[2][1]  R[2][2]  Tz ],
         [    0        0        0      1  ]]
    这是**局部**坐标（不累乘父矩阵），与 mass_props_collect 的 _placement（绝对坐标）不同。

    开销：每个子实例一次 pycatia position.get_components() COM 调用（约 1–5 ms/实例）。
    使用场景：PLM 同步 cadInstances 写入。
    """
    enabled: bool = False


@dataclass
class MassPropsCollectConfig:
    """质量特性收集配置（预留框架，当前不收集）。

    未来扩展字段示例（勿提前添加，等实际需求驱动）：
        source: str = "analyze"        # "analyze" | "keep_inertia"
        read_mode: str = "all"         # "first" | "last" | "all"
        skip_hidden: bool = False      # 是否跳过隐藏实例

    当 enabled=True 但内部实现尚未完成时，collect_bom_part_masters 将记录警告并跳过。
    """
    enabled: bool = False


@dataclass
class CollectConfig:
    """collect_bom_part_masters 的可选收集项配置。

    设计原则：
    - 每类收集项封装为独立子 dataclass，子 dataclass 含 enabled 和自身参数。
    - 默认全部 disabled，确保现有调用方（bom_edit_dialog_v3 等）零改动。
    - 扩展时只在对应子 dataclass 或此处新增字段，调用方签名不变。

    典型用法：
        # BOM 编辑（只需属性，不需位置）
        cfg = CollectConfig()   # 全部默认 False

        # PLM 同步（需要位置信息）
        cfg = CollectConfig(placement=MatrixCollectConfig(enabled=True))

        # 未来：同步 + 质量特性
        cfg = CollectConfig(
            placement=MatrixCollectConfig(enabled=True),
            mass_props=MassPropsCollectConfig(enabled=True, source="analyze"),
        )
    """
    placement:  MatrixCollectConfig   = field(default_factory=MatrixCollectConfig)
    mass_props: MassPropsCollectConfig = field(default_factory=MassPropsCollectConfig)


# ---------------------------------------------------------------------------
# 位置矩阵读取辅助（模块私有）
# ---------------------------------------------------------------------------

def _local_position_to_mat4(product) -> list[list[float]]:
    """从 win32com Product 对象读取局部变换矩阵，返回 4×4 行主序列表。

    "局部"含义：该实例相对**直接父节点**的变换，不累乘父矩阵。
    与 mass_props_collect._position_to_mat4 的矩阵格式完全一致，
    但语义不同（后者在遍历时会与父矩阵累乘得到绝对坐标）。

    CATIA Position.GetComponents 输出布局（列主序，12 元素）：
        arr[0..2]  = X 轴方向向量（旋转矩阵第 1 列）
        arr[3..5]  = Y 轴方向向量（旋转矩阵第 2 列）
        arr[6..8]  = Z 轴方向向量（旋转矩阵第 3 列）
        arr[9..11] = 原点平移向量 (Tx, Ty, Tz)，单位 mm

    重排为行主序 4×4：
        mat[i][j] = arr[j*3 + i]   (旋转部分，i,j ∈ 0..2)
        mat[i][3] = arr[9 + i]     (平移部分)
        mat[3]    = [0, 0, 0, 1]   (齐次行)

    失败时返回 4×4 单位矩阵（不抛异常）。
    """
    try:
        arr = wrap_product(product).position.get_components()
    except Exception as exc:
        logger.debug("_local_position_to_mat4: get_components 失败（%s），返回单位矩阵", exc)
        return _identity_4x4()

    if arr is None or len(arr) < 12:
        logger.debug("_local_position_to_mat4: arr 无效（len=%s），返回单位矩阵",
                     len(arr) if arr is not None else None)
        return _identity_4x4()

    return [
        [arr[0], arr[3], arr[6], arr[9] ],
        [arr[1], arr[4], arr[7], arr[10]],
        [arr[2], arr[5], arr[8], arr[11]],
        [0.0,    0.0,    0.0,    1.0    ],
    ]


def _identity_4x4() -> list[list[float]]:
    """返回 4×4 单位矩阵（无旋转、无平移的恒等变换）。"""
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


# ---------------------------------------------------------------------------
# 核心收集函数
# ---------------------------------------------------------------------------

def collect_bom_part_masters(
    file_path: str | None,
    columns: list[str],
    custom_columns: list[str],
    progress_callback: Callable[[int], None] | None = None,
    config: CollectConfig | None = None,
) -> tuple[str, dict[str, dict], dict[int, dict]]:
    """遍历 CATIA 产品树，构建 part_masters 树和 inst_key_to_info 反向索引。

    返回 (root_pm_key, part_masters, inst_key_to_info)。

    root_pm_key:
        根产品的 pm_key（== root PartNumber，根节点必为独立文件）。
        遍历装配结构从 part_masters[root_pm_key]["instances"] 开始。

    part_masters:
        dict[pm_key → part_master dict]。
        每个 part_master 含属性字段和 instances 列表。
        instances 是该 part_master 内部的直接子实例列表（文件视角，唯一一份）。
        同一 pm_key 只建一份，所有引用共享。

    inst_key_to_info:
        dict[id(product) → inst_info dict]，O(1) 反向索引。
        inst_info 是 part_master["instances"] 中的同一对象引用。
        修改 inst_info["instance_name"] 即同步修改 part_masters 树。

    config:
        CollectConfig 实例，控制可选字段的收集行为。
        默认 None 等价于 CollectConfig()（全部 disabled），确保现有调用方零改动。
        各字段说明见 CollectConfig 及子 dataclass 的文档。
    """
    if config is None:
        config = CollectConfig()

    if config.mass_props.enabled:
        logger.warning(
            "collect_bom_part_masters: config.mass_props.enabled=True 但质量特性收集"
            "尚未在 v3 中实现，已跳过。请继续使用 mass_props_collect.collect_mass_props_rows()。"
        )
    extra_cols = [c for c in custom_columns
                  if c not in _WRITABLE_COLS and c not in _READONLY_PM_COLS]

    _props_cache:     dict[str, dict] = {}
    part_masters:     dict[str, dict] = {}
    inst_key_to_info: dict[int, dict] = {}
    _total_count                      = 0

    # ── 属性读取辅助 ──────────────────────────────────────────────────────────
    def _prop_targets(product):
        try:
            yield product.ReferenceProduct
        except Exception:
            pass
        yield product

    def _get_prop(product, name: str) -> str:
        attr = PRODUCT_ATTR_READ_MAP.get(name)
        if not attr:
            return ""
        for target in _prop_targets(product):
            try:
                v = getattr(target, attr, None)
                if v is not None:
                    return str(v)
            except Exception:
                pass
        return ""

    def _get_user_prop(product, name: str) -> str:
        for target in _prop_targets(product):
            try:
                v = target.UserRefProperties.Item(name).Value
                if v is not None and str(v).strip():
                    return str(v)
            except Exception:
                pass
        return ""

    # ── 主遍历 ────────────────────────────────────────────────────────────────
    def _traverse(product, level: int, parent_filepath: str, host_file_pn: str,
                  _hint_pn: str = "", _hint_filepath: str | None = None) -> str:
        """遍历单个产品节点，确保其 part_master 已建立（含 instances）。

        返回该节点的 pm_key（供父节点将其加入自己的 instances 列表）。
        同一 pm_key 只处理一次：part_master 已存在则直接返回，不重复遍历子节点。

        _hint_pn:       父节点侧已读取的 PartNumber，省一次 COM 调用。
        _hint_filepath: 父节点侧已读取的 filepath，省一次 ReferenceProduct.Parent.FullName。
        """
        nonlocal _total_count

        # ── PartNumber（优先用父节点传入的 hint，省一次 COM）─────────────────
        if _hint_pn:
            pn = _hint_pn
        else:
            try:
                pn = str(product.PartNumber)
            except Exception:
                name = product.Name
                pn   = name.rsplit(".", 1)[0] if "." in name else name

        # ── 文件路径（优先用父节点传入的 hint，省一次 COM）──────────────────
        if _hint_filepath is not None:
            filepath = _hint_filepath
        else:
            try:
                filepath = product.ReferenceProduct.Parent.FullName
            except Exception:
                filepath = ""

        not_found   = not bool(filepath)
        no_file     = bool(filepath) and not Path(filepath).exists()
        is_embedded = (bool(filepath) and bool(parent_filepath)
                       and filepath == parent_filepath)

        # ── pm_key 计算 ──────────────────────────────────────────────────────
        # 独立文件节点：pm_key = pn
        # 嵌入部件：pm_key = "pn:host_file_pn"（宿主文件内 pn 唯一，多层嵌套宿主相同，不叠加）
        pm_key = f"{pn}:{host_file_pn}" if is_embedded else pn

        # 同一 pm_key 只建一次：已存在则直接返回，不重复遍历
        if pm_key in part_masters:
            _total_count += 1
            if progress_callback is not None:
                progress_callback(_total_count)
            return pm_key

        # ── 读取 PartMaster 级属性（带文件级缓存）────────────────────────────
        cached      = not is_embedded and bool(filepath) and filepath in _props_cache
        is_readable = True

        if not_found:
            props = {col: "" for col in columns}
        elif not cached:
            try:
                if product.GetWorkMode() != CATIA_DESIGN_MODE:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
            except Exception:
                try:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
                except Exception:
                    is_readable = False

            props = {}
            for col in columns:
                if col in PRODUCT_ATTR_READ_MAP:
                    props[col] = _get_prop(product, col)
                elif col in custom_columns:
                    props[col] = _get_user_prop(product, col)

            if filepath and not is_embedded:
                _props_cache[filepath] = props
        else:
            props = _props_cache[filepath]

        # ── 节点类型 ──────────────────────────────────────────────────────────
        try:
            node_type = get_bom_node_type(product, parent_filepath, filepath=filepath)
        except Exception:
            node_type = ""

        # ── 建立 part_master ──────────────────────────────────────────────────
        part_masters[pm_key] = {
            "part_number":  pn,           # CATIA 属性值，显示/写回用
            "pm_key":      pm_key,      # 唯一查找 key
            "host_file_pn": host_file_pn if is_embedded else "",
            # host_file_pn：嵌入部件所属宿主文件的 pn（独立文件节点为空串）。
            # 用于冲突检测：同一宿主文件内所有 Component 的 host_file_pn 相同。
            # rename_part_master 改宿主 pn 时会同步更新所有子 Component 的此字段。
            "nomenclature": str(props.get("Nomenclature", "")),
            "revision":     str(props.get("Revision", "")),
            "definition":   str(props.get("Definition", "")),
            "source":       str(props.get("Source", "")),
            "description":  str(props.get("Description", "")),
            **{col: str(props.get(col, "")) for col in extra_cols},
            "type":         node_type,
            "filename":     (FILENAME_UNSAVED   if no_file   else
                             Path(filepath).name if filepath  else
                             FILENAME_NOT_FOUND),
            "filepath":     filepath,
            "_not_found":   not_found,
            "_no_file":     no_file,
            "_unreadable":  not is_readable,
            "_product":     product,   # COM 引用，写回根产品属性用（根节点无 inst_info）
            "instances":    [],        # 下方填充直接子实例
        }

        _total_count += 1
        if progress_callback is not None:
            progress_callback(_total_count)

        # ── 遍历子节点 ────────────────────────────────────────────────────────────
        #
        # 设计原则：
        #   属性去重（pm_key 计算、_traverse 递归）走文件视角（ReferenceProduct.Products）：
        #     同一零件无论有多少个实例，属性只读一次，所有实例共享一个 part_master。
        #
        #   实例名（instance_name）和局部变换矩阵（placement）是实例级数据，
        #   必须从实例视角（product.Products）读取：
        #     - instance_name = child_inst.Name（每个实例在父装配中有独立的实例名）
        #     - placement = Position.GetComponents()（每个实例有独立的放置位置）
        #   两者来自同一个 child_inst 对象，在同一步骤中一并读取，无需额外循环。
        #
        # CATIA 保证 product.Products 与 product.ReferenceProduct.Products
        # 的 Count 相同、下标 i 一一对应同一个子节点，因此可以并行使用两个视角。
        is_assembly = (node_type in BomNodeType.ASSEMBLY_TYPES or level == 0)
        if is_assembly:
            try:
                # 文件视角：驱动属性读取 / pm_key / _traverse 递归（主循环）
                try:
                    ref_products = product.ReferenceProduct.Products
                except Exception:
                    ref_products = product.Products

                # 实例视角：读取 instance_name 和 placement
                # 仅在需要时获取；获取失败则回退到文件视角（Name 仍可用，placement 退化为单位矩阵）
                try:
                    inst_products = product.Products
                    if inst_products.Count != ref_products.Count:
                        logger.warning(
                            "_traverse: inst_products.Count(%d) != ref_products.Count(%d)"
                            "，回退到文件视角，instance_name 和 placement 可能不准确",
                            inst_products.Count, ref_products.Count,
                        )
                        inst_products = None
                except Exception as exc:
                    logger.debug("_traverse: 无法获取 inst_products（%s），回退文件视角", exc)
                    inst_products = None

                child_host = host_file_pn if is_embedded else pn

                for i in range(1, ref_products.Count + 1):
                    try:
                        # 文件视角：用于 PartNumber / ReferenceProduct.Parent.FullName / _traverse
                        child = ref_products.Item(i)

                        # 实例视角：用于 instance_name 和 placement（两者同源，一步读取）
                        child_inst = None
                        if inst_products is not None:
                            try:
                                child_inst = inst_products.Item(i)
                            except Exception as exc:
                                logger.debug(
                                    "_traverse: inst_products.Item(%d) 失败（%s）", i, exc
                                )

                        # ── PartNumber / filepath（文件视角，用于 pm_key 和属性去重）─────
                        try:
                            child_pn_raw = str(child.PartNumber)
                        except Exception:
                            n = child.Name
                            child_pn_raw = n.rsplit(".", 1)[0] if "." in n else n

                        try:
                            child_filepath = child.ReferenceProduct.Parent.FullName
                        except Exception:
                            child_filepath = ""

                        child_is_embedded = (bool(child_filepath) and bool(filepath)
                                             and child_filepath == filepath)
                        child_pm_key_candidate = (f"{child_pn_raw}:{child_host}"
                                                   if child_is_embedded else child_pn_raw)

                        if child_pm_key_candidate in part_masters:
                            child_pm_key = child_pm_key_candidate
                            _total_count += 1
                            if progress_callback is not None:
                                progress_callback(_total_count)
                        else:
                            child_pm_key = _traverse(
                                child, level + 1, filepath, child_host,
                                _hint_pn=child_pn_raw,
                                _hint_filepath=child_filepath,
                            )

                        child_pn = part_masters[child_pm_key]["part_number"]

                        # ── instance_name 和 description_inst 和 placement：同源自实例视角 child_inst ──────
                        # child_inst 不可用时回退到文件视角 child（Name 相同，placement 退化）
                        inst_obj      = child_inst if child_inst is not None else child
                        instance_name = inst_obj.Name
                        # DescriptionInst 是实例级描述，仅在实例视角有效；读取失败静默置空
                        try:
                            description_inst = str(inst_obj.DescriptionInst or "")
                        except Exception:
                            description_inst = ""
                        placement     = (
                            _local_position_to_mat4(inst_obj)
                            if config.placement.enabled
                            else None
                        )

                        inst_key  = id(inst_obj)
                        inst_info = {
                            "inst_key":         inst_key,
                            "pn":               child_pn,
                            "pm_key":           child_pm_key,
                            "instance_name":    instance_name,
                            "description_inst": description_inst,
                            "product":          child,      # 文件视角 COM 引用，防 GC、写回属性用
                            "placement":        placement,  # 局部变换矩阵（行主序 4×4，mm），或 None
                        }
                        part_masters[pm_key]["instances"].append(inst_info)
                        inst_key_to_info[inst_key] = inst_info
                    except Exception as exc:
                        logger.debug("_traverse child error level=%d i=%d: %s",
                                     level, i, exc)
            except Exception:
                pass

        return pm_key

    # ── CATIA 连接与根节点 ────────────────────────────────────────────────────
    application = get_catia_v5_application()

    if file_path is None:
        root_product = application.ActiveDocument.Product
    else:
        from catia_copilot.utils import open_catia_file  # noqa: PLC0415
        root_product = open_catia_file(application.Documents, file_path).Product

    # 根节点始终是独立文件，pm_key == pn，host_file_pn 传 "" 即可（根节点不是嵌入部件）
    root_pm_key = _traverse(root_product, level=0, parent_filepath="", host_file_pn="")

    return root_pm_key, part_masters, inst_key_to_info


# ---------------------------------------------------------------------------
# 视图生成：完整 BOM（每实例一行，深度优先前序）
# ---------------------------------------------------------------------------

def iter_full_rows(
    root_pm_key: str,
    part_masters: dict[str, dict],
) -> list[dict]:
    """从 part_masters 树生成完整 BOM 行列表（每实例一行）。

    根节点（level=0）输出一行，instance_name 为空。
    之后深度优先前序遍历所有实例。同一 part_master 的 instances 被多个父节点引用时，
    每次引用都完整展开（因为遍历由父节点的 instances 驱动，不是由子 part_master 驱动）。
    """
    rows: list[dict] = []

    # 根节点行（level=0，无实例名，无父节点）
    root_pm      = part_masters.get(root_pm_key, {})
    root_row     = _pm_to_root_row(root_pm_key, root_pm)
    root_inst_key = root_row["_inst_key"]   # id(root_product)，供直接子实例的 _parent_inst_key 使用
    rows.append(root_row)

    def _walk(pm_key: str, level: int, parent_inst_key: int | None,
              ancestors: frozenset[str] = frozenset()) -> None:
        pm = part_masters.get(pm_key, {})
        for inst_info in pm.get("instances", []):
            child_pm_key = inst_info["pm_key"]
            child_pm      = part_masters.get(child_pm_key, {})
            rows.append(_inst_to_row(inst_info, child_pm, level, parent_inst_key))
            # 祖先集合防止循环引用导致无限递归
            if child_pm_key not in ancestors:
                _walk(child_pm_key, level + 1, inst_info["inst_key"],
                      ancestors | {pm_key})

    # 直接子实例的 _parent_inst_key = root_inst_key（根节点的 inst_key）
    _walk(root_pm_key, level=1, parent_inst_key=root_inst_key,
          ancestors=frozenset({root_pm_key}))
    return rows


# ---------------------------------------------------------------------------
# 视图生成：层级 BOM（同父节点同 pm_key 合并）
# ---------------------------------------------------------------------------

def iter_hierarchical_rows(
    root_pm_key: str,
    part_masters: dict[str, dict],
) -> list[dict]:
    """从 part_masters 树生成层级 BOM 行（同父节点下同 pm_key 合并，Quantity = 实例数）。

    根节点（level=0）Quantity=1。
    子节点按 pm_key 分组，代表行取第一个实例，Quantity = 同 pm_key 实例数。
    递归只进入代表行对应的 part_master 的 instances，避免重复展开。
    """
    rows: list[dict] = []

    # 根节点行
    root_pm  = part_masters.get(root_pm_key, {})
    root_row = _pm_to_root_row(root_pm_key, root_pm)
    root_row["Quantity"] = 1
    rows.append(root_row)

    def _expand(pm_key: str, level: int,
                ancestors: frozenset[str] = frozenset()) -> None:
        """将 part_masters[pm_key].instances 按 pm_key 分组输出，然后递归。"""
        pm        = part_masters.get(pm_key, {})
        instances = pm.get("instances", [])

        # 按 instances 顺序分组（保持 CATIA 树顺序）
        seen_key:   dict[str, int]  = {}
        rep_by_key: dict[str, dict] = {}
        for inst in instances:
            ck = inst["pm_key"]
            if ck not in seen_key:
                seen_key[ck]   = 0
                rep_by_key[ck] = inst
            seen_key[ck] += 1

        output_order: list[str] = []
        for inst in instances:
            ck = inst["pm_key"]
            if ck not in output_order:
                output_order.append(ck)

        for ck in output_order:
            rep      = rep_by_key[ck]
            qty      = seen_key[ck]
            child_pm = part_masters.get(ck, {})
            row      = _inst_to_row(rep, child_pm, level)
            row["Quantity"] = qty
            rows.append(row)
            # 祖先集合防止循环引用导致无限递归
            if ck not in ancestors:
                _expand(ck, level + 1, ancestors | {pm_key})

    _expand(root_pm_key, level=1, ancestors=frozenset({root_pm_key}))
    return rows


# ---------------------------------------------------------------------------
# 行 dict 构建辅助
# ---------------------------------------------------------------------------

def _pm_to_root_row(root_pm_key: str, pm: dict) -> dict:
    """为根节点生成行 dict（level=0，无实例名，无父节点）。"""
    root_product = pm.get("_product")
    root_inst_key = id(root_product) if root_product is not None else None
    row: dict = {
        "Level":                  0,
        "Part Number":            pm.get("part_number", root_pm_key),
        "_pm_key":               root_pm_key,
        "Instance Name":          "",
        "Quantity":               1,
        "Type":                   pm.get("type", ""),
        "Filename":               pm.get("filename", ""),
        "_filepath":              pm.get("filepath", ""),
        "_not_found":             pm.get("_not_found", False),
        "_no_file":               pm.get("_no_file", False),
        "_unreadable":            pm.get("_unreadable", False),
        "_product":               root_product,
        "_inst_key":              root_inst_key,
        "_parent_inst_key":       None,
        "Nomenclature":           pm.get("nomenclature", ""),
        "Revision":               pm.get("revision", ""),
        "Definition":             pm.get("definition", ""),
        "Source":                 pm.get("source", ""),
        "Description":            pm.get("description", ""),
    }
    _already_written = set(_COL_TO_PM_KEY.values()) | {"part_number", "pm_key", "host_file_pn"}
    for k, v in pm.items():
        if k not in _already_written and not k.startswith("_") and k != "instances":
            row[k] = v
    return row


def _inst_to_row(
    inst_info: dict,
    pm: dict,
    level: int,
    parent_inst_key: int | None = None,
) -> dict:
    """将 inst_info + 子 part_master 转为行 dict（供 _populate_table 使用）。"""
    row: dict = {
        "Level":                  level,
        "Part Number":            inst_info["pn"],
        "_pm_key":               inst_info["pm_key"],
        "Instance Name":          inst_info["instance_name"],
        "description_inst":       inst_info.get("description_inst", ""),
        "Quantity":               1,
        "Type":                   pm.get("type", ""),
        "Filename":               pm.get("filename", ""),
        "_filepath":              pm.get("filepath", ""),
        "_not_found":             pm.get("_not_found", False),
        "_no_file":               pm.get("_no_file", False),
        "_unreadable":            pm.get("_unreadable", False),
        "_product":               inst_info["product"],
        "_inst_key":              inst_info["inst_key"],
        "_parent_inst_key":       parent_inst_key,
        "Nomenclature":           pm.get("nomenclature", ""),
        "Revision":               pm.get("revision", ""),
        "Definition":             pm.get("definition", ""),
        "Source":                 pm.get("source", ""),
        "Description":            pm.get("description", ""),
    }
    _already_written = set(_COL_TO_PM_KEY.values()) | {"part_number", "pm_key", "host_file_pn"}
    for k, v in pm.items():
        if k not in _already_written and not k.startswith("_") and k != "instances":
            row[k] = v
    return row


# ---------------------------------------------------------------------------
# 属性读写辅助
# ---------------------------------------------------------------------------

def get_part_master_attr(
    part_masters: dict[str, dict],
    pm_key: str,
    col_name: str,
    default: str = "",
) -> str:
    """O(1) 读 part_master 属性。Source 返回原始值 '0'/'1'/'2'。"""
    pm = part_masters.get(pm_key)
    if pm is None:
        return default
    key = _COL_TO_PM_KEY.get(col_name, col_name)
    return str(pm.get(key, default))


def set_part_master_attr(
    part_masters: dict[str, dict],
    pm_key: str,
    col_name: str,
    value: str,
) -> bool:
    """O(1) 写 part_master 属性。只读列和 Part Number 拒绝写入。"""
    pm = part_masters.get(pm_key)
    if pm is None:
        return False
    if col_name in _READONLY_PM_COLS:
        return False
    if col_name == "Part Number":
        logger.warning("set_part_master_attr: PN 修改请使用 rename_part_master()")
        return False
    key = _COL_TO_PM_KEY.get(col_name, col_name)
    pm[key] = value
    return True


def rename_part_master(
    part_masters: dict[str, dict],
    pm_key_to_inst_keys: dict[str, list[int]],
    pm_key: str,
    new_pn: str,
) -> bool:
    """将 part_master 的 PartNumber 改为 new_pn。

    pm_key 永不变（基于 filepath/宿主，与 pn 无关）。
    同步更新：pm["part_number"]，以及所有父 part_master 的 instances[*]["pn"]。
    """
    pm = part_masters.get(pm_key)
    if pm is None:
        logger.warning("rename_part_master: pm_key=%r 不存在", pm_key)
        return False

    old_pn = pm["part_number"]
    pm["part_number"] = new_pn

    # 更新所有父 part_master 的 instances 中引用了此 pm_key 的 pn 显示字段
    for other_pm in part_masters.values():
        for inst in other_pm.get("instances", []):
            if inst["pm_key"] == pm_key:
                inst["pn"] = new_pn

    logger.debug("rename_part_master: pm_key=%r  %r → %r", pm_key, old_pn, new_pn)
    return True
