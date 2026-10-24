"""纯参数解析与工具分发；不连接 CATIA。"""
import argparse
from contextlib import redirect_stdout
import json
from importlib import metadata
from pathlib import Path
import sys
from catia_copilot.catia.tool_schema import tools_schema

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
    ('drawing', 'create'): 'generate_drawing',
    ('drawing', 'refresh'): 'refresh_drawing',
    ('drawing', 'export', 'pdf'): 'convert_to_pdf',
    ('part', 'export', 'step'): 'convert_to_step',
    ('part', 'template', 'apply'): 'apply_part_template',
    ('product', 'mass', 'collect'): 'collect_mass_props',
    ('run',): 'run_modeling_script',
}
SCHEMAS = {s['function']['name']: s for s in tools_schema}
CONTROL_COMMANDS = {('task', 'status'): '_task_status', ('task', 'wait'): '_task_wait',
                    ('dialog', 'respond'): '_dialog_respond'}
CONTROL_SCHEMAS = {
    name: {'type': 'function', 'function': {'name': name, 'description': description,
          'parameters': {'type': 'object', 'properties': properties, 'required': ['task']}}}
    for name, description, properties in (
        ('_task_status', '读取原任务状态与当前弹窗，不重新执行业务', {'task': {'type': 'string'}}),
        ('_task_wait', '等待原任务完成或出现弹窗；超时返回 running，不取消任务',
         {'task': {'type': 'string'}, 'timeout': {'type': 'number', 'description': '等待秒数，默认30'}}),
        ('_dialog_respond', '处理上次返回的弹窗并继续原任务；button/control 使用返回的控件 id',
         {'task': {'type': 'string'}, 'button': {'type': 'string'}, 'control': {'type': 'string'},
          'text': {'type': 'string'}, 'timeout': {'type': 'number', 'description': '继续等待秒数，默认30'}}),
    )
}


def version_text():
    info = Path(__file__).with_name('_build_version.json')
    if info.is_file():
        return 'catkit ' + json.loads(info.read_text(encoding='utf-8'))['version']
    try:
        return 'catkit ' + metadata.version('catia-copilot')
    except metadata.PackageNotFoundError:
        return 'catkit (source)'


def json_argument(value):
    try:
        text = Path(value[1:]).read_text(encoding='utf-8-sig') if value.startswith('@') else value
        return json.loads(text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser():
    parser = argparse.ArgumentParser(prog='catkit', description='CATIA 工具命令行')
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
            leaf.add_argument('--' + key.replace('_', '-'), **options)
        if name == 'run_modeling_script':
            scripts = leaf.add_mutually_exclusive_group(required=True)
            scripts.add_argument('--file', dest='_script_file')
            scripts.add_argument('--script')
        if name == 'check_catia_connection':
            leaf.add_argument('--diagnose', action='store_true')
        if name in ('convert_to_pdf', 'convert_to_step'):
            leaf.add_argument('--overwrite', choices=['error', 'skip', 'replace'], default=argparse.SUPPRESS)
    schema = groups[()].add_parser('schema', help='查询工具参数契约',
        description='路径按命令逐词填写，例如 catkit schema bom export 或 catkit schema drawing export pdf；不填路径列出全部命令。')
    schema.add_argument('path', nargs='*', help='空格分隔的命令路径，不使用点号或原工具函数名')
    schema.add_argument('--diagnose', action='store_true', help='查询 status 的详细诊断契约')
    schema.set_defaults(_tool='_schema', pretty=True, verbose=False)
    return parser


def result_exit_code(payload):
    if not isinstance(payload, dict):
        return 0
    if payload.get('error') or payload.get('success') is False or payload.get('failed_count', 0):
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
                    print(json.dumps({'error': '未知命令路径：'+' '.join(path)}, ensure_ascii=False))
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
                raise RuntimeError('请使用 python -m catia_copilot.cli 或 catkit 入口')
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
