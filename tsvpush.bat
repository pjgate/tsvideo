@echo off
chcp 65001 >nul
setlocal
title tsvpush - Đẩy code lên GitHub
cd /d "%~dp0"
set "REPO=https://github.com/pjgate/tsvideo.git"
set "BR=main"

where git >nul 2>&1 || goto :no_git
call :ensure_repo || goto :fail

echo.
echo  [1/3] Gom thay đổi trên máy %COMPUTERNAME%...
git add -A
git diff --cached --quiet
if not errorlevel 1 goto :no_change

git status --short
set "MSG=%*"
if not defined MSG set /p "MSG= Ghi chú cho lần lưu này (Enter = bỏ qua): "
if not defined MSG set "MSG=Cập nhật từ %COMPUTERNAME% lúc %date% %time:~0,5%"
git commit -q -m "%MSG%" || goto :fail
goto :sync

:no_change
echo  Không có thay đổi mới để lưu.

:sync
echo  [2/3] Lấy bản mới nhất trên GitHub (phòng máy kia đã đẩy trước)...
git pull -q --rebase origin %BR%
if errorlevel 1 goto :conflict

echo  [3/3] Đẩy lên GitHub...
git push -u origin %BR% || goto :fail

echo.
echo  XONG. Sang máy kia chạy tsvpull là có bản mới.
echo.
pause
exit /b 0

:conflict
git rebase --abort >nul 2>&1
echo.
echo  CÓ XUNG ĐỘT: cùng một file bị sửa ở cả hai máy.
echo  Chưa đẩy gì lên. Bản lưu của máy này vẫn còn nguyên trong git.
echo  Mở Claude Code / GitHub Desktop để gộp, hoặc báo lại để xử lý.
goto :fail

:no_git
echo  Chưa có Git trên máy. Tải tại https://git-scm.com/download/win rồi chạy lại.
:fail
echo.
echo  Không thành công - xem thông báo phía trên.
pause
exit /b 1

rem ---------- lần đầu trên máy mới: nối thư mục này với repo GitHub ----------
:ensure_repo
if exist ".git" goto :repo_ok
echo  Lần đầu trên máy này: nối thư mục với %REPO% ...
git init -q || exit /b 1
git symbolic-ref HEAD refs/heads/%BR%
git remote add origin %REPO%
git fetch -q origin %BR% || exit /b 1
rem giữ nguyên file đang có, chỉ nhận lịch sử từ GitHub
git reset -q origin/%BR% || exit /b 1
rem lấy về các file có trên GitHub mà máy này chưa có
for /f "delims=" %%F in ('git ls-files -d') do git checkout -q -- "%%F"
git branch -q -u origin/%BR% >nul 2>&1
:repo_ok
git remote get-url origin >nul 2>&1 || git remote add origin %REPO%
git config user.name >nul 2>&1 || git config user.name "%USERNAME% (%COMPUTERNAME%)"
git config user.email >nul 2>&1 || git config user.email "%USERNAME%@%COMPUTERNAME%.local"
exit /b 0
