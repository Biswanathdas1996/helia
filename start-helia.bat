@echo off
setlocal

cd /d "%~dp0"

if exist ".env" call :load_env ".env"

where pnpm >nul 2>nul
if errorlevel 1 (
  echo pnpm is required but was not found on PATH.
  exit /b 1
)

if not exist "node_modules" (
  echo Installing workspace dependencies...
  call pnpm install
  if errorlevel 1 exit /b 1
)

if not defined NODE_ENV set "NODE_ENV=development"
if not defined HELIA_API_PORT set "HELIA_API_PORT=8080"
if not defined HELIA_WEB_PORT set "HELIA_WEB_PORT=21263"
if not defined BASE_PATH set "BASE_PATH=/"

set "API_PORT=%HELIA_API_PORT%"
set "WEB_PORT=%HELIA_WEB_PORT%"

call :ensure_free_port API_PORT "API"
call :ensure_free_port WEB_PORT "Web"

if "%WEB_PORT%"=="%API_PORT%" (
  echo Web port %WEB_PORT% matches the API port; trying the next free port...
  set /a WEB_PORT=%WEB_PORT%+1
  call :ensure_free_port WEB_PORT "Web"
)

if not defined API_ORIGIN set "API_ORIGIN=http://127.0.0.1:%API_PORT%"

echo Starting API on port %API_PORT%...
start "Helia API" cmd /k "cd /d "%~dp0" && set PORT=%API_PORT% && set NODE_ENV=%NODE_ENV% && pnpm --filter @workspace/api-server run dev"

echo Starting web app on port %WEB_PORT%...
start "Helia Web" cmd /k "cd /d "%~dp0" && set PORT=%WEB_PORT% && set BASE_PATH=%BASE_PATH% && set API_ORIGIN=%API_ORIGIN% && pnpm --filter @workspace/support-ai run dev"

echo Helia launch commands started.
echo API: http://127.0.0.1:%API_PORT%/api/healthz
echo Web: http://127.0.0.1:%WEB_PORT%/
exit /b 0

:load_env
for /f "usebackq eol=# tokens=1* delims==" %%A in (%1) do (
  if not "%%~A"=="" set "%%~A=%%~B"
)
exit /b 0

:ensure_free_port
set "PORT_VAR=%~1"
set "PORT_LABEL=%~2"
call set "PORT_VALUE=%%%PORT_VAR%%%"

:ensure_free_port_check
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT_VALUE% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo %PORT_LABEL% port %PORT_VALUE% is already in use; trying the next port...
  set /a PORT_VALUE=%PORT_VALUE%+1
  call set "%PORT_VAR%=%%PORT_VALUE%%"
  goto ensure_free_port_check
)
exit /b 0