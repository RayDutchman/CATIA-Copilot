# API 参考

本文档说明 CATIA Copilot 各核心模块的公开接口。

---

## `catia_copilot.catia.bom_collect`

### `collect_bom(product, settings) -> list[dict]`

递归采集 CATIA 产品树的 BOM 数据。

**参数：**
- `product`：CATIA `Product` COM 对象（根节点）
- `settings`：采集配置（列可见性、是否包含用户属性等）

**返回：** BOM 行列表，每行为字典，键为列名（见 `constants.py` 中的列定义）

**采集的属性：**

| 属性 | 来源 | 说明 |
|------|------|------|
| `PartNumber` | CATIA 内置 | 零件编号 |
| `Nomenclature` | CATIA 内置 | 术语 |
| `Definition` | CATIA 内置 | 定义 |
| `Revision` | CATIA 内置 | 版本 |
| `Source` | CATIA 内置 | 来源（Made/Bought/Unknown） |
| `Description` | CATIA 内置（`description_reference`） | 描述 |
| `PRESET_USER_REF_PROPERTIES` 中的属性 | 用户自定义属性 | 物料编码、物料名称等 |

---

## `catia_copilot.catia.bom_export`

### `export_to_excel(bom_rows, output_path, selected_columns) -> None`

将 BOM 数据导出至 Excel 文件。

**参数：**
- `bom_rows`：`collect_bom` 返回的行列表
- `output_path`：输出文件路径（`str` 或 `Path`）
- `selected_columns`：要导出的列名列表

---

## `catia_copilot.catia.bom_write`

### `write_bom_to_catia(product, changes) -> tuple[int, int]`

将编辑后的属性写回 CATIA。

**参数：**
- `product`：CATIA `Product` COM 对象（根节点）
- `changes`：变更列表，每项为 `{"part_number": str, "field": str, "value": str}`

**返回：** `(成功数, 失败数)`

---

## `catia_copilot.catia.conversion`

### `export_drawings_to_pdf(file_paths, output_dir, prefix) -> list[str]`

批量将 CATDrawing 文件导出为 PDF。

**参数：**
- `file_paths`：CATDrawing 文件路径列表
- `output_dir`：输出目录
- `prefix`：输出文件名前缀（可为空字符串）

**返回：** 成功导出的 PDF 路径列表

### `export_parts_to_step(file_paths, output_dir) -> list[str]`

批量将 CATPart / CATProduct 导出为 STEP 格式。

---

## `catia_copilot.catia.template`

### `apply_template(part, properties) -> None`

向 CATPart 写入标准用户自定义属性。

**参数：**
- `part`：CATIA `Part` COM 对象
- `properties`：属性名到默认值的字典（默认使用 `PRESET_USER_REF_PROPERTIES`）

---

## `catia_copilot.plm.client`

### `PLMClient(base_url, token)`

PLM REST API 客户端。

```python
client = PLMClient(base_url="http://plm-server/api/v1", token="Bearer ...")
```

**主要方法：**

| 方法 | 说明 |
|------|------|
| `get_part(part_number, version)` | 获取零件信息 |
| `create_part(data)` | 创建零件（POST），成功返回创建结果，400"不唯一"表示已存在 |
| `update_part_attributes(part_number, version, attrs)` | 更新属性（需先 checkout） |
| `checkout(part_number, version)` | 签出零件 |
| `checkin(part_number, version, comment)` | 签入零件 |
| `undo_checkout(part_number, version)` | 撤销签出（`iter=1` 时不支持） |

**错误处理：**
- `400`："不唯一" → 零件已存在
- `404`：零件不存在
- `500` + NPE 响应体 → PLM 服务端 bug（PLM-06），客户端跳过而非抛异常

---

## `catia_copilot.plm.sync`

### `sync_bom(root_product, client, options) -> SyncResult`

将 BOM 树同步至 PLM 服务器。

**参数：**
- `root_product`：CATIA 根产品 COM 对象
- `client`：`PLMClient` 实例
- `options`：`SyncOptions` 实例（控制对已存在零件的处理策略）

**返回：** `SyncResult(created, updated, skipped, failed, log_lines)`

### `SyncOptions`

```python
@dataclass
class SyncOptions:
    existing_policy: ExistingPartPolicy  # UPDATE / SKIP / FORCE_UNDO（已灰显）
    dry_run: bool = False                # 仅预览，不实际写入
    ...
```

### `ExistingPartPolicy`

```python
class ExistingPartPolicy(Enum):
    UPDATE = "update"       # checkout → 更新属性 → checkin
    SKIP   = "skip"         # 跳过已存在的零件
    # FORCE_UNDO 已灰显（不支持撤销他人签出且 iter=1 无法撤销，见 PLM-07）
```

---

## `catia_copilot.constants`

### `APP_VERSION`

当前应用版本号，格式 `"x.y.z"`。`build.spec` 通过正则解析此值，打包输出目录名自动同步。

### `PRESET_USER_REF_PROPERTIES`

用户自定义属性名列表（`list[str]`）。驱动 BOM 编辑、导出、零件模板刷写的全部属性逻辑。

修改此列表后，Python 层全部自动跟随；VBA 宏（`generate_drawing.catvbs`、`refresh_drawing_info.catvbs`）中的属性名数组需手动同步。

### BOM 列定义常量

| 常量 | 说明 |
|------|------|
| `BOM_COLUMNS` | 所有列的完整定义列表（顺序即默认显示顺序） |
| `HIDEABLE_COLUMNS` | 可在 UI 中隐藏的列名集合 |
| `EDITABLE_COLUMNS_ORDER` | BOM 编辑对话框中可编辑列的顺序 |
