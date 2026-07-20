# Task 3 报告：CATIA 装配树递归读取模块 assembly_reader

## 状态：已完成

## 文件
- `catia_copilot/catia/assembly_reader.py` — 新文件，160 行

## 公开接口

| 函数 | 说明 |
|------|------|
| `detect_catia_status()` | 检测 CATIA 运行状态与活动文档，返回 dict |
| `read_assembly_tree(catia_app=None)` | 递归读取装配体产品结构树，返回嵌套 dict 或 None |

## 返回数据结构

### detect_catia_status()
```python
{
    "active": bool,       # CATIA 是否正在运行
    "has_document": bool, # 是否有活动文档
    "doc_name": str,      # 文档名（如 "Product1.CATProduct"）
    "doc_type": str,      # 文档类型（PartDocument / ProductDocument / DrawingDocument / ""）
    "doc_path": str,      # 文档完整路径
}
```

### read_assembly_tree()
```python
{
    "instance_name": str,        # CATIA 实例名（product.Name）
    "part_number": str,          # 零件编号
    "path": str,                 # 树路径（如 "0.1.0"，0-based）
    "is_assembly": bool,         # 是否为装配体节点
    "doc_path": str,             # 源文件完整路径
    "builtin": dict[str, str],   # 内置属性（Part Number、Nomenclature 等）
    "user_properties": dict[str, str], # 用户自定义属性
    "matrix": list[float] | None,     # 3x4 变换矩阵（12 个浮点数）或 None
    "children": list[dict],      # 子节点列表（递归结构）
}
```

## 依赖关系
- `catia_copilot.catia.connection.get_catia_v5_application()`
- `catia_copilot.catia.document.get_bom_node_type(product, parent_filepath, filepath=None)`
- `catia_copilot.constants.PRODUCT_ATTR_READ_MAP`
- `catia_copilot.constants.BomNodeType`

## 验证
```
python -c "from catia_copilot.catia.assembly_reader import detect_catia_status, read_assembly_tree; print('OK')"
→ OK
```
