from __future__ import annotations

import base64
import os
from time import sleep
from typing import Any

try:
    import serial  # type: ignore
    from serial.serialutil import SerialException  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    serial = None  # type: ignore

    class SerialException(Exception):
        pass


_arduino_serial: Any | None = None
_warned_unavailable = False


def _get_arduino_serial() -> Any | None:
    global _arduino_serial, _warned_unavailable

    if _arduino_serial is not None:
        return _arduino_serial

    if serial is None:
        if not _warned_unavailable:
            _warned_unavailable = True
            print(
                "[fenril] pyserial is not installed; Arduino serial will be disabled. "
                "Install it with 'pip install pyserial' or run via Poetry."
            )
        return None

    port = os.environ.get('FENRIL_ARDUINO_PORT', 'COM33')
    baudrate = int(os.environ.get('FENRIL_ARDUINO_BAUDRATE', '115200'))

    try:
        _arduino_serial = serial.Serial(port, baudrate, timeout=1)
        return _arduino_serial
    except (SerialException, OSError) as exc:
        if not _warned_unavailable:
            _warned_unavailable = True
            print(
                f"[fenril] Arduino serial unavailable ({port}): {exc}. "
                "Keyboard/mouse via Arduino will be disabled. "
                "Set FENRIL_ARDUINO_PORT to override."
            )
        return None


def sendCommandArduino(command: str) -> None:
    arduino_serial = _get_arduino_serial()
    if arduino_serial is None:
        return

    command_bytes = command.encode('utf-8')
    command_base64 = base64.b64encode(command_bytes).decode('utf-8') + '\n'
    arduino_serial.write(command_base64.encode())
    sleep(0.01)