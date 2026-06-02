"""
重量、重心、惯量统计数据收集模块。

提供：
- collect_mass_props_rows() – 遍历产品树，读取每个零件实例的质量/重心/转动惯量，
                              不对兄弟零件进行数量合并（每个实例单独记录一行）。

数据流概述
----------
1. collect_mass_props_rows() 打开或复用已打开的 .CATProduct 文档，
   调用内部递归函数 _traverse() 深度优先遍历整棵产品树。

2. _traverse() 对每个节点：
   a. 判断节点类型（零件 / 部件 / 产品）；
   b. 通过 _position_to_mat4() 读取该节点相对父节点的局部变换矩阵，
      与父节点的累积矩阵相乘，得到"局部→根"的绝对变换矩阵（_placement）；
   c. 若节点为叶子零件，按 source 参数选择测量路径读取质量特性
      （重心坐标和转动惯量在零件局部坐标系下给出），并写入行字典。

3. _post_process_rows() 对收集到的行列表进行两轮后处理：
   · 第一轮：用 _placement 中的旋转矩阵 R 和平移向量 T，
     将零件局部坐标系下的重心和转动惯量变换到根产品坐标系。
   · 第二轮：对每个产品 / 部件节点，按平行轴定理汇总子孙零件的质量特性，
     计算该节点在根坐标系下的总质量、总重心和总转动惯量。

质量特性读取路径
--------------
source="keep_inertia"（默认）
    依次读取 CATIA SPA "测量惯量 + 保持测量" 写入的 "惯量包络体.1" 至
    "惯量包络体.{MAX_INERTIA_INDEX}" 保持测量参数，在零件级按平行轴定理汇总后存储。
    需用户预先在 SPA 中执行"测量惯量 + 保持测量"操作。

source="analyze"
    通过 pycatia Analyze API（product.analyze.mass / get_gravity_center /
    get_inertia）实时读取零件文档根 Product 的质量特性。
    调用对象为零件文档自身坐标系下的根 Product，故返回值参考系与 keep_inertia
    路径完全一致，_post_process_rows 后处理逻辑不变。
    需零件已赋材料；无需用户手动创建保持测量。

两种路径的返回字典结构相同，单位制均为内部单位（kg / mm / kg·mm²）。

单位制（内部存储）
--------------------------
  质量   ：kg
  长度   ：mm
  惯量   ：kg·mm²
整个流程以 mm 为长度基准，UI 显示时按用户选择换算到 g/m/g·m² 等单位。
"""

import gzip
import json
import logging
import math
from collections.abc import Callable
from pathlib import Path



from catia_copilot.constants import FILENAME_NOT_FOUND, FILENAME_UNSAVED, MAX_INERTIA_INDEX, BomNodeType
from catia_copilot.catia.document import get_bom_node_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 纯 Python 4×4 齐次变换矩阵辅助函数（不依赖 numpy）
#
# 矩阵布局（行主序，4 行 4 列）：
#   [ R[0][0]  R[0][1]  R[0][2]  Tx ]
#   [ R[1][0]  R[1][1]  R[1][2]  Ty ]
#   [ R[2][0]  R[2][1]  R[2][2]  Tz ]
#   [    0        0        0      1  ]
# 其中 R 为 3×3 旋转矩阵，T = (Tx, Ty, Tz) 为平移向量。
#
# 变换关系：P_parent = R @ P_local + T
# 累积（父×子）：M_abs = M_parent @ M_local
# ---------------------------------------------------------------------------

def _identity_4x4() -> list[list[float]]:
    """返回 4×4 单位矩阵（对应"无旋转、无平移"的恒等变换）。"""
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mat4_mul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    """4×4 矩阵乘法，返回 C = A @ B。

    用于将两个齐次变换矩阵复合：若 A 描述"父→祖父"变换，
    B 描述"子→父"变换，则 A @ B 描述"子→祖父"变换。
    """
    C = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            for k in range(4):
                C[i][j] += A[i][k] * B[k][j]
    return C


def _row_inertia_to_root(row: dict) -> list[list[float]]:
    """将行的 _mass_props.inertia（零件局部坐标系）旋转变换到根坐标系。

    公式：I_root = R @ I_local @ R^T
    其中 R 为从行的 _placement 矩阵中提取的 3×3 旋转子矩阵。

    若 _placement 或 _mass_props 缺失，返回 3×3 零矩阵。
    """
    placement = row.get("_placement")
    mp = row.get("_mass_props")
    if placement is None or mp is None:
        return [[0.0] * 3 for _ in range(3)]
    I_local = mp.get("inertia", [[0.0] * 3 for _ in range(3)])
    R  = [[placement[i][j] for j in range(3)] for i in range(3)]
    RT = [[R[j][i] for j in range(3)] for i in range(3)]          # R^T
    RI = [
        [sum(R[i][k] * I_local[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    I_root = [
        [sum(RI[i][k] * RT[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    return I_root


def _position_to_mat4(product) -> list[list[float]]:
    """从 win32com Product 对象的 Position.GetComponents() 读取位置，返回 4×4 变换矩阵。

    ``Position.GetComponents`` 使用 SAFEARRAY ByRef 输出参数，win32com 无法直接处理
    （无参调用报 E_FAIL，传 ``[0.0]*12`` 结果无法反射回 Python）。
    通过 ``wrap_product()`` 包装为 pycatia ``Product``，调用
    ``product.position.get_components()``，pycatia 内部用 ``SystemService.Evaluate``
    注入 VBA 宏绕过此限制。

    CATIA Position.GetComponents 数组布局（**列主序**，共 12 个元素）：
      arr[ 0.. 2] = X 轴方向向量（旋转矩阵第 1 列）
      arr[ 3.. 5] = Y 轴方向向量（旋转矩阵第 2 列）
      arr[ 6.. 8] = Z 轴方向向量（旋转矩阵第 3 列）
      arr[ 9..11] = 原点平移向量 T = (Tx, Ty, Tz)

    组装为行主序 4×4 矩阵：
      mat[i][j] 对应旋转矩阵第 i 行、第 j 列，即 arr[j*3 + i]
      mat[i][3] 对应平移分量 arr[9 + i]

    变换含义：P_parent = R @ P_local + T

    若调用失败或返回值无效，返回 4×4 单位矩阵（等价于零件位于父坐标系原点，无旋转）。
    """
    from catia_copilot.catia.connection import wrap_product

    try:
        product_name = getattr(product, "Name", repr(product))
    except Exception:
        product_name = repr(product)

    try:
        arr = wrap_product(product).position.get_components()
    except Exception as e:
        logger.debug(f"[MAT4] {product_name}: get_components 失败: {e}，返回单位矩阵")
        return _identity_4x4()

    if arr is None or len(arr) < 12:
        logger.debug(
            f"[MAT4] {product_name}: arr 无效（len={len(arr) if arr else None}），返回单位矩阵"
        )
        return _identity_4x4()

    # 将列主序 12 元素数组重新排列为行主序 4×4 矩阵：
    #   第 0 行 = [arr[0], arr[3], arr[6], arr[ 9]]  ← X 分量
    #   第 1 行 = [arr[1], arr[4], arr[7], arr[10]]  ← Y 分量
    #   第 2 行 = [arr[2], arr[5], arr[8], arr[11]]  ← Z 分量
    #   第 3 行 = [    0,      0,      0,      1  ]  ← 齐次行
    # 注：CATIA Position.GetComponents 返回的平移分量单位为 mm，
    #     与内部单位制（mm）一致，直接存储，无需换算。
    mat = [
        [arr[0], arr[3], arr[6], arr[9]  ],
        [arr[1], arr[4], arr[7], arr[10] ],
        [arr[2], arr[5], arr[8], arr[11] ],
        [0.0,    0.0,    0.0,    1.0     ],
    ]
    logger.debug(
        f"[MAT4] {product_name}: R[0]={mat[0][:3]}, T={[mat[0][3], mat[1][3], mat[2][3]]}"
    )
    return mat


# ---------------------------------------------------------------------------
# 质量特性读取辅助函数
# ---------------------------------------------------------------------------


def _read_keep_inertia_params(
    part_com,
    part_number: str = "",
    label: str = "",
    read_mode: str = "all",
) -> dict | None:
    """读取 CATIA 惯量测量 + 保持测量 写入的"惯量包络体.1"至"惯量包络体.MAX_INERTIA_INDEX"参数，
    并在零件级按平行轴定理汇总为单一质量特性。

    先决条件：零件已在 SPA（惯量分析）中执行"测量惯量"并勾选"保持测量"，
    使参数树中出现 "惯量包络体.N\\质量"、"惯量包络体.N\\Gx" 等字段（N ≥ 1）。
    **必须单独打开零件文件再建立测量**——在产品环境下建立的测量其参考系为产品坐标系，
    导致结果不正确，此类测量将不被读取。

    读取策略（对每个编号 N，依次尝试以下前缀，取第一个能读到有效质量的前缀）：
      1. "{part_number}\\惯量包络体.N\\"  ← CATIA 以零件编号作为顶层命名空间
      2. "惯量包络体.N\\"                  ← 当前文档上下文回退前缀
    编号不要求连续，所有 1 ≤ N ≤ MAX_INERTIA_INDEX 中存在的测量均会被读取（取决于 read_mode）。

    read_mode 参数控制读取哪些惯量包络体编号：
      "first" — 仅读取"惯量包络体.1"（固定取编号 1 的测量结果）。
      "last"  — 扫描所有编号，仅返回编号最大的有效测量结果。
      "all"   — 读取全部有效编号并按平行轴定理汇总（默认行为）。

    CATIA 保持测量参数的原始单位（注意坐标为 mm，惯量为 kg·m²）：
      质量                            CATIA 原始: kg    → 内部存储: kg    （无需换算）
      Gx / Gy / Gz                    CATIA 原始: mm    → 内部存储: mm   （无需换算）
      IoxG / IoyG / IozG              CATIA 原始: kg·m² → 内部存储: kg·mm²（× 1e6）
      IxyG / IxzG / IyzG              CATIA 原始: kg·m² → 内部存储: kg·mm²（× 1e6）

    零件级汇总算法（标准刚体力学，均在零件局部坐标系下）：
      1. 累积各测量的质量及"惯量移到局部坐标原点"的贡献。
      2. 计算总重心：r_c = Σ(m_i · r_i) / M。
      3. 平行轴定理从原点移回总重心，得汇总惯量张量。

    CATIA 保持测量参数中亦可选读取密度字段：
      密度                            CATIA 原始: kg/m³ → 内部存储: kg/m³（无需换算）
      当单个测量内材料不统一时 CATIA 返回 -1；跨多个惯量包络体密度不一致时同样返回 -1。

    返回值结构（内部单位）：
      {
        "weight":  float,               # 总质量，kg
        "cog":     [x, y, z],           # 总重心，mm（零件局部坐标系）
        "inertia": [[Ixx, Ixy, Ixz],    # 总重心处转动惯量张量（3×3 对称矩阵），kg·mm²
                    [Ixy, Iyy, Iyz],
                    [Ixz, Iyz, Izz]],
        "density": float | None,        # 密度，kg/m³；-1.0 表示不统一；None 表示无密度数据
      }
    若所有编号均未找到有效质量，则返回 None。
    """
    tag = f"[MP] {label} " if label else "[MP] "
    try:
        params = part_com.Parameters

        def _get(prefix: str, name: str) -> float | None:
            try:
                return float(params.Item(prefix + name).Value)
            except Exception:
                return None

        # ── 确定需要扫描的编号范围 ────────────────────────────────────────────────
        if read_mode == "first":
            check_indices = [1]
        else:
            # "last" 和 "all" 均需扫描全范围，以找到所有或编号最大的有效测量
            check_indices = list(range(1, MAX_INERTIA_INDEX + 1))

        # ── 逐编号读取，收集所有有效测量 ──────────────────────────────────────────
        measurements: list[dict] = []
        for idx in check_indices:
            envelope_name = f"惯量包络体.{idx}"
            probe_prefix = (f"{part_number}\\{envelope_name}\\" if part_number
                            else f"{envelope_name}\\")

            mass_si = _get(probe_prefix, "质量")
            if mass_si is None or mass_si <= 0.0:
                continue  # 该编号不存在，跳过

            prefix_ok = probe_prefix

            gx_si  = _get(prefix_ok, "Gx")
            gy_si  = _get(prefix_ok, "Gy")
            gz_si  = _get(prefix_ok, "Gz")
            ixx_si = _get(prefix_ok, "IoxG")
            iyy_si = _get(prefix_ok, "IoyG")
            izz_si = _get(prefix_ok, "IozG")
            ixy_si = _get(prefix_ok, "IxyG")
            ixz_si = _get(prefix_ok, "IxzG")
            iyz_si = _get(prefix_ok, "IyzG")

            # 惯量分量允许为 0（球对称体），但不允许任意分量读取失败
            if any(v is None for v in (gx_si, gy_si, gz_si,
                                       ixx_si, iyy_si, izz_si,
                                       ixy_si, ixz_si, iyz_si)):
                logger.debug(f"{tag}{envelope_name} 部分参数缺失，跳过该测量")
                continue

            # 密度（可选参数）：CATIA 原始单位 kg/m³，不一致时返回 -1
            density_raw = _get(prefix_ok, "密度")

            measurements.append({
                "weight": mass_si,
                # Gx/Gy/Gz 由 CATIA 以 mm 存储，与内部单位制（mm）一致，直接使用
                "cog": [gx_si, gy_si, gz_si],
                # IoxG/IoyG/IozG 等由 CATIA 以 kg·m² 存储，×1e6 换算为内部单位 kg·mm²
                "inertia": [
                    [ixx_si * 1e6, ixy_si * 1e6, ixz_si * 1e6],
                    [ixy_si * 1e6, iyy_si * 1e6, iyz_si * 1e6],
                    [ixz_si * 1e6, iyz_si * 1e6, izz_si * 1e6],
                ],
                "density": density_raw,  # None：无密度参数；-1.0： CATIA 报材料不统一；>0：kg/m³
            })

        if not measurements:
            logger.debug(f"{tag}未找到任何有效的惯量包络体参数，返回 None")
            return None

        # "last" 模式：仅保留编号最大的有效测量（已按升序扫描，取最后一个）
        if read_mode == "last":
            measurements = [measurements[-1]]

        if len(measurements) == 1:
            # 仅一个测量，无需汇总，直接返回（density 已含在 measurements[0] 中）
            return measurements[0]

        # ── 零件级汇总：平行轴定理（均在零件局部坐标系下）────────────────────────
        M_total   = 0.0
        sum_mr    = [0.0, 0.0, 0.0]
        I_at_orig = [[0.0] * 3 for _ in range(3)]

        for meas in measurements:
            m  = float(meas["weight"])
            r  = meas["cog"]
            Ic = meas["inertia"]
            r2 = sum(r[k] ** 2 for k in range(3))
            for ii in range(3):
                for jj in range(3):
                    delta = (1.0 if ii == jj else 0.0) * r2 - r[ii] * r[jj]
                    I_at_orig[ii][jj] += Ic[ii][jj] + m * delta
            M_total += m
            for k in range(3):
                sum_mr[k] += m * r[k]

        cog_total = [sum_mr[k] / M_total for k in range(3)]

        rc  = cog_total
        rc2 = sum(rc[k] ** 2 for k in range(3))
        I_final = [[0.0] * 3 for _ in range(3)]
        for ii in range(3):
            for jj in range(3):
                delta = (1.0 if ii == jj else 0.0) * rc2 - rc[ii] * rc[jj]
                I_final[ii][jj] = I_at_orig[ii][jj] - M_total * delta

        # ── 跨多个惯量包络体的密度汇总 ────────────────────────────────────────
        # 规则：任意一个测量报"不统一"（-1）→ 整体为 -1；
        #       所有有效密度值（>0）不完全相同 → 整体为 -1（多材料）；
        #       所有有效密度值相同 → 取该值；无任何密度数据 → None。
        agg_density: float | None = None
        has_inconsistent = False
        valid_densities: list[float] = []
        for meas in measurements:
            d = meas.get("density")
            if d is None:
                continue
            if d < 0:
                has_inconsistent = True
            else:
                valid_densities.append(d)
        if has_inconsistent:
            agg_density = -1.0
        elif valid_densities:
            # 判断各密度值是否一致（相对误差 < 1e-9）
            d0 = valid_densities[0]
            if all(math.isclose(d, d0, rel_tol=1e-9) for d in valid_densities[1:]):
                agg_density = d0
            else:
                agg_density = -1.0

        logger.debug(
            f"{tag}汇总 {len(measurements)} 个惯量包络体测量: "
            f"weight={M_total:.4g} kg, cog={[round(v,4) for v in cog_total]} mm, "
            f"density={agg_density} kg/m³"
        )
        return {"weight": M_total, "cog": cog_total, "inertia": I_final, "density": agg_density}

    except Exception as e:
        logger.debug(f"{tag}惯量包络体参数读取异常: {e}")
        return None


def _measure_part_mass_props(
    part_com,
    part_number: str = "",
    read_mode: str = "all",
) -> dict | None:
    """测量零件质量特性。

    所有返回值均使用 **内部单位制（kg / mm / kg·mm²）**。

    先决条件：
      零件已在 SPA 中执行"测量惯量"并勾选"保持测量"，
      从而在参数树中生成 "惯量包络体.N\\质量" 等保持测量参数（N ≥ 1）。
      **必须单独打开零件文件再建立测量**——在产品环境下建立的测量使用产品坐标系，
      将导致结果不正确。

    参数：
        part_com:    COM 对象（Part 层）。
        part_number: 零件编号（PartNumber），用于构造参数前缀。
        read_mode:   控制读取哪些惯量包络体（"first"/"last"/"all"，默认 "all"）。

    返回字典：
      {
        "weight":  float,          # 总质量，kg
        "cog":     [x, y, z],      # 重心坐标（零件局部坐标系），mm
        "inertia": [[Ixx,Ixy,Ixz],
                    [Iyx,Iyy,Iyz],
                    [Izx,Izy,Izz]], # 重心处转动惯量（零件局部坐标轴），kg·mm²
      }
    若所有惯量包络体参数均不存在（零件未执行保持测量）则返回 None。
    """
    return _read_keep_inertia_params(part_com, part_number, read_mode=read_mode)


def _measure_part_mass_props_analyze(product_com) -> dict | None:
    """通过 pycatia Analyze API 读取零件质量特性。

    与 ``_measure_part_mass_props`` 并列的替代路径，无需用户预先在 SPA 中创建
    "惯量包络体"保持测量——只要零件已赋材料，CATIA 即可实时计算。

    实现说明
    --------
    调用对象为**零件文档的根 Product**（``product_com.ReferenceProduct.Parent.Product``），
    而非装配树中的实例。在零件文档根 Product 上调用 ``analyze``，参考系为零件自身
    坐标系，等同于"在零件文档中单独测量"的结果，与 ``惯量包络体`` 路径的坐标系语义
    完全一致。

    已验证（本机测试）：
      - ``analyze.mass``               → 零件质量，kg
      - ``analyze.get_gravity_center()`` → 零件局部坐标系下重心，mm
      - ``analyze.get_inertia()``       → **重心处**转动惯量，kg·mm²，9 元素行主序 tuple
        返回行主序 3×3：raw[0..2]=第0行，raw[3..5]=第1行，raw[6..8]=第2行

    返回字典结构与 ``_measure_part_mass_props`` 完全相同（内部单位）：
      {
        "weight":  float,               # 质量，kg
        "cog":     [x, y, z],           # 重心坐标（零件局部坐标系），mm
        "inertia": [[Ixx, Ixy, Ixz],    # 重心处转动惯量张量，kg·mm²
                    [Ixy, Iyy, Iyz],
                    [Ixz, Iyz, Izz]],
        "density": None,                # Analyze 不提供密度字段，始终为 None
      }
    若零件未赋材料导致 mass == 0 或调用失败，则返回 None。
    """
    from catia_copilot.catia.connection import wrap_product

    try:
        # 零件文档根 Product：参考系 = 零件自身坐标系
        part_doc_product_com = product_com.ReferenceProduct.Parent.Product
        analyze = wrap_product(part_doc_product_com).analyze

        mass = analyze.mass          # kg
        if not mass or mass <= 0.0:
            return None

        cog = analyze.get_gravity_center()   # mm，3 元素 tuple

        raw = analyze.get_inertia()  # kg·mm²，9 元素 tuple，行主序 3×3
        inertia = [[raw[r * 3 + c] for c in range(3)] for r in range(3)]  # CATIA 返回 kg·mm²，与内部单位一致，直接使用

        return {
            "weight":  mass,
            "cog":     cog,
            "inertia": inertia,
            "density": None,
        }
    except Exception as e:
        logger.debug(f"[ANALYZE] get_inertia/get_gravity_center 失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 后处理辅助函数
# ---------------------------------------------------------------------------


def _rollup_one_product(child_parts: list[dict]) -> dict | None:
    """对单个产品/部件节点，按平行轴定理汇总子树内所有零件的根坐标系质量特性。

    参数：
        child_parts: 该节点子树内所有零件的 ``_root_mp`` 字典列表，
                     每个元素含 weight（kg）、cog（mm 列表）、inertia（3×3 列表，kg·mm²）。

    返回字典（若总质量 > 0）：
        {"weight": M_total, "cog": [x, y, z], "inertia": [[3×3]]}
    若所有子零件质量均 ≤ 0，返回 None。

    算法（标准刚体力学，所有计算均在根坐标系下）：
      步骤 1+2：累积质量、质量×重心，同时将各零件重心处惯量移至根坐标原点。
        I_i_at_O = I_i + m_i * (|r_i|² * E - r_i ⊗ r_i)
      步骤 3：计算总重心 r_c = Σ(m_i * r_i) / M
      步骤 4：以平行轴定理从根原点移回总重心。
        I_final = I_total_at_O - M * (|r_c|² * E - r_c ⊗ r_c)
    """
    M_total   = 0.0
    sum_mr    = [0.0, 0.0, 0.0]
    I_at_orig = [[0.0] * 3 for _ in range(3)]

    for rmp in child_parts:
        m = float(rmp.get("weight", 0.0))
        if m <= 0.0:
            continue
        r  = rmp.get("cog",     [0.0, 0.0, 0.0])
        Ic = rmp.get("inertia", [[0.0] * 3 for _ in range(3)])

        # 平行轴定理：将零件重心处惯量移到根坐标原点
        r2 = sum(r[k] ** 2 for k in range(3))
        for ii in range(3):
            for jj in range(3):
                delta = (1.0 if ii == jj else 0.0) * r2 - r[ii] * r[jj]
                I_at_orig[ii][jj] += Ic[ii][jj] + m * delta

        M_total += m
        for k in range(3):
            sum_mr[k] += m * r[k]

    if M_total <= 0.0:
        return None

    # 计算总重心
    cog_total = [sum_mr[k] / M_total for k in range(3)]

    # 平行轴定理：从根原点移回总重心
    rc  = cog_total
    rc2 = sum(rc[k] ** 2 for k in range(3))
    I_final = [[0.0] * 3 for _ in range(3)]
    for ii in range(3):
        for jj in range(3):
            delta = (1.0 if ii == jj else 0.0) * rc2 - rc[ii] * rc[jj]
            I_final[ii][jj] = I_at_orig[ii][jj] - M_total * delta

    return {"weight": M_total, "cog": cog_total, "inertia": I_final}


def _post_process_rows(rows: list[dict]) -> None:
    """对遍历后的行列表进行两轮后处理，使显示字段反映根产品坐标系。

    第一轮（零件行）
        利用 ``_placement``（零件局部→根的变换矩阵）将局部坐标系的重心和
        转动惯量旋转变换到根坐标系，更新 ``CogX/Y/Z`` 及 ``Ixx``-``Iyz``
        显示字段，并将变换结果缓存到 ``_root_mp`` 中供父级汇总和编辑后
        回写使用。

    第二轮（产品/部件行）
        收集该节点子树内所有零件的根坐标系质量特性，按标准刚体力学汇总
        （平行轴定理），计算该节点在根坐标系下的总质量、总重心和总转动
        惯量，并更新到显示字段中。

        若子树内所有零件均测量失败，则该节点的显示字段保持为 ``None``
        （显示为 "—"）。
    """
    n = len(rows)

    # ── 第一轮：零件行 → 将局部坐标系质量特性变换到根产品坐标系 ──────────────────────
    #
    # 每个零件行的 _placement 字段存储了"零件局部→根"的 4×4 齐次变换矩阵，
    # 其中左上 3×3 块为旋转矩阵 R，右上 3×1 列为平移向量 T。
    #
    # （一）重心坐标变换
    #   零件局部坐标系下的重心 r_local，变换到根坐标系：
    #     r_root = R @ r_local + T
    #
    # （二）转动惯量旋转变换
    #   转动惯量张量在不同坐标系下通过相似变换（旋转）互换：
    #     I_root = R @ I_local @ R^T
    #   注意：此处仅做坐标轴旋转，不做平移（平移修正由第二轮平行轴定理完成）。
    #   I_local 是在零件重心处、沿零件局部坐标轴方向的惯量；
    #   I_root  是在零件重心处、沿根产品坐标轴方向的惯量。
    for row in rows:
        if row.get("Type") not in BomNodeType.LEAF_TYPES:
            continue
        mp = row.get("_mass_props")
        if not mp:
            continue
        placement = row.get("_placement")
        if placement is None:
            continue

        # 从 4×4 矩阵中提取 3×3 旋转矩阵 R 和平移向量 T
        R = [[placement[i][j] for j in range(3)] for i in range(3)]
        T = [placement[0][3], placement[1][3], placement[2][3]]

        # ── （一）重心坐标变换：r_root[i] = Σ_k(R[i][k] * r_local[k]) + T[i] ──
        cog_local = mp.get("cog", [0.0, 0.0, 0.0])
        cog_root  = [
            sum(R[i][k] * cog_local[k] for k in range(3)) + T[i]
            for i in range(3)
        ]

        # ── （二）惯量旋转：I_root = R @ I_local @ R^T ────────────────────────
        #   分两步计算：先求 RI = R @ I_local，再求 RI @ R^T
        I_local = mp.get("inertia", [[0.0] * 3 for _ in range(3)])
        RT = [[R[j][i] for j in range(3)] for i in range(3)]  # R^T（转置）
        RI = [
            [sum(R[i][k] * I_local[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]
        I_root = [
            [sum(RI[i][k] * RT[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]

        # 将零件自身坐标系下的值写入显示字段（汇总BOM展示及后续缩放用）
        # 注：汇总BOM 的单行显示以零件自身坐标系为准；
        #     层级BOM 在 _get_display_rows() 中会以 _root_mp 值覆盖这些字段；
        #     根坐标系数据缓存于 _root_mp，供第二轮汇总和底部计算面板使用。
        row["CogX"] = cog_local[0]
        row["CogY"] = cog_local[1]
        row["CogZ"] = cog_local[2]
        row["Ixx"]  = I_local[0][0]
        row["Iyy"]  = I_local[1][1]
        row["Izz"]  = I_local[2][2]
        row["Ixy"]  = I_local[0][1]
        row["Ixz"]  = I_local[0][2]
        row["Iyz"]  = I_local[1][2]

        # 缓存根坐标系数据到 _root_mp，供第二轮汇总及底部计算面板使用
        row["_root_mp"] = {
            "weight":  mp.get("weight", 0.0),
            "cog":     cog_root,
            "inertia": I_root,
        }

    # ── 第二轮：产品/部件行 → 按平行轴定理汇总子孙零件的质量特性 ──────────────────
    # 算法由 _rollup_one_product() 实现（详见其文档字符串）。
    for i in range(n):
        row = rows[i]
        if row.get("Type") not in BomNodeType.ASSEMBLY_TYPES:
            continue

        level = int(row.get("Level", 0))

        # 收集当前节点子树内所有已成功测量零件的根坐标系质量特性（_root_mp）
        # 子树范围：行索引 i+1 开始，直到遇到层级 ≤ 当前层级的行为止
        child_parts: list[dict] = []
        for j in range(i + 1, n):
            desc = rows[j]
            if int(desc.get("Level", 0)) <= level:
                break  # 已超出子树范围，停止遍历
            rmp = desc.get("_root_mp")
            if rmp and float(rmp.get("weight", 0.0)) > 0.0:
                child_parts.append(rmp)

        if not child_parts:
            continue  # 子树内无有效零件质量数据，跳过本节点

        result = _rollup_one_product(child_parts)
        if result is None:
            continue

        # 将汇总结果写入本节点的显示字段
        row["Weight"] = result["weight"]
        row["CogX"]   = result["cog"][0]
        row["CogY"]   = result["cog"][1]
        row["CogZ"]   = result["cog"][2]
        row["Ixx"]    = result["inertia"][0][0]
        row["Iyy"]    = result["inertia"][1][1]
        row["Izz"]    = result["inertia"][2][2]
        row["Ixy"]    = result["inertia"][0][1]
        row["Ixz"]    = result["inertia"][0][2]
        row["Iyz"]    = result["inertia"][1][2]


def recompute_product_rows(rows: list[dict]) -> None:
    """重新计算所有产品/部件行的汇总质量特性。

    与 ``_post_process_rows()`` 第二轮逻辑相同，但可在初始加载后独立调用——
    例如用户在对话框中手动修改了零件重量（同时更新了 ``_root_mp``），
    点击"计算"按钮后需要刷新产品/部件行的汇总结果。

    处理流程（与 _post_process_rows 第二轮完全一致）：
      · 遍历 rows，对每个产品/部件节点，收集子树内全部零件的 ``_root_mp``；
      · 按平行轴定理汇总质量、重心和转动惯量；
      · 将结果写回该节点的显示字段（Weight / CogX/Y/Z / Ixx–Iyz）。
    """
    n = len(rows)
    for i in range(n):
        row = rows[i]
        if row.get("Type") not in BomNodeType.ASSEMBLY_TYPES:
            continue

        level = int(row.get("Level", 0))

        # 收集子树内全部零件的根坐标系质量特性
        child_parts: list[dict] = []
        for j in range(i + 1, n):
            desc = rows[j]
            if int(desc.get("Level", 0)) <= level:
                break
            if desc.get("_excluded"):
                continue
            rmp = desc.get("_root_mp")
            if rmp and float(rmp.get("weight", 0.0)) > 0.0:
                child_parts.append(rmp)

        if not child_parts:
            continue

        result = _rollup_one_product(child_parts)
        if result is None:
            continue

        # 写回显示字段
        row["Weight"] = result["weight"]
        row["CogX"]   = result["cog"][0]
        row["CogY"]   = result["cog"][1]
        row["CogZ"]   = result["cog"][2]
        row["Ixx"]    = result["inertia"][0][0]
        row["Iyy"]    = result["inertia"][1][1]
        row["Izz"]    = result["inertia"][2][2]
        row["Ixy"]    = result["inertia"][0][1]
        row["Ixz"]    = result["inertia"][0][2]
        row["Iyz"]    = result["inertia"][1][2]


# ---------------------------------------------------------------------------
# 二进制序列化 / 反序列化（保存与载入行数据）
#
# 格式：将行列表序列化为 JSON 字符串，再用 gzip 压缩后写入二进制文件（.mpd）。
# 用记事本等文本编辑器打开只显示乱码；只能通过本模块的接口读取。
# ---------------------------------------------------------------------------

# 序列化时跳过的内部字段：_root_mp 可由 _post_process_rows() 重新计算，
# _rows_idx 是显示层临时注入的索引，均无需持久化。
_SERIALIZE_SKIP: frozenset[str] = frozenset({"_root_mp", "_rows_idx"})


def save_rows(rows: list[dict], file_path: str) -> None:
    """将行数据序列化为压缩二进制文件（.mpd）。

    内部以 JSON 序列化行数据后用 gzip 压缩，写为二进制文件。
    用记事本等文本工具打开无法读取有效内容。

    序列化时跳过 ``_root_mp``（加载后可由 :func:`_post_process_rows` 重新计算）
    和 ``_rows_idx``（仅供显示层使用）。其余所有字段均原样写出。

    参数：
        rows:      ``collect_mass_props_rows()`` 或 :func:`load_rows`
                   返回的行列表。
        file_path: 目标文件路径（不存在则创建，已存在则覆盖）。
    """
    serializable = [
        {k: v for k, v in row.items() if k not in _SERIALIZE_SKIP}
        for row in rows
    ]
    payload = json.dumps(serializable, ensure_ascii=False).encode("utf-8")
    with gzip.open(file_path, "wb") as f:
        f.write(payload)


def load_rows(file_path: str) -> list[dict]:
    """从压缩二进制文件（.mpd）反序列化行数据，并重建运行时缓存字段。

    读取由 :func:`save_rows` 保存的文件，恢复行列表后调用
    :func:`_post_process_rows` 重新计算 ``_root_mp`` 及产品/部件汇总字段，
    与从 CATIA 现场加载后的状态完全等价。

    参数：
        file_path: 要读取的文件路径。

    返回：
        经过后处理的行列表（包含 ``_root_mp`` 及汇总显示字段）。
    """
    with gzip.open(file_path, "rb") as f:
        rows: list[dict] = json.loads(f.read().decode("utf-8"))
    _post_process_rows(rows)
    return rows


def merge_rows(base_rows: list[dict], extra_rows: list[dict]) -> list[dict]:
    """将两组行数据合并为一个统一列表，适用于同坐标系分总成拼接。

    当主产品过大、只能分批打开各分总成读取时，可将多次读取结果逐一追加合并。
    前提条件：各分总成的坐标系与主产品一致，即各行的 ``_placement`` 和
    ``_root_mp`` 均已在同一根坐标系下计算，无须额外变换，可直接拼接。

    合并逻辑：
      1. 直接拼接 ``base_rows + extra_rows``（保持各自的内部顺序）。
      2. 调用 :func:`recompute_product_rows` 刷新合并后列表中每个产品/部件行的
         汇总字段（Weight / CogX/Y/Z / Ixx–Iyz），使其反映当前完整子树的结果。
         零件行的 ``_root_mp`` 无需重算，因为它们在各自分总成加载时已正确建立。

    参数：
        base_rows:  已有的行列表（可以为空列表）。
        extra_rows: 要追加的行列表（来自另一个分总成的 :func:`load_rows` 或
                    :func:`collect_mass_props_rows` 结果）。

    返回：
        合并并刷新后的行列表（对 ``base_rows`` 和 ``extra_rows`` 的原始内容
        不做修改，返回新列表）。
    """
    combined = list(base_rows) + list(extra_rows)
    recompute_product_rows(combined)
    return combined


# ---------------------------------------------------------------------------
# 主收集函数
# ---------------------------------------------------------------------------

def _compute_root_mp_from_placement(
    placement: list[list[float]],
    mass_props: dict,
) -> dict:
    """利用 4×4 变换矩阵将零件局部坐标系下的质量特性变换到根坐标系。

    从 *placement*（零件局部→根的 4×4 齐次变换矩阵）中提取 3×3 旋转矩阵 R
    和平移向量 T，对 *mass_props* 中的重心坐标和转动惯量张量执行坐标变换：
      - 重心坐标：r_root = R @ r_local + T
      - 惯量张量：I_root = R @ I_local @ R^T

    返回字典格式与 ``_mass_props`` / ``_root_mp`` 字段一致（内部 SI 单位）。
    """
    R  = [[placement[i][j] for j in range(3)] for i in range(3)]
    T  = [placement[i][3] for i in range(3)]
    cog_local = mass_props.get("cog", [0.0, 0.0, 0.0])
    cog_root  = [
        sum(R[i][k] * cog_local[k] for k in range(3)) + T[i]
        for i in range(3)
    ]
    I_local = mass_props.get("inertia", [[0.0] * 3 for _ in range(3)])
    RT = [[R[j][i] for j in range(3)] for i in range(3)]
    RI = [
        [sum(R[i][k] * I_local[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    I_root = [
        [sum(RI[i][k] * RT[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    return {
        "weight":  mass_props.get("weight", 0.0),
        "cog":     cog_root,
        "inertia": I_root,
    }


def remeasure_part_mass_props(
    filepath: str,
    part_number: str = "",
    read_mode: str = "all",
) -> dict | None:
    """通过 CATIA COM 接口重新读取指定零件的质量特性（惯量包络体保持测量）。

    在 CATIA 当前已打开的文档中查找与 *filepath* 匹配的零件文档，再调用
    :func:`_measure_part_mass_props` 读取保持测量参数。适用于用户在 CATIA 中
    补充或更改惯量包络体后，无需重新遍历整棵产品树即可刷新单个零件的质量特性。

    参数：
        filepath:    零件文档的磁盘完整路径（若文档尚未在 CATIA 中打开则返回 None）。
        part_number: 零件编号（PartNumber），用于构造保持测量参数前缀。
        read_mode:   控制读取哪些惯量包络体（"first"/"last"/"all"）。

    返回：
        成功时返回质量特性字典（内部 SI 单位，与 :func:`collect_mass_props_rows`
        相同格式）；找不到文档或读取失败时返回 None。
    """
    from catia_copilot.catia.connection import get_catia_v5_application  # 运行时导入，避免无 CATIA 环境时报错
    try:
        application = get_catia_v5_application()
        application.Visible = True
        documents = application.Documents

        fp_resolved = Path(filepath).resolve()
        target_doc = None
        doc_count = documents.Count  # 缓存文档数量，减少重复 COM 属性访问
        for i in range(1, doc_count + 1):
            try:
                doc = documents.Item(i)
                if Path(doc.FullName).resolve() == fp_resolved:
                    target_doc = doc
                    break
            except Exception:
                pass

        if target_doc is None:
            logger.debug(f"[REMEAS] 找不到已打开的文档: {filepath}")
            return None

        part_com = target_doc.Part
        return _measure_part_mass_props(part_com, part_number, read_mode=read_mode)
    except Exception as e:
        logger.debug(f"[REMEAS] 重新读取质量特性失败 ({filepath}): {e}")
        return None


def collect_mass_props_rows(
    file_path: str | None,
    progress_callback: Callable[[int], None] | None = None,
    read_mode: str = "all",
    skip_hidden: bool = False,
    source: str = "analyze",
) -> list[dict]:
    """遍历产品树，返回每个节点的质量特性行列表。

    与 collect_bom_rows() 的关键区别：
      - **不对兄弟零件去重**——每个实例单独输出一行。
      - 仅对类型为"零件"的叶子节点执行质量特性测量（通过 MP_* 用户参数或 VBS 绑定脚本），
        部件/产品节点跳过测量，其质量由后处理阶段按平行轴定理汇总子树获得。
      - 每行额外包含 ``_placement`` 字段（4×4 列表），为该实例到根坐标系的变换矩阵。

    参数：
        file_path:
            ``.CATProduct`` 文件路径，或 ``None`` 表示使用当前 CATIA 活动文档。
        progress_callback:
            可选回调，每追加一行后调用，传入当前行数。可通过抛出异常中止遍历。
        read_mode:
            控制读取哪些惯量包络体（仅 ``source="keep_inertia"`` 时生效）：
            "first" — 仅读取惯量包络体.1；
            "last"  — 读取编号最大的惯量包络体；
            "all"   — 全部读取并按平行轴定理汇总（默认）。
        skip_hidden:
            若为 True，则跳过处于隐藏状态的节点：
            零件隐藏时不读取该行；产品/部件隐藏时连同其全部子孙一并跳过。
        source:
            质量特性数据来源，可选值：
            "keep_inertia" — 读取 SPA 保持测量写入的"惯量包络体.N"参数（默认）。
                             需用户预先在 SPA 中执行"测量惯量 + 保持测量"。
            "analyze"      — 通过 pycatia Analyze API 实时计算。
                             需零件已赋材料；无需用户手动创建保持测量。
                             两种方式返回结构相同，坐标系语义一致，后处理逻辑不变。

    返回：
        行字典列表，每行含以下键：
          Level, Type, Part Number, Filename, Nomenclature, Revision,
          Density, Weight, CogX, CogY, CogZ, Ixx, Iyy, Izz, Ixy, Ixz, Iyz,
          _filepath, _placement, _not_found, _no_file, _unreadable, _meas_failed
    """
    from catia_copilot.catia.connection import get_catia_v5_application

    # CatWorkModeType 枚举值（来自 CATIA V5 COM API）
    CATIA_DESIGN_MODE        = 2  # catWorkModeDesign
    CATIA_VISUALIZATION_MODE = 1  # catWorkModeVisualization

    _total_count: int = 0
    # 以文件路径为键缓存质量特性测量结果，避免同一零件多实例重复测量
    _mass_cache: dict[str, dict] = {}

    def _get_prop(product, name: str) -> str:
        """读取直接属性（Nomenclature / Revision）。"""
        attr_map = {"Nomenclature": "Nomenclature", "Revision": "Revision"}
        attr = attr_map.get(name)
        if not attr:
            return ""
        targets = [product]
        try:
            targets.insert(0, product.ReferenceProduct)
        except Exception:
            pass
        for target in targets:
            try:
                value = getattr(target, attr)
                if value is not None:
                    return str(value)
            except Exception:
                pass
        return ""

    def _is_hidden(product, pn: str = "") -> bool:
        """检查产品实例（occurrence / 树节点）在父装配中是否处于隐藏状态。

        通过 ActiveDocument.Selection 读取实例级可见性：
          1. 清空选择集
          2. 将当前节点的 COM 对象加入选择集
          3. 从 Selection.VisProperties 读取 GetShow() 结果

        此方式会临时修改 CATIA 当前选择集（副作用），finally 块中自动清空，
        以将影响降至最低。读取失败则保守地视为可见，返回 False。

        返回：catVisNoShow=1（隐藏）→ True；catVisShow=0（可见）→ False。
        """
        tag = pn or "<unknown>"
        com = product
        sel = None
        try:
            sel = application.ActiveDocument.Selection
            sel.Clear()
            sel.Add(com)
            # win32com 对 GetShow 的返回形式：无参调用时将 ByRef out-param 作为
            # 元组末位元素返回，即 (status, show_val)。取 [1] 得到 show 状态值。
            # 与 pycatia VisPropertySet.get_show() 的实现一致。
            result = sel.VisProperties.GetShow()
            show_val = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
            hidden = bool(show_val) if show_val is not None else False
            logger.debug(f"[VIS] {tag}: Selection.VisProperties.GetShow()={show_val} → hidden={hidden}")
            return hidden
        except Exception as e:
            logger.debug(f"[VIS] {tag}: Selection.VisProperties.GetShow() 不可用 ({e})，视为可见")
        finally:
            try:
                if sel is not None:
                    sel.Clear()
            except Exception:
                pass

        return False

    def _traverse(
        product,
        rows: list,
        level: int,
        parent_filepath: str,
        parent_mat4: list[list[float]],
    ) -> None:
        """递归遍历产品树，将每个节点的质量特性信息追加到 rows。

        参数：
            product:         当前节点的 win32com 产品对象。
            rows:            行字典列表，结果追加于此。
            level:           当前节点的层级深度（根节点为 0）。
            parent_filepath: 父节点的文件路径（用于判断"嵌入式部件"）。
            parent_mat4:     父节点到根的累积 4×4 变换矩阵。
        """
        nonlocal _total_count

        # 读取零件编号（PartNumber）；失败时退而使用名称去掉扩展名
        try:
            pn = product.PartNumber
        except Exception:
            name = product.Name
            pn   = name.rsplit(".", 1)[0] if "." in name else name

        # 可见性探测：仅当用户勾选"忽略隐藏的节点"（skip_hidden=True）时才发起
        # COM 调用（Selection.VisProperties.GetShow）；skip_hidden=False 时完全不
        # 调用 _is_hidden()，从而避免任何多余的 COM 开销。
        # 根节点（level=0）的实例是虚拟根，不存在 parent 上下文，跳过探测。
        if level >= 1 and skip_hidden:
            if _is_hidden(product, pn):
                return

        # 解析本节点对应的磁盘文件路径（通过 COM ReferenceProduct.Parent.FullName）
        try:
            filepath = product.ReferenceProduct.Parent.FullName
        except Exception:
            filepath = ""

        # filepath 为空 → CATIA 无法解析该节点的文件引用（引用丢失或文档未载入）
        not_found = not bool(filepath)
        # filepath 非空但磁盘上不存在 → 文件尚未保存到磁盘（仍在 CATIA 内存中）
        no_file   = bool(filepath) and not Path(filepath).exists()

        # ── 判断节点类型 ──────────────────────────────────────────────────────
        node_type = get_bom_node_type(product, parent_filepath, filepath=filepath)

        # ── 计算本节点到根坐标系的累积变换矩阵 ──────────────────────────────
        # local_mat4：本节点相对父节点的局部变换（由 CATIA Position 读取）
        # abs_mat4  ：本节点到根的绝对变换 = parent_mat4 @ local_mat4
        # 此矩阵存入 _placement 字段，后续 _post_process_rows 用它将局部质量特性变换到根系
        local_mat4 = _position_to_mat4(product)
        abs_mat4   = _mat4_mul(parent_mat4, local_mat4)

        # ── 读取 Nomenclature / Revision 属性 ─────────────────────────────────
        is_readable = True
        nomenclature = ""
        revision     = ""

        if not not_found:
            # 确保节点处于"设计模式"（非可视化/缓存模式），否则属性读取可能失败
            try:
                current_mode = product.GetWorkMode()
                if current_mode != CATIA_DESIGN_MODE:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
            except Exception:
                try:
                    product.ApplyWorkMode(CATIA_DESIGN_MODE)
                except Exception:
                    is_readable = False

            if is_readable:
                nomenclature = _get_prop(product, "Nomenclature")
                revision     = _get_prop(product, "Revision")

        # ── 质量特性测量（仅对叶子零件节点）────────────────────────────────────
        mass_props: dict | None = None
        meas_failed = False

        if node_type == BomNodeType.PART and is_readable and filepath:
            if filepath in _mass_cache:
                # 同一文件路径已测量过（多实例复用），直接取缓存，避免重复耗时测量
                mass_props = _mass_cache[filepath]
            else:
                try:
                    if source == "analyze":
                        # Analyze API：通过零件文档根 Product 实时计算
                        # 参考系 = 零件自身坐标系，与 keep_inertia 路径语义一致
                        mass_props = _measure_part_mass_props_analyze(product)
                    else:
                        # keep_inertia（默认）：读取 SPA 保持测量参数
                        part_doc_com = product.ReferenceProduct.Parent
                        part_com     = part_doc_com.Part
                        mass_props   = _measure_part_mass_props(part_com, pn, read_mode=read_mode)
                except Exception as e:
                    logger.debug(f"无法测量零件 {filepath}: {e}")
                    mass_props  = None
                    meas_failed = True

                if mass_props is not None:
                    logger.debug(
                        f"[TRAV] {pn} 测量成功 (source={source}): "
                        f"weight={mass_props.get('weight')}kg, "
                        f"cog={[round(v,4) for v in mass_props.get('cog',[0,0,0])]}mm, "
                        f"Ixx={mass_props.get('inertia',[[0]])[0][0]:.3g}kg·mm²"
                    )
                else:
                    logger.debug(f"[TRAV] {pn} 质量特性读取失败或未赋材料 (source={source})")

                # 写入缓存（即使测量失败也缓存 None，防止重复尝试）
                _mass_cache[filepath] = mass_props

        # 若零件本应可测但最终无数据，标记 meas_failed（无论是找不到文档还是读参数失败）
        if mass_props is None:
            meas_failed = meas_failed or (node_type == BomNodeType.PART and is_readable and not not_found)

        # ── 组装行字典 ─────────────────────────────────────────────────────────
        # CogX/Y/Z 和 Ixx 等此处存储的是零件局部坐标系下的原始测量值；
        # _post_process_rows() 将在遍历结束后统一将其变换到根坐标系。
        mp = mass_props or {}
        cog = mp.get("cog", [0.0, 0.0, 0.0])
        inertia = mp.get("inertia", [[0.0]*3 for _ in range(3)])

        row: dict = {
            "Level":        level,
            "Type":         node_type,
            "Part Number":  pn,
            # Filename 三态：文件路径为空 → "未检索到"；路径非空但磁盘不存在 → "未保存"；正常 → 文件名（不含扩展名）
            "Filename":     (FILENAME_UNSAVED   if no_file
                             else Path(filepath).stem if filepath
                             else FILENAME_NOT_FOUND),
            "Nomenclature": nomenclature,
            "Revision":     revision,
            "Density":      mp.get("density", None),  # kg/m³；-1.0 表示不统一；None 表示无密度数据
            "Weight":       mp.get("weight", None),
            "CogX":         cog[0] if mp else None,
            "CogY":         cog[1] if mp else None,
            "CogZ":         cog[2] if mp else None,
            "Ixx":          inertia[0][0] if mp else None,
            "Iyy":          inertia[1][1] if mp else None,
            "Izz":          inertia[2][2] if mp else None,
            "Ixy":          inertia[0][1] if mp else None,
            "Ixz":          inertia[0][2] if mp else None,
            "Iyz":          inertia[1][2] if mp else None,
            "_filepath":    filepath,
            "_placement":   abs_mat4,   # 零件局部坐标系 → 根产品坐标系的 4×4 变换矩阵
            "_not_found":   not_found,  # True： CATIA 无法解析文件引用（路径丢失）
            "_no_file":     no_file,    # True：路径有效但文件尚未保存到磁盘
            "_unreadable":  not is_readable,
            "_meas_failed": meas_failed,  # True：零件文档可访问但惯量包络体参数不存在
            "_mass_props":  mass_props,   # 原始测量值，供联动修改时使用
        }

        rows.append(row)
        _total_count += 1
        if progress_callback is not None:
            progress_callback(_total_count)

        # 递归遍历子节点
        # 注意：不跳过重复实例——同一文件多次出现时每个实例单独记录一行，
        # 质量特性通过 _mass_cache 共享，不会重复测量。
        try:
            count = product.Products.Count
            for i in range(1, count + 1):
                try:
                    child = product.Products.Item(i)
                    _traverse(child, rows, level + 1,
                              parent_filepath=filepath,
                              parent_mat4=abs_mat4)
                except Exception as e:
                    logger.debug(f"遍历子节点 {i} 失败: {e}")
        except Exception:
            pass

    # ── CATIA 连接与文档处理 ─────────────────────────────────────────────────
    application = get_catia_v5_application()
    application.Visible = True  # 确保 CATIA 窗口可见，避免后台静默状态下 COM 调用挂起
    documents   = application.Documents

    if file_path is None:
        # 使用当前 CATIA 活动文档（不做文件操作，直接读取）
        root_product = application.ActiveDocument.Product
        rows: list[dict] = []
        # 根节点的父矩阵为单位矩阵（无变换），从第 0 层开始遍历
        _traverse(root_product, rows, level=0, parent_filepath="",
                  parent_mat4=_identity_4x4())
        _post_process_rows(rows)
        # 遍历过程中 VBS 可能激活了各子零件文档；恢复活动文档为根产品
        try:
            application.ActiveDocument.Activate()
        except Exception as e:
            logger.debug(f"恢复根文档激活状态失败（无害）: {e}")
        return rows

    src = file_path

    from catia_copilot.utils import open_catia_file  # noqa: PLC0415
    target_doc = open_catia_file(documents, src)

    root_product = target_doc.Product
    rows = []
    # 根节点的父矩阵为单位矩阵，从第 0 层开始遍历
    _traverse(root_product, rows, level=0, parent_filepath="",
              parent_mat4=_identity_4x4())
    _post_process_rows(rows)
    # 遍历过程中 VBS 可能激活了各子零件文档；恢复活动文档为根产品
    try:
        target_doc.Activate()
    except Exception as e:
        logger.debug(f"恢复根文档激活状态失败（无害）: {e}")
    return rows
