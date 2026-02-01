# BASELINE (STABLE)

**Internal tag (documentation only):**

BASELINE_STABLE = True

This repository is frozen as a stable baseline.

## What is covered (guaranteed by tests)

The following feature-gates are implemented and verified via deterministic tests:

- Targeting
- Healing
- Combat
- Cavebot (minimap + marker tracking)
- Looting (premium + free)
- Deposit

Cross-cutting invariants (contract-level):

- Preflight runs before runtime execution.
- `diagnostics/runtime.log` is created **only** after preflight succeeds.
- `diagnostics/fatal.log` is always written on abort/crash.
- Evidence-or-abort discipline:
  - 1 intent → 1 input → 1 AFTER capture → semantic evidence validation, or abort.
- MockWorld is deterministic and covered by tests.

## What must NOT be changed (baseline freeze)

Do not modify these areas in-place when adding new features:

- Contracts and invariants: `contracts/*`
- Core engine semantics: `core/engine.py`, `contracts/engine.py`
- Runtime config/state definitions: `contracts/runtime.py`
- Preflight modules and logging invariants
- Existing gate loops (tick loops, attempt guardrails, evidence checks)
- Existing abort reasons (strings are part of the contract)

If something “could be better”, ignore it in baseline work. This baseline is intentionally boring.

## How this repo may be extended (only via new gates)

All new features must be introduced as a **new independent gate**, with:

- A new `<gate>_entrypoint.py`
- A new `runtime/<gate>_preflight.py`
- A new `runtime/<gate>_runner.py`
- Deterministic MockWorld flags/state required for that gate
- Blocking pytest coverage for success + abort taxonomy + no-spam limits

Rules:

- Do not modify existing gate loops.
- Do not reuse evidence extracted for one gate as “success” evidence for another gate.
- Do not relax preflight/logging invariants.

## CI baseline

CI is intentionally minimal and must remain deterministic:

- `python -m pytest -q`
- `./smoke.ps1`

No new jobs, no parallelization, no new linters.

---

## CHECKLIST DE VALIDACIÓN REAL-MODE (Windows)

Ejecutar en este orden. No saltar pasos.

Regla de oro: cada gate se valida aislado, con sesiones cortas (2–5 min). Si falla → no sigas al siguiente gate.

Notas importantes de este repo:

- `FRBOT_MODE` selecciona el **gate**: `targeting|healing|combat|cavebot|looting|deposit|trade`.
- El backend **real/mock** se elige por gate:
  - `FRBOT_TARGETING_BACKEND`, `FRBOT_HEALING_BACKEND`, `FRBOT_COMBAT_BACKEND`, `FRBOT_CAVEBOT_BACKEND`, `FRBOT_LOOTING_BACKEND`, `FRBOT_DEPOSIT_BACKEND`, `FRBOT_TRADE_BACKEND`.
  - Default: `real`.
- Logs:
  - `diagnostics/fatal.log` se escribe siempre en abort/crash.
  - `diagnostics/runtime.log` se crea **solo** si preflight pasa.

Antes de cada corrida (recomendado):

```powershell
Remove-Item diagnostics/runtime.log, diagnostics/fatal.log -ErrorAction SilentlyContinue
```

### 0️⃣ Pre-check obligatorio (una sola vez)

☐ Tibia y bot mismo usuario / mismos privilegios
☐ Ventana modo ventana (no fullscreen)
☐ Escala UI fija (100%)
☐ Minimapa visible, sin overlays
☐ Ningún otro programa capturando la pantalla
☐ `FRBOT_MODE` correcto para el gate (targeting/healing/cavebot/etc.)
☐ Backend correcto para ese gate (`FRBOT_<GATE>_BACKEND=real`)
☐ Binding configurado (uno de estos):
  - `FRBOT_WINDOW_HWND` (preferido), o
  - `FRBOT_WINDOW_TITLE` (substring del título)

Esperado:

- Si algo está mal → abort inmediato en preflight con razón clara
- `diagnostics/fatal.log` creado
- `diagnostics/runtime.log` NO existe

Si aquí hay “sigue pero no hace nada” → STOP (bug grave).

### 1️⃣ Targeting-only (REAL)

Objetivo: probar semántica + binding, sin movimiento.

Pasos:

☐ `FRBOT_MODE=targeting`
☐ `FRBOT_TARGETING_BACKEND=real`
☐ Abrir Battle List con 1–2 mobs claros
☐ Ejecutar:

```powershell
$env:FRBOT_MODE = 'targeting'
$env:FRBOT_TARGETING_BACKEND = 'real'
python main.py
```

Esperado (uno de estos, ambos válidos):

- ✅ SUCCESS: target lock confirmado → exit 0
- ❌ ABORT con razón explícita (ejemplos válidos):
  - `targeting_window_binding_lost`
  - `targeting_unstable_or_ambiguous`
  - `target_not_acquired`

Verificación manual:

☐ `diagnostics/runtime.log` contiene:
- `candidates_count`
- `selected_target`
☐ No hay clicks repetidos (una acción por tick cuando aplica)
☐ No selecciona targets “fantasma”

### 2️⃣ Healing-only (REAL)

Objetivo: probar lectura HP/MP + cooldown observable.

Pasos:

☐ `FRBOT_MODE=healing`
☐ `FRBOT_HEALING_BACKEND=real`
☐ Bajar HP manualmente
☐ Ejecutar:

```powershell
$env:FRBOT_MODE = 'healing'
$env:FRBOT_HEALING_BACKEND = 'real'
python main.py
```

Esperado:

- Si HP < threshold:
  - 1 spell
  - Evidencia AFTER: HP↑ o cooldown visible o feedback visible
- Si no hay evidencia: abort `heal_unverified`

☐ Nunca spamea spells (máximo 1 input por intento)

### 3️⃣ Combat-only (target locked)

Objetivo: atacar solo si el target está realmente lockeado.

Pasos:

☐ Lockear target manualmente
☐ `FRBOT_MODE=combat`
☐ `FRBOT_COMBAT_BACKEND=real`

Esperado:

- 1 ataque → 1 evidencia: feedback visual, daño, o cooldown
- Si falta evidencia: abort `combat_unverified_attack`

☐ No ataca sin target
☐ No sigue atacando tras abort

### 4️⃣ Cavebot-only (1 waypoint)

Objetivo: probar progreso semántico real.

Pasos:

☐ `FRBOT_MODE=cavebot`
☐ `FRBOT_CAVEBOT_BACKEND=real`
☐ 1 waypoint corto y claro

Esperado:

- Marker detectado en minimapa
- Progreso medible hacia waypoint
- 1 input → 1 evidencia

Aborts válidos (ejemplos):

- `cavebot_marker_not_found`
- `cavebot_no_progress`
- `cavebot_wrong_direction`
- `cavebot_waypoint_stuck`

☐ Nunca “camina” sin progreso
☐ Nunca loop infinito

### 5️⃣ Looting (Premium / Free)

Objetivo: confirmar delta real de inventario.

Pasos:

☐ Matar 1 mob
☐ `FRBOT_MODE=looting`
☐ `FRBOT_LOOTING_BACKEND=real`

Esperado:

- AFTER inventory: gold/items/capacity_used cambian (delta semántico)
- Si no hay delta (o no se puede verificar): abort típico `looting_unverified_loot`

☐ No acepta “container abierto” como éxito
☐ 1 input máximo por tick (press o click)

### 6️⃣ Deposit

Objetivo: mover items/gold al depot con evidencia real.

Pasos:

☐ Depot abierto/visible
☐ `FRBOT_MODE=deposit`
☐ `FRBOT_DEPOSIT_BACKEND=real`

Esperado:

- Inventory ↓
- Depot ↑

Aborts típicos:

- `deposit_no_inventory_delta`
- `deposit_depot_not_open`
- `deposit_partial_failure`

### 7️⃣ Trade

Objetivo: validar NPC correcto + delta real.

Pasos:

☐ Ventana de trade abierta con el NPC correcto
☐ `FRBOT_MODE=trade`
☐ `FRBOT_TRADE_BACKEND=real`

Esperado:

- gold/items cambian
- NPC identity confirmada

Aborts:

- `trade_no_inventory_delta`
- `trade_wrong_npc`
- `trade_unverified_action`

---

## 🛠️ PLAN DE ACCIÓN CUANDO ALGO FALLA

Caso A — Aborta demasiado pronto (falsos negativos)

Síntoma: aborts legítimos pero muy frecuentes.

Acciones:

- Guardar `diagnostics/fatal.log` y `diagnostics/runtime.log` (si existe)
- (Recomendado) Activar dumps automáticos: `FRBOT_DUMP_FRAMES=1` → escribe PPMs en `diagnostics/frames/`
- (One-command) Ejecutar calibración + dumps + resumen: `.\calibrate_and_diagnose.ps1 -Mode cavebot -Backend real -ConfigPath <ruta_a_rois.json>`
- Capturar evidencia manual (screenshot BEFORE/AFTER) para comparar ROIs/escala
- Identificar: ROI mal calibrado, escala UI distinta, tema gráfico distinto
- Ajustar semántica (no thresholds ciegos): marker detection, OCR estructural, bounding boxes

❌ No bajar validaciones
✔️ Mejorar evidencia

Caso B — No aborta cuando debería (grave)

Síntoma: sigue actuando sin progreso real.

Acciones inmediatas:

- Bloquear gate completo
- Forzar max_attempts = 1 y abort inmediato
- Añadir test que reproduzca el falso positivo

Este caso nunca se parchea “rápido”.

Caso C — Funciona en mock, falla en real

Diagnóstico típico: captura ≠ realidad del cliente.

Acciones:

- Capturar evidencia manual (screenshots) y comparar con encoding/ROIs
- Ajustar semántica (no lógica de control)

---

## 🅱️ PLAN B — SI EXISTE ANTI-CHEAT / BLOQUEO DE CAPTURA

🅱️1 Captura fuera del proceso (bajo riesgo)

- MSS/DXGI
- binding por HWND estricto
- checksum/estabilidad de región

✔️ Es el camino actual

🅱️2 Captura indirecta del cliente (riesgo medio)

Ejemplos:

- Duplicación de minimapa (log/export/archivo)
- APIs públicas / hooks documentados
- Lectura de memoria solo lectura (si existe API oficial)

🅱️3 Instrumentación directa / inyección (alto riesgo)

❌ NO recomendado
❌ Riesgo de ban
❌ Cambia el contrato del sistema

---

## 🧭 CONCLUSIÓN HONESTA

Hoy el sistema:

- No miente
- No actúa a ciegas
- Falla ruidosamente
- No entra en loops peligrosos

Eso es lo máximo exigible antes de producción real.
