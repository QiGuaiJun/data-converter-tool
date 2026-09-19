@echo off
setlocal
cd /d "%~dp0"

set "PORT=51978"
set "PY=%~dp0.venv\Scripts\python.exe"
set "DATAROOT=%~dp0runtime"

if not exist "%PY%" (
  echo.
  echo [错误] 找不到虚拟环境：%PY%
  echo.
  echo 请先在项目目录执行：
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements-lock.txt
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   数据导表工具
echo ------------------------------------------------------------
echo   项目目录：%~dp0
echo   数据目录：%DATAROOT%
echo   访问地址：http://127.0.0.1:%PORT%
echo.
echo   浏览器会自动打开；关闭本窗口即停止服务。
echo ============================================================
echo.

set "HOST=127.0.0.1"
"%PY%" desktop_launcher.py

echo.
echo 服务已停止（退出码 %ERRORLEVEL%）。
pause
