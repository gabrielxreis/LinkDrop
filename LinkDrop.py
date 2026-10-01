#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinkDrop for DaVinci Resolve, by @gabrielxreis_

Paste a link (YouTube, Instagram, TikTok, X, Vimeo, SoundCloud, Spotify...)
and LinkDrop downloads the video or just the audio with yt-dlp, imports it
into the Media Pool ("Downloads" bin) and drops it on your timeline.

Open from: Workspace > Scripts > Utility > LinkDrop
Works on macOS and Windows. Install with the installer from:
https://github.com/gabrielxreis/LinkDrop
"""

import os
import re
import sys
import json
import time
import html
import base64
import shutil
import subprocess

APP_TITLE = "LinkDrop"
VERSION = "2.3.0"
REPO = "gabrielxreis/LinkDrop"
RAW_URL = "https://raw.githubusercontent.com/%s/main/LinkDrop.py" % REPO
INSTAGRAM_URL = "https://instagram.com/gabrielxreis_"

IS_WIN = sys.platform.startswith("win")
HOME = os.path.expanduser("~")
if IS_WIN:
    APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
    SCRIPT_DIR = os.path.join(APPDATA, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")
    DATA_DIR = os.path.join(APPDATA, "LinkDrop")
    DEFAULT_FOLDER = os.path.join(HOME, "Videos", "Resolve Downloads")
    API_MODULES = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design",
                               "DaVinci Resolve", "Support", "Developer", "Scripting", "Modules")
    EXTRA_PATHS = [os.path.join(DATA_DIR, "bin")]
else:
    SCRIPT_DIR = os.path.join(HOME, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                              "Fusion", "Scripts", "Utility")
    DATA_DIR = os.path.join(HOME, "Library", "Application Support", "LinkDrop")
    DEFAULT_FOLDER = os.path.join(HOME, "Movies", "Resolve Downloads")
    API_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"
    EXTRA_PATHS = [os.path.join(DATA_DIR, "bin"), "/opt/homebrew/bin", "/usr/local/bin",
                   os.path.join(HOME, ".local", "bin"), os.path.join(HOME, ".deno", "bin"), "/usr/bin", "/bin"]
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "LinkDrop.py")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
ICON_DIR = os.path.join(DATA_DIR, "icons")
LOG_PATH = os.path.join(DATA_DIR, "job.log")
URL_RE = re.compile(r"https?://\S+", re.I)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

QUALITIES = ["Best", "4K", "1080p", "720p"]
QUALITY_H = [None, 2160, 1080, 720]
TARGETS = ["Playhead", "End of timeline", "New timeline", "Media Pool only"]
RESOLVE_OK_VCODECS = {"h264", "hevc", "prores", "dnxhd", "mjpeg", "mpeg2video", "mpeg4"}
RESOLVE_OK_ACODECS = {"aac", "pcm_s16le", "pcm_s24le", "pcm_f32le", "mp3", "alac"}

# (domains, display name, icon, kind)  kind: video | music | match (looked up on YouTube)
PLATFORMS = [
    (("music.youtube.com",), "YouTube Music", "youtubemusic", "music"),
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube", "youtube", "video"),
    (("instagram.com",), "Instagram", "instagram", "video"),
    (("tiktok.com",), "TikTok", "tiktok", "video"),
    (("twitter.com", "x.com"), "X", "x", "video"),
    (("facebook.com", "fb.watch", "fb.com"), "Facebook", "facebook", "video"),
    (("vimeo.com",), "Vimeo", "vimeo", "video"),
    (("soundcloud.com",), "SoundCloud", "soundcloud", "music"),
    (("twitch.tv",), "Twitch", "twitch", "video"),
    (("spotify.com",), "Spotify", "spotify", "match"),
    (("music.apple.com",), "Apple Music", "applemusic", "match"),
    (("deezer.com",), "Deezer", "deezer", "match"),
]
SHOWCASE = ["youtube", "instagram", "tiktok", "x", "facebook", "vimeo",
            "spotify", "applemusic", "youtubemusic", "soundcloud", "deezer"]
BLOCKED = [(("netflix.com",), "Netflix"), (("primevideo.com", "amazon.com"), "Prime Video"),
           (("disneyplus.com",), "Disney+"), (("max.com", "hbomax.com"), "Max"), (("tidal.com",), "Tidal")]


# ---------------------------------------------------------------- Resolve ---

def get_resolve():
    r = globals().get("resolve") or globals().get("app")
    if r and hasattr(r, "GetProjectManager"):
        return r
    try:
        r = bmd.scriptapp("Resolve")  # noqa: F821 (exists inside Resolve)
        if r:
            return r
    except Exception:
        pass
    if API_MODULES not in sys.path:
        sys.path.append(API_MODULES)
    import DaVinciResolveScript as dvr
    return dvr.scriptapp("Resolve")


resolve = get_resolve()
if not resolve:
    print("[%s] Could not connect to DaVinci Resolve." % APP_TITLE)
    sys.exit(1)
fusion = resolve.Fusion()
ui = fusion.UIManager
disp = bmd.UIDispatcher(ui) if "bmd" in globals() else None  # noqa: F821
if disp is None:
    import DaVinciResolveScript as dvr  # running outside Resolve
    disp = dvr.UIDispatcher(ui)


# --------------------------------------------------------------- helpers ---

def make_env():
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join([p for p in EXTRA_PATHS if p not in parts] + parts)
    # Resolve runs scripts with an ASCII locale; force UTF-8 so titles like "It\u2019s" don't crash
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if not IS_WIN:
        env["LANG"] = env["LC_ALL"] = "en_US.UTF-8"
    return env


ENV = make_env()
NO_WINDOW = 0x08000000 if IS_WIN else 0


def find_tool(name):
    return shutil.which(name, path=ENV["PATH"])


def find_python3():
    """A python.org / Homebrew Python 3.9+ to run the yt-dlp zipapp (never Apple's /usr/bin stub)."""
    cands = []
    fw = "/Library/Frameworks/Python.framework/Versions"
    if os.path.isdir(fw):
        for v in sorted(os.listdir(fw), key=lambda x: [int(n) for n in re.findall(r"\d+", x)] or [0], reverse=True):
            cands.append(os.path.join(fw, v, "bin", "python3"))
    cands += ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def ytdlp_cmd():
    """Command prefix for yt-dlp. On macOS LinkDrop ships the zipapp and runs it with Python 3."""
    if not IS_WIN:
        zipapp = os.path.join(DATA_DIR, "bin", "yt-dlp.pyz")
        py = find_python3()
        if os.path.exists(zipapp) and py:
            return [py, zipapp]
    exe = find_tool("yt-dlp")
    return [exe] if exe else None


def run(cmd, timeout=30):
    """Run a short command and return its stdout as UTF-8 text ('' on failure)."""
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           env=ENV, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    except Exception:
        return ""


def open_url(url):
    try:
        if IS_WIN:
            os.startfile(url)
        else:
            subprocess.Popen(["open", url])
    except Exception:
        pass


def reveal(path):
    try:
        if IS_WIN:
            subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path), creationflags=NO_WINDOW)
        else:
            subprocess.Popen(["open", "-R", path])
    except Exception:
        pass


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def clipboard_url():
    if IS_WIN:
        txt = run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], timeout=5)
    else:
        txt = run(["pbpaste"], timeout=3)
    m = URL_RE.search(txt or "")
    return m.group(0).strip() if m else ""


def url_host(url):
    m = re.match(r"^[a-z]+://([^/?#:]+)", (url or "").strip().lower())
    return m.group(1) if m else ""


def on_domain(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def platform_of(url):
    host = url_host(url)
    for domains, name, icon, kind in PLATFORMS:
        if on_domain(host, domains):
            return name, icon, kind
    return "Web", None, "video"


def blocked_site(url):
    host = url_host(url)
    for domains, name in BLOCKED:
        if on_domain(host, domains):
            return name
    return None


def icon_path(name):
    """Write the embedded PNG icon to disk once and return its path."""
    data = ICONS.get(name)
    if not data:
        return None
    path = os.path.join(ICON_DIR, name + ".png")
    raw = base64.b64decode("".join(data))
    try:
        if not os.path.exists(path) or os.path.getsize(path) != len(raw):
            os.makedirs(ICON_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        return path
    except Exception:
        return None


def img_tag(name, size):
    path = icon_path(name)
    if not path:
        return ""
    return '<img src="%s" width="%d" height="%d">' % (path.replace("\\", "/"), size, size)


def ffprobe_codecs(path):
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None, None
    out = run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", path], 60)
    try:
        streams = json.loads(out).get("streams", [])
    except Exception:
        return None, None
    v = next((s["codec_name"] for s in streams if s.get("codec_type") == "video"), None)
    a = next((s["codec_name"] for s in streams if s.get("codec_type") == "audio"), None)
    return v, a


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def fetch_latest():
    """Fetch LinkDrop.py from GitHub. Returns (version, code) or (None, None)."""
    code = run(["curl", "-fsSL", "--max-time", "5", RAW_URL + "?t=%d" % int(time.time())], timeout=8)
    m = re.search(r'^VERSION = "([^"]+)"', code, re.M)
    if not m:
        return None, None
    try:
        compile(code, "LinkDrop.py", "exec")  # never install a broken file
    except Exception:
        return None, None
    return m.group(1), code


def install_update(code):
    path = globals().get("__file__") or SCRIPT_PATH
    if not os.path.exists(path):
        path = SCRIPT_PATH
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    os.replace(tmp, path)
    return path


def tc_to_frames(tc, fps):
    fps_i = int(round(fps))
    parts = re.split(r"[:;.]", tc or "")
    if len(parts) != 4:
        return 0
    h, m, s, f = [int(p) for p in parts]
    return ((h * 3600 + m * 60 + s) * fps_i) + f


def song_from_text(platform, text):
    """Pull 'Artist - Title' out of a Spotify embed, Apple Music page or Deezer API reply."""
    try:
        if platform == "Spotify":
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
            e = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
            artists = ", ".join(a["name"] for a in e.get("artists", [])[:2])
            return ("%s - %s" % (artists, e["name"])).strip(" -")
        if platform == "Apple Music":
            m = re.search(r'property="og:title"[^>]*content="([^"]+)"', text) or \
                re.search(r'content="([^"]+)"[^>]*property="og:title"', text)
            t = html.unescape(m.group(1))
            t = re.sub(r"\s+on Apple Music$", "", t)
            m2 = re.match(r"(.*) by (.*)$", t)
            return "%s - %s" % (m2.group(2), m2.group(1)) if m2 else t
        if platform == "Deezer":
            d = json.loads(text)
            return "%s - %s" % (d["artist"]["name"], d["title"])
    except Exception:
        pass
    return None


# -------------------------------------------------------------- download ---

THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
AUDIO_FORMATS = [("mp3", "MP3", "Compatible, small files"),
                 ("wav", "WAV", "Lossless, for editing"),
                 ("aac", "AAC", "High quality, efficient")]
AUDIO_QUALITIES = {"mp3": [("best", "Best"), ("320", "320 kbps"), ("128", "128 kbps")],
                   "aac": [("best", "Best"), ("256", "256 kbps"), ("128", "128 kbps")],
                   "wav": [("best", "Lossless")]}
AUDIO_KBPS = {("mp3", "best"): 245, ("mp3", "320"): 320, ("mp3", "128"): 128,
              ("aac", "best"): 256, ("aac", "256"): 256, ("aac", "128"): 128, ("wav", "best"): 1411}
VIDEO_RES = [(2160, "4K"), (1440, "1440p"), (1080, "1080p"), (720, "720p")]
VIDEO_CODECS = [("h264", "H.264", "MP4, small and compatible"),
                ("prores", "ProRes 422", "MOV, smooth editing, larger")]
PRORES_MBPS = {2160: 590, 1440: 262, 1080: 147, 720: 65, 0: 45}


class Proc(object):
    """Runs child processes one step at a time without threads (threads starve inside
    Resolve). Output goes to log files; the window's timer calls poll()."""

    _seq = [0]

    def __init__(self):
        Proc._seq[0] += 1
        self.tag = "%d-%d" % (os.getpid(), Proc._seq[0])
        self.proc = None
        self.on_exit = None
        self.out_path = os.path.join(DATA_DIR, "logs", "%s.out" % self.tag)
        self.err_path = os.path.join(DATA_DIR, "logs", "%s.err" % self.tag)
        self.offset = 0
        self.buf = ""
        self.done = False
        self.error = None
        self.cancelled = False
        self.files = []

    def _spawn(self, cmd, on_exit, merge=True):
        os.makedirs(os.path.dirname(self.out_path), exist_ok=True)
        self._close()
        out = open(self.out_path, "wb")
        err = subprocess.STDOUT if merge else open(self.err_path, "wb")
        self.files = [out] + ([err] if not merge else [])
        self.offset, self.buf, self.on_exit = 0, "", on_exit
        self.proc = subprocess.Popen(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                     env=ENV, creationflags=NO_WINDOW)

    def _close(self):
        for f in self.files:
            try:
                f.close()
            except Exception:
                pass
        self.files = []

    def _new_lines(self):
        try:
            with open(self.out_path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except Exception:
            return []
        self.offset += len(chunk)
        self.buf += chunk.decode("utf-8", "replace")
        lines = re.split(r"[\r\n]", self.buf)
        self.buf = lines.pop()
        return [l.strip() for l in lines if l.strip()]

    def _read(self, path):
        try:
            with open(path, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _fail(self, msg):
        self.error = msg
        self.done = True
        self._cleanup()

    def _cleanup(self):
        self._close()
        for p in (self.out_path, self.err_path):
            try:
                os.remove(p)
            except Exception:
                pass

    def on_line(self, line):
        pass

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def poll(self):
        if self.done or not self.proc:
            return
        for line in self._new_lines():
            self.on_line(line)
        code = self.proc.poll()
        if code is None:
            return
        self._close()
        for line in self._new_lines() + ([self.buf] if self.buf.strip() else []):
            self.on_line(line)
        self.buf = ""
        self.proc = None
        if self.cancelled:
            return self._fail("Canceled.")
        self.on_exit(code)


def human_size(n):
    if not n:
        return ""
    mb = n / 1048576.0
    if mb >= 1024:
        return "%.1f GB" % (mb / 1024)
    if mb >= 10:
        return "%d MB" % round(mb)
    if mb >= 1:
        return "%.1f MB" % mb
    return "%d KB" % max(1, round(n / 1024.0))


def human_duration(sec):
    if not sec:
        return ""
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


class Analyzer(Proc):
    """Reads a link's real metadata with yt-dlp -J (title, duration, thumbnail, available
    resolutions, file sizes) before anything is downloaded."""

    def __init__(self, url):
        Proc.__init__(self)
        self.url = url
        self.platform, self.icon, self.kind = platform_of(url)
        self.state = "queued"
        self.info = None
        self.matched = None
        self.thumb = None
        self.tail = []

    def start(self):
        self.state = "analyzing"
        blocked = blocked_site(self.url)
        if blocked:
            self.state = "failed"
            return self._fail("%s is DRM-protected and can't be downloaded." % blocked)
        if not ytdlp_cmd():
            self.state = "failed"
            return self._fail("yt-dlp not found. Run the LinkDrop installer again.")
        if self.kind == "match":
            return self._lookup()
        self._probe(self.url)

    def on_line(self, line):
        self.tail = (self.tail + [line])[-40:]

    def _lookup(self):
        url = self.url
        if self.platform == "Spotify":
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if not m:
                self.state = "failed"
                return self._fail("Use a link to a single Spotify song (not a playlist, album or podcast).")
            url = "https://open.spotify.com/embed/track/" + m.group(1)
        elif self.platform == "Deezer":
            m = re.search(r"track/(\d+)", url)
            if not m:
                self.state = "failed"
                return self._fail("Use a link to a single Deezer song (deezer.com/track/...).")
            url = "https://api.deezer.com/track/" + m.group(1)
        self.tail = []
        self._spawn(["curl", "-fsSL", "--max-time", "15", "-A", UA, url], self._lookup_done)

    def _lookup_done(self, code):
        song = song_from_text(self.platform, "\n".join(self.tail)) if code == 0 else None
        if not song:
            self.state = "failed"
            return self._fail("Couldn't read the song from this %s link." % self.platform)
        self.matched = song
        self._probe("ytsearch1:%s audio" % song)

    def _probe(self, target):
        self.tail = []
        self._spawn(ytdlp_cmd() + ["-J", "--no-playlist", "--no-warnings", "--encoding", "utf-8", target],
                    self._probe_done, merge=False)

    def _probe_done(self, code):
        text = self._read(self.out_path)
        info = None
        try:
            info = json.loads(text)
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                info = entries[0] if entries else None
        except Exception:
            info = None
        if code != 0 or not info:
            errs = [l for l in self._read(self.err_path).splitlines() if "ERROR" in l]
            self.state = "failed"
            return self._fail(clean_error(errs[-1]) if errs else "This link couldn't be read.")
        self.info = info
        self._thumbnail()

    def _thumbnail(self):
        thumb = self.info.get("thumbnail")
        if not thumb:
            for t in reversed(self.info.get("thumbnails") or []):
                if t.get("url"):
                    thumb = t["url"]
                    break
        ffmpeg = find_tool("ffmpeg")
        if not thumb or not ffmpeg:
            return self._ready()
        os.makedirs(THUMB_DIR, exist_ok=True)
        self.thumb = os.path.join(THUMB_DIR, "%s.png" % re.sub(r"[^A-Za-z0-9_-]", "_", str(self.info.get("id", self.tag))))
        if os.path.exists(self.thumb):
            return self._ready()
        self.thumb_src = self.thumb + ".src"
        self._spawn(["curl", "-fsSL", "--max-time", "15", "-A", UA, "-o", self.thumb_src, thumb], self._thumb_fetched)

    def _thumb_fetched(self, code):
        if code != 0:
            return self._thumb_done(1)
        # 16:9 PNG that Qt can show (webp/avif thumbnails aren't supported by Qt's image plugins)
        self._spawn([find_tool("ffmpeg"), "-y", "-v", "error", "-i", self.thumb_src, "-frames:v", "1", "-vf",
                     "scale=128:72:force_original_aspect_ratio=increase,crop=128:72", self.thumb], self._thumb_done)

    def _thumb_done(self, code):
        try:
            os.remove(self.thumb_src)
        except Exception:
            pass
        if code != 0 or not os.path.exists(self.thumb or ""):
            self.thumb = None
        self._ready()

    def _ready(self):
        self.state = "ready"
        self.done = True
        self._cleanup()

    # ---- facts used by the format and review steps ----
    def title(self):
        if self.info and self.info.get("title"):
            return self.info["title"]
        return self.matched or self.url

    def duration(self):
        return (self.info or {}).get("duration") or 0

    def source_url(self):
        info = self.info or {}
        return info.get("webpage_url") or info.get("original_url") or self.url

    def resolutions(self):
        """Short-side resolutions the source really offers (a 1080x1920 reel counts as 1080p)."""
        out = set()
        for f in (self.info or {}).get("formats") or []:
            if f.get("vcodec") in (None, "none") or not f.get("height"):
                continue
            short = min(f.get("height") or 0, f.get("width") or f.get("height") or 0)
            for res, _ in VIDEO_RES:
                if short >= res * 0.95:
                    out.add(res)
                    break
        return out

    def has_video(self):
        return self.kind == "video" and bool(self.resolutions() or (self.info or {}).get("vcodec") not in (None, "none"))

    def estimate(self, audio, opts):
        """Best estimate of the final file size from yt-dlp's own format data."""
        dur = self.duration()
        formats = (self.info or {}).get("formats") or []

        def size(f):
            return f.get("filesize") or f.get("filesize_approx") or ((f.get("tbr") or 0) * 125 * dur)

        if audio:
            kbps = AUDIO_KBPS.get((opts["audio_fmt"], opts["audio_q"]), 192)
            return int(dur * kbps * 125) if dur else 0
        cap = opts.get("max_h") or 99999
        videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
        ok = [f for f in videos if min(f["height"], f.get("width") or f["height"]) <= cap * 1.05] or videos
        if not ok:
            return 0
        best = sorted(ok, key=lambda f: (min(f["height"], f.get("width") or f["height"]),
                                          "avc1" in (f.get("vcodec") or ""), f.get("tbr") or 0))[-1]
        if opts.get("vcodec") == "prores":
            short = min(best["height"], best.get("width") or best["height"])
            rate = next((PRORES_MBPS[r] for r in (2160, 1440, 1080, 720) if short >= r * 0.95), PRORES_MBPS[0])
            return int(dur * rate * 125000)
        audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        a = max([size(f) for f in audios] or [0])
        return int(size(best) + (a if best.get("acodec") in (None, "none") else 0))


class Job(Proc):
    """Downloads one item: (song lookup) -> yt-dlp -> optional conversion. All progress is real:
    yt-dlp's own percentages and ffmpeg's out_time against the item's duration."""

    def __init__(self, url, folder, opts):
        Proc.__init__(self)
        self.url, self.folder, self.opts = url, folder, opts
        self.platform, _, self.kind = platform_of(opts.get("origin") or url)
        self.audio_only = opts.get("audio", False) or self.kind in ("music", "match")
        self.phase = "queued"
        self.status = "Queued"
        self.percent = 0.0
        self.path = None
        self.matched = opts.get("matched")
        self.part = 0
        self.tail = []
        self.ffmpeg = None
        self.duration = opts.get("duration") or 0

    def start(self):
        if not ytdlp_cmd():
            return self._fail("yt-dlp not found. Run the LinkDrop installer again.")
        self.ffmpeg = find_tool("ffmpeg")
        if not self.ffmpeg:
            return self._fail("ffmpeg not found. Run the LinkDrop installer again.")
        try:
            os.makedirs(self.folder, exist_ok=True)
        except Exception as e:
            return self._fail("Can't use the download folder: %s" % e)
        self._download(self.url)

    def on_line(self, line):
        if self.phase == "downloading":
            self._parse(line)
        elif self.phase == "converting":
            m = re.match(r"out_time_(?:ms|us)=(\d+)", line)
            if m and self.duration:
                p = min(1.0, int(m.group(1)) / 1e6 / self.duration)
                self.percent = max(self.percent, self.conv_base + p * (99 - self.conv_base))
        else:
            self.tail = (self.tail + [line])[-12:]

    def _template(self):
        name = self.opts.get("name")
        if name:
            base = name.replace("%", "%%")
        else:
            base = "%(title).90B [%(id)s]"
        folder = self.folder
        if self.opts.get("subfolders"):
            sub = (self.opts.get("subfolder_name") or "").strip() or self.platform
            folder = os.path.join(folder, re.sub(r'[\\/:*?"<>|]', "-", sub))
        return os.path.join(folder, base + ".%(ext)s")

    def _download(self, target):
        self.phase = "downloading"
        self.status = "Getting video info"
        o = self.opts
        cmd = ytdlp_cmd() + ["--no-playlist", "--newline", "--no-colors", "--windows-filenames",
               "--encoding", "utf-8", "-o", self._template(), "--print", "after_move:FINAL:%(filepath)s",
               "--progress-template",
               "download:PROG:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
               "--ffmpeg-location", os.path.dirname(self.ffmpeg)]
        if self.audio_only:
            fmt = o.get("audio_fmt", "wav")
            cmd += ["-f", "bestaudio/best", "-x", "--audio-format", {"aac": "m4a"}.get(fmt, fmt)]
            q = o.get("audio_q", "best")
            if fmt != "wav":
                cmd += ["--audio-quality", "0" if q == "best" else q + "K"]
            if o.get("keep_meta"):
                cmd += ["--embed-metadata"]
            if o.get("embed_art") and fmt in ("mp3", "aac"):
                cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
        else:
            res = o.get("max_h")
            # 'res' is the short side, so vertical reels and landscape videos are treated alike
            cmd += ["-f", "bv*+ba/b", "-S", "res:%d,vcodec:h264,acodec:aac" % res if res else "vcodec:h264,acodec:aac",
                    "--merge-output-format", "mp4"]
            if o.get("keep_meta"):
                cmd += ["--embed-metadata"]
        cmd.append(target)
        self.part = 0
        self.tail = []
        self._spawn(cmd, self._download_done)

    def _parse(self, line):
        self.tail = (self.tail + [line])[-12:]
        if line.startswith("[download] Destination:"):
            self.part += 1
        elif line.startswith("PROG:"):
            pct, speed, eta = (line[5:].split("|") + ["", "", ""])[:3]
            try:
                p = float(pct.strip().rstrip("%"))
            except ValueError:
                return
            span = 70.0 if self.opts.get("vcodec") == "prores" and not self.audio_only else 95.0
            if self.audio_only:
                overall = p * span / 100
            elif self.part <= 1:
                overall = p * span * 0.88 / 100
            else:
                overall = span * 0.88 + p * span * 0.12 / 100
            self.percent = max(self.percent, overall)
            what = "audio" if (self.audio_only or self.part > 1) else "video"
            speed = speed.strip().replace("MiB/s", "MB/s").replace("KiB/s", "KB/s")
            self.status = "Downloading %s  \u00b7  %s  \u00b7  %s left" % (what, speed, eta.strip())
        elif line.startswith("FINAL:"):
            self.path = line[6:].strip()
        elif line.startswith("[Merger]"):
            self.status = "Merging video and audio"
        elif line.startswith("[ExtractAudio]"):
            self.status = "Converting audio"

    def _download_done(self, code):
        if code != 0 and not self.cancelled and not getattr(self, "retried", False) and \
                any("403" in l or "Unable to download" in l for l in self.tail):
            self.retried = True
            self.status = "Retrying"
            return self._download(self.url)
        if code != 0 or not self.path or not os.path.exists(self.path):
            errs = [l for l in self.tail if "ERROR" in l] or self.tail[-2:]
            return self._fail(clean_error(errs[-1]) if errs else "The download didn't finish.")
        if self.audio_only:
            return self._finish()
        if self.opts.get("vcodec") == "prores":
            return self._convert_prores()
        v, a = ffprobe_codecs(self.path)
        if (v is None or v in RESOLVE_OK_VCODECS) and (a is None or a in RESOLVE_OK_ACODECS):
            return self._finish()
        self._convert_compat(v, a, hw=not IS_WIN)

    def _ffmpeg(self, args, out, on_exit, base):
        self.phase = "converting"
        self.conv_base = max(self.percent, base)
        self.percent = self.conv_base
        self.converted = out
        self._spawn([self.ffmpeg, "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", self.path] + args + [out],
                    on_exit)

    def _convert_prores(self):
        self.status = "Converting to ProRes 422"
        base, _ = os.path.splitext(self.path)
        self._ffmpeg(["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le", "-vendor", "apl0",
                      "-c:a", "pcm_s16le"], base + ".mov", self._converted, 70)

    def _convert_compat(self, v, a, hw):
        self.status = "Converting %s to H.264 so Resolve can play it" % v
        self.conv_args = (v, a, hw)
        base, _ = os.path.splitext(self.path)
        if v in RESOLVE_OK_VCODECS:
            vargs = ["-c:v", "copy"]
        elif hw:
            vargs = ["-c:v", "h264_videotoolbox", "-b:v", "25M", "-pix_fmt", "yuv420p"]
        else:
            vargs = ["-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p"]
        aargs = ["-c:a", "copy"] if (a is None or a in RESOLVE_OK_ACODECS) else ["-c:a", "aac", "-b:a", "320k"]
        self._ffmpeg(vargs + aargs + ["-movflags", "+faststart"], base + " (h264).mp4", self._compat_done, 95)

    def _compat_done(self, code):
        v, a, hw = self.conv_args
        if code != 0 and hw:
            return self._convert_compat(v, a, hw=False)
        self._converted(code)

    def _converted(self, code):
        if code == 0 and os.path.exists(self.converted):
            try:
                os.remove(self.path)
            except Exception:
                pass
            self.path = self.converted
            return self._finish()
        self._fail("Conversion failed. The original file is in your download folder.")

    def _finish(self):
        self.phase = "finished"
        self.percent = 100.0
        self.status = "Downloaded"
        self.done = True
        self._cleanup()


# ------------------------------------------------------- Resolve: import ---

def resolve_target():
    """(project name, current bin name) or None when Resolve has no project open."""
    try:
        project = resolve.GetProjectManager().GetCurrentProject()
        if not project:
            return None
        folder = project.GetMediaPool().GetCurrentFolder()
        return project.GetName(), (folder.GetName() if folder else "Master")
    except Exception:
        return None


def free_track(timeline, kind, start, end):
    """First track with no clip in [start, end); adds a new one if needed."""
    n = timeline.GetTrackCount(kind)
    for i in range(1, n + 1):
        busy = False
        for it in (timeline.GetItemListInTrack(kind, i) or []):
            if it.GetStart() < end and it.GetEnd() > start:
                busy = True
                break
        if not busy:
            return i
    if kind == "audio":
        timeline.AddTrack("audio", "stereo")
    else:
        timeline.AddTrack("video")
    return timeline.GetTrackCount(kind)


def place_in_resolve(path, target, cursor=None):
    """Imports into the current Media Pool bin, then places it. Returns (message, next_cursor);
    cursor chains batch items one after another at the playhead."""
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("Open a project in Resolve to import. The file is saved in your folder.")
    mp = project.GetMediaPool()
    items = mp.ImportMedia([path]) or []
    if not items:
        raise RuntimeError("Resolve couldn't import this file. It's saved in your folder.")
    item = items[0]
    name = os.path.basename(path)

    if target == 3:
        return "Imported into the Media Pool.", cursor

    timeline = project.GetCurrentTimeline()
    if target == 2 or not timeline:
        existing = {project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)}
        base = tl_name = os.path.splitext(name)[0][:60]
        n = 2
        while tl_name in existing:
            tl_name = "%s (%d)" % (base, n)
            n += 1
        tl = mp.CreateTimelineFromClips(tl_name, [item])
        if not tl:
            raise RuntimeError("Imported, but the timeline couldn't be created.")
        project.SetCurrentTimeline(tl)
        return "New timeline created.", None

    if target == 1:
        if not mp.AppendToTimeline([item]):
            raise RuntimeError("Imported, but it couldn't be added to the timeline.")
        return "Added to the end of \"%s\"." % timeline.GetName(), None

    # target == 0: at the playhead, on a free track (never overwrites anything)
    fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    if cursor is None:
        rec_tc = timeline.GetCurrentTimecode()
        rec = tc_to_frames(rec_tc, fps)
    else:
        rec, rec_tc = cursor, "after the previous clip"
    props = item.GetClipProperty() or {}
    try:
        clip_fps = float(props.get("FPS") or fps) or fps
    except ValueError:
        clip_fps = fps
    seconds = tc_to_frames(props.get("Duration") or "", clip_fps) / clip_fps
    tl_len = int(seconds * fps) + 1 if seconds > 0 else int(fps * 600)
    kind = props.get("Type") or ""
    has_video = "Video" in kind or bool(props.get("Video Codec"))
    has_audio = "Audio" in kind or bool(props.get("Audio Codec"))
    infos = []
    if has_video:
        infos.append({"mediaPoolItem": item, "mediaType": 1, "recordFrame": rec,
                      "trackIndex": free_track(timeline, "video", rec, rec + tl_len)})
    if has_audio:
        infos.append({"mediaPoolItem": item, "mediaType": 2, "recordFrame": rec,
                      "trackIndex": free_track(timeline, "audio", rec, rec + tl_len)})
    if not infos:
        infos = [{"mediaPoolItem": item, "recordFrame": rec}]
    placed = mp.AppendToTimeline(infos)
    if not placed:
        if not mp.AppendToTimeline([item]):
            raise RuntimeError("Imported, but it couldn't be added to the timeline.")
        return "Added to the end of the timeline.", None
    if len(placed) == 2:
        try:
            timeline.SetClipsLinked(placed, True)
        except Exception:
            pass
    end = max([p.GetEnd() for p in placed] + [rec + 1])
    return "Placed at the playhead (%s)." % rec_tc, end


# ---------------------------------------------------------------- update ---

def auto_update():
    """On launch: if GitHub has a newer LinkDrop, install it and run that one instead."""
    if globals().get("_LINKDROP_UPDATED_FROM"):
        return False  # we are the freshly updated copy
    v, code = fetch_latest()
    if not v or version_tuple(v) <= version_tuple(VERSION):
        return False
    path = SCRIPT_PATH
    try:
        path = install_update(code)
        print("[LinkDrop] Updated v%s -> v%s" % (VERSION, v))
    except Exception as e:
        print("[LinkDrop] Could not save v%s (%s); running it this time anyway." % (v, e))
    g = dict(globals())
    g.update({"__name__": "__main__", "__file__": path, "_LINKDROP_UPDATED_FROM": VERSION})
    exec(compile(code, path, "exec"), g)
    return True


# -------------------------------------------------------------------- UI ---
# Premium Dark Glass. Every visual rule lives here (palette, components, motion), so screens
# only compose them. Qt (Resolve's UIManager) can't blur or cast real shadows: depth comes from
# layered gradients, luminous hairline borders and light concentrated on bottom edges.

FONT = 'font-family: "SF Pro Display", "SF Pro Text", ".AppleSystemUIFont", "Segoe UI Variable", "Segoe UI";'
BG0, BG1, SURFACE = "#090A0F", "#0B1225", "#101B35"
NAVY, B1, B2, B3, B4 = "#03195B", "#0B2CB1", "#1951FC", "#3781FC", "#69B9FF"
TEXT, SECONDARY = "#F5F7FF", "#929DB8"
SUCCESS, ERROR = "#16D98B", "#FF477E"
GLOWS = {"blue": (25, 81, 252), "success": (22, 217, 139), "error": (255, 71, 126)}

SURF = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(16,27,53,0.92), stop:1 rgba(11,18,37,0.92));"
        "border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(150,190,255,0.20), "
        "stop:1 rgba(105,185,255,0.08));")
SURF_HOVER = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(20,34,66,0.95), stop:1 rgba(13,22,46,0.95));"
              "border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(170,205,255,0.30), "
              "stop:1 rgba(105,185,255,0.22));")
SURF_LIT = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(13,26,64,0.95), stop:0.6 rgba(11,44,177,0.30), "
            "stop:1 rgba(25,81,252,0.55));"
            "border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(150,200,255,0.40), "
            "stop:1 rgba(140,205,255,0.95));")
PRIMARY_BG = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0E2A9C, stop:0.5 %s, stop:1 %s)" % (B2, B3))
PRIMARY_HOVER = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1335B8, stop:0.5 #2A62FF, stop:1 %s)" % B4)
PRIMARY_PRESS = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:0.6 %s, stop:1 %s)" % (NAVY, B1, B2))


def _btn(base, radius="11px", pad="9px 20px", size=13, weight=500, extra=""):
    return ("QPushButton { " + FONT + "font-size: %dpx; font-weight: %d; padding: %s; border-radius: %s; color: %s; %s %s }"
            % (size, weight, pad, radius, TEXT, base, extra))


CSS = {
    "h1": FONT + "font-size: 27px; font-weight: 700; color: %s; background: transparent;" % TEXT,
    "sub": FONT + "font-size: 14px; color: %s; background: transparent;" % SECONDARY,
    "label": FONT + "font-size: 13px; font-weight: 600; color: %s; background: transparent;" % TEXT,
    "caption": FONT + "font-size: 12px; color: %s; background: transparent;" % SECONDARY,
    "box": ("QTextEdit, QLineEdit { " + FONT + "font-size: 14px; padding: 10px 14px; border-radius: 14px; color: %s;"
            "selection-background-color: %s; " + SURF + " }"
            "QTextEdit:focus, QLineEdit:focus { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            "stop:0 rgba(13,24,52,0.95), stop:1 rgba(25,81,252,0.20));"
            "border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(150,200,255,0.55), "
            "stop:1 rgba(140,205,255,1.0)); }") % (TEXT, B2),
    "field_left": ("QLineEdit { " + FONT + "font-size: 13px; padding: 9px 14px; color: %s; border-top-left-radius: 11px;"
                   "border-bottom-left-radius: 11px; border-top-right-radius: 0px; border-bottom-right-radius: 0px; "
                   + SURF + " }") % TEXT,
    "primary": (_btn("background: %s; border: 1px solid rgba(160,210,255,0.95);" % PRIMARY_BG, weight=600)
                + "QPushButton:hover { background: %s; }" % PRIMARY_HOVER
                + "QPushButton:pressed { background: %s; }" % PRIMARY_PRESS
                + "QPushButton:disabled { color: rgba(245,247,255,0.35); background: rgba(16,27,53,0.6);"
                  "border: 1px solid rgba(105,185,255,0.10); }"),
    "secondary": (_btn(SURF) + "QPushButton:hover { " + SURF_HOVER + " }"
                  "QPushButton:pressed { " + SURF_LIT + " }"
                  "QPushButton:disabled { color: rgba(245,247,255,0.30); }"),
    "seg_l": (_btn(SURF, radius="0px", pad="8px 22px", extra="border-top-left-radius: 11px; border-bottom-left-radius: 11px;")
              + "QPushButton:hover { color: #FFFFFF; } QPushButton:checked { font-weight: 600; " + SURF_LIT + " }"
              "QPushButton:disabled { color: rgba(245,247,255,0.28); }"),
    "seg_one": (_btn(SURF, pad="8px 22px") + "QPushButton:checked { font-weight: 600; " + SURF_LIT + " }"),
    "seg_m": (_btn(SURF, radius="0px", pad="8px 12px") + "QPushButton:checked { font-weight: 600; " + SURF_LIT + " }"),
    "seg_r": (_btn(SURF, radius="0px", pad="8px 22px", extra="border-top-right-radius: 11px; border-bottom-right-radius: 11px;")
              + "QPushButton:hover { color: #FFFFFF; } QPushButton:checked { font-weight: 600; " + SURF_LIT + " }"
              "QPushButton:disabled { color: rgba(245,247,255,0.28); }"),
    "pill": (_btn(SURF, radius="12px", pad="9px 18px", size=14)
             + "QPushButton:hover { " + SURF_HOVER + " } QPushButton:checked { " + SURF_LIT + " }"
             "QPushButton:disabled { color: rgba(245,247,255,0.28); }"),
    "iconbtn": (_btn(SURF, radius="11px", pad="9px 12px") + "QPushButton:hover { " + SURF_HOVER + " }"
                "QPushButton:pressed { " + SURF_LIT + " }"),
    "tile": (_btn(SURF, radius="16px", pad="16px 14px", size=13, extra="text-align: left;")
             + "QPushButton:hover { " + SURF_HOVER + " } QPushButton:checked { " + SURF_LIT + " }"),
    "row_l": (_btn(SURF, radius="0px", pad="11px 14px", extra="text-align: left; border-right: none;"
                   "border-top-left-radius: 12px; border-bottom-left-radius: 12px;")),
    "row_r": (_btn(SURF, radius="0px", pad="6px 12px", extra="border-left: none;"
                   "border-top-right-radius: 12px; border-bottom-right-radius: 12px;")),
    "combo": ("QComboBox { " + FONT + "font-size: 13px; padding: 7px 12px; border-radius: 10px; color: %s; min-width: 150px;"
              + SURF + " } QComboBox:hover { " + SURF_HOVER + " }"
              "QComboBox QAbstractItemView { background: #0D1426; color: %s; border: 1px solid rgba(105,185,255,0.25);"
              "selection-background-color: %s; }") % (TEXT, TEXT, B1),
    "check": "",
    "pager": (_btn("background: transparent; border: none;", pad="2px 8px", size=14)
              + "QPushButton:hover { color: %s; } QPushButton:disabled { color: rgba(245,247,255,0.2); }" % B4),
    "link": ("QPushButton { " + FONT + "font-size: 12px; color: %s; background: transparent; border: none; padding: 0px; }"
             "QPushButton:hover { color: %s; }") % (SECONDARY, TEXT),
    "insta": ("QPushButton { " + FONT + "font-size: 14px; font-weight: 600; color: %s; background: transparent;"
              "border: none; padding: 0px; } QPushButton:hover { color: #FFFFFF; }") % B4,
    "sep": "background: rgba(150,190,255,0.10); min-height: 1px; max-height: 1px;",
}


def card_css(state="idle", glow=None):
    """Card surface. idle: navy glass; active: blue light from the bottom (glow 0..1 makes it breathe);
    ok / error tinted edges (glow 0..1 makes a completed card flash)."""
    base = FONT + "padding: 10px 14px; border-radius: 14px; color: %s; " % TEXT
    if state == "active":
        if glow is None:
            return base + SURF_LIT.replace("0.55)", "0.30)")
        g = min(max(glow, 0.0), 1.0)
        return base + ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(13,26,64,0.95), "
                       "stop:0.6 rgba(11,44,177,%.2f), stop:1 rgba(25,81,252,%.2f));"
                       "border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(150,200,255,%.2f), "
                       "stop:1 rgba(140,205,255,%.2f));" % (0.18 + 0.12 * g, 0.22 + 0.22 * g, 0.25 + 0.25 * g,
                                                          0.55 + 0.45 * g))
    if state == "ok":
        if glow:
            g = min(max(glow, 0.0), 1.0)
            return base + ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(13,30,40,0.95), "
                           "stop:1 rgba(22,217,139,%.2f)); border: 1px solid rgba(22,217,139,%.2f);"
                           % (0.05 + 0.30 * g, 0.30 + 0.65 * g))
        return base + SURF.replace("rgba(105,185,255,0.08)", "rgba(22,217,139,0.30)")
    if state == "error":
        return base + SURF.replace("rgba(105,185,255,0.08)", "rgba(255,71,126,0.45)").replace(
            "rgba(150,190,255,0.20)", "rgba(255,71,126,0.25)")
    if state == "panel":
        return base + SURF_LIT.replace("0.55)", "0.18)")
    if state == "danger":
        return (base + "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(60,16,40,0.85), "
                "stop:1 rgba(24,12,30,0.92)); border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                "stop:0 rgba(255,71,126,0.55), stop:1 rgba(255,120,160,0.85));")
    return base + SURF


PILL = {"blue": (B4, "#0B1A3D"), "green": (SUCCESS, "#0A2A20"), "rose": (ERROR, "#2E0E1E"), "gray": (SECONDARY, "#141B2E")}
SPIN = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]


def pill(text, tone, glyph=""):
    fg, bg = PILL[tone]
    return ('<span style="background-color:%s; color:%s; font-size:12px; font-weight:600;">'
            '&nbsp;&nbsp;%s%s&nbsp;&nbsp;</span>' % (bg, fg, (glyph + "&nbsp;") if glyph else "", html.escape(text)))


def bar_html(frac, height=4, tone=None, sheen=None):
    """Progress bar as table cells. sheen (0..1) slides a soft highlight across the filled part."""
    f = min(max(frac, 0.0), 1.0)
    fill = {"green": SUCCESS, "rose": ERROR}.get(tone, B3)
    cells = []
    if sheen is not None and tone is None and f <= 0.004:
        glint = 14
        left = int(round((100 - glint) * sheen))
        for w, col in ((left, "#22305A"), (glint, "#5FA8FF"), (100 - glint - left, "#22305A")):
            if w > 0:
                cells.append('<td width="%d%%" height="%d" bgcolor="%s" style="font-size:2px;">&nbsp;</td>'
                             % (w, height, col))
        return ('<table width="100%%" cellspacing="0" cellpadding="0" style="margin-top:10px;"><tr>%s</tr></table>'
                % "".join(cells))
    if sheen is not None and tone is None and f > 0.12:
        width = round(f * 100)
        glint = 7
        left = int(round((width - glint) * sheen))
        right = width - glint - left
        for w, col in ((left, fill), (glint, "#A8D8FF"), (right, fill)):
            if w > 0:
                cells.append('<td width="%d%%" height="%d" bgcolor="%s" style="font-size:2px;">&nbsp;</td>'
                             % (w, height, col))
        if f < 0.996:
            cells.append('<td height="%d" bgcolor="#22305A" style="font-size:2px;">&nbsp;</td>' % height)
        return ('<table width="100%%" cellspacing="0" cellpadding="0" style="margin-top:10px;"><tr>%s</tr></table>'
                % "".join(cells))
    if f > 0.004:
        cells.append('<td width="%d%%" height="%d" bgcolor="%s" style="font-size:2px;">&nbsp;</td>'
                     % (max(1, round(f * 100)), height, fill))
    if f < 0.996:
        cells.append('<td height="%d" bgcolor="#22305A" style="font-size:2px;">&nbsp;</td>' % height)
    return ('<table width="100%%" cellspacing="0" cellpadding="0" style="margin-top:10px;"><tr>%s</tr></table>'
            % "".join(cells))


def thumb_html(thumb, icon):
    if thumb and os.path.exists(thumb):
        return '<img src="%s" width="64" height="36">' % thumb.replace("\\", "/")
    return img_tag(icon, 30) if icon else ""


def card_html(thumb, title, sub, right="", frac=None, tone=None, pct=None, maxlen=52, sheen=None):
    """One list card: thumbnail, title, subtitle, a status pill on the right and an optional bar."""
    t = html.escape(title if len(title) <= maxlen else title[:maxlen - 3] + "...")
    s = html.escape(sub if len(sub) <= 70 else sub[:67] + "...")
    top = ('<table width="100%%" cellspacing="0" cellpadding="0"><tr>'
           '<td width="76" valign="middle">%s</td>'
           '<td valign="middle"><span style="font-size:14px; font-weight:600; color:%s;">%s</span><br>'
           '<span style="font-size:11px; color:%s;">%s</span></td>'
           '%s</tr></table>'
           % (thumb, TEXT, t, SECONDARY, s,
              ('<td align="right" valign="middle" width="150">%s</td>' % right) if right else ""))
    if frac is None:
        return top
    return top + bar_html(frac, tone=tone, sheen=sheen)


def window_css(glow):
    """Deep black with a soft volumetric glow in the top-right corner. Containers are transparent
    so the window paints it once, with no seams."""
    r, g, b = glow
    return ("QWidget { background: qradialgradient(cx:0.95, cy:-0.12, radius:1.1, fx:0.95, fy:-0.12, "
            "stop:0 rgba(%d,%d,%d,0.40), stop:0.35 rgba(3,25,91,0.32), stop:0.7 %s, stop:1 %s); }"
            "QLabel, QStackedWidget, QStackedWidget > QWidget, QAbstractScrollArea > QWidget,"
            "QScrollBar, QCheckBox { background: transparent; }"
            "QToolTip { color: %s; background: #0D1426; border: 1px solid rgba(105,185,255,0.25); padding: 5px; }"
            % (r, g, b, BG1, BG0, TEXT))


def dot_css(v):
    """Step dot: 8px blue-gray circle that brightens and grows to a 10px luminous dot (v: 0 -> 1)."""
    v = min(max(v, 0.0), 1.2)
    d = int(round(8 + 2 * v))
    col = "#CFE8FF" if v > 0.5 else "rgba(120,150,210,0.35)"
    ring = "border: 2px solid rgba(105,185,255,%.2f);" % (0.85 * v) if v > 0.05 else "border: 2px solid transparent;"
    return ("background: %s; %s border-radius: %dpx; min-width: %dpx; max-width: %dpx; min-height: %dpx; max-height: %dpx;"
            % (col, ring, (d + 4) // 2, d, d, d, d))


def bar_css(fraction, pulse=None):
    """Thin progress track (overall bars). Indeterminate: a soft light sweep."""
    base = "border-radius: 3px; min-height: 6px; max-height: 6px; "
    track = "rgba(105,140,220,0.16)"
    if pulse is not None:
        a, b = max(0.0, pulse - 0.18), min(1.0, pulse + 0.18)
        if b <= a + 0.01:
            return base + "background: %s;" % track
        mid = min(max(pulse, a + 0.001), b - 0.001)
        return base + ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:%.3f %s, "
                       "stop:%.3f %s, stop:%.3f %s, stop:1 %s);" % (track, a, track, mid, B4, b, track, track))
    f = min(max(fraction, 0.0), 1.0)
    if f <= 0.002:
        return base + "background: %s;" % track
    if f >= 0.998:
        return base + "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:0.6 %s, stop:1 %s);" % (B2, B3, B4)
    return base + ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:%.3f %s, stop:%.3f %s, "
                   "stop:%.3f %s, stop:1 %s);" % (B2, f * 0.6, B3, f, B4, f + 0.001, track, track))


def clean_error(text):
    """Turn yt-dlp's 'ERROR: [youtube] abc123: Video unavailable' into 'Video unavailable.'"""
    line = (text or "").strip().split("\n")[0]
    line = re.sub(r"^ERROR:\s*", "", line)
    line = re.sub(r"^\[[^\]]+\]\s*[^:\s]+:\s*", "", line)
    line = line.strip()
    if line and line[-1] not in ".!?":
        line += "."
    return line[:1].upper() + line[1:160]


def mix(c1, c2, k):
    return tuple(int(round(a + (b - a) * k)) for a, b in zip(c1, c2))


def short_title(path_or_url):
    name = os.path.splitext(os.path.basename(path_or_url))[0]
    return re.sub(r"\s*\[[^\]]+\]$", "", name) or path_or_url


def short_url(url):
    return re.sub(r"^https?://(www\.)?", "", url)


def reduce_motion():
    """True when the person turned on Reduce Motion (macOS) or turned off animations (Windows)."""
    try:
        if IS_WIN:
            import ctypes
            enabled = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
            return not enabled.value
        return run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"], 3).strip() == "1"
    except Exception:
        return False


def play_feedback(ok):
    """Short system sound to complement the visual result (HIG: feedback through more than one channel)."""
    try:
        if IS_WIN:
            import winsound
            winsound.MessageBeep(0x40 if ok else 0x10)
        else:
            snd = "/System/Library/Sounds/%s.aiff" % ("Glass" if ok else "Basso")
            if os.path.exists(snd):
                subprocess.Popen(["afplay", snd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def copy_text(text):
    try:
        if IS_WIN:
            p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                                 stdin=subprocess.PIPE, creationflags=NO_WINDOW)
        else:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, env=ENV)
        p.communicate(text.encode("utf-8"), timeout=5)
        return True
    except Exception:
        return False


class Spring(object):
    """Damped spring like SwiftUI's .spring(response:dampingFraction:). Retargetable: changing
    the target keeps the current velocity, so motion never jumps."""

    def __init__(self, value, response=0.22, damping=1.0):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.response, self.damping = response, damping

    def step(self, dt):
        k = (2 * 3.141592653589793 / self.response) ** 2
        c = 4 * 3.141592653589793 * self.damping / self.response
        n = max(1, int(dt / 0.004))
        h = dt / n
        for _ in range(n):
            self.velocity += (k * (self.target - self.value) - c * self.velocity) * h
            self.value += self.velocity * h
        return self.value

    def settled(self):
        return abs(self.target - self.value) < 0.01 and abs(self.velocity) < 0.01

    def snap(self, value):
        self.value = self.target = float(value)
        self.velocity = 0.0


# pages
P_LINKS, P_ANALYZE, P_FORMAT, P_REVIEW, P_SETTINGS, P_RUN, P_DONE, P_ERROR = range(8)
PAGE_DOT = {P_LINKS: 0, P_ANALYZE: 0, P_SETTINGS: 0, P_FORMAT: 1, P_REVIEW: 2, P_RUN: 3, P_ERROR: 3, P_DONE: 3}
LINK_SIZES = [76, 140, 220]
LINK_WEIGHTS = [(3.0, 1.0, 3.0), (1.4, 1.6, 1.4), (0.6, 2.4, 0.6)]   # (space above, box, space below)
LINK_PAGE = [0, 8, 9]   # Pages index of each link-box size   # the link box steps through these heights as links are added
RUN_SLOTS = 3
# one stable window size for every step (resizing per step fought Qt's minimum sizes);
# only the background mini mode is smaller
W, H, W_MINI, H_MINI = 700, 740, 420, 132
SLOTS = 4
TARGETS = ["At the playhead", "At the end of the timeline", "In a new timeline", "Media Pool only"]


def check_css():
    on, off = icon_path("chk_on"), icon_path("chk_off")
    return ("QCheckBox { background: transparent; spacing: 0px; } QCheckBox::indicator { width: 22px; height: 22px; }"
            "QCheckBox::indicator:unchecked { image: url(%s); } QCheckBox::indicator:checked { image: url(%s); }"
            % ((off or "").replace("\\", "/"), (on or "").replace("\\", "/")))


def main():
    settings = load_settings()
    CSS["check"] = check_css()
    calm = reduce_motion()
    S = settings.get

    def h1(id_, text):
        return ui.Label({"ID": id_, "Text": text, "StyleSheet": CSS["h1"], "Weight": 0})

    def sub(id_, text):
        return ui.Label({"ID": id_, "Text": text, "WordWrap": True, "StyleSheet": CSS["sub"], "Weight": 0})

    def icon_btn(id_, icon, tip):
        return ui.Button({"ID": id_, "Text": "", "StyleSheet": CSS["iconbtn"], "Weight": 0, "ToolTip": tip,
                          "Icon": ui.Icon({"File": icon_path(icon)}), "IconSize": [18, 18], "MinimumSize": [44, 0],
                          "MaximumSize": [44, 200]})

    def btn(id_, text, kind="secondary", icon=None, visible=True):
        props = {"ID": id_, "Text": text, "StyleSheet": CSS[kind], "Weight": 0}
        path = icon_path(icon) if icon else None
        if path:
            props["Icon"] = ui.Icon({"File": path})
            props["IconSize"] = [16, 16]
        return ui.Button(props)

    def card(id_, weight=0, height=62):
        return ui.Label({"ID": id_, "Text": "", "WordWrap": True, "StyleSheet": card_css(), "Weight": weight,
                         "MinimumSize": [0, height]})

    def pager(prefix):
        return ui.HGroup({"Weight": 0, "Spacing": 4}, [
            ui.HGap(0, 1),
            ui.Button({"ID": prefix + "Prev", "Text": "\u2039", "StyleSheet": CSS["pager"], "Weight": 0}),
            ui.Label({"ID": prefix + "Page", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0}),
            ui.Button({"ID": prefix + "Next", "Text": "\u203a", "StyleSheet": CSS["pager"], "Weight": 0}),
            ui.HGap(0, 1)])

    def nav(*items):
        return ui.HGroup({"Weight": 0, "Spacing": 10}, list(items))

    def toggle(id_, text, icon):
        path = icon_path(icon)
        props = {"ID": id_ + "Row", "Text": "   " + text, "StyleSheet": CSS["row_l"], "Weight": 1}
        if path:
            props["Icon"] = ui.Icon({"File": path})
            props["IconSize"] = [18, 18]
        return ui.HGroup({"Weight": 1, "Spacing": 0}, [
            ui.Button(props),
            ui.Button({"ID": id_ + "Sw", "Text": "", "StyleSheet": CSS["row_r"], "Weight": 0,
                       "Icon": ui.Icon({"File": icon_path("sw_off")}), "IconSize": [44, 27]})])

    # ------------------------------------------------------------ screens ---
    def links_layout(i):
        top, box, bottom = LINK_WEIGHTS[i]
        return ui.VGroup({"Spacing": 12}, [
            ui.VGap(0, top),
            ui.Label({"ID": "H0_%d" % i, "Text": "Add links", "StyleSheet": CSS["h1"], "Weight": 0}),
            ui.Label({"ID": "S0_%d" % i, "Text": "Paste links from your browser, one per line. Add as many as you like.",
                      "StyleSheet": CSS["sub"], "Weight": 0}),
            ui.VGap(4, 0),
            ui.TextEdit({"ID": "Links%d" % i, "PlaceholderText": "Paste one or more links, one per line",
                         "AcceptRichText": False, "StyleSheet": CSS["box"], "Weight": box,
                         "MinimumSize": [0, 60]}),
            ui.VGap(2, 0),
            ui.Label({"ID": "Detected%d" % i, "Text": "", "Alignment": {"AlignHCenter": True}, "Weight": 0}),
            ui.Label({"ID": "Count%d" % i, "Text": "", "Alignment": {"AlignHCenter": True}, "StyleSheet": CSS["caption"],
                      "Weight": 0}),
            ui.VGap(0, bottom),
            nav(icon_btn("Paste%d" % i, "i_paste", "Paste from clipboard"),
                icon_btn("OpenSettings%d" % i, "i_gear", "Settings"),
                ui.HGap(0, 1), btn("Next0_%d" % i, "Continue  \u2192", "primary")),
        ])

    link_pages = [links_layout(i) for i in range(len(LINK_SIZES))]

    page_analyze = ui.VGroup({"Spacing": 10}, [
        h1("H1", "Analyzing links"),
        sub("S1", "We're checking your links and preparing your downloads."),
        ui.VGap(2, 0),
    ] + [card("A%d" % i) for i in range(SLOTS)] + [
        pager("A"),
        ui.VGap(0, 1),
        ui.Label({"ID": "AnaCount", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0}),
        ui.Label({"ID": "AnaBar", "Text": "", "StyleSheet": bar_css(0), "Weight": 0}),
        ui.VGap(4, 0),
        nav(btn("Back1", "  Back", icon="i_back"), ui.HGap(0, 1), btn("Next1", "Continue  \u2192", "primary")),
    ])

    def tiles(prefix, entries, icon_for):
        return ui.HGroup({"Weight": 0, "Spacing": 10},
                         [ui.Button({"ID": "%s%d" % (prefix, i), "Text": "  %s\n  %s" % (lbl, desc), "Checkable": True,
                                     "StyleSheet": CSS["tile"], "Weight": 1,
                                     "Icon": ui.Icon({"File": icon_path(icon_for(key))}), "IconSize": [26, 26]})
                          for i, (key, lbl, desc) in enumerate(entries)])

    def quality_row(id_):
        return ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Label({"Text": "Quality", "StyleSheet": CSS["label"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.ComboBox({"ID": id_, "StyleSheet": CSS["combo"], "Weight": 0})])

    page_format = ui.VGroup({"Spacing": 12}, [
        h1("H2", "Choose format"),
        sub("S2", "Choose the best format for your workflow."),
        ui.VGap(0, 1),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Button({"ID": "SegAudio", "Text": "  Audio", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1,
                       "Icon": ui.Icon({"File": icon_path("i_audio")}), "IconSize": [18, 18]}),
            ui.Button({"ID": "SegVideo", "Text": "  Video", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1,
                       "Icon": ui.Icon({"File": icon_path("i_video")}), "IconSize": [18, 18]})]),
        ui.VGap(8, 0),
        ui.Stack({"ID": "FmtStack", "Weight": 0}, [
            ui.VGroup({"Spacing": 12}, [
                tiles("AF", AUDIO_FORMATS, lambda k: "i_wave" if k == "wav" else "i_music"),
                quality_row("QualA"),
                ui.HGroup({"Weight": 0, "Spacing": 10}, [toggle("Art", "Embed artwork", "i_music"),
                                                          toggle("Meta", "Keep metadata", "i_info")])]),
            ui.VGroup({"Spacing": 12}, [
                tiles("VC", VIDEO_CODECS, lambda k: "i_movie" if k == "h264" else "i_prores"),
                quality_row("QualV"),
                ui.VGap(0, 1)]),
        ]),
        ui.Label({"ID": "FormatNote", "Text": "", "WordWrap": True, "StyleSheet": CSS["caption"], "Weight": 0,
                  "Alignment": {"AlignHCenter": True}, "MinimumSize": [0, 34]}),
        ui.VGap(0, 1),
        nav(btn("Back2", "  Back", icon="i_back"), ui.HGap(0, 1), btn("Next2", "Continue  \u2192", "primary")),
    ])

    page_review = ui.VGroup({"Spacing": 8}, [
        h1("H3", "Review items"),
        sub("S3", "Review your media before downloading."),
        ui.VGap(2, 0),
    ] + [ui.HGroup({"Weight": 0, "Spacing": 10}, [
        ui.CheckBox({"ID": "RC%d" % i, "Text": "", "StyleSheet": CSS["check"], "Weight": 0}),
        card("R%d" % i, 1),
        ui.Button({"ID": "RF%d" % i, "Text": "", "StyleSheet": CSS["secondary"], "Weight": 0, "MinimumSize": [72, 0],
                   "ToolTip": "Switch this item between video and audio"})]) for i in range(SLOTS)] + [
        pager("R"),
        ui.VGap(0, 1),
        ui.Label({"ID": "ReviewSum", "Text": "", "StyleSheet": card_css("panel"), "Weight": 0, "MinimumSize": [0, 44]}),
        ui.VGap(4, 0),
        nav(btn("Back3", "  Back", icon="i_back"), ui.HGap(0, 1), btn("Next3", "Start Download  \u2192", "primary")),
    ])

    page_settings = ui.VGroup({"Spacing": 7}, [
        h1("H4", "Settings"),
        sub("S4", "Where files go and how they're organized. Saved for next time."),
        ui.Label({"Text": "Save to", "StyleSheet": CSS["label"], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 0}, [
            ui.LineEdit({"ID": "SavePath", "ReadOnly": True, "StyleSheet": CSS["field_left"], "Weight": 1}),
            ui.Button({"ID": "Browse2", "Text": "Browse", "StyleSheet": CSS["row_r"].replace("6px 12px", "9px 18px"),
                       "Weight": 0})]),
        ui.Label({"Text": "File naming", "StyleSheet": CSS["label"], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Button({"ID": "NameOrig", "Text": "Original title", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1}),
            ui.Button({"ID": "NameCustom", "Text": "Custom", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1})]),
        ui.Stack({"ID": "NameStack", "Weight": 0}, [
            ui.Label({"Text": "Files keep the title from the source.", "StyleSheet": CSS["caption"], "Weight": 0}),
            ui.LineEdit({"ID": "CustomName", "PlaceholderText": "Name, e.g. Sunday Service (files become Sunday Service 01, 02...)",
                         "StyleSheet": CSS["box"], "Weight": 0})]),
        toggle("Imp", "Import into current Resolve bin", "i_film"),
        toggle("Sub", "Create subfolders", "i_tree"),
        ui.LineEdit({"ID": "SubName", "PlaceholderText": "Subfolder name (optional). Empty: one folder per source, like \"YouTube\"",
                     "StyleSheet": CSS["box"], "Weight": 0}),
        toggle("Rev", "Reveal in Finder when finished" if not IS_WIN else "Show in Explorer when finished", "i_finder"),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Label({"Text": "Place in timeline", "StyleSheet": CSS["label"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.ComboBox({"ID": "Place", "StyleSheet": CSS["combo"], "Weight": 0, "ToolTip": "Where the clip goes"})]),
        ui.Label({"ID": "TargetInfo", "Text": "", "StyleSheet": card_css("panel"), "Weight": 0, "MinimumSize": [0, 44]}),
        ui.VGap(0, 1),
        nav(ui.HGap(0, 1), btn("SettingsDone", "Done  \u2713", "primary")),
    ])

    page_run = ui.VGroup({"Spacing": 10}, [
        h1("H5", "Downloading"),
        sub("S5", "Your media is being prepared for DaVinci Resolve."),
        ui.Label({"ID": "Overall", "Text": "", "StyleSheet": card_css("panel"), "Weight": 0, "MinimumSize": [0, 124]}),
    ] + [card("D%d" % i, height=92) for i in range(RUN_SLOTS)] + [
        pager("D"),
        ui.VGap(0, 1),
        nav(btn("BgRun", "  Run in Background", icon="i_bg"), ui.HGap(0, 1), btn("CancelAll", "  Cancel All", icon="i_close")),
    ])

    page_done = ui.VGroup({"Spacing": 12}, [
        ui.VGap(0, 1),
        ui.Label({"ID": "DonePanel", "Text": "", "WordWrap": True, "StyleSheet": FONT + "background: transparent;",
                  "Weight": 0, "MinimumSize": [0, 360], "Alignment": {"AlignHCenter": True, "AlignVCenter": True}}),
        ui.VGap(0, 1),
        nav(icon_btn("OpenFolder", "i_folder", "Show the files"), icon_btn("CopyReport", "i_copy", "Copy report"),
            ui.HGap(0, 1), btn("DoneBtn", "Done", "primary")),
    ])

    page_error = ui.VGroup({"Spacing": 9}, [
        h1("H7", "Some files need attention"),
        sub("S7", "Some downloads couldn't be completed."),
        ui.Label({"ID": "ErrPanel", "Text": "", "WordWrap": True, "StyleSheet": card_css("danger"), "Weight": 0,
                  "MinimumSize": [0, 92]}),
    ] + [card("E%d" % i) for i in range(3)] + [
        pager("E"),
        ui.VGap(0, 1),
        nav(btn("Skip", "Skip"), ui.HGap(0, 1), btn("Report", "  Report Issue", icon="i_chat"),
            btn("RetryFailed", "\u21bb  Retry Failed", "primary")),
    ])

    win = disp.AddWindow({
        "ID": "LinkDropWin",
        "WindowTitle": "LinkDrop",
        "Geometry": [330, 120, W, H],
        "MinimumSize": [W, H], "MaximumSize": [W, H],
        "StyleSheet": window_css(GLOWS["blue"]),
    }, ui.HGroup({"Spacing": 0}, [
        ui.HGap(26, 0),
        ui.VGroup({"Spacing": 0}, [ui.VGap(6, 0), ui.VGroup({"Spacing": 12}, [
        ui.HGroup({"Weight": 0, "Spacing": 10}, [ui.HGap(0, 1)] +
                  [ui.Label({"ID": "Dot%d" % i, "Text": "", "StyleSheet": dot_css(1 if i == 0 else 0), "Weight": 0})
                   for i in range(4)] + [ui.HGap(0, 1)]),
        ui.Stack({"ID": "Pages", "Weight": 1}, [link_pages[0], page_analyze, page_format, page_review, page_settings,
                                                page_run, page_done, page_error] + link_pages[1:]),
        ui.Label({"ID": "Sep", "Text": "", "StyleSheet": CSS["sep"], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 0}, [
            ui.HGroup({"Weight": 0, "Spacing": 0, "MinimumSize": [230, 0], "MaximumSize": [230, 40]}, [
                ui.Button({"ID": "Browse", "Text": "", "Flat": True, "StyleSheet": CSS["link"], "Weight": 0,
                           "ToolTip": "Where downloads are saved. Click to change."}),
                ui.HGap(0, 1)]),
            ui.HGap(0, 1),
            ui.Button({"ID": "Insta", "Text": "@gabrielxreis_", "Flat": True, "StyleSheet": CSS["insta"],
                       "ToolTip": "Follow on Instagram: " + INSTAGRAM_URL, "Weight": 0}),
            ui.HGap(0, 1),
            ui.Label({"ID": "Ver", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0,
                      "Alignment": {"AlignRight": True, "AlignVCenter": True},
                      "MinimumSize": [230, 0], "MaximumSize": [230, 40]})]),
    ]), ui.VGap(10, 0)]),
        ui.HGap(26, 0),
    ]))
    mini = disp.AddWindow({
        "ID": "LinkDropMini",
        "WindowTitle": "LinkDrop",
        "Geometry": [80, 80, W_MINI, H_MINI],
        "MinimumSize": [W_MINI, H_MINI], "MaximumSize": [W_MINI, H_MINI],
        "StyleSheet": window_css(GLOWS["blue"]),
    }, ui.VGroup({"Spacing": 10}, [
        ui.HGroup({"Weight": 0}, [
            ui.Label({"ID": "MiniTitle", "Text": "Downloading", "StyleSheet": CSS["label"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.Label({"ID": "MiniPct", "Text": "", "StyleSheet": CSS["label"], "Weight": 0})]),
        ui.Label({"ID": "MiniBar", "Text": "", "StyleSheet": bar_css(0), "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Label({"ID": "MiniStatus", "Text": "", "StyleSheet": CSS["caption"], "Weight": 1}),
            btn("Expand", "  Open", icon="i_expand")]),
    ]))
    mitm = mini.GetItems()
    itm = win.GetItems()
    for t in TARGETS:
        itm["Place"].AddItem(t)

    # --------------------------------------------------------------- state ---
    st = {
        "mode": int(S("mode", 1)), "audio_fmt": S("audio_fmt", "wav"), "audio_q": S("audio_q", "best"),
        "vcodec": S("vcodec", "h264"), "max_h": S("max_h", 1080), "keep_meta": bool(S("keep_meta", True)),
        "embed_art": bool(S("embed_art", True)), "custom": bool(S("custom", False)), "custom_name": S("custom_name", ""),
        "import": bool(S("import", True)), "subfolders": bool(S("subfolders", False)), "reveal": bool(S("reveal", False)),
        "subname": S("subname", ""), "return_page": P_LINKS, "box": 0, "sync": False, "done_note": "",
        "target": int(S("target", 0)), "folder": S("folder") or DEFAULT_FOLDER,
        "page": P_LINKS, "items": [], "analyzers": [], "pages": {"A": 0, "R": 0, "D": 0, "E": 0}, "follow": True,
        "current": None, "cursor": None, "stopped": False, "mini": False, "finished": False, "imported_to": None,
        "last": time.time(), "fade": None, "pending": None, "glow": GLOWS["blue"], "glow_from": GLOWS["blue"],
        "glow_to": GLOWS["blue"], "spin": 0, "q_audio": [], "q_video": [], "filling": False,
    }
    springs = {"opacity": Spring(0.0, 0.18),
               "glow": Spring(1.0, 0.22), "overall": Spring(0.0, 0.35), "check": Spring(1.0, 0.24, 1.0 if calm else 0.6)}
    for i in range(4):
        springs["dot%d" % i] = Spring(1.0 if i == 0 else 0.0, 0.2)
    timer = ui.Timer({"ID": "Tick", "Interval": 16})

    def save_all():
        keys = ("mode", "audio_fmt", "audio_q", "vcodec", "max_h", "keep_meta", "embed_art", "custom", "custom_name",
                "import", "subfolders", "reveal", "target", "folder", "subname")
        save_settings({k: st[k] for k in keys})

    def lock_size(w, h):
        try:
            win.Resize([w, h])
        except Exception:
            pass

    def show(ids, on):
        for i in ([ids] if isinstance(ids, str) else ids):
            itm[i].Visible = on

    # -------------------------------------------------------------- motion ---
    def kick():
        st["last"] = time.time()
        timer.Start()

    def set_glow(key):
        target = GLOWS[key]
        if target == st["glow_to"]:
            return
        st["glow_from"], st["glow_to"] = st["glow"], target
        springs["glow"].snap(0.0)
        springs["glow"].target = 1.0
        kick()

    def go(page):
        if page == st["page"] and st["pending"] is None:
            return
        st["page"] = page
        for i in range(4):
            springs["dot%d" % i].target = 1.0 if PAGE_DOT.get(page) == i else 0.0
        st["fade"] = 0.35 if calm else 0.7
        st["pending"] = page
        springs["opacity"].target = st["fade"]
        kick()

    def apply_motion(dt):
        sp = springs
        win.WindowOpacity = min(1.0, max(0.0, sp["opacity"].step(dt)))
        for i in range(4):
            d = sp["dot%d" % i]
            if not d.settled():
                v = d.step(dt)
                if d.settled():
                    d.snap(d.target)
                    v = d.target
                itm["Dot%d" % i].StyleSheet = dot_css(v)
        if not sp["glow"].settled():
            g = mix(st["glow_from"], st["glow_to"], min(1.0, max(0.0, sp["glow"].step(dt))))
            if g != st["glow"]:
                st["glow"] = g
                win.StyleSheet = window_css(g)
        sp["overall"].step(dt)
        if not sp["check"].settled():
            sp["check"].step(dt)
            render_done()
        if st["pending"] is not None and sp["opacity"].value <= st["fade"] + 0.03:
            itm["Pages"].CurrentIndex = LINK_PAGE[st["box"]] if st["pending"] == P_LINKS else st["pending"]
            st["pending"] = None
            sp["opacity"].target = 1.0

    def motion_busy():
        return st["pending"] is not None or not all(s.settled() for s in springs.values())

    # ---------------------------------------------------------- 1. links ---
    def read_clipboard():
        txt = run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], 5) if IS_WIN else run(["pbpaste"], 3)
        return list(dict.fromkeys(u.rstrip(",;") for u in URL_RE.findall(txt or "")))

    def box_id():
        return "Links%d" % st["box"]

    def links_text():
        return itm[box_id()].PlainText or ""

    def set_links_text(t):
        st["sync"] = True
        for i in range(len(LINK_SIZES)):
            itm["Links%d" % i].PlainText = t
        st["sync"] = False
        refresh_links()

    def links_in_box():
        return list(dict.fromkeys(u.strip().rstrip(",;") for u in URL_RE.findall(links_text())))

    def on_links_changed(i):
        if st.get("sync") or i != st["box"]:
            return
        refresh_links()

    def refresh_links(ev=None):
        text = links_text()
        lines = len([l for l in text.split("\n") if l.strip()])
        want = 0 if lines <= 2 else (1 if lines <= 5 else 2)
        if want != st["box"]:
            # move to the next box size: same text, same centered composition, one size up/down
            st["sync"] = True
            itm["Links%d" % want].PlainText = text
            st["sync"] = False
            st["box"] = want
            if st["page"] == P_LINKS and st["pending"] is None:
                itm["Pages"].CurrentIndex = LINK_PAGE[want]
            try:
                itm["Links%d" % want].SetFocus()
            except Exception:
                pass
        urls = links_in_box()
        icons = []
        for u in urls:
            ic = platform_of(u)[1]
            if ic and ic not in icons:
                icons.append(ic)
        if urls:
            det = "&nbsp;&nbsp;".join(img_tag(i, 20) for i in icons[:8])
            cnt = "1 link ready" if len(urls) == 1 else "%d links ready" % len(urls)
        else:
            det = "&nbsp;&nbsp;".join(img_tag(n, 20) for n in SHOWCASE)
            cnt = "YouTube, Instagram, TikTok, X, Spotify and 1,000+ more sites"
        itm["Detected%d" % st["box"]].Text = det
        itm["Count%d" % st["box"]].Text = cnt
        for _k in range(len(LINK_SIZES)):
            itm["Next0_%d" % _k].Enabled = bool(urls)

    def on_paste(ev):
        urls = read_clipboard()
        if urls:
            cur = links_text().strip()
            set_links_text((cur + "\n" if cur else "") + "\n".join(urls))
        else:
            itm["Count%d" % st["box"]].Text = "Your clipboard doesn't have a link. Copy one in your browser first."

    # -------------------------------------------------------- 2. analyze ---
    def start_analysis(ev=None):
        urls = links_in_box()
        if not urls:
            return
        for a in st["analyzers"]:
            a.cancel()
        st["analyzers"] = [Analyzer(u) for u in urls]
        st["pages"]["A"] = 0
        st["follow"] = True
        go(P_ANALYZE)
        render_analyze()
        kick()

    def tick_analysis():
        running = [a for a in st["analyzers"] if a.state == "analyzing"]
        for a in running:
            a.poll()
        running = [a for a in st["analyzers"] if a.state == "analyzing"]
        for a in st["analyzers"]:
            if len(running) >= 3:
                break
            if a.state == "queued":
                a.start()
                if a.state == "analyzing":
                    running.append(a)
        if st["follow"]:
            active = next((i for i, a in enumerate(st["analyzers"]) if a.state in ("analyzing", "queued")), None)
            if active is not None:
                st["pages"]["A"] = active // SLOTS
        render_analyze()

    def analysis_busy():
        return any(a.state in ("queued", "analyzing") for a in st["analyzers"])

    def placeholder_title(a):
        if a.matched:
            return a.matched
        noun = {"match": "song", "music": "track"}.get(a.kind, "video")
        return "%s %s" % (a.platform if a.platform != "Web" else url_host(a.url).replace("www.", ""), noun)

    def analyzer_card(a):
        spin = SPIN[st["spin"] % 4]
        dur = human_duration(a.duration())
        if a.state == "ready":
            right, state = pill("Ready", "green", "\u2713"), "ok"
            subline = a.platform + ("  \u00b7  " + dur if dur else "")
        elif a.state == "failed":
            right, state = pill("Failed", "rose", "\u2715"), "error"
            subline = a.error or "This link couldn't be read."
        elif a.state == "analyzing":
            reading = "Finding song" if a.kind == "match" and not a.matched else "Reading metadata"
            right, state = pill(reading, "blue", spin), "active"
            subline = a.platform
        else:
            right, state = pill("Queued", "gray", "\u25f7"), "idle"
            subline = a.platform
        title = a.title() if a.info else placeholder_title(a)
        return card_html(thumb_html(a.thumb, a.icon), title, subline, right), state

    def render_list(prefix, rows, n_slots, render):
        page = st["pages"][prefix]
        pages = max(1, (len(rows) + n_slots - 1) // n_slots)
        page = min(page, pages - 1)
        st["pages"][prefix] = page
        for i in range(n_slots):
            k = page * n_slots + i
            cid = "%s%d" % (prefix, i)
            if k < len(rows):
                text, state = render(rows[k], k)
                glow = None
                if isinstance(state, tuple):
                    state, glow = state
                css = card_css(state, glow)
                itm[cid].Text = text
                if st.setdefault("css_cache", {}).get(cid) != css:
                    st["css_cache"][cid] = css
                    itm[cid].StyleSheet = css
                show(cid, True)
            else:
                show(cid, False)
        multi = pages > 1
        show([prefix + "Prev", prefix + "Page", prefix + "Next"], multi)
        if multi:
            a, b = page * n_slots + 1, min(len(rows), (page + 1) * n_slots)
            itm[prefix + "Page"].Text = "%d\u2013%d of %d" % (a, b, len(rows))
            itm[prefix + "Prev"].Enabled = page > 0
            itm[prefix + "Next"].Enabled = page < pages - 1
        return page

    def render_analyze():
        al = st["analyzers"]
        render_list("A", al, SLOTS, lambda a, k: analyzer_card(a))
        done = len([a for a in al if a.state in ("ready", "failed")])
        ready = len([a for a in al if a.state == "ready"])
        itm["AnaCount"].Text = "%d of %d links processed" % (done, len(al))
        if done < len(al) and done == 0:
            itm["AnaBar"].StyleSheet = bar_css(0, pulse=-0.2 + 1.4 * ((time.time() % 1.6) / 1.6))
        else:
            itm["AnaBar"].StyleSheet = bar_css(done / float(max(1, len(al))))
        itm["Next1"].Enabled = done == len(al) and ready > 0
        if done == len(al) and ready == 0:
            itm["AnaCount"].Text = "None of these links could be read. Go back to check them."

    def page_step(prefix, delta, render):
        st["pages"][prefix] += delta
        if prefix in ("A", "D"):
            st["follow"] = False
        render()

    # --------------------------------------------------------- 3. format ---
    def ready_items():
        return [a for a in st["analyzers"] if a.state == "ready"]

    def build_items():
        """Keep per-item choices when coming back; add new ready items with the global format."""
        old = {id(e["a"]): e for e in st["items"]}
        items = []
        for a in ready_items():
            e = old.get(id(a)) or {"a": a, "selected": True, "mode": None, "state": "queued", "job": None,
                                    "path": None, "error": None, "status": "", "placed": None}
            e["forced_audio"] = a.kind in ("music", "match") or not a.has_video()
            items.append(e)
        st["items"] = items

    def available_res():
        res = set()
        for e in st["items"]:
            if not e["forced_audio"]:
                res |= e["a"].resolutions()
        return [r for r, _ in VIDEO_RES if r in res]

    def refresh_format():
        all_audio = all(e["forced_audio"] for e in st["items"]) if st["items"] else False
        if all_audio:
            st["mode"] = 1
        itm["SegVideo"].Enabled = not all_audio
        audio = st["mode"] == 1
        itm["SegAudio"].Checked, itm["SegVideo"].Checked = audio, not audio
        itm["FmtStack"].CurrentIndex = 0 if audio else 1
        for i, (key, _, _) in enumerate(AUDIO_FORMATS):
            itm["AF%d" % i].Checked = key == st["audio_fmt"]
        for i, (key, _, _) in enumerate(VIDEO_CODECS):
            itm["VC%d" % i].Checked = key == st["vcodec"]
        # audio quality: only what the chosen format supports
        qa = AUDIO_QUALITIES[st["audio_fmt"]]
        if st["audio_q"] not in [k for k, _ in qa]:
            st["audio_q"] = qa[0][0]
        # video quality: only resolutions the sources really offer
        avail = available_res()
        qv = [(0, "Best available")] + [(r, lbl) for r, lbl in VIDEO_RES if r in avail]
        if st["max_h"] not in [k for k, _ in qv]:
            st["max_h"] = 1080 if 1080 in avail else 0
        st["q_audio"], st["q_video"] = qa, qv
        st["filling"] = True
        for cid, opts, cur in (("QualA", qa, st["audio_q"]), ("QualV", qv, st["max_h"])):
            itm[cid].Clear()
            for _, lbl in opts:
                itm[cid].AddItem(lbl)
            itm[cid].CurrentIndex = [k for k, _ in opts].index(cur)
        st["filling"] = False
        wav = st["audio_fmt"] == "wav"
        itm["QualA"].Enabled = not wav
        itm["ArtRow"].Enabled = itm["ArtSw"].Enabled = not wav
        set_switch("Art", st["embed_art"] and not wav)
        set_switch("Meta", st["keep_meta"])
        notes = []
        if audio and wav:
            notes.append("WAV is lossless and can't hold artwork.")
        if not audio and len(qv) <= 1:
            notes.append("These sources don't offer 720p or higher, so LinkDrop takes the best available.")
        if not audio and st["vcodec"] == "prores":
            notes.append("ProRes is converted after download and takes much more space.")
        if any(e["a"].kind == "match" for e in st["items"]):
            notes.append("Spotify, Apple Music and Deezer songs are found on YouTube and saved as audio.")
        itm["FormatNote"].Text = " ".join(notes)

    def set_switch(prefix, on):
        itm[prefix + "Sw"].Icon = ui.Icon({"File": icon_path("sw_on" if on else "sw_off")})

    def to_format(ev=None):
        build_items()
        if not st["items"]:
            return
        refresh_format()
        go(P_FORMAT)

    def pick(key, value):
        st[key] = value
        if key == "mode":
            for e in st["items"]:
                e["mode"] = None
        refresh_format()
        save_all()

    def pick_quality(kind):
        if st.get("filling"):
            return
        if kind == "audio":
            i = itm["QualA"].CurrentIndex
            if 0 <= i < len(st["q_audio"]):
                st["audio_q"] = st["q_audio"][i][0]
        else:
            i = itm["QualV"].CurrentIndex
            if 0 <= i < len(st["q_video"]):
                st["max_h"] = st["q_video"][i][0]
        save_all()

    def flip(key):
        st[key] = not st[key]
        refresh_format()
        refresh_settings()
        save_all()

    # --------------------------------------------------------- 4. review ---
    def item_audio(e):
        return e["forced_audio"] or (e["mode"] if e["mode"] is not None else st["mode"]) == 1

    def opts_for(e, index=None, count=1):
        o = {"audio": item_audio(e), "audio_fmt": st["audio_fmt"], "audio_q": st["audio_q"],
             "keep_meta": st["keep_meta"], "embed_art": st["embed_art"] and st["audio_fmt"] != "wav",
             "max_h": st["max_h"] or None, "vcodec": st["vcodec"], "subfolders": st["subfolders"],
             "subfolder_name": st["subname"],
             "duration": e["a"].duration(), "origin": e["a"].url, "matched": e["a"].matched, "name": None}
        if st["custom"] and st["custom_name"].strip() and index is not None:
            base = re.sub(r'[\\/:*?"<>|]', "-", st["custom_name"].strip())
            o["name"] = base if count == 1 else "%s %02d" % (base, index + 1)
        return o

    def format_label(e):
        if item_audio(e):
            q = dict(AUDIO_QUALITIES[st["audio_fmt"]]).get(st["audio_q"], "")
            return "%s %s" % (st["audio_fmt"].upper(), "" if st["audio_fmt"] == "wav" else q)
        res = e["a"].resolutions()
        cap = st["max_h"] or (max(res) if res else 0)
        got = max([r for r in res if r <= cap] or [0])
        lbl = dict(VIDEO_RES).get(got, "Best") if got else "Best"
        return "%s %s" % ("ProRes" if st["vcodec"] == "prores" else "MP4", lbl)

    def review_card(e, k):
        a = e["a"]
        size = human_size(a.estimate(item_audio(e), opts_for(e)))
        bits = [a.platform, human_duration(a.duration()), format_label(e).strip()]
        if size:
            bits.append("~" + size)
        return card_html(thumb_html(a.thumb, a.icon), a.title(), "  \u00b7  ".join(b for b in bits if b), "",
                         maxlen=38), ("active" if e["selected"] else "idle")

    def render_review():
        page = render_list("R", st["items"], SLOTS, review_card)
        for i in range(SLOTS):
            k = page * SLOTS + i
            vis = k < len(st["items"])
            show(["RC%d" % i, "RF%d" % i], vis)
            if vis:
                e = st["items"][k]
                itm["RC%d" % i].Checked = e["selected"]
                itm["RF%d" % i].Text = "Audio" if item_audio(e) else "Video"
                itm["RF%d" % i].Enabled = not e["forced_audio"]
        sel = [e for e in st["items"] if e["selected"]]
        total = sum(e["a"].estimate(item_audio(e), opts_for(e)) for e in sel)
        itm["ReviewSum"].Text = ('<span style="font-size:13px; color:%s; font-weight:600;">%d of %d items selected</span>'
                                 '<span style="font-size:13px; color:%s;">&nbsp;&nbsp;\u00b7&nbsp;&nbsp;%s</span>'
                                 % (TEXT, len(sel), len(st["items"]), SECONDARY,
                                    ("Estimated download ~" + human_size(total)) if total else "Size unknown"))
        itm["Next3"].Enabled = bool(sel)

    def to_review(ev=None):
        st["pages"]["R"] = 0
        render_review()
        go(P_REVIEW)

    def toggle_item(slot):
        k = st["pages"]["R"] * SLOTS + slot
        if k < len(st["items"]):
            st["items"][k]["selected"] = bool(itm["RC%d" % slot].Checked)
            render_review()

    def flip_item_mode(slot):
        k = st["pages"]["R"] * SLOTS + slot
        if k < len(st["items"]):
            e = st["items"][k]
            if not e["forced_audio"]:
                e["mode"] = 0 if item_audio(e) else 1
                render_review()

    # ------------------------------------------------------- 5. settings ---
    def refresh_settings():
        path = st["folder"]
        itm["SavePath"].Text = "~" + path[len(HOME):] if path.startswith(HOME) else path
        itm["NameOrig"].Checked, itm["NameCustom"].Checked = not st["custom"], st["custom"]
        itm["NameStack"].CurrentIndex = 1 if st["custom"] else 0
        set_switch("Imp", st["import"])
        set_switch("Sub", st["subfolders"])
        itm["SubName"].Enabled = st["subfolders"]
        set_switch("Rev", st["reveal"])
        itm["Place"].CurrentIndex = st["target"]
        itm["Place"].Enabled = st["import"]
        if st["import"]:
            tgt = resolve_target()
            if tgt:
                itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">Resolve target: '
                                          '%s &gt; Media Pool &gt; %s</span>' % (img_tag("i_info", 16), B4,
                                                                                html.escape(tgt[0]), html.escape(tgt[1])))
                itm["TargetInfo"].StyleSheet = card_css("panel")
            else:
                itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">No project is open in '
                                          'Resolve. Files will be saved but not imported.</span>'
                                          % (img_tag("i_warn", 16), ERROR))
                itm["TargetInfo"].StyleSheet = card_css("error")
        else:
            itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">Files are only saved to '
                                      'your folder.</span>' % (img_tag("i_info", 16), SECONDARY))
            itm["TargetInfo"].StyleSheet = card_css()
        refresh_folder()

    def refresh_folder():
        path = st["folder"]
        itm["Browse"].Text = "Save to " + ("~" + path[len(HOME):] if path.startswith(HOME) else path)

    def to_settings(ev=None):
        st["return_page"] = st["page"]
        itm["SubName"].Text = st["subname"]
        itm["CustomName"].Text = st["custom_name"]
        refresh_settings()
        go(P_SETTINGS)

    def close_settings(ev=None):
        st["custom_name"] = itm["CustomName"].Text or ""
        st["subname"] = itm["SubName"].Text or ""
        save_all()
        go(st["return_page"] if st["return_page"] != P_SETTINGS else P_LINKS)

    def on_browse(ev):
        d = fusion.RequestDir(st["folder"])
        if d:
            st["folder"] = str(d).rstrip("/\\")
            refresh_settings()
            save_all()

    # ----------------------------------------------------- 6. downloading ---
    def run_items():
        return [e for e in st["items"] if e["selected"]]

    def start_downloads(ev=None):
        save_all()
        sel = run_items()
        for i, e in enumerate(sel):
            e.update({"state": "queued", "job": None, "path": None, "error": None, "status": "Queued", "placed": None,
                      "opts": opts_for(e, i, len(sel))})
        st.update({"stopped": False, "cursor": None, "finished": False, "follow": True, "imported_to": None})
        st["pages"]["D"] = 0
        springs["overall"].snap(0)
        set_glow("blue")
        show(["BgRun", "CancelAll"], True)
        go(P_RUN)
        next_job()

    def next_job():
        todo = [e for e in run_items() if e["state"] == "queued"]
        if not todo or st["stopped"]:
            st["current"] = None
            return finish_all()
        e = todo[0]
        e["state"] = "downloading"
        e["job"] = Job(e["a"].source_url(), st["folder"], e["opts"])
        st["current"] = e
        e["job"].start()
        kick()

    def item_percent(e):
        if e["state"] in ("completed", "failed"):
            return 100.0
        return e["job"].percent if e["job"] else 0.0

    def overall_percent():
        items = run_items()
        return sum(item_percent(e) for e in items) / max(1, len(items))

    def download_card(e, k):
        a = e["a"]
        spin = SPIN[st["spin"] % 4]
        job = e["job"]
        s = e["state"]
        glow, sheen = None, None
        now = time.time()
        if s == "completed":
            label = "Imported" if e["placed"] else "Saved"
            right, tone, state = pill(label, "green", "\u2713"), "green", "ok"
            frac, pct = 1.0, "100%"
            age = now - e.get("done_t", 0)
            if age < 0.9 and not calm:
                glow = 1.0 - age / 0.9
        elif s == "failed":
            right, tone, state = pill("Failed", "rose", "\u2715"), "rose", "error"
            frac, pct = item_percent(e) / 100.0 if job else 0.0, ""
        elif s == "queued":
            right, tone, state, frac, pct = pill("Queued", "gray", "\u25f7"), None, "idle", 0.0, "0%"
        else:
            name = {"importing": "Importing"}.get(s) or ("Converting" if job and job.phase == "converting" else "Downloading")
            frac = (job.percent if job else 0) / 100.0
            pct = "%d%%" % int(frac * 100)
            label = "%s  %s" % (name, pct) if frac > 0 or name != "Downloading" else "Getting info"
            right, tone, state = pill(label, "blue", spin), None, "active"
            if not calm:
                import math
                glow = 0.5 + 0.5 * math.sin(now * 2 * math.pi / 1.8)
                sheen = (now % 1.6) / 1.6
        title = short_title(e["path"]) if e["path"] else a.title()
        subline = (e["error"] if s == "failed" and e["error"] else
                   (job.status if s in ("downloading", "importing") and job else format_label(e)))
        return (card_html(thumb_html(a.thumb, a.icon), title, subline, right, frac=frac, tone=tone, pct=pct, sheen=sheen),
                (state, glow))

    def render_run():
        items = run_items()
        if st["follow"] and st["current"] in items:
            st["pages"]["D"] = items.index(st["current"]) // RUN_SLOTS
        render_list("D", items, RUN_SLOTS, download_card)
        springs["overall"].target = overall_percent()
        p = min(100.0, max(0.0, springs["overall"].value))
        done_n = len([e for e in items if e["state"] in ("completed", "failed")])
        cur = st["current"]
        detail = cur["job"].status if cur and cur["job"] and cur["job"].status not in ("Queued", "Starting") else (
            "Finishing up" if st["finished"] else "Getting video info")
        sheen = None if calm or st["finished"] else (time.time() % 1.8) / 1.8
        itm["Overall"].Text = (
            '<table width="100%%" cellspacing="0" cellpadding="0"><tr>'
            '<td valign="bottom"><span style="font-size:40px; font-weight:700; color:%s;">%d</span>'
            '<span style="font-size:20px; font-weight:600; color:%s;">%%</span><br>'
            '<span style="font-size:12px; color:%s;">Overall progress</span></td>'
            '<td align="right" valign="bottom"><span style="font-size:14px; font-weight:600; color:%s;">%d of %d files</span><br>'
            '<span style="font-size:12px; color:%s;">%s</span></td></tr></table>%s'
            % (TEXT, int(p), B4, SECONDARY, TEXT, done_n, len(items), SECONDARY, html.escape(detail[:60]),
               bar_html(p / 100.0, height=8, sheen=sheen)))
        mitm["MiniTitle"].Text = "Downloading %d of %d" % (min(done_n + 1, len(items)), len(items)) \
            if not st["finished"] else "Finished"
        mitm["MiniPct"].Text = "%d%%" % int(p)
        mitm["MiniBar"].StyleSheet = bar_css(p / 100.0)
        cur = st["current"]
        mitm["MiniStatus"].Text = (cur["job"].status if cur and cur["job"] else "")

    def tick_downloads():
        e = st["current"]
        if not e:
            return
        job = e["job"]
        job.poll()
        if job.done:
            if job.error:
                e["state"], e["error"] = "failed", job.error
            else:
                e["path"] = job.path
                if st["import"]:
                    e["state"] = "importing"
                    render_run()
                    try:
                        msg, st["cursor"] = place_in_resolve(job.path, st["target"], st["cursor"])
                        e["placed"] = msg
                        tgt = resolve_target()
                        st["imported_to"] = tgt
                    except Exception as ex:
                        e["error"] = str(ex)
                        e["placed"] = None
                e["state"] = "completed"
                e["done_t"] = time.time()
            next_job()

    def finish_all():
        st["finished"] = True
        st["done_note"] = ""
        show(["BgRun", "CancelAll"], False)
        items = run_items()
        ok = [e for e in items if e["state"] == "completed"]
        failed = [e for e in items if e["state"] == "failed"]
        render_run()
        if st["reveal"] and ok:
            reveal(ok[-1]["path"])
        if not st["stopped"]:
            play_feedback(bool(ok) and not failed)
        if st["mini"]:
            on_expand()
        if failed and not (st["stopped"] and not ok):
            st["pages"]["E"] = 0
            render_error()
            set_glow("error")
            go(P_ERROR)
        elif ok:
            springs["check"].snap(0.3)
            springs["check"].target = 1.0
            render_done()
            set_glow("success")
            go(P_DONE)
        else:
            reset()

    def on_cancel_all(ev):
        st["stopped"] = True
        for e in run_items():
            if e["state"] == "queued":
                e["state"], e["error"] = "failed", "Canceled."
        if st["current"] and st["current"]["job"]:
            st["current"]["job"].cancel()

    def on_background(ev):
        """Run in Background: a small always-available progress window; the main window steps aside."""
        st["mini"] = True
        render_run()
        mini.Show()
        win.Hide()

    def on_expand(ev=None):
        st["mini"] = False
        win.Show()
        mini.Hide()

    # ---------------------------------------------------- 7-8. results ---
    def summary_chips(items):
        groups = {}
        for e in items:
            kind = "audio" if e["opts"]["audio"] else "video"
            key = (e["a"].platform, e["a"].icon, kind)
            groups[key] = groups.get(key, 0) + 1
        cells = []
        for (platform, icon, kind), n in list(groups.items())[:3]:
            cells.append('<td align="center" valign="middle">%s&nbsp;&nbsp;<span style="font-size:14px; font-weight:600;'
                         ' color:%s;">%d %s%s</span><br><span style="font-size:12px; color:%s;">from %s</span></td>'
                         % (img_tag(icon, 22) if icon else "", TEXT, n, kind, "s" if n > 1 else "", SECONDARY,
                            html.escape(platform)))
        return '<table width="100%%" cellspacing="12" cellpadding="6"><tr>%s</tr></table>' % "".join(cells)

    def render_done():
        items = [e for e in run_items() if e["state"] == "completed"]
        imported = [e for e in items if e["placed"]]
        scale = max(0.05, springs["check"].value)
        size = int(round(96 * scale))
        n = len(items)
        if imported and st["imported_to"]:
            head = "%d file%s ready in Resolve" % (n, "s" if n != 1 else "")
            where = "Imported to %s &nbsp;\u203a&nbsp; Media Pool &nbsp;\u203a&nbsp; %s" % (
                html.escape(st["imported_to"][0]), html.escape(st["imported_to"][1]))
        else:
            head = "%d file%s saved" % (n, "s" if n != 1 else "")
            path = st["folder"]
            where = "Saved to %s" % html.escape("~" + path[len(HOME):] if path.startswith(HOME) else path)
        groups = {}
        for e in items:
            key = (e["a"].icon, "audio" if e["opts"]["audio"] else "video", e["a"].platform)
            groups[key] = groups.get(key, 0) + 1
        chips = "&nbsp;&nbsp;&nbsp;".join(
            '%s&nbsp;<span style="font-size:13px; color:%s;">%d %s%s</span>'
            % (img_tag(icon, 16) if icon else "", SECONDARY, c, kind, "s" if c > 1 else "")
            for (icon, kind, _), c in list(groups.items())[:4])
        note = ('<div align="center" style="margin-top:14px;"><span style="font-size:12px; color:%s;">%s</span></div>'
                % (B4, html.escape(st["done_note"])) if st["done_note"] else "")
        itm["DonePanel"].Text = (
            '<div align="center"><img src="%s" width="%d" height="%d"></div>'
            '<div align="center" style="margin-top:10px;"><span style="font-size:30px; font-weight:700; color:%s;">'
            'All done</span></div>'
            '<div align="center" style="margin-top:6px;"><span style="font-size:16px; color:%s;">%s</span></div>'
            '<div align="center" style="margin-top:18px;">%s</div>'
            '<div align="center" style="margin-top:14px;"><span style="font-size:12px; color:%s;">%s</span></div>%s'
            % (icon_path("big_ok").replace("\\", "/"), size, size, TEXT, TEXT, head, chips, SECONDARY, where, note))

    def error_card(e, k):
        a = e["a"]
        if e["state"] == "completed":
            right, state = pill("Imported" if e["placed"] else "Saved", "green", "\u25cf"), "ok"
        else:
            right, state = pill("Failed", "rose", "\u25cf"), "error"
        subline = "%s%s" % (a.platform, ("  \u00b7  " + e["error"]) if e["state"] == "failed" and e["error"] else "")
        return card_html(thumb_html(a.thumb, a.icon), short_title(e["path"]) if e["path"] else a.title(), subline,
                         right), state

    def render_error():
        items = run_items()
        failed = [e for e in items if e["state"] == "failed"]
        ok = [e for e in items if e["state"] == "completed"]
        reasons = list(dict.fromkeys(e["error"] for e in failed if e["error"]))
        n = len(failed)
        itm["S7"].Text = ("One item couldn't be downloaded. You can retry it or skip it and continue." if n == 1 else
                          "%d items couldn't be downloaded. You can retry them or skip and continue." % n)
        safe = " Your other items are safe." if ok else ""
        itm["ErrPanel"].Text = ('<table cellspacing="0" cellpadding="0"><tr><td valign="middle" width="64">'
                                '<img src="%s" width="48" height="48"></td><td valign="middle">'
                                '<span style="font-size:17px; font-weight:700; color:%s;">Download failed</span><br>'
                                '<span style="font-size:13px; color:%s;">%s%s</span></td></tr></table>'
                                % (icon_path("big_err").replace("\\", "/"), TEXT, SECONDARY,
                                   html.escape(reasons[0] if reasons else "Something went wrong."), safe))
        rows = failed + ok
        render_list("E", rows, 3, error_card)

    def report_text():
        lines = ["LinkDrop %s report, %s" % (VERSION, time.strftime("%Y-%m-%d %H:%M")),
                 "Saved to: %s" % st["folder"],
                 "Resolve: %s" % ("imported into %s > Media Pool > %s" % st["imported_to"] if st["imported_to"]
                                  else ("import on" if st["import"] else "not imported")), ""]
        for i, e in enumerate(run_items(), 1):
            mark = {"completed": "OK", "failed": "FAILED"}.get(e["state"], e["state"].upper())
            line = "%d. [%s] %s (%s, %s)" % (i, mark, e["a"].title(), e["a"].platform, format_label(e).strip())
            lines.append(line)
            lines.append("   %s" % e["a"].url)
            if e["path"]:
                lines.append("   File: %s" % e["path"])
            if e["a"].matched:
                lines.append("   Matched on YouTube: %s" % e["a"].matched)
            if e["error"]:
                lines.append("   Problem: %s" % e["error"])
        return "\n".join(lines)

    def on_copy_report(ev):
        ok = copy_text(report_text())
        st["done_note"] = "Report copied to your clipboard." if ok else "Couldn't copy the report."
        render_done()

    def on_report_issue(ev):
        import urllib.parse
        body = "What happened:\n\n\n---\n" + report_text() + "\n\nOS: %s  |  yt-dlp: %s" % (
            sys.platform, run((ytdlp_cmd() or ["yt-dlp"]) + ["--version"], 10).strip())
        url = "https://github.com/%s/issues/new?%s" % (REPO, urllib.parse.urlencode(
            {"title": "Download failed: %s" % ", ".join(sorted({e["a"].platform for e in run_items()
                                                                 if e["state"] == "failed"})),
             "body": body[:6000]}))
        open_url(url)

    def on_retry_failed(ev):
        again = [e for e in run_items() if e["state"] == "failed"]
        for e in again:
            e.update({"state": "queued", "job": None, "error": None, "status": "Queued"})
        if again:
            st.update({"stopped": False, "finished": False, "follow": True})
            show(["BgRun", "CancelAll"], True)
            set_glow("blue")
            go(P_RUN)
            next_job()

    def on_skip(ev):
        ok = [e for e in run_items() if e["state"] == "completed"]
        if ok:
            springs["check"].snap(0.3)
            springs["check"].target = 1.0
            render_done()
            set_glow("success")
            go(P_DONE)
        else:
            reset()

    def on_open_folder(ev):
        files = [e["path"] for e in run_items() if e.get("path")]
        if files:
            reveal(files[-1])
        else:
            open_url(st["folder"])

    def reset(ev=None):
        st.update({"items": [], "analyzers": [], "current": None, "finished": False, "stopped": False})
        set_links_text("")
        set_glow("blue")
        go(P_LINKS)

    # ---------------------------------------------------------------- tick ---
    def on_tick(ev):
        now = time.time()
        dt = min(0.05, max(0.001, now - st["last"]))
        st["last"] = now
        if int(now * 8) != st["spin"]:
            st["spin"] = int(now * 8)
            spin_tick = True
        else:
            spin_tick = False
        if analysis_busy():
            tick_analysis()
        elif st["page"] == P_ANALYZE and spin_tick:
            render_analyze()
        if st["current"]:
            tick_downloads()
            if st["page"] == P_RUN or spin_tick or st["current"] is None:
                render_run()
        elif st["page"] == P_RUN and not springs["overall"].settled():
            render_run()
        apply_motion(dt)
        busy = analysis_busy() or st["current"] is not None
        if not busy and not motion_busy():
            timer.Stop()

    def on_close(ev):
        st["stopped"] = True
        for a in st["analyzers"]:
            a.cancel()
        if st["current"] and st["current"]["job"]:
            st["current"]["job"].cancel()
        timer.Stop()
        save_all()
        mini.Hide()
        disp.ExitLoop()

    # -------------------------------------------------------------- events ---
    on = win.On
    on.LinkDropWin.Close = on_close
    for _i in range(len(LINK_SIZES)):
        on["Links%d" % _i].TextChanged = (lambda i: (lambda ev: on_links_changed(i)))(_i)
    for _i in range(len(LINK_SIZES)):
        on["Paste%d" % _i].Clicked = on_paste
        on["OpenSettings%d" % _i].Clicked = lambda ev: to_settings()
        on["Next0_%d" % _i].Clicked = lambda ev: start_analysis()
    on.Back1.Clicked = lambda ev: go(P_LINKS)
    on.Next1.Clicked = to_format
    on.SegAudio.Clicked = lambda ev: pick("mode", 1)
    on.SegVideo.Clicked = lambda ev: pick("mode", 0)
    for _i, (_k, _, _) in enumerate(AUDIO_FORMATS):
        on["AF%d" % _i].Clicked = (lambda k: (lambda ev: pick("audio_fmt", k)))(_k)
    for _i, (_k, _, _) in enumerate(VIDEO_CODECS):
        on["VC%d" % _i].Clicked = (lambda k: (lambda ev: pick("vcodec", k)))(_k)
    on.QualA.CurrentIndexChanged = lambda ev: pick_quality("audio")
    on.QualV.CurrentIndexChanged = lambda ev: pick_quality("video")
    for _p, _k in (("Art", "embed_art"), ("Meta", "keep_meta"), ("Imp", "import"), ("Sub", "subfolders"),
                   ("Rev", "reveal")):
        on[_p + "Row"].Clicked = (lambda k: (lambda ev: flip(k)))(_k)
        on[_p + "Sw"].Clicked = (lambda k: (lambda ev: flip(k)))(_k)
    on.Back2.Clicked = lambda ev: go(P_ANALYZE)
    on.Next2.Clicked = to_review
    for _i in range(SLOTS):
        on["RC%d" % _i].Clicked = (lambda i: (lambda ev: toggle_item(i)))(_i)
        on["RF%d" % _i].Clicked = (lambda i: (lambda ev: flip_item_mode(i)))(_i)
    for _p, _r in (("A", render_analyze), ("R", render_review), ("D", render_run), ("E", render_error)):
        on[_p + "Prev"].Clicked = (lambda p, r: (lambda ev: page_step(p, -1, r)))(_p, _r)
        on[_p + "Next"].Clicked = (lambda p, r: (lambda ev: page_step(p, 1, r)))(_p, _r)
    on.Back3.Clicked = lambda ev: go(P_FORMAT)
    on.Next3.Clicked = start_downloads
    on.SettingsDone.Clicked = close_settings
    on.CustomName.TextChanged = lambda ev: (st.update({"custom_name": itm["CustomName"].Text or ""}), save_all())
    on.SubName.TextChanged = lambda ev: (st.update({"subname": itm["SubName"].Text or ""}), save_all())
    on.Browse2.Clicked = on_browse
    on.Browse.Clicked = on_browse
    on.NameOrig.Clicked = lambda ev: (st.update({"custom": False}), refresh_settings(), save_all())
    on.NameCustom.Clicked = lambda ev: (st.update({"custom": True}), refresh_settings(), save_all())
    on.Place.CurrentIndexChanged = lambda ev: (st.update({"target": itm["Place"].CurrentIndex}), save_all())
    on.BgRun.Clicked = on_background
    on.CancelAll.Clicked = on_cancel_all
    mini.On.Expand.Clicked = on_expand
    mini.On.LinkDropMini.Close = on_expand
    on.OpenFolder.Clicked = on_open_folder
    on.CopyReport.Clicked = on_copy_report
    on.DoneBtn.Clicked = reset
    on.Skip.Clicked = on_skip
    on.Report.Clicked = on_report_issue
    on.RetryFailed.Clicked = on_retry_failed
    on.Insta.Clicked = lambda ev: open_url(INSTAGRAM_URL)
    disp.On.Timeout = on_tick

    # ---------------------------------------------------------------- start ---
    if IS_WIN:
        itm["OpenFolder"].ToolTip = "Show in Explorer"
    itm["Pages"].CurrentIndex = P_LINKS
    refresh_format()
    refresh_settings()
    itm["CustomName"].Text = st["custom_name"]
    itm["Ver"].Text = ("Updated to v%s" if globals().get("_LINKDROP_UPDATED_FROM") else "v%s") % VERSION
    refresh_folder()
    urls = read_clipboard()
    set_links_text("\n".join(urls) if urls else "")

    win.WindowOpacity = 0.0
    itm["Pages"].CurrentIndex = LINK_PAGE[st["box"]]
    win.Show()
    lock_size(W, H)
    springs["opacity"].target = 1.0
    kick()
    disp.RunLoop()
    win.Hide()


# ----------------------------------------------------------------- icons ---
# Platform logos (from Simple Icons, CC0), embedded so updates carry them.

ICONS = {
    "i_gear": (
        "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"
    ),
    "chk_on": (
        "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"
    ),
    "chk_off": (
        "iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAAEpklEQVR4nOyZ60+bVRjAn/O+bykttIUWeuEyylVKAdmsyCgM"
        "IoE4k2micSYav/gFN6Mx/iF+UGey+FljiJp5W3TLNhiChC2TQWkplxTKpV1tgd7o/T2eU8IGyzZYKNAm/D71nPOe5pe3T5/z"
        "vs/DQIbB7eWiL4axXARQBQi0PAYJgyCHfBbAfsAQI98RwAj8KAELTBCsvd3Iu9s2BLuIChF0hgNenc0ypves2Bsi4Q1lPBqV"
        "8TwvhH3AMEyEy8ryCrPFLoX6xES5vmlSnCMz4wT0X2xHa/C8wpdGcFtgzd1lHh3octimW9yORcG62wmhgB8ioQDwiQTsS5hl"
        "QSjKBbFECrICFRSoS2Oa8pqRuuaOG7mKgusfN6Nh2Ivw5btYHI/Cm3Om0a6pu4Pv20z/ZjsWpuEw0JS/AOV1J8O1Lxm/r2x8"
        "5VoEw6+ft6LQ9mt2xHBfH2bdkcRb9waufrA4M9lj+ucGbPh3DauU4bBZwet2Zsdj0Q+9q66iU53nOOL0w/nzKPFE4f9K4XXL"
        "6NDZpRlLz/3bfwKJVzhs6A26P/gX+enRayKxdB0ZOtbJ9B/wuPDXg7jGbhk7O2++96559NaRyG4RCQXBfGcABELhO6IciffL"
        "ITz1qRHN0TVm6yKGAaN9eqLdPjPB+tc9cNT419ywNGNml2YnjRwLp7fmk8Jf/Y2L/D73Cc+DFf3ijBnSBXLzwONy6L0eV9W3"
        "I1hF55LCDAcV85axBrdjAWF+f+kqldDU6VlZQHbreH0CQQWd24zhBKg9jsWmNecypBurrhWgbuRAUdNxUpgkY0k4GCgO+tYh"
        "3djwrgF1QyxI6HhTmAEJBszFYhFIN6LRCFA3+gxDx5shQR5kMI85zPOQbtD/FHUjUZBNxwxkGMfCB83DoxljDJnAcUgcNBko"
        "jDGCDOI4JA6azBN+lH2P8/CBcCx80DAIIYx5SJDSURwx6edPS1qkDhcmjzrR5Dg5S6qILCvwZwlFkG5QJ1I09CEMQTpOChP7"
        "oCgnd1kszYN0I0eWD7TCyaNtwqTe65AqlHNyZRGkG/mFRZCn1FhJ2DroOCkcT8C0RlttVZZogeX2V6dOJZwgCwqLy6CkQjcV"
        "Z8FK55LCn7ShFVVJxbhUXmgtrdJDulBaUw95BSqTXF1s+qwFPaBzj9IChv665s4rJdV1cUl+ARw15OZBcWVtvKGt+2dS6Lm5"
        "Nf9Q+EIrWlZqtNe0upM/6l4+A0eZMcifDHSGDtDWnurLk6tvXjAi19bajvrwR61w6zLXoQoFfXKSOnomSEE7FPDBYSKWyKDh"
        "dBcJB/3V+pYzv/c2w+2L29Z3CNNDhLQMfjG8eo6TKZRLJOjfs1lIy8B2OC2Doopa0OqawrWGtu+qGpv7NxJwhTrtcHzSRiIt"
        "4OPwxqrL+aJp6Prbbudince5BKlsytBsJBSJQZS72ZRRqEpoY8Zcb+z+Ka9QPUaWf+s1oNjj+575enRpGFeR09pgs4y3rzrs"
        "lQGvpywaCctT1vYSCHxZ2SJPjkxuV2jKZst1jYOk9HuHiM49bd+e3ue+Gcf5KADVmIUyUvaUprSxyIMfczCPIjD7rP7ccwmn"
        "E/8DAAD//3ChuUkAAAAGSURBVAMAoeLbrFR/4tcAAAAASUVORK5CYII="
    ),
    "i_paste": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAEvUlEQVR4nOydy4scVRTGv9MjM/0Yo5hFfCxEIWaSCKJgxCxU"
        "3AUDA4oR40ZXLiQIZqOCL4wQ5i/IzoWCkI1BEmV8IC5EE0FEiW8IitGgREKYruoeZvrkqwyByUymem5VdZ+qzvnBMNV9q2ru"
        "Pb/76tvDrRocU2pwTHEBxrgAY1yAMS7AGBdgjAsw5hoYE0V6vwqeVmArFNtEsBEDRBVnIfhRwJ8xvN0cl+MwRGCEqo7HXRxg"
        "QPbDriUu8memWcfrIjIPA8wEtCM9wb9+L8qA4kSrKffBAJOa1471QGmCnyDYwTy9AQOG3gLa83oPG/43KN8EoKc13D05Id9j"
        "iAw/CIvYh3LOvmrSw3MYMsMPhGIKJYUzsaHnbfjTUMGdKCsGlaPQMYBTyw1RB3tYkGnO7e/gzW/m25Mh92g1ZKDjEgdbDbsC"
        "c7zgtCh+Y7Te55T1MKescyiIQgo7p7oJMV5k6J7lywZyUEIBK4lYuQ61JnCQIv5DTnIXtt3Vae3hHd7oWhRABQRchDc5z5w+"
        "2arLh8hBrkGYywj70cORooJfJVjmDbRwLOroPuQgc22LY32qB7yLgqlKC1gOx4e9zaa8hwxkKmy3q9sWejiJAVBFAQljgql6"
        "XX5BIJm6oIVFvAnnMhYUmZYygmtbpHqLdvAXBkRVW0ACp6ibODP6N+Sa8A9iXTzW9xzFB4ziTKOBk8zQueVJXAX9h9pvXOvS"
        "QQaoL4ozXBW96bK3VK+PY2xnpl5ivh9Juzzq4lH+OoQAgrsghmdP+gk4ykJMc1D6cmXwLyL4DmVF8O2qt1iGpCws026+TJ1y"
        "MjZPIJAsY8BdaYms+W/1SS+vAKwWsBzOdtLHPk2PzRXviUD6dRHsBydZa9prpXP6eiunr7/ycBzlImaAp1jb/1zrBHZHN3Cp"
        "5WzKPeY5hk0ggMJXQ9OCn9BoyB9U+BpKBvP0alrwE1i2/5FOcKUqvAWsdxbDwfhr/nWTrwGvwFfM9871nFhU+S9h9sVIs4EH"
        "mNUZHvZgxyKjdZDd5kMwwqwFXKJq/5ZSdPnNBVSNkemCnCVcgDEuwBgXYIwLMMYFGOMCjHEBxrgAY1yAMS7AGBdgjAswxgUY"
        "4wKMcQHGuABjXIAxLsAYF2CMCzDGBRjjAoxxAca4AGNcgDEuwBgXYIwLMMYFGOMCjHEBxrgAY1yAMS7AGBdgjAswxgUY4wKM"
        "cQHGuABjXIAxLsCYLAJS9wNKNjXCiLKOsgXvKZ1FwJm0xDjGFowo/cqmwN8IJFyA4of0ZLyMEUUFr6SliyL46RvhAmr4JDVd"
        "sLsd6dEo0p1ssteh4rAMG5OytGM9xpe7Uk+u4VMEEry3T6ejWxYVP8NZxZhgc70uv4dcE9wCkv2R2c18Bmcls6HBT8i0u1Uc"
        "62094CceBm3ROMLEUsfmpshpBJLpc0CjIac44DwDZ4ka9mYJ/tKlGUn2y6eEF3CVI4LnWxNyBBnJ/wiTju7i3PMwAh/WU3WS"
        "R5gweo9P1uVj5CD3UkSrLh+xJWzl4SyuHmZriu15g59Q6BaTHJwf5OD8MA93sIbcnuUxViVk6TFWwCkeH2eN/Zxj4BcoiJHe"
        "47MK+GqoMS7AGBdgjAswxgUY4wKMcQHGXAAAAP//LC61aAAAAAZJREFUAwBa9GVeNEa8tQAAAABJRU5ErkJggg=="
    ),
    "i_next": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAADLUlEQVR4nOycy46MQRTH/9U9Y/oisdEmNjYWgvEILrGwmeAV"
        "PMUkVnZewhNY2LnshJBYEyGzEbEYzMwKfQu6nCMtEcQ3mTLOpc5v093Vteg+v6+qvsup00IgSguBKCFAmBAgTAgQJgQIEwKE"
        "CQHChABhQoAwIUCYECBMCBAmBAgTAoQJAcKEAGFCgDAhQJgFOGI6zSv8urSUnsMICQ4YjfKRnHCf3h7lzxlYb2Vc6PXSGyjH"
        "vIBPOS9jjKcpYfnn9pzxfrGN8zQaXkAx5teANMaNX4P/vZ3aPn/FA5qWjkMxpkfAMOfDmGDjb31oJGwttHC600nrUIjpEdCe"
        "otvUh0bC4MsMjyaTfAwKMS2AjupXtOA2nvHMJTym6egElGF+DWgDF3maaepHEg5qXBPMC+h202ue43coYUASHmqajlxcBzAc"
        "VJ7rOchNfTUtzG4EMBYluBLAWJPgTgBjSYJLAYwVCW4FMBYkuBbAaJfgXgCjWUIVAhitEqoRwGiUUJUARpuE6gQwmiRUKYDR"
        "IqFaAYwGCVULYKQlVC+AkZTwTwQMp/kyZlijH3eS/sQBOIclpBau9DvpLgopFjCc5FVk3EaNJKyWSih/JJlxFbUywzUUUj4C"
        "xvkjvexHpfQ6GKSUtrFLikcAzYdTVMwI2IcCigXQoqsy4+x/wDlJ/ZQ2UED5GpBwHZVCB98aCikWQGcBd0jCJXr7hI6ID6gA"
        "mnY352dA91BIXIhB9kLM1Q6Z3SB9K6JqARpuxlUrQMvt6CoFaHogU50AbY8kqxKg8aF8NQK0pqVUIUBzYpZ7AdpTE10LsJCc"
        "61aAlfR0lwIsbdBwJ8DaFiVXAixu0nMjwOo2VRcCrAaf8VIv6BkF/1BTXwr+9mIbZzXVEDI/ArheEHYW/K158F9CEe7rBTGa"
        "awa5rxcUBZv2kKZ6QfM5/5zW4DMu6gUh491vX2S8peCfiaJ9ewzXC+p1cYoCfutHG42Km9S2om3B/ROu8oL4lLRP8U8pbcII"
        "kZglTNSOFiYECBMChAkBwoQAYUKAMCFAmBAgTAgQJgQIEwKECQHChABhQoAwIUCYECBMCBAmBAjzDQAA///X/5RiAAAABklE"
        "QVQDAPai8TtzP5LDAAAAAElFTkSuQmCC"
    ),
    "i_back": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAADQUlEQVR4nOycu24TQRSG/3GCbK/TQbg0NBQRKDwCiIomUiRe"
        "hZKGBvEOSLwABR2XDoGAAiE6LgoNEkhcAgIhYeMI8HAmsRVHju11ZnfPjOf/Gie2C/v/ds+ONXtODUSVGogqFKAMBShDAcpQ"
        "gDIUoAwFKEMBylCAMhSgDAUoQwHKUIAyFKAMBShDAcpQgDIUoMwiImJry666x3rdvMScYBABnY49aQ0eyJ+n3P8W2KhZXMwy"
        "8x6RE3wJkqP+TA94jn74DjlqVkTCM2vtUURO0AIk/NN//uGhMRgN2uB45zduIHKCLUHdrl3528NjCX950vuyBpaNMd8QKUFe"
        "hPOG72gDC4iY4EqQq/kS/pM84cvV+MWSMV8QMUEJGKr5R6a911p8lfp5CZETTAlyZUfCf5TnyHfhL9ZwrtEwHxA5QQiYpeYP"
        "hb+BOUBdQMrhO1QFpB6+Q00Aw99BRQDD36VyAQx/L5UKYPijVCaA4e9PJQIY/nhKF8DwJ1OqAIY/ndIEMPx8lCKA4eencAEM"
        "fzYKFcDwZ6cwAQz/YBSyKd/u2nXbw81c24iRIwfPT/meryS5662GuQtPvAVI+GuyN3sHKWKwJhLuwQP/PeEeriJVLK7AE68z"
        "oG3tCXTxEYki5ej7UmYOwwOvM6DWRQNpY+GJl4Bm07yTT/AWiSIXY+9VnPc1QD7EZaSKwTV44i1geylmsO7W9kgA+Z4/5OFp"
        "fwV0H54UdnMuf4gdjELvjqaE2Sn89nRKmI1S+gMoIT+lNWhQQj5K7ZChhOmU3qJECZOppEeMEsZTWZMeJexPpV2SlDBK5W2q"
        "lLAXlT5hSthFrVGbEnZQ7ZSnhABGFaQuIYhZESlLCGZYR6oSgpqW4uZE9Lvl84wq2Gw1cdYYs4mICWpWRL1uXh9awPk825tu"
        "hhDnBZXELOUo9nlBQU7McrXd1fg8ZwLnBZWEkyDl6IJIGH90c15QuQyuCRL055EXLT7Nw7yg4KcmioQ3max2JPDbg+cscEue"
        "W82y+OcFRTE3dMAva4+1JP/Yl57DRCVgHuHsaGUoQBkKUIYClKEAZShAGQpQhgKUoQBlKEAZClCGApShAGUoQBkKUIYClKEA"
        "ZShAmf8AAAD//9n44FYAAAAGSURBVAMAuHnzJ68fWdEAAAAASUVORK5CYII="
    ),
    "i_folder": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAELklEQVR4nOycy4tcRRTGv9MzOre7hyg+wZ1CjDoLcSOoaERX"
        "IUFXaqIrd7qK6EYFQXEhuHKnf4AgZqNooowPNDCBZJv3C7JKmBDynu7bQ2bm5FQey1TV7e7J6b7z/TbTPffcmb71O3Wq6l66"
        "GiCuNEBcoQBnKMAZCnCGApyhAGcowBkKcIYCnKEAZyjAGQpwhgKcoQBnKMCZSQyAqq7r9vA2FG+q4EkBHrNfT2MAVHHe/s6c"
        "NvHBtMg8ao6gDxZUH0WJT0XwEVYLxfzkBF6fmpLDqDGVBXR6utka52d72cYqY73h3GQDLxeFHENNqTQGdLv6iTX+TtyFxg9Y"
        "D3t4aQVzi4v6DGpKdg8oS31vBfgRDtS5J2QJsAycsUw8CEeChHsm8GrdxoSsErS0jK/gTChH15bxvyXD06gRyR5gU81HbKo5"
        "jz5nTMOmbuUouQ4oF7EVqcZX/GYB3zabOCQil9AHYaC1DN9tmf5QLC70hGXF0U6pcGLB/vNpUZywVvmlVWCHXfMC+iSZ1Xah"
        "c/bjpTsGKH5vt+QNDIFcCSNGz3rl9+0mvjER51CRHAFXEVndWia80GrJXgyJMZVgeYgr9pm3tQv5o8p5OYNw9NaClZ0jGCJh"
        "lmOznY2h1mOMsExeZxZ2dXu6vcp5A9+Ms253GUMmSAgD7bhJCNhn/s4WrNty43NKUHS0azdl1WZH41qOAhOCp3JmaiN9O/pW"
        "OXrFUuAsxowlxZc5cSP/PMAkHJEmngv3oEzERYwJVha2hjVURlwczxI0ilij3l+WmLFG+cxab3M0WPChzYp+iIXwiVhFwkLT"
        "pt17bO2zxd5Gp5yWuu8gAQUMgK2Bvo4GKJ5FAgoYAFsDHY8dt+KcfG5CAQNg5ehCIuTexHEK8IYCnKEAZyjAGQpwhgKcoQBn"
        "KMAZCnCGApyhAGcowBkKcIYCnKEAZyjAGQpwhgKcoQBnKMAZCnCGApyhAGcowBkKcIYCnKEAZyjAGQpwhgKcoQBncgR0YgdV"
        "9QGsUTKuPbmHRI6A6MZ5ZYkNWKOkrl2BM0iQFqA4ED+Mz7FGUcEXseOi2I8EaQEN/B09LtjS6erObldftC55H2qOXeOD4Vo7"
        "pe6yt5uiwQ38gwTJ7/j2eroh7M8DUpkJwfqikJOxmGQPCPsdWJn5F6Qqs6nGD2R9y70s9fEV3NiWZgokh1IKrG+JnE4FZq0D"
        "mk05ZQPK+yB5NPBuTuPfDM2k1ZKfTMLHIFFEsL09Jb9mx6MinZ5usrnnDgy4SXfdCFuWWWu+NV3IX1XOq3wrol3In9YTwt6d"
        "syC3mW0oZqo2fmCgrWZscN5og/Nr9vJ5y4AnhrF9/Rhwc9tK4JS93mcZ/J+NkbvRJ2tqr59RhHdDnaEAZyjAGQpwhgKcoQBn"
        "KMAZCnCGApyhAGcowBkKcIYCnKEAZyjAmesAAAD//5WePIAAAAAGSURBVAMAMz0s+2sRCtgAAAAASUVORK5CYII="
    ),
    "i_copy": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAEGElEQVR4nOydy2vUUBTGvzsWO49SxYKCO4VapQt3LnQhuqtV"
        "uvJBXbnRnYJuVHChIogbt/0DBLEbpVSkPlDBjVsf+MSuKgVBRTrJDLS9nqGFPqDp3CSTL8mc32bSuUm5yS8399x7kkwBCpUC"
        "FCoqgIwKIKMCyKgAMiqAjAogowLIqAAyKoCMCiDTgRix1nZ7NZyAxZA12GWA7fJ1F7jMWGDKWHyDwcNyEaPGmBmkBIMYmLF2"
        "G3xcNgbn5M8S0o0nJ8dIpRO3RcQvkIksoFqzg3LGP5DFCjKEtIp/svfHu4rmKYhE6gM8z16SPRlHxg5+AznzuuWyNOHV7HkQ"
        "Cd0CfN+engfuIQeIiOFy2dwHgVAC6nXbPzuPD8gRGwx2F4vmCxIm1CVodg43kDNmLa6DgHMLkFBzq4Sa04gpgkoRVkLUHomM"
        "/iBBnMcBfh2nsN7BtxiTFe6USvgoO/QXROSE2ez76Jeo54rUejBgVePVcVI+R5Agzmdx1bdv5OPAmitIVFQpm2NIIVL3x/Jx"
        "ZK1ykfSqq2QOIUHC9AF7gwrF6C2kFIl2bgauYIP3rRWEERA4tSCXnfdIKVK3r0HlMpJPfDwT+2ScXPOrSClSt9/rrLIRCaOz"
        "oWRUABkVQCbWfEBU0pBPkFDVrvqqpfmEMOMAG1ReKRnn/9nO+QS6gHbPJ1D7AM0nEFuA5hMWt4UjcQjQfMISlEuQ5hOWSLwF"
        "aD5hJYmPAzSfsGojOBK1BWg+YSWMPkDzCctgCNB8wjJSNxnXbvkEnQ0lowLIqAAyKoCMCiCjAsioADIqgIwKIKMCyKgAMiqA"
        "jAogowLIqAAyYQQEztdLDnULlKYJI2A6qFAS2H1QmsZdgA1OGUpi+iqUpnEXUMCzwHKDo1XPjnue3S+Xo01QAnG+LaVWs31z"
        "Fp/RIsLc3p4kcd8d7twCGvc/Sg1eQImFUGFoh8FZKLEQSoC0gh+NW7KhRCb0QKxxP7xIuAglEpFGwiLhrnTjQ7KYmpfgZY3I"
        "UxGVohmTlrBHFiegOBNryOf79uA8cFgW90mktDPMY6btFoYmvrOteMw1SejjACVeVAAZFUCGISCz+YQm6uYcjjMEZDafsF7d"
        "pHf+CUeSF5DhfII1uBZULuOhd3AkeQEZyydIHXoadVl8QnIgcOUCnsORxGPuVucTmGww6JWJyu8u2yTeAnKcT5hwPfgNKKNO"
        "mbLYIVMWn2SxE/nAN0X0lo2ZgiOUcUCpZCalwzqDvFDAcJiDv7ApibzkE2Tm50Kl0zxCSOgTX9WaHZDYcxT8H/txIhevLGsg"
        "+YQnGcwnTBQs+uP4/ZlUTf3GkU9oAQuvrQQmZfmtnLEvpQ97jZjI20uTMofOhpJRAWRUABkVQEYFkFEBZFQAGRVARgWQUQFk"
        "VACZ/wAAAP//AO8LBgAAAAZJREFUAwDklokhsMlKHwAAAABJRU5ErkJggg=="
    ),
    "i_close": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAD0klEQVR4nOycy04UQRSGTw0YmMElF924ceElGPcaL3HhzoWP"
        "Z3wCF76Cgrp1oTEaNsZoooLEsJBbvJTnJzMRB4auhur+T8H5EjLQ9KRPna+7qqfrTHXEodIRh4oLIOMCyLgAMi6AjAsg4wLI"
        "uAAyLoCMCyDjAsi4ADIugIwLIOMCyLgAMi6AjAsgMy4Nsr0d5/E6MRHeSIG0EX+QBtjYiOdikCf663n8HUWWOlHu9nrhoxRA"
        "m/FnF/AjxjnZlFchyNzu7THK8qkxuaNn01sxjJ71l3/+lqca/+x//4jytdeVqyGEFclI9jEgbMrD4eTvbNdt2rAFbeAlMQpi"
        "Q4x7kg+CnNnYlAeSmaxXwHqMZ2VLPh+0j14J38Y7cmNyMiyJIba24oVff+S5Jn/moP16kzKjV8GqZCLrFTC2Ld2qfdBANBQN"
        "FiOkJh+sazMlI1kF6Fn9XgesyjuGvoQX6G+FDGJALCnJ13Hg5ekQliUj2ccAPT3uoZup2k8bPM0eE3b1+dNV+6JN2l/fl8xk"
        "F9Dthg/o4xMlzGgCFhndEY6JY6ec+YNxS29DP0lmGvkcAOr0q20PzJZia0wAsCjBWkyNCgCWGmzxhGhcALDQcKtdYisCADMB"
        "lsej1gQARiIsJx+0KgC0mRDryQetCwBtJKaE5AOKANBkgkpJPqAJAE0kqqTkA6oAkDNhpSUf0AWAHIkrMfnAhABwlASWmnxg"
        "RgDQx8MX9Qnls9RE6hzzTX2NpSYfmBIA+pPii6nP6PGamPxVFXbLWlGAOQGgTpeSgtV5aGBSAMglwXLygVkB4KgSrCcfmBYA"
        "DiuhhOQD8wJAXQmlJB8UUR2tiR/TU2Ut/Q2ytvOeAvAuiIwPwmT8NpSMfxAjY24Q7tdqpj7b+Y6fqv0GBcEWalGHMSWg/zBu"
        "ocbDuOt6Zl+rUQa5YKkqG/jjaDI+IUPGpyRP8pSkT8p7WcrJLEvxwqx/eGlipmMdFi/ObeCYdfDy9IaPXYV/QaPFGPbDv6JE"
        "imWAf0mPHJN/TVW4sTUioITbPysxNrVe0Ot9l3wZgl0uWLMMcmWqK1dKWS8oJfk7xbXMWk0cu1/gmzKfMHss1gsC1p7J1+mO"
        "il8vyOL8LGJJXWBkveT1gvp9/m2Lk+OICbEhxpE7lbJeEBa42/OPKF/YfX4VgzFhVPzFrBfU07sFDfjxYJteFY9027w28J0Y"
        "BzGOir+o9YIAbkmnNP7ct25t0Ub8RVRHH2d87WgyLoCMCyDjAsi4ADIugIwLIOMCyLgAMi6AjAsg4wLIuAAyLoCMCyDjAsi4"
        "ADIugMxfAAAA//8jntnLAAAABklEQVQDAF2TtXZQhDRMAAAAAElFTkSuQmCC"
    ),
    "i_retry": (
        "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"
    ),
    "i_chat": (
        "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"
    ),
    "i_bg": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAEZ0lEQVR4nOydu4sUSxTGv5q93J3p2eu9XOFeMFPwxQZmBhqI"
        "Zr4w8h2ZaKagiQoGKoL4F/gHCKKJIoqMD1QQwdQHvhEERRBUZLd7FtY9nlYTg62qnu6a09WeXzK9W2dmuuurx6mP7poWFFFa"
        "UERRAYRRAYRRAYRRAYRRAYRRAYT5AzWCiOakfWwBYRMZLDLAPP73GOrFBAFvDeEFDC4kbZw3xkxgQAxqwATR/8hw0Bjs4T87"
        "iIuUG8vp7ihOshAfUBBxASb7tJ5b/Dk+7CJiuFd84drcPNY214q8T3QOSFM6wGd+GZFXfg635Dk8LPXSPu0t+D4Zsox2zgBn"
        "0EBYiB1JYs56xUKAqSkan57BIzSYEYMl7bZ55ooTGYKmv+IYGs404ahP3NB7AKea/3Gq+R41ycACQpyizuXM6JMtaOjrgGwK"
        "2+CqfMIlDjjV6eAxX8Bn1AhuQP9kGcY56znEV7HeEmrSKWzl19O2zxt6K5zM6C6/rJw1gLOibmI2IgL4Wq7wy7rZylmk22Md"
        "s9r2GRJzwDJbIbeIE4gEznaOWwPIfq05EgJYrQUedh4iEvhcn9vKeWXvXN/UzozjMX8SkcDn+tER8qejXN1QaVQAYVQAYUqt"
        "A0L495zaEcpRqV8fmoHWAZH596X8eheuBtPtGGsdFxYgVv9+UL/eRVkBCs0BMfv3g/r1ofHuAU3y74v49S6GMgQ10b/39etd"
        "DGUIaqJ/7+vXh8bZAxrs33v59S7K9gDnOiA2/75qvz40zlYds39fhV/v8R3B54Bo/fsq/PrQ+AgQrX9fhV8fmtJmXJ39+yr8"
        "+tCoGyqMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMCiCMClACIvrXEeJ8LkEF"
        "KEGWYbGtnIB3cFCrHbNigwyO2MoN4QEcaA8oCA87c9OUVvy8626tNbiFG3Dw2/eAos+kpX1436Y8AhVAkl67bV65glSAMPR5"
        "/N/tE6gChKCF7cmoeeMTqgJUjDHYx5V/0TdeBaiI/DFYrvxtSdtcLfI+TUOrIH9CqI2l3YKVn6M9oDg/tkIAXvPxfZ5sryWJ"
        "uYcB8REgv/9/1gcZcj/E4z58EfJz+563z85Et2P+giA+Q9B7W6HLD5GkCq8mNG4ByP4IEl/EYdSUKrya0LQ8Iq5byw02TKZ0"
        "OfdHuMv/DWGq9mpC43Q1+n1a/JXwFA1kxGAh2wUvIYizB+T7KfAwcxPNoydd+Tlevl6W0fwZ4AkfjqIZZJy3L0yMeQthvBZi"
        "nY55zRPWLjSFFnbUofJzvFfC+f46LMJ+RE7u1XQLeDWhGWTLsrWce55H/X5cx0qoLcvKUtgLyv0O7glL+bCHeOi1CON1q/yc"
        "UnsA8eS8iifnNXy4nFvYgpr87NQvXg23sFs8h91BTWn6jyjUHrWjhVEBhFEBhFEBhFEBhFEBhFEBhFEBhFEBhFEBhFEBhFEB"
        "hFEBhFEBhFEBhPkGAAD//+TFA5gAAAAGSURBVAMAgUB/JXC+ho4AAAAASUVORK5CYII="
    ),
    "i_expand": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAEQ0lEQVR4nOydy2oUQRSG/0qiySSCIEYFF4IgGhPJxpXgZScY"
        "g/gerlyIxoUr9Rlc+AAuXHlbuNAowZU7jURQIyqKBq+ZG3FSnoo9mHGu1dPTp6r6fBB6Mt3JJP9Xl+7qqpk+CKz0QWBFBDAj"
        "ApgRAcyIAGZEADMigBkRwIwIYEYEMDMAITblsp4w28FB9QwxUQiQfFmfwirOaY1xpbAZvWeRkjwzMqTuwJLgBORLegoat8GB"
        "whRJuGvzI+H1ARoXwMUqLsGS8GpAUf+izSYwMTyEUaXUUqfHB1cDqN0vg5Ei9ck2xwcngDrdBTAyrNQHm+PD6wMUrsAjghOw"
        "diqoME0Pn2jgJxwnyOuAbimV9N7fq3hMzdkoLBnJKatM5Ur4P7oJPw4iYB1ph28QAREc4RtEAPjCN2R+OJpGNPd3Gj5d5H1F"
        "wmRagCn5KxU87DD8LwN9OISEyawAm2YnCv/w0JBK/Co7k32AK+EbMifApfANmRLgWviGzAhwMXxDJgS4Gr4heAEuh28IWoDr"
        "4RuCFeBD+IYgBfgSviE4AT6FbwhKgG/hG4IR4GP4hiAG49IMv9WQdJzhau8FaK1HKfxHnYa/oR9Huyn59Dov4uxrhvcCCkVc"
        "p398W7vjquEPDirrkGpQuNpin/WcJK+npRS03qlLeN/uuMTCj8iX9ElozGhgnLYVKgDzJnzbmdEGrwVQ27+7ovGq1TEudbiN"
        "8LoJolBfUylsujqFwl+ikn/M1fAN3vcB/cA0NQOf6nZofKTwj1CzMw+H8UqAWZNVXZdVJZdTi8M5HKDAb1afo1pxg56bSKrN"
        "7yVe9AHFot5VAe7TH7vHfE8BL1DJP07hv11/3LLW20dot1LqMzzBeQFm3s5KBQ/qTjWp2aFSPulT2I1wugmi8MeieTv15/kK"
        "O+ga4Bo8x9ka0Onwgu2aLNeIVQPMOtx8Uc8tF/R382Uery0PTQib6YIFYCM8xroGRFeBt5r8thN0NXgPXRC1+bMU/ta2B2s8"
        "HRlWB+Ex9jVA43yLfRfRBeva/LbhmytcKj2n4Tlx7gdMNttBoYwhJtFE2VnLIeV38Jw4ApougqbwtiAGvt5MSQL2O2JZDt/A"
        "KiDr4RvYBEj4f2ERIOH/I3UBEn4tqQqQ8OtJTYCE35hUBEj4zem5AAm/NT0VIOG3p2cCJPzO6IkACb9zEhcg4dthf0OmSLG1"
        "YG2cvrPwl6Lpgk7P2+k1idcAm5JP4We25FdJfShCmp1aUhUg4deTmgAJvzGpCJDwm5P4WVDS0Kv9oI79ebQAwvr9+V3HeQE1"
        "xHh/ftfxa32AxgwCw0qAWZMFRqju7UNgWAlQJb/nYbqIlYBcTr2hDuAlmOD+bIBeYN0HUAhnwYXCZQSGtYC035+f2v1vtJkL"
        "8QzI0NUCDR/XZLmGfIADM/JZksyIAGZEADMigBkRwIwIYEYEMCMCmBEBzIgAZv4AAAD//6DBev4AAAAGSURBVAMACCZtAwZM"
        "PYMAAAAASUVORK5CYII="
    ),
    "i_info": (
        "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"
        "QmCC"
    ),
    "i_warn": (
        "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"
        "RU5ErkJggg=="
    ),
    "i_film": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAADl0lEQVR4nOydO4sUTRSG3xo/vp2LqCgomCl4YwMzAxPBTBSM"
        "vKCRkZmCJioYqAjiL/AHCKKJIquyXhADA1MveAUjRRBUZKd7FtY9nkYUUaiq3kFfnH6fZHu2Tk9P1VPVVV3dVLcgqLQgqEgA"
        "GQkgIwFkJICMBJCRADISQEYCyEgAGQkg8x+GwMwWFAPshGG7BawOwHL/93yMNlMGvAmGlwi43G3jUghhCnMkYA5MmS1DiSMh"
        "YL9/7KDZFF75zvXGcMZFvEdNagvoD2yr1/iLvtmD+IG3is9emjvmt8PNOvvV6gOKwg77kSagwv8Nr8kL/LQ0WQzsQM398ihL"
        "2zsLnIdI4iL2dLvhQlZsTtD0tI3PzOIxRDbzAta22+F5Ki7rFDTzBSchajFjOJETl2wBPtRc6kPNd5jjiKnBmA9Rl/jI6GMs"
        "KHkdUE5jN1KFb7jqAWc7HTzxA376OalfmsV27XUCVWzd3+cVclFZYtx3OuqlsjWyayimscv/novEpE9B/vN2xgMw0euG7d7p"
        "3P+18EeRKo9VXj3P2/zj9Visl92uxNdl9QHrY4lePU6jofho51Q0wOJlV5EjIDq14KedR2gonvcXsXQ/eSWvl4aejPMm2UdD"
        "8bx/SIT8n0jXbCgbCSAjAWQkgIwEkJEAMhJAZqh7whWpuZR/nT+dP7UAMhJARgLISAAZCSAjAWQkgMzQ1wGpe7r/+nXCn86f"
        "WgAZCSAjAWQkgIwEkJEAMhJARgLISAAZCSAjAWQkgIwEkJEAMhJARs8FJdBzQSOOBJCRADISQEYCyEgAGQkgo+eCEui5oBFH"
        "AshIABkJICMBZCSAjASQyREQXQ/IzBajoWTkPbmmdI6Ad7HEssQaNJRU3v0K7S0SpAVYfEkyP8gxNBQLOB5LD4aHSJAW0MKt"
        "aHrAtn5hE0VhG71JLsSI43lcUuXVpyCu+cct0eAWbiNBcs3OwcDWfDE8g6jNvIBV7XZ4FYtJtoBq/WM/zdyBqMtkqvArslat"
        "LUtbMQs89c0xiBzK0MaqbghvUoFZ1wGdTnjtHco+iDxa2JNT+N9CM6nWw3cJhyCi+N2Dg72xcCU7HjXpD2yLjz0vYfRf1lOL"
        "v/IKk4peO9zwlrDONychvjPZMozXLfyKoZaO9855k3fOm31zg9eAlY16jRXw2rcfeA2+633kPcwRvZSBjGZDyUgAGQkgIwFk"
        "JICMBJCRADISQEYCyEgAGQkg8xUAAP//KvANXAAAAAZJREFUAwD2TO53YnNsTAAAAABJRU5ErkJggg=="
    ),
    "i_tree": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAEoklEQVR4nOydu28cRRzHv3Nn8N6dFSKeEh1IIYALRIMECIKg"
        "imJeBZBARQdVEBQEJCQQBY+KDv4AJESaIHBA5iGIZEvQAiG8pFRBRhEQwHd7FrZ/fCdOihSZmb1d329v/fs0vvPO2rPzmcdv"
        "Zvb2WjBUacFQxQQoYwKUMQHKmABlTIAyJkAZE6CMCVDGBChjApSZQglEZMdgiEcheFAcbnDAtfz1DEoggj/4dxalg6dmnFtG"
        "w3EYgRWRa5DjkHN4BluFYHmqjfump90PaDCFBfSHso+F8z5f9rDFsDWcnmrhrixzP6GhFBoDBgN5joU/jzEUvoct7Kq1DSyu"
        "rsrNaCjJLSDP5YkN4F0o0OSWkCSANXCWNfF7KOIlXNLGPU0bE5K6oLV1vAJlfHf03zq+YmW4CQ0i2gIYal7NUHMZI0ZMVdO0"
        "7ig6D8hXsR+xwhd8yARvdjo47pw7gxHwAy1r+DHW9CtD6XxLWBf82M8FSqzwP59ygl9YKke6GQ7zmlcwItFazQtd5I87L5pA"
        "8FGv6x5ABaRKqBlDtsq3ex28RhGnUZAUAf8iMLtlTbi923VfoyImVALrIf5hng/0MvdxkfNSBuHg0gK7nROoEB/lMNrZ4/t6"
        "TBCsyTto4ehgKAeLnFd6MY7N7m9UjJfgB9pJk+Bhnt/ihPVAavqULig42vU6bsuio0ntjjxthxtTIrVaL0ef647uZhX4HRPG"
        "muDllHS13w+ghBOug1v9GhRF/IUJgd3Cfj+HSkgXRrMLqiMs1J15jlkWygssvX3BxA5PMyp6J5TEdsQK4ieaDLuXOPeZ49tg"
        "yMmq+xgimIAScA70ajCB4BZEMAEl4Bzo59Bxds7RfRMTUAJ2R39GklwaOW4CtDEBypgAZUyAMiZAGROgjAlQxgQoYwKUMQHK"
        "mABlTIAyJkAZE6CMCVCm1GfERqU/lDnuFh3ilt0sNy12QhHm4QzzcJz7t69z/3YeY2bsm/L9VXkIGziCOtLCw71p90GRU8qW"
        "z/i7oHU8j7qikLexC2B1qe0HLDTyZoOwMmMXwA6x0rupq0Qjb+NvAW28gbqikLexCzgbZTjcz5dLPgSEMufysOTzVDQCqgK7"
        "N7QkkxeGGhdgApQxAcqYAGVMgDImQBkToIwJUMYEKGMClDEByqQI6IcOisjl2KYkXHv0OUIpAoIPT81z7MY2JXbtXKX7DRHi"
        "AgTfhQ/jRWxTxOGl0HEn+BYR4gJa+Cx43GGuP5D5wUDuYJO8DA2H13iFv1YuQx/l273BxC18jgjRtfzhUHb7Z7TBKEzbYVeW"
        "uV9DaaItwD/zht3MFzCKshArfE/Sblaey3UbmxvW0zBSyF2GXV3nTsUSJs0DOh13kgPKkzDSaOHxlMLfTJpIt+veo4RnYQTh"
        "DvDBIpv7ozy+fi9jz8Mo+UUNTcM/tpKl+chM5j4tcl7hpYhe5j5hS/C38C3AOM9CSzBbtPA9pW4p4eC8h4PzvXx5G2vA9VV8"
        "hckEsPnoYuAkX3/DGvwlx8hjGJFtfU9PHbDVUGVMgDImQBkToIwJUMYEKGMClDEBypgAZUyAMiZAGROgjAlQxgQoYwKU+R8A"
        "AP//bCMdAAAAAAZJREFUAwACHmCDG5+UogAAAABJRU5ErkJggg=="
    ),
    "i_finder": (
        "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"
        "QmCC"
    ),
    "i_audio": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAACcUlEQVR4nOycTWocMRBGSyYLJx4IJJfIKgfIDRIn5DyBLLPI"
        "cQwesC9gfASfwj8Yj382ltXeDbgH1BL9qVrvwYBw0Ru/6Srp62b2DKTsGUhBgBgEiEGAGASIQYAYBIhBgBgEiEGAGASIcSlg"
        "8xR/bx7i2d19vBk+w3rzGA/NIcGckf7RPy3a8ZvFYD8O9sOJOcLfHRDtz47aX3PGO/PH17FCjPbFnOFRwGqsEIJ9MmewCxKD"
        "ADEIEIMAMQgQgwAxCBAjEbCkLKeU2bOg0iwnyYq76gfvg6t8a/47YGFZTimKKGJRWU4pihkgz3JamkHzz4DCHl58fWPPE/rb"
        "hjY2gzzG0aU0NYN6FNDU8wROwmIQIAYBYiYJIMupR/Y5QJ3lqK+vTf4dQJZTlSnbULKcikyZAYt6L2cKNWcgu6BMXmfgsx2l"
        "5bf0hfs4fIZ1ar/rVPtumSAgl8ozsMcoopSqMxAB+VSdgbQgMQgQgwAxCBCDADEIEIMAMQjIJB22rqbUxkBAJumwdTGlNgYC"
        "cgn2f6wUg/2zTBCQSXrit04SfqXleXq0dpvaznVan6W/Ha72w6llkp0FDX1uLPOY0gM98irBbG0VyL4DavfA3slvQZV7YO9k"
        "C6jdA3unv9fT3b+WAlVBgBgEiOlOQO0sp5TuBLR2jumvBTV2julOQGvnmNnPAXf38XJXlrT6ED7vup6fKiiELGmb+VsQWdIW"
        "swsgS9rG308XMwOgJggQgwAxCBCDADEIEIMAMe4EtJbnl+JOwNKyJH8taGFZkjsBS8uS3GVBS4NdkBgEiEGAGASIQYAYBIhB"
        "gBgEiEGAGASIQYCYFwAAAP//QXyj3wAAAAZJREFUAwBtVjOC4MKRjgAAAABJRU5ErkJggg=="
    ),
    "i_video": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAFiklEQVR4nOycS4gcRRjH/zUrpudBIq6u4EHwsUZdXyfBZEX0"
        "tkbx5Ntd8KIXNYZcVPBgRBBfJJ5y8CLrxVwEiehqRMWNIl5CfERNICBE4ytGnZmeld0t/zWssBmzVd3z+qanv99lerura7u/"
        "f9X3fd1VXQUoohSgiKICCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCKMCCHMGOsBa"
        "u77ewJ2wuN0aXGqA87m7gsGiaoFjxuIwDN4sRdhjjKliQDBog6q15yHG48bgIf5ZRLaos7HsLq/DcxTiVwiTWoBaw25hi3+D"
        "m2VkGPaKv3j3d1Qi8x4ESRUD6nW7nVe+Fxk3voMtbz3d0ly9YR9Fl6BL3lD9x16T5pzEAsSxvY9d90UMGdZiFxvWPegAnj9Z"
        "je0+xsOTZgkHqnX7M/dtSnJuIhe0sGAnFpfxFYaYEYPLosh8l+Yctvhzaw28RCNO//8YfjFFXFs25idfHYl6wOISdmDIWbR4"
        "OmlZGn6ErmsrjX/kdMZ3MEEZsw28EKor2AP4z8bYtY6jzYwpQ1imqKPMjP7wFaJruX7Z4FUa4wqEKrT4vVIy5/jKBJ8D4gXc"
        "jZDxLd5igeeLRXzNGziJAYIN6Kw4xgSznid4F1s8RU19AXfxd/ca9TTdDeuZTtoS2QtGg2VCBWqxnefP5jULMCsql8xtyAC8"
        "l7f5c8tax2ncjypFc9Mp++hu2AgfXrbY4TInpKRcNF4bJ4kB3rSKtT+LjMC08xlvAXvqvTp3w1Z/kK5kZzvGT0ISAbyvFuh2"
        "vkRG4LV+7zvOttp8vnHuhmnlLNPuT5P4+k7o+GUcfX4NGYHXeiJQ5MxmdhPjMA1/P/pxTaEC9JvWdzzk4waN0P10m27EgKGn"
        "r4q0oAKgvQccivY32/Y2dIgK0AY0/uvlCBeXIrMTHdLRgEwmcf6mzajFUw8VLB7kc888ukSuegDTS9OO8Rm2/6S72cpWf1Wp"
        "i8Z35KoHNBq4AClhq58tF7G9V6NnueoBUYTfkpZ17oZPzpN8NTHTy6HLXAmQ5KGxxd3sR4/JXxD24OIz3c14PwfrVYBVuPjc"
        "75kS+hwgjAogjAogjAogjAogjAogjAogjAogjAogjArQgpsRgT6iArTgZkS4KetuQhb6QN4GZIJf8/BN6AY3Zb3WwIF63W5G"
        "j8mVAByQGUtali/mruT42Xwttq/10i3lbUDmB6RnppduKW8DMm1NAWpxS5PoIhqEU7Dilj5pzhvtkltSAdrAzRv9zy2hQ3Ru"
        "KDqaKhQkZB8dkoTst1fqglpge32s+RF3n0gigHcqB4PR2cgICa61WorMrnKES9z8T/SBJAIc9x2MY2xERghdK43+o/t1MyMq"
        "RTPNpHUT932DHhIWwPo/QeIFPomMwBTyKd9xGvzg6r9LJfMZe8PVvXRLhQQl3vceN7i1Vrd73af5bq0EDBi8plF3bStfSE55"
        "Cxewr3UXe8PSKrc0iy4TTAAaDbtxyeJb5IARg/EoMkd8ZVJ+qH2iUjLeb4WDPcCtn0DlP8DwMxcyvsO5JcaHCbqlR0JuiWUO"
        "BapLlgLHsb1wGc3K1mE4iU2E8ZIxx9Kc5Fuso3ncYKoSmXd9dSR6DigWzVEGqAcwrBRwb1rjO1aypRna5gZmI1+4fXQ7bq2J"
        "/S42hozfrAMpoP/bRlVfxhDhpqIzyL4CIdpZsmyKau/B4C3Ol4pMLlnmKEfmHXa5y7k5h+wyV7CYkDa+o6P3UAzONzI438zN"
        "69iiLhroZSuBo9z+nC3uQ8a0jzEgDPsiTAOPvg0VRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQRgUQ"
        "RgUQRgUQRgUQRgUQ5l8AAAD///qGUeUAAAAGSURBVAMAvurUsMGPtCYAAAAASUVORK5CYII="
    ),
    "i_music": (
        "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"
        "4wAAAABJRU5ErkJggg=="
    ),
    "i_wave": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAACYElEQVR4nOydXUokMRRGb5p5HBiYVQwMuAA34D8uQ92AIPgo"
        "4g7UXQi2qAsQVyDuQsEFWKae1IcSkgr15VbOgaaryEuTQ+5NvirohYGUhYEUBIhBgBgEiEGAGASIQYAYBIhBgBgEiEGAmF/m"
        "kL27bncR7LAz+9/fB7Mn6+z0fD3cmDOCOWP/vtuOP/p6YHjzfC3cmiPclaA4+Uc/DB+bMzyWoJWhga6zf+YMjwJ+Dw2EYH/N"
        "GeyCxCBADALEIEAMAsQgQAwCxEjOAXPKcsYy+Qros5w4+VfxcjVO/J/+019HC8uD+27DGmNyAXPLcsaiKEGzynLGohAgz3Jq"
        "6kHN7YJq60HNCaitB7l8JDmSqnpQiwKqep7ASVgMAsQgQExWDyDLKUfyCiDLKUuyALKcsuSUILKcguQImNV7OTmU7IHsghIp"
        "3QMRkEjpHthiFDGWoj0QAekU7YGUIDEIEIMAMQgQgwAxCBCDADEISCQetl5yxoZAQCrBnrPGBkBAOmdDA3EFnFgiCEjkYi0s"
        "Ywy9Eyf7MUbQb/HzGu8f3oNtXa6HO0skOQvq69xQ5pFTAz3SS4hfSytA+gooXANbJ6cEFa2BrZMsoHQNbJ2s5wEla2DrsAsS"
        "gwAxCBDTnIDSWc5Y2lsBlZ1jWixBVZ1jmhNQ2zlm8veCasiSajrHTL8CyJK+oShBZElfmFwAWdJ3JO+GkiV9wklYDALEIEAM"
        "AsQgQAwCxCBAjDsBteX5Y/G3AmaWJXksQbPKktwJmFuW5O5vrOYGuyAxCBCDADEIEIMAMQgQgwAxCBCDADEIEIMAMR8AAAD/"
        "/z0/lwUAAAAGSURBVAMAylffOZ/EDOEAAAAASUVORK5CYII="
    ),
    "i_movie": (
        "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"
    ),
    "i_prores": (
        "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAAENUlEQVR4nOydS2gVVxjH/9+QSB8ipUILXRbatGTRXXel2NIk"
        "PsBuaovdtNhSg1RBNz5woaKIWyE3ioKgILpRJOqNRsSFC7c+wBe4UgTBF/GZZI7fXK4aH/c8chO/wfv/wc09954zM2e+X2bO"
        "zOWcMxmIKRmIKRRgDAUYQwHGUIAxFGAMBRhDAcZQgDEUYAwFGNOGJvj/iJsxmmGBE8wXh68h+EK/nv56OecAEX2vf5bal88T"
        "9iTWb1jzrus+X9H0gWwa9vfNkmFMkAmF4J8h93n7GFZq8j99fegrW6I4v5Vm66fyHury/W05Nm+dI7eQSPK2e6turi61T5Mf"
        "g7zE4b5k+K2vS46lLJbUBiyuuhUa/AFMUvCdQ6lJqp9ghpYfXDzoliYsFX8E6Ir/1MJ7QILkgoXbumRvTNkoAUuqrlNXeh4k"
        "nhzfVGbLpVCxqFNQDqwHSSPDuphiwSNATz2f6dtNKffFTOnQ5sPJR5hZ+UHu+MrF3Af8EQy+wyGXYcvjdlzYNUvujs/qHfQ3"
        "ZZVuMRWbWr+/TrpPPhhBp+RYpVGZ22i5Wswe4HdN9sNDjIAFvkyt/UB/j8xHi1D/Bzutr3l6djisgZ7TqKzGJigg2AboBr7z"
        "5mfYiBZF932DNz8Qu4KYRni6L3M4xzm0KE/HcNmX7yLul5r+MW53tzxAi7KzR2778rX1mIYA/DXUGAowhgKMoQBjKMAYCjCG"
        "AoyhAGMowBgKMIYCjKEAYyjAGAowhgKMoQBjKMAYCjCGAowJ9skp+s2M7z/P3ll+XsSongj1ewr2C6qtp74KBj+MvJHwExTA"
        "oE8tTQ1RIn5ixhdQwBQS0+s1eBVU8kEspSYmdjwCppCY9jOqEa6ZdPUVslUO8spw1wBRR4Av8KHr3PdtfEDM8ik7xDthYyjA"
        "GAowhgKMoQBjKMAYCjCGAoyhAGMowBgKMIYCjKEAYyjAGAowhgKMoQBjKMAYCjAmRoB3PqBFVfcpWpSIfQ/OKR3uF+Rw05ff"
        "5tCBFiW47w43ECA8Z5z4pySTDKvRomQZ1vryneAsAgS7peTAcbX0a6N8Aeb1Vt2Aito0muHC9l/kHt5jlgy5mW4MHRqXNfpx"
        "trewYAgBwuMDjroOPU4ugiQjDl/19chVX5ngKaiY/1jbgRMgSRQzqYeCXxB1GToq+FcblCcgsTxCOxbFFIwSsKNbruUZ/gaJ"
        "ZWH/z3I9pmD0jVgxH74DliMRX8/LMjzAYbLrp4ssq3TLwdjy6Y8wGXRFy78fgRl1x1Wo4UbKMOhv0ur3Lh5hUqB2j46M4dui"
        "kUGTlL2ne2z9iliM5OhMDX7KNt5K7zH3o94o/KTJ7/X1ZcPHWNX/PO/mntJ//l2QWL/aY6z0/ZreaJ3R+5+TlS45hQnC4RbG"
        "8NdQYyjAGAowhgKMoQBjKMAYCjCGAoyhAGMowBgKMOYZAAAA//+MxZZOAAAABklEQVQDAEfRCa+rQVHdAAAAAElFTkSuQmCC"
    ),
    "sw_on": (
        "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"
    ),
    "sw_off": (
        "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"
        "UgZW5NHTAAAAAElFTkSuQmCC"
    ),
    "big_ok": (
        "iVBORw0KGgoAAAANSUhEUgAAAMgAAADICAYAAACtWK6eAAAQAElEQVR4nOz9WZcdV3olCJ7Bhjv4CBDgBIDgEINAKUJLTCk1"
        "hQKZKWUtrVpVlfXAXF1/QfUP+iXI1b+h9dDvvbJXUw/Z1VmZtZZUJYSUmSGVxJAUEhERDAYJYiQAAvDhTjacc2rv75jda9fd"
        "AXeA4Bgw0mF+B7+DmX1nf3t/k1FPtifbk+2+m1FPtifbk+2+2xMDebI92R6wPTGQJ9uT7QHbEwN5sj3ZHrA9MZAn25PtAdsT"
        "A3myPdkesD0xkCfbk+0B2xMDebI92R6wJerJ9ultQenujcWm99w+bNP3+Xvd3nyYF3uyPcT2BEEexxYCr1QtexhFaPZvqDe0"
        "evONZv8mnhN/3lCh2asj7oP8/Rv8+ze7t+Prt++nunvVNc4n26NuTw7io2yCDO0Kjj0v/u/h1zfx873O8956B094nb9gh/1b"
        "zf5RN/x5+3Jx3/zy+sVlBHmz2X/ve839T5DmUbcnCHKUrVmR5yt1gwgtErx+7pymMbz+vWavsMfP+ROvanXioj7/OvYXuP+j"
        "uFcnHm3/eru/2Oz5+n8c3++tc82++Rzn3okIJMjVQRr1BGEeZntykO63yUW0ByHajciwBwnO44K90L19/rvqwtv/Di/wGm69"
        "jX9fw7+L7TXc/fbbe/btg/e73blj6fVeuxHOK7zfhe/PX//18+fCW/PPuwdp9iKMlsvgCbocsD1BkP1bXGnf1Auk+N731BwZ"
        "sBdk2LPCX1i9rl9bfVYr7s8/qy+8/a4+13tOq/dvYP/7+u0e95tacf/apn77nQP2Co8/6PZrzd/z9eR1b8jrv6b4fv9O3lc1"
        "n+OtJeSJSLaEMPhKbzQI80Z4o8tdnmyd7ckB4TbnFNzeXByTLlLgQuPu/Hn8yl/eflZutyv9OVzAF7nHT9yfw/6ieiU/pt/D"
        "7Vfw0+7b7VFvz/c/+5k694ffCRffudh537h/7dVngyATP6pA0P8UFBHmvIpfAAgz5zKqgyyCKk84S7v9QiPIfk6hljlElzPM"
        "keG6rNhLSICV/uLlO5rGMN/nH+lXXv0N/d7Vu/pscrfZr8q+5h73nX0l7o98u/n7+f6Xfk1ffO+j5v2OLb3/HJkEcYBkQDRB"
        "mLcbpOtwmiVkUQdwll/g7Rfzy7eI0XKLA5GCHOLd+fGhO9NdoXkRto9xRacRLL/JC/JvfeNevP+0enzbleb1sE+e3QyX1Ifq"
        "rLzfh/LwpUv4fKeOzVf/g5CmRRjZdsFhzuOrX1ANsuxVx94Icry0/oVDlF8kBJnHCSR+QPWpVZ1oHPuQ4t9FH79BigUyYKV+"
        "9dgyEmB/6cY2bq9hv9bs72F/T1/NdrQ/O9RXb2Jvh9o3+8Nu+wc93r5eNtSX8B6n5u+3Jp/j1CtrC+TZizRdhCEnIodpkXGO"
        "LEDO199SLbK0cZc9cZZfiO0X44veDzGIFueb53Q5hbhNWGl5MX3tawufnxfb2bhS1zAE+bsOMvDiVc89q9T1G0r22NztneVj"
        "/DR+bh6yb7fu/ffZ7Ik1fDG8n2reF5t5em15pW+QpkWYpI7oMv9exd0g33f2bJirZVDGItlSHb7SIspc/frKI8pXGkGWOEYH"
        "MeYqFNwodQCnmCPFH/6hmvv8WJG5Ml+68SNZqf1eZLA72p0a6uu3fyZ7GoYzfe3vjvHYAD9jffxZ7D/G7WZ/39vmgMfbv7eL"
        "1+M+vk/zfnz/dLQfefBZiWYtwixxGhXdRS4Gc05F5MSCIZxFkddfXFLBVKN+/SJwlK/ql6MbcDBicKMbxTjCawv1aYlTzJFi"
        "mUMIQrQbEGIJHbDS82Ke3z55Qqlb3MNQ744OP87H8XNHPdSmj63EFfzWbfyD91O35aZxw9BFHntiHA5EmCUO00EWcpYz4Cxg"
        "K5GrNHEXcBX5u9vvhMjZvvoc5auFIC3HCFGVOhAxaBzwI0Td6ahPXEnrfUixzCFcOow/DULMV/JmpQ9JX36OcQ+jCE/3ZL9h"
        "gSRb07i3B+xPNI+f6Ny293n+1mIfdHz9Y0+fxvuO4vtv9eefp/18zrw0RxjXIMzVhsssOMz2nLO88odfUxfzv+xwFcRdcHzm"
        "iNKoe8scRX8lI/RfnS8zJ45tHOPc3D24gP1rcBkkXtFbIMay+vRCRIsuUjxHDjGMv88RolmpgRB7kYEXrTp+DL/dxc+xxUfb"
        "nmi1iV/u4WdTffLtXtzp9cGeFTu+r3HTpft13SBNF2GaTTjMHmRJ6h3Zt2oYj9O54pkOoqgFR+nykzfVIo7yFYmhfBUQJK5c"
        "e+MYbYS74Rhv71GjWm5xSRE51mQlbZFC3CciBVZeeimyEsM4TtAotj6MK3UHGfwcCbCiwxg2DPaG+1z26xb7nZleP97szQF7"
        "/DzwdnfP17EzvcG9vN9k8b7b1/YhjiAZEIbIxgPWchr19NPq+u2IjFwMWmTh8aAIcfb8C6qN40hcZ44oHY7C4wx+Mo+jtLlf"
        "bVbzl3z7cn+BrjrFhEG6xQ3PoGQpMj9PplpwjBon/NJZJXEDulC8r1WfupyCRiG/7OEQNIZ2pZ4jgyIwbGJhv6fCTo7Ht9XG"
        "xrraaj/mLi5qxXuVmu/X17Hfjk/YXtyWx7cPery5v/M6O6u9uErjjTY2sNvaVnot3tcCFv9ZIM1doEd/vrILhyFPArKYp4Aq"
        "LWepwFlwPIwby3OvAlEkznLpQ5UQUTocRUH5Yqz+/GvPhaU4yuuvhqh2fbnR5MuLIHvjGcB5UVtU9JWjO7U5N46WY9A4TrWI"
        "0ag9ghY0DqyoLaeISDFa5hDgBnGlflaQYfOlTdWu7Hd3bsQVHiv72rGT+h6MYnhrat3Pb9mVaTD38o/tMC+s7J/CfvZzO0wL"
        "62dFc/uW3L6357aftc9v9u3rTHdM+/prx3J5vzW7QJ67zeeipQiSNQjTRZZjujfnLi1n4fGKHOtnc64SOcqPtDobEYXqHjnK"
        "uVfPyQJETkekFo5HbiKp/W028Rtf6rjJl++DH8Q12njG2w0BlzjGOSVugaJhkGfs4RgtYixxC7x8EtGCF1J8/WWkWAdCEBlk"
        "xcZ+DRfmvVs7Zm0VK/rz0ADGud7F/bipZL8a9/079+wY+6G8youdL4R7hvGRMf4bDnF73Dyz3SvuP1CLvwdPOL7pdneb199t"
        "329XrQ2Ph51r2OP2qL/mN4EyLZKpDsJw075BljtAlo2ILBFVlrmKqGDX93OUuerVjaPwNMzVLqBJl5t8CbOGv1QIEuYp6G8u"
        "c40mnsGco0Uc46N5/EJ86g7HaBGj5RZCuOmjw1dvOQUNQ5Biu8MhsBJvwTCG5S175+dc4aeyoq+ey/XORq5X8fj2vXu2X5Z2"
        "u8Q6vfG83dpK07xcT0fVpu2nz1nuPZBh1/nEbfhkd2M3cSn26W7S475c3I6P30wcnjvC3y7+/rn4uvl6uj3D+5TP4/34vrnd"
        "GZd69TQ/T6mJNHeIRA3SbJxdV10u0yLLsa8dUy2yiPrW5Sp3xzHOkg51l6MsqV7dOAoj86ut2nViiZuEEG1QfYm2L8+HbYh4"
        "G9cgjDOlWzUn41zDNcq5OrWqD0QMpcSNEsTo8ItoFAvFiZzi7g5+34hvP7x1yxIh1rBW74zv6FUs3W4yMmFa6TEQgSu/n42M"
        "MqWe4Pn9WaoltsHX3JqYoCut+s3tUpsYIp/hp9fZt9ve+0kOcqWzmZeHySs20qDzQbyN+InuVXFl9lmYws4FlIA6up+GiVvx"
        "gjDDIj7nJwi7vHzSCbLgH71WBOEsLV8horwMRLlzH0SpIjcRREEs5eqzO+GVs0ATHPgMaLLI9YKju/tcEF4iW4smb4QvCy/5"
        "UiBIaFWqpi6DcQ0aR7tStepU2VWn6DPvc6cix2gRg3GENq4QffTos7ecYnjnlnWziBRckQUh7tzFSv1Ny5XbJc/biAxABKz4"
        "YTWYcVXZPA3JOOykbtRPx9Mid2uDdBKS1BXDnD8+G6RTdS9z2TRz7T7DvnevJz+r02z58SybBps6hb/fSXK/Pkwn3qb1VOXj"
        "kU5dXqSjepL4JLFjfAYi0ajaEqTZLTcFybbvbdjBrLKqQZg7zfdy+I4tZxFOxeNwonNc7nbUrw5Hua4a1et05HTvXYq5X21W"
        "MZGc7i7jTm1diiC+1KE09Sdfgu2L/iFjRLxxqbr5UxLX6EWuUcKd4pPJNSRHqhPLEAlTLVBjwTEaxADZlr2oT0qtTLfN9qTQ"
        "a98Ep+DFRMQAMgzMKT0GAeibYNSs1GGYxr8rIjqE8hkT7JbhSh/q2oa8jsfWrMoiFCZjK7dz27wvFqeyxJKbxW86KfG8VMUf"
        "YNAg8/PHsde8PeIfuuinDIZO/s7v+nigkqBdIvdpp73OtryeAGU2IsroMRDG17CeoSeZ0f2rS8iyBlTZHeTh2LPrfq6GzRWw"
        "hfrVIkqrerVxlIgmVxCZXw8HcZPluMmXR+X6IiNI9FlFV2+DfliJ3u3ENaCiXLz8l5qG0XKNqyqubBLkw0onuVCiSvXnHKOL"
        "GK1P7mEYwxNTu71ZyAq7faeyA7tqttMN21snUmxZrsxjG4wDQriPtazgXMllZcdK79Nj2bT0PZ8mWQhp5nXa8+Npz9Wh74e4"
        "3dN9NyoG4BQDX+U961cHfmYG3uEn9AbGwmcKeMz25H5r8fguHtOrPTfF/Tle29Z4rUHm6rLvq7pXVKEn7zXzmdNJb4rfBXmI"
        "VkAuQTAgGREmrPbNbjVKhAM1yCLGD2TZOY3F4EwmyLIyVWYeb9mjfrUcpVW9mHtGhJYF6fRvqX3cRDXuL+MmTbbwksrF7Qus"
        "cn1RP9g+5JCIeEelmsc1YBiXVIT5A9WpNuJ9/ClEusdz1BCOgb2/sW0CjGJtdVUQY4ALZgS06MMQ5LlwWYRTACl8NbQB94cK"
        "C0uv1oIS1Sp4yCwJPdxXJ0ATIISbWpxyIE1iVIZ9Kgs/Xs9o5XFbT01sSWaa419zqTLczW9nPS+3iQm2DPL8oPHXCRChBP9I"
        "vZrisRKv2Ku9Bj4o23eaCOMnXvN3eHUq3fV6B+iSEF0KoEvhTZ04QRZyjAxrQ8NZtL8abG/FC6LgpXcv58EAUbSP3KVVvdpI"
        "/RxN9qpd5Vpos4eJJjFucjwIknCDyiU180tIor6QuVxfRASZI4f4rGIcJ/YZxzyuwfoHIkfHOFp1ajnifSVGnBnZBmJ8/LOf"
        "iA9O44jc4jK4xQa4BFZWconVsfHZOBlvb9scKzCRwmdV6u0s8VmS+ULnvowI4Q1W9go/QAa4W32rdT8Y/OS4HSYD78MwmHoY"
        "3Gw12HIlmMEgmARXlVkJ1q+ELFvxwazgLfDDfX8lBN7fPA6/KJhsIL8XeA38rS/xmrYchKQcWJP2vYYPlAKpCiANkMS7iSCM"
        "H4Wez3TuiGx4tg+DVJAF3MWvlnbsxqKejezYuPXnRX0TRBlHRBEOdnNmu6pXmzsmcZQ296tRu3gCJcerqU+Z53ZR5WriUm3N"
        "/DKS6C8kknzRPtACOZbiG8/qbg4Vn1jvUakWXCMSyS7XTy4KQgAAEABJREFUaJUpnmS6D9uCGJFjDKw2oykRY9wgBlgGVCef"
        "gEfY3ISxtWENSGEWSBFcbgUliiQhQgRTWBUslngs5Ca1qi5TRWLry4gkuOpVprWDLxURJDXK4ZGk6h5/0/4Tpal2xy+bBmWx"
        "uoYKf229JVo4HCjjvNYJ4SeoGqt6klUanw7vz5hDrT08PzxXm7oWdHG7YCfw/Pq9mtxFkGUINHE7c1Qhf1GTCVEBOHQ1rBFR"
        "fIMo98BR+ut+G6pX5CezIPGTOwdzkzYSz7iJIInq5HQBTZazg7+YSPJFQpBl5FiKb3QSDJtKPq5Q6rdOqasd4zjeiYB3I9+b"
        "m5t7jGNVRY7xbSO+uCBGH4jRS8YfQ33KBylXWq64sxWThhQ+PpDCkgOAEwSNlVqn/TCs+1jVB7K68wdIgMtzNaT9QV3OcIWk"
        "K8443NYrrnLrxuE683bNuHot+GoNrH41OL1mTIbbyRo+wJqr4h6vt2ZUssbXM7rCvsZtu6b4t9qtO7wmnLbV2vm1UFZrdYr3"
        "V3g9Io+xzefR2M/wWfv9MAOykfOYokdkEd5yrJfOHBERqJKDS42KdLw7Fq5CRCH32pk9bQYpEAULChHl7vSyWUTon53HjVq1"
        "S9zap1WD5EPhhIIkTf0JOSO549tNJSPPm2Rbf0GR5IvyQTqc41xsttbUhHeNg3lUTHcgcrAIaIlvmIFexDQQBT92jPEHTXWK"
        "BHwVhiEnGZFuIaarp7THAkiOERHDRsSgAtXHz6SHPVDCrdrgp6mChho8kMICKQwW8TpkKsHzQ50onUChciDJuGT5e+0So611"
        "ugay8DFQAh0saAgXJKBIrY1O8CpaG09igTsNISPoiCB4IR8hxBgNWwrwqjw8NRPiwzU+gHWOyKFrR/XKMJxY2VqltVOFxzWb"
        "larGY0QY7/CGDnvjlHMRWbIa9/UrbQunp85pSAV6ChQBV9FjIErSIMpu5Cgr5CdQvTT4yQ4i9+tAk1EfXwxq1z5uAjQ7SOWa"
        "ZwkDTc698ozUyEsuF2vicf+FNvL+BUKSLwKCLCPH3Dj+3T7koHEQOejjUod3ZifWYzTGcZxEnGoLs1q3JnN1iivfjiEBr2yr"
        "Su1+NIpxiwYxpqFOfTrLoCLhB6trFjJfJb248gItdNkDQsjKjMdX4optVgUhYHfO2ZW44nsgAm4rrPhAC3xKIIBZB3Vefwn3"
        "n0Ws8UWbrnn8/iLCnKd8WDsLUVX2nnu9uI396eBXTaJWT+N+/P3aWfwdfDp5PQOHUNV2DW7VmuPzrI+Ik2YrrgSSCcLgW6Z2"
        "SI4jqBLw0/e9AFgJVNfInTJoyAW+L9Q34ViM2wBRfFnGuMoOI/ybtk7BUYC8wtmw4LRq15ybNNL5sY7KJQsYFrJ5BF7FIO7F"
        "95pcrrYmXhFJLn7hkOTz/gAHIAdWkj2cY44cTBdpyTiNo6NS7Y1rOBLLrjplR+JODQYDRSXKl9s2lBvGz6qUHCPM4EpZh4W5"
        "QYwciDF1qbIQdYkWxiYBPMMZT00qU7VOQcRTmyXGuTKFu5RiDU5MCsoOC3rJG7waZCrIWpCwrMQ9gBsBv0cFK3B5NO1FACww"
        "zRER5crbloPgFah9qSb+gbggAi1Ytfk2eDGrHYzCXzaQCOCLmQr3W8CgNpWzSYUvU/tKVSACFdZ73J9WJEeqLrxWGgGWvIKp"
        "VKrYgyhwxMhRTG+10u4jb9bXnd6dymeaZffcmlvmJvbpZx1D8fNIPLhJW4di3CQchiT35ySfb5zkc0WQpTjH622rnev3Rw7b"
        "QY62PqNjHG1cY9gxjrk6JVyjb+hj09f2rpcwXjDbgKALjhFSsGsdESMQMSYTrLZQnjQ4BzkFV2D4/biSV4xJoC5xD25RV+sv"
        "AinO+HrtxSSsEAFe0Ha11vWaIIvyq/CJVsGmh+AHqz5AvQrxfnyTFcX78bjTagh/iNG/obO8DwiA+2FcK/I6wYBfJEPna96P"
        "5wVBCDwfr+2ANEAnvPdpfIaXgD5n8DtMZc2Ru2ggiyLXkc+7Qt4TkrwfekCTajYMJdQwIIptECWkeUqOEp5et1N1E2i6LtxM"
        "OBpMvZc0ahe5iSI3KbS7ecO2cRPJ7aLKhbgTU+kfiCT/aT8n2Rcn+Ry3z+8DhNj4eTnOsQc52izcJjW9G9+YJ9TZlogj3tvw"
        "jVa6dUCNgJjGaDY2NA4PyTbYZ+D270IQhWFQlUrILUqgA4J7iLvjgoRkBIRIbQIPPkfcIcXinYE45A6WATjIsL5mcHuSs7g/"
        "MDKiILCCe8AAEg30IBMhaxDk0EAQaXLdIAh/l+8OR0xrhMk93huQohUW+5DKSQESYM2EvUiApMbzEVL3nj45Vn28hOfKytU8"
        "EJIAUg5GAG6BZ+Jv+Xeaf+8DQvzavW91iVchccHrADW8r601hZff6wqfvFAVuEmLKDX+Lq1LbTLYOZAFqhcOrDMl0AcRepMB"
        "TRwxDBF7z4h88GvDLEjcBCoXc7vke1C/IC/5mLlj8PCAJLyfuVwRSWKs5CBOshwn+fxytz4XA2FulW5qxnkQWOMcOcdzS8jB"
        "irZLP7innzsbu4U8zVwqGAd9XPY3oM97p1GpGNdYTXpaUs7XQcRvPw0VZmbH18dmcHqguALmQyyqjkaxkgS3mwQ7SMIEUY3E"
        "YS2kYdiM7hOu15yuFLwiGIPPIdLCr/G5MXCjvM9e1HDHsMeFiagd9CRYBwRe8HAD3TTQICxFWHBqvJZ5CtczlCfENwJiF0AK"
        "nOohjnz6MMcMhoDooIL/qMdwxxDWCyOYGKDUfwzbKmE7ns4MjBQApGocYkepFz4cjQQAqMoroPvQiZnTgn0orDfYqwJuWsmk"
        "GRhYBVQrdYLbRQVDg7GkuG0hGU/w0y9rA+UBr+yLlbwa4CRMs6lfzY65iXvPS6r9T3YlEXLbxwTIrduzcOLl0+HOT38KI3lh"
        "biQ3YSSnYCRtMdal+ocwku+Ei2/B3XopulutkbwBI3lDszHEZ28kn72BdLNy21jH6nIQcC/nEBn3PshBudH9bGZXX4gq1QAk"
        "MuxTqIZWuMYG1CmXAjGSxE/KTG0APWY1lj3GMHQOXz7nHu5LgxgqxZrfg3sETqGgUhlc1C6HcaRQh2BQWP2ZBCUIYGgUNJBN"
        "59VzMJinsewxn/fTdWMRt4cB3MGb3MQHuKG1vifIA2SB0RBJ6mggvoRx1TDyAkJCdRkG44EU1ugZPi94iSkUAzc2mQlHEYMp"
        "anidhRrVtRlkpaDJWlXq25GbmPpOo3RNfRs3WT9+zO3s7iotvKQXc8NaJFGxPn4/knQ4CeMkr94Lr73d1JVIJvDnlwX8mXKQ"
        "eT1Hk7I+z63qRsgP4BytcRzb61a9tAnjuGlXf7kxDqhUTBPxhnENXhKlFTUmo3HApbJpggh0BtraC31c6Nx7RKCxotfBIVpd"
        "0bcfGuMRv/ArVKPOOA/fHmpVRIA1uCVAAcQ38DxE4Ibem2Gt9Eul979bqvA/IkT3r8Enfhln8sRncnwBe/C5TuLz/Aos4F/H"
        "z+B/B1bxIpQrfMaG25DD4LPXzgmanarV6kuJcJ5VchMYO76vwXOn4Egu/ojSBfWur3M/9T3LHLMZuAmOJd1Uqn/MNGjjJoPV"
        "b0hGArkf3VxyQX5EZjBQXaTKyDjVvJz5IE7COMk7m7GLSpu71WQBB/XZ15N8tgjS4R0MDl04r2JHw95ybhUzcudxjoaQ7+cc"
        "cKtADHkiVGMcoTEOqlRutANVikorXCoqVH3EOUq4ReQaegbUyCNqWJWD+PYMkcMAQWrDAowcrknO/WlvcvjuqWahhVIgrzrB"
        "iQLK6OfABM549j55SHfps9roluGgXYcBXLZKX8PtiqiC70HEKOGqlVcUjgrcLHz/gnvEeGZwoeB2gZeYRH4XNNHQyclNNLgJ"
        "QvXaITLfm1XGgZ8gbmKfWqtaXjLZPQRJOpxkr7q1L3ermwWsmhjJZxgf+ewQRGrI35z3wr2g2Jp/YRzd3KqlOEfHODY6yCGc"
        "48zCOAQ5ysJKti1UqhmCfmIcBRWqPBV1JslyiWdwZeQKWTqoQ2po6mQFzvsKJFK8GuIJWq+c1mHleQXVxzpWuWIl1ohIQ51S"
        "6leBEv8D9t/B6vzCF9U4uIHpZfjcZ2ERv1eo8G/gd30LVH9V8bsoD8QMA37HF/BdPb8/lTUcAyIKnKqVObJiD0EDyMljCDSh"
        "2oe4kUTjyemOrUk8iQvTaGvLHook31ggyV51ixH3Re7WDelGLxkVqukV3PYI/oy2z+aN5u0/uUXeIV1HmpT18r2P9FItB9t4"
        "3h7qeXFTI+VGzgG3CsjRRsZpHA4SLlY77avK+uQ4+MZu5BtTuFSULCdVHnpQqBzcqqQHruF7YLK5InLotI/wcs+AUHun+rg4"
        "cqyk+RmSdR+w/AExvFqBQXwTxvS1L7JBHGXDSSjBq94FcXpXGzMiMQdnKS+Dd1PhEiTRampqXSKuMrNY8iFN4woHiigDfjKD"
        "reF3RwKfFkYXiJ1Utd5CiKiXgpeMnUmpadRQzO+5g5EEBNHtj5Psy93aW0+iunzks0GSzwJBdCs+vN41DtyWeo7/FOs5iBzq"
        "dJNbZaJxnNhjHHSrXIMcrXHsNsYxboxjOvs48g2QcYhUWahgHBlcKW16IYde6asBjj6iheQaQAhXc+Uc+po5T2b4vA9ADz2g"
        "4oQLZFD58K0ihP/ecfLBl9w4uOFMZNCFf7kI5r+rg/8VIglcxeEpxFheCHqFvxtwLvhKQ4Nj4ByQhXtdI1bCnJKsFzR/HH3U"
        "HMcpE+FDeEmVTmoIIohPTmaVZsbCwUgS60v2xkkkd+tKk7vVqSd5TTpBNJWJDAt873vxu3wGSPLpI8hctdqbnbupWQnY9qlq"
        "6znmilWTPvIgzkHkCGUfnINKVYscOFHFDqXahm9ApVLRQCjVksQEW/esxl6FHqLWdDd6ZwwlXrgQkHYRogaKhOewtP4zfNoV"
        "9dXedqBn/y0CNvBnXCmxECDFVfIRDx5i1dR7IAlQxTs9Y8JVYsIU2hmeCyQJSSG8JAOn0b1Sb00jkqRAkvqoSNLN3YKy5ZoY"
        "SduHC0giNe6q4SPdSPunjCKfLoI0xhF75L6lFtm5m6JWsPd+axzdrNzWODaaJm3zOAeNQy04B5Fj0hjHNBA51qNxUG2p8JPC"
        "OKhS0Th8GECVgt+NFZLcwjEWEVWeF7AnBxGe4cNTuEK+C+M4rz4F4wBRhpsSPsaB/wDL6U/hulxERPFHcHl+CCXgr3HfXyda"
        "/ZD38TH8/NRo9QEugzv8W/X4tzUEQ/4llK/vepUc5zHAaVshksrxwXECmcfxcUOqXIzhADTAR0JUACl4ICRPAcSHWVS4oBpO"
        "dhdIspeTSDYwtmPHYsQ9LGUBx97Bkq3NPlwqxsXefivOYuTfLSLt0vrpU13kP70Xn8/9W+RZMd7RDQbWTZms31PsJLUcx9nr"
        "ti8FTkwfcXcKq77ZUatAyOeco4mMz5GjRzkSaKAr/PSxXAEpNJTtv1gAABAASURBVFNIDOBG3Cdyjd4p7aBYGSJMj6gBafQV"
        "8IzXlES/H8sx2IEadg+kfxtHY9uyhwhr/yS3Cpc74IuRccbW2zqQpb10kpKcLTzPMAKA2CUjgGoDi8+6Dxpha+nYtaYex8bU"
        "ZKKJ1+/D7SzAUWCQvrgc7Ay3wUU8kMRO4H5NVWDHByheocRPOtND8JNZB0kk8j6pWGdiqtSZjdzN1S0EEy2CiaxUXI6RRCRh"
        "zqUoW1KZuBPm8ZFuHcmyqhWP9qewfaoI8kbTK1eyNBV9ydfUcl3HmvicS8YBdSOWx07nNePMGlXPN8gxbeIcw1TPOUdjHAmR"
        "I8EP4xuC3Tm0SPpgiGDTl8YKKD621VBvlKgz5BqMBSCE/Lu4XH/jkxlHcFhtL6cq/BXko/+QK30BYfa/wwu+Z7y/iSt9igtu"
        "CqOZqYC9pvWHKa75ifJ+YoJf7HEf7p/i3E/g/uDv3Ay/T2E9U4TvbwIF3+Nrw3e8kCn9HxCp/Cu+Nz+DetSNufFB/2alwu8y"
        "14vHighyWlQ8HDdBWuydXlEuDMFm+sJHEixEs7Jne0SSIiKJIScZCCcJJ1JJ95kjyTdXFUud1++TBUz1Uj4POCm5aewN3ORs"
        "sc1p28FxoWqpT2v7tBAEH/pg1eqVbt+qTqS823WEBA6h2qWs3EG5HCFnI4J9nKNFDmnBkfZALIEcCchlNTA66eOkDoSAS4zD"
        "4DkOkmU4UXr9u0waVI+40Q+EEVwBUlzFBc84AwwFmKRxrvEDNYyrHOJ1mvUYvBCd5FURRPBhvGkQxDfIYZo9opJG8rq4RAJn"
        "fGA9Cskp/wGSar4ELkRobbB+CRriIsft00DD0/iTR+8lH/wo0+Y/40U/BspB4YKyBTS5AkiHGzaxgAhvLAy5ntga8RJbzZoT"
        "N1twEkTdqW6pSSVxkl2sbeDn0+qKa3O3hI803VOIJFHZinXu3Uh70u27NbsepBfwZ6BqfSoIErN0D1atWuNg36q2+wj7Vcnf"
        "NZWA9E25tVm5TDwcq5dVGyGPcY4tE1NHoMO3nKNrHKEYML4RyD20HXA1PB0EQfo4kAM4Kmy78GoZzB88onHwOr6KYMD3oQTw"
        "QnofSMV2bUSEKfPuIacCEcIE1zh8Rj0SZFB2ZI0eXQseAR7cj9CBNWYXj+/u22vD5l2ja1rhucy9Ska4cCcI+mGvRkQe3D8R"
        "JPJAIgXEwWeA9fw89+YvgSp/Acu6prrlu0fdEG0HC/8DWPs5GF0PC8mAi8ppIkiMzg+Md4PAeImBwuXS6NJ6IEmISMLMBTlH"
        "bZwE506QxL5i5jXvP7tpyTHJNSXORff6ZGwDuxQfuRDHVERVq+kFrJr6EVG1Pp21/vG/aqtaNd3WY8OFpjIQ6HF2T+8qac2z"
        "N1IuzRVyaRgQU9bv2l66KSkNrOPwbj1pg4DSYoculW4IeQK3KlCSNANlNeIcOLHgHacDjcKDh/ieN0leef+7cBvOqIfdAkJp"
        "KlyBr/6eji4Q1FJoOux1whxXG9gQC7ZnahZxWA2hNFh4LIFlhe6yBy4QN0zDRTwrQe7jI2CpRmQ7cg/gERQlc0YhYuOAIAht"
        "w8hZqGjxPlbD4WTrk+B0Ahyx+NsUFsxK9oRytXP+ZQDS6UeRqikoQED4AdxE4SX4btMrMH4YDdw/O3EO7l/iR7YmgoCTGO6h"
        "bgFNTAY0AZIw4m7K7bqtK7HZPTfppW5tV6nxDHxkreEjG9NY414vIu0SH+E0rAJI0qpaXS7yKWb9fgoGoqI78Na/1fuydDkd"
        "9tJdPZd0aRw/hmv1aox3tNm5rCu4eyNWAtJnHay+pnc/upn0T/aB72Wa2iDGkSAIWLB+ow/jKGkcIOTkHIyM26oXkUMPzniH"
        "ow0yTiMJrlcq/V2sis+oh9voBl1OtP4JDGMGQ6vh4dT4tmWTHFhjBYfdwb2iscB/w0XEpgk13KIafBxGwUdhEsA9Vjvxa7CN"
        "g1UV7/SsqfLS1cEoV1fGJjmEVzySMoEWWlxI4U4RuGCefLoLELvq5LSG30XzUSaF9WGvQYMCDSZjQiXre2HEuG36+CDfhN2d"
        "fthzz5SVTAVIj3oGBAEyuuIqEA1fA+4WjAWGYpN6okoH41AwkjDN7Oq0LqvCZJCE6xm+UK8ui0k1RCBxmuWOZbwTEPi2fPfY"
        "2rNh6/aNwLan+iaJ+wl1230QnjvxtXD90jic5bi4endRZEXp90KT9QtXK4Cw68fsZj1eA2kj5t1cK0pzbZbugXlWUbVays6F"
        "a0XjqIEc5B2EZfhMiWTlsvXOzMf0EUEOxjjqPtMmHLvf+oStd8StInKc6hgHvPmVwvvzuNSOP9T30mo38f4frDV3mB0LFxIo"
        "EaDUACm0Zhp6jYukwgVYwSiAJCAa0JhhEDXrNDy7BTFFMhAvCCHYay78NSUqL62vFIlLEmTva1EKAm2FFe1yjTuWqUN8M8QU"
        "Y2o+uTKedSeARGUreFTWAimZZcwU/BSfC7+bVLKNFWRYGA5T82GUx2DR38b5Wn24wxBup9p8n65kNBIPI7GjiCTRSIBsY2sq"
        "JscDaRK6fIVJgDCVr/DZwUvSyq6EmLe129STZNFIxi0fqWO3FEGRp4Aivttvq6Nqze4tcrVYz34RfOQxc5HHyUEWEfNurlWn"
        "jxWLn9jLda5afRxVK2Z5tvEOqlZSQz6+A0/3ZdUWO0mEFpFaqiNMPPShhI/rUhy9TOIcCE/hy/RCiMZxunWrGuPAyro2C+pf"
        "Paxx4DV/TjUKaHFbEvcg0dDnhwtDbjG5igvimrajy16PrmAPvMAFkuxizd6B0Yzw3iOb+l0E2+CCqLHsLVZe/A6vaOShTkA6"
        "Hfsa+xoXWYmLDffxfvDtUeDz+bhKsMfzCw+Ogb2qdvm4DjU4TBjh8+DHjK/g/a56O7oS7Ogqfoe1gaN4kmfswY10ALHzH+Og"
        "8UJ//2GOBTOUES/5fRjdGox9ADPNT4cweBHHFz5mH0Y8oJyuqnQgcRKeG1ukEm1nOXMfcarZKPYsZtkzewIophPHGve2Wwor"
        "EuX9uHB+HPttSVY3rp1L2L3yyivSUVP6bHVytT4NLvL4Xm1PxHzRlWSzGUWwQI9FfUeLHlG1mrfmOVPoNo2krQRk2adn+x1K"
        "iMzKTXR0q4wZ1DVIdq7AL0AcmXyHk8ZUb5ygPl0sr91QyHgczHTUA1MgevxDG9QtfLUKxLeER19IjxEHHmJ0AdmqVKGifwT3"
        "QZVwmypczEAKz3YmlcMqabOk8nXtmGTsTc2id49/guwBOgl9M7wf+x7KXjV7y33UnB0WftAXg9eLe5+A5+NbJYm1JcLVlu1S"
        "wDm0SYw06IWL5ZJMSf28yU6pOieiALYyoFJO10vxBwgD+DoJ3/BXA5sKH33byrT+sxZJyEnwecYfQDTA9xa3Cx9+miRhrI2f"
        "CCepmctVl7CB0lS90ljwkSzykRhpTx1VrbbvVhsfYb8tvESMsndVrfpBEfbHx0UeI4LoaMESMVdNV5IYMT/btAelrr1c37Gs"
        "WrV9q9iah5FyNliYsOCJxkG1Sn7gW1MlqV0e8mEO3yY3mQVBtzEIqOq+12IUfQYA4W70qmDwiR7GOOBKIL6A6/gjXL/0uads"
        "P23Y7BOq1DXLFboe4zFZuYEIu/OVngnFNiJDYjWQAjJoksL9qCaJyabap5OE6ICYSBryKcBvkpqVCUS1Sao7e7l/ODYgviAP"
        "bD41TnAfDH+aGPYWxevQrVF+gTA+HROZQE/w2aoxL1rwlMlVJcgyMc7w8+NitlDZwBM8XR59E74ZuAWT0I+8bYBkfReqcp9I"
        "Av1BFiIozX02EGICKHhXzsrMoHox1QcqI5PjCiaQsnOK64FHxraubMHUqlp3b142m820IlG13v1QPA25g6oW42aqmXTVRtjf"
        "7kTYKfk+Rp7+eBCkwz0Um4A1FYJTuFcykiDpEPP0gCzdph3onblqtWlZLhvrOrDywbXyHr50Bk+eKddu1peU9RyKVVENA64k"
        "pkNgqR2ehmMGJ3+oEkiTSvWxrP+LhyHkdKlgHO9QlQqsk9BADe9jxR3ckysqwM2ypTFVEbjXbHfoSylf1USQpIaKBnmJXUYM"
        "wYLeNjgJwnu2InP2rgKdgpMYkQKMQ3WQg3v8kiTNbWeN7FmozgpadmosoCmlNUDJU80CZHnQI2MdEcSB9QabSHYyrlRTJT1g"
        "DtBEg2Ob3hmgSe11T9P1BC8B+uWRzIcM0ts3wFNePOqxAr+4lmn1F0YWEUOjgyHCjVTsR2nATRw8qQR8pJgKihTJFI7YTJeu"
        "NH1wkjLykeluU7bbu+c4LsvOctf22zowNtL0/k1A2GNcpDPZ6jFzkceCIKHDPbi1FYLzeR1nwT34AFYA2U4uVKs2EfHuTy+b"
        "tgvJYDXR42IsFYHMcqDvCtUwEd7h2W4nB0G1GdPVg7Z9qJ89GFDvjIFR4EdZOfmUcn/7IYyD7s7fwyf5J0i1M+EajHgDNcAz"
        "Jld8jZOPldlJkGwc8DtcMHAFFTlEgr0gRY1IN1yOduWv9CxlKgbkzwyIgZV2liamdBA9M5UhRqnLTKclePliny727fNc7Uqs"
        "DzgYdprmTPFoXrdqkKgK06SEfMGGwjqd+jpBAI/vBwTTCT63B//Qk8tAkutQohQ/G9zGION8ON1HVUnQ78JF+rE64hKMY/t8"
        "pdQ/x0KQQ7mQY3/KUFqnpqKoJg4YrA02y1jWHHppKudwgHWCDfpw/bMikW70yNCtvitZDJI5oZr6EXoYTRtZ9XTzxsJFPpTi"
        "urbn7zzC/pi5yCd/lW7cY29HRIl7xB66LfdYjpi3uVa5HvZuWWnuhiCSK+/SQU/YDtM3ki4bREuknC1qVNaDRLSKhXsQy0TV"
        "8EUGr7yPxT1KQ85Uvwwf/leP+CVcqvTfYnm+wYtGtXlIdSivca9pKL5gv3YYYwGttsDSDXBSJRseOO/qBKFkrNCxk2EoPBx+"
        "VwMdUqBIXVnEMmtBCuiwvkKggsEIXFwqk9txKshBewi6ZnGbjbdURJQ6YYoTtDBvgVfGAk1qAwE8lCkCMynuSVwwsbZeauyB"
        "JEAPpZMeFhYGS3vPwQUFIDFlvY9jQFdIkAQX/glc8L+Cy+NIaTcAu7+H/P2OlsBlRc4BwQKLh6OggH2tJ0lmdxFgn+lBNoGz"
        "h8Unxkdsb7M0ozvOrA7rWd1za+6m9NuyveVcrW4n+c+SizwGBGlqzNVbaqkjIiw7ziH/UCxetj0R840mEZFDa9hfiY2kd8u7"
        "MSDIFaa2tphmTWuehKtOziZuIJp9rIg5vIaeq2WmTZ/FTnAXyDtwQahnj2ocAOECJ/i/RuNgeamaXYPaAy48vUa1it1D2EWE"
        "EeoQ1SUb8glEW+EWiIRgBY8kNOOKXs8KrvSy8gMR3CxUGdw1N7VVBsEXYm2FD147yJ45K41gXRmWg7i3+/bxefi7uqrzPMHr"
        "FHidHv6Or0sBQJcp3bwEclHNeIMuE5UVOB4l1K4ZHJlCjLqqsM8K2BgEHukrAAAQAElEQVQM3cJNTAqoZSVFB00b5RQgiMcw"
        "GHha4Q7Eib9nUPQox5BVlgjvnITLhwC+6anmfBifcAZDz/QQyWEQ1+LczWbi2oVkLN1lplsfp2z7GvttzezO7CPxJBa5Wtfm"
        "vX+7EXbZzna4yDt7sn3VwrP5JNsnQ5DQjPk910x/Quhfhtt04x6HcI82Yk6pr043LNNJPJu7sYH0AGRuBLlQJZmkYbPoCUtI"
        "TJhTq1g/h0YKnHyTuq5BFt06fIY/DKGdCPigzx/q1Kj/Yry5h0g0K+pm15iOznoHbYEaDIpVUxhngWtn5p2JyJF4XISczGEr"
        "rR0vyTrJIFoBLbCGu5qBCKAG4iRgSrjCoWUyullxYBQLH1VEBI2vxY9R4vEs5QtGeandq2b4VDktTZq2iBIioiCQEBBMDGZg"
        "BFl8lVRpBpMr0gqqhWIchBckC6ScyiEw9JyVWnsEUy0RQy7k0xAxoL5BnjVw/FWPvbpAupmrBo/Lr+O7fPtISALBIE+S/007"
        "vyvpL0qNhI8Yys9hlOBMwl2FdI2LoCpnxrKBMlStFRxjLDO2cFACh3U3V2tctSjSxEVCB0WaCDtRZF9chDlab/FyJBf5ZCjy"
        "CRGkwz2Yc8WxaG/tiXtw11q8WuYe0jsXK4W0B8XKIblW1MeT43ZW47dpDye5Z6laiRdEdJhBrXI42SZgIXa9U6xq0wjRcu9D"
        "Dtz+nSMZh2ZDK/XX0ThUYxyKtRo0DpEpwTUmgdIlI8Xw6alCCceAkgSWPMssEQMOYM9EzuCSCh/b5VjZueKzf3W9mwAJ+g7g"
        "V+dmta6KMSJ2eLqb4gqfhqrYDvkA+4B9uryfP44oz/zv8DoZfqtnCa7zVcf3yTyA1KU1e6c6BMtTn2FBFzGaCATBAIhlE0QV"
        "oRDDc2XwUjV9sy7Dd4Ut0KYRP1S1GDUfY3M6xFJwOpgEcXguF5RD+Jq/JcRfGyxoAZzQ5DhvwA+Vw0h7PH/yk6ep5M6xzes2"
        "W75aK1m/81wtbdgge4XDjaT3L6zm59N5c3JykevXb8ivlxTjIqqJizQ17Kw8FC6iPvH2CRGk6VIiI9Iu6naOR7knar4/7rGX"
        "e+S6T/SYcWgNzqAfxEbSbKjMBgHSZIFtQPM+nPehY4Nmp1ZOG3AONhqQDF01gOz0bZCAXznKJ0+1/hu4Ttd1rMWeUYm5Ksih"
        "J5BGIaElbPY0xX6GoNzMWsWuH1CrImqkzsBdAuiBF8P3r4kYbqWqU+gKkVsAKYAQWXlPECFPTVhCiHHc54Pc70WOdl9MCjO/"
        "PezcD70syzZViQVYEAVokgALyxxmUCRJadksESFrNsJzZV6nSQrjIAfpGZvlri4HypKLMHHT9Z7D8SUnkQpLUbR0jJVoxTAN"
        "aJM+Drf1G0e5XoCsP+IP1L0xJWUmXn5QQ9yw1QgXwq5NGCB1Y4mym2pqUkOUltiILSYVuQgj7Em25chFjvdiE7qHjouox9MF"
        "5ZMgyLxLyXmW0soEqJhtye0S/wH3OCjusdE0mF5wj5GsHOQerEQLluMHjA29VYsTmbCBNFuBCoJUHr4tFBMs1IqRc1wEnkRT"
        "meMwjleP8sGp1OA1IiH3uoChzK4SOZgq0RoH1R+nZ1iIcZIRv6g11uy0NFSPFBwCcIwUBlK7XpV5D5v1VTrqhRqicIsUOREA"
        "AcI87QXYCYwig6nluH/VVxb7waovC16JmTpoz8fb5/Hv+Pec6ymvN74Hd23F8/1SPB/hSpVWqaooEeO/uhnnxr0EGxlkxG+A"
        "LsNcr4gJcQ+hA2gFMVqKuJhEiSgk/CwmxuAGvCR9z0i64OEbxRG4upueI4SJJN7DKHmuksywxVJF9y3AIxinSd5jMlocNQHO"
        "Sc9B1EvAFkdU0LOgusm4SFs3ct+4iIrZ4pw8LXGR5XqRRwaCRy8OwpteUN/X6tXz+tKl2/q1/Ov67XuL5m+nnorcYxVfItxC"
        "APqZDT2Zcjrsmqb2Sffq3q0ds7qR693ipBmsgGruTKzppyBvHKTs07ATgBj0iWvEPbI+VrG+gugbrBq8aBCk4m0FsTCovGLi"
        "sFaDwz42jtZN6MT/GN0qD24B44AvgzgBc4sWxpEokWShUs0YA9FcqKHVkhC7cuayDG4LNMw8Y1NbBPpxUdUZ3Cl86Kqewi3S"
        "WPGdymbgDoiN8Vqo8McZyUuB+3MVH08O2N/n8fj3TpclbgMiSgAEuQhbujPWDvZuIGVYGAVsBKo1FmJtc/hUzHL0qUNU3RjQ"
        "FUTTcebTmNToM3j2DLNYryGQM+kRERsFNweP8V/weFBvqFIxn+1Q95Utgjfh1l3iVQLrc+twZ3fh13lmNMtYrMAcZu9rFnd5"
        "Kt2IzKzASAsgLvRqU4NUPgUCMw4KXlVhp+HYRqLuwX2Aq4E32Fb6JEd75Wp1cgfu+mV1bB3497FTz+jb6kf1u+r8b6+q/wiX"
        "Xp3njPd/0QQQH357NAQRaVcDQaKTR/WKOvS83oNxjytXlisFYflt5RhXBI4aZmUZVwqZ8GTHwj0CuQd1UQS+1DpETWkmnWXO"
        "1pnMWEYI9iV6MDzJ0kdXwdsJL+ETHTvsY+PNxzi8b2vF5tABqk0yu4qwLhyumXK2Yxxmyog1J5EDIwpc6iUbGkj8Al5/lsGX"
        "KoqoNgFXyAm6iJFlWOmBGC1S7EOI/GDEOOzxdIIYmUyGxu1qE7FC3MYPhGV8MDCJopfIaB6QdVUBMHRqHatooTZD8k3g9iSu"
        "glFYGbtgpXuwllorzmgxTLz3cWh0iKXBvNbBUTQU40BrMz/X6vC6eOZsgey8KI32iBZA+DOenJGCi88txQPGsSAD0kOgp8Ax"
        "2fQcJkAS6cI/jJ4FK0k3NtaZw8iZSEs17IvKw9Pq0qX43vN6kQusR4rbG7Hl1COhyKNBzwGRc0lph7T7yte+pubqlW1a+DT1"
        "Ht2cq+GdQkYPk5CxO8k87oF4+Zx7CO9ATCMkLGhaxcFegeA/ZNvMYPWKkpY1YaVU+r9nWskhH5rDPv4CC9k9rJAzXA2zq7BL"
        "Tc4RSMhrBPkaQm5gHM4W2pXSyBn7Kk1zcA6AALlGHrkGItIyz7OENcw5BrhFNaBR7OcUn2Sfgo+UwwUHYfIA/D1DBEHUJYEV"
        "kBxZ/JOCgRB1s5qIQdWqhltK9wa/GwWLZQfJwA6SdX46NNkJ5BraHCMasxALyzkQKMw7z1PJwnPAScwAOvAvq0MWV82+1ir8"
        "L0QePBVXfxhdMmrXOsZHwkhZu5voYgxhEpzkYC4yq6/FOSRXEBc5DkXLgYt0Kg+79SLsgsJUePB1mc/edomXepE3Hz2y/ogc"
        "ZE/kXDW15jCOttZcpN3bQ30TZKrVryXn6l5cEaQLO3xM5lwJ92DcA2pGAaCguiHcg5m74Biy4nAEAQIfZ+pFmgROXgb55VcP"
        "Nw6pKPoxLpZtEMfKSBwAcq5VrA0HQa+m0O1ByLMmbgDjKBvjoEqlYRyMOxQ9UY3SMnKNzK94UZloHC3HOAgxlHowYhzl8TTe"
        "LiYk6E9HzoHVv5ym7NOmK0fjSNMKQUJcxymoRmYcjMODEiVYeAyNJZWmeDga+amaxqFZP0J0eXamzP+AsPp/i5f9fcDlfwME"
        "OC4o4iOKwDuCe8QuE55ZzJcPO964QhCs1d/mOWratuYveYsIuwMXAarAI8DHSVQ1jihCLmJWjKiXg0RTzWQN+67aFU9DqW3V"
        "Vh4qyf5WEle7/o8/w7U2lApVxt2kbaliu7kmss5iKnF0Hg0LHp6DSOwDHvz578EoQM7P3tY3bqzqc8kZ7UDC145Ntb+GAM/p"
        "Y1iLCyAE0IMiBNyryUdQrmyuZ7icYRNYCLZhEZD48lT3MqhYAUoqTmRwVR4cExBnfZekCA7avjVqwFSGDSWp633NmEjw61g1"
        "f0s1bS0esG3hDP0DPnkBxChgFMV1LYl7WLX0DJ7FNLpVUKsSVsxVYhwpkCPRPQb3IKXCnUrgI6sEcQ4fMj3GMj6FTavoTm3k"
        "fs4xGs7A68kmzcV/AKc46uPFFtSsFSUTCbI+1aupSTIGUnG0Bs6WE/hSCFVXhqk4IUNYH4sKyLAOOBG2hxUbhFn1PZ12rfNT"
        "ocZtGofOaq2/Bn+Tmc7dbF7EQPQpHJuPEROaSVIJy+BhK5ptVRDfwAV/DDeyB18q6hj8vJ/jvSvEsBBm8W7HgmAEkhvmrMEs"
        "PMUAHblINYN6heeVPQ8v0yu44Tm83xFU6GPZ8XBv+2Otprh47tzFVYBXH91Wq8NN+IV31fpzJ7G/pZ7aPKV+6i6r1779dfUD"
        "9U8KHFkxj0u9eV6L3/WQ28MjCJ0r6c7+b2NNcDsRqu1zxeecRnSCOnWTOyP1HndY78Fb22p4a2pZ7xGm39Dsm9QvgplVW4aj"
        "ltWE8yp7gHUGgLHClEG4B1eelyAZBSyDmoVAIJcI+/4Si6cP+cSUdP8+0CvXUAdCKK8DIRR9aQPjMGoKoo4wFRs2062SMQFw"
        "q0jKwTVMHeMMvkJ0oQ8RJpN4RCUDNRuusXGwGqUO4RRHeVyMY3OBHAwaJviPyEGeVo4TOFQG8cEi54g4BDgQJVR5XbFvVQK1"
        "D8YB6UOQA27VKefg48KtgivltHmhCjI/88BN0k04JEfiIsG1exzyGoj8M3V4zhbD8r9EFBHEhzdwpmabVx3nrtRGRtsRClVJ"
        "j2Eg1wBztJjFzb5no48rye6+8/Nblp4Ht2PHG7rZrV2XX/b00eqqWXIlPLza+wgI0lGvLkT16kdAjldOntLv/ahVr67GyPks"
        "Rs4nd7fm6tVGf6i3PrpnVtcH8HytsasZ4h8449CtAsQhRn+dZ9ZGFuMfGXtW+QGe0V+JqSTQ7NnnCldvUL/F2uwHfVy4VR9g"
        "Eb5smJQHtGACIqTlWRMpn1rlZiCms8TK0L7CmhRGAsEIoSwHSouFeo4cFcLBmbuHB+NVWI4Rol5lHOM+atQn3BeTicnW9iAH"
        "JLjSMcsFxuHpPCGCDv4AKEtrVebW5DnktT4TBr31iLABOULVZ8OFU5BeKf2wLh9R9WdKr/5AsoPvtwEhQKw/lIWlYZ6UAWXK"
        "FahWiAkBaw++XNQGXOSfwS3jkEU4AQFH0ILJKUgHfF3Dgkqo5CXnmQBFIHJVkNxsL+gV4/P+MT9GJJ3TSXeHoKSI5syoZh2D"
        "A3eLCcpbWo2CWt8ZwYEOULO2gCR99cyxKdQsuGa/PVIX34Ih/dGjqVmPwEF4lARCHqheyca0EurWncj5Fu5u1StGzrlSMK09"
        "YFEKPH0F4x5YVQLVFqwwlcptotMznlVDJo3Ekf2b2EhaJw/+pGGWeP1jrHxMJCy1D+UVaWFDN6ueWUb5QlokaTJDIKNknEMX"
        "IOII/LkSpL7iCJEOcpQjJSpVixyHxDE+yV6QYxjjHpnqIMeMXuCKLUtWS4UEtiOTsfCBc6jfOXOeoGNF5MCCIl1cTNp7XtM4"
        "gMAc52CSY/gevy+BwMPONtACiC1Rd6p/HMZDwYMj3qAHf9Dkrz3oBVJc8l/n7HHvsQAAEABJREFUOdNy7pL0LNDEMRUf6wt0"
        "kIgi+C6J7yeB0gOHqjKLu5g0kfWR2RE1S6kYWZ9IZP3ALN9L8dd5T98LSjVU+ZFysx6OuXRyr9qa8/vmXkm3kkXFINMFOEMQ"
        "XxyRtI9tvzxtfVogBDhJ3KyHqy9FwC/LQ1r1sdYPsOqtOEPlCt535WVApvO4zVb9Sg3LEP4N29E86OMyogsf+H02XTOiVpnx"
        "NQNVpcISnJpdxPfABTVUKztFaK1gCnmWIAjoESkH5xC1agRXPyEhH4Nj0DiUBPsiZ7iP6jTnFI/2eHSrGvWqWtelaSLmGZh4"
        "kSZlOUuTQc9UzLeq8Sl93UO8Jq8RJTe+6ntwNjgtfc+YhVG956swQKQjVhNCcyy8+teKo+AOvTjCDPGd/ygj85rOjiEijmUz"
        "CMWOKVo9B+v5xgNfR1PR0v8eYceJtDqCmnWl1iOIz4gBhBHAeBfQONLyeDo1RVmwft2G7dImg3paXXfr2aYbF/AKV4Esdw5X"
        "s6SxAyInSu2tWX+43KyHRJA9NedvL4xj3imxaSOqRL2Kf0X1ijo2VwDP4CDUK+rcYZjq6Q5oIJkFljTFzI4pxy7PsMJgoZRA"
        "Vpm+iEAXZwIK9/BUXdQLhxkHTy4O8mW4zMxQhUCjymuSYBgKlsuCRFSw0ApssUy4OhbsBpG5GotT6nMHChLVqqDnnKNFjkeN"
        "Yxzl8ZZzEDnS6oTUFhE5mE5ii34COQ18fMUIctQwCnp7KVUrGofve/AO4RyCHDoaB1U/xwYXagXG8a+OYhzccAL+ml1Z2K1F"
        "0UWVvQeKcAAPs38RozTm+mFZv8yNc8GfwZXJ4UMp3L7sBcNaMZ5f/JgsCeMZu8RbXgMK10Ohx9QXpIPm8OQvK6pZK1NcI1t7"
        "1azbMUscnLerZsn03Hfa+SL8FE1u1kOKWQ+PIBJ0OTz3SqpKJfaxmOvB/KthOrWxz9XzkpxWT1UueVccxrJrBoigD0IKrR1s"
        "LXCEMZ7FMWhQ/BHwhSiuzbDw4TxO9vMP+qjQ3X+KwBakXTdjfhXzIK8Yv8uWnmziJsVOQfo6TaGuzDJEzB2CgUxNrwFskoLO"
        "OAeDgOm2IAezbh+UO/VJ95J7NWyRwwM5hkvIUSHWYb1NBDmUA2IEaj45goM9q+qe0wkgOwzmyOHY7A2oEY0lx3H7l1hYDg2o"
        "cmMzbcSE3pVukE2zYAYScHEn0m+Lsxm9EpcX7u4r0LdeetDr4c+vZDr8BUsHNJvn+Wr0vtE78KZ3EKgfJcrvCmn1PYgms0mb"
        "5Zv086LbR+t4FXOzlrufTMN960QO7uX76SDIvHKQ/1yIPt79cq+4HVcL9Srs3NBr4CBUrwZYDyezbcq/poALG8a4LAkZjJyn"
        "bKPGIFWZWAO3lGkQaWq9jFqGwMUTbtSDqwQBGziD78cWPVztVHnF87rDCug9R49VuPprtknRvpbkw7qswDlYOY6rDnYtKer4"
        "r6zuxYu47Kzwn0Kco8s5EIwEcgw7yJEKcliot4IcNk1rKYIC9/CNcQSsRFrKjJkxG5GDjblpHFQefPjdoxqHdJw36sesMsSC"
        "Avsjf8NPEAQp4F9VYCEk2PI4KOSHeM8H9gT2HFmHmAy03kRiL5pVwglC9A4Rfo7hrqFm9a2IBlSzKiiaea7matY0qln3dmeL"
        "7vBUs6SSvqNmkQN3c7M68ZCY+fFwmHB0FQt+6JtM/Lr9jr746qvq/Nlz+gfqjj53e6R/CqQ4a07iGM70yjriH/Pcqz3q1c5Y"
        "r+Cy33XPmgHCfZ5dOKu86d/kIP1BvTIJD2Ifljvg5KcXA+d4QJbAiWa2KbSLl8OiBOvADXzjGr7YVS0jkGGBUKx2tWKKxAyB"
        "+6lnZaBmajv8F7hfmqUUuaoRaq9ZuljTnwPv0LBPxhIRhFMZruIyg7oEPaiclHDocSfDJeCVjyXOsRnvTz2Roy9qFb0YOHzR"
        "OFjqUXEGO+JErM7F5YPgaQ4XB8ahB5odXJib5tXgedZ38HFWClqdwr/8TSybzx/lNENleh+Rvb8Rw9BwrzSoDkgZ1Sc4w2ws"
        "zKbzoVlZtTAMzeR4v4JfVx/40kSMoO+R4MPNrRHTqra8r0FnKkTSsXixTB9qluRsKadnNRarGWTGFCHZp8LYzsKGS8I2GOOx"
        "DdfkZtVLuVnrq/0YD8ElY29cVrcRD7nUxEMuvIXr90/+rXqYeMjREUS3lYPMCrwYOya+06kchO8XH70RfcJbt9W831WTe0Uf"
        "chf3DE8OFX3LUD5jeB5EveLKEaheWdBkHBGOuneWTc9gQIk0aOaoZaw+h7YL5cBKNpAWGR5+8jUVJPcKQalKWvLUumaDBZ1k"
        "FQWVBFd7DfpD1aqqJoIcjFQH5jrhszNizTiErOxEEq70FDixL6rCMBR9ELI8TJyjixzx/VkQBawV41ggB0AR8YRUiqAQ55Px"
        "cbg+e+QejHOIWiWpJtKDOK2c+lUOG1VH2OA+3YB0+Fd4HbZPFdTgyGgLDLuK2NGVwB9yEVNKXMlwdntg3jAMyFw77PWhZr2g"
        "OE8eP2ykynapWPhSwzn0U6hZcv4DR2sbJlFzIMV0Z8P0ca2Qs4K6Kia4ksu2uVmyMR5yM/7KeEg7oYquv+rGQ+Zy1tHVrKMb"
        "SNOQmpoZDbCbXsJJQKewqLfpJf7j94V/3P75FfCPnr4n+SXragdEi1/S37ll6BwHi+BgD1DKMfdwJ9iMFq6VdTlDYPSAIA0y"
        "pS5Ia0GOO8bPXNQ7cBNyHtQtI22lpE+utAHlighKUYPIYi/ZkA7v62iNjhWAKxB08SdSuadnOslZuVfh7FFDykyaDxGnXlFp"
        "vq6Lqg/N2WM/04Isw1IRAeAsw1gKGFXZIMYR9ptxz9eT12dloWVgn/UdM6wSRA7WQnlJ/AuQvqWugw26Fat3qVbZPrNsT3nJ"
        "boaUK3GiVOYqavV1dYQNx+0OUPMvEYqoeeETWSELz1iTf0VqZnyhGbEyqrzqOYkKIVxHAh/owrIP2G0GYR/0HjjLJwNzuiSv"
        "C4ElnF/DqjJ4aSpnay9WCQO/4HWpOgYN82EEK9ZV72J1ZQr8lrzaprrzwWRejkvOy8TY5ygSXWHIQUU36xyTTuL21iOknTwE"
        "B1nuezWPf/zsZ1Ib3FYO3hRTbnKvmoin1BbDd2Rq+/blezJTUHxL+JjFuDAsHZAcBjYdqHF0qoSlcbhGvHHkIGDwbMzsfXhG"
        "HfLt2EOWTaHZDpRKizSONuQfbMmTwjgQ+ktS5+oSIIKFii14WD8xJedIsHIPcJFmsoJL3IHZsu0ey7SUvxoG64aGke1isi7I"
        "IpFuFZEF2GgO5ioHZ+cKcqgmO9fh4+HiKEnIywy0yFsxjgLGASHNIUJuaqhCmgNI2ZTailolxkE9MPgeGzBUSr8IAvYtdZQt"
        "qN1U6+8DQQpe5EBaXPx1dVUlHNg5U3H8ATu8lKxl12yaF7uhlFS5dPxBoM9cP+R96AGcEDWLIxtEJg5MtbOKNV6BxB9L4gDS"
        "BAdT1A1HRTxkcndb98vS7mzc0Wu4lshpRcwCx51vTzccuImHMHFxHg9Rj9Y36+im1K0eZHnt+Y7E2/a96kynnStYTWOGtWM5"
        "+xnYurxnR+lzNmf8oxjmcHkySK39OPpLDWtrVoDFa1hPVs8YswpCsAbOICOJ4ZG/5kJ4YFEULOkHNJIY9/CT61qPFbNIAwIZ"
        "gU3VEPeAgoXTUeg6naXgIfPKwDxWBtK9winb021koup+6uV2W2OuVKwc5L7J6pU4iaS831/1EuMR5GnUqozGUUp2bgKxoqSU"
        "a2NloFT3wfUAL0b0OxEpVxpVaGmpA+SwAyWlx4wdifRNnvYcSPzvHOX8coBPqs3/boLfBq8p2FaIaHGFswoRqoPz5NokeMTB"
        "2TQbmoDO+Z64lrPTRDAFroP3hpU8iwXtnz/o/eBb/SMH/zDLV3s/wgvufGBqjnrYhQe9jUg6oKAes2Ydq+OUAG7TccF4iEE8"
        "JGniIZuIhyxVGh4QD+n2zTr/2tfDBXW7UbKOHgt5OATp1n+83Zn3wdpzqexaRNC5CX402bvb2/gd7tVYvaj6syb+kXORx0Ki"
        "pa05IJWNzr0V2HUhYWUrU601O5ZrDorxTx3yETmk5o5h5Feqfdh1Hb+7xOH16Cdjh6UZZ16qr20VYrM2IAeutYTghYszHWBp"
        "NHI52sU+t7bI02KU4trtJ2w7kGbkDFjxJTeKyLIx5ywtVxGOQqTYG+dI9yAHEQrvHY2jl5SBSyo4R2scisZhYheX1PTEOKzt"
        "seb7lETMY703gnYn8KV/Sx3NOEp81u9j6d4R5Ahx5NoVo2dShuyxgKsKojf2lSkgmTMLusQ6X3AYAQkkG1+wC2qgcqgsNaUH"
        "XngcWMRmd15GOLAyGE6jgxoCzgmZJOE1kJhe5KQ5ziCrfhgPmdGTi/GQ9fV14bSxA+Pdhuvejs73vFb9Q4GQFkEuXPj+vD7k"
        "YSLqR1WxcCG/oS/c/mOoWFpfuvMDxD+0vnj7tnrlm0CKrVyvbHJsBJTuVaD07ZQ8DsEH7Afi0ev1fq23Jw7q1XETVkqw7Q02"
        "hcM1ACI+sRnAFsFW9t5lpWCNFTL0V02C4JZIldxncBl+/UG5VziC96APX8KvFf4e4o0pAR2yIloDshF0ATxns7aK7arwpVgj"
        "Empc1qmF22sgXOGNoP1aOsdV2LOHu5OyN44FvwcrqKCxihSLF06zWvIwmIRawUAyxOJKP9I5XGpRv1gJiP9EBWvVKrXecBz4"
        "kxDWYGI20USOIrEk2GDheDYu+jwihzRlw14ZqSGHZD1kozbP/DQS9RA2ccX+HvnHEc4pm1b8JUS0O0zajCkjvrhq1RSLR2Gs"
        "w/FypWXKl3aVhWxCD8myHtBwdCIrdLVZj6SacRErLpSW8PD9qw5D6EnpgaSvOJwnXd1DyMmyrDAkDDzivM0YuIVqVtSUC43r"
        "u3wD/ratfX8Ywi4+is5X1CyMYORrajbeUfqpTcXG+mblKdkf88+o9z8u1blvvqh+9M4H+HTPqovvfqDU35xTb/7Jnyj15xeO"
        "lJd1NAQJ96n/oHt14e5ilBqrBznvYx5Bj3uIDurezxhBJ4KM4509LFLmuGTvYmE17N4p8Q8DYmpFY6WyaCXlhxWaHi9zyPxA"
        "vMgdrA5egltxuoyTeRyGQqWT2xxBYNlVlF616+u2vWdVTiGVGTECqauAamRZUDuzGfSCuMeKLiu7YYMqbZl1LJHt3NkCsYqI"
        "AA2iQEGOHIXcZBFHWeIcJiIHNCkg0QAcB1cIexDifeqaFzmCgQgESrGTd+QcQBIWOimRvU8pK7lVWjq7hBWoEb+HWEd2lDOK"
        "heQHQFL6HGytWl6FuHHVspsLkIJ9dlhhKX212CQvUMlg7X6Fg1lQDYSiWDE/S9YTHFdyPshg0AT03Qe+syb/MGtyTpUM/zEm"
        "TTXUWwTCGKAH7ydRZ+E8uFhR8xrtKfbwVcMhO5OqlY8icV+KqEM1ZWrT9dvL9SESUVC3zmYAABAASURBVJc3flu1EfWHCYcc"
        "0cWKEq+oAKoh6M0jVAuWyvnhXbV9r6gykH8wuEP1gRvVCO5ZmypeORtCpTxjfVBriCcuh2cFN4sgy8JoOLpBZmK4Q2dZIAZ1"
        "2xA/g5OmA5eZnm01ri3xnr3MeqKCSItn9QOTw02diFFQSmV5b6JBcGdZgFRaqSJnnSOMIO6TIF3ly7rIrTSLKDNaM+sxaCSl"
        "MaI+VVlUwUI9EvVLiHyjes3VKmEcwIwVp+m28e8rGBa7kdT8HBlJuU5hZbmrqj5euYcvMqBhnIa/f5qNuY0jF5Bm3aU3v8e+"
        "YYcdI351+DZ/Cz0VfBYXPcK1qu0Dxo72Fg49EIV9wDQCUzgRBfCukN7D4MyIX2CF5yx1Nop01TU8SUi6l4wtb72/c9hH4LkM"
        "Gq/sSdS1PcvxQbW2jl4l3SycfkmjhKnn2aqonVQ9/Z2RLLJUQ0nU7354Q1RST7VUJpTdhpdFP+uGOnWaobIPRWUVN0u91oQ/"
        "GqJ+RKn3iAbSzBzs9N6dR9AvqUUEvdlkAMe8/gMq1jq+FNSH+s49SzXCb5d2VoDIT6S7mpZOzWXNalBRs+CNmheclHhyjpIk"
        "WcOLOXTUsdHuXhD+AbrCNGqOOgOJZkTFs8MzAvLOlbxANJV7xANZt40oYN/Kik2fn2oRrKV2lcilTASEExH3lFBJmnEhVrDk"
        "CqevrB3IScqkroSIIupTQY4C91G4CRAiX6heEudoOAeUgMY46L5Z4RxVAWO0zM7F+/iUHbGAIJn0usVFDM7BNqFs9ubYSZI1"
        "HXkZwu/iKB1pGA4g+B/xRT+gMiUNub2diVrF2YpOspsRgFBTNslTLCKDscDfK+kRckgQzALBQ3I7X8swUkTT2Y2RvxsqWeYQ"
        "BInbKouwgrRRgZeAc80O3/AzWfojZYyyaJKfbo8teer45ral274zLqIaqiK35fbAiHpTYfh2B0FEjdVHg5CHRhCReEnQlWpS"
        "3JUgyHPPtc89oWQJwYe+JxmKkaCv4j86V4MB3dMp+FceEQQuSSjgRnGyBc4KZRy2CZEuNEacXSOfU0PdevAnZLWgkx4vWiY5"
        "camEnws4of4C6YQDZlkBKDkRdEQA56EqQYQnKYKyCbgF1+2s9owzMDvW5zZLe1DWerL3nEEipaS5jFewLdJAp2TE2sJNw55V"
        "u0k+YIcHS4SQbFwYSTHbTbiXOAc8mKIIETngWkkfKxuRA9dgJg2gVcExcpB0GdeoGSLunSbngHKEtTfD0cqhY/92UOpoKSRB"
        "vQvj+Kk0rJDYBnuBKXZlnwLuZvg0E2v8DG5ckaR6hrWlJDdwcMEsMzYtb3NSFgIXNBJis0hcHOhLok7iL7UjD4yHeJlsxfMq"
        "8S/pOGRxgmD0LDsE1sMLbhMXaSgjkK4GGweziQSbaRok6uTpd+lmiXxzOz7p+l4EubhAECzyb3C1f+wIopos3guLHCzqzC2C"
        "CIQ83fmQzcQoZl8SQag+DIe/rCYTcOZ+w+GoUlBEpYKFi9VyZCVn8UlKtZb0avi+JIOaDRsO+ZBjSiRBvjnwHouS4iuzXQfi"
        "i8I96F7RSBB3qcd1Il0/cAUyozTWcFMFgloEKKs5b8Rn/ZoND2gU3ENThO2yAUKfzxNjcabHi7p0EV2Eu/g8KUu6XWbOTZhT"
        "leL7xiZvNB7WgDXIwQREGBY+KpBD4XNY9hfOOWcDFycRAz9J73QVBpKLxj5gQI9a+V/Hez59lDOIY3oZkuCPBDksy41VwU6S"
        "UpMPdwqryQzSoVRWJlCrHJUClZSssGRinKuwktXiuzZDgjinh8NJEbFirAlBKsOhpIxBqUgZ7n81sdmfkk4q5CI8wY57Un02"
        "eCQPwcdKpT9rVLIWJ/lFFSvUGwThItysDyegnkpAfalfVosgalGjLtvjRhAl9IZ5Jg2CRBYiCNJ8qPjpGphTTHG/t0AQYejj"
        "BkF6sQKa4Tu42eQCEGKgaeB3n5g4+o+SOVOBmI0AOPbhMASZhGYlI0E3HDIrZ0BzCG2c/cfcJzYbhJXAm0NoHSJNAi7O1Gtc"
        "jdIFHW4VibBJqj7APwbjAkdJt/swcDWu8GCHYixAkrpy1KZyiXSDm5DLWDCWsqzn3ASxvqQgUmBfQohIiDQtcvDv2IVEkIuE"
        "HFTNOnCOqifd2CvF+e49b2JmLlZauFbhW0dOIVHhJnSQv8FyUzDWwU7111LEidiez5hpYIsj7aa44mcJAkKIorI/I4zDyYBS"
        "ze71SR+6hCgQoHIMHeEgGidurAz0JdejSysx2kMMJOiVID4O6+LY+5Tj4AlFZIq4BnRfHivrzvXZLKrM0xcEgW1IRF3c+OjV"
        "3b7VrNGCIPwFCKIaBKHUq76/qJ5SjxFBDs7ijSzkUvOc6/Ma9OUcrIgg61AfdkWF4JELZW6KEipre8nTG3UMOzF6IU3K2tCU"
        "zEmWIKXVDxwRhotgLKWgntjDHhxKRHecPZkeixU/sLOgIEjC7mWIAEBjxsXOa5UdBDI2jMBSKP2bmNtkYBA0CrzqECvcUIyk"
        "0itGZ7i/kolWTowE7hfnhEOOjblSDZKwKLHhJkxVJ2Kw2AmR47SNc8QcKyKHad4/7bGnJOMdipOaSMoNO0cSoSB5s0JP2W/g"
        "axwxhUTdzbT+r4EjnHFl0526xnr8WkmcI0gnSTV1LpkldK+mNbsGFdIlniMdHLVT5u5WiBJlsB2QBmAihQ+2UcaKTzLhSdJ1"
        "05lRuuE/eMubbody/Wlm0TPDhZ5E1ow01dN4bWbybDXdyeW2zDTEWstFl+oo1+BjrYfZqKcHd1wEB1EPn9V7JANpX4oIMg8S"
        "qkWQUD7TvEgKHOQOGVNEkBgk3BYEocTLICGzFhhJDhOqVsy7iccESxNChOwzk0T6QRWLjAK3qLE/8EMGxQS6IH0A6afJ4YZU"
        "hh+PFW6OIAh9OxojuDvOujXiXoFD8OJELB1rvazg8B5AxPVLQScvSK6ThbsTOHtd0jyGsC4YRwlEAdLoghptnzlSkdDPesJN"
        "LHNnGm5CEZOIkScJwpOpcBWqZDn7V4VMjIJunWZdB7OXraSOYCHkBF8ZJYeFiq7XS0CPo/Qf5lK9C2j6L+ziAsmIYw9w1SEY"
        "GHD1aYXwMxGknkHlLgQ5KhhHDuOAVo14T0WrgNqGjwOIhxbNsYuWtQSuwHFlv1UjeZPSaI7/AURi3eGhZbjUqNgUUM5xdKsB"
        "TazqBTYpqVDAU2asholR3V4vkpChilJvF0HmqsCtJliougiCxZwjo9lMrkGQN6KMpY6yPVQ9CBGEEckWQV55pYlYcru+yOJV"
        "e7J4I4IQQIAgInE1jItWIrVo9F5rIwSawrjnihK4HBkBYPpZh2w8qsbLiZJes7EzYCIzl9kWQJCjRRDpf0mZMbFik+xF7YoU"
        "NpJBYs43lH9tw5n/x1Mq/M/Hlf+fjwf/f3/G+2+fDmGIa0HGS0OhHKhIoAcI/sb0D5+KuwXlNxcVDDxFJGIgSjlzuWmQBUiT"
        "sbG0qFWezdykfxWDo0AOS3WqiXXAMJjSwX3goBv1fE3F8ggb1okZmP5fcCwzZxIiOlQQOa4wtwocRLq54DZVq8SVhRiHIAc7"
        "SPrYmNvk0i2e+QUIqHuOkQOTgufJbsQMgZOfK7J/T5zn+3r2QJHKwwdvjKDLutdwEJ5vLCJ4raasBItYChJSlS3fn0kF6ng4"
        "3ocg7XZintXbmR9CDtL07F1GkKNtD2Eg31vmIHhTQkiLILFRw82YZtJBEDVHEBU5SPNl48b+ktzHTstSg8MkaM5mZXIvsYOG"
        "4v2hwS/41nXsmtlMIm++maWayzwVUa+84AqrqYgkEI8jkrDRacJMD5U9r9Wv4Yz935QIjXHDGc9wmv7HQoXf4WjpU8oPT1s1"
        "EGPRdiBGwtyopIBxGFG7bJZwfjuQBD+UbBu1K45HhvqlYECORkPOQeRAEJAJh5YpJFCtLFNIQt9LLhZT9/QJHJ3fVEdNIVGa"
        "swPZ1aDAalFcp1vFYaRUrXw9YxNuuFwcHlQ47mkcbK/Kbi5wrZibpsE1uOeIesT4xWG1pDCGCTAirVDACjx94m6pGOQKHL1w"
        "2GeErqgYV4wvohA+EoTnOeFiqdhTDhibZk2sQO1FkPUOB4kbOUiLILJdUpGDvLPMQd54iNYmR+Qg3N5c5iB4U9VBkHZew946"
        "ENVBkJaDyJctm7CGTIVJxMVqfSgahavK+WcL+vABLlgha/kDsQ0jSyhPmCBSixw2NjVHDNhArTHMBYKmxKhU6pk9DKF3ZvS/"
        "ud97wAH4bVxVv8lJVrhqBmc4i93RUMBFFLiKJ5FPaCy9emqHUeUCL0mi0QQaj47ql6hgRCAxjgY56FoxhQSv+5xjAieb49Gg"
        "9GaTfHiU1CAH7vBftKytRmYtykxCJm96psZSO+VIB4+oJbu5lOUcOWAYAFL6fzJvhNX5dZABUIHN8uj2MNgaETlh2ZOK5as6"
        "Nov3zfHV6lAD8VQPecLEgWYUlzw/aYw/iW3puUY1CDJrFlW66RFBtiOCqG5diJojyNXmrm7Xd0EQpT4DDqL2I8gDOYiKCDLu"
        "IohgQhnTZZtW/Q09iweZuoYQOauO0p6GiYrEfP4atKxuslnp8O/m8Q/OFychkTnjsIrIeXApspw36KcOa2MKpec38Kl/neiB"
        "1V1Q5JT3Q3xCiWhr3XCTDFyCvXCh6dI4qHY5ackD1UuIPTgL+ItJ+PxauI0gB/7+OfwdXHLOC2Q8fqVy/vdCc8QO2Vhj9wNc"
        "brfZQRLvUVxnLlrg0E4gCNUqxfiGm0IwgHEUQI50JvNOUpnrXmfDGop2kMbcLPFLOTGB5EkOJluZ4Hhi7ZfFxrTBhBAPNzwm"
        "DksMcYziIacLH5Vni12xgfhG5EcXVwDbjPmVtOr4tXstgmCRVV0EwXa3JSFzBFHqlPzb5SANgij1GXAQtR9BupWE+ziI6nCQ"
        "5uvGreEgzeXPi5krPT+Ub2UsuayPFNWZx0ZbJJG/7iJIc7eR+2EcvJ/qCTwwDhFQ/mi5m0CSXwP9/zUvkqvUfvdPOd1wE3IU"
        "26heVLt0lIbphoWk2dfN/U2cQ9SqShBJBmsCZfC6LIIaPkwKSao5iDTIUCDA40xL53o2yEPcg+XGcK98qSQ7F6GZInGMczBl"
        "2iLuYVyW67oeWZd5BMwTdgJLfcUBpKyXIRYjdISj5lsuZ+aXT4MgTQ45VUR1lK35c0EQ1XBEuSciSJouEKR1y7nIqr0I0uxP"
        "zBGk2c6+sB9BJA7yeXAQ1XAQ9TAcRDUI0nCQeJQWCNJ2nwhHWJHAXjiZXF4neLOMIJyG1iCIEuPTEg+xtQQlJdPeSm31SMUY"
        "1KEbPtC3Ee/4VY6cpqEE6/vPBy/uFg0FkDUQtQuG4oIbxvqNZi/SsRcjYod6FaRvFdwwqauA0VU0jqz06nceJoUEC/oHOqaQ"
        "yJjoK6bhHLZCnENPZN5Jbqae8Q5EyKFhlKJWgXJnA3Zzwb4P43AM6cOt6k98nI24Kws6v7WgDqxJAAAQAElEQVQcRxcR2QuH"
        "kBPULEpwhnEOcDwOzyZmAwgvJ044IycwuNYzcxFBqg6CLDiIWuYgqlGxjt+Pg6h9HER9fhyEn24vB1EHcpCy3Ikv3nIQ1+Eg"
        "HFehzXwuHhN21CEbjnHSwHxc2SotJbyIXukYB+kiSIso9H3j871Yj+cc5D9VR9ycNr+C9e1bbLET2GIHaPIcEOK0GAnWAs/B"
        "olC7Qj7E51gJMqqaAc9sRe6X56WD03juKU93LXIOFfOrfucoM0+4MYUE8utPJc2Dki4M5ArHViO+gYsNyGGmcQApGXfMrWKc"
        "I3UZK98r4RxsdcQBowURg+oRs46byVXziVWsvEQM1sd+oca2brDuDqzi+TrC0E8Vw4Chw0HmV0DDQWhm+ziIOpiDML/pC8NB"
        "2jjIPg5y8wAOouYcpIsgsjCUjUZH1RwUwLHFLK4QlujFlF/eb5krUh76GRHabsreJDY7h28lwUbFMrg5glCFMS5m9zLzRygL"
        "3AI8gHPyAejnf1VH3FxQv1yxkkcx0m36sJoBzBIXvR6eMg5fV6+cMZzC62SeCfdnbJD78dyV04YVeU6q8qQLCZCjCuqfPUwK"
        "iaCHYQqJn0LgLq5A4pUSSEPjsDNBDuOm0OvAOTzcKo6OrtjnqJKpvOQccKkqzpiAkdRJ7eM03jLW6ONaThDQZ8ZDgqCdF0EF"
        "x9NXMes2kVk7siDFReoo89VpAdFD8CBpDOc6SAHs7iTClktiPD6LinFP3ScOog7iIDc+Zw5yaBxEPvY+DiIIcmeiooqlFnEQ"
        "OikqSuCOxsE2DUx70/SG+Js/FEHYcVGCi4zEe9KRIEU9EkWxewQAF/cxuzeB/cHBjrMwJFUlV+ZvQWb/T3XEDS93rpL8TZJq"
        "Ke6CShWgYtmV57Qf4uVXnlfJ8LS3q9zzNu9/ntwEz4vchZFzJh/qb/lD2hq1G1NIEFL9Gw4DYj05vmxxxTKFBJeWM9M4MQuq"
        "lZcBpDG3SidRrSJyhIgceY4gSMHoBtkHmEfT1WU++xDvhbgOYdjUrDz3pPUEEPpZzIzGcXZKBBUf04IOc7EYyGURgpLEUpHi"
        "mbqN16uaU23LsDcOgo8cyEHuFwfpIohsD4yDfNq5WHMO0mwtgggH4R0HcJBbYzXssf9CVLE0tXQKoC0HYRm/YRyjjis61hQT"
        "eYiX0tkHf0TWt2sZ6c3wO1c0T+NIKKubGEmPQUSqMJw+pqQ+BEyAe6yksFAnxT+IvEOL/bvEqx+qI27wxn+pDvoca7OlRjsG"
        "+uBChaFnBF6zK6QTBMEVgftN5B6KkfnQ51CgWodX8A1ePsr7tSkktkkhYWT8ShyPxuYKhaXRhCpKukCUxCrJrUpdiBFyJpnD"
        "evIVGMfICnJU7CSJc1YlW8I9VIsgLEeWdwUHyXLF/GQWrqia80DZwspwEZIFiUwuaPVgUUEiJ0xsjOeYwwvZEYN1uypN6HSJ"
        "pFzZqc8GCFgVvK8niLKPg3RyseYI0oYcLn34gDjIp1EPwl8vdDiIeshcLBU5CMSd+KWbxSGReZzwkcqK5SCsY2VmT4jvLCAQ"
        "WNzz4E9oBtKZ38Q6Ah8FeSZnG6aJCoJUzA6OagnNh6nwsAiH4B6ETFtzfviHzGtgsTpOPyTTf8CF8ffqiBuM5BuIV3xTsUSY"
        "ChelYBgHvsoQwAbkMHHvaTRsEaoHcaY7s4jVGcDYkab0sgsJjOM/m9AOIrUI/jGFBHtOytJ24sE9AJEF1nYYBydmwa1K8kKQ"
        "g9N5qWVBtYrGwRmLwA9oFCV5cRkRRDUIkvAPWD8DScNVbDuSawI1c0KJKuxsHftbm5hYqg4ZpgqeFAUv5nKxrpDZ1nR3cbuU"
        "ui0l03q4UTaBWzdzH8ntfRxkby4W1+jPOhdLttjxZ389yN44iLpPLpaKuVjxyF2fZ/PSsamJIGHKIYIwjpplAZIHrQObh9eS"
        "GY3Dv/ugj4cnD8UiCArsPErOzrUsoc7uRK2iSiLulivwpJpal7dwGhzrsmhKKkGw27srms3SAsdgVsCffwQL+kd1xI19qPCJ"
        "v+4ZMWeXER3jIzjSfXwaGIsWw+D9JOR8Hi6OZ+B9f+soRTwxhcQzSj7B86dUrYgcVyxTSPxUpvPiKrNpCmJeFglbqxI5cki5"
        "RA52b2mRo3QL5Eg598SFHCJ0Cb9fkCMlgkCLw2GxKeKwrgYgOyN1O8x4sLIgMVNRSfKIHHca0oMNBGg50oISrLNK4UU7Ssee"
        "OXOKXWbrGJSMqaeKJYTzCok5gijWg6hFPQi3W4vGDftysaQe5NPMxXozKmT760G6HIS/NPUgd7q5WE09CDBQ6kGmffnSOBhB"
        "SIjpSZ4PG0BJjo+Nueqxv5WWtHUc0AdmiGpKq0SMmNxI78FKf3KGDwlLVRLnhBNBjDTVwPtR3E8rm4TKs29WhH7p4QvroSLE"
        "PgzQcjSMhP0rj7bhtV7B27yMjyLtP8Go+oIY/F15uS3343F83mMQm7+tHiqFxI6klhw/V32TQuKBHF6LSgWRtmC8I2HFoMoK"
        "ZuVK7+ESkXFEyMk0BDn8Ajnm3euldj5VrIRMpgMjIqPLdF0qJhwQQbDg8zjXVsKFUmibcG+lAU0M0z6we7wmLgQfE0rFCDi+"
        "ShhJkNtswWxmXgLp8HhlMW24xnj8QaceRO2vB6Gb30WQpYrCT7Me5HuL1yZc7atJnyNIs0lFIT/9oqKQACn1ILMpNRyvxjgb"
        "ds1xRibnaYt6ERjOqCSFGtjCeg5JXAeiPBBBgnQxrxOmTstIYybNO3aiYaGCiZWKjHvMCTuzUgnrJOieXTUAXaGEcRYAl/KK"
        "NJtjTEGyhDl+8B242e+oI27sdo5T+xJVqehAw1i6e96v9TpI+rfZ2OYIL+kSbf4LvtU9Jd0jw+wqO9enMt9dasmZPsKKQLpV"
        "1vJSt4X0/QJyZDqtazOrMwAJ4xyCHMkCOaRPFzs9cj/NTDrk0lVxicFKMYvGwaK2jN2YWFCeRbbB/GsoAuwb0LhXWTeP7aDN"
        "iDcQz6sET3Cu2VuW7cS5ePFa0FDGsZY4iljSX75hNavHB15a/6gFgrTN427famrSuwiyVFHYxEHebK6YI2wPWZMeb0lXk6Yo"
        "fV6T3hKjduvMJOSX4Sjf5Pimm/Cif/oZx0IEvb4JvJhBa+GkYRK2WhouMPHtAzZL1tZR3ojp1H506KcMyYaEPWBPTurN8TtI"
        "QEwn0bqNe4haRgTR7LDI3EUgCPwSGA32QBJIKHgJuC62lIuRjQ1gLKmCkfh5Of6hG9ynF0ZKn5kEnXFgJoAsG2M9hG+UjZm/"
        "5d0vHzYlq/1qYGx/xfae/Bz8PFcVU0g43z3E+e64iqUxNyVdDgNSMYVEpvO6fl27Ztai6znhHHsnZpWLKbpRPSL+JqaaMYsz"
        "N6J0eE4yZd8yyF+O3UaZZU1ibqTzDEkKnKONw7+NMAtaE/GH3Qcc278aUEHFUc9YtdQuO8fyGoGhrCVhto61pJcFTqNf2zoe"
        "uggSa9KpDLFY7+aDa9LfaueEfEo16dykqwlFLLz5vCa9++xjK8G83A9bHi732rNhZ7UX2NVEWrYcP+lZk95LN7x4M3AahIN4"
        "EQxrlQYqr07q+RlcBS9kWTmk9Z3DPiWO7lNaWgEwtUqbU1rKF2JDAPjLTG2XHCxFN4vdTWCcpvTSTA4cBCstjMSA2DJ+UM9i"
        "zXZSBM5zYY1DAMHV+qJVRzcSeAenQaSeB1lIxwrXovSs0jl8q186Uose6UKi3k60uiaEXOsZGy0AGbEH5zAKiMEEREi5rh1E"
        "CkrOdR7CRsoGYDmRg7MWJ36OHKqDHOMGOYggaUQO1tJX8OISVr+kpIM4Ecy0YiNxQyMhSSjtGc/ca2YuaBOlwfDUYV8JF92u"
        "ll4BFEi0e1+6xclJZu5oDanXi2xGxQBooSfak9SQ7LRdTXTbWXF9ELbYWfHrL4To3jcIglWb7v++mnRITW98ul1NLswR5Nwf"
        "fidcqo+F5NlNGVwS2z4O4zvfaSKccLNo6KN+/HKSS8NtxiZ+JU7WxIt8rkoptVYlPOyavLr2UUNEzFuDrGssGodkiXqJwDC1"
        "ykSfOCRx3gT8AHbuY/E560BiugSbduTCReg0OJ9WjBNA1opG4tnippaa7WtM9Ks5BNRIE7o00EjCj9URN3gHzwGZnuXnYOJl"
        "HvnJ4flVqulCEvwlabRgEg4ejY0WyDlsIGJMgCAzhOhwO5lJdzrOdy9MJZOyNOe7W/egWYtLfbvgs0hfr51UmuKJeoXQIRsp"
        "WIH0GgdQOvbgEJBRJ6LJBmlBQgTRxx/4hdj5ROHaDhRenHSbMa7iHDBva/bnTjiihwqnVwPtS4rWUK4XfbF21eYzp4W8t50V"
        "275Y5qmXws1miM7VZ3fCK+d/Q4boxKnpVLG4tX2xjoYgR4H3mBMFi3uLVnL+j/BW1JWfVdN3LrI3L9Qsdndv0tdJ1KkmJP0Y"
        "4dyMvuI6jOUeExbLmJCocnLLSBTBDYAfDCjPAo43vSPitmOAkPNW8ACbXbD/0sf4IPeNMOOI84hRI2ZyKP16IyO+avahxsmV"
        "knSeFmbx5pyOAHcukwFiCRSbGlQAJqVwCSjmssJl06QyDFZew/55rnnSt1RGK10k0cAH++YRjqDqafUME6QgbXHC95FGoMGa"
        "3sXy/K6Sji1K5rqDlEMiNdKFBJx4ap1FELBit71ZHFiSyQjrtM/ZKKWTKb00DshPRIaMIxqbWYuqRQ5pk8ppujOd4upnHkqS"
        "40kMBvKKpbKb1AkWEdigwQuy75xPWC6ANQv6Rew+w8JoHML1B7n3ml3gpesMB4FCmNGeQVpHm3PSexTGURmJj7H5aZ4zHDBR"
        "wzEO+xaChNmqqKKbqz1RSSUbh6op65Bu3IxqqhtHdfW994Qrx968321686o4ZYr9eY+wPXxvXrb+Ud8Voq729eZV85R3bove"
        "vKoh6qTpHyjdqwLHokiKCf1MXnElSxTo6iAekWBlsXUdZC4317A4nxuO8K0HfkR4IyAtx2PsXMvYBFohLmO8YgUnQbMGHddS"
        "rnlWZH0uCpISD2/LcVah42grXHRslkYXizXbhmpQrWbXUjNhflNs0xlmcF9A3NXRkSSIcR8p+ZAjyxIV/kkx4CeNFpouJIiS"
        "t11IdMhmEilv57u7Hn4g5XLmTslAYC7Gkaa92JB7z6xF1XKPZj5JljUR9Ka7fWD8g4CMI1d7lsyz675iV41UCe8ICZ0hdmVn"
        "OAMo+dRhpdFAoZsE8nhuvextapghyabENa+BlO42hUWqnDtYD7KeF/UTKuhyb14lXFcg69YBvXnV/XrzHn17CAMJUT+W1j/f"
        "j24W9OVX5LEPo2rQKZriJvr0pmrSTVqpN26DHufiJE7oJytX1/OoZKkiMNvahrT+gNFeTnyW1vqMSumbh31KLOrPQM1qhrQE"
        "e5pEkmjCeg/2kJMK24quL7vJGZXFSjkZSgyCniB25ixb3cCnN1ipeTGydtvGFfwaUzkaI2EjBDzvGJfMxQAAEABJREFUxziN"
        "P1WPcWMKSarV3ypOwWobLRzUhcQ7cA6mkbiSLX014xys62AlIPt/59OaXehr+DGZ5ZTeAzhH1plPUmfSv0saaedQrzhtzdTU"
        "/1Lp+oK1A4tXwi5mdBVPIXIF45XjrKXGPxyaP4bPf1NKP1i/A1n9QxJ0dpDHwsjFkNcAtDJHdVOxm/Jw6PSkDlQ/pVhKRQSh"
        "OioiKQh6bOV4Yh4k5HZWNYmKKsbt6F+1KuzRnKvm8x79qR0EOd+2H23mg6jFfPT5fJCPmwgnvsWxlqhDfZhmm6JGTPOBJ4Mt"
        "0xJRdWgYE2a6cdWztYWi6Igc7MkuahZjW5LHcFsdktkLF+x5FaulEQYAoRSibiQESWLparrYWAlpNVXz/S3iMBxgDy8dzLTG"
        "csjiIUES1myzdhsrHQeBTgVJbEQS5kBhPwOBfuexGYkO9yB3cbosazo4Pu7+XUgCs3M7892ZY8X57t0pvcX+Kb2c717Z3Lfz"
        "TFruITlY5MnwQKuCvYf7lp24Rb2ChM5WkZ4LTkgSfJ5Mx4VH5nzwmIe2Tun+Jwfn191hlJznVBT4KvZMBgbViEhymAR+lzRs"
        "p4fsbodDsDHwVD/XoYJSDeXoA72G/dJ8kIggrZo6nw9ysSniv/DQme6yHX1G4RtBX+CMwhPn9aXbt/Vr+df1j5IP9CsnT+n3"
        "L3+kTyUntV6t1MrKM2DkUPHODtVsAmrVP4bldldvgDluje+Z1V6iR6av+zjaYQwXHxFaOJ24RVeIPyHFEpLgcGc+JNlxDp9X"
        "JuUQ+mYuN6v+HtCGVAbW38OKN5JmZggV7ihJrYax+djAGnGPhP2dwG1IQWV2K8e3aPZ3kjChYj8SqFw4Vyyqgu2yR5qWmmsp"
        "ct9NdFiTTEiR25TEJ5h3rPWhKs59N3YhUfo/44ITpBAjBPfYkb2GVIP7dQ2jSKR/FRz5IglCygvEaSoxClItU9RSz8HhK5pT"
        "eqccUQPkcJx7ohA5DzIJaxhvF+wkQe5hpO4YUUUcnp7F+lHl0sExmJxTjqTbCwOdil0eTb7GbADWzRuNmI7MS3/hQV8P5+Kq"
        "dN/XTMlPJJazY+wUp6MABMGVdDDnHBJ1XZlBoLtYZ3W/NpnyPXPMmelu6OGs7A6H4VhYU1uUzXIVZ4M8xfkYa2F9gMsJi/Wx"
        "0z2Q0b565thU/ah+V50vV9V//JsLSv3RSZzBf6GOuj3CjMK31Ovnz8lo3ddefVZUglfOHxPVQJ7XRtRvdTosNhF1qg/iZo1v"
        "qentHUQHCkGP2Byk8pKXDpfK5mDtPpV0D1zUMr0oiDpLN2tewnjfDS9ySlQVXNa4mJPTuNyFrHMFpCMNdwuR5ZRdeZoKamk9"
        "xDkh4LEeAMOO5XC3qGqV8Cuy6RKS6PshiX4oTrJ0eJlCog2MI4yMTSZ03/h+V7SfkpQvdSGxrpD+VUQOBgMlUj5bzHcPLXKs"
        "ADm2O2oVuAWRI1+Oe0jPYOkyb3U5a2YilliQQMoRQGGWdOqkrUUCicmCf+Tp80pDkOfkYapXhqOgTx32HWEQPHdOFPcgI+hr"
        "x86MlpMe4GLh3CtpFoFTuOskT2jmtrweM4v3n4S9jfprXsIf9EzaF+5UErIOJHn2W6KuZq880wzP+Z/CBVyz8wlTDzGj8OgI"
        "wu2NPwcxp6p8Qquzt/WNt1f1ueSMdjsjvdZMuQ3tlFuzITNCglnT061d/D7UszJOufVuxyS9Y6CeuPazISIEKc49YrZYCxWn"
        "KnHkGjsGwNfd0Um6JsjCgFpgVf8MDtk3HjQKAVAwhBtyhZFHJmMzG2uX8VjDeUnEAyEjDlIlDA5wgaVLM1/OwtmW7u8cD8DO"
        "7wn7P8F+qogkvBQ4baxFEvy+iz9cC/JpJGkCItddKpi468RRD2uTQvJ9fNYt4RjkFlpPrlC5wo8mcjg9AyAW0NVm7MGTJBmU"
        "rRqCFCTqWjMyXtdwRIgYLXKU8HNlPkmLHIPEc7puMZmYbK2ZtsuZucDXNCXuwAT6AX5azRm77MDC4Gbs9Kh97Ndl2FRC9deZ"
        "LmPYaZrzWzyiJvrB6TISQwp/Hd1GLVOGP0A8J0rVXAz8DJpywVoxLBCF6fUqU6s6Tz3d7pD73TCGfLbhBmF7OobblUBX2aXD"
        "AThHBADLgNm+q9ZX8vmU25/euKxe+/aq+oFkCZ1gPCSo85/qnHS1P6u37bB4aU93E2b1ctdE1MVnhO+4y+BOPw30KfXGcV/M"
        "OKxvF9gx4XrlpY6TK4mWxIOamYyWU4wcuYiSoZxwN64e8kkRSgkvy7B7/OBvsuc9lkuP44wVEJEW0FVYJH5qNrDRscs7pxmy"
        "ewcrRBC1cnC4nS6yiuWpgiR7OQkC4jgok2uB6R5mqjlfQ7kprPsfcQEcNXeLKSR/AffjNnx9cauu0zhYDchOJLWXFj2Ssm6a"
        "LiRquQtJZnCQisJldtUJciQrnvPdc6hXESmUajnHvnnsU85jBzGfVjASo4V7GDjAnGwVEhwnnXLgCVYPNmPiMUxPKxZeKk5J"
        "ktmROCmvHHYt4Zxd5vAPTsUFzREEwfHntHpJ80HMtGJXYlVOgfm8DnbZH9wLVyVnBXddGx4XLktOO8/inUfQ1Z456T/rZPGq"
        "R56T/lAGortd3lWjDqj9EXUJGD41DHcZUd+IEXXyKabOrIEQTNyKX+m1EXW4WTu4jpOMmhLTOwGzDgQuK0G0mf9eva+ls0Kp"
        "vYx2JkRfOuyzQnZ8AUieipHItFzptpViiU+xAmeGJ9vgmmKzH/jcHIUAlmg4ThdnjC3lPMOVEk+gkbCBs5ouqVtY+WdAmCnC"
        "8uNruJiBUTAe7I0ew5/7hyNkAfs0hL+wbLRAYzB6co0HhZm6UKwQ3cQKy0gqjCPVQI6mC0kZu5CwFWGW1ZK6nvV6TtSqPmcl"
        "3lTMyiVGMJcpulVEjl3TnaobTFSthJizG33R9AquCKT4UQWOU8iAuTnWpjzmWHHkgqNrlVBGJ/cHL3zhsPMBI7hEF5mqCStE"
        "LwcWIkgJQ6UktaciJazhvdXMFeBpo9/ECPq0jaD/JKbjSfyjE0FvZxMuRdCbOhC1t6v7Q4m8D2kg3Yj6BdziYEQFH68bUb+O"
        "KKY8tSPINgH1JqK+1kTU/0nRt5yRXQwh985wxkxW65IjL6A1wfm1Sc1cg9J4W2HFgRDDeZi6xEkDTOkHp57gZOJMvAy3JBoJ"
        "EIQrHw49LhW2F/Xskp7VJVbINEucJNundC+gZELclHaosEyyjz6bs8NIHDkJLlL84+t0CgOZIDozhdsmF/VVG0ZX2ckw+Am8"
        "rAkuv78F+vzd/T4ijO4/w+A+AELAuMz0WgCncRWQyU6wGIHfZBOE0SJylJ0uJHnsQqLzWNeR9fE5W+QYL5BDjRecYx9yuCEC"
        "gYkYh/X92Ds4Y2EMk6pdCtymOJI548FBLOefpTxuzwOQiMjwJ+GCIfrow8uSIfDgbccq/5HMVzcyUiHmvHEx5Dlm0NDD3/ZD"
        "1iXUemOtxoF2jH+Qq5KzSgT9a5BFOrMJZbu1UK8WEfRjwo3JkRW48nk+b67xPhyCPNyzuT1g2m2dcBzbWhzHlnan3eJaMj2Z"
        "NsX6kDvpLTuYVdaVmzas9k09LXKclgSMsud1gnhaPQzGDsAbV0CAVxA1XDur/CqMBJBkViD5DgEtbOD8zw/5rDV4559ChpL2"
        "m/Opt4FxjTDCNxlbXOgxl4lz+hhwC5GgyzjPhNl5huWmiR7oCs4xAtgITsOSTJKyG7tVGQI4rmd80sPyyMTs/HSNwHnixT2h"
        "BFrBBQHT+e48MTEwO1h9HwbyrgzCxPtdYe9cFQdlwr1BcJJqVZzTId3WXUwhAVWQcllwhyo2WmCMLXHtlF0GwKWaeRyrmjll"
        "t5jQOOLtpam68G4S6FdUhucjGJRM1IrtU2s7lC73AeErp1fYZOIUKySl/Sqr3MIKgjS/f1jCJXzcH8Dafsam1kx1x/EdXdHJ"
        "ttcVlEY9SrQfYXmc4Nodm9TApaxmxvXqpK+YV+Zn9TW33jvmxvgoG3BDtl3MwVqebjsO5tKaLNLd6bbqtRtYsM+FON324fgH"
        "t4dEELWwQImHfHee+k6fbx4P6bQA0jUEmI3TMq6XviPVrLVrq+JmmeMnESGdSDozKwzpZomahQUM6n4tEwKqWrJrL2H1gZAi"
        "CMKKP6zM78OPmhzyWdm1/VUwaLoDEIlMhiAJc0xy6d5ecaxZlUPdzBA8hFdUpsy8ZnAM+EVl10jbzWwVBBh+h2d3ZS0cQHrZ"
        "MgeKkeyQA2fqCdSxMTjU+Iq2o8s48dewx8owyrX+x4Gyf9xX+v/T0/r/vWLU/xO84Ee8YK5oN7piwojJ//DxoF5hDwMWtQrq"
        "mSvriByq6ULC7FxYhfSvYoOFGbQs1m90kCMbd1Sqvcixdx67Y75gJkN8gsxGpCJss5rz2BmKL5IedD1J0z/Nmnu6puwrrAJL"
        "qF49NBsZHA3CxQdS+MO56rDTyziXjhwEv3O0G0yopHsFAcFXU6YV4VoY70i+Fd2rtcGKp3tFD4TGcbwxjpNw4dXtdmiOauaj"
        "L0fQJf7BO958WOcqbg+nYsUvrNWb57X6I8RDLvytVoiH3E5G+pXklKYHtLZ+RgUQDd3EQ0TNKrZwnaSa8RDVh5r1EeIh6zi7"
        "O5c5OlmzJj1kOdP4tKhZQeKzNtbfZsIP2Ed8kzERSLSBpFpG7bGTiX72wR9XrTP/R7Jgg4i/0EM4P5JBSKyDrM4Fd7Qcp8pg"
        "pPXSPCeRairWJTr26wppxtSkUoJpoCYKHkdgEYP03WIZY2DKllRnOVaaIJzmqKLtaFOvydAZDr7kFF61hYur5KjlHU9FJ0ja"
        "CiL87HjIpjsz50uoVECOUFBiZreSCpYLC3WxCwnc0bxXuYoDSQyXlF3cJnJAlZoxt8o1EXLs1zpTdbN1lYaSA0ZllmKZV4i3"
        "YxWSdjJe0ANHnsjRg5/J+AaCCUEmW+FIDVYtG1IA5dlmw6jjcK8OrYJEuOhHOFk3Jc7BCa0+zO5CcMBiAm6VSDM7XVWlzvIC"
        "lKZMRb0q8AlACAvl89kVUa/WcDnsVkMIO26uXk0QNTIv4CIYAD0GazIb/akVGyTFfX2gfnTlXXz5VXWxiX+8qf7FQwcKHwFB"
        "VMynB2QxHqI68RDeu9Qnq61R504qDJXUqB9/+aRbVrMGvnA6KhfgnDqnxYC0kaxzsienp4PQXTIsMCsrhoa4ImGNf1cfUquu"
        "RPrS38LfMOUEV7XKT+G446ru8Ue6sqs0l+E55CS4pkIPi2sNbgLnu+YAaQ7hY5smXBuCJGyxixVP4hC1jtNgg51ak0+wRjA3"
        "agxrGmN1HEN2GF/zYfQhlGZwjPGHDrfhYtDtg9UDYfQIa8FYEMiDkIPPJCniLtJYulGrGG9e6kKiF11IirHfhxxZs78fcpiB"
        "kfnvRS+JnAPGUTNrM80QPc1tCuLibQ7dvg+UluP0XHDsKQxJl26jps70q4cZB4vNsAK/R8QX7ghh/32jZb+RDIoAABAASURB"
        "VDAolrjCWhwtnOOUcSeHI86jPKN6tTFXr6BtBqpX45Mn3bz+Q7UzCdWiD1aTCyjpJeDEF2fXJf6hcI2+8fr/N6g331CPgiEP"
        "jyDYLjAeor6vL751Qp//b2/rH0g8ZKR/Cr5xFmqnv5brFbjz0Oa0PpmpyWio9IAJTANm7CnOTV+9O4bvMVBDxGPDFiIOg21w"
        "lB77rBsGzpWkU8M+8ItjlWdgHrS2x2MiYsIWASrE8Wx+3uflvhs8G5ao6h2ZXwE/dAN/vBOnjrCUjbnxjCmyEyOLPDmLOfCf"
        "iCAF22AyM1uBsca56hzrZghC7OMBQQY268DOk5SRFu2lUpGCmKqwAmgSUiz4JKihxEWGvYJK5xHvSOM8cqpjCZGE7hRuE8e0"
        "J2JgpbC1cA6mH6bw7YgcfNAnQI4yIgcj5VWzZ4S8O4/d9AEIta3SEvAbkSOp4zz2YHQuxpFx9AN7Byd9qHwSMfdW9yFU9J8P"
        "0nWF80n6dE8hMzJd/9DWRIkKfw+YvwGQYyIz62mmO+wN7CnJEE2AheBewacS+9AFDy3kCn2vzsa9MIPsuw7te/d9CHzHhqHg"
        "opoPQkHucQrcoz+RUT0S/3gO8Y/3Ef9Ya+If9dfVD96F0n72BLysP4Gi9OcPzT+4PTyCtFu3Rl3FnBepUd9THyKdgNq56fyn"
        "UbN2dmNa62T3KhAERCxbd1SzKlvWooNjNUnSvFJc8jIcGxBYWAlWoFBgSZ+1XATX90/w+6HtQusQzsFTWsPFTw0/k1l/UA4Q"
        "OuwpjlgOKnZZh7qF677HiU8yMxDRdmtiZFmQRDVIgus/9ZnjBCYthaFZQU4CgIPqBNcZUTvhEj4dg/COPLkFOAqClCMQ4rF3"
        "yQjGMMJyOQaJxf3pJKFIwK7rQEWYVpGxCwnjHDCOCq+Wmy5yZDCW6TzOMUeMtFMZWJ0QtYpxjvlsRE7hLeOEK7xLLvEOvKTj"
        "KAbOY9c1uIfpUyzBMsHRDXCzOBeRIyh0KsdQq19Sh2/bWBR+GpsOAMBgJJcRJMTfFyxr5rkkeiD6Wgk+Iw6m+2VdMlNmfd1N"
        "AaIrfitQvZK8ola9utNRr+CizOs/6p2QUL2SCkK1vwZdP7weJX+mHm3DOXyj+dv9atZ7ULNOHaRmbU01sy+hZup1k2tRs+5U"
        "drR6SvdSn7jRTupng5Q1PWyIFOqqjyAzGJkZgECu4GiuAjLA7v3wBZlzp1a8jB5Qz8Hv+oMjfGp4reH7bJGDyC2n4iLIZ2Lc"
        "QTvIqgncpBpnSoEgc9AljdCXksCYYCVn8BB4RnWLYxU5QxGyLFSugWbXjxriFkl+DDwG046Zjo2y2V8oQnYcxcB5o5YzOOBa"
        "4tVYh83Xp3rGlG/KnUy7MGkj5bLzIY1Dx3JZxgJSIytidKuaXnyKuVVMH9kEAHMWe2ik3FSkXBY9xbFvjVtF4+DgUnab5yLh"
        "2UOYTewqaYv6vJYGEwMRNsBF8Gmpxj04ZZ/trYz5U/Cy6+y+gmMI1UpjkTBjHI4dVWksChWUK8jhOpvoBMHXcsJhwKXpTSpb"
        "rFV2bbVOqmtud1ggtH/SHYPIswUEobxrXH+hXtFAyv3qFUMQsf4D6hV5x/e+95kiiBScLLJ7O2oWg4YdNUu+wFMvBVGzXu6H"
        "bm6WqFm94CU3axdqawoxcwgVY93jnO46WVmYc8uVBisOYuAytxvUoIAiUiiRRh3dlVs4EZeP8KlX8YLfFh2ffAQoQt86jh3A"
        "SslkJsXZhFam2oYWSdIOJymZSK4M4yXa9tjtuU7A7+WixmclZxAEYGUf68ILqFCmD50bRgeeAlEXxof9LMh90MVn2obINbDS"
        "ItJfAAEYBoBPvlJJhLwfe+a2nQ/bLiTzeo5swTWKah12s2GKGQAN9KmcsSQfcQ6qVT5Plo0ja4xDxQlZJus53QzwCbp3Clor"
        "j5GKxyqD3b12qHHwotLhkvX+Fl1FniOeL0gfZHPS9IoeAVEl0bmgh0ZYp+rhnMODKMDs6FGMbzWJCD+JHkebeyXGcWxlHhyk"
        "eHWQejWv//gExsHtUREkxkOY3XsOCHLiolarz8prnXttU5fvfYTzvoqfe1hLh9rd3sF+ABTBomH7OmxjUdncFCQZplO7fecu"
        "JJJTQBuiSJEyrYHpRHMU0b5HFAFzX0XcYAX6E+IjangGsREnDaA9tHm9gfXlvztKEwS4ZRcTZd5l+ayW4TJhKhOYYmshSQ6k"
        "f0wkkfJbLfUhIJRMYuSFm3A6ABiSk8lLdTvOQoKMTCHqKUbkK0bmlYxL2LcQIS4gk5uYeCavg99rvFGaMB7OERyQq3KmrIOE"
        "KPqErCVnuey9GN9o4h0S1+DjQAw4KEb6PRsmHjrNEdTsa0VNDEZsonFw3DQHjXIiJ6RcWI0gh6Gb6WM3SJl5wgE+Me+KtfM4"
        "9r8E4PvGYceWMZ7M+v8fmP8OkRnIOboEpoDFZde7cmJtPgKi7IJ7TBBYms3Ro0f8AHqsAD1wCLW/Gia91B0HerD2nK65rmeS"
        "mSGLrRtG9Hia6hXcq3oRHJRivt3nIAG/E9Trr8bqQf1IKu8n4CDsrkPLfL2jZnU7Lu7JzaLFnxTLn0qQh2QkchH4mL0Vz1J1"
        "LfFUIEiCVYQrCgv7BEVCFRUtrERYGy2H3HtcDTqZsRcfCCT8drUL3/9vjvLRcUGcw8VwRgkPCTKPg3M5pJ8Tx6DhQoFGFed5"
        "cAKUszkIMuInUHIKjmxOU6o/7PZRFfWSysWZfmmWxrgJc6XAUTh/Axy+6O59ErNwJejn+jURI2sj4743r+dgdxFBjqqp26g2"
        "pW9VMVmPHIPqFBCjnJZSQy7BP4dQ+wh4LFN1gXyqzDgjkbMSZXaiNj0Z5oOLf+5WiXFgsXEwDhbISM28xnNZJKPPQLs+3DgU"
        "ZV3/19DmJ+yECbWFleUlgp4lkAkfEIsRmwEI96iqbFiX9BTkXLNsKl119CRIvCf4QGtgqW3loF6fhRMvn47GAY9kX/cS1emg"
        "KOrVO1G9ktLaR7IN2R4dQbi1UfW33tHnT7yqLwBFzvVu6IuX7+hX/vBrqr7UoAi5yClwkY8XXCSciJF1P4XSvlnoQbqIrLuP"
        "wUXygaBISPPUF3BAU6JI0q9DtYLvOyQXgU8/fFFGCoCP4ORCox9AGvodnNyzR/j0HuT4r0EFPpJWOkZPr4SYYq6YfMhGDUZJ"
        "cRLcnpmoTIpdT/JSs1shO7BAqUqZyiXjaxfchEQ+geAve9Xcbvd8nENp2OEfaCH3N4gR98xPTD0rAefIoWID6fleymKbyRHk"
        "GLwfl3E5ZaMFsF6GaWAcFZuFMH0mEDXKdJ4BUJmeJSFnBgDdK3adskQL1svDtQLfwMmF8QTKus8cdTYijt37iMb+gGk2EO0m"
        "cKlGHwI9PCVtq0WoQOQebsRkAvd0io8+01VRRe4BfWYlrzQMZFbdc+vHj7ndy2Wg0Kzh6e2PnLNByFhiH5fAPSgQSahhFtNL"
        "KO8+avR8+Tt90q3NzYLFkhhdbHKz3nvvZ/JwW2nYNrbmCiArgZ9JZJ0HgC2BuGIYd8sLF1lNEEB2cWVhXGSAlSYJkW9QBWFu"
        "UoAvC/foAzZuVrFHLctg09iVffco3x3K1q/7QNc2yESn05zs5HQ/zu2gq8HRzxzQyR5WkIB81qcUypmDVVlzbnlWFViZceGR"
        "oFvE38TN4hwNiJUJLmc2D0orKKmItwN2oH3BqHrsMlXXKWcAAjFSAFld0DioBuBecAqZz8EIOctfiQzdfZ7EeEav4RghS4kY"
        "zBspOZ89B9egUXCqLqfsJkFmI1rb69VxJmIzxIczFW38vkAOnAFIuloab9M4IKsfh/jxG+poC+k2bPZvODBU2hMB6j8USVez"
        "b1cR+3UZOYdpMijbuAdcrYopXeI5wIOY7tzy9CjoWRx7dl2Mo42c607kfFE5GNuLdnOvxKP5hOpVu32yv+a2NzcLKPJ2r1Gz"
        "rt7VZ8++oB7ERdYRj7o73TarZzJNLtJLI4r4ctu6nWEeNvrWj3d63vR6IcXJruphyPO+KxBWTc3QOL8KSXL4glLDIM2iNSJM"
        "4QR0xf/mKB0LaVR40l/D8Jg3zZSO2VWgxwswxCsmAR/xLHctpOetRxwjpIhpzkrJ7oWTw5ZBYMz4FVLvrILzBUQJDaKQYxBJ"
        "xP0iIkwVWIEXqRgR0DhaoEUK1SAFEKSf+jlSqAY5WsSYVkCMQex4qOCNcl/DdMA1ottncZsTDDSrNBNE9VNoaAAWZuUqRMgt"
        "u1z1EDxCpBwEHFLuKV/3r8CYnhe3SiLl+DjhOIDon4fYRfmQa0C5TNv/DZLex0AkSdS84mtI1+AeNt3FFx3bPB2DFE3xFmNw"
        "uKlJixkC9aXemjq7Ni4MZP4ZuMdKwz305Tyix1pPFtOoXE0lSxwXBG5H7sG3Z2jh3GPKvdq7fXIE6eZmYbe30nBf716sALrh"
        "IsdfHATm1nClYK4NVw7CqySo4YCZ3mpVTrfg/fcrZoKmNSDYcHwxEINaOn15bSdGxcIiJauXg7ukPgb5PdJ8D14AQJLfxsXA"
        "ogLGPnqntEyY7XPyk3Jx3DOk2WGUQCuswOlAVmJOsWUrUYTZxLdHwERWbCAKCbEkAJb4h3UW8CcSSN7SqRAoUM2UIAIvciHS"
        "TI0kQrgBNJ9+UowQX4b6RKJNiVZuFzkiagACIgW5hc0ASjaBrWUVo0VEDES+Q55gD4TjNF2f9KEaDGU2Ymb6rqI7qoZxlqIe"
        "PE+3Ct8VxtEXQk63SpsTiG7+9pGMQwnv+AGM446RrAYcf5wLDyPAJ2NmgMQ9eM50BlWvZtwIQgdzrvwUMlda0TjoOTDuIdzj"
        "Gqc+Kk8PQ4xjI3IPeiDSe5dx4ab37qX6Tjg3rxz8emBg7pPkXu3dPjmCcAuSgDJHEbV6HVzkOV0ywxcocumsUhIXIYqYnQUX"
        "YZbvdk+ThQ1vzuz2GXCRNi6ygzBxsm19tg43epb5kGYIGWQ44H38yvEC4B5mAB4A5KgG5CRnOF5ARgrwJwwq7f+ZU/pI5FJq"
        "FbR6G+oSlMNQxll/5CSquMIqOCMqV8y25b4KLOQulIhOiIwzTgKzF0SR9G3jpAlaTWcnqVu1ipyjRRZ5V/CRiggj1b7TOKyG"
        "QSE+iCWVY0b4eCJd1tlIulHHgBSKhsembsz+YMyF/b9rIAaCf4YlxqlkhDEImJkUqhVTbBQbgsVUGyYfEkXgWuUy/Ic5Vowp"
        "afVrh7XvaTccs4sgi3+nmaLP7i9Aj0u6HlO9ghrJVhzjJKlGHM2gK3EIp1gHZhpOqpn0EDnfhg4OZpFtOAaN14+n4B55eOrp"
        "Zx2lXZLzpbhHh3tw/kdyqlGvYCBvP0bu0W6fHEFk68wPUU3vXnxg1gRfwhdYxEXUMhfhyoADwLjIiFwEmvcEB4gwy0gqI6rF"
        "eMfrKS8LwDHrRYAiqWYtLxs0QxVhm1BFH1fNLpsgAUAiCdytWRL02xxRdsTvYOF3/zouorNsSCCDORlRDmZwimmF3XxCAAAQ"
        "AElEQVTezq6cdghMOh1nDRov8z6EoyTJAG7UUBClQvxEJwjrkatwxjlje6FXJc0KT7PifobbVZpC5slsApUJkW1whAQBe8CK"
        "zcop/o635XEjCAH+3StLvK4b9IgUpWMPAyCYr2TUdCBSEDFkyi4WCcjhTFUH6q4QAZV8h4Afu/IcZ5ToJM4nEeTQPci4Z+HG"
        "/bOjGgePbeLD38M4KJVTUSwuKTfD+8/AtaQzJecicuKO9O3S5JT4b7BWl7fxCV3h2XN30FsP0TiOuR1cA/QoaByRe/SXuIfq"
        "cI+5cTTS7nk+IJWD6hNzj3Z7PK/C7QFchCiiHsBF5M8RE3FAkVWgyM441/1ygxkZJjryQ2if0wynAP5znktcRGIj0Ed9OuCM"
        "cqPd0FV+FStU/zQvBlzEzeyNAaJV/yrM6zIP/R4sD/0g0eEdxZwp9kT1Ui0PGk1BQIObMPQgWbjgJMzPtwg9uDhCgc3nArRW"
        "8nHDpHmpgncJu0Dl0LBw5QiyKGnUgEsx/q4OmuMrcFPHcY2ccMLIfWxWHBtJsy+3lcgMAtdQAcF2mbPmGGNHbB8qVdsLOJN0"
        "dc+GikQNncXEwxCDgLiND/0rR6kMbDeI/B9lRv05KNDUyAx2Nf6Q9RzBTX1qdnWwYygVCIoi3sFaDxB1fGycQTEc6FarUK2m"
        "lRDzXe3Xsy1JYF2/l4fx0yx/h6B2B1HzA+MeQA/14afKPdrtMSEINx0tt+Uiry24CC19eRruclxE7rgXV47dy7OwNmQW51Uw"
        "gIGasgbQbXmWl+p1+qtQtHRUtRixZt6TZYVfBSItk5UU23CCj+gpTkwBrX+a6XBBLWY+HvI1ZGLri6XXv4ejvOmV5CL1QhNA"
        "w/U9PO31ENG/FXwLrNRmiCseKzWDl+lQuAqRxXAOOpClYjwFnMWmPa7wCAb22cal5lAU7fpVNRsQAWQvahO4DPbz+4FEPoG8"
        "TYQAMtU1R0mnUJ9MfH0JmoIb1dnQGL4vViAGUzlNV5thjO3oleexj9zDRBdURjCwGbLehDT+3YcxDiwi9zLOKaEk7iUqDvKt"
        "p8YZBFfTMWciMt7BrGRmj0k7VMZBVuqyqmcVG13Oxh/N6z1W+ldDaxz0JCTrW91Vi7jHcE/coyHmwj2uN9xjUffxOLfHiCB8"
        "LV7rTVzkdcRF3n5AXKSTo8U/F1ULKBJMrt3sll3l3o7MPDbCCDtChKJqFTuZVB4ycKcznGjgcMjgXpS4qHDhWtN/UctgTPAT"
        "9nHSRJJ1nMl/qdQR2vMvvlSNWMmPrDdXYRzMvoJ7Dn7CTFum3oNrX5MUqLqKwcwMXFyaitSWvfwRHGe/Rs9pPuynCnbK9oMy"
        "egHo0syDlxGlNo2TqNtcLY5a5kxLw8a1mrlceczpYs+cGrRX12xgzDmaBu+ChdqnfJM4koBZK0SOJD0N4QHLcSrpNWyghw/p"
        "VUxZx0c5A4HiVx7UIeaAY7KFyOr/zsxoHIuZllw2FolplgdPNYu/gBoI3QNBAniHn3Exk0rBVrVKoVrBtWLMw2b3nBBzhLzG"
        "vZMMFUtxndmAUdz5WM3R4+HiHvGKfAzb40MQPR+nHuMiF2LN+v3iIpKFiZWBB6BbL6KblYQrigUbme4MvUTYEUSiqqWnU1wv"
        "fQSaxa+t6NvSx8V1y0jDlGTRArU/wM8VkEbJd5IZH3o71+FPtczYOPKXguCkfw2RwX8OA9uQFoxN3IByMj7skNNqT6t0eDok"
        "QxxNfFis2JCfYRerWClXyQGMISfgPscKj70F4ji7qo0B8jDdNmGkZwUUf0X2KlkRBKIQQWQKPSBTvSKvgygRzEFeR5CiMqs4"
        "8KswDnErYY6riOcMOU33FP4WxtpM0aVqFbkGkRFW/puw+F99GOPAsfsYTP5PaRxsw0rj+BDHmJN1PSM9qWaFnJwHteuYuF4C"
        "OCp4cKXeaVSrHryA9aha4cL3YhzD42H3Q8TF6Fbda7K+aRziYUTjeFDc4zwfWOYej8U44nd+3Nt9uAjTAC6+95E+m+xHEdKD"
        "yEcWtesriLBvNxH2YE5pXxYWerp1jLBb8G/yEeZqsfufq4kSTF8fAL0B0nBrtBswTwuqCmeWDwRJvGcKxaAM+jvsBPhQ34sq"
        "V/Dvw+X/GctfgQN1aNoQIaZQa6urwL7kbI3KpEX4FJcDRzewSTMVLYpduGRYJ8LJVp49VZlPiOiFsV7mt7tmjruNe0EMOPQu"
        "1JzfhOND/0/LaAfFCIhUVgZ7RhNX4Jg5dnHRnBwYm7oZaXkEVQt7lh0ryNFKfQNGfPaoRHz+7ZW+AW/3LwwXHWXmxsHbnjlX"
        "UKzAuSbMPBDOYROgB5AjidFyBn3pJlvDZDWEZrLctcQ8RswZFOzUmpOYc6TBfXKuYtd2ulfPwX9+/Nyj3R4jB2m2Tlyk24Gx"
        "bXTdxkXaepHbbhS/0Mcwk41FhH3URtgr6JsMHrWVh5AFWdAvB1zjwFdYqUIPfAS+LpWt2saUdZywWNuNFW6OJGwIECYglxfw"
        "xT9QD7VxEI/+GiLL/xI67je8MZxOK4gCkjvw9PXBBbwSiZnIsHI62JVTNVZ064dnsKK/AI6Aa35VmXRFVnyggANa4Ppfc45I"
        "AMRR2BN5cB8C9StECiIShIAVGNrqGSDEGcOZ6zV+V6un8B6cu+5qD3XNDhzLAXyUu2NjBQsX1A5r5X8JCtW/glv38sMaB47Z"
        "JfC4P6cxKL/XOBxTShrjIOdw4lLpWaiy1IBz1HVrHMbCX+A4NbhTc9WqjZivFRIXm3NSmfcxXMz72DMxat4x8a13gswnf/Mx"
        "wkZne/wIwu2AuMhrr72mDqoXobt1kKq1AQ7y8c0blnla7NA4sK+Aj9yNUfbtRXwEgWKmwYJEJxn8NXAS8BGf91VS9+ragI/w"
        "AkH0zQFJWOegQl8m0EplXPilOsDNeISFglwES/gHWDE/kHEIcbhPDa2JgXSuZDWnKEmrf+zxPEdaASAIV2RGqfGn2ZyRzVEP"
        "UrBM8+O0ucLBmAisnPKc4MD5yDKP3LLCnnX5sg+s1+cgTfaCFORgJD13LrwMozl72NzA+2zMV/t7BFp+zO8oahUI+Ychdrvn"
        "4qNhHCo0xhGmMB66AVCsEnIOOIy2qukOGzurGRDUUKxm1TXHoLCUXcOdtlStNuBe3W1rPdqIeex1dVC9B4v0Po24x97t8SOI"
        "bIyLvAGf8HVJOaald+tFXjl7TCrAuDLMOzF2Iuyxj9Y99dQ3n3X6w5nUJE/ce970hjFXqxMf4QnQmYnKFpGklibIU2jKsyTz"
        "I1yeEUm0EiKJcz5lMiJWefhp4WKmzZ8pfUh3lAO/oc6Y/o2I4h/g59chwD4PFwa+vkfw0rKOAvEFmZHOvK4hV3ZcpCteuZXn"
        "wRFOKUa02dIoADn0KlZ8cAg9bPdyv9erkIhXEIdZeZ5qmQEpCwq+hxGUCC0XwnuJ2hazcJlweLoM6jdKr/6AqPdIxoFjkmn1"
        "ZzxGsMmZFJXh2IlxkJATOUJrHMksGgci5RWMg0qjm9DtrMstahMqVoxKrpX2ayeicaw3xiGq1c8WxhFHGcTGatcb4yAx5+22"
        "3uPtNucKxvGGfN5PZ63/dF6V2zzTt6kXOY/7GlWrbHto3VjTLHbxFnzk9n1UrZ0cR5W5WoXeRpSddSPMPsDlY6QCMQzS0O+z"
        "WYZE2kOPkx5LSKWQS4EkDkginMSBk6S2ZzzlWtV7ngEy5QRJWJkIKYqpFc+oT7BpGc0cbgBZrmMpv9OMOJZR1ibIPAwlA6sC"
        "Owc2HZTZK142s2f1awb8cJo7GwGzobAMIVec5MTrwcokLcX+8hS71FOAq+dgHM/i8cNnHz7oewT1Uab9f8Vrj2JTDDu7xvQR"
        "SOgmgXFUnGiVLtwqGkcN4xjC/ZrBODKWIqyJYjXP0qVxUNKFa8Vg8PrlKOnGXKvGtYKbrTeWI+ZMKUmKnbAUFFQ3RNZ9HPUe"
        "h22fEoIoOYOBsHexqRe5sKhdlwh7U7suvuV1titdVB7KB2OuFmBXz3O1lKLPOtn9CXj8inTcYwViWUxE2RIJkUgy2y31MJu1"
        "SCKcROuxT8MI6tYYJ3mMS3WCdx2zUZk0K9N+Gxro/9G0Cn3kAx1TNfRZeEy/jdD+H0Jao1IEkV4fl4i1cBaWswJptMKX0xKp"
        "h0IRkSDoxZ5pNEQIPAd/K9yGGUqUrJWkqIenIoLp3+R7QX/+LTz3hU9oHGzK/SO8wP8By9sGUsjxgZSN40bkwN77MdUqXOvj"
        "yDka5MjNLHMwDrVsHJKlO66CGAe5ZJY6ZkyMlnKt4lzLk19fASddtBHldrVjHHEYThMxv/146j0O2z49BOEmPbTARc69oyn9"
        "vqWAJEAR1UTY+ZQWSfzZGGFnQmM3V4ui37yGHfGRtVWl5kjSKFuMtCMEAdWmgyQBSKJneWDKh7UQYBBc03WPQTrFYiFtegoB"
        "udOO6SRegmagBewl/jSI+K8Hveiu/5g2jpGLFYyxkpHBNcZPZLwDs7IkzoKIuafqxLa97GofpEktU9eZhp7jNfAdWLz1aB1p"
        "7rfhQH+MN3kb3OImSE4Vc9DMDDg19TXLnL20NhL3VaRciB6uLLVpOAddXCxQslDJgrUKteqOY9v4KRSrNkuXhQh2lrtjQA72"
        "bJ7XmNO99pPAeOA+1YrE/Mzx5Yg5iTkW309STnuU7bEe5H0bo5p/fkG98eq/VX/81gV9/vZJfakcqdd+41n1zo3b6hiM4P3L"
        "U33sO8+oK/+0o9afX1Xh1mWtT34TSww4CRNM7+3qzWGit8B3Vz9m3x6n1zc2wmjr5+CBT+tpOUWcGti/ywA0zlPaDzII3bOo"
        "bajYDla+pmHDXc2UDS/N4mTmJ6Je8FNG+B0myh5CPNAsjvoAb45oPEM0R5pjfpTNSPBOGiLoVQ4cxZtxGNBJoMPTTBLkXBPs"
        "T4X4+9N8DD98Dt101oL3YwDw8SE/G3DDHfwhIol/A19uh03sqPZdDgz6mSnUBKhUesr2+5zDHkqOyKZRIACYAqULGEevZhFZ"
        "aRKEV7ZoFJtAjjsuh3FQfVw1tzzVyDVOFPvZLBx/7kV/794Ndby3pgrKGDSOG7GFj+10KWFE9/2fXo19rq78A7js+XDpwofq"
        "9bMn1MU/+fSNQ46P+rS3pQh7W7++rGot85E2V2t/fIQvJ1m/ULYoAbf1I5BX9RxJ2F8NnCQgVuILKFtUuJRLmYpO5KgtpzAD"
        "SWq4MNbnpk6gavmekQTFuv+CtqyFIC9J4f0zzf3bLrb3//SP1We7eaDAz63x/2AI0RwrETRQw5UcKc3EQywZM5sgusdU9YQr"
        "jp4lDpyEBsIgIBExJWpQrYL0Lm7VAjnEOBgpB3KsATl2hZQ/69S92J39fvEO4R3P7oSYa/VMuPjqvfDa2weqVvwen6qBfHoc"
        "pN06EfY3Xt+rap2TiChhlL4mn3YdxKydt87swphq0BdflcsoE9moflA/JyeZVVccdfVZVeOkjMFJ0ibaDnmRJ87DFQhWIU65"
        "cwAAEABJREFUEuXYKCDBxWBNMsEJH7HzIUJtI2PoU9djY5LdD4Oj2jWi742I2w6HvuRa/wccqJ8fNh/xS7FhtQC3eA8rwP/K"
        "79Y005PvDDl5DCl615CrGRwfa0bkHAmPVZ1MEtObwK+dcLquLkMBhCjqKkq5emtLjKPouFViHEQOvC0j5VQl1TxLt+mt+06M"
        "d4hxvHZOOGlUrZpcK5DW1jhenxuHUp+FccjbqM9q66par3Po6Al94e13F7laX/ua6naHX8RHwEe2oGw9tVC2+HJ7kaTlJGGY"
        "ajdi9w52qIKSW6QZfK8EnlSm2NGjlvTwnrS84cQkZxE7qXtwI3JoxlC5XA6ym70QONnTwTYQkfaG05bY+mYokWilXgnqkylF"
        "n/WmYxOFnycq/FRToJB8Ms8ujwi4mtkVA8RwHKrAhhJQqbwGgiTgIOwXHIAYCMiaVIKAbJYn6SPSJrYqJZHUbXjJziUhbzhH"
        "0ouBQP1hHp76WlvfsQgGLvKsxlKFE+MdETkOzLViGe2nrFrt3T59BGk38RUbVUvFvkUxV+t6OHfmO0LEkj3xkRZJKP1xkhDV"
        "jvshiahb2THJ8UFsuWKcxCSAfV1B6EngPtQzfIRCWs1klmPNxonNOJkNe7WL4CERBStmGHNF/dCXUG9kEOgUjvqYneRN8Fup"
        "Cj9EJP7fw6n7Kx3HUn8mJ+oRN65KH8Eo/ioL5t/zs/M7GMVGeWHMlPQr+I5XgiQajk2DqJZN3nBMLI+RsrtaY5Uy7ONFdTAm"
        "Hkr6CIyjEuOA6tCmrreEHMjBD8A4VmscLXIw924eDARysELwerkdWuS4X67VZ20c8vnVZ7s18v+eXC3cI0gCPnIWKMIn1veJ"
        "tLMTtt86BElmY9ONuAdOcevHLOBgB0mo4JOxV4I0NACiOJ87chMjI+37Tmr1Qg/Gkr8ETlJzaIwxqfctoqhmwhLbz+rV2voX"
        "EbFmodWK+mJsu1j1P0yM4YgIzuOoZaAiBzISMQxcTi8l8OXlAM4hTd30DKH+SprzecQuOMyNpbKMg5BrmFzKnrWta1GqPN1Y"
        "DuBsIuRtnKNRq+7HOfjh9kXKmxoPSUQ8dVB/q3PSXkoWWHkB/ZkZyGeHIHELi4hnjI9IrhZvMeuX8ZEf35EvvzfS3taPqJ8e"
        "AUncIuLOOAklR/GR87ykzywJdDjx6WAAn9rB0jJZKa3o/Fw5gSQVEMWY3fddPb5KTkL9n4GzYEYwll2cIiCNwY/7ONX6h7nV"
        "/0umzH+ALvt/IuQHFUxtq88GXXhMt9lyh++d4jOAX/z/+ZngLt7R8hn1OH5mrjD4fvgul/CdLmsEtHkb6Im4y0jQwjeoAXSF"
        "4jzRJptwsJAJTlJHKo4qoIxbphVL8Gd1umwcVWMcHc6hD0CORaR8+77GIf2tJBj4epjnWn2GxiFvpz6Prc3VeusdHTtgd7J+"
        "8fACSZoqxCbzN8ZIgCRbowUnkS6NXSRRamfcyd0qWYE6MT45DmVrGnmJz9iwOQkuSWSUmGcvtzQNZdVTbDPqdG6loTPQxbAK"
        "T8Gr0hm4Cctl09PkM15ljJsE1l9IvIJ7ZwVVEFSR0ene5M7444ilcxDuCtSxIct1gTrDh07/CJzXCLdI6TGMgWMTcNGrHbiF"
        "dwLnizNFS3HmO/t0WxBxSEgg5JBj4+DTxJQfBqmE5Eiqympb4rMBCXAf27pySI+tC7aHZGMMyUh2lgXCMW2kzms2l5ZE0TG+"
        "WTKGYSCOmbGP1TG31rvp2UeXcY7oVn3zYM4hxtHMsWzrOxrjkDwrPrGbpUvX6uKrn4mke9D2+UmXHSORIGJrJO/DSF5navxf"
        "wkh+TbMSkYmNV2EkT9NIsPlnYSTvfqjVN76h/M+v6I0TPc0ZiOvHc333RmwhtDO+AyPRZrfctIPBAJLU2PTKxE5dSDJOU6ij"
        "FBxmrH/FBR9gLAlnqVMSrjIEUwAICRzpmsaRuzLIwE/p/s6ZfVolL3LApWMqOYJ5uMKCTxImDXJKNEfraEkklNZDWoLUmpki"
        "TCHBbc8xD5yZyCRDzwInw/HXclIMgoZyccPjZxPrEErF0E1oCh6DXCheohiAQMNyeu0gPCGYQ3eKs6qZI89yFhjIFfb8hcGA"
        "Y8icFahz7FFXssO6Y8MJz/7WCPolvVK5Gq4U3Ck9a3oAgIj31+pyesv1kqFjNvWwqefgINbJ7k9Dm3jYVgQyQn7v3vvgHM/L"
        "oE35Th3ksCe+Fq5fGoezv7UpcY1XTu1NYWeW7h/LXPO33rwoHTz152Ac8rnV57l1OjPuRxIYSf7RPiTpchJ5iSZO0kbc57lb"
        "QJMdoAlnIUo9SZO/FbaIJuAkdg0eCAzEAU0SNnpeZV5timsDRoMIvMnZ+zDjwBj2lWLbUXZ1w2VN9plZ1o06n7LP1Blc2Lja"
        "WSyIEKTUeMPLqsFRrJV4JNPLZbR6ooN8Z8+YJC514gwTcWXsuopOxGIvxiBFIngFMQqYgFwonB+kmTPMCDzeQMaW1bAVrxPN"
        "Ll2MlLpLBjGKIK1+2cuR403YxreSwZkqK6wvmXEsI9Dwt7XuJYUq2MytL21f4WAtUMNB9JDG0kANuq+9oe+mrLfG0eZW8dws"
        "xTkO4BzdriTROJjCfqNBDtzx+qeXpXvU7bPmIEtb/NZvxISzPX21OH99wUk+VPvVrclSnIQViW3uFlvG0Admb1f6xEm1hXhJ"
        "L/bbsrpmg2S2m7G9TfjU08qs6Jk0UE4L9vidIm44RWxkTB8c1/oogfaf0FfPoHZ5cpN6R7gKB7xXZocDKd/39S7UoNElVe1c"
        "CQ6+vNkFtOww4Q+vhdtUgxR+qjHAYheWsQvjGME6GBHF7YDXo/zq5rf5GIxjxOfK3yBWA5cJj9ldI8/Ha4dAN4vxm93LJux8"
        "4IJwi0vaQ6Gwu95pcqpdb4odxjVgsrv8Loj/TQCQI3Iwk7GpW4JVJp1S7TPsgs1Bmgbh17JfGrtdFzxmq8NaVEKqhYg/dY2D"
        "XLDNreJZ3Rfn8OMY5yoXhDzZhxxfDxfavlYwjrAIBn5u2+cfHd5Ty65OvCqf6TBO0o2TyMt0s4DBSzY3N9XdnYmgCWtKpMZ9"
        "husJKtcIKlcfKtcCTXJ4KXA4DNyuyZhjkG3oAVG2q1ytY+3n3HC6XuwDzZaeDi5ZbuH50zULieJYDyINopCIEcBdYs24t561"
        "44jHIyJtzgYHFDGsNbfvA0VgNPoFwgJC+t4n7CKnBFH4HWR0Fm9zppbyH3DYCPDijGY7EwUvz6vLOg5yB7vAk6pY884KRstp"
        "JnhzanWVjXPnYeH4zWsrg2wqXU3hWQ0r3Qd6bINer69B1QJisJnbYOgk0MoG4g1qTDeUH0L4oAunmY3ba8pkWQnIYieZ20FM"
        "Yy05XKo7Si1FyNs4xx61aplzwK2C9L+sWGn1WUq6B21fjPQJcTvYFeUN/UbbCBt3y1Cet7qc5Din0c85yXONkainnwZ5f3+e"
        "Kr8BI+EcIhZd3d25MS/fpRR8EDfpF8pMdoc2H8IFayThMIX7xRGW8K/gpaSqBEfJYCD1OA0p9yUEI07PhfuVwFhqB/fLJbjY"
        "paGCA2kHZzGuYiNpD9aMMKRO8Lwaj6toFJCaIKlqGoNnlwalJUkMxhNoBHK/+F+8XStpzQC8ZCqZwwNWc4JBCWuGI0WOEUCR"
        "TIHH+zVTCSzHkLIci83sdFbR+cJnrVIZbj2LrhS7q9s+tN4UHCNKtwz6MSI+gDtFxBiogeoWOfE47v7T7XDsG2f89p1YCXiX"
        "9Rwvn4bK+LHSX29yqyCq2KswDpyn65d+Es4++60ltWqJc3CeB1TNt1REDv0ZRcoP2744+UUPhSQxTsJ9m7vVzQKO9y/6bcU9"
        "uMmNWFeyg9vs4Dgevqx6ULrk8TmiRLWLra0EUWoqnJGjKE58tgl7UjNsBqQA3zVEFeluCELOfUK6Di3Jch5OrAusEYrUjpN7"
        "EbyGkbAK3bIXReyY4NhspJay9NjVhHsYg0OoQm4Hsg0RxqAe1bQpmU4FLsGn+vi7rTimRhnYBsi5DEHV5CWsagQr6dtKsc+h"
        "NIzOZMydZpAvKWPhmSAGHMMU6lQ+kEYZajJRdKfG45+rZa4RS2RlZsf6IiN3Xs/x1HJWbsyt2jzAOJrs3Au40c2x+gIgR7t9"
        "rhxkaZtXEDWchDlbquEkOJCsI2k5SRtx57Ophkj9ANSRtp6krUw8wXjJ7RgvoRsgfbf+CdwEUqTUJbibnrlc80pF+NiFgeUY"
        "xE520srAA59zlKKUBgRYoBFFTqfaVNPMDifCVTxiBYxOas8Co51kBs7i/G7iEsQcwFWs2VH4gU0gvlKOAC070J52IHnt4Ore"
        "sXWJ2Ao4ArlNu6+nI3nc4fVcugP42bHO4e81eES6Y/l34BiJA58Az0hG04m2YQfaNnhFTSF2kpFbYC9R8EpB4wZssKa8dKUp"
        "Go7BcljEiRgvYgbCjHN7d2NcI7bkec/vJeLHGuNoI+NtJSCNg/UcC+Po5lbthlcO4BzSS7cxjjnn+IIYB7cvXobqXiTpqluI"
        "KJ57Z0/uVicLmH++NxOY93VnI8pbdLqmLOImFGdjFJ71t/s4SpVznIVRvVVDVFGMkOzA7UKEUJCFEq/DgoPXC1NwFh1MrB+0"
        "8Hwg7XLKFKgANVyl0njc4RW1BQfBLy9W8LtipSExwjTdFxXn23oZDsVqRc15t/JYGaTUvi8N5GUWlozShuukCyjAPY7VBn9I"
        "8jhqe0atLfdLylSDGK06Je5UG9eAYXBx2e5wDe678Y14ezgvdtqblTvPrWL6iAQBn9uvVn2BkKPdvjgI0m7zA/RGeKNRt+YR"
        "97ebiHubu9VkASenNgN9XKlMbPptUeViw+PYA7gfI7l+IBIk1RaqLsd7J6VRstS8z9Wue27FnewgSlIl/byo8klhFVWvEVZc"
        "U1bjcWmGCSymQRYzm5hcLHBqGNBjtaJlP9UQV/KCq3pvDE8LaMHfZ2NQkhHCFyMoVOyQNyICtfv2fg7AnD/fZUCHZJwh2i3I"
        "pZkJgPfQCdBrMiayGQdVatVPjAwqVYVJkxnz0fifIOJOv0w2sqLsq4JqHlU9Gsf0o4gYrTolcQ0cGxY3MSIuXIMqIVQqInMr"
        "4bYTn4wbzftXzafOHpRb1UTI2Y1E1KqLX0zkaLcvbo3Dg5BENblb2C/1/r1xL6LJISqX3G4i8OuIm7An6QrUrgWRB6JA8SJH"
        "GZhCUIU1J8wUJqowETbYDfZoFmRRjOXlnETYoAsnbLJhKSdrGrbmsjFuPpuyZ1VclDgsmhtbLjArivd3R1FxIFzVHAvue3XT"
        "zxf39zjKnL/PWHvo+T6aDRPTDkqwEJHtu1Ml6KCzLa8nqbROYsYtEwimPaaHQKIFx2gRo82hojrF0cuMa8y5RtMrVz5HzSHD"
        "nfiGgmG4NXlsaW4HU9bfuaik+7pqcqvO74tzxDP+Bdy+eAjSbns5SQdJ5lnAbT0Je//Cx21r3AXem3gJ+261HRylF/DNq1ID"
        "zYWob4gAAAvaSURBVIGQXBG33UeBPjV9a0EUqDNruFBajsIVNa6s+SKOkgwQFwhAlg1BFkPFiPEC06zUeSjEx1/pzaqQRP4y"
        "nkgOGOSsie0boM1MVnrnsO9hxXe7jLlMBAE07mvudyY+DzxGfgQZxohT8DXT9VnNeEUKjsR0diIbEM6Wm6XZmVQJPkdEilAV"
        "27pu4xgG34Wq1Hpvy022IsdoEWPMcthGuj3WDK9p00VONI2k21EE0rfqapRwr1+KxnH1vR3hiPO5HW/BOF59tjlvMbfq9aZc"
        "NnzBjYPbl6NKbt5ni1vMAuboadaTSCeId5Zr3OO+UbkETchNFrlc8pItPxFEuavaikV5rOUok0Kr55UUu66O88hx7MiMpqf0"
        "cDhUfnZLkGUMZBm0fztHmGew+MwU61LYNivUNrINTuJAAHupmqQdU1M1CxZWfQ6Vm2/NAHQE8+KFxHbRwySmk5M7cN9BiPbP"
        "ukgx5JDU/lVp57oLYkG3ksRbXcP9g2WOsU+dUp24RieXaplrxI7rixry70glIEGjTVm/0CpVbyr1eeVWPez2xUWQ7kbfVL/R"
        "HMyYBXxB/XFUQeDTShf5v7sb55HUjcr13s48cst6A3bo44nlyicvuaR2nRa163jbG7blKC+fdPTBubLuXimCZtf5XgoffQuI"
        "8pOILFvX3Wq1WnN8mCBMVbuIMHdEFZIV3GRVORkXNvdFOR1ztqJwAa70jFTbXex3sZ9tzuysP5PbZedxcp+dspS/9/j7FHuo"
        "Tglem0g2RwhW8kmkO34eU12fI8U0u+JYwCRxjK0Y5CNi2uPgGE9HjhHVvlnYp051IuIxl6rDNToq1bwtKI1j9mfCGduZgRfY"
        "hUQ1WblfEuPg9uVAkHbbW98ulVeLnluqiZeULZo8gJu0cZN2bMgyojRv1/AUIoraWJf72Ejgzs9v2bVvQv1SEVk4MZQdFXbA"
        "Wwamwt+/qNQwvoZnDhbUISLN0leZpVLbcqSNkeletXxB9bIQVaeTQIxx8/8HaqWfhglQgp+nixQcazaGwfPzbzWDICSWoRaq"
        "1BLHaBCDG9Up7mNxU4yIc9/lGi0RX8Q3KOF+Xy1XAn729RyfdPtyIEi76baeJFYmzjs3XlCyUkm8BNyEKQz7uEmby9XETRjh"
        "5Yp4u4Moc45C+ZK5XeuDplcwfHGssOw+zrndRJbx7ZMy5H5c5G5tKxOEoS8/zRD9yIgwW1ixt1xRbFeRw1x3ptpu9sfcDMEO"
        "S+TZwr5Z8fft28ftak0OZLJtQSxBhu1rLttglOYn8j5EiPVsM9aAN59ns/eyG1elqHVjIAWNg0hBzsWcNVGlbi9yp7ocI7pT"
        "LwmHk+PVxDXa+o051/hx5BqtSrWIb3x/Ht9oKwE/j3qOT7p9uRCkuzE95U0gyffweydlPqLJomtK21X+FSxx9aUmn+s+iMIt"
        "ospC9ZL7mjiK3L8dYymbm+KqK2YP47JTG0AYLsxhdyaPr0x32ENHsWGBIM1GPn89QZxVNR9WvXQbe1KD+z6uGmSAm7TDPV8f"
        "929+bc1vb+OErvbkAmyRokUJuk/3OtyC2c/zRtFqP8fgtsQzFBFjMdmJiJEVz9AA1LxX7lJUnFvLNz7bMtnHuX25EKS7cSWi"
        "L9vES95qVC6eoLZriqxob8Ws4Pf+U4ybSBZpF1GaLiqixtyM7kQ3It+No5xo1S9cZFtNRaN0fgRnIcE9th6ziTdxkY6z3B0/"
        "iZUbP5v9NU+kSXq5IM4mV/SPefvk/tsfH/K43H7Kjfray+tnJ4Ur7dyN7yvqE5CuRQqJeAMptm4vuAU5l7m5+H6iSt2YhC7H"
        "kK7qODaSfXtqs+F0Hy65U9E4rs8bSQuS327iG282Eu4bX17j4PblRZB26/ISbl1uoppcLrUcN+H9rdp1EKJwE1Thdh+uwq2L"
        "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"
    ),
    "big_err": (
        "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"
    ),
    "youtube": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAEBElEQVR4nOybW0gUURjH/yNSFN3oahcijKKsh4ISwUcriEBN"
        "fAqpkIJAieziSy/WgxQodhFKAkXtVRKkh8InHwQhKZBE0dREcc0Qb6RdcPt/jmPkbZ35Ftfd4w/+e2ZnZ3f2++bMnO9855xo"
        "LAE/sJXFOeosFUftpnZQ67Ey+EF9o3zUZ+o9VWsBg4G+aC32IQ3fyOIhlU1FI7z4Qz2jHtDIkYUOmtcBfnv/FeoRtQvhjdSK"
        "XOq1NWXa/yzkgHssHiNADQkjxPAcGvN09gfWrKOiWJRRlxGZPKHu0OhJZ0fUrANuInKNF27BtnGGmRrAq3+GxTvMdUqkIVf/"
        "vGW3FLYDaPxaFl8R/g+8pdJHxdL4Cedq58Ac4wWJY27IhlMDxCMxMIs+Gr8nmsYnwDzjhd1iu0R3qTCXVHFAAsxlqgaYWP0d"
        "YizeB0Pc2AwzGRYH+GEwkR71BWTVATAc4x0QvDRXbCwzhkwZHjkCHDoEbNhg79/IrNq+fcDOnXBFfz+7Z+yfjY/b70dHgbY2"
        "oKWF2b5aoKMDwUDfCohhhYVARgaWlfJy5q2YuBoYgAa9Ayorl994hzImrzIzoUHngKNHgeZmhBS55Vpb4RXdQzAlBSEnORka"
        "dA/BuDiEnMOHoUHngP37EXKkhVGguwW8nLy4GCgoQNCI0XVmdQ7Ytg2uGR62m6+DB4GqKqjZsgUadA7wcnJrOhMvgUx6OpCY"
        "CDQ2wjObNkGDdwesW4egUF8PnD7NkUgORfb2wjVr1kCDdwc4Iapb/P7591VU2E/0vDx3vx2lq8Qra8h7cnrIzu8iNluvm6Kg"
        "c4D8UcvlAPJ8x8s+Cafz8923LGNj0KBzwMSE/lkQHw+UlAAnTsATSgfobiDponrlwAG7GWxo8G68MDQEDToHeDn5Ziagi4qA"
        "zk4gLQ1qBgNOA1oU3S3g87mPxbOyEFR6eqBBVwO6uhBylJkhXQ1oakLIkTSZAl0NqKlByKmrgwadAyQTI7m5UFFaCrS3Q4M+"
        "LS49O/kjy42cMzcXWoI3Nijd26QkO0c3MmKHqLJPIjtp892mxaWF6e7+1y+Q35SrveLS4mHO6tAYDGfVATAcccBPmMuwOMAH"
        "c/GtOoAvn2AuLeKAaphLtUSCkliXFVemzRWUhVTbo5iP/cWNlzCPF7T9tzNdXpbHfYG9FtAEZF6NLJgYmwqEuCHpXZnnMonI"
        "R2y8JMbLm5lIcHoNzX1EPrJqrNZ5M2eYhrfDKxbXEJmUWNNLZRzm9AV4wHUWdxFZeQKxJXu28YK1yDcusnhO7UV400XdpqFv"
        "5vsw4MgmHXEV9mLDkwgvPlLFNHDRhOWSh3b9dhN5gTpFHaeOUduxMvhOyYRFGaj4QL217KYuIH8BAAD//xBRWaQAAAAGSURB"
        "VAMAvxXvBDcVfwMAAAAASUVORK5CYII="
    ),
    "youtubemusic": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAGaklEQVR4nOybaUxUVxTH/6MopkrqggvUtWqMlQ+iNWo0mrjU"
        "NXGJW1BbTDA2xqCgbdw+WJekjUZprQHUFI0mxh0XouL2wT027jQ1sQbcINQFW6wLCD1/roMIc98Mve9NGKa/5PCGuXde3jnv"
        "3HvPOfe9EPhAKdBUDl+IDBX5TCRCpLnIR6gZ/COSL5InkiWSKXLCBTz19kOXVaMoHiaH5SJzREIQWBSL/CTynSj5l66TRwOU"
        "qu+/EvlepCUCG3rFtyLbXWWqfYjOAN/I4Qd48ZAAgooniDI/Vm5wVepVRw5pIl+idpIkMl+ULnF/UadSh3jUXuXJPCgdyyn3"
        "ALn7Q+RwDFWNUtvg3R/hUiuFMoAoHyqHHAT+hOcruSKfivKv3Hc7AcGjPGEc8zU/uD2AFmmF4CJXlI8MEeX7IPiUJxHUndHd"
        "WAQvY2mAPgheyjwgGN3fTSuXjIMC+fAxgpPnNEAp/EFLWWUnTZIQZATQujXQrJkk2U1V25MnkrhK5nr/PnD4MHDgAPDoEfyB"
        "8wbo1w9YvRro27daP8O5cxK1zwcuXYKTOBf2Npd6yZYtwJkz1Vee0HAXLgCpqUDjxnAKZzygf39g3z5lBDvIlTht9GjgyhXY"
        "jf0ekCBR9enT9ilPIiKUN0ybBrux1wCLFwNr10rxzIHqWf36wLZtwMyZsBP7hsCoUcChQ3JGh4tIb98CAweqSdIG7DFAp07A"
        "9etSI/ahSLxjh1Qdjqn+d+68/310NDBsGDB5svdzPHsmtWkpTuflwRR7hsCqVd6VT08H2rUDYmLUhU+ZImXK7Ur4+eFDdezY"
        "EThyxPpcTZoAS5bADsw9oHt34OpV6z6LFkl9WQrMCxcCCxaoIMgT+fmq37p1wIoVwNKl1uelQe/dgwl1lwHLYALX6S5d9O2J"
        "icrtuTJMn27tKQ0bqmEwRKpz86R89+YNMGCAvj/PxcjRADMPoCs+tdh82bVLjemLF4HevVEtjh+XvSjZjDp5Ehg0yHOfggJ1"
        "DQaYzQHjxlm38+7T/Ssqf+qUCnELC61/O1R24eLigLlz9X0YIdJjDDAzAKMzHZs2ScnxlYoNKlJUpGKFDh2AjRthyXLZlbt1"
        "y9rNra7BB8wM0Latvm3nTmDqVKBRI8/tjx8Ds2YBUVH6NZ0R4PjxwO7d0NK5M0wwM0B4uL6N456TmTeyslTuMHGi5xl9+HCV"
        "UOlgam2AmQEiIz1/Tzd/8ULVAHxlzx61rHF9rzg/MC6gYUo1c3WbNjDBzACvX1u3M2ytDqGhnr/jeXTnMgy9zQygq9rUq6ey"
        "wQcP4DMTJgC3b6uosuK8wbvPuUCXYDGCNMDMAMzTdfToAZw/D68wpj97Vk10HAKV4fjv1Qv/6Rp8wMwA2dn6tpEjgf379e2c"
        "QFNS1CTI6o8O5hDMNHXcvQsTzAzA9FcHl0AudZs3f/h93boqEGImyGXQiqQkFUswSdJx8CBMMAuFGzRQqSmPnli5ElizBrh8"
        "ufrrNdNlegbPwbzAE1wtwsJggpkH8O7s3atvZzY3ZgwweLD3jLEiDIyYB8yYoVeeMNgyxLwewHDViq1bVQ2AkyLTXF/Ox8CI"
        "OcD69dZ96R2G2FMRSksDYmOt+7BKHB8PtGihips9e75vKylRRU8WR5gCc3L0FkUmJwOzZ8MUewzA5evmTe/j8eVLlSJnZqrZ"
        "nxMhi53t26uljhkg4wFv2FgSs68oypg9I0MGlcOPGDEi5BBhrmED9l3t0aOq5OU0rBHYpDyx93ZxD5CTV3ExbId5B0tq3G6z"
        "Eee2xhjB6Yqf1YU5BaPBGzdgN84MWMb2XbuqJdAEjvcNG4Bu3RxRnvhne5zrv1VC4wkWQ1lCv3YNTuK/ByRYuGARlauFpwck"
        "KDk5KrLkasI8wg/QABLPIhTByXPOAebRROCS978B5I+zs0zN5ncaIB3BSzonQclGyt64CrZnBfkiVXgdKSpL/okUBB/JonuR"
        "+3F55rF/QL0LGAz8CfXCRGFZKCwf/pYDH8EqQe2HOsZQef5Tngu8e4fGnudOajZ8a+yE+58q+0oyHGRfG3GonaS63r0q46ZK"
        "Nigd+CCeZCF+yhH8A3WZU1l54rL4BR//YFn2EwQ22SKJoqjHbSqvW6tiiFiolw2jEVhwI+JnUfAXq04+7y2XqiWSm3Sfi0SJ"
        "SJUC4agZMHf+TURK0/hVJMOlljqv/AsAAP//61bkRAAAAAZJREFUAwCWc86AwD96/wAAAABJRU5ErkJggg=="
    ),
    "instagram": (
        "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"
        "VbMc9jZtl1WnBjf6B2RZX0tlGYrumJ6SguObpKJvX72/GXuD3PyHPrEI9qu/gS8DnjXOJrmSDaZuj5f+4icpKTCzibP9A//a"
        "TQi98Q7MwlQFcPhqjs/SjmvHwsEsQr+je76ou8xNJ9SNEL6win0o5vKMIM3e+jCL4h0oQNib97GVNW1HgcJl526wYBXAf0zB"
        "FfBfFChs6L8vSZA2oUDhsktVpwO7+GSAAoNS6q/qse+USNPGqAT6HAoMNvs/Sw4+H1PXAvaY9VE2I2Te4M8XmKw2hJbxoqqA"
        "YXtX9UgUs/nPSZDvMBkJoXcNb9yopqa01SD/DQ0lykPId4iy2NPYsEOr9j3e7K17gYD8DHkJXfm1xoZ5+pZ++YARjQ0/Z18P"
        "8GkS+QKThb3p+/sKz0m7x9RWW3eHTPBnZg0eXNx8LFG6qPrNhleNDmbcZGvxzpkLSuazjQ+x/0j8sqH0MKFY7tndsGqgblnv"
        "Mp6advcw2RKfRSFdx64+jp04lp0+FBcEtIMN2Cb2ko4SKAcssvWfVXtWZ+XWPwcAAP//PWp0GQAAAAZJREFUAwD3Z9771Z9b"
        "0AAAAABJRU5ErkJggg=="
    ),
    "tiktok": (
        "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"
    ),
    "x": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAJJ0lEQVR4nOSbCUxU2xnHvxkGhhnZZDXwlGdBQECeiIii4lY0"
        "7sbdGDWpVdTEamijRBrjkqCtmjZqjUtMlRitu3Frn7u2ImiqER8VXtlEhAeMwAAzMINCz/+8d++bGRTQuUOF+SU3c88590K+"
        "757zbfceBXUOT2dn5xkuLu5zVSp1hErVy5sd6nfv3snd3XvT/xOttoYUCkVLY6NOr9PpNAaD/jutVnumubnpGhuu7uh+WQfj"
        "ro6Oyj8EBgYneXn5yHW6evrxaCDh/EugVy9Xk8OF/1ZXV7UUF39/oLm5OZVdUvexe2Xt9C/z8/vqz4GBQe45OU+/GGE7i4uL"
        "G4WHD6Hi4v/WVla++Q3rOsGOVsvrPqgAuVyeEhoalWY0GmQFBS+pOxMcHM6WiGNrbu7z37LmnyzHHSzbjo5OJ2NjE9az6SMr"
        "Ly+h7g5bCvT+/XtZdPSISRUVpb4tLS3fkslMMFMAe/LJTPjkzMw71NTUSD2FpiY9lZYWUVzcuFj2W8u6MoUxUwUkhoV9k44n"
        "35OENwV27OuvB0zUaCqggAL0yX8ac2YG7yzWfG3tW+qp1NRoiLluma+v/99YU4U+rgAnJ+cUWPvubvA6Q35+DvXvH+KhUDit"
        "RRteQBYSEqUrKytWNTTUkT0AFxkQ0E+fl/edi9zFxWVc796ediM8gKweHj5qFt0mKNRq1191tyBHCiAzC+1/rVAqVXH2qgBn"
        "Z/VwOUtqfBDb2xtQAGSXq9W9XOx1BkB2BcuWHOxVASwmcFBYk8+PGDGClEplh9fp9Xp6/PgxfS4+Pj4UERFh1tfU1ESZmZlk"
        "DZBdQVYQExND+/bt69S1M2fOpMuXL9On4urqSg8ePKCwsDCxr7W1lWbPnk1SICcr2L9/P507d65T1544cYKCgoLoU2CVHrp6"
        "9aqZ8GDDhg106dIlkgKrZgBYtmwZDRo0iEJDQ8U+CFtQwHMN/uQHDx7Mn+TFixdp2LBhfPp2hvT0dEpISDDrO3jwIO3evZuk"
        "wqoZALC+Z82axX8FoqKiKC0tjbZs2UJTp06l2tpa3g9FYdZ0hp07d9KiRYvM+q5fv05r1qwhKbFaASA3N5eWLl0qtqGAAwcO"
        "8POysjJauHChOLZ8+XJasmRJu39vxYoVtHHjRrO+p0+f0pw5c/j6lxKHvn1/seX160KylpcvX1KfPn1o6NChvD1kyBCWeeXT"
        "ixcv+HJwc3PjXgNMnjyZLly4QBqNps3fwdjJkydJJvu5Wvfq1SsaPXo01ddL666Z7NLMAIG1a9dSdna22D569KhowGC4njx5"
        "ws9ZEsI9gkqlMrs/Ojqazp8/j8qU2FdTU0MTJkygt29tU6eQVAEssKDp06eLax4xwpUrV7igrC7HbYUwFhwcTMeOHRPv7dev"
        "H924ccNMKUajkc8IwaDaAkkVAEpKSmj+/PliG4LCmgPYA1PDhutWr15NXl5edPv2bfL29hbHsNYXLFhAWVlZZEskswGmFBYW"
        "kqOjo+jCwsPDqaqqii8B2AW1Wk0jR47kY/ASkyZN4teYkpycbDZDbAFsgCw+/petGRm3SGpgxO7cuUNjx47lbZZz0PDhw7k1"
        "d3BwoEePHlFsbOwH7z18+DAlJSWRrWGyS78EBDCF4bYqKip4GzMChs/d3b2NPTAFvn7VqlXUVdhMAaC6uprmzp0rtgMCAniU"
        "CCzjA5CXl8evl9rXt4dNbIApMIrsrS1NnDiRt0NCQngo/PDhQ27d4RJHjRrFxzA7kFtUVlZSVyB5HPAxELvDHQogTIY9AJs2"
        "beLKAFgmuA6K6Cq6RAEA7g8RHf+nLNBBNufp6SmmtkKgExgYSGfOnKGuossUgGWQmpoqtv38/Hh2CDDlTe0BlotlLmArbG4D"
        "BBDpYX2z9xBiH542pj3cJWIH5P9C7DB+/HgeGZaWlpKt6DIb4OHhwSM9PHVLYAMSExP5+ebNm0V7ICwTX19fsiU2VwBi+2vX"
        "rvGQ2BQERAAB09mzZ9nT6Mvtwbx587j7BFBYZytOn4vNFQAB4uPjzfoQ5WGKv3nzhrdh9WEPECGWl5eb5f1Ig3fs2EG2wqYK"
        "OHToEE2ZMsWsb9euXTzU1Wq1NGPGDB4iAxRY9+zZw8/v3btH27ZtE+9JSUkRl4nU2EwBsOIrV64068NTRl1AAMtg/fr1Ynvd"
        "unU0bdo0fr5161a6e/euOAbXCEMqNTZRANJc1PRMQQ0f6a0lKJ2ZrvNTp07x6jGWAMJiIZeAIUWQBE8hJZIrAGtWiPcFkAKj"
        "sCFMd0tQT8Q1AG4Sgjo5OXFjiCCppaWFj6HWuHfvXpISSRUQGRnJ6/jw7QKo+6Gk9aHMT6CxsZFXkoTK8sCBA8WiakZGBq8u"
        "C6CAAiMpFZIpwN/fn27dusWLnwIQDFEdEqKOsKwso3q8ePFifr59+3Yze4AKk+XLks9FEgVA6Js3b7YJdODTnz171um/g4Io"
        "XnwIHDlyhHsEHCibCaCiBIOKX2uxWgFCBmdZ0oIHQAD0qZhWlhFEjRkzhh9Y/6ZgBhw/fpysxWoFnD59us3rK/h6PL3PwbKy"
        "3B7wEta+KbLKp2DqwyqbWma4r/v375M1wGbgHSIqSB1hMBjIGmQRETGtOTn/JnuEyU5yFli04vt6ewMyM9nfyxsbdTp7VYBe"
        "r2uQY5uJvSqAPfxKucGgf45tJvbGTwrIkdfV1R+z1xnQ0KD9q9xo1P+9pkZjxAfE9oKrqzt2khiZC/0HAiFDUVHeX7DByF6A"
        "rEVFufi8zSh8huHm6+v/2s3Nwy0//z/UkxkwIIJFmdV1VVXlX7FmvRAK11VWls3H7qrevb2pp+Lp6cOqzQ6tTHi8sOTf25jm"
        "At/m5j5PDQv7hnoqkC0vLzuZnd4U+iy3zf2roqK0f1zcuMH4lha7rXoCePLR0fGUlXX3KMtVNpmOfWzn6O+Ytv6IDUbYY9Od"
        "wZrHtGdPHrtH23yk6PCR+zI0mopslusnRkbGqLTaajIarcu6uhq4Ojz1H34orSopyceHiekfuq6jzdOs4KFMCgoKTXN39/L8"
        "efN0/Re8efrHo7ZW87awMPf3rBB7sL17O1SAAHuV3Vep7JWkVDqPZkeIs7Pamy0RxReyff4ds1cag6Hpe3b802DQHWIV5ded"
        "uf9/AAAA//+5/4v0AAAABklEQVQDAE0w7iAA5PC0AAAAAElFTkSuQmCC"
    ),
    "facebook": (
        "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"
    ),
    "vimeo": (
        "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"
    ),
    "soundcloud": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAE2UlEQVR4nOyba2wUVRiGn+kNeofSKpcqplGjVWNajVR+6A+V"
        "WEiIYKvRGLwEYzUFQS5i+stblB8mgCZiIJoQNZqCoglBFI2aGGOiEUIQY4wpBG3t/W7v43t6doGWXrbMLmV39mm+PWfOOTud"
        "753vnDlzZieJEHDLycFlibL34FCo/Dzl85RP41LApVvHUq9cnew4Q3xJAoedapon+6oz4X6Xk0kKLylbqZZJRBMuAzrmHXTy"
        "onOQ9vGaOWN/V+XlPKrs67LLiW5MVGymmvedYddGMrYA5WxS062qnTBCogZXfw7r1SW2j65yRrZTzynjPZWuIhZx2cZeNsjp"
        "oWBRwogGZayNWecNDuuGfRxRFMBdyd0kcojRosQeQ4qEUmevrhQEBHBLmUEGJ4n+AS80XGpppMD5lh57tjNYj1+cNziax+RS"
        "YbLBcH8W/7HZfDjq+yXq+z/iRwa5PUkxcB9+Rb4nqT+U4Ffku5nfz8W/zI0LoI9s/Et2rM/6JiUuAD4nLgA+Jy4APsf3Akz/"
        "UvfMTCgqhfxCLVRoqa75H6j9A058z8VgegWYfx1sOzF2nRHis61wcAeRZHq6QP4NNi0oHr9Nznx4XKvYT+0iklwcATJzR26X"
        "roFrdBfuhPDv71oNWw7AjHQiQeQEmDXPpsXLYPkmm6/YbdOMHGuhUrxUoq0lEkROgFVvwFVFut/SWmv2ZZCYDCVlts5EhDFn"
        "Cg+eSu4nEkRuEEyfpbM82zqalWfTtMCdd2qmte620PdXcAus+1iRJUE7W+CrnXD0EF4JTwQs0tlZGlhYfvBlmxoB0oxl6VKX"
        "IQHm2HIjRkqqtamy+AEovBNu0zJm1Rdw7xq84k2AlVW2L195Eyy8WSN3vg6q0talz7Z1ZvAyFnQ4Je1smVee0CXy+jvwgjcB"
        "ljwD1y5WWGpVLWcB5C20Zz41y4a7ESB5prUzAihNSrYWDla8gBe8jQHmWm36ZDCkg2fVOBwc4IYGtf7eP3LAM2UD/YSFqxfh"
        "BW8R0Ncj+09TWD1YHxzQ3oK703Z3uwarZjmr8v5eOdxnq/p7bFtTHhZcvOBNgBZNV+v+hJ5OOdxqnTa0N9gR3ghgRDL1RihD"
        "b7fN93QRFo54uxJ46wJH9YT51DHoaJSUiXK4CbpabZ0RpEuXq7Z/A1Zvy9uVttZZS/R4FTaCf7gFL3g7gt1P27TptO3nzX/b"
        "vMEI0dFkt+dcYaOlpdbWBQUw84QLpb4Gtj8EjafwQngmQj/t055SbKjvf82WfbdHB3fSRkJrwPEPnj9bd/q47QZ7NtpJ0VRo"
        "qIEfPrLjiUcct9zjKBLlxJfE8DlxAfA5cQHwOXEB8DkJmgb14l/aTATU4V/q4gLo4wh+xeX3BBz241fku4mAb2RTWKCPGdr5"
        "i68TnGr6FAo78Rsubzu/0G/nAX28qs8G/EODwv8VkxkWwPmcDinyCOe8TBTDDDHIw4r8TrNxZiYYeIemiljHZYPzCYeDm+c9"
        "nnXL2KXS1cQiLu/oRFecW3TevYAaPKmGG4dfNowVjC9DVI523jDuA3otlq5Q8qZsAdFNjew59flPx6qc9BcKEuIx6WdeqCwi"
        "mnD5VfaWs493J2oW8k80JIQe7LNMO71V37pRefNLp1wuDfRoit90bMd0bD8rf0BnPKTL+v8AAAD//xj2rm4AAAAGSURBVAMA"
        "kZtJXu+wMgEAAAAASUVORK5CYII="
    ),
    "spotify": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAIRklEQVR4nOSbeXRU9RXHv783k22ykgWyTAgEIkgji0KNStEo"
        "u5VqCJ7aUgEbrKfFWrFCLdpaD7T8YVts02pLlRY51VPSYKkFEjaPFRRNAogmbAaSTJLJvpFkss3Pex8kJmQm23sTTeZzzjsz"
        "7/3edu/v/u7v3vubMWIAmA+sCBZCLpSQC2h3GiQi6DNMCGHCVwApZZMAyqWAlXY/lRKZAsohy+Ld1f1dK/pqDH1vmb9Po+cL"
        "JPg6AWHECILeuV1I/KHR3vSr6qX7652dJ5xcLaIOJq1SpLKV9sZhJCNhtcO+oXhR+i6SVl7fbHB0jfn25U9Tj6fS5oeRjoAf"
        "bfcHXJxWW78r70Tv5u5IKNGZyTvo28MYnWwrWpj2FElt7zzQwwKibkt+QghsxOglwZ8soWFX7gedB7osIPJA0nwFSgYpQMEo"
        "hmYIu1CwhCwhk/evKmDfEq9og28BRrrDGyhSlhq8qmIvJ75jU3vbbDA9CXcRnhEioq0l9DH+qipASPEE3AwFcgN/8nyfoNiV"
        "9+GG2BX7bUaKlu6Hm8KyGyGVBLgrJDsNBYTDXSHZjTTvu60CWHbO8AIxDIzzGoOFY+fgnrBbEOkdiiAPP4zxuJpq1LZdQQ1t"
        "JbZKHKnIQUb5hyhrqcEwECiiM5IlXMjMwMl4bsoqzAmaOqjrsmrPYfO5ncipOw9X4jIFcA8/E7cS3zbfTeGmGNI9KKfHm5Yj"
        "2HJ+J+rbm+AKXKKAWYFx+OvMp1Wz1wMeDo/kbMWZhnzoje6Jz8PRi5D29Rd0E57he+25dTOSIuZBb3Qtc62NuY/Gu2tKCZ6K"
        "B7bd9Di8FU/8s/gQ9EI3C5gbPB2bblgJV7NlWgpmD9Kh9oUuCoj2GYtXZ22AIlxfSjAIA3bcvBFhnkHQA13eeEPcQ/AxeGG4"
        "CDT64fHYJOiBZh8wxW88vhU+t9dxnrayas/C0lyBouZylNqqUN5Sq7YpFIIFGH0R6HF1C/EIxARTOKJNYzHRFAFfg3e/z109"
        "fgn+fOktWFv6Lf33iWYFPDlpRY/9s1cKsSl3Oz4i4YfKFL9ozAuZgbtCZ+EbIdOdnvej2AfwXN6r0IKmOMDfaMKnd/+jx7Ef"
        "nH4R+8tOQC8C6Blrxi9FyoR7VdPvTk1bA2YcfQRa0GQBC8Jm9zrGQ6JTAewcOSiKIfOOMY1TcwAjOTG7tKOurVEVoKq1DhZb"
        "BT5rLMGlxlJUtNb2uB8PpZfy0/DK5b34fsxS/Czuu11tYzz8cXtwPI5Xf4Khok0BY3srYP2kB5FIpstOcSopY7BUt9XjRHUe"
        "TtTkqklRMSVITIu9VR3zMwImY8m4W7vOn0+doEUBmmYB7lFHcK8PRXgm2CNAFfD5qWvw/ryXsfnGFLWnOwnw6LkeO9GkLZvX"
        "ZAHBngFO27jHcmovIJuyuRzK7Ky2atXkOfVt7LBReBuspsN8j0m+UbRFYjJ9svPrDofWyyPvxJn6fHV2uCkgtkd7lE8YtKBJ"
        "AWGejksJ71SexNpTL6pKcEYZTV9l16aw7ibMQ4d9y7LwO9T6AcOCJ4yZ5vA+zqxwoGhSgB2OJxALjdvuwnPvRnmHIcwrSDVx"
        "T8WISnJ+7ABLKD7IbbjcdW5zRwv2Wo+pW5yvGT+n8JqLKM4YWqL9BZoUUE5pqp/Jp9fxleYF8KLk5Ub/GMT7TxzIrZDXUKDG"
        "Dv+v+hjvVp1WFXGh0YI1J7eqw2LXLc86vO5LDYQ4sos1RTpsWxF5FwYDK4s3HvNsPTuLMpCav0f1G6wQ5++grXSmSQGFzWVO"
        "x2Z3CpqtKGgqUx1gVWs9GmhuDyX/wUPCTEODBe+OF6W8nFp/j5TxelGmGiY7vTfdVwuaFHCoPBsPRiY6bNtRuJ9ePgMXG4sx"
        "ELh2eGfITIot5mD6NU/vrSrim31ed7AiC1rQpAD29myu3GPXs6f03S7hQ2iq46HClR2e9jgBYksoUROkGjVROlV3Ud046mPn"
        "l0KCP2S+p8/nsyUdrsiGFjTXBLlK46hUZSPFsHfnOT6QMr/+YGe2t/QY3qBqD4fFDEd9/034jdNr3rAcxsbcV6AFzfWA1Px0"
        "h8fZfG8OvGFAwjPhFBg9OuE+HL3jJbWazLOIydh3jSH1Ujq0oktV+Hfx65BM0dpwwv5lU97foBVdKkK/vfgmmii8HS7q2hux"
        "7bM06IEuCuCM7VEKfTnNdTVt9nasyv51r7R5qOhWxeRgZcv51+Fq1n/yJ12Xy3Qt424veBvP0rhsk+3QmxZ7G354+vf4j/U9"
        "6InudWwOYZM//IWa6OgFF1aXffAM3i47Dr1xSSH/ZN0FJB77Cf5VfFRd4BwqHeRTXivch/nH1yPvSgFcgcuXx7k69Mupq9WY"
        "YDAcqz6D58/+HeeoyuxKXK6ATjjQWUylLq4XRniHqNWgoGs/kOAfR9S0NqDIVo6D5VnILP9IzQKHAxF9YLkNQgzfss5XizqF"
        "ut8KN0VKWBUI91UAy64IiFNwU6ieeFaRsuMtuCksu2IxG4/Qd/2ilpGCRL0ltPCwgvjdrVLYtVUVRiBSsb+M2dltaiRoM7Vv"
        "IY9YATeBZW3xMGzm76oCKufubRAKVvLfSTDKYRntsH+nInH3Fd7vygXU/9AIuQmjHIp7nipZnN71M7NeK0vmA8nbhUAKRid/"
        "KVqU9lj3A72yQcvitLWkpp9KNpZRgiqLkOuuF55xurZozkx6gFzlH+mEKIxkpLwsFbnesjB9j6PmfhdXozKTVgu7+LEQYhZG"
        "EFLKkzSUU4sW/fu1vs4b8Opy+L4VYQZjx72kjNmUPcaTXX2NHqBtcV4nyMArycRz6Z3OUG9ndbQb/mdduntA0/rnAAAA//8C"
        "uYenAAAABklEQVQDAFpaDDWJ/6ThAAAAAElFTkSuQmCC"
    ),
    "applemusic": (
        "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"
    ),
    "deezer": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAADUUlEQVR4nOybT0gUURzHv29c2UpC0kylk0sQVBBG6Eody4gu"
        "dfEQ1to/FBL7Y3jpkEZBx+gPFZGx6qlDdSlKrFNhK5VhJJ7KTv7F0Ew0131936KitrO7qMvuzJvPws68eW+G+X3f+/1+b9/s"
        "uBAHT0pk1mQIpRLYz+I2IZAvJXK4XYcUgPcyAYFBSPTznr4JoNUt0FbWLkZinSuiVT7aI9enB3GVrapZdMFaBNlht9IkGsoD"
        "YsysUUQBJKRo9sLHnRtskQsLI9WoAOqOBdAiIOTSeiPSSU1FuETjG61uvIIukceNv6kYNRHrFxbqIQ1PMR7z6HHYEYmb3wOo"
        "rYcIzR1aNAI8SiW7Gq8QOO9ZMhLmR4DfK/dRodccMgZsDGNCiIHgYEVAtKpyWICXW6R7OBs/7eDzcSHRNzMAz4leMRnu7aFs"
        "XNDGeAXnMWm5qFK7c8P9HPSjTn2JlmLpDQm0Q0MMiRJjRuIwNEXZ7hIGvNAUZbvBiJgHXaHtdAN9BVC2uzjxyYSmKNttPeuL"
        "B0cAaI4jADTHEut8BQeAvfXm9c0lWDaOC0BznBgAC9DXwScdZ5EQEi5A7i7zupEeYHoCSSWhAmTwZ1bpXfP6V5VcjutCTPKL"
        "nCyQMBwBoDmiySslEshqBME1G4DMAvP6gc9YNklPg+kZQNZW8/qVGBcPSc8C6tFUtDZPuWa9aSezQIN5GycLrABHAGhO0rOA"
        "CgKxgmDKZoFoxo3+ACZ/IeVZkQDRove7K8Bgl5MFUh5HAGhOwrNAquO4ADTHEQCa4wjAFDAFTWH+GzU4Fe2Hpijb1b/EtBWA"
        "CvQrF/gCTaEL9BhGCM+hKcp2w/0Hb1UwgG5IjE2E8MYo6xZ/WbgPzWCn36v8JKbD84BgOq4zFgxBE5Sta8dxTe2HBTj1Xvym"
        "IuXqdRLYnFkbj3Lkj6vy/Exw9h2ay7A5zP21vg+ibUF5MX6vfMiDp2FD2PsPfAFRtfDYf78FqM4ZOol6cdI+CyUy/KlearzC"
        "9N1hf5E8Qnlus8FmWBj2Yi+9/qKvQzyLVC9iXYBLZhW8SA0bFsJC8J47+XWHvd4YrV1MAeZoLJQ5LjcO8aK7edYObrdzuxGp"
        "gMQwDe6mMV95Tx+DU3hxslPEldb/AQAA//8Ew2MGAAAABklEQVQDAKRKAe8gyCOCAAAAAElFTkSuQmCC"
    ),
    "twitch": (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAEpUlEQVR4nOybW2wUVRzGvzO7pGgtxWKVikaDL0RMA/FWNSZG"
        "2mK9E6kP3tBEA4m4hRZqIj4UoolEQ9vVqMREH4Q3USAhhC4l+GAMRoNCJBIfrFGgFZVlq6WX7R6//05X3O122cYzu7Mz/Jp2"
        "zpxzdqffd86c60wQebBlqa4KaDQy2ACNG3msgUI1j5fCHQxp4Del0c/j9zz2jAewv3Wf+vNCH1S5EjffpSvKyrGJolcrhSBK"
        "CK0Rp7iwCmJjaK+KTZUvqwEaWnU1YAUT36Dwq1Da9FNQeyiCbQqsGxlkNaCrUa9n1s1MVfACLFH+XdsSUd2ZSWkCO6Ct2Y34"
        "iJHPwIPQha5oD9o6oBKpOOu/GarqEfKqeIHa1ojGjDib8BJdnwhgn8owxWuwFiQsjaZQRPXIedKAcJMuS8TxswcavLygCacq"
        "RzH/uYNqOFnaiXGs9Yt4gaVeMxjEKglbExEt8BkJC+1yVOF6XactfAkfwr7gDiuh8Ch8imgPsvrXwaeIdksrzIVPEe0W+wTf"
        "GiDag+z+KuEAFVcCj70FzHKoc/1uF/D5u/hfiHZHprgivrkTuOwKuB7jBlReDSxnyZfPsc+jJ4HjB2CMRY8AZRUwhlEDZrE1"
        "ad7CZaLLz8dFTwCHPoYxFtxr1gBjE5/Z1wCPd6WLLwWM1AARLyV/iSPNqbMYMaD2oRzi9dSfE+PKq4Cxc1zR/DE9rYZLrxb/"
        "u9gAMDiQ5WtzfO90MGKAyrVwliPt5mZg4X3AHz8B21elpz28yb7XD23L3oYoQ4t1nl78yIeLBsDnXDQAPsf3BhR1v6+30/7N"
        "xtblKAhGDMg5KDE0YEkRmJHHNadBUQdC02VJK6fY1XlccxqUzJb3PS9x1LjUDg9FgSO7YYSSMOBuDpNrH7TDIn5HG3DmVxjB"
        "9Qbc/jSweJkdNi1ecLwRnMkJzbzayfEDx4H4yPnzOdcxb8aM8tpFwG1P2uFzZyl+nVnxguON4NwFXBx9c3L89pWcBfbZ4fl3"
        "Ave/ykFJIPt3DMeAT1rNixeKfgvkI37HemfEC0U1IF/xqZriBEUz4AaKr1sxdfrwoPPihaLNBXKKj9kNntPiBdd1gyN/F6bk"
        "U7jKABH/aXvhxAuqu1Ebnq6kc/2tXOB8LT1uPA7s2Qj0fYWiU/AaIOJ3s+X/5TBcQUEbwfiou8QLBasBIn7XBuDEEbiKghjg"
        "VvGC4waMDgM7XwFOHoUrEQNkTlYGh3CrcIH931l5RqgffkWh39LwrwHyio10g9/Cp7Dwf7BUAjvhU0S7deYUDkhjAP8RmxlF"
        "r9VxTLGXxvvwGSz091Z+o8aSQ+GRIbzOw2n4h9MzxpCcoiUNePkLNcgW8Sl5nQQeRzSqcTzx4kH1l5z/OxmaeIdmAzyOlUBb"
        "qFftT51PWtDubtAfMPZ5eBDe91vXRFTa41iTpsMtEfUCM66beNnQG+jkz+pM8cKUWxqsCcvowNvc9JiHEoYa+ti+tbJgP8uW"
        "fsFN5s4G/SyrSYg5F6OU0DjMwnsn1KM+zJUt7132cJOu5nLWAzTjFp7eRGcX8gKueCCet+zvVHKMJX2U3djXgSD2hPaqvLr1"
        "fwAAAP//s5jkAwAAAAZJREFUAwAyS13TgECZEgAAAABJRU5ErkJggg=="
    ),
}


def update_downloader():
    """Keep yt-dlp current (sites change often). Runs at most once a day, in the background."""
    stamp = os.path.join(DATA_DIR, "ytdlp-updated")
    try:
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
            return
        cmd = ytdlp_cmd()
        if not cmd or not cmd[-1].startswith(DATA_DIR):
            return  # only self-update our own copy (not Homebrew's)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(time.time()))
        subprocess.Popen(cmd + ["-U"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, env=ENV, creationflags=NO_WINDOW)
    except Exception:
        pass


if not auto_update():
    update_downloader()
    main()
