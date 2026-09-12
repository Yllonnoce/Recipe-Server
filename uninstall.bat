@echo off
rem Recipe Library uninstaller for Windows.
rem
rem   uninstall.bat            stop + remove the scheduled task, firewall rules, .venv and config
rem   uninstall.bat /purge     ...and delete the library (all recipes, PDFs, database) after confirming
rem
rem Left alone: uv, Ollama and its models, and Playwright's Chromium cache (%LOCALAPPDATA%\ms-playwright).
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set PURGE=0
if /i "%~1"=="/purge" set PURGE=1
rem Honour RECIPELIB_CONFIG / RECIPELIB_LIBRARY_DIR like the app, then the config file.
rem If the library folder cannot be determined, /purge deletes nothing.
set "CONFIG=%APPDATA%\recipelib\config.toml"
if defined RECIPELIB_CONFIG set "CONFIG=%RECIPELIB_CONFIG%"
set "LIB="
if defined RECIPELIB_LIBRARY_DIR set "LIB=%RECIPELIB_LIBRARY_DIR%"
if not defined LIB if exist "%CONFIG%" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%CONFIG%") do (
    set "K=%%a"
    set "K=!K: =!"
    if /i "!K!"=="library_dir" (
      set "V=%%b"
      set "V=!V:"=!"
      for /f "tokens=1 delims=#" %%v in ("!V!") do set "LIB=%%v"
      set "LIB=!LIB: =!"
    )
  )
)

schtasks /Query /TN "Recipe Library" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo ==^> Stopping and removing the scheduled task
  schtasks /End /TN "Recipe Library" >nul 2>&1
  schtasks /Delete /TN "Recipe Library" /F >nul
)
taskkill /F /IM recipes.exe >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq recipes*" >nul 2>&1

netsh advfirewall firewall show rule name="Recipe Library web" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo ==^> Removing the Windows Firewall rules ^(admin prompt^)
  powershell -NoProfile -Command "Start-Process cmd -Verb RunAs -Wait -ArgumentList '/c netsh advfirewall firewall delete rule name=\"Recipe Library web\" & netsh advfirewall firewall delete rule name=\"Recipe Library printer\" & netsh advfirewall firewall delete rule name=\"Recipe Library discovery\"'"
)

if exist "%HERE%\.venv" (
  echo.
  echo ==^> Removing the Python environment %HERE%\.venv
  rmdir /s /q "%HERE%\.venv"
)
if exist "%CONFIG%" (
  echo.
  echo ==^> Removing the config file %CONFIG%
  del /q "%CONFIG%"
  rmdir "%APPDATA%\recipelib" >nul 2>&1
)
for /d /r "%HERE%\src" %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
for /d %%d in ("%HERE%\src\*.egg-info") do rmdir /s /q "%%d"

if "%PURGE%"=="1" (
  if not defined LIB (
    echo !!  Could not determine the library folder ^(no config found^); not deleting anything.
  ) else if exist "!LIB!\recipes.db" (
    echo.
    echo !!  This deletes every recipe, PDF and the database in: !LIB!
    set /p ANS=Type the full path above to confirm: 
    if /i "!ANS!"=="!LIB!" (
      rmdir /s /q "!LIB!"
      echo ==^> Library deleted
    ) else (
      echo ==^> Library kept
    )
  ) else (
    echo !!  !LIB! does not look like a Recipe Library folder ^(no recipes.db^); not deleting it.
  )
) else (
  echo.
  echo ==^> Your recipes are untouched in !LIB!  ^(re-run with /purge to delete them^)
)

echo.
echo ==^> Uninstalled. Still installed and safe to remove yourself if unwanted:
echo     uv:        del "%USERPROFILE%\.local\bin\uv.exe" "%USERPROFILE%\.local\bin\uvx.exe"
echo     Chromium:  rmdir /s /q "%LOCALAPPDATA%\ms-playwright"
echo     Ollama:    Settings ^> Apps   ^(model: ollama rm qwen3:8b^)
echo     This folder ^(%HERE%^) is just the source code; delete it if you like.
echo.
pause
exit /b 0
