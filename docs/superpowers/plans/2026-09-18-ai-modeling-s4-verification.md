# AI 建模结果验证与基准验收（S4）Implementation Plan

> **执行状态：已完成。** 本文件用于补齐阶段计划与实际交付记录；当前 S4 已完成无 CATIA 验证、手动基准和正式回归。

**Goal:** 让建模成功不仅表示脚本未抛异常，还能按特征、质量和重心条件输出独立的模型验收结果，并用有限 CATIA 基准验证几何查询闭环。

**Architecture:** `modeling_verifier.py` 是纯数据验证器，不访问 COM；`run_modeling_script` 将可选 `verification` 条件接入结果和运行记录；S2 手动基准脚本负责几何/属性断言，不能仅凭特征名判定成功。

**Tech Stack:** Windows Python 3.13、标准库 `unittest`、现有 CATIA V5/pycatia；不新增依赖。

## Global Constraints

- 无 CATIA 单元测试不得 import CATIA COM。
- 验证失败必须结构化返回，不得伪装成脚本执行失败。
- 不触碰 `part_templates/`。
- 手动基准只创建未保存试验零件，不保存、关闭或删除文档。

## Task 1：纯数据模型结果验证

**Files:**
- Create: `catia_copilot/catia/modeling_verifier.py`
- Test: `catia_copilot/tests/test_modeling_verifier.py`

- [x] 支持 `required_features`、`feature_count`、`mass_kg`、`cog_mm`。
- [x] 返回 `not_requested`、`passed`、`failed` 与逐项 `checks`。
- [x] 对非法类型、布尔值、非三维边界、反向范围返回结构化失败，不抛出异常。

## Task 2：工具契约与执行结果接线

**Files:**
- Modify: `catia_copilot/ai/tools.py`, `catia_copilot/ai/modeling_contract.py`
- Test: `catia_copilot/tests/test_modeling_contract.py`

- [x] 在 `run_modeling_script` schema 增加可选 `verification` 对象。
- [x] 将验证结果写入返回 JSON 和三层状态中的 `status.verification`。
- [x] Prompt/schema 说明验收失败不能被说成模型验证通过。

## Task 3：手动 CATIA 基准与退出码

**Files:**
- Modify: `catia_copilot/tests/catia_manual/s2_geometry_query_bench.py`
- Update: `docs/superpowers/benchmarks/2026-09-18-ai-modeling-s2-geometry-acceptance.md`

- [x] B1-B5 完整基准运行：`PASS=5 FAIL=0 BLOCKED=0`。
- [x] B6 `--b6` 运行：`PASS=1 FAIL=0 BLOCKED=0`。
- [x] B7 `--fail-check` 返回 1；无 CATIA 路径返回 2。
- [x] B8 正式 `unittest discover` 全量通过。

## 验收证据

- 2026-09-20 B1-B6 全部 PASS。
- B7 退出码验证通过：FAIL/1、BLOCKED/2。
- B8 最终命令：`python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -q`，`363 tests OK`。
- 提交：`e544552 feat(ai): add modeling result verification`、`7033f44 fix(ai): validate modeling verification bounds`。
