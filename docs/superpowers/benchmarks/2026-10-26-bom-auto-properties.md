# BOM 自定义属性自动枚举验收

- collect/export 省略 custom_columns 自动枚举全装配主数据 UserRefProperties，合并列；显式列表筛选，[] 关闭自定义列。edit 仍要求显式声明。
- 不新增 COM 文档生命周期操作；既有 collector 和导出逻辑继续负责读取值与写出行。
- 按主数据键去重枚举；字段与保留列冲突、枚举失败均明确报错，不伪装无属性。
- 四项新增回归先失败后通过：三视图/重复引用/导出一致性、失败与显式空列表、保留列冲突、带对象路径的属性名合并。
- 全量 Python 469 项通过（50.140s），Node 安装器 6 项通过；本次修改文件 diff --check 通过。主仓用户原有模板尾随空格未改。
- 源码及正式 EXE 自建未保存装配实测通过：中文属性、三视图、显式列、空列表、XLSX 内容；夹具关闭并清理，用户文档未保存。
- 绕过 Catkit 的原始 win32com 实测：CreateString('备注', ...) 返回 Name 为 RawPropertyProbe\属性\备注，Item(1).Name 相同，Item('备注').Value 正常。前缀来自 COM，CLI 仅去掉对象/属性组路径后合并列。
- Description 是 PartMaster 的 DescriptionRef；description_inst 是实例的 DescriptionInst，当前只读；自定义“备注”是另一字段。Skill 已明确区分。
- 正式 EXE 已构建安装，安装版实机通过；补充的描述语义文档同步至 ZIP、runtime 和两处已安装 Skill，未为文档变更重复编译。
- 未自动提交。
