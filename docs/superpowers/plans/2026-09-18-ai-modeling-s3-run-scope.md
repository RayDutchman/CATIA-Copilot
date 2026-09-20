# AI 建模运行作用域与记录（S3）Implementation Plan

> **执行状态：已完成。** 本文件用于补齐阶段计划与实际交付记录；代码已通过无 CATIA 单元测试并提交推送。

**Goal:** 为每次建模运行建立明确的目标文档身份、独立脚本/运行记录和可取消的步骤边界语义。

**Architecture:** `document_scope.py` 只负责文档身份的纯数据描述与匹配；`tool_run_modeling_script` 负责每 run 唯一文件、文档绑定和三层状态；`ModelingContext` 只在步骤开始前检查取消，不承诺中断正在执行的 COM 调用。

**Tech Stack:** Windows Python 3.13、标准库 `unittest`、pycatia/win32com 现有运行时。

## Global Constraints

- 不主动切换或保存用户文档。
- 不承诺 CATIA COM 调用可被单步打断。
- 不引入事务回滚或新依赖。
- 不触碰 `part_templates/`。

## Task 1：文档身份与目标匹配

**Files:**
- Create: `catia_copilot/catia/document_scope.py`
- Test: `catia_copilot/tests/test_document_scope.py`

- [x] 定义 `DocumentIdentity(name, full_name, part_number, document_type)` frozen 数据结构。
- [x] 实现 `describe_document(document)`、`matches_document(identity, requested_id)`。
- [x] 覆盖未保存文档、完整路径、PartNumber 和文档名匹配。

## Task 2：per-run 文件与状态分层

**Files:**
- Modify: `catia_copilot/ai/tools.py`
- Test: `catia_copilot/tests/test_document_scope.py`

- [x] 为每次运行生成独立 `generated_model_<run_id>.py` 和 `modeling_run_<run_id>.json`。
- [x] 在执行前拒绝不匹配的 `target_document_id`，不静默操作其他活动文档。
- [x] 返回 `execution`、`model_update`、`verification` 三层状态及前后文档身份。

## Task 3：步骤边界取消

**Files:**
- Modify: `catia_copilot/catia/modeling.py`, `catia_copilot/ai/agent.py`, `catia_copilot/ui/ai_chat_panel.py`
- Test: `catia_copilot/tests/test_modeling_cancellation.py`

- [x] `AgentWorker.stop_requested` 注入 `ModelingContext(cancel_check=...)`。
- [x] `_run()` 与 `step()` 在新步骤开始前抛出 `ModelingCancelledError`。
- [x] 工具层返回 `execution=cancelled`，不把取消包装成普通步骤失败。

## 验收证据

- S3 运行探针创建 `S3_RunProbe` 新文档，返回 `created_document`、run_id、脚本路径和运行记录路径。
- 错误 `target_document_id` 在执行前拒绝。
- 取消测试与全量正式回归通过。
- 提交：`5b35ca9`、`788c057`、`831dba5`。
