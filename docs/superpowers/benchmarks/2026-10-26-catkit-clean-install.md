# Catkit 完整移除与安装验收

## 范围及决定

- 用户确认完整移除现有安装后验证 npm/npx、Skill 安装流程。
- 用户要求避免为消除闪窗引入非标准启动层；保留既有控制台 EXE 和计划任务 worker，未改启动代码。
- 本轮没有运行时代码变更，复用已有正式 2.2.0 standalone ZIP 及 SHA256SUMS，没有重新编译相同 EXE。
- 不 commit、push、发布包；不关闭、写入或保存 CATIA 文档。

## 验证过程

- 前置：无 catkit 进程、无未结束请求、无残留 Catkit-* 计划任务。
- 每轮清理：删除整个本机 Catkit 安装目录（含完成任务记录及 current.json）、默认和 OpenCode 两处 Catkit Skill，仅删除用户 PATH 的 Catkit runtime 项。
- 第一轮：npm pack → npm install -g 本地 tgz → 通过全局包入口运行 install，使用本地 ZIP + 显式 SHA-256，Skill 默认安装到 ~/.agents/skills/catkit。
- 第一轮核验：ZIP 全部文件与 runtime 哈希一致、全部 Skill 文件一致、用户 PATH 恰好一项、EXE version/schema 及全局启动器 help 正常。
- npm uninstall -g 移除全局启动器，核对入口不存在；再次完整清理产品目录、Skill 和 PATH。
- 第二轮：npx --yes --package=<本地tgz> catkit install，使用相同 ZIP 和校验值，显式指定 ~/.config/opencode/skills。
- 第二轮核验：runtime/Skill 全文件哈希一致，用户 PATH 恰好一项，version/schema 正常；默认 Skill 目录不存在，避免双份。
- Node 安装器回归：6/6 通过。

## 真实业务核验

原 Product19.CATProduct 已不在打开文档中，第一次显式查询返回文档不存在；未尝试打开或恢复它。

查询 current 得到 Product1.CATProduct。对其执行只读 summary 采集，首次返回欢迎窗口等待状态；以同一 task_id 调用 task wait，最终 success=true、row_count=0（空装配）。没有重复提交、点击欢迎窗口或保存文档。

## 最终状态及边界

- 正式 runtime 和 OpenCode Skill 已恢复，保留计划任务路径可能出现的短暂控制台窗口。
- 最终通过 npx 安装，无全局 @raydutchman/catkit 包；npx 的 npm 缓存属于正常 npm 行为。
- 环境为 Windows、Node 24.16.0、npm 11.13.0；未验证最低 Node 18 版本或另一台无 Python 机器。
- 尚未发布 npm 包/GitHub Release，因此线上下载流程未冒充验收通过；两条入口均用真实本地 tgz 与 ZIP 验证。
- npm 包没有 postinstall；全局 npm 安装后仍需显式执行产品 install。
- 安装器正常使用 ZIP 内 Skill，哈希基准也是 ZIP，不是工作区后续未打包的文档编辑。
- 详细命令、退出码和 stdout/stderr 留在 `.superpowers/install-acceptance/evidence.json`。
