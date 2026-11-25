---
name: tsvpull
description: TSVPULL - kéo code mới nhất của app Nén Video (NenVideo) từ GitHub pjgate/tsvideo về máy này. Dùng khi người dùng gõ TSVPULL, /tsvpull, hoặc vừa ngồi vào máy (đổi từ máy công ty sang máy nhà hoặc ngược lại).
---

# tsvpull — kéo code NenVideo từ GitHub về máy

Repo: `https://github.com/pjgate/tsvideo`, nhánh `main`. Trả lời bằng tiếng Việt, ngắn gọn.
Nguyên tắc: KHÔNG làm mất thay đổi chưa đẩy trên máy. Gặp xung đột thì dừng và hỏi.

## Cách A — đang chạy ngay trong thư mục code (Claude Code trên máy, thư mục có `.git`)
1. `git status --short` — có thay đổi chưa đẩy thì báo cho người dùng biết (vẫn giữ lại).
2. `git pull --rebase --autostash origin main`.
3. Xung đột → `git rebase --abort` (nếu đang rebase), báo file xung đột, hỏi người dùng giữ bản nào.
4. Báo: commit mới nhất (`git log -1 --format="%h %s (%cr)"`) và các file vừa thay đổi.

Nếu thư mục chưa có `.git` (máy mới): chạy `tsvpull.bat`, hoặc thư mục rỗng thì `git clone https://github.com/pjgate/tsvideo .`.

## Cách B — phiên cloud nối với máy qua cầu thiết bị (có tool mcp__remote-devices__*)
Thư mục code trên máy là `NenVideo` đã kết nối (`$HOME/mnt/NenVideo` trong device_bash). Không chạy git trong thư mục đó; làm qua bản clone `/home/claude/tsvideo`.
1. Clone chưa có → `add_repo` (pjgate/tsvideo) rồi clone; khi đó mốc cũ OLD = rỗng. Có rồi → ghi `OLD=$(git rev-parse HEAD)` rồi `git pull --rebase origin main`.
2. Lấy danh sách file đổi: `git diff --name-status OLD HEAD` (clone mới: mọi file trong `git ls-files`).
3. Với mỗi file đổi/mới, so md5 của file trên máy với bản ở OLD (`git show OLD:<file> | md5sum`):
   - Máy đang giống bản OLD, hoặc máy chưa có file → an toàn: chép bản HEAD vào `/mnt/user-data/outputs/tsvpull/<file>` rồi `device_commit_files` về đúng đường dẫn trên máy.
   - Máy đã khác bản OLD (sửa chưa đẩy) mà GitHub cũng đổi → XUNG ĐỘT: không ghi đè, liệt kê và hỏi người dùng.
   - Máy đã giống bản HEAD → bỏ qua.
4. File bị xoá trên GitHub → hỏi người dùng trước khi xoá trên máy.
5. Báo: commit mới nhất và các file đã cập nhật trên máy.
