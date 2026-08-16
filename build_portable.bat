@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 1. Build tmp venv and install dependencies
python -m venv .venv_tmp
call ".venv_tmp\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

REM 2. Install Playwright browsers (they will be pulled into dist if not present)
python -m playwright install chromium
if errorlevel 1 goto :error

REM 3. Build with PyInstaller
python -m PyInstaller book_note_creator.spec --noconfirm
if errorlevel 1 goto :error

REM 4. Copy result to app/ and cleanup temp folders
set FINAL_APP=%~dp0app
if exist "%FINAL_APP%" rmdir /s /q "%FINAL_APP%" 2>nul
mkdir "%FINAL_APP%"
xcopy /E /I /Y "dist\BookNoteCreator" "%FINAL_APP%" > nul
REM Copy launcher to app/ for local running (overwrite if exists)
copy /Y "%~dp0app\start.bat" "%FINAL_APP%\start.bat" > nul
echo.
echo SUCCESS! Portable build created in %FINAL_APP%.
goto :end

:error
echo.
echo [!] ERROR: Build failed! Check logs above.
exit /b 1
:end
