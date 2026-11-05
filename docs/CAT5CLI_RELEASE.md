# cat5cli 发布流程

CLI 独立版本从 `catia_copilot/cli/version.py` 读取，须与 `packages/cat5cli/package.json` 一致。首个预览版为 `0.1.0-beta.1`，GUI 仍为 `2.2.0`。CLI/Skill 共用同一发布提交，不改写旧 Catkit 历史记录。

## 本地准备

```powershell
python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -q
node --test packages/cat5cli/test/installer.cjs
python scripts/build_cat5cli.py --no-install
```

在 `packages/cat5cli` 目录执行：

```powershell
npm pack --dry-run --ignore-scripts
npm pack --pack-destination ../../dist/cat5cli --ignore-scripts
```

发布产物仅包括：

- `dist/cat5cli/cat5cli-0.1.0-beta.1-windows-x64.zip`
- `dist/cat5cli/SHA256SUMS`
- `dist/cat5cli/raydutchman-cat5cli-0.1.0-beta.1.tgz`

ZIP 内为 standalone runtime、许可证及配套 Skill，不含用户 CAD 文件和公司模板。npm tgz 仅含启动器、安装器、说明和许可证。不发布仓库整体 dist 或 GUI 安装包。

## 发布顺序

1. 审核并提交 CLI/Skill 变更；用户模板修改不混入发布提交。
2. 确认 npm 账号有 `@raydutchman` scope 发布权限，GitHub 账号有目标仓库 Release 权限。
3. 将发布提交推送至仓库，并为该提交建立 `cat5cli-v0.1.0-beta.1` 标签。
4. 创建 GitHub prerelease，上传 ZIP 与 SHA256SUMS。先确认公开下载地址可读，再发 npm，避免启动器指向不存在的文件。
5. 在 `packages/cat5cli` 执行 `npm publish --access public --tag beta`。当前 publishConfig 默认 beta，不应把预览版发布为 latest。
6. 从另一干净环境运行 `npx @raydutchman/cat5cli@beta install`，确认版本、校验和、PATH、Skill，以及有许可 CATIA 环境中的业务调用。

本机登录操作由账号持有人完成：

```powershell
npm login
gh auth login --web
```

npm 发布按账号策略完成2FA或使用已配置的受支持发布认证。不要在提示词、仓库或日志里保存 token。

## Skill 单独发布

通用Skill保存在 `skills/cat5cli/`，包含 SKILL.md 和 references。将它推送至公开仓库后即可被 Skills CLI 发现，不需要另发 npm 包。

```powershell
npx skills add https://github.com/RayDutchman/CATIA-Copilot/tree/cat5cli-v0.1.0-beta.1/skills/cat5cli --skill cat5cli -g -a opencode
```

公开仓库默认分支尚未包含该目录时，上述安装入口不可用；预览期可明确使用包含该提交的 GitHub tree URL（标签或分支）。发布版Skill应与CLI版本对应，不把未发布命令写成已可用。

当前 Skills CLI 要求 Node.js 22.20+。离线安装可由 cat5cli install 复制ZIP附带Skill；`--skills-dir` 选择宿主目录。公司BOM填写规则单独放入私有Skill，不写入通用CLI白名单。

## 旧本机安装迁移

先结束旧Catkit任务，验证新cat5cli安装，再退役旧runtime和旧PATH项。旧任务记录可移至 `%LOCALAPPDATA%/Cat5cli/legacy-catkit-tasks` 作为历史证据，不从新CLI续接旧任务。旧Skill备份后退出宿主加载目录，避免AI混用两套命令。

## 验收边界

本机本地TGZ/ZIP验证不替代真实registry/Release下载验证。另一台无Python的Windows、部分图纸及保持测量实机场景仍需补测；预览发布应明确这些范围。
