Part_Template.CATPart
│
├─ 参数
│   ├─ 材料牌号
│   ├─ 分隔符
├─ 关系
│   ├─ 规则.1
│   ├─ 反应.1
├─ [Body] MAIN
│
├─ 外部参考
│
├─ 构造几何
│   ├─ Composites Design
│   │   ├─ Process
│   │   ├─ Element
│   │   ├─ Ply-drop
│   │   ├─ Hole
│   │   ├─ Core
│   │   ├─ Cross Section
│   │   └─ Expect
│   ├─ 理论数据基准
│   │   ├─ 气动外形
│   │   ├─ 机身
│   │   ├─ 机翼
│   │   ├─ 垂尾
│   │   ├─ 机臂
│   │   └─ 起落架
│   ├─ Analysis
│   └─ 设计文件
│
├─ 基本信息
│   ├─ [param] 物料编码       = "" (通过规则.1定义，始终等于 PartNumber)
│   ├─ [param] 物料名称       = "..." (通过规则.1定义，始终等于 PartNumber)
│   ├─ [param] 版本           = "A" (通过规则.1定义，始终等于 Revision)
│   ├─ [param] 定义           = "..." (通过规则.1定义，始终等于 Definition)
│   ├─ [param] 中文名称       = "..." (通过规则.1定义，始终等于 Nomenclature)
│   ├─ [param] 规格型号       = "..." (通过规则.1定义，始终等于 UserRefProperties.规格型号)(= `材料信息\材料牌号` +`分割符` +`材料信息\零件尺寸` +`分割符` +`表面处理\底涂` +`分割符`  +`表面处理\面漆1` )
│   ├─ [param] 数据来源       = "..." ()
│   ├─ [param] 数据状态       = "设计"
│   ├─ [param] 存货类别       = "物料-复合材料"
│   ├─ [param] 质量           = "123"
│   ├─ [param] 备注           = "..."
│   ├─ [param] 关重件说明     = "关键件"
│   ├─ [param] 验收规范       = "..."
│   ├─ [param] 对称件信息     = "..."
│   └─ [param] 版权声明       = "..."
│
├─ 零件注释
│   ├─ [param] EN1  = "未注尺寸公差按GB/T1804-2000-m级"
│   ├─ [param] EN2  = "未注形位公差按GB/T1184-1996-k级"
│   ├─ [param] EN3  = "未注公差按HB7741-2004..."
│   ├─ [param] EN4  = "未注厚度公差按HB7224-2020..."
│   ├─ [param] EN5  = "未注重量公差按HB8673-2022..."
│   ├─ [param] EN6  = "机械加工工艺，按S-886PS303..."
│   ├─ [param] EN7  = "标记标识，按S-886PS901..."
│   └─ [param] EN8  = "未注气动面公差按HB7086-2023..."
│
├─ 材料信息
│   ├─ [param] 材料牌号    = "C-12K-UD150//JN5010-A150"
│   ├─ [param] 材料规范_1  = "按S-886MS003..."
│   ├─ [param] 材料规范_2  = "按S-886MS007..."
│   ├─ [param] 材料类型    = "碳纤维增强预浸料"
│   ├─ [param] 树脂类型    = "热固性环氧树脂"
│   ├─ [param] 成型工艺    = "热压罐高温成型"
│   ├─ [param] 成型规范    = "按S-886PS402..."
│   └─ [param] 零件尺寸    = "91.6*91.6*17"
│
├─ 表面处理
│   ├─ [param] 底涂        = "喷涂环氧底漆_MIPA 4+1..."
│   ├─ [param] 面漆1       = "喷涂环氧面漆_RAL9003..."
│   ├─ [param] 面漆2       = "---"
│   ├─ [param] 面漆3       = "---"
│   ├─ [param] 清漆        = "喷涂环氧清漆_Mipa 2K C85 HS..."
│   ├─ [param] 底漆规范    = "按S-886PS602..."
│   └─ [param] 面漆规范    = "按S-886PS603..."
│
├─ 连接定义
│   ├─ 机械连接
│   │   └─ 机械连接
│   │       ├─ HR1122S-06-03_钛合金扁圆头抽芯铆钉
│   │       └─ HB1-101 M6X30_六角头螺栓
│   ├─ 胶接
│   ├─ 焊接
│   ├─ 密封
│   └─ 其他连接
│
├─ 旗注信息
│   ├─ [param] FN1  = "A类检测方法"
│   ├─ [param] FN2  = "B类检测区域：Z101，Z102"
│   ├─ [param] FN3  = "全检，按S-886PS801..."
│   ├─ [param] FN4  = "关键疲劳区..."
│   ├─ [param] FN5  = "轴承装配区..."
│   ├─ [param] FN6  = "铜网搭接区..."
│   └─ [param] FN7  = "丢层过渡区..."
│
├─ 标注辅助
│   ├─ [param] 尺寸公差    = "按GB/T1800.2-2020..."
│   ├─ [param] 形位公差    = "按GB/T1184-1996..."
│   └─ 连接定义
│       ├─ HB1-101 M6X30_六角头螺栓
│       ├─ HR1122S-06-03_钛合金扁圆头抽芯铆钉
│       └─ Ply-drop
│
├─ 审签修订
│   ├─ [param] DESIGNED BY 设计  = "设计者"
│   ├─ [param] 设计-时间          = "20260315"
│   ├─ [param] CHECKED BY 校对   = "校对者"
│   ├─ [param] 校对-时间          = "20260315"
│   ├─ [param] VERIFIED BY 审核  = "审核者"
│   ├─ [param] 审核-时间          = "20260316"
│   ├─ [param] APPROVED BY 批准  = "批准者"
│   ├─ [param] 批准-时间          = "20260316"
│   └─ 升版修订
│       ├─ 修订记录_A
│       │   ├─ [param] 版本      = "A"
│       │   ├─ [param] 修订区域  = ""
│       │   ├─ [param] 修订记录  = "新设"
│       │   └─ [param] 时间      = "20260310"
│       └─ 修订记录_B
│           ├─ [param] 版本      = "A"
│           ├─ [param] 修订记录_B = ""
│           ├─ [param] 时间      = "20260315"
│           └─ [param] 修订区域  = "B"
│
├─ 复合参数
│   ├─ KEVLAR4
│   ├─ C-12K-UD150//JN5010-A150
│   ├─ 压层.242
│   └─ 压层.243
│
├─ CS_Tool
│
├─ 堆栈
│   └─ Plies Group.1
│       ├─ Sequence.1 → Ply.1 → Composites Geometry.1
│       ├─ Sequence.2 → Ply.2 → Composites Geometry.2
│       ├─ Sequence.3 → Ply.3 → Composites Geometry.3
│       ├─ Sequence.4 → Ply.4 → Composites Geometry.4
│       ├─ Sequence.5 → Ply.5 → Composites Geometry.6
│       ├─ Sequence.6 → Ply.6 → Composites Geometry.7
│       ├─ Sequence.7 → Ply.7 → Composites Geometry.8
│       └─ Sequence.8 → Ply.8 → Composites Geometry.9
│
├─ 几何图形集.47
│
├─ 审查工具
│   └─ 3D 截面
│       └─ 3D 截面.1
│
└─ [AnnotationSet] 标注集.1  (43 个标注)