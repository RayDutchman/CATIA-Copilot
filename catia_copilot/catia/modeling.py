"""
CATIA V5 建模操作模块。

提供面向 AI 工具层的高层建模函数，封装 pycatia / win32com 细节。
所有函数均通过项目自有的连接层（connection.wrap_application / wrap_product）
访问 CATIA，不使用 pycatia 的 catia() 入口。

单位约定
--------
所有几何参数（长度、坐标、深度、半径等）单位为 **mm**，与 CATIA 默认单位一致。

使用示例
--------
::

    from catia_copilot.catia.modeling import create_part, add_sketch, draw_rect, add_pad, add_shaft, add_groove, update_part

    part = create_part("MyPart")
    sk   = add_sketch(part, "xy")
    draw_rect(sk, 0, 0, 100, 50)
    pad  = add_pad(part, sk, 20)
    update_part(part)
"""

import logging
import math
import traceback as _tb
from dataclasses import replace
from typing import Callable, Literal

from pycatia.in_interfaces.reference import Reference as PyRef
from pycatia.mec_mod_interfaces.part_document import PartDocument
from pycatia.sketcher_interfaces.curve_2D import Curve2D as _PyCurve2D

from catia_copilot.catia.connection import (
    get_catia_v5_application,
    wrap_application,
    wrap_product,
)
from catia_copilot.catia.geometry_faces import (
    GeometryQueryError,
    KIND_PLANAR,
    STATUS_OK,
    SketchOutline,
    sketch_outline_from_element_types,
    read_error_outline,
    describe_sides,
    describe_surfaces,
    filter_faces_by_normal,
    side_neighbor_positions,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _get_part_from_active_doc():
    """从当前活动文档获取 pycatia Part 对象。"""
    app_com = get_catia_v5_application()
    return PartDocument(app_com.ActiveDocument).part


def _get_feature_en_name(part_com, feature_cn_name: str) -> str:
    """从特征的中文 UI 名推导其英文内部名（B-Rep 中使用的名称）。

    原理：构造一个临时 BRep Reference，读 DisplayName 中的英文名。
    例如：凸台.1 → Pad.1，凹槽.1 → Pocket.1，旋转体.1 → Shaft.1

    参数
    ----
    part_com         : win32com Part COM 对象（part.part，即 pycatia Part 的底层）
    feature_cn_name  : 特征的中文 UI 名称（如 "凸台.1"）

    返回
    ----
    英文内部名字符串（如 "Pad.1"）

    异常
    ----
    若无法推导则抛出 RuntimeError
    """
    import re
    brep_str = f"Face:(Brp:({feature_cn_name};2);None:())"
    try:
        ref = part_com.CreateReferenceFromBRepName(brep_str, part_com)
        dn  = ref.DisplayName
        m   = re.search(r"Brp:\(([^;]+);", dn)
        if m:
            return m.group(1)
    except Exception:
        pass
    # 回退：如果是已知映射，直接返回
    known = {
        "凸台": "Pad", "凹槽": "Pocket",
        "旋转体": "Shaft", "环形槽": "Groove",
    }
    for cn, en in known.items():
        if feature_cn_name.startswith(cn + "."):
            return en + feature_cn_name[len(cn):]
    raise RuntimeError(f"无法推导特征英文内部名：{feature_cn_name!r}")


def _plane_ref(part, plane: str):
    """将平面名称转换为 pycatia Reference 对象。

    参数
    ----
    part  : pycatia Part 对象
    plane : "xy" | "yz" | "zx"

    返回
    ----
    pycatia Reference（可直接传给 Sketches.add）

    注意：origin_elements.plane_xy/yz/zx 返回 AnyObject 而非 Reference，
    必须经 create_reference_from_object() 转换。
    """
    origin = part.origin_elements
    plane_lower = plane.lower()
    if plane_lower == "xy":
        plane_obj = origin.plane_xy
    elif plane_lower == "yz":
        plane_obj = origin.plane_yz
    elif plane_lower == "zx":
        plane_obj = origin.plane_zx
    else:
        raise ValueError(f"不支持的平面: {plane!r}，可选值为 'xy' / 'yz' / 'zx'")
    return part.create_reference_from_object(plane_obj)


# CATIA 手工“草图定位”在三个基准面上的默认局部坐标方向。
# 每组数据依次为：原点、H 轴、V 轴；法向由 H×V 决定。
_BASE_PLANE_AXIS_DATA = {
    "xy": (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
    "yz": (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
    "zx": (0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
}


def _set_base_plane_sketch_axis(sketch, plane: str) -> None:
    """复现 CATIA 手工选择基准面后创建“定位草图”的默认 H/V 方向。"""
    plane_lower = plane.lower()
    try:
        axis_data = _BASE_PLANE_AXIS_DATA[plane_lower]
    except KeyError as exc:
        raise ValueError(f"不支持的草图基准面: {plane!r}") from exc
    sketch.set_absolute_axis_data(axis_data)


# ---------------------------------------------------------------------------
# 文档与零件
# ---------------------------------------------------------------------------

def create_part(name: str = "Part", nomenclature: str = ""):
    """在 CATIA 中新建一个 CATPart 文档，返回 pycatia Part 对象。

    参数
    ----
    name         : 零件号（PartNumber），写入 Product.PartNumber，
                   也是特征树中零件节点显示的名称。
    nomenclature : 命名（Nomenclature），写入 Product.Nomenclature，
                   用于描述零件的通用名称/用途（如"底座"、"支架"）。
                   不传则不设置。

    返回
    ----
    pycatia ``Part`` 对象
    """
    app_py  = wrap_application()
    app_com = get_catia_v5_application()

    app_py.documents.add("Part")
    part_doc = PartDocument(app_com.ActiveDocument)
    part     = part_doc.part

    # 通过 Product COM 对象设置属性（CATIAPart 无 PartNumber，需走 Product 层）
    prod = app_com.ActiveDocument.Product
    prod.PartNumber = name
    if nomenclature:
        prod.Nomenclature = nomenclature

    logger.debug(f"[MODELING] create_part: PartNumber={name}")
    return part


def get_active_part():
    """获取当前活动文档的 pycatia Part 对象。

    若活动文档不是 CATPart，抛出 RuntimeError。
    """
    app_com = get_catia_v5_application()
    try:
        part_doc = PartDocument(app_com.ActiveDocument)
        return part_doc.part
    except Exception as e:
        raise RuntimeError(
            f"当前活动文档不是 CATPart，无法获取 Part 对象: {e}"
        )


def update_part(part) -> None:
    """刷新零件模型（等价于 CATIA 中点击"更新"）。"""
    part.update()
    logger.debug(f"[MODELING] update_part: {part.name}")


def save_part(part, path: str) -> None:
    """将零件文档另存为指定路径。

    参数
    ----
    part : pycatia Part 对象
    path : 目标文件完整路径（含 .CATPart 扩展名）
    """
    app_com = get_catia_v5_application()
    app_com.ActiveDocument.SaveAs(path)
    logger.debug(f"[MODELING] save_part -> {path}")


# ---------------------------------------------------------------------------
# 草图
# ---------------------------------------------------------------------------

def add_sketch(part, plane: Literal["xy", "yz", "zx"] = "xy"):
    """在指定基准平面上新建草图，返回 pycatia Sketch 对象。

    参数
    ----
    part  : pycatia Part 对象
    plane : 基准平面，"xy"（默认）/ "yz" / "zx"

    返回
    ----
    pycatia ``Sketch`` 对象（尚未进入编辑状态）
    """
    ref    = _plane_ref(part, plane)
    sketch = part.main_body.sketches.add(ref)
    _set_base_plane_sketch_axis(sketch, plane)
    logger.debug(f"[MODELING] add_sketch: {sketch.name} on {plane}")
    return sketch


def add_sketch_on_pad_top(part, pad):
    """在 Pad 特征的顶面直接建草图，返回 pycatia Sketch 对象。

    草图的支撑面就是 Pad 产生的 B-Rep 顶面（而非偏移平面），
    因此草图与 Pad 特征**真正关联**——Pad 深度参数修改时草图自动跟随。

    参数
    ----
    part : pycatia Part 对象
    pad  : pycatia Pad 对象（由 add_pad 返回）

    返回
    ----
    pycatia ``Sketch`` 对象（尚未进入编辑状态）

    草图坐标系
    ----------
    支撑面法向 = Z+（向上），H 轴 = X+，V 轴 = Y+，与 XY 基准面一致。
    草图坐标即为零件 XY 坐标，Z 固定在顶面高度。
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part

    cn_name = pad.name
    en_name = _get_feature_en_name(part_com, cn_name)

    ref_str = f"Selection_RSur:(Face:(Brp:({en_name};2);None:());{en_name}_ResultOUT)"
    ref_com = part_com.CreateReferenceFromName(ref_str)

    part.in_work_object = part.main_body
    sketch = part.main_body.sketches.add(PyRef(ref_com))

    logger.debug(f"[MODELING] add_sketch_on_pad_top: {sketch.name} on {en_name} top")
    return sketch


def add_sketch_on_pad_side(part, pad, edge_index: int):
    """在 Pad 特征的指定侧面直接建草图，返回 pycatia Sketch 对象。

    草图支撑面是 Pad 的 B-Rep 侧面，真正关联（Pad 深度/尺寸变化时草图自动跟随）。

    参数
    ----
    part       : pycatia Part 对象
    pad        : pycatia Pad 对象（由 add_pad 返回）
    edge_index : 草图轮廓边的索引（1 起），对应 Pad 拉伸时生成的侧面。
                 对于 ``draw_rect`` 生成的矩形草图，索引与边的对应关系：

                 绘制顺序（draw_rect 内部）：底边 → 右边 → 顶边 → 左边
                 对应零件坐标（草图原点在矩形左下角 (x,y)，宽 w，高 h）：

                 | edge_index | 侧面 | 草图法向 | 草图 origin |
                 |-----------|------|---------|------------|
                 | 1 | Y=y 的面 | Y- | (x, y, 0) |
                 | 2 | X=x+w 的面 | X+ | (x+w, y, 0) |
                 | 3 | Y=y+h 的面 | Y+ | (x+w, y+h, 0) |
                 | 4 | X=x 的面   | X- | (x, y+h, 0) |

    返回
    ----
    pycatia ``Sketch`` 对象（尚未进入编辑状态）

    草图坐标系
    ----------
    - H 轴：沿侧面宽度方向（与面的走向一致）
    - V 轴：Z+（拉伸高度方向）
    - 草图 H/V 坐标原点在侧面的一个角点

    原理
    ----
    VBA 宏录制发现的格式：
        ``Selection_RSur:(Face:(Brp:(<Pad英文名>;0:(Brp:(<Sketch英文名>;<edge_index>)));None:());<Pad英文名>_ResultOUT)``

    草图英文名规律：草图.N → Sketch.N（固定映射）。
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part

    # Pad 英文名
    cn_pad = pad.name
    en_pad = _get_feature_en_name(part_com, cn_pad)

    # 草图英文名：草图.N → Sketch.N
    cn_sk = pad.sketch.name                       # 如 "草图.1"
    en_sk = "Sketch." + cn_sk.split(".")[-1]      # → "Sketch.1"

    ref_str = (
        f"Selection_RSur:(Face:(Brp:({en_pad};0:(Brp:({en_sk};{edge_index})));"
        f"None:());{en_pad}_ResultOUT)"
    )
    ref_com = part_com.CreateReferenceFromName(ref_str)

    part.in_work_object = part.main_body
    sketch = part.main_body.sketches.add(PyRef(ref_com))

    logger.debug(
        f"[MODELING] add_sketch_on_pad_side: {sketch.name} on {en_pad} "
        f"side edge_index={edge_index}"
    )
    return sketch


def add_sketch_on_pad_bottom(part, pad):
    """在 Pad 特征的底面直接建草图，返回 pycatia Sketch 对象。

    参数
    ----
    part : pycatia Part 对象
    pad  : pycatia Pad 对象（由 add_pad 返回）

    草图坐标系
    ----------
    与 XY 基准面一致，Z 固定在 Pad 底面高度（通常为 0）。
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part

    cn_name = pad.name
    en_name = _get_feature_en_name(part_com, cn_name)

    ref_str = f"Selection_RSur:(Face:(Brp:({en_name};1);None:());{en_name}_ResultOUT)"
    ref_com = part_com.CreateReferenceFromName(ref_str)

    part.in_work_object = part.main_body
    sketch = part.main_body.sketches.add(PyRef(ref_com))

    logger.debug(f"[MODELING] add_sketch_on_pad_bottom: {sketch.name} on {en_name} bottom")
    return sketch


def add_sketch_at_height(part, height: float, base_plane: Literal["xy", "yz", "zx"] = "xy"):
    """在距基准平面偏移 ``height`` mm 处创建草图，返回 pycatia Sketch 对象。

    适用场景：在已有凸台的顶面（或任意偏移高度）继续建模，
    比直接使用 B-Rep 面引用更可靠。

    参数
    ----
    part        : pycatia Part 对象
    height      : 偏移距离（mm），正值沿平面法向远离原点
    base_plane  : 基准平面，"xy"（默认）/ "yz" / "zx"

    返回
    ----
    pycatia ``Sketch`` 对象（尚未进入编辑状态）
    """
    app_com = get_catia_v5_application()
    raw_part = app_com.ActiveDocument.Part

    # 将 InWorkObject 设为 PartBody，使后续插入追加到末尾而非插入到当前特征之前
    part.in_work_object = part.main_body

    hsf = part.hybrid_shape_factory
    base_ref = _plane_ref(part, base_plane)
    off_plane = hsf.add_new_plane_offset(base_ref, float(height), False)
    off_plane.name = f"偏移平面_{base_plane}_h{height:.3g}"
    raw_part.MainBody.InsertHybridShape(off_plane.com_object)
    part.update_object(off_plane)

    off_ref = part.create_reference_from_object(off_plane)
    sketch  = part.main_body.sketches.add(off_ref)
    _set_base_plane_sketch_axis(sketch, base_plane)
    logger.debug(f"[MODELING] add_sketch_at_height: {sketch.name} on {base_plane} offset={height}")
    return sketch


def draw_rect(sketch, x: float, y: float, width: float, height: float) -> None:
    """在草图中绘制矩形（四条首尾相连的直线），单位 mm。

    参数
    ----
    sketch         : pycatia Sketch 对象
    x, y           : 矩形左下角坐标（mm）
    width, height  : 宽度与高度（mm）
    """
    x2, y2 = x + width, y + height
    f2d = sketch.open_edition()
    f2d.create_line(x,  y,  x2, y )
    f2d.create_line(x2, y,  x2, y2)
    f2d.create_line(x2, y2, x,  y2)
    f2d.create_line(x,  y2, x,  y )
    sketch.close_edition()
    logger.debug(f"[MODELING] draw_rect: ({x},{y}) {width}x{height}")


def draw_circle(sketch, cx: float, cy: float, radius: float) -> None:
    """在草图中绘制圆，单位 mm。

    参数
    ----
    sketch        : pycatia Sketch 对象
    cx, cy        : 圆心坐标（mm）
    radius        : 半径（mm）
    """
    f2d = sketch.open_edition()
    f2d.create_circle(cx, cy, radius, 0, 2 * 3.141592653589793)
    sketch.close_edition()
    logger.debug(f"[MODELING] draw_circle: center=({cx},{cy}) r={radius}")


def draw_point(sketch, x: float, y: float):
    """在草图中创建一个点，返回 pycatia Point2D 对象。

    通常用于定位孔的中心（配合 add_hole_from_sketch）。
    """
    f2d    = sketch.open_edition()
    point  = f2d.create_point(x, y)
    sketch.close_edition()
    logger.debug(f"[MODELING] draw_point: ({x},{y})")
    return point


def draw_line(sketch, x1: float, y1: float, x2: float, y2: float) -> None:
    """在草图中绘制一条直线段，单位 mm。

    参数
    ----
    sketch     : pycatia Sketch 对象
    x1, y1     : 起点坐标（mm）
    x2, y2     : 终点坐标（mm）
    """
    f2d = sketch.open_edition()
    f2d.create_line(x1, y1, x2, y2)
    sketch.close_edition()
    logger.debug(f"[MODELING] draw_line: ({x1},{y1}) -> ({x2},{y2})")


def draw_arc(sketch, cx: float, cy: float, radius: float,
             start_angle: float, end_angle: float) -> None:
    """在草图中绘制圆弧，单位 mm，角度单位为度（°）。

    角度以水平右方向（3 点钟）为 0°，逆时针为正方向。

    参数
    ----
    sketch      : pycatia Sketch 对象
    cx, cy      : 圆心坐标（mm）
    radius      : 半径（mm）
    start_angle : 起始角度（°），范围 0~360
    end_angle   : 终止角度（°），范围 0~360；end > start 时为逆时针弧
    """
    import math
    start_rad = math.radians(start_angle)
    end_rad   = math.radians(end_angle)
    f2d = sketch.open_edition()
    f2d.create_circle(cx, cy, radius, start_rad, end_rad)
    sketch.close_edition()
    logger.debug(
        f"[MODELING] draw_arc: center=({cx},{cy}) r={radius} "
        f"{start_angle}°~{end_angle}°"
    )


def draw_slot(sketch, x1: float, y1: float, x2: float, y2: float,
              radius: float) -> None:
    """在草图中绘制腰形槽（两端半圆 + 两条直线），单位 mm。

    腰形槽的中轴线从 (x1, y1) 到 (x2, y2)，两端各为半径 radius 的半圆。

    参数
    ----
    sketch          : pycatia Sketch 对象
    x1, y1          : 中轴线起点（mm）
    x2, y2          : 中轴线终点（mm）
    radius          : 两端半圆半径（mm）

    注意
    ----
    腰形槽宽度 = 2 * radius，总长度 = dist(P1,P2) + 2 * radius
    """
    import math

    # 中轴线方向向量（单位化）
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise ValueError("draw_slot: 起点与终点不能重合")
    ux, uy = dx / length, dy / length   # 沿中轴方向单位向量
    nx, ny = -uy, ux                    # 法向量（垂直中轴，左侧）

    # 四个角点（矩形部分）
    p1l = (x1 + nx * radius, y1 + ny * radius)   # 起端左
    p1r = (x1 - nx * radius, y1 - ny * radius)   # 起端右
    p2l = (x2 + nx * radius, y2 + ny * radius)   # 末端左
    p2r = (x2 - nx * radius, y2 - ny * radius)   # 末端右

    # 弧的角度：从法向到反法向（180° 弧），用中轴方向推算
    axis_angle_deg = math.degrees(math.atan2(uy, ux))
    # 起端半圆：圆心在 (x1,y1)，从右侧 → 左侧（绕中轴起点的半圆，朝外）
    arc1_start = axis_angle_deg + 90.0
    arc1_end   = axis_angle_deg + 270.0
    # 末端半圆：圆心在 (x2,y2)，从左侧 → 右侧
    arc2_start = axis_angle_deg - 90.0
    arc2_end   = axis_angle_deg + 90.0

    def _deg_to_rad(d):
        return math.radians(d)

    f2d = sketch.open_edition()
    # 两条直线（腰部）
    f2d.create_line(p1l[0], p1l[1], p2l[0], p2l[1])
    f2d.create_line(p2r[0], p2r[1], p1r[0], p1r[1])
    # 两端半圆弧
    f2d.create_circle(x1, y1, radius, _deg_to_rad(arc1_start), _deg_to_rad(arc1_end))
    f2d.create_circle(x2, y2, radius, _deg_to_rad(arc2_start), _deg_to_rad(arc2_end))
    sketch.close_edition()
    logger.debug(
        f"[MODELING] draw_slot: ({x1},{y1})->({x2},{y2}) r={radius}"
    )


def _get_z_axis_ref(part_doc_com):
    """通过 HybridShapeFactory 创建 Z 轴方向线，命名为 'Z 轴' 并插入 MainBody，
    返回其 Reference（与 CATIA GUI 手动创建的 Z 轴行为一致）。

    参数
    ----
    part_doc_com : win32com 的 ActiveDocument COM 对象

    返回
    ----
    pycatia Reference（可直接赋给 shaft.revolute_axis / groove.revolute_axis）

    说明
    ----
    - body.InsertHybridShape(line) 将线直接插入 MainBody.HybridShapes，
      使其出现在旋转体子树下（与手工建旋转体的树结构一致）
    - 线命名为 "Z 轴"，与 CATIA GUI 行为一致
    - 之后可通过 body.HybridShapes.Item("Z 轴") 再次访问
    """
    return _get_axis_ref(part_doc_com, "Z 轴", (0, 0, 0), (0, 0, 1))


def _get_x_axis_ref(part_doc_com):
    """创建 X 轴方向线（(1,0,0)）并插入 MainBody，命名为 'X 轴'。"""
    return _get_axis_ref(part_doc_com, "X 轴", (0, 0, 0), (1, 0, 0))


def _get_y_axis_ref(part_doc_com):
    """创建 Y 轴方向线（(0,1,0)）并插入 MainBody，命名为 'Y 轴'。"""
    return _get_axis_ref(part_doc_com, "Y 轴", (0, 0, 0), (0, 1, 0))


def make_line_from_points(part_doc_com,
                          name: str,
                          pt_start: tuple, pt_end: tuple,
                          pt_start_name: str | None = None,
                          pt_end_name:   str | None = None):
    """通用：在 MainBody 中创建命名直线（两点式），构造点作为其子节点。

    参数
    ----
    part_doc_com  : win32com ActiveDocument COM 对象
    name          : 直线名称（显示在树中）
    pt_start      : 起点坐标 (x, y, z)
    pt_end        : 终点坐标 (x, y, z)
    pt_start_name : 起点命名，默认 "{name}_起"
    pt_end_name   : 终点命名，默认 "{name}_终"

    返回
    ----
    pycatia Reference（可直接赋给 revolute_axis / pad 方向等）

    树结构
    ------
    MainBody
    └─ <name>          ← 只 InsertHybridShape 线本身
         ├─ <pt_start_name>   ← 点作为线的子节点，可双击编辑坐标
         └─ <pt_end_name>

    说明
    ----
    - 构造点**不单独** InsertHybridShape，CATIA 会自动将其作为线的输入子节点显示
    - 若同名线已存在则直接复用，不重复插入
    """
    part_com = part_doc_com.Part
    body_com = part_com.MainBody
    hsf      = part_com.HybridShapeFactory

    # 复用已有
    try:
        existing = body_com.HybridShapes.Item(name)
        return PyRef(part_com.CreateReferenceFromObject(existing))
    except Exception:
        pass

    # 构造点（命名但不 Insert，挂为线的子节点）
    pt1 = hsf.AddNewPointCoord(*pt_start)
    pt2 = hsf.AddNewPointCoord(*pt_end)
    pt1.Name = pt_start_name or f"{name}_起"
    pt2.Name = pt_end_name   or f"{name}_终"

    # 只 Insert 线
    line = hsf.AddNewLinePtPt(
        part_com.CreateReferenceFromObject(pt1),
        part_com.CreateReferenceFromObject(pt2))
    line.Name = name
    body_com.InsertHybridShape(line)

    return PyRef(part_com.CreateReferenceFromObject(line))


def _get_axis_ref(part_doc_com, axis_name: str, pt_start: tuple, pt_end: tuple):
    """通用：在 MainBody 中创建（或复用）命名轴线，返回 Reference。

    构造点命名为 "{axis_name}_起" / "{axis_name}_终"，
    作为轴线的子节点显示（不单独插入）。
    """
    return make_line_from_points(part_doc_com, axis_name, pt_start, pt_end)


# ---------------------------------------------------------------------------
# 特征：旋转体 / 环形槽
# ---------------------------------------------------------------------------

def _get_named_axis_ref(part_doc_com, axis: str):
    """根据轴名称返回对应的 Reference。axis: 'x'/'y'/'z'（大小写不敏感）"""
    a = axis.lower()
    if a == "x":
        return _get_x_axis_ref(part_doc_com)
    elif a == "y":
        return _get_y_axis_ref(part_doc_com)
    elif a == "z":
        return _get_z_axis_ref(part_doc_com)
    else:
        raise ValueError(f"不支持的旋转轴: {axis!r}，可选值为 'x' / 'y' / 'z'")


def add_shaft(part, sketch, axis: str = "z"):
    """将草图旋转 360° 生成旋转体，返回 pycatia Shaft 对象。

    参数
    ----
    part   : pycatia Part 对象
    sketch : ZX / YZ / XY 平面上的闭合轮廓草图
    axis   : 旋转轴，"x" / "y" / "z"（默认 "z"）
             - axis="z"：草图须在 ZX 平面，轴向为 V(Z)，轮廓在 H>0 侧
             - axis="x"：草图须在 XY 平面，轮廓在 H>0 侧
             - axis="y"：草图须在 YZ 平面，轮廓在 H>0 侧

    返回
    ----
    pycatia ``Shaft`` 对象
    """
    app_com = get_catia_v5_application()

    axis_ref = _get_named_axis_ref(app_com.ActiveDocument, axis)
    shaft    = part.shape_factory.add_new_shaft(sketch)
    shaft.revolute_axis = axis_ref

    logger.debug(f"[MODELING] add_shaft: {shaft.name}, axis={axis}")
    return shaft


def add_groove(part, sketch, axis: str = "z"):
    """在已有实体上按草图旋转切除（环形槽），返回 pycatia Groove 对象。

    参数
    ----
    part   : pycatia Part 对象（必须已有实体）
    sketch : 闭合矩形草图，位于实体内部
    axis   : 旋转轴，"x" / "y" / "z"（默认 "z"）

    返回
    ----
    pycatia ``Groove`` 对象
    """
    app_com = get_catia_v5_application()

    axis_ref = _get_named_axis_ref(app_com.ActiveDocument, axis)
    groove   = part.shape_factory.add_new_groove(sketch)
    groove.revolute_axis = axis_ref

    logger.debug(f"[MODELING] add_groove: {groove.name}, axis={axis}")
    return groove


def ensure_revolute_axis(part, axis: str = "z"):
    """在 MainBody 中提前创建旋转轴线（若已存在则复用），返回 Reference。

    **必须在 add_sketch 之前调用**，使轴线节点出现在草图节点之前，
    保证特征树顺序与 CATIA GUI 手工操作一致。

    参数
    ----
    part : pycatia Part 对象
    axis : "x" / "y" / "z"（默认 "z"）

    返回
    ----
    pycatia Reference（可传给 shaft.revolute_axis，但 add_shaft 会自动获取，无需手动传）

    示例
    ----
    ::

        ctx.prepare_revolute_axis(part, "y")   # ← 先建轴线（在草图之前）
        sk = ctx.add_sketch(part, "xy")         # ← 再建草图
        ctx.draw_rect(sk, 25, 0, 25, 80)
        shaft = ctx.add_shaft(part, sk, axis="y")
        ctx.update_part(part)
    """
    app_com = get_catia_v5_application()
    return _get_named_axis_ref(app_com.ActiveDocument, axis)


    """根据轴名称返回对应的 Reference。

    参数
    ----
    axis : "x" / "y" / "z"（大小写不敏感）
    """
    a = axis.lower()
    if a == "x":
        return _get_x_axis_ref(part_doc_com)
    elif a == "y":
        return _get_y_axis_ref(part_doc_com)
    elif a == "z":
        return _get_z_axis_ref(part_doc_com)
    else:
        raise ValueError(f"不支持的旋转轴: {axis!r}，可选值为 'x' / 'y' / 'z'")


def add_pad(part, sketch, depth: float,
            symmetric: bool = False,
            second_depth: float | None = None):
    """将草图拉伸指定深度，返回 pycatia Pad 对象。

    参数
    ----
    part         : pycatia Part 对象
    sketch       : 截面草图（pycatia Sketch）
    depth        : 第一限制拉伸深度，mm（朝草图法向正方向）
    symmetric    : 对称拉伸，以草图平面为中心双向等量拉伸（总厚度 = 2×depth）。
                   为 True 时忽略 second_depth。
    second_depth : 第二限制拉伸深度（mm），仅当 symmetric=False 时有效。
                   None（默认）表示单向拉伸；传值时为双向非对称拉伸。

    返回
    ----
    pycatia ``Pad`` 对象

    示例
    ----
    对称拉伸（上下各 10mm，总厚 20mm）::

        pad = add_pad(part, sk, 10, symmetric=True)

    双向非对称拉伸（正向 20mm，反向 5mm）::

        pad = add_pad(part, sk, 20, second_depth=5)
    """
    pad = part.shape_factory.add_new_pad(sketch, depth)

    if symmetric:
        pad.is_symmetric = True
        logger.debug(f"[MODELING] add_pad: {pad.name}, depth={depth}mm, symmetric=True")
    elif second_depth is not None:
        pad.second_limit.dimension.value = second_depth
        logger.debug(
            f"[MODELING] add_pad: {pad.name}, depth={depth}mm, second={second_depth}mm"
        )
    else:
        logger.debug(f"[MODELING] add_pad: {pad.name}, depth={depth}mm")

    return pad


def add_pocket(part, sketch, depth: float):
    """在实体上按草图挖槽，返回 pycatia Pocket 对象。

    参数
    ----
    part   : pycatia Part 对象
    sketch : 截面草图（pycatia Sketch）
    depth  : 挖槽深度，mm
    """
    pocket = part.shape_factory.add_new_pocket(sketch, depth)
    logger.debug(f"[MODELING] add_pocket: {pocket.name}, depth={depth}mm")
    return pocket


def add_hole_from_sketch(part, sketch, diameter: float, depth: float):
    """以草图（含点）为定位依据打简单盲孔，返回 pycatia Hole 对象。

    参数
    ----
    part     : pycatia Part 对象
    sketch   : 定位草图（草图中应含圆或点，作为孔中心）
    diameter : 孔径，mm
    depth    : 孔深，mm
    """
    hole = part.shape_factory.add_new_hole_from_sketch(sketch, depth)
    hole.diameter.value = diameter
    logger.debug(f"[MODELING] add_hole: d={diameter}mm, depth={depth}mm")
    return hole


# ---------------------------------------------------------------------------
# 特征：修饰（圆角 / 倒角）
# ---------------------------------------------------------------------------

# B-Rep 面描述字符串辅助函数
# 这些函数返回字符串，用于传入 make_edge_ref_from_pad 构造边引用

def _brep_face_top(en_pad: str) -> str:
    """Pad 顶面的 BRep 描述字符串（idx=2）。"""
    return f"Face:(Brp:({en_pad};2);None:();Cf14:())"

def _brep_face_bottom(en_pad: str) -> str:
    """Pad 底面的 BRep 描述字符串（idx=1）。"""
    return f"Face:(Brp:({en_pad};1);None:();Cf14:())"

def _brep_face_side(en_pad: str, en_sk: str, edge_index: int) -> str:
    """Pad 侧面的 BRep 描述字符串。
    edge_index：草图轮廓边索引（1 起），对 draw_rect：1=前/2=右/3=后/4=左。
    """
    return f"Face:(Brp:({en_pad};0:(Brp:({en_sk};{edge_index})));None:();Cf14:())"

def _make_edge_brep(face_a: str, face_b: str) -> str:
    """由两个面 BRep 字符串构造边 BRep 字符串（两面交线）。"""
    return (
        f"REdge:(Edge:({face_a};{face_b};"
        f"None:(Limits1:();Limits2:());Cf14:());"
        f"WithTemporaryBody;WithoutBuildError;"
        f"WithSelectingFeatureSupport;MFBRepVersion_CXR29)"
    )


def make_pad_edge_ref(part, pad, face_a_brep: str, face_b_brep: str):
    """由两个面 BRep 字符串构造 Pad 上的边引用（pycatia Reference）。

    这是构造边引用的通用低层函数，配合 ``_brep_face_top`` /
    ``_brep_face_bottom`` / ``_brep_face_side`` 使用。

    参数
    ----
    part        : pycatia Part 对象
    pad         : pycatia Pad 对象（边所属的特征）
    face_a_brep : 第一个面的 BRep 字符串（用 ``_brep_face_*`` 辅助函数生成）
    face_b_brep : 第二个面的 BRep 字符串

    返回
    ----
    win32com Reference COM 对象（可直接传给 ``add_fillet_edges``）

    示例
    ----
    在矩形 Pad（draw_rect 生成）上构造边引用::

        en_pad = _get_feature_en_name(part_com, pad.name)   # "Pad.1"
        en_sk  = "Sketch." + pad.sketch.name.split(".")[-1]  # "Sketch.1"

        # 侧楞（相邻两侧面交线）
        edge = make_pad_edge_ref(part, pad,
            _brep_face_side(en_pad, en_sk, 2),
            _brep_face_side(en_pad, en_sk, 1))

        # 顶楞（侧面与顶面交线）
        edge = make_pad_edge_ref(part, pad,
            _brep_face_side(en_pad, en_sk, 1),
            _brep_face_top(en_pad))
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    pad_com  = part_com.MainBody.Shapes.Item(pad.name)

    edge_brep = _make_edge_brep(face_a_brep, face_b_brep)
    return part_com.CreateReferenceFromBRepName(edge_brep, pad_com)


def add_fillet_edges(part, edge_refs: list, radius: float):
    """对任意一组边施加等半径倒圆角，返回圆角特征 COM 对象。

    这是倒圆角的核心低层 API，接受任意边引用列表，通用性最强。
    边引用通过 ``make_pad_edge_ref`` 构造。

    参数
    ----
    part      : pycatia Part 对象
    edge_refs : 边引用列表（``make_pad_edge_ref`` 返回的 COM 对象列表）
    radius    : 圆角半径，mm

    返回
    ----
    圆角特征 COM 对象

    示例（对矩形 Pad 的 4 条侧楞 + 4 条顶楞共 8 条边倒圆角）
    ----------------------------------------------------------
    ::

        en_pad = _get_feature_en_name(part_com, pad.name)
        en_sk  = "Sketch." + pad.sketch.name.split(".")[-1]

        edges = []
        # 4 条侧楞
        for n in range(1, 5):
            edges.append(make_pad_edge_ref(part, pad,
                _brep_face_side(en_pad, en_sk, n % 4 + 1),
                _brep_face_side(en_pad, en_sk, n)))
        # 4 条顶楞
        for n in range(1, 5):
            edges.append(make_pad_edge_ref(part, pad,
                _brep_face_side(en_pad, en_sk, n),
                _brep_face_top(en_pad)))

        fillet = add_fillet_edges(part, edges, 5.0)
        update_part(part)

    注意
    ----
    - ``update_part`` 需在本函数调用后单独调用
    - ``catTangencyFilletEdgePropagation = 1``（切线传播模式，与 CATIA GUI 默认一致）
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    sf       = part_com.ShapeFactory

    CAT_TANGENCY_PROP = 1
    empty_ref = part_com.CreateReferenceFromName("")
    fillet    = sf.AddNewSolidEdgeFilletWithConstantRadius(
        empty_ref, CAT_TANGENCY_PROP, float(radius)
    )
    for ref in edge_refs:
        fillet.AddObjectToFillet(ref)

    logger.debug(f"[MODELING] add_fillet_edges: {len(edge_refs)} edges, r={radius}mm")
    return fillet


def add_auto_fillet(part, radius: float, inner_radius: float | None = None):
    """对零件所有可圆角的边自动施加等半径圆角，返回 AutoFillet COM 对象。

    等价于 CATIA GUI「修饰特征 → 自动圆角」功能。
    与 add_fillet_edges 的区别：无需指定边，CATIA 自动选取所有适合的边。

    参数
    ----
    part         : pycatia Part 对象
    radius       : 外角圆角半径，mm
    inner_radius : 内角圆角半径，mm（默认与 radius 相同）

    返回
    ----
    AutoFillet COM 对象

    注意
    ----
    调用后需 update_part。
    """
    r_inner = inner_radius if inner_radius is not None else radius
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    sf       = part_com.ShapeFactory

    auto = sf.AddNewAutoFillet(float(radius), float(r_inner))
    auto.RoundRadiusActivation = True

    logger.debug(
        f"[MODELING] add_auto_fillet: r={radius}mm, r_inner={r_inner}mm"
    )
    return auto


def add_chamfer(part, edge_ref, length: float, angle: float = 45.0):
    """对指定边倒角，返回 pycatia Chamfer 对象。

    参数
    ----
    part     : pycatia Part 对象
    edge_ref : pycatia Reference（边的引用）
    length   : 倒角长度，mm
    angle    : 倒角角度，度（默认 45°）
    """
    chamfer = part.shape_factory.add_new_chamfer(edge_ref, 1, length, angle)
    logger.debug(f"[MODELING] add_chamfer: l={length}mm, angle={angle}°")
    return chamfer


# ---------------------------------------------------------------------------
# 特征：阵列
# ---------------------------------------------------------------------------

def add_rect_pattern(part, feature, nx: int, ny: int,
                     dx: float, dy: float):
    """对特征执行矩形阵列，返回 pycatia RectPattern 对象。

    参数
    ----
    part    : pycatia Part 对象
    feature : 要阵列的特征（pycatia Shape 对象）
    nx, ny  : X / Y 方向数量（含原始特征）
    dx, dy  : X / Y 方向间距，mm
    """
    pattern = part.shape_factory.add_new_rect_pattern(
        feature, nx, ny, dx, dy, 1, 1, feature
    )
    logger.debug(f"[MODELING] add_rect_pattern: {nx}x{ny}, step=({dx},{dy})mm")
    return pattern


def add_circ_pattern(part, feature, count: int, total_angle: float = 360.0):
    """对特征执行圆形阵列，返回 pycatia CircPattern 对象。

    参数
    ----
    part        : pycatia Part 对象
    feature     : 要阵列的特征
    count       : 阵列数量（含原始特征）
    total_angle : 总角度范围，度（默认 360° 均匀分布）
    """
    angular_step = total_angle / count if count > 1 else total_angle
    pattern = part.shape_factory.add_new_circ_pattern(
        feature, count, angular_step, 1, 1, feature, feature
    )
    logger.debug(f"[MODELING] add_circ_pattern: n={count}, total={total_angle}°")
    return pattern


# ---------------------------------------------------------------------------
# 查询（AI 感知当前模型状态）
# ---------------------------------------------------------------------------

def list_features(part) -> list[str]:
    """返回当前 MainBody 中所有特征的名称列表。

    供 AI 感知模型当前状态（"现在有哪些特征"）。
    """
    try:
        shapes = part.main_body.shapes
        return [shapes.item(i + 1).name for i in range(shapes.count)]
    except Exception as e:
        logger.debug(f"[MODELING] list_features 失败: {e}")
        return []


def list_sketches(part) -> list[str]:
    """返回当前 MainBody 中所有草图的名称列表。"""
    try:
        sketches = part.main_body.sketches
        return [sketches.item(i + 1).name for i in range(sketches.count)]
    except Exception as e:
        logger.debug(f"[MODELING] list_sketches 失败: {e}")
        return []


def get_mass_props(part) -> dict | None:
    """读取零件的质量特性（质量、重心、惯量），通过 Analyze API 实时计算。

    零件须已赋材料，否则返回 None。

    返回字典（SI 单位）：
      {
        "mass":    float,          # kg
        "cog":     [x, y, z],      # mm，零件局部坐标系
        "inertia": [[3×3]],        # kg·m²，重心处
      }
    """
    try:
        app_com = get_catia_v5_application()
        analyze = wrap_product(app_com.ActiveDocument.Product).analyze
        mass    = analyze.mass
        if not mass or mass <= 0:
            return None
        cog = list(analyze.get_gravity_center())      # mm
        raw = analyze.get_inertia()                   # kg·m²，9 元素
        inertia = [[raw[r * 3 + c] for c in range(3)] for r in range(3)]
        return {"mass": mass, "cog": cog, "inertia": inertia}
    except Exception as e:
        logger.debug(f"[MODELING] get_mass_props 失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 几何查询（面 / 边）
# ---------------------------------------------------------------------------

# --- 通用辅助 ---

def _get_sk_edge_count_from_sketch(sketch_com) -> int | None:
    """兼容保留的旧计数接口：返回过滤后的草图边数；读失败返回 None（不再默认 4）。内部不再使用。"""
    return _get_sketch_outline(sketch_com).edge_count


def _edge_endpoints(sketch_com, raw_i) -> tuple | None:
    """尽力读取 GE 项的 (x0, y0, x1, y1)；失败返回 None。
    路径 A：_PyCurve2D 包装原始 GE 项 + get_end_points()（Line2D 继承 Curve2D）；
    路径 B：start_point/end_point.get_coordinates()。两路径方法存在性已静态读 pycatia 定义确认
    （pycatia.sketcher_interfaces.curve_2D：get_end_points / start_point / end_point；Point2D.get_coordinates），
    但原始 GE 项能否成功包装与真实 COM 值由 Task4 B1/B2 实机裁决。端点读取失败只影响 normal，不影响枚举。"""
    def valid(values):
        values = tuple(float(value) for value in values)
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            raise ValueError("曲线端点须包含四个有限坐标")
        return values

    try:
        return valid(_PyCurve2D(sketch_com.GeometricElements.Item(raw_i)).get_end_points())
    except Exception:
        pass
    try:
        cu = _PyCurve2D(sketch_com.GeometricElements.Item(raw_i))
        return valid((*cu.start_point.get_coordinates(), *cu.end_point.get_coordinates()))
    except Exception:
        return None


def _get_sketch_outline(sketch_com) -> SketchOutline:
    """枚举草图轮廓：GeometricType + 原始索引 + 尽力端点。
    GE 整体枚举失败 → 经 read_error_outline 构造 status=READ_ERROR，不抛、不作任何默认（原返回 4 的兜底移除）。
    GeometricType（CatGeometricType 静态枚举定义，真实 COM 值由 Task4 B2 复核）：1=Axis2D,2=Point2D,
    3=Line2D,4=ControlPoint2D,5=Circle2D（整圆与圆弧）,8=Ellipse2D,9=Spline2D。3D 类型(10..12)、
    unknown(0)与控制点(4)不录入。"""
    try:
        ge = sketch_com.GeometricElements
        types = [ge.Item(i).GeometricType for i in range(1, ge.Count + 1)]
    except Exception as exc:
        return read_error_outline(f"GeometricElements 枚举失败: {exc}")
    outline = sketch_outline_from_element_types(types)   # 纯模块：ordinal/raw/kind/status=ok
    filled = []
    for edge in outline.edges:
        ep = _edge_endpoints(sketch_com, edge.raw_collection_index)
        st, en = ((ep[0], ep[1]), (ep[2], ep[3])) if ep else (None, None)
        filled.append(replace(edge, start=st, end=en))   # from dataclasses import replace
    return replace(outline, edges=tuple(filled))


def _make_feature_edge_ref(part, feature, face_a_brep: str, face_b_brep: str):
    """通用：由两个面 BRep 字符串构造特征上的边引用。

    feature 可以是 Pad / Pocket / Shaft 等任意特征的 COM 对象或
    pycatia 包装对象（会自动取 .com_object）。
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    # 兼容 pycatia 包装对象和原始 COM 对象
    try:
        feat_com = feature.com_object
    except AttributeError:
        feat_com = part_com.MainBody.Shapes.Item(feature.name)
    edge_brep = _make_edge_brep(face_a_brep, face_b_brep)
    return part_com.CreateReferenceFromBRepName(edge_brep, feat_com)


def _pad_geometry(pad) -> dict:
    """从 Pad 对象推导几何信息（不依赖 SPA）。

    返回 dict：
      normal    : 顶面法向单位向量 (nx,ny,nz)
      h_axis    : 草图 H 轴方向
      v_axis    : 草图 V 轴方向
      sk_origin : 草图原点 (ox,oy,oz)
      depth1    : 第一限制深度（顶面方向）
      depth2    : 第二限制深度（底面方向）
      top_origin    : 顶面原点坐标
      bottom_origin : 底面原点坐标
      en_pad    : Pad 英文内部名（如 "Pad.1"）
      en_sk     : 草图英文内部名（如 "Sketch.1"）
      outline         : 草图轮廓推断（SketchOutline，GUI 读取失败为 READ_ERROR 状态，不 raise）
      profile_edge_count : 候选侧面数（=过滤后草图边数；读失败为 None，非最终拓扑面数）
    """
    import math

    from pycatia.sketcher_interfaces.sketch import Sketch as PySketch

    def _cross(a, b):
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    def _normalize(v):
        m = math.sqrt(sum(x*x for x in v))
        return tuple(x/m for x in v) if m > 1e-9 else v
    def _vadd(a, b): return tuple(x+y for x,y in zip(a,b))
    def _vmul(v, s): return tuple(x*s for x in v)

    psk    = PySketch(pad.sketch.com_object)
    ax     = psk.get_absolute_axis_data()
    h_axis = tuple(ax[3:6])
    v_axis = tuple(ax[6:9])
    normal = _normalize(_cross(h_axis, v_axis))
    sk_origin = tuple(ax[0:3])
    depth1 = pad.first_limit.dimension.value
    depth2 = pad.second_limit.dimension.value

    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    en_pad   = _get_feature_en_name(part_com, pad.name)
    en_sk    = "Sketch." + pad.sketch.name.split(".")[-1]

    outline = _get_sketch_outline(pad.sketch.com_object)

    return {
        "normal":        normal,
        "h_axis":        h_axis,
        "v_axis":        v_axis,
        "sk_origin":     sk_origin,
        "depth1":        depth1,
        "depth2":        depth2,
        "top_origin":    _vadd(sk_origin, _vmul(normal,  depth1)),
        "bottom_origin": _vadd(sk_origin, _vmul(normal, -depth2)),
        "en_pad":        en_pad,
        "en_sk":         en_sk,
        "outline":       outline,
        "profile_edge_count": outline.edge_count,
    }


def get_pad_faces(part, pad) -> list[dict]:
    """返回 Pad 所有面的描述列表。

    每项包含：

    - ``type``        : ``"top"`` / ``"bottom"`` / ``"side"``
    - ``source``      : ``"feature_inference"``（特征+草图推断的候选面，非最终拓扑枚举）
    - ``geometry_type`` : ``"planar"`` / ``"cylindrical"`` / ``"unknown"``（按草图边类型归类）
    - ``normal``      : 面法向单位向量 (nx, ny, nz)，朝向实体外侧；圆柱/未解析时可能为 None
    - ``normal_unresolved`` : normal 为 None 时的中文原因；已解析为 None
    - ``origin``      : 面上一点坐标（顶/底面为草图原点偏移，侧面有端点时为真实 3D 点）
    - ``face_brep``   : 面的 BRep 字符串，传给 ``make_pad_edge_ref``
    - ``edge_index``  : 仅 side 时有效，草图边索引（1 起）
    - ``edge_count``  : 候选侧面数（outline.edge_count 语义）
    - ``error``       : 恒 None（读失败改为抛 GeometryQueryError）

    参数
    ----
    part : pycatia Part 对象
    pad  : pycatia Pad 对象

    返回
    ----
    list[dict]，顺序：顶面、底面、侧面 1…N

    异常
    ----
    草图轮廓不可枚举时抛 GeometryQueryError（不再静默返回 4 边）。
    """

    def _neg(v): return tuple(-x for x in v)

    geo = _pad_geometry(pad)
    n   = geo["normal"]
    en  = geo["en_pad"]
    es  = geo["en_sk"]
    outline = geo["outline"]
    if outline.status != STATUS_OK:
        raise GeometryQueryError(
            f"get_pad_faces 无法枚举 {pad.name} 草图轮廓：{outline.note}；"
            "已停止，不再默认4边；请用 list_features 确认特征状态或重建草图")

    faces = []

    # 顶面
    faces.append({
        "type":              "top",
        "source":            "feature_inference",
        "geometry_type":     KIND_PLANAR,
        "normal":            n,
        "normal_unresolved": None,
        "origin":            geo["top_origin"],
        "face_brep":         _brep_face_top(en),
        "edge_index":        None,
        "edge_count":        outline.edge_count,
        "error":             None,
    })

    # 底面（法向朝下 = -normal）
    faces.append({
        "type":              "bottom",
        "source":            "feature_inference",
        "geometry_type":     KIND_PLANAR,
        "normal":            _neg(n),
        "normal_unresolved": None,
        "origin":            geo["bottom_origin"],
        "face_brep":         _brep_face_bottom(en),
        "edge_index":        None,
        "edge_count":        outline.edge_count,
        "error":             None,
    })

    # 侧面：纯几何规则（geometry_faces.describe_sides）逐边推导外法向与真实面上 3D 点
    brep_map = {e.index: _brep_face_side(en, es, e.index) for e in outline.edges}
    faces += describe_sides(outline, brep_map, geo["h_axis"], geo["v_axis"],
                            origin=geo["sk_origin"], outward=True)

    logger.debug(f"[MODELING] get_pad_faces: {pad.name}, {len(faces)} faces")
    return faces


def get_pad_faces_by_normal(part, pad, normal: tuple,
                            tolerance_deg: float = 5.0) -> list[dict]:
    """按法向筛选 Pad 的面（纯过滤，无零向量兜底）。

    参数
    ----
    part          : pycatia Part 对象
    pad           : pycatia Pad 对象
    normal        : 目标法向 (nx, ny, nz)，不需要单位化
    tolerance_deg : 角度容差，度（默认 5°）

    返回
    ----
    符合条件的面描述列表（可能有多个）。

    异常
    ----
    目标法向非法（非有限向量/零向量）或容差越界抛 ValueError；
    草图轮廓不可枚举时抛 GeometryQueryError（经 get_pad_faces 传导）；
    face["normal"] 为 None 的面自动跳过（圆柱/未解析/未知法向不参与筛选）。
    """
    return filter_faces_by_normal(get_pad_faces(part, pad), normal, tolerance_deg, planar_only=True)


def get_pad_face_edges(part, pad, face_info: dict) -> list:
    """返回 Pad 某一面的所有边引用列表（可直接传给 add_fillet_edges）。

    参数
    ----
    part      : pycatia Part 对象
    pad       : pycatia Pad 对象
    face_info : ``get_pad_faces`` 返回的单个面描述 dict

    返回
    ----
    边引用 COM 对象列表（每项为两面交线的引用）

    原理
    ----
    - 顶面的边 = 顶面与每个侧面的交线
    - 底面的边 = 底面与每个侧面的交线
    - 侧面的边 = 该侧面与顶面 + 底面 + 相邻两侧面的交线
    """
    all_faces = get_pad_faces(part, pad)
    top_faces    = [f for f in all_faces if f["type"] == "top"]
    bottom_faces = [f for f in all_faces if f["type"] == "bottom"]
    side_faces   = [f for f in all_faces if f["type"] == "side"]

    edges = []
    fa_brep = face_info["face_brep"]
    ftype   = face_info["type"]

    if ftype == "top":
        # 顶面的边：顶面 × 每个侧面
        for sf in side_faces:
            edges.append(make_pad_edge_ref(part, pad, fa_brep, sf["face_brep"]))

    elif ftype == "bottom":
        # 底面的边：底面 × 每个侧面
        for sf in side_faces:
            edges.append(make_pad_edge_ref(part, pad, fa_brep, sf["face_brep"]))

    elif ftype == "side":
        # 相邻面按列表位置（side_neighbor_positions），不再把 ordinal 当列表 rank
        neighbors = side_neighbor_positions(side_faces, face_info["edge_index"])
        # 侧面的边：与顶面、底面、相邻两侧面
        if top_faces:
            edges.append(make_pad_edge_ref(part, pad, fa_brep, top_faces[0]["face_brep"]))
        if bottom_faces:
            edges.append(make_pad_edge_ref(part, pad, fa_brep, bottom_faces[0]["face_brep"]))
        if neighbors is not None and neighbors[0] != neighbors[1]:
            prev_j, next_j = neighbors
            edges.append(make_pad_edge_ref(part, pad, fa_brep,
                                           side_faces[prev_j]["face_brep"]))
            edges.append(make_pad_edge_ref(part, pad, fa_brep,
                                           side_faces[next_j]["face_brep"]))

    logger.debug(
        f"[MODELING] get_pad_face_edges: {pad.name} {ftype}, {len(edges)} edges"
    )
    return edges
    # （此处无多余代码）


# ---------------------------------------------------------------------------
# 几何查询：Pocket
# ---------------------------------------------------------------------------

def _pocket_geometry(pocket) -> dict:
    """从 Pocket 对象推导几何信息。

    Pocket 面编号规律（由宏验证）：
      - 底面（挖槽最深处）：Pocket.N;2
      - 侧面：Pocket.N;0:(Brp:(Sketch.M;K))
      - 开口面不属于 Pocket 本身，是下层特征（如 Pad）的面

    返回 dict：
      en_pocket     : Pocket 英文内部名（如 "Pocket.1"）
      en_sk         : 草图英文内部名（如 "Sketch.2"）
      outline       : 草图轮廓推断（SketchOutline，GUI 读取失败为 READ_ERROR 状态，不 raise）
      profile_edge_count : 候选侧面数（读失败为 None）
      normal        : 法向（草图 H×V，指向开口方向）
      h_axis        : 草图 H 轴
      v_axis        : 草图 V 轴
      sk_origin     : 草图原点 (ox,oy,oz)
      depth         : 挖槽深度
    """
    import math

    from pycatia.sketcher_interfaces.sketch import Sketch as PySketch

    psk    = PySketch(pocket.sketch.com_object)
    ax     = psk.get_absolute_axis_data()
    h_axis = tuple(ax[3:6])
    v_axis = tuple(ax[6:9])

    def _cross(a, b):
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    def _normalize(v):
        m = math.sqrt(sum(x*x for x in v))
        return tuple(x/m for x in v) if m > 1e-9 else v

    normal = _normalize(_cross(h_axis, v_axis))

    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    en_pocket = _get_feature_en_name(part_com, pocket.name)
    en_sk     = "Sketch." + pocket.sketch.name.split(".")[-1]
    outline = _get_sketch_outline(pocket.sketch.com_object)

    try:
        depth = pocket.first_limit.dimension.value
    except Exception:
        depth = 0.0

    return {
        "en_pocket":     en_pocket,
        "en_sk":         en_sk,
        "outline":       outline,
        "profile_edge_count": outline.edge_count,
        "normal":        normal,
        "h_axis":        h_axis,
        "v_axis":        v_axis,
        "sk_origin":     tuple(ax[0:3]),
        "depth":         depth,
    }


def get_pocket_faces(part, pocket) -> list[dict]:
    """返回 Pocket 自身面的描述列表（不含开口面，开口面属于下层特征）。

    每项 dict 包含：
      type       : "bottom"（挖槽底面）或 "side"（侧面）
      source     : "feature_inference"（特征+草图推断的候选面，非最终拓扑枚举）
      geometry_type : "planar" / "cylindrical" / "unknown"（按草图边类型归类）
      normal     : 面法向单位向量，朝向实体内侧（槽内朝外）；圆柱/未解析时可能为 None
      normal_unresolved : normal 为 None 时的中文原因；已解析为 None
      face_brep  : BRep 字符串，传给 make_feature_edge_ref
      edge_index : 仅 side 时有效（1 起）
      edge_count : 候选侧面数（outline.edge_count 语义）
      error      : 恒 None（读失败改为抛 GeometryQueryError）

    注意：Pocket 开口面 = 下层 Pad 的顶面（用 get_pad_faces_by_normal 查询）。

    异常
    ----
    草图轮廓不可枚举时抛 GeometryQueryError（不再静默返回 4 边）。
    Pocket 侧面法向 = Pad 外法向反向（B4 实机核验）。
    """
    geo = _pocket_geometry(pocket)
    en  = geo["en_pocket"]
    es  = geo["en_sk"]
    h   = geo["h_axis"]
    v   = geo["v_axis"]
    outline = geo["outline"]
    if outline.status != STATUS_OK:
        raise GeometryQueryError(
            f"get_pocket_faces 无法枚举 {pocket.name} 草图轮廓：{outline.note}；"
            "已停止，不再默认4边；请用 list_features 确认特征状态或重建草图")
    # Pocket 法向指向开口，底面法向 = 开口方向（从底面朝外指向槽口）
    n = geo["normal"]

    faces = []

    # 底面（挖槽最深处，idx=2）
    # 底面法向朝向槽内开口方向（= +normal，即从底面指向开口）
    faces.append({
        "type":              "bottom",
        "source":            "feature_inference",
        "geometry_type":     KIND_PLANAR,
        "normal":            n,
        "normal_unresolved": None,
        "origin":            None,
        "face_brep":         f"Face:(Brp:({en};2);None:();Cf14:())",
        "edge_index":        None,
        "edge_count":        outline.edge_count,
        "error":             None,
    })

    # 侧面：纯几何规则推导；Pocket 侧面法向 = Pad 外法向反向（outward=False）
    brep_map = {e.index: f"Face:(Brp:({en};0:(Brp:({es};{e.index})));None:();Cf14:())"
                for e in outline.edges}
    faces += describe_sides(outline, brep_map, h, v,
                            origin=geo["sk_origin"], outward=False)

    logger.debug(f"[MODELING] get_pocket_faces: {pocket.name}, {len(faces)} faces")
    return faces


def get_pocket_face_edges(part, pocket, face_info: dict,
                          pad_for_opening=None) -> list:
    """返回 Pocket 某一面的所有边引用列表。

    参数
    ----
    part             : pycatia Part 对象
    pocket           : pycatia Pocket 对象
    face_info        : get_pocket_faces 返回的单个面 dict
    pad_for_opening  : 可选。若 face_info 是底面且需要构造底楞，
                       传入 pocket 所在的 Pad 对象以获取开口面 BRep；
                       通常不需要此参数（底楞=底面×侧面）。

    返回
    ----
    边引用 COM 对象列表

    底面的边 = 底面 × 每个侧面
    侧面的边 = 该侧面 × 底面 + 相邻侧面
    """
    all_faces  = get_pocket_faces(part, pocket)
    bot_faces  = [f for f in all_faces if f["type"] == "bottom"]
    side_faces = [f for f in all_faces if f["type"] == "side"]

    edges   = []
    fa_brep = face_info["face_brep"]
    ftype   = face_info["type"]

    if ftype == "bottom":
        for sf in side_faces:
            edges.append(_make_feature_edge_ref(part, pocket, fa_brep, sf["face_brep"]))

    elif ftype == "side":
        # 相邻面按列表位置（side_neighbor_positions），不再把 ordinal 当列表 rank
        neighbors = side_neighbor_positions(side_faces, face_info["edge_index"])
        if bot_faces:
            edges.append(_make_feature_edge_ref(part, pocket, fa_brep, bot_faces[0]["face_brep"]))
        if neighbors is not None and neighbors[0] != neighbors[1]:
            prev_j, next_j = neighbors
            edges.append(_make_feature_edge_ref(part, pocket, fa_brep,
                                                side_faces[prev_j]["face_brep"]))
            edges.append(_make_feature_edge_ref(part, pocket, fa_brep,
                                                side_faces[next_j]["face_brep"]))

    logger.debug(
        f"[MODELING] get_pocket_face_edges: {pocket.name} {ftype}, {len(edges)} edges"
    )
    return edges


def get_pocket_opening_edges(part, pocket, pad) -> list:
    """返回 Pocket 开口楞的边引用列表（开口面 = Pad 顶面）。

    开口楞 = Pad 顶面 × Pocket 每个侧面的交线。

    参数
    ----
    part   : pycatia Part 对象
    pocket : pycatia Pocket 对象
    pad    : Pocket 所在的 Pad 对象（开口面属于 Pad）

    返回
    ----
    边引用 COM 对象列表（条数 = Pocket 侧面数）
    """
    geo_pad  = _pad_geometry(pad)
    top_brep = _brep_face_top(geo_pad["en_pad"])

    pocket_faces = get_pocket_faces(part, pocket)
    side_faces   = [f for f in pocket_faces if f["type"] == "side"]

    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    pocket_com = part_com.MainBody.Shapes.Item(pocket.name)

    edges = []
    for sf in side_faces:
        edge_brep = _make_edge_brep(top_brep, sf["face_brep"])
        edges.append(part_com.CreateReferenceFromBRepName(edge_brep, pocket_com))

    logger.debug(
        f"[MODELING] get_pocket_opening_edges: {pocket.name}, {len(edges)} edges"
    )
    return edges


# ---------------------------------------------------------------------------
# 几何查询：Shaft（旋转体）
# ---------------------------------------------------------------------------

def _shaft_geometry(shaft) -> dict:
    """从 Shaft 对象推导几何信息。

    Shaft 面编号规律（由宏验证）：
      - 所有面都用侧面格式：Shaft.N;0:(Brp:(Sketch.M;K))
      - K 对应草图轮廓边索引（对矩形轮廓旋转：
          外圆面=草图外边, 上端面=上边, 内圆面=内边, 下端面=下边）
      - 无独立的 top/bottom (idx;1/2) 格式

    返回 dict：
      en_shaft      : Shaft 英文内部名（如 "Shaft.1"）
      en_sk         : 草图英文内部名（如 "Sketch.1"）
       outline       : 草图轮廓推断
       profile_edge_count : 候选旋转面数（读取失败为 None）
    """
    app_com  = get_catia_v5_application()
    part_com = app_com.ActiveDocument.Part
    en_shaft = _get_feature_en_name(part_com, shaft.name)
    en_sk    = "Sketch." + shaft.sketch.name.split(".")[-1]
    outline = _get_sketch_outline(shaft.sketch.com_object)

    return {
        "en_shaft":      en_shaft,
        "en_sk":         en_sk,
        "outline":       outline,
        "profile_edge_count": outline.edge_count,
    }


def get_shaft_faces(part, shaft) -> list[dict]:
    """返回 Shaft（旋转体）所有面的描述列表。

    每项 dict 包含：
      type       : "surface"（Shaft 无顶/底概念，统一用 surface）
      source     : "feature_inference"（候选面，不是最终拓扑枚举）
      geometry_type / normal / normal_unresolved : S2 暂不解析，分别为 unknown / None / 原因
      origin     : None（当前无可靠坐标依据）
      face_brep  : BRep 字符串，传给 make_feature_edge_ref
      edge_index : 过滤后草图边序号（1 起）
      edge_count : 候选旋转面数

    注意：旋转面类型依赖轮廓边相对旋转轴的方向，当前不伪造平面/圆柱分类。
    使用 get_shaft_face_edges 可自动枚举相邻面的交线。
    """
    geo = _shaft_geometry(shaft)
    en  = geo["en_shaft"]
    es  = geo["en_sk"]
    outline = geo["outline"]
    if outline.status != STATUS_OK:
        raise GeometryQueryError(
            f"get_shaft_faces 无法枚举 {shaft.name} 草图轮廓：{outline.note}；"
            "已停止，不再默认4边；请用 list_features 确认特征状态或重建草图")

    brep_map = {
        edge.index: f"Face:(Brp:({en};0:(Brp:({es};{edge.index})));None:();Cf14:())"
        for edge in outline.edges
    }
    faces = describe_surfaces(outline, brep_map)

    logger.debug(f"[MODELING] get_shaft_faces: {shaft.name}, {len(faces)} faces")
    return faces


def get_shaft_face_edges(part, shaft, face_info: dict) -> list:
    """返回 Shaft 某一面与相邻面的交线边引用列表。

    参数
    ----
    part      : pycatia Part 对象
    shaft     : pycatia Shaft 对象
    face_info : get_shaft_faces 返回的单个面 dict

    返回
    ----
    边引用 COM 对象列表（该面与所有相邻面的交线）

    原理：按返回面列表中的循环位置寻找相邻面，不把原始草图集合索引当作 BRep 编号。
    """
    all_faces = get_shaft_faces(part, shaft)
    fa_brep   = face_info["face_brep"]

    edges = []
    neighbors = side_neighbor_positions(all_faces, face_info["edge_index"])
    if neighbors is not None and neighbors[0] != neighbors[1]:
        prev_i, next_i = neighbors
        edges.append(_make_feature_edge_ref(
            part, shaft, fa_brep, all_faces[prev_i]["face_brep"]))
        edges.append(_make_feature_edge_ref(
            part, shaft, fa_brep, all_faces[next_i]["face_brep"]))

    logger.debug(
        f"[MODELING] get_shaft_face_edges: {shaft.name} ei={face_info['edge_index']}, {len(edges)} edges"
    )
    return edges


# ---------------------------------------------------------------------------
# 逐步执行上下文（M0：结构化反馈）
# ---------------------------------------------------------------------------

class ModelingStepError(Exception):
    """建模步骤异常，携带已完成步骤记录和失败上下文。

    由 ModelingContext 内部抛出，供 tool_run_modeling_script 捕获并格式化为
    结构化 JSON 返回给 AI。
    """
    def __init__(
        self,
        step_name: str,
        original_error: Exception,
        steps_completed: list,
        features_at_failure: list,
        traceback_str: str,
    ):
        super().__init__(str(original_error))
        self.step_name          = step_name          # 失败的步骤名称
        self.original_error     = original_error     # 原始异常
        self.steps_completed    = steps_completed    # 失败前已成功的步骤列表
        self.features_at_failure = features_at_failure  # 失败时的特征树
        self.traceback_str      = traceback_str      # 完整 traceback 字符串


class ModelingCancelledError(Exception):
    """建模在步骤边界收到取消请求；不承诺打断正在执行的 COM 调用。"""


class ModelingContext:
    """建模执行上下文，供 AI 生成的脚本通过 build(ctx) 调用。

    设计目标
    --------
    - 代理 modeling.py 中所有公开函数，AI 只需操作 ctx，不必 import。
    - 每次函数调用自动记录步骤名称、执行状态、调用后的特征树。
    - 函数调用失败时，抛出 ModelingStepError，携带完整的步骤历史，
      使 tool_run_modeling_script 能返回"第几步在哪里失败"的精确信息。
    - ctx.step(name) 可选：在多个函数调用之间插入语义化里程碑标记。

    脚本写法示例
    ------------
    ::

        def build(ctx):
            part = ctx.create_part("底座")
            sk   = ctx.add_sketch(part, "xy")
            ctx.draw_rect(sk, 0, 0, 100, 50)
            pad  = ctx.add_pad(part, sk, 20)
            ctx.step("底座主体完成")          # 可选里程碑

            sk2  = ctx.add_sketch(part, "xy")
            ctx.draw_circle(sk2, 50, 25, 15)
            ctx.add_pocket(part, sk2, 10)
            ctx.step("挖槽完成")

            ctx.update_part(part)
    """

    def __init__(self, cancel_check: Callable[[], bool] | None = None):
        self._steps: list[dict] = []  # 步骤记录列表
        self._part = None             # 最后一次操作的 Part，用于 features 快照
        self._cancel_check = cancel_check

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _snapshot(self) -> list[str]:
        """安全地读取当前特征树快照，失败时返回空列表。"""
        if self._part is None:
            return []

    def _raise_if_cancelled(self) -> None:
        """仅在下一步开始前检查取消，不在 COM 调用中轮询。"""
        if self._cancel_check is not None and self._cancel_check():
            raise ModelingCancelledError("建模已取消；已停止继续执行后续步骤")
        try:
            return list_features(self._part)
        except Exception:
            return []

    def _run(self, func_label: str, fn, *args, **kwargs):
        """执行一个建模函数，记录结果，失败时抛出 ModelingStepError。

        参数
        ----
        func_label : 步骤标签（如 "add_pad(depth=20)"）
        fn         : 要调用的函数
        *args/**kwargs : 传给 fn 的参数
        """
        try:
            self._raise_if_cancelled()
            result = fn(*args, **kwargs)
            # 成功：记录步骤
            self._steps.append({
                "step":           func_label,
                "status":         "ok",
                "features_after": self._snapshot(),
            })
            return result
        except ModelingCancelledError:
            # 取消不是建模失败，不包装成 ModelingStepError；工具层负责上报 cancelled。
            raise
        except Exception as exc:
            tb_str = _tb.format_exc()
            feats  = self._snapshot()
            # 记录失败步骤
            self._steps.append({
                "step":           func_label,
                "status":         "error",
                "error":          str(exc),
                "traceback":      tb_str,
                "features_after": feats,
            })
            raise ModelingStepError(
                step_name          = func_label,
                original_error     = exc,
                steps_completed    = [s for s in self._steps if s["status"] == "ok"],
                features_at_failure = feats,
                traceback_str      = tb_str,
            ) from exc

    # ------------------------------------------------------------------
    # 手动里程碑
    # ------------------------------------------------------------------

    def step(self, name: str, feature=None) -> None:
        """插入一个命名里程碑，记录当前特征树状态。

        不执行任何 CATIA 操作，纯粹用于给 AI 的反馈结构加注语义标签。
        """
        self._raise_if_cancelled()
        self._steps.append({
            "step":           name,
            "status":         "ok",
            "milestone":      True,
            "features_after": self._snapshot(),
        })

    @property
    def steps(self) -> list[dict]:
        """返回已记录的步骤列表（只读）。"""
        return list(self._steps)

    # ------------------------------------------------------------------
    # 文档与零件
    # ------------------------------------------------------------------

    def create_part(self, name: str = "Part", nomenclature: str = ""):
        """新建 CATPart，设置零件号和命名，返回 Part 对象。

        name         : 零件号（PartNumber），显示在特征树节点上。
        nomenclature : 命名（Nomenclature），描述零件用途（如"底座"）。
        """
        result = self._run(
            f"create_part({name!r})",
            create_part, name, nomenclature)
        self._part = result
        return result

    def get_active_part(self):
        """获取活动文档的 Part 对象。"""
        result = self._run("get_active_part()", get_active_part)
        self._part = result
        return result

    def update_part(self, part) -> None:
        """刷新零件模型。"""
        self._part = part
        self._run("update_part()", update_part, part)

    def save_part(self, part, path: str) -> None:
        """另存为指定路径。"""
        self._run(f"save_part({path!r})", save_part, part, path)

    def prepare_revolute_axis(self, part, axis: str = "z"):
        """提前在 MainBody 中创建旋转轴线（若已存在则复用）。

        **必须在 add_sketch 之前调用**，确保轴线节点在草图节点之前出现在特征树中。
        axis: "x" / "y" / "z"（默认 "z"）
        """
        self._part = part
        self._run(f"prepare_revolute_axis(axis={axis!r})",
                  ensure_revolute_axis, part, axis)

    # ------------------------------------------------------------------
    # 草图
    # ------------------------------------------------------------------

    def add_sketch(self, part, plane="xy"):
        """在基准平面上新建草图。plane: 'xy' / 'yz' / 'zx'"""
        self._part = part
        return self._run(f"add_sketch(plane={plane!r})", add_sketch, part, plane)

    def add_sketch_at_height(self, part, height: float, base_plane="xy"):
        """在距基准平面 height mm 处建草图（偏移平面）。
        适用于在凸台顶面继续建模，比 B-Rep 面引用更可靠。"""
        self._part = part
        return self._run(
            f"add_sketch_at_height(h={height}, base={base_plane!r})",
            add_sketch_at_height, part, height, base_plane)

    def add_sketch_on_pad_top(self, part, pad):
        """在 Pad 顶面直接建草图（B-Rep 面支撑，关联 Pad 深度）。"""
        self._part = part
        return self._run(
            f"add_sketch_on_pad_top(pad={pad.name})",
            add_sketch_on_pad_top, part, pad)

    def add_sketch_on_pad_side(self, part, pad, edge_index: int):
        """在 Pad 侧面直接建草图（B-Rep 面支撑，真正关联）。

        edge_index：草图轮廓边索引（1 起）。
        对于 draw_rect(x,y,w,h) 生成的矩形：
          1=Y=y 的面，2=X=x+w 的面，3=Y=y+h 的面，4=X=x 的面。
        草图 V 轴 = Z+（拉伸高度），H 轴沿面宽度方向。
        """
        self._part = part
        return self._run(
            f"add_sketch_on_pad_side(pad={pad.name}, edge={edge_index})",
            add_sketch_on_pad_side, part, pad, edge_index)

    def add_sketch_on_pad_bottom(self, part, pad):
        """在 Pad 底面直接建草图（B-Rep 面支撑，关联）。"""
        self._part = part
        return self._run(
            f"add_sketch_on_pad_bottom(pad={pad.name})",
            add_sketch_on_pad_bottom, part, pad)

    def draw_rect(self, sketch, x: float, y: float,
                  width: float, height: float) -> None:
        """在草图中绘制矩形。"""
        self._run(f"draw_rect(x={x}, y={y}, w={width}, h={height})",
                  draw_rect, sketch, x, y, width, height)

    def draw_circle(self, sketch, cx: float, cy: float, radius: float) -> None:
        """在草图中绘制圆。"""
        self._run(f"draw_circle(cx={cx}, cy={cy}, r={radius})",
                  draw_circle, sketch, cx, cy, radius)

    def draw_point(self, sketch, x: float, y: float):
        """在草图中创建定位点。"""
        return self._run(f"draw_point(x={x}, y={y})", draw_point, sketch, x, y)

    def draw_line(self, sketch, x1: float, y1: float,
                  x2: float, y2: float) -> None:
        """在草图中绘制一条直线段。"""
        self._run(f"draw_line(({x1},{y1})->({x2},{y2}))",
                  draw_line, sketch, x1, y1, x2, y2)

    def draw_arc(self, sketch, cx: float, cy: float, radius: float,
                 start_angle: float, end_angle: float) -> None:
        """在草图中绘制圆弧。角度单位：度（°），逆时针，0° = 水平右方向。"""
        self._run(
            f"draw_arc(cx={cx}, cy={cy}, r={radius}, {start_angle}°~{end_angle}°)",
            draw_arc, sketch, cx, cy, radius, start_angle, end_angle)

    def draw_slot(self, sketch, x1: float, y1: float,
                  x2: float, y2: float, radius: float) -> None:
        """在草图中绘制腰形槽（两端半圆 + 两条直线）。"""
        self._run(
            f"draw_slot(({x1},{y1})->({x2},{y2}) r={radius})",
            draw_slot, sketch, x1, y1, x2, y2, radius)

    # ------------------------------------------------------------------
    # 特征：拉伸 / 挖槽 / 孔
    # ------------------------------------------------------------------

    def add_pad(self, part, sketch, depth: float,
                symmetric: bool = False,
                second_depth: float | None = None):
        """拉伸草图，返回 Pad 对象。

        symmetric=True：以草图平面为中心双向对称（总厚 2×depth）。
        second_depth=N：双向非对称，正向 depth，反向 N（mm）。
        """
        self._part = part
        label = f"add_pad(depth={depth}"
        if symmetric:
            label += ", symmetric=True"
        elif second_depth is not None:
            label += f", second={second_depth}"
        label += ")"
        return self._run(label, add_pad, part, sketch, depth, symmetric, second_depth)

    def add_pocket(self, part, sketch, depth: float):
        """挖槽，返回 Pocket 对象。"""
        self._part = part
        return self._run(f"add_pocket(depth={depth})", add_pocket, part, sketch, depth)

    def add_shaft(self, part, sketch, axis: str = "z"):
        """旋转草图生成旋转体（360°），返回 Shaft 对象。

        axis: 旋转轴 "x" / "y" / "z"（默认 "z"）
        草图平面与旋转轴的对应关系：
          axis="z" → 草图在 ZX 平面；axis="x" → XY 平面；axis="y" → YZ 平面
        轮廓须全在 H>0 侧（不跨越旋转轴）。调用后需 update_part。
        """
        self._part = part
        return self._run(f"add_shaft(axis={axis!r})", add_shaft, part, sketch, axis)

    def add_groove(self, part, sketch, axis: str = "z"):
        """在已有实体上旋转切除（环形槽），返回 Groove 对象。

        前提：Part 已有实体且已 update_part。
        axis: 旋转轴 "x" / "y" / "z"（默认 "z"）
        """
        self._part = part
        return self._run(f"add_groove(axis={axis!r})", add_groove, part, sketch, axis)

    def add_hole_from_sketch(self, part, sketch,
                             diameter: float, depth: float):
        """以草图定位打盲孔，返回 Hole 对象。"""
        self._part = part
        return self._run(
            f"add_hole_from_sketch(d={diameter}, depth={depth})",
            add_hole_from_sketch, part, sketch, diameter, depth,
        )

    # ------------------------------------------------------------------
    # 特征：修饰（圆角）
    # ------------------------------------------------------------------

    def get_pad_face_brep(self, pad, face: str, edge_index: int = 1) -> str:
        """返回 Pad 指定面的 BRep 字符串，供 make_pad_edge_ref 使用。

        face       : "top" / "bottom" / "side"
        edge_index : 仅 face="side" 时有效，草图边索引（1 起）。
                     对 draw_rect(x,y,w,h)：1=前/2=右/3=后/4=左

        返回字符串，不计入步骤记录。
        """
        app_com  = get_catia_v5_application()
        part_com = app_com.ActiveDocument.Part
        en_pad   = _get_feature_en_name(part_com, pad.name)
        en_sk    = "Sketch." + pad.sketch.name.split(".")[-1]

        if face == "top":
            return _brep_face_top(en_pad)
        elif face == "bottom":
            return _brep_face_bottom(en_pad)
        elif face == "side":
            return _brep_face_side(en_pad, en_sk, edge_index)
        else:
            raise ValueError(f"face 必须是 'top'/'bottom'/'side'，收到: {face!r}")

    def make_pad_edge_ref(self, part, pad, face_a_brep: str, face_b_brep: str):
        """由两个面 BRep 字符串构造 Pad 上的边引用（两面交线）。

        配合 get_pad_face_brep 使用：先获取面 BRep 字符串，再构造边引用。

        示例（矩形 Pad 的一条侧楞）::

            fa = ctx.get_pad_face_brep(pad, "side", 2)
            fb = ctx.get_pad_face_brep(pad, "side", 1)
            edge = ctx.make_pad_edge_ref(part, pad, fa, fb)

        示例（矩形 Pad 的一条顶楞）::

            fa = ctx.get_pad_face_brep(pad, "side", 1)
            fb = ctx.get_pad_face_brep(pad, "top")
            edge = ctx.make_pad_edge_ref(part, pad, fa, fb)

        返回 COM 引用对象，直接传入 add_fillet_edges 的列表中。
        不计入步骤记录。
        """
        return make_pad_edge_ref(part, pad, face_a_brep, face_b_brep)

    def add_fillet_edges(self, part, edge_refs: list, radius: float):
        """对任意一组边施加等半径倒圆角，返回圆角特征 COM 对象。

        edge_refs：由 make_pad_edge_ref 构造的 COM 引用列表。
        调用后需单独调用 update_part。

        示例（对矩形 Pad 8 条楞倒圆角，r=5mm）::

            edges = []
            for n in range(1, 5):
                # 4 条侧楞
                edges.append(ctx.make_pad_edge_ref(part, pad,
                    ctx.get_pad_face_brep(pad, "side", n % 4 + 1),
                    ctx.get_pad_face_brep(pad, "side", n)))
            for n in range(1, 5):
                # 4 条顶楞
                edges.append(ctx.make_pad_edge_ref(part, pad,
                    ctx.get_pad_face_brep(pad, "side", n),
                    ctx.get_pad_face_brep(pad, "top")))
            ctx.add_fillet_edges(part, edges, 5.0)
            ctx.update_part(part)
        """
        self._part = part
        return self._run(
            f"add_fillet_edges(n={len(edge_refs)}, r={radius})",
            add_fillet_edges, part, edge_refs, radius)

    def add_auto_fillet(self, part, radius: float,
                        inner_radius: float | None = None):
        """对零件所有可圆角的边自动施加圆角。

        无需指定边，CATIA 自动选取所有适合的边（等价于 GUI「自动圆角」）。
        调用后需 update_part。

        radius       : 外角圆角半径，mm
        inner_radius : 内角圆角半径，mm（默认与 radius 相同）
        """
        self._part = part
        r_label = f"r={radius}" + (f", ri={inner_radius}" if inner_radius else "")
        return self._run(
            f"add_auto_fillet({r_label})",
            add_auto_fillet, part, radius, inner_radius)

    # ------------------------------------------------------------------
    # 特征：阵列
    # ------------------------------------------------------------------

    def add_rect_pattern(self, part, feature,
                         nx: int, ny: int, dx: float, dy: float):
        """矩形阵列。"""
        self._part = part
        return self._run(
            f"add_rect_pattern(nx={nx}, ny={ny}, dx={dx}, dy={dy})",
            add_rect_pattern, part, feature, nx, ny, dx, dy,
        )

    def add_circ_pattern(self, part, feature,
                         count: int, total_angle: float = 360.0):
        """圆形阵列。"""
        self._part = part
        return self._run(
            f"add_circ_pattern(count={count}, angle={total_angle})",
            add_circ_pattern, part, feature, count, total_angle,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_features(self, part) -> list[str]:
        """返回当前特征列表（不计入步骤记录）。"""
        return list_features(part)

    def list_sketches(self, part) -> list[str]:
        """返回当前草图列表（不计入步骤记录）。"""
        return list_sketches(part)

    def get_mass_props(self, part) -> dict | None:
        """读取质量特性（不计入步骤记录）。"""
        return get_mass_props(part)

    # ------------------------------------------------------------------
    # 几何查询（面 / 边）
    # ------------------------------------------------------------------

    def get_pad_faces(self, part, pad) -> list[dict]:
        """返回 Pad 所有面的描述列表（不计入步骤记录）。

        每项 dict 包含：
          type       : "top" / "bottom" / "side"
          source     : "feature_inference"（特征+草图推断的候选面）
          geometry_type : planar / cylindrical / unknown
          normal     : 仅已确认的 planar 面有法向；其他情况为 None
          origin     : 面上一点坐标；无法确认时为 None
          face_brep  : BRep 字符串，传给 make_pad_edge_ref
          edge_index : 仅 side 时有效，草图边索引（1 起）
          edge_count : 候选侧面数，不代表最终拓扑面数

        轮廓无法枚举时抛出 GeometryQueryError，不再按矩形默认四边。
        """
        return get_pad_faces(part, pad)

    def get_pad_faces_by_normal(self, part, pad, normal: tuple,
                                tolerance_deg: float = 5.0) -> list[dict]:
        """按法向筛选 Pad 的面（不计入步骤记录）。

        normal        : 目标法向 (nx,ny,nz)，如 (0,0,1) 表示朝上
        tolerance_deg : 角度容差，度（默认 5°），必须在 0~180°

        仅匹配有 normal 的 planar 面；圆柱面和未解析面自动跳过。

        示例——找顶面::

            faces = ctx.get_pad_faces_by_normal(part, pad, (0,0,1))
            # faces[0] 即顶面描述

        示例——找朝 Y- 的侧面::

            faces = ctx.get_pad_faces_by_normal(part, pad, (0,-1,0))
        """
        return get_pad_faces_by_normal(part, pad, normal, tolerance_deg)

    def get_pad_face_edges(self, part, pad, face_info: dict) -> list:
        """返回 Pad 某一面的所有边引用列表（不计入步骤记录）。

        face_info : get_pad_faces / get_pad_faces_by_normal 返回的单个面 dict
        返回值    : 边引用列表，可直接传给 add_fillet_edges

        示例——对顶面所有棱倒圆角::

            top = ctx.get_pad_faces_by_normal(part, pad, (0,0,1))[0]
            edges = ctx.get_pad_face_edges(part, pad, top)
            ctx.add_fillet_edges(part, edges, 3.0)
            ctx.update_part(part)
        """
        return get_pad_face_edges(part, pad, face_info)

    # ------------------------------------------------------------------
    # 几何查询：Pocket
    # ------------------------------------------------------------------

    def get_pocket_faces(self, part, pocket) -> list[dict]:
        """返回 Pocket 自身面的描述列表（不含开口面）。

        每项：{type, source, geometry_type, normal, normal_unresolved,
        origin, face_brep, edge_index, edge_count, error}
          type="bottom" : 挖槽底面（idx=2）
          type="side"   : 侧面（idx=草图边索引）

        开口面不属于 Pocket 自身，而是下层 Pad 的顶面。
        Pocket 侧面法向按槽内语义取对应 Pad 外侧法向的反向值；
        圆柱/未解析面不伪造法向。轮廓无法枚举时抛 GeometryQueryError。
        """
        return get_pocket_faces(part, pocket)

    def get_pocket_face_edges(self, part, pocket, face_info: dict) -> list:
        """返回 Pocket 某一面的所有边引用列表。

        face_info : get_pocket_faces 返回的单个面 dict
        返回值    : 边引用列表，可直接传给 add_fillet_edges

        底面 → 与所有侧面的交线
        侧面 → 与底面 + 相邻侧面的交线
        """
        return get_pocket_face_edges(part, pocket, face_info)

    def get_pocket_opening_edges(self, part, pocket, pad) -> list:
        """返回 Pocket 开口楞的边引用列表（开口面 = 下层 Pad 顶面）。

        pad    : Pocket 所在的 Pad 对象
        返回值 : 边引用列表（条数 = Pocket 侧面数），可直接传给 add_fillet_edges

        示例——对 Pocket 开口所有楞倒圆角::

            edges = ctx.get_pocket_opening_edges(part, pocket, pad)
            ctx.add_fillet_edges(part, edges, 2.0)
            ctx.update_part(part)
        """
        return get_pocket_opening_edges(part, pocket, pad)

    # ------------------------------------------------------------------
    # 几何查询：Shaft（旋转体）
    # ------------------------------------------------------------------

    def get_shaft_faces(self, part, shaft) -> list[dict]:
        """返回 Shaft（旋转体）所有面的描述列表。

        每项：{type="surface", source, geometry_type="unknown",
        normal=None, normal_unresolved, origin=None, face_brep, edge_index,
        edge_count, error}
        edge_index 对应草图轮廓边索引（1 起）。

        注意：Shaft 所有面均用侧面格式，无独立的 top/bottom 编号；
        当前不对旋转面类型和全局法向作未经拓扑解析的推断。
        轮廓无法枚举时抛 GeometryQueryError。
        """
        return get_shaft_faces(part, shaft)

    def get_shaft_face_edges(self, part, shaft, face_info: dict) -> list:
        """返回 Shaft 某一面与相邻面的交线边引用列表。

        face_info : get_shaft_faces 返回的单个面 dict
        返回值    : 边引用列表，可直接传给 add_fillet_edges

        示例——对外圆与上端面的交线倒圆角（假设外圆面是边索引2）::

            faces = ctx.get_shaft_faces(part, shaft)
            # 找边索引为2的面
            outer = next(f for f in faces if f["edge_index"] == 2)
            edges = ctx.get_shaft_face_edges(part, shaft, outer)
            ctx.add_fillet_edges(part, edges, 3.0)
            ctx.update_part(part)
        """
        return get_shaft_face_edges(part, shaft, face_info)
