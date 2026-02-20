# Full PowerShell launcher for real-mode UI testing
# - Runs `poetry install` if `poetry` is available (ensures deps) unless -NoInstall
# - Opens a separate terminal window that tails the runtime log unless -NoTailLog
# - Sets recommended environment variables and runs the UI
# Usage examples (from repo root):
#   .\scripts\run_ui_real_full.ps1                # default behavior
#   .\scripts\run_ui_real_full.ps1 -NoInstall     # skip poetry install
#   .\scripts\run_ui_real_full.ps1 -LogPath logs\my_runtime.log  # use custom log

param(
    [switch]$NoInstall,
    [string]$LogPath = '',
    [switch]$NoTailLog
)

# Move to repo root (script location assumed to be <repo>/scripts)
Set-Location -Path (Join-Path $PSScriptRoot "..")

Write-Host "Launching FRBot UI (real-mode) from: $(Get-Location)"

# --- Hotkeys (customize as needed) ---
$env:FRBOT_ROPE_KEY = 'F8'
$env:FRBOT_SHOVEL_KEY = 'F9'
$env:FRBOT_PICK_KEY = 'F10'
$env:FRBOT_LADDER_UP_KEY = 'F11'
$env:FRBOT_LADDER_DOWN_KEY = 'F12'
$env:FRBOT_OPEN_DOOR_KEY = 'F7'

# --- Capture / environment hints ---
$env:FRBOT_CAPTURE_SOURCE = ''
$env:FRBOT_DEFAULT_Z = '7'

# -- Optional config --
# If you want waypoint coords to be interpreted in world coordinates set:
# $env:FRBOT_CAVEBOT_WAYPOINT_SPACE = 'world'

# --- Ensure dependencies (if poetry is available) ---
if (-not $NoInstall) {
    if (Get-Command poetry -ErrorAction SilentlyContinue) {
        Write-Host "Detected poetry. Running 'poetry install' to ensure dependencies..."
        try {
            poetry install
        } catch {
            Write-Warning "'poetry install' failed or was interrupted. You may need to run it manually."
        }
    } else {
        Write-Warning "'poetry' not found on PATH. Skipping automatic install. Install Poetry or run in your venv."
    }
} else {
    Write-Host "Skipping 'poetry install' because -NoInstall was provided."
}

# --- Tail runtime log in a new terminal window ---
$logPath = Join-Path (Get-Location) 'runtime_ui.log'
if (-not (Test-Path $logPath)) {
    # fallback to any runtime_ui.log.* file
    $alt = Get-ChildItem -Path (Get-Location) -Filter 'runtime_ui.log*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($alt) { $logPath = $alt.FullName }
}

if (-not $NoTailLog) {
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
        $logPath = Resolve-Path -Path $LogPath -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($logPath) { $logPath = $logPath.Path }
    }
    if (-not $logPath) {
        if (Test-Path $logPath) {
            # already set
        } else {
            # fallback to any runtime_ui.log.* file
            $alt = Get-ChildItem -Path (Get-Location) -Filter 'runtime_ui.log*' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($alt) { $logPath = $alt.FullName }
        }
    }

    if ($logPath -and (Test-Path $logPath)) {
        Write-Host "Opening separate terminal to tail log: $logPath"
        if (Get-Command pwsh -ErrorAction SilentlyContinue) {
            Start-Process pwsh -ArgumentList "-NoExit", "-Command", "Get-Content -Path '$logPath' -Wait"
        } else {
            Start-Process powershell -ArgumentList "-NoExit", "-Command", "Get-Content -Path '$logPath' -Wait"
        }
    } else {
        Write-Warning "No runtime log found to tail. Expected 'runtime_ui.log' in repo root or provide -LogPath.">
    }
} else {
    Write-Host "Skipping log tail because -NoTailLog was provided."
}

# --- Run the UI using poetry (recommended) ---
Write-Host "Starting UI... (press Ctrl+C to stop)"
try {
    poetry run python ui_main.py
} catch {
    Write-Warning "Failed to launch UI via poetry. Try: python ui_main.py or activate your virtualenv and run the same command."
}
