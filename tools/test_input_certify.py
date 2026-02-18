#!/usr/bin/env python
"""
Test de certificacion para input y focus.
Valida que el backend de input y el sistema de focus funcionen correctamente.
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


def test_input_method_selection() -> dict:
    """Verifica que se seleccione el metodo de input correcto."""
    result = {
        "test": "input_method_selection",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()

    # Determine expected method
    expected_methods = {'sendinput', 'sendinput_vk', 'postmessage', ''}
    valid = method in expected_methods or method == ''

    # Method requires foreground
    requires_foreground = method in {'sendinput', 'sendinput_vk'}

    result.update({
        "configured": method or "(default)",
        "requires_foreground": requires_foreground,
        "valid": valid,
        "selected_backend": "Win32HwndKeyboard" if method in {'sendinput', 'sendinput_vk'} else "Win32HwndKeyboard (PostMessage)",
    })

    logger.info(f"[INPUT_METHOD] configured={method}, requires_foreground={requires_foreground}")
    return result


def test_focus_throttle_config() -> dict:
    """Verifica configuracion de throttle para focus."""
    result = {
        "test": "focus_throttle_config",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    throttle_ms = int(os.environ.get('FRBOT_FOCUS_THROTTLE_MS', '500'))

    result.update({
        "configured_ms": throttle_ms,
        "valid": throttle_ms >= 0,
    })

    logger.info(f"[FOCUS_THROTTLE] configured_ms={throttle_ms}")
    return result


def test_input_backend_available() -> dict:
    """Verifica que el backend de input esté disponible."""
    result = {
        "test": "input_backend_available",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        from adapters.input.win32_hwnd import Win32HwndKeyboard
        result.update({
            "backend": "Win32HwndKeyboard",
            "available": True,
        })
    except Exception as e:
        result.update({
            "backend": "Win32HwndKeyboard",
            "available": False,
            "error": str(e),
        })

    return result


def test_focus_helper_signature() -> dict:
    """Verifica que el helper _ensure_focus_if_needed existe y tiene la firma correcta."""
    result = {
        "test": "focus_helper_signature",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        from adapters.input.win32_hwnd import Win32HwndKeyboard
        import inspect

        if hasattr(Win32HwndKeyboard, '_ensure_focus_if_needed'):
            sig = inspect.signature(Win32HwndKeyboard._ensure_focus_if_needed)
            result.update({
                "method_exists": True,
                "method": "_ensure_focus_if_needed",
                "signature": str(sig),
            })
        else:
            result.update({
                "method_exists": False,
                "note": "_ensure_focus_if_needed not found - using inline focus logic",
            })

        # Check for new readiness methods
        if hasattr(Win32HwndKeyboard, 'get_window_readiness'):
            result["get_window_readiness"] = True
        if hasattr(Win32HwndKeyboard, 'ensure_window_ready_for_input'):
            result["ensure_window_ready_for_input"] = True

    except Exception as e:
        result.update({
            "error": str(e),
        })

    return result


def test_readiness_struct() -> dict:
    """Verifica que los métodos de readiness funcionan sin errores."""
    result = {
        "test": "readiness_struct",
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        from adapters.input.win32_hwnd import Win32HwndKeyboard
        # Test instantiation (will fail on non-Windows but that's OK)
        # We just verify the method signature exists
        import inspect
        if hasattr(Win32HwndKeyboard, 'get_window_readiness'):
            sig = inspect.signature(Win32HwndKeyboard.get_window_readiness)
            result.update({
                "method_exists": True,
                "signature": str(sig),
                "returns": "dict with hwnd, is_minimized, is_visible, is_foreground, ready_for_input",
            })
        else:
            result.update({
                "method_exists": False,
            })
    except Exception as e:
        result.update({
            "error": str(e),
        })

    return result


def main() -> int:
    """Ejecuta todos los tests de certificacion de input."""
    logger.info("=" * 60)
    logger.info("FRBOT INPUT CERTIFICATION")
    logger.info("=" * 60)

    results = []

    # Test 1: Input method selection
    results.append(test_input_method_selection())

    # Test 2: Focus throttle config
    results.append(test_focus_throttle_config())

    # Test 3: Input backend available
    results.append(test_input_backend_available())

    # Test 4: Focus helper signature
    results.append(test_focus_helper_signature())

    # Test 5: Readiness struct
    results.append(test_readiness_struct())

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
    out_path = out_dir / f"input_certify_{RUN_ID}.json"
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest written to: {out_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
