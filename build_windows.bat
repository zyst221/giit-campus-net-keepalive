@echo off
setlocal

pushd "%~dp0"
py -m pip install -r requirements-build.txt
if errorlevel 1 (
    popd
    exit /b 1
)

py -m PyInstaller --noconfirm --clean --onefile --windowed --name CampusNetKeepalive --specpath build campus_net_keepalive.py
set BUILD_RESULT=%ERRORLEVEL%
popd
exit /b %BUILD_RESULT%