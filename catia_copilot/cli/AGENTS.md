# Cat5cli CLI 开发约定

适用于本目录及其子目录。用户调用指引维护在 `skills/cat5cli/`；本文件约束 CLI 实现与验证，不记录开发进度。

## 先看哪里

| 修改内容 | 入口及复用资产 |
|---|---|
| 命令、参数、help/schema、退出码 | `app.py`；`../catia/tool_schema.py`、`inspection_schema.py` |
| CLI 入口与隐藏 worker | `__main__.py`、`tasks.py` |
| COM 初始化、业务分发、文件覆盖策略 | `backend.py` |
| 业务操作 | `../catia/tool_service.py`、`inspection_service.py`；优先复用 document、conversion、assembly_reader、mass_props_collect |
| 文档身份与显式目标 | `../catia/document_scope.py` |
| 脚本能力 | `../catia/modeling.py`、`modeling_contract.py` |
| 弹窗观察/响应与连接 | `../catia/dialogs.py`、`connection.py` |
| 目标与验收 | `../../docs/superpowers/specs/2026-10-23-catia-cli-skills-design.md`、`../../docs/CATKIT_ACCEPTANCE.md` |

## 实现边界

- 在 Windows 原生环境运行 CATIA/COM、测试和构建；CLI 业务导入链保持 Qt-free。
- 参数解析和 schema 查询不连接 CATIA；业务由执行适配层调用，日志不得污染 stdout JSON。
- CLI 自身生成的用户可见输出统一使用英语，包括 help/schema 描述、提示、进度、日志、异常消息（ValueError 等）及返回结果中的错误文本；CLI 不引入 i18n 或语言切换，代码注释允许中文。
- CLI 调用的共用业务层消息也直接使用英语；允许 GUI 显示这些共用消息时为英语，不改 GUI 自身菜单/按钮，不增加运行时翻译映射层。
- 用户数据保持原文，包括文档名、路径、自定义属性名和值；CATIA/系统返回的原始弹窗及诊断内容保留原文，不冒充 CLI 自身生成的提示，也不为满足英语输出而改写业务数据。
- 新命令同步维护注册、schema、工具映射及必要 Skill 参考。先检查已有业务函数、GUI调用和回归资产，不因换入口重写算法。
- 原工具省略参数时沿用 Python 默认值，保留返回结构；不用统一信封替换旧结果，不新增统一确认、预检或强制 verification。
- BOM collect/export 是用户明确调整的例外：统一工作台采集与视图，默认 hierarchical，不保留旧采集/导出回退。输入只选已打开文档，打开/保存继续使用 document 命令；新增代码和公开契约不带版本标签。
- 建模只走 `run --file` / `build(ctx)`。扩展建模能力时维护 ctx 契约，不增加逐项 Body/feature/sketch CLI。
- 通用文档查询采用已有身份解析；指定非活动文档时不得重新读取 ActiveDocument。名称歧义返回候选，不取首个。
- 新增对象操作的 doc/active 互斥；文件集也不得与显式文档目标静默冲突。旧路径参数保持兼容。
- product STEP 只导出整装配文档/文件集，不支持 `--node`；product tree 的 node 是只读索引路径，会随装配变化失效。
- COM 方法、属性、单位先查项目资产及可信接口证据；不按名称推测。结构读取失败应返回部分结果及 errors，不伪装完整成功。

## 任务生命周期

- COM 操作留在独立 STA worker；前台用 Win32 观察弹窗，不能靠已阻塞的 COM 查询弹窗。
- 保留原子任务记录、worker 身份和独占执行认领；前台退出/等待超时不能终止或重跑原业务。
- waiting_for_dialog/running/busy 必须保留 task_id；扫描失败不是业务失败，busy 的新请求不能被当作已执行。
- 响应前核对 PID、窗口快照和控件归属；多 CATIA 实例无法定位时不猜。继续复用 connection.py，不另造连接/自动启动层。
- 不通过吞异常、静默降级 worker 脱离方式或自动点击所有确认来绕过失败。
- 当前进程在受限 Windows Job 中且 breakaway 被拒绝时，可使用当前交互用户的低权限一次性计划任务；必须保留启动握手、待启动状态、幂等和清理，不能退化为仍依附前台 Job 的普通子进程。

## 验证与交付

- 新增分支先写行为回归，重点覆盖显式非活动目标、歧义、部分读取、重复执行与失败接续；未调整工具的迁移 AST 校验仍须通过，BOM采集/导出以新的行为回归为准，历史基线不篡改。
- 英文化允许旧工具的消息字面量变化：按明确 AST 路径和原/英文文案白名单还原后继续核验历史指纹，不整体忽略字符串，不放宽业务逻辑或用户数据字段的对照。
- CLI 自定义属性不硬编码公司预设名单或枚举值；公司规范交由 Skill 指导，GUI/模板/PLM 自身预设不限制 CLI。document properties set 已获准取消预设校验，改由属性写入行为回归覆盖；其余18项未调整工具仍保留精确 AST 对照。
- 日常调试用 `python -m catia_copilot.cli`，不为每次修改重跑 Nuitka。
- 项目根目录执行：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -q
node --test packages/cat5cli/test/installer.cjs
```

- 经授权的真实验收仅操作自建夹具；复用 `tests/catia_manual/catkit_completion_smoke.py` 和 `catkit_dialog_smoke.py`（位于 catia_copilot 下），`--exe` 可验证正式二进制。离线 mock 通过不等于 CATIA 实机通过。
- 最后统一运行 `python scripts/build_cat5cli.py`（项目根目录），支持 `--no-install`，覆盖 `%LOCALAPPDATA%/Cat5cli/runtime` 或 CAT5CLI_HOME；不积累 UUID 安装副本，构建中间文件和测试夹具用完清理。
- 安装器默认将 runtime 加入用户 PATH，保留已有项且不重复添加；隔离安装测试必须使用 --no-path，避免将临时目录写入用户环境。
- Nuitka 不整包包含 pycatia/win32com/comtypes；必要动态 comtypes 模块及生成包装由构建脚本精确保留。任何裁剪必须验证实际 EXE 建模与弹窗接续。
- 目标设计保持目标用途；过程和证据写计划/验收文档。未经用户授权不 commit、push 或发布。
