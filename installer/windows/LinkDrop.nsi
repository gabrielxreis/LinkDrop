; LinkDrop installer for Windows (NSIS). Installs into the current user's DaVinci Resolve, no admin needed.
; Build on macOS:  makensis -DVERSION=x.y.z LinkDrop.nsi
; by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop

Unicode true
!ifndef VERSION
  !define VERSION "2.4.0"
!endif

Name "LinkDrop"
Caption "Install LinkDrop"
OutFile "..\LinkDrop-Installer-windows.exe"
RequestExecutionLevel user
InstallDir "$APPDATA\LinkDrop"
SetCompressor /SOLID lzma
ShowInstDetails nevershow
BrandingText "LinkDrop ${VERSION}  -  @gabrielxreis_"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "LinkDrop"
VIAddVersionKey "FileDescription" "LinkDrop installer for DaVinci Resolve"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "@gabrielxreis_"

!include "MUI2.nsh"
!define MUI_ICON "LinkDrop.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install LinkDrop"
!define MUI_WELCOMEPAGE_TEXT "Download links straight into DaVinci Resolve.$\r$\n$\r$\nLinkDrop will be added to DaVinci Resolve with everything it needs. Any previous version is replaced. Your settings are kept.$\r$\n$\r$\nClick Install to continue."
!define MUI_FINISHPAGE_TITLE "LinkDrop is installed"
!define MUI_FINISHPAGE_TEXT "In DaVinci Resolve, open Workspace > Scripts > LinkDrop.$\r$\nIf Resolve was open, restart it once.$\r$\n$\r$\nLinkDrop updates itself every time it opens."
!define MUI_FINISHPAGE_LINK "Follow @gabrielxreis_ on Instagram"
!define MUI_FINISHPAGE_LINK_LOCATION "https://instagram.com/gabrielxreis_"
!define MUI_INSTFILESPAGE_COLORS "FFFFFF 0B1225"

!define MUI_WELCOMEPAGE_TITLE_3LINES
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

; runs one step of steps.ps1; stops with the step's error message if it fails
!macro STEP name label
  DetailPrint "${label}"
  SetDetailsPrint none
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\steps.ps1" ${name}'
  Pop $0
  Pop $1
  SetDetailsPrint both
  StrCmp $0 "0" +3 0
    MessageBox MB_ICONSTOP|MB_OK "LinkDrop couldn't finish installing.$\r$\n$\r$\n$1"
    Abort
!macroend

Section "LinkDrop" SecMain
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "steps.ps1"
  File "..\..\LinkDrop.py"

  !insertmacro STEP "detect" "Preparing..."
  StrCmp $1 "" +2 0
    DetailPrint "Preparing..."
  !insertmacro STEP "clean" "Preparing..."
  !insertmacro STEP "python" "Installing components..."
  !insertmacro STEP "ytdlp" "Installing components..."
  !insertmacro STEP "ffmpeg" "Installing components..."
  !insertmacro STEP "deno" "Installing components..."
  !insertmacro STEP "script" "Adding LinkDrop to DaVinci Resolve..."
  DetailPrint "Done."
SectionEnd
