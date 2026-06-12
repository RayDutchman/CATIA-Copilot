"""
CATIA 宏执行模块。

提供 run_macro()，通过 CATIA SystemService.ExecuteScript 执行宏文件：

- .catvba：VBA 项目文件模式（CATIA_MACRO_LIBRARY_VBA）
- .catvbs / .catscript：目录模式（CATIA_MACRO_LIBRARY_DIR）

ExecuteScript 签名::

    SystemService.ExecuteScript(iLibraryName, iLibraryType,
                                iProgramName, iFunctionName, iParameters)

.catvba（VBA 项目文件模式）：
    iLibraryName = .catvba 文件完整路径
    iLibraryType = CATIA_MACRO_LIBRARY_VBA
    iProgramName = VBA 模块名（module_name）

.catvbs / .catscript（目录模式）：
    iLibraryName = 宏文件所在目录
    iLibraryType = CATIA_MACRO_LIBRARY_DIR
    iProgramName = 宏文件名（含扩展名）

catia_copilot.catvba 模块注册表
--------------------------------
CATIA_COPILOT_MODULES 记录合并宏文件 catia_copilot.catvba 内所有已知模块的名称。
新增模块时在此处追加，调用方通过 CATIA_COPILOT_MODULES["功能key"] 取模块名。

当前已注册模块：
    fastener_assembly   快速装配紧固件
    nut_plate_assembly  快速装配托板螺母
"""

import logging
from pathlib import Path

from catia_copilot.catia.connection import get_catia_v5_application
from catia_copilot.constants import CATIA_MACRO_LIBRARY_DIR, CATIA_MACRO_LIBRARY_VBA

logger = logging.getLogger(__name__)

# catia_copilot.catvba 内已知模块的注册表
# key：功能标识，value：VBA 模块名
# 新增模块时在此处追加一行即可，调用方无需改动。
CATIA_COPILOT_MODULES: dict[str, str] = {
    "fastener_assembly":  "fastener_assembly",
    "nut_plate_assembly": "nut_plate_assembly",
}


def run_macro(
    macro_path: Path,
    module_name: str | None = None,
    params: list | None = None,
) -> None:
    """通过 CATIA SystemService.ExecuteScript 执行宏文件。

    :param macro_path:  宏文件路径（.catvba / .catvbs / .catscript）
    :param module_name: 仅 .catvba 有效。指定 VBA 模块名；None 时依次尝试
                        "模块1"（中文 CATIA）和 "Module1"（英文/法语）兼容轮询。
    :param params:      传递给宏 CATMain 的参数列表，默认为空列表。
    :raises FileNotFoundError: 宏文件不存在时抛出。
    :raises Exception:         CATIA 执行宏失败时抛出。
    """
    if not macro_path.exists():
        raise FileNotFoundError(f"宏文件不存在：{macro_path}")

    _params = params or []
    app = get_catia_v5_application()

    if macro_path.suffix.lower() == ".catvba":
        if module_name is not None:
            app.SystemService.ExecuteScript(
                str(macro_path), CATIA_MACRO_LIBRARY_VBA,
                module_name, "CATMain", _params,
            )
        else:
            # 兼容轮询：依次尝试中文/英文默认模块名
            last_exc: Exception | None = None
            for m in ("模块1", "Module1"):
                try:
                    app.SystemService.ExecuteScript(
                        str(macro_path), CATIA_MACRO_LIBRARY_VBA,
                        m, "CATMain", _params,
                    )
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
            if last_exc is not None:
                raise last_exc
    else:
        # .catvbs / .catscript — CATScript 目录模式
        app.SystemService.ExecuteScript(
            str(macro_path.parent), CATIA_MACRO_LIBRARY_DIR,
            macro_path.name, "CATMain", _params,
        )

    logger.info("宏执行成功：%s", macro_path.name)
