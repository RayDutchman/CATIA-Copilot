; setup.iss — CATIA Copilot Inno Setup 安装脚本
;
; 前置步骤：
;   1. 先运行 .\build_nuitka.ps1 生成编译产物
;   2. 安装 Inno Setup 6：https://jrsoftware.org/isinfo.php
;   3. 执行打包：iscc setup.iss
;      或直接用 Inno Setup Compiler GUI 打开此文件并点击 Build
;
; 产物：..\CATIA-Copilot-dist-nuitka\CATIA-Copilot-Setup-<version>.exe
;
; 安装目标：%ProgramFiles%\CATIA Copilot\
; 快捷方式：桌面 + 开始菜单
; 卸载：控制面板"程序和功能"中可完整卸载

#define AppName      "CATIA Copilot"
#define AppVersion   "2.2.0"
#define AppPublisher "Chen Weibo"
#define AppExeName   "CATIA Copilot 2.2.0.exe"
; Nuitka 编译产物目录（相对于本 .iss 文件所在的项目根目录）
#define SourceDir    "..\CATIA-Copilot-dist-nuitka\CATIA Copilot 2.2.0"

[Setup]
AppId={{89E7150F-7E21-4B13-B613-999FC8E4C4E7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/your-org/CATIA-Copilot
AppSupportURL=https://github.com/your-org/CATIA-Copilot/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; 安装包输出到 dist-nuitka 同级目录
OutputDir=..\CATIA-Copilot-dist-nuitka
OutputBaseFilename=CATIA-Copilot-Setup-{#AppVersion}
; 图标（使用编译产物内的图标，或直接引用源码资源目录）
SetupIconFile=resources\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
; 64 位应用，仅支持 64 位 Windows
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 需要管理员权限写入 Program Files
PrivilegesRequired=admin
WizardStyle=modern
; 安装完成后可选择直接运行
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
; 桌面快捷方式（默认勾选）
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 递归复制整个 Nuitka 产物目录
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式（受 Tasks 控制）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理程序目录（如有运行时生成的文件）
Type: filesandordirs; Name: "{app}"

[Code]
{ 卸载前强制终止正在运行的程序进程，避免文件被占用导致卸载不完整 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec('taskkill.exe', '/F /IM "CATIA Copilot 2.2.0.exe"', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
    { ResultCode 忽略：进程不存在时 taskkill 返回非零，属正常情况 }
end;
