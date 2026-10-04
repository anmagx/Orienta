@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Virtual environment not found. Run install.bat first.
    exit /b 1
)

"%PYTHON%" -m pip install -r "%~dp0requirements-build.txt" || exit /b 1
"%PYTHON%" -m PyInstaller --noconfirm --clean --distpath "%~dp0dist" --workpath "%~dp0work" "%~dp0orienta.spec" || exit /b 1

echo Built "%~dp0dist\Orienta.exe"
