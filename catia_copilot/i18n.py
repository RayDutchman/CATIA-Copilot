"""CATIA Copilot 国际化基础设施。

编码规范（提取形式已在 Phase 1 用命令+断言验证并固化）：
- 采用形式：
      from catia_copilot.i18n import translate
      translate("CATIACopilot", "中文字面量")
  context 与 source 必须为调用点字符串字面量；禁止用变量/常量当 context 或 source
  （lupdate 按字面量提取，常量引用会漏提取）。
- 禁止句子拼接（多个 translate 结果 +）与把变量塞入 source；
  动态内容用占位符：translate("CATIACopilot", "共 {0} 项").format(n)。
- 首版导出恒中文：导出表头/Sheet 名保持现有中文字面量工厂值，不走本入口。
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import (
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QSettings,
    QTranslator,
)

from catia_copilot.utils import resource_path

logger = logging.getLogger(__name__)

# 覆盖范围：主窗口 / 嵌入菜单 / BOM / 质量特性 / 导出 / 工具 / PLM / AI / 帮助
APP_CTX = "CATIACopilot"

LANG_SYSTEM = "system"
LANG_ZH_CN = "zh_CN"
LANG_EN_US = "en_US"

_ORG = "CATIACopilot"
_APP = "Application"

# install_translators 后实际生效的界面语言（默认源语言中文）
_current_ui_lang: str = LANG_ZH_CN


def translate(context: str, source: str, disambiguation: str = "", n: int = -1) -> str:
    """UI 文案统一入口（透传 QCoreApplication.translate）。

    source 必须为调用点中文字面量；未收录词条时返回 source 本身（回退中文）。
    """
    return QCoreApplication.translate(context, source, disambiguation, n)


def current_ui_language() -> str:
    """返回 install_translators 后实际生效的界面语言（zh_CN / en_US）。"""
    return _current_ui_lang


def resolve_ui_language(setting: str) -> str:
    """把用户设置解析为实际界面语言。

    system 下系统区域语言族为 zh → zh_CN，其余一律回退 en_US。
    """
    if setting == LANG_ZH_CN:
        return LANG_ZH_CN
    if setting == LANG_EN_US:
        return LANG_EN_US
    name = QLocale.system().name()  # 例如 zh_CN / en_US / ja_JP
    if name.split("_")[0].lower() == "zh":
        return LANG_ZH_CN
    return LANG_EN_US


def read_language() -> str:
    """读取 QSettings 中的界面语言，缺省为跟随系统。"""
    return QSettings(_ORG, _APP).value("language", LANG_SYSTEM, type=str)


def write_language(value: str) -> None:
    """写入界面语言设置并立即同步到磁盘/注册表。"""
    qs = QSettings(_ORG, _APP)
    qs.setValue("language", value)
    qs.sync()


def _qm_path(lang: str) -> str:
    """返回对应语言应用翻译 qm 的绝对路径；缺文件返回空串。

    zh_CN 是源语言，不加载自己的 qm。
    """
    if lang == LANG_ZH_CN:
        return ""
    p = resource_path(f"resources/i18n/catia_copilot_{lang}.qm")
    return str(p) if p.is_file() else ""


def _qtbase_zh_qm_path() -> str:
    """返回 Qt 内置基础翻译 qtbase_zh_CN.qm 路径（中文化标准对话框按钮用）。

    打包环境经 QLibraryInfo.TranslationsPath 定位，无需额外打包配置。
    """
    d = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    p = os.path.join(d, "qtbase_zh_CN.qm")
    return p if os.path.isfile(p) else ""


def _install_qtbase_zh(app) -> QTranslator | None:
    """安装 Qt 内置基础中文翻译（中文化 QMessageBox 等标准按钮）。

    缺失或加载失败仅 ``logging.warning`` 并返回 None，不中断启动。
    """
    qm = _qtbase_zh_qm_path()
    if not qm:
        logger.warning("未找到 Qt 基础翻译 qtbase_zh_CN.qm，标准按钮保持英文")
        return None
    qt_translator = QTranslator(app)
    if not qt_translator.load(qm):
        logger.warning("Qt 基础翻译加载失败，标准按钮保持英文: %s", qm)
        return None
    app.installTranslator(qt_translator)
    return qt_translator


def install_translators(
    app, ui_lang: str | None = None
) -> tuple[QTranslator | None, QTranslator | None]:
    """按设置安装界面语言翻译器。

    返回 ``(app_translator, qt_translator)``，两者任一为 None 表示未安装；
    **调用方必须保持返回值引用直到程序退出**，否则 GC 后翻译立即失效。

    - ``zh_CN``：源语言，不装应用翻译器；安装 ``qtbase_zh_CN.qm`` 使
      QMessageBox 等标准按钮（OK/Cancel/Open…）显示中文。
    - 其他语言：加载 ``resources/i18n/catia_copilot_<lang>.qm``；
      **仅在成功加载并安装后把 ``_current_ui_lang`` 记为目标语言**
      （否则界面实际已回退中文，仍按中文记录）。
      缺失或加载失败仅 ``logging.warning``、照常安装 ``qtbase_zh_CN.qm``
      保证标准按钮中文化，不中断启动。
    - 首版只装界面语言翻译器，不安装导出翻译器。
    """
    global _current_ui_lang
    lang = resolve_ui_language(ui_lang or read_language())

    if lang == LANG_ZH_CN:
        _current_ui_lang = LANG_ZH_CN
        return None, _install_qtbase_zh(app)

    qm = _qm_path(lang)
    if not qm:
        logger.warning(
            "未找到翻译文件，界面回退中文: resources/i18n/catia_copilot_%s.qm", lang
        )
        _current_ui_lang = LANG_ZH_CN
        return None, _install_qtbase_zh(app)

    app_translator = QTranslator(app)
    if not app_translator.load(qm):
        logger.warning("翻译文件加载失败，界面回退中文: %s", qm)
        _current_ui_lang = LANG_ZH_CN
        return None, _install_qtbase_zh(app)

    # 仅成功加载目标语言 qm 后才记录为实际生效语言
    app.installTranslator(app_translator)
    _current_ui_lang = lang
    return app_translator, None