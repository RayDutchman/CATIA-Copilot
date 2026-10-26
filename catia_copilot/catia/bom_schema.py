"""BOM 采集、字段编辑及行导出的公开契约。"""
from .inspection_schema import schema


VIEW_FIELDS = {
    'view': {'type': 'string', 'enum': ['full', 'hierarchical', 'summary'], 'default': 'hierarchical',
             'description': 'Full instance, hierarchical, or summary view; defaults to hierarchical and does not read GUI preferences'},
    'columns': {'type': 'array', 'items': {'type': 'string'},
                'description': 'List of standard columns; omit to use the BOM default columns'},
    'custom_columns': {'type': 'array', 'items': {'type': 'string'},
                       'description': 'When omitted, enumerate custom properties across the assembly and merge them as columns; an explicit list reads only those columns, and [] disables custom-property reads'},
    'include_assemblies': {'type': 'boolean', 'default': False,
                           'description': 'Whether the summary view includes assemblies; defaults to false'},
    'sort_column': {'type': 'string', 'description': 'Sort column for the summary view; defaults to Part Number'},
}

bom_schema = [
    schema('collect_bom', 'Collect a BOM from an open document; defaults to the hierarchical view. Returns row data and current assembly-node locators. '
           'Node paths become invalid when the assembly structure changes. Does not open, close, or save documents.', VIEW_FIELDS),
    schema('export_bom_to_excel', 'Collect and export the selected BOM view from an open document; exported content matches collect for the same view. '
           'The file extension selects XLSX or CSV. Does not use GUI filters or save the CATIA document.', {
        **VIEW_FIELDS,
         'output_file': {'type': 'string', 'description': 'Full path to the output .xlsx or .csv file'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'default': 'error',
                       'description': 'Policy for an existing output file'},
    }, ['output_file']),
    schema('edit_bom', 'Edit BOM fields in order. master refers to shared master data; instance refers to an occurrence. '
           'Reused subassembly occurrences may be linked across multiple paths. Returns success or failure for each change; successful changes are not rolled back and are not saved automatically.', {
        'changes': {'type': 'array', 'description': 'Ordered change array or @UTF-8 JSON file; node comes from collect or product_tree',
                    'items': {'type': 'object', 'required': ['node', 'scope', 'column', 'value'],
                              'properties': {
                                  'node': {'type': 'string'},
                                  'scope': {'type': 'string', 'enum': ['master', 'instance']},
                                   'column': {'type': 'string', 'description': 'For master, use a writable standard or declared custom column; instance supports only Instance Name'},
                                  'value': {'type': 'string'},
                              }}},
        'custom_columns': {'type': 'array', 'items': {'type': 'string'},
                            'description': 'Custom property columns allowed for this request; declare them explicitly. Existing properties are updated and missing ones are created as string properties. Structural fields and instance descriptions are not custom properties'},
    }, ['changes']),
]
