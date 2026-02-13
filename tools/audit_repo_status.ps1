[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot
try {
  # Keep output UTF-8 consistent.
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
  & poetry run python (Join-Path $RepoRoot "tools\audit_repo_status.py")
  Write-Host ("Reporte: {0}" -f (Join-Path $RepoRoot "diagnostics\status_repo.json"))
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
