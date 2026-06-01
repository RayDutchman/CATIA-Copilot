"""
空对话框模板。

提供：
- TemplateDialog – 可复用的空对话框模板，包含完整的窗口行为和主题支持。

使用说明：
1. 复制本文件，重命名为你的对话框名称
2. 修改 _SETTINGS_KEY 为唯一的键名（避免与其他对话框共用 QSettings）
3. 修改 setWindowTitle、setMinimumSize、resize 为合适的值
4. 在 _setup_ui() 中添加你的 UI 组件
5. 在 main_window.py 中添加对应的 _open_xxx_dialog() 方法和按钮
"""

import logging
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

logger = logging.getLogger(__name__)


class TemplateDialog(QDialog):
    """空对话框模板。

    包含：
    - 窗口几何持久化（位置和尺寸），在 showEvent 首次显示时恢复，
      避免 _show_dialog 中 setParent(None) 重建原生窗口后位置被重置
    - 主题跟随（通过 QApplication.setStyleSheet 全局生效，无需额外注册）
    - 标准按钮布局
    - 可缩放、最小化、最大化
    """

    # 修改此键名以区分不同对话框的 QSettings 存储
    _SETTINGS_KEY = "TemplateDialog"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模板对话框")
        self.setMinimumSize(500, 500)
        self.resize(500, 500)

        self._settings = QSettings("CATIACompanion", self._SETTINGS_KEY)
        self._geometry_restored = False   # 标记：首次 showEvent 时恢复几何

        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。在这里添加你的 UI 组件。"""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 内容区域 ──────────────────────────────────────────────────────
        label = QLabel("这是一个空对话框模板。\n\n在这里添加你的 UI 组件。")
        label.setWordWrap(True)
        layout.addWidget(label)

        layout.addStretch()

        # ── 按钮区域 ──────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def showEvent(self, event):  # noqa: N802
        """首次显示时恢复窗口几何，避免 setParent 重建原生窗口后位置丢失。"""
        super().showEvent(event)
        if not self._geometry_restored:
            self._geometry_restored = True
            saved = self._settings.value("geometry")
            if isinstance(saved, QByteArray) and not saved.isEmpty():
                self.restoreGeometry(saved)

    def closeEvent(self, event):  # noqa: N802
        """关闭时保存窗口几何（位置和尺寸）。"""
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
