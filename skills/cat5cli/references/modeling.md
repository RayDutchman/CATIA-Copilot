# 脚本建模

建模只有一个入口：`cat5cli run --file <脚本>`。由宿主写 UTF-8 Python 文件，CLI 在自身 worker 中执行 `build(ctx)`；不直接从宿主调用 Python/COM 来替代 CLI。

## 先发现能力

运行 `cat5cli schema run` 获取该版本公开的 ctx 方法、签名和示例；需要参数用法时再读 `cat5cli run --help`。尺寸计算、循环、草图及特征引用在同一脚本内组合。

已有脚本入口不代表任意 Body 编辑、既有特征参数修改、草图编辑都已支持。未在契约中提供的方法不能按名称猜测；先明确缺口。运行环境只保证内置能力所需依赖，不保证任意第三方 import。

## 完整示例

用户已指定输出位置后，由宿主保证目录存在，再写入：

```python
def build(ctx):
    length, width, depth = 100.0, 60.0, 20.0
    part = ctx.create_part("底座")
    sketch = ctx.add_sketch(part, "xy")
    ctx.draw_rect(sketch, 0, 0, length, width)
    ctx.add_pad(part, sketch, depth)
    ctx.update_part(part)
    ctx.save_part(part, r"D:\任务\底座.CATPart")
```

```powershell
cat5cli run --file D:/任务/build.py
```

`ctx.save_part` 的 SaveAs 路径使用 Windows 原生反斜杠 raw string。不要依赖 CATIA 接受正斜杠，更不要传 `/mnt/d/...`。

## 目标与失败恢复

- 修改已有零件时，按契约取目标对象；可选 `--target-document-id` 提供文档约束，不与其他命令的 `--doc` 混用。
- `--verification @条件.json` 是可选验收条件，缺省不要求用户补交。
- 输出包含 run_id、步骤、执行/更新/验证状态和运行记录路径。脚本无异常不等于模型满足全部需求。
- 若已有步骤完成而保存失败，针对已有零件处理保存，不重新创建整件。根据 failed_step 和已完成步骤编写修复脚本；失败不意味着回滚。
- 出现 `waiting_for_dialog/running/busy` 时，按 [执行与任务接续](execution.md) 处理原任务；不是另起一个脚本来绕过阻塞。
