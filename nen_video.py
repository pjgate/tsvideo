# -*- coding: utf-8 -*-
"""
Nén Video - Giảm dung lượng video, mặc định xuất MP4 dưới 25 MB
================================================================
  - Kéo thả video / thư mục vào cửa sổ (hoặc bấm “+ Thêm video”), nén lần lượt từng cái
  - Mặc định: mỗi video ra file MP4 DƯỚI 25 MB (tự hạ độ phân giải nếu video quá dài,
    nén xong tự kiểm tra lại, lỡ vượt thì nén lại cho vừa)
  - Hoặc chọn nén theo chất lượng: Đẹp / Cân bằng / Nhẹ nhất
  - H.264 (phát được ở mọi nơi) hoặc H.265 (nhẹ hơn), giảm độ phân giải, giới hạn 30 fps,
    dùng card đồ hoạ NVIDIA / Intel / AMD cho nhanh
  - File gốc giữ nguyên, bản nén lưu cạnh file gốc với tên “..._danen.mp4”
  - Thêm vào menu chuột phải › Gửi tới (Send to) › Nén Video

Yêu cầu : Python 3.8 trở lên + imageio-ffmpeg (run.bat tự cài, có sẵn ffmpeg bên trong).
          Kéo thả cần thêm tkinterdnd2 (run.bat tự cài).
Chạy    : python nen_video.py [file / thư mục ...]   (hoặc nháy đúp run.bat)
"""

import base64
import datetime as dt
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import nen_engine as ne

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    TkinterDnD = None
    DND_FILES = None

APP_NAME = "Nén Video"
APP_VERSION = "1.0"

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# ---------------------------------------------------------------- màu sắc --
BG = "#0d1117"
CARD = "#161b22"
BORDER = "#262d38"
TRACK = "#222a35"
TEXT = "#e6edf3"
MUTED = "#8b949e"
DIM = "#5b6572"
SELECT = "#1d3b63"

C_ACC = "#f0883e"      # màu chủ đạo của Nén Video (cam)
C_BLUE = "#4c8dff"
C_OK = "#3fb950"
C_WARN = "#d29922"
C_BAD = "#f85149"

S = 1.0
F = {}

MODES = [("dung_luong", "Theo dung lượng"), ("chat_luong", "Theo chất lượng")]
LEVELS = [("cao", "Đẹp"), ("can_bang", "Cân bằng"), ("nhe", "Nhẹ nhất")]
CODECS = [("h264", "H.264"), ("hevc", "H.265")]
RES = [("goc", "Giữ nguyên"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]
QUICK_MB = (10, 25, 50, 100)


# --------------------------------------------------------------- tiện ích --
def px(v):
    return int(round(v * S))


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x * t + y * (1 - t))) for x, y in zip(a, b))


def shorten(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def data_dir():
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "NenVideo")
    else:
        d = os.path.join(os.path.expanduser("~"), ".nenvideo")
    os.makedirs(d, exist_ok=True)
    return d


def open_path(path):
    if IS_WIN:
        os.startfile(path)                      # noqa
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path):
    """Mở thư mục chứa file và chọn sẵn file đó."""
    if IS_WIN and os.path.exists(path):
        subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))
    elif IS_MAC and os.path.exists(path):
        subprocess.Popen(["open", "-R", path])
    else:
        open_path(os.path.dirname(path))


def fmt_seconds(sec):
    sec = int(round(sec))
    if sec < 60:
        return "%d giây" % max(1, sec)
    m, s = divmod(sec, 60)
    if m < 60:
        return "%d phút %d giây" % (m, s) if s else "%d phút" % m
    h, m = divmod(m, 60)
    return "%d giờ %d phút" % (h, m)


def fmt_eta(sec):
    if sec is None:
        return ""
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def text_bar(frac, n=10):
    k = int(round(max(0.0, min(1.0, frac)) * n))
    return "▰" * k + "▱" * (n - k)


# ------------------------------------------------------------ cài đặt --
class Settings:
    def __init__(self, folder):
        self.path = os.path.join(folder, "cai_dat.json")
        self.lock = threading.Lock()
        self.data = ne.default_settings()
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k in self.data:
                    if k in loaded:
                        self.data[k] = loaded[k]
        except (OSError, ValueError):
            pass

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass


# ----------------------------------------------- menu chuột phải › Gửi tới --
def sendto_path():
    base = os.environ.get("APPDATA") or ""
    return os.path.join(base, "Microsoft", "Windows", "SendTo", APP_NAME + ".lnk")


def launch_target():
    """(chương trình, tham số) để mở app — dùng cho lối tắt Gửi tới."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return exe, '"%s"' % os.path.abspath(__file__)


def set_sendto(enabled):
    path = sendto_path()
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    exe, args = launch_target()
    icon = resource_path("icon.ico")

    def q(s):
        return "'" + s.replace("'", "''") + "'"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%s);$s.TargetPath=%s;$s.Arguments=%s;"
          "$s.WorkingDirectory=%s;%s$s.Save()" % (q(path), q(exe), q(args), q(ne.app_dir()),
                                                 ("$s.IconLocation=%s;" % q(icon)) if os.path.exists(icon) else ""))
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")      # tránh lỗi đường dẫn có dấu
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ne.NO_WINDOW, timeout=30)
    if r.returncode != 0 or not os.path.exists(path):
        raise OSError(r.stderr.decode("utf-8", errors="replace").strip()[:300] or "không tạo được lối tắt")


# ------------------------------------------- chỉ cho chạy một bản duy nhất --
SINGLE_PORT = 47823          # PC Monitor 47821, Cầu Nối 47822


def acquire_single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", SINGLE_PORT))
        s.listen(4)
        return s
    except OSError:
        s.close()
        return None


def send_to_existing(paths):
    """Chuyển các file cho cửa sổ Nén Video đang mở (khi bấm Gửi tới lần nữa)."""
    try:
        c = socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=2)
        c.sendall(("NENVIDEO\n" + "\n".join(paths)).encode("utf-8"))
        c.close()
        return True
    except OSError:
        return False


# ------------------------------------------------------- thành phần giao diện --
def init_fonts(root):
    fams = set(tkfont.families(root))
    lower = {f.lower(): f for f in fams}

    def pick(*names):
        for n in names:
            if n.lower() in lower:
                return lower[n.lower()]
        return tkfont.nametofont("TkDefaultFont").actual("family")

    ui = pick("Segoe UI", "Helvetica Neue", "Noto Sans", "Ubuntu", "DejaVu Sans", "Arial")
    semi = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else None
    b = 2 if IS_MAC else 0

    def strong(size):
        return (semi, size + b) if semi else (ui, size + b, "bold")

    F.update(title=strong(13), med=strong(12), big=strong(11), textb=strong(10), label=strong(8),
             text=(ui, 10 + b), small=(ui, 9 + b), tiny=(ui, 8 + b))


class Fitter:
    """Rút gọn chữ cho vừa bề rộng cột (có dấu …)."""

    def __init__(self, font):
        self.font = tkfont.Font(font=font)
        self.cache = {}

    def fit(self, text, width):
        key = (text, width)
        if key in self.cache:
            return self.cache[key]
        if width <= 0 or self.font.measure(text) <= width:
            res = text
        else:
            lo, hi = 1, len(text) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.font.measure(text[:mid] + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            res = text[:lo] + "…"
        if len(self.cache) > 3000:
            self.cache.clear()
        self.cache[key] = res
        return res


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def draw_logo(cv, sz):
    """Biểu tượng: nút phát video bị ép nhỏ lại bởi hai mũi tên."""
    round_rect(cv, 1, 1, sz - 1, sz - 1, px(9), fill=C_ACC, outline="")
    cv.create_polygon(sz * 0.40, sz * 0.33, sz * 0.40, sz * 0.67, sz * 0.66, sz * 0.50, fill="white", outline="")
    w = max(2, px(2))
    for x, d in ((0.20, 1), (0.84, -1)):
        cv.create_line(sz * x, sz * 0.50, sz * (x + 0.10 * d), sz * 0.50, fill="white", width=w,
                       arrow="last", arrowshape=(px(4), px(4), px(3)))


class Card(tk.Frame):
    def __init__(self, master, title, color=None):
        super().__init__(master, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        head = tk.Frame(self, bg=CARD)
        head.pack(fill="x", padx=px(14), pady=(px(12), px(8)))
        if color:
            d = px(8)
            dot = tk.Canvas(head, width=d, height=d, bg=CARD, highlightthickness=0)
            dot.create_oval(0, 0, d - 1, d - 1, fill=color, outline=color)
            dot.pack(side="left", padx=(0, px(8)))
        tk.Label(head, text=title.upper(), bg=CARD, fg=MUTED, font=F["label"]).pack(side="left")
        self.head = head
        self.note = tk.Label(head, text="", bg=CARD, fg=MUTED, font=F["small"])
        self.note.pack(side="right")
        self.body = tk.Frame(self, bg=CARD)
        self.body.pack(fill="both", expand=True, padx=px(14), pady=(0, px(14)))


class FlatButton(tk.Label):
    def __init__(self, master, text, command=None, accent=C_ACC, danger=False, bg=CARD, font=None,
                 padx=10, pady=4):
        super().__init__(master, text=text, font=font or F["small"], cursor="hand2",
                         padx=px(padx), pady=px(pady), bd=0)
        self.command = command
        self.accent = accent
        self.danger = danger
        self.base_bg = bg
        self.active = False
        self.hover = False
        self.enabled = True
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.enabled and self.command and self.command())
        self._paint()

    def _set_hover(self, v):
        self.hover = v
        self._paint()

    def set_active(self, v):
        self.active = v
        self._paint()

    def set_enabled(self, v):
        self.enabled = v
        self.config(cursor="hand2" if v else "arrow")
        self._paint()

    def _paint(self):
        if not self.enabled:
            bg, fg = (blend(self.accent, CARD, 0.18) if self.active else self.base_bg), DIM
        elif self.active:
            bg = blend(self.accent, CARD, 0.55 if self.hover else 0.38)
            fg = TEXT
        elif self.hover:
            bg = blend(C_BAD, CARD, 0.3) if self.danger else TRACK
            fg = TEXT
        else:
            bg, fg = self.base_bg, (TEXT if self.danger else MUTED)
        self.config(bg=bg, fg=fg)


class Segmented(tk.Frame):
    """Nhóm nút chọn một."""

    def __init__(self, master, options, value, on_change=None, bg=CARD):
        super().__init__(master, bg=BORDER, padx=1, pady=1)
        self.value = value
        self.on_change = on_change
        self.btns = {}
        for key, label in options:
            b = FlatButton(self, label, lambda k=key: self.set(k), bg=bg, padx=9)
            b.pack(side="left", fill="x", expand=True)
            self.btns[key] = b
        self.set(value, notify=False)

    def set(self, key, notify=True):
        if key not in self.btns:
            key = next(iter(self.btns))
        self.value = key
        for k, b in self.btns.items():
            b.set_active(k == key)
        if notify and self.on_change:
            self.on_change(key)


class Toggle(tk.Frame):
    """Ô tích tự vẽ kèm dòng giải thích."""

    def __init__(self, master, text, value=False, sub="", bg=CARD, command=None, wrap=None):
        super().__init__(master, bg=bg, cursor="hand2")
        self.value = bool(value)
        self.command = command
        self.enabled = True
        s = px(16)
        self.cv = tk.Canvas(self, width=s, height=s, bg=bg, highlightthickness=0, cursor="hand2")
        self.cv.grid(row=0, column=0, sticky="nw", pady=(px(2), 0))
        self.lbl = tk.Label(self, text=text, bg=bg, fg=TEXT, font=F["text"], anchor="w", cursor="hand2")
        self.lbl.grid(row=0, column=1, sticky="w", padx=(px(8), 0))
        self.sub = tk.Label(self, text=sub, bg=bg, fg=MUTED, font=F["small"], anchor="w", justify="left",
                            cursor="hand2", wraplength=wrap or px(260))
        if sub:
            self.sub.grid(row=1, column=1, sticky="w", padx=(px(8), 0))
        self.columnconfigure(1, weight=1)
        for wdg in (self, self.cv, self.lbl, self.sub):
            wdg.bind("<Button-1>", lambda e: self.toggle())
        self._draw()

    def toggle(self):
        if not self.enabled:
            return
        self.set(not self.value)
        if self.command:
            self.command(self.value)

    def set(self, v):
        self.value = bool(v)
        self._draw()

    def set_enabled(self, v):
        self.enabled = v
        self.lbl.config(fg=TEXT if v else DIM)
        self._draw()

    def set_sub(self, text):
        if self.sub.cget("text") != text:
            self.sub.config(text=text)
        if text and not self.sub.winfo_ismapped():
            self.sub.grid(row=1, column=1, sticky="w", padx=(px(8), 0))

    def _draw(self):
        cv = self.cv
        cv.delete("all")
        s = int(cv["width"])
        if self.value and self.enabled:
            round_rect(cv, 1, 1, s - 1, s - 1, px(4), fill=C_ACC, outline="")
            cv.create_line(s * 0.25, s * 0.52, s * 0.43, s * 0.70, s * 0.76, s * 0.32,
                           fill="white", width=max(2, px(2)), capstyle="round", joinstyle="round")
        else:
            round_rect(cv, 1, 1, s - 1, s - 1, px(4), fill=TRACK, outline=DIM)


class Field(tk.Frame):
    def __init__(self, master, value="", width=10, justify="left"):
        super().__init__(master, bg=TRACK, highlightthickness=1, highlightbackground=BORDER)
        self.var = tk.StringVar(value=value)
        self.entry = tk.Entry(self, textvariable=self.var, bg=TRACK, fg=TEXT, insertbackground=TEXT,
                              relief="flat", bd=0, font=F["text"], highlightthickness=0, width=width,
                              selectbackground=SELECT, selectforeground=TEXT, justify=justify)
        self.entry.pack(fill="x", padx=px(8), pady=px(5))
        self.entry.bind("<FocusIn>", lambda e: self.config(highlightbackground=C_ACC))
        self.entry.bind("<FocusOut>", lambda e: self.config(highlightbackground=BORDER))

    def get(self):
        return self.var.get().strip()

    def set(self, v):
        self.var.set(v)
        self.entry.icursor("end")
        self.entry.xview_moveto(1.0)


class ProgressBar(tk.Canvas):
    def __init__(self, master, height=8, bg=CARD):
        super().__init__(master, height=px(height), bg=bg, highlightthickness=0)
        self.frac = 0.0
        self.color = C_ACC
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, frac, color=C_ACC):
        frac = max(0.0, min(1.0, frac))
        if abs(frac - self.frac) < 0.001 and color == self.color:
            return
        self.frac, self.color = frac, color
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self["height"])
        if w < 4:
            return
        r = h // 2
        round_rect(self, 0, 0, w, h, r, fill=TRACK, outline="")
        if self.frac > 0:
            round_rect(self, 0, 0, max(h, int(w * self.frac)), h, r, fill=self.color, outline="")


# ----------------------------------------------------------- luồng chạy nền --
class Worker(threading.Thread):
    """Đọc thông tin và nén lần lượt từng video. Gửi sự kiện về giao diện qua hàng đợi events."""

    def __init__(self, settings, events):
        super().__init__(daemon=True)
        self.settings = settings
        self.events = events
        self.ffmpeg = None
        self.encs = {}
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.probe_q = []          # (iid, path)
        self.jobs = []             # (iid, path, info)
        self.current = None        # Job đang chạy
        self.current_id = None
        self.stop_all = threading.Event()
        self.wake = threading.Event()
        self.quit = threading.Event()

    def add_probe(self, iid, path):
        with self.lock:
            self.probe_q.append((iid, path))
        self.wake.set()

    def enqueue(self, items):
        with self.lock:
            self.stop_all.clear()
            for it in items:
                if all(j[0] != it[0] for j in self.jobs) and it[0] != self.current_id:
                    self.jobs.append(it)
        self.wake.set()

    def remove(self, iid):
        with self.lock:
            self.jobs = [j for j in self.jobs if j[0] != iid]
            self.probe_q = [p for p in self.probe_q if p[0] != iid]

    def is_queued(self, iid):
        with self.lock:
            return any(j[0] == iid for j in self.jobs)

    def busy(self):
        with self.lock:
            return bool(self.jobs) or self.current_id is not None

    def stop(self):
        """Dừng video đang nén và bỏ hết hàng chờ."""
        with self.lock:
            dropped = [j[0] for j in self.jobs]
            self.jobs = []
            self.stop_all.set()
            job = self.current
        if job:
            job.cancel()
        return dropped

    def shutdown(self):
        self.quit.set()
        self.stop()
        self.wake.set()

    def run(self):
        try:
            self.ffmpeg = ne.find_ffmpeg()
            if self.ffmpeg:
                self.encs = ne.available_encoders(self.ffmpeg)
        except Exception:
            traceback.print_exc()
        self.ready.set()
        self.events.put(("ffmpeg", self.ffmpeg, self.encs))
        while not self.quit.is_set():
            task = None
            with self.lock:
                if self.probe_q:
                    task = ("probe",) + self.probe_q.pop(0)
                elif self.jobs and self.ffmpeg:
                    task = ("job",) + self.jobs.pop(0)
                    self.current_id = task[1]
            if task is None:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            if task[0] == "probe":
                self._probe(task[1], task[2])
            else:
                self._job(task[1], task[2], task[3])

    def _probe(self, iid, path):
        if not self.ffmpeg:
            return
        try:
            info = ne.probe(self.ffmpeg, path)
            self.events.put(("info", iid, info))
        except ne.NenError as e:
            self.events.put(("info_err", iid, str(e)))
        except Exception as e:
            traceback.print_exc()
            self.events.put(("info_err", iid, "không đọc được: %s" % e))

    def _job(self, iid, path, info):
        st = self.settings.snapshot()            # cài đặt lúc video này BẮT ĐẦU nén
        job = ne.Job(self.ffmpeg, path, st, self.encs, info)
        with self.lock:
            self.current = job
            if self.stop_all.is_set():
                job.cancel()
        self.events.put(("start", iid, st))

        def prog(frac, speed, eta):
            self.events.put(("progress", iid, frac, speed, eta, (job.plan or {}).get("retried")))
        try:
            if not os.path.exists(path):
                raise ne.NenError("không còn thấy file gốc")
            res = job.run(prog)
            self.events.put(("done", iid, res))
        except ne.Cancelled:
            self.events.put(("cancelled", iid))
        except ne.NenError as e:
            self.events.put(("error", iid, str(e)))
        except Exception as e:
            traceback.print_exc()
            self.events.put(("error", iid, "lỗi không mong muốn: %s" % e))
        finally:
            with self.lock:
                self.current = None
                self.current_id = None
                idle = not self.jobs
            if idle:
                self.events.put(("idle",))


# ---------------------------------------------------------------- ứng dụng --
class App:
    def __init__(self, root, initial_paths=(), server=None):
        global S
        self.root = root
        self.server = server
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.items = {}            # iid -> dict(path, info, status, msg, frac, speed, eta, result)
        self.order = []
        self.run_ids = []          # các video trong đợt nén hiện tại (để tính tiến trình chung)
        self.run_started = None
        self.ffmpeg_ok = None
        self.encs = {}
        self.dirty = True
        self._quitting = False

        S = max(1.0, min(3.0, root.winfo_fpixels("1i") / 96.0))
        init_fonts(root)
        self.settings = Settings(data_dir())
        self.worker = Worker(self.settings, self.events)

        self._setup_window()
        self._setup_style()
        self._build()
        self._load_settings_to_ui()

        self.worker.start()
        if self.server is not None:
            threading.Thread(target=self._listen, daemon=True).start()
        try:
            if IS_WIN and os.path.exists(sendto_path()):
                set_sendto(True)          # thư mục app bị dời đi: cập nhật lối tắt
        except Exception:
            pass
        if initial_paths:
            self.add_paths(initial_paths)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.render()
        self.root.after(150, self.tick)

    # --- cửa sổ & kiểu dáng ---
    def _setup_window(self):
        r = self.root
        r.title(APP_NAME)
        r.configure(bg=BG)
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w = min(px(1180), sw - px(40))
        h = min(px(740), sh - px(90))
        r.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2 - px(20))))
        r.minsize(min(px(940), sw - 40), min(px(640), sh - 90))
        try:
            ico, png = resource_path("icon.ico"), resource_path("icon.png")
            if IS_WIN and os.path.exists(ico):
                r.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon = tk.PhotoImage(file=png)
                r.iconphoto(True, self._icon)
        except Exception:
            pass

    def _setup_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("NV.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                     bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, borderwidth=0,
                     rowheight=px(30), font=F["text"])
        st.map("NV.Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
        st.layout("NV.Treeview", [("NV.Treeview.treearea", {"sticky": "nswe"})])
        st.configure("NV.Treeview.Heading", background=CARD, foreground=MUTED, relief="flat",
                     bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD, font=F["label"],
                     padding=(px(6), px(5)))
        st.map("NV.Treeview.Heading", background=[("active", TRACK)], foreground=[("active", TEXT)])
        st.configure("Dark.Vertical.TScrollbar", background=TRACK, troughcolor=CARD,
                     bordercolor=CARD, lightcolor=TRACK, darkcolor=TRACK, arrowcolor=MUTED,
                     gripcount=0, relief="flat")
        st.map("Dark.Vertical.TScrollbar", background=[("active", BORDER)])

    # --- dựng giao diện ---
    def _build(self):
        G = px(12)
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=G, pady=G)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0)
        main.rowconfigure(1, weight=1)

        self._build_header(main).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, G))
        self._build_list(main).grid(row=1, column=0, sticky="nsew", padx=(0, G))
        self._build_settings(main).grid(row=1, column=1, rowspan=2, sticky="nsew")
        self._build_footer(main).grid(row=2, column=0, sticky="ew", padx=(0, G), pady=(G, 0))

        if TkinterDnD is not None:
            try:
                for w in (self.root, self.tree, self.empty, self.drop_box, self.list_card, self.list_card.body):
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                    w.dnd_bind("<<DropEnter>>", lambda e: self._drop_hover(True))
                    w.dnd_bind("<<DropLeave>>", lambda e: self._drop_hover(False))
            except Exception:
                traceback.print_exc()

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=BG)
        sz = px(34)
        logo = tk.Canvas(hdr, width=sz, height=sz, bg=BG, highlightthickness=0)
        draw_logo(logo, sz)
        logo.pack(side="left", padx=(0, px(12)))
        titles = tk.Frame(hdr, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text=APP_NAME, bg=BG, fg=TEXT, font=F["title"]).pack(anchor="w")
        tk.Label(titles, text="Giảm dung lượng video, xuất MP4 gọn nhẹ để gửi đi", bg=BG, fg=MUTED,
                 font=F["small"]).pack(anchor="w")

        right = tk.Frame(hdr, bg=BG)
        right.pack(side="right")
        self.sendto_var = tk.BooleanVar(value=IS_WIN and os.path.exists(sendto_path()))
        self.opt_menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT, activebackground=SELECT,
                                activeforeground=TEXT, selectcolor=TEXT, bd=0)
        self.opt_menu.add_checkbutton(label="Thêm vào chuột phải › Gửi tới › Nén Video", variable=self.sendto_var,
                                      command=self.apply_sendto, state="normal" if IS_WIN else "disabled")
        self.opt_menu.add_command(label="Khôi phục cài đặt mặc định (MP4 dưới 25 MB)", command=self.reset_settings)
        self.opt_menu.add_separator()
        self.opt_menu.add_command(label="Mở thư mục dữ liệu của app", command=lambda: open_path(data_dir()))
        self.opt_menu.add_command(label="Thông tin ffmpeg", command=self.show_ffmpeg_info)
        self.opt_btn = FlatButton(right, "Tùy chọn ▾", self.show_options, bg=BG)
        self.opt_btn.pack(side="right", padx=(px(6), 0))
        self.status = tk.Label(right, text="", bg=CARD, fg=MUTED, font=F["small"], padx=px(10), pady=px(4))
        self.status.pack(side="right", padx=(0, px(8)))
        return hdr

    def _build_list(self, parent):
        c = Card(parent, "Danh sách video", C_ACC)
        self.list_card = c
        actions = tk.Frame(c.body, bg=CARD)
        actions.pack(side="bottom", fill="x", pady=(px(10), 0))
        add = FlatButton(actions, "+  Thêm video", self.pick_files)
        add.set_active(True)
        add.pack(side="left")
        FlatButton(actions, "+  Thêm thư mục", self.pick_folder).pack(side="left", padx=(px(4), 0))
        FlatButton(actions, "Xóa hết", self.clear_all, danger=True).pack(side="right")
        FlatButton(actions, "Bỏ video đã chọn", self.remove_selected).pack(side="right", padx=(0, px(4)))
        FlatButton(actions, "Mở bản đã nén", self.open_selected_result).pack(side="right", padx=(0, px(4)))

        self.detail = tk.Label(c.body, text="", bg=blend(TRACK, CARD, 0.5), fg=MUTED, font=F["small"],
                               anchor="w", justify="left", padx=px(10), pady=px(6))
        self.detail.bind("<Configure>", lambda e: self.detail.config(wraplength=max(px(300), e.width - px(24))))

        wrap = tk.Frame(c.body, bg=CARD)
        wrap.pack(fill="both", expand=True)
        self.tree_wrap = wrap
        cols = ("name", "dur", "res", "old", "new", "save", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", style="NV.Treeview", selectmode="extended")
        heads = {"name": "TÊN FILE", "dur": "THỜI LƯỢNG", "res": "KÍCH THƯỚC", "old": "DUNG LƯỢNG",
                 "new": "SAU NÉN", "save": "GIẢM", "status": "TRẠNG THÁI"}
        for col, width, anchor, stretch in (("name", 200, "w", True), ("dur", 96, "e", False),
                                            ("res", 112, "center", False), ("old", 104, "e", False),
                                            ("new", 90, "e", False), ("save", 62, "e", False),
                                            ("status", 220, "w", True)):
            self.tree.heading(col, text=heads[col], anchor=anchor)
            self.tree.column(col, width=px(width), minwidth=px(40), anchor=anchor, stretch=stretch)
        self.tree.tag_configure("odd", background=blend(TRACK, CARD, 0.35))
        self.tree.tag_configure("even", background=CARD)
        self.tree.tag_configure("run", foreground="#ffc08a")
        self.tree.tag_configure("ok", foreground="#9be9a8")
        self.tree.tag_configure("bad", foreground="#ff9d97")
        self.tree.tag_configure("dim", foreground=MUTED)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.empty = tk.Frame(wrap, bg=CARD, highlightthickness=0)
        self.drop_box = tk.Canvas(self.empty, width=px(380), height=px(150), bg=CARD, highlightthickness=0)
        self.drop_box.pack()
        self._draw_drop_box(False)
        b = FlatButton(self.empty, "+  Chọn video", self.pick_files)
        b.set_active(True)
        b.pack(pady=(px(12), 0))

        self.fitter = Fitter(F["text"])
        self.tree.bind("<Configure>", lambda e: self._mark_dirty())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._mark_dirty())
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT, activebackground=SELECT,
                            activeforeground=TEXT, bd=0)
        self.menu.add_command(label="Mở bản đã nén", command=self.open_selected_result)
        self.menu.add_command(label="Hiện bản đã nén trong thư mục", command=self.reveal_selected_result)
        self.menu.add_command(label="Mở video gốc", command=self.open_selected_source)
        self.menu.add_separator()
        self.menu.add_command(label="Nén lại video này", command=self.recompress_selected)
        self.menu.add_command(label="Bỏ khỏi danh sách", command=self.remove_selected)
        for seq in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            self.tree.bind(seq, self._popup)
        return c

    def _draw_drop_box(self, hover):
        cv = self.drop_box
        cv.delete("all")
        w, h = int(cv["width"]), int(cv["height"])
        col = C_ACC if hover else DIM
        round_rect(cv, px(2), px(2), w - px(2), h - px(2), px(14), fill=blend(C_ACC, CARD, 0.12) if hover else CARD,
                   outline=col, dash=(6, 4), width=max(1, px(1.5)))
        cv.create_text(w / 2, h * 0.36, text="⬇", fill=col, font=(F["title"][0], px(20)))
        msg = "Kéo thả video hoặc thư mục vào đây" if TkinterDnD is not None else "Chưa có video nào"
        cv.create_text(w / 2, h * 0.62, text=msg, fill=TEXT, font=F["med"])
        sub = ("MP4, MOV, MKV, AVI…  ·  mặc định nén xuống dưới 25 MB" if TkinterDnD is not None else
               "Bấm “Chọn video” (cài tkinterdnd2 để kéo thả được)")
        cv.create_text(w / 2, h * 0.80, text=sub, fill=MUTED, font=F["small"])

    def _drop_hover(self, v):
        if not self.order:
            self._draw_drop_box(v)
        return "copy"

    def _build_settings(self, parent):
        c = Card(parent, "Cài đặt nén", C_BLUE)
        b = c.body
        W = px(300)
        self.set_wrap = W
        b.config(width=W)

        def label(text):
            tk.Label(b, text=text, bg=CARD, fg=MUTED, font=F["small"], anchor="w").pack(fill="x", pady=(px(9), px(3)))

        label("Cách nén")
        self.mode_seg = Segmented(b, MODES, "dung_luong", on_change=lambda k: self._ui_changed())
        self.mode_seg.pack(fill="x")

        # --- theo dung lượng ---
        self.target_box = tk.Frame(b, bg=CARD)
        row = tk.Frame(self.target_box, bg=CARD)
        row.pack(fill="x", pady=(px(10), 0))
        tk.Label(row, text="Mỗi video dưới", bg=CARD, fg=TEXT, font=F["text"]).pack(side="left")
        self.mb_field = Field(row, "25", width=5, justify="center")
        self.mb_field.pack(side="left", padx=(px(8), px(6)))
        tk.Label(row, text="MB", bg=CARD, fg=TEXT, font=F["text"]).pack(side="left")
        self.mb_field.entry.bind("<KeyRelease>", lambda e: self._ui_changed())
        self.mb_field.entry.bind("<FocusOut>", lambda e: self._ui_changed(normalize=True), add="+")
        chips = tk.Frame(self.target_box, bg=CARD)
        chips.pack(fill="x", pady=(px(6), 0))
        self.chip_btns = {}
        for mb in QUICK_MB:
            ch = FlatButton(chips, "%d MB" % mb, lambda m=mb: self._set_mb(m), padx=8, pady=2)
            ch.pack(side="left", padx=(0, px(4)))
            self.chip_btns[mb] = ch
        self.target_note = tk.Label(self.target_box, text="", bg=CARD, fg=DIM, font=F["small"], anchor="w",
                                    justify="left", wraplength=W)
        self.target_note.pack(fill="x", pady=(px(6), 0))

        # --- theo chất lượng ---
        self.quality_box = tk.Frame(b, bg=CARD)
        self.level_seg = Segmented(self.quality_box, LEVELS, "can_bang", on_change=lambda k: self._ui_changed())
        self.level_seg.pack(fill="x", pady=(px(10), 0))
        self.quality_note = tk.Label(self.quality_box, text="", bg=CARD, fg=DIM, font=F["small"], anchor="w",
                                     justify="left", wraplength=W)
        self.quality_note.pack(fill="x", pady=(px(6), 0))
        self.mode_anchor = tk.Frame(b, bg=CARD, height=1)
        self.mode_anchor.pack(fill="x")

        label("Chuẩn nén")
        self.codec_seg = Segmented(b, CODECS, "h264", on_change=lambda k: self._ui_changed())
        self.codec_seg.pack(fill="x")
        self.codec_note = tk.Label(b, text="", bg=CARD, fg=DIM, font=F["small"], anchor="w", justify="left",
                                   wraplength=W)
        self.codec_note.pack(fill="x", pady=(px(6), 0))

        label("Độ phân giải tối đa")
        self.res_seg = Segmented(b, RES, "goc", on_change=lambda k: self._ui_changed())
        self.res_seg.pack(fill="x")

        tk.Frame(b, bg=CARD, height=px(8)).pack(fill="x")
        self.gpu_tg = Toggle(b, "Dùng card đồ hoạ (nhanh hơn)", False, sub="Đang kiểm tra card đồ hoạ…",
                             command=lambda v: self._ui_changed(), wrap=W - px(24))
        self.gpu_tg.pack(fill="x", pady=(px(6), 0))
        self.fps_tg = Toggle(b, "Giới hạn 30 khung hình/giây", False,
                             sub="Video 60 fps nhẹ đi nhiều, xem vẫn mượt.",
                             command=lambda v: self._ui_changed(), wrap=W - px(24))
        self.fps_tg.pack(fill="x", pady=(px(8), 0))
        self.mute_tg = Toggle(b, "Bỏ âm thanh", False, command=lambda v: self._ui_changed(), wrap=W - px(24))
        self.mute_tg.pack(fill="x", pady=(px(8), 0))

        label("Lưu bản nén vào")
        self.out_seg = Segmented(b, [("canh", "Cạnh file gốc"), ("khac", "Thư mục khác…")], "canh",
                                 on_change=self._out_changed)
        self.out_seg.pack(fill="x")
        self.out_lbl = tk.Label(b, text="", bg=CARD, fg=DIM, font=F["small"], anchor="w", justify="left",
                                wraplength=W, cursor="hand2")
        self.out_lbl.pack(fill="x", pady=(px(6), 0))
        self.out_lbl.bind("<Button-1>", lambda e: self.out_seg.value == "khac" and self._pick_out_dir())
        return c

    def _build_footer(self, parent):
        foot = Card(parent, "Tiến trình", C_OK)
        b = foot.body
        b.columnconfigure(0, weight=1)
        self.bar = ProgressBar(b, height=10)
        self.bar.grid(row=0, column=0, sticky="ew", padx=(0, px(14)))
        self.foot_lbl = tk.Label(b, text="", bg=CARD, fg=MUTED, font=F["small"], anchor="w")
        self.foot_lbl.grid(row=1, column=0, sticky="w", pady=(px(6), 0))
        self.start_btn = FlatButton(b, "▶   Bắt đầu nén", self.start, font=F["big"], padx=22, pady=8)
        self.start_btn.set_active(True)
        self.start_btn.grid(row=0, column=1, rowspan=2, sticky="e")
        return foot

    # --- cài đặt <-> giao diện ---
    def _load_settings_to_ui(self):
        st = self.settings.snapshot()
        self.mode_seg.set(st["che_do"], notify=False)
        self.level_seg.set(st["muc"], notify=False)
        self.mb_field.set(ne._num(float(st["muc_tieu_mb"] or 25)))
        self.codec_seg.set(st["codec"], notify=False)
        self.res_seg.set(st["do_phan_giai"], notify=False)
        self.gpu_tg.set(st["gpu"])
        self.fps_tg.set(st["fps30"])
        self.mute_tg.set(st["bo_am_thanh"])
        self.out_dir = st["thu_muc_ra"] or ""
        self.out_seg.set("khac" if self.out_dir else "canh", notify=False)
        self._refresh_settings_ui()

    def _read_mb(self):
        txt = self.mb_field.get().replace(",", ".")
        try:
            v = float(txt)
            return v if 0 < v <= 100000 else None
        except ValueError:
            return None

    def _set_mb(self, mb):
        self.mb_field.set(str(mb))
        self.mode_seg.set("dung_luong", notify=False)
        self._ui_changed()

    def _ui_changed(self, normalize=False):
        mb = self._read_mb()
        if normalize:
            self.mb_field.set(ne._num(mb) if mb else ne._num(float(self.settings.snapshot()["muc_tieu_mb"])))
            mb = self._read_mb()
        kw = dict(che_do=self.mode_seg.value, muc=self.level_seg.value, codec=self.codec_seg.value,
                  do_phan_giai=self.res_seg.value, gpu=self.gpu_tg.value, fps30=self.fps_tg.value,
                  bo_am_thanh=self.mute_tg.value)
        if mb:
            kw["muc_tieu_mb"] = mb
        self.settings.set(**kw)
        self._refresh_settings_ui()
        self._mark_dirty()

    def _out_changed(self, key):
        if key == "khac":
            if not self._pick_out_dir():
                if not self.out_dir:
                    self.out_seg.set("canh", notify=False)
        else:
            self.out_dir = ""
            self.settings.set(thu_muc_ra="")
        self._refresh_settings_ui()

    def _pick_out_dir(self):
        start = self.out_dir if os.path.isdir(self.out_dir) else os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self.root, title="Chọn thư mục lưu bản nén", initialdir=start)
        if not path:
            return False
        self.out_dir = os.path.normpath(path)
        self.settings.set(thu_muc_ra=self.out_dir)
        self.out_seg.set("khac", notify=False)
        self._refresh_settings_ui()
        return True

    def _refresh_settings_ui(self):
        st = self.settings.snapshot()
        target = st["che_do"] == "dung_luong"
        if target:
            self.quality_box.pack_forget()
            if not self.target_box.winfo_ismapped():
                self.target_box.pack(fill="x", before=self.mode_anchor)
        else:
            self.target_box.pack_forget()
            if not self.quality_box.winfo_ismapped():
                self.quality_box.pack(fill="x", before=self.mode_anchor)
        mb = self._read_mb()
        for m, chb in self.chip_btns.items():
            chb.set_active(mb is not None and abs(mb - m) < 1e-6)
        if mb is None:
            self.target_note.config(text="Nhập một số MB lớn hơn 0.", fg="#ff9d97")
        else:
            self.target_note.config(fg=DIM, text="Luôn ra MP4 dưới %s MB. Video dài tự hạ độ phân giải cho khỏi vỡ hình; "
                                                 "MP4 đã nhỏ hơn thì giữ nguyên."
                                                 % ne._num(mb))
        self.quality_note.config(text={"cao": "Gần như không thấy khác bản gốc, file nặng hơn.",
                                       "can_bang": "Hình vẫn đẹp, file nhẹ đi nhiều. Hợp với đa số video.",
                                       "nhe": "File nhẹ nhất, hình hơi mềm khi xem kỹ."}[self.level_seg.value])
        self.codec_note.config(text="Phát được ở mọi nơi: Zalo, Facebook, Messenger." if st["codec"] == "h264"
                               else "Nhẹ hơn H.264 ~30–40%, nén lâu hơn; máy đời cũ có thể không phát được.")
        # card đồ hoạ
        if self.ffmpeg_ok is None:
            self.gpu_tg.set_sub("Đang kiểm tra card đồ hoạ…")
            self.gpu_tg.set_enabled(False)
        else:
            vendor = ne.gpu_vendor(self.encs, st["codec"])
            if not vendor:
                self.gpu_tg.set_enabled(False)
                self.gpu_tg.set_sub("Không tìm thấy card NVIDIA / Intel / AMD nén được %s trên máy này."
                                    % ("H.264" if st["codec"] == "h264" else "H.265"))
            elif target:
                self.gpu_tg.set_enabled(False)
                self.gpu_tg.set_sub("Có card %s, nhưng nén theo dung lượng dùng CPU để ra đúng số MB."
                                    % ne.GPU_NAMES[vendor])
            else:
                self.gpu_tg.set_enabled(True)
                self.gpu_tg.set_sub("Dùng card %s: nhanh gấp nhiều lần, file nặng hơn CPU một chút."
                                    % ne.GPU_NAMES[vendor])
        self.mute_tg.set_sub("Video sẽ không có tiếng." if st["bo_am_thanh"] else "")
        if st["thu_muc_ra"]:
            self.out_lbl.config(text=st["thu_muc_ra"] + "   (bấm để đổi)")
        else:
            self.out_lbl.config(text="Bản nén: <tên gốc>%s.mp4 · file gốc giữ nguyên." % ne.OUT_SUFFIX)

    def reset_settings(self):
        self.settings.set(**ne.default_settings())
        self._load_settings_to_ui()
        self._mark_dirty()

    # --- thêm / bớt video ---
    def pick_files(self):
        exts = " ".join("*" + e for e in sorted(ne.VIDEO_EXTS))
        paths = filedialog.askopenfilenames(parent=self.root, title="Chọn video cần nén",
                                            filetypes=[("Video", exts), ("Tất cả file", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def pick_folder(self):
        path = filedialog.askdirectory(parent=self.root, title="Chọn thư mục chứa video (gồm cả thư mục con)")
        if path:
            self.add_paths([path])

    def _on_drop(self, e):
        self._draw_drop_box(False)
        try:
            paths = list(self.root.tk.splitlist(e.data))
        except Exception:
            paths = [e.data]
        self.add_paths(paths)
        return "copy"

    def add_paths(self, paths):
        paths = [os.path.normpath(p) for p in paths if p]
        vids = ne.collect_videos(paths)
        known = {os.path.normcase(it["path"]) for it in self.items.values()}
        added = 0
        for v in vids:
            if os.path.normcase(v) in known:
                continue
            known.add(os.path.normcase(v))
            iid = uuid.uuid4().hex[:10]
            self.items[iid] = {"path": v, "info": None, "status": "doc", "msg": "", "frac": 0.0,
                               "speed": 0.0, "eta": None, "result": None, "retried": None}
            self.order.append(iid)
            self.worker.add_probe(iid, v)
            added += 1
        skipped = len([p for p in paths if os.path.isfile(p) and not ne.is_video(p)])
        if not vids and paths:
            messagebox.showinfo(APP_NAME, "Không thấy video nào trong những gì vừa thêm.\n\n"
                                          "App nhận các đuôi: " + ", ".join(sorted(ne.VIDEO_EXTS)), parent=self.root)
        elif skipped:
            self.flash("Bỏ qua %d file không phải video" % skipped)
        if added:
            self._mark_dirty()                 # đang nén dở thì video mới tự vào hàng chờ khi đọc xong thông tin
        return added

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            it = self.items.get(iid)
            if not it:
                continue
            if it["status"] == "nen":
                if not messagebox.askyesno(APP_NAME, "“%s” đang nén dở. Dừng và bỏ khỏi danh sách?"
                                           % os.path.basename(it["path"]), parent=self.root):
                    continue
                job = self.worker.current
                if job and self.worker.current_id == iid:
                    job.cancel()
            self.worker.remove(iid)
            self.items.pop(iid, None)
            if iid in self.order:
                self.order.remove(iid)
            if iid in self.run_ids:
                self.run_ids.remove(iid)
        self._mark_dirty()

    def clear_all(self):
        if not self.order:
            return
        if self.worker.busy():
            if not messagebox.askyesno(APP_NAME, "Đang nén. Dừng lại và xóa hết danh sách?\n\n"
                                                 "(Các bản đã nén xong vẫn giữ nguyên trên ổ đĩa.)", parent=self.root):
                return
            self.worker.stop()
        for iid in list(self.order):
            self.worker.remove(iid)
        self.items.clear()
        self.order.clear()
        self.run_ids.clear()
        self._mark_dirty()

    # --- nén ---
    def start(self):
        if self.worker.busy():
            self.stop()
            return
        if self.ffmpeg_ok is False:
            self.show_ffmpeg_missing()
            return
        if self.settings.snapshot()["che_do"] == "dung_luong" and self._read_mb() is None:
            messagebox.showerror(APP_NAME, "Hãy nhập dung lượng mục tiêu (số MB lớn hơn 0).", parent=self.root)
            return
        if not self.order:
            self.pick_files()
            return
        todo = [iid for iid in self.order if self.items[iid]["status"] in ("san_sang", "loi", "da_dung", "doc")]
        if not todo:
            if messagebox.askyesno(APP_NAME, "Tất cả video trong danh sách đã nén xong.\n\n"
                                             "Nén lại toàn bộ với cài đặt hiện tại?", parent=self.root):
                todo = [iid for iid in self.order if self.items[iid]["info"]]
            if not todo:
                return
        self.run_ids = list(todo)
        self.run_started = time.time()
        for iid in todo:
            it = self.items[iid]
            if it["status"] != "doc":
                it.update(status="cho", msg="", frac=0.0, result=None, retried=None)
            else:
                it["queued"] = True               # sẽ xếp hàng ngay khi đọc xong thông tin
        self.worker.enqueue([(iid, self.items[iid]["path"], self.items[iid]["info"]) for iid in todo
                             if self.items[iid]["status"] == "cho"])
        self._mark_dirty()

    def stop(self):
        dropped = self.worker.stop()
        for iid in dropped:
            it = self.items.get(iid)
            if it and it["status"] == "cho":
                it["status"] = "san_sang"
        for it in self.items.values():
            it.pop("queued", None)
        self._mark_dirty()

    def recompress_selected(self):
        sel = [i for i in self.tree.selection() if i in self.items and self.items[i]["info"]
               and self.items[i]["status"] not in ("nen", "cho")]
        if not sel:
            return
        if not self.worker.busy():
            self.run_ids = []
            self.run_started = time.time()
        for iid in sel:
            self.items[iid].update(status="cho", msg="", frac=0.0, result=None, retried=None)
            if iid not in self.run_ids:
                self.run_ids.append(iid)
        self.worker.enqueue([(iid, self.items[iid]["path"], self.items[iid]["info"]) for iid in sel])
        self._mark_dirty()

    # --- vòng lặp ---
    def tick(self):
        if self._quitting:
            return
        self._handle_commands()
        self._handle_events()
        if self.dirty:
            self.dirty = False
            try:
                self.render()
            except Exception:
                traceback.print_exc()
        self.root.after(200, self.tick)

    def _mark_dirty(self):
        self.dirty = True

    def _handle_commands(self):
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            if cmd[0] == "add":
                self.show_window()
                self.add_paths(cmd[1])

    def _handle_events(self):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            self.dirty = True
            kind = ev[0]
            if kind == "ffmpeg":
                self.ffmpeg_ok = bool(ev[1])
                self.encs = ev[2] or {}
                self._refresh_settings_ui()
                if not self.ffmpeg_ok:
                    for it in self.items.values():
                        it.update(status="loi", msg="chưa có ffmpeg")
                    self.root.after(300, self.show_ffmpeg_missing)
                continue
            it = self.items.get(ev[1]) if len(ev) > 1 else None
            if kind == "idle":
                self._finish_run()
                continue
            if it is None:
                continue
            if kind == "info":
                it["info"] = ev[2]
                it["status"] = "san_sang"
                if it.pop("queued", False):
                    it["status"] = "cho"
                    self.worker.enqueue([(ev[1], it["path"], it["info"])])
                elif self.worker.busy() and self.run_ids and ev[1] not in self.run_ids:
                    # thêm video trong lúc đang nén: nén luôn trong đợt này
                    it["status"] = "cho"
                    self.run_ids.append(ev[1])
                    self.worker.enqueue([(ev[1], it["path"], it["info"])])
            elif kind == "info_err":
                it.update(status="hong", msg=ev[2])
                it.pop("queued", None)
            elif kind == "start":
                it.update(status="nen", frac=0.0, speed=0.0, eta=None, started=time.time())
            elif kind == "progress":
                it.update(frac=ev[2], speed=ev[3], eta=ev[4], retried=ev[5])
            elif kind == "done":
                res = ev[2]
                it.update(status="xong", result=res, frac=1.0)
            elif kind == "cancelled":
                it.update(status="da_dung", frac=0.0, msg="đã dừng")
            elif kind == "error":
                it.update(status="loi", msg=ev[2], frac=0.0)

    def _finish_run(self):
        ids = [i for i in self.run_ids if i in self.items]
        done = [self.items[i] for i in ids if self.items[i]["status"] == "xong"]
        if not ids or not done and not any(self.items[i]["status"] == "loi" for i in ids):
            return
        saved = sum(max(0, d["result"]["old"] - d["result"]["new"]) for d in done if d["result"]["kept"])
        n_bad = sum(1 for i in ids if self.items[i]["status"] == "loi")
        msg = "Xong %d video" % len(done)
        if saved:
            msg += ", nhẹ đi %s" % ne.fmt_size(saved)
        if n_bad:
            msg += " · %d lỗi" % n_bad
        if self.run_started:
            msg += " · %s" % fmt_seconds(time.time() - self.run_started)
        self.last_summary = msg
        try:
            self.root.bell()
            if self.root.state() == "iconic" or self.root.focus_displayof() is None:
                self._flash_taskbar()
        except Exception:
            pass

    def _flash_taskbar(self):
        if not IS_WIN:
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def flash(self, text):
        self.flash_msg = (text, time.time() + 4)
        self._mark_dirty()

    def _listen(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            try:
                conn.settimeout(3)
                data = b""
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if data.startswith(b"NENVIDEO\n"):
                    paths = [p for p in data[9:].decode("utf-8", errors="replace").split("\n") if p]
                    self.commands.put(("add", paths))
            except OSError:
                pass
            finally:
                conn.close()

    # --- vẽ lại ---
    def _row(self, it, st):
        info = it["info"]
        name = os.path.basename(it["path"])
        dur = ne.fmt_duration(info["duration"]) if info else "—"
        res = "%d×%d" % (info["width"], info["height"]) if info and info["width"] else "—"
        old = ne.fmt_size(info["size"]) if info else ne.fmt_size(_safe_size(it["path"]))
        new, save, tags = "", "", []
        s = it["status"]
        if s == "doc":
            status, tags = "Đang đọc thông tin…", ["dim"]
        elif s == "hong":
            status, tags = "✗  " + it["msg"], ["bad"]
        elif s == "san_sang":
            status = "Sẵn sàng"
            tags = ["dim"]
            if info:
                if st["che_do"] == "dung_luong" and info["size"] <= st["muc_tieu_mb"] * ne.MB \
                        and it["path"].lower().endswith(".mp4"):
                    status = "Đã dưới %s MB, sẽ giữ nguyên" % ne._num(st["muc_tieu_mb"])
                else:
                    est = ne.estimate_ratio(info, st)
                    new = "~" + ne.fmt_size(est)
        elif s == "cho":
            status, tags = "⏳  Đang chờ lượt…", ["run"]
        elif s == "nen":
            frac = it["frac"]
            status = "%s  %d%%" % (text_bar(frac), int(frac * 100))
            if it.get("retried"):
                status += "  ·  nén lại cho vừa"
            if it["speed"]:
                status += "  ·  %.1fx" % it["speed"]
            if it["eta"]:
                status += "  ·  còn %s" % fmt_eta(it["eta"])
            tags = ["run"]
        elif s == "xong":
            r = it["result"]
            if r.get("skipped"):
                status, tags = "✓  Đã dưới %s MB, giữ nguyên" % ne._num(st["muc_tieu_mb"]), ["dim"]
            elif not r["kept"]:
                status, tags = "✓  Đã gọn sẵn, nén thêm không nhẹ hơn: giữ bản gốc", ["dim"]
            else:
                new = ne.fmt_size(r["new"])
                if r["old"]:
                    save = "-%d%%" % round(100 * (1 - r["new"] / float(r["old"]))) if r["new"] < r["old"] else "+%d%%" % \
                        round(100 * (r["new"] / float(r["old"]) - 1))
                status = "✓  Xong · %s" % fmt_seconds(r["seconds"])
                p = r.get("plan") or {}
                if p.get("auto_res") and p.get("target_short"):
                    status += " · tự hạ về %dp" % p["target_short"]
                tags = ["ok"]
        elif s == "da_dung":
            status, tags = "■  Đã dừng", ["dim"]
        else:
            status, tags = "✗  " + it["msg"], ["bad"]
        return (name, dur, res, old, new, save, status), tags

    def render(self):
        st = self.settings.snapshot()
        pad = px(16)
        existing = set(self.tree.get_children())
        for iid in existing - set(self.order):
            self.tree.delete(iid)
        for idx, iid in enumerate(self.order):
            it = self.items[iid]
            vals, tags = self._row(it, st)
            vals = tuple(self.fitter.fit(str(v), int(self.tree.column(c, "width")) - pad)
                         for v, c in zip(vals, ("name", "dur", "res", "old", "new", "save", "status")))
            tags = tuple(["odd" if idx % 2 else "even"] + tags)
            if self.tree.exists(iid):
                if self.tree.item(iid, "values") != vals:
                    self.tree.item(iid, values=vals)
                if tuple(self.tree.item(iid, "tags")) != tags:
                    self.tree.item(iid, tags=tags)
            else:
                self.tree.insert("", "end", iid=iid, values=vals, tags=tags)
        if self.order:
            self.empty.place_forget()
        else:
            self._draw_drop_box(False)
            self.empty.place(relx=0.5, rely=0.45, anchor="center")

        # chi tiết video đang chọn
        sel = self.tree.selection()
        detail = ""
        if len(sel) == 1 and sel[0] in self.items:
            it = self.items[sel[0]]
            detail = it["path"]
            info = it["info"]
            if info:
                detail += "\n%s · %s · %d×%d · %.0f fps · %s · %s" % (
                    ne.fmt_size(info["size"]), ne.fmt_duration(info["duration"]), info["width"], info["height"],
                    info["fps"], info["vcodec"].upper(), "có tiếng" if info["has_audio"] else "không có tiếng")
            r = it.get("result")
            if r and r.get("dst"):
                detail += "\n→ " + r["dst"]
            elif it["status"] in ("loi", "hong") and it["msg"]:
                detail += "\nLỗi: " + it["msg"]
        if self.detail.cget("text") != detail:
            self.detail.config(text=detail)
        if detail and not self.detail.winfo_ismapped():
            self.detail.pack(side="bottom", fill="x", pady=(px(8), 0), before=self.tree_wrap)
        elif not detail and self.detail.winfo_ismapped():
            self.detail.pack_forget()

        n = len(self.order)
        total = sum(it["info"]["size"] for it in self.items.values() if it["info"])
        self.list_card.note.config(text="%d video · %s" % (n, ne.fmt_size(total)) if n else "")

        # tiến trình chung
        busy = self.worker.busy() or any(self.items[i].get("queued") for i in self.run_ids if i in self.items)
        ids = [i for i in self.run_ids if i in self.items]
        flash = getattr(self, "flash_msg", None)
        if busy and ids:
            weights = [max(1.0, (self.items[i]["info"] or {}).get("duration", 1.0)) for i in ids]
            done_w = 0.0
            for i, w in zip(ids, weights):
                s = self.items[i]["status"]
                if s in ("xong", "loi", "da_dung", "hong"):
                    done_w += w
                elif s == "nen":
                    done_w += w * self.items[i]["frac"]
            frac = done_w / sum(weights)
            self.bar.set(frac)
            n_done = sum(1 for i in ids if self.items[i]["status"] in ("xong", "loi", "hong"))
            cur = next((self.items[i] for i in ids if self.items[i]["status"] == "nen"), None)
            txt = "Đang nén %d/%d" % (min(n_done + 1, len(ids)), len(ids))
            if cur:
                txt += ":  " + shorten(os.path.basename(cur["path"]), 60)
            el = time.time() - (self.run_started or time.time())
            if frac > 0.02:
                txt += "   ·   còn khoảng %s" % fmt_eta(el / frac - el)
            txt += "   ·   thay đổi cài đặt sẽ áp dụng cho video chưa bắt đầu"
            self.foot_lbl.config(text=txt, fg=MUTED)
            self.start_btn.config(text="■   Dừng nén")
            self.start_btn.accent = C_BAD
            self.start_btn.set_active(True)
            self.status.config(text="⟳  Đang nén  %d%%" % int(frac * 100), fg="#ffd8b8", bg=blend(C_ACC, BG, 0.28))
            self.root.title("%d%% — %s" % (int(frac * 100), APP_NAME))
        else:
            self.start_btn.config(text="▶   Bắt đầu nén")
            self.start_btn.accent = C_ACC
            self.start_btn.set_active(True)
            self.root.title(APP_NAME)
            summary = getattr(self, "last_summary", "")
            if ids and summary:
                self.bar.set(1.0, C_OK)
                self.foot_lbl.config(text="✓  " + summary, fg=C_OK)
            else:
                self.bar.set(0.0)
                ready = sum(1 for it in self.items.values() if it["status"] in ("san_sang", "loi", "da_dung"))
                if st["che_do"] == "dung_luong":
                    how = "mỗi video thành MP4 dưới %s MB" % ne._num(st["muc_tieu_mb"])
                else:
                    how = "theo chất lượng “%s”" % dict(LEVELS)[st["muc"]]
                self.foot_lbl.config(text=("%d video sẵn sàng — bấm “Bắt đầu nén” để nén %s." % (ready, how))
                                     if ready else "Thêm video để bắt đầu. Bản nén sẽ là %s." % how, fg=MUTED)
            if self.ffmpeg_ok is False:
                self.status.config(text="●  Thiếu ffmpeg", fg="#ffd7d5", bg=blend(C_BAD, BG, 0.32))
            elif self.ffmpeg_ok is None:
                self.status.config(text="●  Đang khởi động…", fg=MUTED, bg=CARD)
            else:
                vendor = ne.gpu_vendor(self.encs, "h264")
                self.status.config(text="●  Sẵn sàng" + ("  ·  card %s" % ne.GPU_NAMES[vendor] if vendor else ""),
                                   fg=C_OK, bg=blend(C_OK, BG, 0.14))
        if flash and time.time() < flash[1]:
            self.foot_lbl.config(text=flash[0], fg="#e3b341")
            self.root.after(4100, self._mark_dirty)
        elif flash:
            self.flash_msg = None
        if busy and getattr(self, "last_summary", None):
            self.last_summary = ""

    # --- mở file ---
    def _sel_item(self):
        sel = self.tree.selection()
        return self.items.get(sel[0]) if sel else None

    def _on_double(self, e):
        iid = self.tree.identify_row(e.y)
        it = self.items.get(iid)
        if not it:
            return
        r = it.get("result")
        target = r["dst"] if r and r.get("dst") and os.path.exists(r["dst"]) else it["path"]
        try:
            open_path(target)
        except Exception as ex:
            messagebox.showerror(APP_NAME, "Không mở được:\n%s" % ex, parent=self.root)

    def open_selected_result(self):
        it = self._sel_item()
        if not it:
            return messagebox.showinfo(APP_NAME, "Hãy chọn một video trong danh sách trước.", parent=self.root)
        r = it.get("result")
        if not r or not r.get("dst") or not os.path.exists(r["dst"]):
            return messagebox.showinfo(APP_NAME, "Video này chưa có bản nén.", parent=self.root)
        open_path(r["dst"])

    def reveal_selected_result(self):
        it = self._sel_item()
        if not it:
            return
        r = it.get("result")
        reveal(r["dst"] if r and r.get("dst") and os.path.exists(r["dst"]) else it["path"])

    def open_selected_source(self):
        it = self._sel_item()
        if it and os.path.exists(it["path"]):
            open_path(it["path"])

    def _popup(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(e.x_root, e.y_root)
            self.menu.grab_release()

    # --- tùy chọn ---
    def show_options(self):
        b = self.opt_btn
        self.sendto_var.set(IS_WIN and os.path.exists(sendto_path()))
        self.opt_menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + px(2))
        self.opt_menu.grab_release()

    def apply_sendto(self):
        want = self.sendto_var.get()
        try:
            set_sendto(want)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Không thay đổi được menu Gửi tới:\n%s" % e, parent=self.root)
        self.sendto_var.set(os.path.exists(sendto_path()))
        if want and self.sendto_var.get():
            messagebox.showinfo(APP_NAME, "Đã thêm. Từ giờ chọn video trong thư mục → chuột phải → "
                                          "Gửi tới (Send to) → Nén Video.\n\n(Windows 11: bấm “Hiện thêm tùy "
                                          "chọn” hoặc giữ Shift khi chuột phải.)", parent=self.root)

    def show_ffmpeg_missing(self):
        messagebox.showerror(APP_NAME, "Không tìm thấy ffmpeg, công cụ nén video mà app cần.\n\n"
                                       "Cách sửa (chọn một):\n"
                                       "  • Chạy lại run.bat — nó tự cài gói imageio-ffmpeg có sẵn ffmpeg.\n"
                                       "  • Hoặc mở cmd và gõ:  pip install imageio-ffmpeg\n"
                                       "  • Hoặc chép ffmpeg.exe vào cùng thư mục với app.", parent=self.root)

    def show_ffmpeg_info(self):
        if not self.ffmpeg_ok:
            return self.show_ffmpeg_missing()
        gpu = [n for n, ok in self.encs.items() if ok and ("nvenc" in n or "qsv" in n or "amf" in n)]
        messagebox.showinfo(APP_NAME, "ffmpeg: %s\n\nH.264: %s   ·   H.265: %s\nCard đồ hoạ: %s" % (
            self.worker.ffmpeg, "có" if self.encs.get("libx264") else "không",
            "có" if self.encs.get("libx265") else "không", ", ".join(gpu) or "không có"), parent=self.root)

    # --- hiện / thoát ---
    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()

    def on_close(self):
        if self.worker.busy() and not messagebox.askyesno(
                APP_NAME, "Đang nén dở. Thoát bây giờ thì video đang nén sẽ bị bỏ (file gốc không sao, "
                          "các bản đã nén xong vẫn còn).\n\nThoát?", parent=self.root):
            return
        self._quitting = True
        self.worker.shutdown()
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
        self.root.after(300, self.root.destroy)        # cho ffmpeg kịp dừng và xoá file dở


def _safe_size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


def main():
    if IS_WIN:
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NenVideo.App")
        except Exception:
            pass
    log_path = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp", "nenvideo_error.log")

    def log_error(*exc):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[%s]\n" % dt.datetime.now())
                traceback.print_exception(*exc, file=f)
        except Exception:
            pass
        traceback.print_exception(*exc)

    paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("--")]
    server = acquire_single_instance()
    if server is None and send_to_existing(paths):
        return                                   # đã có cửa sổ đang mở: chuyển file sang đó

    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    root.report_callback_exception = log_error
    try:
        App(root, initial_paths=paths, server=server)
    except Exception:
        log_error(*sys.exc_info())
        messagebox.showerror(APP_NAME, "Không khởi động được Nén Video.\n\n%s\n\nChi tiết lỗi: %s"
                             % (traceback.format_exc(limit=2), log_path))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
