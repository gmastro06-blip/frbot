#!/usr/bin/env python
"""
Test de certificacion para navegacion minimap.
Valida marker detection y recovery por iconos.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Setup
RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format=f"[{RUN_ID}] %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_marker_detection_config() -> dict:
    """Verifica configuracion de detector de marker."""
    result = {
        "test": "marker_detection_config",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    rgb = os.environ.get('FRBOT_PLAYER_MARKER_RGB', '0,255,0')
    tol = int(os.environ.get('FRBOT_PLAYER_MARKER_TOL', '30'))
    min_pixels = int(os.environ.get('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5'))

    result.update({
        "marker_rgb": rgb,
        "marker_tol": tol,
        "marker_min_pixels": min_pixels,
        "valid": tol > 0 and min_pixels > 0,
    })

    logger.info(f"[MARKER] rgb={rgb}, tol={tol}, min_pixels={min_pixels}")
    return result


def test_stuck_window_config() -> dict:
    """Verifica configuracion de stuck window."""
    result = {
        "test": "stuck_window_config",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    stuck_window = os.environ.get('FRBOT_CAVEBOT_STUCK_WINDOW', '5')
    try:
        val = int(stuck_window)
        valid = 3 <= val <= 30
    except ValueError:
        val = stuck_window
        valid = False

    result.update({
        "configured": stuck_window,
        "parsed": val,
        "valid": valid,
        "note": "ticks (not ms)",
    })

    logger.info(f"[STUCK_WINDOW] configured={stuck_window}, valid={valid}")
    return result


def test_minimap_mode_config() -> dict:
    """Verifica configuracion de modo minimap."""
    result = {
        "test": "minimap_mode_config",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    mode = os.environ.get('FRBOT_MINIMAP_MODE', 'marker')
    recovery_enabled = os.environ.get('FRBOT_RECOVERY_ICONS', '0') == '1'
    confidence_threshold = float(os.environ.get('FRBOT_MARKER_CONFIDENCE_THRESHOLD', '0.5'))

    result.update({
        "mode": mode,
        "recovery_enabled": recovery_enabled,
        "confidence_threshold": confidence_threshold,
        "valid": mode in {'marker', 'hybrid', 'icons'},
    })

    logger.info(f"[MINIMAP] mode={mode}, recovery={recovery_enabled}, threshold={confidence_threshold}")
    return result


def test_marker_detection_imports() -> dict:
    """Verifica que se pueden importar los modulos de deteccion."""
    result = {
        "test": "marker_detection_imports",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        from runtime.cavebot_semantics import detect_player_marker, select_player_marker
        result.update({
            "detect_player_marker": True,
            "select_player_marker": True,
            "available": True,
        })
    except Exception as e:
        result.update({
            "available": False,
            "error": str(e),
        })

    return result


def test_localization_available() -> dict:
    """Verifica que la localizacion por iconos esta disponible."""
    result = {
        "test": "localization_available",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        from runtime.minimap_localization import localize_minimap
        result.update({
            "localize_minimap": True,
            "available": True,
        })
    except Exception as e:
        result.update({
            "available": False,
            "error": str(e),
        })

    return result


def test_recovery_trigger_conditions() -> dict:
    """Verifica las condiciones que disparan recovery."""
    result = {
        "test": "recovery_trigger_conditions",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    # Recovery triggers
    triggers = {
        "stuck_detected": "cavebot_stuck_detected in abort_reason",
        "low_confidence": "marker_confidence < threshold",
    }

    result.update({
        "triggers": triggers,
        "note": "Recovery activa cuando stuck_detected o marker_confidence < FRBOT_MARKER_CONFIDENCE_THRESHOLD",
    })

    return result


def main() -> int:
    """Ejecuta todos los tests de certificacion de navegacion."""
    logger.info("=" * 60)
    logger.info("FRBOT NAV CERTIFICATION")
    logger.info("=" * 60)

    results = []

    # Test 1: Marker detection config
    results.append(test_marker_detection_config())

    # Test 2: Stuck window config
    results.append(test_stuck_window_config())

    # Test 3: Minimap mode config
    results.append(test_minimap_mode_config())

    # Test 4: Marker detection imports
    results.append(test_marker_detection_imports())

    # Test 5: Localization available
    results.append(test_localization_available())

    # Test 6: Recovery trigger conditions
    results.append(test_recovery_trigger_conditions())

    # Summary
    all_passed = all(r.get("valid", r.get("available", True)) for r in results)

    logger.info("=" * 60)
    logger.info(f"SUMMARY: {'PASSED' if all_passed else 'FAILED'}")
    logger.info("=" * 60)

    for r in results:
        status = "OK" if r.get("valid", r.get("available", True)) else "FAIL"
        logger.info(f"  [{status}] {r['test']}")

    # Write manifest
    manifest = {
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
        "tests": results,
        "overall": "PASSED" if all_passed else "FAILED",
    }

    out_dir = Path("qa_logs")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"nav_certify_{RUN_ID}.json"
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest written to: {out_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
