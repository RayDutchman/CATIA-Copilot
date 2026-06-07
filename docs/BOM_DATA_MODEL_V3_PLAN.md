# BOM 数据模型 V3 升级计划

## 背景

当前 V2 对话框（`bom_edit_dialog_v2.py`）使用扁平 row dict 列表作为数据层，以
`id(product)`（inst_key）为 key 存储属性缓存（`_canonical_data`）。
这导致若干结构性问题，本文档描述从"以实例为中心"升级为"part_master / instance 分离"
的完整方案。

---

## 一、现有模型的问题

### 1.1 同文件多实例属性不一致

`_canonical_data[inst_key]` 对同一零件文件的每个实例分别存一份属性（PartNumber、
Nomenclature 等），而这些属性在 CATIA 端绑定到文件，所有实例天然共享。
当前解决方式是维护 `_ref_to_insts` + `_inst_to_ref_unk` + `_sync_siblings_in_ui()`
做跨实例同步，逻辑复杂且容易遗漏。

### 1.2 实例名同步需要复杂逻辑

修改 Product2.1 下 Part1.2 的实例名时，CATIA 端会自动同步引用同一 Product2 文件
的 Product2.2 下的对应实例。但程序端需要手动找"父节点 PN 相同的兄弟父节点"并逐行
比对，代码复杂且难以维护。

在新架构下，单格编辑实例名后，程序端通过 `_pn_to_inst_keys[parent_pn]` 找到所有同
父 PartMaster 的父实例，再找其下同 `child_pn` 的子实例，直接更新界面——无需任何
COM 调用，也无需重建整张表格。

### 1.3 撤销栈两套 key 并存

PartMaster 属性走 `inst_key (int)`，实例名属性也走 `inst_key (int)`，但语义不同：
前者一个 key 对应多行（同文件多实例），后者一个 key 只对应一行。

### 1.4 `_hierarchical_range` 用 `id(parent)` 分组

`build_hierarchical_rows` 按 `(id(parent_product), pn)` 分组，`id()` 是 Python
内存地址，在不同 `collect_bom_rows` 调用之间不保证稳定（虽然单次会话内稳定）。

---

## 二、新数据模型：part_master / instance 分离

参考 PLM 系统（DocDokuPLM）的设计：

> **PartMaster** 是"什么"，**PartUsageLink** 是"谁用了谁"，**CADInstance** 是"放在哪里"。

### 2.1 `part_master` 数据结构

一条 `part_master` 代表一个零件文件（`.CATPart` / `.CATProduct` / 嵌入 Component）。
唯一标识：`part_number`（字符串）。

```python
part_master: dict = {
    # ── 唯一标识 ──────────────────────────────────────────────────────────
    "part_number":  str,          # 唯一 key，修改时同步所有引用

    # ── 可写属性（part_master 级，所有实例共享，直接映射 CATIA ReferenceProduct 属性）
    "nomenclature": str,          # 术语（中文名称）
    "revision":     str,          # 版本
    "definition":   str,          # 定义
    "source":       str,          # 源（制造 / 购买 等）
    "description":  str,          # 描述
    # + 用户自定义列，如：
    # "零件类型":   str,
    # "设计状态":   str,
    # "材料":       str,
    # ...

    # ── 只读/派生属性 ──────────────────────────────────────────────────────
    "type":         str,          # "零件" / "产品" / "部件"（由文件类型决定，part_master 级）
    "filename":     str,          # 文件名（含扩展名，不含路径）
    "filepath":     str,          # 文件完整路径（只读；未保存零件为空或形如 Part1.CATPart）

    # ── 子件列表（装配结构，有序）────────────────────────────────────────
    # 按 CATIA 产品树 Products.Item(i) 顺序排列，严格保持 CATIA 顺序
    "children": [
        {
            "child_pn": str,      # 子 part_master 的 part_number
            "instances": [        # 该子件在本装配中的所有实例（有序，按遍历顺序）
                {
                    "inst_key":      int,        # id(product)，写回用，session 内唯一
                    "instance_name": str,        # product.Name，实例级，每实例独有
                    "placement":     list|None,  # 4×4 变换矩阵（mass props 用，可为 None）
                    # 父子关系由 children 列表结构隐含，不再需要 _parent_product
                },
                # 同一子件在本装配中出现 N 次，就有 N 个 instance
            ]
        },
        # ... 其他子件（保持 CATIA 中的顺序）
    ]
}
```

### 2.2 关键设计决策

| 决策 | 说明 |
|------|------|
| `part_number` 作唯一 key | 不用 `id(product.ReferenceProduct)`（PyIUnknown 不可哈希、每次调用新对象） |
| `filepath` 存在 `part_master` 中 | 存储但不作为 key，永远靠 `part_number` 区分 part_master |
| `children` 有序 | 严格保持 CATIA 产品树的 `Products.Item(i)` 顺序 |
| `placement` 属于 instance | 不同位置的同一零件各有不同 mat4，是实例级属性 |
| `type` 属于 `part_master` | 同一零件文件的所有实例类型相同，由文件类型决定 |
| `instance_name` 属于 instance | `product.Name` 是每个实例在父装配中的独有名称 |
| 无 `_canonical_data` | `part_master` 完全替代，PartMaster 级属性只存一份，天然共享 |

### 2.3 `_canonical_data` 的命运

**`_canonical_data` 在新架构中消失。** 其职责完全由 `_part_masters` 承担：

- 现在：`_canonical_data[inst_key]["Nomenclature"]` → 每实例一条，重复存储
- 新方案：`_part_masters[pn]["nomenclature"]` → 每 PartMaster 一条，天然共享

---

## 三、新程序侧数据结构

### 3.1 核心存储

```python
# PartMaster 字典：part_number → part_master dict（含 children）
_part_masters: dict[str, dict]

# 实例快速索引（session 内，不持久化，在 _populate_table 时重建）
_inst_to_product:  dict[int, COM]        # id(product) → COM 对象，写回用（保留）
_inst_to_items:    dict[int, list]       # id(product) → QTreeWidgetItem 列表（保留）
_pn_to_inst_keys:  dict[str, list[int]] # part_number → [inst_key, ...]
                                         # 替代 _ref_to_insts，语义更清晰

# 显示层（保留）
_rows:             list[dict]            # 显示层扁平行列表，由 _full_rows 派生
_full_rows:        list[dict]            # 完整 BOM 每实例一行（collect_bom_rows 返回）
_hierarchical_rows: list[dict]           # 层级 BOM（build_hierarchical_rows 派生）
```

### 3.2 `_part_masters` 对比现有 `_canonical_data`

| 维度 | 现有 `_canonical_data[inst_key]` | 新 `_part_masters[pn]` |
|------|----------------------------------|----------------------|
| key 类型 | `int`（id(product)，易混淆） | `str`（PartNumber，语义清晰） |
| 同文件多实例 | 每实例一条（重复数据，需手动同步） | 共享一条（PartMaster 级，天然共享） |
| 是否含 Instance Name | 是（不合理，实例级属性混入） | 否（实例级，存 instance） |
| 是否含结构性列 | 是（Level/Type/Filename/Quantity） | 否（只含可写属性 + 只读属性） |
| 装配关系 | 无（靠 `_parent_product` 的 `is` 比较） | 显式 `children` 列表 |
| 是否含 filepath | 是（每实例存一次） | 是（part_master 级存一次） |

---

## 四、可删除的代码

新方案实施后，以下代码可以完全删除：

| 代码 | 原因 |
|------|------|
| `_canonical_data` | 完全由 `_part_masters` 替代 |
| `_ref_to_insts: dict[str, list[int]]` | 由 `_pn_to_inst_keys` 替代（同等功能，更清晰命名） |
| `_inst_to_ref_unk: dict[int, str]` | 不再需要"从 inst_key 反查 PN"（直接从 `_rows[i]["Part Number"]` 读） |
| `_sync_siblings_in_ui()` | 整个函数删除，不再有"兄弟同步"概念 |
| `_apply_field_changes` 中的 `_sync_siblings_in_ui` 调用 | 同上 |
| `_on_item_changed` 末尾的 `_ref_to_insts` key 迁移逻辑 | 简化为 `_rename_part_master(old_pn, new_pn)` |
| PN 冲突检查中的 `same_pn_insts` 逻辑 | 简化为 `new_pn in _part_masters` |
| `_handle_instance_name_changed` 中的兄弟同步代码 | 通过 `_pn_to_inst_keys` 直接查找，无需复杂匹配 |
| `_auto_rename_instance_names` 中的兄弟同步代码 | 同上 |
| `build_hierarchical_rows` 中 `(id(parent), pn)` 分组 | 改为 `(parent_pn, pn)` 分组，更稳定 |

---

## 五、关键逻辑变化

### 5.1 加载 BOM（`_load_bom`）

```python
# 现在：
_full_rows = collect_bom_rows(...)
_canonical_data = {inst_key: {col: val} for each instance}  # 重复存储

# 新方案：
_full_rows = collect_bom_rows(...)   # 不变，仍是每实例一行，作为中间产物

# 从 _full_rows 构建 _part_masters 树
_part_masters = {}
for row in _full_rows:
    pn = row["Part Number"]
    if pn not in _part_masters:
        _part_masters[pn] = {
            "part_number":  pn,
            "nomenclature": row.get("Nomenclature", ""),
            "revision":     row.get("Revision", ""),
            "definition":   row.get("Definition", ""),
            "source":       SOURCE_TO_DISPLAY.get(row.get("Source", ""), ""),
            "description":  row.get("Description", ""),
            # + 自定义列...
            "type":         row.get("Type", ""),
            "filename":     row.get("Filename", ""),
            "filepath":     row.get("_filepath", ""),
            "children":     [],   # 在第二次遍历中填充
        }

# 第二次遍历填充 children（按 _full_rows 的顺序）
# ...（构建父子关系的逻辑）
```

### 5.2 修改属性（如 Nomenclature）

```python
# 现在：
_canonical_data[inst_key]["Nomenclature"] = new_val   # 只改一个实例
_sync_siblings_in_ui(insts_to_update, ...)             # 再手动同步其他实例

# 新方案：
pn = _rows[row_idx]["Part Number"]
_part_masters[pn]["nomenclature"] = new_val            # 改 part_master，天然共享
# 写任意一个实例，CATIA 自动同步（属性绑定到文件）
inst_key = next(k for k in _pn_to_inst_keys[pn] if _inst_to_product.get(k))
_write_cell_to_catia(inst_key, "Nomenclature", new_val)
# 更新所有同 PN 实例的界面（无需"兄弟同步"，直接遍历）
for ik in _pn_to_inst_keys[pn]:
    for tree_item in _inst_to_items.get(ik, []):
        tree_item.setText(col_idx, new_val)
```

### 5.3 修改 PartNumber

```python
# 现在（复杂）：
_canonical_data[inst_key]["Part Number"] = new_pn
old_list = _ref_to_insts.pop(old_pn, [])
_ref_to_insts[new_pn] = old_list
for k in old_list: _inst_to_ref_unk[k] = new_pn
# + 更新 _rows 中所有实例行

# 新方案（封装为 _rename_part_master）：
def _rename_part_master(self, old_pn: str, new_pn: str) -> None:
    pm = _part_masters.pop(old_pn)
    pm["part_number"] = new_pn
    _part_masters[new_pn] = pm
    _pn_to_inst_keys[new_pn] = _pn_to_inst_keys.pop(old_pn, [])
    # 更新所有引用了该 PartMaster 的父 PartMaster 的 children
    for other_pm in _part_masters.values():
        for child_entry in other_pm["children"]:
            if child_entry["child_pn"] == old_pn:
                child_entry["child_pn"] = new_pn
    # 更新 _rows 中的 "Part Number" 字段
    for row in _full_rows:
        if row.get("Part Number") == old_pn:
            row["Part Number"] = new_pn
```

### 5.4 PN 冲突检查

```python
# 现在（复杂）：
same_pn_insts = set(_ref_to_insts.get(old_pn, []))
for other_inst, data in _canonical_data.items():
    if other_inst == this_inst or other_inst in same_pn_insts:
        continue
    if data["Part Number"] == new_value:  # 冲突

# 新方案（一行）：
if new_pn in _part_masters and new_pn != old_pn:
    # 冲突
```

### 5.5 撤销栈 tuple 格式

```python
# PartMaster 属性（PN/Nomenclature 等）：key 是 str
(pn: str,       col_name: str, old_val: str, new_val: str)

# 实例属性（Instance Name）：key 是 int
(inst_key: int, col_name: str, old_val: str, new_val: str)

# _apply_field_changes 按 isinstance(key, str) 区分路径：
#   str  → _part_masters[pn][col] = val
#          写任意一个 inst_key（CATIA 自动同步）
#          更新 _pn_to_inst_keys[pn] 内所有实例的界面
#   int  → product.Name = val
#          更新 _rows 内存中对应行
#          更新当前格 + 同父 PartMaster 下同 child_pn 的所有实例格
```

### 5.6 实例名修改

```python
# 单格编辑（_handle_instance_name_changed）：
#   写入 CATIA（product.Name = new_value）
#   更新当前行内存
#   更新当前格界面
#   推入撤销栈（inst_key, BOM_INSTANCE_NAME_COLUMN, old, new）
#   同步界面：找所有"父 PartMaster PN 相同、子 PartMaster PN 相同"的其他实例
#             通过 _pn_to_inst_keys[parent_pn] 和 part_master["children"] 索引
#             直接更新对应 QTreeWidgetItem，无需 COM 调用，无需重建表格

# 批量改名（_auto_rename_instance_names）：
#   写入 CATIA（按规则批量 product.Name = new_name）
#   更新 _full_rows 内存
#   通过 _pn_to_inst_keys 找同 PartMaster 的其他父实例，
#   用相同规则更新其子树内存（不写 COM）
#   调用 _populate_table 重建表格
#   保存/恢复滚动位置
#   推入撤销栈
```

### 5.7 `build_hierarchical_rows` 分组 key

```python
# 现在：key = (id(parent_product), pn)   # id() 跨调用不稳定

# 新方案：key = (parent_pn, pn)           # 字符串，稳定可靠
# parent_pn 从行的 _parent_product 通过 _part_masters 反查，
# 或直接在 _full_rows 遍历时记录父行的 "Part Number"
```

---

## 六、`collect_bom_rows` 返回格式

当前 `collect_bom_rows` 返回扁平 row dict 列表，每行含 `_product`、`_parent_product`
等内部字段。**新方案保持此格式不变**作为中间产物，在 `_load_bom` 中消费时构建
`_part_masters` 树。

`bom_collect.py` 本身不需要修改，保持稳定。

---

## 七、`_rows` 的角色

`_rows` 作为显示层的扁平行列表继续存在：

```
显示模式        _rows 来源
──────────────  ─────────────────────────────────────────────────────
完整 BOM        _full_rows（每实例一行，直接来自 collect_bom_rows）
层级 BOM        build_hierarchical_rows(_full_rows)（PN 分组合并）
汇总 BOM        flatten_bom_to_summary(...)（按 PN 去重累加）
```

每行的 `"Part Number"` 字段作为 `_part_masters` 的查找 key 读取 PartMaster 级属性。
实例级属性（Instance Name）直接从行 dict 读取，不经过 `_part_masters`。

---

## 八、实施方式：新建文件

由于改动量大且涉及架构重构，采用**新建文件**方式实施：

| 新文件 | 对应现有文件 | 说明 |
|--------|------------|------|
| `catia_copilot/catia/bom_collect_v3.py` | `bom_collect.py` | 新增 `collect_bom_part_masters()` 函数，直接返回 `part_master` 树；保留 `collect_bom_rows` 作为内部中间产物 |
| `catia_copilot/catia/bom_write_v3.py` | `bom_write.py` | 适配 `part_master` 结构的写回函数 |
| `catia_copilot/ui/bom_edit_dialog_v3.py` | `bom_edit_dialog_v2.py` | 全新对话框，使用 `_part_masters` 替代 `_canonical_data` |

现有 V1/V2 文件**保持不变**，V3 与 V2 并行运行，通过 `main_window.py` 的
`_show_dialog` 切换入口来对比测试。

---

## 九、不需要修改的模块

| 模块 | 原因 |
|------|------|
| `catia_copilot/catia/bom_collect.py` | row dict 格式不变，继续作为中间产物 |
| `catia_copilot/catia/bom_write.py` | V2 继续使用，V3 有新版本 |
| `catia_copilot/ui/bom_edit_dialog.py`（V1） | V1 仍以 PN 为 key，不受影响 |
| `catia_copilot/ui/bom_edit_dialog_v2.py`（V2） | 保持运行，与 V3 并行 |
| `catia_copilot/ui/plm_workbench.py` | 不依赖内部数据结构 |

---

## 十、实施顺序

1. **新建 `bom_collect_v3.py`**：实现 `collect_bom_part_masters()`，返回
   `dict[str, part_master]` 树结构，内部仍调用现有 `collect_bom_rows` 作为中间产物。

2. **新建 `bom_edit_dialog_v3.py`**（骨架）：复制 V2，将 `_canonical_data` 替换为
   `_part_masters`，`_ref_to_insts`/`_inst_to_ref_unk` 替换为 `_pn_to_inst_keys`。

3. **逐步迁移各功能**（每步可独立验证）：
   - `_load_bom`：构建 `_part_masters`
   - `_populate_table`：从 `_part_masters[pn]` 读属性
   - `_on_item_changed` / `_on_source_changed` / `_on_option_col_changed`：写回路径
   - `_apply_field_changes`：撤销/重做路径（区分 `str` / `int` key）
   - PN 冲突检查、PN 改名逻辑
   - 实例名修改（单格 + 批量）
   - `build_hierarchical_rows_v3`：使用 `parent_pn` 分组

4. **删除 `_sync_siblings_in_ui` 及所有调用点**

5. **在 `main_window.py` 注册 V3 入口，与 V2 并行对比测试**

6. **V3 稳定后，将 V2 标记为废弃，V1 继续保留**

---

## 十一、风险点

| 风险 | 缓解措施 |
|------|---------|
| PN 相同但文件不同的零件（理论上 CATIA 不允许，实践中可能存在） | 加载时记录警告，首次出现者优先，后续实例复用同一 part_master |
| 嵌入 Component 的 PN 可能与父产品 PN 相同（罕见） | `type == "部件"` 可区分，或在 part_master 中加 `is_embedded: bool` |
| `_pn_to_inst_keys[pn]` 中取任意实例写回时，COM 引用可能已失效 | 遍历列表找第一个 `_inst_to_product.get(k) is not None` 的实例 |
| PN 修改时需同步所有引用该 PN 的父 part_master 的 children | 封装 `_rename_part_master(old_pn, new_pn)` 统一处理 |

---

*文档版本：v1.1，2026-06-06*  
*对应代码版本：CATIA Copilot v2.1.0*
