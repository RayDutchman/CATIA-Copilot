# Task 5 报告：CATIA 文件导出模块

## 状态：✅ 完成

## 产出文件

- `catia_copilot/catia/file_exporter.py` — 112 行，包含两个公开函数

## 公开接口

| 函数 | 签名 | 功能 |
|------|------|------|
| `export_stp` | `(path, product_doc=None, output_path=None) -> str \| None` | 按装配树路径定位实例并导出 STP |
| `export_pdf` | `(drawing_path, output_path=None) -> str \| None` | 将 CATDrawing 文件转为 PDF |

## 依赖关系

- `catia_copilot.catia.connection.get_catia_v5_application()` — COM 连接
- `catia_copilot.catia.property_rw._resolve_product_by_path()` — 按路径定位实例（懒加载）
- `catia_copilot.catia.conversion.convert_drawing_to_pdf()` — PDF 转换（懒加载，避免 PySide6 导入依赖）

## 与原任务代码的差异

原任务代码中 `export_pdf` 直接调用 `convert_drawing_to_pdf(drawing_path, output_path)`，但实际函数签名为 `convert_drawing_to_pdf(file_paths: list[str], output_folder: str, ...)`。已修正为：
1. 传入 `[drawing_path]` 列表
2. 提取 `output_dir` 作为 `output_folder`
3. 设置空 `prefix`/`suffix` 以保留原始文件名
4. 转换成功后将文件重命名到用户指定的 `output_path`

## 验证

```
python -c "from catia_copilot.catia.file_exporter import export_stp, export_pdf; print('OK')"
→ OK
```
