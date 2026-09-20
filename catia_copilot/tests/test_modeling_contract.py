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
        self.assertTrue(prompt.startswith("**建模**"))
        self.assertIn("参数化", prompt)
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
        self.assertIn("不盲目重跑 create_part", prompt)
        self.assertIn("update_part", prompt)
        self.assertIn("mm", prompt)
        self.assertIn("度", prompt)

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
        self.assertIn("未指定材料", desc)
        self.assertIn("密度", desc)
        self.assertNotIn("保持测量", desc, "Analyze 实时测量不依赖保持测量")

    def test_mass_notes_field_specific(self):
        """回归：get_mass_props 应说明读取失败/质量无效返回 None，而非笼统的保持测量缺失。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "get_mass_props"}
        text = " ".join(notes)
        self.assertIn("读取失败或质量无效", text)
        self.assertIn("取决于密度", text)
        self.assertIn("未指定材料", text)
        self.assertIn("不代表真实材料", text)
        self.assertNotIn("测量缺失", text)
        # 两个消费端都应渲染该 notes 原文（单源）
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("读取失败或质量无效", out)

    def test_axis_mapping_matches_used_prompt(self):
        prompt = mc.build_modeling_prompt_section()
        for frag in ('axis="z"', 'plane="zx"', 'axis="y"', 'plane="xy"', 'axis="x"', "内半径"):
            self.assertIn(frag, prompt, frag)

    def test_axis_mapping_embedded_in_both_outputs(self):
        """回归：两个消费端都必须嵌入同源轴向映射表，不能只引用不呈现。"""
        table = mc._AXIS_MAPPING_TEXT
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("旋转体轴向映射", out)
            self.assertIn(table, out, "输出应整体嵌入 _AXIS_MAPPING_TEXT（同源不复制）")
            self.assertEqual(out.count("轴=V(Z)"), 1, "映射表不得重复渲染")

    def test_axis_mapping_no_stage_leak(self):
        """回归：轴向映射应以中性场景说明，不向 LLM 泄漏 S1/S2 阶段编号。"""
        self.assertNotIn("S1", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("S2", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("S2", mc.build_run_modeling_script_description())

    def test_axis_plane_phrase_accurate(self):
        """回归：旋转轮廓草图所在平面包含旋转轴，而非"画在法向平面上"。"""
        self.assertIn("旋转轮廓草图所在平面包含旋转轴", mc._AXIS_MAPPING_TEXT)
        self.assertNotIn("法向平面", mc._AXIS_MAPPING_TEXT)

    def test_draw_rect_axis_roles_clarified(self):
        """回归：draw_rect 旋转场景注明 x/width 半径向、y/height 轴向，避免内径半径混称。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "draw_rect"}
        text = " ".join(notes)
        self.assertIn("半径向起点", text)
        self.assertIn("壁厚", text)
        self.assertIn("轴向起点", text)
        self.assertIn("轴向长度", text)

    def test_pad_side_sketch_xy_context_restored(self):
        """回归：add_sketch_on_pad_side 应补回旧 H/V 说明并标注适用场景，不推广到任意旋转面。"""
        notes = {s.notes for s in mc.MODELING_API_CATALOG if s.name == "add_sketch_on_pad_side"}
        text = " ".join(notes)
        self.assertIn("H 沿面宽", text)
        self.assertIn("V=Z+", text)
        self.assertIn("XY矩形Pad侧面场景", text)
        self.assertIn("不能推广到任意旋转面", text)

    def test_description_trailing_no_extra_whitespace(self):
        """回归：description 末尾不凭空多空格（收尾缩进不能进入字符串）。"""
        desc = mc.build_run_modeling_script_description()
        self.assertTrue(desc.endswith("模型验证说成通过。"), "description 应以状态语义收尾")
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
# ---------------------------------------------------------------------------

def _module_value(tree: ast.Module, name: str) -> ast.AST | None:
    """取模块级赋值（Assign/AnnAssign）中指定目标名的值表达式。"""
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", "") == name for t in targets if isinstance(t, ast.Name)):
                return node.value
    return None


def _schema_description(tree: ast.Module) -> ast.AST:
    """在 tools_schema 中按 name 键的"值"定位条目，取 description 表达式结点。"""
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


class TestAssembledConsumerBehavior(unittest.TestCase):
    """AST 抽取真实 consumer 表达式，在仅注入 renderer 的受控环境求值，验证最终行为。"""

    def _eval_expr(self, name: str, inject: dict) -> str:
        tree = ast.parse(_TOOLS_SRC.read_text(encoding="utf-8"))
        expr = _module_value(tree, name)
        self.assertIsNotNone(expr)
        return eval(compile(ast.Expression(expr), "<tools.py>", "eval"), inject)

    def test_prompt_head_tail_preserved_and_renderer_embedded(self):
        prompt = self._eval_expr(
            "DEFAULT_SYSTEM_PROMPT",
            {"build_modeling_prompt_section": mc.build_modeling_prompt_section},
        )
        # defaultprompt 头尾原文保留
        self.assertTrue(prompt.startswith("你是 CATIA Copilot"))
        self.assertIn("传 summary_only=true 减少返回数据量。", prompt)
        self.assertIn("**文档属性**", prompt)
        self.assertTrue(
            prompt.endswith("遇到错误时，解释可能的原因并给出下一步建议。"),
            "defaultprompt 尾部原文应保留（不使用 strip 掩盖，确定收尾无多余空白）")
        # 渲染段与模板实际出现
        self.assertIn("**建模**", prompt)
        self.assertIn("def build(ctx):", prompt)
        self.assertIn("create_part", prompt)
        # 旧 ghost API 与错误 keyword 消失
        for g in ("add_edge_fillet", "add_chamfer", "add_rect_pattern", "add_circ_pattern",
                  "add_sketch_at_height(part, h, base", "add_hole_from_sketch(part, sk, d"):
            self.assertNotIn(g, prompt, g)

    def test_prompt_titles_at_line_start_no_leading_spaces(self):
        """回归：**建模**/**文档属性**/**回复风格** 标题必须顶格，前无 4 空格缩进污染；
        API 目录末行与 **文档属性** 之间须有空行。"""
        prompt = self._eval_expr(
            "DEFAULT_SYSTEM_PROMPT",
            {"build_modeling_prompt_section": mc.build_modeling_prompt_section},
        )
        for frag in ("**建模**", "**文档属性**", "## 回复风格"):
            idx = prompt.find(frag)
            self.assertGreaterEqual(idx, 0, f"缺 {frag}")
            self.assertEqual(prompt[idx - 1], "\n", f"{frag} 前应为换行而非空格")
        self.assertIn("\n\n**文档属性**", prompt, "建模段与文档属性段之间应相隔一空行")
        # 非建模旧头尾字符原样保留且无多余收尾空白
        self.assertTrue(prompt.startswith("你是 CATIA Copilot"))
        self.assertTrue(prompt.endswith("遇到错误时，解释可能的原因并给出下一步建议。"))

    def test_schema_description_is_the_contract(self):
        tree = ast.parse(_TOOLS_SRC.read_text(encoding="utf-8"))
        desc = eval(
            compile(ast.Expression(_schema_description(tree)), "<tools.py>", "eval"),
            {"build_run_modeling_script_description": mc.build_run_modeling_script_description},
        )
        self.assertIsInstance(desc, str)
        self.assertTrue(desc.startswith("这是建模脚本的完整契约"), "description 应以契约单源渲染")
        self.assertIn("def build(ctx):", desc)


class TestGeometryFaceSemanticsS2(unittest.TestCase):
    def test_face_query_notes_carry_geometry_semantics(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        for token in ("cylindrical", "unknown", "normal=None", "不再默认4边"):
            self.assertIn(token, notes["get_pad_faces"])

    def test_pocket_and_shaft_notes_synced(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("反向", notes["get_pocket_faces"])
        self.assertIn("normal=None", notes["get_shaft_faces"])
        self.assertIn("unknown", notes["get_shaft_faces"])

    def test_by_normal_validation_and_skip_none(self):
        notes = {s.name: s.notes for s in mc.MODELING_API_CATALOG}
        self.assertIn("planar", notes["get_pad_faces_by_normal"])
        self.assertIn("跳过", notes["get_pad_faces_by_normal"])

    def test_query_error_is_not_a_step_error(self):
        for out in (mc.build_modeling_prompt_section(), mc.build_run_modeling_script_description()):
            self.assertIn("不产生 failed_step", out)
            self.assertIn("不再默认4边", out)
            self.assertIn("feature_inference", out)

    def test_run_description_documents_scope_and_status_layers(self):
        desc = mc.build_run_modeling_script_description()
        self.assertIn("target_document_id", desc)
        for token in ("execution", "model_update", "verification"):
            self.assertIn(token, desc)
        self.assertIn("required_features", desc)
