# 用 Cat5Dev 把 AI 编写的 VBA 变成 CATVBA

目标：让 AI 编辑普通文本，用 Cat5Dev 同步到 CATIA，再由 CATIA 编译、
保存为可运行的 `.catvba`。不需要从 EXE 逆向，也不需要 AI 操作 VSCode 界面。

本文依据本机 Cat5Dev **0.4.0** 和上游文档整理，核对日期：2026-11-19。

## 1. 先理解这条流程

```text
CATIA 中的 VBA 项目
       │ Pull
       ▼
modules/*.bas_utf、*.cls_utf、*.frm_utf
       │ AI / 人工编辑
       │ Push
       ▼
CATIA 内存中的 VBA 项目
       │ 编译、测试、保存
       ▼
磁盘上的 .catvba
```

**Push 不等于保存 CATVBA。** Push 后必须在 CATIA VBA 编辑器保存，
否则关闭 CATIA 后可能丢失刚同步的代码。

## 2. 一次性准备

需要 Windows、已安装 VBA 支持的 CATIA V5、Windows 版 VSCode 和 Cat5Dev。
扩展可从 [Cat5Dev Releases](https://github.com/kantoku-code/Cat5Dev/releases)
下载 VSIX，再通过 VSCode 的 “Install from VSIX...” 安装。

1. 在 CATIA 的宏库管理中创建或打开一个 VBA 项目，例如 `MyMacro.catvba`。
   不同 CATIA 版本的菜单文字可能不同，以宏库中的 VBA 项目类型为准。
2. 按 `Alt+F11` 打开 VBA 编辑器，确认项目实际显示在项目列表中。
3. 在项目中插入一个标准模块，例如 `Main`，保存项目。
4. 用 Windows VSCode 打开专用工作目录。
5. 执行命令面板中的 **CATIA: Initialize Project (cat5dev.toml)**。
6. 打开左侧 **CATIA V5 VBA** 面板，通过齿轮按钮选择刚才的目标项目。

**VBA 编辑器需要保持打开。** 扩展通过 Windows COM 和 32 位
`cscript.exe` 与 CATIA 通信，不建议把扩展只安装在 WSL 的远程扩展宿主里。
AI 可以在 WSL 编辑 `/mnt/d/...` 下的同一批文本，Pull/Push 仍由 Windows
侧 VSCode 执行。

### 本仓库的配置注意事项

当前根目录 `cat5dev.toml` 指向 `CDF_Toolkit_CATIA`，编码为 `gbk`。
开发独立宏时应使用自己的工作目录和配置，不能直接把其他项目代码
Push 到这个工具集。

示例配置片段：

```toml
[project]
target_project = "MyMacro"
language = "zh"
encoding = "gbk"
```

`target_project` 是宿主中显示的 VBA 项目名，不一定等于文件名。
优先通过扩展选择目标，让它写入正确名称。保留初始化生成的其他配置项。

## 3. 第一个 AI 宏：只读取当前文档名称

先点击 **Pull**，让扩展把已有模块导出到 `modules/`。
假设得到 `modules/Main.bas_utf`，让 AI 编辑该文件：

```vb
Option Explicit

Public Sub CATMain()
    If CATIA.Documents.Count = 0 Then
        MsgBox "请先打开一个 CATIA 文档。", vbInformation
        Exit Sub
    End If

    MsgBox CATIA.ActiveDocument.Name, vbInformation, "当前文档"
End Sub
```

这是模块正文，不需要人为加入 `.frm` 设计头或复制其他项目的模块属性。
实际导出文件中若包含扩展生成的结构，应以 Pull 的结果为准。

可以给 AI 这样的任务：

```text
只修改 modules/Main.bas_utf。
编写 CATIA V5 VBA 宏，入口 Public Sub CATMain()。
读取并显示当前文档名称，无文档时给出提示。
不要修改模型、不要保存文档、不要修改 cat5dev.toml 的目标项目。
使用 Option Explicit；遇到不确定的 CATIA API 先核对文档或类型库。
```

然后：

1. 查看 AI 修改的差异，确认仅修改了指定模块。
2. 点击 **Push**，核对目标项目及任何新增/删除模块提示。
3. 回到 VBA 编辑器，使用其编译命令检查项目。
4. 运行 `Main.CATMain`，确认结果。
5. 在 VBA 编辑器按 `Ctrl+S` 保存。
6. 关闭并重新打开该宏库，确认代码和入口仍在。

到这一步，AI 写的文本已经成为真正保存到磁盘的 CATVBA。

## 4. 日常开发怎样分工

| 工作 | 合适的执行方 |
|------|--------------|
| 写模块、修改算法、解释错误 | AI 编辑文本 |
| 选择目标项目、Pull/Push | 用户使用 Cat5Dev |
| 编译、断点调试、检查对象类型 | CATIA VBA 编辑器 |
| 模型效果与交互验收 | 在新建测试模型上验证 |
| 保存、分发 CATVBA | CATIA 宿主 |

同一轮修改尽量只选择一边作为代码来源：

- **CATIA 中改过代码**：保护 VSCode 未同步修改后再 Pull。
- **AI 改过文本**：先 Push、编译、保存，不要马上 Pull 覆盖修改。
- Pull 会刷新导出内容；本机版本会清理 `modules/` 中同类导出文件。
- Push 可能提示删除宿主中本地不存在的模块，不要未经核对直接同意。

## 5. 有窗体时怎样做

Cat5Dev 同步的是 **UserForm 代码**，不是完整窗体设计。

1. 在 CATIA VBA 编辑器中创建 UserForm，例如 `OptionsForm`。
2. 在设计器添加按钮、文本框，并确定控件名称。
3. Pull 后，让 AI 编辑 `OptionsForm.frm_utf` 中的事件代码。
4. Push 回同名的已有 UserForm，编译和测试。
5. 保存 CATVBA。

**Push 不能创建新 UserForm。** `.frm_utf` 也不能替代包含布局资源的
`.frm/.frx`。需要交换完整窗体时，使用 VBA 编辑器导出、导入 `.frm/.frx`，
再通过 Cat5Dev 维护代码。

## 6. 编码与连接排错

| 症状 | 优先检查 |
|------|----------|
| 找不到项目、Pull/Push 失败 | CATIA 和 VBA 编辑器是否都打开；目标项目是否加载 |
| CATIA 已启动但 COM 连接失败 | 两端用户、Windows 会话和权限级别是否一致 |
| 中文变乱码 | `encoding` 是否符合宿主文本编码；本项目是 `gbk`，不要盲用扩展默认值 |
| Push 成功但重启后代码没了 | 是否在 CATIA VBA 编辑器保存过 |
| 新窗体没出现 | 先在 CATIA 创建同名 UserForm，Push 仅更新代码 |
| VSCode 提示正确、CATIA 编译失败 | 以 VBA 编译器为准；补全和静态检查不能代替宿主编译 |

本次曾出现调用方为管理员、CATIA 为普通权限，`GetActiveObject` 返回
`0x800401E3`；统一权限后连接成功。这是一个已遇到的原因，不代表所有
此错误都应通过“全部管理员运行”解决。更多见
[COM 连接排错](CATIA_COM_CONNECTION_ISSUE.md)。

## 7. AI 能否连 Push 和保存也自动完成？

可以开发宿主桥接脚本，但这与“AI 使用 VSCode 插件命令”是两件事。

本次实际验证的另一条链路是：

```text
Windows COM → CATIA.SystemService.ExecuteScript
            → 宿主内 MSAPC.Apc → VBA 模块/窗体 → 编译保存
```

其中用到了 `MSAPC.Apc.7.1`、`ProjectItems`、`VBComponents` 等接口。
保存有效 CATVBA 时还涉及 `apc` 子存储及 `SaveCompleted`；简单写个
CFB 文件再改扩展名并不成立。这条路径需要处理项目身份、宿主版本、
保存和错误恢复，适合专门实现自动化工具。

**日常使用优先采用“AI 改文本 → Cat5Dev Push → CATIA 编译保存”。**
它已经足够把写代码和打包宏的工作串起来，不必依赖本次逆向过程文件。

## 相关资源

- [Cat5Dev 上游与安装说明](https://github.com/kantoku-code/Cat5Dev)
- [同步行为与限制](https://github.com/kantoku-code/Cat5Dev/blob/main/docs/en/sync.md)
- [配置说明](https://github.com/kantoku-code/Cat5Dev/blob/main/docs/en/configuration.md)
- [REA 逆向 EXE 教程](REA_EXE_REVERSE_ENGINEERING.md)
