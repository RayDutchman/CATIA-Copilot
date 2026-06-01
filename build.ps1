# build.ps1 — CATIA Copilot 打包脚本
#
# 用法（在项目根目录执行）：
#   .\build.ps1
#
# 产物输出到：..\CATIA-Copilot-dist\CATIA Copilot <version>\
#
# 依赖：pyinstaller 已安装（pip install pyinstaller）

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 项目根目录（脚本所在目录）
$ProjectRoot = $PSScriptRoot

# 输出目录：项目上一级的 CATIA-Copilot-dist/
$DistPath = Join-Path $ProjectRoot '..\CATIA-Copilot-dist'
$DistPath = [System.IO.Path]::GetFullPath($DistPath)

Write-Host "[build] 输出目录: $DistPath"
Write-Host "[build] 开始打包..."

# 切换到项目根目录，确保 spec 里的相对路径（如 catia_copilot/constants.py）可以找到
Set-Location $ProjectRoot

# 执行 PyInstaller
# --distpath  : 指定产物输出目录，PyInstaller 会将其注入为 DISTPATH 内置变量
# --workpath  : 中间文件（.toc、.pkg 等）放到项目内的 build/，不影响产物
# --noconfirm : 覆盖已有输出目录时不询问
pyinstaller `
    --distpath "$DistPath" `
    --workpath "build" `
    --noconfirm `
    build.spec

if ($LASTEXITCODE -ne 0) {
    Write-Error "[build] PyInstaller 失败，退出码 $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[build] 完成！产物位于: $DistPath"
