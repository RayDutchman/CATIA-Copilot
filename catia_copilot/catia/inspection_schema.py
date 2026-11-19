"""新增对象命令契约；参数同时用于 CLI 帮助和 schema。"""
TARGET = {
    'doc': {'type': 'string', 'description': 'Key, path, or unambiguous name of an open document; omit to use the active document'},
    'active': {'type': 'boolean', 'description': 'Explicitly use the active document; mutually exclusive with doc'},
}


def schema(name, description, properties=None, required=(), target=True):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': {
            **(TARGET if target else {}), **(properties or {})}, 'required': list(required)},
    }}


inspection_schema = [
    schema('current_document', 'Get the active document identity, type, and save state without activating or modifying it', target=False),
    schema('document_info', 'Get the specified document identity, type, active state, and save state; unreadable properties are null'),
    schema('activate_document', 'Activate the specified open document without opening files implicitly'),
    schema('save_document_as', 'Save the document to another path while preserving the original; CATIA prompts are handled by the task/dialog flow',
           {'file_path': {'type': 'string', 'description': 'Destination Windows file path'}}, ['file_path']),
    schema('close_document', 'Close the specified document; CATIA handles unsaved changes in its dialog, and changes are not discarded automatically'),
    schema('part_info', 'Get CATPart document identity and part name'),
    schema('part_tree', 'Get the Body, sketch, feature, and geometrical-set tree. Returns scope/complete/errors; feature parameters and Boolean-absorbed bodies are not expanded'),
    schema('update_part_document', 'Update the specified CATPart without switching the active document'),
    schema('part_mass', 'Get part mass in kg, local center of gravity in mm, and inertia in kg*mm^2; returns an error if reading fails'),
    schema('drawing_info', 'Read drawing sheets, views, and each view source document (GenerativeBehavior.Document); does not enumerate all underlying geometry links; unreadable items are returned in errors'),
    schema('product_info', 'Get root assembly properties, position, and direct child instance count'),
    schema('product_tree', 'Read the assembly instance tree; repeated references remain separate instances. Node paths become invalid when the tree changes',
            {'node': {'type': 'string', 'description': 'Optional subtree path, for example 0.0; obtain it from the tree result'}}),
    schema('replace_product_component', 'Replace the selected component reference in an open CATProduct. The file must exist; the root cannot be replaced. Default scope is the component definition and can affect occurrences sharing it. Readback verifies only the selected node; all_instances reports the CATIA request, not verification of every occurrence. Changes are not saved automatically.', {
        'node': {'type': 'string', 'description': 'Zero-based product tree node path, excluding root 0'},
        'file_path': {'type': 'string', 'description': 'Existing CATPart or CATProduct replacement file'},
        'all_instances': {'type': 'boolean', 'description': 'Request CATIA ReplaceComponent all-instances mode; defaults to false'},
    }, ['node', 'file_path']),
    schema('set_drawing_source', 'Set a generative drawing view source to an already open CATPart or CATProduct. Updates the view but does not save either document.', {
        'sheet': {'type': 'string', 'description': 'Exact sheet name'},
        'view': {'type': 'string', 'description': 'Exact view name'},
        'source_doc': {'type': 'string', 'description': 'Key, path, or unambiguous name of an open CATPart or CATProduct'},
    }, ['sheet', 'view', 'source_doc']),
    schema('merge_mass_files', 'Offline-merge MPD files in the same root coordinate system using existing mass rollup; coordinates are not transformed', {
        'files': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Non-empty array of MPD paths or an @JSON file'},
        'output_file': {'type': 'string', 'description': 'Output MPD path'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'description': 'Policy for existing output; defaults to error'},
    }, ['files', 'output_file'], target=False),
    schema('remeasure_product_part', 'Re-read saved inertia measurements for an open part; does not force Analyze or modify MPD; returns kg/mm/kg*mm^2 data', {
        'file_path': {'type': 'string', 'description': 'Full path of the open referenced part'},
        'part_number': {'type': 'string', 'description': 'Part number used as the measurement-parameter prefix; defaults to empty'},
        'read_mode': {'type': 'string', 'enum': ['first', 'last', 'all'], 'description': 'Range of saved measurements to read; defaults to all'},
    }, ['file_path'], target=False),
    schema('export_product_step', 'Export a full CATProduct or file set to STEP; instance --node is unsupported. The active assembly must have a file path. Returns per-file results', {
        'file_paths': {'type': 'array', 'items': {'type': 'string'}, 'description': 'CATProduct paths; alias --files; mutually exclusive with doc/active'},
        'output_folder': {'type': 'string', 'description': 'Output directory; defaults to the source directory'},
        'prefix': {'type': 'string', 'description': 'Filename prefix; defaults to MD_'},
        'suffix': {'type': 'string', 'description': 'Filename suffix; defaults to empty'},
        'overwrite': {'type': 'string', 'enum': ['error', 'skip', 'replace'], 'description': 'Overwrite policy; defaults to error'},
    }),
]
