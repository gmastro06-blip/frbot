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
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
