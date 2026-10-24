"""文件冲突回调：业务模块不依赖 GUI，调用端提供原有交互。"""
from pathlib import Path
from typing import Callable

overwrite_handler: Callable[[Path], str] | None = None
locked_handler: Callable[[Path], bool] | None = None
locked_error_handler: Callable[[Path], None] | None = None


def prompt_overwrite(dest: Path) -> str:
    if overwrite_handler is None:
        raise FileExistsError(f"输出文件已存在：{dest}；请通过 --overwrite 指定 replace 或 skip")
    return overwrite_handler(dest)


def retry_locked(dest: Path) -> bool:
    if locked_handler is None:
        raise PermissionError(f"文件正在使用，请关闭后重试：{dest}")
    return locked_handler(dest)


def report_locked(dest: Path) -> None:
    if locked_error_handler is None:
        raise PermissionError(f"文件仍在使用：{dest}")
    locked_error_handler(dest)
