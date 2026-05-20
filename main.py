"""
CATIA Copilot - 应用程序入口点。

所有应用逻辑都在 ``catia_copilot`` 包中实现。
本文件负责启动 Qt 应用程序、初始化主题并显示主窗口。
"""

import sys

# 确保在创建任何控件之前初始化日志系统和 Qt 信号发射器
import catia_copilot.logging_setup  # noqa: F401

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from catia_copilot.utils import resource_path, ensure_clean_gencache
from catia_copilot.constants import APP_ICON_PATH
from catia_copilot.ui.main_window import MainWindow


def main() -> None:
    """应用程序主入口函数。

    初始化 Qt 应用程序，加载主题，显示主窗口。
    """
    # 清理 win32com 早绑定缓存，防止 gencache 污染 COM 连接
    ensure_clean_gencache()

    app = QApplication(sys.argv)
    app.setApplicationName("CATIA Copilot")

    # 设置应用程序图标（resources/icon.ico）
    icon_path = resource_path(APP_ICON_PATH)
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    # 将应用图标同步到自定义标题栏
    if not app.windowIcon().isNull():
        window._title_bar.set_app_icon(app.windowIcon())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
