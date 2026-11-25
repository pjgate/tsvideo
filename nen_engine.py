# -*- coding: utf-8 -*-
"""
nen_engine.py — Bộ máy nén video của app Nén Video (dùng FFmpeg).
==================================================================
  • Tìm ffmpeg: cạnh app (ffmpeg.exe / bin\\ffmpeg.exe) → PATH → gói pip imageio-ffmpeg
  • Đọc thông tin video bằng chính ffmpeg (không cần ffprobe)
  • Hai chế độ:
      - "chat_luong": nén theo mức chất lượng (CRF) → nhẹ nhất mà vẫn đẹp
      - "dung_luong": nén cho vừa một dung lượng mục tiêu (vd. 25 MB), 2 lượt cho chính xác
  • H.264 (phát được ở mọi nơi) hoặc H.265 (nhẹ hơn ~30–40%), có thể dùng card NVIDIA / Intel / AMD
  • Báo tiến trình theo thời gian thực, dừng được giữa chừng (file dở bị xoá)

Không dùng tkinter để kiểm thử độc lập; giao diện ở nen_video.py.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

IS_WIN = sys.platform.startswith("win")
# chạy ffmpeg không hiện cửa sổ đen và ở mức ưu tiên thấp để máy vẫn dùng mượt khi đang nén
NO_WINDOW = 0x08000000 if IS_WIN else 0
BELOW_NORMAL = 0x00004000 if IS_WIN else 0

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".mts", ".m2ts",
              ".ts", ".3gp", ".mpg", ".mpeg", ".vob", ".ogv", ".mxf", ".dv", ".f4v", ".asf"}
OUT_SUFFIX = "_danen"          # vd. "phim.mov" -> "phim_danen.mp4"

# CRF theo mức chất lượng: (H.264, H.265). Số càng nhỏ càng đẹp, file càng nặng.
CRF = {"cao": (20, 23), "can_bang": (23, 27), "nhe": (27, 31)}
# GPU dùng thang -cq / -global_quality, xấp xỉ tương đương
GPU_Q = {"cao": (21, 24), "can_bang": (25, 28), "nhe": (29, 32)}
AUDIO_KBPS = {"cao": 160, "can_bang": 128, "nhe": 96}
RESOLUTIONS = {"goc": None, "1080": 1080, "720": 720, "480": 480}   # theo CẠNH NGẮN (video dọc cũng đúng)

MB = 1000 * 1000             # "25 MB" tính chặt = 25.000.000 byte → hợp với mọi nơi đặt giới hạn 25 MB
MIN_VIDEO_KBPS = 80          # dưới mức này hình vỡ nát, báo người dùng thay vì nén ra rác


class NenError(Exception):
    pass


class Cancelled(Exception):
    pass


# ------------------------------------------------------------ tìm ffmpeg --
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg():
    """Đường dẫn ffmpeg dùng được, hoặc None."""
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    bases = [app_dir()]
    if getattr(sys, "_MEIPASS", None):
        bases.insert(0, sys._MEIPASS)
    for base in bases:
        for p in (os.path.join(base, exe), os.path.join(base, "bin", exe), os.path.join(base, "ffmpeg", "bin", exe)):
            if os.path.isfile(p):
                return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


def _run(args, timeout=60):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                          timeout=timeout, creationflags=NO_WINDOW)


def _decode(b):
    return b.decode("utf-8", errors="replace")


# ------------------------------------------------------ bộ mã hoá có sẵn --
_ENC_CACHE = {}


def available_encoders(ffmpeg):
    """{'libx264': True, 'libx265': True, 'h264_nvenc': True/False, ...} — GPU được thử thật."""
    if ffmpeg in _ENC_CACHE:
        return _ENC_CACHE[ffmpeg]
    try:
        out = _decode(_run([ffmpeg, "-hide_banner", "-encoders"], 20).stdout)
    except Exception:
        out = ""
    listed = set(re.findall(r"^\s*V\S*\s+(\S+)", out, re.M))
    res = {}
    for name in ("libx264", "libx265"):
        res[name] = name in listed
    # bộ mã hoá GPU có trong danh sách chưa chắc chạy được (máy không có card đó) → thử nén 1 khung nhỏ
    for name in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
        ok = False
        if name in listed:
            try:
                r = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                          "color=black:s=320x240:d=0.2", "-frames:v", "3", "-c:v", name, "-f", "null", "-"], 25)
                ok = r.returncode == 0
            except Exception:
                ok = False
        res[name] = ok
    _ENC_CACHE[ffmpeg] = res
    return res


def gpu_vendor(encs, codec):
    """Hãng GPU dùng được cho codec ('h264'/'hevc'): 'nvenc' / 'qsv' / 'amf' hoặc None."""
    for v in ("nvenc", "qsv", "amf"):
        if encs.get("%s_%s" % (codec, v)):
            return v
    return None


GPU_NAMES = {"nvenc": "NVIDIA", "qsv": "Intel", "amf": "AMD"}


# ------------------------------------------------------ đọc thông tin video --
def probe(ffmpeg, path):
    """Đọc thời lượng, kích thước, fps, có âm thanh không... từ thông báo của `ffmpeg -i`."""
    try:
        r = _run([ffmpeg, "-hide_banner", "-i", path], 60)
    except subprocess.TimeoutExpired:
        raise NenError("đọc file quá lâu")
    err = _decode(r.stderr)
    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False,
            "vcodec": "", "bitrate_kbps": 0, "size": 0}
    try:
        info["size"] = os.path.getsize(path)
    except OSError:
        pass
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Duration:.*?bitrate:\s*(\d+)\s*kb/s", err)
    if m:
        info["bitrate_kbps"] = int(m.group(1))
    vline = None
    for line in err.splitlines():
        if re.search(r"Stream #\S+.*: Video:", line) and "attached pic" not in line:
            vline = line
            break
    if vline is None:
        raise NenError("không có hình (không phải file video)")
    m = re.search(r"Video:\s*([A-Za-z0-9_]+)", vline)
    if m:
        info["vcodec"] = m.group(1)
    m = re.search(r",\s*(\d{2,5})x(\d{2,5})", vline)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+)\s*fps", vline) or re.search(r"([\d.]+)\s*tbr", vline)
    if m:
        try:
            info["fps"] = float(m.group(1))
        except ValueError:
            pass
    # video quay dọc trên điện thoại: hình lưu ngang + cờ xoay 90°
    if re.search(r"rotat\w*\s*(?:of|:)\s*-?(90|270)", err):
        info["width"], info["height"] = info["height"], info["width"]
    info["has_audio"] = bool(re.search(r"Stream #\S+.*: Audio:", err))
    if info["duration"] <= 0:
        raise NenError("không đọc được thời lượng video")
    return info


# ------------------------------------------------------------ dựng lệnh --
def default_settings():
    return {"che_do": "dung_luong",      # chat_luong | dung_luong  (mặc định: MP4 dưới 25 MB)
            "muc": "can_bang",           # cao | can_bang | nhe
            "muc_tieu_mb": 25.0,
            "codec": "h264",             # h264 | hevc
            "do_phan_giai": "goc",       # goc | 1080 | 720 | 480
            "gpu": False,
            "fps30": False,
            "bo_am_thanh": False,
            "thu_muc_ra": "",            # rỗng = cùng thư mục với video gốc
            "giu_neu_nang_hon": False}   # False: nếu bản nén nặng hơn gốc thì bỏ bản nén


def output_path(src, out_dir=""):
    folder = out_dir or os.path.dirname(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    cand = os.path.join(folder, stem + OUT_SUFFIX + ".mp4")
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(folder, "%s%s (%d).mp4" % (stem, OUT_SUFFIX, n))
        n += 1
    return cand


def _scale_filter(info, target_short):
    if not target_short:
        return None
    short = min(info["width"], info["height"]) if info["width"] and info["height"] else 0
    if short and short <= target_short:
        return None                                    # không phóng to video nhỏ
    t = int(target_short)
    # giới hạn cạnh ngắn, cạnh dài tự tính (luôn chẵn) — đúng cho cả video ngang lẫn dọc
    return "scale=w='if(lte(iw,ih),%d,-2)':h='if(lte(iw,ih),-2,%d)':flags=lanczos" % (t, t)


def plan(info, st, encs):
    """Tính toán cách nén → dict mô tả (bộ mã hoá, bitrate, độ phân giải...) để dựng lệnh và hiện cho người dùng."""
    codec = st["codec"] if st["codec"] in ("h264", "hevc") else "h264"
    lib = "libx264" if codec == "h264" else "libx265"
    if not encs.get(lib):
        if codec == "hevc" and encs.get("libx264"):
            codec, lib = "h264", "libx264"
        elif not encs.get("libx264"):
            raise NenError("bản ffmpeg này không có bộ mã hoá H.264")
    muc = st["muc"] if st["muc"] in CRF else "can_bang"
    audio = info["has_audio"] and not st["bo_am_thanh"]
    a_kbps = AUDIO_KBPS[muc] if audio else 0
    target_short = RESOLUTIONS.get(st["do_phan_giai"])
    fps = info["fps"] or 30.0
    if st["fps30"] and fps > 31:
        fps = 30.0

    p = {"codec": codec, "lib": lib, "audio": audio, "a_kbps": a_kbps, "mode": st["che_do"],
         "target_short": target_short, "fps30": st["fps30"] and (info["fps"] or 0) > 31, "gpu": None}

    if st["che_do"] == "dung_luong":
        mb = float(st["muc_tieu_mb"])
        if mb <= 0:
            raise NenError("dung lượng mục tiêu phải lớn hơn 0")
        total_kbps = mb * MB * 8 / 1000.0 / info["duration"] * 0.96     # chừa 4% cho phần vỏ file
        if audio:
            # video dài + mục tiêu nhỏ: hạ âm thanh trước để dành chỗ cho hình
            a_kbps = 64 if total_kbps < 500 else (96 if total_kbps < 1200 else a_kbps)
            p["a_kbps"] = a_kbps
        v_kbps = total_kbps - a_kbps
        if v_kbps < MIN_VIDEO_KBPS:
            need = (MIN_VIDEO_KBPS + a_kbps) * 1000 / 8 * info["duration"] / MB / 0.96
            raise NenError("video dài %s, mục tiêu %s MB quá nhỏ (cần ít nhất khoảng %s MB)"
                           % (fmt_duration(info["duration"]), _num(mb), _num(need * 1.1)))
        p["target_bytes"] = mb * MB
        if info["bitrate_kbps"] and v_kbps + a_kbps >= info["bitrate_kbps"] * 0.97:
            # dung lượng cho phép còn lớn hơn file gốc (vd. file .mov/.avi nhỏ): không đẩy bitrate lên
            # cao hơn bản gốc, nén theo chất lượng rồi kiểm tra lại dung lượng
            p.update(passes=1, crf=CRF[muc][0 if codec == "h264" else 1], v_kbps=None, already_small=True)
            return p
        # bitrate thấp mà vẫn giữ 4K/1080p thì hình bết → tự hạ độ phân giải cho hợp lý
        if info["width"] and info["height"]:
            short = min(info["width"], info["height"])
            cur = min(short, target_short) if target_short else short
            factor = 0.7 if codec == "hevc" else 1.0          # H.265 cần ít bitrate hơn cho cùng độ nét
            best = _best_res(cur, v_kbps / factor)
            if best is not None:
                p["target_short"] = best
                p["auto_res"] = True
        p["v_kbps"] = int(v_kbps)
        p["passes"] = 2
    else:
        vendor = gpu_vendor(encs, codec) if st["gpu"] else None
        p["gpu"] = vendor
        if vendor:
            p["q"] = GPU_Q[muc][0 if codec == "h264" else 1]
        else:
            p["crf"] = CRF[muc][0 if codec == "h264" else 1]
        p["passes"] = 1
    return p


def _best_res(cur_short, v_kbps_h264):
    """Độ phân giải (cạnh ngắn) lớn nhất hợp với bitrate H.264 đã cho; None nếu giữ nguyên được."""
    # bitrate tối thiểu để hình còn ổn ở từng mức (H.264, nội dung thông thường)
    need = [(2160, 9000), (1440, 5000), (1080, 2200), (720, 1000), (480, 400), (360, 0)]
    for res, k in need:
        if cur_short >= res and v_kbps_h264 >= k:
            return None if res >= cur_short else res
    return 360 if cur_short > 360 else None


def build_commands(ffmpeg, src, dst, info, p, passlog=None):
    """Danh sách lệnh ffmpeg (1 hoặc 2 lượt)."""
    vf = []
    sf = _scale_filter(info, p["target_short"])
    if sf:
        vf.append(sf)
    if p["fps30"]:
        vf.append("fps=30")
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src, "-map", "0:v:0"]
    if p["audio"]:
        base += ["-map", "0:a:0?"]
    if vf:
        base += ["-vf", ",".join(vf)]
    base += ["-pix_fmt", "yuv420p"]
    prog = ["-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    tag = ["-tag:v", "hvc1"] if p["codec"] == "hevc" else []          # để iPhone / Mac phát được H.265
    audio = ["-c:a", "aac", "-b:a", "%dk" % p["a_kbps"], "-ac", "2"] if p["audio"] else ["-an"]
    tail = ["-map_metadata", "0", "-movflags", "+faststart", dst]

    if p["passes"] == 2 and p.get("v_kbps"):
        vk = p["v_kbps"]
        null = "NUL" if IS_WIN else "/dev/null"
        if p["lib"] == "libx264":
            v1 = ["-c:v", "libx264", "-preset", "medium", "-b:v", "%dk" % vk, "-pass", "1", "-passlogfile", passlog]
            v2 = ["-c:v", "libx264", "-preset", "medium", "-b:v", "%dk" % vk, "-pass", "2", "-passlogfile", passlog]
        else:
            stats = passlog + ".x265"
            v1 = ["-c:v", "libx265", "-preset", "medium", "-b:v", "%dk" % vk,
                  "-x265-params", "pass=1:stats='%s':log-level=error" % stats.replace("\\", "/")]
            v2 = ["-c:v", "libx265", "-preset", "medium", "-b:v", "%dk" % vk,
                  "-x265-params", "pass=2:stats='%s':log-level=error" % stats.replace("\\", "/")]
        # lượt 1 chỉ phân tích hình, không cần âm thanh
        c1 = _strip_audio_map(base) + v1 + tag + ["-an", "-f", "mp4"] + prog + [null]
        c2 = base + v2 + tag + audio + prog + tail
        return [c1, c2]

    if p["gpu"]:
        enc = "%s_%s" % (p["codec"], p["gpu"])
        if p["gpu"] == "nvenc":
            v = ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(p["q"]), "-b:v", "0"]
        elif p["gpu"] == "qsv":
            v = ["-c:v", enc, "-preset", "medium", "-global_quality", str(p["q"])]
        else:
            v = ["-c:v", enc, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(p["q"]),
                 "-qp_p", str(p["q"] + 2)] + (["-qp_b", str(p["q"] + 4)] if p["codec"] == "h264" else [])
    elif p["lib"] == "libx264":
        v = ["-c:v", "libx264", "-preset", "medium", "-crf", str(p["crf"])]
    else:
        v = ["-c:v", "libx265", "-preset", "medium", "-crf", str(p["crf"]), "-x265-params", "log-level=error"]
    return [base + v + tag + audio + prog + tail]


def _strip_audio_map(base):
    out, skip = [], False
    for i, a in enumerate(base):
        if skip:
            skip = False
            continue
        if a == "-map" and i + 1 < len(base) and base[i + 1] == "0:a:0?":
            skip = True
            continue
        out.append(a)
    return out


# ------------------------------------------------------------------ nén --
class Job:
    """Nén một video. on_progress(phan_tram 0..1, toc_do_x, giay_con_lai) được gọi từ luồng nén."""

    def __init__(self, ffmpeg, src, settings, encs, info=None):
        self.ffmpeg = ffmpeg
        self.src = src
        self.st = settings
        self.encs = encs
        self.info = info
        self.proc = None
        self.cancel_event = threading.Event()
        self.dst = None
        self.plan = None

    def cancel(self):
        self.cancel_event.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def run(self, on_progress=None):
        """Trả về dict kết quả. Ném NenError khi lỗi, Cancelled khi bị dừng."""
        if self.info is None:
            self.info = probe(self.ffmpeg, self.src)
        info = self.info
        out_dir = self.st.get("thu_muc_ra") or ""
        t0 = time.time()
        old_size = info["size"]
        if self.st["che_do"] == "dung_luong" and old_size and \
                old_size <= float(self.st["muc_tieu_mb"]) * MB and \
                os.path.splitext(self.src)[1].lower() == ".mp4":
            # đã là MP4 và đã nhỏ hơn mục tiêu: giữ nguyên bản gốc, nén lại chỉ làm mờ hình
            return {"dst": None, "old": old_size, "new": old_size, "seconds": 0, "plan": None,
                    "kept": False, "skipped": "da_nho"}
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        self.plan = p = plan(info, self.st, self.encs)
        self.dst = dst = output_path(self.src, out_dir)
        tmp_dst = dst[:-4] + ".dang_nen.mp4"
        tmpdir = tempfile.mkdtemp(prefix="nenvideo_")
        passlog = os.path.join(tmpdir, "pass")
        try:
            attempt = 0
            while True:
                cmds = build_commands(self.ffmpeg, self.src, tmp_dst, info, p, passlog)
                n = len(cmds)
                for i, cmd in enumerate(cmds):
                    self._exec(cmd, info["duration"], i, n, t0 if attempt == 0 else time.time(), on_progress)
                if self.cancel_event.is_set():
                    raise Cancelled()
                if not os.path.isfile(tmp_dst) or os.path.getsize(tmp_dst) == 0:
                    raise NenError("ffmpeg không tạo ra file")
                new_size = os.path.getsize(tmp_dst)
                limit = p.get("target_bytes")
                if not limit or new_size <= limit:
                    break
                # lỡ vượt mục tiêu (cảnh nhiều chuyển động / bản nén theo chất lượng còn nặng):
                # nén lại 2 lượt với bitrate hạ theo đúng tỉ lệ vượt
                attempt += 1
                if attempt > 3:
                    raise NenError("không nén được xuống dưới %s MB" % _num(limit / MB))
                cur_total = new_size * 8 / 1000.0 / info["duration"]
                v_now = p["v_kbps"] or max(MIN_VIDEO_KBPS, cur_total - p["a_kbps"])
                v_new = int(v_now * (limit / float(new_size)) * 0.95)
                if v_new < MIN_VIDEO_KBPS:
                    raise NenError("không nén được xuống dưới %s MB mà hình còn xem được"
                                   % _num(limit / MB))
                p.update(v_kbps=v_new, passes=2, already_small=False, retried=attempt)
                if on_progress:
                    on_progress(0.0, 0.0, None)
            result = {"dst": dst, "old": old_size, "new": new_size, "seconds": time.time() - t0,
                      "plan": p, "kept": True}
            if p["mode"] == "chat_luong" and old_size and new_size >= old_size and not self.st.get("giu_neu_nang_hon"):
                os.remove(tmp_dst)
                result.update(kept=False, dst=None)
                return result
            os.replace(tmp_dst, dst)
            try:
                st = os.stat(self.src)
                os.utime(dst, (st.st_atime, st.st_mtime))          # giữ ngày quay gốc để ảnh/video xếp đúng
            except OSError:
                pass
            return result
        except BaseException:
            for f in (tmp_dst,):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except OSError:
                    pass
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _exec(self, cmd, duration, idx, n, t0, on_progress):
        if self.cancel_event.is_set():
            raise Cancelled()
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                         creationflags=NO_WINDOW | BELOW_NORMAL, bufsize=1,
                                         universal_newlines=True, encoding="utf-8", errors="replace")
        except OSError as e:
            raise NenError("không chạy được ffmpeg: %s" % e)
        err_lines = []

        def read_err():
            for line in self.proc.stderr:
                err_lines.append(line.rstrip())
                if len(err_lines) > 40:
                    del err_lines[0]
        te = threading.Thread(target=read_err, daemon=True)
        te.start()
        speed = 0.0
        for line in self.proc.stdout:
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms"):          # cả hai đều tính bằng micro giây
                try:
                    t = int(val) / 1e6
                except ValueError:
                    continue
                frac = (idx + max(0.0, min(1.0, t / duration))) / n
                if on_progress:
                    el = time.time() - t0
                    eta = el / frac - el if frac > 0.01 else None
                    on_progress(frac, speed, eta)
            elif key == "speed":
                try:
                    speed = float(val.rstrip("x"))
                except ValueError:
                    pass
        self.proc.wait()
        te.join(2)
        if self.cancel_event.is_set():
            raise Cancelled()
        if self.proc.returncode != 0:
            msg = next((l for l in reversed(err_lines) if l.strip()), "") or "mã lỗi %s" % self.proc.returncode
            raise NenError(_friendly(msg))
        if on_progress:
            on_progress((idx + 1) / n, speed, 0)


def _friendly(msg):
    low = msg.lower()
    if "no space left" in low:
        return "ổ đĩa đầy"
    if "permission denied" in low:
        return "không có quyền ghi vào thư mục"
    if "invalid data" in low or "moov atom not found" in low:
        return "file video bị hỏng hoặc chưa tải xong"
    if "nvenc" in low or "cuda" in low or "qsv" in low or "amf" in low:
        return "card đồ hoạ không nén được video này, hãy tắt “Dùng card đồ hoạ”: " + msg[:120]
    return msg[:200]


# -------------------------------------------------------------- tiện ích --
def _num(x):
    """25 -> '25', 2.5 -> '2.5', 0.05 -> '0.05'."""
    if x >= 10:
        return "%.0f" % x
    return ("%.2f" % x).rstrip("0").rstrip(".") if x < 1 else ("%.1f" % x).rstrip("0").rstrip(".")


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_our_output(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.endswith(OUT_SUFFIX) or re.search(re.escape(OUT_SUFFIX) + r" \(\d+\)$", stem) is not None \
        or stem.endswith(".dang_nen")


def collect_videos(paths):
    """Mở rộng thư mục thành danh sách video (đệ quy), bỏ các file do app tạo ra."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if is_video(fp) and not is_our_output(fp):
                        out.append(fp)
        elif os.path.isfile(p) and is_video(p):
            out.append(p)              # file được chọn trực tiếp: luôn nhận, kể cả bản đã nén
    return out


def fmt_size(n):
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" if unit in ("B", "KB") or n >= 100 else "%.1f %s") % (n, unit)
        n /= 1024.0


def fmt_duration(sec):
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def estimate_ratio(info, st):
    """Ước lượng thô dung lượng sau nén (byte) ở chế độ chất lượng — chỉ để hiện tham khảo."""
    if st["che_do"] == "dung_luong":
        return float(st["muc_tieu_mb"]) * MB
    short = min(info["width"], info["height"]) or 1080
    t = RESOLUTIONS.get(st["do_phan_giai"])
    if t and short > t:
        short = t
    # bitrate điển hình (kbps) H.264 CRF 23 cho cảnh quay bình thường, theo cạnh ngắn
    base = 6000 if short >= 2000 else 4200 if short >= 1400 else 3000 if short >= 1000 else 1500 if short >= 700 else 800
    base *= {"cao": 1.6, "can_bang": 1.0, "nhe": 0.55}[st["muc"] if st["muc"] in CRF else "can_bang"]
    if st["codec"] == "hevc":
        base *= 0.65
    fps = info["fps"] or 30
    if st["fps30"] and fps > 31:
        fps = 30
    base *= (fps / 30.0) ** 0.6
    a = 0 if (st["bo_am_thanh"] or not info["has_audio"]) else AUDIO_KBPS.get(st["muc"], 128)
    est = (base + a) * 1000 / 8 * info["duration"]
    if info["size"]:
        est = min(est, info["size"] * 0.98)
    return est
