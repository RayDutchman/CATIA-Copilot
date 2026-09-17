"""文件重命名/移动对话框，供 BOM 编辑器使用。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from catia_copilot.constants import PART_NUMBER_VALID_PATTERN
from catia_copilot.i18n import translate


class _FileRenameDialog(QDialog):
    """通过 CATIA SaveAs 重命名或移动单个 CATIA 文件的对话框。

    允许用户独立更改文件茎名（不含扩展名的文件名）和/或目标目录。
    使用 :data:`~catia_copilot.constants.PART_NUMBER_VALID_PATTERN`
    验证新文件茎名，并按需创建目标目录。
    """

    def __init__(self, current_fp: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("CATIACopilot", "另存为"))
        self.setMinimumWidth(540)
        self._current_fp = current_fp
        self._p          = Path(current_fp)

        layout = QFormLayout(self)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setSpacing(8)

        # 当前路径（只读）
        cur_label = QLabel(current_fp)
        cur_label.setWordWrap(True)
        cur_label.setStyleSheet("color: #555;")
        layout.addRow(translate("CATIACopilot", "当前路径："), cur_label)

        # 新文件名（仅文件茎；扩展名自动保留）
        self._name_edit = QLineEdit(self._p.stem)
        layout.addRow(translate("CATIACopilot", "新文件名（不含扩展名 {0}）：").format(self._p.suffix), self._name_edit)

        # 新目录（带浏览按钮）
        dir_widget = QWidget()
        dir_layout = QHBoxLayout(dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        self._dir_edit = QLineEdit(str(self._p.parent))
        dir_btn        = QPushButton(translate("CATIACopilot", "浏览…"))
        dir_btn.setFixedWidth(64)
        dir_btn.clicked.connect(self._browse_dir)
        dir_layout.addWidget(self._dir_edit)
        dir_layout.addWidget(dir_btn)
        layout.addRow(translate("CATIACopilot", "新目录："), dir_widget)

        # 路径预览
        self._preview_label = QLabel()
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color: #333; font-style: italic;")
        layout.addRow(translate("CATIACopilot", "新路径预览："), self._preview_label)

        self._name_edit.textChanged.connect(self._update_preview)
        self._dir_edit.textChanged.connect(self._update_preview)
        self._update_preview()

        # 按钮
        btn_widget = QWidget()
        btn_row    = QHBoxLayout(btn_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()
        ok_btn     = QPushButton(translate("CATIACopilot", "确认"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._validate_and_accept)
        cancel_btn = QPushButton(translate("CATIACopilot", "取消"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_widget)

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def new_stem(self) -> str:
        return self._name_edit.text().strip()

    @property
    def new_dir(self) -> str:
        return self._dir_edit.text().strip()

    @property
    def new_path(self) -> str:
        stem      = self.new_stem or self._p.stem
        directory = self.new_dir  or str(self._p.parent)
        return str(Path(directory) / (stem + self._p.suffix))

    # ── Slots ───────────────────────────────────────────────────────────────

    def _update_preview(self) -> None:
        self._preview_label.setText(self.new_path)

    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            translate("CATIACopilot", "选择目标目录"),
            self._dir_edit.text() or str(self._p.parent),
        )
        if d:
            self._dir_edit.setText(d)

    def _validate_and_accept(self) -> None:
        stem = self.new_stem or self._p.stem
        if stem != self._p.stem and not PART_NUMBER_VALID_PATTERN.fullmatch(stem):
            QMessageBox.warning(
                self,
                translate("CATIACopilot", "文件名含非法字符"),
                translate("CATIACopilot",
                    "文件名 「{0}」 含有非法字符。\n"
                    "不允许：控制字符、非ASCII字符，以及Windows文件名禁用字符"
                    "（\\ / : * ? \" < > |）。"
                ).format(stem),
            )
            return
        new_p = Path(self.new_path)
        if new_p.resolve() == self._p.resolve():
            QMessageBox.warning(self,
                                translate("CATIACopilot", "路径未改变"),
                                translate("CATIACopilot", "新路径与当前路径相同，无需操作。"))
            return
        dest_dir = new_p.parent
        if not dest_dir.exists():
            ret = QMessageBox.question(
                self,
                translate("CATIACopilot", "目录不存在"),
                translate("CATIACopilot", "目标目录不存在：\n{0}\n\n是否创建该目录？").format(dest_dir),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self,
                                     translate("CATIACopilot", "创建目录失败"),
                                     translate("CATIACopilot", "无法创建目录：\n{0}").format(exc))
                return
        self.accept()
