# CLI/GUI COM 共存与 BOM 工作台研究

## 本次只读诊断

- 用户反馈：使用 CLI/Skill 后，原 GUI 无法连接 CATIA。未执行终止进程、重启 CATIA或修改用户文档。
- 检查时无 catkit.exe worker。最后任务 f5a958601adc432abce11892c93d7928 为 collect_bom，已有 result.json，结果字段 row_count/rows，无 error；worker PID 31264 已不存在。
- 任务记录的 CATIA PID 为12436，当前为27828，说明任务后 CATIA 实例已更换。不能将旧任务的 PID 当作当前实例。
- 当前 CATIA 主窗口 enabled=true，另有 Penetration Management Tools 浮动窗口；不能仅凭浮动窗口判断存在阻塞弹窗。
- 独立只读 GetActiveObject('CATIA.Application') 返回 HRESULT -2147221021（0x800401E3，MK_E_UNAVAILABLE）。原 GUI checkout 的 utils.check_catia_connection() 返回 broken。
- 诊断进程与 CATIA 同属当前用户，但诊断进程 elevated=true、CATIA elevated=false。原 GUI Python进程在核对token前已退出，故未确认GUI的权限级别。
- 当前证据排除“仍有CLI worker持续持有COM”这一现时解释；不能证明过去从未阻塞，也尚不能将本次连接失败定性为权限差异。

## 生命周期代码依据

- cli/tasks.py：worker 独立进程、worker.claim独占执行、原子结果记录；前台30秒等待是观察期限，不取消后台任务。
- cli/backend.py：业务前CoInitialize，finally中CoUninitialize；正常完成/异常返回后worker退出。持有COM引用不等于独占COM接口，但CATIA忙碌或模态交互可阻塞其他客户端调用。
- CLI任务mutex仅协调CLI任务，不与GUI共用，也不是CATIA全局COM锁。
- GUI和CLI的connection.py复用同类连接策略；原GUI的utils.check_catia_connection捕获具体COM错误后归类为broken，状态本身不足以定位原因。
- 下一步应在故障GUI实际运行时核对GUI/CATIA权限、具体HRESULT与实例，而不是盲杀worker、清空COM缓存或重启CATIA。

## BOM 工作台资产

- 真正入口：ui/catia_embed.py 的 MENU_BOM_EDIT → ui/bom_edit_dialog_v3.py / BomEditDialog。它与“从产品导出BOM”是不同功能。
- catia/bom_collect_v3.py 的 collect_bom_part_masters 返回 root_pm_key、part_masters、inst_key_to_info；复用 iter_full_rows、iter_hierarchical_rows、flatten_bom_to_summary 生成完整/层级/汇总视图。
- 主数据与实例分离：共享零件属性、文件信息与子实例挂在主数据；实例名/实例描述属于具体实例，重复引用不得错误合并写回。
- get_part_master_attr/set_part_master_attr/rename_part_master 承载字段访问和PN改名；catia/bom_write.py 的 write_cell 承载既有单元格写回语义。
- GUI含多行填值、序列填充、撤销重做、筛选、列配置、选中行刷新和当前视图导出；其中控件、选区、弹窗和显示联动不能直接搬到CLI。
- QSettings 保存模式/列等偏好，不保存完整BOM工作对象。数据中含COM引用，实例键含进程内id；不能直接JSON化并跨CLI进程复用。

## 当前 CLI 差距

- bom collect/export/write 对应旧采集、表格导出、按PN批量写回，不是V3工作台的完整迁移。
- 缺少V3三种视图入口、主数据与实例分开的编辑契约、基于当前编辑结果的导出。
- 未查到工作台已实现独立离线BOM文件、通用差异/同步/模板流程，不应把这些臆造为现成资产。

## 建议集成方向（待设计确认，不是现有命令）

1. 优先接通V3采集与三种视图，保留原bom命令兼容；将Qt无关字段编辑逻辑最小提取，复用现有字段规则。
2. 编辑契约明确主数据目标与实例目标；按PN旧接口不替代实例级写回。批量填充由AI生成明确变更列表，CLI执行已有业务语义。
3. 每次CATIA操作保持短生命周期worker；不为复刻GUI工作台引入常驻COM服务。
4. 如果需要一次采集后连续分析/编辑，引入可选、可序列化快照及变更集，移除COM指针，单独设计跨进程身份；不能用id(product)持久定位。写回时重新解析当前目标，歧义或失效明确报错，不额外加统一审批门禁。
5. Skill增加BOM专用场景参考：选择完整/层级/汇总，区分共享属性和实例属性，生成变更列表、写回及保存，导出指定视图；不将BOM工作台简化成三个旧命令的说明。

## 需确认的产品边界

- 首期以V3采集/视图/属性及实例编辑/导出为核心，还是也包含文件移动改名等外围操作？
- 是否需要可持久化离线快照？若需要，需明确源装配变化后的冲突处理；当前GUI没有可直接复用的离线工作对象协议。
- GUI即时写回可以保持；CLI分次调用中的编辑/写回边界需要明确，不把GUI交互细节当成必需命令。

## 用户确认后的启动/PATH/编码修复

- BOM协同仍暂缓；本次范围为启动链、PATH、局部输出编码及worker Job兼容。
- server目录从4文件简化为3文件：原计划任务 → VBS → PowerShell；删除CMD。VBS改为隐藏等待并传回退出码，避免异步启动导致计划任务误报成功、不能重试。
- PS1明确使用UTF-8 BOM以兼容Windows PowerShell5.1，不修改系统代码页；用户目录与日志目录取环境变量，错误不打印凭据。加载Machine/User PATH后检查可用性；重复启动只保留同路径OpenCode监听，不抢占其他进程。
- 独立真实检查：Elevated=false，MachinePathLength=756，System32Available=true，Catkit路径正确；VBS检查分支返回0，错误参数返回1；已有40961服务PID保持32396，未重启。
- 对当前版本脚本的独立副本比较：无BOM、BOM、ASCII注释三种均能读到Machine PATH；因此不能把正在运行服务的旧PATH简单归咎于当前脚本注释。最终切换后仍需核对新服务环境。
- 原CMD错误字节按GBK解码正常、按UTF-8解码乱码；仅对子进程chcp65001未修复该错误输出。增加本机OpenCode工具 `~/.config/opencode/tools/run-windows-command.py`，逐输出流按显式编码或auto转UTF-8，保留返回码；不改系统、父进程或旧软件环境。
- 输出桥3项验证通过：UTF-8/GBK/UTF-16解码、真实CMD中文错误及退出码1、UTF-8程序输出不被二次解码。旧命令需经该桥调用，未更改OpenCode工具内部解码器。
- worker新增通用WindowsJob回退：仅当前进程在Job中且breakaway返回WinError5时，使用当前交互用户的低权限一次性计划任务；不依赖OpenCode、PowerShell7或本机用户名/固定路径。
- 源码/EXE隐藏入口显式传递任务根目录及日志；调度metadata在Run前落盘；worker.claim后写PID/创建时间握手，正常结束与状态查询清理精确任务名。启动状态不确定不重复派发；已尝试运行、无存活实例且返回失败时明确launch_failed，避免永久busy。
- 真实纯Win32夹具通过：start进程退出后worker存活，另一进程respond接回结果，calls==1，临时注册任务删除。测试失败路径增加夹具释放，避免断言异常遗留弹窗。
- 最终源码回归423 tests / OK / 47.345s，Node安装器6项通过，git diff --check通过；已修正测试中UTF-8子进程输出被默认GBK读取的问题，本轮无readerthread解码异常。
- 新版EXE已构建并覆盖正式runtime，68,406,784字节（约65.24MiB）。当前受限Job宿主直接执行 `catkit status --diagnose` 返回connected/is_elevated=false；launch.json证实使用Scheduler回退，完成后注册任务已删除。
- 正式EXE的16步CATIA实机闭环全部通过，SaveAs错误弹窗通过原task_id响应并返回原失败结果，script_calls=1；测试夹具/临时任务清理，dist仅保留ZIP与SHA256SUMS。
- 启动链文件已改好但当前server未被agent重启；最终服务切换仍由用户手动执行，之后核对新server的PATH与工具shell。BOM协同继续暂缓，本轮产品改动未提交。

## OpenCode shell 核验边界

- 官方V2配置文档 https://opencode.ai/v2/docs/config/ 说明 shell 同时作用于terminal与shell tools；文档未证明当前两者的默认发现结果或缓存必然一致。
- 用户在内置终端实测PowerShell7.6.9；agent工具本轮实际回显 `C:\WINDOWS\system32\cmd.exe /c "echo %CMDCMDLINE%"`，PowerShell变量表达式按字面输出。两者当前运行态确实不同，原因未作无依据定性。
- 安装新版后，独立探测可运行WindowsApps执行别名并得到7.6.9，但不能将该别名直接填入shell配置：此前OpenCode stat该路径报EACCES。配置项保持注释，不再修改。
- 启动server使用哪个解释器与工具选择shell分开处理；CLI不依赖宿主使用CMD还是PowerShell。运行中server未被agent重启。
