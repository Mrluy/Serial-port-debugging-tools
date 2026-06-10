@echo off
cd /d "%~dp0"

python -m pip install -r requirements.txt pyinstaller pillow
python tools\make_icon.py
if exist "dist\COM串口调试工具.exe" del /f /q "dist\COM串口调试工具.exe"
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "COM串口调试工具" ^
  --icon "assets\app.generated.ico" ^
  --add-data "assets\app.png;assets" ^
  --hidden-import serial.tools.list_ports_windows ^
  main.py

if errorlevel 1 (
  echo.
  echo 打包失败。
  pause
  exit /b 1
)

echo.
echo 打包完成：dist\COM串口调试工具.exe
pause
