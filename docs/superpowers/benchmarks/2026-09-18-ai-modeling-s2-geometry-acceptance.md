# S2 几何查询手动验收记录

> 本文档对应 `catia_copilot/tests/catia_manual/s2_geometry_query_bench.py`。
> 该脚本不是 CI 测试，只能在用户确认后、CATIA 已启动时运行。

## 运行方式

```powershell
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py --b6
python catia_copilot/tests/catia_manual/s2_geometry_query_bench.py --fail-check
```

默认结果 JSON 写入 `%TEMP%\catia_s2_smoke`。脚本只新建未保存试验零件，
不会保存、关闭或删除用户文档。没有 CATIA 时结果为 `BLOCKED`，退出码为 2。

## 验收项目

| 编号 | 构造输入 | 重点检查 | 实测值/结论 |
|---|---|---|---|
| B1 | XY 矩形 `100×60×20` | 6 面；顶/底 planar；4 侧面法向按 `(-V,+H,+V,-H)`；记录 GE 类型、raw/ordinal、端点路径；顶楞 R3 回归 | **PASS**：6 面、矩形法向、端点和圆角均通过 |
| B2 | 圆 `R20` Pad 深度 20 | 3 面；圆柱侧面 `normal=None`；不得出现矩形法向；记录真实 `GeometricType`；圆楞回归 | **PASS**：3 面，圆柱侧面未伪造法向 |
| B3 | 腰形槽 `P1=(25,30),P2=(75,30),R15` Pad | 2 个 planar 侧面 + 2 个 cylindrical 侧面，均不伪造矩形法向 | **PASS**：6 面，平面/圆柱侧面分类正确 |
| B4 | 矩形 Pad 顶面 Pocket 深度 10 | Pocket 底/侧面；开口楞 4 条；Pocket 侧面法向与 Pad 对应外法向反向；开口圆角 R2 | **PASS**：5 面、开口圆角和反向法向通过 |
| B5 | `axis=z` 旋转圆筒 | Shaft 面 `geometry_type=unknown`、`normal=None`、`origin=None`；相邻边圆角 R2 | **PASS**：4 面、未知语义和圆角通过 |
| B6 | `axis=y` 旋转体 | 对比契约中的 XY 映射、`add_shaft` 文档中的 YZ 映射与实际轴向；不能只以“能生成”判定 | **PASS**：实际重心沿 Y 轴，当前实现采用 XY/V(Y) 映射 |
| B7 | 纯逻辑读失败路径 | `GeometryQueryError` 消息可定位；查询不产生 `failed_step`；CLI 无 CATIA 为 BLOCKED/2，`--fail-check` 为 FAIL/1 | 纯测试已覆盖；CLI 待运行 |
| B8 | 正式回归 | `catia_copilot/tests` 全量通过；不把特征名作为唯一几何断言 | 待回归 |

## 实机结论补充

用户在 CATIA 中复核确认：ZX 平面草图的 H 轴指向 -X，V 轴指向 +Z；
因此 `axis="z"` 采用轴=V(Z)、半径向=H(-X)、H>0 的映射。`axis="y"` 的实测重心沿 Y 轴，
与当前 XY/V(Y) 映射一致。

## 记录规则

每个样本至少记录：`geometry_type`、`normal`、`normal_unresolved`、`origin`、
`edge_index`、`edge_count`、`face_brep`、GeometricElements 类型序列、原始集合位置与
过滤后 ordinal 的对应关系。`source="feature_inference"` 表示候选面描述，不等价于
CAA 拓扑枚举。

如果端点读取失败，矩形侧面法向应诚实降级为 `None` 并记录 `PARTIAL`，不能把它记为
“法向修复通过”。如果实测 BRep 编号、Pocket 法向或 axis=y 语义与计划不同，应停止扩展
并记录实测结果，不能用猜测覆盖已验证行为。

## 代码回归命令

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest discover -s catia_copilot/tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw "回归失败" }
```
