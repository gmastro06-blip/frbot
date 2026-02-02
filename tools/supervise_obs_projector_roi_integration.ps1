$ErrorActionPreference = 'Stop'

$repo = 'C:\Users\gmast\Documents\GitHub\frbot'
$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "venv_python_not_found: $py" }

$roiJson = Join-Path $repo 'obs_projector_rois.json'
$image = Join-Path $repo 'diagnostics\obs_projector_full.ppm'
$outRuntime = Join-Path $repo 'obs_projector_rois_runtime.json'
$outConvertLog = Join-Path $repo 'obs_projector_rois_runtime_convert.log.json'

if (-not (Test-Path $roiJson)) { throw "roi_json_not_found: $roiJson" }
if (-not (Test-Path $image)) { throw "image_not_found: $image" }

# Clean only the derived outputs (keep obs_projector_rois.json)
foreach ($p in @($outRuntime, $outConvertLog)) {
  if (Test-Path $p) { Remove-Item -Force $p }
}

Start-Process -WorkingDirectory $repo -FilePath $py -ArgumentList @(
  'tools\calibrate_obs_projector_rois.py',
  '--image','diagnostics\obs_projector_full.ppm',
  '--load-json','obs_projector_rois.json',
  '--out-json','obs_projector_rois.json',
  '--out-verify-ppm','obs_projector_roi_verify.ppm',
  '--out-log','obs_projector_roi_calibration.log.json'
)

Write-Host 'ROI UI launched (existing ROIs loaded).' 
Write-Host 'Add missing ROIs (absolute px) and click Save:'
Write-Host '- hp_mp'
Write-Host '- inventory'
Write-Host '- npc_dialog'
Write-Host '- trade'

$required = @('minimap','battle_list','hp_mp','inventory','npc_dialog','trade')
$lastReport = Get-Date

Write-Host 'Waiting for required ROI names in obs_projector_rois.json...'
while ($true) {
  Start-Sleep -Milliseconds 500
  try {
    $j = Get-Content -Raw $roiJson | ConvertFrom-Json
    $names = @($j.rois | ForEach-Object { "$($_.name)" })

    $missing = @()
    foreach ($req in $required) {
      if ($req -eq 'battle_list') {
        if (-not ($names -contains 'battle_list') -and -not ($names -contains 'battlelist')) {
          $missing += $req
        }
      } else {
        if (-not ($names -contains $req)) {
          $missing += $req
        }
      }
    }

    if ($missing.Count -eq 0) { break }

    $now = Get-Date
    if (($now - $lastReport).TotalSeconds -ge 5) {
      Write-Host ("Still missing: {0}" -f ($missing -join ', '))
      $lastReport = $now
    }
  } catch {
    # Ignore parse errors while file is being written.
  }
}

Write-Host 'All required names detected. Converting to runtime schema...'
& $py (Join-Path $repo 'tools\convert_obs_projector_rois_to_runtime.py') `
  --in-json $roiJson `
  --image $image `
  --out-json $outRuntime `
  --out-log $outConvertLog `
  --rename 'battlelist:battle_list'

Write-Host 'Conversion OK. Running strict projector capture test (keep projector window foreground)...'
$env:FRBOT_CONFIG_PATH = $outRuntime
$env:FRBOT_MINIMAP_ROI = 'minimap'
$env:FRBOT_PROJECTOR_WINDOW_TITLE = 'Proyector en ventana (Fuente) - Tibia_Fuente'
$env:FRBOT_PROJECTOR_REQUIRE_FOREGROUND = '1'
$env:FRBOT_PROJECTOR_FOCUS_ON_START = '1'
& $py (Join-Path $repo 'tools\test_capture_projector_real.py') --config $outRuntime --window-title 'Proyector en ventana' --wait-seconds 1 --frames 1

Write-Host 'Artifacts:'
Get-Item (Join-Path $repo 'obs_projector_rois.json'), (Join-Path $repo 'obs_projector_rois_runtime.json'), (Join-Path $repo 'obs_projector_roi_verify.ppm'), (Join-Path $repo 'obs_projector_roi_calibration.log.json'), (Join-Path $repo 'obs_projector_rois_runtime_convert.log.json') | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize
