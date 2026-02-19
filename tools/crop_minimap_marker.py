from pathlib import Path
import json
from PIL import Image

BASE = Path('diagnostics/frames_full/20260218_213854')
TRACE = BASE / 'cavebot_trace.jsonl'
LAST = BASE / 'cavebot_full_last_result.json'
CFG = Path('config/rois_prod_full.json')

def load_ppm(path: Path):
    data = path.read_bytes()
    parts = data.split(b'\n', 3)
    wh = parts[1].strip().split()
    w = int(wh[0]); h = int(wh[1])
    raw = parts[3]
    return Image.frombytes('RGB', (w, h), raw)

def main():
    trace_lines = TRACE.read_text().strip().splitlines()
    last_line = trace_lines[-1]
    entry = json.loads(last_line)
    marker = entry.get('before_marker') or entry.get('after_marker')
    if marker is None:
        print('No marker in trace')
        return
    mx = int(marker['x_px']); my = int(marker['y_px'])

    cfg = json.loads(CFG.read_text())
    minimap = cfg['rois']['minimap']
    mm_x = int(minimap['x']); mm_y = int(minimap['y'])

    full_x = mm_x + mx
    full_y = mm_y + my

    before_ppm = BASE / 'cavebot_full_20260218-223929_cavebot_wrong_direction_before.ppm'
    after_ppm = BASE / 'cavebot_full_20260218-223929_cavebot_wrong_direction_after.ppm'

    img_before = load_ppm(before_ppm)
    img_after = load_ppm(after_ppm)

    box_size = 200
    half = box_size // 2
    def crop_and_save(img, center_x, center_y, out_name):
        left = max(0, center_x - half)
        top = max(0, center_y - half)
        right = min(img.width, center_x + half)
        bottom = min(img.height, center_y + half)
        crop = img.crop((left, top, right, bottom))
        crop.save(BASE / out_name)
        print('Wrote', out_name, 'box:', left, top, right, bottom)

    crop_and_save(img_before, full_x, full_y, 'cavebot_before_crop.png')
    crop_and_save(img_after, full_x, full_y, 'cavebot_after_crop.png')

if __name__ == '__main__':
    main()
