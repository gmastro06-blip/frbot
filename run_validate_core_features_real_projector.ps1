[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectorTitle,

  [Parameter(Mandatory = $true)]
  [string]$TibiaTitle,

  [Parameter(Mandatory = $true)]
  [Alias("Config")]
  [string]$ConfigPath,

  [Parameter(Mandatory = $false)]
  [int]$MaxTicks = 30,

  [Parameter(Mandatory = $false)]
  [int]$GraceSeconds = 10

  ,
  [Parameter(Mandatory = $false)]
  [ValidateSet("Strict", "StrictSafe", "Diagnostic")]
  [string]$Mode = "Strict"

  ,
  [Parameter(Mandatory = $false)]
  [switch]$StrictSafe

  ,
  [Parameter(Mandatory = $false)]
  [switch]$ForceBackgroundInput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Set-Location -LiteralPath $PSScriptRoot

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$framesDir = Join-Path $PSScriptRoot ("diagnostics\\frames_validate_core\\" + $ts)
New-Item -ItemType Directory -Path $framesDir -Force | Out-Null

$python = Join-Path $PSScriptRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

$scriptPath = Join-Path $PSScriptRoot "tools\\validate_core_features.py"

function Quote-Arg {
  param([Parameter(Mandatory = $true)][string]$Value)
  return ('"' + ($Value -replace '"', '\\"') + '"')
}

function To-DisplayPath {
  param([Parameter(Mandatory = $true)][string]$Value)

  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $Value
  }

  try {
    $root = [System.IO.Path]::GetFullPath($PSScriptRoot)
    $full = if ([System.IO.Path]::IsPathRooted($Value)) {
      [System.IO.Path]::GetFullPath($Value)
    }
    else {
      [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Value))
    }
    if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
      $relative = $full.Substring($root.Length).TrimStart('\\')
      if ([string]::IsNullOrWhiteSpace($relative)) {
        return ".\\"
      }
      return ".\\" + $relative
    }
  }
  catch {
  }

  return $Value
}

$argString = @(
  (Quote-Arg -Value $scriptPath),
  "--projector-title", (Quote-Arg -Value $ProjectorTitle),
  "--tibia-title", (Quote-Arg -Value $TibiaTitle),
  "--config", (Quote-Arg -Value $ConfigPath),
  "--frames-dir", (Quote-Arg -Value $framesDir),
  "--max-ticks", [string]$MaxTicks,
  "--grace-seconds", [string]$GraceSeconds
) -join " "

$effectiveStrictSafe = $StrictSafe.IsPresent
$effectiveBackgroundInput = $ForceBackgroundInput.IsPresent
$modeValue = [string]$Mode
if ([string]::IsNullOrWhiteSpace($modeValue)) {
  $modeValue = "Strict"
}
$modeResolved = $modeValue
switch ($modeValue.ToLowerInvariant()) {
  "strictsafe" {
    $modeResolved = "StrictSafe"
    $effectiveStrictSafe = $true
  }
  "diagnostic" {
    $modeResolved = "Diagnostic"
    $effectiveStrictSafe = $true
    $effectiveBackgroundInput = $true
  }
  default {
    $modeResolved = "Strict"
  }
}

Write-Host ("ModeResolved: " + $modeResolved + " | StrictSafe=" + $(if ($effectiveStrictSafe) { "1" } else { "0" }) + " | BackgroundInput=" + $(if ($effectiveBackgroundInput) { "1" } else { "0" }))

if ($effectiveBackgroundInput) {
  $argString = $argString + " --force-background-input"
}
if ($effectiveStrictSafe) {
  $argString = $argString + " --strict-safe"
}
Write-Host ("PythonExecutable: " + $python)

$pythonDisplay = To-DisplayPath -Value $python
$scriptPathDisplay = To-DisplayPath -Value $scriptPath
$configPathDisplay = To-DisplayPath -Value $ConfigPath
$framesDirDisplay = To-DisplayPath -Value $framesDir

$argStringDisplay = @(
  (Quote-Arg -Value $scriptPathDisplay),
  "--projector-title", (Quote-Arg -Value $ProjectorTitle),
  "--tibia-title", (Quote-Arg -Value $TibiaTitle),
  "--config", (Quote-Arg -Value $configPathDisplay),
  "--frames-dir", (Quote-Arg -Value $framesDirDisplay),
  "--max-ticks", [string]$MaxTicks,
  "--grace-seconds", [string]$GraceSeconds
) -join " "
if ($effectiveBackgroundInput) {
  $argStringDisplay = $argStringDisplay + " --force-background-input"
}
if ($effectiveStrictSafe) {
  $argStringDisplay = $argStringDisplay + " --strict-safe"
}

Write-Host ("PythonArguments: " + $argStringDisplay)
Write-Host ("EffectiveCommand: " + $pythonDisplay + " " + $argStringDisplay)

$stdoutPath = Join-Path $framesDir "stdout.log"
$stderrPath = Join-Path $framesDir "stderr.log"

$proc = Start-Process -FilePath $python -ArgumentList $argString -WorkingDirectory $PSScriptRoot -WindowStyle Normal -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru

Write-Host "Artifacts: $framesDir"
Write-Host "Stdout: $stdoutPath"
Write-Host "Stderr: $stderrPath"
Write-Host "ExitCode: $($proc.ExitCode)"
exit ([int]$proc.ExitCode)
