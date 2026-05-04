@echo off
setlocal

cd /d "%~dp0"

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

set "API_PORT=8080"
set "WEB_PORT=21263"
set "BASE_PATH=/"
set "API_ORIGIN=http://127.0.0.1:%API_PORT%"

echo Starting API on port %API_PORT%...
start "Helia API" cmd /k "cd /d "%~dp0" && set PORT=%API_PORT% && set NODE_ENV=development && pnpm --filter @workspace/api-server run dev"

echo Starting web app on port %WEB_PORT%...
start "Helia Web" cmd /k "cd /d "%~dp0" && set PORT=%WEB_PORT% && set BASE_PATH=%BASE_PATH% && set API_ORIGIN=%API_ORIGIN% && pnpm --filter @workspace/support-ai run dev"

echo Helia launch commands started.
echo API: http://127.0.0.1:%API_PORT%/api/healthz
echo Web: http://127.0.0.1:%WEB_PORT%/