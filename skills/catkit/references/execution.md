# 执行、结果与任务接续

## Windows 权限与连接

- 默认让 CATIA、CATIA-Copilot GUI 和 CLI 宿主均以同一用户的普通权限运行，并处于同一交互会话；不要把“管理员运行”当成通用修复。
- `CNEXT.exe` 存在但 COM 返回对象不可用/`broken` 时，先检查调用进程与 CATIA 的实际权限和会话，不仅看快捷方式或计划任务的配置。
- OpenCode shell 继承后台 server 的权限和环境。只重开网页、前端或终端标签页，不保证改变后台 token 或刷新 PATH。
- 普通权限计划任务不能自动替换端口上已有的管理员服务。需要先修正持久启动链路，再由用户手动切换旧后台；不要终止承载当前会话的 server。
- 没有残留 worker、原任务已完成时，不把连接失败解释为 CLI 仍独占 COM。先用 `status --diagnose` 和实际进程信息定位，不重复执行 BOM/建模操作试连接，也不先关闭有未保存内容的 CATIA。
- worker 启动拒绝访问也可能是宿主 Windows Job 的限制，不等同于 CATIA 权限错误；不要直接通过提权绕过。支持的版本会用当前交互用户的一次性计划任务接续启动，仍按原 task_id 查询。

## 输入

- 文件参数使用 Windows 路径。列表/对象接受 JSON 或 `@UTF-8文件`；PowerShell 优先用文件，避免嵌套引号被吞。
- 参数及默认目标以各命令 schema 为准；没有全局统一的 `--doc`、`--data`、`--yes` 或 `--dry-run`。
- 导出覆盖通过已有 `--overwrite error|skip|replace` 表达，不把它当作所有 CATIA 弹窗的自动确认。

## 结果判读

stdout 是操作自己的 JSON，stderr 是日志；help/version 是文本。没有统一 `ok/data` 信封，也没有顶层 `code == 0` 成功约定。

| 返回 | 应如何处理 |
|---|---|
| `error`、`success:false`、非零失败数量 | 报告失败及已知部分修改；按错误处理 |
| `complete:false` 且有 tree/其他数据 | 仅为部分查询结果，说明 errors 与缺失范围 |
| `status:"waiting_for_dialog"` | 原 worker 正在等待；按下节响应 |
| `status:"running"` | 原任务未完成；用 task wait 接续 |
| `status:"busy"`、`executed:false` | 本次请求未执行；先查询/处理返回的已有任务 |
| `status:"worker_lost"` | worker 已退出且没有结果；不代表 CATIA 修改已撤销 |
| 建模结果中的状态对象 | 分别阅读 execution、model_update、verification；不要当作上述任务状态字符串 |

退出码 0 也可能是等待中，不等于业务完成；1 表示操作失败或查询不完整，2 为参数错误，3 为 CATIA 不可用。不要仅凭退出码或某一个 success 字段忽略详细结果。

## 弹窗接续

普通命令自动管理 worker，无需预先启动监控。脚本建模、SaveAs 和其他业务都走相同接续路径。

```powershell
catkit task status --task <task-id>
catkit task wait --task <task-id> --timeout 30
catkit dialog respond --task <task-id> --button <返回的按钮id>
catkit dialog respond --task <task-id> --control <返回的输入框id> --text "输入内容"
```

- 根据弹窗文字选择控件；不能一律点“确定/是”，也不猜按钮 ID。用户目标不足以决定选项时再询问。
- 输入框填写后，依据返回的新快照提交按钮；按钮响应后可能返回最终结果或下一个弹窗。
- `running` 且 `dialog_monitor:"scan_error"` 只是观察失败：保留 task_id，后续继续查询原任务。
- 窗口/控件已失效时重新获取任务状态，不复用旧快照或改用坐标点击。
- **不重新执行等待中的原命令**。前台退出和等待超时不取消原操作；重复执行可能重复建模或写入。
- 任务结束后再决定下一次业务操作。`busy` 表示原请求未执行，不能把已有任务的结果当成本次请求的结果。
