# build_nuitka.ps1 — CATIA Copilot Nuitka 打包脚本
#
# 用法（在项目根目录执行）：
#   .\build_nuitka.ps1
#
# 产物输出到：..\CATIA-Copilot-dist-nuitka\CATIA Copilot <version>\
#
# 依赖：
#   pip install nuitka  （或 pip install -U nuitka）
#   pip install ordered-set zstandard  （可选，加速编译）
#
# 与 PyInstaller (build.spec + build.ps1) 的等价关系：
#   build.spec  datas[]         → --include-data-dir
#   build.spec  hiddenimports[] → --include-package / --include-module
#   build.spec  excludes[]      → --nofollow-import-to
#   build.spec  binaries[]      → 不需要：--include-package=win32com/pythoncom 时
#                                  Nuitka 会自动把 pywin32_system32/*.dll 一并收集，
#                                  手动再指定 --include-data-files 会产生冲突。
#   build.spec  EXE(console=F)  → --windows-disable-console
#   build.spec  slim 后处理     → Nuitka 按需编译，天然不含未用模块
#
# Nuitka standalone 模式产出一个目录（含 .exe + _internal/），结构与 PyInstaller 相同。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── 版本号（自动从 constants.py 读取，避免硬编码）──────────────────────────
$ConstantsFile = Join-Path $PSScriptRoot 'catia_copilot\constants.py'
$VerLine = Select-String -Path $ConstantsFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' |
           Select-Object -First 1
if (-not $VerLine) { Write-Error "找不到 APP_VERSION"; exit 1 }
$AppVersion = $VerLine.Matches[0].Groups[1].Value
$AppName    = "CATIA Copilot $AppVersion"

# ── 路径 ──────────────────────────────────────────────────────────────────
$ProjectRoot = $PSScriptRoot
$DistRoot    = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot '..\CATIA-Copilot-dist-nuitka'))
$OutputDir   = Join-Path $DistRoot $AppName   # 最终产物目录

Write-Host "[build] 版本: $AppVersion"
Write-Host "[build] 输出目录: $OutputDir"
Write-Host "[build] 开始 Nuitka 编译..."

Set-Location $ProjectRoot

# ── Nuitka 参数 ────────────────────────────────────────────────────────────
$NuitkaArgs = @(
    '-m', 'nuitka',

    # ── 模式：独立目录（等价于 PyInstaller onedir）──────────────────────────
    '--standalone',

    # ── 输出 ──────────────────────────────────────────────────────────────
    "--output-dir=$DistRoot",
    "--output-filename=$AppName.exe",

    # ── Windows 子系统（无控制台窗口）──────────────────────────────────────
    '--windows-disable-console',

    # ── 图标 ──────────────────────────────────────────────────────────────
    '--windows-icon-from-ico=resources/icon.ico',

    # ── 版本信息（嵌入 PE 资源）────────────────────────────────────────────
    "--windows-product-name=CATIA Copilot",
    "--windows-product-version=$AppVersion.0",
    "--windows-company-name=CATIA Copilot Project",
    "--windows-file-description=CATIA Copilot $AppVersion",

    # ── PySide6 插件（必须启用，否则 Qt 绑定无法正确打包）──────────────────
    '--enable-plugin=pyside6',

    # ── 数据目录（等价于 build.spec datas[]）──────────────────────────────
    '--include-data-dir=resources=resources',
    '--include-data-dir=macros=macros',
    '--include-data-dir=drawing_templates=drawing_templates',
    '--include-data-dir=crack=crack',
    '--include-data-dir=catia_copilot=catia_copilot',

    # ── openpyxl 数据文件（模板/schema，Nuitka 不自动收集）──────────────────
    # openpyxl 将 XML 模板和 schema 文件放在包目录下，需显式包含。
    '--include-package-data=openpyxl',

    # ── 显式包含静态分析可能遗漏的包 ──────────────────────────────────────
    # pywin32 系列：--include-package 会让 Nuitka 自动把
    # pywin32_system32/*.dll 一并打包，无需手动 --include-data-files。
    '--include-package=win32com',
    '--include-package=win32api',
    '--include-package=win32gui',
    '--include-package=win32con',
    '--include-package=pythoncom',
    '--include-package=pywintypes',

    # openpyxl / pycatia（含所有子包）
    '--include-package=openpyxl',
    '--include-package=pycatia',

    # ── 不跟随导入：排除项目未用到的 Qt 模块（等价于 build.spec excludes[]）
    # QML / Quick
    '--nofollow-import-to=PySide6.QtQuick',
    '--nofollow-import-to=PySide6.QtQuickWidgets',
    '--nofollow-import-to=PySide6.QtQuickControls2',
    '--nofollow-import-to=PySide6.QtQml',
    '--nofollow-import-to=PySide6.QtQmlModels',
    '--nofollow-import-to=PySide6.QtQmlWorkerScript',
    '--nofollow-import-to=PySide6.QtQmlMeta',
    # PDF
    '--nofollow-import-to=PySide6.QtPdf',
    '--nofollow-import-to=PySide6.QtPdfWidgets',
    # 虚拟键盘 / 蓝牙 / 近场
    '--nofollow-import-to=PySide6.QtVirtualKeyboard',
    '--nofollow-import-to=PySide6.QtBluetooth',
    '--nofollow-import-to=PySide6.QtNfc',
    # 多媒体
    '--nofollow-import-to=PySide6.QtMultimedia',
    '--nofollow-import-to=PySide6.QtMultimediaWidgets',
    # WebEngine
    '--nofollow-import-to=PySide6.QtWebEngine',
    '--nofollow-import-to=PySide6.QtWebEngineCore',
    '--nofollow-import-to=PySide6.QtWebEngineWidgets',
    '--nofollow-import-to=PySide6.QtWebChannel',
    '--nofollow-import-to=PySide6.QtWebSockets',
    # 3D / 图表 / 地图
    '--nofollow-import-to=PySide6.Qt3DCore',
    '--nofollow-import-to=PySide6.Qt3DRender',
    '--nofollow-import-to=PySide6.Qt3DInput',
    '--nofollow-import-to=PySide6.QtCharts',
    '--nofollow-import-to=PySide6.QtDataVisualization',
    '--nofollow-import-to=PySide6.QtLocation',
    '--nofollow-import-to=PySide6.QtPositioning',
    '--nofollow-import-to=PySide6.QtRemoteObjects',
    '--nofollow-import-to=PySide6.QtScxml',
    '--nofollow-import-to=PySide6.QtSensors',
    '--nofollow-import-to=PySide6.QtSerialPort',
    '--nofollow-import-to=PySide6.QtSerialBus',
    '--nofollow-import-to=PySide6.QtTest',
    # Pillow / lxml / numpy（openpyxl 可选依赖，项目不使用）
    '--nofollow-import-to=PIL',
    '--nofollow-import-to=lxml',
    '--nofollow-import-to=numpy',
    # Pythonwin：pywin32 MFC GUI 组件，项目只用 win32com/win32api
    '--nofollow-import-to=Pythonwin',

    # ── 编译优化（可选）─────────────────────────────────────────────────────
    # '--lto=yes',         # 启用链接时优化（编译更慢，产物更小）
    # '--jobs=4',          # 并行编译线程数

    # ── 入口脚本 ──────────────────────────────────────────────────────────
    'main.py'
)

# ── 执行编译 ───────────────────────────────────────────────────────────────
& python @NuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "[build] Nuitka 编译失败，退出码 $LASTEXITCODE"
    exit $LASTEXITCODE
}

# ── 重命名输出目录（Nuitka 默认产出 main.dist，重命名为 $AppName）─────────
$DefaultDist = Join-Path $DistRoot 'main.dist'
if (Test-Path $DefaultDist) {
    if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
    Rename-Item -Path $DefaultDist -NewName $AppName
    Write-Host "[build] 已重命名产物目录: $OutputDir"
}

Write-Host "[build] 完成！产物位于: $OutputDir"
#
# 用法（在项目根目录执行）：
#   .\build_nuitka.ps1
#
# 产物输出到：..\CATIA-Copilot-dist-nuitka\CATIA Copilot <version>\
#
# 依赖：
#   pip install nuitka  （或 pip install -U nuitka）
#   pip install ordered-set zstandard  （可选，加速编译）
#
# 与 PyInstaller (build.spec + build.ps1) 的等价关系：
#   build.spec  datas[]         → --include-data-dir
#   build.spec  hiddenimports[] → --include-package / --include-module
#   build.spec  excludes[]      → --nofollow-import-to
#   build.spec  binaries[]      → --include-data-files（pywin32 DLL）
#   build.spec  EXE(console=F)  → --windows-disable-console
#   build.spec  slim 后处理     → Nuitka 按需编译，天然不含未用模块
#
# Nuitka standalone 模式产出一个目录（含 .exe + _internal/），结构与 PyInstaller 相同。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── 版本号（自动从 constants.py 读取，避免硬编码）──────────────────────────
$ConstantsFile = Join-Path $PSScriptRoot 'catia_copilot\constants.py'
$VerLine = Select-String -Path $ConstantsFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' |
           Select-Object -First 1
if (-not $VerLine) { Write-Error "找不到 APP_VERSION"; exit 1 }
$AppVersion = $VerLine.Matches[0].Groups[1].Value
$AppName    = "CATIA Copilot $AppVersion"

# ── 路径 ──────────────────────────────────────────────────────────────────
$ProjectRoot = $PSScriptRoot
$DistRoot    = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot '..\CATIA-Copilot-dist-nuitka'))
$OutputDir   = Join-Path $DistRoot $AppName   # 最终产物目录

# ── pywin32 DLL 路径（pywintypes / pythoncom，Nuitka 不自动收集）─────────
$SitePkgs     = python -c "import site; print(site.getsitepackages()[0])"
$UserSitePkgs = python -c "import site; print(site.getusersitepackages())"
$Win32DllArgs = @()
foreach ($sp in @($SitePkgs, $UserSitePkgs)) {
    $win32Dir = Join-Path $sp 'pywin32_system32'
    if (Test-Path $win32Dir) {
        Get-ChildItem -Path $win32Dir -Filter '*.dll' | ForEach-Object {
            # 目标子目录用 "." 放到 exe 同级（与 PyInstaller 行为一致）
            $Win32DllArgs += "--include-data-files=$($_.FullName)=./"
        }
    }
}
if ($Win32DllArgs.Count -eq 0) {
    Write-Warning "[warn] pywin32_system32 DLL 未找到，pywin32 功能可能异常"
}

Write-Host "[build] 版本: $AppVersion"
Write-Host "[build] 输出目录: $OutputDir"
Write-Host "[build] 开始 Nuitka 编译..."

Set-Location $ProjectRoot

# ── Nuitka 参数 ────────────────────────────────────────────────────────────
$NuitkaArgs = @(
    '-m', 'nuitka',

    # ── 模式：独立目录（等价于 PyInstaller onedir）──────────────────────────
    '--standalone',

    # ── 输出 ──────────────────────────────────────────────────────────────
    "--output-dir=$DistRoot",
    "--output-filename=$AppName.exe",

    # ── Windows 子系统（无控制台窗口）──────────────────────────────────────
    '--windows-disable-console',

    # ── 图标 ──────────────────────────────────────────────────────────────
    '--windows-icon-from-ico=resources/icon.ico',

    # ── 版本信息（嵌入 PE 资源）────────────────────────────────────────────
    "--windows-product-name=CATIA Copilot",
    "--windows-product-version=$AppVersion.0",
    "--windows-company-name=CATIA Copilot Project",
    "--windows-file-description=CATIA Copilot $AppVersion",

    # ── PySide6 插件（必须启用，否则 Qt 绑定无法正确打包）──────────────────
    '--enable-plugin=pyside6',

    # ── 数据目录（等价于 build.spec datas[]）──────────────────────────────
    '--include-data-dir=resources=resources',
    '--include-data-dir=macros=macros',
    '--include-data-dir=drawing_templates=drawing_templates',
    '--include-data-dir=crack=crack',
    '--include-data-dir=catia_copilot=catia_copilot',

    # ── openpyxl 数据文件（模板/schema，Nuitka 不自动收集）──────────────────
    # openpyxl 将 XML 模板和 schema 文件放在包目录下，需显式包含。
    '--include-package-data=openpyxl',

    # ── 显式包含 PyInstaller 分析可能遗漏的包 ──────────────────────────────
    # pywin32 系列：COM、Win32 API、GUI（win32com/win32api/win32gui/win32con 等）
    '--include-package=win32com',
    '--include-package=win32api',
    '--include-package=win32gui',
    '--include-package=win32con',
    '--include-package=pythoncom',
    '--include-package=pywintypes',
    '--include-package=win32con',

    # openpyxl / pycatia（含所有子包）
    '--include-package=openpyxl',
    '--include-package=pycatia',

    # ── 不跟随导入：排除项目未用到的 Qt 模块（等价于 build.spec excludes[]）
    # QML / Quick
    '--nofollow-import-to=PySide6.QtQuick',
    '--nofollow-import-to=PySide6.QtQuickWidgets',
    '--nofollow-import-to=PySide6.QtQuickControls2',
    '--nofollow-import-to=PySide6.QtQml',
    '--nofollow-import-to=PySide6.QtQmlModels',
    '--nofollow-import-to=PySide6.QtQmlWorkerScript',
    '--nofollow-import-to=PySide6.QtQmlMeta',
    # PDF
    '--nofollow-import-to=PySide6.QtPdf',
    '--nofollow-import-to=PySide6.QtPdfWidgets',
    # 虚拟键盘 / 蓝牙 / 近场
    '--nofollow-import-to=PySide6.QtVirtualKeyboard',
    '--nofollow-import-to=PySide6.QtBluetooth',
    '--nofollow-import-to=PySide6.QtNfc',
    # 多媒体
    '--nofollow-import-to=PySide6.QtMultimedia',
    '--nofollow-import-to=PySide6.QtMultimediaWidgets',
    # WebEngine
    '--nofollow-import-to=PySide6.QtWebEngine',
    '--nofollow-import-to=PySide6.QtWebEngineCore',
    '--nofollow-import-to=PySide6.QtWebEngineWidgets',
    '--nofollow-import-to=PySide6.QtWebChannel',
    '--nofollow-import-to=PySide6.QtWebSockets',
    # 3D / 图表 / 地图
    '--nofollow-import-to=PySide6.Qt3DCore',
    '--nofollow-import-to=PySide6.Qt3DRender',
    '--nofollow-import-to=PySide6.Qt3DInput',
    '--nofollow-import-to=PySide6.QtCharts',
    '--nofollow-import-to=PySide6.QtDataVisualization',
    '--nofollow-import-to=PySide6.QtLocation',
    '--nofollow-import-to=PySide6.QtPositioning',
    '--nofollow-import-to=PySide6.QtRemoteObjects',
    '--nofollow-import-to=PySide6.QtScxml',
    '--nofollow-import-to=PySide6.QtSensors',
    '--nofollow-import-to=PySide6.QtSerialPort',
    '--nofollow-import-to=PySide6.QtSerialBus',
    '--nofollow-import-to=PySide6.QtTest',
    # Pillow / lxml / numpy（openpyxl 可选依赖，项目不使用）
    '--nofollow-import-to=PIL',
    '--nofollow-import-to=lxml',
    '--nofollow-import-to=numpy',
    # Pythonwin：pywin32 MFC GUI 组件，项目只用 win32com/win32api
    '--nofollow-import-to=Pythonwin',

    # ── 编译优化（可选）─────────────────────────────────────────────────────
    # '--lto=yes',         # 启用链接时优化（编译更慢，产物更小）
    # '--jobs=4',          # 并行编译线程数

    # ── 入口脚本 ──────────────────────────────────────────────────────────
    'main.py'
)

# 追加 pywin32 DLL 参数
$NuitkaArgs += $Win32DllArgs

# ── 执行编译 ───────────────────────────────────────────────────────────────
& python @NuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "[build] Nuitka 编译失败，退出码 $LASTEXITCODE"
    exit $LASTEXITCODE
}

# ── 重命名输出目录（Nuitka 默认产出 main.dist，重命名为 $AppName）─────────
$DefaultDist = Join-Path $DistRoot 'main.dist'
if (Test-Path $DefaultDist) {
    if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
    Rename-Item -Path $DefaultDist -NewName $AppName
    Write-Host "[build] 已重命名产物目录: $OutputDir"
}

Write-Host "[build] 完成！产物位于: $OutputDir"
