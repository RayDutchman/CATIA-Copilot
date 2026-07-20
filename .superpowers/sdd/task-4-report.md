# Task 4 完成报告 — CATIA 属性读写模块 property_rw

**日期**: 2026-07-20
**提交**: `d2e1079` — feat: 新增 CATIA 属性读写模块 property_rw

## 产出文件

| 文件 | 操作 |
|------|------|
| `catia_copilot/catia/property_rw.py` | 新建，141 行 |

## 公共接口

| 函数 | 签名 | 说明 |
|------|------|------|
| `read_properties` | `(path, product_doc=None) -> dict[str, str] \| None` | 读取指定路径实例的全部属性（内置 + 用户自定义） |
| `write_property` | `(path, product_doc, prop_name, value) -> bool` | 写入单个属性，自动判断内置/用户属性 |

## 内部函数

| 函数 | 说明 |
|------|------|
| `_resolve_product_by_path` | 按路径字符串定位 CATIA COM 产品实例，处理 0-based → 1-based 索引转换 |

## 路径格式

- 根节点: `"0"`
- 一级子节点: `"0.0"`, `"0.1"`
- 支持任意深度: `"0.1.2"` 等

## 依赖

- `catia_copilot.catia.connection.get_catia_v5_application()`
- `catia_copilot.constants.PRODUCT_ATTR_READ_MAP`
- `catia_copilot.constants.PRODUCT_ATTR_WRITE_MAP`

## 验证

```powershell
python -c "from catia_copilot.catia.property_rw import read_properties, write_property; print('OK')"
# → OK
```

## 规范检查

- [x] 中文注释
- [x] snake_case 函数命名
- [x] 无新增外部依赖
- [x] 遵循现有代码风格（参照 assembly_reader.py）
