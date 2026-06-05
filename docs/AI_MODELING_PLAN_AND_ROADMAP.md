# AI 辅助建模完整计划与路线图

## 背景与验证

已验证 pycatia 写入 API 实际可用（`experiments/debug_create_part.py`），
完整链路跑通：新建零件 → 在 XY 平面新建草图 → 画矩形 → Pad → 刷新 → Analyze 读取几何中心。

连接层使用项目自有的 `wrap_application()` / `wrap_product()`，不使用 pycatia 的 `catia()` 入口，
保持连接稳定性。

---

## 核心架构思路

### 脚本驱动建模（主流程）

AI 作为"规划者"，**一次性生成完整的 Python 脚本文件**，程序直接执行脚本完成建模，
执行过程不调用 AI。

```
用户描述 → AI 思考 → 生成 .py 脚本文件 → 用户确认 → 程序执行
```

生成的脚本既是机器可执行的，也是人类可读、可修改、可版本管理的：

```python
# generated_model.py  ← AI 生成，人类可读，程序直接执行
from catia_copilot.catia.modeling import *

def build():
    part = create_part("底座")

    sk = add_sketch(part, "xy", name="轮廓草图")
    draw_rect(sk, 0, 0, 100, 50)
    pad = add_pad(part, sk, 20)

    top  = get_top_face(part)
    sk2  = add_sketch_on_face(part, top, name="孔草图")
    draw_circle(sk2, 10, 10, 2)
    hole = add_hole_from_sketch(part, sk2, diameter=4, depth=20)

    add_rect_pattern(part, hole, nx=2, ny=3, dx=80, dy=15)
    update_part(part)
    return part
```

**优点：**
- 脚本本身可读、可改、可复用、可版本管理
- 执行时完全确定性，不依赖 AI
- 可用 Python 的循环、函数、条件表达复杂模型，突破"工具调用数量"上限
- AI 生成一次，反复执行

**约定：** 所有 AI 生成的脚本包含一个 `build()` 函数，程序调用 `build()` 执行，
安全 import 而不会立即触发 CATIA 操作。

### MCP 工具调用（辅助流程）

MCP（Model Context Protocol）允许 AI 逐步调用工具并实时获得反馈，适用于：
- 交互式调试：AI 每步能看到执行结果再决定下一步
- 错误恢复：某步失败时 AI 诊断并修正

复杂模型不应依赖 MCP 主流程（每步调用 AI 代价高、会话断则无记录），
但可作为脚本生成的辅助手段——执行脚本出错后，把错误喂给 AI 修正脚本。

### 两层架构

```
┌─────────────────────────────────────────────────────────────┐
│  用户描述                                                    │
│     ↓                                                       │
│  AI（规划层）─── 生成 .py 脚本 ──→ 用户确认                  │
│                                        ↓                   │
│                                    程序执行脚本              │
│                                        ↓                   │
│                                    CATIA 建模               │
│                                        ↓                   │
│                                    出错？─→ 错误信息 → AI 修正 │
└─────────────────────────────────────────────────────────────┘
```

---

## API 设计原则

### 1. 名称字符串接口（而非传递对象）

AI 生成脚本时只需处理字符串，不管理 pycatia 对象生命周期：

```python
# 当前（对象传递）
sk  = add_sketch(part, "xy")
pad = add_pad(part, sk, 20)

# 目标（名称字符串）
add_sketch(part, "xy", name="轮廓草图")
add_pad(part, sketch_name="轮廓草图", depth=20)
```

### 2. 语义化几何查询（最关键的扩展）

几何引用（面、边）是最大的障碍。需要提供语义化查询函数，
让 AI 能用自然语言对应的函数来定位几何元素：

```python
get_top_face(part)                    # 最高 Z 的面
get_bottom_face(part)                 # 最低面
get_face_by_normal(part, [0, 0, 1])   # 法向量为 Z+ 的面
get_edge_by_length(part, 20)          # 长度约为 20mm 的边
get_face_by_feature(part, "凸台.1", "top")  # 某特征的特定面
```

这决定了 AI 能建多复杂的零件，**优先级高于阵列、倒角等其他特征**。

### 3. 脚本执行机制

```python
# 程序侧执行脚本
import importlib.util, traceback

def run_modeling_script(script_path: str) -> tuple[bool, str]:
    """执行 AI 生成的建模脚本，返回 (成功, 错误信息)"""
    try:
        spec   = importlib.util.spec_from_file_location("model", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build()
        return True, ""
    except Exception:
        return False, traceback.format_exc()
```

---

## 阶段计划

### 阶段一：建模工具层 ✅（已完成）

`catia_copilot/catia/modeling.py`，封装 pycatia 建模操作，已验证端到端可用。

| 分类 | 函数 |
|---|---|
| 文档 | `create_part` / `get_active_part` / `update_part` / `save_part` |
| 草图 | `add_sketch` / `draw_rect` / `draw_circle` / `draw_point` |
| 特征 | `add_pad` / `add_pocket` / `add_hole_from_sketch` |
| 修饰 | `add_edge_fillet` / `add_chamfer` |
| 阵列 | `add_rect_pattern` / `add_circ_pattern` |
| 查询 | `list_features` / `list_sketches` / `get_mass_props` |

关键已解决的技术坑：

| 问题 | 解决方案 |
|---|---|
| `plane_xy` 返回 `AnyObject` 而非 `Reference` | 必须经 `part.create_reference_from_object()` 转换 |
| `Part` 没有 `sketches` 属性 | 草图挂在 `part.main_body.sketches` 下 |

### 阶段一补充：语义化几何查询（下一个里程碑）

扩展 `modeling.py`，增加面/边定位函数：

```python
get_top_face(part)
get_bottom_face(part)
get_face_by_normal(part, normal_vector, tolerance=5.0)
get_face_of_feature(part, feature_name, position="top"|"bottom"|"side")
get_edge_by_length(part, length_mm, tolerance=0.5)
add_sketch_on_face(part, face_ref, name=None)
add_hole_on_face(part, face_ref, x, y, diameter, depth)
```

### 阶段二：AI 工具定义层

扩展 `catia_copilot/ai/tools.py`，将阶段一函数包装为 AI 可调用工具，
定义 JSON schema，供脚本生成时的 AI 规划使用。

### 阶段三：脚本生成与执行

- **Prompt 模板**：告诉 AI 生成什么格式、用哪些函数、如何处理参数单位
- **脚本执行器**：`run_modeling_script()` + 错误捕获 + 反馈给 AI 的接口
- **UI 集成**：在主界面加入"AI 建模"入口，用户输入描述，AI 生成脚本，
  确认后执行

### 阶段四：测试与迭代

`experiments/` 目录，按复杂度递增验证：

| 用例 | 涉及功能 |
|---|---|
| 长方体 | create_part + draw_rect + pad |
| 带通孔底座 | + 语义化面定位 + hole |
| 法兰盘 | draw_circle + shaft + circ_pattern |
| 壳体 | + fillet + shell |
| 多特征组合件 | 综合 |
| 产品 | ProductDocument + 摆放位置 |

---

## 文件结构

```
catia_copilot/
  catia/
    modeling.py              ← 阶段一（已完成）
    connection.py            ← wrap_application / wrap_product
  ai/
    tools.py                 ← 阶段二（扩展）
experiments/
  debug_create_part.py       ← 原始端到端验证
  test_modeling_api.py       ← modeling.py 接口验证
  generated/                 ← AI 生成的脚本存放处（建议）
docs/
  AI_MODELING_PLAN_AND_ROADMAP.md   ← 本文件
  PYCATIA_OVERVIEW.md               ← pycatia 模块参考
```

---

## 关于 Markdown 保存

本文件即为完整的 Markdown 格式文档，可直接在任何支持 Markdown 的编辑器中查看
（VS Code、Typora、GitHub 等）。

对话中 AI 的回复如果需要保存为 Markdown，可以：
1. 直接复制回复内容粘贴到 `.md` 文件
2. 请 AI 将内容写入指定的 `.md` 文件（如本次操作）

---

*最后更新：2026-06-02*
