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
VERSION = "2.0.0"
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

# (url fragment, display name, icon, kind)  kind: video | music | match (looked up on YouTube)
PLATFORMS = [
    ("music.youtube.", "YouTube Music", "youtubemusic", "music"),
    ("youtu", "YouTube", "youtube", "video"),
    ("instagram.", "Instagram", "instagram", "video"),
    ("tiktok.", "TikTok", "tiktok", "video"),
    ("twitter.com", "X", "x", "video"),
    ("x.com", "X", "x", "video"),
    ("facebook.", "Facebook", "facebook", "video"),
    ("fb.watch", "Facebook", "facebook", "video"),
    ("vimeo.", "Vimeo", "vimeo", "video"),
    ("soundcloud.", "SoundCloud", "soundcloud", "music"),
    ("twitch.", "Twitch", "twitch", "video"),
    ("spotify.", "Spotify", "spotify", "match"),
    ("music.apple.", "Apple Music", "applemusic", "match"),
    ("deezer.", "Deezer", "deezer", "match"),
]
SHOWCASE = ["youtube", "instagram", "tiktok", "x", "facebook", "vimeo",
            "spotify", "applemusic", "youtubemusic", "soundcloud", "deezer"]
BLOCKED = [("netflix.", "Netflix"), ("primevideo.", "Prime Video"), ("disneyplus.", "Disney+"),
           ("max.com", "Max"), ("tidal.", "Tidal")]


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


def platform_of(url):
    low = (url or "").lower()
    for key, name, icon, kind in PLATFORMS:
        if key in low:
            return name, icon, kind
    return "Web", None, "video"


def blocked_site(url):
    low = (url or "").lower()
    for key, name in BLOCKED:
        if key in low:
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

class Job(object):
    """Downloads without threads: every step is a child process whose output goes to a
    log file, and the window's timer calls poll(). (Threads starve inside Resolve.)"""

    def __init__(self, url, folder, audio_only, max_h):
        self.url, self.folder, self.audio_only, self.max_h = url, folder, audio_only, max_h
        self.platform, _, self.kind = platform_of(url)
        self.status = "Getting info"
        self.percent = 0.0
        self.done = False
        self.error = None
        self.cancelled = False
        self.path = None
        self.matched = None
        self.proc = None
        self.log = None
        self.offset = 0
        self.buf = ""
        self.part = 0
        self.tail = []
        self.on_exit = None
        self.ffmpeg = None

    # -- process plumbing --
    def _spawn(self, cmd, on_exit):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.log = open(LOG_PATH, "wb")
        self.offset, self.buf, self.on_exit = 0, "", on_exit
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=ENV, creationflags=NO_WINDOW)

    def _read_new(self):
        try:
            with open(LOG_PATH, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except Exception:
            return []
        self.offset += len(chunk)
        self.buf += chunk.decode("utf-8", "replace")
        lines = re.split(r"[\r\n]", self.buf)
        self.buf = lines.pop()
        return [l.strip() for l in lines if l.strip()]

    def _fail(self, msg):
        self.error = msg
        self.done = True

    def start(self):
        if not find_tool("yt-dlp"):
            return self._fail("yt-dlp not found. Run the LinkDrop installer again.")
        if not find_tool("ffmpeg"):
            return self._fail("ffmpeg not found. Run the LinkDrop installer again.")
        try:
            os.makedirs(self.folder, exist_ok=True)
        except Exception as e:
            return self._fail("Can't use the download folder:\n%s" % e)
        if self.kind == "match":
            self.audio_only = True
            self._lookup()
        else:
            self._download(self.url)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def poll(self):
        if self.done or not self.proc:
            return
        lines = self._read_new()
        if self.on_exit == self._download_done:
            for line in lines:
                self._parse(line)
        else:
            self.tail = (self.tail + lines)[-200:]
        code = self.proc.poll()
        if code is None:
            return
        try:
            self.log.close()
        except Exception:
            pass
        for line in self._read_new() + ([self.buf] if self.buf.strip() else []):
            if self.on_exit == self._download_done:
                self._parse(line)
            else:
                self.tail.append(line)
        self.buf = ""
        if self.cancelled:
            return self._fail("Canceled.")
        self.proc = None
        self.on_exit(code)

    # -- step 0 (Spotify / Apple Music / Deezer): find the song --
    def _lookup(self):
        self.status = "Finding the song on %s" % self.platform
        url = self.url
        if self.platform == "Spotify":
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if not m:
                return self._fail("Use a link to a single Spotify song (not a playlist or album).")
            url = "https://open.spotify.com/embed/track/" + m.group(1)
        elif self.platform == "Deezer":
            m = re.search(r"track/(\d+)", url)
            if not m:
                return self._fail("Use a link to a single Deezer song (deezer.com/track/...).")
            url = "https://api.deezer.com/track/" + m.group(1)
        self.tail = []
        self._spawn(["curl", "-fsSL", "--max-time", "15", "-A", UA, url], self._lookup_done)

    def _lookup_done(self, code):
        song = song_from_text(self.platform, "\n".join(self.tail)) if code == 0 else None
        if not song:
            return self._fail("Couldn't read the song from this %s link." % self.platform)
        self.matched = song
        self.status = "Found \"%s\". Looking for it on YouTube" % song
        self._download("ytsearch1:%s audio" % song)

    # -- step 1: yt-dlp --
    def _download(self, target):
        ffmpeg = find_tool("ffmpeg")
        self.ffmpeg = ffmpeg
        out_tpl = os.path.join(self.folder, "%(title).90B [%(id)s].%(ext)s")
        cmd = [find_tool("yt-dlp"), "--no-playlist", "--newline", "--no-colors", "--windows-filenames",
               "--encoding", "utf-8", "-o", out_tpl, "--print", "after_move:FINAL:%(filepath)s",
               "--progress-template",
               "download:PROG:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
               "--ffmpeg-location", os.path.dirname(ffmpeg)]
        if self.audio_only:
            cmd += ["-f", "bestaudio/best", "-x", "--audio-format", "wav"]
        else:
            h = "[height<=%d]" % self.max_h if self.max_h else ""
            # prefer H.264 + AAC (Resolve opens it without trouble)
            cmd += ["-f", "bv*%s+ba/b%s/bv*+ba/b" % (h, h),
                    "-S", "vcodec:h264,acodec:aac" + (",res:%d" % self.max_h if self.max_h else ""),
                    "--merge-output-format", "mp4"]
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
            if self.audio_only:
                overall = p * 0.95
            elif self.part <= 1:
                overall = p * 0.85
            else:
                overall = 85 + p * 0.12
            self.percent = max(self.percent, overall)
            what = "audio" if (self.audio_only or self.part > 1) else "video"
            self.status = "Downloading %s  \u00b7  %s  \u00b7  %s left" % (what, speed.strip().replace("MiB/s", "MB/s").replace("KiB/s", "KB/s"), eta.strip())
        elif line.startswith("FINAL:"):
            self.path = line[6:].strip()
        elif line.startswith("[Merger]") or line.startswith("[ExtractAudio]"):
            self.percent = max(self.percent, 97)
            self.status = "Merging video and audio" if not self.audio_only else "Converting to WAV"

    def _download_done(self, code):
        if code != 0 or not self.path or not os.path.exists(self.path):
            errs = [l for l in self.tail if "ERROR" in l] or self.tail[-3:]
            return self._fail("\n".join(errs)[-600:] + "\n\nIf the site changed, run the installer again "
                              "to update the downloader.")
        if self.audio_only:
            return self._finish()
        v, a = ffprobe_codecs(self.path)
        if (v is None or v in RESOLVE_OK_VCODECS) and (a is None or a in RESOLVE_OK_ACODECS):
            return self._finish()
        self._convert(v, a, hw=not IS_WIN)

    # -- step 2 (rare): convert to something Resolve can decode --
    def _convert(self, v, a, hw):
        self.percent = max(self.percent, 98)
        self.status = "Converting %s to H.264 so Resolve can play it" % v
        base, _ = os.path.splitext(self.path)
        self.converted = base + " (h264).mp4"
        if v in RESOLVE_OK_VCODECS:
            vargs = ["-c:v", "copy"]
        elif hw:
            vargs = ["-c:v", "h264_videotoolbox", "-b:v", "25M", "-pix_fmt", "yuv420p"]
        else:
            vargs = ["-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p"]
        aargs = ["-c:a", "copy"] if (a is None or a in RESOLVE_OK_ACODECS) else ["-c:a", "aac", "-b:a", "320k"]
        self.conv_args = (v, a, hw)
        self._spawn([self.ffmpeg, "-y", "-v", "error", "-i", self.path] + vargs + aargs +
                    ["-movflags", "+faststart", self.converted], self._convert_done)

    def _convert_done(self, code):
        v, a, hw = self.conv_args
        if code != 0 and hw:
            return self._convert(v, a, hw=False)
        if code == 0 and os.path.exists(self.converted):
            try:
                os.remove(self.path)
            except Exception:
                pass
            self.path = self.converted
        self._finish()

    def _finish(self):
        self.percent = 99
        self.status = "Placing in Resolve"
        self.done = True


# ------------------------------------------------------- Resolve: import ---

def get_downloads_bin(mp):
    root = mp.GetRootFolder()
    for f in (root.GetSubFolderList() or []):
        if f.GetName() == "Downloads":
            return f
    return mp.AddSubFolder(root, "Downloads") or root


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
    """Returns (message, next_cursor). cursor chains batch items one after another at the playhead."""
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("Open a project in Resolve first.")
    mp = project.GetMediaPool()
    prev_folder = mp.GetCurrentFolder()
    mp.SetCurrentFolder(get_downloads_bin(mp))
    items = mp.ImportMedia([path]) or []
    if prev_folder:
        mp.SetCurrentFolder(prev_folder)
    if not items:
        raise RuntimeError("Resolve could not import:\n" + path)
    item = items[0]
    name = os.path.basename(path)

    if target == 3:
        return "Imported into the Media Pool (Downloads bin).", cursor

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
            raise RuntimeError("Could not create the timeline.")
        project.SetCurrentTimeline(tl)
        return "New timeline created with the file.", None

    if target == 1:
        if not mp.AppendToTimeline([item]):
            raise RuntimeError("Could not add it to the timeline.")
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
            raise RuntimeError("Could not add it to the timeline.")
        return "Couldn't use the playhead, so it went to the end of the timeline.", None
    if len(placed) == 2:
        try:
            timeline.SetClipsLinked(placed, True)
        except Exception:
            pass
    end = max([p.GetEnd() for p in placed] + [rec + 1])
    return "Placed at the playhead (%s) on a free track." % rec_tc, end


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
# Visual language follows Apple's Human Interface Guidelines (dark appearance):
# system dark palette, one accent, spring-based motion, Reduce Motion respected.

ACCENT = "#FF375F"      # systemPink (dark)
SUCCESS = "#30D158"     # systemGreen (dark)
BG = "#1C1C1E"
FIELD = "#2C2C2E"
FIELD_HI = "#3A3A3C"
SEPARATOR = "#38383A"
LABEL = "#FFFFFF"
SECONDARY = "#98989F"
TERTIARY = "#5E5E63"

CSS = {
    "h1": "font-size: 20px; font-weight: 600; color: %s;" % LABEL,
    "body": "font-size: 13px; color: %s;" % SECONDARY,
    "caption": "font-size: 11px; color: %s;" % SECONDARY,
    "box": "QTextEdit { font-size: 13px; padding: 8px 10px; border-radius: 10px; background: %s;"
           "border: 1px solid %s; color: %s; }"
           "QTextEdit:focus { border: 1px solid %s; }" % (FIELD, SEPARATOR, LABEL, ACCENT),
    "primary": "QPushButton { font-size: 13px; font-weight: 600; padding: 8px 18px; border-radius: 8px;"
               "background: %s; color: #FFFFFF; border: none; }"
               "QPushButton:hover { background: #FF4F72; }"
               "QPushButton:pressed { background: #D92E50; }"
               "QPushButton:disabled { background: %s; color: %s; }" % (ACCENT, FIELD, TERTIARY),
    "secondary": "QPushButton { font-size: 13px; padding: 8px 16px; border-radius: 8px; background: %s;"
                 "color: %s; border: none; }"
                 "QPushButton:hover { background: %s; }"
                 "QPushButton:pressed { background: #48484A; }" % (FIELD, LABEL, FIELD_HI),
    "plain": "QPushButton { font-size: 13px; color: %s; background: transparent; border: none; padding: 4px 2px; }"
             "QPushButton:hover { color: %s; }"
             "QPushButton:pressed { color: %s; }" % (SECONDARY, LABEL, TERTIARY),
    "seg_left": "QPushButton { font-size: 13px; padding: 7px 0px; border-top-left-radius: 8px;"
                "border-bottom-left-radius: 8px; background: %s; color: %s; border: none; }"
                "QPushButton:checked { background: #636366; color: #FFFFFF; font-weight: 600; }" % (FIELD, SECONDARY),
    "seg_right": "QPushButton { font-size: 13px; padding: 7px 0px; border-top-right-radius: 8px;"
                 "border-bottom-right-radius: 8px; background: %s; color: %s; border: none; }"
                 "QPushButton:checked { background: #636366; color: #FFFFFF; font-weight: 600; }" % (FIELD, SECONDARY),
    "combo": "QComboBox { font-size: 13px; padding: 5px 10px; border-radius: 7px; background: %s;"
             "color: %s; border: none; min-width: 110px; }" % (FIELD, LABEL),
    "row": "QPushButton { text-align: left; font-size: 13px; padding: 10px 14px; border-radius: 10px;"
           "background: transparent; color: %s; border: none; }"
           "QPushButton:hover { background: %s; }"
           "QPushButton:pressed { background: %s; }"
           "QPushButton:checked { background: %s; color: %s; }" % (SECONDARY, FIELD, FIELD_HI, FIELD, LABEL),
    "tree": "QTreeWidget { background: transparent; border: none; color: %s; font-size: 13px; outline: 0; }"
            "QTreeWidget::item { padding: 5px 0px; border-bottom: 1px solid %s; }"
            "QTreeWidget::item:selected { background: %s; color: %s; }" % (LABEL, SEPARATOR, FIELD, LABEL),
    "insta": "QPushButton { font-size: 11px; font-weight: 600; color: %s; background: transparent;"
             "border: none; padding: 0px; } QPushButton:hover { text-decoration: underline; }" % ACCENT,
    "link": "QPushButton { font-size: 11px; color: %s; background: transparent; border: none; padding: 0px; }"
            "QPushButton:hover { color: %s; }" % (SECONDARY, LABEL),
    "update": "font-size: 11px; font-weight: 600; color: %s;" % SUCCESS,
    "badge": "font-size: %dpx; font-weight: 700; color: %s;",
}


def dot_css(v):
    """Step indicator: a 6px dot that stretches into an 18px accent capsule (v: 0 -> 1)."""
    v = min(max(v, 0.0), 1.3)
    w = int(round(6 + 12 * v))
    col = ACCENT if v > 0.5 else "#48484A"
    return ("background: %s; border-radius: 3px; min-width: %dpx; max-width: %dpx;"
            "min-height: 6px; max-height: 6px;" % (col, w, w))


def bar_css(fraction, pulse=None):
    """Thin progress track drawn with a gradient on a QLabel (determinate or shimmering)."""
    base = "border-radius: 2px; min-height: 4px; max-height: 4px; "
    track = FIELD
    if pulse is not None:
        a, b = max(0.0, pulse - 0.16), min(1.0, pulse + 0.16)
        if b <= a + 0.01:
            return base + "background: %s;" % track
        mid = min(max(pulse, a + 0.001), b - 0.001)
        return base + ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:%.3f %s, "
                       "stop:%.3f %s, stop:%.3f %s, stop:1 %s);" % (track, a, track, mid, ACCENT, b, track, track))
    f = min(max(fraction, 0.0), 1.0)
    if f <= 0.002:
        return base + "background: %s;" % track
    if f >= 0.998:
        return base + "background: %s;" % ACCENT
    return base + ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:%.3f %s, "
                   "stop:%.3f %s, stop:1 %s);" % (ACCENT, f, ACCENT, f + 0.001, track, track))


def short_title(path_or_url):
    name = os.path.splitext(os.path.basename(path_or_url))[0]
    return re.sub(r"\s*\[[^\]]+\]$", "", name) or path_or_url


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


class Spring(object):
    """Damped spring like SwiftUI's .spring(response:dampingFraction:). Retargetable: changing
    the target keeps the current velocity, so motion never jumps."""

    def __init__(self, value, response=0.45, damping=1.0):
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
        return abs(self.target - self.value) < 0.05 and abs(self.velocity) < 0.05

    def snap(self, value):
        self.value = self.target = float(value)
        self.velocity = 0.0


STEP_HEIGHTS = [384, 384, 384, 450]
PLACE_ROWS = [("At the playhead", "On a free track, so nothing gets overwritten"),
              ("At the end of the timeline", "Right after the last clip"),
              ("In a new timeline", "A timeline with just the downloaded files"),
              ("In the Media Pool only", "In the Downloads bin, not on a timeline")]
FORMAT_NOTES = ["MP4 (H.264 + AAC). Opens smoothly in Resolve.",
                "WAV, lossless. Ready for Fairlight."]


def main():
    settings = load_settings()
    calm = reduce_motion()
    W = 500

    def h1(id_, text):
        return ui.Label({"ID": id_, "Text": text, "StyleSheet": CSS["h1"], "Weight": 0})

    page_links = ui.VGroup({"Spacing": 10}, [
        h1("H1", "Add links"),
        ui.TextEdit({"ID": "Links", "PlaceholderText": "Paste one or more links, one per line",
                     "AcceptRichText": False, "StyleSheet": CSS["box"], "MinimumSize": [0, 84],
                     "MaximumSize": [16777215, 84], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 6}, [
            ui.Label({"ID": "Detected", "Text": "", "Weight": 0}),
            ui.Label({"ID": "Count", "Text": "", "StyleSheet": CSS["caption"], "Weight": 0}),
            ui.HGap(0, 1),
        ]),
        ui.VGap(0, 1),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Button({"ID": "Paste", "Text": "Paste", "StyleSheet": CSS["secondary"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.Button({"ID": "Next1", "Text": "Continue", "StyleSheet": CSS["primary"], "Weight": 0}),
        ]),
    ])

    page_format = ui.VGroup({"Spacing": 10}, [
        h1("H2", "Format"),
        ui.HGroup({"Weight": 0, "Spacing": 2}, [
            ui.Button({"ID": "ModeVideo", "Text": "Video", "Checkable": True, "StyleSheet": CSS["seg_left"],
                       "Weight": 1}),
            ui.Button({"ID": "ModeAudio", "Text": "Audio", "Checkable": True, "StyleSheet": CSS["seg_right"],
                       "Weight": 1}),
        ]),
        ui.Label({"ID": "FormatNote", "Text": "", "WordWrap": True, "StyleSheet": CSS["body"], "Weight": 0}),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Label({"ID": "QualityLabel", "Text": "Maximum quality", "StyleSheet": CSS["body"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.ComboBox({"ID": "Quality", "StyleSheet": CSS["combo"], "Weight": 0}),
        ]),
        ui.Label({"ID": "MatchNote", "Text": "", "WordWrap": True, "StyleSheet": CSS["caption"], "Weight": 0}),
        ui.VGap(0, 1),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Button({"ID": "Back2", "Text": "Back", "StyleSheet": CSS["secondary"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.Button({"ID": "Next2", "Text": "Continue", "StyleSheet": CSS["primary"], "Weight": 0}),
        ]),
    ])

    page_place = ui.VGroup({"Spacing": 4}, [
        h1("H3", "Place in Resolve"),
        ui.VGap(4, 0),
    ] + [ui.Button({"ID": "Place%d" % i, "Text": "", "Checkable": True, "StyleSheet": CSS["row"], "Weight": 0})
         for i in range(len(PLACE_ROWS))] + [
        ui.VGap(0, 1),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Button({"ID": "Back3", "Text": "Back", "StyleSheet": CSS["secondary"], "Weight": 0}),
            ui.HGap(0, 1),
            ui.Button({"ID": "Go", "Text": "Download", "StyleSheet": CSS["primary"], "Weight": 0}),
        ]),
    ])

    page_run = ui.VGroup({"Spacing": 10}, [
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            h1("RunTitle", "Downloading"),
            ui.HGap(0, 1),
            ui.Label({"ID": "Badge", "Text": "", "StyleSheet": CSS["badge"] % (1, SUCCESS),
                      "Alignment": {"AlignHCenter": True, "AlignVCenter": True},
                      "MinimumSize": [36, 32], "MaximumSize": [36, 32], "Weight": 0}),
        ]),
        ui.Label({"ID": "Bar", "Text": "", "StyleSheet": bar_css(0), "Weight": 0}),
        ui.Label({"ID": "Detail", "Text": "", "StyleSheet": CSS["body"], "Weight": 0}),
        ui.Tree({"ID": "Queue", "StyleSheet": CSS["tree"], "RootIsDecorated": False, "HeaderHidden": True,
                 "ColumnCount": 2, "Weight": 1}),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Button({"ID": "Reveal", "Text": "Show Files", "StyleSheet": CSS["secondary"], "Visible": False,
                       "Weight": 0}),
            ui.HGap(0, 1),
            ui.Button({"ID": "Stop", "Text": "Cancel", "StyleSheet": CSS["secondary"], "Weight": 0}),
            ui.Button({"ID": "More", "Text": "Add More Links", "StyleSheet": CSS["primary"], "Visible": False,
                       "Weight": 0}),
        ]),
    ])

    win = disp.AddWindow({
        "ID": "LinkDropWin",
        "WindowTitle": "LinkDrop",
        "Geometry": [340, 200, W, STEP_HEIGHTS[0]],
        "StyleSheet": "QWidget#LinkDropWin { background: %s; }" % BG,
    }, ui.VGroup({"Spacing": 14}, [
        ui.HGroup({"Weight": 0, "Spacing": 5}, [ui.HGap(0, 1)] +
                  [ui.Label({"ID": "Dot%d" % i, "Text": "", "StyleSheet": dot_css(1 if i == 0 else 0), "Weight": 0})
                   for i in range(4)] + [ui.HGap(0, 1)]),
        ui.Stack({"ID": "Pages", "Weight": 1}, [page_links, page_format, page_place, page_run]),
        ui.HGroup({"Weight": 0, "Spacing": 8}, [
            ui.Button({"ID": "Browse", "Text": "", "Flat": True, "StyleSheet": CSS["link"], "Weight": 0,
                       "ToolTip": "Where downloads are saved. Click to change."}),
            ui.HGap(0, 1),
            ui.Label({"ID": "Update", "Text": "", "StyleSheet": CSS["update"], "Weight": 0}),
            ui.Button({"ID": "Insta", "Text": "@gabrielxreis_", "Flat": True, "StyleSheet": CSS["insta"],
                       "ToolTip": INSTAGRAM_URL, "Weight": 0}),
        ]),
    ]))

    itm = win.GetItems()
    for q in QUALITIES:
        itm["Quality"].AddItem(q)
    itm["Quality"].CurrentIndex = int(settings.get("quality", 2))
    try:
        itm["Queue"].ColumnWidth[0] = 370
    except Exception:
        pass

    state = {"mode": int(settings.get("mode", 0)), "target": int(settings.get("target", 0)),
             "folder": settings.get("folder") or DEFAULT_FOLDER, "page": 0, "urls": [],
             "queue": [], "current": None, "cursor": None, "stopped": False,
             "last": time.time(), "fade": None, "pending_page": None}
    # springs (Apple-like: smooth for layout, snappy for indicators, bouncy for the success badge)
    springs = {
        "height": Spring(STEP_HEIGHTS[0], response=0.42, damping=1.0),
        "opacity": Spring(0.0, response=0.30, damping=1.0),
        "bar": Spring(0.0, response=0.55, damping=1.0),
        "badge": Spring(0.0, response=0.50, damping=1.0 if calm else 0.55),
    }
    for i in range(4):
        springs["dot%d" % i] = Spring(1.0 if i == 0 else 0.0, response=0.35, damping=1.0 if calm else 0.8)
    timer = ui.Timer({"ID": "Tick", "Interval": 16})

    # -------------------------------------------------------------- motion ---
    def apply_springs(dt):
        sp = springs
        h = sp["height"].step(dt)
        if not calm:
            win.Resize([W, int(round(h))])
        win.WindowOpacity = min(1.0, max(0.0, sp["opacity"].step(dt)))
        for i in range(4):
            itm["Dot%d" % i].StyleSheet = dot_css(sp["dot%d" % i].step(dt))
        sp["bar"].step(dt)
        b = sp["badge"].step(dt)
        if itm["Badge"].Text:
            itm["Badge"].StyleSheet = CSS["badge"] % (max(1, int(round(4 + 22 * b))), badge_color[0])
        # cross-fade between steps: dip, swap the page at the bottom, come back
        if state["pending_page"] is not None and sp["opacity"].value <= state["fade"] + 0.02:
            itm["Pages"].CurrentIndex = state["pending_page"]
            state["pending_page"] = None
            sp["opacity"].target = 1.0

    def springs_busy():
        return state["pending_page"] is not None or not all(s.settled() for s in springs.values())

    def kick():
        state["last"] = time.time()
        timer.Start()

    def go(page):
        if page == state["page"]:
            return
        state["page"] = page
        springs["height"].target = STEP_HEIGHTS[page]
        if calm:
            win.Resize([W, STEP_HEIGHTS[page]])
        for i in range(4):
            springs["dot%d" % i].target = 1.0 if i == page else 0.0
        # Reduce Motion: a plain fade; otherwise a quick, shallow dip while the window morphs
        state["fade"] = 0.25 if calm else 0.6
        state["pending_page"] = page
        springs["opacity"].target = state["fade"]
        kick()

    # ------------------------------------------------------------- helpers ---
    def save_all():
        save_settings({"folder": state["folder"], "mode": state["mode"], "target": state["target"],
                       "quality": itm["Quality"].CurrentIndex})

    def read_clipboard():
        txt = run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], 5) if IS_WIN else run(["pbpaste"], 3)
        return list(dict.fromkeys(u.rstrip(",;") for u in URL_RE.findall(txt or "")))

    def links_in_box():
        return list(dict.fromkeys(u.strip().rstrip(",;") for u in URL_RE.findall(itm["Links"].PlainText or "")))

    def refresh_links(ev=None):
        urls = links_in_box()
        icons = []
        for u in urls:
            icon = platform_of(u)[1]
            if icon and icon not in icons:
                icons.append(icon)
        itm["Detected"].Text = "&nbsp;".join(img_tag(i, 15) for i in icons[:8])
        if urls:
            itm["Count"].Text = "1 link" if len(urls) == 1 else "%d links" % len(urls)
        else:
            itm["Count"].Text = "YouTube, Instagram, TikTok, X, Spotify and 1,000+ more sites"
        itm["Next1"].Enabled = bool(urls)

    def refresh_format():
        audio_only_batch = not itm["ModeVideo"].Enabled
        itm["ModeVideo"].Checked = state["mode"] == 0
        itm["ModeAudio"].Checked = state["mode"] == 1
        itm["FormatNote"].Text = FORMAT_NOTES[state["mode"]]
        show_q = state["mode"] == 0 and not audio_only_batch
        itm["QualityLabel"].Visible = itm["Quality"].Visible = show_q

    def refresh_place():
        for i, (name, desc) in enumerate(PLACE_ROWS):
            on = i == state["target"]
            itm["Place%d" % i].Checked = on
            itm["Place%d" % i].Text = "%s%s\n%s" % (name, "   \u2713" if on else "", desc)

    def refresh_folder():
        path = state["folder"]
        itm["Browse"].Text = "Save to " + ("~" + path[len(HOME):] if path.startswith(HOME) else path)

    # --------------------------------------------------------------- steps ---
    def on_next1(ev=None):
        urls = links_in_box()
        if not urls:
            return
        state["urls"] = urls
        kinds = [platform_of(u)[2] for u in urls]
        music_only = all(k in ("music", "match") for k in kinds)
        itm["ModeVideo"].Enabled = not music_only
        if music_only:
            state["mode"] = 1
        notes = []
        if "match" in kinds:
            notes.append("Songs from Spotify, Apple Music and Deezer are found on YouTube and saved as audio.")
        blocked = sorted(set(b for b in (blocked_site(u) for u in urls) if b))
        if blocked:
            notes.append("%s can't be downloaded because of DRM, so those links will be skipped."
                         % ", ".join(blocked))
        itm["MatchNote"].Text = " ".join(notes)
        refresh_format()
        go(1)

    def pick_mode(m):
        state["mode"] = m
        refresh_format()
        save_all()

    def pick_place(i):
        state["target"] = i
        refresh_place()
        save_all()

    # --------------------------------------------------------------- queue ---
    badge_color = [SUCCESS]

    def set_row(entry, title=None, status=None):
        if title is not None:
            entry["item"].Text[0] = title
        if status is not None:
            entry["item"].Text[1] = status

    def pending():
        return [e for e in state["queue"] if e["state"] == "waiting"]

    def start_download(ev=None):
        save_all()
        itm["Queue"].Clear()
        state["queue"] = []
        for url in state["urls"]:
            name, icon, kind = platform_of(url)
            it = itm["Queue"].NewItem()
            it.Text[0] = url if len(url) <= 48 else url[:45] + "..."
            it.Text[1] = "Waiting"
            path = icon_path(icon) if icon else None
            if path:
                try:
                    it.Icon[0] = ui.Icon({"File": path})
                except Exception:
                    pass
            it.ToolTip[0] = url
            itm["Queue"].AddTopLevelItem(it)
            entry = {"url": url, "item": it, "state": "waiting", "job": None, "path": None,
                     "audio": state["mode"] == 1 or kind in ("music", "match")}
            if blocked_site(url):
                entry["state"] = "failed"
                set_row(entry, status="Skipped (DRM)")
            state["queue"].append(entry)
        state.update({"stopped": False, "cursor": None})
        springs["bar"].snap(0)
        springs["badge"].snap(0)
        itm["Badge"].Text = ""
        itm["RunTitle"].Text = "Downloading"
        itm["Bar"].StyleSheet = bar_css(0)
        itm["Stop"].Visible = True
        itm["More"].Visible = itm["Reveal"].Visible = False
        go(3)
        next_job()

    def next_job():
        todo = pending()
        if not todo or state["stopped"]:
            state["current"] = None
            all_done()
            return
        entry = todo[0]
        entry["state"] = "running"
        entry["job"] = Job(entry["url"], state["folder"], entry["audio"], QUALITY_H[itm["Quality"].CurrentIndex])
        state["current"] = entry
        springs["bar"].snap(0)
        entry["job"].start()
        kick()

    def finish(entry):
        job = entry["job"]
        if job.error:
            entry["state"] = "failed"
            set_row(entry, status="Canceled" if job.cancelled else "Couldn't download")
            entry["item"].ToolTip[0] = entry["item"].ToolTip[1] = job.error
            return
        entry["path"] = job.path
        t = short_title(job.path)
        set_row(entry, title=t if len(t) <= 48 else t[:45] + "...", status="Placing...")
        try:
            msg, state["cursor"] = place_in_resolve(job.path, state["target"], state["cursor"])
            entry["state"] = "ok"
            set_row(entry, status="\u2713 Placed")
            entry["item"].ToolTip[0] = "%s%s\n%s" % (("Matched: %s\n" % job.matched) if job.matched else "",
                                                     msg, job.path)
        except Exception as e:
            entry["state"] = "failed"
            set_row(entry, status="Resolve couldn't import it")
            entry["item"].ToolTip[0] = entry["item"].ToolTip[1] = str(e)

    def all_done():
        q = state["queue"]
        ok = [e for e in q if e["state"] == "ok"]
        failed = [e for e in q if e["state"] != "ok"]
        springs["bar"].target = 100.0
        itm["Stop"].Visible = False
        itm["More"].Visible = True
        itm["Reveal"].Visible = bool(ok)
        if ok and not failed:
            itm["RunTitle"].Text = "Done"
            itm["Detail"].Text = ("1 file placed in Resolve." if len(ok) == 1
                                  else "%d files placed in Resolve." % len(ok))
        elif ok:
            itm["RunTitle"].Text = "Done, with issues"
            itm["Detail"].Text = "%d placed, %d couldn't be downloaded. Hover a row to see why." % (len(ok), len(failed))
        else:
            itm["RunTitle"].Text = "Canceled" if state["stopped"] else "Nothing was downloaded"
            first = next((e for e in failed if e["job"] and e["job"].error), None)
            itm["Detail"].Text = (first["job"].error.split("\n")[0][:110] if first and not state["stopped"]
                                  else "Hover a row to see why.")
        badge_color[0] = SUCCESS if ok else SECONDARY
        itm["Badge"].Text = "\u2713" if ok else "!"
        springs["badge"].snap(0)
        springs["badge"].target = 1.0
        if not state["stopped"]:
            play_feedback(bool(ok))
        kick()

    # ----------------------------------------------------------------- tick ---
    def on_tick(ev):
        now = time.time()
        dt = min(0.05, max(0.001, now - state["last"]))
        state["last"] = now
        entry = state["current"]
        if entry:
            job = entry["job"]
            job.poll()
            if job.percent <= 0 and not job.done:
                phase = (now % 1.6) / 1.6
                itm["Bar"].StyleSheet = bar_css(0, pulse=-0.2 + 1.4 * phase)
                set_row(entry, status="Finding song" if job.kind == "match" else "Getting info")
            else:
                springs["bar"].target = job.percent
                set_row(entry, status="%d%%" % int(round(max(0, springs["bar"].value))))
            done_n = len([e for e in state["queue"] if e["state"] in ("ok", "failed")])
            prefix = "" if len(state["queue"]) == 1 else "%d of %d  \u00b7  " % (done_n + 1, len(state["queue"]))
            itm["Detail"].Text = prefix + job.status
            if job.done:
                finish(entry)
                next_job()
        apply_springs(dt)
        if not (entry and entry["job"].percent <= 0):
            itm["Bar"].StyleSheet = bar_css(springs["bar"].value / 100.0)
        if not state["current"] and not springs_busy():
            timer.Stop()

    # --------------------------------------------------------------- events ---
    def on_paste(ev):
        urls = read_clipboard()
        if urls:
            cur = (itm["Links"].PlainText or "").strip()
            itm["Links"].PlainText = (cur + "\n" if cur else "") + "\n".join(urls)
        else:
            itm["Count"].Text = "Your clipboard doesn't have a link. Copy one in your browser first."

    def on_stop(ev):
        state["stopped"] = True
        for e in pending():
            e["state"] = "failed"
            set_row(e, status="Canceled")
        if state["current"]:
            state["current"]["job"].cancel()

    def on_more(ev):
        itm["Links"].PlainText = ""
        go(0)

    def on_reveal(ev):
        files = [e["path"] for e in state["queue"] if e.get("path")]
        if files:
            reveal(files[-1])

    def on_browse(ev):
        d = fusion.RequestDir(state["folder"])
        if d:
            state["folder"] = str(d).rstrip("/\\")
            refresh_folder()
            save_all()

    def on_open_item(ev):
        for e in state["queue"]:
            if e["path"] and e["item"].Text[0] == ev["item"].Text[0]:
                reveal(e["path"])
                return

    def on_close(ev):
        state["stopped"] = True
        if state["current"]:
            state["current"]["job"].cancel()
        timer.Stop()
        save_all()
        disp.ExitLoop()

    win.On.LinkDropWin.Close = on_close
    win.On.Links.TextChanged = refresh_links
    win.On.Paste.Clicked = on_paste
    win.On.Next1.Clicked = on_next1
    win.On.ModeVideo.Clicked = lambda ev: pick_mode(0)
    win.On.ModeAudio.Clicked = lambda ev: pick_mode(1)
    win.On.Quality.CurrentIndexChanged = lambda ev: save_all()
    win.On.Back2.Clicked = lambda ev: go(0)
    win.On.Next2.Clicked = lambda ev: go(2)
    win.On.Back3.Clicked = lambda ev: go(1)
    for _i in range(len(PLACE_ROWS)):
        win.On["Place%d" % _i].Clicked = (lambda i: (lambda ev: pick_place(i)))(_i)
    win.On.Go.Clicked = start_download
    win.On.Stop.Clicked = on_stop
    win.On.More.Clicked = on_more
    win.On.Reveal.Clicked = on_reveal
    win.On.Browse.Clicked = on_browse
    win.On.Queue.ItemDoubleClicked = on_open_item
    win.On.Insta.Clicked = lambda ev: open_url(INSTAGRAM_URL)
    disp.On.Timeout = on_tick

    if IS_WIN:
        itm["Reveal"].Text = "Show in Explorer"
    itm["Pages"].CurrentIndex = 0
    refresh_folder()
    refresh_format()
    refresh_place()
    urls = read_clipboard()
    if urls:
        itm["Links"].PlainText = "\n".join(urls)
    refresh_links()
    if globals().get("_LINKDROP_UPDATED_FROM"):
        itm["Update"].Text = "Updated to %s" % VERSION

    win.WindowOpacity = 0.0
    win.Show()
    springs["opacity"].target = 1.0
    kick()
    disp.RunLoop()
    win.Hide()


# ----------------------------------------------------------------- icons ---
# Platform logos (from Simple Icons, CC0), embedded so updates carry them.

ICONS = {
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
        "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"
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
        ytdlp = find_tool("yt-dlp")
        if not ytdlp or not ytdlp.startswith(DATA_DIR):
            return  # only self-update our own copy (not Homebrew's)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(time.time()))
        subprocess.Popen([ytdlp, "-U"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, env=ENV, creationflags=NO_WINDOW)
    except Exception:
        pass


if not auto_update():
    update_downloader()
    main()
