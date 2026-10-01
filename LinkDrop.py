#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinkDrop for DaVinci Resolve, by @gabrielxreis_

Paste a link (YouTube, Instagram, TikTok, X, Vimeo, public Drive...) and
LinkDrop downloads the video or just the audio with yt-dlp, imports it into
the Media Pool ("Downloads" bin) and drops it on your timeline.

Open from: Workspace > Scripts > Utility > LinkDrop
Requires: yt-dlp and ffmpeg (brew install yt-dlp ffmpeg)
"""

import os
import re
import sys
import json
import time
import shutil
import threading
import subprocess

APP_TITLE = "LinkDrop"
VERSION = "1.0.0"
REPO = "gabrielxreis/LinkDrop"
RAW_URL = "https://raw.githubusercontent.com/%s/main/LinkDrop.py" % REPO
SCRIPT_PATH = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
                                  "Fusion/Scripts/Utility/LinkDrop.py")
INSTAGRAM_URL = "https://instagram.com/gabrielxreis_"
SETTINGS_PATH = os.path.expanduser("~/Library/Application Support/LinkDrop/settings.json")
DEFAULT_FOLDER = os.path.expanduser("~/Movies/Resolve Downloads")
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin"),
               os.path.expanduser("~/.deno/bin"), "/usr/bin", "/bin"]
URL_RE = re.compile(r"https?://\S+", re.I)

QUALITY_H = [None, 2160, 1080, 720]
RESOLVE_OK_VCODECS = {"h264", "hevc", "prores", "dnxhd", "mjpeg", "mpeg2video", "mpeg4"}
RESOLVE_OK_ACODECS = {"aac", "pcm_s16le", "pcm_s24le", "pcm_f32le", "mp3", "alac"}


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
    api = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"
    if api not in sys.path:
        sys.path.append(api)
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

def env_with_path():
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join([p for p in EXTRA_PATHS if p not in parts] + parts)
    return env


ENV = env_with_path()


def find_tool(name):
    return shutil.which(name, path=ENV["PATH"])


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def clipboard_url():
    try:
        txt = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""
    m = URL_RE.search(txt or "")
    return m.group(0).strip() if m else ""


def ffprobe_codecs(path):
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None, None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                              "-of", "json", path], capture_output=True, text=True, env=ENV, timeout=60).stdout
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
    try:
        r = subprocess.run(["curl", "-fsSL", "--max-time", "8", RAW_URL + "?t=%d" % int(time.time())],
                           capture_output=True, text=True, timeout=12)
        code = r.stdout if r.returncode == 0 else ""
        m = re.search(r'^VERSION = "([^"]+)"', code, re.M)
        if not m:
            return None, None
        compile(code, "LinkDrop.py", "exec")  # never install a broken file
        return m.group(1), code
    except Exception:
        return None, None


def install_update(code):
    path = globals().get("__file__") or SCRIPT_PATH
    if not os.path.exists(path):
        path = SCRIPT_PATH
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(code)
    os.replace(tmp, path)


def tc_to_frames(tc, fps):
    fps_i = int(round(fps))
    parts = re.split(r"[:;.]", tc)
    if len(parts) != 4:
        return 0
    h, m, s, f = [int(p) for p in parts]
    return ((h * 3600 + m * 60 + s) * fps_i) + f


# -------------------------------------------------------------- download ---

class Job(object):
    """Runs yt-dlp on a thread; the UI polls its state with a timer."""

    def __init__(self, url, folder, audio_only, max_h):
        self.url, self.folder, self.audio_only, self.max_h = url, folder, audio_only, max_h
        self.status = "Getting ready..."
        self.percent = 0.0
        self.done = False
        self.error = None
        self.path = None
        self.proc = None
        self.cancelled = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _cmd(self, ytdlp, ffmpeg):
        out_tpl = os.path.join(self.folder, "%(title).90B [%(id)s].%(ext)s")
        cmd = [ytdlp, "--no-playlist", "--newline", "--no-colors", "--windows-filenames",
               "-o", out_tpl, "--print", "after_move:FINAL:%(filepath)s",
               "--progress-template", "download:PROG:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"]
        if ffmpeg:
            cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]
        if self.audio_only:
            cmd += ["-f", "bestaudio/best", "-x", "--audio-format", "wav"]
        else:
            h = "[height<=%d]" % self.max_h if self.max_h else ""
            # prefer H.264 + AAC (Resolve opens it without trouble)
            cmd += ["-f", "bv*%s+ba/b%s/bv*+ba/b" % (h, h),
                    "-S", "vcodec:h264,acodec:aac" + (",res:%d" % self.max_h if self.max_h else ""),
                    "--merge-output-format", "mp4"]
        cmd.append(self.url)
        return cmd

    def _run(self):
        try:
            ytdlp = find_tool("yt-dlp")
            ffmpeg = find_tool("ffmpeg")
            if not ytdlp:
                raise RuntimeError("yt-dlp not found. Install it with: brew install yt-dlp")
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found. Install it with: brew install ffmpeg")
            os.makedirs(self.folder, exist_ok=True)

            self.status = "Connecting..."
            tail = []
            self.proc = subprocess.Popen(self._cmd(ytdlp, ffmpeg), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, env=ENV, bufsize=1)
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                tail = (tail + [line])[-8:]
                if line.startswith("PROG:"):
                    pct, speed, eta = (line[5:].split("|") + ["", "", ""])[:3]
                    try:
                        self.percent = float(pct.strip().rstrip("%"))
                    except ValueError:
                        pass
                    self.status = "%s  ·  %s  ·  %s left" % (pct.strip(), speed.strip(), eta.strip())
                elif line.startswith("FINAL:"):
                    self.path = line[6:].strip()
                elif "[Merger]" in line or "[ExtractAudio]" in line or "[VideoConvertor]" in line:
                    self.status = "Processing with ffmpeg..."
            code = self.proc.wait()
            if self.cancelled:
                raise RuntimeError("Download cancelled.")
            if code != 0 or not self.path or not os.path.exists(self.path):
                errs = [l for l in tail if "ERROR" in l] or tail[-3:]
                raise RuntimeError("yt-dlp failed:\n" + "\n".join(errs) +
                                   "\n\nIf the site changed, update it: brew upgrade yt-dlp")

            if not self.audio_only:
                self._ensure_resolve_codecs(ffmpeg)
            self.status = "Download complete."
            self.percent = 100.0
        except Exception as e:
            self.error = str(e)
        finally:
            self.done = True

    def _ensure_resolve_codecs(self, ffmpeg):
        v, a = ffprobe_codecs(self.path)
        if (v is None or v in RESOLVE_OK_VCODECS) and (a is None or a in RESOLVE_OK_ACODECS):
            return
        self.status = "Converting %s/%s to H.264/AAC..." % (v, a)
        base, _ = os.path.splitext(self.path)
        out = base + " (h264).mp4"
        vargs = ["-c:v", "copy"] if v in RESOLVE_OK_VCODECS else \
                ["-c:v", "h264_videotoolbox", "-b:v", "25M", "-pix_fmt", "yuv420p"]
        aargs = ["-c:a", "copy"] if (a is None or a in RESOLVE_OK_ACODECS) else ["-c:a", "aac", "-b:a", "320k"]
        cmd = [ffmpeg, "-y", "-v", "error", "-i", self.path] + vargs + aargs + ["-movflags", "+faststart", out]
        r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        if r.returncode != 0 and "videotoolbox" in " ".join(vargs):
            vargs = ["-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p"]
            cmd = [ffmpeg, "-y", "-v", "error", "-i", self.path] + vargs + aargs + ["-movflags", "+faststart", out]
            r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        if r.returncode == 0 and os.path.exists(out):
            os.remove(self.path)
            self.path = out


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


def place_in_resolve(path, target):
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
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
        return "Imported into the Media Pool (Downloads bin)."

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
        return "New timeline created with the file."

    if target == 1:
        ok = mp.AppendToTimeline([item])
        if not ok:
            raise RuntimeError("Could not add it to the timeline.")
        return "Added to the end of timeline \"%s\"." % timeline.GetName()

    # target == 0: at the playhead, on a free track (never overwrites anything)
    fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    rec_tc = timeline.GetCurrentTimecode()
    rec = tc_to_frames(rec_tc, fps)
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
        return "Could not place at the playhead; added to the end of the timeline."
    if len(placed) == 2:
        try:
            timeline.SetClipsLinked(placed, True)
        except Exception:
            pass
    return "Placed at the playhead (%s) on a free track." % rec_tc


# -------------------------------------------------------------------- UI ---

ACCENT = "#FF4D6D"
ACCENT2 = "#FF8A3D"
PLATFORMS = [("music.youtube", "YouTube Music"), ("youtu", "YouTube"), ("instagram", "Instagram"), ("tiktok", "TikTok"),
             ("twitter.com", "X / Twitter"), ("x.com", "X / Twitter"), ("vimeo", "Vimeo"),
             ("facebook", "Facebook"), ("fb.watch", "Facebook"), ("drive.google", "Google Drive"),
             ("soundcloud", "SoundCloud"), ("twitch", "Twitch"), ("dropbox", "Dropbox")]
MUSIC_SITES = ("YouTube Music", "SoundCloud")
DRM_SITES = [("spotify.com", "Spotify"), ("music.apple.com", "Apple Music"), ("deezer.com", "Deezer"),
             ("tidal.com", "Tidal"), ("netflix.com", "Netflix"), ("primevideo.com", "Prime Video"),
             ("disneyplus.com", "Disney+")]
STEPS = ["Link", "Options", "Done"]
MODE_CARDS = [("Video + audio", "H.264 MP4, plays smooth"),
              ("Audio only", "Lossless WAV for Fairlight")]
QUALITY_SHORT = ["Best", "4K", "1080p", "720p"]
TARGET_CARDS = ["Playhead", "End of timeline", "New timeline", "Media Pool"]
TARGET_HINTS = ["Drops at the playhead on a free track. Nothing gets overwritten.",
                "Goes right after the last clip of the current timeline.",
                "Creates a new timeline with just this file.",
                "Only imports it into the Media Pool, in the Downloads bin."]

GRAD = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:1 %s)" % (ACCENT, ACCENT2)
CSS = {
    "title": "font-size: 22px; font-weight: 700; color: #FFFFFF;",
    "sub": "font-size: 13px; color: #9A9AA5;",
    "brand": "font-size: 15px; font-weight: 800; color: #FFFFFF; letter-spacing: 1px;",
    "step_on": "font-size: 12px; font-weight: 700; color: #FFFFFF; background: %s;"
               "border-radius: 11px; padding: 3px 10px;" % GRAD,
    "step_done": "font-size: 12px; color: #FF8FA3; background: #3A2530; border-radius: 11px; padding: 3px 10px;",
    "step_off": "font-size: 12px; color: #6E6E78; background: #26262C; border-radius: 11px; padding: 3px 10px;",
    "url": "QLineEdit { font-size: 16px; padding: 12px 14px; border-radius: 10px; background: #1B1B20;"
           "border: 1px solid #3A3A44; color: #FFFFFF; }"
           "QLineEdit:focus { border: 1px solid %s; }" % ACCENT,
    "ghost": "QPushButton { font-size: 13px; padding: 9px 16px; border-radius: 9px; background: #2A2A31;"
             "color: #E6E6EA; border: 1px solid #3A3A44; }"
             "QPushButton:hover { background: #33333B; }"
             "QPushButton:disabled { color: #55555E; }",
    "primary": "QPushButton { font-size: 14px; font-weight: 700; padding: 10px 22px; border-radius: 10px;"
               "background: %s; color: #FFFFFF; border: none; }"
               "QPushButton:hover { background: %s; }"
               "QPushButton:disabled { background: #3A3A44; color: #77777F; }" % (GRAD, ACCENT),
    "card": "QPushButton { text-align: left; font-size: 14px; padding: 12px 16px; border-radius: 12px;"
            "background: #222228; color: #D8D8DE; border: 1px solid #34343D; }"
            "QPushButton:hover { border: 1px solid #5A5A66; }"
            "QPushButton:checked { background: #34202A; color: #FFFFFF; border: 2px solid %s; }" % ACCENT,
    "seg": "QPushButton { font-size: 13px; padding: 8px 0px; border-radius: 8px; background: #222228;"
           "color: #B8B8C0; border: 1px solid #34343D; }"
           "QPushButton:checked { background: %s; color: #FFFFFF; border: none; font-weight: 700; }" % GRAD,
    "detect_ok": "font-size: 13px; color: #5BD69B; font-weight: 600;",
    "detect_bad": "font-size: 13px; color: #FF7A7A;",
    "label": "font-size: 12px; color: #8A8A94; font-weight: 600; letter-spacing: 1px;",
    "path": "font-size: 12px; color: #B8B8C0; background: #1B1B20; border-radius: 8px; padding: 8px 10px;",
    "big_icon": "font-size: 54px;",
    "pct": "font-size: 34px; font-weight: 800; color: #FFFFFF;",
    "bar": "QSlider::groove:horizontal { height: 10px; border-radius: 5px; background: #26262C; }"
           "QSlider::sub-page:horizontal { border-radius: 5px; background: %s; }"
           "QSlider::add-page:horizontal { border-radius: 5px; background: #26262C; }"
           "QSlider::handle:horizontal { width: 0px; margin: 0px; background: transparent; }" % GRAD,
    "detail": "font-size: 13px; color: #A8A8B2;",
    "hint": "font-size: 12px; color: #8A8A94;",
    "folder": "QPushButton { font-size: 12px; color: #8A8A94; background: transparent; border: none;"
              "padding: 0px; } QPushButton:hover { color: #FFFFFF; }",
    "foot": "font-size: 12px; color: #6E6E78;",
    "insta": "QPushButton { font-size: 12px; font-weight: 700; color: %s; background: transparent;"
             "border: none; padding: 0px; } QPushButton:hover { color: %s; text-decoration: underline; }"
             % (ACCENT, ACCENT2),
    "update": "QPushButton { font-size: 12px; font-weight: 700; color: #0F1F17; background: #5BD69B;"
              "border-radius: 8px; padding: 5px 12px; border: none; }",
}


def nav(*buttons):
    return ui.HGroup({"Weight": 0, "Spacing": 10}, [ui.HGap(0, 1)] + list(buttons))


def card(id_, title, desc, group):
    return ui.Button({"ID": id_, "Text": "%s\n%s" % (title, desc), "Checkable": True,
                      "StyleSheet": CSS[group], "Weight": 1})


settings = load_settings()

header = ui.HGroup({"Weight": 0, "Spacing": 8}, [
    ui.Label({"Text": "⬇  LINKDROP", "StyleSheet": CSS["brand"], "Weight": 0}),
    ui.HGap(0, 1),
] + [ui.Label({"ID": "Step%d" % i, "Text": "%d  %s" % (i + 1, n), "Weight": 0}) for i, n in enumerate(STEPS)])

page_link = ui.VGroup({"Spacing": 10}, [
    ui.Label({"Text": "Paste a video link", "StyleSheet": CSS["title"], "Weight": 0}),
    ui.Label({"Text": "YouTube, Instagram, TikTok, X, Vimeo, Facebook and 1,000+ other sites.",
              "StyleSheet": CSS["sub"], "Weight": 0}),
    ui.VGap(6, 0),
    ui.LineEdit({"ID": "Url", "PlaceholderText": "https://", "StyleSheet": CSS["url"], "Weight": 0}),
    ui.HGroup({"Weight": 0, "Spacing": 12}, [
        ui.Button({"ID": "Paste", "Text": "📋  Paste copied link", "StyleSheet": CSS["ghost"], "Weight": 0}),
        ui.Label({"ID": "Detect", "Text": "", "WordWrap": True, "Weight": 1}),
    ]),
    ui.VGap(0, 1),
    nav(ui.Button({"ID": "Next1", "Text": "Next  →", "StyleSheet": CSS["primary"]})),
])

page_options = ui.VGroup({"Spacing": 8}, [
    ui.Label({"Text": "What do you want?", "StyleSheet": CSS["title"], "Weight": 0}),
    ui.Label({"ID": "LinkEcho", "Text": "", "StyleSheet": CSS["sub"], "Weight": 0}),
    ui.VGap(4, 0),
    ui.HGroup({"Weight": 0, "Spacing": 10}, [card("Mode%d" % i, t, d, "card") for i, (t, d) in enumerate(MODE_CARDS)]),
    ui.VGap(4, 0),
    ui.Label({"ID": "QualityLabel", "Text": "MAX QUALITY", "StyleSheet": CSS["label"], "Weight": 0}),
    ui.HGroup({"Weight": 0, "Spacing": 6},
              [ui.Button({"ID": "Q%d" % i, "Text": q, "Checkable": True, "StyleSheet": CSS["seg"], "Weight": 1})
               for i, q in enumerate(QUALITY_SHORT)]),
    ui.VGap(4, 0),
    ui.Label({"Text": "PLACE IT AT", "StyleSheet": CSS["label"], "Weight": 0}),
    ui.HGroup({"Weight": 0, "Spacing": 6},
              [ui.Button({"ID": "T%d" % i, "Text": t, "Checkable": True, "StyleSheet": CSS["seg"], "Weight": 1})
               for i, t in enumerate(TARGET_CARDS)]),
    ui.Label({"ID": "TargetHint", "Text": "", "StyleSheet": CSS["hint"], "Weight": 0}),
    ui.VGap(0, 1),
    nav(ui.Button({"ID": "Back2", "Text": "←  Back", "StyleSheet": CSS["ghost"]}),
        ui.Button({"ID": "Go", "Text": "⬇  Download && place", "StyleSheet": CSS["primary"]})),
])

page_progress = ui.VGroup({"Spacing": 8}, [
    ui.VGap(0, 1),
    ui.Label({"ID": "BigIcon", "Text": "⬇", "Alignment": {"AlignHCenter": True}, "StyleSheet": CSS["big_icon"], "Weight": 0}),
    ui.Label({"ID": "ProgTitle", "Text": "Downloading...", "Alignment": {"AlignHCenter": True},
              "StyleSheet": CSS["title"], "Weight": 0}),
    ui.Label({"ID": "Pct", "Text": "0%", "Alignment": {"AlignHCenter": True}, "StyleSheet": CSS["pct"], "Weight": 0}),
    ui.Slider({"ID": "Bar", "Minimum": 0, "Maximum": 1000, "Value": 0, "Enabled": False,
               "StyleSheet": CSS["bar"], "Weight": 0}),
    ui.Label({"ID": "Detail", "Text": "", "Alignment": {"AlignHCenter": True}, "WordWrap": True,
              "StyleSheet": CSS["detail"], "MinimumSize": [0, 54], "Weight": 0}),
    ui.VGap(0, 1),
    nav(ui.Button({"ID": "Cancel", "Text": "Cancel", "StyleSheet": CSS["ghost"]}),
        ui.Button({"ID": "Reveal", "Text": "Show in Finder", "StyleSheet": CSS["ghost"], "Visible": False}),
        ui.Button({"ID": "Again", "Text": "↻  Download another", "StyleSheet": CSS["primary"], "Visible": False})),
])

footer = ui.HGroup({"Weight": 0, "Spacing": 6}, [
    ui.Label({"Text": "LinkDrop v%s  ·  made by" % VERSION, "StyleSheet": CSS["foot"], "Weight": 0}),
    ui.Button({"ID": "Insta", "Text": "@gabrielxreis_", "Flat": True, "StyleSheet": CSS["insta"],
               "ToolTip": INSTAGRAM_URL, "Weight": 0}),
    ui.HGap(0, 1),
    ui.Button({"ID": "Browse", "Text": "", "Flat": True, "StyleSheet": CSS["folder"], "Weight": 0,
               "ToolTip": "Where downloads are saved. Click to change."}),
    ui.Button({"ID": "Update", "Text": "", "Visible": False, "StyleSheet": CSS["update"], "Weight": 0}),
])

win = disp.AddWindow({
    "ID": "LinkDropWin",
    "WindowTitle": "LinkDrop",
    "Geometry": [300, 160, 680, 520],
    "StyleSheet": "QWidget#LinkDropWin { background: #17171B; }",
}, ui.VGroup({"Spacing": 14}, [
    header,
    ui.Stack({"ID": "Pages", "Weight": 1}, [page_link, page_options, page_progress]),
    footer,
]))

itm = win.GetItems()
state = {"job": None, "mode": int(settings.get("mode", 0)), "quality": int(settings.get("quality", 2)),
         "target": int(settings.get("target", 0)), "file": None,
         "folder": settings.get("folder") or DEFAULT_FOLDER,
         "update": None, "update_checked": False}
timer = ui.Timer({"ID": "Poll", "Interval": 200})


def platform_of(url):
    low = url.lower()
    for key, name in PLATFORMS:
        if key in low:
            return name
    return "Link"


def drm_site(url):
    low = url.lower()
    for key, name in DRM_SITES:
        if key in low:
            return name
    return None


def go_page(n):
    itm["Pages"].CurrentIndex = n
    for i in range(len(STEPS)):
        key = "step_on" if i == n else ("step_done" if i < n else "step_off")
        itm["Step%d" % i].StyleSheet = CSS[key]


def current_url():
    m = URL_RE.search(itm["Url"].Text or "")
    return m.group(0) if m else ""


def refresh_detect(ev=None):
    url = current_url()
    drm = drm_site(url) if url else None
    if drm:
        itm["Detect"].Text = "%s is DRM-protected and can't be downloaded. Try YouTube Music or SoundCloud." % drm
        itm["Detect"].StyleSheet = CSS["detect_bad"]
        itm["Next1"].Enabled = False
        return
    if url:
        itm["Detect"].Text = "✓  %s link detected" % platform_of(url)
        itm["Detect"].StyleSheet = CSS["detect_ok"]
    elif itm["Url"].Text:
        itm["Detect"].Text = "That does not look like a link (it should start with http)"
        itm["Detect"].StyleSheet = CSS["detect_bad"]
    else:
        itm["Detect"].Text = ""
    itm["Next1"].Enabled = bool(url)


def select(prefix, count, idx):
    for i in range(count):
        itm["%s%d" % (prefix, i)].Checked = (i == idx)


def refresh_choices():
    select("Mode", len(MODE_CARDS), state["mode"])
    select("Q", len(QUALITY_SHORT), state["quality"])
    select("T", len(TARGET_CARDS), state["target"])
    itm["TargetHint"].Text = TARGET_HINTS[state["target"]]
    audio = state["mode"] == 1
    itm["QualityLabel"].Visible = not audio
    for i in range(len(QUALITY_SHORT)):
        itm["Q%d" % i].Visible = not audio


def save_all():
    save_settings({"folder": state["folder"], "mode": state["mode"],
                   "quality": state["quality"], "target": state["target"]})


def make_choice(prefix, key, idx):
    def handler(ev):
        state[key] = idx
        refresh_choices()
    return handler


def show_progress(kind):
    """kind: running | ok | error"""
    itm["Cancel"].Visible = kind == "running"
    itm["Again"].Visible = kind != "running"
    itm["Reveal"].Visible = kind == "ok"
    itm["Pct"].Visible = kind == "running"
    itm["Bar"].Visible = kind != "error"
    itm["BigIcon"].Text = {"running": "⬇", "ok": "✅", "error": "⚠️"}[kind]


def on_close(ev):
    job = state["job"]
    if job and not job.done:
        job.cancel()
    timer.Stop()
    save_all()
    disp.ExitLoop()


def on_paste(ev):
    url = clipboard_url()
    if url:
        itm["Url"].Text = url
    else:
        itm["Detect"].Text = "No link found on your clipboard"
        itm["Detect"].StyleSheet = CSS["detect_bad"]
    refresh_detect()


def on_next1(ev):
    url = current_url()
    if not url or drm_site(url):
        refresh_detect()
        go_page(0)
        return
    if platform_of(url) in MUSIC_SITES:
        state["mode"] = 1
        refresh_choices()
    itm["LinkEcho"].Text = "%s  ·  %s" % (platform_of(url), url if len(url) < 60 else url[:57] + "...")
    go_page(1)


def refresh_folder():
    home = os.path.expanduser("~")
    path = state["folder"]
    itm["Browse"].Text = "📁  " + ("~" + path[len(home):] if path.startswith(home) else path)


def on_browse(ev):
    d = fusion.RequestDir(state["folder"])
    if d:
        state["folder"] = str(d).rstrip("/")
        refresh_folder()
        save_all()


def on_go(ev):
    url = current_url()
    if not url:
        go_page(0)
        return
    save_all()
    job = Job(url, state["folder"], state["mode"] == 1,
              QUALITY_H[state["quality"]])
    state["job"] = job
    itm["Bar"].Value = 0
    itm["Pct"].Text = "0%"
    itm["ProgTitle"].Text = "Downloading from %s" % platform_of(url)
    itm["Detail"].Text = "Connecting..."
    show_progress("running")
    go_page(2)
    job.start()
    timer.Start()


def on_cancel(ev):
    if state["job"]:
        state["job"].cancel()
        itm["Detail"].Text = "Cancelling..."


def on_again(ev):
    itm["Url"].Text = ""
    refresh_detect()
    go_page(0)


def on_reveal(ev):
    if state["file"]:
        subprocess.Popen(["open", "-R", state["file"]])


def on_insta(ev):
    subprocess.Popen(["open", INSTAGRAM_URL])


def check_update_bg():
    v, code = fetch_latest()
    if v and code and version_tuple(v) > version_tuple(VERSION):
        state["update"] = (v, code)
    state["update_checked"] = True


def on_update(ev):
    upd = state["update"]
    if not upd:
        return
    try:
        install_update(upd[1])
        itm["Update"].Text = "✓ v%s installed: close and reopen LinkDrop" % upd[0]
        itm["Update"].Enabled = False
    except Exception as e:
        itm["Update"].Text = "Update failed: %s" % e


def on_timer(ev):
    if state["update"] and itm["Update"].Text == "":
        itm["Update"].Text = "⬆  Update to v%s" % state["update"][0]
        itm["Update"].Visible = True
    job = state["job"]
    if not job:
        if state["update_checked"]:
            timer.Stop()
        return
    itm["Bar"].Value = int(job.percent * 10)
    itm["Pct"].Text = "%d%%" % int(job.percent)
    itm["Detail"].Text = job.status
    if not job.done:
        return
    state["job"] = None
    if job.error:
        show_progress("error")
        itm["ProgTitle"].Text = "Cancelled" if job.cancelled else "Download failed"
        itm["Detail"].Text = job.error
        return
    itm["ProgTitle"].Text = "Placing it in DaVinci..."
    try:
        msg = place_in_resolve(job.path, state["target"])
        state["file"] = job.path
        show_progress("ok")
        itm["Bar"].Value = 1000
        itm["ProgTitle"].Text = "All set!"
        itm["Detail"].Text = "%s\n%s" % (msg, os.path.basename(job.path))
    except Exception as e:
        state["file"] = job.path
        show_progress("error")
        itm["Reveal"].Visible = True
        itm["ProgTitle"].Text = "Downloaded, but Resolve had a problem"
        itm["Detail"].Text = "%s\n%s" % (e, job.path)


win.On.LinkDropWin.Close = on_close
win.On.Url.TextChanged = refresh_detect
win.On.Url.ReturnPressed = on_next1
win.On.Paste.Clicked = on_paste
win.On.Next1.Clicked = on_next1
win.On.Back2.Clicked = lambda ev: go_page(0)
win.On.Browse.Clicked = on_browse
win.On.Go.Clicked = on_go
win.On.Cancel.Clicked = on_cancel
win.On.Again.Clicked = on_again
win.On.Reveal.Clicked = on_reveal
win.On.Insta.Clicked = on_insta
win.On.Update.Clicked = on_update
for _i in range(len(MODE_CARDS)):
    win.On["Mode%d" % _i].Clicked = make_choice("Mode", "mode", _i)
for _i in range(len(QUALITY_SHORT)):
    win.On["Q%d" % _i].Clicked = make_choice("Q", "quality", _i)
for _i in range(len(TARGET_CARDS)):
    win.On["T%d" % _i].Clicked = make_choice("T", "target", _i)
disp.On.Timeout = on_timer

itm["Url"].Text = clipboard_url()
refresh_detect()
refresh_choices()
refresh_folder()
if current_url():
    on_next1(None)  # link already copied: skip straight to the options
else:
    go_page(0)

win.Show()
threading.Thread(target=check_update_bg, daemon=True).start()
timer.Start()
disp.RunLoop()
win.Hide()
