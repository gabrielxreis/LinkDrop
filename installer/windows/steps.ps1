# LinkDrop installer steps for Windows (run by the LinkDrop installer .exe one step at a time).
# Everything is downloaded fresh: LinkDrop itself from GitHub, plus yt-dlp, ffmpeg and deno.
# Usage: powershell -ExecutionPolicy Bypass -File steps.ps1 <step>
#   steps: detect | clean | python | ytdlp | ffmpeg | deno | script
# by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop
param([string]$Step)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRaw = "https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main"
$Data    = Join-Path $env:APPDATA "LinkDrop"
$Bin     = Join-Path $Data "bin"
$Util    = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
$Tmp     = Join-Path $env:TEMP "linkdrop-install"
# Python is mirrored on the LinkDrop GitHub (release "deps"); python.org is the fallback
$PyUrls  = @("https://github.com/gabrielxreis/LinkDrop/releases/download/deps/python-3.12.10-amd64.exe",
             "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe")
$PySha   = "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB"

function Fail($msg) { [Console]::Error.WriteLine($msg); exit 1 }
function Get-File($url, $out) {
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
        catch { Start-Sleep -Seconds 2 }
    }
    Fail "Couldn't download $url. Check your internet connection and try again."
}
function Test-Python {
    # DaVinci Resolve on Windows only lists .py scripts when Python 3 (64-bit) is installed for ALL users
    foreach ($root in @("HKLM:\Software\Python\PythonCore", "HKLM:\Software\WOW6432Node\Python\PythonCore")) {
        if (Test-Path $root) {
            foreach ($v in (Get-ChildItem $root | Where-Object { $_.PSChildName -like "3.*" })) {
                $ip = Join-Path $v.PSPath "InstallPath"
                if (Test-Path $ip) {
                    $dir = (Get-ItemProperty $ip -ErrorAction SilentlyContinue)."(default)"
                    if ($dir -and (Test-Path (Join-Path $dir "python.exe"))) { return $true }
                }
            }
        }
    }
    return $false
}

try {
    New-Item -ItemType Directory -Force -Path $Bin, $Tmp | Out-Null
} catch { Fail "Couldn't create the LinkDrop folders: $($_.Exception.Message)" }

switch ($Step) {
    "detect" {
        $f = Join-Path $Util "LinkDrop.py"
        if (Test-Path $f) {
            $m = Select-String -Path $f -Pattern '^VERSION = "(.*)"' | Select-Object -First 1
            if ($m) { Write-Output $m.Matches[0].Groups[1].Value }
        }
        exit 0
    }
    "clean" {
        # removes the installed LinkDrop and its tools/caches; keeps the person's settings (settings.json)
        foreach ($p in @((Join-Path $Util "LinkDrop.py"), $Bin, (Join-Path $Data "icons"), (Join-Path $Data "thumbs"),
                         (Join-Path $Data "logs"), (Join-Path $Data "ytdlp-updated"), (Join-Path $Data "job.log"))) {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
        }
        New-Item -ItemType Directory -Force -Path $Bin | Out-Null
        exit 0
    }
    "python" {
        if (Test-Python) { exit 0 }
        $py = Join-Path $Tmp "python.exe"
        $got = $false
        foreach ($u in $PyUrls) {
            try {
                Invoke-WebRequest -Uri $u -OutFile $py -UseBasicParsing
                if ((Get-FileHash $py -Algorithm SHA256).Hash -eq $PySha) { $got = $true; break }
            } catch { }
        }
        if (-not $got) { Fail "Couldn't download Python 3. Check your internet connection and try again." }
        # a per-user copy of the same version makes the Python installer silently "repair" it and ignore
        # InstallAllUsers, so remove that copy first (it belongs to this user: no permission needed)
        $userPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (Test-Path $userPy) {
            Start-Process -FilePath $py -ArgumentList "/quiet /uninstall" -Wait | Out-Null
        }
        # all-users install (Windows asks for permission once): Resolve doesn't see per-user Python
        try {
            $p = Start-Process -FilePath $py -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1" -Verb RunAs -Wait -PassThru
        } catch { Fail "Python needs your permission to install. Run the installer again and click Yes when Windows asks." }
        if ($p.ExitCode -ne 0) { Fail "Python wasn't installed (code $($p.ExitCode)). Run the installer again." }
        if (-not (Test-Python)) { Fail "Python was installed but DaVinci Resolve won't see it. Install Python 3 for all users from python.org." }
        exit 0
    }
    "ytdlp" {
        Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $Bin "yt-dlp.exe")
        exit 0
    }
    "ffmpeg" {
        $zip = Join-Path $Tmp "ffmpeg.zip"
        Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
        $dir = Join-Path $Tmp "ffmpeg"
        Expand-Archive -Path $zip -DestinationPath $dir -Force
        foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
            $found = Get-ChildItem -Path $dir -Recurse -Filter $tool | Select-Object -First 1
            if (-not $found) { Fail "Couldn't find $tool in the ffmpeg download." }
            Copy-Item $found.FullName (Join-Path $Bin $tool) -Force
        }
        exit 0
    }
    "deno" {
        $zip = Join-Path $Tmp "deno.zip"
        Get-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $zip
        Expand-Archive -Path $zip -DestinationPath $Bin -Force
        exit 0
    }
    "script" {
        # always the latest LinkDrop from GitHub; the copy inside the installer is only an offline fallback
        New-Item -ItemType Directory -Force -Path $Util | Out-Null
        $dest = Join-Path $Util "LinkDrop.py"
        $dl = Join-Path $Tmp "LinkDrop.py"
        $ok = $false
        try {
            Invoke-WebRequest -Uri ("$RepoRaw/LinkDrop.py?t=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile $dl -UseBasicParsing
            $ok = (Select-String -Path $dl -Pattern '^VERSION = ' -Quiet)
        } catch { $ok = $false }
        if ($ok) { Copy-Item $dl $dest -Force }
        elseif (Test-Path (Join-Path $PSScriptRoot "LinkDrop.py")) { Copy-Item (Join-Path $PSScriptRoot "LinkDrop.py") $dest -Force }
        else { Fail "Couldn't download LinkDrop from GitHub. Check your internet connection and try again." }
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
        exit 0
    }
    default { Fail "Unknown step: $Step" }
}
