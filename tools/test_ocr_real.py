#!/usr/bin/env python3
"""Test OCR with real battle list image."""

import os
os.environ['FRBOT_OCR_DEBUG'] = '1'
os.environ['FRBOT_OCR_DUMP_ROIS'] = '1'

from PIL import Image
from runtime.battle_list_ocr import run_ocr_pipeline

# Test with real image
img = Image.open('battleatacklist.png')
print(f'Image size: {img.size}')

result = run_ocr_pipeline(img)
print(f'Entities: {result["entities_count"]}')
print(f'Method: {result.get("detection_method", "none")}')
print(f'Fallback used: {result.get("fallback_used", False)}')
print(f'Profile: {result["profile_name"]}')
print()
print('Entities:')
for e in result.get('entities', [])[:10]:
    print(f'  - {e.get("name", "?")} (conf={e.get("confidence", 0):.2f}, bbox={e.get("bbox")})')
