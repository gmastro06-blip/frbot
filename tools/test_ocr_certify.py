#!/usr/bin/env python3
"""QA Certification Script for Battle List OCR.

Usage:
    poetry run python tools/test_ocr_certify.py
"""

import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw


def main():
    print("=" * 60)
    print("QA CERTIFICATION: Battle List OCR")
    print("=" * 60)

    os.environ['FRBOT_OCR_MOCK'] = '1'
    os.environ['FRBOT_OCR_DEBUG'] = '1'
    os.environ['FRBOT_OCR_DUMP_ROIS'] = '1'

    passed = 0
    failed = 0

    # Test 1: Mock mode
    print("\n[1/5] Mock mode...")
    try:
        from runtime.battle_list_ocr import detect_monsters_with_ocr
        img = Image.new('RGB', (200, 200), color=(50, 50, 50))
        result = detect_monsters_with_ocr(img)
        if len(result) == 3:
            print(f"  OK: 3 entities")
            passed += 1
        else:
            print(f"  FAIL: {len(result)}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 2: UI profiles
    print("\n[2/5] UI profiles...")
    try:
        from runtime.battle_list_ocr import UI_PROFILES, get_ui_profile
        p1 = get_ui_profile(640, 480)
        p2 = get_ui_profile(1920, 1080)
        if p1.name == 'tibia_low_res' and p2.name == 'tibia_hi_res':
            print(f"  OK: auto-detect works")
            passed += 1
        else:
            print(f"  FAIL: {p1.name}, {p2.name}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 3: Pipeline
    print("\n[3/5] Pipeline...")
    try:
        from runtime.battle_list_ocr import run_ocr_pipeline
        img = Image.new('RGB', (960, 540), color=(50, 50, 50))
        result = run_ocr_pipeline(img)
        if result.get('entities_count', 0) > 0:
            print(f"  OK: entities_count={result['entities_count']}")
            passed += 1
        else:
            print(f"  FAIL: 0 entities")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 4: Dumps
    print("\n[4/5] Debug dumps...")
    try:
        debug_dir = 'diagnostics/ocr_debug'
        if os.path.exists(debug_dir):
            for f in glob.glob(f'{debug_dir}/*.png')[:5]:
                os.remove(f)

        from runtime.battle_list_ocr import run_ocr_pipeline
        img = Image.new('RGB', (200, 200), color=(50, 50, 50))
        run_ocr_pipeline(img)

        dumps = glob.glob(f'{debug_dir}/*.png')
        if len(dumps) > 0:
            print(f"  OK: {len(dumps)} files")
            passed += 1
        else:
            print(f"  FAIL: no dumps")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Tesseract + Fallback
    print("\n[5/5] Tesseract + Fallback...")
    try:
        from runtime.battle_list_ocr import is_tesseract_available, _get_tesseract_cmd
        available = is_tesseract_available()
        resolved, method = _get_tesseract_cmd()
        print(f"  OCR_AVAILABLE: {available}")
        print(f"  RESOLVED_TESSERACT: {resolved}")
        print(f"  RESOLUTION_METHOD: {method}")

        if not available:
            print(f"  NOTE: Set FRBOT_TESSERACT_CMD or ensure Tesseract is in PATH")

        # Test fallback structure (no mock)
        os.environ['FRBOT_OCR_MOCK'] = '0'
        from importlib import reload
        import runtime.battle_list_ocr as blo
        reload(blo)

        img2 = Image.new('RGB', (200, 200), color=(50, 50, 50))
        draw = ImageDraw.Draw(img2)
        for i in range(3):
            y = 20 + i * 25
            draw.rectangle([5, y, 20, y+20], fill=(200, 50, 50))

        result2 = blo.run_ocr_pipeline(img2)
        entities = result2.get('entities', [])

        has_stable_id = all('stable_id' in e for e in entities)
        has_status = all('status' in e for e in entities)

        if has_stable_id and has_status:
            print(f"  OK: {len(entities)} entities with stable_id+status")
            passed += 1
        else:
            print(f"  FAIL: missing stable_id or status")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
