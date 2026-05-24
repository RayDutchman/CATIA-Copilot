# 故障排查指南

本文档列出 CATIA Copilot 使用中的常见问题及解决方法。

---

## CATIA 连接问题

### 症状：状态栏指示器显示红色（未连接）

**可能原因：**
1. CATIA V5 未启动
2. CATIA 正在初始化中

**解决方法：**
1. 确认 CATIA V5 已完全启动（主界面可见）
2. 等待 CATIA 完成初始化后，程序每 5 秒自动重试连接

---

### 症状：状态栏指示器显示橙色（连接异常）

**可能原因：** `gen_py` 早绑定缓存污染了 COM 接口（`EnsureDispatch` 残留）

**解决方法：**
1. 菜单「帮助 → CATIA 连接诊断」查看详细报告和修复建议
2. 关闭 CATIA Copilot
3. 手动删除 `%LOCALAPPDATA%\Temp\gen_py\` 目录
4. 重新启动 CATIA Copilot（启动时会自动清理该缓存）

---

### 症状：COM 连接检测被安全软件拦截

**现象：** 状态栏持续显示红色，即使 CATIA 已运行

**原因：** 安全软件拦截了 `tasklist` 进程检测命令

**解决方法：**
1. 将 CATIA Copilot 添加到安全软件白名单
2. 或在安全软件中允许 `python.exe` / `CATIA Copilot.exe` 执行进程查询

---

## BOM 相关问题

### 症状：BOM 采集后某些属性为空

**可能原因：**
1. 该零件在 CATIA 中未填写对应属性
2. 用户自定义属性名拼写与 `PRESET_USER_REF_PROPERTIES` 不一致

**解决方法：**
1. 在 CATIA 中检查对应零件的属性是否已填写
2. 检查 `catia_copilot/constants.py` 中 `PRESET_USER_REF_PROPERTIES` 的属性名是否与 CATIA 中的一致（区分大小写）

---

### 症状：BOM 写回失败，某些零件属性未更新

**可能原因：**
1. 零件文件只读（未签出）
2. CATIA 当前活动文档不是目标产品

**解决方法：**
1. 确认 CATIA 中目标产品处于可编辑状态
2. 查看程序日志（「帮助 → 日志窗口」）中的具体错误信息

---

## VBA 宏问题

### 症状：运行装配宏时 FlipForm 按钮无响应（CATIA R33）

**原因：** CATIA R33 中 `SelectElement2` 等待用户选择时会冻结 UI，导致 FlipForm 所有按钮无法点击。

**解决方法：** 确认使用的是最新版宏（已改用 `SelectElement3`）。在 CATIA VBA IDE 中检查装配宏是否包含 `SelectElement3` 调用。

---

### 症状：VBA 宏报错"Type mismatch"（错误 13）

**原因：** `SelectElement3` 的 filter 参数使用了强类型 `String` 数组（`Dim f(0) As String`）。

**解决方法：** 改用 `Variant` 类型数组：
```vba
Dim f As Variant
f = Array("MonoDim")
sel3.SelectElement3 f, "请选择...", False, 0, False
```

---

### 症状：装配宏选择元素后立即报错 91 或 0x80004005

**已知情况：** CATIA R33 中 `SelectElement3` 返回后，在某些情况下 `sel.Item(1)` 可能失败。

**调试步骤：**
1. 在报错位置前后添加 `If sel.Count > 0 Then` 防御判断
2. 在 CATIA VBA IDE 中启用 `DEBUG_MODE = True`，观察 MsgBox 输出的 `sel.Count` 值
3. **注意：** CATIA R33 中任何 `MsgBox` 弹出后会清空当前 Selection，这会影响调试结果

---

### 症状：点击"停止"按钮后宏未退出

**可能原因：** `btnStop_Click` 中的 `SendEsc` 触发时序问题

**解决方法：**
1. 点击停止后等待 1-2 秒，宏通常会在下一次循环检查时退出
2. 如宏完全卡死，可在 CATIA VBA IDE 中按 Ctrl+Break 强制中断

---

## PLM 同步问题

### 症状：同步时报错"PLM-06"或 500 NPE

**原因：** PLM 服务端 `GET /parts/{pn}-{ver}` 接口存在全局 NPE bug。

**影响：** 零件的最新版本查询可能返回 500 错误。

**程序行为：** 程序已针对此 bug 做了防御处理（跳过而非抛异常），同步会继续进行，受影响的零件会在日志中标注为跳过。

---

### 症状：同步时"FORCE_UNDO"策略选项显示灰色

**原因：** PLM 服务端不支持撤销他人签出，且 `iter=1` 时无法撤销签出（PLM-07）。

**行为：** 该策略已在 UI 中灰显，选择后退化为 SKIP（跳过已存在零件）。

---

## 打包/运行问题

### 症状：打包后程序启动报错找不到资源文件

**检查项：**
1. 确认 `build.spec` 中的 `datas` 列表包含了所需的资源文件
2. 确认 `ISO.xml`、`ChangFangSong.ttf`、`drawing_templates/` 等目录存在于源码根目录

---

### 症状：打开文件/文件夹在中文路径下乱码

**已修复版本：** 1.8.0+

**原因：** 旧版使用 `subprocess` + PowerShell 调用 Explorer，在 OEM 代码页下中文路径乱码。

**修复：** 改用 `ShellExecuteW`（Unicode 宽字符 API）。

如仍有问题，请确认使用的是 1.8.0 及以上版本。

---

## 日志查看

菜单「帮助 → 日志窗口」可查看实时操作记录与错误详情。

日志文件位置：
```
%LOCALAPPDATA%\CATIA Copilot\logs\catia_copilot.log
```

报告问题时请附上相关日志片段。
