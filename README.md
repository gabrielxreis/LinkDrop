# ⬇ LinkDrop for DaVinci Resolve

Paste a link, get the clip on your timeline.

LinkDrop is a DaVinci Resolve script that downloads a video (or just the audio) from a link and drops it right where you need it: at the playhead, at the end of the timeline, in a new timeline or only in the Media Pool.

## Features

- **Reads your clipboard.** Copy a link, open LinkDrop and it's already there.
- **Video + audio (H.264 MP4)** or **audio only (lossless WAV)**.
- **Max quality picker:** Best, 4K, 1080p, 720p.
- **Smart placement.** At the playhead it uses a free track, so nothing on your timeline gets overwritten.
- **Resolve-friendly codecs.** Picks H.264/AAC and converts anything Resolve can't decode.
- **Organized.** Every download goes into a `Downloads` bin in the Media Pool.
- **Auto-updates from GitHub.** Every time you open LinkDrop it checks this repo and, if there is a newer version, installs it and opens it right away.

## Supported sites

YouTube, YouTube Music, Instagram, TikTok, X/Twitter, Vimeo, Facebook, SoundCloud, Twitch, public Google Drive/Dropbox links and [1,000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

DRM-protected services (Spotify, Apple Music, Deezer, Tidal, Netflix...) **can't** be downloaded.

## Install (macOS)

1. [Download this repo as ZIP](https://github.com/gabrielxreis/LinkDrop/archive/refs/heads/main.zip) and unzip it.
2. Double-click **`Install LinkDrop.command`** (if macOS blocks it: right-click > Open).
3. In DaVinci Resolve: **Workspace > Scripts > Utility > LinkDrop**.

The installer sets up `yt-dlp`, `ffmpeg` and `deno` with Homebrew, then copies `LinkDrop.py` to:

```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
```

## Troubleshooting

- **A site stopped working:** sites change often, so update the downloader with `brew upgrade yt-dlp`.
- **LinkDrop doesn't show up in the Scripts menu:** restart Resolve after installing.
- **Window doesn't open:** script windows need DaVinci Resolve Studio in recent versions.

## Releasing an update

Bump `VERSION` in `LinkDrop.py` and push to `main`. Every LinkDrop updates itself the next time it opens (offline it just opens the installed version).

---

Use it only for content you have the rights to.

Made by [@gabrielxreis_](https://instagram.com/gabrielxreis_) · [gabrielxreis.com](https://gabrielxreis.com)
