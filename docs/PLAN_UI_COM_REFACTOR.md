# Plan：UI 层 COM 操作下沉重构

> 状态：待执行  
> 来源：2026-05-31 对 `catia_copilot/ui/` 全量扫描  
> 相关文件：见下方各 Step

---

## 背景与问题

扫描 `ui/` 目录下全部 23 个 Python 文件，发现 **6 个文件**中存在直接的 CATIA COM 操作
（调用 `get_catia_v5_application()`、访问 `app.Documents`、`doc.SaveAs()` 等）。

这违反了项目的分层原则：

```
ui/       ← 只管界面：布局、事件、弹框、数据绑定
catia/    ← 只管 CATIA 操作：COM 调用、文件读写、属性操作
ai/       ← 只管 AI 工具：包装 catia/ 层，暴露给 LLM
```

---

## 现状：UI 层 COM 操作清单

| 文件 | 方法 | COM 操作 | 严重程度 |
|------|------|---------|---------|
| `bom_edit_dialog.py` | `_rename_by_part_number`（155 行） | 构建 doc_cache、`documents.Open`、`doc.SaveAs` | **高** |
| `bom_edit_dialog.py` | `_rename_selected_file`（131 行） | 同上，单文件版本 | **高** |
| `bom_edit_dialog.py` | `_open_in_catia`（11 行） | `get_catia_v5_application` + `open_catia_file` | 低 |
| `bom_catia_helpers.py` | `_find_catia_doc_by_path`（全文 40 行） | 遍历 `docs.Count/Item/FullName` | 中 |
| `convert_dialog.py` | `_confirm` | `app.ActiveDocument.FullName` | 低 |
| `find_deps_dialog.py` | `_resolve_target` | `app.ActiveDocument.FullName` | 低 |
| `find_deps_dialog.py` | `_open_all` | `get_catia_v5_application` + `open_catia_file` × N | 低 |
| `find_deps_dialog.py` | `_open_in_catia` | `get_catia_v5_application` + `open_catia_file` | 低 |
| `mass_props_dialog.py` | `_open_in_catia` | `get_catia_v5_application` + `open_catia_file` | 低 |

**不在本次范围：**
- `main_window.py` 的宏执行（`_run_macro`、`_run_template_macro`）：宏执行固有需要传递 `app` 对象给 VBA/Script 引擎，属于宏机制的固有要求，不是分层问题。
- `plm_workbench.py`：已通过 `bom_collect` 间接调用，UI 层不持有 COM 对象，无需改动。

---

## 目标结构

```
catia/
  connection.py     ← 新增 get_active_document_path()、open_document()
  document.py       ← 新建：find_open_document()、rename_document()
                       （后续可扩展 get_document_properties、set_document_properties）
  ...（其余不变）

ui/
  bom_catia_helpers.py  ← 删除（两个函数均下沉到 catia/document.py）
  bom_edit_dialog.py    ← 重命名 COM 操作替换为 catia/document.rename_document()
                          _open_in_catia 替换为 catia/connection.open_document()
  convert_dialog.py     ← ActiveDocument 替换为 catia/connection.get_active_document_path()
  find_deps_dialog.py   ← 同上，3 处替换
  mass_props_dialog.py  ← _open_in_catia 替换为 catia/connection.open_document()
```

---

## 详细改动

### Step A：新建底层函数（纯新增，零风险）

#### A1. `catia/connection.py` 末尾新增 2 个函数

```python
def get_active_document_path() -> str | None:
    """返回当前活动文档的完整路径，无活动文档时返回 None。
    CATIA 未连接时抛出 RuntimeError（与 get_catia_v5_application 一致）。
    """
    app = get_catia_v5_application()
    try:
        return app.ActiveDocument.FullName
    except Exception:
        return None


def open_document(file_path: str, foreground: bool = False) -> None:
    """在 CATIA 中打开指定文件，已打开则激活。

    封装 get_catia_v5_application + utils.open_catia_file，
    调用方无需自行获取 app 对象。
    选项 A：比直接调用 utils.open_catia_file 多一层包装，但接口更干净，
    UI 层不再需要 import connection + utils 两个模块。
    """
    from catia_copilot.utils import open_catia_file
    app = get_catia_v5_application()
    open_catia_file(app.Documents, file_path, foreground=foreground)
```

#### A2. 新建 `catia/document.py`

职责：以**单个 CATIA 文档**为操作对象的通用工具函数。
与 `bom_collect.py`（遍历产品树）的区别：粒度不同，`document.py` 操作单个文件。

```python
def find_open_document(file_path: str):
    """在 CATIA 已打开文档中按路径查找，返回 COM 文档对象或 None。

    原 bom_catia_helpers._find_catia_doc_by_path 下沉到此处。
    调用方不需要自行获取 application/documents 对象。
    """
    # 内部实现：get_catia_v5_application() → 遍历 documents.Count/Item/FullName


def rename_document(
    file_path: str,
    new_part_number: str,
    delete_old: bool = False,
) -> tuple[str, bool]:
    """将 CATIA 文档另存为以新零件编号命名的文件（SaveAs）。

    参数
    ----
    file_path:
        源文件的完整路径。
    new_part_number:
        新零件编号（将作为新文件名的 stem）。
    delete_old:
        SaveAs 成功后是否删除旧文件。

    返回
    ----
    (new_file_path, was_skipped_by_user)
        new_file_path:       新文件的完整路径（即使 was_skipped_by_user=True 也返回预期路径）。
        was_skipped_by_user: True 表示用户在 CATIA 的 SaveAs 对话框中主动取消，
                             不是错误，调用方可以选择静默跳过。

    异常
    ----
    非 COM 错误（OSError、PermissionError 等）直接向上抛出，由调用方处理。

    设计说明
    --------
    - _is_catia_com_error 判断（区分"用户取消"和"真实错误"）在底层做，
      因为它依赖 COM 异常类型（pywintypes.com_error）。
    - "是否弹框提示"是 UI 决策，通过 was_skipped_by_user 返回值传递给调用方。
    - delete_old（os.remove）是文件系统操作，不属于 COM，保留在调用方（UI 层）。
    - 不接受 doc_cache 参数（选项 B 被否决）：
      每次调用内部自行查找文档，避免 UI 层持有 COM 对象引用。
      批量重命名时文件数量通常不超过几十个，性能影响可忽略。
    """
```

**关于 `_is_catia_com_error`：**
从 `bom_catia_helpers.py` 移入 `catia/document.py` 作为模块内部函数（`_is_catia_com_error`），
不对外暴露。`bom_edit_dialog.py` 不再需要 import 它。

---

### Step B：简单替换——`open_document`（5 处）

每处改动 3–5 行，模式完全一致。

**替换前（各文件重复的模式）：**
```python
from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.utils import open_catia_file
app = get_catia_v5_application()
open_catia_file(app.Documents, fp, foreground=True)
```

**替换后：**
```python
from catia_copilot.catia.connection import open_document
open_document(fp, foreground=True)
```

涉及位置：
- `bom_edit_dialog.py:_open_in_catia`
- `mass_props_dialog.py:_open_in_catia`
- `find_deps_dialog.py:_open_in_catia`
- `find_deps_dialog.py:_open_all`（循环内）
- （`find_deps_dialog._open_all` 中的 `bring_catia_to_foreground` 调用保持不变）

---

### Step C：简单替换——`get_active_document_path`（2 处）

**替换前：**
```python
from catia_copilot.catia.connection import get_catia_v5_application
app = get_catia_v5_application()
active_path = app.ActiveDocument.FullName
```

**替换后：**
```python
from catia_copilot.catia.connection import get_active_document_path
active_path = get_active_document_path()
# 注意：get_active_document_path() 返回 None 而非抛异常（无活动文档时）
# 调用方需要检查 None 并给出友好提示
```

涉及位置：
- `convert_dialog.py:_confirm`
- `find_deps_dialog.py:_resolve_target`

**注意**：原代码在 `except Exception` 中弹框提示"无法获取活动文档"。
新函数在无活动文档时返回 `None`（不抛异常），调用方需将 `except` 改为 `if active_path is None`。
CATIA 未连接时仍会抛 `RuntimeError`，`except Exception` 仍可捕获。

---

### Step D：复杂重构——重命名逻辑（中等复杂度）

#### D1. `bom_catia_helpers.py` 处置

- `_find_catia_doc_by_path` → 下沉到 `catia/document.py`（作为 `find_open_document` 的实现）
- `_is_catia_com_error` → 下沉到 `catia/document.py`（模块内部函数）
- `bom_catia_helpers.py` 删除
- `bom_edit_dialog.py` 中的 `from .bom_catia_helpers import ...` 删除

#### D2. `bom_edit_dialog._rename_by_part_number` 重构

**UI 层保留（业务逻辑）：**
- 写回前置检查（`_write_back`）
- 构建 `to_rename` 列表（遍历 `self._rows`）
- 零件编号合法性校验（`PART_NUMBER_VALID_PATTERN`）
- `delete_old` 确认弹框
- `os.remove`（文件系统操作，不是 COM）
- 更新 `self._rows` / `self._raw_rows` 中的路径字段
- `_populate_table()`
- 弹框提示（成功/失败/跳过）

**下沉到 `catia/document.rename_document`：**
- `get_catia_v5_application()`
- `application.Visible = True`
- `documents.Count / documents.Item(i) / doc.FullName`（查找文档）
- `documents.Open(str(src))`
- `documents.Item(documents.Count)`（取刚打开的文档）
- `target_doc.SaveAs(new_fp)`
- `_is_catia_com_error` 判断

**重构后核心循环（示意）：**
```python
for fp, pn in reversed(to_rename):
    if not PART_NUMBER_VALID_PATTERN.fullmatch(pn):
        QMessageBox.warning(...)
        continue
    if not Path(fp).exists():
        continue
    try:
        new_fp, skipped = rename_document(fp, pn)   # ← 底层函数
        if skipped:
            logger.info(f"SaveAs skipped for {Path(fp).name} (user cancelled)")
            continue
        if delete_old and Path(fp).resolve() != Path(new_fp).resolve():
            os.remove(fp)                            # ← 文件系统，留在 UI 层
        for row in self._rows:                       # ← 数据更新，留在 UI 层
            if str(row.get("_filepath", "")) == fp:
                row["_filepath"] = new_fp
                row["Filename"]  = pn
        for row in self._raw_rows:
            if str(row.get("_filepath", "")) == fp:
                row["_filepath"] = new_fp
                row["Filename"]  = pn
        renamed_count += 1
    except Exception as e:
        QMessageBox.warning(self, "另存为失败", f"文件「{Path(fp).name}」另存为失败：\n{e}")
```

#### D3. `bom_edit_dialog._rename_selected_file` 重构

与 D2 类似，单文件版本，逻辑更简单。

---

## 执行顺序

```
Step A（新建底层函数）
  ↓
Step B（open_document 替换，5 处）
  ↓
Step C（get_active_document_path 替换，2 处）
  ↓
Step D（重命名逻辑重构）
  ↓
删除 bom_catia_helpers.py
```

每步完成后独立做 AST 语法检查。
Step D 完成后需要手动测试重命名功能（需要 CATIA 运行）。

---

## 工作量与风险评估

| Step | 改动量 | 风险 | 预估时间 |
|------|--------|------|---------|
| A | ~60 行新增 | 低（纯新增） | 30 分钟 |
| B | 每处 3–5 行替换，共 5 处 | 低（机械替换） | 20 分钟 |
| C | 每处 1–3 行替换，共 2 处 | 低（需注意 None 处理） | 10 分钟 |
| D | 提取约 60 行 COM 逻辑，UI 层保留约 220 行 | **中** | 60 分钟 |
| **合计** | | | **约 2 小时** |

**中等风险点（Step D）的具体来源：**
1. `_is_catia_com_error` 的异常分类判断必须在正确位置（底层），否则"用户取消"会被误报为错误
2. `doc_cache` 不再传递给底层，每次 `rename_document` 内部自行查找文档——需验证性能可接受
3. `delete_old` 的 `os.remove` 必须在 `SaveAs` 成功后执行，顺序不能错

**降低风险的措施：**
- Step D 完成后，在有 CATIA 的环境中手动测试批量重命名和单文件重命名两个场景
- 测试"用户在 CATIA 对话框中点取消"的场景，确认不弹错误框

---

## 后续扩展（不在本次范围）

本次重构完成后，`catia/document.py` 可以继续扩展：

```python
# 读取单个文档的属性（标准属性 + 用户自定义属性）
def get_document_properties(
    file_path: str | None,
    property_names: list[str] | None = None,
    custom_property_names: list[str] | None = None,
) -> dict[str, str]: ...

# 写入单个文档的属性
def set_document_properties(
    file_path: str | None,
    properties: dict[str, str],
    custom_property_names: list[str] | None = None,
) -> None: ...
```

这两个函数完成后，可以在 `ai/tools.py` 中新增：
- `tool_get_document_properties`
- `tool_set_document_properties`

填补当前 AI 工具集中"操作单个文档属性"的空白。
