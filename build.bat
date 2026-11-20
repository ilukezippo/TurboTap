@echo off
rem Builds dist\TurboTap.exe (single file, no console) - same options as the GitHub release workflow.
set VGAMEPAD_SKIP_VIGEMBUS_INSTALL=true
python -m pip install --upgrade pip pyinstaller
python -m pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name TurboTap ^
  --icon assets\TurboTap.ico --version-file version_info.txt ^
  --collect-submodules pynput --collect-all sdl2dll --collect-data sv_ttk --collect-all vgamepad ^
  TurboTap.pyw
echo.
echo Done: dist\TurboTap.exe
pause
