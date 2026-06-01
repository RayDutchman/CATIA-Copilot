# build.spec — PyInstaller spec file for CATIA Copilot
#
# How to build（推荐，使用 build.ps1）：
#   .\build.ps1
#
# 也可以手动指定输出目录：
#   pyinstaller --distpath ..\CATIA-Copilot-dist build.spec
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

# 打包输出目录：由 build.ps1 通过 --distpath 命令行参数传入，
# PyInstaller 会将其注入为 DISTPATH 内置变量（与 SPECPATH 同级）。
# 直接使用 DISTPATH 可确保清理代码与实际输出目录一致，
# 避免 spec 内硬编码路径与命令行参数不同步的问题。
import os as _os
_project_root = SPECPATH  # noqa: F821  — PyInstaller 在执行 spec 时注入
# DISTPATH 由 PyInstaller 注入，值等于 --distpath 参数（已规范化为绝对路径）
_dist_root = DISTPATH      # noqa: F821

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
        ('catia_copilot/ui/style.qss', 'catia_copilot/ui'),
        ('catia_copilot', 'catia_copilot'),
    ],
    hiddenimports=[
        # qdarkstyle 通过 Qt 资源系统注册图标，PyInstaller 静态分析找不到这两个模块，
        # 缺失时深色/浅色主题的图标（滚动条箭头、复选框等）会显示为空白。
        'qdarkstyle.dark.darkstyle_rc',
        'qdarkstyle.light.lightstyle_rc',
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
)

# ── 打包后清理：删除不需要的大文件 ─────────────────────────────────────────────
# 必须在 COLLECT 之后执行，否则目录尚不存在。
# _dist_root 由 DISTPATH 注入，与 --distpath 参数保持一致。

_dist = _os.path.join(_dist_root, _app_name, '_internal')
_pyside6 = _os.path.join(_dist, 'PySide6')

# 1. opengl32sw.dll：Qt 软件渲染回退，桌面环境有系统 OpenGL 不需要（-20 MB）
_opengl_sw = _os.path.join(_pyside6, 'opengl32sw.dll')
if _os.path.exists(_opengl_sw):
    _os.remove(_opengl_sw)
    print(f"[slim] removed opengl32sw.dll")

# 2. Qt translations：只保留中文和英文，其余语言包一律删除（-~5.5 MB）
_trans_dir = _os.path.join(_pyside6, 'translations')
if _os.path.isdir(_trans_dir):
    for _f in _os.listdir(_trans_dir):
        # 保留 zh_CN、zh_TW、en（含无后缀的基础文件如 qtbase.qm）
        if not any(tag in _f for tag in ('zh_CN', 'zh_TW', '_en', 'qtbase.qm')):
            _path = _os.path.join(_trans_dir, _f)
            _os.remove(_path)
            print(f"[slim] removed translation: {_f}")

# 3. PySide6/plugins/imageformats：只保留常用格式，删除 webp/tiff/icns/tga/wbmp/pdf 等
_imgfmt_dir = _os.path.join(_pyside6, 'plugins', 'imageformats')
_keep_formats = {'qpng', 'qsvg', 'qico', 'qjpeg', 'qgif'}
if _os.path.isdir(_imgfmt_dir):
    for _f in _os.listdir(_imgfmt_dir):
        _stem = _f.split('.')[0].lower()  # e.g. "qavif", "qwebp"
        if _stem not in _keep_formats:
            _path = _os.path.join(_imgfmt_dir, _f)
            _os.remove(_path)
            print(f"[slim] removed imageformat: {_f}")

# 4. 未被项目使用的 Qt6 DLL（excludes 只排除 Python 绑定层，底层 DLL 需手动删除）
#    - Qt6Quick / Qt6Qml*：QML 框架，项目 UI 全用 Widgets（-12 MB）
#    - Qt6Pdf：PDF 渲染，项目无 PDF 功能（-4.4 MB）
#    - Qt6VirtualKeyboard：虚拟键盘，桌面应用不需要（-0.4 MB）
_unused_qt_dlls = [
    'Qt6Quick.dll',
    'Qt6Qml.dll',
    'Qt6QmlModels.dll',
    'Qt6QmlMeta.dll',
    'Qt6QmlWorkerScript.dll',
    'Qt6Pdf.dll',
    'Qt6VirtualKeyboard.dll',
]
for _dll in _unused_qt_dlls:
    _path = _os.path.join(_pyside6, _dll)
    if _os.path.exists(_path):
        _os.remove(_path)
        print(f"[slim] removed Qt DLL: {_dll}")

# 5. Pythonwin 目录：pywin32 的 MFC GUI 组件，项目只用 win32com/win32api（-6.4 MB）
#    excludes 里的 'Pythonwin' 只排除 Python 包，底层目录需手动删除。
import shutil as _shutil
_pythonwin_dir = _os.path.join(_dist, 'Pythonwin')
if _os.path.isdir(_pythonwin_dir):
    _shutil.rmtree(_pythonwin_dir)
    print(f"[slim] removed Pythonwin/")

# 6. 非当前 Python 版本的 .pyc 缓存（如 cpython-314.pyc 混入 cpython-313 环境）
#    这些文件由 datas 收集 catia_copilot/ 源码目录时带入，运行时不会被加载。
_pyc_ver = f"cpython-{_sys.version_info.major}{_sys.version_info.minor}"
for _root, _dirs, _files in _os.walk(_dist):
    for _fname in _files:
        if _fname.endswith('.pyc') and _pyc_ver not in _fname:
            _fpath = _os.path.join(_root, _fname)
            _os.remove(_fpath)
            print(f"[slim] removed stale pyc: {_os.path.relpath(_fpath, _dist)}")
