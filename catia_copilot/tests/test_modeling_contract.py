# -*- coding: utf-8 -*-
"""modeling_contract.py 契约回归测试（S1）：只读源码文本做 AST，绝不 import modeling/tools。"""
import ast
import copy
import inspect
import pathlib
import unittest

import catia_copilot.catia.modeling_contract as mc

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODELING_SRC = _ROOT / "catia_copilot" / "catia" / "modeling.py"
_TOOLS_SRC = _ROOT / "catia_copilot" / "catia" / "tool_schema.py"


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


def _make_func(node: ast.FunctionDef):
    """由真实 AST 参数生成纯 pass 函数，取其 inspect.Signature 供 bind 严格校验调用。"""
    ns: dict = {}
    exec(f"def _p({_sig_str(node)}):\n    pass", ns)   # noqa: S102 - 受控 AST 源码文本
    return inspect.signature(ns["_p"])


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
            return [self]  # 示例可能对返回值做 [0]（如面列表），返回可下标假值

        return _call


_DIM_RULES = {
    "add_pad": {"kw": ("depth",), "pos_from": 2},
    "add_pocket": {"kw": ("depth",), "pos_from": 2},
    "add_hole_from_sketch": {"kw": ("diameter", "depth"), "pos_from": 2},
    "draw_rect": {"kw": ("width", "height"), "pos_from": 3},  # x/y 为起点坐标可写字面量，width/height 才是尺寸
    "draw_circle": {"kw": ("radius",), "pos_from": 2},
    "add_fillet_edges": {"kw": ("radius",), "pos_from": 2},
    "add_auto_fillet": {"kw": ("radius", "inner_radius"), "pos_from": 1},
}


def _is_num_literal(node: ast.AST) -> bool:
    """数字字面量（含负数字面量如 -3.0，即 UnaryOp(USub, Constant)）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float)))


class TestRenderedSections(unittest.TestCase):
    def test_sections_render_all_notes_single_source(self):
        prompt = mc.build_modeling_prompt_section()
        desc = mc.build_run_modeling_script_description()
        self.assertTrue(prompt.startswith("**Modeling**"))
        self.assertIn("Parameterizing Dimensions", prompt)
        self.assertIn("def build(ctx):", desc)
        for spec in mc.MODELING_API_CATALOG:
            self.assertIn(spec.notes, prompt, f"prompt 缺 {spec.name}.notes")
            self.assertIn(spec.notes, desc, f"description 缺 {spec.name}.notes")

    def test_examples_embedded_and_catalog_single_source(self):
        """示例不孤立：prompt 与 description 都嵌入真实可执行模板；目录行同源于同一 helper。"""
        block0 = mc._example_blocks()[0]
        lines_cat = set(mc._render_catalog_lines().splitlines())
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn(block0, out, "两个消费端都要嵌入真实示例模板")
            for line in lines_cat:
                self.assertIn(line, out, f"输出的目录行与 _render_catalog_lines 不一致: {line}")

    def test_failure_guidance_and_units_rendered(self):
        prompt = mc.build_modeling_prompt_section()
        self.assertIn("failed_step", prompt)
        self.assertIn("do not blindly rerun create_part", prompt)
        self.assertIn("update_part", prompt)
        self.assertIn("mm", prompt)
        self.assertIn("degrees", prompt)

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
        self.assertIn("no material is assigned", desc)
        self.assertIn("density", desc)
        self.assertNotIn("keep measure", desc, "Analyze live measurements do not depend on kept measures")

    def test_mass_notes_field_specific(self):
        """回归：get_mass_props 应说明读取失败/质量无效返回 None，而非笼统的保持测量缺失。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "get_mass_props"}
        text = " ".join(notes)
        self.assertIn("unavailable or invalid", text)
        self.assertIn("depends on density", text)
        self.assertIn("no material is assigned", text)
        self.assertIn("does not represent the real material", text)
        self.assertNotIn("measurement missing", text)
        # 两个消费端都应渲染该 notes 原文（单源）
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("unavailable or invalid", out)

    def test_axis_mapping_matches_used_prompt(self):
        prompt = mc.build_modeling_prompt_section()
        for frag in ('axis="z"', 'plane="zx"', 'axis="y"', 'plane="xy"', 'axis="x"', "inner radius"):
            self.assertIn(frag, prompt, frag)

    def test_axis_mapping_embedded_in_both_outputs(self):
        """回归：两个消费端都必须嵌入同源轴向映射表，不能只引用不呈现。"""
        table = mc._AXIS_MAPPING_TEXT
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("Revolved Profile Axis Mapping", out)
            self.assertIn(table, out, "输出应整体嵌入 _AXIS_MAPPING_TEXT（同源不复制）")
            self.assertEqual(out.count("axis=V(Z)"), 1, "The mapping table must not be rendered more than once")

    def test_axis_mapping_no_stage_leak(self):
        """回归：轴向映射应以中性场景说明，不向 LLM 泄漏 S1/S2 阶段编号。"""
        self.assertNotIn("S1", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("S2", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("S2", mc.build_run_modeling_script_description())

    def test_axis_plane_phrase_accurate(self):
        """回归：旋转轮廓草图所在平面包含旋转轴，而非"画在法向平面上"。"""
        self.assertIn("The sketch plane contains the revolution axis", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("normal plane", mc._AXIS_MAPPING_TEXT)

    def test_draw_rect_axis_roles_clarified(self):
        """回归：draw_rect 旋转场景注明 x/width 半径向、y/height 轴向，避免内径半径混称。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "draw_rect"}
        text = " ".join(notes)
        self.assertIn("inner radius", text)
        self.assertIn("wall thickness", text)
        self.assertIn("axial start", text)
        self.assertIn("axial length", text)

    def test_pad_side_sketch_xy_context_restored(self):
        """回归：add_sketch_on_pad_side 应补回旧 H/V 说明并标注适用场景，不推广到任意旋转面。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "add_sketch_on_pad_side"}
        text = " ".join(notes)
        self.assertIn("H follows face width", text)
        self.assertIn("V=Z+", text)
        self.assertIn("XY rectangular Pads", text)
        self.assertIn("do not generalize to rotated faces", text)

    def test_description_trailing_no_extra_whitespace(self):
        """回归：description 末尾不凭空多空格（收尾缩进不能进入字符串）。"""
        desc = mc.build_run_modeling_script_description()
        self.assertTrue(desc.endswith("A failed state query must not be reported as successful model verification."),
                        "description must end with the status semantics")
        self.assertNotIn(" \n", desc.rstrip() + "\n")
        for line in desc.splitlines():
            self.assertEqual(line, line.rstrip(), f"行尾不得有多余空格: {line!r}")


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

    def test_negative_literal_is_still_a_literal(self):
        """负数 UnaryOp 也判为字面量（M3）：depth=-20.0 这类负尺寸必须能被 AST 检查拦截。"""
        neg = ast.parse("x = -3.0").body[0].value
        self.assertTrue(_is_num_literal(neg))
        self.assertFalse(_is_num_literal(ast.parse("x = -d").body[0].value))

    def test_example0_rect_maps_length_width_to_xy(self):
        """回归：示例0 的 draw_rect 必须以 (length, width) 对应 (width=X向, height=Y向)，
        不靠文本片段而是核对 FakeCtx 真实入参。"""
        fake = self._run(mc._example_blocks()[0])
        rect_calls = [c for c in fake.calls if c[0] == "draw_rect"]
        self.assertEqual(len(rect_calls), 1)
        _, args, _ = rect_calls[0]
        self.assertEqual(args[3], 100.0, "draw_rect width 应为 length=100.0（X 向）")
        self.assertEqual(args[4], 60.0, "draw_rect height 应为 width=60.0（Y 向）")


# ---------------------------------------------------------------------------
# T3: tools.py 消费端接线验证（AST 抽取，绝不 import tools/modeling）
class TestGeometryFaceSemanticsS2(unittest.TestCase):
    def test_face_query_notes_carry_geometry_semantics(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        for token in ("cylindrical", "unknown", "normal is available only", "rather than assuming four edges"):
            self.assertIn(token, notes["get_pad_faces"])

    def test_pocket_and_shaft_notes_synced(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("underlying Pad", notes["get_pocket_faces"])
        self.assertIn("opposite the corresponding Pad outward normals", mc._FACE_SEMANTICS_TEXT)
        self.assertIn("null normal/origin", notes["get_shaft_faces"])
        self.assertIn("unknown", notes["get_shaft_faces"])

    def test_by_normal_validation_and_skip_none(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("planar", notes["get_pad_faces_by_normal"])
        self.assertIn("only known planar normals match", notes["get_pad_faces_by_normal"])

    def test_query_error_is_not_a_step_error(self):
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("do not create failed_step records", out)
            self.assertIn("instead of assuming four edges", out)
            self.assertIn("feature_inference", out)

    def test_run_description_documents_scope_and_status_layers(self):
        desc = mc.build_run_modeling_script_description()
        self.assertIn("target_document_id", desc)
        for token in ("execution", "model_update", "verification"):
            self.assertIn(token, desc)
        self.assertIn("required_features", desc)
