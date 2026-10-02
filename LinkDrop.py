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
VERSION = "2.5.1"
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
    # yt-dlp runs on Python: without this its progress lines sit in a buffer until the download ends
    env["PYTHONUNBUFFERED"] = "1"
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
AUDIO_FORMATS = [("mp3", "MP3", "Small, compatible"),
                 ("wav", "WAV", "Lossless"),
                 ("aac", "AAC", "Efficient")]
AUDIO_QUALITIES = {"mp3": [("best", "Best"), ("320", "320 kbps"), ("128", "128 kbps")],
                   "aac": [("best", "Best"), ("256", "256 kbps"), ("128", "128 kbps")],
                   "wav": [("best", "Lossless")]}
AUDIO_KBPS = {("mp3", "best"): 245, ("mp3", "320"): 320, ("mp3", "128"): 128,
              ("aac", "best"): 256, ("aac", "256"): 256, ("aac", "128"): 128, ("wav", "best"): 1411}
VIDEO_RES = [(2160, "4K"), (1440, "1440p"), (1080, "1080p"), (720, "720p")]
VIDEO_CODECS = [("h264", "H.264", "MP4, compatible"),
                ("prores", "ProRes 422", "MOV, for editing")]
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
        # read the whole response: the song title sits at the top of long pages (Apple Music)
        song = song_from_text(self.platform, self._read(self.out_path)) if code == 0 else None
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
               "--progress",  # --print turns on quiet mode, which would hide the progress
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


# --------------------------------------------------------------- license ---
# Licensing API on the LinkDrop site (one key per computer, 24 h trial once per computer).
# The last good answer is stored locally, encrypted with a key derived from this computer, so LinkDrop
# keeps working offline for OFFLINE_GRACE_DAYS and the file is useless if copied to another PC.

# official domain first; the previous one keeps working as a fallback (e.g. while a certificate renews)
LICENSE_APIS = ["https://linkdrop.com.br/api/public", "https://linkdrop.gabrielxreis.com/api/public"]
LICENSE_API = LICENSE_APIS[0]
LICENSE_SITE = "https://linkdrop.com.br"
LICENSE_PATH = os.path.join(DATA_DIR, "license.dat")
OFFLINE_GRACE_DAYS = 7
PRICE_TEXT = "R$ 19,90 / year"
LOCKED_STATUSES = ("expired", "revoked", "invalid", "disabled", "not_found", "inactive", "no_trial")
# public half of the site's signing key: every "active" answer carries a token signed with the private half
LICENSE_N = int(
    "0xc990c78d2e1f022257cdbfd651c15d0096c405a97942145133637a01139191fd98b3431fb081fd3aa9d630570ba366a95e646f9078c01dba6cc4679b00187ddad697043c45bf39ec2669671fefbd43ff64b3eef13aeff8d473789fe0730f55088da1013c64a3f710bb8aa585b5c73ea96e17d2f7ad8c9fbd8285b54f3f8df07e0dce594d1b63ee9c72cad9e99ba14f333667cfc62b100ddb334537b3ec30dedafd8004d74f43963560444d57122fb2855d140019d1485763b0a7ae7cc1dc135d87102adfd549cb44ed59ffa8bb202c71dab6359025d1746f1ecd80e650fbb9d28a7ebb288ad3914d20bb56289713ec2c5f777133b3729e0972307cb18ae4a26b", 16)
LICENSE_E = 65537


def verify_license_token(token):
    """Payload of a server token (base64url JSON + "." + RSA PKCS#1 v1.5 SHA-256 signature), or None."""
    try:
        import hashlib

        def dec(x):
            return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
        body_b64, sig_b64 = token.split(".")
        body = dec(body_b64)
        size = (LICENSE_N.bit_length() + 7) // 8
        em = pow(int.from_bytes(dec(sig_b64), "big"), LICENSE_E, LICENSE_N).to_bytes(size, "big")
        info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(body).digest()
        if em != b"\x00\x01" + b"\xff" * (size - 3 - len(info)) + b"\x00" + info:
            return None
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


_MACHINE = []


def machine_id():
    """sha256 of the computer's hardware ID (never sent raw). Stable across network changes and reinstalls."""
    if _MACHINE:
        return _MACHINE[0]
    import hashlib
    raw = ""
    if IS_WIN:
        out = run(["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"], 10)
        m = re.search(r"MachineGuid\s+REG_SZ\s+(\S+)", out)
        raw = m.group(1) if m else ""
    else:
        out = run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], 10)
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
        raw = m.group(1) if m else ""
    if not raw:  # last resort: hardware MAC of the first interface
        import uuid
        raw = "%012x" % uuid.getnode()
    _MACHINE.append(hashlib.sha256(("linkdrop:" + raw.strip().upper()).encode()).hexdigest())
    return _MACHINE[0]


def machine_name():
    if IS_WIN:
        return os.environ.get("COMPUTERNAME", "Windows PC")
    return run(["scutil", "--get", "ComputerName"], 5).strip() or "Mac"


def _license_cipher(data, salt):
    """XOR with a SHA-256 keystream bound to this computer (keeps the key unreadable and per-machine)."""
    import hashlib
    seed = ("linkdrop-license:" + machine_id()).encode() + salt
    out = bytearray()
    block = 0
    while len(out) < len(data):
        out.extend(hashlib.sha256(seed + block.to_bytes(4, "big")).digest())
        block += 1
    return bytes(a ^ b for a, b in zip(data, out))


def _license_mac(blob):
    import hmac, hashlib
    return hmac.new(("linkdrop-mac:" + machine_id()).encode(), blob, hashlib.sha256).digest()


def load_license():
    try:
        with open(LICENSE_PATH, "rb") as f:
            raw = base64.b64decode(f.read())
        salt, mac, blob = raw[:16], raw[16:48], raw[48:]
        import hmac
        if not hmac.compare_digest(mac, _license_mac(salt + blob)):
            return {}
        return json.loads(_license_cipher(blob, salt).decode("utf-8"))
    except Exception:
        return {}


def save_license(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        salt = os.urandom(16)
        blob = _license_cipher(json.dumps(data).encode("utf-8"), salt)
        with open(LICENSE_PATH, "wb") as f:
            f.write(base64.b64encode(salt + _license_mac(salt + blob) + blob))
    except Exception:
        pass


def clear_license():
    try:
        os.remove(LICENSE_PATH)
    except Exception:
        pass


def parse_time(value):
    """ISO 8601 (as the API sends it) -> epoch seconds; 0 when missing or unreadable."""
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime
        v = str(value).strip().replace("Z", "+00:00")
        if "." in v:  # trim fractions Python's fromisoformat may reject
            head, _, tail = v.partition(".")
            frac = re.match(r"\d*", tail).group(0)
            v = head + "." + (frac[:6].ljust(6, "0")) + tail[len(frac):]
        return datetime.fromisoformat(v).timestamp()
    except Exception:
        return 0


def mask_key(key):
    parts = (key or "").split("-")
    if len(parts) < 3:
        return "\u2022\u2022\u2022\u2022"
    return "-".join([parts[0]] + ["\u2022\u2022\u2022\u2022"] * (len(parts) - 2) + [parts[-1]])


def license_state(data=None):
    """('ok', data) when the saved license is active and was confirmed recently; otherwise (reason, data).
    Reasons: none | expired | stale (offline longer than the grace period) | locked."""
    data = load_license() if data is None else data
    if not data.get("mode"):
        return "none", data
    now = time.time()
    if data.get("status") in LOCKED_STATUSES:
        return "locked", data
    if data.get("expires") and now >= data["expires"]:
        return "expired", data
    if now - data.get("checked", 0) > OFFLINE_GRACE_DAYS * 86400:
        return "stale", data
    return "ok", data


class ApiCall(Proc):
    """POSTs JSON to the licensing API with curl, without blocking Resolve.
    result: the decoded JSON (dict) or None when the server couldn't be reached."""

    def __init__(self, name, body):
        Proc.__init__(self)
        self.name, self.body = name, body
        self.result = None
        self.http = 0
        self.bases = [LICENSE_API] + [b for b in LICENSE_APIS if b != LICENSE_API]

    def start(self):
        path = self.out_path + ".req"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.body, f)
        self.req = path
        # -L follows the site's redirects between its domains (307 keeps the POST and its body)
        self._spawn(["curl", "-sS", "-L", "--max-redirs", "3", "--proto-redir", "=https", "--max-time", "35",
                     "-X", "POST", "-H", "Content-Type: application/json",
                     "-H", "Accept: application/json", "--data-binary", "@" + path, "-w", "\n%{http_code}",
                     "%s/%s" % (self.bases[0], self.name)], self._done, merge=False)

    def _done(self, code):
        text = self._read(self.out_path).rstrip()
        body, _, http = text.rpartition("\n")
        try:
            self.http = int(http)
        except ValueError:
            body, self.http = text, 0
        try:
            res = json.loads(body)
            self.result = res if isinstance(res, dict) else None
        except Exception:
            self.result = None
        if self.result is None and len(self.bases) > 1:
            self.bases.pop(0)  # this address didn't answer: try the next one
            try:
                os.remove(self.req)
            except Exception:
                pass
            return self.start()
        if self.result is None:
            self.error = "Couldn't reach the LinkDrop server. Check your internet connection."
        try:
            os.remove(self.req)
        except Exception:
            pass
        self.done = True
        self._cleanup()


def license_body(key=None):
    body = {"machine_id": machine_id(), "machine_name": machine_name(), "app_version": VERSION,
            "os": "Windows" if IS_WIN else "macOS"}
    if key is not None:
        body["key"] = key
    return body


def api_message(res, fallback):
    """The text to show for a refusal: the server's own message when it sends one."""
    res = res or {}
    for k in ("message", "error", "detail", "reason"):
        if isinstance(res.get(k), str) and res[k].strip():
            return res[k].strip()
    status = res.get("status")
    return {
        "expired": "Your license has expired. Renew it on the LinkDrop site.",
        "revoked": "This computer was removed from your license. Activate it again or get a new key on the site.",
        "invalid": "This key isn't valid. Check it and try again.",
        "not_found": "This key isn't valid. Check it and try again.",
        "in_use": "This key is already active on another computer. Remove that computer on the site first.",
        "trial_used": "This computer already used its free trial. Get a license on the LinkDrop site.",
    }.get(status, fallback)


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
        "border: 1px solid rgba(128,188,255,0.14);")
SURF_HOVER = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(20,34,66,0.95), stop:1 rgba(13,22,46,0.95));"
              "border: 1px solid rgba(138,195,255,0.26);")
SURF_LIT = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(13,26,64,0.95), stop:0.6 rgba(11,44,177,0.30), "
            "stop:1 rgba(25,81,252,0.55));"
            "border: 1px solid rgba(145,202,255,0.68);")
PRIMARY_BG = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0E2A9C, stop:0.5 %s, stop:1 %s)" % (B2, B3))
PRIMARY_HOVER = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1335B8, stop:0.5 #2A62FF, stop:1 %s)" % B4)
PRIMARY_PRESS = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:0.6 %s, stop:1 %s)" % (NAVY, B1, B2))


# Component frames are pre-rendered @2x images (rounded corners, luminous hairline, inner bottom light,
# soft outer glow) drawn as 9-slice border-images: Qt's own rounded borders are drawn unevenly.
FRAME_SLICE = {"card": 16, "btn": 14, "tile": 21, "field": 15, "rowl": 16, "rowr": 16}


def frame(name):
    kind = name.split("_")[0]
    sl = FRAME_SLICE[kind]
    path = (icon_path("fr_" + name) or "").replace("\\", "/")
    return ("border-image: url(%s) %d %d %d %d stretch stretch; border-width: %dpx; background: transparent;"
            % (path, sl * 2, sl * 2, sl * 2, sl * 2, sl))


def _btn(frame_name, pad="0px 8px", size=13, weight=500, extra=""):
    return ("QPushButton { " + FONT + "font-size: %dpx; font-weight: %d; padding: %s; color: %s; %s %s }"
            % (size, weight, pad, TEXT, frame(frame_name), extra))


CSS = {}


def build_css():
    """Built once the frame images are on disk (they live in the ICONS table at the end of the file)."""
    states = lambda base, hover, press=None, checked=None, dis=None: (
        "QPushButton:hover { %s }" % frame(hover)
        + ("QPushButton:pressed { %s }" % frame(press) if press else "")
        + ("QPushButton:checked { %s color: #FFFFFF; }" % frame(checked) if checked else "")
        + ("QPushButton:disabled { %s color: rgba(245,247,255,0.32); }" % frame(dis) if dis else
           "QPushButton:disabled { color: rgba(245,247,255,0.30); }"))
    CSS.update({
        "h1": FONT + "font-size: 23px; font-weight: 700; color: %s; background: transparent;" % TEXT,
        "sub": FONT + "font-size: 13px; color: %s; background: transparent;" % SECONDARY,
        "label": FONT + "font-size: 13px; font-weight: 600; color: %s; background: transparent;" % TEXT,
        "caption": FONT + "font-size: 11px; color: %s; background: transparent;" % SECONDARY,
        "box": ("QTextEdit, QLineEdit { " + FONT + "font-size: 14px; padding: 0px 2px; color: %s; "
                "selection-background-color: %s; " + frame("field_idle") + " }"
                "QTextEdit:focus, QLineEdit:focus { " + frame("field_focus") + " }") % (TEXT, B2),
        "field_left": ("QLineEdit { " + FONT + "font-size: 13px; padding: 0px 2px; color: %s; " + frame("rowl_idle") + " }")
                      % TEXT,
        "primary": _btn("btn_pri", weight=600) + states("btn_pri", "btn_pri_h", "btn_pri_p", dis="btn_dis"),
        "secondary": _btn("btn_idle") + states("btn_idle", "btn_hover", "btn_lit"),
        "pill": _btn("btn_idle", size=14) + states("btn_idle", "btn_hover", "btn_lit", "btn_lit", "btn_dis"),
        "iconbtn": _btn("btn_idle", pad="0px 2px") + states("btn_idle", "btn_hover", "btn_lit"),
        "tile": _btn("tile_idle", pad="0px 4px", extra="text-align: left;") + states("tile_idle", "tile_hover",
                                                                                     checked="tile_lit"),
        "row_l": _btn("rowl_idle", pad="0px 4px", extra="text-align: left;") + states("rowl_idle", "rowl_hover"),
        "row_r": _btn("rowr_idle", pad="0px 4px") + states("rowr_idle", "rowr_hover"),
        "combo": ("QComboBox { " + FONT + "font-size: 13px; padding: 0px 4px; color: %s; min-width: 150px; "
                  + frame("btn_idle") + " } QComboBox:hover { " + frame("btn_hover") + " }"
                  "QComboBox QAbstractItemView { background: #0D1426; color: %s; border: 1px solid rgba(105,185,255,0.25);"
                  "selection-background-color: %s; }") % (TEXT, TEXT, B1),
        "check": check_css(),
        "pager": ("QPushButton { " + FONT + "font-size: 14px; color: %s; background: transparent; border: none;"
                  "padding: 2px 8px; } QPushButton:hover { color: %s; } QPushButton:disabled { color: rgba(245,247,255,0.2); }"
                  % (TEXT, B4)),
        "link": ("QPushButton { " + FONT + "font-size: 12px; color: %s; background: transparent; border: none; padding: 0px; text-align: left; }"
                 "QPushButton:hover { color: %s; }") % (SECONDARY, TEXT),
        "insta": ("QPushButton { " + FONT + "font-size: 13px; font-weight: 600; color: %s; background: transparent;"
                  "border: none; padding: 0px; } QPushButton:hover { color: #FFFFFF; }") % B4,
        "sep": "background: rgba(150,190,255,0.10); min-height: 1px; max-height: 1px;",
        "trial": ("QPushButton { " + FONT + "font-size: 12px; font-weight: 600; color: %s; background: transparent;"
                  "border: none; padding: 0px; text-align: right; } QPushButton:hover { color: #FFFFFF; }") % B4,
        "trial_low": ("QPushButton { " + FONT + "font-size: 12px; font-weight: 600; color: %s; background: transparent;"
                      "border: none; padding: 0px; text-align: right; } QPushButton:hover { color: #FFFFFF; }") % ERROR,
        "msg_err": FONT + "font-size: 13px; color: %s; background: transparent;" % ERROR,
        "msg_ok": FONT + "font-size: 13px; color: %s; background: transparent;" % SUCCESS,
    })
    for alias in ("seg_l", "seg_r", "seg_m", "seg_one"):
        CSS[alias] = CSS["pill"]


def card_css(state="idle", glow=None):
    """Card surface from the pre-rendered frames. active: blue light (glow 0..1 makes it breathe);
    ok: emerald edge (glow 0..1 makes a just-finished card flash)."""
    base = FONT + "padding: 0px; color: %s; " % TEXT
    if state == "active":
        g = 0.5 if glow is None else min(max(glow, 0.0), 1.0)
        return base + frame("card_lit%d" % int(round(g * 5)))
    if state == "ok":
        return base + frame("card_ok%d" % int(round(min(max(glow or 0.0, 0.0), 1.0) * 4)))
    if state == "error":
        return base + frame("card_err")
    if state == "panel":
        return base + frame("card_panel")
    if state == "danger":
        return base + frame("card_danger")
    return base + frame("card_idle")


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
P_LICENSE = 10   # pages 8 and 9 are the bigger link-box layouts
PAGE_DOT = {P_LINKS: 0, P_ANALYZE: 0, P_SETTINGS: 0, P_FORMAT: 1, P_REVIEW: 2, P_RUN: 3, P_ERROR: 3, P_DONE: 3}
LINK_SIZES = [76, 140, 220]
LINK_WEIGHTS = [(3.0, 1.0, 3.0), (1.4, 1.6, 1.4), (0.6, 2.4, 0.6)]   # (space above, box, space below)
LINK_PAGE = [0, 8, 9]   # Pages index of each link-box size   # the link box steps through these heights as links are added
RUN_SLOTS = 3
# one stable window size for every step (resizing per step fought Qt's minimum sizes);
# only the background mini mode is smaller
W, H, W_MINI, H_MINI = 580, 720, 400, 150
SLOTS = 4
TARGETS = ["At the playhead", "At the end of the timeline", "In a new timeline", "Media Pool only"]

# Keyboard shortcut that opens LinkDrop (macOS): Resolve has no shortcuts for scripts, but macOS App Shortcuts
# (NSUserKeyEquivalents) work on any menu item, including Workspace > Scripts > LinkDrop. Read at Resolve launch.
# "^+" fires on Ctrl + Shift + = (the + key); ^ Ctrl, $ Shift, ~ Option, @ Cmd.
SHORTCUTS = [("Off", ""), ("Ctrl + Shift + +", "^+"), ("Ctrl + Shift + L", "^$l"), ("Ctrl + Option + L", "^~l"),
             ("Cmd + Shift + L", "@$l"), ("Ctrl + Shift + D", "^$d")]
RESOLVE_DOMAIN = "com.blackmagic-design.DaVinciResolve"


def apply_shortcut(code):
    """Sets (or removes, for "") the LinkDrop menu shortcut in Resolve's macOS App Shortcuts; keeps any others."""
    if IS_WIN:
        return False
    try:
        import plistlib
        raw = subprocess.run(["defaults", "export", RESOLVE_DOMAIN, "-"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=10).stdout
        current = dict((plistlib.loads(raw) if raw else {}).get("NSUserKeyEquivalents") or {})
        if code:
            current["LinkDrop"] = code
        else:
            current.pop("LinkDrop", None)
        if current:
            args = ["defaults", "write", RESOLVE_DOMAIN, "NSUserKeyEquivalents", "-dict"]
            for k, v in current.items():
                args += [k, v]
        else:
            args = ["defaults", "delete", RESOLVE_DOMAIN, "NSUserKeyEquivalents"]
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return True
    except Exception:
        return False


def check_css():
    on, off = icon_path("chk_on"), icon_path("chk_off")
    return ("QCheckBox { background: transparent; spacing: 0px; } QCheckBox::indicator { width: 22px; height: 22px; }"
            "QCheckBox::indicator:unchecked { image: url(%s); } QCheckBox::indicator:checked { image: url(%s); }"
            % ((off or "").replace("\\", "/"), (on or "").replace("\\", "/")))


def main():
    settings = load_settings()
    build_css()
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

    def card(id_, weight=0, height=64):
        return ui.Label({"ID": id_, "Text": "", "WordWrap": True, "StyleSheet": card_css(), "Weight": weight,
                         "MinimumSize": [0, height], "MaximumSize": [16777215, height]})

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
            ui.Label({"ID": "Detected%d" % i, "Text": "", "Alignment": {"AlignHCenter": True, "AlignVCenter": True},
                      "Weight": 0, "MinimumSize": [0, 30]}),
            ui.Label({"ID": "Count%d" % i, "Text": "", "Alignment": {"AlignHCenter": True}, "StyleSheet": CSS["caption"],
                      "Weight": 0, "MinimumSize": [0, 20]}),
            ui.VGap(0, bottom),
            nav(icon_btn("Paste%d" % i, "i_paste", "Paste from clipboard"),
                icon_btn("OpenSettings%d" % i, "i_gear", "Settings"),
                ui.HGap(0, 1), btn("Next0_%d" % i, "Continue  \u2192", "primary")),
        ])

    link_pages = [links_layout(i) for i in range(len(LINK_SIZES))]

    page_license = ui.VGroup({"Spacing": 12}, [
        ui.VGap(0, 1),
        h1("H9", "Activate LinkDrop"),
        sub("S9", "Enter your license key to unlock LinkDrop."),
        ui.VGap(6, 0),
        ui.LineEdit({"ID": "LicKey", "PlaceholderText": "LD-XXXX-XXXX-XXXX", "StyleSheet": CSS["box"], "Weight": 0}),
        ui.Label({"Text": "No key yet? Get a free 24-hour trial key or a license (%s) on the site." % PRICE_TEXT,
                  "WordWrap": True, "StyleSheet": CSS["caption"], "Weight": 0, "MinimumSize": [0, 18]}),
        ui.Label({"ID": "LicMsg", "Text": "", "WordWrap": True, "StyleSheet": CSS["caption"], "Weight": 0,
                  "MinimumSize": [0, 40]}),
        ui.VGap(0, 1),
        nav(btn("StartTrial", "Get a free trial key"), btn("GetKey", "  Buy a license", icon="i_next"),
            ui.HGap(0, 1), btn("Activate", "Activate  \u2192", "primary")),
    ])

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

    page_settings = ui.VGroup({"Spacing": 6}, [
        h1("H4", "Settings"),
        sub("S4", "Saved for next time."),
        ui.HGroup({"Weight": 0, "Spacing": 0}, [
            ui.LineEdit({"ID": "SavePath", "ReadOnly": True, "StyleSheet": CSS["field_left"], "Weight": 1}),
            ui.Button({"ID": "Browse2", "Text": "Browse", "StyleSheet": CSS["row_r"].replace("6px 12px", "9px 18px"),
                       "Weight": 0})]),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Button({"ID": "NameOrig", "Text": "Original title", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1}),
            ui.Button({"ID": "NameCustom", "Text": "Custom", "Checkable": True, "StyleSheet": CSS["pill"], "Weight": 1})]),
        ui.Stack({"ID": "NameStack", "Weight": 0}, [
            ui.Label({"Text": "Files keep the title from the source.", "StyleSheet": CSS["caption"], "Weight": 0}),
            ui.LineEdit({"ID": "CustomName", "PlaceholderText": "Name (files become Name 01, 02...)",
                         "StyleSheet": CSS["box"], "Weight": 0})]),
        toggle("Imp", "Import into current Resolve bin", "i_film"),
        toggle("Sub", "Create subfolders", "i_tree"),
        ui.LineEdit({"ID": "SubName", "PlaceholderText": "Subfolder name (empty: one per source)",
                     "StyleSheet": CSS["box"], "Weight": 0}),
        toggle("Rev", "Reveal in Finder when finished" if not IS_WIN else "Show in Explorer when finished", "i_finder"),
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.Label({"Text": "Place in timeline", "StyleSheet": CSS["label"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.ComboBox({"ID": "Place", "StyleSheet": CSS["combo"], "Weight": 0, "ToolTip": "Where the clip goes"})]),
        ui.Label({"ID": "TargetInfo", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0, "MinimumSize": [0, 22]}),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Label({"ID": "LicInfo", "Text": "", "StyleSheet": CSS["caption"], "Weight": 1}),
            ui.Button({"ID": "Deactivate", "Text": "Deactivate this computer", "Flat": True, "StyleSheet": CSS["link"],
                       "Weight": 0, "ToolTip": "Frees your key so you can use it on another computer"})]),
        ui.VGap(0, 1),
        nav(ui.Label({"Text": "Shortcut", "StyleSheet": CSS["label"], "Weight": 0}),
            ui.ComboBox({"ID": "Shortcut", "StyleSheet": CSS["combo"], "Weight": 0,
                         "ToolTip": "Keyboard shortcut that opens LinkDrop in DaVinci Resolve"}),
            ui.HGap(0, 1), btn("SettingsDone", "Done  \u2713", "primary")),
    ])

    page_run = ui.VGroup({"Spacing": 10}, [
        h1("H5", "Downloading"),
        sub("S5", "Your media is being prepared for DaVinci Resolve."),
        ui.Label({"ID": "Overall", "Text": "", "StyleSheet": card_css("panel"), "Weight": 0, "MinimumSize": [0, 112]}),
    ] + [card("D%d" % i, height=86) for i in range(RUN_SLOTS)] + [
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
        ui.HGap(18, 0),
        ui.VGroup({"Spacing": 0}, [ui.VGap(2, 0), ui.VGroup({"Spacing": 6}, [
        ui.HGroup({"Weight": 0, "Spacing": 10}, [
            ui.HGroup({"Weight": 0, "MinimumSize": [170, 16], "MaximumSize": [170, 16]}, [ui.HGap(0, 1)]),
            ui.HGap(0, 1)] +
            [ui.Label({"ID": "Dot%d" % i, "Text": "", "StyleSheet": dot_css(1 if i == 0 else 0), "Weight": 0})
             for i in range(4)] + [
            ui.HGap(0, 1),
            ui.Button({"ID": "TrialBtn", "Text": "", "Flat": True, "StyleSheet": CSS["trial"], "Weight": 0,
                       "MinimumSize": [170, 16], "MaximumSize": [170, 16],
                       "ToolTip": "Free trial. Click to get a license."})]),
        ui.Stack({"ID": "Pages", "Weight": 1}, [link_pages[0], page_analyze, page_format, page_review, page_settings,
                                                page_run, page_done, page_error] + link_pages[1:] + [page_license]),
        ui.Label({"ID": "Sep", "Text": "", "StyleSheet": CSS["sep"], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 0}, [
            ui.HGroup({"Weight": 0, "Spacing": 0, "MinimumSize": [190, 26], "MaximumSize": [190, 40]}, [
                ui.Button({"ID": "Browse", "Text": "", "Flat": True, "StyleSheet": CSS["link"], "Weight": 0,
                           "Icon": ui.Icon({"File": icon_path("i_folder")}), "IconSize": [22, 22],
                           "MinimumSize": [0, 26], "ToolTip": "Choose where downloads are saved (remembered)"}),
                ui.HGap(0, 1)]),
            ui.HGap(0, 1),
            ui.Button({"ID": "Insta", "Text": "@gabrielxreis_", "Flat": True, "StyleSheet": CSS["insta"],
                       "ToolTip": "Follow on Instagram: " + INSTAGRAM_URL, "Weight": 0, "MinimumSize": [0, 26]}),
            ui.HGap(0, 1),
            ui.Label({"ID": "Ver", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0,
                      "Alignment": {"AlignRight": True, "AlignVCenter": True},
                      "MinimumSize": [190, 26], "MaximumSize": [190, 40]})]),
    ]), ui.VGap(4, 0)]),
        ui.HGap(18, 0),
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
    for t, _ in SHORTCUTS:
        itm["Shortcut"].AddItem(t)

    # --------------------------------------------------------------- state ---
    st = {
        "mode": int(S("mode", 1)), "audio_fmt": S("audio_fmt", "wav"), "audio_q": S("audio_q", "best"),
        "vcodec": S("vcodec", "h264"), "max_h": S("max_h", 1080), "keep_meta": bool(S("keep_meta", True)),
        "embed_art": bool(S("embed_art", True)), "custom": bool(S("custom", False)), "custom_name": S("custom_name", ""),
        "import": bool(S("import", True)), "subfolders": bool(S("subfolders", False)), "reveal": bool(S("reveal", False)),
        "subname": S("subname", ""), "return_page": P_LINKS,
        "lic": None, "lic_call": None, "lic_purpose": None, "lic_key": None, "clock_on": False, "lock_msg": None, "box": 0, "sync": False, "done_note": "",
        "target": int(S("target", 0)), "shortcut": int(S("shortcut", -1)), "folder": S("folder") or DEFAULT_FOLDER,
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
    clock = ui.Timer({"ID": "Clock", "Interval": 1000})

    def save_all():
        keys = ("mode", "audio_fmt", "audio_q", "vcodec", "max_h", "keep_meta", "embed_art", "custom", "custom_name",
                "import", "subfolders", "reveal", "target", "folder", "subname", "shortcut")
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

    # -------------------------------------------------------------- license ---
    def licensed():
        lic = st["lic"]
        return bool(lic) and lic.get("status") not in LOCKED_STATUSES and time.time() < (lic.get("expires") or 9e12)

    def fmt_left(sec):
        sec = max(0, int(sec))
        return "%d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)

    def refresh_trial():
        lic = st["lic"]
        trial = bool(lic) and lic.get("mode") == "trial"
        itm["TrialBtn"].Visible = trial
        if trial:
            left = (lic.get("expires") or 0) - time.time()
            itm["TrialBtn"].Text = "Trial  %s left" % fmt_left(left)
            itm["TrialBtn"].StyleSheet = CSS["trial_low" if left < 3600 else "trial"]
            if not st["clock_on"]:
                st["clock_on"] = True
                clock.Start()
        elif st["clock_on"]:
            st["clock_on"] = False
            clock.Stop()
        refresh_license_row()

    def refresh_license_row():
        lic = st["lic"] or {}
        if lic.get("mode") == "paid":
            lifetime = lic.get("plan") == "lifetime" or not lic.get("expires") or lic["expires"] - time.time() > 50 * 365 * 86400
            until = "" if lifetime else time.strftime("%d/%m/%Y", time.localtime(lic["expires"]))
            itm["LicInfo"].Text = "%s %s%s" % ("Lifetime license" if lifetime else "License", mask_key(lic.get("key")),
                                               ("  \u00b7  valid until " + until) if until else "")
            itm["Deactivate"].Enabled = True
        elif lic.get("mode") == "trial":
            itm["LicInfo"].Text = "Free trial on this computer"
            itm["Deactivate"].Enabled = False
        else:
            itm["LicInfo"].Text = ""
            itm["Deactivate"].Enabled = False

    def show_license(message="", error=True):
        itm["LicKey"].Text = ""
        itm["LicMsg"].Text = message
        itm["LicMsg"].StyleSheet = CSS["msg_err" if error and message else "caption"]
        for b in ("Activate", "StartTrial"):
            itm[b].Enabled = True
        go(P_LICENSE)

    def lock(message):
        """Back to the activation screen; waits for a running download to finish first."""
        data = load_license()
        data["status"] = "revoked" if data else "invalid"
        save_license(data)
        st["lic"] = None
        refresh_trial()
        if st["current"] is not None or analysis_busy():
            st["lock_msg"] = message
        else:
            show_license(message)

    def license_call(name, body, purpose, key=None):
        call = ApiCall(name, body)
        st["lic_call"], st["lic_purpose"], st["lic_key"] = call, purpose, key
        call.start()
        kick()

    def set_msg(text, kind="caption"):
        itm["LicMsg"].Text = text
        itm["LicMsg"].StyleSheet = CSS[kind]

    def on_activate(ev=None):
        key = re.sub(r"\s+", "", (itm["LicKey"].Text or "")).upper()
        if not re.match(r"^[A-Z0-9]{2,6}(-[A-Z0-9]{4}){2,4}$", key):
            set_msg("That doesn't look like a LinkDrop key (LD-XXXX-XXXX-XXXX).", "msg_err")
            return
        set_msg("Activating... this can take a few seconds.")
        itm["Activate"].Enabled = itm["StartTrial"].Enabled = False
        license_call("license-activate", license_body(key), "activate", key)

    def on_start_trial(ev=None):
        set_msg("Starting your free trial...")
        itm["Activate"].Enabled = itm["StartTrial"].Enabled = False
        license_call("license-start-trial", license_body(), "trial")

    def on_deactivate(ev=None):
        lic = st["lic"] or {}
        if lic.get("mode") != "paid" or st["current"] is not None:
            return
        itm["Deactivate"].Enabled = False
        itm["LicInfo"].Text = "Removing this computer..."
        license_call("license-deactivate", {"key": lic.get("key"), "machine_id": machine_id()}, "deactivate",
                     lic.get("key"))

    def accept(data):
        data["checked"] = time.time()
        save_license(data)
        st["lic"] = data
        refresh_trial()
        if st["page"] == P_LICENSE:
            set_msg("")
            go(P_LINKS)

    def tick_license():
        call = st["lic_call"]
        call.poll()
        if not call.done:
            return
        st["lic_call"] = None
        res, purpose, key = call.result, st["lic_purpose"], st["lic_key"]
        ok = bool(res) and res.get("ok") is True
        status = (res or {}).get("status")
        signed = None
        if ok and res.get("token"):
            signed = verify_license_token(res["token"])
            if not signed or signed.get("machine") != machine_id():
                ok, res = False, {"ok": False, "status": "error",
                                  "message": "The license server's answer couldn't be verified. Try again."}
            elif signed.get("expires") and not res.get("expires_at"):
                res["expires_at"] = signed["expires"]
        for b in ("Activate", "StartTrial"):
            itm[b].Enabled = True

        if purpose == "activate":
            if ok and status in (None, "active", "trial"):
                trial = (status == "trial" or key.startswith("LDT-") or (signed or {}).get("kind") == "trial"
                         or res.get("plan") == "trial")
                accept({"mode": "trial" if trial else "paid", "key": key, "status": status or "active",
                        "signed": bool(signed), "expires": parse_time(res.get("expires_at")), "plan": res.get("plan")})
            else:
                set_msg(api_message(res, call.error or "Activation failed. Try again."), "msg_err")
        elif purpose == "trial":
            ends = parse_time((res or {}).get("trial_expires_at") or (res or {}).get("expires_at"))
            if ok and ends > time.time():
                accept({"mode": "trial", "status": "trial", "expires": ends})
            else:
                set_msg(api_message(res, call.error or "Couldn't start the trial. Try again."), "msg_err")
        elif purpose == "deactivate":
            if ok:
                clear_license()
                st["lic"] = None
                refresh_trial()
                show_license("This computer was removed from your license. You can use the key on another computer.",
                             error=False)
            else:
                refresh_license_row()
                itm["LicInfo"].Text = api_message(res, call.error or "Couldn't remove this computer. Try again.")
        else:  # background / resume: confirm the saved license
            data = load_license()
            if ok and status in ("active", "trial"):
                data["status"] = status
                if res.get("expires_at"):
                    data["expires"] = parse_time(res["expires_at"])
                accept(data)
            elif res is not None and (status in LOCKED_STATUSES or res.get("ok") is False):
                lock(api_message(res, "This computer is no longer licensed."))
            elif purpose == "resume" and st["page"] == P_LICENSE:
                set_msg(call.error or "Couldn't check your license. Connect to the internet and try again.",
                        "msg_err")
            # offline during a background check: keep working (offline grace)

    # -------------------------------------------------------- 2. analyze ---
    def start_analysis(ev=None):
        urls = links_in_box()
        if not urls:
            return
        if not licensed():
            show_license("Activate LinkDrop to download.")
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
        itm["S4"].Text = "Saved for next time."
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
        itm["Shortcut"].CurrentIndex = max(0, st["shortcut"])
        itm["Shortcut"].Enabled = not IS_WIN
        if IS_WIN:
            itm["Shortcut"].ToolTip = "Keyboard shortcuts are available on Mac."
        if st["import"]:
            tgt = resolve_target()
            if tgt:
                itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">Resolve target: '
                                          '%s &gt; Media Pool &gt; %s</span>' % (img_tag("i_info", 16), B4,
                                                                                html.escape(tgt[0]), html.escape(tgt[1])))
                itm["TargetInfo"].StyleSheet = CSS["caption"]
            else:
                itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">No project is open in '
                                          'Resolve. Files will be saved but not imported.</span>'
                                          % (img_tag("i_warn", 16), ERROR))
                itm["TargetInfo"].StyleSheet = CSS["caption"]
        else:
            itm["TargetInfo"].Text = ('%s&nbsp;&nbsp;<span style="color:%s; font-size:13px;">Files are only saved to '
                                      'your folder.</span>' % (img_tag("i_info", 16), SECONDARY))
            itm["TargetInfo"].StyleSheet = CSS["caption"]
        refresh_folder()

    def refresh_folder():
        path = st["folder"]
        short = "~" + path[len(HOME):] if path.startswith(HOME) else path
        itm["Browse"].Text = ""
        itm["Browse"].ToolTip = "Saving to %s. Click to change (remembered)." % short

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
            '<td valign="bottom"><span style="font-size:34px; font-weight:700; color:%s;">%d</span>'
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
        if st.get("lock_msg"):
            msg, st["lock_msg"] = st["lock_msg"], None
            st["finished"] = True
            show(["BgRun", "CancelAll"], False)
            show_license(msg)
            return
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
        if st["lic_call"] is not None:
            tick_license()
        if st["clock_on"] and int(now) != st.get("clock_s"):
            st["clock_s"] = int(now)
            refresh_trial()
            lic = st["lic"]
            if lic and lic.get("expires") and now >= lic["expires"]:
                lock("Your free trial ended. Get a license on the LinkDrop site to keep using it."
                     if lic.get("mode") == "trial" else "Your license has expired. Renew it on the site.")
        apply_motion(dt)
        busy = analysis_busy() or st["current"] is not None or st["lic_call"] is not None
        if not busy and not motion_busy():
            timer.Stop()

    def on_close(ev):
        st["stopped"] = True
        for a in st["analyzers"]:
            a.cancel()
        if st["current"] and st["current"]["job"]:
            st["current"]["job"].cancel()
        timer.Stop()
        clock.Stop()
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

    def shortcut_changed(ev):
        i = itm["Shortcut"].CurrentIndex
        if IS_WIN or i < 0 or i == st["shortcut"]:
            return
        st["shortcut"] = i
        save_all()
        if apply_shortcut(SHORTCUTS[i][1]):
            itm["S4"].Text = ("Shortcut off after you restart DaVinci Resolve." if not SHORTCUTS[i][1]
                              else "Restart DaVinci Resolve once, then press %s." % SHORTCUTS[i][0])
    on.Shortcut.CurrentIndexChanged = shortcut_changed
    if st["shortcut"] < 0:
        # first run: Ctrl + Shift + + on the Mac (takes effect the next time Resolve starts)
        st["shortcut"] = 0 if IS_WIN else 1
        if not IS_WIN:
            apply_shortcut(SHORTCUTS[1][1])
        save_all()
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
    on.TrialBtn.Clicked = lambda ev: open_url(LICENSE_SITE)
    on.GetKey.Clicked = lambda ev: open_url(LICENSE_SITE + "/account")
    on.Activate.Clicked = on_activate
    on.StartTrial.Clicked = lambda ev: open_url(LICENSE_SITE + "/account")
    on.Deactivate.Clicked = on_deactivate
    on.LicKey.ReturnPressed = on_activate
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

    lic_state, lic_data = license_state()
    start_page = LINK_PAGE[st["box"]]
    if lic_state == "ok":
        st["lic"] = lic_data
        license_call("license-validate", license_body(lic_data.get("key", "")), "background", lic_data.get("key"))
    else:
        st["page"] = P_LICENSE
        start_page = P_LICENSE
        if lic_state == "stale":
            itm["LicMsg"].Text = "Checking your license..."
            license_call("license-validate", license_body(lic_data.get("key", "")), "resume", lic_data.get("key"))
        elif lic_state == "expired":
            itm["LicMsg"].Text = ("Your free trial ended. Get a license to keep using LinkDrop."
                                  if lic_data.get("mode") == "trial" else "Your license has expired. Renew it on the site.")
            itm["LicMsg"].StyleSheet = CSS["msg_err"]
        elif lic_state == "locked":
            itm["LicMsg"].Text = "This computer is no longer licensed. Enter a key to continue."
            itm["LicMsg"].StyleSheet = CSS["msg_err"]
    for i in range(4):
        springs["dot%d" % i].snap(1.0 if (i == 0 and start_page != P_LICENSE) else 0.0)
        itm["Dot%d" % i].StyleSheet = dot_css(springs["dot%d" % i].value)
    refresh_trial()

    win.WindowOpacity = 0.0
    itm["Pages"].CurrentIndex = start_page
    win.Show()
    lock_size(W, H)
    springs["opacity"].target = 1.0
    kick()
    disp.RunLoop()
    win.Hide()


# ----------------------------------------------------------------- icons ---
# Platform logos (from Simple Icons, CC0), embedded so updates carry them.

ICONS = {
    "fr_btn_dis": (
        "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"
    ),
    "fr_btn_hover": (
        "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"
        "s+B8VP/L+a0AAAAASUVORK5CYII="
    ),
    "fr_btn_idle": (
        "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"
    ),
    "fr_btn_lit": (
        "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"
        "AElFTkSuQmCC"
    ),
    "fr_btn_pri": (
        "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"
        "AwBs7k8YAGMV4wAAAABJRU5ErkJggg=="
    ),
    "fr_btn_pri_h": (
        "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"
    ),
    "fr_btn_pri_p": (
        "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"
    ),
    "fr_card_danger": (
        "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"
        "RmAlMJXhIjLEEOoJslcuP+fKvI8AjpjM2EwY5SV0xG5h4PlhKK+fSOjeS7tdYS/gV+KeF/zUtRdnv/PDN5bZ20BfjXGKjbli"
        "2GtUbs0uFU2O+mpTGkxfDiAxM2dCydWzU9vVMacrAPtM4juF/EbMxsnjPH2j2tqeV8SynpENTalavmJP4qAEJTPzcuiqxHCq"
        "hMH6zW76YLua8fr92dxevj7afjiapKWfM5L60ce2Xd0+zfk6TkNyl3dYleq021zflKhlX2+GeenzPlPL12jUeysn1N1A8mR5"
        "xeRkT0BWejyxi1THKuuVEaRdDEShQa3wKuMiNY/q0UPgg1y0YZzILeDPXH8xg2IW3FcBI3i7uGxOxyhTDDh1Y30/6u/OlQAH"
        "D0OG46VY4TSus/cO/KsB/uk33WLxyuWr88PZHh6vlnl6pKf3O1rnJ9OSNrLKBpeHGeYHH2jUMJ/Q4mVWiv9yZKkuF/P3SR1B"
        "lpNYlB/yvXgNyRxUBxiJ8/JBd/2NoTjvjPIzGIoT0vKTlV+XoyJ5+Zk9tFDjgNvjvE07bdwUVD6QcvNhCuTQTumPuqHaLoRm"
        "uR7KtCTJebiIOO+M8jPuL7UQym441U/XVQ9IQNmwKvrrRH+g6lep9FmtBxsFAa0jI6Gf6+G8kijtKiPH8wvY+IEZt4JCPR5Y"
        "9cGgdU7GFWRfkMCMUo8YOK1c6R1leHU+R2Lg01fX+LswP/AZm7NIsA9OQmbBkyuRgfeOojN8LeQeLeJd7ccWVsFf7w7uoVv+"
        "dmzslz517fnu6+++vcqdCcRgVdmuROOo8zMK3wbk9/yWXnaOdg1LBKDcK79jC2q1OjfTtlg9gQTi8zrQqCPQK+Viq0bD0+fl"
        "80K14AW8gMZnClJP4rLK4gcYjfMacOZxdBUstV1dvxopG+flg/IqT4zM198tzjvk01bMhN3jvNlc1XJeAYjt7EcLlBGGIebl"
        "77UKhMaEd01pp2Tacebr8j4N9lpgApY9jaKnuhxn8CKY8qJucsyIYes84rzCR35RPx20/MPXbmTAxI9/+zIcPkiYhHn8O01M"
        "eVDw6o9oCZ38YB8DxSGy8CpRbKTr1dzvzcI/jF38pZ/+yI3ZNyKAebmcT0B5fj7UZ981LYZ8vmePtFwLnVo9riuTIo3dGvgF"
        "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"
    ),
    "fr_card_err": (
        "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"
    ),
    "fr_card_idle": (
        "iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAYAAAAy2+FlAAAQAElEQVR4nOyda4gk13XHz6nqrul57K6kXWt3JWW9cjaGCGwJ"
        "nGgjLPIyGGIJYhJEkBREHoQkTggOBOdbPhtDHEgcJ18CgSSWweCAIm8wjsARUoQShGWUKH4IabWytZK8WmmnZ6a7q7vq+txH"
        "dfXU9r3d09NdvaP9/2Hn9L9+XbdqYM85t54TEQRBB1YRQRB0YIUEhqADLCQwBB1gIYEh6AALCQxBB1gNWqBOn/7FVn5o/V6O"
        "6awiOhUpPinxVmZ1kog3WL4jnhARD3rMSW2xotfFvU6sLsryCyqjZ6P29lPnz3+zSwsS05x164d/5bYGx5+UkT/GpD7KxC1V"
        "2SA8/PXjVTcnfko+PpFz719f+9Y3Xqc5am4JfOvdjxxdi/M/l93/HY6ihJT8GsyUDQakBqniPJVfjJXKBrJYCbG1S+mlI7UM"
        "Hv4gepWLjyKlOJIQyf/wmKLmKksuDL+vmDryvb+P0vxvvvfco5doDtp3Ar/vjgc2bjy88idyNP3HYjf0sixLiQZ9pfo9ydSc"
        "y02hNsNfX14pOXBsJkzxKseN2DHVJhV94XJ05QuXnn6sTfvQvhL4A3c/8sEozr4UcfRBXYnUICOVbucqS1mfHtOVJ8sG0oAz"
        "6ueZqVK5xDxXZeWqxKsqGzj4AeFx1JDuqxtxLJ+ZkiRhjtnwPM8paqwoaq5GcSOx6yn6Ti/r/car//2VV2hGzZzAP3n2oV+K"
        "o+ifZITDeWYSV6lBj1h2POtnKu33Sf/TOw5B16tkOk1Js2n+xc2YzVQ7ToiTNenITakH6u2c84de+q9Hn6EZNNNlpDP3PPS7"
        "Ulm+qpM36/co27mslD7Glb3b3Gyrza0t6vR6JnmVm1UUkwvlFpQeHPy9y7Msp67kwhXJiXZ7W0mzU6yksXXeVbk0POnDRyMV"
        "/dsH7vnNB2kG7bkDm+Tl6K/0ilnaVarXplwSt9PpUV86brHzowPDw8OXakg3Xl9dkZO5EfPKOkfJmuGSR3/60jNf+gfag/bU"
        "gc+cffjn5PTa50zydtuK+ltyrmogXXeL0jS1O6uUPVIoKhE8PPwuP5BG9+6VtlyhyaQBSldOtwyW89WfO3P3g/fQHjR1B5bk"
        "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"
        "Y2WVdx94K4KHh5+fT9bW5exzSz6pH21uZX9LEzQxgfXJLEXR5/UmDh89oa8uk60UbpMmwsPD79frqz2HbjgeGU/0l5df+vdN"
        "miCmafSRjzRPD048Ju33o2naoStvvlb8ObXdI8DDw8/sbzh+Kmqa7kvPnI8v3k/PPdenCZrYgY1koG6UPyJt/bVmskobR09E"
        "xdS9vPgMDw8/q9+46aRJ3pzUD9Idfnia5CWatgM7nb7rE3fJKk/Iv+bO5ttq5923zdbNGwTM7V/FTpGd29t9AwcHD/DVIzfx"
        "xpFjctaZOtynj7/yf49/m6bUdB3Y6fzz555XOX1Gb3vt8FE+dEw6sd4ndmfTXEXhorJwufPg4OBVzrQhOeSSV6755p/aS/Jq"
        "xbRHXXnz+986cvNPvSU79cuN5kqs79Lq7bRJ74F9lw8jIiJOiHHUoMPHb41arQ19w1UqSz/96rfPPUp71J4TuEjijZvP/GfE"
        "dH8cN1dXN45wLq15kPaIRneWPREc/DrluXTe9cM3yexVjnmbiV7+Rk7Rr114/vFzNIOY9qGTP/2J97dWon+W5nunHqjfT2l7"
        "81KebrepmOJX4+iGwcGvJ95cP0TrR45FjUZivJyweoFT9evnXzz3Bs2ofSWw0R13JLc3b/99xfxnMtiNemcHaZe6O22V7myr"
        "bNDz/rKIiO/1yI0Vaq2tc7J2iO0NGkbvyLT58xfSl/+OXnwxpX1o/wnsdOpD991IEX9GptW/JzYplueDlLqdLZVJd86ygcr7"
        "A8oGep/VcAdUZYfg4Q+cl+lxFCfUaDYkNjiW6XGyeoibjebw+0qfZab8i3nGf33hha+9Q3PQ3BK40JmzD98WRdGvSup+TEXR"
        "vXK6rcXFr+nOxtlT6iMeHPw9yuWYtyuXap6WLzzRiQZfHS3NVAAAAHRJREFUfe3pL79Oc9TcE3hUp0//Vqt5Ir1XOvNZUnxK"
        "Fp2U3+wW+b1ukStYG6jd8O8dT1uSvBdl+Q/l80VJ3guUZ8/230ieOn/+H7u0IC00gSEIWqz2dCMHBEHXlpDAEHSAhQSGoAMs"
        "JDAEHWAhgSHoAOvHAAAA//+sNXIrAAAABklEQVQDAOhQffIAN7iCAAAAAElFTkSuQmCC"
    ),
    "fr_card_lit0": (
        "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"
        "O99Y3P7RtxezxVz29uBVjjYzxaVNEG2MRhPJxVYq40UZY37v0/K9F8MB+cErLq48tusmdS2PP/+5Gir1BEe192fX/9wP7/7J"
        "r0Pb7VonLXCoHupt2MRLe3gkhZ0cDxkNqnO2e2cZwDd3vvWxm1s/+jK04Mfff/3H7Rvf/Aez2eEi7SE0pGoyr5RHMw2SPfQw"
        "CWVtw41Mh0IX+jzTsCoZXcq0gdgPYHAq0uTKpW03mTbu6T/6r0+uPfOpBjvfe/voU1965+Bzb0CfbRuo0weQxLSHj6nSGlpS"
        "0W1XFC0DfG6hqrNmWB0xTOQlXN568/LNrR//JhxU1/fuvNe99a1/dLh/cJTmR4tkD+XMdY6r9Gk04/DZIYnpkRv4kbQrdKHP"
        "P60CFyh2eP9TsnAwAeN9xgvGvf1Daq3urW/+46PJzhW/c/X69Se3X/lvb89v/gV3eGMfUZ8ELMa9qaTn4AN7hXnTd1beMxjQ"
        "Lzq1fb8NCYz/LrXi2y0JuGdFyUu79zOPffU3go//7uxwP772T//u/sH+QTw6muO+KamOgMeCszzppsY1aVESP2bF159R6EJf"
        "DDrDdY1v+2WJF+dCavs2BVeloztvtpef+mQTmvrm9em7R2+1z39jWkOvxokLfPy+pPszYc60yG+INgsggOm0Yu2uB8LDHJ/d"
        "iQQAN2DGqK5NXrp5bXL7r+Oek9e+8fcPDu++3+3tHUa7ubeH5gihm7VmfbjIG/LhuV+87nf54Ve0P0YXfuGfL77hwafoVuCF"
        "RDa8uBU+5m2bmhDS4nC/v/axTzXOdT9/0G797wu5MusgwIHJeNSJPPW4pPcfw1Vew+fbVKFfxJevmKkKvosAc5esMIEhpI9d"
        "evXP456X99+/1e7fenNx995+Dgw5eWB05shSw30YnY2STQGRMX18LPzCP1/8dRxkvVe5LtMr/Ny5t4+48Rtp7/Y77aXrN658"
        "6vK3/9zv3/7435hCM19EmNS7EpnOrJegv+pFqtA3IIEPxT0P0C5uit/dU+9z5SZSXd/6/hNX69v/De4eXv3a7+7ffe922/fq"
        "XovZ3ZzMzewpe3UGSva0KT91MlvdaDfaf9JY+IV/bvmSJa7ShheOS3xQUsNubdsuSjuL1z7+6QaC+gWJ7m/v718/6BaSLh1B"
        "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"
    ),
    "fr_card_lit1": (
        "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"
        "E3pKUiaxAAAAAElFTkSuQmCC"
    ),
    "fr_card_lit2": (
        "iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAYAAAAy2+FlAAAQAElEQVR4nOy9a4wlR5YediIiM++tfrLJ5mOazRkOhySomd2Z"
        "XWtnoacxsiDoj2VANhawLFgQYMiAbeiH/9iGYQNt2DDkJwzIlmEb/uEfFhaQLQkwYEHGWlo9VovV7mp2hsvZ2Zkm2Rxy+Go2"
        "+1FdVbfuzYzQ+b4TcW/eenY398Gqjpwpnjp5MvPmrY4vzjNONPJIR3J7Tji5pv99Q+z8TaWv5t9vG335vtKXRfpNcYc9td86"
        "XFaPenxejuaspENl51V2XeT6uXzNpUx/oPTp/PvXlF7jb3ue4w597mHHIwBmBN5r+X4At4BWATsGa39JXFRgDjt2bTy/+sw0"
        "q4Ctx8k/3HQFRL9pv4cNSV6B3tzWnzGoAegC5q/l+66NgfxwIH4IAK1p3ZXGHQH3xbPioUXjJQMswDrcFf/EWf19ngG8kWnm"
        "0yK/w3mpRz1OzrFpxLUGPt9lurPi72xJChclAtQENMCsoL6xJXEfkK/xcQ8N5AcE8BK8+4D78rsK2mfFzTvxAO3Qir/QiAdA"
        "+yA+TfXctvhz50RU7vm0Tt+uX//s2FdtXI+Tc/hm3fx1yrv5UvvG7V3lFdxupqAdFMQK6Hu9xLDQHwVzN5fYfCTp+gsK5kOB"
        "fDyIHwA0I/D+3H6NC+AuFLAXVdP2Cl6ANurPJEoAUIeZ8l78NIM2DXrvVJ867AdxPepxEg+CNyjoZvq7UvC7USIAHaZGd70M"
        "XoEMMDcK4ruqmVsFNIC8TyP/DQL4gUB8DIAyeOHrFq37tPiXnxOnM0wowJ1fkDAApIPRXsE7URqd/uyKb/X31OibLIzyyUOm"
        "zcgnHiqg6/H5PwjW8ntvv5dz4FXzxrlSryD2E/1JEgHoRkEclM6C0e6eDADyZCLDVMF8/UN9xk0F80obHwviQwCzJ1AF8F5S"
        "8zdr3R0AVbXtOQXq4qwEABegbfVnmOiP0gYgnsjkJ/6AvHLhrLzSNHLZOXkq+HQ5OXfZSTpjH5+k0kpPOk2StpXejEO8lZK/"
        "1ffxk3tb/oe/+Vvyw91d2e0BXvzsyrBQCjADyO2WDPf1967Rn7E2vq10DcQ49gP5aABfW4EXvu7snJrLU/Hnn5CgM0zoZtIs"
        "WmkA3L6RBqB97qpcfulq/ObGhvxU491P6lMmUo96PL7H7jCk7+7M5TfeuuF/9YP35RbA3PTSA8jtQvr5VHoAePOO8jOJ0/uq"
        "jeEb7wPxAwH4EPC+Jl5nknBegburoO12FbwKWn2BJgRpLm18+OSXn33z33Ju+AveS1fmp53Nu+mTd64PO3c+TYvtzbS7tZnm"
        "2/dSjAg/68yVkjjHGYxUT+iNoxmu8pX/vPGSx+to/IoPMj37hGvPnHPdmfNu49KT7vKXXg3Ts+dtWBNasrMYmv/9/U9e+us3"
        "t567NQwK4lZ69YX7+UT6iYJ5U4FMk/r7DwZi9zDgPbstjQatGp0tmiDS9nNpnty4c+Gl537rz3dN/5f0yedw++YnH8Vb7/z2"
        "cPvHN4bZvVv80l6fHEcfjXB0HP0uh9gC9lpVXuWfP/lyPCvl+C4CV8a3xmsvPCVPPP/l8LSC+dzlZ32+YrMfwv/25gdf/euf"
        "7jxxrxVZ9J2CWIGsZnS/dUbB/IAgHr3WnmjzIeDVD2sB3oX+fOWZt7783Ln3/wfVuK9iJtq6fSve+PYvLTbff3P5Xfq+l/mi"
        "T71ON+ofiJoTiuco+n9MWssJbqmJl/xeWuVV/vmUEz2K5qBqDdc0ihDvvEwnjfOabyr3X7z6Ff/Fn/pj3fknLzu7L33/vTsv"
        "/OV37rz4busUxDCrFcyHgviA6PReAK9SRRptvqoprd2n94NXp5H21ee/+7MXJ3f/qr7phZ17d+N73/3l+c23vz9gJur7QWbz"
        "3bQ709Ca5oqWbniZyfDl/Tov4yklHUCrvMo/x/IC5CWi8nkfGpmoyTqdTFzTWKj68ot/IFz9xh/uNs5dAAo++XTr0l9+88Ov"
        "/YbqtMWgP2sgvinDeyLDMjq9BPEagEem8y+q9lfwlmjz2Q1pNL3VFvC6Qbqvv/Rrf3bS7Pxnzvnw6Xtv9T/8pf93lhY6faiW"
        "3draTphKMCPZ1BNHU5VR5w+c4iqt9ETTlMe3rI1vIYXX3HadnN2YukbR5ENwr/zxf3ly6cqXGr1kPptv/Mev3/iDfycFmRcQ"
        "T/Vna0f6DY1WMzoNIO8xpZ0cZDpPxCPPe2YizU5U8Opn+146aN6f/OKv/tlpt/tf4IYPf/ibi3d+9e/NBgXs1g407jw/Kq5m"
        "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"
    ),
    "fr_card_lit3": (
        "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"
        "ylcWGqT+0vXbm/9Hv71YciXToCBuJPYLNZ0/Frm1KzYRyFOvhVdUmy+fFn9eAbwUNZtVA6u01emh++kvvv3veT/+uRsfvrd8"
        "+7df37p+4060mYZWAqmUmQhnXD6XqSsaWJydcyUqV2mlp5NSA2fNrClTo2vXuVV0WozX87KrMSXXb8cLz77Ubpx77OyT52/v"
        "/fj6U7+j1jaC0BEAPoflimpKP31NtfBN+MCvilMVzU4aSv3GhgQXkV6S8OQT159o2/4v4wPe+v/+31t37qiRnW18keLizvmV"
        "D5Dlxvt9vKt85R813mU+C9bl/BX/3rq9Fd/61q/f/pl/41ceb9v4K889fvXvfXT18geClX7qCw9bMo6jta6SDj0FrmMtsgIY"
        "wSuNcOM/nQ8Cfl565tqfU9HG1R9d2bl99YPlEvWQmlMqb5E4o/hcFGqh9HmVMzgD73R9/jKVr/wp5tNBcr8GXspdljPTZNf3"
        "CrHbH7+3d+3Hb+08+bmXN1986sYvfPDx5b+v9wb4pqVxxnBbQbal6EMjOvSyYg+rYJ00sAgfvvBGu/d1fM61t9/Y1WiZ4ttF"
        "BTaqmBGfQpg5WnjNI6AF1BpPuWS5VL7ylScuMj5gEk/4WV1f8HV7a3f4+O0/2IG5DQwCi8AksRlyv7lL1kSyQevX8QaeJA49"
        "rPRx1MIXN2+dU1X8L+Hjrr71xo59YDIFO+aQuUahJYfEp/LIvCyDtc4spxSed5lKPj/JK1/508YTH3m8hyL3ZpgGlxVyxotd"
        "5yb8oG3HEEWV5o780r92ERjsQt8l3y6pxhfWLJKtmRW7Dfo2o/XroIGssMNFFEjr+i88f+1nFKTtpx+8s7u1vdVzRiFYDZxx"
        "hPZ3xPB0HnGsLGeAHHQ0cPN8WX3k5zTL7zpf5VV+MuUA8QoHB9zPfLHLS2yZD3ZMyIaMG/31zq07w40P3tu7+Nzziz/1wrtf"
        "+/ZbX/gtKNa0Y33oiFnFboMezmpbO+hvvzBrHFp4sVi+Akv9kx+9ub27uxwtQCV0c2k12+yBW3XCSWbap5Wjbnx5WdPYks+v"
        "ySXLpcqr/HTIs05bG/++yIvizXIUc4wxOk2xKuh9Brtzy71+/OTdt7YvKYDPntn7sgLtm3gIPgoaGBTYRc20oOn6ndy3GYUj"
        "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"
        "CiuXjKYM0vI4NtLJ13PNsEpffE5eunjRP4+Lbtxwf+sff0t+K/YKXKSMRPrJ4MbozQAABsVJREFUdL6gPz+QeOXFlfYVWQFY"
        "qIW/tzKlb9+Q5vxF9YlVE7fOQKxBra4fpfvlX5S/eOa8/Id6Y7uzm66/+fbwRsImwkWBWksedCXIQPRWYz1KNqXzXyGy88Fa"
        "/tc5n7EvKzULGnKUej5zWkThnih7bN7H9ZWebFp6Vt3T9Tj2Yxo6spjX+TwXALgZ0AHEUnaZK7d8Xp2UZjlh+sghX4sGHonr"
        "f5uXPue/dOaMu4iOkLe35L97/TfldWwplvoMXtW8G7sybJ/dZzr/hL75a4a1lQGA4xv6aVf1nJrSL50Vj+KOs5vS7LXSdHv6"
        "VRS8fkM0QC3tT39FvvTC8+k/1cdcVBW/d/VDeevTG+k61k96bFkM+IqzDc+Q3Yo0Z/g/nydMppbGrEgJ+JBWTsUod82sWMJI"
        "m8jRyvZYPiGzAESlld4vRVVFHk8TzamjtfHnM1zo/Bb5ZHebpkUTSva4cOgixXULwhCO4zogl/H2xOP+ictPykvq+i50QF99"
        "9wP3X/2z78kPGyyT39UfBfFyKcNiQ4atHRlYtFFM58v6gb9WVJhL6wAupnQGMfzhfkP8HMTQxD3KuFUbv/yiPPXlV9Pf0Jf/"
        "Em7e3ZWtq9fkrRu30x0sNhpHW5mUXI4s45OSgZkpI25TmFM+Yn8LZ6Wsc6Vc/kh3/Todzv7ka7LDZtwqr/Iijwds2+kOZkoc"
        "SzXGtGiOVrPLqSVne/mCgrdtflGE7TjooZQunHWPPf2UvLTo5CyfleSNb3/P/Zc//kA+hdncYmMh/VkuFLy9gbfdlThFnQ28"
        "afVN3PwL7ANx8YfPiUdQ6/ygwa0ZiIdGsO1Ce2ZDNn/uZ+VfOXtW/h19wGN4wPZuunXrunx08066rR54j2/FKHO0Wgyr2GJi"
        "mLWXVoBunfM4M6aQVx2J8XF95kRm/BBPRspkcKi80keb+sPlqJZa09C+jLvcvdKZ3GowJI9bjtMMYkN8WX0EJaxBpPbCWX/+"
        "wkV5enPTER967a2dbflffutb8v9s78qODu++g8ms+d4C3tuNjCjY2Lhzt98rPNzchC7HAf7wPhCrcR4WCmRV8YxSw5Yf9efZ"
        "C/LY1/5M/Debzv1bzvYr59H3sn3zpny8u3S745j6fumW+ozeIl58A/46xZuKj7zvr2uqXA6fWY+bcevxaB73O14ytfSuHIBy"
        "O7+q3yCO2awd/2sXqdNYVruxSBsXH3NPKYDPzN5md2/p/vff/b3+/7x2s70VRvaLHxBl7joZ9hS4nZrLRfOugXfm9xbwlq+3"
        "7zgcxDCnEdhaKpC7XWl6BXIbJUAbh16a2El44eKHzzx3+epfWDT9LyrmflGfsCH1qMejemhqSPMy/3RvCP/k3U8v//oHnz77"
        "kV+qd9nK0OQKK/VJh6X6u51qXQ0ej/cKXnIHfOLq3AzE+iBXAlv64f6cArc/K2FDeWjjbkPPY0WV/qBJTmglnAnbGy89/+5X"
        "z3Q7Xws+Pqc2xdM6WT2j74Cw+TmpRz1Oz3FHlfH76hN+oNbzR2P0728vN7/99nsvfGd7PLPLJYEa4xoUsOhrMSh4sWR4VzVu"
        "uyXjHZzvJU4Bq0uS7gYvjmMBjOMAEOfA1isKP1Rr9Y34C2ozQBsPMKsBXE2HQSNjh19ku7BRIku90awAdKT5wU2dkvXAyQ20"
        "Z0c87J3qUY/PwOHXHTNXtiJCMeFIh5l0aJm55NYKfabYEgwaFxsj7SnFRiTQujdHiQtNE20MqnU/1GeUgNUx4OUZOfRYi9Ct"
        "otOvipub1NDGjymYB+yRrECFRgYdd8UvuDmEAhdF5HviO/S8HfNPU4FbjxN+zMDscttXdo/EZvcLBXDPwG3ci9YoA91uoHFB"
        "GwXtLQUsAIzOsJPJ/IY+865oMz/hwIjOPYBmX3QaRzapoY13PzQgx/PioJGH2wZmzWV5TT67cYf7PmGrRQdgw6NHz2hq4838"
        "CUMFbz1O3oE9wPjLjrBDD9pBbwvbbaF3DptEhk1rVeVHA21zXiI0LlrC0td9JmvdYjLjOCDafOg7yD0dh5jUqo3nQI6XCFgH"
        "rXzxLLWuI5iX+hYb+qMJpTN9/kz1gFP+PVYA1+MEHj4D2LVK79i5bf0dPFq/onskQIuNE9AGBz4udlOgxp0Dt2jdezCZ9x/3"
        "AZx9JvVr+u9+IN9RwD6tP1sK3gxmaOa0K+4JvXzMoC4PAbDnnxA3K5Dr8dk/yubaE9+teIAVTdfRt5ntmm9TCxO0aOHcfGS9"
        "rA4ALo77Ai+vkvs+DgEyjn1glldEM9IGaIgBalAAW+pRj1N2lH3GAFZQAva8sHvkXaDF8RDALcc/BwAA///9jNL6AAAABklE"
        "QVQDAF2uoLFOgvOIAAAAAElFTkSuQmCC"
    ),
    "fr_card_lit4": (
        "iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAYAAAAy2+FlAAAQAElEQVR4nOy9a6wl15UetvbeVeec27eb7G421SRFUqTUokav"
        "sRI58UxmkhknhpEAQQIDVoAEEzuvH/llIIP8CZAgTPzPhu2Bf/iXYRvwExgbNuyBXzO2BwPPjIwZj8fiUI9WS5REiuwmW923"
        "u+/jnFNVe3t939pVp87t++iHJOve3kVe7rvOqjqnzmV9e631rbXXruShj+TyL05e1/++KSZ/oONr+nNb3JVtHa+ItPfEtTvi"
        "4gU7p9uzMZ4Tt/aO83W5HOU4CYebSRrL/p7JYcNGf1tStak/51S+JnLtrI4X9Oeq/jybr/20jq/zt/xeLslDHA8BnLQ693W9"
        "bh9wr7wtHqCdXxcPwAKsACrAeX6hv2/qzzIDeGP1uf1r5SjHSTz8ZAViv5eBq6/5HUlbUwWNghzABqgB6NlzEgnml3S8H8gj"
        "8D4YkB8QPCOr+4URcJ8Vf+U5tbJqaZcT8QBtV4s/v8nXPIDa7YhPM3FnGr2jqYK4tesTxjP53dsC4nKcvMNVGXC7q9+9jm4h"
        "abdWwHYSCeY9WuG4paAOjUSAebKUWN2QdB+Qf5HjA1vjBwBOBm9vdUcWt71swG0q8U/fUVnB2wYFrv5seAX0XME7URDr7zMF"
        "KX4HWFOn7zHTd+5GYC5HOU7Y0YPWBR3nNuI1t9SoUMcQJeL3MJO4p78D0BV+FMR3npZYtxIPBPKaNT4axEcA54BYN1vdVzbF"
        "j4G7fEoCLO2GArfd0N8VuJOZhLhQMDvxtQI4VRm4Ucf8e9LfZSJSdwXA5ThZRxMywJYKEK9gAXhbfW30ewPQJv2ZaqQ4lw5A"
        "rvaUClIQh00F713peiCfmUp37bpe/4EiZAViHEcC+RDgHOAyX1B3Wa3u/KwCd6bWVa3t2ShBbzLMzkhodaz1p0v6M5FQ6e/J"
        "ZwA3NqZgAA76O98zrj6funKU44QcrhvFq95+12c/EsCqA3Cb2gDsFMitVwAv9cdJ1+jvlf7Md6VT49ZtQ6dWuZ5LnG1LpDW+"
        "rT84jnGpDwDNPvD2LvNUwasE1eJZCedaCctOgQsA11IBuG0lFUDbBanUdQhRgXxmU6Y//kn5xFNPyydCiJer4C9JSpd1hvqQ"
        "3tImPz3luyhjGU/yGGVHf1Vn2L3fpniza/2Nu3fka1/6inxtd0cWXoHbAaidtABz1eo4kRYWea7yJEh3r5Ju+oF0ILquLUYu"
        "9REgPgzA94F3d6GgVeDS6m5KmCykUhe6qgHYRvR+pA5eqlc/LJdevSJ/YGMqv9+F9HknbiqHHkmODsOLvuhPtj6JW6Qkv63W"
        "9revfVO++J1vy/e6SloAudMfNYCtutDtcqrjjlnjiQKZLvWBID4SwEeDV9+0mswVrBrnBgVsk6RW5Ko3IPXFS/cufvqV6z+n"
        "5veP6ZtM+nfcvXMrXb/6e9321gdxee9O2ruzlebbWyl2mlvSf5J+O+ecfVUd9QW9g9HUVuQi/6jJkp/X0fOrAaScOXfRTc89"
        "5aZPXXCbF571L7z2mbChv8sKzntNG/7S1bee/dvv3zr/PfWRG/Vc29pJ0+nvsMbLmbSKt/ZBQez2gXfFNiPmPQC8C6eg1R+A"
        "tp1Kff7s3rnPvfrufzetu/9NrzqLt9i6/k68fvV3u5tvfb3du31D7yAx6I2juYNy//voLspRjpNyHPY8A1U+/7Jx4bI889HX"
        "qhde+1x4+vKH+bJedm/ZhL/4e9de+Ns3tzbuKpiWsMZtkmaqPweCGDHxAex0hs5DgHdDamXYJgDwqx/54JVXL9/+03rlJ3D5"
        "vQ+ux6u/8U+Wt976Std/pbZtZdm0qe00kte77DTST0o/67/iw2qCK2MZT+pIBOnzHNQXhTVGXOmVmp5NKxc0MUyaWk+49Oon"
        "w8d/+r+cnLt02YCc5KvfeufSz7/13sV3YI01O7Ns9x4OxGMAD67zFXWRwTbPz0k1Bm+YKHA7mSgJNfnxK9/5/LPn9/6c3vFT"
        "u3dux2tf/JXF9a/+bgvnom07mS+XaTlvlJlrZRTo25j4nVavl7GMJ3wkonpZVq+HUMlkUstsNnFVQH7Jy/Of+Fz1sZ/8L6Zn"
        "nroA/vrme7fO/h9f/uYLb2hqdZkmstgP4tk9/QE7rS72flfa7Qcv8rwv6ueCbd7cVWZZgTtY3qVMAd6f+Oy3/uvN2eJ1jQHC"
        "+29dbb/0T/7GXlw0qY1KxW3vpEYBLPo7pijMPKupykbnv09TXxnL+CM0pvx8y+j5pqPrDNW1AnnzzIarKmV/1bR+9r/6uTPP"
        "fuRjlQaYy+3d+v/+V2++8stjECvZtZwspd05I61isf3WjprAPk/8i+axG4BfX3ed721JtbmhVhcpIiWq3EKBqxOJ3scU4D07"
        "W/xJXPrtN35rcfVX/8FeF5eyu7NM80WTgdpPSTHb+iS9l27fJR0ylfVH0Rf9ydSbS51BTUhrnAisuWCX6uvqWsuZzZmrfC2v"
        "/cH/duPlz/yHU1y/PZ/8P19845Vf0vdYqOO6TFNZNEpwbXhpFJPdufPS7nel9V1f94poc513Jexqikj94KAzRzXp1G1Wi6uJ"
        "6Yn3MvnMx2585uJTO39WTai/+hu/svfWF39lb9Es0527O7FlcMt5KGHesdGPRk1uO8exl+8fi77oT7ZeEENCdva69HonAy5i"
        "TGlvsUiwxFtvX22UHkrPvPSqGuX40xsbiy/evHXupnf0ttU2S1qqXWzU2QZpdvltkVt39DN29J2/DNi/nnxeWRRQIqnmuupd"
        "5/lUgatW1zUy/diLd56/8vKNv6onPvvdr35p/pV/9nd2F3uLdHd7F/eeDW8aTVIpz1VJshnuPYlylONUHwMMHP7ND/344Ydr"
        "7eyEp86ecdONqfuxP/hHNl/81Oc09JUPrn3n8v/4jXeefi/VslDQLmYLJbc0Jh5c6YnGwtkKB3lWLTCsrzri84sSzt3T+FeJ"
        "NNEUkU4QdWhk8sz5vfOf+ej1X9AZ4dWtG+82b/yjv7m9vb3X7e3OMSskcmyJTjLSYslZeixhwkkHvF7GMp7mUe573mF53ZoM"
        "y6ZMdVouO8p33n2rufjSx6uNs0+du/j07o+/vzX7p928bpQ0TnEmsVlIOrsnab4haWyFg3xaAfyi+Kc2FMBTCV5Z56nGvkHd"
        "Z3UAJgq+6X/0mXf++6qO/83e3a34b/7eX7m7u32v29ubR7OtvCW7yZTdZJXhAkQxpqzX4/eV3r6UONOvvV70RX+C9bHHw1jv"
        "168LGOkmO5C+qdJg9vZ3vt5c/sTvm9STyfOXL+5tv/3uhTc0dI2+lVRNJaHWWjHK37dQjDyH8fzAlgZidRFWFmFVUZtrm1HX"
        "/PylrUuzafs/AYpf+dVfurN3716zvTPv9C6jOgJgwqLehHr1+kEOY4pw1/UEHcFiqezEXt8/4tqiL/pTrMfzT0zHuMJHxouC"
        "HCyv4kji3Xt73Xx3u/nyv/j79+B1T+v2j1+8tPcUMAgstnnhEFf/TWxhEbAb5A+/Hi5+SNx8mymraurV+uZKK7DOn//0u/+z"
        "yj9x+713lt/84q9s397aiXSYc4AuFgDbTANrrG4B5JQDfskBPkaXRyljGU/xGNPouXcroosEl8vEVsaLc8GuU7DNF8vklnvd"
        "+Rc/Wp85d37z4tM787ffufC7mAiwtrgWrmpSt1vi9AWJt86Lxrq3Fbz6ad05tbxY5qdutKaNgv5TvXh+65kqxP8B4fbVf/mP"
        "7m5vz3NVhhVGsoYZovh8D55YRpWG0zibMvLBuSpLyWthXriXXZGLfPpkN5al18sKHyO8yCoF5YCPe9s78dqv//LdZ/7o/3pp"
        "Usc/rh7w331n6/x1hVObNtQzXnBVE1cG4uIKDeh2XxJ3fqrjwtxnP5Ew0fEjL939CQXp9PpbX9u9c+PdxbJtY5/fwpxi4O0L"
        "q3PRRsqiuHyzqzxZkYv8xMluJOfiJjsMP87lF4xDEhRBbV1/e3Hj21/fu/zKazNg8L1b5/+Bus0hzcXHSjyxWjH0dRUa0XVb"
        "4tql+I2ZohrdM7q8nnfa/Cze++ZbX9m9d3evdflD7YaGe2UBClYz8+bWv0MZy/jkjXLQ62419i/2Kx6GlxyLI7d39uIH1768"
        "CwADg4rFf1jDrM/UL45qZO+px4wuNordCg3p0D0yAoZ74jUq9qJB8oVzzaYG2z8JV/nG17865/KDHqP5ZlJk7CuDV8Ba5zVw"
        "21jkIj9J8nj0+/TBLki5zNKgZIlhQkfZaNRE3Xjr6u5nnFwABiezZrJo6qUsaVzZLBIMMrDr0bcZrV/ZPXJCBdvfvPbyB5/X"
        "95x8792393Z2thvyUmDPxNizZM4++Cz4BSZ7Ibsmzlg2yWyb6UeyK3KRT7Es+15f0zNoziNlVGjtu17S9t077e1335kDg599"
        "9f3P9rgkRpeGWWC3Qg/n8/rL7mbuHok5oxF/Zrq8gpni5jev7s7ny44BNzJcOo2QgwYh1QHDTvNTOSBPeXE+LLXn+Tbx4PVe"
        "dkUu8umWY8yyxrwGC7V9ee3sGB/OZAa/Hi0yeH3gactlKze/9fWdiy+8NDt7pvlk3chvIbydo8+cwo+YvYAYGNPBpvVtVvPd"
        "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"
        "y1GO78cxep5yQYVkUnpdn61tXi4v+fdsa8XKsoQrbUhc8f1sMcPATjtvVtmxIsKqsLh2EKWPT7mnXvtY+AP6+vd+7V+5//3W"
        "lmzp7wvFzqKdy3Kjk0ZTvu2Zt6W7tgcL/KykaxckvrIUd+dpiZu70gWEri16aEi4e1vu7e66v7F5Jv2JVz5SffYrV5vf7lpn"
        "e3wjRtV/uCcqyyIZCzB11HIxs0+dxa7J+uL6ZCDkC2jsgR5b/EYgxNC8gEQVg2n+9cB4k7XGzEcLLXTLMyMx+h8w+mPHfbIU"
        "uciZWDry/Lz+1+XnrS/iyLGqpThl8CQtYeT751NYrBHBKvcpJOO/JLfAQwzNWNc8TS61txJKcNEJNU++evUl/1lctrsnf/3O"
        "Tdmm9dUMMHbrhcN65ywxGq+9pK/eht37Qgrygbj9Vrg+qxyQxsKwxLOZbP6h/zz9v/pmn9/dTR989Rvdm/gmMVoVF6olGft2"
        "dOTZlRLFYLkIGtumJHoZ3WCJqWe5eBIZDHBuIhAJcYudfS9bw4Q0lstYxh/EOMSv49c917eL9dfgqiOht52jXxY50UjxfHrV"
        "NGxW/JStNNbpJ8bAJtMs05XW1/yPXQk/vnHGXYyd+51f/hfy+nwuO7C8iH2bbWn2W18YXyevKwbf1Le4IF6tcNjTCHgDbrQS"
        "WnOlskIrSEHPXvywPPMf/wfyZ/R2Xti6Hd9+6534TWEMTFChcsrCAiZ+4dnTOUg21SBiznlfR9CzwwG+JdxoyLDEmMkSLHHM"
        "M523zhyZ5Uv3red0++RHHdP36X3K+MMdf7D//10fKw+W19My2xJBn7MjrHrwkv+ljI4y0conXYxWeogtF8h2Y9WvJ5YNxJnE"
        "euVF/9HzF/xLevZ7v/U77ue/+57cBHjVR17OlLjSZEuj2GwUm923sJvLbb2hT9sSQy9f0DcaWeGl8lBnpuoBNzLxE42HAWKN"
        "hz/1ye7KlVf9L+iH1NdvyLUb77fvkZWODMm5iSGDfLzmMhHl+N2HONibpRabfzKo4zrjHA9yb8aM9EO4T/TEk33ecBT5iZHd"
        "/lhWHlAOo3fM+vEpQ0wrmaV2tNJmryS7zT5aPG334cyyW0rJ8sHmbj/3bPXc5efk43ra/Ovfdj//1TflG2h0pW7zMi5l2dWy"
        "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"
        "C0yn0j87fgAAAABJRU5ErkJggg=="
    ),
    "fr_card_lit5": (
        "iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAYAAAAy2+FlAAAQAElEQVR4nOx9a6wkx3XeqarumXvv7nJ3qaVIShRFiTQli5Is"
        "PyP4HRhO4gAJAhhEEgOBHcBwfgT54+RHfsQOYcBwHjCC/AkQA0nsAEGQyAiQhw3bCGzHil+yFVkWH5JMSTRFiVwtH7t7nzPT"
        "XZXzfaeqp+fufayWtMWdrSZma06fnumey/rqvE81cuKR3Ihw8oT++5SOV8TOP6rja+IeuU/cwUvi40U7v2jEpwNxF2bi4hl9"
        "ze183Fy+Twsdz+W7LOzcWalHPd78x04eXSuJb7aX7/1Ekt8fvd+VdHUqKSwkhs18/jVJG/dJfPYlpS/q67P5e+7R8TF9PUEq"
        "5bukk57FHc86BN7H87UAbwbuQ2fEd7sKSgVuv6+vVvwFBWy3rWDOYI1BPIAZFaRpqnSno75kS+/QLe8fu5OepR71eHMdvpEB"
        "WA7v92zEeTfTUQENoPteYgF1c07i1d0lmAHk5oyk53YlDkC+J3/vRziOwHs0kI8BzQDeVal7hMSFtD1/Td8DuArW3os/o2DF"
        "2B+oJJ6Im+p7ArfXV5slsL6XjTyO71yBXI838eEaWQGSC0of5BH0QsETDMizKNHNFbAbClh9v6ugxtgA1Arka+cltp2+P0oi"
        "36Q0PgIsx4P3kS+qxL1X3HwivgC3U6kL4G5l8BbgErT6AmDjTLWJlhLZp0a/K2YQNyMwj46J1KMeb75jfogeQNtlgHkFj75X"
        "iRvnCmQ/VQBjVNACzAXIeO31BuRGpXEB8mSu9GVJz75DEXKTID4E4GPAe4/4InUXGwpQBe1dCuB5LwHAXUQJAGubR7wavHcK"
        "WJxXgOI9gJuCvqK9JMB00LvGk6UuPiP1qMef8+F6Odn+9HDf6KHX4T1f+IyOPqnVqAB3Cla877z0ADJei/y+1RFAngTpryuA"
        "oVq3BxIHaXxFEXMKiEfAOAK8F/VRsq07SF0F7LyT0Cl4NxWcnQIVwO2ThGaiI4CL9wCvvgDcprP3BbwAblNALMcANFbQ1uNN"
        "dPgbwVwADuB24PdLEDuAtjHw8r2+vJM+KGi7uY76HkBu9LUPtVpBPGmkv9YvpfFgG7+m4zEgziC5OfCeU+BC6m4oSLtNBelc"
        "mgG4nTRB38fGAIz3W2dl8tj75T13n5OvV5vgXn3YexS893qR+/QuZ1x+mjrW8XYfe5FdBeuLKsC+ooLpigq5y9e35ZlPPSmf"
        "2duRea+ABYB9Jz3eh0Y6ALltpZsfKJD13IGCGdJ4W4F8syBeBfAT+ixHgBcS96yCFqryxpaCWIE72VAQ6xiCvnppAFz1uDUP"
        "PiiXHnlUPry1Kd/mvXyLfvGG1KMed+ihSDvoe/mDg3352Gc+J7/3wnPyikZmOrWDu05Bq7xOBSBBPNHxYE/Bq0De8SaRjwHx"
        "GMCHwJtt3hvAe0bCZCZN20izWBhwe4BXJa9+QfvWe3YuPvae6z/Utv3f0S8e/FB7116JX37mk/32q5fT7NrVdHD91bh3/dUU"
        "Ow0S638pJXFOR/0PtJ7Qj4/WtkpX+k1Il/la5q8iQ85cuOQ2zl7wG+cvurNvude9/b3f2Gzq+xGa92dz/x8+/bm7f/Hy5Y2r"
        "+k2LXiWxCkC1RGUBdXrRKZin0rW7R4B41SYmiAuALc57DHj1fTOFravgVU9cq3p84+eiUl7aS3d1Zz/0DS//4KTtflR/EHMx"
        "Xnvx+filZz7RXfn8U/3elRdVo4AJAbM2Db/FixtoL0cd5Y923FH5lf+15a/OZ8zvJc/nz2/dc7+79O7HwgPv+6bm4n3v4FRX"
        "8G8fzMK/++TTF/7bq9c2r0Max4mCWUeVfIubAnGOEwPAfgAvQkVT8Xszlbhq857ZU2mbJW9QsKpR3qierxLeXg+/a/vBdz+4"
        "/c91HXoPHuz6lRfjM7/1v2Yvf/apvvz4rlvIfNGnTnWFGGEAJK5aeHk3ksAcywJ3+HzlV/6bly95DCrZcK5RaYy5vTFtHc6J"
        "BErst37dY+E93/PXpufvud+AnNKnn3nurn/0wvN3fRkSmC+nklgBrABaAMTThXTFJt6aSv/sbBRi+ggA/IQCONu9iPMenBU/"
        "U1cTHFazA7V1J6Y2Q/ISvFEm+jvaD33olQ/de3H/X+nT3wU1+dMf/ZWDF5/8A6ZhdHr7g9kszWcL6WMvS4s/DQvbWCupYx1v"
        "9zGt0G44H1T0TdRTtbE5dY0PnPj3v+9bmvd+9w9sbJ2/WyElL1++vPXjn3z64lMJAPYyB4gHSTxXEG8YiKdXpN/Y0RAT4sTZ"
        "HlbVWYM4I9V5X73M5y5IUGO6mbYmecfg1cVk8p3fevkHzm51/1SfJrz0uae6P/rvP7/ba+QaFvnO7l7CnQtYU8xo5Y9LPO8G"
        "WsqSVulK3/Z0Gmg/8GEtG99Ju9HK2Y0NB1s0TKbuG//Gj2zd+66vb5Q5v7bT/sTvfvytv67u7DlAnBqZt71JYpWDnTqPu+2r"
        "0m+ql3qsSjv5Xr10pDrD7lVR3UxU+s70npC+rlGp2+tLAfwdH778V89u9T+ljyfP/dHvzp75tY/sQT3e29tPs4MFn5s/wt7Z"
        "Wx5mITipRz3W9xime/bsuGHCOxIgpwrkra1N1zSNvO/7H99654c+PMUVO3vhJ3/79+795QQAB311Gn5SALcK4LlKYcVnB3t4"
        "rEoHeegJr/fyWzsK3nMS7lKpP08K2qmpzpC6GruaaEho8oH3vvbY3RdnP6sSNDz9W7+09+xv/fLebDFL167txq7vsNSoWh91"
        "VdBRoXt49Bwl03Ws4/qNhuER7dKITxCkGGPaP5ilieq3r/zpZ+bwC1165yMqLON3qnD8va+8vPmK6wz8yKnuVS4udD04o5+d"
        "7et3XFV/04GggEK/88dS+9Ccnma/tSvNwYapzvoFkwVU51bV5oVM3/HO3fs++Mi1X9AHvOeFJz9+8Klf+c87M32InZ09hWwy"
        "FcL+Ga1DBl+Tvs7UidvVyKljHW9yTAMtZi6S9jY6n9VqlZqK77NnzzqVyO79f+Vvn3nH+79lUy+68sfPnv/hF7545kVI4ag+"
        "4Gljkpiq9IFK4jMqieGVnohGoV4Tgn2xZYUJyG2eacjIw6kGCaxx3gsX98998OFr/0wf5tKrX35+/uSvfWR7Z/egn+3Psqmr"
        "LnUEi5BHhsoEeOUA2nScN89+5EocuPIrf034MvC9Rl402uJX+d6hLABCLcj29p5es+me/NVf3NHYcbj7bQ9e+sAj13/m6lcm"
        "f3/voI0uSFws9BIdFZtx3lpWJCqYZFedUI983xMNTp7bphRWn5V6wVWFDhON90ZBrcH0w9/8yt9UR9pf37v2Wv+x//pvr+5t"
        "X+/39w8Sg0F4Uv1X3eaAcIL5jnN4ZtB5QUr68FQvcJ3xTa0QnB/RlV/5tzs/5vk+8GWVP77eqRheLHoFnUuvPPeZ+dve903T"
        "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"
        "HbJ5cayAl+9ufKxjQIwQk8aJu3vFjaVxp2DuFMQFyHwdiJ9mICd05JtxGxkX50pbwqhJ3iaP/epzTKQe9XjzHfNDtAtZAucd"
        "EyT3bvYTifOFjlOVvgumFscZut1sWM19AW7TW/3B1YVVF0HqNpclMc5bQkUngJfU0Y86Uq3H3mkFMVTqIo3jRXGwjc9f0/dn"
        "9HdkEJ9Z6OKVgZwm4gjmjvv7OgCad+jNvXUYvKmr0rYeb95j2MWk0ADxwQjMC2sSid0DAVo0oCvA3W31fTTgopnktfMqcdGv"
        "+TVJg9SFyrwK3tH9bsw+PAEsK+llFifGMQIybONuVyWrAhkSGar1BQB5WwG7SYnr1OPm4a2KCuo0VRpABki3VsEaK3DrcRsd"
        "fgRkgnrPRpx3M7Z/ZbtmD7Bi95N9VTjPSUT3m5AlLoCLqqLB1i3AxZHjvMs7Hp06fBOgOUalxnGERMbpAcwzBewZAzLOF1Dz"
        "WxXQci7fYWHnqlu6HrfDsZNHbgyIY3v5voB1eK+ARefXAlqeP0ri4rgJlfnw8f8BAAD//3NE5LcAAAAGSURBVAMACJugHtaY"
        "lRoAAAAASUVORK5CYII="
    ),
    "fr_card_ok0": (
        "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"
        "nV2Ck4sBXlpkjapWYxteJ80e4K2LGZw+ki1894M/mxfl3ykfry37t6/9mM4TBixSHkZzScuzAyv5EUFKv0m0ZamjatnOBS4u"
        "uuVBB8f8oeNohMUvX0rEyst+a7693azZnz3LkSwmfbiOJ6HUOFNLotTRuIsP9YOulZaH+fi1FVkUt+On7pGH5o4fBv4Lp1/O"
        "57uzgzz7D0t/8eU/hmv3SnjjvP6hhdhaQ2jS8xvnAlw+E5aOu/ePfhJWs/neDMyTG1jxZTfr0DBiQFVoiriU+2ZZN3BozlDe"
        "fKXV6HKNIzOUa1xddhqZJcI2PJmkDnVOwTwPP7q8LI1wN2tbSTGgR/PQvH8v9qstiX+NPkzE662mKsYbn7aznJr3D/MJd2kO"
        "aNT9l7CLlnJm1PtY+1eRle/zgkTbpcFmmcVIDfF+h7pfhyKuRHJA1k+XtxcUkXk438VOb2GmSzn56tKR2fdg9V6ANy6Hdsxt"
        "RWDGFxDeOZnB2nJ++Ecf/T3S+ueLD27/FG6uPAB90wblwIPAFwbyiKNX3+OzuNiTm1d3FF8yoL/zyqkyuqnKl2kr43HrDcN8"
        "G+uiU9j0/oYNbwa7+PrIzewfOlhvinxzN4mk6feS54qq3vImjGr5pBOexQ36gjnNi58/eDh/5bkXCL1//5WXfg+68wWcvUln"
        "uBAqXUE7AjNxgf796k1d79gJV9Fnn8PnDx0I9x7f5dxVmu/R3KyKVbSEMyJqrQ6jiquMn4YLXPwrP8XPIpbfW19G1o0uMom4"
        "C8MDk4jL2CWrecQ1rrlDTXLrtPobb/xoXh4YaPPZmPsvtYLF2+Rn1WZxk7xHNt7X/IdN+PhZ7SQwOQteDiXP688QLvQNjnw5"
        "JGb8U4vzNFVVUm56FVaXgvxw/nsBGoGuHYG5yQc652gYnR1477NDndXyn/CLPcqrt34YHq72dR+Owj5GVBZpisg+xJffxs+r"
        "HxplJXeuUTaJWYgo9nScdZ5uDcZkIfGLLCfzYLzxm+DzjfrDej5r8CJSbnlDwBxpeXsUfxy2yys5QtCl/THfFXFzREfd3x/o"
        "znS/fOKLVGMK9xfnfgte7D2EM5dpp0u+GX3jGUe1MwH6N8Jnf+7U0uIfPfwOZbd/tfy5I8fd5TsflSRSfu+WBsxuKCj5zPNu"
        "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"
        "QmCC"
    ),
    "fr_card_ok1": (
        "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"
    ),
    "fr_card_ok2": (
        "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"
    ),
    "fr_card_ok3": (
        "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"
    ),
    "fr_card_ok4": (
        "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"
        "pl4/x+38Ob58fcEL/iBwWpBh6fz+9UbillWYp5BaFqIX/xKhy9LwRiUITjnCMqpb6fQcDWh1PFxTAmwrziXA7N62s9LSaCQw"
        "VetE+5KoY9IcMJnfSvpkZvPPJ6PnP459IW3UzYcYO39+ax2pNuBU8II/Ejgu4q6HY2vKgqUtKpbapCaUGJKeo8oPbCGD2Nqi"
        "se/HNBKr+KhMUmdVJ6ptby6nrjx1Kp7cfKRAZ6knZjfYcankbjF/aUtNyTX06j6+XBe84I8S7k7j2v/R+ruQk5LZz0Lnlisu"
        "2PEQwHbmcKa6g7OL0aaU4N4yscwMZkc8Jd2Rg8w07kjsjZwuP2xoyYr2Jbtc0RxFo9xu1R032S/VIeMetuBY8II/RNxtxkOu"
        "M1fN8lQlljOa8/XCn9aOVj/U2znZbDaNnpS8TjXxPS5mSBIQ88omGVLUxRXu8WGdB44ywIj2NfuYNDrXrj0UizqSWQ/QKmKb"
        "QspfzmRakpdxKHjBH0HcrcF75BVPsuOD6ry8ykgu8RqWBg1qi7nsvfFLiCv+stOU6I1xrK0EdhXfIsq+WGIuGGntKdU8IAmp"
        "m3b26gejePzqNnslLZqVAMrmdnstG6Kg3QhvQYaeTCvwIhf5Ycu4pn+uOt9r5ApVztvl6BnGerWOTduaLNa0MtoGBVS+ONB0"
        "SvAJN5FXyloCt8znSV3JxLJgnM1baVCOWLXKw2E01aqrk4InrbnZpBt5eLJVSWDzYyBpXUjdfBnk+TJs54mLXOTHUHbe+jNK"
        "v2dV5/vzwXnBgihs361GsqX1XtOLUUhvipfPZ7q5oFNRopHxrjWw6ln54ENkC5l8lMYbTRbhe0rs20hsepYsaSMaieWhKYjq"
        "5tpTOz5J9Msly0jJqzJIyG640yknpwndkOtU5CI/erJD669AqqwsOzJnaElacedEUiarubYJTbkm165FYnUWgmpnPZ81rrSv"
        "rQ3UU8aOh3dK4AUNLKY9q1qMQWpd3G+50Pol8l52koElZDTNa1tpsRzzKiad8PJ5Fz77cmJud7nSIrtAC7v66Y/Tpl3K+fYj"
        "2n1TTg5JbWK4yQUv+L3iStYV/U+VjaVlUKapJSu1eHudKKeI7UsPbJ2vkVdkr8pLFjJI+3lBg+Pa6/0kKxl1NhbCPWlgvTlU"
        "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"
        "w6ZS1bOEziQEmYRB08QDsJ1oVKGYO0g5ap1f/KdWre7gESEE3cHD1hPL6rxgmleUGg3ZKR0PzvMFU/78+yjaV/5GslaCP50I"
        "hw6ZwF/n5zrvkjvAdMLTRVWUiaqZzOHc5gnbz/Gz/xhc3LsI107eVAPAaUK3GAZKXiVe8prMoSlaZG8vVAOhDXCJWSC7WNqP"
        "qcZK8jpS0cKyojZ6LdzGPJVkJ/WmDnKC14IM86kAcHNZl2L5peuz3E4NFPzJx6ldXN/vH8v9Z6F/kU5tpjbylNMcRccGMDXJ"
        "ZO06brTBQXUzWFQabHsp1FeXYER9p5gcz29EMDNZbpt1tM++M/vGml95YXzJGAGfC9Fx3Ipd3OiUowFG5KccMHpb2PMd1+nq"
        "PmggC2NIrhpE55rYQIzY4Gf5sX/MX9h7rnnvmAnsbY0A5cWDmqQhDrFXxxhjlfPHRGNGI3f3Gga0zC1ZR6wjV2P5ZrZbEH9u"
        "5oZMNq81zSz/vGZ72J6e+uX1eLfRdP4xq/wjLnrZy3XB3294Rav7R2X9raNx7l+u379IEzbkg+NBwZuRnF/8qdtKoSYp6a0k"
        "TJzXBasPHNRgZrLnbe5s3to0s00X2Zas3vI15D+WQgn4wfFzoDO0+NnGxxgmIdIgRIzs/6IFsGDvOmvg6QWC0QF5tpDjPgfA"
        "bp2kWXLRuypGiL8rihYPhs+7vdFrNJvdlod0avjrFhsSf9NkDs1Y0Y0ozc/V+Vyz90nJKufFJpvNTLqqal9mbj9SqgBaMstU"
        "k9SYF0joj71Y60inpG/t78XarTle8IIv4s74u6p/tbLPSkL1i4ZyUcmnluDAvOB2PKBKt0wHyZGSS6QWXLOOmOzIuNPnUHPT"
        "KX9tsJD5GvW/x2HPnRt9SFjB2vR/eebjLNQxRPZiz46TP+IGBwcEU7nL5C3iWSbCSzzCNIM0GQzi4GQa3XjYsGK9htP4S/yU"
        "P4ffcfA99MZ7vyNLCTXMnYysjsmYvNkG8r8UbZ5Xd/CSr6sk5C/BAa9U2XpIhwONVGuxsByYjOajDKtWe9txDUM3WpsPk8PS"
        "OLT9qPWlTVab7zKXC17wrXj3es1+/wLopj9CztUXqgoegvVfZ6SUKLM61bZYn2NVztqVJI8mStqkyerm8WlhINREvY8c8eYT"
        "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"
    ),
    "fr_card_panel": (
        "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"
    ),
    "fr_field_focus": (
        "iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAYAAAAy2+FlAAAQAElEQVR4nOx9a4wt2XXW2ntX1em+fZ/jedrjydgZTwaPYytO"
        "4jg2iW2ivIhFiIUliBAKEokQUn4QECCQ0AgBkUCAovyIRASBoCQCR8pLEJwg47zsOLFDbGdsbI/HY4/tec993+4+p2pv1vet"
        "vevUOd19+86dSbC7d43PXWfVqtdp17fXc6/dyIFbchPGyUP678NKn9HP2/X7l5WeF3ffneL6y/o5Jy5eFTds66cV/zI9ZNjS"
        "fbv6mQuvFTczzfzK3RZ799Wtbl+pm2slTXnfLXm/bd+xz88khauSnt/Q75f1+6bSLUnNef2ckvTIk3rsOf28XD/v15NuU/qg"
        "fh7ipSb3cGnf55B9tzXwvjsDF9v9q8Cdd+ILaM8CrAra/op4OaMAvio+bYg7oeBMvZ63peDtl/fkvhNrd+4rkOv2lbu5ZhW4"
        "cm11n8f3q7bvmoLc7ShotyTKRZHmpESvYL4AUC8kAszdXOIKkD+tn9vy9d5Del0Q7wOWEbyrWncf4C7Oi7/llLhFIx6ghYaN"
        "Qfzmtn6f6d06BfaOgllBuqnABHjToJ92Hw08VODW7atnc2EVyATxtu0HiLfBK7jDhkQ31327um9TATzoRzU0wNz2Ep9Xrdye"
        "k3ggkPdo41UQr4HmYPDe97iC9I6lxj2rGpfAVcAOXvzGJQkAbdTvM/0Mu6p9W+OhVbvWwMu7NMvv3GIFb92+Cje/BDGA63rj"
        "8X2+UF5B7KMCWL+HmcRd/Q4eYN45LUPQ781gQL6gGrm9VXmY1k8pkF+pqLgBEE+Asw94H9SP+rr3zRSoqnW3BwkwlU9PgYt9"
        "SrtoNDr9KHjb/F19Ww/AtgW0CtYEwLb5roeAN1Vw1+3/4+a8pBuSL/J3/QDAi94Arb5y9EniAsCd2XcAd+4NwDthCeRLCuSu"
        "kWHUxruKFvjGDx8M4gyOCXjfnfcBvA+Lv3dLwaoBqsWz6uMqeHUUCfowYSMpYE8ZcHuAVz8Ere5vXAYvaK9ugVIAVjWxL+Ad"
        "gbkO0FbqVrev3G2xxmcAE7wZxKp5I773ClY1p6NLBlzyTgaAOSiAG/0QyJcVyLpf8TOo9ToUbbyhgKZJ/aBe7wAQuwM17wS8"
        "u7sSzqjGnfcK3MEA3G8oYOfSALhNp+azAldHEqP6+brXycsefI184+aG3B0aud0HUe9Z7tSbnZS61e2IboqsK/rPk3GQJ4de"
        "nt7ekS8+/Bn5yKc/Ic8WAKulOvi5DL1+B5AXnfTNjgEYGhla+OKTEmd3K8jVpH7sqgL4ABAvAfwQQbsCXgaq1Fw+pcCdK3A3"
        "oH1baVoAuJGmgeYN0oRegaygfctb5J5X3SNv2dyUtzgn90nd6lY3binJI9e25fce+4J88AMfkC8EALmRPgzS99DGvVIAeVtB"
        "rHynQL6sQIZ/DJN6HxBPAFzAi4DVbfuDd6bAnavGVV+WWjcAuPrRUaZ542vPf82r7rn8w8q/U6/AAWH36uX0pU/9yfDE5x6J"
        "V555Kl147ul0+emn4u72ZR6S7OZKU+Urf2T4dnNLTt9+pz976x3u1K23uztffZ9/xf2vC7OtU6Yo9YQhul/99KMnf/rjnzr3"
        "uFqn/TDYRy3Zfq6auFtIv6vf9wXxM/pZBrZoQvsxz3u/Bax2FLgwm/cDr9dnhNZVE6B9zf1X73jda57/Ed3/Ln00f/XS+fSx"
        "9/364vMPf3R49vOfjUmW/v96JMp+fOUrf/T4tEfu5PZ77/P3vPb14fV/4XvbrdPngLt+0Ydf+PjHz/znR75w8pnYyQLaOKqX"
        "rQGwvlMA7y6WIJ7NZKBPvDuJTr+H2lcB/H51xXOqaOekglfTV2fu1O/XpJkpcOftEry9fkDf9s3PfNMdd+z8G32SM9cuPZ8+"
        "/Ou/On/4d967GBTpTu3nGCNpUtuhUL/GV1rpUaSyz37CWKlrGnn927+3/Ybv/ksdgKxHX3z6mY0f+60/vO3DatmqZyqLEcRZ"
        "E2+ckP7yBdXEOxrYuqIgLimmt2uATN6dwtTvRarolJrI89MSuh1pdjVmXMBbAPx93/HE929tLP6pPlD42Pv+x+5vv+dndjnc"
        "8OHFfsSesUgfXnWyonjP/pFWeZUfAXnaV+6FhraGqcGHTgNFP/CDG294x/d2ipfh4qX2H733d+76zQLgAuKZxrbnGwrmS6qJ"
        "1TfeVI089YeD3P5QkE6171nVuKp5NTIWFK1Bw9nNPEir30fgKtxn7/ruL/7o5iz+mKLUve9nf2r7I+/95TkfiiONxtBjfsjM"
        "22/wzFo5v+T5W9waX+VVfgTk7iA5wWt8Uqf38U98tL964fl47+vf2G1upO+6794ru5947PTHAaEAyOvhvX7RlG3SrFTaVXQj"
        "zXrHWUnPX9Dvj+O4H3rIyxfEn1YLd+eShNNbqn0X0ug9miap5h3U520VxEm6v/j2J77vxIn0Y/Ptq+mXf/JfXvn8xz6yyMNL"
        "4vBiqnfCy8i7NV4qX/ljxTvyxAF4b/yzjz82fPEzn+y/9hve1M422jffc9e1L3z2sZOPxiCpWUBvq7OcWBgiW52k7fMiist0"
        "4ZJe5U3QwPc/FO4NCtrOCjXU32XQCpp3wfILBW8v3Zvf9Ozr7njZ/N/h1r/6kz9++clH/m9fHooPlPS7S8aXh88P69Z4qXzl"
        "jzjv9sid7Sfv7XiPQgyXrjz/7PDUo58ZHnjz27rZLH77mZOLD37p8RPPAsSaakqDAnem5nG/Iem07ttWVL5MNfOFawDwqx9q"
        "Ti4UuDsSvAJXQdvorVCK3ShwW69m9GvuvXrnA/dd+feq+0+9/+d++uqjH/2DuWArvm4mtrn8se9u5Ounfo7XZ7933wJa+Eg+"
        "xujl558Zdq9eSV/zum/YOHO6/7b5PPzG+UvdNZRjAu5qBVMTi2pitZSTX0i6tKMUs4viOXGYVYTZRJtlMgKqqjoJaqo3b3jd"
        "hX+od7nt4d/5ze2HP/A+zLlAiDmqfkeqSP3tTN2SFyuSrPIqr/KD5AU/zpF+/Ld/49onP/C/t4E1YA7YIwYjY1Ie2ARGidVz"
        "NjOwwSSFxVXxi3Pie41A64gQVKGyPNKpL/zWb33260OQd1w9f77/3V/8L5c95yJEG0WSjSiJz2rRNRtTBjHr3caYUZ7KmBOr"
        "vMqPoVw5v9S+S/wkyhHo/e3/+jOX7/36N3YnTp99x7cr9n7v92/9Y9/J0PcKZMF0eomLRj/nVSNvSWStc1JEY3bRivZNnF3U"
        "3HX77t/B7f7w13/pauwHjBS0241KGvkcwEoQqY1v1HgHPi35Kq/yYyu3XGumxvsJfoZ+ET/y3l+5Aszdddvu31Yf2LBYtPC2"
        "zQQkZhW7nrOMtsShewYm32NmUYMJCb003/VtT327DhhvunL++eGTH3zfVah5xwlHEv0KTSjSjiZ3K9QXms0Fv76/yqv8CMtH"
        "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"
        "p2xfKIjWHqesZhQCr1tSSQQsmIgRJdq0WwwqSBuhqAJyNWXf/K2nv+fM2fBaDfL+3n/8T/Iv1Dpfal9R7etksana97LqTLWa"
        "h+aRJyXdrZd6Xj9bqv62GRBWvenZvQ5eeP+hD7mfeOub00+cOds++Odee/KLn/zM7sOyQMVVk+AzA8+aANM0kufKDR4rMHBi"
        "gqaIFlb7DIODKSeNlbMTXix/NBRZByvsxA+Eu57NaVZ1JJnwmQr/egj35wWMRW6GjnnCcX8ptVnyVf6VL7f9N/8eSKnubdfe"
        "M8AOUeLJ+0gwBpv44IsZjqVAYyggNJBD1TeBGsZHa5vsUKtIazqwjY6nT71gyggHvO7BE68HePWU5z/0QfmpgCaXCDspFncV"
        "Z+owRg08x+c18IyZyI9wPPmR1N47lzDvxE+18LxR+EELowNOlO7b3yEPvObV6V/j5370Yzu/8NQz/VMJ2hdaONLwZkU0/ASW"
        "YgJkzuSssbY/FKcrxvLHi1mD0m4x35duCfll18rxj8rvw/KPX7bKV/6l4tlCWWRlQQQYlM783eALP2n5mgygPAX/9AWwCE4R"
        "5KzYklwDzf2aG+aUQVjTCua7Xt7d8fUPdn8NZ3/6Eff3fue35NOaqplH9INU7dtdVBP65FL7dnOJj6FFrDyk6duHxd+7Jf7q"
        "VWlOnZOwu4OsDxebaAHeHmWdCuR3/WX5jlvOpr+vg961T3xy/mtPPBmfENZNwmU11Qi86o9MfQ9zGr+UM59kYXOfCWDw/QTU"
        "HAgnIx+Pn/xRuXsC+pXj8+7KV/4l4VHeKJP3rRxWeD85PluRrM0oZri3MsrxRBZe+aUZncsn1fVluggAvvvl/q6vu7/9fnWB"
        "N5497378l39Ffltf8jlM59agsJjPpZ9tKHjPy7C1Jf1jV/XuD6Ls4u0K4PvF3TcTD18Y3WBPRvbhanYt9gYDoe0z/Rt/Nf5w"
        "t+HehYKPz32u/++f/Vz/KHzRaCbHspsHNK1bzi1GdCrm/ZJ914z5kS/nTbe0n587yI1vTap+8nHeenfD/q2EVdbt5xtPNXOW"
        "jwsoJEsHsZrLS5mlZJ018jEum9f0DpW++mv8q179td07oYuvXXM/+/P/TX5BIbBQhb1QZM77XoGbAXxFTeku+76PaABLPg14"
        "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"
    ),
    "fr_field_idle": (
        "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"
    ),
    "fr_rowl_hover": (
        "iVBORw0KGgoAAAANSUhEUgAAAMgAAAB4CAYAAAC3kr3rAAAJt0lEQVR4nOydTYwcRxmGv+qZ2T+v12t7E1s2tvmT8iMCiYKF"
        "vGZFhEhAIIKIFMlROORAJDgAInCJclnJ4oIQl9zgxgkQElIQB65IJAgp5AZBCMSPcXbX8f7PTE/3dH9UVVf39Ey2q2dtR9qp"
        "ej/Jfuedp6r69Orr6u7tCQiFQlVWQCgUqrIQEBTKUggICmUpBASFshQCgkJZqknvY33wiRdmlhZOrzQadIWZL5IIzjOn5wMR"
        "nJN+PhCCUmaCQo+qCrrHdfkrL10gIZ4JmJ5kQZ+WLWompaxVQaGTpvesgzz+3OrSnGi+LDvDizJ4U8QkcyIo7nUp6exyFEXM"
        "SUxpv89pEpFIU83leNIxNePh4Y+Sv+sO8vCzq/MnW43vysR9Ry46r76LwzZF7R2OO+2U4zAbaA5aFDz8BPi76iBXv3b9AZHQ"
        "L+VaD6jFVLfobq2l/c4+C3OQJE0ojPvUl//UeV2aJJQkcrBOqihUJVeUPDj4UeB33EGWn7v+Wdk1fi4XWOhHIbW3Njhpb6dq"
        "ybjfp7AXUxRHlPQ5i2IRUSh0cvSOLvOuXLv+YsD8GxWOsL3Le//7R5K0d1LVGbb29mlre5863VCHg83BoNBJ1EN3EBUOOetV"
        "9bkju0YoT6kSueFut7vUk11DVZ6/vODhJ9UfqoNcuba6LHcRP1bnZ7sb/017W+tp2Itoc3OXwjDKFlfnckbh4Sfdj91BLl97"
        "5cIMTb0ub/bd19ne4N7merrf7VKnHRaJyzc6qj2p5jTw4OCTycfqIJ96fnVhmqZ+pcLR6+zJ06r1dK/dycJRSlx+HXno4ODg"
        "E8zHuszbSoJvy7GfiNXVqo1/p2oD3u2qcGQ8W2ykTcHDO+BrO8jlZ1fPkuBvqTR0bt1II7nnaO93dDiEOUHLFxv24OCTz2s7"
        "yHQgvi9YnAg7uxTJ06vt7b2CMZdVjHhw8Mnn1g6iuodM0dfV5+7mWronO0cxW29kMh3y4OAOcWsHmWmILyvp7t7msL3HURSb"
        "615slhgsVnhwcIe4tYPIAU8rjTq7vL/XLa8FhXqhlQFRl3aJxWfUA4ad3W1WDx3qSYKgUG+0MiDNmL4g9x9TPbk5D7vZ/Q41"
        "KVca8eDgLvLKgIgGfVxp0tnnMOxR/ihwNldNHvbg4C7yyoDIIWeUdrttzu8w6oSp8BRejHhwcLd45VUsee/jkhocheXHSWiQ"
        "uAMVHNwtXn0Vi/msGtQLO/qvA7MHuEqT4eE98NX3QQSdUYOSOKXi6UbK2xI8vB/e0kHohBqU9GMqnm5UmTIKD++Dt3UQPUin"
        "igceCvVJbR1EX+kiTvVgeHgffXVAdILUhyCbRCMeHNwDbukgJknqBYwifxFjyYODe8Dtfw9iklUopQd/Dw7uKLfdSc806zvw"
        "8F766mexcs1vxw95Bgf3gld3EM41G1wkiwfXicHBXefVz2KZXXymDIV6qdYOMhySg1SAgzvNqzuICUkRlgM9g4M7zS1Xsdhs"
        "VEy7gYf30Fvug2S7eSpvaIY8OLj7vLKD6CSpDOV9R08pe3Bw97n1TrpOlICH99dbO0gmUKi/at2D6JBUXwODQp3Xmg5iNizi"
        "vUoV34ODu8QtAclCovsIm0tgPPA04sHBXeQ1ASHKrwez2ZPoviLKHhzcXV4bEP3gFnFJdbAO+B4c3D1e30F4ZJG8DQ15cHA3"
        "eW1A1OjyJHh4n7z1rSa5Fpt6eHjPvPW9WDokZjAU6qNaOoiJUEkFPLxn3n4nXUeJCuUhL0Y8OLh73BIQJnuBg7vP669ioVAe"
        "l/UvCrXm7Qce3kNveS+W3qmYS14MD++lt7zVJN+oyP+FgIf30te81aSUqFEFB/eAW9/NWzlZKTE4uPN8jPdiCZOochuCh/fD"
        "2zuI+VR+2hEe3idf81aTXBkK9VKtNwp1B2ETE4aH989bA6KHZjsXPRoe3jc/3nux8knw8J55+28U5n1maDI8vD9+rL9JH7Qf"
        "M1cYKMDB3ebjvdWEOZszErDMg4O7y+uf5qXBdeGBBwf3g9vvpJcGQ6E+qv03ClWyeNB24OF98/ZfuaVstB4MD++hr+kg2Yfs"
        "sjDDw3vn7c9iyZCo0eWnHAeaLQIO7jK3P4slyKREDyeRfWFaC2UeHNxhXt9Bhjznaw17cHBHef2zWFCox1r/G4VQqMc6xltN"
        "zOARDw7uA69+L5beqAwuedGIBwf3gdd0kCxYxSI0mAwO7gO3vllRD84XKbwY8eDg7nLr07z6DXNkFoGH99DXvlmxCAs8vIe+"
        "soOkRDtqUNBoltpQVlnCRjw4uIPc9iOe62pQszUl2MziAh2g4OAO8uqACFpTCWq2WmZ01nbItCF4eB+87Uc8sw4yM5NNMhuY"
        "QT8yN1PKHhzcMW7pIGJdyczscVFM0sGh0iIjHhzcMV69SU/TPymdWzwlDjw5g0I90OqAtBq/kzyanjtGzalpytsPl2eXPDi4"
        "i7xBFfXOX/7Qu/Dg8rK8WfKRsL0nok6b1SRRjMj606BLDTw4uCu85sVxwWvq//mTS9mGhvKzLbOhMT6/LQ8O7hq3BqSX8G9V"
        "2zixdEbMHJsXxR3Gg1RUfA8OPsHcGpC3XvvhTZGmP1Cf77v4UaEnsZkMhXqgte/mjVvNVynl9eOnlsTs8UWRXx8ufip3yBOB"
        "g7vEG3UBUZv1cw9fTQTzU3OLp8XOxk1WK+WLUGkxeHjXfG1AVJ2d+9hbwcL0SrPZvKS6yM6td5hQKA+q9hRL1Ztv/iTuCnqe"
        "Bf3n2MKiOPOhB/U8FTgZuWIcPLxrXtAh6pNPv/RYoyF+LzcirZv/fDvdXrthVssuiQ0KHt4NP9YpVl43//bG2oWHrr4rP37x"
        "+OKSaM3Oif3NDdaLmQ1PsXjuRxUcfIL4oQKi6sbbr//5Aw8tr8vTrc/NHptvzi2eCtrb73KaJsMDRYWCg08QH2sPMlp//PWP"
        "fsoxfV5m7Pbs/CJ9+NErjVNnLwZq15+q5MlKMymUCg8OPjl8NDeHqsee+d6lqVT8Qq71aCBXisIub/zr77x9e52DvFuR/vNd"
        "goefRH/oU6xyrf31jZ0Tjzz1s+k02pHXjR9vtlqz86fvFwun7w+CZpPSfkxxHMmDZZ1Fdxh5VHj4SfF31UHK9ciXvnlytjn3"
        "slzzGzJ4U+rcTSUx7nVpb/MWR902yw5DSS/kKAqJk0TzfBwUehT1ngUkr8vXXrkwHUx/Va7+ZBAEK7KzzJBpV/kdSnj4SfH3"
        "PCDleuKF1Zm4J1YCEldYiIvy6OflYc/Jg56XR57Pz/l0lc4B4eGPin9fA4JCTXrd0WVeFMqXQkBQKEshICiUpRAQFMpSCAgK"
        "Zan/AwAA//+gCYOqAAAABklEQVQDAC1EOlo9vl8CAAAAAElFTkSuQmCC"
    ),
    "fr_rowl_idle": (
        "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"
        "AAAAAElFTkSuQmCC"
    ),
    "fr_rowr_hover": (
        "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"
    ),
    "fr_rowr_idle": (
        "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"
    ),
    "fr_tile_hover": (
        "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"
        "x6gP9dyBtY68+PT8+Zdd/4LajvePbdrqlVeWqVouBRvJ4WaCweCz8PiWaT7/4rfr51T5DV/2HvjagweoD/WVwFpHvvvMS9sv"
        "3VNV2/MzuoqsnD5JjVotspGIiIirxaHRCdp5+Y9nzHPm/MY9B7760N9Tn+o7gbUOv7h///bL9lyi1tBXTkzNcGHhlCCJERG7"
        "Je+kSt53ZLxsXrH/5Wcfe/gTtAb1tQ8cVaOx9Jtqu/4nm8vTrt3XZia2TrPeWF/aN77J0sHw4Z8j/sTULO+66icz+vly6vj0"
        "dzI5+XVao5gS0NU33zma8Sb+Vo12s+aTr7/snzz0sjQLTygw+BzlbTsv4W3bd9mGKfL1TF4++My+h0u0RiWSwOFY19zyiU+p"
        "Ae9Vo6rTSyfoyPf+r9Fo+KTPG+trPBERz7XInKEdl+32xjZPqaarU1o+9+xjD/0R2fRee9JRwrrmlk/erLZUd+PRWqVsuvHi"
        "sSMSWU0gIr7lo34AxpaZ7Tx9wUVqd9c846qkltEf/M/HHvo6JajEE1jrml/67XcweY+q4S/UP02lVJTjr/y/FBbmRe8L6Pse"
        "ERHfqnFS7etO77yYh/TNCTarX6379b3//dU/f44SVioJrLX75z85Njzs36XOUn9c/Qjn6feKy4syr/aNdSJrNk8eYG4uJsDg"
        "jcyjW6ZoZufF3vDYpM0rkWPC/BeVIn/huScfLFAKYkpZu278yPD0pqlfY5bfUdNdoH/aRr1Oah9ZVtTX0umTom9hRuVG3HDR"
        "y9DY5q3mRgT9oAt9Jkb//xbhH6jE+tPji/Nf7OXOon6UegJHde0tv/thFW5Ts/5s9P1llcTlpQWp1WvUqJapXqup88lVqqp9"
        "aH3YzidCRFyXmFFJ6uVy+q8l6Mi5/DBl1Dnc0cnNPL5lKpo/ZZXT31RvqHO7D/0dDUgDTeBQu3/5gbHJavmnyZN3q024SR20"
        "3tFchzQ3Cwx+c7N653UVn1Dp/q+N+vw3kjgt1KvWJYE7teeO+6/whN8uLDvVL2QniVpqM5vXKrk3EQStk1SaLqo0eU29OqS+"
        "XjOvfTkkHh98+h/veYHWWW+KBIYgqD+t+VJKCILWT0hgCNrAQgJD0AYWEhiCNrCQwBC0gYUEhqANLCQwBG1g/RAAAP//7DoB"
        "vgAAAAZJREFUAwAvhgBrcesL5AAAAABJRU5ErkJggg=="
    ),
    "fr_tile_idle": (
        "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"
        "tMjEAAAAAElFTkSuQmCC"
    ),
    "fr_tile_lit": (
        "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"
        "On/iw9/5yekk/lV90PT13/9Ht2699mJz4+Z2O5816mBziPNGtW3ZF9qBjoiDOwR5b+OP9EifRLo/nnu5w/lt08bripHt115q"
        "v/57/2gL2AGGfuwjLzwJXPmcSwGszfNiIGAQWBzUjHMyWIPvlgB8ZVn6bt2QqqjOe7B5vUyhPn/4wRc//ND9N/9Hvfjc1S//"
        "0+0XvvzZ3R+8fitGDRXhcc2wx8wUi46Rs1dSvneS4LMrfuAgGPa9637kj/wTwI/FMZtVzIClSxTBqlcT08G0UR33XoF07z3n"
        "/SOf+vTmB578uU1l3nzx1XO/8Wfff/CbUKNjlNm62sRFlT57Qdo7SeEsgQdVNQbSF2t554MVRUiNRLz34ftv/U39Amevfefp"
        "vRe+9Lmdre3dNmq8iDOQS9rTbaW9p6R13nos/UUfgs8z0zJ/2I/8kX+S+IpLfXmEWqIf8oEHXqew9NA8E3k3b+203/3y57av"
        "Pf80ikqdu3zfrf8I2ALGgDVgDtgDBg+Qwj1m3YAwz7NKXyRswHGlRnQNr7OfykTPQKGb6U988LlfOndm9nfb+X78wv/y967d"
        "un4z7u7tRc42KfuaUxrcx95TBufDQztibGM7La0f9g7/Z9k4HOzOAk7s9bWxvu7P33Mx/PSv/5V7q+mav3Vr+h9+9ZnHPq84"
        "mal8ncWZSuNa5rdJ4YFHemEDX5Fe+qJKJJCPNb3tui3ER36zSvTp2TOz38Tpz37pc1u721vd7t5+NPeU3PFFfnJZ7I+v8XXK"
        "X8mlo2AC2Nnd2mqf+8o/2wKmzp6b/QYwBqy1ee38gVL4ymJOqHrpC9v3Q+LUj+2aG+a8WmvUVMXCexXras5WP/mR73/ai3vv"
        "3s7N9qVv/PHOrW2V05xh8OBJluvbLc1NMraxvfuay68hXQ7bPze3duSlb3xp5+FP/OTmxpkLHwPGvvSNR39XTeiqWpOuwnLd"
        "Wjqst59fUOl7MWOVLWU5fyV/frZ9UYAO0hcF54r0XZ/urm+uz/49nPbMF//wxtb2ltrYDknN2evGQG80z5UYnaDvx94rZ+fL"
        "SI/0u4KGHygvq1vBRzlfeP721nb7zBf/6U1gSzH2G8BakcLEYDBMApvA6BCzCxX6mtVtRj2r87kcTimDE2oJn/zQK/+mThrv"
        "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"
    ),
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
        "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"
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
        "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"
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
        "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"
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
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAGtElEQVR4nOSba0xTZxjHn5629N5CwS6iG14ACaCMKRYwUzGw"
        "GY2LHwxekrmEkPjBDKfsg1HiBxONmx823UaYZtnEZeIWLx8koCzjw6biZUsklNvQojCgUEsLbekF6N7naB3CObQLpzf6S960"
        "53nPoTz/816e9yYA/1CLxeIP5HLVDolEmiGRyBJIko6Pj1MqVRyEEotlGAQCweTYmM1us9mMTqe9xWKx/Ox2O2pJtsnX8zwf"
        "+QqhUPRZUlLyvvj4BZTNNgovkhW838MBmUwxJcnpT5NpaLK7u7PS7XYfJbeMsD3Lm8X+0RtvLP4yKWm5Sqf7K2yc9Re5XAnp"
        "6e9Ad/ff5sHBf8qI6UeSPNPvYxSAoqjDK1asOulyOXmPH7dBJJOcnE6qiNDT3v6onFx+MT2fP/1aKIz5KSdn/Sek+PD6+59B"
        "pEOqAkxMTPCys/PeNxh6NZOTkzdhSkl4TQDy5g8R5w81Nf0GDscYzBccDjv09upBqy3IIZ9mYmry5k0VoCgtLasa3/x8cn4q"
        "2I4tWZLyntFoQAEeo416mScmDd4vWOfN5ucwXxkeNgLpunkaTWINuZSgjRYgJkZ8GFv7SG/w/KGrSwdLl6bGCgQxH+M19gK8"
        "1NRVtr6+bonVOgLRAHaRixa9Ze/oaJFTcrm8IC5OHTXOI+hrbOwCKYlu1wukUkVJpAU5XIA+k9C+VCASSbTRKoBYLM2lyKBm"
        "Acb20QYKgL5TUqlMHq0lAH2nyGiJH0oBNm3aBB6PZ0bavXs3BBL0mcQEfEGox/NspKamQqBB3ykIMWT8wWgvKCiAoPw+hCkb"
        "NmyAxMRECDQhF4DHY5+UOnLkCASasK0CyP79+2Hbtm0Q0N+HEDNbCUCuXLkCZWVlECjCtg3wIhQK4cyZM3D37l3YvHkzcE3Y"
        "C+AlNzcX6urqyJh+GG7cuEFXDy4I+yowndjYWNi6dStngVLElIBAEXIBMOxlQqfTQTAImgAJCQmQk5MDGzdu9CvMvX79OlnY"
        "SIeLFy+SuN0GgSKgAqxbtw6qqqqgr68PhoaG4P79+9DY2Ajl5eV+Pd/W1gZ79+6lxdu1axdcvXoVenp6gEsEEAAyMjLg/Pnz"
        "kJeXx5jPVuzZ7nE4HHD58mU6ITKZjMzpLQIu4FyAnTt3Qk1NDcwVsoLDmodVorOzE7iAUwG2bNnil/NkqQrCBc4EiI+Ph+rq"
        "ar/uJRMREC5wJsCpU6doEfxhqgBsgyF/2gku4EQAjUYDpaWlrPnXrl2Dc+fOQVNTE5jNZli4cOGrvGA5ygYnAmAXxQbG7JWV"
        "la/Z+vv7X33n8/mMz0VUCcjOzma037p1a4bzM/4BQUB6Yr/h5NeXLVvGaD979iz4IiYmhtE+WzfIJZwIwNaQYSTnCxzvMxFR"
        "VWB0lHldQalUgi/IAiWj3WAwQDDgZCzQ3NzMaN++fTv4Ij8/n9He2toKwYATAR4+fMhoP3jwIKSkpLA+h2OGkpKSGXa73Q63"
        "b9+GYMCJAPX19XT/Ph2sAg0NDbBmzZoZeZmZmVBbW8v49zBmCBactAFWq5Vu8Y8dOzYjLykpCR48eEA729LSAgqFgh4lsnWd"
        "yOnTpyFY8PLzCz137vwKc0WlUtHj/bmu6Z04cQIqKiogGBDfuZsQsVgsUFhYOKfW+9KlS0Fz3gunM0I4W6PVauki/3/BKrRn"
        "zx4INpxPiT19+hTWrl0Lx48fp6fBfDEwMEB3lwcOHIBQwMvIWO3R6f6EQFFcXAxFRUWwcuVKunQger2eTjh/cOHCBQgVxHfg"
        "ZWVpJ7u6WnnRtk0GzxQkJ6dPUGNjNhteRBvos91us1J4zCRaBSAvf5ByOu2P8JhJtPFSAB01MjL6Q7SWAKvV8j3lctnrhoeN"
        "LtxAHC0oFCo8SeJyOp31GAc49fqOb/CAUbSAvur17V+Rry7v4rxSo0nsUSpjlaRLhPlMSkoGGbmaRoaG+heTy1FvJDgyONhX"
        "jKer4uISYL6iVi8g03d8D3F+B7mkA5+pofDN9vZHR9PSsmC+gr51dDQfIl8bvLbpk/J/GAy9S7XagrcxMsTTVvMBfPPZ2flw"
        "717jd2Sy9bXNh2wbdD4lan2OB4zwjE0kg3Ueiz1587jX7uvp+XyW5+4YjYZmMmVdlJm5WmKxmMDlckIkgV0dvvWBgd6hZ8+6"
        "PiQmxpVbn1u0hELRvuXLV5xUqeLV/x2eHg3jw9MvktlsfP7kSXuF2+2umu1Zv/eoqdXqN0Ui2T6RSPwuSalisTSBVJGQb7d/"
        "eXx+nLRXRqfT0UnS706n7VuTyeTXXpp/AQAA///n0aUmAAAABklEQVQDADEzrthgVGTiAAAAAElFTkSuQmCC"
    ),
    "x": (
        "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"
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
        "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"
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
        if not IS_WIN and len(cmd) == 2:
            # browser impersonation for sites like TikTok (the Windows yt-dlp.exe already includes it)
            subprocess.Popen([cmd[0], "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check",
                              "--upgrade", "curl_cffi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, env=ENV)
    except Exception:
        pass


if not auto_update():
    update_downloader()
    main()
