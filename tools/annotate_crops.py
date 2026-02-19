from pathlib import Path
from PIL import Image, ImageDraw, ImageChops
import json

BASE = Path('diagnostics/frames_full/20260218_213854')
CROP_BEFORE = BASE / 'cavebot_before_crop.png'
CROP_AFTER = BASE / 'cavebot_after_crop.png'
TRACE = BASE / 'cavebot_trace.jsonl'

def read_last_positions():
    lines = TRACE.read_text().strip().splitlines()
    last = json.loads(lines[-1])
    before = last.get('before_marker')
    after = last.get('after_marker')
    return before, after

def annotate(img_path: Path, pos_rel, out_path: Path, label='B'):
    img = Image.open(img_path).convert('RGBA')
    draw = ImageDraw.Draw(img)
    x, y = int(pos_rel[0]), int(pos_rel[1])
    r = 8
    draw.ellipse((x-r, y-r, x+r, y+r), outline='red', width=3)
    draw.text((x+r+2, y-r), label, fill='yellow')
    img.save(out_path)
    return out_path

def make_diff(a_path: Path, b_path: Path, out_path: Path):
    a = Image.open(a_path).convert('RGB')
    b = Image.open(b_path).convert('RGB')
    diff = ImageChops.difference(a, b)
    # amplify
    diff = diff.convert('L').point(lambda p: min(255, p*6)).convert('RGB')
    diff.save(out_path)
    return out_path

def main():
    before_marker, after_marker = read_last_positions()
    # crop box used earlier: left=1725 top=7
    crop_left = 1725
    crop_top = 7
    if before_marker is None:
        print('No marker found in trace')
        return
    # use abort entry markers (pixel coords inside minimap)
    b_x = int(before_marker['x_px'])
    b_y = int(before_marker['y_px'])
    a_x = int(after_marker['x_px']) if after_marker else b_x
    a_y = int(after_marker['y_px']) if after_marker else b_y

    # map to full then to crop-relative
    # minimap roi origin from config was used earlier: minimap.x=1751 minimap.y=25
    mm_x = 1751
    mm_y = 25
    b_full_x = mm_x + b_x
    b_full_y = mm_y + b_y
    a_full_x = mm_x + a_x
    a_full_y = mm_y + a_y

    b_rel = (b_full_x - crop_left, b_full_y - crop_top)
    a_rel = (a_full_x - crop_left, a_full_y - crop_top)

    annotated_before = BASE / 'cavebot_before_annot.png'
    annotated_after = BASE / 'cavebot_after_annot.png'
    make_diff(CROP_BEFORE, CROP_AFTER, BASE / 'cavebot_crop_diff.png')
    annotate(CROP_BEFORE, b_rel, annotated_before, label='before')
    annotate(CROP_AFTER, a_rel, annotated_after, label='after')
    print('Wrote annotated and diff images in', BASE)

if __name__ == '__main__':
    main()
