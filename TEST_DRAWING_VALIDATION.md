# 图纸操作 COM 调用验证测试

## 目的

在正式将 VBScript 宏改写为 Python 之前，验证所有核心 COM 调用在 Python 中的可行性。

## 测试文件

`test_drawing_com_operations.py` - 独立的验证脚本，不会修改现有代码。

## 测试覆盖范围

### ✅ 已覆盖的核心操作

| 测试项 | 对应 VBScript 操作 | 验证内容 |
|--------|-------------------|---------|
| 1. 连接 CATIA | `CATIA.Application` | `get_catia_v5_application()` |
| 2. 文档类型检查 | `TypeName(doc)` | `type(doc).__name__` |
| 3. 读取零件标准属性 | `Product.PartNumber/Nomenclature/Revision` | 属性访问 |
| 4. 读取用户自定义属性 | `UserRefProperties.Item(name).Value` | 属性读取 |
| 5. 创建用户自定义属性 | `UserRefProperties.CreateString(name, value)` | 属性创建 |
| 6. 遍历已打开文档 | `Documents.Count` / `Documents.Item(i)` | 集合遍历 |
| 7. 根据 PartNumber 查找零件 | 刷新图纸宏的核心逻辑 | 文档匹配 |
| 8. 图纸参数读写 | `Parameters.Item(name).Value` | 参数操作 |
| 9. 从模板创建新图纸 | `Documents.NewFrom(template)` | 图纸创建 |

## 使用方法

### 前置条件

1. 启动 CATIA V5 R28
2. 打开一个 CATPart 或 CATProduct 文件（用于测试零件属性读取）
3. （可选）准备一个 CATDrawing 模板文件（用于测试图纸创建）

### 基础测试（不创建图纸）

```bash
cd /mnt/d/CATIA_Related/CATIA-Copilot
python test_drawing_com_operations.py
```

### 完整测试（包含创建图纸）

```bash
python test_drawing_com_operations.py --template "D:\path\to\template.CATDrawing"
```

## 预期输出

### 成功示例

```
================================================================================
开始验证图纸操作核心 COM 调用
================================================================================
✅ 通过 | 连接 CATIA | 版本: CATIA V5R28
✅ 通过 | 文档类型检查 | 当前文档类型: PartDocument (Part=True, Product=False, Drawing=False)
✅ 通过 | 读取零件标准属性 | PartNumber=TEST001, Nomenclature=测试零件, Revision=A
✅ 通过 | 读取用户自定义属性 | 找到 2 个属性: 物料编码=MAT001, 材料=铝合金
✅ 通过 | 创建用户自定义属性 | 成功创建并验证: _TEST_DRAWING_VALIDATION_=测试值_12345
✅ 通过 | 遍历已打开文档 | 共 3 个文档: PartDocument: test.CATPart, ProductDocument: asm.CATProduct, DrawingDocument: drw.CATDrawing
✅ 通过 | 根据 PartNumber 查找零件 | 查找 PartNumber='TEST001': 找到
✅ 通过 | 图纸参数读写 | 找到 3 个参数: PartNumber=TEST001, Nomenclature=测试零件, Revision=A
✅ 通过 | 从模板创建新图纸 | 成功创建图纸，共 1 张图纸页
================================================================================
测试总结
================================================================================
总计: 9 项测试 | 通过: 9 | 失败: 0
================================================================================
🎉 所有测试通过！可以开始正式改写。
```

### 部分跳过示例（正常情况）

某些测试会根据当前文档类型自动跳过：

```
❌ 失败 | 读取零件标准属性 | 当前文档不是零件/装配体，跳过测试（类型: DrawingDocument）
❌ 失败 | 图纸参数读写 | 当前文档不是图纸，跳过测试（类型: PartDocument）
```

这是正常的，因为某些测试需要特定类型的文档。

## 测试场景建议

### 场景 1：验证零件属性操作（推荐先测试）

1. 打开一个 CATPart 文件
2. 运行基础测试
3. 预期通过：测试 1-7
4. 预期跳过：测试 8（图纸参数）

### 场景 2：验证图纸参数操作

1. 打开一个 CATDrawing 文件
2. 运行基础测试
3. 预期通过：测试 1, 2, 6, 8
4. 预期跳过：测试 3-5, 7（零件属性）

### 场景 3：验证图纸创建（完整测试）

1. 打开一个 CATPart 文件
2. 准备一个图纸模板（如 `drawing_templates/A3.CATDrawing`）
3. 运行完整测试：`python test_drawing_com_operations.py --template "path/to/template.CATDrawing"`
4. 预期通过：所有测试（1-9）

## 注意事项

### 1. 测试属性清理

测试会创建一个名为 `_TEST_DRAWING_VALIDATION_` 的临时属性。由于 CATIA COM API 可能不支持删除属性，请在测试后手动删除：

1. 在 CATIA 中打开测试的零件
2. 工具 → 属性 → 用户自定义属性
3. 删除 `_TEST_DRAWING_VALIDATION_` 属性

### 2. 测试图纸不保存

测试 9 创建的图纸会在验证后自动关闭且不保存，不会留下垃圾文件。

### 3. 权限问题

如果测试失败并提示权限错误，请确保：
- CATIA 和 Python 脚本使用相同的权限级别运行（都用普通用户或都用管理员）
- 推荐：都用普通用户权限运行

## 下一步

### 如果所有测试通过

可以开始正式改写：
1. 创建 `catia_copilot/catia/drawing_operations.py`
2. 实现 `generate_drawing()` 和 `refresh_drawing()` 函数
3. 修改 `main_window.py` 调用方式

### 如果有测试失败

1. 检查失败原因（日志中会显示详细错误信息）
2. 确认 CATIA 版本和环境配置
3. 根据失败的测试项调整实现方案

## 测试结果记录

请在运行测试后记录结果：

- **测试日期**：____________________
- **CATIA 版本**：____________________
- **测试文档类型**：____________________
- **通过测试数**：______ / 9
- **失败测试**：____________________
- **备注**：____________________
