param()
$cfg = (Resolve-Path "config\rois_prod_full.json").Path
$env:FRBOT_CONFIG_PATH = $cfg
Write-Host "CONFIG: $cfg"
Write-Host "START: $(Get-Date -Format 'HH:mm:ss')"
& "$PSScriptRoot\run_release_prod_full.ps1" -ObsSource "Tibia_Fuente" -WindowTitle "Tibia - Onniwabanshu" -InvertHorizontal
$rc = $LASTEXITCODE
Write-Host "END: $(Get-Date -Format 'HH:mm:ss')  exit=$rc"
exit $rc
