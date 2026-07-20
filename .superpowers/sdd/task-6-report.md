# Task 6 报告：装配树扁平化与属性同步工具

## 状态：已完成

## 创建的文件

### 1. `catia_copilot/ui/flatten_tree.py`
- 从 myPDM 前端 `flattenTree.ts` 移植
- 同父节点下同件号实例合并为一行，用量累加，所有变换矩阵保留
- 件号为空的节点不参与合并
- 包含辅助函数 `build_path_indices`

### 2. `catia_copilot/ui/sync_rows.py`
- 从 myPDM 前端 `syncRows.ts` 移植
- 按 PartNumber 同步同零部件所有实例行的属性更新
- 自动判断属性属于 `builtin` 还是 `user_properties`
- 不修改原始数据，返回新列表

## 验证

```powershell
python -c "from catia_copilot.ui.flatten_tree import flatten_tree; from catia_copilot.ui.sync_rows import sync_rows_by_part_number; print('OK')"
# → OK
```

## 约束检查
- [x] 中文注释
- [x] snake_case 命名
- [x] 无外部依赖（仅使用标准库 typing）
