"""BOM 采集与纯行写出的薄适配，不管理输入文档生命周期。"""
from .bom_service import collect_bom
from .bom_export import export_bom_rows


def export_bom(output_file, doc=None, active=False, view='hierarchical', columns=None,
               custom_columns=None, include_assemblies=False, sort_column=None, **kwargs):
    data = collect_bom(doc=doc, active=active, view=view, columns=columns,
                       custom_columns=custom_columns, include_assemblies=include_assemblies,
                       sort_column=sort_column, **kwargs)
    if data.get('error') or data.get('success') is False or data.get('complete') is False:
        return data
    result = export_bom_rows(data['rows'], data['columns'], output_file)
    result['view'] = data['view']
    if 'document' in data:
        result['document'] = data['document']
    return result
