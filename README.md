# fenril

Windows-first image capture + analysis application.

This repository focuses on grabbing frames (e.g. via DXGI capture), processing them with OpenCV/Numpy, optional OCR via Tesseract, and presenting/controlling workflows via a UI.

## Requirements

- Windows 10/11
- Python 3.11.7 (the project is pinned to this version in pyproject)
- Poetry

Optional (depending on what features you use):

- Tesseract OCR (for text recognition)
- OBS (for consistent, controlled capture setups)
- Virtual display driver (only if you need a second “virtual monitor”)

## Setup

From the repo root:

```bash
poetry install
poetry run python main.py
```

If Poetry complains about the Python version, point it to Python 3.11.7:

```bash
py -0p
poetry env use C:\\Path\\To\\Python311\\python.exe
poetry install
```

## Virtual display (optional)

Some capture setups work best with a dedicated secondary display. If you use a virtual display driver, install it from the vendor package and run its installer commands from an elevated terminal (Run as Administrator).

Example (paths will vary):

```bash
cd C:\\DIRECTORY\\OF\\EXTRACTED\\FOLDER
deviceinstaller64 install usbmmidd.inf usbmmidd
deviceinstaller64 enableidd 1
```

## OBS note (optional)

If you capture a window/game/app through OBS, disabling cursor capture helps keep frames stable (the cursor can occlude UI elements and change pixels).

## Contributing

Issues and pull requests are welcome.
- FOLLOW ENEMY OR GO TO BOX TO KILL MONSTERS (DEPENDS THE CHAR LEVEL) ✔️
