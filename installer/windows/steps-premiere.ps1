# LinkDrop for Premiere Pro: installer steps for Windows (run by the installer .exe one step at a time).
# The panel comes from GitHub (premiere/ on main); yt-dlp, ffmpeg and deno are the same as steps.ps1 (no Python needed).
# Usage: powershell -ExecutionPolicy Bypass -File steps-premiere.ps1 <step>
#   steps: detect | clean | ytdlp | ffmpeg | deno | script
# by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop
param([string]$Step)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoZip = "https://codeload.github.com/gabrielxreis/LinkDrop/zip/refs/heads/main"
$Data    = Join-Path $env:APPDATA "LinkDrop"
$Bin     = Join-Path $Data "bin"
$Ext     = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.gabrielxreis.linkdrop"
$Tmp     = Join-Path $env:TEMP "linkdrop-premiere-install"

function Fail($msg) { [Console]::Error.WriteLine($msg); exit 1 }

try {
    New-Item -ItemType Directory -Force -Path $Bin, $Tmp | Out-Null
} catch { Fail "Couldn't create the LinkDrop folders: $($_.Exception.Message)" }

switch ($Step) {
    "detect" {
        $f = Join-Path $Ext "CSXS\manifest.xml"
        if (Test-Path $f) {
            $m = Select-String -Path $f -Pattern 'ExtensionBundleVersion="([^"]*)"' | Select-Object -First 1
            if ($m) { Write-Output $m.Matches[0].Groups[1].Value }
        }
        exit 0
    }
    "clean" {
        # removes the installed panel and the shared tools/caches; keeps the license and settings
        foreach ($p in @($Ext, $Bin, (Join-Path $Data "thumbs"), (Join-Path $Data "logs"), (Join-Path $Data "ytdlp-updated"))) {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
        }
        New-Item -ItemType Directory -Force -Path $Bin | Out-Null
        exit 0
    }
    { $_ -in "ytdlp", "ffmpeg", "deno" } {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "steps.ps1") $Step
        exit $LASTEXITCODE
    }
    "script" {
        # always the latest panel from GitHub; the copy inside the installer is only an offline fallback
        $src = $null
        try {
            $zip = Join-Path $Tmp "repo.zip"
            Invoke-WebRequest -Uri $RepoZip -OutFile $zip -UseBasicParsing
            $dir = Join-Path $Tmp "repo"
            Expand-Archive -Path $zip -DestinationPath $dir -Force
            $cand = Get-ChildItem -Path $dir -Directory | ForEach-Object { Join-Path $_.FullName "premiere" } | Select-Object -First 1
            if ($cand -and (Test-Path (Join-Path $cand "CSXS\manifest.xml"))) { $src = $cand }
        } catch { $src = $null }
        if (-not $src) {
            $local = Join-Path $PSScriptRoot "premiere"
            if (Test-Path (Join-Path $local "CSXS\manifest.xml")) { $src = $local }
            else { Fail "Couldn't download LinkDrop from GitHub. Check your internet connection and try again." }
        }
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path $Ext) | Out-Null
            if (Test-Path $Ext) { Remove-Item -Recurse -Force $Ext }
            Copy-Item $src $Ext -Recurse -Force
            $dbg = Join-Path $Ext ".debug"
            if (Test-Path $dbg) { Remove-Item -Force $dbg }
        } catch { Fail "Couldn't copy LinkDrop into Premiere's extensions folder: $($_.Exception.Message)" }
        # Premiere loads panels that aren't from the Adobe Exchange only with this switch (one per CEP version)
        foreach ($v in 9..14) {
            $k = "HKCU:\Software\Adobe\CSXS.$v"
            New-Item -Path $k -Force | Out-Null
            Set-ItemProperty -Path $k -Name "PlayerDebugMode" -Value "1" -Type String
        }
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
        exit 0
    }
    default { Fail "Unknown step: $Step" }
}
