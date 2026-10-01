@echo off
cd /d "%~dp0"
if not exist "data\local\catalog.json" (
  echo Preparing local SKeyDB art and names...
  python sync_skeydb_assets.py
  if errorlevel 1 pause & exit /b 1
)
python app.py --port 8765 --open
pause
