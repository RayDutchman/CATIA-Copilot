# Catkit 安装启动器

这是 Windows CLI 的薄启动器，业务实现位于 Nuitka standalone 包中，不需要系统 Python。
包名尚未发布，当前 package 标记 private；以下使用本地入口，不代表 npm latest 已可用。

```powershell
node packages/catkit/bin/catkit.cjs install --from-zip D:/Downloads/catkit-2.2.0-windows-x64.zip --skills-dir "$env:USERPROFILE/.config/opencode/skills"
node packages/catkit/bin/catkit.cjs --help
```

未指定 `--skills-dir` 时安装到 `~/.agents/skills`；宿主使用不同路径时显式指定。
程序固定安装到 `%LOCALAPPDATA%/Catkit/runtime/catkit.exe`，`CATKIT_HOME` 可覆盖安装根目录。重复安装替换 runtime 并清除旧文件，不保留 UUID 多版本目录；程序占用时退出后重试。安装不修改系统 PATH。
发布后 npm 的 bin shim 提供 `catkit` 命令；`install` 默认下载匹配版本的 GitHub ZIP 与 SHA256SUMS。
本地 ZIP 可附 `--sha256`，远端发布必须提供校验清单。发布 tag 约定 `catkit-v<版本>`。
构建与发布需同步 package.json 和应用版本；当前没有执行 npm publish 或上传 release。

运行 `python scripts/build_catkit.py` 会构建、验证并覆盖上述正式安装。成功后删除 Nuitka 中间目录和重复的 standalone 副本，`dist/catkit` 仅保留最新 ZIP 与 SHA256SUMS。安装临时目录在结束时清理；开发验收临时文件用完即删，结果摘要留在仓库文档中。
