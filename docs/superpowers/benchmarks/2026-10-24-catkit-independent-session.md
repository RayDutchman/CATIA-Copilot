# Catkit 全新会话 CLI 验收

- 用户要求：新建独立 OpenCode 会话，只按 SKILL.md 测试 CLI，不直接调用 Python。
- 会话：`ses_e94b4926affeUoWvhsRMptOKia`，模型 `openai/gpt-6-luna`，agent `build`。
- 目录：`C:/Users/Chen Weibo/AppData/Local/Temp/opencode/catkit-independent-test-20261024-082427`。
- Skill：已复制到测试目录 `.opencode/skills/catkit/`；不提供旧会话实现过程。
- 初始消息：`msg_16b4c9553001i2EfkeYM9RmfD4`，只读Skill并准备清单，等待最新EXE。
- 验收限制：通过 EXE、help/schema 与Skill执行；不读产品源码、不直接 Python/COM、不修改现有用户文件。
- build(ctx) 文件可以由宿主写入，但仅经 `catkit.exe run --file` 执行。
- 结果文件：测试目录 `acceptance.md`；完成后由独立会话主动回传主会话。
- 状态：独立会话已完成二进制验收并主动回传；核心真实闭环通过，发现的问题与未测范围如下。
- 开始测试消息：`msg_16b5748b6001cqHVlZC1bj4gIm`。
- 测试 EXE：`C:/Users/Chen Weibo/AppData/Local/Temp/opencode/catkit-independent-test-20261024-082427/runtime/2.2.0-118663f9-a7a0-4fe9-bcfe-2956c4d348e5/catkit.exe`。

## 构建阻塞与处理

- 首次包含 pywinauto 的 EXE 编译完成，但启动失败：`ModuleNotFoundError: comtypes.stream`。
- 核实 comtypes 的 `_get_known_namespaces()` 使用 `importlib.import_module` 动态导入 persist/typeinfo/automation/stream；仅自动跟踪静态 import 不足。
- 构建脚本已加入 `--include-package=comtypes`，构建前导入 pywinauto 生成类型库包装，排除 comtypes.test；重建完成，help/version/schema 和未知路径退出码验证通过。
- 未把启动失败的 EXE 或旧 ZIP 交给测试会话，也未使用 Python 绕过二进制验收。

测试授权：仅通过 CLI 创建及操作当前测试目录内的新夹具，至少验证一个真实闭环；原用户文档保持不变。未执行业务状态/产物由测试会话独立记录，主会话不并发操作 CATIA。

## 独立测试结果

- help/version/schema、连接状态、文档列表、属性 get/set 通过。
- 新建并保存 `ACPT_20261024_01.CATPart`（58,466 字节），特征 `凸台.1`，质量 0.014112 kg、重心 [21,14,6] mm。
- 属性写入并回读 Revision=A、Description、设计状态=草稿；STEP 导出 `CLI_ACPT_20261024_01.stp`（9,075 字节），含 AP242、MANIFOLD_SOLID_BREP、CLOSED_SHELL。
- 首次 SaveAs 使用正斜杠路径报“文件名无效”；会话没有重建零件，而是通过任务接续取回失败结果，再用反斜杠保存已有零件成功。
- 首次弹窗枚举发生 invalid window handle，前台返回了错误却未给 task_id；会话通过 busy/task status 找回原任务并响应，避免重复执行。
- 主会话已补源码回归：扫描瞬时异常不再丢失任务，返回 running/task_id/monitor_message 并可继续等待；21项定向测试通过。此修复已包含在验收后重新构建的正式 EXE 中。
- 修复后全量回归：2026-10-24，369 tests / OK / 39.787s。
- Skill 已补 `ctx.save_part` 原生反斜杠路径说明；内联 JSON 引号问题由 @UTF-8文件方式解决。
- BOM、PDF、模板、图纸刷新等未在新会话测试：缺本目录内合适样本/模板，不计为通过。
- 全程仅 EXE + Skill/help/schema；没有直接 Python/COM/Win32 或产品源码读取。

Windows 全局 AGENTS.md 已按 WSL 对应章节补齐建会话和跨会话传话方法；WSL 文件未修改。

## 验收后清理

- 用户授权清理后，测试会话已删除并 GET 验证 404。上述测试 EXE、原始报告和测试产物均已删除，本文件保留验收摘要，历史路径不再有效。
- Temp/opencode 下的 catkit-live-w04w6bkq、catkit-shell-dialog-5mapk776 已删除；独立测试目录内容已清空，空目录因 Windows 进程占用暂未删除。
- 正式安装统一为 `%LOCALAPPDATA%/Catkit/runtime/catkit.exe`；重复构建和安装覆盖此目录，不保留 UUID 安装副本。
- 最新构建、安装及正式路径 `--version` 验证通过（2.2.0）；安装回归 3 项通过。构建中间目录已清除，`dist/catkit` 仅保留最新 ZIP 和 SHA256SUMS。
