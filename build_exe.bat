@echo off
chcp 65001 >nul
title Đóng gói Nén Video thành .exe
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
    echo  Chưa có Python trên máy. Tải tại https://www.python.org/downloads/
    pause & exit /b 1
)

echo  [1/3] Cài công cụ đóng gói...
%PY% -m pip install --upgrade --quiet pyinstaller imageio-ffmpeg tkinterdnd2 || (pause & exit /b 1)

echo  [2/3] Đóng gói app...
%PY% -m PyInstaller --noconfirm --clean --windowed --name "NenVideo" --icon icon.ico ^
    --add-data "icon.ico;." --add-data "icon.png;." ^
    --collect-all tkinterdnd2 ^
    nen_video.py || (pause & exit /b 1)

echo  [3/3] Chép ffmpeg vào cạnh app (để máy khác chạy được mà không cần cài gì)...
for /f "delims=" %%F in ('%PY% -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"') do set "FF=%%F"
copy /y "%FF%" "dist\NenVideo\ffmpeg.exe" >nul || (echo  Không chép được ffmpeg & pause & exit /b 1)

rem xoá thư mục nháp "build" (exe trong đó KHÔNG chạy được, dễ mở nhầm)
rmdir /s /q "build" >nul 2>&1
del /q "NenVideo.spec" >nul 2>&1

echo.
echo  Xong! App nằm ở:  %~dp0dist\NenVideo\NenVideo.exe
echo  Chép cả thư mục dist\NenVideo sang máy khác là chạy được.
echo.
explorer "%~dp0dist\NenVideo"
pause
