"""OCR for battle list using Tesseract.

Provides both structure-based detection and OCR text extraction.
Features:
- Structure-based row detection (icon + HP bar analysis)
- OCR with Tesseract for name extraction
- Mock mode for deterministic testing
- Debug dump instrumentation
- Configurable UI profiles (Tibia-like)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import unicodedata

from PIL import Image, ImageOps
import numpy as np
from contracts.capture import Frame
from contracts.evidence import Roi


# === Configuration ===

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)

# Mock mode for deterministic testing
def is_mock_mode() -> bool:
    """Check if mock mode is enabled (evaluated dynamically for pytest compatibility)."""
    return _env_bool('FRBOT_OCR_MOCK', False)

# Backward compatible alias
MOCK_MODE = is_mock_mode()

def is_ocr_debug() -> bool:
    """Check if OCR debug is enabled (evaluated dynamically)."""
    return _env_bool('FRBOT_OCR_DEBUG', False)

def is_dump_rois() -> bool:
    """Check if ROI dumps enabled."""
    return _env_bool('FRBOT_OCR_DUMP_ROIS', False)

def is_dump_preprocess() -> bool:
    """Check if preprocessed image dumps enabled."""
    return _env_bool('FRBOT_OCR_DUMP_PREPROCESS', False)

# Backward compatible aliases
OCR_DEBUG = is_ocr_debug()
OCR_DUMP_ROIS = is_dump_rois()
OCR_DUMP_PREPROCESS = is_dump_preprocess()


# === Mock Data for Deterministic Testing ===

MOCK_MONSTERS = [
    {'name': 'Orc', 'confidence': 0.95},
    {'name': 'Goblin', 'confidence': 0.92},
    {'name': 'Spider', 'confidence': 0.88},
    {'name': 'Rat', 'confidence': 0.85},
]


def generate_mock_ocr_results() -> list[dict[str, Any]]:
    """Generate mock OCR results for deterministic testing.

    Returns list of monster dicts with entities_count > 0.
    """
    return list(MOCK_MONSTERS)


# === UI Profiles (Tibia-like) ===

@dataclass
class UIProfile:
    """Configuration for a specific Tibia UI profile."""
    name: str
    scale_factor: float = 1.0
    min_row_height: int = 8
    max_row_height: int = 50
    icon_width: int = 15
    header_height: int = 0
    # OCR thresholds
    ocr_upscale: int = 3
    ocr_min_confidence: float = 0.5
    # Color thresholds for structure detection
    colored_threshold: int = 15
    hp_bar_green_min: int = 60
    hp_bar_red_max: int = 90


# Predefined UI profiles
UI_PROFILES: dict[str, UIProfile] = {
    'default': UIProfile(
        name='default',
        scale_factor=1.0,
        min_row_height=8,
        max_row_height=50,
        icon_width=15,
        header_height=0,
        ocr_upscale=3,
        ocr_min_confidence=0.5,
        colored_threshold=15,
        hp_bar_green_min=60,
        hp_bar_red_max=90,
    ),
    'tibia_standard': UIProfile(
        name='tibia_standard',
        scale_factor=1.0,
        min_row_height=12,
        max_row_height=40,
        icon_width=18,
        header_height=20,
        ocr_upscale=3,
        ocr_min_confidence=0.6,
        colored_threshold=20,
        hp_bar_green_min=80,
        hp_bar_red_max=70,
    ),
    'tibia_hi_res': UIProfile(
        name='tibia_hi_res',
        scale_factor=1.5,
        min_row_height=18,
        max_row_height=60,
        icon_width=25,
        header_height=25,
        ocr_upscale=4,
        ocr_min_confidence=0.55,
        colored_threshold=25,
        hp_bar_green_min=100,
        hp_bar_red_max=60,
    ),
    'tibia_low_res': UIProfile(
        name='tibia_low_res',
        scale_factor=0.75,
        min_row_height=6,
        max_row_height=30,
        icon_width=12,
        header_height=0,
        ocr_upscale=2,
        ocr_min_confidence=0.4,
        colored_threshold=10,
        hp_bar_green_min=50,
        hp_bar_red_max=100,
    ),
}


def get_ui_profile(frame_width: int = 0, frame_height: int = 0,
                   container_bbox: tuple[int, int, int, int] | None = None,
                   estimated_row_height: int | None = None,
                   detect_from_image: Image.Image | None = None) -> UIProfile:
    """Get current UI profile from environment or auto-detect from features.

    Args:
        frame_width: Frame width for auto-detection
        frame_height: Frame height for auto-detection
        container_bbox: (x, y, width, height) of battle list container
        estimated_row_height: Estimated row height in pixels

    Selection priority:
    1. FRBOT_UI_PROFILE or FRBOT_OCR_PROFILE env var (explicit override)
    2. Feature-based detection (container_bbox + row_height)
    3. Resolution-based detection (frame_width/height)
    4. Default fallback

    Logs selection reason if OCR_DEBUG enabled.
    """
    # Priority 1: Explicit override
    explicit_profile = os.environ.get('FRBOT_UI_PROFILE', '').strip().lower()
    if not explicit_profile:
        explicit_profile = os.environ.get('FRBOT_OCR_PROFILE', '').strip().lower()

    if explicit_profile and explicit_profile in UI_PROFILES:
        if OCR_DEBUG:
            print(f'[OCR] Profile: {explicit_profile} (explicit override)')
        return UI_PROFILES[explicit_profile]

    # Priority 2: Feature-based detection (container_bbox + row_height)
    if container_bbox is not None and estimated_row_height is not None:
        cx, cy, cw, ch = container_bbox
        # Calculate scale factor from row height
        # Standard Tibia: ~16px at 1x, ~24px at 1.5x, ~32px at 2x
        if estimated_row_height >= 28:
            profile = 'tibia_hi_res'
        elif estimated_row_height >= 14:
            profile = 'tibia_standard'
        else:
            profile = 'tibia_low_res'

        if OCR_DEBUG:
            print(f'[OCR] Profile: {profile} (feature-based: container={cw}x{ch}, row_height={estimated_row_height})')
        return UI_PROFILES[profile]

    # Priority 3: Resolution-based detection
    if frame_width > 0 and frame_height > 0:
        if frame_width >= 1920 or frame_height >= 1080:
            if OCR_DEBUG:
                print(f'[OCR] Profile: tibia_hi_res (resolution: {frame_width}x{frame_height})')
            return UI_PROFILES['tibia_hi_res']
        elif frame_width <= 800 or frame_height <= 600:
            if OCR_DEBUG:
                print(f'[OCR] Profile: tibia_low_res (resolution: {frame_width}x{frame_height})')
            return UI_PROFILES['tibia_low_res']
        else:
            if OCR_DEBUG:
                print(f'[OCR] Profile: tibia_standard (resolution: {frame_width}x{frame_height})')
            return UI_PROFILES['tibia_standard']

    # Fallback: check env vars
    frame_width_env = os.environ.get('FRBOT_FRAME_WIDTH', '').strip()
    frame_height_env = os.environ.get('FRBOT_FRAME_HEIGHT', '').strip()

    if frame_width_env and frame_height_env:
        try:
            w = int(frame_width_env)
            h = int(frame_height_env)
            if w >= 1920 or h >= 1080:
                return UI_PROFILES['tibia_hi_res']
            elif w <= 800 or h <= 600:
                return UI_PROFILES['tibia_low_res']
            else:
                return UI_PROFILES['tibia_standard']
        except ValueError:
            pass

    # Priority 4: Default fallback
    if OCR_DEBUG:
        print('[OCR] Profile: default (fallback)')
    return UI_PROFILES['default']


def _ensure_debug_dir() -> Path:
    """Ensure debug directory exists."""
    debug_dir = Path('diagnostics') / 'ocr_debug'
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def dump_roi_crop(roi_image: Image.Image, label: str) -> None:
    """Dump ROI crop for debugging."""
    if not OCR_DUMP_ROIS:
        return
    try:
        debug_dir = _ensure_debug_dir()
        stamp = int(time.time() * 1000)
        roi_image.save(debug_dir / f'{stamp}_{label}.png')
    except Exception as e:
        print(f'[OCR] Failed to dump ROI: {e}')


def dump_preprocessed(processed: Image.Image, label: str) -> None:
    """Dump preprocessed image for debugging."""
    if not OCR_DUMP_PREPROCESS:
        return
    try:
        debug_dir = _ensure_debug_dir()
        stamp = int(time.time() * 1000)
        processed.save(debug_dir / f'{stamp}_{label}_preprocessed.png')
    except Exception as e:
        print(f'[OCR] Failed to dump preprocessed: {e}')


def log_ocr_metrics(
    roi_size: tuple[int, int],
    profile_name: str,
    elapsed_ms: int,
    raw_texts: int,
    entities_found: int,
) -> None:
    """Log OCR metrics for debugging/iteration."""
    if not OCR_DEBUG:
        return
    print(f'[OCR] Metrics: roi={roi_size}, profile={profile_name}, '
          f'time={elapsed_ms}ms, raw_texts={raw_texts}, entities={entities_found}')


# === Tesseract Resolution & Verification ===

# Track resolution details for logging
TESSERACT_RESOLUTION_METHOD = None  # ENV / WHICH / COMMON_PATH / NONE
TESSERACT_VERSION_CHECK = False     # True if --version succeeded


def _get_tesseract_cmd() -> tuple[str | None, str]:
    """Get Tesseract executable path with multiple fallback strategies.

    Resolution order:
    1. FRBOT_TESSERACT_CMD env var (highest priority)
    2. shutil.which('tesseract') (process PATH)
    3. Common Windows installation paths (fallback)
    4. Return (None, 'NONE') if not found

    Returns:
        (path, method) tuple
    """
    # Priority 1: ENV override
    env_path = os.environ.get('FRBOT_TESSERACT_CMD', '').strip()
    if env_path:
        # Check if it's a full path or command name
        if os.path.isfile(env_path):
            return env_path, 'ENV'
        # Try as command name via which
        import shutil
        which_result = shutil.which(env_path)
        if which_result:
            return which_result, 'ENV'

    # Priority 2: Process PATH (shutil.which)
    import shutil
    system_path = shutil.which('tesseract')
    if system_path:
        return system_path, 'WHICH'

    # Priority 3: Common OS-specific paths
    import platform
    system = platform.system()

    if system == 'Windows':
        common_paths = [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        ]
    elif system == 'Darwin':  # macOS
        common_paths = [
            '/usr/local/bin/tesseract',
            '/opt/homebrew/bin/tesseract',
            '/usr/bin/tesseract',
        ]
    else:  # Linux
        common_paths = [
            '/usr/bin/tesseract',
            '/usr/local/bin/tesseract',
        ]

    for path in common_paths:
        if os.path.isfile(path):
            return path, 'COMMON_PATH'

    return None, 'NONE'


def _verify_tesseract(cmd: str) -> bool:
    """Verify Tesseract executable works in THIS process.

    Runs `<cmd> --version` via subprocess to confirm executability.

    Args:
        cmd: Path to tesseract executable

    Returns:
        True if --version succeeds, False otherwise
    """
    import subprocess
    try:
        result = subprocess.run(
            [cmd, '--version'],
            capture_output=True,
            timeout=10,
            text=True
        )
        if result.returncode == 0 and result.stdout:
            return True
    except Exception:
        pass
    return False


# Initialize Tesseract with verification
TESSERACT_AVAILABLE = False
TESSERACT_CMD = None

_resolved_cmd, TESSERACT_RESOLUTION_METHOD = _get_tesseract_cmd()

if _resolved_cmd:
    if _verify_tesseract(_resolved_cmd):
        TESSERACT_CMD = _resolved_cmd
        TESSERACT_AVAILABLE = True
        TESSERACT_VERSION_CHECK = True
    else:
        # Path exists but --version failed - log warning
        if OCR_DEBUG:
            print(f'[OCR] Tesseract found at {_resolved_cmd} but --version failed')
else:
    if OCR_DEBUG:
        print('[OCR] Tesseract not found in PATH or common paths')


def is_tesseract_available() -> bool:
    """Check if Tesseract is available at runtime."""
    global TESSERACT_AVAILABLE
    if TESSERACT_AVAILABLE:
        return True

    # Retry verification
    _resolved_cmd, _method = _get_tesseract_cmd()
    if _resolved_cmd:
        if _verify_tesseract(_resolved_cmd):
            global TESSERACT_CMD, TESSERACT_RESOLUTION_METHOD, TESSERACT_VERSION_CHECK
            TESSERACT_CMD = _resolved_cmd
            TESSERACT_RESOLUTION_METHOD = _method
            TESSERACT_VERSION_CHECK = True
            TESSERACT_AVAILABLE = True
            return True

    return False


# Common monster names for filtering OCR results
COMMON_MONSTERS = [
    # Spanish
    'Araña', 'Abeja', 'Mosca', 'Escorpión', 'Gusano', 'Rata', 'Ratas', 'Cucaracha',
    'Orco', 'Goblin', 'Troll', 'Cíclope', 'Ogro', 'Dragón', 'Demonio',
    'Esqueleto', 'Zombie', 'Momia', 'Vampiro', 'Lobisón', 'Homúnculo',
    'Slime', 'Murciélago', 'Barbazú', 'Hada', 'Elfo', 'Enano',
    # English
    'Spider', 'Bee', 'Fly', 'Scorpion', 'Worm', 'Rat', 'Rats', 'Cockroach',
    'Orc', 'Goblin', 'Troll', 'Cyclops', 'Ogre', 'Dragon', 'Demon',
    'Skeleton', 'Zombie', 'Mummy', 'Vampire', 'Slime', 'Bat',
    # Variants (common OCR errors / compound names)
    'Cave Rat', 'CaveRat', 'Cave', 'Giant Spider', 'Wild Rat',
    'Giant', 'Elder', 'Young', 'Adult', 'Baby',
    # Very common (Tibia basic)
    'Wolf', 'Bear', 'Snake', 'Ghost', 'Wraith', 'Dragon Lord',
    'Dwarf', 'Elf', 'Behemoth', 'Mammoth', 'Tortoise', 'Crab', 'Leech',
    # OTS common
    'Crystal', 'Golem', 'Elemental', 'Warlock', 'Knight',
    # Bugs/Insects
    'Ant', 'Wasp', 'Hornet', 'Beetle', 'Centipede', 'Tarantula',
    # Undead
    'Lich', 'Necromancer', 'Ghoul',
    # Magical
    'Energy', 'Fire', 'Ice', 'Earth', 'Storm', 'Poison',
]


@dataclass
class OCRResult:
    """OCR detection result."""
    text: str
    confidence: float
    bounding_box: tuple[int, int, int, int]


# === Structure-based detection ===

@dataclass
class MonsterRow:
    """Detected monster in battle list."""
    row_index: int
    y_position: int
    height: int
    has_icon: bool
    has_hp_bar: bool
    name_bbox: tuple[int, int, int, int]


@dataclass
class BattleListResult:
    """Battle list detection result."""
    monster_count: int
    monsters: list[MonsterRow]
    has_header: bool


def detect_battle_list_rows(roi_image: Image.Image) -> BattleListResult:
    """Detect monster rows in battle list ROI based on structure.

    Uses UI profile settings for configurable thresholds.
    """
    arr = np.array(roi_image)
    height, width = arr.shape[:2]

    # Get config from profile
    profile = get_ui_profile()

    icon_width = profile.icon_width

    colored_counts = []
    for y in range(height):
        row = arr[y]
        icon_area = row[:, :icon_width]
        colored = ((icon_area[:, 0] != icon_area[:, 1]) |
                   (icon_area[:, 1] != icon_area[:, 2])).sum()
        colored_counts.append(colored)

    # Threshold from profile
    threshold = profile.colored_threshold
    min_row_height = profile.min_row_height
    max_row_height = profile.max_row_height
    monsters = []
    monster_index = 0

    in_monster = colored_counts[0] >= threshold if height > 0 else False
    start = 0

    for i in range(1, height):
        current = colored_counts[i] >= threshold
        if current and not in_monster:
            start = i
            in_monster = True
        elif not current and in_monster:
            h = i - start
            if min_row_height <= h <= max_row_height:
                row = arr[min(i-1, height-1)]
                hp_area = row[width//3*2:]  # right third
                # HP bar: green dominant, red and blue low (from profile)
                has_hp = ((hp_area[:, 1] > profile.hp_bar_green_min) &
                          (hp_area[:, 0] < profile.hp_bar_red_max) &
                          (hp_area[:, 2] < profile.hp_bar_red_max)).sum() > 5

                monsters.append(MonsterRow(
                    row_index=monster_index,
                    y_position=start,
                    height=h,
                    has_icon=True,
                    has_hp_bar=has_hp,
                    name_bbox=(icon_width, start, width - icon_width - 10, h)
                ))
                monster_index += 1
            in_monster = False

    if in_monster and height > start:
        h = height - start
        if min_row_height <= h <= max_row_height:
            row = arr[height-1]
            hp_area = row[width//2:]
            has_hp = ((hp_area[:, 1] > profile.hp_bar_green_min) &
                      (hp_area[:, 0] < profile.hp_bar_red_max) &
                      (hp_area[:, 2] < profile.hp_bar_red_max)).sum() > 15

            monsters.append(MonsterRow(
                row_index=monster_index,
                y_position=start,
                height=h,
                has_icon=True,
                has_hp_bar=has_hp,
                name_bbox=(icon_width, start, width - icon_width - 10, h)
            ))

    has_header = height > profile.header_height and colored_counts[min(10, height-1)] < threshold

    return BattleListResult(
        monster_count=len(monsters),
        monsters=monsters,
        has_header=has_header
    )


def has_battle_list_monsters(roi_image: Image.Image, min_monsters: int = 1) -> bool:
    """Check if battle list has monsters."""
    result = detect_battle_list_rows(roi_image)
    return result.monster_count >= min_monsters


def detect_monsters_from_structure(roi_image: Image.Image) -> list[dict[str, Any]]:
    """Detect monsters from battle list structure (icon + HP bar).

    Returns list of monster dicts with basic info (no names).
    Useful when OCR fails but structure is visible.
    """
    result = detect_battle_list_rows(roi_image)
    monsters = []

    for row in result.monsters:
        # Use row index as fallback name
        monsters.append({
            'name': f'row_{row.row_index}',  # Fallback name
            'original': 'structure_detected',
            'confidence': 0.5,
            'bbox': (row.name_bbox[0], row.name_bbox[1], row.name_bbox[2], row.name_bbox[3]),
            'y_position': row.y_position,
            'row_index': row.row_index,
            'has_hp_bar': row.has_hp_bar,
            'match_type': 'structure',
        })

    return monsters


def check_battle_list_presence(frame: Frame, roi: Roi) -> bool:
    """Check if battle list has content (monsters)."""
    if not hasattr(frame, 'rgb') or not frame.rgb:
        return False

    try:
        from runtime.battle_list_semantics import crop_roi_rgb
        rgb = crop_roi_rgb(frame, roi)
        if not rgb:
            return False

        w, h = int(roi.width), int(roi.height)
        arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
        image = Image.fromarray(arr, 'RGB')

        return has_battle_list_monsters(image, min_monsters=1)
    except Exception as e:
        print(f'Error in check_battle_list_presence: {e}')
        return False


# === OCR functions ===

# Debug flag: set via environment variable FRBOT_OCR_DEBUG=1
_OCR_DEBUG = os.environ.get('FRBOT_OCR_DEBUG', '').strip().lower() in {'1', 'true', 'yes'}


def preprocess_for_ocr(roi_image: Image.Image, *, debug: bool = False) -> Image.Image:
    """Preprocess ROI for better OCR results.

    Pipeline (configurable via UI profile):
    1. Upscale 2-4x for better text resolution
    2. Convert to grayscale
    3. Apply contrast enhancement (autocontrast or CLAHE)
    4. Optional: adaptive thresholding for high contrast
    5. Optional: denoise for noisy captures
    """
    import numpy as np
    # ImageFilter is not required here; avoid importing unused symbols

    profile = get_ui_profile()

    # Convert to numpy for processing
    orig_arr = np.array(roi_image)
    h, w = orig_arr.shape[:2]

    # 1. Upscale based on profile
    scale = profile.ocr_upscale
    upscaled_w = w * scale
    upscaled_h = h * scale
    upscaled = roi_image.resize((upscaled_w, upscaled_h), Image.Resampling.LANCZOS)

    # 2. Convert to grayscale
    gray = upscaled.convert('L')

    # 3. Apply contrast enhancement (use profile settings)
    gray_np = np.array(gray)

    # Apply CLAHE if available (better for varied lighting)
    try:
        import cv2
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced_np = clahe.apply(gray_np)
        enhanced = Image.fromarray(enhanced_np)
    except ImportError:
        # Fallback to PIL autocontrast
        enhanced = ImageOps.autocontrast(gray, cutoff=2)

    # 4. Optional: adaptive threshold (for difficult backgrounds)
    if _env_bool('FRBOT_OCR_ADAPTIVE_THRESH', False):
        try:
            import cv2
            gray_np = np.array(enhanced)
            thresh = cv2.adaptiveThreshold(gray_np, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY, 11, 2)
            enhanced = Image.fromarray(thresh)
        except ImportError:
            pass

    # Note: Skip sharpening - can introduce artifacts

    # Debug: save intermediate steps
    if debug and OCR_DEBUG:
        debug_dir = Path('diagnostics') / 'ocr_debug'
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        roi_image.save(debug_dir / f'{stamp}_1_orig.png')
        upscaled.save(debug_dir / f'{stamp}_2_upscaled.png')
        gray.save(debug_dir / f'{stamp}_3_gray.png')
        enhanced.save(debug_dir / f'{stamp}_4_enhanced.png')

    return enhanced


def extract_text_with_ocr(roi_image: Image.Image) -> list[OCRResult]:
    """Extract text from ROI using Tesseract OCR."""
    if not is_tesseract_available():
        return []

    try:
        import pytesseract
        import time as time_module

        start_ms = int(time_module.time() * 1000)

        # Use improved preprocessing
        processed = preprocess_for_ocr(roi_image, debug=False)

        # Config: --oem 3 (LSTM), --psm 6 (default/auto), whitelist simple
        custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '

        data = pytesseract.image_to_data(
            processed,
            output_type=pytesseract.Output.DICT,
            config=custom_config,
            lang='spa+eng'
        )

        elapsed_ms = int(time_module.time() * 1000) - start_ms

        results = []
        n_boxes = len(data['text'])

        for i in range(n_boxes):
            text = data['text'][i].strip()
            if not text or len(text) < 2:
                continue

            confidence = float(data['conf'][i]) if data['conf'][i] != '-1' else 0.0

            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]

            results.append(OCRResult(
                text=text,
                confidence=confidence / 100.0,
                bounding_box=(x, y, w, h)
            ))

        # Debug logging
        if _OCR_DEBUG:
            print(f'[OCR] Extracted {len(results)} texts in {elapsed_ms}ms')
            for r in results[:5]:
                print(f'  - "{r.text}" at y={r.bounding_box[1]}, conf={r.confidence:.2f}')

        return results

    except Exception as e:
        print(f'[OCR] Error: {e}')
        return []


def _normalize_text(text: str) -> str:
    """Normalize OCR text for matching with configurable thresholds."""
    # unicodedata already imported above when needed; avoid duplicate import

    # Collapse multiple spaces, strip
    text = ' '.join(text.split())

    # Common OCR confusions - only ONE direction (digit/特殊 -> letter)
    # to avoid converting O to 0 and back
    replacements = [
        ('|', 'I'),
        ('0', 'O'),  # zero to O
        ('1', 'I'),
        ('5', 'S'),
        ('l', 'I'),
        ('rn', 'm'),  # common OCR error
        ('vv', 'w'),  # common OCR error
        ('--', ''),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    # Normalize unicode
    text = unicodedata.normalize('NFKC', text)
    return text.strip()


def _fuzzy_match(text: str, monster: str, max_distance: int = 2) -> bool:
    """Fuzzy string matching using Levenshtein distance."""

    # Normalize both strings
    text_norm = _normalize_text(text).lower()
    monster_norm = monster.lower()

    # Direct substring match
    if monster_norm in text_norm or text_norm in monster_norm:
        return True

    # Quick length check
    if abs(len(text_norm) - len(monster_norm)) > max_distance:
        return False

    # Compute Levenshtein distance
    # Using simple dynamic programming
    m, n = len(text_norm), len(monster_norm)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if text_norm[i-1] == monster_norm[j-1] else 1
            dp[i][j] = min(
                dp[i-1][j] + 1,      # deletion
                dp[i][j-1] + 1,      # insertion
                dp[i-1][j-1] + cost  # substitution
            )

    return dp[m][n] <= max_distance


def fuzzy_match(text: str, candidates: list[str], max_distance: int = 2) -> str | None:
    """Find best fuzzy match from a list of candidates.

    Args:
        text: The text to match
        candidates: List of candidate strings to match against
        max_distance: Maximum Levenshtein distance threshold

    Returns:
        Best matching candidate string, or None if no match found
    """
    if not text or not candidates:
        return None

    best_match = None
    best_score = max_distance + 1  # Anything > max_distance is "no match"

    for candidate in candidates:
        # Try exact substring match first
        text_lower = text.lower()
        candidate_lower = candidate.lower()
        if candidate_lower in text_lower or text_lower in candidate_lower:
            return candidate

        # Try fuzzy matching
        distance = _levenshtein_distance(text_lower, candidate_lower)
        if distance <= max_distance and distance < best_score:
            best_score = distance
            best_match = candidate

    return best_match


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(
                dp[i-1][j] + 1,      # deletion
                dp[i][j-1] + 1,      # insertion
                dp[i-1][j-1] + cost  # substitution
            )

    return dp[m][n]


def detect_monsters_with_ocr(roi_image: Image.Image, min_confidence: float = 0.5) -> list[dict[str, Any]]:
    """Detect monsters using OCR with improved matching.

    In MOCK mode, returns deterministic entities for testing.
    """
    # Mock mode: return deterministic mock entities
    if is_mock_mode():
        w, h = roi_image.size
        mock_monsters = [
            {'name': 'Dragon', 'original': 'Dragon', 'confidence': 0.95,
             'bbox': (20, 10, 80, 16), 'match_type': 'mock'},
            {'name': 'Orc', 'original': 'Orc', 'confidence': 0.90,
             'bbox': (20, 30, 60, 16), 'match_type': 'mock'},
            {'name': 'Goblin', 'original': 'Goblin', 'confidence': 0.85,
             'bbox': (20, 50, 70, 16), 'match_type': 'mock'},
        ]
        if OCR_DEBUG:
            print(f'[OCR] MOCK mode: returning {len(mock_monsters)} entities')
        return mock_monsters

    results = extract_text_with_ocr(roi_image)

    monsters = []
    for result in results:
        text = _normalize_text(result.text)
        if not text or len(text) < 2:
            continue

        # Skip obvious non-monster text (headers, UI elements)
        text_lower = text.lower()
        if any(skip in text_lower for skip in ['battle', 'list', 'window', 'game', 'ui']):
            continue

        # Try matching FIRST, then filter by confidence
        matched = None

        # First try exact substring match (any confidence)
        for monster in COMMON_MONSTERS:
            monster_lower = monster.lower()
            # Only match if the monster name is a significant part of the text
            # (avoid matching "bat" in "battle")
            if monster_lower in text_lower and len(monster_lower) >= len(text_lower) * 0.3:
                matched = monster
                break

        # Try fuzzy match if no exact match
        if not matched:
            matched = fuzzy_match(text, COMMON_MONSTERS, max_distance=2)

        if matched:
            # Accept if confidence >= min OR if matched is in known monsters list
            if result.confidence >= min_confidence or matched in COMMON_MONSTERS:
                monsters.append({
                    'name': matched,
                    'original': text,
                    'confidence': result.confidence,
                    'bbox': result.bounding_box,
                    'match_type': 'exact' if result.confidence >= min_confidence else 'fuzzy',
                })

    return monsters


def extract_names_from_roi(roi_image: Image.Image) -> list[str]:
    """Extract monster names from battle list ROI with instrumentation."""
    start_ms = int(time.time() * 1000)
    roi_size = roi_image.size
    profile = get_ui_profile()

    # Dump ROI crop if enabled
    dump_roi_crop(roi_image, 'battle_list_roi')

    monsters = detect_monsters_with_ocr(roi_image, min_confidence=profile.ocr_min_confidence)
    entities_count = len(monsters)

    # Log metrics
    log_ocr_metrics(
        roi_size=roi_size,
        profile_name=profile.name,
        elapsed_ms=int(time.time() * 1000) - start_ms,
        raw_texts=0,  # Would need to track from extract_text_with_ocr
        entities_found=entities_count,
    )

    if OCR_DEBUG and entities_count == 0:
        print(f'[OCR] WARNING: No entities found. ROI size={roi_size}, profile={profile.name}')

    return [m['name'] for m in monsters]


# === Full OCR Pipeline ===

def run_ocr_pipeline(roi_image: Image.Image, *, allow_structure_fallback: bool = True) -> dict[str, Any]:
    """Run full OCR pipeline with graceful degradation.

    Pipeline stages:
    1. OCR extraction (if Tesseract available)
    2. Structure-based fallback (if OCR fails)
    3. Returns entities even if partial

    Args:
        roi_image: PIL Image of battle list ROI
        allow_structure_fallback: If True, try structure detection if OCR returns empty

    Returns:
        dict with keys: entities_count, entities, roi_size, elapsed_ms, profile_name,
                        fallback_used, fallback_method
    """
    start_ms = int(time.time() * 1000)
    profile = get_ui_profile(roi_image.width, roi_image.height)

    fallback_used = False
    fallback_method = None
    entities = []

    # Dump ROI if enabled
    dump_roi_crop(roi_image, 'pipeline_input')

    # Preprocess
    processed = preprocess_for_ocr(roi_image, debug=True)
    dump_preprocessed(processed, 'pipeline_preprocessed')

    # Stage 1: Try OCR
    if not is_mock_mode():
        try:
            entities = detect_monsters_with_ocr(roi_image, min_confidence=profile.ocr_min_confidence)
        except Exception as e:
            if OCR_DEBUG:
                print(f'[OCR] OCR extraction failed: {e}')
            entities = []

    # If mock mode, use mock entities
    if is_mock_mode():
        entities = detect_monsters_with_ocr(roi_image, min_confidence=profile.ocr_min_confidence)

    # Stage 2: Structure fallback if no entities found
    if len(entities) == 0 and allow_structure_fallback:
        try:
            structure_result = detect_battle_list_rows(roi_image)
            if structure_result.monster_count > 0:
                fallback_used = True
                fallback_method = 'structure'

                # Generate stable IDs for each row
                import hashlib

                for row in structure_result.monsters:
                    # Generate stable_id from bbox position (x, y, width, height)
                    row_id_str = f"{row.name_bbox[0]}_{row.name_bbox[1]}_{row.name_bbox[2]}_{row.name_bbox[3]}"
                    stable_id = hashlib.md5(row_id_str.encode()).hexdigest()[:12]

                    # Build entity with full metadata
                    entity: dict[str, object] = {
                        'name': None,  # No name - requires OCR
                        'stable_id': stable_id,
                        'row_index': row.row_index,
                        'original': None,  # No OCR text available
                        'confidence': 0.5,  # Structure-only confidence
                        'bbox': row.name_bbox,
                        'match_type': 'structure',
                        'status': 'name_unavailable_no_ocr',
                        'reason': 'Tesseract not available or OCR failed',
                        'debug_artifacts': {},  # Populated if dumps enabled
                    }

                    # Add dump paths if enabled
                    if is_dump_rois():
                        entity['debug_artifacts'] = {
                            'row_roi_path': f'row_{row.row_index:03d}_roi.png',
                            'row_preprocess_path': f'row_{row.row_index:03d}_preprocess.png',
                        }

                    entities.append(entity)

                    if OCR_DEBUG:
                        print(f'[OCR] Structure fallback: {len(entities)} rows detected')
                        print('[OCR] WARNING: OCR not available. To enable name extraction:')
                        print('[OCR]   - Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki')
                        print('[OCR]   - Or set FRBOT_TESSERACT_CMD=/path/to/tesseract.exe')
        except Exception as e:
            if OCR_DEBUG:
                print(f'[OCR] Structure fallback failed: {e}')

    entities_count = len(entities)
    elapsed_ms = int(time.time() * 1000) - start_ms
    roi_size = roi_image.size

    # Log metrics
    log_ocr_metrics(
        roi_size=roi_size,
        profile_name=profile.name,
        elapsed_ms=elapsed_ms,
        raw_texts=0,
        entities_found=entities_count,
    )

    # Warning if no entities
    if entities_count == 0 and OCR_DEBUG:
        print(f'[OCR] WARNING: No entities found. ROI={roi_size}, profile={profile.name}, '
              f'fallback_used={fallback_used}')

    return {
        'entities_count': entities_count,
        'entities': entities,
        'roi_size': roi_size,
        'elapsed_ms': elapsed_ms,
        'profile_name': profile.name,
        'fallback_used': fallback_used,
        'fallback_method': fallback_method,
    }
