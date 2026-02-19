from __future__ import annotations

"""Smoke tests para el recorder de UI (sin levantar flujo real de captura).

Test 1a/1b: _new_route_runtime_context() usa mode="real" aunque FRBOT_MODE sea prod_full/cavebot_full.
Test 2:     config_path vacío sin fallback disponible => _show_error llamado, grabación NO inicia.
Test 3:     config_path vacío CON fallback válido => campo actualizado, grabación inicia.
"""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from ui_main import MainWindow


# ---------------------------------------------------------------------------
# Tests 1a/1b: mode="real" independiente de FRBOT_MODE del entorno
# ---------------------------------------------------------------------------

def test_new_route_runtime_context_mode_is_always_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """FRBOT_MODE=prod_full no debe contaminar el modo del recorder."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FRBOT_MODE", "prod_full")

    w = MainWindow()
    try:
        ctx = w._new_route_runtime_context()
        assert ctx.config.mode == "real", (
            f"mode esperado 'real', got {ctx.config.mode!r} "
            "(FRBOT_MODE=prod_full no debe afectar al recorder)"
        )
    finally:
        w.close()


def test_new_route_runtime_context_mode_is_real_even_with_cavebot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """FRBOT_MODE=cavebot_full no debe contaminar el modo del recorder."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FRBOT_MODE", "cavebot_full")

    w = MainWindow()
    try:
        ctx = w._new_route_runtime_context()
        assert ctx.config.mode == "real", (
            f"mode esperado 'real', got {ctx.config.mode!r}"
        )
    finally:
        w.close()


# ---------------------------------------------------------------------------
# Test 2: config_path vacío sin fallback => error controlado, NO excepción
# ---------------------------------------------------------------------------

def test_route_start_empty_config_no_fallback_shows_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config path vacío sin ningún fallback válido debe mostrar un error
    accionable sin crashear y sin iniciar la grabación."""
    app = QApplication.instance() or QApplication([])

    errors_shown: list[tuple[str, str]] = []

    w = MainWindow()
    try:
        # Vaciar el campo de config
        w.input_config_path.setText("")

        # Hacer que resolve siempre devuelva una ruta inexistente
        monkeypatch.setattr(
            w,
            "_resolve_valid_roi_config_path",
            lambda requested, *, profile: "/nonexistent/__frbot_test_cfg__.json",
        )

        # Capturar _show_error en vez de mostrar diálogo real
        def _fake_show_error(title: str, msg: str) -> None:
            errors_shown.append((title, msg))

        w._show_error = _fake_show_error  # type: ignore[method-assign]

        w._on_route_start_clicked()
        app.processEvents()

        assert len(errors_shown) == 1, f"Se esperaba 1 error, got {len(errors_shown)}: {errors_shown}"
        title, msg = errors_shown[0]
        assert "config" in title.lower() or "config" in msg.lower(), (
            f"El mensaje de error debe mencionar config: {title!r} / {msg!r}"
        )
        assert w._route_recording_active is False, (
            "La grabación NO debe iniciar con config_path inválido"
        )
    finally:
        w.close()


# ---------------------------------------------------------------------------
# Test 3: config_path vacío CON fallback válido => campo actualizado, graba
# ---------------------------------------------------------------------------

def test_route_start_empty_config_with_fallback_updates_path_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si config_path está vacío pero hay un fallback válido (mockeado),
    el campo se actualiza y la grabación puede iniciar."""
    app = QApplication.instance() or QApplication([])

    # Necesitamos un JSON de ROIs real para que el preflight (mockeado) funcione
    real_cfg = Path("config/rois_prod_full.json")
    if not real_cfg.exists():
        pytest.skip("config/rois_prod_full.json no existe — omitiendo test de fallback")

    from contracts.capture import Frame

    class _FakeCapture:
        name = "obs_source"

        def grab(self) -> Frame:
            return Frame(
                width=4, height=4, monotonic_ts_ns=1, digest_hex="d",
                rgb=bytes([1] * 48),
                minimap_detected=True,
                minimap_rgb=bytes([1] * 12),
                minimap_width=2, minimap_height=2,
                minimap_digest_hex="mm",
            )

    monkeypatch.setattr("ui_main.route_capture_only_preflight", lambda _ctx: _FakeCapture())

    w = MainWindow()
    try:
        w.input_config_path.setText("")

        # El fallback resuelve al config real del repo (que existe)
        monkeypatch.setattr(
            w,
            "_resolve_valid_roi_config_path",
            lambda requested, *, profile: str(real_cfg.resolve()),
        )

        # Parar el timer para evitar ticks de captura durante processEvents
        w._route_timer.stop()

        w._on_route_start_clicked()
        app.processEvents()

        updated_cfg = str(w.input_config_path.text() or "").strip()
        assert updated_cfg, "El campo config_path debe haberse actualizado al fallback"
        assert w._route_recording_active is True, (
            "La grabación debe iniciar cuando hay fallback válido"
        )
    finally:
        w._route_timer.stop()
        w.close()
