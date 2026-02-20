# Minimal PowerShell launcher for real-mode UI testing
# Usage:
# 1. Open PowerShell as Administrator (if required for input binding/capture).
# 2. From the repo root run: .\scripts\run_ui_real_minimal.ps1
# This script sets recommended environment variables and runs the UI.

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
# If you use a specific capture source/adapter, set it here (optional)
# Example: $env:FRBOT_CAPTURE_SOURCE = 'hdmi_capture'
$env:FRBOT_CAPTURE_SOURCE = ''

# --- Optional tuning (defaults are usually safe) ---
# Default minimap z-level for recording/sampling
$env:FRBOT_DEFAULT_Z = '7'

# If you want waypoint coords to be interpreted in world coordinates set:
# $env:FRBOT_CAVEBOT_WAYPOINT_SPACE = 'world'

# --- Run commands ---
Write-Host "Environment variables set. Running UI..."

# Ensure dependencies are installed (uncomment to auto-install if needed)
# Write-Host "Installing dependencies via poetry (this may take a while)..."
# poetry install

# Run the UI using poetry (recommended) so the virtualenv is used
poetry run python ui_main.py

# If you prefer to run directly with system python, replace the line above with:
# python ui_main.py
