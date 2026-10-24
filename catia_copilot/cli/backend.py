"""Windows 执行适配；原工具默认值与返回结构保持不变。"""
import sys
import pythoncom
from catia_copilot.catia.tool_service import tools_map
from catia_copilot.catia import file_interaction


class Progress:
    def emit(self, message):
        print(message, file=sys.stderr)


def execute(name, parameters):
    kwargs = dict(parameters)
    overwrite = kwargs.pop('overwrite', 'error')
    pythoncom.CoInitialize()
    old_handler = file_interaction.overwrite_handler
    if overwrite in ('replace', 'skip'):
        file_interaction.overwrite_handler = lambda dest: 'overwrite_all' if overwrite == 'replace' else 'skip_all'
    try:
        return tools_map[name](**kwargs, progress_signal=Progress())
    finally:
        file_interaction.overwrite_handler = old_handler
        pythoncom.CoUninitialize()
