# 直接改名 + 引用重连试验

## 结论

本机自建夹具验证走通：迁移阶段不使用SaveAs，也不写入FullName；文件和目录由文件系统改名，CATProduct通过Products.ReplaceComponent重连，CATDrawing通过GenerativeBehavior.Document重绑生成源，内容通过普通Save保存。

这证明该路线具有可行性，不代表已验证同事的3907文件项目全部关系或全量性能。

## 夹具与步骤

- 4个待迁移文档：实体CATPart、子CATProduct、顶层CATProduct、CATDrawing；另有1个保持不变的供应商CATPart。
- 2个目录改名，VL01/VT28统一为VE01，保持原目录层级。
- 顶层包含子装配、同一零件的直接引用及供应商件；子装配重复引用零件，共6个出现节点（含根）。
- 每个实体零件均有平面publication；实例名和位置固定记录。
- 增强试验包含5个装配约束：4个Reference类型约束，以及一个30 mm Distance双元素约束。
- 图纸含顶层装配的生成视图和待改文本。

先通过SaveAs创建初始夹具并全部关闭，取供应商文件哈希基线；这部分不是迁移算法。

迁移过程：
1. 预先记录完整旧路径/出现节点关系。
2. 文件系统改2个目录、4个文件名。
3. 按零件→子装配→顶层顺序打开新路径，使用改名前清单替换旧引用。
4. 重新绑定图纸生成源，修改3个主数据件号及图纸文本。
5. 对4份迁移文档普通Save，全部关闭。
6. 不预加载零件，直接重新打开顶层和图纸核验。

## 结果

- 两轮有效迁移阶段分别4.1544秒和4.0041秒，均不含初始夹具创建和最终冷重开验证；样本很小，不能线性外推几千文件耗时。
- 实际调用ReplaceComponent 4次、图纸生成源重绑定1次；迁移阶段SaveAs调用为0。
- 冷重开后全部引用为新路径；实例名、12项位置矩阵、publication名称及目标、主数据件号符合预期。
- 5个约束Status均为0，Distance保持30 mm；质量保持0.003125 kg。
- 图纸生成源指向新顶层路径，文本改为VE01，视图可Update。
- 供应商文件与完整夹具关闭后的基线SHA-256一致，文件名和件号保持原值。
- 无需人工点击迁移弹窗，脚本临时设置DisplayFileAlerts=False并在finally恢复；用户原活动文档恢复。

## 重要发现及边界

- 直接改名后，即使预加载了新零件，部分组件的ReferenceProduct仍不可读；不能依靠此时的FullName反查旧路径。迁移清单必须在改名前保存。
- iMultiInstances=True没有让本夹具两个断链出现一次全部修复；逐出现检查并补齐重连，不能假定一次调用覆盖所有引用。
- publication的Reference需要使用产品上下文创建；直接传Part创建的平面Reference在本机SetDirect报E_NOINTERFACE，夹具构建已纠正。
- 供应商基线若在初始装配/图纸尚未保存关闭时就取，会混入夹具创建影响；增强轮已在迁移开始前冻结基线，并在保存/关闭/重开各阶段核对。
- 本次覆盖生成视图和文本，未证明复杂图纸尺寸关联、CATPart上下文外部链接、装配柔性/场景等全部关系。
- 本机初始小夹具的单次原生SaveAs也仅约0.02–0.04秒；同事每份约11秒不能全部归因于SaveAs方法本身，调用启动、弹窗和模型体量需要分别衡量。
- 当前是可复现实机试验，不是已交付的通用批量迁移命令。

## 复现与清理

脚本：catia_copilot/tests/catia_manual/cat5cli_rename_reference_probe.py，通过run --file执行；默认在工作目录.superpowers创建全新UUID夹具目录，可用CAT5CLI_PROBE_ROOT指定验证目录。仅操作自建文件，结果保存在其report.json。

本轮所有夹具文档已关闭，CAD夹具文件已清理，报告归档在.superpowers/rename-reference-evidence。普通生产测试/源码文件未被批量改名。
