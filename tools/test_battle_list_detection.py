#!/usr/bin/env python3
"""Battle list detection test script.

Usage:
    python tools/test_battle_list_detection.py [--mock] [--debug] [--image PATH]

Flags:
    --mock    : Use mock mode with synthetic monsters
    --debug   : Enable debug dumps (ROIs, preprocessing)
    --image   : Use specific image file
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class DetectionResult:
    """Result from battle list detection."""
    method: str  # 'structure', 'ocr', 'mock', 'none'
    entities_count: int
    entities: list[dict]
    processing_time_ms: int
    debug_artifacts: dict = None

    def __post_init__(self):
        if self.debug_artifacts is None:
            self.debug_artifacts = {}


def run_structure_detection(roi_image, debug: bool = False) -> DetectionResult:
    """Detect battle list using structure (icons + HP bars)."""
    from runtime.battle_list_ocr import detect_battle_list_rows

    start = time.time() * 1000
    rows_result = detect_battle_list_rows(roi_image)
    elapsed = int(time.time() * 1000 - start)

    entities = []
    for row in rows_result.monsters:
        entities.append({
            'name': f'row_{row.row_index}',
            'type': 'structure',
            'y_position': row.y_position,
            'height': row.height,
            'has_hp_bar': row.has_hp_bar,
            'bbox': row.name_bbox,
        })

    return DetectionResult(
        method='structure',
        entities_count=len(entities),
        entities=entities,
        processing_time_ms=elapsed,
    )


def run_ocr_detection(roi_image, debug: bool = False, debug_dir: Path = None) -> DetectionResult:
    """Detect battle list using OCR."""
    from runtime.battle_list_ocr import detect_monsters_with_ocr, preprocess_for_ocr

    start = time.time() * 1000

    # Preprocess
    processed = preprocess_for_ocr(roi_image, debug=debug)

    # Debug: save preprocessed
    if debug and debug_dir:
        processed.save(debug_dir / 'ocr_preprocessed.png')

    # OCR
    monsters = detect_monsters_with_ocr(roi_image)
    elapsed = int(time.time() * 1000 - start)

    entities = []
    for m in monsters:
        entities.append({
            'name': m['name'],
            'type': 'ocr',
            'original': m.get('original', ''),
            'confidence': m.get('confidence', 0),
            'match_type': m.get('match_type', 'unknown'),
            'bbox': m.get('bbox'),
        })

    return DetectionResult(
        method='ocr',
        entities_count=len(entities),
        entities=entities,
        processing_time_ms=elapsed,
        debug_artifacts={'preprocessed': processed} if debug else {},
    )


def run_mock_detection() -> DetectionResult:
    """Mock detection with synthetic data."""
    from runtime.battle_list_semantics import BattleListObservation, BattleListEntry, Rect

    start = time.time() * 1000

    # Create mock observation (3 monsters)
    entries = [
        BattleListEntry(
            name='Rat',
            screen_bbox=Rect(x=20, y=10, width=80, height=16),
            is_attackable=True,
            hp_bar_visible=True,
            highlighted=False,
            row_index=0,
        ),
        BattleListEntry(
            name='Spider',
            screen_bbox=Rect(x=20, y=30, width=80, height=16),
            is_attackable=True,
            hp_bar_visible=True,
            highlighted=False,
            row_index=1,
        ),
        BattleListEntry(
            name='Bat',
            screen_bbox=Rect(x=20, y=50, width=80, height=16),
            is_attackable=True,
            hp_bar_visible=True,
            highlighted=False,
            row_index=2,
        ),
    ]

    obs = BattleListObservation(
        container_bbox=Rect(x=0, y=0, width=100, height=80),
        entries=tuple(entries),
    )

    elapsed = int(time.time() * 1000 - start)

    entities = []
    for e in obs.entries:
        entities.append({
            'name': e.name,
            'type': 'mock',
            'row_index': e.row_index,
            'hp_bar_visible': e.hp_bar_visible,
            'is_attackable': e.is_attackable,
        })

    return DetectionResult(
        method='mock',
        entities_count=len(entities),
        entities=entities,
        processing_time_ms=elapsed,
    )


def run_full_pipeline(image_path: Optional[str] = None, mock: bool = False, debug: bool = False) -> DetectionResult:
    """Run full detection pipeline with fallback."""
    debug_dir = None
    if debug:
        debug_dir = Path('diagnostics') / 'test_detection'
        debug_dir.mkdir(parents=True, exist_ok=True)

    if mock:
        return run_mock_detection()

    if image_path:
        from PIL import Image
        roi_image = Image.open(image_path)
        print(f"Loaded image: {image_path}, size: {roi_image.size}")
    else:
        # Try to find latest battle list ROI
        frame_dir = Path('diagnostics/frames_emergency')
        if frame_dir.exists():
            files = list(frame_dir.glob('*battle_list*roi*.ppm'))
            if files:
                files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                from PIL import Image
                roi_image = Image.open(files[0])
                print(f"Using latest: {files[0].name}, size: {roi_image.size}")
            else:
                print("No battle list ROI images found. Use --image PATH")
                sys.exit(1)
        else:
            print("No frames directory. Use --image PATH or --mock")
            sys.exit(1)

    # Try OCR first (more accurate if it works)
    ocr_result = run_ocr_detection(roi_image, debug=debug, debug_dir=debug_dir)
    if ocr_result.entities_count > 0:
        print(f"\n=== OCR Detection ===")
        print(f"Entities: {ocr_result.entities_count}")
        print(f"Time: {ocr_result.processing_time_ms}ms")
        for e in ocr_result.entities:
            print(f"  - {e['name']} (conf={e.get('confidence', 0):.2f})")
        return ocr_result

    # Fallback to structure
    struct_result = run_structure_detection(roi_image, debug=debug)
    if struct_result.entities_count > 0:
        print(f"\n=== Structure Detection (fallback) ===")
        print(f"Entities: {struct_result.entities_count}")
        print(f"Time: {struct_result.processing_time_ms}ms")
        for e in struct_result.entities:
            print(f"  - {e['name']}: y={e['y_position']}, h={e['height']}")
        return struct_result

    # No detection
    return DetectionResult(
        method='none',
        entities_count=0,
        entities=[],
        processing_time_ms=0,
    )


def main():
    parser = argparse.ArgumentParser(description='Battle list detection test')
    parser.add_argument('--mock', action='store_true', help='Use mock mode')
    parser.add_argument('--debug', action='store_true', help='Enable debug dumps')
    parser.add_argument('--image', type=str, help='Path to battle list ROI image')
    args = parser.parse_args()

    print("=" * 60)
    print("BATTLE LIST DETECTION TEST")
    print("=" * 60)

    result = run_full_pipeline(image_path=args.image, mock=args.mock, debug=args.debug)

    print("\n" + "=" * 60)
    print("RESULT SUMMARY")
    print("=" * 60)
    print(f"Method: {result.method}")
    print(f"Entities count: {result.entities_count}")
    print(f"Processing time: {result.processing_time_ms}ms")

    if result.entities_count > 0:
        print("\n[PASS] entities_count > 0")
        sys.exit(0)
    else:
        print("\n[FAIL] No entities detected")
        sys.exit(1)


if __name__ == '__main__':
    main()
