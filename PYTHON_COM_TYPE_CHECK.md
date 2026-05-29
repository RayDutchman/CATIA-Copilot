# Python COM 文档类型检测方法说明

## 问题

在 Python 中使用 `win32com` 访问 CATIA COM 对象时，`type(doc).__name__` 返回的是 `CDispatch`（动态 dispatch 对象），而不是具体的文档类型名（如 `PartDocument`、`DrawingDocument`）。

## VBScript vs Python

### VBScript（宏中的方法）
```vbscript
If TypeName(CATIA.ActiveDocument) = "PartDocument" Then
    ' 是零件文档
End If
```

### Python（错误方法）
```python
# ❌ 错误：返回 "CDispatch"
doc_type = type(doc).__name__
```

### Python（正确方法）
```python
# ✅ 正确：通过检查对象属性判断类型
def get_document_type(doc) -> str:
    """返回 "PartDocument" | "ProductDocument" | "DrawingDocument" | "Unknown" """
    
    # 检查 Product 属性（零件和装配体都有）
    if hasattr(doc, 'Product'):
        # 零件有 Part 属性
        try:
            _ = doc.Part
            return "PartDocument"
        except:
            pass
        # 装配体有 Products 集合
        try:
            _ = doc.Product.Products
            return "ProductDocument"
        except:
            return "PartDocument"
    
    # 检查 Sheets 属性（图纸特有）
    if hasattr(doc, 'Sheets'):
        return "DrawingDocument"
        
    return "Unknown"
```

## 实现原理

CATIA COM 对象的类型可以通过其特有的属性/方法来判断：

| 文档类型 | 特有属性/方法 | 判断逻辑 |
|---------|--------------|---------|
| **PartDocument** | `Part`, `Product` | 有 `Product` 且有 `Part` |
| **ProductDocument** | `Product.Products` | 有 `Product.Products` 集合 |
| **DrawingDocument** | `Sheets`, `DrawingRoot` | 有 `Sheets` 或 `DrawingRoot` |

## 项目中的应用

### 修改前（test_drawing_com_operations.py）
```python
doc_type = type(active_doc).__name__  # 返回 "CDispatch"
if doc_type == "PartDocument":        # 永远为 False
    # ...
```

### 修改后
```python
doc_type = self.get_document_type(active_doc)  # 返回 "PartDocument"
if doc_type == "PartDocument":                  # 正确判断
    # ...
```

## 参考

项目中其他模块（如 `bom_collect.py`）不需要显式检查文档类型，因为它们直接尝试访问特定属性（如 `doc.Product`），通过异常处理来判断是否支持该操作。

但在验证测试和正式实现中，显式的类型检查可以提供更清晰的错误信息和更好的用户体验。
