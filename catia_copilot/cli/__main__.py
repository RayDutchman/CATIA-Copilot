"""python -m catia_copilot.cli 入口。"""
import sys
sys.coinit_flags = 2  # CATIA worker 与 Win32 弹窗读取统一使用 STA。
from catia_copilot.cli.app import main
from catia_copilot.cli.tasks import execute, run_worker


def entrypoint():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    if len(sys.argv) == 3 and sys.argv[1] == '_worker':
        return run_worker(sys.argv[2])
    return main(executor=execute)


if __name__ == '__main__':
    raise SystemExit(entrypoint())
