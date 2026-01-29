import pygetwindow as gw
import re
import win32gui
from ...typings import Context


# TODO: add unit tests
def setTibiaWindowMiddleware(context: Context) -> Context:
    if context['window'] is None:
        windows_list: list[int] = []
        win32gui.EnumWindows(lambda hwnd, param: param.append(hwnd), windows_list)

        regex = re.compile(r'Tibia - .*')
        for hwnd in windows_list:
            title = win32gui.GetWindowText(hwnd)
            if not title:
                continue
            if regex.match(title):
                context['window'] = gw.Win32Window(hwnd)
                break
    return context
