# 使用 REA 分析 EXE / DLL

目标：用 REA 调用 Ghidra，定位 Windows 程序中的函数、字符串和调用关系，
形成可核对的分析结论。本文讲静态分析，不把反编译伪代码等同于原始源码。

核对日期：2026-11-19。本次实际使用 WSL、REA **6.8.0**、
Ghidra **12.1.4**、OpenJDK **25**。

## 1. REA 是哪一层工具

```text
人或 AI → REA CLI → Ghidra → 反汇编 / 伪代码 / 交叉引用
```

REA 是分析工具的统一入口，不是单独的反编译算法。本次使用 CLI，
没有依赖 REA MCP 服务；AI 通过 shell 调用相同命令即可。

适合回答：

- 这个 DLL 导出函数的参数怎样使用？
- 哪段代码读取配置、调用文件 API、设置 CATIA 属性？
- 某个字符串在哪里被引用？
- 某条失败路径是否写文件、是否依赖驱动？

不能直接保证：

- 恢复原始变量名、注释、完整工程。
- 把任意 EXE 一键转换为可编译源码或 CATVBA。
- 仅凭静态伪代码证明重写结果与原程序完全等价。

## 2. 环境准备

安装与所选 REA 版本兼容的 Node.js、JDK 和 Ghidra。版本要求会变化，
先看 [安装文档](https://github.com/morluto/rea/blob/main/docs/getting-started/installation.md)
与 npm 包元数据：

```bash
node --version
java -version
npm view rea-agents version engines
```

全局安装的示例：

```bash
npm install --global rea-agents
```

若要复现本次工具版本，可明确安装 `rea-agents@6.8.0`。
是否需要系统权限取决于 npm 的安装目录，不要把全局工具误装到一次性临时目录。

Ghidra 从 [官方 Releases](https://github.com/NationalSecurityAgency/ghidra/releases)
获取，核对发布方提供的校验值后解压到长期目录。REA 不会替你安装 JDK。

本机 WSL 示例：

```bash
export GHIDRA_INSTALL_DIR=/opt/ghidra
export JAVA_HOME=/usr/lib/jvm/java-25-openjdk-amd64

rea --version
rea doctor --provider ghidra --json
```

上述目录是本机路径，其他机器请替换。`export` 只作用于当前 shell；
需要长期使用时再加入自己的 shell 配置。

**版本兼容的实际教训：** 本次 REA 6.8.0 拒绝了 Ghidra 12.2，
换成 12.1.4 后诊断通过。上游主分支文档和已发布 npm 包可能不同步；
以安装版本的帮助和 `doctor` 结果为准，不要把“最新 Ghidra”当作必然兼容。

## 3. 建立只读分析目录

以下命令在 WSL bash 中执行，把目标路径替换为自己的文件：

```bash
TARGET='/mnt/d/path/to/program.exe'
OUT="$HOME/rea-analysis/program"
mkdir -p "$OUT"

sha256sum "$TARGET" > "$OUT/target.sha256"
file "$TARGET"
rea inspect "$TARGET" --provider ghidra --json > "$OUT/inspect.json"
```

目标是 DLL 时流程相同。记录哈希是为了避免把不同版本的地址和结论混在一起。
分析输出保存在单独目录，避免修改输入文件。

先判断文件类型：

| 类型 | 分析重点 |
|------|----------|
| 原生 C/C++ PE | 函数、导入导出、字符串、数据结构 |
| 原生 VB6 | VB 元数据、事件映射、COM 调用、运行时辅助函数 |
| .NET | 应评估更适合托管程序集的 provider，不直接套原生路径 |
| 加壳/强混淆文件 | 初始分析可能只看到壳，需另行判断分析范围 |

本教程使用的是静态命令；不需要启动目标 EXE 或加载目标 DLL 执行。

## 4. 从一个具体问题开始

不要一开始让工具“完整反编译整个程序”。例如先问：

> 某个按钮怎样取得 RGB？它把颜色应用到 Body 还是 Face？

或：

> DecryptLockFileW 的第三个参数选择了哪种算法？

围绕问题搜索字符串、API 或已知符号：

```bash
rea search "$TARGET" "SetRealColor" --provider ghidra --json > "$OUT/search-color.json"
rea search "$TARGET" "Conf2.ini" --provider ghidra --json > "$OUT/search-config.json"
```

从结果中选择实际存在的函数或地址，再查看：

```bash
# 0x00420860 只是语法示例，必须换成当前目标实际返回的地址。
ADDR=0x00420860
rea function "$TARGET" "$ADDR" --provider ghidra --json > "$OUT/function.json"
rea decompile "$TARGET" "$ADDR" --provider ghidra --json > "$OUT/decompile.json"
rea xrefs "$TARGET" "$ADDR" --provider ghidra --json > "$OUT/xrefs.json"
```

这些命令分别帮助了解函数上下文、查看伪代码、查找引用关系。
JSON 结构可能随版本变化，不要假定所有版本都把伪代码放在同一个字段中；
本次部分输出的 `normalized_result` 是伪代码字符串。

## 5. 分清 VA、RVA 和文件偏移

- **VA**：分析视图中的虚拟地址。
- **RVA**：相对映像基址的地址。
- **文件偏移**：原始文件中的字节位置。

PE 通常满足 `VA = ImageBase + RVA`，但**文件偏移通常不等于 RVA**。
需要结合节表映射，不能对任意文件偏移直接加映像基址。

REA 文档提供：

```bash
rea inspect-native-load-image "$TARGET" --provider ghidra --json
rea address-to-file-offset "$TARGET" "$ADDR" --provider ghidra --json
rea decompile "$TARGET" --file-offset=0x1013 --provider ghidra --json
```

同样应替换示例地址。若安装版本缺少某命令，先查看 `rea --help` 和
对应子命令帮助，不要通过猜测偏移继续分析。

## 6. 伪代码只是线索，关键结论要回到指令

本次原生 VB6 分析中遇到了几类典型误判：

1. **声明字符串不等于调用。** 程序同时存在读、写配置 API 的名称，
   实际调用的却是 `GetPrivateProfileStringA`。只看到写 API 的字符串，
   就判断“程序会生成配置”，会得到错误结论。
2. **函数边界可能错误。** 反编译结果可能把后续函数拼在一起。
3. **COM 参数可能混淆。** `VARIANT` 的类型标记和实际参数值不能混为一谈；
   需要追踪参数块及调用次序，并核对宿主类型库。
4. **方法表不一定保存绝对函数地址。** 可能指向跳转 thunk，
   再通过相对跳转到达真实处理函数。
5. **事件名仍在，不代表源码仍在。** 窗体存储与事件元数据可以保留，
   但不一定包含完整 VBA 工程或可直接导入的源码。

安装了 GNU binutils 时，可以额外查看指定地址范围的指令：

```bash
objdump -d -Mintel \
  --start-address=0x00420860 \
  --stop-address=0x00420c00 \
  "$TARGET" > "$OUT/instructions.txt"
```

这里同样使用示例地址。不要把反编译器推断的函数签名直接当成已验证 ABI。

## 7. 建立证据表，而不是堆输出

每个结论至少记录：

| 项目 | 示例 |
|------|------|
| 目标身份 | 文件名、SHA-256、位数、工具版本 |
| 问题 | 配置缺失时是否写入默认文件 |
| 定位 | 函数 VA、调用点、相关字符串 |
| 证据 | API 声明描述符、实际调用目标、跳转条件 |
| 结论 | 已证实只读 / 仍不确定 |
| 边界 | 未执行原程序，不代表所有输入路径已覆盖 |

将“已证实”“推测”“未验证”分开。工具输出可以很多，但报告应围绕原问题。
若证据始终不足，应说明卡在哪一步，不要让 AI 反复读取同一份伪代码。

给 AI 的任务模板：

```text
对指定 EXE/DLL 做只读静态分析，不执行目标。
先记录哈希、架构和工具版本。
本轮只回答：[一个具体问题]。
用 REA 定位函数、反编译并核对交叉引用。
关键参数和读写行为必须追踪实际调用点，必要时核对指令。
输出：结论、地址证据、未确认部分、下一步最小验证。
不要默认重写整个程序，不修改原文件。
```

## 8. 从分析到重写，需要额外做什么

如果确实需要重写，应把它当作独立开发任务：

1. 先写行为规格及不确定项。
2. 用已知输入/输出建立测试。
3. 分离格式解析、业务逻辑和宿主交互。
4. 编译并在独立样本上验证。
5. 报告行为差异，不能仅凭“返回成功”或“文件头变化”认定等价。

例如 VB6 EXE 到 CATVBA，通常是依据证据**重新实现 VBA**，
不是机械转换。若原 EXE 已经满足使用需求，停在分析报告阶段完全合理，
不必为了逆向而重写。

若只是希望 AI 帮忙开发新的 CATIA 宏，直接走
[Cat5Dev 的 AI → CATVBA 流程](CAT5DEV_TEXT_TO_CATVBA.md) 更合适。

## 9. 常见问题

| 问题 | 处理方式 |
|------|----------|
| `doctor` 找不到 Ghidra | 核对 `GHIDRA_INSTALL_DIR` 指向实际安装根目录 |
| Java / provider 不兼容 | 核对 REA 包版本、JDK、Ghidra 的组合 |
| 首次分析较慢 | 先缩小问题，保留输出，不并发重复启动同一目标的大量分析 |
| 伪代码无法解释参数 | 查实际调用点、指令、类型库，明确保留不确定性 |
| Windows 原生安装是否支持 | 查当前 provider 文档；本文实测路径是 WSL，不把它当作 Windows 原生验证 |
| 必须装 MCP 吗 | 不需要，CLI 已能完成本文流程 |

## 相关资源

- [REA 项目](https://github.com/morluto/rea)
- [Native investigation 工作流](https://github.com/morluto/rea/blob/main/docs/guides/native/native-investigation.md)
- [CLI 文档](https://github.com/morluto/rea/blob/main/docs/getting-started/cli.md)
- [Ghidra provider](https://github.com/morluto/rea/blob/main/docs/providers/ghidra-provider.md)
- [Windows Ghidra provider](https://github.com/morluto/rea/blob/main/docs/providers/windows-ghidra-p0.md)
