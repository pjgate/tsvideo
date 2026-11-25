@echo off
chcp 65001 >nul
title Nén Video
cd /d "%~dp0"

rem ---- tìm Python ----
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
    echo.
    echo  Chưa có Python trên máy.
    echo  Tải tại https://www.python.org/downloads/  ^(nhớ tích "Add python.exe to PATH" khi cài^)
    echo.
    pause
    exit /b 1
)

rem ---- lần đầu: cài ffmpeg (imageio-ffmpeg) + kéo thả (tkinterdnd2) ----
%PY% -c "import imageio_ffmpeg, tkinterdnd2" >nul 2>&1
if errorlevel 1 (
    echo  Lần đầu chạy: đang cài ffmpeg và thư viện kéo thả, chờ chút...
    %PY% -m pip install --upgrade --quiet imageio-ffmpeg tkinterdnd2
    if errorlevel 1 (
        echo.
        echo  Cài thư viện không thành công. Kiểm tra mạng rồi chạy lại run.bat.
        pause
        exit /b 1
    )
)

rem ---- mở app không kèm cửa sổ đen ----
set "PYW="
where pyw >nul 2>&1 && set "PYW=pyw -3"
if not defined PYW (where pythonw >nul 2>&1 && set "PYW=pythonw")
if defined PYW (
    start "" %PYW% "%~dp0nen_video.py" %*
) else (
    %PY% "%~dp0nen_video.py" %*
)
