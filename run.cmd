@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe was not found.
  echo Create the virtual environment and install dependencies, then try again.
  echo.
  pause
  exit /b 1
)

if not exist "codex_nomad_surface\app.py" (
  echo ERROR: codex_nomad_surface\app.py was not found.
  echo This script must be run from the repository root.
  echo.
  pause
  exit /b 1
)

set "SKIP_COMPONENT_BUILD=0"
set "STREAMLIT_ARGS="

:parse_arguments
if "%~1"=="" goto arguments_parsed

if "%~1"=="--skip-component-build" (
  set "SKIP_COMPONENT_BUILD=1"
  shift
  goto parse_arguments
)

if "%~1"=="--" (
  shift
  goto collect_streamlit_arguments
)

echo Usage: run.cmd [--skip-component-build] [-- STREAMLIT_ARGS...]
exit /b 2

:collect_streamlit_arguments
if "%~1"=="" goto arguments_parsed
set "STREAMLIT_ARGS=%STREAMLIT_ARGS% %1"
shift
goto collect_streamlit_arguments

:arguments_parsed
if "%SKIP_COMPONENT_BUILD%"=="1" goto run_streamlit

".venv\Scripts\python.exe" scripts\dev.py build-components
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto component_build_failed

:run_streamlit
".venv\Scripts\python.exe" -m streamlit run codex_nomad_surface/app.py --server.address 0.0.0.0%STREAMLIT_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Streamlit exited with error code %EXIT_CODE%.
  echo Review the error above, then press any key to close this window.
  echo.
  pause
)

exit /b %EXIT_CODE%

:component_build_failed
echo.
echo Component build exited with error code %EXIT_CODE%.
echo Review the error above, then press any key to close this window.
echo.
pause
exit /b %EXIT_CODE%
