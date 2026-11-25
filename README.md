# Nén Video

App Windows nhỏ gọn để giảm dung lượng video. **Mặc định: mỗi video ra file MP4 dưới 25 MB**, gửi Zalo / Messenger / Discord / Gmail đều lọt.

## Tính năng
- Kéo thả video hoặc cả thư mục, nén lần lượt từng cái, có thanh tiến trình và thời gian còn lại
- **Theo dung lượng** (mặc định 25 MB): nén 2 lượt cho vừa đúng; video dài tự hạ 1080p → 720p → 480p để không vỡ hình; nén xong tự kiểm tra, lỡ vượt thì nén lại cho vừa; MP4 đã nhỏ hơn mức này thì giữ nguyên
- **Theo chất lượng**: Đẹp / Cân bằng / Nhẹ nhất
- H.264 (phát ở mọi nơi) hoặc H.265 (nhẹ hơn ~30–40%), giới hạn độ phân giải, giới hạn 30 fps, bỏ âm thanh
- Dùng card đồ hoạ NVIDIA / Intel / AMD (chế độ chất lượng)
- File gốc giữ nguyên, bản nén lưu cạnh file gốc: `ten_goc_danen.mp4`
- Thêm vào chuột phải › Gửi tới › Nén Video

## Chạy
1. Cài [Python 3.8+](https://www.python.org/downloads/) (tích "Add python.exe to PATH")
2. Nháy đúp `run.bat` — lần đầu tự cài `imageio-ffmpeg` (có sẵn ffmpeg) và `tkinterdnd2` (kéo thả)

Đóng gói thành .exe: chạy `build_exe.bat` → `dist\NenVideo\NenVideo.exe` (mở trong `dist`, không phải `build`).

## Cấu trúc
| File | Vai trò |
|---|---|
| `nen_video.py` | Giao diện (tkinter) |
| `nen_engine.py` | Bộ máy nén dùng FFmpeg, không phụ thuộc giao diện |
| `run.bat` | Cài thư viện lần đầu + mở app |
| `build_exe.bat` | Đóng gói .exe bằng PyInstaller, kèm ffmpeg.exe |
| `HUONG_DAN.txt` | Hướng dẫn sử dụng chi tiết |
