---
name: tsvpush
description: TSVPUSH - commit và đẩy code app Nén Video (NenVideo) của máy này lên GitHub pjgate/tsvideo. Dùng khi người dùng gõ TSVPUSH, /tsvpush, hoặc xong đợt sửa trước khi đổi máy (máy công ty <-> máy nhà).
---

# tsvpush — đẩy code NenVideo lên GitHub

Repo: `https://github.com/pjgate/tsvideo`, nhánh `main`. Trả lời người dùng bằng tiếng Việt, ngắn gọn.
Tham số sau lệnh (nếu có) là ghi chú commit; không có thì tự viết ghi chú ngắn mô tả thay đổi.

## Cách A — đang chạy ngay trong thư mục code (Claude Code trên máy, thư mục có `.git`)
1. `git status --short`. Không có gì thay đổi và không có commit chưa đẩy → báo "không có gì mới" rồi dừng.
2. `git add -A` → `git commit -m "<ghi chú>"`.
3. `git pull --rebase origin main` (phòng máy kia đã đẩy trước). Xung đột → `git rebase --abort`, báo file nào xung đột, hỏi người dùng muốn giữ bản nào. Không tự ghi đè.
4. `git push origin main`. Báo commit hash + danh sách file.

Nếu thư mục chưa có `.git`: chạy `tsvpush.bat` có sẵn trong thư mục (nó tự nối repo, giữ nguyên file đang có), hoặc làm tay: `git init`, `git remote add origin <repo>`, `git fetch origin main`, `git reset origin/main` (mixed — không mất file), rồi làm tiếp bước 1.

## Cách B — phiên cloud nối với máy qua cầu thiết bị (có tool mcp__remote-devices__*)
Thư mục code trên máy là thư mục `NenVideo` đã kết nối (xem `get_device_info.connectedFolders`; trong device_bash là `$HOME/mnt/NenVideo`). Không chạy git trong thư mục đó (cầu thiết bị chặn xoá file khoá của git). Làm git trong bản clone ở container:
1. Bản clone `/home/claude/tsvideo`: nếu chưa có → `add_repo` (pjgate/tsvideo, access push) rồi clone theo hướng dẫn của tool. Nếu có → `git -C /home/claude/tsvideo pull --rebase origin main`.
2. Lấy danh sách + mã băm file trên máy, bỏ qua những gì `.gitignore` loại (dist/, build/, *.spec, __pycache__, video) và `.git/`:
   `cd $HOME/mnt/NenVideo && find . \( -path ./dist -o -path ./build -o -path ./.git -o -name __pycache__ \) -prune -o -type f ! -name '*.spec' ! -name '*.pyc' ! -iname '*.mp4' ! -iname '*.mov' ! -iname '*.mkv' ! -iname '*.avi' ! -iname '*.webm' ! -iname '*.m4v' ! -iname '*.flv' ! -iname '*.wmv' -print0 | xargs -0 md5sum`
   So với `md5sum` các file đã theo dõi trong clone (`git ls-files`).
3. File mới hoặc khác → `device_stage_files` (đường dẫn Windows của thư mục) → chép vào clone đúng đường dẫn tương đối. File có trong repo mà máy không còn → HỎI người dùng trước khi `git rm`.
4. `git add -A`, xem `git status --short`. Không có gì → báo "không có gì mới", dừng.
5. Commit với ghi chú, kèm các dòng attribution mà phiên yêu cầu. Đẩy: `git -c push.negotiate=true push origin main`. Bị từ chối vì máy kia đã đẩy → pull --rebase rồi đẩy lại; xung đột → dừng và hỏi.
6. Báo: commit hash, danh sách file đã đẩy.
