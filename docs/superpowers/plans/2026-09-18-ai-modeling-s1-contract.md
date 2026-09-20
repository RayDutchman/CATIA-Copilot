# AI 建模提示词契约化（S1：建模契约注册表）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. 计划是工程指导而非已执行代码：任何步骤均未完成，严格按 TDD 红→绿推进。

**Goal:** 建立单一建模 API 契约注册表（AST 校验），令 `DEFAULT_SYSTEM_PROMPT` 建模段与 `run_modeling_script` 的 schema description 从同一注册表渲染，消除幻象 API（`add_edge_fillet`/`add_chamfer`）与错误参数名（`h`/`base`/`w`/`t`/`d`/`tol`）。

**基线：** 全量 `python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -v` = 249 tests / 0 fail / 48.127s；完成后应为 249 + 15 新增 = 264 tests / 0 fail。

**Architecture:** 新增纯标准库模块 `catia_copilot/ai/modeling_contract.py`（frozen dataclass + `MODELING_API_CATALOG`(37) + `EXCLUDED_MODELING_APIS`(仅 2 故障阵列) + `_example_blocks()` + 两个渲染函数）。`tools.py` 顶层 import，按最小 patch 边界（保留头/尾字符串字面量、普通拼接注入）替换两处消费端。不改 COM、不改底层接口。测试只读 `modeling.py`/`tools.py` 源码文本做 AST 断言，绝不 import 这两模块。

**S2 注记（不实施）：** 轴向映射沿用 defaultprompt（z→zx、y→xy、x→xy），与 `modeling.py:add_shaft` docstring(Y→YZ) 冲突列为 S2 验证项；`add_rect_pattern`/`add_circ_pattern` 修复与 `add_sketch_on_pad_top` 推广到其他特征亦为后续阶段。

## Global Constraints

- 不改 `part_templates`/`docs/AI_MODELING_PLAN_AND_ROADMAP.md`/COM 实现；不执行任何 CATIA 写入（烟测留 S4）；低层接口原样保留、不新增 ctx 方法。
- 不暴露幻象 API 与故障阵列为可用 API；渲染结果不含冗长"暂不可用"列表。
- 示例尺寸参数引用 build 开头具名变量；固定坐标/边索引可用字面量并注释。尺寸语义一律"内半径"，禁"内径(半径)"混称。单位 mm/度。
- 渲染必须覆盖：轴线先于草图；`update_part` 在末尾与修饰后；基准面/偏移/B-Rep 区别；`get_mass_props` 可 None 且质量依赖密度；失败精准修正而非盲目重跑 `create_part`。
- **任何步骤不自动 `git add`/`git commit`**：用 `git diff`/`--cached` 展示，用户明确授权后才提交（Conventional Commits）。

---
## Task 1: 建模契约注册表（ModelingApiSpec + 37 目录 + 排除表）与 AST 全等契约测试

**Files/Interfaces:** Create `catia_copilot/ai/modeling_contract.py`、`catia_copilot/tests/test_modeling_contract.py`。`ModelingApiSpec`(@dataclass frozen: `name/category/signature/notes`)；`MODELING_API_CATALOG: tuple[ModelingApiSpec, ...]`(37 条，signature 为不含方法名/self 的形参串，与 `ast.unparse` 输出逐字符一致，能表达 `/`/`*`)；`EXCLUDED_MODELING_APIS: dict[str, str]`(仅 `add_rect_pattern`/`add_circ_pattern`)。

- [ ] **Step 1: 编写 T1 失败测试**（写入测试文件；T2/T3 追加类）

```python
# -*- coding: utf-8 -*-
"""modeling_contract.py 契约回归测试（S1）：只读源码文本做 AST，绝不 import modeling/tools。"""
import ast
import copy
import inspect
import pathlib
import unittest

import catia_copilot.ai.modeling_contract as mc

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODELING_SRC = _ROOT / "catia_copilot" / "catia" / "modeling.py"
_TOOLS_SRC = _ROOT / "catia_copilot" / "ai" / "tools.py"


def _ctx_public_methods() -> dict[str, ast.FunctionDef]:
    """ModelingContext 公开可调用方法（排除私有与 @property，如 steps）。"""
    tree = ast.parse(_MODELING_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ModelingContext":
            return {n.name: n for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not n.name.startswith("_")
                    and not any(isinstance(d, ast.Name) and d.id == "property"
                                for d in n.decorator_list)}
    raise AssertionError("未找到 ModelingContext 类")


def _sig_str(node: ast.FunctionDef) -> str:
    """deepcopy 参数 AST、删 self 与注解后 ast.unparse，自动保留 / 与 * 标记。"""
    a = copy.deepcopy(node.args)
    if a.posonlyargs and a.posonlyargs[0].arg == "self":
        a.posonlyargs.pop(0)
    elif a.args and a.args[0].arg == "self":
        a.args.pop(0)
    for n in a.posonlyargs + a.args + a.kwonlyargs:
        n.annotation = None
    if a.vararg:
        a.vararg.annotation = None
    if a.kwarg:
        a.kwarg.annotation = None
    return ast.unparse(a)


class TestCatalogContract(unittest.TestCase):
    def test_catalog_equals_public_methods_minus_broken_arrays(self):
        cat = {s.name for s in mc.MODELING_API_CATALOG}
        self.assertEqual(cat, set(_ctx_public_methods()) - {"add_rect_pattern", "add_circ_pattern"})

    def test_every_signature_matches_ast_exactly(self):
        methods = _ctx_public_methods()
        for spec in mc.MODELING_API_CATALOG:
            self.assertEqual(spec.signature, _sig_str(methods[spec.name]), spec.name)

    def test_legacy_param_names_rejected(self):
        legacy = {"h", "base", "w", "t", "d", "tol"}  # 旧提示词中出现的错误参数名
        for spec in mc.MODELING_API_CATALOG:
            params = {p.split("=")[0] for p in spec.signature.split(", ") if p}
            self.assertFalse(params & legacy, f"{spec.name} 含旧参数名: {params & legacy}")

    def test_excluded_dict_is_auditable(self):
        self.assertEqual(set(mc.EXCLUDED_MODELING_APIS), {"add_rect_pattern", "add_circ_pattern"})
        self.assertNotIn("steps", {s.name for s in mc.MODELING_API_CATALOG})  # @property，过滤非排除
```

Expected RED：`ModuleNotFoundError: No module named 'catia_copilot.ai.modeling_contract'`（模块不存在，helper 未坏）。

- [ ] **Step 2: 创建模块并写入目录**

```python
# -*- coding: utf-8 -*-
"""建模 API 单一契约注册表与两个消费端渲染数据（S1）。signature 与 modeling.py 形参逐字符一致。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelingApiSpec:
    name: str
    category: str
    signature: str  # 形参串（不含 self/方法名），与 ast.unparse(参数AST) 逐字符一致
    notes: str      # 中文、单行，带参数名与具体说明


MODELING_API_CATALOG: tuple[ModelingApiSpec, ...] = (
    # 文档与零件
    ModelingApiSpec("create_part", "文档与零件", "name='Part', nomenclature=''", "新建 CATPart；name=零件号(PartNumber)、nomenclature=命名(如'底座')"),
    ModelingApiSpec("get_active_part", "文档与零件", "", "获取活动文档的 Part"),
    ModelingApiSpec("update_part", "文档与零件", "part", "刷新模型（build 末尾与每次修改后必须调用）"),
    ModelingApiSpec("save_part", "文档与零件", "part, path", "另存为"),
    # 草图
    ModelingApiSpec("add_sketch", "草图", "part, plane='xy'", "在基准平面('xy'/'yz'/'zx')建草图"),
    ModelingApiSpec("add_sketch_at_height", "草图", "part, height, base_plane='xy'", "在 base_plane 上方 height mm 处建偏移草图（已知高度时用）"),
    ModelingApiSpec("add_sketch_on_pad_top", "草图", "part, pad", "Pad 顶面 B-Rep 支撑草图（随 Pad 深度变化，叠加建模推荐）"),
    ModelingApiSpec("add_sketch_on_pad_side", "草图", "part, pad, edge_index", "Pad 侧面 B-Rep 支撑草图；edge_index 从 1 起：draw_rect(x,y,w,h) 的 1=Y=y面,2=X=x+w面,3=Y=y+h面,4=X=x面"),
    ModelingApiSpec("add_sketch_on_pad_bottom", "草图", "part, pad", "Pad 底面 B-Rep 支撑草图"),
    # 图元
    ModelingApiSpec("draw_rect", "图元", "sketch, x, y, width, height", "画矩形（左下角 x,y + 宽 width/高 height，mm）；x=H起点、y=V起点"),
    ModelingApiSpec("draw_circle", "图元", "sketch, cx, cy, radius", "画圆（圆心 cx,cy + 半径 radius，mm）"),
    ModelingApiSpec("draw_arc", "图元", "sketch, cx, cy, radius, start_angle, end_angle", "画圆弧（半径 + 起止角，度，逆时针，0°=水平右）"),
    ModelingApiSpec("draw_line", "图元", "sketch, x1, y1, x2, y2", "画直线段，mm"),
    ModelingApiSpec("draw_slot", "图元", "sketch, x1, y1, x2, y2, radius", "画腰形槽（中轴两端点 + 半圆半径，mm）"),
    ModelingApiSpec("draw_point", "图元", "sketch, x, y", "画定位点（用于孔定位）"),
    # 特征
    ModelingApiSpec("add_pad", "特征", "part, sketch, depth, symmetric=False, second_depth=None", "拉伸；symmetric=True 对称(总厚2×depth)、second_depth=N 双向非对称"),
    ModelingApiSpec("add_pocket", "特征", "part, sketch, depth", "挖槽，mm（仅基准面草图）"),
    ModelingApiSpec("add_shaft", "特征", "part, sketch, axis='z'", "旋转体（360°）；先 prepare_revolute_axis 再 add_sketch"),
    ModelingApiSpec("add_groove", "特征", "part, sketch, axis='z'", "环形槽（旋转切除，需已有实体且 update_part）"),
    ModelingApiSpec("add_hole_from_sketch", "特征", "part, sketch, diameter, depth", "对草图打孔（直径 diameter + 深度 depth，mm）"),
    ModelingApiSpec("prepare_revolute_axis", "特征", "part, axis='z'", "预建旋转轴线，必须先于 add_sketch 调用"),
    # 修饰（倒圆角）
    ModelingApiSpec("add_auto_fillet", "修饰（倒圆角）", "part, radius, inner_radius=None", "自动圆角所有适合边；inner_radius 缺省=radius；需 update_part"),
    ModelingApiSpec("add_fillet_edges", "修饰（倒圆角）", "part, edge_refs, radius", "对指定边引用列表圆角；需 update_part"),
    ModelingApiSpec("make_pad_edge_ref", "修饰（倒圆角）", "part, pad, face_a_brep, face_b_brep", "低层：由两面 BRep 构造 Pad 边引用"),
    ModelingApiSpec("get_pad_face_brep", "修饰（倒圆角）", "pad, face, edge_index=1", "低层：获取 Pad 面 BRep 字符串"),
    # 面/边查询
    ModelingApiSpec("get_pad_faces", "面/边查询", "part, pad", "Pad 全部面（type=top/bottom/side）"),
    ModelingApiSpec("get_pad_faces_by_normal", "面/边查询", "part, pad, normal, tolerance_deg=5.0", "按法向筛选面；normal=(nx,ny,nz) 如(0,0,1)=朝上"),
    ModelingApiSpec("get_pad_face_edges", "面/边查询", "part, pad, face_info", "Pad 某面的所有边引用列表"),
    ModelingApiSpec("get_pocket_faces", "面/边查询", "part, pocket", "Pocket 自身面（type=bottom/side；开口面属下层 Pad）"),
    ModelingApiSpec("get_pocket_face_edges", "面/边查询", "part, pocket, face_info", "Pocket 某面的所有边引用列表"),
    ModelingApiSpec("get_pocket_opening_edges", "面/边查询", "part, pocket, pad", "Pocket 开口楞（=Pad 顶面×Pocket 侧面）"),
    ModelingApiSpec("get_shaft_faces", "面/边查询", "part, shaft", "Shaft 面列表（type=surface、edge_index=草图边索引）"),
    ModelingApiSpec("get_shaft_face_edges", "面/边查询", "part, shaft, face_info", "Shaft 面与相邻面的交线边引用列表"),
    # 查询与步骤记录
    ModelingApiSpec("list_features", "查询与步骤记录", "part", "查询特征列表"),
    ModelingApiSpec("list_sketches", "查询与步骤记录", "part", "查询草图列表"),
    ModelingApiSpec("get_mass_props", "查询与步骤记录", "part", "查询质量特性；可能返回 None（未赋材料/保持测量缺失），质量依赖用户材料密度，不代表真实材料"),
    # 里程碑
    ModelingApiSpec("step", "里程碑", "name, feature=None", "打里程碑标记，不执行 CATIA 操作；feature 可选"),
)


# 故障阵列：源码存在但方向参数有 bug，不入目录、不渲染为可用 API；steps 为 @property 由测试过滤。
EXCLUDED_MODELING_APIS: dict[str, str] = {
    "add_rect_pattern": "方向参数有 bug，暂不推荐在使用",
    "add_circ_pattern": "方向参数有 bug，暂不推荐在使用",
}
```

- [ ] **Step 3: 运行 T1 全绿**

```powershell
python -m unittest catia_copilot.tests.test_modeling_contract -v   # Expected: TestCatalogContract 4 项 OK，discover ≥ 253
```

---
## Task 2: 渲染函数（单源）+ 示例脚本（fake-ctx 可执行）+ 渲染测试

**Interfaces（T2 追加到 `modeling_contract.py` 末尾）：** `_example_blocks() -> tuple[str, ...]`、`build_modeling_prompt_section() -> str`、`build_run_modeling_script_description() -> str`。行为：清单按目录分组渲染 `ctx.<name>(<signature>)  <notes>`；尺寸参数化（具名变量）；轴向映射沿用 defaultprompt（z→zx 轴=V(Z) 半径向=H(-X) H>0；y→xy 轴=V(Y) 半径向=H(X) H>0；x→xy 轴=H(X) 半径向=V(Y) V>0），`draw_rect` x=H起点|y=V起点、旋转体用"内半径"；轴线先于草图、`update_part` 末尾与修饰后、三类草图区别、`get_mass_props` 可 None/依赖密度、失败精准修正；description 以 `## 关键约束` 收束。

- [ ] **Step 1: 追加 T2 测试**（辅助追加在 `_sig_str` 后，测试类追加在 `TestCatalogContract` 后）

```python
def _make_func(node: ast.FunctionDef):
    """由真实 AST 参数生成纯 pass 函数，供 inspect.Signature 严格校验调用。"""
    ns: dict = {}
    exec(f"def _p({_sig_str(node)}):\n    pass", ns)   # noqa: S102 - 受控 AST 源码文本
    return ns["_p"]


class FakeCtx:
    """每个 ctx 调用都经 inspect.Signature.bind 校验：缺必填/多余位置/重复绑定直接抛错。"""

    def __init__(self):
        self.sigs = {n: _make_func(m) for n, m in _ctx_public_methods().items()}
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        if name not in self.sigs:
            raise AttributeError(name)

        def _call(*args, **kwargs):
            self.sigs[name].bind(*args, **kwargs)  # 非法调用 → TypeError → 测试失败
            self.calls.append((name, args, kwargs))

        return _call


_DIM_RULES = {
    "add_pad": {"kw": ("depth",), "pos_from": 2},
    "add_pocket": {"kw": ("depth",), "pos_from": 2},
    "add_hole_from_sketch": {"kw": ("diameter", "depth"), "pos_from": 2},
    "draw_rect": {"kw": ("width", "height"), "pos_from": 2},
    "draw_circle": {"kw": ("radius",), "pos_from": 2},
    "add_fillet_edges": {"kw": ("radius",), "pos_from": 2},
    "add_auto_fillet": {"kw": ("radius", "inner_radius"), "pos_from": 1},
}


def _is_num_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


class TestRenderedSections(unittest.TestCase):
    def test_sections_render_all_notes_single_source(self):
        prompt = mc.build_modeling_prompt_section()
        desc = mc.build_run_modeling_script_description()
        self.assertTrue(prompt.startswith("**建模**"))
        self.assertIn("参数化", prompt)
        self.assertIn("def build(ctx):", desc)
        for spec in mc.MODELING_API_CATALOG:
            self.assertIn(spec.notes, prompt, f"prompt 缺 {spec.name}.notes")
            self.assertIn(spec.notes, desc, f"description 缺 {spec.name}.notes")

    def test_no_ghost_or_broken_apis_in_outputs(self):
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            for g in ("add_edge_fillet", "add_chamfer", "add_rect_pattern", "add_circ_pattern"):
                self.assertNotIn(g, out, f"输出不应含 {g}")

    def test_real_keywords_rendered_exactly(self):
        desc = mc.build_run_modeling_script_description()
        for frag in ("add_sketch_at_height(part, height, base_plane=",
                     "get_pad_faces_by_normal(part, pad, normal, tolerance_deg=",
                     "add_hole_from_sketch(part, sketch, diameter, depth)",
                     "draw_rect(sketch, x, y, width, height)"):
            self.assertIn(frag, desc, frag)

    def test_mass_none_and_density_caveat_renderable(self):
        desc = mc.build_run_modeling_script_description()
        self.assertIn("None", desc)
        self.assertIn("密度", desc)

    def test_axis_mapping_matches_used_prompt(self):
        prompt = mc.build_modeling_prompt_section()
        for frag in ('axis="z"', 'plane="zx"', 'axis="y"', 'plane="xy"', 'axis="x"', "内半径"):
            self.assertIn(frag, prompt, frag)


class TestExamplesExecutable(unittest.TestCase):
    def _run(self, block: str) -> FakeCtx:
        fake = FakeCtx()
        ns: dict = {}
        exec(compile(block, "<example>", "exec"), ns)
        ns["build"](fake)
        return fake

    def test_all_blocks_execute_and_end_with_update_part(self):
        blocks = mc._example_blocks()
        self.assertGreaterEqual(len(blocks), 4)
        for block in blocks:
            self.assertEqual(self._run(block).calls[-1][0], "update_part", "build 末尾必须 update_part")

    def test_revolute_axis_created_before_sketch(self):
        for block in mc._example_blocks():
            names = [c[0] for c in self._run(block).calls]
            if not {"add_shaft", "add_groove"} & set(names):
                continue
            self.assertLess(names.index("prepare_revolute_axis"), names.index("add_sketch"),
                            "必须先建轴线再建草图")

    def test_dimension_params_use_variables_not_literals(self):
        """尺寸形参须引用变量而非数字字面量（运行时不可分，故静态检查源码 AST）。"""
        for block in mc._example_blocks():
            for node in ast.walk(ast.parse(block)):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                rule = _DIM_RULES.get(node.func.attr)
                if rule is None:
                    continue
                for i in range(rule["pos_from"], len(node.args)):
                    self.assertFalse(_is_num_literal(node.args[i]),
                                     f"{node.func.attr} 第 {i} 个尺寸形参不得直接传数字字面量")
                for kw in node.keywords:
                    if kw.arg in rule["kw"]:
                        self.assertFalse(_is_num_literal(kw.value),
                                         f"{node.func.attr} 的参数 {kw.arg} 不得直接传数字字面量")
```

- [ ] **Step 2: 实现渲染与示例**

在 `modeling_contract.py` 末尾实现三个接口（按上面行为），`_example_blocks()` 返回下面 4 个脚本；**不预写/覆盖其他生产代码**。体例：尺寸用具名变量、固定坐标/边索引写字面量并注释。

```python
def build(ctx):
    length = 100.0   # 长度（X 向）
    width  = 60.0    # 宽度（Y 向）
    height = 30.0    # 高度（Z 向）
    part = ctx.create_part(name="底座")
    sk   = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk, 0, 0, width, length)
    ctx.add_pad(part, sk, depth=height)
    ctx.step("主体完成")
    ctx.update_part(part)
```

```python
def build(ctx):
    inner_radius = 25.0   # 内半径（H 起点）
    wall         = 25.0   # 壁厚（H 方向）
    height       = 80.0   # 轴向长度（V 方向）
    axis         = "y"
    part = ctx.create_part(name="圆筒")
    ctx.prepare_revolute_axis(part, axis)   # 必须先于 add_sketch
    sk   = ctx.add_sketch(part, "xy")       # axis="y" → plane="xy"
    ctx.draw_rect(sk, inner_radius, 0, wall, height)
    ctx.add_shaft(part, sk, axis=axis)
    ctx.update_part(part)
```

```python
def build(ctx):
    base_w = 100.0   # 底层宽度
    base_l = 60.0    # 底层长度
    h1     = 30.0    # 第一层高度
    h2     = 15.0    # 第二层高度
    r      = 20.0    # 圆柱半径
    part = ctx.create_part(name="叠加件")
    sk1  = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk1, 0, 0, base_w, base_l)
    pad1 = ctx.add_pad(part, sk1, depth=h1)
    ctx.update_part(part)
    sk2  = ctx.add_sketch_on_pad_top(part, pad1)   # B-Rep 面支撑，随 Pad 深度变化
    ctx.draw_circle(sk2, base_w / 2, base_l / 2, r)  # 圆心=固定坐标（形心），允许表达式
    ctx.add_pad(part, sk2, depth=h2)
    ctx.update_part(part)
```

```python
def build(ctx):
    w     = 50.0    # 长度
    l     = 40.0    # 宽度
    depth = 20.0    # 拉伸深度
    r     = 3.0     # 圆角半径
    part = ctx.create_part(name="圆角件")
    sk   = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sk, 0, 0, w, l)
    pad  = ctx.add_pad(part, sk, depth=depth)
    ctx.update_part(part)
    top   = ctx.get_pad_faces_by_normal(part, pad, normal=(0, 0, 1), tolerance_deg=5.0)[0]
    edges = ctx.get_pad_face_edges(part, pad, top)
    ctx.add_fillet_edges(part, edges, radius=r)
    ctx.update_part(part)
```

- [ ] **Step 3: 运行 T2 全绿**

```powershell
python -m unittest catia_copilot.tests.test_modeling_contract -v   # Expected: TestRenderedSections(5) + TestExamplesExecutable(3) 全 OK
```

---
## Task 3: tools.py 消费端接线（最小 patch，保留头/尾字符串）+ 集成测试 + 全量回归

**Files:** Modify `catia_copilot/ai/tools.py`（仅 3 处小改，不整文件覆盖、不动未修改行）。

- [ ] **Step 1: 追加 T3 集成测试**

注：两个消费端是两个模块级赋值——`DEFAULT_SYSTEM_PROMPT = """\...` 是 `Assign`，`tools_schema: list[...] = [` 是 `AnnAssign`；`run_modeling_script` 是条目内 `name` 键的**值**（按 key/value 映射读 values，不是读 keys）。

```python
def _module_value(tree: ast.Module, name: str) -> ast.AST | None:
    """取模块级赋值（Assign/AnnAssign）中指定目标名的值表达式。"""
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", "") == name for t in targets if isinstance(t, ast.Name)):
                return node.value
    return None


def _schema_description(tree: ast.Module) -> ast.AST:
    """在 tools_schema 中按 name 键的“值”定位条目，取 description 表达式结点。"""
    schema = _module_value(tree, "tools_schema")
    assert schema is not None, "未找到 tools_schema"
    for elt in schema.elts:
        if not isinstance(elt, ast.Dict):
            continue
        kv: dict[object, ast.AST] = {}
        for k, v in zip(elt.keys, elt.values):
            if isinstance(k, ast.Constant):
                kv[k.value] = v
        fn = kv.get("function")
        if not isinstance(fn, ast.Dict):
            continue
        fkv: dict[object, ast.AST] = {}
        for k, v in zip(fn.keys, fn.values):
            if isinstance(k, ast.Constant):
                fkv[k.value] = v
        name = fkv.get("name")
        if isinstance(name, ast.Constant) and name.value == "run_modeling_script":
            return fkv["description"]
    raise AssertionError("tools_schema 中未找到 run_modeling_script 条目")


class TestToolsConsumersWired(unittest.TestCase):
    def test_prompt_is_assembled_via_generator(self):
        tree = ast.parse(_TOOLS_SRC.read_text(encoding="utf-8"))
        value = _module_value(tree, "DEFAULT_SYSTEM_PROMPT")
        self.assertIsInstance(value, ast.BinOp,
                              "DEFAULT_SYSTEM_PROMPT 应为 头字面量+生成器+尾字面量 拼接")
        self.assertTrue(
            any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "build_modeling_prompt_section"
                for n in ast.walk(value)),
            "建模段未接入 build_modeling_prompt_section")

    def test_description_is_generator_call(self):
        desc = _schema_description(ast.parse(_TOOLS_SRC.read_text(encoding="utf-8")))
        self.assertIsInstance(desc, ast.Call, "run_modeling_script 的 description 应为生成器调用")
        self.assertEqual(getattr(desc.func, "id", ""), "build_run_modeling_script_description")

    def test_tools_source_has_no_ghost_apis(self):
        src = _TOOLS_SRC.read_text(encoding="utf-8")
        for g in ("add_edge_fillet", "add_chamfer", "add_rect_pattern", "add_circ_pattern"):
            self.assertNotIn(g, src)
```

- [ ] **Step 2: 运行确认 RED 原因正确（先于实现）**

```powershell
python -m unittest catia_copilot.tests.test_modeling_contract -v   # Expected RED（仅因未接线）：prompt 现为 Constant 非 BinOp、desc 现为 Constant 非 Call、tools.py 仍含幻象字面量；helpers 须保持 T1/T2 时全绿
```

- [ ] **Step 3: 接线修改（3 处小改，基于当前行号）**

3a. `DEFAULT_SYSTEM_PROMPT`（第 67-221 行）改三段普通拼接，保留原头/尾字符串内容与行序，仅 3 处 Edit：
1) 第 67 行 `DEFAULT_SYSTEM_PROMPT = """\` → `DEFAULT_SYSTEM_PROMPT = (\n    """\`；
2) 第 97-190 行（**建模** 段整体）替换为 `    """` / `    + build_modeling_prompt_section()` / `    + """\` 三行；
3) 第 221 行收尾 `"""` → `    """`，其后再加一行 `)`。

完成形态：`DEFAULT_SYSTEM_PROMPT = ( HEAD + build_modeling_prompt_section() + TAIL )`，其中 HEAD=原 68-96 行、TAIL=原 192-221 行（含 `## 回复风格`）。

3b. 顶层 import（第 24 行后插入）：

```python
from catia_copilot.ai.modeling_contract import (
    build_modeling_prompt_section,
    build_run_modeling_script_description,
)
```

3c. `run_modeling_script` 条目的 `description`（第 2037-2149 行，整个 `"description": ( … )` 拼接块）替换为 `"description": build_run_modeling_script_description(),` 一行；仅动该条目，`"name"`/`"parameters"` 键与相邻条目原样不动，不重写文件。

- [ ] **Step 4: 全绿**

```powershell
python -m unittest catia_copilot.tests.test_modeling_contract -v
$env:QT_QPA_PLATFORM='offscreen'; python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -v   # Expected: 契约15项+264全绿；计数不符则以实际为准，不得回归
```

- [ ] **Step 5: diff 卫生（不 stage）**

```powershell
git diff --check -- catia_copilot/ai/tools.py docs/AI_MODELING_PLAN_AND_ROADMAP.md   # Expected: 无 whitespace errors
git diff --stat
```
并单独查新增文件尾随空白（避免 `part_templates/Part_Template.md` 既有尾随空格误判）：

```powershell
& "C:\Users\Chen Weibo\AppData\Local\Programs\Python\Python313\python.exe" -c "
import pathlib
for p in [r'catia_copilot/ai/modeling_contract.py', r'catia_copilot/tests/test_modeling_contract.py']:
    bad = [n for n, ln in enumerate(pathlib.Path(p).read_text(encoding='utf-8').splitlines(), 1) if ln != ln.rstrip()]
    print(p, 'trailing-whitespace lines:', bad)
"
```

- [ ] **Step 6: 提交（用户授权门槛，执行 agent 不得自动执行）**

```powershell
git add catia_copilot/ai/modeling_contract.py catia_copilot/tests/test_modeling_contract.py catia_copilot/ai/tools.py
git diff --cached --stat
```

停在此处，向用户展示 `git diff --cached`；获得明确授权后执行 `git commit -m "fix(ai): 建模提示词与 run_modeling_script schema 改用契约注册表单源渲染"`。

---
## Self-Review 记录（精简）
- 覆盖：注册表+排除表（T1）；渲染+4 示例+严格 sig-bind 执行（T2）；两消费端最小 patch 接线+集成+全量回归（T3）。边界：未改 COM/part_templates/roadmap；`EXCLUDED` 仅 2 故障阵列，`steps` 由 @property 过滤；幻象 API 由 AST 缺席+双端负向断言拦截；S2 项仅注记。
- 预演依据（本机 Python 3.13 实跑）：`ast.unparse(arguments)` 保留 `/`/`*`；`inspect.Signature.bind` 可捕获缺必填/多余关键字/重复绑定；`tools_schema` 为 AnnAssign、`run_modeling_script` 为 name 键的值、description 现为 `ast.Constant` ⇒ T3 RED 仅源于未接线。
- 不得标记任何步骤"已完成"；任何 `git add`/`git commit` 均须用户授权。