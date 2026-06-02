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

    from catia_copilot.catia.modeling import create_part, add_sketch, draw_rect, add_pad, update_part

    part = create_part("MyPart")
    sk   = add_sketch(part, "xy")
    draw_rect(sk, 0, 0, 100, 50)
    pad  = add_pad(part, sk, 20)
    update_part(part)
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _get_part_from_active_doc():
    """从当前活动文档获取 pycatia Part 对象。"""
    from catia_copilot.catia.connection import get_catia_v5_application
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    app_com = get_catia_v5_application()
    return PartDocument(app_com.ActiveDocument).part


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


# ---------------------------------------------------------------------------
# 文档与零件
# ---------------------------------------------------------------------------

def create_part(name: str = "Part"):
    """在 CATIA 中新建一个 CATPart 文档，返回 pycatia Part 对象。

    参数
    ----
    name : 零件名称，会设置为 Part 的 PartNumber。

    返回
    ----
    pycatia ``Part`` 对象
    """
    from catia_copilot.catia.connection import wrap_application, get_catia_v5_application
    from pycatia.mec_mod_interfaces.part_document import PartDocument

    app_py  = wrap_application()
    app_com = get_catia_v5_application()

    app_py.documents.add("Part")
    part_doc = PartDocument(app_com.ActiveDocument)
    part = part_doc.part

    # 设置零件编号
    try:
        part.part.PartNumber = name
    except Exception:
        pass

    logger.debug(f"[MODELING] create_part: {part.name}")
    return part


def get_active_part():
    """获取当前活动文档的 pycatia Part 对象。

    若活动文档不是 CATPart，抛出 RuntimeError。
    """
    from catia_copilot.catia.connection import get_catia_v5_application
    from pycatia.mec_mod_interfaces.part_document import PartDocument

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
    from catia_copilot.catia.connection import get_catia_v5_application
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
    logger.debug(f"[MODELING] add_sketch: {sketch.name} on {plane}")
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


# ---------------------------------------------------------------------------
# 特征：拉伸 / 挖槽 / 孔
# ---------------------------------------------------------------------------

def add_pad(part, sketch, depth: float):
    """将草图拉伸指定深度，返回 pycatia Pad 对象。

    参数
    ----
    part   : pycatia Part 对象
    sketch : 截面草图（pycatia Sketch）
    depth  : 拉伸深度，mm

    返回
    ----
    pycatia ``Pad`` 对象
    """
    pad = part.shape_factory.add_new_pad(sketch, depth)
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
    hole = part.shape_factory.add_new_hole_from_sketch(sketch, diameter / 2.0, depth)
    logger.debug(f"[MODELING] add_hole: d={diameter}mm, depth={depth}mm")
    return hole


# ---------------------------------------------------------------------------
# 特征：修饰（圆角 / 倒角）
# ---------------------------------------------------------------------------

def add_edge_fillet(part, edge_ref, radius: float):
    """对指定边倒圆角，返回 pycatia EdgeFillet 对象。

    参数
    ----
    part     : pycatia Part 对象
    edge_ref : pycatia Reference（边的引用）
    radius   : 圆角半径，mm
    """
    fillet = part.shape_factory.add_new_edge_fillet_with_constant_radius(
        edge_ref, 1, radius
    )
    logger.debug(f"[MODELING] add_edge_fillet: r={radius}mm")
    return fillet


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
    from catia_copilot.catia.connection import wrap_product, get_catia_v5_application
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

    def __init__(self):
        self._steps: list[dict] = []  # 步骤记录列表
        self._part = None             # 最后一次操作的 Part，用于 features 快照

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _snapshot(self) -> list[str]:
        """安全地读取当前特征树快照，失败时返回空列表。"""
        if self._part is None:
            return []
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
        import traceback as _tb
        try:
            result = fn(*args, **kwargs)
            # 成功：记录步骤
            self._steps.append({
                "step":           func_label,
                "status":         "ok",
                "features_after": self._snapshot(),
            })
            return result
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

    def create_part(self, name: str = "Part"):
        """新建 CATPart，返回 Part 对象。"""
        result = self._run(f"create_part({name!r})", create_part, name)
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

    # ------------------------------------------------------------------
    # 草图
    # ------------------------------------------------------------------

    def add_sketch(self, part, plane="xy"):
        """在基准平面上新建草图。plane: 'xy' / 'yz' / 'zx'"""
        self._part = part
        return self._run(f"add_sketch(plane={plane!r})", add_sketch, part, plane)

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

    # ------------------------------------------------------------------
    # 特征：拉伸 / 挖槽 / 孔
    # ------------------------------------------------------------------

    def add_pad(self, part, sketch, depth: float):
        """拉伸草图，返回 Pad 对象。"""
        self._part = part
        return self._run(f"add_pad(depth={depth})", add_pad, part, sketch, depth)

    def add_pocket(self, part, sketch, depth: float):
        """挖槽，返回 Pocket 对象。"""
        self._part = part
        return self._run(f"add_pocket(depth={depth})", add_pocket, part, sketch, depth)

    def add_hole_from_sketch(self, part, sketch,
                             diameter: float, depth: float):
        """以草图定位打盲孔，返回 Hole 对象。"""
        self._part = part
        return self._run(
            f"add_hole_from_sketch(d={diameter}, depth={depth})",
            add_hole_from_sketch, part, sketch, diameter, depth,
        )

    # ------------------------------------------------------------------
    # 特征：修饰
    # ------------------------------------------------------------------

    def add_edge_fillet(self, part, edge_ref, radius: float):
        """对边倒圆角（需传入 edge_ref Reference）。"""
        self._part = part
        return self._run(f"add_edge_fillet(r={radius})",
                         add_edge_fillet, part, edge_ref, radius)

    def add_chamfer(self, part, edge_ref,
                    length: float, angle: float = 45.0):
        """对边倒角（需传入 edge_ref Reference）。"""
        self._part = part
        return self._run(f"add_chamfer(l={length}, angle={angle})",
                         add_chamfer, part, edge_ref, length, angle)

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
