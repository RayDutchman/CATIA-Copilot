# text-to-cad 项目参考与启发

> 来源项目：[earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad)
> 本文档记录对 CATIA-Copilot AI 建模部分的参考价值与具体借鉴方向。

---

## 背景

text-to-cad 是一个 AI Agent 驱动的 CAD 建模技能集，底层基于 build123d + OpenCASCADE，
支持自然语言生成 3D 零件并导出 STEP/STL/3MF 等格式。

与 CATIA-Copilot 不同，它面向的是"从零生成独立零件"场景，没有宿主 CAD 软件，
也没有特征树、BRep 引用等工程级约束。但其工具链设计和 Agent 协作模式对本项目有参考价值。

---

## 核心参考点

### 1. 生成 → 验证 → 修复的迭代闭环（最值得借鉴）

text-to-cad 设计了一条完整的验证链路：

```
scripts/step（执行生成）
  → scripts/inspect refs --facts --planes（几何验证：尺寸、法向量、位置）
    → scripts/snapshot（视觉确认：渲染截图）
      → 发现问题 → 修改脚本 → 重跑
```

**CATIA-Copilot 现状**：`tool_run_modeling_script` 返回"成功/失败 + 步骤列表"，
但没有几何验证环节。AI 建完零件后不知道尺寸是否符合预期，也无法自主发现错误。

**具体借鉴**：在 `tool_run_modeling_script` 执行完成后，自动触发一次几何验证：
调用已有的 `get_mass_props`、`get_pad_faces_by_normal` 等查询 API，
把结果（边界框、面法向量、特征尺寸）附在反馈 JSON 里，让 AI 对照用户意图自检，
而不是盲目告诉用户"已完成"。

对应 roadmap 中的**方向 C：AI 端到端建模验证**——不只是跑通链路，
而是让 AI 能自主验证结果是否符合预期，进而决定是否需要修复重试。

示例反馈结构（扩展后）：

```json
{
  "success": true,
  "part_name": "圆筒",
  "features": ["旋转体.1"],
  "steps": [...],
  "geometry_check": {
    "bounding_box": {"x": 100, "y": 100, "z": 80},
    "mass_kg": 0.247,
    "top_face_normal": [0, 0, 1],
    "top_face_z": 80.0
  }
}
```

---

### 2. 参数化脚本约束

text-to-cad 强制要求生成器脚本开头声明具名参数变量，几何调用里只能引用变量名，
不允许硬编码数字。背后逻辑是：**AI 修改参数比重写几何代码容易得多，也不易引入新错误。**

**CATIA-Copilot 现状**：AI 生成的脚本可能写出：

```python
ctx.draw_rect(sk, 0, 25, 80, 25)  # 数字直接硬编码
```

用户说"把高度改成 120"时，AI 需要重新理解整段脚本才能定位该改哪个数字。
随着脚本复杂度上升，这类修改极易出错。

**具体借鉴**：在 `DEFAULT_SYSTEM_PROMPT` 里明确要求：

```
脚本开头必须声明所有控制参数为具名变量：
    outer_r = 50.0   # 外径
    inner_r = 25.0   # 内径
    height  = 80.0   # 高度
几何调用里只能使用变量名，不得硬编码数字。
```

这让"局部修改"成为可能——用户说改某个尺寸，AI 只需定位并修改对应变量，
不需要重新生成整段脚本。

---

### 3. 视觉截图反馈

text-to-cad 的 `scripts/snapshot` 在生成 STEP 后自动渲染截图，
作为 AI 验证结果的视觉手段之一，也作为最终反馈展示给用户。

**CATIA-Copilot 的天然优势**：宿主就是 CATIA，视觉反馈天然存在，但目前 AI 看不到，
用户也没有建模完成后的即时视觉确认。

CATIA 提供截图 API（`viewer.CaptureToFile()`），技术成本不高。

**具体借鉴**：在 `tool_run_modeling_script` 成功后，可选地调用截图 API，
将截图路径返回给前端展示，或作为 AI 下一轮的视觉输入（如果模型支持图像输入）。
优先级低于几何验证，但用户体验提升明显。

---

### 4. 技能边界的明确声明

text-to-cad 的 SKILL.md 在描述能做什么之外，专门有一节"Do not use this skill when"，
明确列出不适用的场景（渲染概念图、CAM 刀路、FEA 仿真、BIM 建筑等）。

**CATIA-Copilot 现状**：System Prompt 是"能做什么的 API 清单"，
没有明确的"不能做什么"边界。AI 在遇到超出能力范围的请求时，
倾向于编造不存在的 `ctx.xxx()` 调用，而不是拒绝并解释原因。

**具体借鉴**：在 `DEFAULT_SYSTEM_PROMPT` 里加一节明确边界：

```
## 当前不支持的操作（不要尝试调用或编造）
- FEA / 强度分析（需要 CATIA Analysis 模块，未封装）
- 装配约束（Assembly Design，未封装）
- 工程图标注（Drafting，未封装）
- 曲面特征（Generative Shape Design，未封装）
- 阵列（add_rect_pattern / add_circ_pattern 方向参数有 bug，暂不可用）

遇到上述请求时，直接告知用户当前不支持，不要编造 API 调用。
```

---

## 不值得借鉴的部分

- **SKILL.md / Agent Skills 标准**：这是 text-to-cad 为 Codex/Claude Code 等外部 Agent 设计的接入协议，CATIA-Copilot 有自己的 `tools_schema` + `tools_map` 体系，两者不兼容也不必兼容。

- **build123d / cadpy 工具链**：底层 CAD 内核完全不同（OpenCASCADE vs CATIA V5 COM），无法复用任何代码。

- **CAD Viewer（WebGL 预览）**：CATIA 本身就是可视化环境，不需要独立的浏览器预览。

---

## 优先级排序

| 借鉴点 | 实现难度 | 对 AI 建模质量的提升 | 优先级 |
|---|---|---|---|
| 几何验证结果反馈给 AI | 低（API 已有） | 高 | ★★★ |
| System Prompt 参数化约束 | 极低（改文字） | 中高 | ★★★ |
| System Prompt 边界声明 | 极低（改文字） | 中（减少幻觉） | ★★ |
| 建模完成后截图 | 中 | 低（用户体验） | ★ |

---

*记录时间：2026-06-13*
