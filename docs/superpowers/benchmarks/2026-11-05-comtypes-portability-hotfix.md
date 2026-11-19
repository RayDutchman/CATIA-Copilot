# beta.2 类型库包装器可移植性修复候选

## 故障证据

同事机器的beta.1在pywinauto导入阶段报Typelib different than module，连--version/schema都不可用。安装器与独立ZIP结果一致，本机源码GetModule正常。核对comtypes._tlib_version_checker确认此错误检查的是类型库文件mtime与生成模块所记录mtime之差，而非注册表版本号。

beta.1显式--include-package=comtypes.gen会纳入构建机已生成的包装器，跨机器系统文件mtime不同即可能失败；清理目标机TEMP不能删除EXE内的包装器。

## 实现

- 元信息入口延迟导入任务系统；help、version、schema不初始化COM。
- 构建仅包含comtypes.gen包壳，排除comtypes.gen.*，保留代码生成器及原生版本校验模块。Nuitka报告若出现任何生成子模块则构建失败。
- 业务入口和worker在任务模块导入前初始化目标机缓存：CAT5CLI_HOME/comtypes_cache/py<version>-<bits>-<comtypes-version>，默认根为LOCALAPPDATA/Cat5cli。
- 不禁用mtime校验；陈旧或不匹配的包装器由comtypes重新生成。
- 首次生成在进程间互斥锁内完成。并发测试发现同一Windows路径偶尔返回普通或\\?\形式，导致字符串哈希锁名不同；改为目录st_dev/st_ino身份，避免路径别名造成双写。
- Description、业务命令、CATIA实例选择与文件迁移能力未扩展。

## 本机验证

- 全量495项通过（50.893s）。
- 元信息命令在阻断COM模块导入时仍成功。
- 中文路径/空缓存、人工陈旧缓存、真实复制类型库mtime变更后再生通过；10轮两进程同时冷启动通过。
- 候选EXE编译报告确认零comtypes.gen生成子模块。
- 候选EXE空缓存连接诊断成功；将自建缓存中原生_check_version的mtime改为0后，再次诊断自动重建包装器并连接成功。
- 候选EXE的12项BOM业务调用通过；原生SaveAs错误对话框接续通过，建模脚本执行计数为1。仅操作自建夹具，已清理。
- 只读审查未发现阻断问题。

## 交付状态

- 本地候选：dist/cat5cli/cat5cli-0.1.0-beta.2-windows-x64.zip，校验值见同目录SHA256SUMS。
- 未替换已发布beta.1资产，未上传beta.2或更新npm标签，也未覆盖正式安装。
- 本机模拟mtime差异不能替代同事机器实测；需在原故障机器解压候选ZIP后验证--version、schema、status --diagnose和一次只读BOM采集，再决定发布。
- 多CATIA实例消歧、批量文件/目录改名与引用更新、无焦点干扰均为独立后续任务。当前仓库的批量改名仍逐份调用SaveAs，没有据此承诺可绕过SaveAs。
