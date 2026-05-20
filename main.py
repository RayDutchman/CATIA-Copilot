"""
CATIA Copilot - 应用程序入口点。

所有应用逻辑都在 ``catia_copilot`` 包中实现。
本文件负责启动 Qt 应用程序、初始化主题并显示主窗口。
"""

import sys

# 确保在创建任何控件之前初始化日志系统和 Qt 信号发射器
import catia_copilot.logging_setup  # noqa: F401

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from catia_copilot.utils import resource_path, ensure_clean_gencache
from catia_copilot.constants import APP_ICON_PATH
from catia_copilot.ui.main_window import MainWindow


def main() -> None:
    """应用程序主入口函数。

    初始化 Qt 应用程序，加载主题，显示主窗口。
    """
    # 必须在 QApplication 创建之前设置 DPI 舍入策略。
    # PassThrough：不对缩放因子做四舍五入，直接使用 1.25/1.5 等精确值。
    # 在多显示器混合 DPI 场景下（例如主屏 150%、副屏 100%），Qt 的坐标换算
    # 与 WM_NCHITTEST 补丁中的 QCursor.pos()+mapFromGlobal() 保持一致，
    # 避免舍入误差导致 resize 热区在窗口跨屏时出现微小偏移。
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

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
