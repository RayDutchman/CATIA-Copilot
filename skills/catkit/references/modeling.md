# 建模脚本

调用 `catkit schema run` 获取当前完整 `ctx` API、参数签名及例子，避免凭记忆编造方法。
脚本保存为 UTF-8 Python 文件，以 `build(ctx)` 为入口。

```python
def build(ctx):
    length, width, depth = 100.0, 60.0, 20.0
    part = ctx.create_part("底座")
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sketch, 0, 0, length, width)
    ctx.add_pad(part, sketch, depth)
    ctx.update_part(part)
```

```powershell
catkit run --file D:/任务/build.py
```

已有目标限制可用 `--target-document-id`；验收条件可用 `--verification @条件.json`，二者都沿用原工具的可选语义。
需要保存时，在 `build(ctx)` 内使用 `ctx.save_part(part, r"C:\任务\part.CATPart")`；CATIA SaveAs 的路径使用反斜杠。若保存失败但建模已完成，接续已有零件保存，不要重新创建。
结果保留 run_id、步骤、脚本与运行记录路径；出现 failed_step 时检查已完成步骤，不盲目重新创建零件。
