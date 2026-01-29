import sys
from pathlib import Path
from types import ModuleType

# Ensure the repository root is on sys.path so tests can import `src.*`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Some unit tests patch `pyautogui.*` directly. In CI/headless environments
# `pyautogui` often isn't available (or isn't installable). Provide a small
# stub so `mocker.patch('pyautogui.foo')` works.
try:  # pragma: no cover
    import pyautogui  # noqa: F401
except Exception:  # pragma: no cover
    stub = ModuleType('pyautogui')
    for name in (
        'hotkey',
        'keyDown',
        'keyUp',
        'press',
        'write',
        'moveTo',
        'dragTo',
        'leftClick',
        'rightClick',
        'scroll',
    ):
        setattr(stub, name, lambda *args, **kwargs: None)
    sys.modules['pyautogui'] = stub
