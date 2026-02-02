from __future__ import annotations

import os


def test_win32_module_import_is_safe_on_non_windows() -> None:
	# Importing the module must not attempt to load user32 on non-Windows.
	import adapters.windows.win32 as w

	if os.name != 'nt':
		assert w.user32 is None
		assert w._IS_WINDOWS is False
