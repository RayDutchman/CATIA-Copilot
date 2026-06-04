"""
per-session 设置面板。

触发位置：AI 聊天面板输入框下方的 ⚙ 按钮。
内容：
  - 会话专属模型（覆盖全局默认）
  - Temperature（None = 跟随全局）
  - 上下文消息数上限
  - 工作空间路径（None = 不限制）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QSlider,
    QComboBox, QDialogButtonBox, QFileDialog, QWidget,
    QCheckBox, QMessageBox,
)

from catia_copilot.ai import config as ai_config
from catia_copilot.ai.session import ChatSession

logger = logging.getLogger(__name__)

# 滑块"未设置"位置对应的特殊值
_TEMP_UNSET_POS  = -1   # 滑块最左端 → None
_TEMP_MIN        = 0    # 对应 0.0
_TEMP_MAX        = 20   # 对应 2.0（步长 0.1）


class SessionConfigDialog(QDialog):
    """
    per-session 设置对话框。

    接收当前 ChatSession，用户确认后将修改写回 session 对象（不直接持久化，
    由调用方负责调用 session_manager.save_session()）。
    """

    def __init__(self, session: ChatSession, parent=None, on_clear=None):
        super().__init__(parent)
        self._session = session
        self._on_clear = on_clear  # 可选回调：清空消息后调用
        self.setWindowTitle("会话设置")
        self.setMinimumWidth(460)
        self._build_ui()

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 标题提示
        title = QLabel(f"会话：{self._session.name}")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setSpacing(8)

        # ── 模型 ──────────────────────────────────────────────────────────────
        cfg = ai_config.load()
        model_ids = ai_config.list_model_ids(cfg)
        global_default = ai_config.get_default_model_id(cfg)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItem(f"使用全局默认（{global_default}）", userData="")
        for mid in model_ids:
            self._model_combo.addItem(mid, userData=mid)

        current_model = self._session.model or ""
        if current_model:
            idx = self._model_combo.findData(current_model)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                # 手动输入的模型 ID
                self._model_combo.setCurrentText(current_model)
        else:
            self._model_combo.setCurrentIndex(0)

        form.addRow("模型：", self._model_combo)

        # ── Temperature ───────────────────────────────────────────────────────
        temp_widget = QWidget()
        temp_layout = QHBoxLayout(temp_widget)
        temp_layout.setContentsMargins(0, 0, 0, 0)
        temp_layout.setSpacing(8)

        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        # 位置 0 = 未设置（None），位置 1–21 对应 0.0–2.0
        self._temp_slider.setRange(0, 21)
        self._temp_slider.setFixedWidth(160)
        self._temp_slider.valueChanged.connect(self._on_temp_changed)

        self._temp_label = QLabel()
        self._temp_label.setFixedWidth(60)

        temp_layout.addWidget(self._temp_slider)
        temp_layout.addWidget(self._temp_label)
        temp_layout.addStretch()

        # 初始化滑块位置
        t = self._session.config.get("temperature")
        if t is None:
            self._temp_slider.setValue(0)
        else:
            pos = max(1, min(21, round(float(t) * 10) + 1))
            self._temp_slider.setValue(pos)
        self._on_temp_changed(self._temp_slider.value())

        form.addRow("Temperature：", temp_widget)
        form.addRow("", QLabel(
            "  0=最确定，2=最富创造性；建议 0.5–0.7；"
            "\"未设置\"跟随全局 ai_config.json"
        ))

        # ── 上下文消息数上限 ──────────────────────────────────────────────────
        ctx_widget = QWidget()
        ctx_layout = QHBoxLayout(ctx_widget)
        ctx_layout.setContentsMargins(0, 0, 0, 0)
        ctx_layout.setSpacing(8)

        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(1, 500)
        self._ctx_spin.setValue(
            self._session.config.get("max_context_messages") or 100
        )
        self._ctx_spin.setFixedWidth(80)

        ctx_layout.addWidget(self._ctx_spin)
        ctx_layout.addWidget(QLabel("条（发给 LLM 的最近消息数，system 不计入）"))
        ctx_layout.addStretch()

        form.addRow("上下文消息数：", ctx_widget)

        # ── 工作空间路径 ──────────────────────────────────────────────────────
        ws_widget = QWidget()
        ws_layout = QHBoxLayout(ws_widget)
        ws_layout.setContentsMargins(0, 0, 0, 0)
        ws_layout.setSpacing(4)

        self._ws_edit = QLineEdit()
        self._ws_edit.setPlaceholderText("留空 = 不限制（可访问任意路径）")
        if self._session.workspace:
            self._ws_edit.setText(self._session.workspace)

        ws_browse = QPushButton("浏览…")
        ws_browse.setFixedWidth(60)
        ws_browse.clicked.connect(self._browse_workspace)

        ws_clear = QPushButton("清除")
        ws_clear.setFixedWidth(50)
        ws_clear.clicked.connect(lambda: self._ws_edit.clear())

        ws_layout.addWidget(self._ws_edit, 1)
        ws_layout.addWidget(ws_browse)
        ws_layout.addWidget(ws_clear)

        form.addRow("工作空间：", ws_widget)
        form.addRow("", QLabel(
            "  设置后，AI 只能操作该目录下的文件（防止误操作其他项目）"
        ))

        layout.addLayout(form)

        # ── 重置按钮 ──────────────────────────────────────────────────────────
        reset_btn = QPushButton("重置所有字段为默认值")
        reset_btn.clicked.connect(self._reset_all)
        layout.addWidget(reset_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # ── 危险操作区：清空消息记录 ──────────────────────────────────────────
        danger_line = QHBoxLayout()
        clear_msg_btn = QPushButton("清空本会话消息记录…")
        clear_msg_btn.setToolTip("删除本会话的所有对话历史，此操作不可撤销")
        clear_msg_btn.setStyleSheet(
            "QPushButton { color: #c0392b; }"
            "QPushButton:hover { background: rgba(192,57,43,20); }"
        )
        clear_msg_btn.clicked.connect(self._clear_messages)
        danger_line.addStretch()
        danger_line.addWidget(clear_msg_btn)
        layout.addLayout(danger_line)

        # ── 确认/取消 ─────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── 槽函数 ────────────────────────────────────────────────────────────────

    def _clear_messages(self):
        """二次确认后清空会话消息记录，并调用 on_clear 回调。"""
        ret = QMessageBox.question(
            self,
            "清空消息记录",
            f"确定要清空会话「{self._session.name}」的所有对话历史吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._session.messages.clear()
        if self._on_clear:
            self._on_clear()

    def _on_temp_changed(self, pos: int):
        """滑块位置变化时更新旁边的文字标签。"""
        if pos == 0:
            self._temp_label.setText("未设置")
        else:
            val = (pos - 1) / 10.0
            self._temp_label.setText(f"{val:.1f}")

    def _browse_workspace(self):
        """弹出目录选择对话框。"""
        current = self._ws_edit.text().strip()
        start = current if current and Path(current).exists() else ""
        folder = QFileDialog.getExistingDirectory(
            self, "选择工作空间目录", start
        )
        if folder:
            self._ws_edit.setText(folder)

    def _reset_all(self):
        """重置所有字段为默认值（None / 全局默认）。"""
        self._model_combo.setCurrentIndex(0)
        self._temp_slider.setValue(0)
        self._ctx_spin.setValue(100)
        self._ws_edit.clear()

    def _apply_and_accept(self):
        """将 UI 值写回 session.config 和 session.workspace，然后关闭对话框。"""
        # 模型
        model_data = self._model_combo.currentData()
        if model_data is not None:
            self._session.model = model_data  # "" = 使用全局默认
        else:
            # 用户手动输入了模型 ID
            text = self._model_combo.currentText().strip()
            # 过滤掉"使用全局默认（...）"这种显示文字
            if text.startswith("使用全局默认"):
                self._session.model = ""
            else:
                self._session.model = text

        # Temperature
        pos = self._temp_slider.value()
        if pos == 0:
            self._session.config["temperature"] = None
        else:
            self._session.config["temperature"] = round((pos - 1) / 10.0, 1)

        # 上下文消息数
        self._session.config["max_context_messages"] = self._ctx_spin.value()

        # 工作空间
        ws = self._ws_edit.text().strip()
        self._session.workspace = ws if ws else None

        self.accept()
