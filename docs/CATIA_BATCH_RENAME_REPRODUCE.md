# 批量改名：固定输入、脚本执行、结果检查

把 AI 的作用限制在确认一次范围和规则，执行过程由固定脚本完成，不逐文件询问或临时推断。

## 32 案例的固定方法

1. 以原始文件清单为输入，规则固定为 `VL01` / `V01` → `VE01`。不把 `VT28` 加入规则，不把新输出再次当成源文件扫描。
2. 按主设计、gear-V1、gear-V2、gear-V3、gear-V4 五个版本批次执行。上一批卸载后再加载下一批；每次打开核对实际 `FullName`。
3. 一次加载该批 CAD 与引用方，修改指定业务属性、实例名、图纸文字及表格单元格，集中执行原生 SaveAs，然后显式 Save 本批文档。保留旧文件。
4. 关闭本批文档，先重新打开引用方核对磁盘引用。不是先打开新零件再判断是否断链。
5. 从已保存图纸重新导出 PDF；多页输出合并后核对页数，抽取文字确认旧编号没有遗漏。
6. STEP 来源唯一时按对应 CAD 输出；来源有多个版本时使用原 STEP 自身导入、改产品编号、重导出，不猜 CAD 版本。固定质量相对误差阈值 **0.01%**，并核对产品结构和编号。

本机执行脚本与原始输入、各阶段日志保存在仓库 `.superpowers/`：主脚本 `case32_batch_main.py`、批次切换 `case32_output_batches.py`、图框及 PDF `case32_drawing_tables.py`。这些是包含案例路径的实验脚本，**不是已发布的通用 cat5cli 命令**。已完成的 32 目录不应重新运行首次准备阶段。

## STEP 批处理的可复用命令

独立脚本：`scripts/catia_step_rename_batch.py`。需要 Windows Python、pywin32 和已运行的 CATIA；它不是 npm 安装中无需 Python 的 EXE。

准备 UTF-8 JSON 文件 `step-files.json`，列出源 STEP 的完整路径：

```json
[
  "D:/项目/输出/MD_VL01-32100-013_A.stp",
  "D:/项目/输出/MD_V01-32200-003_A.stp"
]
```

从仓库目录执行：

```powershell
python -X utf8 scripts/catia_step_rename_batch.py --input step-files.json --report step-result.json --relative-mass-tolerance 0.0001
```

`0.0001` 是比率，即 **0.01% / 100 ppm**。脚本会：

- 提前检查输入文件、规则和多个源映射到同一目标的冲突。
- 顺序导入 STEP，从 Documents 找到真实 CATPart/CATProduct，而非使用临时 STEP 对象的 Product。
- 改产品编号并导出；旧文件保留，目标已存在时不覆盖，直接核验。
- 冷重开输出，比较质量、树结构、预期产品编号，逐项记录结果；失败项不标为完成。
- 恢复原 DisplayFileAlerts。返回码 0 表示所有项目及关闭步骤通过，否则返回 1。

再次运行时换一个报告文件名即可，已有输出仍只做核验。报告文件不覆盖；不需要 AI 判断哪个输出该重新导出。失败输出先保留，依据报告中的具体原因处理。

本次 34 个 STEP 的实际命令：

```powershell
python -X utf8 scripts/catia_step_rename_batch.py --input .superpowers/case32-main-batch/exports.json --report .superpowers/case32-main-batch/step-roundtrip-approved.json --relative-mass-tolerance 0.0001
```

脚本也支持导出报告的 `ambiguous[].file` 输入形式。质量和树结构检查是此次采用的验收标准，不等价于逐面几何完全一致。缺失引用或未知模态窗口不会被当作成功；不要以“运行结束”代替检查报告和返回码。

本轮结果：34 件中 32 件通过，两件超出 0.01%（017 为 0.0426755%，019 为 0.0239614%）。两件源路径已固化到 `.superpowers/case32-main-batch/failed-step-files.json`，不需要 AI 重新挑选。需要复查时可直接使用该文件作为 `--input`，另指定新报告文件；已有输出不会被重导出覆盖。
