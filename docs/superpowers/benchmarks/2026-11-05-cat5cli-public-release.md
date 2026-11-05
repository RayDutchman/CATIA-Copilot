# cat5cli 首次公开发布

- 版本：0.1.0-beta.1；npm包：@raydutchman/cat5cli。
- 发布代码提交：f629941；远端发布分支：release/cat5cli-0.1。
- GitHub标签：cat5cli-v0.1.0-beta.1；以该标签固定配套Skill，未合并或改写远端main。
- GitHub预发布：https://github.com/RayDutchman/CATIA-Copilot/releases/tag/cat5cli-v0.1.0-beta.1 。公开ZIP与SHA256SUMS下载校验通过。
- npm：https://www.npmjs.com/package/@raydutchman/cat5cli 。registry已返回0.1.0-beta.1版本和beta标签。
- 首次npm发布要求浏览器WebAuthn确认；上传返回202后曾出现0.0.0-stage占位，随后registry公开了实际版本。没有把exit code 0单独当作公开发布成功依据。
- 发布操作使用本机已有GitHub PAT及npm登录状态；凭据未写入仓库或输出。

## 安装入口

```powershell
npx @raydutchman/cat5cli@beta install
npx skills add https://github.com/RayDutchman/CATIA-Copilot/tree/cat5cli-v0.1.0-beta.1/skills/cat5cli --skill cat5cli -g -a opencode
```

第二条为独立Skill安装；第一条安装器已附带匹配Skill，可用--skills-dir指定宿主目录。

## 线上验证与最终状态

- 已从公开npm registry运行npx安装，验证GitHub runtime下载、版本和help；npm dist.integrity与本地发布TGZ一致，shasum为e245ebcd7a390938edb75e4f7b7c2a02c1006f07。
- 固定Git标签的Skill通过真实Skills CLI安装，5个文档均与该标签内容一致；Windows Git产生CRLF/LF转换，按规范化换行后逐字符比较，无内容差异。
- 线上验证在临时目录中使用CAT5CLI_HOME及--no-path，不修改正式runtime和用户PATH。
- registry最终确认latest和beta均为0.1.0-beta.1。移除latest的尝试未成功；用户明确要求保留latest，此后不再尝试移除。
- 默认安装也可使用 `npx @raydutchman/cat5cli install` 或显式 `@latest`；包版本仍明确包含beta，不将默认标签解释为完整稳定性验收。
- 本轮改名、迁移、GitHub/npm发布及线上安装验证已完成。跨机器无Python、部分图纸/保持测量场景仍按预览版验收边界记录。
