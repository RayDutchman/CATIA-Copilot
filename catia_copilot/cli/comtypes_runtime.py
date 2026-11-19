"""为CLI在目标机生成类型库包装器，不使用构建机的comtypes.gen缓存。"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import warnings

_prepared = False


def cache_directory():
    import comtypes
    home = Path(os.environ.get('CAT5CLI_HOME') or
                Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Cat5cli')
    identity = f'py{sys.version_info.major}{sys.version_info.minor}-{struct.calcsize("P") * 8}-{comtypes.__version__}'
    return (home / 'comtypes_cache' / identity).resolve()


def _cache_lock_name(cache):
    # Windows同一目录可能返回普通路径或\\?\前缀；用目录身份而非路径文本定锁名。
    identity = cache.stat()
    key = hashlib.sha256(f'{identity.st_dev}:{identity.st_ino}'.encode('ascii')).hexdigest()[:24]
    return 'Local\\Cat5cliComtypes-' + key


def prepare_com_runtime():
    """首次生成串行化；类型库变化仍交给comtypes原生校验和重新生成。"""
    global _prepared
    if _prepared:
        return
    sys.coinit_flags = 2
    import win32event
    import comtypes.gen

    cache = cache_directory()
    cache.mkdir(parents=True, exist_ok=True)
    mutex = win32event.CreateMutex(None, False, _cache_lock_name(cache))
    acquired = False
    try:
        status = win32event.WaitForSingleObject(mutex, 60000)
        if status not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise RuntimeError('Timed out while preparing the local COM type-library cache')
        acquired = True
        # 先重定向包搜索路径，client初始化时就不会访问安装目录中的旧生成文件。
        comtypes.gen.__path__ = [str(cache)]
        import comtypes.client
        comtypes.client.gen_dir = str(cache)
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Apply externally defined coinit_flags: 2',
                                    category=UserWarning, module='pywinauto')
            # pywinauto导入时生成UIAutomation/Accessibility及其依赖，在锁内完成。
            import pywinauto  # noqa: F401
        _prepared = True
    finally:
        if acquired:
            win32event.ReleaseMutex(mutex)
        mutex.Close()
