import pyautogui
import os
from src.shared.typings import XYCoordinate
from .ino import sendCommandArduino


_INPUT_BACKEND = os.environ.get('FENRIL_INPUT_BACKEND', 'pyautogui').strip().lower()

def drag(x1y1: XYCoordinate, x2y2: XYCoordinate):
    if _INPUT_BACKEND != 'arduino':
        pyautogui.moveTo(x1y1[0], x1y1[1])
        pyautogui.dragTo(x2y2[0], x2y2[1], button='left')
        return

    sendCommandArduino(f"moveTo,{int(x1y1[0])},{int(x1y1[1])}")
    sendCommandArduino("dragStart")
    sendCommandArduino(f"moveTo,{int(x2y2[0])},{int(x2y2[1])}")
    sendCommandArduino("dragEnd")

def leftClick(windowCoordinate: XYCoordinate = None):
    if _INPUT_BACKEND != 'arduino':
        if windowCoordinate is None:
            pyautogui.leftClick()
        else:
            pyautogui.leftClick(windowCoordinate[0], windowCoordinate[1])
        return

    if windowCoordinate is None:
        sendCommandArduino("leftClick")
        return
    sendCommandArduino(f"moveTo,{int(windowCoordinate[0])},{int(windowCoordinate[1])}")
    sendCommandArduino("leftClick")

def moveTo(windowCoordinate: XYCoordinate):
    if _INPUT_BACKEND != 'arduino':
        pyautogui.moveTo(windowCoordinate[0], windowCoordinate[1])
        return

    sendCommandArduino(f"moveTo,{int(windowCoordinate[0])},{int(windowCoordinate[1])}")

def rightClick(windowCoordinate: XYCoordinate = None):
    if _INPUT_BACKEND != 'arduino':
        if windowCoordinate is None:
            pyautogui.rightClick()
        else:
            pyautogui.rightClick(windowCoordinate[0], windowCoordinate[1])
        return

    if windowCoordinate is None:
        sendCommandArduino("rightClick")
        return
    sendCommandArduino(f"moveTo,{int(windowCoordinate[0])},{int(windowCoordinate[1])}")
    sendCommandArduino("rightClick")

def scroll(clicks: int):
    if _INPUT_BACKEND != 'arduino':
        pyautogui.scroll(clicks)
        return

    curX, curY = pyautogui.position()
    sendCommandArduino(f"scroll,{curX}, {curY}, {clicks}")
