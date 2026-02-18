#!/usr/bin/env python3
"""OCR Battle List Reproduction Script.

Usage:
    FRBOT_OCR_MOCK=1 python tools/test_ocr_reproduction.py

Environment variables:
    FRBOT_OCR_MOCK=1           # Enable mock mode (no real OCR)
    FRBOT_OCR_DEBUG=1          # Enable debug logging
    FRBOT_OCR_DUMP_ROIS=1     # Dump ROI crops
    FRBOT_OCR_DUMP_PREPROCESS=1 # Dump preprocessed images
    FRBOT_UI_PROFILE=tibia_standard  # UI profile selection

Command line arguments:
    --input PATH    # Input image path (default: ./battleatacklist.png if exists, else synthetic)
    --profile NAME  # UI profile (overrides FRBOT_UI_PROFILE)
    --mock          # Enable mock mode (equivalent to FRBOT_OCR_MOCK=1)

Output:
    - JSON metrics to stdout
    - Debug images to diagnostics/ocr_debug/
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw


def create_test_battle_list_image(width: int = 200, height: int = 200) -> Image.Image:
    """Create a synthetic battle list image for testing."""
    img = Image.new('RGB', (width, height), color=(45, 45, 45))
    draw = ImageDraw.Draw(img)

    # Draw header
    draw.rectangle([0, 0, width, 18], fill=(60, 60, 60))

    # Draw monster rows (icon + name + HP bar)
    row_height = 20
    y_offset = 20

    monsters = [
        ('Dragon', (150, 50, 50)),
        ('Orc', (100, 120, 80)),
        ('Goblin', (80, 140, 60)),
    ]

    for i, (name, color) in enumerate(monsters):
        y = y_offset + i * row_height
        # Icon area (colored)
        draw.rectangle([5, y + 2, 18, y + row_height - 2], fill=color)
        # Name text area (white-ish)
        draw.text((25, y + 3), name, fill=(220, 220, 220))
        # HP bar area (green-ish)
        draw.rectangle([width - 30, y + 4, width - 5, y + row_height - 4], fill=(50, 180, 50))

    return img


def main():
    parser = argparse.ArgumentParser(description='OCR Battle List Reproduction Script')
    parser.add_argument('--input', type=str, default=None,
                        help='Input image path (default: ./battleatacklist.png if exists, else synthetic)')
    parser.add_argument('--profile', type=str, default=None,
                        help='UI profile (overrides FRBOT_UI_PROFILE env var)')
    parser.add_argument('--mock', action='store_true',
                        help='Enable mock mode')
    args = parser.parse_args()

    # Get env config
    mock_mode = os.environ.get('FRBOT_OCR_MOCK', '').strip().lower() in {'1', 'true', 'yes'}
    mock_mode = mock_mode or args.mock
    debug = os.environ.get('FRBOT_OCR_DEBUG', '').strip().lower() in {'1', 'true', 'yes'}
    dump_rois = os.environ.get('FRBOT_OCR_DUMP_ROIS', '').strip().lower() in {'1', 'true', 'yes'}
    dump_preprocess = os.environ.get('FRBOT_OCR_DUMP_PREPROCESS', '').strip().lower() in {'1', 'true', 'yes'}
    profile_name = args.profile or os.environ.get('FRBOT_UI_PROFILE', 'default')

    print("=== OCR Battle List Reproduction Script ===")
    print(f"Mock mode: {mock_mode}")
    print(f"Debug: {debug}")
    print(f"Dump ROIs: {dump_rois}")
    print(f"Dump preprocess: {dump_preprocess}")
    print(f"UI Profile: {profile_name}")
    print()

    # Import after env setup
    from runtime.battle_list_ocr import (
        run_ocr_pipeline,
        get_ui_profile,
        MOCK_MODE,
    )

    profile = get_ui_profile()
    print(f"Active profile: {profile.name}")
    print(f"  - ocr_upscale: {profile.ocr_upscale}")
    print(f"  - ocr_min_confidence: {profile.ocr_min_confidence}")
    print()

    # Determine input source
    default_input = Path('battleatacklist.png')
    input_path = Path(args.input) if args.input else None

    # Check if input file exists
    if input_path and input_path.exists():
        print(f"Loading image from file: {input_path}")
        img = Image.open(input_path)
        print(f"INPUT_SOURCE=FILE path={input_path} size={img.size}")
    elif default_input.exists():
        print(f"Loading image from default path: {default_input}")
        img = Image.open(default_input)
        print(f"INPUT_SOURCE=FILE path={default_input} size={img.size}")
    else:
        # Fall back to synthetic image
        print("Creating test battle list image...")
        img = create_test_battle_list_image()
        print(f"INPUT_SOURCE=SYNTHETIC size={img.size}")
    print()

    # Run OCR pipeline
    print("Running OCR pipeline...")
    start = time.time()
    result = run_ocr_pipeline(img)
    elapsed = time.time() - start

    # Output results
    print()
    print("=== RESULTS ===")
    print(f"Entities found: {result['entities_count']}")
    print(f"Elapsed time: {result['elapsed_ms']}ms")
    print(f"Profile: {result['profile_name']}")
    print()

    if result['entities']:
        print("Entities:")
        for e in result['entities']:
            print(f"  - {e['name']} (confidence: {e['confidence']:.2f}, type: {e['match_type']})")
    else:
        print("WARNING: No entities found!")

    # Metrics summary
    metrics = {
        'timestamp': time.time(),
        'mock_mode': mock_mode,
        'profile': profile_name,
        'entities_count': result['entities_count'],
        'elapsed_ms': result['elapsed_ms'],
        'roi_size': result['roi_size'],
        'entities': result['entities'],
    }

    print()
    print("=== METRICS JSON ===")
    print(json.dumps(metrics, indent=2, default=str))

    # Check success criteria
    print()
    if result['entities_count'] > 0:
        print("SUCCESS: entities_count > 0")
        return 0
    else:
        print("FAILURE: No entities found")
        return 1


if __name__ == '__main__':
    sys.exit(main())
