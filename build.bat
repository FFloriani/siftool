@echo off
echo ==========================================
echo Siftool App Builder (Horizon 2)
echo ==========================================
echo This builds the OLD Tkinter program (siftool.py),
echo into dist\legacy. The new program (siftool_gui) is built with:
echo   .venv\Scripts\python.exe packaging\build.py
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
:: --distpath dist\legacy keeps this old program out of dist\Siftool, the folder packaging\build.py makes for the
:: new one (Windows paths ignore case: the old default output, dist\siftool, was that very folder).
.venv\Scripts\pyinstaller.exe --clean -y --distpath dist\legacy siftool.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Error: PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo [3/3] Build completed successfully!
echo.
echo Standalone distribution (the old Tkinter program) is available at:
echo   dist\legacy\siftool\siftool.exe
echo.
echo You can run it now!
echo ==========================================
pause
