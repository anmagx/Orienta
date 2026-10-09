@echo off
setlocal
set "PYTHON=%~dp0dev\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Virtual environment not found. Run build\dev\setup_dev.bat first.
    exit /b 1
)

"%PYTHON%" -m pip install -r "%~dp0requirements-build.txt" || exit /b 1
"%PYTHON%" -m PyInstaller --noconfirm --clean --distpath "%~dp0dist" --workpath "%~dp0work" "%~dp0orienta.spec" || exit /b 1

echo Build complete. Versioned executable is in "%~dp0dist"
