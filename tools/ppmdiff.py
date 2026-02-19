from PIL import Image, ImageChops
from pathlib import Path

def load_ppm(path: Path):
    data = path.read_bytes()
    # parse minimal P6 header
    if not data.startswith(b'P6\n'):
        raise ValueError('Not a P6 PPM')
    # find third newline after header
    parts = data.split(b'\n', 3)
    # parts: [b'P6', b'W H', b'MAX', b'binary...']
    if len(parts) < 4:
        raise ValueError('Malformed PPM')
    wh = parts[1].strip().split()
    w = int(wh[0]); h = int(wh[1])
    raw = parts[3]
    img = Image.frombytes('RGB', (w, h), raw)
    return img

def main():
    base = Path('diagnostics/frames_full/20260218_213854')
    before = base / 'cavebot_full_20260218-223929_cavebot_wrong_direction_before.ppm'
    after = base / 'cavebot_full_20260218-223929_cavebot_wrong_direction_after.ppm'
    out_before = base / 'cavebot_before_thumb.png'
    out_after = base / 'cavebot_after_thumb.png'
    out_diff = base / 'cavebot_diff.png'

    img_before = load_ppm(before)
    img_after = load_ppm(after)

    thumb_size = (640, 360)
    img_before.thumbnail(thumb_size)
    img_after.thumbnail(thumb_size)
    img_before.save(out_before)
    img_after.save(out_after)

    # compute visual diff (on resized copies to make processing fast)
    a = Image.open(out_before).convert('RGB')
    b = Image.open(out_after).convert('RGB')
    diff = ImageChops.difference(a, b)
    # enhance diff by multiplying (simple trick)
    diff = diff.convert('L').point(lambda p: min(255, p*4))
    diff = diff.convert('RGB')
    diff.save(out_diff)

    print('Wrote:', out_before, out_after, out_diff)

if __name__ == '__main__':
    main()
