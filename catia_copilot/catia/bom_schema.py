"""BOM 采集、字段编辑及行导出的公开契约。"""
from .inspection_schema import schema


VIEW_FIELDS = {
    'view': {'type': 'string', 'enum': ['full', 'hierarchical', 'summary'], 'default': 'hierarchical',
             'description': '完整实例/层级合并/汇总视图；默认 hierarchical，不读取 GUI 偏好'},
    'columns': {'type': 'array', 'items': {'type': 'string'},
                'description': '标准列列表，省略使用 BOM 默认列'},
    'custom_columns': {'type': 'array', 'items': {'type': 'string'},
                       'description': '用户自定义属性列列表'},
    'include_assemblies': {'type': 'boolean', 'default': False,
                          'description': 'summary 视图是否包含装配件，默认 false'},
    'sort_column': {'type': 'string', 'description': 'summary 视图排序列，省略按 Part Number'},
}

bom_schema = [
    schema('collect_bom', '采集已打开文档的 BOM，默认层级视图；返回列数据和当前装配节点定位信息。'
           '节点路径随装配结构变化失效；不打开、关闭或保存文档。', VIEW_FIELDS),
    schema('export_bom_to_excel', '采集已打开文档的指定 BOM 视图并导出，内容与 collect 的同视图一致。'
           '文件扩展名决定 XLSX/CSV；不使用 GUI 当前筛选状态，不保存 CATIA 文档。', {
        **VIEW_FIELDS,
        'output_file': {'type': 'string', 'description': '输出 .xlsx 或 .csv 文件完整路径'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'default': 'error',
                      'description': '已有输出文件的处理策略'},
    }, ['output_file']),
    schema('edit_bom', '按顺序编辑 BOM 字段，master 表示共享主数据，instance 表示实例定义。'
           '复用子装配中的实例定义可能在多个出现路径联动。逐项返回成功或失败；成功项不回滚，不自动保存。', {
        'changes': {'type': 'array', 'description': '有序变更数组或 @UTF-8 JSON文件；node 来自 collect 或 product tree',
                    'items': {'type': 'object', 'required': ['node', 'scope', 'column', 'value'],
                              'properties': {
                                  'node': {'type': 'string'},
                                  'scope': {'type': 'string', 'enum': ['master', 'instance']},
                                  'column': {'type': 'string', 'description': 'master 使用可写标准列或声明的自定义列；instance 仅支持 Instance Name'},
                                  'value': {'type': 'string'},
                              }}},
        'custom_columns': {'type': 'array', 'items': {'type': 'string'},
                           'description': '本次允许写入的自定义属性列；结构字段和实例描述不可冒充自定义属性'},
    }, ['changes']),
]
