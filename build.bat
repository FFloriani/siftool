@echo off
echo ==========================================
echo Siftool App Builder (Horizon 2)
echo ==========================================
echo.

:: Ensure virtual environment is present
if not exist .venv (
    echo Error: .venv virtual environment not found!
    echo Please run iniciar.bat first to set up dependencies.
    pause
    exit /b 1
)

echo [1/3] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [2/3] Building Siftool executable using PyInstaller...
.venv\Scripts\pyinstaller.exe --clean -y siftool.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Error: PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo [3/3] Build completed successfully!
echo.
echo Standalone distribution is available at:
echo   dist\siftool\siftool.exe
echo.
echo You can run it now!
echo ==========================================
pause
