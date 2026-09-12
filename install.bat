@echo off
rem Recipe Library installer for Windows 10/11. Double-click, or from a command prompt:
rem
rem   install.bat              install into .\.venv, set up config + library, pull model
rem   install.bat /service     ...and start at login (Task Scheduler, no window)
rem   install.bat /firewall    open TCP 8000, 8631 and UDP 5353 in Windows Firewall (admin prompt)
rem   install.bat /nomodel     skip the Ollama model download
rem   install.bat /nobrowser   skip the Chromium download
rem   install.bat /port80      serve the web app on port 80 (so the address is just http://<pc>)
rem
rem Nothing is installed system-wide except (optionally) Ollama and the firewall rules.
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
cd /d "%HERE%"
set SERVICE=0
set FIREWALL=0
set MODEL=1
set BROWSER=1
set PORT80=0
:args
if "%~1"=="" goto argsdone
if /i "%~1"=="/service" set SERVICE=1
if /i "%~1"=="/firewall" set FIREWALL=1
if /i "%~1"=="/nomodel" set MODEL=0
if /i "%~1"=="/nobrowser" set BROWSER=0
if /i "%~1"=="/port80" set PORT80=1
if /i "%~1"=="/?" goto help
shift
goto args
:argsdone

set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\Ollama;%PATH%"

echo.
echo ==^> Checking for uv (Python manager)
where uv >nul 2>&1
if errorlevel 1 (
  echo     Installing uv into %USERPROFILE%\.local\bin
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>&1
if errorlevel 1 (
  echo !!  uv did not install. See https://docs.astral.sh/uv/
  goto fail
)

echo.
echo ==^> Creating the Python 3.12 environment in %HERE%\.venv
uv venv --python 3.12 --allow-existing .venv
if errorlevel 1 goto fail
set "PY=%HERE%\.venv\Scripts\python.exe"
set "RECIPES=%HERE%\.venv\Scripts\recipes.exe"

echo.
echo ==^> Installing Recipe Library and its dependencies
uv pip install --python "%PY%" -e ".[ocr]"
if errorlevel 1 goto fail

if "%BROWSER%"=="1" (
  echo.
  echo ==^> Downloading Chromium for URL capture
  "%HERE%\.venv\Scripts\playwright.exe" install chromium
)

where ollama >nul 2>&1
if errorlevel 1 (
  echo.
  echo ==^> Ollama ^(local model runner^) is not installed
  where winget >nul 2>&1
  if errorlevel 1 (
    echo !!  Download it from https://ollama.com/download, run it, then: ollama pull qwen3:8b
  ) else (
    set /p ANS=Install it now with winget? [Y/n] 
    if /i not "!ANS!"=="n" (
      winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
      set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
    )
  )
)
if "%MODEL%"=="1" (
  where ollama >nul 2>&1
  if not errorlevel 1 (
    echo.
    echo ==^> Pulling the recipe model ^(qwen3:8b, about 5 GB, one time^)
    ollama pull qwen3:8b
    if errorlevel 1 echo !!  Model pull failed; is the Ollama app running? Run 'ollama pull qwen3:8b' later.
  )
)

echo.
echo ==^> Writing the config file and creating %USERPROFILE%\RecipeLibrary
"%RECIPES%" init
if "%PORT80%"=="1" (
  echo     Web app on port 80
  "%RECIPES%" config set port 80
  set "WEBPORT="
) else (
  set "WEBPORT=:8000"
)
"%RECIPES%" doctor

if "%FIREWALL%"=="1" (
  echo.
  echo ==^> Opening the web, printer and discovery ports in Windows Firewall ^(admin prompt^)
  powershell -NoProfile -Command "Start-Process cmd -Verb RunAs -Wait -ArgumentList '/c netsh advfirewall firewall add rule name=\"Recipe Library web\" dir=in action=allow protocol=TCP localport=80,8000 profile=private & netsh advfirewall firewall add rule name=\"Recipe Library printer\" dir=in action=allow protocol=TCP localport=8631 profile=private & netsh advfirewall firewall add rule name=\"Recipe Library discovery\" dir=in action=allow protocol=UDP localport=5353 profile=private'"
) else (
  echo.
  echo !!  Other devices need Windows Firewall to allow TCP 8000, 8631 and UDP 5353 on the Private profile.
  echo !!  Re-run with /firewall to add the rules, and set your Wi-Fi network to "Private" in Windows settings.
)

if "%SERVICE%"=="1" (
  echo.
  echo ==^> Installing the background service ^(starts at login, no window^)
  "%RECIPES%" service install
)

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do if not defined IP set "IP=%%a"
set "IP=%IP: =%"
if not defined IP set "IP=localhost"
echo.
echo ==^> Installed.
if "%SERVICE%"=="1" (
  echo     The server is running:  http://%IP%%WEBPORT%
) else (
  echo     Start it with:          "%RECIPES%" serve
  echo     then open:              http://%IP%%WEBPORT%
  echo     ^(re-run with /service to start it at login automatically^)
)
echo     Printer for other devices: 'Recipe Library'  ^(ipp://%IP%:8631/ipp/print^)
echo     Config: %APPDATA%\recipelib\config.toml
echo.
pause
exit /b 0

:help
for /f "tokens=* delims=" %%l in ('findstr /b "rem " "%~f0"') do echo %%l
exit /b 0

:fail
echo.
echo !!  Installation failed. Scroll up for the error.
pause
exit /b 1
