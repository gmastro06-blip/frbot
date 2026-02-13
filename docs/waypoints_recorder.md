# Waypoints Recorder

## Cómo grabar

1. Ejecuta la UI: `poetry run python app.py`
2. En Home, entra a **Waypoints**.
3. Pulsa **Iniciar grabación**.
4. Registra movimientos con `W/A/S/D` o flechas.
5. Marca acciones especiales con botones u hotkeys:
   - Rope (`F7`)
   - Shovel (`F8`)
   - Pick (`F9`)
   - Subir nivel (`F10`)
   - Bajar nivel (`F11`)
6. Usa **Pausar/Reanudar** cuando necesites.
7. Pulsa **Exportar sesión** para generar artefactos.

## Formato de salida

Se guarda en `diagnostics/waypoints/waypoints_<timestamp>/`:

- `waypoints_<timestamp>.jsonl` (eventos/steps append-safe)
- `waypoints_<timestamp>.json` (sesión consolidada por escritura atómica)
- `step_<index>_before.ppm`
- `step_<index>_after.ppm`

Cada `step` incluye:

- `step_index`
- `action_kind` (`move`, `rope`, `shovel`, `pick`, `stairs_up`, `stairs_down`)
- `key_or_click`
- `before_ppm`
- `after_ppm`
- `ts`
- `window_hwnd`
- `capture_source`
- `frame_size`
- `metrics.marker_delta_px`
- `metrics.floor_change_flag`
- `inputs_sent` (siempre `1`)

## Guardrails y abortos

- Verificación de binding antes de cada input.
- Regla de 1 input por step/intent.
- Si no hay evidencia semántica suficiente, aborta con `no_progress`.
- Si se pierde binding o marker, aborta con reason canónica.
- En fallos: `diagnostics/fatal.log` se escribe en JSON con traceback y detalles.

## Uso en cavebot

- En la UI, usa **Aplicar ruta al editor** para convertir la sesión grabada a `Script` editable.
- Luego guarda con **Save Script** para usarlo en flujo de cavebot.
