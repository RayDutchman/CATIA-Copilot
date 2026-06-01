# build.spec — PyInstaller spec file for CATIA Copilot
#
# How to build:
#   pyinstaller build.spec
#
# Output: ../CATIA-Copilot-dist/CATIA Copilot <version>/
# 打包产物输出到项目上一级目录的 CATIA-Copilot-dist/ 文件夹，
# 避免 dist/ 污染项目目录。
# The executable is placed there and all supporting files
# (resources/, macros/, catia_copilot/, etc.) are placed inside the default
# _internal/ subdirectory alongside it.
#
# Before building, place the application icon at:
#   resources/icon.ico
# Then uncomment the `icon=` line in the EXE block below.

# 从 constants.py 自动读取版本号，避免手动维护多处硬编码。
# 使用正则解析而非 import，防止 spec 执行时触发应用代码的副作用。
import re as _re
_ver = _re.search(
    r'APP_VERSION\s*=\s*"([^"]+)"',
    open('catia_copilot/constants.py', encoding='utf-8').read(),
).group(1)
_app_name = f"CATIA Copilot {_ver}"

# 打包输出目录：项目上一级的 CATIA-Copilot-dist/
# spec 文件所在目录即项目根目录，用 SPECPATH 获取（PyInstaller 内置变量）
import os as _os
_project_root = SPECPATH  # noqa: F821  — PyInstaller 在执行 spec 时注入此变量
_dist_root = _os.path.normpath(_os.path.join(_project_root, '..', 'CATIA-Copilot-dist'))

# ── pywin32 DLL 动态查找 ────────────────────────────────────────────────────
# pywintypes / pythoncom DLL 位于 site-packages/pywin32_system32/，
# PyInstaller 默认不会自动收集，必须手动声明为 binaries。
# 文件名含 Python 版本号（如 pywintypes313.dll），动态构造避免硬编码。
import sys as _sys, glob as _glob
_pyver = f"{_sys.version_info.major}{_sys.version_info.minor}"
_site_pkgs = _os.path.join(_os.path.dirname(_sys.executable), 'Lib', 'site-packages')
_pywin32_dir = _os.path.join(_site_pkgs, 'pywin32_system32')
_pywin32_binaries = [
    (dll, '.')
    for dll in _glob.glob(_os.path.join(_pywin32_dir, '*.dll'))
]
if not _pywin32_binaries:
    print(f"[warn] pywin32_system32 DLL not found in {_pywin32_dir}")

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pywin32_binaries,
    datas=[
        ('resources', 'resources'),
        ('macros', 'macros'),
        ('drawing_templates', 'drawing_templates'),
        ('crack', 'crack'),
        ('catia_copilot', 'catia_copilot'),
    ],
    hiddenimports=[
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ── Qt 模块：项目不使用 QML / PDF / 虚拟键盘 ──────────────────────────
        'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
        'PySide6.QtQml', 'PySide6.QtQmlModels', 'PySide6.QtQmlWorkerScript',
        'PySide6.QtQmlMeta',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtVirtualKeyboard',
        'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtLocation', 'PySide6.QtPositioning',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
        'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
        'PySide6.QtTest',
        # ── PIL / Pillow：openpyxl 可选依赖，项目代码不使用 ───────────────────
        'PIL',
        # ── lxml：openpyxl 可选 XML 后端，项目代码不使用 ──────────────────────
        'lxml',
        # ── numpy：项目未调用矩阵运算相关功能 ────────────────────────────────
        'numpy',
        # ── Pythonwin：pywin32 的 MFC GUI 组件，项目只用 win32com/win32api ─────
        'Pythonwin',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=_app_name,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,  # 以普通用户权限运行；CATIA V5 本身也应以普通用户运行，管理员进程反而看不到其 ROT
    icon='resources/icon.ico',  # Uncomment after placing icon.ico in resources/
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=_app_name,
    distpath=_dist_root,
)

# ── 打包后清理：删除不需要的大文件 ─────────────────────────────────────────────
# 必须在 COLLECT 之后执行，否则目录尚不存在。

_dist = _os.path.join(_dist_root, _app_name, '_internal')

# 1. opengl32sw.dll：Qt 软件渲染回退，桌面环境有系统 OpenGL 不需要（-20M）
_opengl_sw = _os.path.join(_dist, 'PySide6', 'opengl32sw.dll')
if _os.path.exists(_opengl_sw):
    _os.remove(_opengl_sw)
    print(f"[slim] removed opengl32sw.dll")

# 2. Qt translations：只保留中文和英文，其余语言包一律删除（-~5M）
_trans_dir = _os.path.join(_dist, 'PySide6', 'translations')
if _os.path.isdir(_trans_dir):
    for _f in _os.listdir(_trans_dir):
        # 保留 zh_CN、zh_TW、en（含无后缀的基础文件如 qtbase.qm）
        if not any(tag in _f for tag in ('zh_CN', 'zh_TW', '_en', 'qtbase.qm')):
            _path = _os.path.join(_trans_dir, _f)
            _os.remove(_path)
            print(f"[slim] removed translation: {_f}")

# 3. PySide6/plugins/imageformats：只保留 png/svg，删除 avif/webp/jp2/tiff 等
_imgfmt_dir = _os.path.join(_dist, 'PySide6', 'plugins', 'imageformats')
_keep_formats = {'qpng', 'qsvg', 'qico', 'qjpeg', 'qgif'}
if _os.path.isdir(_imgfmt_dir):
    for _f in _os.listdir(_imgfmt_dir):
        _stem = _f.split('.')[0].lower()  # e.g. "qavif", "qwebp"
        if _stem not in _keep_formats:
            _path = _os.path.join(_imgfmt_dir, _f)
            _os.remove(_path)
            print(f"[slim] removed imageformat: {_f}")
