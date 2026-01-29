import os
try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None

from .ino import sendCommandArduino


_INPUT_BACKEND = os.environ.get('FENRIL_INPUT_BACKEND', 'pyautogui').strip().lower()


def _require_pyautogui():
    if pyautogui is None:
        raise RuntimeError('pyautogui is not available; set FENRIL_INPUT_BACKEND=arduino or install pyautogui')


def _as_key_list(first, rest):
    if rest:
        return [first, *rest]
    if isinstance(first, (list, tuple)):
        return list(first)
    return [first]

def getAsciiFromKey(key):
    if not key:
        return 0

    if not isinstance(key, str):
        return 0

    sanitized = key.lower()

    if sanitized == '?':
        return 63

    if sanitized.isalpha() and len(sanitized) == 1:
        return ord(sanitized)
    
    if sanitized == 'space':
        return 32
    elif sanitized == 'esc':
        return 177
    elif sanitized == 'ctrl':
        return 128
    elif sanitized == 'alt':
        return 130
    elif sanitized == 'shift':
        return 129
    elif sanitized == 'enter':
        return 176
    elif sanitized == 'up':
        return 218
    elif sanitized == 'down':
        return 217
    elif sanitized == 'left':
        return 216
    elif sanitized == 'right':
        return 215
    elif sanitized == 'backspace':
        return 178
    elif sanitized == 'f1':
        return 194
    elif sanitized == 'f2':
        return 195
    elif sanitized == 'f3':
        return 196
    elif sanitized == 'f4':
        return 197
    elif sanitized == 'f5':
        return 198
    elif sanitized == 'f6':
        return 199
    elif sanitized == 'f7':
        return 200
    elif sanitized == 'f8':
        return 201
    elif sanitized == 'f9':
        return 202
    elif sanitized == 'f10':
        return 203
    elif sanitized == 'f11':
        return 204
    elif sanitized == 'f12':
        return 205
    else:
        return 0

def hotkey(*args):
    if not args:
        return

    keys = _as_key_list(args[0], args[1:])

    if _INPUT_BACKEND != 'arduino':
        _require_pyautogui()
        pyautogui.hotkey(*keys)
        return

    for key in keys:
        asciiKey = getAsciiFromKey(key)
        if asciiKey != 0:
            sendCommandArduino(f"keyDown,{asciiKey}")

    for key in keys:
        asciiKey = getAsciiFromKey(key)
        if asciiKey != 0:
            sendCommandArduino(f"keyUp,{asciiKey}")

def keyDown(key: str):
    if _INPUT_BACKEND != 'arduino':
        _require_pyautogui()
        pyautogui.keyDown(key)
        return

    asciiKey = getAsciiFromKey(key)
    if asciiKey != 0:
        sendCommandArduino(f"keyDown,{asciiKey}")

def keyUp(key: str):
    if _INPUT_BACKEND != 'arduino':
        _require_pyautogui()
        pyautogui.keyUp(key)
        return

    asciiKey = getAsciiFromKey(key)
    if asciiKey != 0:
        sendCommandArduino(f"keyUp,{asciiKey}")

def press(*args):
    if not args:
        return

    keys = _as_key_list(args[0], args[1:])

    if _INPUT_BACKEND != 'arduino':
        _require_pyautogui()
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.press(keys)
        return

    for key in keys:
        asciiKey = getAsciiFromKey(key)
        if asciiKey != 0:
            sendCommandArduino(f"press,{asciiKey}")

def write(phrase: str):
    if _INPUT_BACKEND != 'arduino':
        _require_pyautogui()
        pyautogui.write(phrase)
        return

    sendCommandArduino(f"write,{phrase}")
