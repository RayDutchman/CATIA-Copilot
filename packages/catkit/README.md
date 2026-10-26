# Catkit 安装启动器

这是 Windows CLI 的薄启动器，业务实现位于 Nuitka standalone 包中，不需要系统 Python。
包名尚未发布，当前 package 标记 private；以下使用本地入口，不代表 npm latest 已可用。

```powershell
node packages/catkit/bin/catkit.cjs install --from-zip D:/Downloads/catkit-2.2.0-windows-x64.zip --skills-dir "$env:USERPROFILE/.config/opencode/skills"
node packages/catkit/bin/catkit.cjs --help
```

未指定 `--skills-dir` 时安装到 `~/.agents/skills`；宿主使用不同路径时显式指定。
程序固定安装到 `%LOCALAPPDATA%/Catkit/runtime/catkit.exe`，`CATKIT_HOME` 可覆盖安装根目录。重复安装替换 runtime 并清除旧文件，不保留 UUID 多版本目录；程序占用时退出后重试。
安装默认将 runtime 加入当前用户 PATH，不需管理员权限、不改系统级 PATH，保留已有项并避免重复。重新打开终端或 AI 宿主后可直接运行 `catkit --help`；已有进程不会自动刷新环境。隔离测试/便携安装可用 `--no-path` 跳过。
发布后 npm 的 bin shim 提供 `catkit` 命令；`install` 默认下载匹配版本的 GitHub ZIP 与 SHA256SUMS。
本地 ZIP 可附 `--sha256`，远端发布必须提供校验清单。发布 tag 约定 `catkit-v<版本>`。
构建与发布需同步 package.json 和应用版本；当前没有执行 npm publish 或上传 release。

运行 `python scripts/build_catkit.py` 会构建、验证并覆盖上述正式安装。成功后删除 Nuitka 中间目录和重复的 standalone 副本，`dist/catkit` 仅保留最新 ZIP 与 SHA256SUMS。安装临时目录在结束时清理；开发验收临时文件用完即删，结果摘要留在仓库文档中。

## 环境前提

- Windows 原生 x64；npm/npx 入口需要 Node.js 和随附的 npm。包声明 Node.js >=18，建议使用仍受支持的 Node.js LTS；本机验收为 Node.js 24.16.0 / npm 11.13.0。
- 安装器使用系统 Windows PowerShell 的 Expand-Archive 解压，不要求 PowerShell 7，不需要系统 Python、编译器或管理员权限。
- CATIA 业务需要可用的 CATIA V5、许可及同用户交互会话。安装、help、schema 本身不需要打开 CATIA。
- 在线安装需要访问 npm registry（获取启动器）和 GitHub Releases（获取 ZIP/校验文件）。本地 tgz + ZIP 可以免去这两项下载。

## npm 与 npx 的区别

包尚未发布，以下先使用 `npm pack` 得到的本地 tgz。tgz 只有薄启动器；业务 EXE、依赖及 Skill 在单独的 standalone ZIP 中。

```powershell
# 方式一：全局安装启动器，然后明确执行产品安装。
npm install -g .\raydutchman-catkit-2.2.0.tgz
# 使用 npm 的 shim，避免已有 runtime/catkit.exe 在 PATH 中优先匹配。
# 默认 Windows npm prefix 通常为 $env:APPDATA\npm；以 npm prefix -g 为准。
$prefix = npm prefix -g
& "$prefix\catkit.cmd" install --from-zip 'D:\Downloads\catkit-2.2.0-windows-x64.zip' --skills-dir "$env:USERPROFILE\.config\opencode\skills"

# 方式二：npx 临时提供启动器，不全局安装 npm 包。
npx --yes --package .\raydutchman-catkit-2.2.0.tgz catkit install --from-zip 'D:\Downloads\catkit-2.2.0-windows-x64.zip' --skills-dir "$env:USERPROFILE\.config\opencode\skills"
```

`npm install -g` 不会自动执行 Catkit 的 `install` 子命令；本包没有 postinstall。npx 的 `--yes` 只确认 npm 获取包，不代表批准 CATIA 操作。两条安装命令都可加 `--sha256 <ZIP校验值>`。

发布到 npm 和匹配的 GitHub Release 后，才可将本地 tgz 替换为 `@raydutchman/catkit@2.2.0` 并省略 `--from-zip`；目前不能把这些线上入口当作已发布可用。

## Skill 安装与发现

产品 `install` 把 ZIP 内 `skills/catkit/` 的完整目录复制到目标 Skill 根目录下的 `catkit/`，包含 SKILL.md 和 references；不是通过 npm 单独下载 Skill。

- 默认目标：`~/.agents/skills/catkit/`。
- OpenCode 显式目标：`--skills-dir ~/.config/opencode/skills`，实际落在其 `catkit/` 子目录。
- 替换目标目录中的旧 Catkit Skill；安装时切换目标目录不会自动删除另一位置的旧副本。
- 宿主是否读取该目录由宿主决定；复制成功不等于正在进行的会话已刷新 Skill。新会话中确认发现结果。

## 移除与重新安装

`npm uninstall -g @raydutchman/catkit` 只移除 npm 启动器，不删除独立 runtime、Skill 或用户 PATH 项。npx 使用 npm 缓存，不会留下全局 npm 包，但产品安装结果仍持久保留。

完整移除需先确认没有运行中的 worker/未完成任务，再删除 `%LOCALAPPDATA%/Catkit`（或实际 CATKIT_HOME）、已安装的 Catkit Skill 目录，并仅移除用户 PATH 中该 runtime 项。其他 Skill、系统 PATH、CAD 文件不属于清理范围。现有终端环境可能仍保留旧 PATH，需重开。

## 控制台窗口

CLI 为控制台程序。受限 Windows Job 不允许 worker 脱离时，使用当前交互用户的临时计划任务直接启动 worker；这种路径可能短暂显示控制台窗口。这是当前标准启动方式的正常表现，保留该方式，不增加隐藏脚本或启动后隐藏窗口的处理。
