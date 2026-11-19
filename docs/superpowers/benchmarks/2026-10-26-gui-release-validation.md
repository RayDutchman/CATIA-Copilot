# GUI Nuitka / Inno Setup 发布验收

- 使用现有 build_nuitka_installer.ps1，覆盖 D:/CATIA_Related/CATIA-Copilot-dist-nuitka 的同版本产物；未修改 CLI runtime 或用户模板文件。
- 发布前的翻译目录校验、QM 重建和 Nuitka standalone 编译成功；GUI 专项回归47项通过。此前当前业务代码完整回归481项通过。
- 首次 postbuild 因 Nuitka 未生成空的 PySide6/translations 目录而中止；脚本现会在已存在的 PySide6 包目录下创建 translations，再复制 qtbase_zh_CN.qm。找不到 PySide6 包仍明确失败。
- 修复后从现有脚本 postbuild 段续接，未重复编译；Inno Setup 成功（80.828s）。
- 正式GUI通过UI自动化启动主窗口、识别BOM/Mass Properties/PLM入口、检查无AI聊天入口、打开BOM工作台并关闭，进程退出码0。BOM按钮及对话框英语文本正常。
- 验证应用中英文QM和Qt中文基础QM存在；未对用户CAD执行修改或保存。未执行安装器安装，也未宣称覆盖全部CAD业务手工验收。
- main.build 编译中间目录已清理，仅保留正式目录及安装器。

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| CATIA Copilot 2.2.0/CATIA Copilot 2.2.0.exe | 134428160 | 202b9110efe8db17c9901fef529b66d5efc681508185b16ee3d623940d9c3e16 |
| CATIA-Copilot-Setup-2.2.0.exe | 59931914 | bf85a65bfa84dd305af18c756bbec6e63048ce5de01d580d660f701894543ff2 |

构建和UI证据位于 .superpowers/gui-release-build.log、gui-installer-resume.log、gui-bundle-check.json、gui-release-artifacts.json。
