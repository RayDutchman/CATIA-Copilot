# AI 建模可靠性架构复盘与设计规格

- **日期**：2026-09-18
- **状态**：S1-S4 已实施并完成当前验收；代码与实机证据见各阶段计划/基准记录
- **分支**：`feat/ai-modeling-reliability`（自 `ca8b8b7` 基于原目录开发）
- **配套文档**：`docs/AI_MODELING_PLAN_AND_ROADMAP.md`（复盘与阶段索引）
- **S1 精细计划**：`docs/superpowers/plans/2026-09-18-ai-modeling-s1-contract.md`（另立）
- **S3 精细计划**：`docs/superpowers/plans/2026-09-18-ai-modeling-s3-run-scope.md`
- **S4 精细计划**：`docs/superpowers/plans/2026-09-18-ai-modeling-s4-verification.md`

## 1. 背景

AI 辅助建模主链路已跑通：后台 LLM 生成 `def build(ctx)` 脚本 → 主线程 COM 执行 → 结构化反馈。
此前验证聚焦"能建出来"，本次转向**可靠性**：让同一套链路可重复、可诊断、可验证。

## 2. 复盘结论

### 2.1 保留的架构（不推翻）

| 项 | 说明 |
|---|---|
| 脚本驱动建模 | AI 一次性生成 `build(ctx)` 完整脚本，程序执行 |
| `ModelingContext` | `ctx` 封装全部建模 API，脚本零 import |
| 主线程 COM | COM 调用全部在主线程，后台线程只跑 LLM |
| 后台 LLM | 生成、反馈、重试都在后台，不阻塞 COM |

### 2.2 已确认缺口

前六项属于本次可靠性基线；第七项记录为后续参数编辑立项的依据：

1. **prompt/schema API 漂移**：默认 prompt 的**建模**段（`tools.py` 自 `**建模**` 至 `**文档属性**` 前）与 `run_modeling_script.description` 各自维护、重复描述，接口变更时两边容易不一致。
2. **法向语义错用**：矩形四边的固定法向被套用到其他轮廓；圆柱侧面及腰形槽的圆弧侧面无单一全局法向，腰形槽的直线侧面仍是平面。
3. **失败默认 4 边**：几何未知时按矩形 4 边默认处理，对圆柱/槽产生错误边数。
4. **文档目标不一致**：脚本隐式依赖 CATIA 当前 `ActiveDocument`，与任务指定的 part 目标可能不是同一文档。
5. **固定文件覆盖**：`generated_model.py` 固定名每次覆盖，缺每 run 独立记录，无法追溯单次。
6. **success 含义过弱**：`success` 只代表脚本未抛异常，不代表模型数据正确，无结果验证层。
7. **无参数绑定**：脚本内数值只是 Python 变量，尚未绑定为 CATIA 参数，无法参数化修改。

### 2.3 本立项不做（后续独立立项）

以下独立立项，不在本次四阶段内一锅实现：

- 参数增量修改（脚本变量绑定为 CATIA 参数）
- 草图约束（尺寸/几何约束化）
- 阵列、倒角、镜像、Shell 等特征扩展
- 图片观察（截图/视觉反馈）
- **Responses 协议**改造：独立立项，不掺入 S1

### 2.4 关键决策记录

| 决策 | 理由 |
|---|---|
| 契约用标准库纯函数模块 | 可被无 CATIA 环境单测直接验证，不引入运行时副作用 |
| 单一来源生成建模段与 description | 消除 prompt/schema 双处维护导致的 API 漂移 |
| 签名以 `ModelingContext` 为准 | `ctx` 是脚本唯一入口，改写上层描述必须与之一致 |
| 阵列从可用项移除、内部记录原因 | 方向参数有 bug 未验证，不再误导 LLM 调用 |
| 不加 A3 不支持列表 | 用户已否决长篇清单，契约只写可用与约定 |
| 不宣称 COM 缓存解决拓扑稳定 | 缓存仅复用引用，拓扑问题必须由几何查询层面解决 |
| 不假设自动事务回滚、不主动保存用户文件 | 当前没有经过验证的任务级恢复机制，失败中间态须如实报告；保存仍需用户要求 |

## 3. 阶段总览

| 阶段 | 名称 | 主题 | 状态 |
|---|---|---|---|
| S1 | 能力契约统一 | 单一来源建模描述 + 契约测试 | 已完成 |
| S2 | 几何查询可靠性 | planar/cylindrical/unknown 分类，不伪造法向 | 已完成 |
| S3 | 文档绑定与运行记录 | 目标文档绑定、每 run 记录、状态分层 | 已完成 |
| S4 | 无 CATIA 测试与手动基准 | 单元测试 + 有限手动基准 | 已完成 |

阶段依赖链：S1 → S2 → S3 → S4。各阶段同步补齐对应测试，S4 汇总实机基准；进入后阶段前须单独写精细计划（见第 8 节）。

## 4. S1 能力契约统一

### 4.1 范围

- 新建 `catia_copilot/ai/modeling_contract.py`：标准库元数据纯函数模块，无 COM、无 UI、不读运行时源码；`build(ctx)` 与建模 API 的签名以 `catia_copilot/catia/modeling.py` 的 `ModelingContext` 为准。
- 生成两个函数：
  - `build_modeling_prompt_section() -> str`：默认 prompt 的**建模**段（自 `**建模**` 至 `**文档属性**` 前，含脚本模板与失败处理模板）。
  - `build_run_modeling_script_description() -> str`：`run_modeling_script` 的 `description` 字段。
- `catia_copilot/ai/tools.py` 仅做两处拼接替换：`DEFAULT_SYSTEM_PROMPT` 的建模段落、`run_modeling_script.description`。

### 4.2 边界（红线）

- **不动 COM 实现**：不修改 `catia_copilot/catia/modeling.py` 的 COM 行为。
- **不补未实测倒角**：`add_chamfer` 等无自动化验证的特征不在契约中扩写能力。
- **保留低层接口说明**：`get_pad_face_brep` / `make_pad_edge_ref`（含 `ctx` 同名列，`modeling.py:1970/1993`）为已验证可用能力，契约中保留、不删除已公开可用能力。
- **阵列不当可用项**：`add_rect_pattern` / `add_circ_pattern` 方向参数有 bug 暂不可用，内部排除集合记录原因，不列入可用项。
- **不加 A3 不支持列表**：不加用户拒绝的长篇"不支持"清单。

### 4.3 语义约定（写进契约）

- 长度单位 `mm`，角度单位 `度`。
- 变量定义先于使用。
- `axis` 平面语义沿用原提示的三轴旋转体约束映射（z→zx、y→xy、x→xy 且 H 为旋转轴）。
- `prepare_revolute_axis` 先于草图创建。
- 脚本末尾执行 `update`。
- 命名区分 `name` / `PartNumber`（零件号）、`nomenclature` / `Nomenclature`（用途命名）及文档名字段。
- B-Rep 面草图（`add_sketch_on_pad_top/bottom/side`，关联 Pad 深度）与固定偏移草图（`add_sketch_at_height`，不关联特征）语义不同，分别说明。
- 已有模型与新建零件不可盲目重跑：进入前确认目标状态（是否已在同一 part 上建模）。

### 4.4 测试

- 测试放 `catia_copilot/tests/`（根 `tests/` 被 git 忽略，不放根）。
- AST 测试：从模块源码解析，断言契约列出 API 的**精确参数顺序、默认值、示例调用**与 `ModelingContext` 一致。
- 文本断言：建模 prompt 段与 description 均含关键约定（mm/度、变量先行、末尾 update、命名区分）。
- 运行：`python -m unittest catia_copilot.tests.test_modeling_contract -v`（PowerShell）。

### 4.5 依赖

- 现有 `tools.py`（修改描述消费端）、`modeling.py`（只读不改）；`unittest` 标准库（不新增依赖）。

### 4.6 可交付物

- `catia_copilot/ai/modeling_contract.py`（两函数 + 元数据）。
- `tools.py` 两处替换（建模段、`description`）。
- `catia_copilot/tests/test_modeling_contract.py`。

### 4.7 验收与 stop 条件

- **验收**：`unittest discover` 通过；默认 prompt 建模段与 description 单一来源、无重复维护；`tools.py` 仅增加契约导入及替换两处消费端；不触碰 `part_templates/`。
- **stop**：契约导入失败、需要改 COM 行为、或需动 `part_templates/` 时，立即停止并汇报。

### 4.8 涉及文件边界（S1）

| 文件 | 动作 |
|---|---|
| `catia_copilot/ai/modeling_contract.py` | 新建 |
| `catia_copilot/ai/tools.py` | 契约顶层导入及两处替换（建模段、`description`） |
| `catia_copilot/tests/test_modeling_contract.py` | 新建 |
| `catia_copilot/catia/modeling.py` | 只读，不改 |
| `part_templates/` | 禁止触碰 |
| `docs/`（spec/roadmap/plan） | 本规格、既有路线图、S1 详细计划；后阶段按进入规则另立计划 |

## 5. S2 几何查询可靠性

### 5.1 范围

- 面几何分类：`planar / cylindrical / unknown`。
- 曲面不伪造全局 normal：`normal` 仅对 planar 面成立；圆柱侧面及槽的圆弧侧面不套用矩形法向，槽的直线侧面独立计算。
- 未知不假装矩形：移除失败默认 4 边处理，未知几何明确报错或返回 `unknown` 分类。
- 保留已验证 BRep 路径：`get_pad_face_brep`、`get_pocket_*`、`make_pad_edge_ref` 等行为不变。
- 不宣称 COM 缓存解决拓扑稳定：引用缓存仅复用引用，不解决拓扑变化，文档不写"缓存即稳定"。

### 5.2 依赖

- 依赖 S1 契约（查询能力描述写入契约），读取现有几何查询实现作参考。

### 5.3 可交付物

- 面分类查询接口（planar/cylindrical/unknown）+ 契约条目 + 单元测试。
- 移除 4 边默认分支的相关改动及测试。

### 5.4 验收与 stop 条件

- **验收**：矩形/圆柱/槽已知样本分类正确；无全局 normal 的曲面不再产出矩形法向；失败不再默认 4 边。
- **stop**：若需大规模 COM 行为改动或引入拓扑容差魔法，停止并重新立项。

## 6. S3 文档绑定与运行记录

### 6.1 范围

- 目标文档绑定：不依赖隐式 `ActiveDocument`，显式绑定与任务一致的 part 目标，避免漂移。
- 每 run 独立记录：固定名 `generated_model.py` 改为每 run 唯一记录，可追溯单次。
- 状态分层：`execution / model-update / verification` 三层独立上报，不把"执行成功"当"模型正确"。
- 取消语义：删除不实承诺，取消只在步骤边界检查，不承诺打断进行中的 COM 调用。
- 不保存用户文件：除非用户明确要求，不主动保存文档。
- 无事务回滚假设：不承诺回滚，失败保留中间态并记录。
- docID 标识：未保存文档不可只用路径，须用文档标识（docID）跟踪。

### 6.2 依赖

- 依赖 S1 契约（记录与状态语义），读取现有 `modeling.py`、`tools.py`、`connection.py` 的文档访问路径作参考。

### 6.3 可交付物

- 文档绑定逻辑、per-run 独立记录、三层状态上报、步骤边界取消检查、docID 追踪。

### 6.4 验收与 stop 条件

- **验收**：目标与 `ActiveDocument` 不一致时不静默错建；每 run 记录独立可回溯；三层状态分别可读；取消不承诺打断 COM；未保存文档仍可追踪。
- **stop**：若需保存用户文件或引入回滚事务机制，停止并更新设计。

## 7. S4 无 CATIA 测试与手动基准

### 7.1 范围

- 无 CATIA 单元测试：mock/伪对象覆盖契约合成、状态分层、记录逻辑等纯逻辑。
- 手动 CATIA 有限基准：固定输入冒烟清单，逐项执行并记录。
- 断言规则：特征名称不可作为唯一断言（`凸台.1` 存在不代表几何正确），须配合几何/属性断言。

### 7.2 依赖

- 依赖 S1-S3 的可 mock 纯逻辑；手动基准需本机 CATIA V5 与已验证 API。

### 7.3 可交付物

- 无 CATIA 单元测试套件、手动基准清单与记录文档。

### 7.4 验收与 stop 条件

- **验收**：无 CATIA 环境单元测试全绿；手动基准逐项有结果记录；断言均以几何/属性为准而非仅特征名。当前证据：正式测试 `363 tests OK`；S2 B1-B6 全部 PASS；B7 的 `--fail-check` 返回 1、模拟无 CATIA 路径返回 2；B8 与正式测试同为 `363 tests OK`。
- **stop**：若手动基准暴露需改 COM 实现的缺口，回退补 S2/S3 再进。

## 8. 阶段进入规则与实施纪律

- 每阶段进入前单独写精细计划到 `docs/superpowers/plans/`；S1-S4 均已补齐对应计划与实际完成状态。
- 子 agent 一次只接 1 个可验收任务；2-3 核心文件 + 对应测试；报告 diff / 命令 / 结果 / 待决。
- 不同 agent 不并发写同一文件（尤其 `tools.py` / `modeling.py`）。
- 不嵌套代理；Windows PowerShell + Python `unittest`；不新增依赖；不 commit/push（除非明确要求）；中文注释、英文命名。

### 阶段进入检查清单

| 阶段 | 进入前必须满足 |
|---|---|
| S1 | 本 spec 已批准；S1 精细计划落盘（另一 agent）；契约文件路径确认 |
| S2 | S1 验收通过；面分类样本与期望结论明确 |
| S3 | S2 验收通过；绑定/记录/状态需求拆成可验收任务 |
| S4 | S1-S3 纯逻辑可 mock；手动基准清单与 CATIA 环境就绪；B1-B8 结果已记录 |
