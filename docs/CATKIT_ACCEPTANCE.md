# Catkit 验收记录

> **当前状态（2026-11-05）：** 用户侧名称已迁移为 cat5cli，预览版本为 0.1.0-beta.1；本文其余 Catkit 命名及数据均为历史验收记录，不代表当前发布状态。CLI/Skill 当前入口见 [skills/cat5cli/SKILL.md](../skills/cat5cli/SKILL.md)。

最新验证：488项Python、8项安装器测试通过；本机npx安装、独立Skill安装和正式EXE业务实测通过，尚未公开发布。详见[cat5cli发布准备验收](superpowers/benchmarks/2026-11-05-cat5cli-release-preparation.md)及[发布流程](CAT5CLI_RELEASE.md)。

实现工作区：`feat/catkit-cli`（独立 worktree）；迁移基线：`1523f65`。
目标：原 21 个 AI CATIA 工具等价迁移到 CLI，GUI 不再内置 AI。

## BOM 接口更新（2026-10-26）

用户已明确将BOM采集/导出切换为工作台统一能力，不保留旧行为；默认hierarchical，增加bom edit，输入文档复用document命令。最终回归465项Python、6项Node通过；新版EXE已安装并通过12命令BOM专项实机验证。其余旧记录中的BOM参数是历史用法，不应继续用于当前版本。

详细证据：[BOM CLI 验收](superpowers/benchmarks/2026-10-26-bom-cli-acceptance.md)。

## 命令补全验收（基线 e6ffbfa 之后）

### Windows Job 兼容修复（2026-10-26）

- 最新回归：423项Python / OK / 47.345s，6项Node安装器测试通过，无子进程解码线程异常。
- 正式EXE 68,406,784字节，已覆盖runtime；当前普通权限、受限Job宿主下 `catkit status --diagnose` 返回connected。
- Windows标准任务调度器回退通过真实前台退出存活、一次性执行、任务清理验证；不依赖OpenCode、PowerShell7、本机用户名或硬编码安装目录。
- 正式EXE的16步实机闭环与脚本SaveAs弹窗接续通过；临时夹具已清理，BOM协同未改。详见 `superpowers/benchmarks/2026-10-26-com-bom-workbench-research.md`。

- 新增 15 个操作：document current/info/activate/save-as/close，part info/tree/update/mass，product info/tree/mass merge/mass remeasure/export step，drawing info；保留原 21 个工具函数及契约。
- 实机脚本：`catia_copilot/tests/catia_manual/catkit_completion_smoke.py`。16 次源码 CLI 调用通过，覆盖非活动文档查询/更新/质量、另存/激活/关闭、装配树与子树、整装配 STEP、真实生成视图的源文档查询。
- 质量证据：10×20×5 mm 实体质量 0.001 kg、重心 [5,10,2.5] mm，Ixx = mass×(20²+5²)/12 ≈ 0.0354167 kg·mm²，与实机一致。
- COM 依据：已安装 pycatia 的 Document Activate/SaveAs/Close/Saved/ReadOnly、Part/Body/HybridBody/OrderedGeometricalSet 集合、DrawingView IsGenerative 与 GenerativeBehavior.Document 包装器，以及实机结果。
- 实机发现 Body.HybridBodies 可为 None，按空集合处理；绘图 NextLink 末尾抛 E_FAIL，不将异常当作枚举成功，改用文档明确的 GenerativeBehavior.Document 读取每视图生成源。
- 复用 mass_props_collect 的 MPD 文件读写/合并与保持测量重读；单位以实际模块顶部定义及 Analyze 实机结果为准（kg/mm/kg·mm²），不沿用局部旧注释中的 SI 误称。
- 审查发现并回归修复：装配子节点异常不再静默遗漏；图纸源对象无法定位文档时返回 errors；不完整查询返回非零退出码。
- `part tree` 的范围为 Bodies/Shapes/Sketches/HybridBodies/HybridShapes/OrderedGeometricalSets，不展开特征内部参数、布尔吸收体；不能将其称为任意 CATIA 模型树的完全复刻。
- 正向 MPD 合并有真实压缩文件往返测试；保持测量重读缺有效测量夹具，未完成正向实机验收。Close/SaveAs 取消路径有离线测试，本轮未模拟用户真实取消。
- 本轮夹具自动清理、仅关闭自建文档并恢复原活动文档；不新建独立测试会话。
- 全量回归：411 tests / OK / 40.932s；Node 安装器 3 tests passed；git diff --check 通过。新版 EXE 已构建并覆盖 `%LOCALAPPDATA%/Catkit/runtime/catkit.exe`，正式安装的 15 个新增命令 schema 逐项验证通过；dist 仅保留最新 ZIP 和 SHA256SUMS，构建中间目录及临时版本文件已清理。下文保留旧迁移阶段历史证据，不代表最新产物。

## EXE 体积对照实验

- 基准：catkit.exe 101,221,888 字节（96.53 MiB）；其中 .text 73,037,312 字节、.rdata 27,080,192 字节。ZIP 53,356,531 字节；完整 runtime 168,436,905 字节。
- 单变量：移除 `--include-package=pycatia`，其余包含/排除配置不变；额外生成 compilation-report.xml 用于分析，不使用 onefile。
- 结果：EXE 70,869,504 字节（67.59 MiB），减少 30,352,384 字节（29.99%）；ZIP 47,757,298 字节，完整 runtime 138,084,521 字节。链接输入文件数从 1844 降至 1067。
- 编译报告：剩余编译模块/包中 pycatia 490、openpyxl 176、numpy 113、PIL 75、comtypes 63、win32com 36。该数量不是各包的字节贡献，不能按数量推算大小。大型报告提取摘要后删除，日常构建不额外保留报告。
- 新正式 EXE 的 16 步实机闭环全部通过（`catkit_completion_smoke.py --exe`），包含脚本建模、指定文档质量/更新、文档操作、整装配 STEP 与图纸生成源查询。
- `catkit_dialog_smoke.py --exe` 实测脚本 SaveAs 到不存在目录：返回 waiting_for_dialog，经 dialog respond 确认后得到原保存失败结果，执行计数为 1；验证裁剪后 pywinauto 扫描、COM 动态依赖和原任务接续仍可用。
- 两组测试夹具和独立任务目录均已清理，原活动文档恢复。脚本能力以 ctx 契约为准，不承诺包含所有可供任意 import 的第三方模块；未补测的旧图纸场景仍按前述缺口记录。
- 第二轮仅取消 win32com 整包包含：EXE 69,004,800 字节（65.81 MiB），较上一轮减少 1,864,704 字节；ZIP 47,178,008 字节；编译 win32com 模块从 36 减为 10，保留 client/gencache/genpy/makepy 等实际调用链。正式 EXE 的16步实机闭环及脚本SaveAs弹窗接续均通过。
- 第三轮将 comtypes 整包包含替换为 persist/typeinfo/automation/stream 四个动态命名空间和 comtypes.gen 生成包装子包，其他模块沿静态导入收集。EXE 68,346,368 字节（65.18 MiB），本轮减少 658,432 字节；ZIP 46,954,353 字节，runtime 135,502,505 字节；编译 comtypes 模块从 63 降为 50。正式 EXE 的16步实机闭环与脚本SaveAs弹窗接续再次通过，脚本执行计数为1。
- 三轮累计：EXE 从101,221,888降至68,346,368字节，减少32.48%；取消pycatia/win32com/comtypes顶层整包强制包含，仅保留comtypes.gen生成包装小子包。临时报告及实机夹具清理后，正式目录保留最新版本，dist只保留ZIP与SHA256SUMS。

## Skill 与安装入口维护

- 仓库 Skill 参考已加载 OpenCode Skill 的索引式组织及飞书官方 larksuite/cli 的 lark-shared、lark-drive，采用短入口、场景路由、按需 references；未移植飞书的身份、路径限制、退出码或确认门禁。
- 基线使用测试发现“已安装但不在 PATH 且没有 Python”时无法从旧 Skill 找到 EXE；改写后独立文档使用测试覆盖入口、脚本路由、弹窗接续、部分结果、busy 和结果字段，均正确回答。4份 Skill 文档链接与 frontmatter 检查通过。
- 新增 `catia_copilot/cli/AGENTS.md` 开发约定草案，区分使用说明与实现约束。
- Node 安装器默认把 runtime 追加到用户 PATH，保留原始变量表达式并按 Windows 路径语义去重；支持 --no-path 隔离安装。6项安装器测试通过。
- 当前用户 PATH 已加入正式 runtime；原值读写往返及环境变更通知验证通过。使用用户/系统环境重建进程 PATH 后，Get-Command catkit 解析到正式 EXE，catkit --version 返回2.2.0。已有终端/AI宿主需重新打开获取环境。
- 本次修改不涉及 EXE 业务代码，未重跑 Nuitka；仓库内新版 Skill 尚未同步到已安装副本和既有 ZIP，随下次安装包更新分发。

## 旧迁移阶段的离线验证

当时正式回归：2026-10-24，`343 tests / OK / 157.461s`。安装器：`3 tests passed`。

最终 Nuitka ZIP 已构建并在隔离临时目录安装；实际 EXE 的版本、帮助、21工具 schema 和错误退出码通过。
ZIP 大小 50,009,606 字节，SHA-256 `90f415a033f52626972218c6bebbe5c6a71e1e94b18e209821129568e9f981c9`。

- 原工具函数 AST 校验值逐项比对，参数默认值和业务函数体保持一致。
- 全命令分发、JSON/@文件、布尔参数、run 文件输入、schema 路径、错误退出码。
- 独立进程导入工具服务无 PySide6；GUI 仍从纯数据模块重导出原业务值。
- Python 脚本实际加载/执行/运行记录测试使用 fake CATIA，未连接真实 CATIA。
- GUI 非 AI 回归及翻译目录一致性测试。
- npm 启动器的目录安装与 Windows ZIP 解压测试使用临时目录，没有写入用户安装目录。

## 集中 CATIA 实机验收（按用户要求停止）

2026-10-24：20 个实机场景通过，覆盖 17/21 个旧工具；其余未完成，不再继续补测。
STEP 曾被“文档已打开，是否重新打开”提示阻塞，已修复导出复用已打开文档，并实测通过。
图纸创建通过；图纸刷新、两项图纸/零件关联查找、PDF 导出尚未完成。
后续弹窗功能验证查明图纸保存阻塞原因：模板为教育许可证创建，CATIA 报另存为失败；已确认该错误提示，未重试保存或绕过许可。
测试进程已停止；报告曾位于 `%TEMP%/opencode/catkit-live-w04w6bkq/report.json`，测试文件后续已按用户要求清理，本表保留摘要。
当前 ZIP 构建早于本次导出修复；最新修复在源码中，未为此次实测再次执行 Nuitka。

准备：测试用 CATPart、含子装配的 CATProduct、对应 CATDrawing、图纸模板及独立导出目录。
以旧版本工具在相同测试文件上的结果为对照，不把离线通过当作 COM 通过。

| 原工具 | CLI 路径 | 验收内容 | 状态 |
|---|---|---|---|
| check_catia_connection | status | 正常连接（关闭 CATIA 场景未测） | 通过 |
| diagnose_catia_connection | status --diagnose | 诊断字段、退出码 | 通过 |
| open_catia_file | document open | 文件路径及 foreground 行为 | 通过 |
| get_open_documents | document list | 活动与全部已开文档 | 通过 |
| save_catia_document | document save | 指定已开路径 | 通过 |
| get_document_properties | document properties get | 标准与用户属性 | 通过 |
| set_document_properties | document properties set | 写入后属性回读 | 通过 |
| find_dependencies | document dependencies list | 正向找到两个测试零件 | 通过 |
| find_reverse_dependencies | document dependencies reverse | 反向找到测试装配 | 通过 |
| find_part_for_drawing | document find part | 图纸关联零件策略 | 待验 |
| find_drawing_for_part | document find drawing | 零件关联图纸策略 | 待验 |
| collect_bom | bom collect | 明细、汇总与内部字段过滤 | 通过 |
| export_bom_to_excel | bom export | XLSX/CSV、活动文档与路径列表 | 通过 |
| write_bom_to_catia | bom write | pn_data 写回并回读确认 | 通过 |
| collect_mass_props | product mass collect | summary_only：0.132 kg，重心符合夹具体积计算 | 通过 |
| generate_drawing | drawing create | 模板生成图纸与属性同步 | 通过 |
| refresh_drawing | drawing refresh | 当前图纸刷新 | 待验 |
| convert_to_pdf | drawing export pdf | DR_ 前缀、更新、覆盖选项 | 待验 |
| convert_to_step | part export step | 修复重复打开后，零件与产品共2个STEP导出 | 通过 |
| apply_part_template | part template apply | 2个测试零件，keep_open=true | 通过 |
| run_modeling_script | run | 创建保存两个实体，特征数与重心 verification 通过 | 通过 |

## 发布验收

- Nuitka 产物的 help/version/schema 为离线冒烟；连接和业务操作仍按上表测试。
- 在没有系统 Python、已安装并许可 CATIA 的 Windows 环境验证 standalone 包。
- 外部 AI 加载 `skills/catkit/SKILL.md`，完成 BOM 导出与建模脚本各一例。
- GUI 检查 BOM、质量特性、PLM、文档和图纸功能，以及不存在 AI Tab/配置入口。
- npm 包名和发布版本需确认；当前包为 private，未执行 publish/release 上传。
