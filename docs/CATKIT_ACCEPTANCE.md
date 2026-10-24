# Catkit 验收记录

实现工作区：`feat/catkit-cli`（独立 worktree）；迁移基线：`1523f65`。
目标：原 21 个 AI CATIA 工具等价迁移到 CLI，GUI 不再内置 AI。

## 已执行的离线验证

最新正式回归：2026-10-24，`343 tests / OK / 157.461s`。安装器：`3 tests passed`。

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
测试进程已停止，CATIA 与测试夹具保持原位。报告：`%TEMP%/opencode/catkit-live-w04w6bkq/report.json`。
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
