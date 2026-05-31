# TODO: AI Tools Schema 修复与包装函数更新

> 状态：已完成（2026-05-30）  
> 来源：2026-05-30 对 `catia_copilot/ai/tools.py` 的全量核查  
> 相关文件：`catia_copilot/ai/tools.py`

---

## 一、tools_schema 错误修复（需要改）

以下问题会导致 LLM 传入合法参数时 JSON Schema 验证失败，或 AI 对工具行为产生误解。

### 1. `collect_bom` — `file_path` 类型错误

**问题**：`file_path` 的 JSON Schema 类型为 `"string"`，但包装函数和底层 `collect_bom_rows` 均接受 `None`（表示使用当前活动文档）。LLM 传 `null` 时 schema 验证失败。

**修复**：
```json
"file_path": {
  "type": ["string", "null"],
  "description": "CATProduct 文件路径，传 null 使用当前活动文档"
}
```

---

### 2. `export_bom_to_excel` — `file_paths` 元素类型错误

**问题**：`file_paths.items` 类型为 `"string"`，但底层函数接受 `list[str | None]`，`null` 元素表示使用当前活动文档。

**修复**：
```json
"file_paths": {
  "type": "array",
  "items": { "type": ["string", "null"] },
  "description": "CATProduct 文件路径列表（传 [null] 使用当前活动文档）"
}
```

---

### 3. `write_bom_to_catia` — `file_path` 类型错误

**问题**：同 #1，`file_path` 应允许 `null`。

**修复**：
```json
"file_path": {
  "type": ["string", "null"],
  "description": "CATProduct 文件路径，传 null 使用当前活动文档"
}
```

---

### 4. `collect_mass_props` — `file_path` 类型错误

**问题**：同 #1，`file_path` 应允许 `null`。

**修复**：
```json
"file_path": {
  "type": ["string", "null"],
  "description": "CATProduct 文件路径，传 null 使用当前活动文档"
}
```

---

### 5. `generate_drawing` — description 不准确

**问题**：schema 描述未说明"不提供 `property_values` 时保留 CATIA 中已有的属性值"，可能导致 AI 误以为必须提供所有属性值才能调用。

**修复**：在 `property_values` 的 description 中补充：
```
不提供或留空时，保留 CATIA 文档中已有的属性值，不做修改。
```

---

## 二、包装函数功能增强（建议做）

以下是对现有包装函数的功能扩展建议，优先级从高到低排列。

### A. `tool_export_bom_to_excel` — 透传汇总参数

**现状**：`summary_include_assemblies` 和 `summary_sort_column` 两个参数被硬编码为默认值，AI 无法控制汇总 BOM 的行为。

**建议**：在包装函数和 schema 中暴露这两个参数：
- `summary_include_assemblies: bool = false` — 汇总模式是否包含装配体行
- `summary_sort_column: string | null = null` — 汇总模式的排序列名

---

### B. `tool_collect_bom` — 增加 `summarize` 参数

**现状**：`collect_bom` 只返回层级 BOM，AI 如果需要汇总结果，只能拿到原始数据后自行处理（但 AI 无法调用 Python 函数）。

**建议**：增加 `summarize: bool = false` 参数，为 `true` 时内部调用 `flatten_bom_to_summary`，直接返回去重汇总结果。同步增加 `include_assemblies` 和 `sort_column` 参数。

---

### C. 新增 `tool_find_part_for_drawing` / `tool_find_drawing_for_part`

**现状**：`dependencies.py` 中的 `find_part_for_drawing` 和 `find_drawing_for_part` 完全未包装，AI 无法使用。

**建议**：新增两个工具：

```python
def tool_find_part_for_drawing(
    drawing_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    progress_signal=None,
) -> str
# 返回: {"matches": list[str]}

def tool_find_drawing_for_part(
    part_path: str,
    strategies: list[str] | None = None,
    max_parent_levels: int = 2,
    progress_signal=None,
) -> str
# 返回: {"matches": list[str]}
```

`strategies` 可选值（来自 `constants.py`）：
- 图纸→零件：`pn_param_open_docs`, `pn_param_scan_dirs`, `same_name_scan_dirs`, `strip_prefix_scan_dirs`, `doc_file_links`
- 零件→图纸：`pn_param_open_drws`, `pn_param_scan_drws`, `same_name_scan_dirs`, `strip_prefix_scan_dirs`, `doc_file_links`

---

### D. `tool_collect_mass_props` — 增加 `summary_only` 参数

**现状**：返回产品树所有节点的完整质量特性数据，包含大量数值字段（Ixx/Iyy/Izz 等），对 AI 来说 token 消耗大，且大多数情况下只需要总重量和重心。

**建议**：增加 `summary_only: bool = false` 参数，为 `true` 时只返回根节点（Level=0）的汇总质量特性：

```json
{
  "total_weight_kg": 12.34,
  "cog_x_m": 0.123,
  "cog_y_m": 0.456,
  "cog_z_m": 0.789,
  "Ixx": ..., "Iyy": ..., "Izz": ...
}
```

---

### E. 新增 `tool_get_open_documents`

**现状**：没有工具能告诉 AI 当前 CATIA 里打开了哪些文件、活动文档是什么。`diagnose_catia_connection` 返回的 `active_doc` 只有文件名，不含完整路径，AI 无法用它作为其他工具的 `file_path` 参数。

**建议**：新增工具，返回所有已打开文档的完整路径列表和当前活动文档路径：

```python
def tool_get_open_documents(**_kwargs) -> str
# 返回:
# {
#   "active_document": "D:\\project\\part.CATPart" | null,
#   "open_documents": [
#     {"name": "part.CATPart", "path": "D:\\project\\part.CATPart", "type": "PartDocument"},
#     ...
#   ]
# }
```

---

### F. 新增 `tool_save_catia_document`

**现状**：`write_bom_to_catia` 写回属性后不自动保存，AI 目前无法触发保存操作，需要用户手动在 CATIA 里保存。

**建议**：新增工具，包装 `document.Save()`：

```python
def tool_save_catia_document(
    file_path: str | None = None,  # None = 保存当前活动文档
    **_kwargs,
) -> str
# 返回: {"success": True, "saved_path": str} 或 {"error": str}
```

---

## 三、优先级建议

| 优先级 | 项目 | 理由 |
|--------|------|------|
| P0（必须修） | schema 错误 #1~#4 | null 参数是常用场景，不修会导致工具调用失败 |
| P0（必须修） | schema 错误 #5 | 影响 AI 正确理解工具行为 |
| P1（建议做） | E. `get_open_documents` | AI 最常见的第一步就是"看看现在打开了什么"，没有这个工具 AI 很难自主工作 |
| P1（建议做） | F. `save_catia_document` | 写回属性后必须保存，否则操作不完整 |
| P2 | C. `find_part_for_drawing` / `find_drawing_for_part` | 图纸与零件互查是常用操作 |
| P2 | B. `collect_bom` 增加 `summarize` | 减少 AI 处理数据的负担 |
| P3 | A. `export_bom_to_excel` 透传汇总参数 | 细节完善 |
| P3 | D. `collect_mass_props` 增加 `summary_only` | 减少 token 消耗 |
