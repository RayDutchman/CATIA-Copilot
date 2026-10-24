# CATIA 弹窗返回与原任务接续

用户已确认直接实现完整路径，不接 Synthetic，不要求调用者先启动后台监控。

## 执行契约
- 普通业务命令自动由独立 Windows worker 执行，stdout/stderr 重定向到任务目录，避免 shell 管道被子进程占住。
- 前台等待完成或 CATIA 弹窗：完成时原样返回工具结果；遇弹窗返回 waiting_for_dialog、task_id、控件快照。
- dialog respond 处理原任务的弹窗并继续等待原任务，不重跑业务命令。
- task status/task wait 用于查询和收取长任务；30 秒仍未结束且无弹窗时返回 running，worker 不被取消。
- 以 pywinauto Win32 读取控件、设置输入；按钮异步消息点击，不依赖鼠标位置或前台焦点。
- 弹窗响应定位到任务记录的 CATIA PID/窗口/控件，不推断“是/否”的业务决定。
- 不自动重试业务，不终止正在进行的 COM 调用；worker 异常退出报告失联状态。
- 任务文件保存在本机用户目录；源代码运行和 standalone 使用同一隐藏 worker 入口。

## 实施清单
- [x] 弹窗适配器与假对象测试。
- [x] 原子任务记录、Windows 独立 worker 生命周期、前台等待与接续测试。
- [x] CLI dialog respond/task status/task wait 和 schema/help。
- [x] 独立进程模拟弹窗验证：前台返回后 worker 存活、响应后仅执行一次并取回原结果。
- [x] CATIA 只读探查；需要实际点击时只操作本次可控测试弹窗。
- [x] 回归与独立审查；更新 Skill。源码调试为主，不为每轮重跑 Nuitka。

依赖：用户批准 pywinauto Win32；安装 pywinauto 0.6.9，与已有 pywin32 共用。
进度：源码功能、审查修复及完整回归完成；不提交、不发布，不修改主仓。

## 证据与实现取舍

- 33 项定向测试通过；Win32 标准消息框由独立测试进程产生，不操作 CATIA 零件。
- OpenCode 两次独立前台 shell：首次返回 waiting_for_dialog，第二次源码 CLI dialog respond 返回 calls=1，证明没有重执行。
- worker 以 BREAKAWAY_FROM_JOB、DETACHED_PROCESS 创建，文件接收日志；无法脱离宿主进程组时明确报启动失败，不降级后冒称可接续。
- 用户目录内的任务启动认领串行化：已有未完成任务时返回 busy、原 task_id、executed=false，不启动第二条业务操作争抢同一弹窗。
- 操作 worker 继续使用既有 connection.py；Win32 监测不通过 COM 连 CATIA、不另写启动逻辑。
- 实际读到 CATIA 遗留“另存为”错误：模板由教育许可证创建，保存失败；经 dialog respond 确认提示，未重试保存，随后扫描为0个弹窗。
- 多 CATIA 实例时不猜监测对象，返回 running 与 multiple_catia_instances；当前单实例路径已验证。
- 新增依赖 pywinauto 0.6.9；当前仅源码验证，未重新执行 Nuitka，新功能不在旧 ZIP 中。
- 全量测试曾发现关闭弹窗期间的空控件被误判为新弹窗；补充窗口销毁/空快照回归并修复。
- 最新全量验证：2026-10-24，367 tests / OK / 40.492s。
- `-y/--yes` 仅提出建议，尚未实施；默认仍由调用者选择弹窗响应。
