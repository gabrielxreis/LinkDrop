; LinkDrop for Premiere Pro installer for Windows (NSIS). Installs the panel for the current user, no admin needed.
; Build on macOS:  makensis -DVERSION=x.y.z LinkDrop-Premiere.nsi
; by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop

Unicode true
!ifndef VERSION
  !define VERSION "1.0.0"
!endif

Name "LinkDrop for Premiere"
Caption "Install LinkDrop for Premiere"
OutFile "..\LinkDrop-Premiere-Installer-windows.exe"
RequestExecutionLevel user
InstallDir "$APPDATA\LinkDrop"
SetCompressor /SOLID lzma
ShowInstDetails nevershow
BrandingText "LinkDrop for Premiere ${VERSION}  -  @gabrielxreis_"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "LinkDrop for Premiere"
VIAddVersionKey "FileDescription" "LinkDrop installer for Adobe Premiere Pro"
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
!define MUI_WELCOMEPAGE_TITLE "Install LinkDrop for Premiere"
!define MUI_WELCOMEPAGE_TEXT "Download links straight into Premiere Pro.$\r$\n$\r$\nLinkDrop will be added to Premiere Pro with everything it needs. Any previous version is replaced. Your settings are kept.$\r$\n$\r$\nClick Install to continue."
!define MUI_FINISHPAGE_TITLE "LinkDrop for Premiere is installed"
!define MUI_FINISHPAGE_TEXT "In Premiere Pro, open Window > Extensions > LinkDrop.$\r$\nIf Premiere was open, restart it once."
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
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\steps-premiere.ps1" ${name}'
  Pop $0
  Pop $1
  SetDetailsPrint both
  StrCmp $0 "0" +3 0
    MessageBox MB_ICONSTOP|MB_OK "LinkDrop couldn't finish installing.$\r$\n$\r$\n$1"
    Abort
!macroend

Section "LinkDrop for Premiere" SecMain
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "steps.ps1"
  File "steps-premiere.ps1"
  File /r /x .debug /x .DS_Store "..\..\premiere"

  !insertmacro STEP "detect" "Preparing..."
  StrCmp $1 "" +2 0
    DetailPrint "Preparing..."
  !insertmacro STEP "clean" "Preparing..."
  !insertmacro STEP "ytdlp" "Installing components..."
  !insertmacro STEP "ffmpeg" "Installing components..."
  !insertmacro STEP "deno" "Installing components..."
  !insertmacro STEP "script" "Adding LinkDrop to Premiere Pro..."
  DetailPrint "Done."
SectionEnd
