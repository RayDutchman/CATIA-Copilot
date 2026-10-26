"""文件冲突回调：业务模块不依赖 GUI，调用端提供原有交互。"""
from pathlib import Path
from typing import Callable

overwrite_handler: Callable[[Path], str] | None = None
locked_handler: Callable[[Path], bool] | None = None
locked_error_handler: Callable[[Path], None] | None = None


def prompt_overwrite(dest: Path) -> str:
    if overwrite_handler is None:
        raise FileExistsError(f"Output file already exists: {dest}; use --overwrite to specify replace or skip")
    return overwrite_handler(dest)


def retry_locked(dest: Path) -> bool:
    if locked_handler is None:
        raise PermissionError(f"File is in use; close it and try again: {dest}")
    return locked_handler(dest)


def report_locked(dest: Path) -> None:
    if locked_error_handler is None:
        raise PermissionError(f"File is still in use: {dest}")
    locked_error_handler(dest)
