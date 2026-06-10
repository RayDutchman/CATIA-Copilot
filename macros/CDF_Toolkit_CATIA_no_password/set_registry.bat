@echo off
setlocal EnableDelayedExpansion

powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" > "%TEMP%\_reg_date.txt"
set /p TODAY=<"%TEMP%\_reg_date.txt"
del "%TEMP%\_reg_date.txt" >nul 2>&1

powershell -NoProfile -Command "Get-Random -Minimum 10000000 -Maximum 100000000" > "%TEMP%\_reg_rand.txt"
set /p RANDNUM=<"%TEMP%\_reg_rand.txt"
del "%TEMP%\_reg_rand.txt" >nul 2>&1

echo.
echo S2 (date)   : %TODAY%
echo S3 (random) : %RANDNUM%
echo.

echo [1/5] HKCU\Software\Microsoft
reg add "HKCU\Software\Microsoft"          /v S1 /t REG_SZ /d 0         /f
reg add "HKCU\Software\Microsoft"          /v S2 /t REG_SZ    /d %TODAY%   /f
reg add "HKCU\Software\Microsoft"          /v S3 /t REG_SZ /d %RANDNUM% /f
reg add "HKCU\Software\Microsoft"          /v S4 /t REG_SZ /d 0         /f

echo [2/5] HKCU\Software\Microsoft\TabletPC
reg add "HKCU\Software\Microsoft\TabletPC"  /v S1 /t REG_SZ /d 0         /f
reg add "HKCU\Software\Microsoft\TabletPC"  /v S2 /t REG_SZ    /d %TODAY%   /f
reg add "HKCU\Software\Microsoft\TabletPC"  /v S3 /t REG_SZ /d %RANDNUM% /f
reg add "HKCU\Software\Microsoft\TabletPC"  /v S4 /t REG_SZ /d 0         /f

echo [3/5] HKCU\Software\Microsoft\Windows
reg add "HKCU\Software\Microsoft\Windows"  /v S1 /t REG_SZ /d 0         /f
reg add "HKCU\Software\Microsoft\Windows"  /v S2 /t REG_SZ    /d %TODAY%   /f
reg add "HKCU\Software\Microsoft\Windows"  /v S3 /t REG_SZ /d %RANDNUM% /f
reg add "HKCU\Software\Microsoft\Windows"  /v S4 /t REG_SZ /d 0         /f

echo [4/5] HKCU\Console
reg add "HKCU\Console"                     /v S1 /t REG_SZ /d 0         /f
reg add "HKCU\Console"                     /v S2 /t REG_SZ    /d %TODAY%   /f
reg add "HKCU\Console"                     /v S3 /t REG_SZ /d %RANDNUM% /f
reg add "HKCU\Console"                     /v S4 /t REG_SZ /d 0         /f

echo [5/5] HKCU\Network
reg add "HKCU\Network"                     /v S1 /t REG_SZ /d 0         /f
reg add "HKCU\Network"                     /v S2 /t REG_SZ    /d %TODAY%   /f
reg add "HKCU\Network"                     /v S3 /t REG_SZ /d %RANDNUM% /f
reg add "HKCU\Network"                     /v S4 /t REG_SZ /d 0         /f

echo.
echo Done.
pause
