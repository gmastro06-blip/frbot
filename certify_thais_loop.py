#!/usr/bin/env python
"""
Script de certificacion para prod_full en Thais.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import main as main_module


def certify_main() -> int:
    """Ejecuta certificacion continua."""
    print("=" * 60)
    print("FRBOT CERTIFICACION: Thais Cavebot + Healing")
    print("=" * 60)

    # Configuracion principal
    os.environ['FRBOT_MODE'] = 'cavebot_full'
    os.environ['FRBOT_CONFIG_PATH'] = 'rois_prod_full.json'

    # Mas tiempo de ejecución y mas rapido
    os.environ['FRBOT_MAX_TICKS'] = '20000'
    os.environ['FRBOT_SESSION_SECONDS'] = '7200'  # 2 horas
    os.environ['FRBOT_TICK_HZ'] = '30'  # 30 ticks por segundo

    # Healing
    os.environ['FRBOT_ENABLE_HEALING'] = '1'
    os.environ['FRBOT_HEAL_KEY'] = '5'
    os.environ['FRBOT_HEAL_HP_THRESHOLD'] = '0.99'
    os.environ['FRBOT_HEAL_MP_MIN'] = '0.0'
    os.environ['FRBOT_HEAL_HP_INCREASE_MIN'] = '0.01'
    os.environ['FRBOT_HEAL_ALLOW_NO_EVIDENCE'] = '1'

    # Waypoints
    os.environ['FRBOT_CAVEBOT_WAYPOINTS_FILE'] = 'Waypoints/thais_certify_loop.json'

    # Player marker
    os.environ['FRBOT_PLAYER_MARKER_RGB'] = '0,255,0'
    os.environ['FRBOT_PLAYER_MARKER_TOL'] = '30'
    os.environ['FRBOT_PLAYER_MARKER_MIN_PIXELS'] = '8'
    os.environ['FRBOT_PLAYER_MARKER_MIN_FILL_RATIO'] = '0.3'

    # Cavebot - tolerancia maxima
    os.environ['FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG'] = '180'
    os.environ['FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK'] = '1000'
    os.environ['FRBOT_CAVEBOT_STUCK_WINDOW'] = '30'
    os.environ['FRBOT_TRY_FOCUS'] = '1'

    # Input: usar sendinput para que haga focus automaticamente antes de enviar teclas
    os.environ['FRBOT_INPUT_METHOD'] = 'sendinput'

    print(f"\n[CONFIG] Tick Hz: {os.environ.get('FRBOT_TICK_HZ')}")
    print(f"[CONFIG] Max Ticks: {os.environ.get('FRBOT_MAX_TICKS')}")
    print(f"[CONFIG] Session: {os.environ.get('FRBOT_SESSION_SECONDS')}s")
    print(f"[CONFIG] Healing: key={os.environ.get('FRBOT_HEAL_KEY')}, threshold={os.environ.get('FRBOT_HEAL_HP_THRESHOLD')}")
    print(f"[CONFIG] Cavebot: waypoints={os.environ.get('FRBOT_CAVEBOT_WAYPOINTS_FILE')}")
    print(f"[CONFIG] Stuck Window: {os.environ.get('FRBOT_CAVEBOT_STUCK_WINDOW')}")
    print("\nIniciando... Ctrl+C para detener.")

    try:
        result = main_module.main()
        print(f"\n[FINAL] Runtime finalizo con codigo: {result}")
        return result
    except KeyboardInterrupt:
        print("\n[DETENIDO] Usuario interrompio la ejecucion")
        return 0
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(certify_main())
