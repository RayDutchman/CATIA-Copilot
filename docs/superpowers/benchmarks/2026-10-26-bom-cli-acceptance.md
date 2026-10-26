# BOM CLI 接口验收

## 交付范围

- collect/export 统一使用工作台采集和视图能力；默认 hierarchical，支持 full/summary。未指定新选项不再进入旧采集路径。
- 输入文档由既有 --doc/--active 解析；BOM 不打开、关闭或保存输入文档。
- edit 接收 node/scope/column/value 有序变更，逐项返回结果；共享主数据与实例名分开，成功项不自动回滚或保存。
- export 接收明确的 .xlsx/.csv 输出文件，复用行写出、样式及覆盖处理；不按 GUI 当前筛选或偏好操作。
- 公开命令、schema 和新增服务采用 BOM 业务命名，无版本选择开关；CLI/GUI 独立，没有新增 IPC 或共享撤销。

## 验证结果

- 最终全量 Python：465 tests / OK / 47.012s。Node 安装器：6项通过。git diff --check 通过。
- 新增接口/路由回归覆盖默认层级、旧开关拒绝、非活动/未保存目标、三视图、重复引用、同PN不同文件歧义、逐项失败、PN规则及缓存恢复、自定义列与导出覆盖协议。
- 其余19个旧工具函数的AST对照保持通过；被用户明确替换的两项BOM工具改由新的行为回归验证，历史指纹文件未篡改。
- 源码实机：重复引用零件的full/hierarchical/summary行数分别为3/2/1，汇总数量为2；共享Revision、单实例名、中文自定义属性写回及读取一致；只读Quantity失败后合法后续项继续执行；CSV、skip/replace、既有document保存路径验证通过。
- 正式EXE专项：`catia_copilot/tests/catia_manual/catkit_bom_smoke.py --exe <正式路径>`，12次命令调用通过；其中故意写只读Quantity的一次edit返回退出码1，其他合法项成功，符合预期。
- 专项使用自建未保存装配与嵌入部件，验证三视图、显式非活动文档、宿主字段未被改变、实例名、中文属性、真实CSV/XLSX内容与两种覆盖操作；不调用CAD保存或STEP导出来绕开本轮已有CATIA会话冲突。
- summary沿用既有规则：默认排除装配，include_assemblies=true时包含根装配。之前“根总是排除”的源码注释不准确，已纠正注释，未改算法。

## 审查结论与实机裁决

审查根据旧注释提出“嵌入部件的ReferenceProduct可能指向宿主”。本机CATIA AddNewProduct创建的嵌入部件中，实测reference.PartNumber与部件一致，不是宿主；标准属性和自定义属性的CLI编辑均通过，宿主Revision/备注保持不变。直接从实例访问UserRefProperties会失败，访问其ReferenceProduct的属性集合可用。

因此未按该静态推论改写既有write_cell目标选择；补充了嵌入部件实机验证，并纠正绝对化注释。此证据覆盖本轮创建的组件类型，不扩展宣称所有历史数据形态已经实测。

## 测试过程中的其他处理

- CATIA曾对本轮自建CAD文件的SaveAs/STEP弹出会话数据提示；确认后查询原任务，未重新执行原业务。最后STEP任务返回成功，文件实际存在（13,244字节）。
- 不点击无关的Penetration Management Tools浮动窗口；确认提示后的短暂状态通过task wait重新读取原任务结果。
- 手工验收脚本改为：待处理任务保留目录/文档；任务结束后才清理，避免删掉worker仍需使用的记录。
- document save-as增加输出文件存在性检查，防止仅FullName改变时误报文件已生成；有失败→通过回归，仍复用该命令而非在BOM重写保存逻辑。
- 自建弹窗、任务、文档与临时文件已清理；用户原有文档未被保存或关闭。

## 分发状态

- 正式EXE已覆盖 `%LOCALAPPDATA%\Catkit\runtime\catkit.exe`，68,835,840字节（约65.65MiB）。dist仅保留最新ZIP与SHA256SUMS。
- OpenCode专用安装目录的Skill已同步主入口、工具路由和BOM专页，3个文件与仓库源内容哈希一致；旧参数不再作为BOM采集/导出用法。
- 新实现未提交、未push或发布。GUI行为没有迁移到CLI或由CLI遥控。
