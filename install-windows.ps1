# LinkDrop installer for DaVinci Resolve on Windows
# by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop
#
# Installs everything LinkDrop needs, no admin rights required:
#   %APPDATA%\LinkDrop\bin          -> yt-dlp.exe, ffmpeg.exe, ffprobe.exe, deno.exe
#   Resolve's Scripts\Utility folder -> LinkDrop.py
#   Python 3 (per user), if missing  -> Resolve needs it to run .py scripts

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRaw = "https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main"
$Data    = Join-Path $env:APPDATA "LinkDrop"
$Bin     = Join-Path $Data "bin"
$Util    = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
$PyExe   = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$Tmp     = Join-Path $env:TEMP ("LinkDrop-" + [guid]::NewGuid().ToString("N"))

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "  $msg" }
function Fail($msg) {
    Write-Host ""
    Write-Host "  [X] $msg" -ForegroundColor Red
    Write-Host ""
    Read-Host "  Press Enter to close" | Out-Null
    exit 1
}
function Get-File($url, $out) {
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
        catch { Start-Sleep -Seconds 2 }
    }
    Fail "Couldn't download $url. Check your internet connection and try again."
}
function Test-Python {
    foreach ($cmd in @("py", "python", "python3")) {
        try {
            $v = & $cmd --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3") { return $true }
        } catch { }
    }
    foreach ($root in @("HKCU:\Software\Python\PythonCore", "HKLM:\Software\Python\PythonCore")) {
        if (Test-Path $root) {
            if (Get-ChildItem $root | Where-Object { $_.PSChildName -like "3.*" }) { return $true }
        }
    }
    return $false
}

Clear-Host
Write-Host ""
Write-Host "  LinkDrop for DaVinci Resolve" -ForegroundColor White
Write-Host "  Paste a link, get the clip on your timeline."
Write-Host ""

try {
    New-Item -ItemType Directory -Force -Path $Bin, $Util, $Tmp | Out-Null
} catch { Fail "Couldn't create the LinkDrop folders: $($_.Exception.Message)" }

# 1. Python 3 (Resolve needs it to run .py scripts)
if (Test-Python) {
    Ok "Python 3 is installed"
} else {
    Info "Installing Python 3 (Resolve needs it for scripts)..."
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        $installed = Test-Python
    }
    if (-not $installed) {
        $py = Join-Path $Tmp "python.exe"
        Get-File $PyExe $py
        $p = Start-Process -FilePath $py -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Fail "Python wasn't installed (code $($p.ExitCode)). Install it from python.org and run this again." }
    }
    Ok "Python 3 installed"
}

# 2. yt-dlp (downloader)
Info "Downloading yt-dlp..."
Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $Bin "yt-dlp.exe")
Ok ("yt-dlp " + (& (Join-Path $Bin "yt-dlp.exe") --version))

# 3. ffmpeg + ffprobe (merging and converting)
Info "Downloading ffmpeg (about 100 MB)..."
$ffZip = Join-Path $Tmp "ffmpeg.zip"
Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $ffZip
$ffDir = Join-Path $Tmp "ffmpeg"
Expand-Archive -Path $ffZip -DestinationPath $ffDir -Force
foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $found = Get-ChildItem -Path $ffDir -Recurse -Filter $tool | Select-Object -First 1
    if (-not $found) { Fail "Couldn't find $tool in the ffmpeg download." }
    Copy-Item $found.FullName (Join-Path $Bin $tool) -Force
}
Ok "ffmpeg and ffprobe"

# 4. deno (YouTube needs a JavaScript runtime)
Info "Downloading deno..."
$denoZip = Join-Path $Tmp "deno.zip"
Get-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $denoZip
Expand-Archive -Path $denoZip -DestinationPath $Bin -Force
Ok "deno"

# 5. LinkDrop itself
$local = Join-Path $PSScriptRoot "LinkDrop.py"
$dest = Join-Path $Util "LinkDrop.py"
if ($PSScriptRoot -and (Test-Path $local)) { Copy-Item $local $dest -Force }
else { Get-File "$RepoRaw/LinkDrop.py" $dest }
Ok "LinkDrop added to DaVinci Resolve"

Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  All set!" -ForegroundColor White
Write-Host "  In DaVinci Resolve, open:  Workspace > Scripts > LinkDrop"
Write-Host "  (If Resolve was open, restart it once.)"
Write-Host ""
Write-Host "  LinkDrop updates itself every time it opens."
Write-Host "  Made by @gabrielxreis_  -  instagram.com/gabrielxreis_"
Write-Host ""
Read-Host "  Press Enter to close" | Out-Null
