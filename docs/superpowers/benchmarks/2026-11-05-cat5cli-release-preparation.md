# cat5cli 改名与发布准备验收

## 当前交付

- 命令/EXE/npm包/Skill分别统一为 cat5cli、cat5cli.exe、@raydutchman/cat5cli、cat5cli。
- CLI 独立版本0.1.0-beta.1，GUI版本仍为2.2.0，Python内部包catia_copilot不改。
- 运行目录为LOCALAPPDATA/Cat5cli/runtime；CAT5CLI_HOME影响安装及默认任务目录，CAT5CLI_TASKS_DIR优先覆盖任务位置。
- 计划任务和互斥名使用Cat5cli；历史测试文件名、迁移基线及旧验收记录保留原名。
- npm包为public、默认beta dist-tag，无postinstall；在线安装下载cat5cli-v<VERSION> Release中的同版本ZIP及SHA256SUMS。
- 构建入口为scripts/build_cat5cli.py；--no-install可单独生成发布产物。

## 验证

- 全量Python488项通过（49.336s），Node安装器8项通过。
- 精确检查改名后的PowerShell环境变量绑定及广播类型名，补回归防止PATH写入命名不同步。
- npm pack预检及真实打包成功，TGZ只含6个启动器/说明/许可证文件。
- 真实npx本地TGZ安装通过，版本/Skill/user PATH保持校验通过。旧Catkit runtime和已加载目录中的旧Skill已退役，任务记录移至新安装根的legacy-catkit-tasks，旧Skill留在本地迁移证据目录。
- 使用真实Skills CLI发现1个cat5cli Skill，并在隔离项目中安装，SKILL.md及BOM参考内容与源文件一致。
- 正式EXE实例名实测通过：共享子装配定义联动及根层实例隔离均正确。
- 正式EXE BOM专项12个业务操作均经原任务接续完成，包含预期的只读字段失败。CATIA欢迎页触发短暂waiting_for_dialog，但原任务成功完成；未重复原操作或自动点击恢复会话提示。自建夹具已清理。
- CATIA启动与npx捕获曾出现旧Windows原生命令的编码线程警告，安装/业务结果已单独校验；没有修改系统代码页或区域设置。
- 最终ZIP内每个文件与已安装runtime哈希一致，旧dist/catkit及新编译中间目录已清理。
- 改名/版本/下载/发布标签的只读审查未发现实质缺陷，本次改动范围diff --check通过。

## 产物

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| dist/cat5cli/cat5cli-0.1.0-beta.1-windows-x64.zip | 47144672 | 9f3a5604795c6a5bc586f94c851bcae5c73f2070c929c29e0edced53c7162269 |
| dist/cat5cli/raydutchman-cat5cli-0.1.0-beta.1.tgz | 6641 | 4475304b06f281ebf26111928a106c471fd2dbfbdb692b39a9b8f6b32580b2c8 |

## 发布阻塞及未覆盖范围

- npm whoami返回ENEEDAUTH，gh auth status报告未登录；尚未验证scope发布权限，未执行npm publish、git push或创建Release/tag。
- 公共仓库默认分支尚未包含新Skill，线上npm/Release/Skills安装不能视为可用。
- 跨机器无Python验证、旧图纸与保持测量实机缺口仍保留。
- 本轮未自动提交；原有用户模板和GUI发布脚本修复仍保留在工作区。
- 发布顺序和账号操作见docs/CAT5CLI_RELEASE.md。
