# BOM 数据模型 V3 设计文档

## 一、背景与目标

V2 对话框（`bom_edit_dialog_v2.py`）以 `id(product)` 为 key，每个实例独立存一份
属性缓存（`_canonical_data`），同文件多实例属性靠 `_sync_siblings_in_ui()` 手动同步，
逻辑复杂且容易遗漏。

V3 目标：**part_master / instance 分离**，PartMaster 级属性只存一份，
装配结构和实例名以 `instances` 列表的形式存在 part_master 内部。

---

## 二、核心设计决策（基于实验验证）

### 2.1 `product.ReferenceProduct.Products` 是文件视角

**实验结论**（已在 CATIA 中验证）：

```
Product2
├── Product3.1
│   ├── Part1.1
│   └── Part1.2
└── Product3.2
    ├── Part1.1   ← 与 Product3.1/Part1.1 是同一个 COM 对象（底层相同）
    └── Part1.2
```

通过 `Product3.1.ReferenceProduct.Products` 和
`Product3.2.ReferenceProduct.Products` 导航到的 `Part1.x`，
Python `id()` 不同（每次调用返回新的 wrapper 对象），
但底层 COM 指针相同——修改任意一个的 `Name`，另一个立即同步。

**推论**：
- `part_masters["Product3"]["instances"]` 只需存**一份**（通过任意 Product3.x 导航收集）
- 修改 `inst_info["instance_name"]` 后写入 CATIA，Product3.2 下的对应实例自动同步
- 程序端不需要"兄弟同步"逻辑

### 2.2 PartNumber 是 part_master 的唯一标识

- 不用 `filepath`（未保存零件无完整路径）
- 不用 `id(product.ReferenceProduct)`（PyIUnknown 每次调用返回新包装，`id()` 不稳定）
- `pn` 字符串是唯一可靠的 part_master key

### 2.3 根节点没有实例名

根产品（level=0）是一个 part_master，不是任何父节点的子实例，
因此没有 `inst_info`，`instance_name` 为空字符串。

---

## 三、数据结构

### 3.1 part_master

```python
part_master: dict = {
    # ── 唯一标识 ──────────────────────────────────────────────────────────
    "part_number":  str,          # 唯一 key，修改时同步 instances 里所有引用

    # ── PartMaster 级可写属性（绑定到文件，所有实例共享）───────────────────
    "nomenclature": str,
    "revision":     str,
    "definition":   str,
    "source":       str,          # CATIA 原始值："0"（未知）/"1"（自制）/"2"（外购）
    "description":  str,
    # + 用户自定义列（直接作为 dict key，key 为原始列名，如 "物料编码"）

    # ── 只读属性（派生，不可通过 BOM 工作台写回）────────────────────────────
    "type":         str,          # BomNodeType 英文 key："Part"/"Product"/"Component"
    "filename":     str,          # 文件名（含扩展名，不含路径）
    "filepath":     str,          # 文件完整路径（只读；未保存零件可能为空）
    "_not_found":   bool,         # 文件未被 CATIA 检索到
    "_no_file":     bool,         # 从未保存到磁盘
    "_unreadable":  bool,         # 处于轻量化模式，无法读取属性

    # ── 装配结构（该 part_master 内部的直接子实例，文件视角，唯一一份）────────
    "instances": [
        {
            "inst_key":      int,        # id(product)，任取一个 Python wrapper 的 id
            "pn":            str,        # 子 part_master 的 part_number
            "instance_name": str,        # product.Name，实例名唯一真相
            "product":       object,     # COM 实例引用（防 GC、写回实例名用）
            "placement":     list|None,  # 4×4 变换矩阵（mass props 用）
        },
        # ...
    ]
}
```

### 3.2 顶层数据

```python
root_pn:          str              # 根产品的 PartNumber（part_masters 入口 key）
part_masters:     dict[str, dict]  # pn → part_master dict
inst_key_to_info: dict[int, dict]  # id(product) → inst_info（O(1) 反向索引）
```

`inst_key_to_info` 中每个 value 是 `part_masters[parent_pn]["instances"][i]`
的**同一对象引用**——修改 `inst_info["instance_name"]` 即同步修改 `part_masters` 树。

### 3.3 与 V2 的对比

| 维度 | V2 `_canonical_data[inst_key]` | V3 `part_masters[pn]` |
|------|-------------------------------|----------------------|
| key 类型 | `int`（id(product)） | `str`（PartNumber） |
| 同文件多实例 | 每实例一条，需手动同步 | 共享一条，天然同步 |
| 装配结构 | 无（靠 `_parent_product` 关联） | `instances` 列表，文件视角 |
| 实例名存储 | 混在 `_canonical_data` 里 | `inst_info["instance_name"]`，唯一真相 |
| 兄弟同步 | `_sync_siblings_in_ui()`，复杂 | 无需，文件视角天然同步 |

---

## 四、`collect_bom_part_masters` 的遍历策略

```
_traverse(product, level, parent_filepath):
    1. 读 pn
    2. 若 pn 已在 part_masters 中 → 直接返回 pn（不重复遍历子节点）
    3. 读属性，建 part_master（含空 instances 列表）
    4. 若是装配体：通过 product.ReferenceProduct.Products 遍历子节点
       - 对每个子节点递归调用 _traverse(child, level+1, filepath)，得到 child_pn
       - 建 inst_info 并 append 到 part_masters[pn]["instances"]
       - 同时注册到 inst_key_to_info（O(1) 反向索引）
    5. 返回 pn

collect_bom_part_masters 调用 _traverse(root_product, 0, "")
返回 (root_pn, part_masters, inst_key_to_info)
```

**关键**：同一 PN 只遍历一次。第二次遇到 Product3（通过 Product3.2 路径到达时）
直接返回，因为 `part_masters["Product3"]` 已存在（含完整 instances）。

---

## 五、视图生成

### 5.1 完整 BOM（iter_full_rows）

```
输出根节点行（level=0，instance_name=""，inst_key=None）
遍历 part_masters[root_pn]["instances"]：
    对每个 inst_info：
        输出行（level=1，instance_name=inst_info["instance_name"]）
        递归 part_masters[inst_info["pn"]]["instances"]：
            对每个子 inst_info：
                输出行（level=2，...）
                ...（继续递归）
```

注意：完整 BOM 中 Product3.1 和 Product3.2 都会出现，
它们的子节点（Part1.1、Part1.2）来自同一份 `part_masters["Product3"]["instances"]`，
行数据相同（inst_key、instance_name 都一样），level 不同。

### 5.2 层级 BOM（iter_hierarchical_rows）

对每一层的 instances 按 PN 分组，同 PN 合并为一行，Quantity = 同 PN 实例数。
代表行取 instances 中第一个同 PN 的 inst_info。
递归只进入代表 inst_info 对应的 part_master 的 instances。

### 5.3 汇总 BOM

复用 `bom_collect.flatten_bom_to_summary()`，从层级 BOM 行列表派生。
注意：`flatten_bom_to_summary` 输出不含 `_inst_key` 等 V3 内部字段，
需要从层级行按 PN 回填后再使用。

---

## 六、实例名修改

### 6.1 单格编辑（_handle_instance_name_changed）

1. 写 CATIA：`product.Name = new_value`
2. 更新唯一真相：`inst_info["instance_name"] = new_value`
3. 同父唯一性检查：遍历 `part_masters[parent_pn]["instances"]`，
   找同 PN 的其他实例，检查是否重名
4. 同步界面：`part_masters[parent_pn]["instances"]` 中其他同 PN 实例
   的 `instance_name` 也更新（同一 COM 对象，CATIA 端已自动同步），
   并刷新对应 QTreeWidgetItem
5. 推入撤销栈（key = inst_key: int，区别于 PartMaster 属性的 str key）

### 6.2 批量改名（_auto_rename_instance_names）

直接遍历 `part_masters[target_pn]["instances"]`，
按 PartNumber.n 规则批量写入 CATIA 并更新 `inst_info["instance_name"]`。
不需要兄弟同步（instances 是唯一一份，所有引用自动同步）。

---

## 七、PN 改名（rename_part_master）

1. `part_masters.pop(old_pn)` → 修改 `pm["part_number"]` → `part_masters[new_pn] = pm`
2. 遍历所有 part_master 的 instances，将 `inst["pn"] == old_pn` 的改为 `new_pn`
3. 迁移 `pn_to_inst_keys`：`pn_to_inst_keys[new_pn] = pn_to_inst_keys.pop(old_pn)`
4. 调用方负责更新 `inst_key_to_info` 中对应 inst_info 的 `"pn"` 字段

---

## 八、撤销栈 key 类型约定

| key 类型 | 含义 | 对应属性 |
|---------|------|---------|
| `str` (pn) | PartMaster 属性 | Nomenclature、Revision、Definition、Source、Description、自定义列 |
| `int` (inst_key) | 实例属性 | instance_name |

PN 改名也用 `str` key（key = old_pn），`_apply_field_changes` 按 `isinstance(key, str)` 分发。

---

## 九、不需要的字段/逻辑（相比 V2）

| 删除 | 原因 |
|------|------|
| `_canonical_data` | 完全由 `part_masters` 替代 |
| `_ref_to_insts` / `_inst_to_ref_unk` | 由 `pn_to_inst_keys` 替代 |
| `_sync_siblings_in_ui()` | instances 是唯一一份，天然同步 |
| `_parent_product` 字段 | 父子关系通过 `inst_info["pn"]` → `part_masters` 树表达 |
| `full_rows` / `hierarchical_rows` 缓存 | 由 `iter_full_rows` / `iter_hierarchical_rows` 按需生成 |
| `build_hierarchical_rows_v3` | 由 `iter_hierarchical_rows` 替代 |
| 兄弟同步的槽位匹配逻辑 | 不再需要，文件视角天然同步 |

---

## 十、相关文件

| 文件 | 说明 |
|------|------|
| `catia_copilot/catia/bom_collect_v3.py` | V3 数据收集，`collect_bom_part_masters` + 视图生成函数 |
| `catia_copilot/ui/bom_edit_dialog_v3.py` | V3 对话框，使用 `_part_masters` + `_inst_key_to_info` |
| `catia_copilot/ui/bom_edit_dialog_v2.py` | V2（保留，与 V3 并行运行对比测试） |
| `catia_copilot/catia/bom_collect.py` | V1/V2 收集（保留，V3 仅复用 `flatten_bom_to_summary`） |

---

*文档版本：v2.0，2026-06-07*
*对应代码版本：CATIA Copilot v2.1.0，分支 feat/bom-v3-part-master*
