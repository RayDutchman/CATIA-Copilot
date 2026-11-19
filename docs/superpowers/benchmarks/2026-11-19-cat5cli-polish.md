# cat5cli 通用可靠性验收

## 范围

本轮回到通用 CATIA V5 CLI：安装启动、任务接续、BOM 与引用工具。项目改号工作已停止，不新增专用迁移框架。

## 修复与证据

- 类型库包装在目标机缓存生成，不捆绑构建机的 `comtypes.gen.*`；保留 comtypes 原生版本及时间戳校验。
- help/version/schema 不加载 COM。缓存目录锁使用文件系统身份，避免长路径别名绕过同一把锁。
- 任务扫描窗口成功或失败后重新读取终态，避免扫描期间业务完成却继续报告等待。两项回归先失败后通过；保留用户显式响应已有弹窗的既有语义。
- 安装器显式导入 Windows PowerShell `$PSHOME` 内置 Archive 模块，避免继承 PowerShell 7 模块路径后加载不兼容版本。实际 ZIP 安装测试先失败后通过。
- Python 全量 **507 项通过，47.137s**；Node 安装器 **8 项通过**。

## beta.2 实际 EXE

- 重新构建完成，产物 `dist/cat5cli/cat5cli-0.1.0-beta.2-windows-x64.zip`。
- 元数据命令不生成 COM 缓存；空缓存连接 CATIA 为 connected。
- 将测试缓存包装的时间戳设为过期值后，再次连接为 connected，包装重新生成；没有关闭原生校验。
- BOM 专项 12 次命令符合预期，包含只读字段的预期失败、中文属性、CSV/XLSX 和覆盖策略。
- 实例改名专项：3 项成功、1 项预期拒绝；共享定义联动、根层实例隔离及旧值读回通过。
- 组件替换与图纸生成源：共享子装配断链修复、根节点拒绝、Part/Product 源切换、显式保存与冷重开读回通过。
- 原生保存错误弹窗可接续原任务；操作最终失败被正确报告，脚本计数为 **1**。

测试使用自建夹具。启动前当前文档 Product19.CATProduct，已有 42 个文档；现有用户文档不作为写入目标。

## 本机交付

- 固定 `%LOCALAPPDATA%/Cat5cli/runtime` 与 OpenCode Skill 已更新为 beta.2，保留既有任务记录，用户 PATH 未重复追加。
- 覆盖前确认已安装 Skill 与原 runtime 配套副本一致，没有本地差异。
- 安装后 67 个 runtime 文件与 ZIP 逐项哈希一致，6 个 Skill Markdown 文件与源码一致。
- ZIP SHA256：`383d3081717ecd9176a81f6931a1a633ccdc2fbe802ba78d0f348a290ce7fed0`。
- 安装后 `--version` 为 `0.1.0-beta.2`，`status --diagnose` 为 connected；活动文档仍为 Product19.CATProduct，文档数仍为 42。
- 本轮临时候选 runtime 已清理，保留验收报告。范围内 `git diff --check` 通过。

## 过程问题

- 首次构建 shell 两分钟超时，子进程残留；对象文件停在 12:28:41，13:35 已无编译器子进程。不能用进程存在判断进展。未取得阻塞调用栈，不把输出管道阻塞当作已证实根因。
- 核验并结束本轮残留构建进程后，保留缓存、使用无超时后台执行且日志直接落盘，13:37 构建成功。
- 初次连接为 broken；用户改为普通权限运行 CATIA 后，源码和候选 EXE 均 connected。
- 实例脚本原来只接受 --installed，编排误传 --exe；补充候选路径选项后单独验收，没有重复已成功的 BOM。
- 独立审查会话因 `Reconnect OpenCode Console to continue` 未运行，不能声称独立审查通过。

## 边界

这些是本机证据，不代替另一台无 Python Windows 的验收。保留测量、复杂关联尺寸、所有 GUI 功能没有在本轮全面验收。未自动提交、推送或公开发布；公开 beta.1 与本机候选版本应明确区分。

详细临时证据位于 `.superpowers/portable-comtypes-exe-check.json`、`.superpowers/cat5cli-build-20261119-retry.log`、`.superpowers/cat5cli-live-20261119.log`、`.superpowers/cat5cli-reference-live-20261119.log`。
