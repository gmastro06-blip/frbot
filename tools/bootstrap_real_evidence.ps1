param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('15.x','15.y','15x','15y')]
  [string]$Version,

  # Python executable to use (must have real-capture deps like mss installed).
  [Parameter(Mandatory = $false)]
  [string]$PythonExe = 'python',

  # Optional override paths (absolute). If omitted, defaults are used.
  [string]$OldOutDir = '',
  [string]$OutDir = ''

  ,
  # Optional capture backend override (e.g., 'mss' or 'obs-projector').
  # If omitted, uses existing FRBOT_CAPTURE_BACKEND or the runtime default.
  [Parameter(Mandatory = $false)]
  [string]$CaptureBackend = ''

  ,
  # Optional: enable dxcam output probing (recommended for obs-projector).
  [Parameter(Mandatory = $false)]
  [switch]$TryAllOutputs

  ,
  [Parameter(Mandatory = $false)]
  [int]$MaxOutputs = 6
)

$ErrorActionPreference = 'Stop'

function HardStop([string]$Reason, [hashtable]$Extra = @{}) {
  $payload = @{
    reason = $Reason
    ts = (Get-Date).ToString('o')
  }
  foreach ($k in $Extra.Keys) { $payload[$k] = $Extra[$k] }
  $json = ($payload | ConvertTo-Json -Depth 6)
  Write-Host $json
  exit 2
}

function Invoke-PythonSnippet([string]$Code) {
  $tmp = Join-Path $env:TEMP ("frbot_snippet_{0}.py" -f ([guid]::NewGuid().ToString('n')))
  try {
    $repoForSnippet = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $prefix = "import sys`n" + "sys.path.insert(0, r`"$repoForSnippet`")`n`n"
    Set-Content -LiteralPath $tmp -Value ($prefix + $Code) -Encoding utf8
    # IMPORTANT: In Windows PowerShell 5.1, native stderr can be surfaced as a NativeCommandError
    # record, which (with $ErrorActionPreference='Stop') aborts execution and truncates the
    # original traceback in redirected logs. Execute via cmd.exe so stderr is merged into stdout
    # as plain text and callers can inspect $LASTEXITCODE deterministically.
    $cmd = '"{0}" "{1}" 2>&1' -f $PythonExe, $tmp
    return (& cmd.exe /d /s /c $cmd)
  }
  finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -Force -LiteralPath $tmp }
  }
}

function ConvertTo-VersionTag([string]$v) {
  $raw = $v
  if ($null -eq $raw) { $raw = '' }
  $s = ($raw).Trim().ToLower()
  if ($s -eq '15.x' -or $s -eq '15x') { return '15x' }
  if ($s -eq '15.y' -or $s -eq '15y') { return '15y' }
  HardStop 'invalid_version' @{ expected = '15.x|15.y'; got = $v }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -Path $repoRoot

if ($env:OS -notmatch 'Windows') {
  HardStop 'unsupported_os' @{ os = $env:OS }
}

$tag = ConvertTo-VersionTag $Version
$verLong = $(if ($tag -eq '15x') { '15.x' } else { '15.y' })

# Output directories.
$defaultOut = Join-Path $repoRoot ("diagnostics\frames_$tag")
$outDir = if ($OutDir) { (Resolve-Path $OutDir).Path } else { $defaultOut }

# Versioned config path (must be absolute). Spec: no manual configs; bootstrap enforces canonical.
$configPath = Join-Path $repoRoot ("rois_$tag.json")

# Require explicit window selector (same contract as calibrate_all_real.py)
if (-not $env:FRBOT_WINDOW_HWND -and -not $env:FRBOT_WINDOW_TITLE) {
  HardStop 'missing_precondition' @{ missing = 'FRBOT_WINDOW_HWND_or_FRBOT_WINDOW_TITLE' }
}

# Require config exists and is canonical.
if (-not (Test-Path -LiteralPath $configPath)) {
  HardStop 'missing_precondition' @{ missing = "config_not_found"; expected = $configPath }
}

# Validate canonical config schema now.
$cfgCheck = @'
import json
from pathlib import Path
p = Path(r"__CFG__")
data = json.loads(p.read_text(encoding="utf-8"))
if not isinstance(data, dict) or set(data.keys()) != {"rois"}:
  raise SystemExit("config_invalid_schema")
if not isinstance(data.get("rois"), dict):
  raise SystemExit("config_invalid_schema")
print("OK")
'@.Replace('__CFG__', $configPath)

$cfgOut = Invoke-PythonSnippet $cfgCheck
if ($LASTEXITCODE -ne 0 -or (($cfgOut | Out-String).Trim() -ne 'OK')) {
  HardStop 'config_invalid_schema' @{ config = $configPath; detail = ($cfgOut | Out-String) }
}

# Ensure output directory is clean for declared evidence.
if (Test-Path -LiteralPath $outDir) {
  Remove-Item -Recurse -Force -LiteralPath $outDir
}
New-Item -ItemType Directory -Path $outDir | Out-Null

# Call the existing strict real calibrator to capture gate-by-gate evidence.
# NOTE: This script does NOT simulate evidence; it relies on the live Tibia client foreground binding.
$env:FRBOT_TIBIA_VERSION = $verLong
$env:FRBOT_REAL_FRAMES_DIR = $outDir
$env:FRBOT_CONFIG_PATH = (Resolve-Path $configPath).Path

# Keep frame dumps enabled; evidence must be generated deterministically.
$env:FRBOT_DUMP_FRAMES = '1'

# Optional capture backend selection.
if ($CaptureBackend) {
  $env:FRBOT_CAPTURE_BACKEND = $CaptureBackend
}

$backendEffective = $env:FRBOT_CAPTURE_BACKEND
$isProjectorBackend = $false
if ($backendEffective) {
  $b = ([string]$backendEffective).Trim().ToLower()
  if ($b -in @('obs-projector','projector','meld-projector')) {
    $isProjectorBackend = $true
  }
}

if ($TryAllOutputs -or $isProjectorBackend) {
  if (-not $env:FRBOT_TRY_ALL_OUTPUTS) {
    $env:FRBOT_TRY_ALL_OUTPUTS = '1'
  }
  if (-not $env:FRBOT_MAX_OUTPUTS) {
    $env:FRBOT_MAX_OUTPUTS = [string]([Math]::Max(1, $MaxOutputs))
  }
}

# Run calibrator (sequential gates, no parallelism).
& $PythonExe tools\calibrate_all_real.py --version $env:FRBOT_TIBIA_VERSION
if ($LASTEXITCODE -ne 0) {
  HardStop 'bootstrap_failed' @{ step = 'calibrate_all_real'; exit_code = $LASTEXITCODE }
}

# Verify evidence completeness: at least one BEFORE/AFTER full pair per gate exists in the flat directory.
$verify = @'
import json
import re
from pathlib import Path

frames_dir = Path(r"__FRAMES__")
config_path = Path(r"__CFG__")

FILENAME_RE = re.compile(r'^(?P<gate>[a-z0-9-]+)_(?P<stamp>\d{8}-\d{6})_(?P<reason>.+)_(?P<side>before|after)(?P<mini>_minimap)?\.ppm$', re.IGNORECASE)
GATES = ('targeting','healing','combat','cavebot','looting','deposit','trade')

ppms = list(frames_dir.glob('*.ppm'))
if not ppms:
  raise SystemExit('real_evidence_missing')

def _read_ppm_size(path: Path) -> tuple[int, int]:
  data = path.read_bytes()[:256]
  if not data.startswith(b'P6'):
    raise ValueError('not_ppm_p6')
  # Very small header parser: split by whitespace, ignore comments.
  parts = []
  buf = b''
  for b in data.splitlines():
    if b.startswith(b'#'):
      continue
    buf += b + b'\n'
  for tok in buf.split():
    parts.append(tok)
    if len(parts) >= 4:
      break
  if len(parts) < 4:
    raise ValueError('ppm_header_too_short')
  w = int(parts[1]); h = int(parts[2])
  return w, h

pairs = {g: {} for g in GATES}
for p in ppms:
  m = FILENAME_RE.match(p.name)
  if not m:
    continue
  g = (m.group('gate') or '').lower()
  if g not in pairs:
    continue
  side = (m.group('side') or '').lower()
  is_mini = bool(m.group('mini'))
  if is_mini:
    continue
  key = (m.group('stamp') or '', m.group('reason') or '')
  bucket = pairs[g].setdefault(key, {'before': None, 'after': None})
  bucket[side] = p.name

manifest = {
  'version': '__VER__',
  'timestamp': None,
  'window_hwnd': None,
  'window_title': None,
  'gates': {},
}

missing = []
for g in GATES:
  ok_pairs = []
  for key, sides in pairs[g].items():
    if sides.get('before') and sides.get('after'):
      ok_pairs.append([sides['before'], sides['after']])
  if not ok_pairs:
    missing.append(g)
  else:
    manifest['gates'][g] = ok_pairs

if missing:
  raise SystemExit('missing_gate_pairs:' + ','.join(missing))

# Minimal ROI visibility check: require minimap/battle_list/inventory ROIs exist in config.
cfg = json.loads(config_path.read_text(encoding='utf-8'))
rois = cfg.get('rois') if isinstance(cfg, dict) else None
if not isinstance(rois, dict):
  raise SystemExit('config_invalid_schema')
required_any = ('minimap', 'battle_list', 'inventory')
missing_rois = [r for r in required_any if r not in rois]
if missing_rois:
  raise SystemExit('missing_required_rois:' + ','.join(missing_rois))

# In-bounds check against a real captured frame (deterministic).
sample = ppms[0]
w, h = _read_ppm_size(sample)
for name in required_any:
  r = rois.get(name)
  if not isinstance(r, dict):
    raise SystemExit('config_invalid_schema')
  x = int(r.get('x')); y = int(r.get('y')); rw = int(r.get('width')); rh = int(r.get('height'))
  if x < 0 or y < 0 or rw <= 0 or rh <= 0:
    raise SystemExit('roi_out_of_bounds:' + name)
  if (x + rw) > w or (y + rh) > h:
    raise SystemExit('roi_out_of_bounds:' + name)

print(json.dumps(manifest, indent=2, sort_keys=True))
'@
$verify = $verify.Replace('__FRAMES__', $outDir).Replace('__CFG__', (Resolve-Path $configPath).Path).Replace('__VER__', $verLong)

$manifestJson = Invoke-PythonSnippet $verify
if ($LASTEXITCODE -ne 0) {
  # Preserve the exact failure string for auditing.
  HardStop 'evidence_incomplete' @{ detail = ($manifestJson | Out-String) }
}

# Resolve and record foreground window details (hwnd + title) deterministically.
$winInfo = @'
import json
from adapters.windows.win32 import get_foreground_window, get_window_text
fg = int(get_foreground_window() or 0)
print(json.dumps({'window_hwnd': hex(fg), 'window_title': get_window_text(fg) or ''}))
'@
$winRaw = Invoke-PythonSnippet $winInfo
if ($LASTEXITCODE -ne 0) {
  HardStop 'wininfo_failed' @{ detail = ($winRaw | Out-String) }
}
try {
  $winText = ($winRaw | Out-String).Trim()
  $win = $winText | ConvertFrom-Json
}
catch {
  HardStop 'wininfo_invalid_json' @{ detail = ($winRaw | Out-String) }
}

# Write manifest.
$manifestPath = Join-Path $outDir '_evidence_manifest.json'
$manifestObj = $manifestJson | ConvertFrom-Json
$manifestObj.timestamp = (Get-Date).ToString('o')
$manifestObj.window_hwnd = $win.window_hwnd
$manifestObj.window_title = $win.window_title
$manifestObj | ConvertTo-Json -Depth 10 | Out-File -FilePath $manifestPath -Encoding utf8

Write-Host "OK: evidence bootstrapped"
Write-Host "version=$Version"
Write-Host "frames=$outDir"
Write-Host "config=$configPath"
Write-Host "manifest=$manifestPath"
