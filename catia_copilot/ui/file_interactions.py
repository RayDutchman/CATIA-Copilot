"""GUI 文件冲突交互，保留原对话框行为。"""
from pathlib import Path
from PySide6.QtWidgets import QMessageBox
from catia_copilot.catia import file_interaction

def _prompt_overwrite(dest: Path) -> str:
    """显示 *dest* 的覆盖冲突对话框。

    返回以下之一：``"skip"``、``"skip_all"``、``"overwrite"``、
    ``"overwrite_all"`` 或 ``"cancel"``。

    参数：
        dest: 目标文件路径

    返回：
        用户选择的操作
    """
    msg = QMessageBox()
    msg.setWindowTitle("文件已存在")
    msg.setText(f'"{dest.name}" 已存在于输出文件夹中。')
    msg.setInformativeText(str(dest.parent))
    msg.setIcon(QMessageBox.Icon.Warning)
    skip_btn          = msg.addButton("跳过",     QMessageBox.ButtonRole.RejectRole)
    skip_all_btn      = msg.addButton("全部跳过", QMessageBox.ButtonRole.RejectRole)
    _overwrite_btn    = msg.addButton("覆盖",     QMessageBox.ButtonRole.AcceptRole)
    overwrite_all_btn = msg.addButton("全部覆盖", QMessageBox.ButtonRole.AcceptRole)
    cancel_btn        = msg.addButton("取消",     QMessageBox.ButtonRole.DestructiveRole)
    msg.exec()
    clicked = msg.clickedButton()
    if clicked is cancel_btn:
        return "cancel"
    if clicked is skip_all_btn:
        return "skip_all"
    if clicked is skip_btn:
        return "skip"
    if clicked is overwrite_all_btn:
        return "overwrite_all"
    return "overwrite"

def _retry_locked(dest: Path) -> bool:
    return QMessageBox.question(
        None, "文件正在使用",
        f"该文件当前在 Excel 中已打开：\n{dest}\n\n请在 Excel 中关闭该文件，然后点击【重试】，或点击【取消】以中止。",
        QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
    ) != QMessageBox.StandardButton.Cancel

def _report_locked(dest: Path) -> None:
    QMessageBox.critical(None, "文件仍在使用中", f"文件仍处于打开状态，请关闭后重试。\n{dest}")

def install_file_interactions() -> None:
    file_interaction.overwrite_handler = _prompt_overwrite
    file_interaction.locked_handler = _retry_locked
    file_interaction.locked_error_handler = _report_locked
