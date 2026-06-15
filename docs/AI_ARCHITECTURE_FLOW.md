# CATIA Copilot 建模请求全链路说明

> 以"建一个外径 100mm、内径 50mm 的圆筒，用旋转特征建"为例。

---

## 总览

```
用户输入
  → LLM（理解意图 + 生成脚本）
    → tool_run_modeling_script（沙箱执行）
      → ModelingContext（步骤追踪）
        → modeling.py（CATIA 封装层）
          → pycatia / win32com（COM 通信）
            → CATIA V5（实际建模）
              → 结构化结果逐层返回
```

---

## 第 1 步：用户输入

用户在聊天界面输入：

```
建一个外径 100mm、内径 50mm 的圆筒，用旋转特征建
```

这条消息被发送给 LLM，同时携带 **System Prompt**。

---

## 第 2 步：System Prompt 告诉 AI 能做什么

System Prompt（`catia_copilot/ai/tools.py: DEFAULT_SYSTEM_PROMPT`）包含：

```
**build(ctx) 可用 API 清单**

  草图：
    ctx.add_sketch(part, plane)   plane: "xy"/"yz"/"zx"
    ctx.draw_rect(sk, x, y, w, h)
    ctx.draw_circle(sk, cx, cy, r)
    ...

  特征：
    ctx.add_shaft(part, sk)  → Shaft  旋转体（绕 Z 轴 360°）
    ...

  旋转体 / 环形槽约束（重要）：
    - 草图必须在 ZX 平面（plane="zx"）
    - H 轴 = -X 方向，V 轴 = Z 方向
    - 轮廓须全在 H > 0 侧，不可跨越旋转轴
```

**AI 的全部"建模知识"都来自这段文字。** 没有写进 System Prompt 的 API，AI 不会用。

---

## 第 3 步：LLM 推理 → 生成脚本

LLM 根据用户意图 + System Prompt 的约束，推理出：

- 圆筒 = 旋转特征 → 用 `ctx.add_shaft`
- 旋转草图必须在 ZX 平面
- 外径 100 → 轮廓右边界 V = 50（半径）
- 内径 50  → 轮廓左边界 V = 25（半径）
- H 方向 = 高度，用户未指定，LLM 自行设定一个合理值（如 80mm）

生成的 `build(ctx)` 脚本（字符串形式）：

```python
def build(ctx):
    part = ctx.create_part("圆筒")
    sk   = ctx.add_sketch(part, "zx")
    # ZX 平面约定：H = -X 方向，V = Z 方向（旋转半径方向）
    # 矩形轮廓：H=0~80（高度），V=25~50（内外半径）
    ctx.draw_rect(sk, 0, 25, 80, 25)   # x=H起, y=V起, w=H宽, h=V高
    ctx.step("旋转轮廓完成")
    shaft = ctx.add_shaft(part, sk)
    ctx.step("旋转体完成")
    ctx.update_part(part)
```

LLM 通过 **tool call** 将此脚本传给 `run_modeling_script` 工具。

---

## 第 4 步：tool_run_modeling_script 执行脚本

入口：`catia_copilot/ai/tools.py: tool_run_modeling_script(script)`

```
1. 写入临时文件  → %TEMP%/catia_copilot_modeling/generated_model.py
2. 检查是否有 def build(   （格式校验）
3. importlib 加载模块
4. 创建 ModelingContext()
5. 调用 module.build(ctx)
```

---

## 第 5 步：ModelingContext 逐步执行并追踪

`ModelingContext`（`catia_copilot/catia/modeling.py`）是 AI 脚本的执行沙箱：

每次 `ctx.xxx()` 调用都经过 `_run()` 包装：

```python
def _run(self, func_label, fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        self._steps.append({"step": func_label, "status": "ok", ...})
        return result
    except Exception as exc:
        self._steps.append({"step": func_label, "status": "error", ...})
        raise ModelingStepError(step_name=func_label, ...)
```

本例执行顺序：

| 步骤 | ctx 调用 | 实际函数 |
|------|---------|---------|
| 1 | `ctx.create_part("圆筒")` | `modeling.create_part` |
| 2 | `ctx.add_sketch(part, "zx")` | `modeling.add_sketch` |
| 3 | `ctx.draw_rect(sk, 0,25,80,25)` | `modeling.draw_rect` |
| M | `ctx.step("旋转轮廓完成")` | 仅记录里程碑 |
| 4 | `ctx.add_shaft(part, sk)` | `modeling.add_shaft` |
| M | `ctx.step("旋转体完成")` | 仅记录里程碑 |
| 5 | `ctx.update_part(part)` | `modeling.update_part` |

---

## 第 6 步：modeling.py 调用 CATIA COM

`modeling.add_shaft(part, sketch)`（`catia_copilot/catia/modeling.py`）：

```python
def add_shaft(part, sketch):
    app_com = get_catia_v5_application()          # 获取 COM 连接
    shaft   = part.shape_factory.add_new_shaft(sketch)   # pycatia
    z_ref   = _get_z_axis_ref(app_com.ActiveDocument)    # 构造 Z 轴
    shaft.revolute_axis = z_ref                          # 设旋转轴
    return shaft
```

`_get_z_axis_ref` 的内部过程：

```python
# 1. 创建 (0,0,0)→(0,0,1) 的方向线
# 2. 命名为 "Z 轴"，插入 MainBody.HybridShapes
# 3. 返回 Reference
```

**这一层的知识**（Z 轴怎么创建、草图平面约定等）是我们通过实验探查出来的，
记录在 `docs/BREP_NAMING_REFERENCE.md`，并封装进函数，AI 不需要了解细节。

---

## 第 7 步：pycatia / win32com → CATIA V5

pycatia 是对 win32com COM 调用的 Python 封装：

```
pycatia.part.shape_factory.add_new_shaft(sketch)
  → win32com: part_com.ShapeFactory.AddNewShaft(sketch_com)
    → CATIA V5 COM 服务器（进程间 RPC）
      → CATIA 创建 "旋转体.1" 特征
```

通信机制：**Windows COM（组件对象模型）进程间调用**，Python 进程通过 RPC 控制 CATIA 进程，两者运行在同一台机器。

---

## 第 8 步：结果逐层返回

CATIA 执行完成后，结果逐层向上返回：

```
modeling.py 返回 Shaft 对象
  → ModelingContext._run() 记录 {"step": "add_shaft()", "status": "ok"}
    → build(ctx) 正常结束
      → tool_run_modeling_script 组装 JSON：
        {
          "success": true,
          "part_name": "圆筒",
          "features": ["旋转体.1"],
          "steps": [
            {"step": "create_part('圆筒')", "status": "ok"},
            {"step": "add_sketch(plane='zx')", "status": "ok"},
            {"step": "draw_rect(...)", "status": "ok"},
            {"step": "旋转轮廓完成", "status": "ok", "milestone": true},
            {"step": "add_shaft()", "status": "ok"},
            {"step": "旋转体完成", "status": "ok", "milestone": true},
            {"step": "update_part()", "status": "ok"}
          ]
        }
          → LLM 读取结果，告诉用户："已成功建立圆筒，旋转体.1 已创建。"
```

---

## 失败时的路径

若 `add_shaft()` 失败（例如草图不在 ZX 平面）：

```
modeling.add_shaft() 抛出 COMException
  → ModelingContext._run() 捕获，抛出 ModelingStepError
    → tool_run_modeling_script 捕获，返回：
      {
        "success": false,
        "failed_step": "add_shaft()",
        "error": "COMException: ...",
        "steps": [...已完成的步骤...],
        "features_at_failure": ["草图.1"]
      }
        → LLM 读到 failed_step + error，分析原因，修改脚本，重新调用
```

**AI 可以自主诊断和重试**，因为它知道精确的失败位置。

---

## 知识层与代码层的对应关系

```
用户/AI 可见层          代码位置                    维护方式
─────────────────────────────────────────────────────────────
AI 行为规范            DEFAULT_SYSTEM_PROMPT        手动更新（每加新API就写进去）
脚本执行 + 反馈         tool_run_modeling_script     自动（框架层，少改）
步骤追踪               ModelingContext               自动（框架层，少改）
CATIA API 封装         modeling.py                  每次探查后更新
COM 行为知识           BREP_NAMING_REFERENCE.md     开发者备忘录（不影响AI）
CATIA 实际操作         pycatia / win32com / CATIA   透明（不需要关心）
```

**关键规律**：探查出新的 COM 用法 → 封装进 `modeling.py` → 加进 `DEFAULT_SYSTEM_PROMPT` → AI 就会用了。中间的文档只是给人类看的。

---

## 本例完整数据流时序图

```
用户        LLM          tool层            ModelingContext    modeling.py    CATIA
 │           │              │                    │                │            │
 │─"建圆筒"─▶│              │                    │                │            │
 │           │─tool_call──▶│                    │                │            │
 │           │  script=     │─build(ctx)────────▶│                │            │
 │           │  "def build" │                    │─create_part()─▶│─AddPart()─▶│
 │           │              │                    │◀──Part─────────│◀──────────│
 │           │              │                    │─add_sketch()──▶│─AddSketch─▶│
 │           │              │                    │─draw_rect()───▶│─CreateLine▶│
 │           │              │                    │─add_shaft()───▶│─AddShaft──▶│
 │           │              │                    │                │─_get_z_ref─▶(创建Z轴)
 │           │              │                    │─update_part()─▶│─Update()──▶│
 │           │              │◀──steps JSON───────│                │            │
 │           │◀─tool result─│                    │                │            │
 │◀─"已完成"─│              │                    │                │            │
```
