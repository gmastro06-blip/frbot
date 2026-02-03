from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.hdmi_capture_real import HdmiCaptureReal
from runtime.pacing import sleep_ms

def main() -> int:
    cap = HdmiCaptureReal(device_index=0)

    print("Verifying HDMI capture...")
    vr = cap.verify()
    print("Verification:", vr)

    if not vr.ok:
        print("❌ FAIL")
        return 1

    cap.open()

    print("Grabbing 5 frames...")
    last_digest = None

    for i in range(5):
        frame = cap.grab()
        print(
            f"[{i}] {frame.width}x{frame.height} "
            f"ts={frame.timestamp_ms} "
            f"digest={frame.digest_hex[:12]}"
        )

        if last_digest and last_digest == frame.digest_hex:
            print("⚠️ WARNING: identical frame digest")

        last_digest = frame.digest_hex
        sleep_ms(300.0)

    cap.close()
    print("✅ PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
