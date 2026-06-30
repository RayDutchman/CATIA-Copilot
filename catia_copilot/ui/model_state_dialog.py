#! usr/bin/python3
"""
AI 建模状态面板 —— 非模态子窗口，展示当前零件特征、质量、步骤日志。
数据由 AIChatPanel 在 tool_run_modeling_script 执行完成后推送。
"""

import json
import os as _os

from PySide6.QtCore import Qt, QByteArray, QSettings
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QHeaderView,
)

_STYLE_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "styles", "native.qss")


class ModelStateDialog(QDialog):
    """非模态窗口，展示 AI 建模后的零件状态。

    用法（AIChatPanel 中）::

        dialog = ModelStateDialog(None)
        dialog.set_state(parsed_json)   # 从 tool_run_modeling_script 返回 JSON 解析
        dialog.show()
    """

    _SETTINGS = None

    # ── 常量 ──────────────────────────────────────────────────────────
    MIN_WIDTH = 420
    MIN_HEIGHT = 480
    DEFAULT_WIDTH = 500
    DEFAULT_HEIGHT = 600

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_state: dict | None = None
        self._init_ui()
        self._load_geometry()
        self._apply_style()

    # ── 公共接口 ──────────────────────────────────────────────────────
    def set_state(self, state: dict) -> None:
        """根据 tool_run_modeling_script 返回的 JSON dict 刷新面板。

        参数 dict 字段（均为可选）：
          success      : bool
          part_name    : str
          features     : list[str]
          steps        : list[dict]  — step/status/features_after
          mass_kg      : float
          cog_mm       : list[float]
          error        : str  — 失败时
        """
        self._last_state = state
        self._update_title(state)
        self._update_features(state)
        self._update_mass_cog(state)
        self._update_steps(state)

    # ── UI 构建 ───────────────────────────────────────────────────────
    def _init_ui(self) -> None:
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        # 关闭时自动删除 C++ 对象，避免残留
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ── 标题行 ───────────────────────────────────────────────
        self._title_label = QLabel("模型状态")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        root.addWidget(self._title_label)

        # ── 分区：特征树 ──────────────────────────────────────────
        feat_label = QLabel("特征树")
        feat_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        root.addWidget(feat_label)

        self._feat_tree = QTreeWidget()
        self._feat_tree.setHeaderHidden(True)
        self._feat_tree.setRootIsDecorated(True)
        self._feat_tree.setAnimated(True)
        self._feat_tree.setUniformRowHeights(True)
        self._feat_tree.header().setStretchLastSection(True)
        self._feat_tree.setMaximumHeight(220)
        root.addWidget(self._feat_tree)

        # ── 分区：质量属性 ────────────────────────────────────────
        mass_label = QLabel("质量属性")
        mass_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        root.addWidget(mass_label)

        mass_form = QFormLayout()
        mass_form.setContentsMargins(4, 2, 4, 2)
        mass_form.setSpacing(2)

        self._mass_value = QLabel("—")
        mass_form.addRow("质量：", self._mass_value)

        self._cog_value = QLabel("—")
        mass_form.addRow("重心：", self._cog_value)

        root.addLayout(mass_form)

        # ── 分区：步骤日志 ────────────────────────────────────────
        steps_label = QLabel("步骤日志")
        steps_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        root.addWidget(steps_label)

        self._steps_log = QPlainTextEdit()
        self._steps_log.setReadOnly(True)
        self._steps_log.setMaximumBlockCount(100)
        font = self._steps_log.font()
        font.setPointSize(8)
        self._steps_log.setFont(font)
        root.addWidget(self._steps_log, stretch=1)

        # ── 底部：关闭按钮 ────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        root.addLayout(btn_layout)

    # ── 更新子组件 ────────────────────────────────────────────────────
    def _update_title(self, state: dict) -> None:
        part_name = state.get("part_name", "—")
        success = state.get("success", None)
        if success is False:
            self._title_label.setText("模型状态 — 失败")
        elif part_name and part_name != "—":
            self._title_label.setText(f"模型状态 — {part_name}")
        else:
            self._title_label.setText("模型状态 — 无数据")

    def _update_features(self, state: dict) -> None:
        self._feat_tree.clear()
        features = state.get("features", [])
        if not features:
            item = QTreeWidgetItem(self._feat_tree, ["无特征"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            return

        # 根节点：零件几何体（CATIA 默认 Body）
        body = QTreeWidgetItem(self._feat_tree, ["零件几何体"])
        body.setExpanded(True)
        for fn in features:
            child = QTreeWidgetItem(body, [fn])
            child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        body.setFlags(body.flags() & ~Qt.ItemFlag.ItemIsSelectable)

    def _update_mass_cog(self, state: dict) -> None:
        mass = state.get("mass_kg")
        cog = state.get("cog_mm")

        if mass is not None:
            self._mass_value.setText(f"{mass:.3f} kg")
        else:
            self._mass_value.setText("—（未赋材料或无数据）")

        if cog is not None and len(cog) == 3:
            self._cog_value.setText(f"({cog[0]:.1f}, {cog[1]:.1f}, {cog[2]:.1f}) mm")
        else:
            self._cog_value.setText("—")

    def _update_steps(self, state: dict) -> None:
        self._steps_log.clear()
        steps = state.get("steps", [])
        if not steps:
            self._steps_log.setPlainText("— 无步骤记录 —")
            return

        lines = []
        for i, s in enumerate(steps, 1):
            step_name = s.get("step", "?")
            status = s.get("status", "?")
            symbol = "✓" if status == "ok" else "✗"
            lines.append(f"[{i:02d}] {symbol} {step_name}")

        self._steps_log.setPlainText("\n".join(lines))

    # ── 窗口几何持久化 ────────────────────────────────────────────────
    def _settings(self):
        if ModelStateDialog._SETTINGS is None:
            ModelStateDialog._SETTINGS = QSettings("CATIA-Copilot", "ModelStateDialog")
        return ModelStateDialog._SETTINGS

    def _load_geometry(self) -> None:
        saved = self._settings().value("geometry")
        if isinstance(saved, QByteArray) and not saved.isEmpty():
            self.restoreGeometry(saved)

    def _save_geometry(self) -> None:
        self._settings().setValue("geometry", self.saveGeometry())

    def done(self, result: int) -> None:
        self._save_geometry()
        super().done(result)

    def _apply_style(self) -> None:
        if _os.path.isfile(_STYLE_PATH):
            with open(_STYLE_PATH, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
