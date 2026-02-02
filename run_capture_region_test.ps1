param(
  [string]$Hwnd = "",
  [string]$Title = "",
  [int]$WaitSeconds = 60,
  [switch]$Focus,
  [switch]$Clamp,
  [switch]$TryAllOutputs,
  [int]$MaxOutputs = 6,
  [string]$ConfigPath = ".\\rois_15y.json"
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Py)) {
  Write-Host "ERROR: .venv Python not found at $Py"
  Write-Host "Run: poetry install"
  exit 2
}

$env:FRBOT_CAPTURE_BACKEND = 'meld'
$env:FRBOT_CONFIG_PATH = $ConfigPath

if ($Hwnd -ne "") {
  $h = $Hwnd.Trim()
  if ($h.ToLower() -eq '0xyourhwnd' -or $h.ToLower() -eq 'yourhwnd') {
    Write-Host "ERROR: Replace -Hwnd 0xYOURHWND with the real Tibia HWND." -ForegroundColor Red
    Write-Host "Tip: powershell -NoProfile -ExecutionPolicy Bypass -File .\run_capture_test.ps1 -ListWindows -Filter tibia" -ForegroundColor Yellow
    exit 2
  }
  $env:FRBOT_WINDOW_HWND = $h
}
if ($Title -ne "") { $env:FRBOT_WINDOW_TITLE_SUBSTRING = $Title }
if ($Clamp) { $env:FRBOT_CAPTURE_CLAMP = '1' }

$argsList = @('--wait-seconds', $WaitSeconds)
if ($Focus) { $argsList += '--focus' }
if ($TryAllOutputs) { $argsList += '--try-all-outputs'; $argsList += '--max-outputs'; $argsList += $MaxOutputs }

& $Py (Join-Path $RepoRoot 'test_capture_region_real.py') @argsList
exit $LASTEXITCODE
