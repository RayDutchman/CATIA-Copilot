"""新增对象命令契约；参数同时用于 CLI 帮助和 schema。"""
TARGET = {
    'doc': {'type': 'string', 'description': '已打开文档的 key、路径或无歧义名称；省略使用活动文档'},
    'active': {'type': 'boolean', 'description': '显式使用活动文档，与 doc 互斥'},
}


def schema(name, description, properties=None, required=(), target=True):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': {
            **(TARGET if target else {}), **(properties or {})}, 'required': list(required)},
    }}


inspection_schema = [
    schema('current_document', '查询活动文档身份、类型与保存状态；不激活或修改文档', target=False),
    schema('document_info', '查询指定文档身份、类型、活动及保存状态；不可读属性返回 null'),
    schema('activate_document', '激活已打开的指定文档，不隐式打开文件'),
    schema('save_document_as', '文档另存为，保留原文件；CATIA 提示通过 task/dialog 接续',
           {'file_path': {'type': 'string', 'description': '另存的 Windows 文件路径'}}, ['file_path']),
    schema('close_document', '关闭指定文档；未保存修改由 CATIA 弹窗处理，不自动丢弃'),
    schema('part_info', '查询 CATPart 文档身份和零件名称'),
    schema('part_tree', '查询 Body、草图、特征和几何图形集树；返回 scope/complete/errors，不展开特征内部参数与布尔吸收体'),
    schema('update_part_document', '更新指定 CATPart，不切换活动文档'),
    schema('part_mass', '查询指定零件质量 kg、局部重心 mm、惯量 kg*mm^2；读取失败返回错误'),
    schema('drawing_info', '读取图纸页、视图及各视图的生成源文档（GenerativeBehavior.Document）；不枚举所有底层几何链接，不可读项返回 errors'),
    schema('product_info', '查询装配根对象属性、位置和直接子实例数量'),
    schema('product_tree', '读取装配实例树；重复引用保留各自实例。节点路径随装配结构变化失效',
           {'node': {'type': 'string', 'description': '可选子树路径，例如 0.0；从 tree 结果取得'}}),
    schema('merge_mass_files', '离线合并同一根坐标系下的 MPD 文件，复用现有质量汇总；不自动变换坐标', {
        'files': {'type': 'array', 'items': {'type': 'string'}, 'description': '非空 MPD 文件路径数组或 @JSON文件'},
        'output_file': {'type': 'string', 'description': '输出 MPD 文件路径'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'description': '已有输出策略，默认 error'},
    }, ['files', 'output_file'], target=False),
    schema('remeasure_product_part', '重新读取已打开零件的惯量包络体保持测量；不强制 Analyze，不修改 MPD，返回 kg/mm/kg*mm^2 数据', {
        'file_path': {'type': 'string', 'description': '已打开的引用零件完整路径'},
        'part_number': {'type': 'string', 'description': '测量参数前缀使用的零件编号，默认空'},
        'read_mode': {'type': 'string', 'enum': ['first', 'last', 'all'], 'description': '保持测量读取范围，默认 all'},
    }, ['file_path'], target=False),
    schema('export_product_step', '整装配 CATProduct 或文件集导出 STEP；不支持实例 --node。默认活动装配须有文件路径，返回逐文件结果', {
        'file_paths': {'type': 'array', 'items': {'type': 'string'}, 'description': 'CATProduct 文件数组，别名 --files；与 doc/active 互斥'},
        'output_folder': {'type': 'string', 'description': '输出目录，默认源文件目录'},
        'prefix': {'type': 'string', 'description': '文件名前缀，默认 MD_'},
        'suffix': {'type': 'string', 'description': '文件名后缀，默认空'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'description': '输出覆盖策略，默认 error'},
    }),
]
