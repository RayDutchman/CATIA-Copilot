# build.spec — PyInstaller spec file for CATIA Copilot
#
# How to build:
#   pyinstaller build.spec
#
# Output: dist/CATIA Copilot/
# The executable is placed in dist/CATIA Copilot/ and all supporting files
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

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('macros', 'macros'),
        ('drawing_templates', 'drawing_templates'),
        ('crack', 'crack'),
        ('catia_copilot/ui/style.qss', 'catia_copilot/ui'),
        ('catia_copilot', 'catia_copilot'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
