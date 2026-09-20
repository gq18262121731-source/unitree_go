@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
echo Starting Go2 UWB companion monitor (mock data)...
echo The browser will open automatically. Press Ctrl+C here to stop.
echo.

set "ROOT=%~dp0..\.."
set "EXE=%~dp0Go2-UWB-Mock.exe"
set "EXE_DIR=%~dp0"
if not exist "%EXE%" (
  set "EXE_DIR=%ROOT%\artifacts\pyinstaller-build\Go2-UWB-Mock\"
  set "EXE=%EXE_DIR%Go2-UWB-Mock.exe"
)

if exist "%EXE%" if exist "%EXE_DIR%_internal\" (
  "%EXE%" %*
  set "EXIT_CODE=!ERRORLEVEL!"
  if errorlevel 1 (
    echo.
    echo Startup failed.
    pause
  )
  exit /b !EXIT_CODE!
)

set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if exist "%ROOT%\tools\go2_uwb_mock_showcase.py" (
  echo Packaged runtime not found; using the local Python source.
  pushd "%ROOT%"
  "!PYTHON!" "tools\go2_uwb_mock_showcase.py" %*
  set "EXIT_CODE=!ERRORLEVEL!"
  popd
  if not "!EXIT_CODE!"=="0" (
    echo.
    echo Startup failed.
    pause
  )
  exit /b !EXIT_CODE!
)

echo Go2-UWB-Mock.exe was not found.
echo Put the release package next to this BAT, or build the executable first.
echo Expected build path:
echo %ROOT%\artifacts\pyinstaller-build\Go2-UWB-Mock\
echo.
echo Startup failed.
pause
exit /b 1
