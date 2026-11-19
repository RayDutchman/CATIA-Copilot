"""纯参数解析与工具分发；不连接 CATIA。"""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from catia_copilot.catia.tool_schema import tools_schema
from .version import CLI_VERSION

COMMANDS = {
    ('status',): 'check_catia_connection',
    ('document', 'open'): 'open_catia_file',
    ('document', 'list'): 'get_open_documents',
    ('document', 'save'): 'save_catia_document',
    ('document', 'properties', 'get'): 'get_document_properties',
    ('document', 'properties', 'set'): 'set_document_properties',
    ('document', 'dependencies', 'list'): 'find_dependencies',
    ('document', 'dependencies', 'reverse'): 'find_reverse_dependencies',
    ('document', 'find', 'part'): 'find_part_for_drawing',
    ('document', 'find', 'drawing'): 'find_drawing_for_part',
    ('bom', 'collect'): 'collect_bom',
    ('bom', 'export'): 'export_bom_to_excel',
    ('bom', 'write'): 'write_bom_to_catia',
    ('bom', 'edit'): 'edit_bom',
    ('drawing', 'create'): 'generate_drawing',
    ('drawing', 'refresh'): 'refresh_drawing',
    ('drawing', 'export', 'pdf'): 'convert_to_pdf',
    ('part', 'export', 'step'): 'convert_to_step',
    ('part', 'template', 'apply'): 'apply_part_template',
    ('product', 'mass', 'collect'): 'collect_mass_props',
    ('run',): 'run_modeling_script',
    ('document', 'current'): 'current_document',
    ('document', 'info'): 'document_info',
    ('document', 'activate'): 'activate_document',
    ('document', 'save-as'): 'save_document_as',
    ('document', 'close'): 'close_document',
    ('part', 'info'): 'part_info',
    ('part', 'tree'): 'part_tree',
    ('part', 'update'): 'update_part_document',
    ('part', 'mass'): 'part_mass',
    ('drawing', 'info'): 'drawing_info',
    ('product', 'info'): 'product_info',
    ('product', 'tree'): 'product_tree',
    ('product', 'component', 'replace'): 'replace_product_component',
    ('drawing', 'source', 'set'): 'set_drawing_source',
    ('product', 'mass', 'merge'): 'merge_mass_files',
    ('product', 'mass', 'remeasure'): 'remeasure_product_part',
    ('product', 'export', 'step'): 'export_product_step',
}
SCHEMAS = {s['function']['name']: s for s in tools_schema}
CONTROL_COMMANDS = {('task', 'status'): '_task_status', ('task', 'wait'): '_task_wait',
                    ('dialog', 'respond'): '_dialog_respond'}
CONTROL_SCHEMAS = {
    name: {'type': 'function', 'function': {'name': name, 'description': description,
          'parameters': {'type': 'object', 'properties': properties, 'required': ['task']}}}
    for name, description, properties in (
        ('_task_status', 'Read the original task status and current dialog without rerunning the operation', {'task': {'type': 'string'}}),
        ('_task_wait', 'Wait for the original task to complete or show a dialog; timeout returns running and does not cancel the task',
         {'task': {'type': 'string'}, 'timeout': {'type': 'number', 'description': 'Wait time in seconds (default: 30)'}}),
        ('_dialog_respond', 'Respond to the last dialog and resume the original task; use returned control IDs for button/control',
         {'task': {'type': 'string'}, 'button': {'type': 'string'}, 'control': {'type': 'string'},
          'text': {'type': 'string'}, 'timeout': {'type': 'number', 'description': 'Additional wait time in seconds (default: 30)'}}),
    )
}


def version_text():
    info = Path(__file__).with_name('_build_version.json')
    if info.is_file():
        return 'cat5cli ' + json.loads(info.read_text(encoding='utf-8'))['version']
    return 'cat5cli ' + CLI_VERSION


def json_argument(value):
    try:
        text = Path(value[1:]).read_text(encoding='utf-8-sig') if value.startswith('@') else value
        return json.loads(text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


class UniqueFiles(argparse.Action):
    """文件数组别名只接受一次，避免后一个参数静默覆盖前一个。"""
    def __call__(self, parser, namespace, values, option_string=None):
        if hasattr(namespace, self.dest):
            raise argparse.ArgumentError(self, 'File arrays can only be specified once, including the --files alias')
        setattr(namespace, self.dest, values)


def build_parser():
    parser = argparse.ArgumentParser(prog='cat5cli', description='CATIA command-line tools')
    parser.add_argument('--version', action='version', version=version_text())
    parsers = {(): parser}
    groups = {}
    for path, name in (COMMANDS | CONTROL_COMMANDS).items():
        for depth in range(1, len(path) + 1):
            prefix, parent = path[:depth], path[:depth - 1]
            if prefix not in parsers:
                if parent not in groups:
                    groups[parent] = parsers[parent].add_subparsers(required=True)
                parsers[prefix] = groups[parent].add_parser(prefix[-1])
        leaf = parsers[path]
        spec = (SCHEMAS | CONTROL_SCHEMAS)[name]['function']
        leaf.description = spec['description']
        leaf.set_defaults(_tool=name)
        leaf.add_argument('--pretty', action='store_true')
        leaf.add_argument('--verbose', action='store_true')
        parameters = spec['parameters']
        target_group = leaf.add_mutually_exclusive_group() if 'doc' in parameters.get('properties', {}) else None
        for key, field in parameters.get('properties', {}).items():
            if name == 'run_modeling_script' and key == 'script':
                continue
            kind = field.get('type', 'string')
            if isinstance(kind, list):
                kind = next((k for k in kind if k != 'null'), 'string')
            options = dict(dest=key, default=argparse.SUPPRESS,
                           required=key in parameters.get('required', []), help=field.get('description'))
            if kind == 'boolean':
                options['action'] = argparse.BooleanOptionalAction
            elif kind in ('array', 'object'):
                options['type'] = json_argument
            elif kind in ('number', 'integer'):
                options['type'] = float if kind == 'number' else int
            else:
                options['type'] = str
            if 'enum' in field:
                options['choices'] = field['enum']
            destination = target_group if target_group is not None and key in ('doc', 'active', 'file_paths') else leaf
            flags = ['--' + key.replace('_', '-')]
            if key == 'file_paths':
                flags.append('--files')
                options['action'] = UniqueFiles
            destination.add_argument(*flags, **options)
        if name == 'run_modeling_script':
            scripts = leaf.add_mutually_exclusive_group(required=True)
            scripts.add_argument('--file', dest='_script_file')
            scripts.add_argument('--script')
        if name == 'check_catia_connection':
            leaf.add_argument('--diagnose', action='store_true')
        if name in ('convert_to_pdf', 'convert_to_step'):
            leaf.add_argument('--overwrite', choices=['error', 'skip', 'replace'], default=argparse.SUPPRESS)
    schema = groups[()].add_parser('schema', help='Inspect tool parameter schemas',
        description='Enter command paths as separate words, such as cat5cli schema bom export or cat5cli schema drawing export pdf. Omit the path to list all commands.')
    schema.add_argument('path', nargs='*', help='Space-separated command path; do not use dots or tool function names')
    schema.add_argument('--diagnose', action='store_true', help='Inspect the detailed schema for status diagnostics')
    schema.set_defaults(_tool='_schema', pretty=True, verbose=False)
    return parser


def result_exit_code(payload):
    if not isinstance(payload, dict):
        return 0
    if payload.get('error') or payload.get('success') is False or payload.get('failed_count', 0):
        return 1
    if payload.get('complete') is False:
        return 1
    if payload.get('status') in ('disconnected', 'broken', 'not_connected'):
        return 3
    verification = payload.get('verification')
    if isinstance(verification, dict) and verification.get('status') == 'failed':
        return 1
    return 0


def main(argv=None, *, executor=None):
    parser = build_parser()
    try:
        args = vars(parser.parse_args(argv))
    except SystemExit as exc:
        return int(exc.code)
    name = args.pop('_tool')
    pretty = args.pop('pretty', False)
    verbose = args.pop('verbose', False)
    try:
        if name == '_schema':
            path = tuple(args['path'])
            if args.get('diagnose'):
                path += ('--diagnose',)
            if path == ('status', '--diagnose'):
                payload = SCHEMAS['diagnose_catia_connection']
            elif path:
                if path not in COMMANDS and path not in CONTROL_COMMANDS:
                    print(json.dumps({'error': 'Unknown command path: '+' '.join(path)}, ensure_ascii=False))
                    return 2
                payload = (SCHEMAS | CONTROL_SCHEMAS)[(COMMANDS | CONTROL_COMMANDS)[path]]
            else:
                payload = {'commands': [{'path': list(p), 'tool': t} for p, t in COMMANDS.items()],
                           'diagnostics': {'path': ['status', '--diagnose'], 'tool': 'diagnose_catia_connection'},
                           'continuation': [{'path': list(p), 'tool': t} for p, t in CONTROL_COMMANDS.items()]}
        else:
            if args.pop('diagnose', False):
                name = 'diagnose_catia_connection'
            script_file = args.pop('_script_file', None)
            if script_file is not None:
                args['script'] = Path(script_file).read_text(encoding='utf-8-sig')
            if args.get('script', object()) is None:
                args.pop('script')
            if executor is None:
                raise RuntimeError('Use the python -m catia_copilot.cli or cat5cli entry point')
            # 业务函数中偶发 print 也不能污染 JSON。
            with redirect_stdout(sys.stderr):
                result = executor(name, args)
            payload = json.loads(result) if isinstance(result, str) else result
        print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, default=str))
        return result_exit_code(payload)
    except Exception as exc:
        if verbose:
            print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1
