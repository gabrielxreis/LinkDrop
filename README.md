# LinkDrop for DaVinci Resolve and Premiere Pro

Paste a link, get the clip on your timeline.

LinkDrop is a DaVinci Resolve script that downloads videos or audio from links and drops them right where you need them: at the playhead, at the end of the timeline, in a new timeline or only in the Media Pool. Works on macOS and Windows.

**LinkDrop for Premiere Pro** is a panel with the same flow for Adobe Premiere Pro (2024 or newer). It has its own license, separate from the DaVinci Resolve one.

## How it works

1. **Add links.** Paste one or more links, one per line. The box grows as you add more. A link you copied before opening LinkDrop is already there.
2. **Analyzing links.** LinkDrop reads each link's real title, duration, thumbnail and available resolutions. A broken link never blocks the rest.
3. **Choose format.** Audio: MP3, WAV or AAC, with quality, Keep metadata and Embed artwork. Video: H.264 (MP4) or ProRes 422 (MOV), with only the resolutions the sources really offer.
4. **Review items.** Pick what to download, switch single items between video and audio, see the estimated size, then **Start Download**.
5. **Downloading.** Real progress for every file. **Run in Background** shrinks LinkDrop to a small progress window.
6. **All done** or **Some files need attention**, with Open Folder, Copy Report, Report Issue and Retry Failed.

**Settings** (gear button on the first screen, saved for next time): download folder, original or custom file names, import into the current Resolve bin, subfolders (with an optional fixed name), reveal when finished, where the clip goes in the timeline, and a keyboard shortcut to open LinkDrop (Mac, default Ctrl + Shift + +; restart Resolve after changing it).

## Supported sites

YouTube, YouTube Music, Instagram, TikTok, X, Facebook, Vimeo, SoundCloud, Twitch and [1,000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

**Spotify, Apple Music and Deezer:** their audio is DRM-protected, so LinkDrop reads the song and artist from the link, finds the track on YouTube and downloads that audio. Use links to single songs.

Video services with DRM (Netflix, Prime Video, Disney+, Max) can't be downloaded.

**YouTube asking to confirm you're not a bot:** LinkDrop then uses the YouTube login of a browser on your computer (Chrome, Brave, Edge, Firefox or Safari) and remembers the one that worked. Be signed in to YouTube in one of them. On Windows, use Firefox (Chrome and Edge protect their logins there).

## License

LinkDrop needs a license key, entered once per computer.

- **Free trial:** 24 hours. Get a trial key at [linkdrop.com.br](https://linkdrop.com.br/account) (one trial per computer).
- **License:** R$ 19,90 per year, one computer per key. To move it, deactivate it in LinkDrop (Settings) or revoke the computer on your account page.
- LinkDrop checks the license each time it opens and keeps working offline for up to 7 days.

**Requires DaVinci Resolve Studio** for the LinkDrop window. On Windows, Python 3 must be installed for all users (the installer handles it).

## Install

| | Download |
|---|---|
| **macOS** | [LinkDrop-Installer-mac.dmg](https://github.com/gabrielxreis/LinkDrop/releases/latest/download/LinkDrop-Installer-mac.dmg) |
| **Windows** | [LinkDrop-Installer-windows.exe](https://github.com/gabrielxreis/LinkDrop/releases/latest/download/LinkDrop-Installer-windows.exe) |
| **Premiere Pro, macOS** | [LinkDrop-Premiere-Installer-mac.dmg](https://github.com/gabrielxreis/LinkDrop/releases/latest/download/LinkDrop-Premiere-Installer-mac.dmg) |
| **Premiere Pro, Windows** | [LinkDrop-Premiere-Installer-windows.exe](https://github.com/gabrielxreis/LinkDrop/releases/latest/download/LinkDrop-Premiere-Installer-windows.exe) |

- **macOS:** open the DMG and double-click **Install LinkDrop**. The first time, macOS may block it: right-click the app and choose **Open**.
- **Windows:** run the installer. If SmartScreen appears, click **More info > Run anyway**.

Then in DaVinci Resolve: **Workspace > Scripts > LinkDrop**, or press **Ctrl + Shift + +** on a Mac (after restarting Resolve once). In Premiere Pro: **Window > Extensions > LinkDrop** (restart Premiere once after installing).

The installer removes any previous version (your settings are kept) and downloads everything fresh, including the latest LinkDrop from this repository. No admin rights needed, except on a Mac that doesn't have Python 3 yet.

## Updates

- **LinkDrop updates itself** every time it opens: it checks this repo, installs the newer version and opens it right away. Offline, it opens the installed version. The Premiere panel does the same with the `premiere/` folder.
- **The downloader (yt-dlp) updates itself** once a day in the background, so sites keep working.

## Design

Premium dark glass: deep black with electric-blue light on the edges, emerald for success and rose for errors. Motion follows Apple's Human Interface Guidelines: short spring-based transitions, real progress only, completion confirmed with a symbol and a system sound, and **Reduce Motion** respected on macOS and Windows.

## Troubleshooting

- **LinkDrop isn't in the Scripts menu:** restart Resolve after installing.
- **LinkDrop isn't in the menu on Windows:** DaVinci needs Python 3 installed for all users; run the installer again.
- **The window doesn't open:** recent versions of Resolve need DaVinci Resolve Studio for script windows.
- **A site stopped working:** run the installer again to get the latest tools.

## Releasing an update

Bump `VERSION` in `LinkDrop.py` and push to `main`. Keep the file ASCII-only (use `\u` escapes) so older versions can always read the update.

Premiere panel: bump `ExtensionBundleVersion` in `premiere/CSXS/manifest.xml` (and `VERSION` in `premiere/js/main.js`) and push to `main`.

To refresh the installers: `installer/build-mac.sh`, then `LANG=en_US.UTF-8 makensis -DVERSION=x.y.z installer/windows/LinkDrop.nsi`, then attach both files to a new GitHub release (`gh release create vX.Y.Z installer/LinkDrop-Installer-*`). Premiere installers: `installer/build-mac.sh premiere` and `makensis -DVERSION=x.y.z installer/windows/LinkDrop-Premiere.nsi`.

---

Use LinkDrop only for content you have the rights to.

Made by [@gabrielxreis_](https://instagram.com/gabrielxreis_) · [gabrielxreis.com](https://gabrielxreis.com)
