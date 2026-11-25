@echo off
chcp 65001 >nul
setlocal
title tsvpull - Tải code mới nhất từ GitHub
cd /d "%~dp0"
set "REPO=https://github.com/pjgate/tsvideo.git"
set "BR=main"

where git >nul 2>&1 || goto :no_git
call :ensure_repo || goto :fail

git status --porcelain | findstr . >nul
if errorlevel 1 goto :pull
echo.
echo  Lưu ý: máy này có thay đổi CHƯA tsvpush:
git status --short
echo  Các thay đổi này được giữ lại, bản mới từ GitHub sẽ được ghép vào.

:pull
echo.
echo  Đang tải bản mới nhất từ GitHub về máy %COMPUTERNAME%...
git pull --rebase --autostash origin %BR%
if errorlevel 1 goto :conflict

echo.
git log -1 --format=" Bản hiện tại: %%s  (%%cr)"
echo  XONG. Làm việc xong nhớ chạy tsvpush.
echo.
pause
exit /b 0

:conflict
echo.
echo  CÓ XUNG ĐỘT: cùng một file bị sửa ở cả hai máy.
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
git reset -q origin/%BR% || exit /b 1
for /f "delims=" %%F in ('git ls-files -d') do git checkout -q -- "%%F"
git branch -q -u origin/%BR% >nul 2>&1
:repo_ok
git remote get-url origin >nul 2>&1 || git remote add origin %REPO%
git config user.name >nul 2>&1 || git config user.name "%USERNAME% (%COMPUTERNAME%)"
git config user.email >nul 2>&1 || git config user.email "%USERNAME%@%COMPUTERNAME%.local"
exit /b 0
