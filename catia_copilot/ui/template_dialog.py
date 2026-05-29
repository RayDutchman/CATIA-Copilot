"""
空对话框模板。

提供：
- TemplateDialog – 可复用的空对话框模板，包含完整的窗口行为和主题支持。
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDialogButtonBox,
)
from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)


class TemplateDialog(QDialog):
    """空对话框模板。
    
    包含：
    - 窗口几何持久化（位置和尺寸）
    - 主题管理器注册
    - 标准按钮布局
    - 可缩放、最小化、最大化
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模板对话框")
        self.setMinimumSize(480, 360)
        self.resize(640, 480)

        self._settings = QSettings("CATIACompanion", "TemplateDialog")

        # 恢复窗口几何
        saved_geom = self._settings.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        # 注册主题管理器
        try:
            from catia_copilot.ui.theme_manager import theme_manager
            theme_manager.register(self)
        except Exception:
            pass

        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
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

    def closeEvent(self, event):  # noqa: N802
        """关闭时保存窗口几何。"""
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
