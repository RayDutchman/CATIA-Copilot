"""python -m catia_copilot.cli 入口。"""
import sys
sys.coinit_flags = 2  # CATIA worker 与 Win32 弹窗读取统一使用 STA。
from catia_copilot.cli.app import main


def _execute(name, args):
    # 元信息命令不会触发此导入；业务执行时再加载可能依赖 COM 的任务模块。
    from catia_copilot.cli.comtypes_runtime import prepare_com_runtime
    prepare_com_runtime()
    from catia_copilot.cli.tasks import execute
    return execute(name, args)


def entrypoint():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    if len(sys.argv) >= 3 and sys.argv[1] == '_worker':
        root = sys.argv[3] if len(sys.argv) > 3 else None
        if len(sys.argv) > 4:
            log = open(sys.argv[4], 'a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = log
        from catia_copilot.cli.comtypes_runtime import prepare_com_runtime
        prepare_com_runtime()
        from catia_copilot.cli.tasks import run_worker
        return run_worker(sys.argv[2], root=root)
    return main(executor=_execute)


if __name__ == '__main__':
    raise SystemExit(entrypoint())
