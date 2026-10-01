# LinkDrop for DaVinci Resolve

Paste a link, get the clip on your timeline.

LinkDrop is a DaVinci Resolve script that downloads videos or audio from links and drops them right where you need them: at the playhead, at the end of the timeline, in a new timeline or only in the Media Pool. Works on macOS and Windows.

## How it works

1. **Add links.** Paste one or more links, one per line. The box grows as you add more. A link you copied before opening LinkDrop is already there.
2. **Analyzing links.** LinkDrop reads each link's real title, duration, thumbnail and available resolutions. A broken link never blocks the rest.
3. **Choose format.** Audio: MP3, WAV or AAC, with quality, Keep metadata and Embed artwork. Video: H.264 (MP4) or ProRes 422 (MOV), with only the resolutions the sources really offer.
4. **Review items.** Pick what to download, switch single items between video and audio, see the estimated size, then **Start Download**.
5. **Downloading.** Real progress for every file. **Run in Background** shrinks LinkDrop to a small progress window.
6. **All done** or **Some files need attention**, with Open Folder, Copy Report, Report Issue and Retry Failed.

**Settings** (gear button on the first screen, saved for next time): download folder, original or custom file names, import into the current Resolve bin, subfolders (with an optional fixed name), reveal when finished, and where the clip goes in the timeline.

## Supported sites

YouTube, YouTube Music, Instagram, TikTok, X, Facebook, Vimeo, SoundCloud, Twitch and [1,000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

**Spotify, Apple Music and Deezer:** their audio is DRM-protected, so LinkDrop reads the song and artist from the link, finds the track on YouTube and downloads that audio. Use links to single songs.

Video services with DRM (Netflix, Prime Video, Disney+, Max) can't be downloaded.

## Install

### macOS

1. [Download LinkDrop](https://github.com/gabrielxreis/LinkDrop/archive/refs/heads/main.zip) and unzip it.
2. Double-click **`Install LinkDrop.command`**. If macOS blocks it, right-click it and choose **Open**.
3. In DaVinci Resolve: **Workspace > Scripts > LinkDrop**.

### Windows

1. [Download LinkDrop](https://github.com/gabrielxreis/LinkDrop/archive/refs/heads/main.zip) and unzip it.
2. Double-click **`Install LinkDrop (Windows).bat`**. If SmartScreen appears, click **More info > Run anyway**.
3. In DaVinci Resolve: **Workspace > Scripts > LinkDrop**.

The installers don't need Homebrew or admin rights. They put everything LinkDrop uses in its own folder:

| | macOS | Windows |
|---|---|---|
| Tools (yt-dlp, ffmpeg, ffprobe, deno) | `~/Library/Application Support/LinkDrop/bin` | `%APPDATA%\LinkDrop\bin` |
| Script | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility` | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility` |

If Python 3 is missing (Resolve needs it to run scripts), the installer adds it. On macOS that's the only step that asks for your password.

## Updates

- **LinkDrop updates itself** every time it opens: it checks this repo, installs the newer version and opens it right away. Offline, it opens the installed version.
- **The downloader (yt-dlp) updates itself** once a day in the background, so sites keep working.

## Design

Premium dark glass: deep black with electric-blue light on the edges, emerald for success and rose for errors. Motion follows Apple's Human Interface Guidelines: short spring-based transitions, real progress only, completion confirmed with a symbol and a system sound, and **Reduce Motion** respected on macOS and Windows.

## Troubleshooting

- **LinkDrop isn't in the Scripts menu:** restart Resolve after installing.
- **The window doesn't open:** recent versions of Resolve need DaVinci Resolve Studio for script windows.
- **A site stopped working:** run the installer again to get the latest tools.

## Releasing an update

Bump `VERSION` in `LinkDrop.py` and push to `main`. Keep the file ASCII-only (use `\u` escapes) so older versions can always read the update.

---

Use LinkDrop only for content you have the rights to.

Made by [@gabrielxreis_](https://instagram.com/gabrielxreis_) · [gabrielxreis.com](https://gabrielxreis.com)
