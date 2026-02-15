from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Optional, cast

from contracts.capture import Frame
from contracts.errors import PreflightFailed
from contracts.evidence import Roi
from contracts.verification import VerificationResult
from diagnostics.frame_dump import dump_enabled, dump_pair


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _to_rgb_bytes_from_png(png_bytes: bytes) -> tuple[bytes, int, int]:
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise PreflightFailed('missing_dependency') from exc

    try:
        img = Image.open(BytesIO(png_bytes))
        rgb_img = img.convert('RGB')
        w, h = rgb_img.size
        return bytes(rgb_img.tobytes()), int(w), int(h)
    except Exception as exc:
        raise PreflightFailed('obs_capture_invalid_content') from exc


def _sample_luma_std(rgb: bytes, *, width: int, height: int, max_pixels: int = 20000) -> tuple[float, bool]:
    if int(width) <= 0 or int(height) <= 0:
        return 0.0, True
    expected = int(width) * int(height) * 3
    if len(rgb) != expected or expected <= 0:
        return 0.0, True

    px = int(width) * int(height)
    step = max(1, px // int(max_pixels))

    mean = 0.0
    m2 = 0.0
    n = 0
    all_zero = True

    idx = 0
    for _ in range(0, px, step):
        r = rgb[idx]
        g = rgb[idx + 1]
        b = rgb[idx + 2]
        if (r | g | b) != 0:
            all_zero = False
        y = (float(r) + float(g) + float(b)) / 3.0
        n += 1
        delta = y - mean
        mean += delta / float(n)
        delta2 = y - mean
        m2 += delta * delta2
        idx += 3 * step
        if idx + 2 >= len(rgb):
            break

    if n <= 1:
        return 0.0, bool(all_zero)
    var = m2 / float(n - 1)
    std = var ** 0.5
    return float(std), bool(all_zero)


def _crop_rgb(rgb: bytes, *, frame_w: int, frame_h: int, roi: Roi) -> bytes:
    if int(frame_w) <= 0 or int(frame_h) <= 0:
        return b''
    if int(roi.width) <= 0 or int(roi.height) <= 0:
        return b''
    if int(roi.x) < 0 or int(roi.y) < 0:
        return b''
    if (int(roi.x) + int(roi.width)) > int(frame_w) or (int(roi.y) + int(roi.height)) > int(frame_h):
        return b''

    row_stride = int(frame_w) * 3
    out = bytearray(int(roi.width) * int(roi.height) * 3)
    out_row_stride = int(roi.width) * 3

    for row in range(int(roi.height)):
        src_start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = rgb[src_start:src_end]

    return bytes(out)


@dataclass(frozen=True, slots=True)
class ObsSourceCaptureSnapshot:
    obs_source_name: str
    frame_resolution: list[int]
    luma_std: float
    all_zero: bool


class _ObsWsV5Client:
    def __init__(self, *, host: str, port: int, password: str, timeout_s: float) -> None:
        self._host = str(host)
        self._port = int(port)
        self._password = str(password)
        self._timeout_s = float(timeout_s)
        self._ws = None
        self._identified = False
        self._request_id = 0

    def _connect_and_identify(self) -> None:
        if self._ws is not None and self._identified:
            return

        try:
            import websocket  # type: ignore
        except Exception as import_exc:  # pragma: no cover
            raise PreflightFailed('missing_dependency') from import_exc

        url = f"ws://{self._host}:{int(self._port)}"
        ws = cast(Any, websocket.WebSocket())
        ws.settimeout(self._timeout_s)
        ws.connect(url)

        hello_raw = ws.recv()
        hello = json.loads(str(hello_raw))
        if int(hello.get('op', -1)) != 0:
            protocol_exc = PreflightFailed('obs_ws_protocol_error')
            setattr(protocol_exc, 'details', {'reason': 'obs_ws_protocol_error', 'expected_op': 0, 'got': hello.get('op')})
            raise protocol_exc

        auth = None
        auth_info = (hello.get('d') or {}).get('authentication')
        if isinstance(auth_info, dict):
            challenge = str(auth_info.get('challenge') or '')
            salt = str(auth_info.get('salt') or '')
            if challenge and salt:
                if not self._password:
                    raise PreflightFailed('obs_ws_auth_required')
                secret = base64.b64encode(hashlib.sha256((self._password + salt).encode('utf-8')).digest()).decode('utf-8')
                auth = base64.b64encode(hashlib.sha256((secret + challenge).encode('utf-8')).digest()).decode('utf-8')

        identify_d: dict[str, Any] = {
            'rpcVersion': 1,
        }
        identify: dict[str, Any] = {
            'op': 1,
            'd': identify_d,
        }
        if auth is not None:
            identify_d['authentication'] = auth

        ws.send(json.dumps(identify))
        identified_raw = ws.recv()
        identified = json.loads(str(identified_raw))
        if int(identified.get('op', -1)) != 2:
            auth_exc = PreflightFailed('obs_ws_auth_failed')
            setattr(auth_exc, 'details', {'reason': 'obs_ws_auth_failed', 'expected_op': 2, 'got': identified.get('op')})
            raise auth_exc

        self._ws = ws
        self._identified = True

    def request(self, request_type: str, request_data: dict[str, Any]) -> dict[str, Any]:
        self._connect_and_identify()
        if self._ws is None:
            raise PreflightFailed('obs_ws_unavailable')

        self._request_id += 1
        rid = f"frbot-{int(self._request_id)}"

        payload = {
            'op': 6,
            'd': {
                'requestType': str(request_type),
                'requestId': rid,
                'requestData': dict(request_data),
            },
        }
        self._ws.send(json.dumps(payload))

        deadline = time.monotonic() + float(self._timeout_s)
        while True:
            if time.monotonic() > deadline:
                raise PreflightFailed('obs_ws_timeout')
            raw = self._ws.recv()
            msg = json.loads(str(raw))
            if int(msg.get('op', -1)) != 7:
                continue
            d = msg.get('d') or {}
            if str(d.get('requestId') or '') != rid:
                continue
            status = d.get('requestStatus') or {}
            ok = bool(status.get('result', False))
            if not ok:
                code = status.get('code')
                comment = str(status.get('comment') or '')
                # Resource not found (source missing).
                # OBS WS v5 commonly returns code=600 with: "No source was found by the name of `...`".
                code_s = str(code)
                cmt = comment.lower()
                if code_s in {'404', '600', 'resource_not_found', 'ResourceNotFound'} or ('not found' in cmt) or ('no source was found' in cmt):
                    exc = PreflightFailed('obs_source_not_found')
                    setattr(exc, 'details', {'reason': 'obs_source_not_found', 'obs_source_name': request_data.get('sourceName')})
                    raise exc
                exc = PreflightFailed('obs_ws_request_failed')
                setattr(
                    exc,
                    'details',
                    {
                        'reason': 'obs_ws_request_failed',
                        'request_type': str(request_type),
                        'obs_source_name': str(request_data.get('sourceName') or ''),
                        'code': code,
                        'comment': comment,
                    },
                )
                raise exc
            resp = d.get('responseData')
            return dict(resp) if isinstance(resp, dict) else {}


def list_obs_input_names() -> list[str]:
    host = _env_str('FRBOT_OBS_WS_HOST', '127.0.0.1')
    port = _env_int('FRBOT_OBS_WS_PORT', 4455)
    password = _env_str('FRBOT_OBS_WS_PASSWORD', '')
    timeout_s = _env_float('FRBOT_OBS_WS_TIMEOUT_S', 2.0)
    try:
        client = _ObsWsV5Client(host=host, port=port, password=password, timeout_s=timeout_s)
        resp = client.request('GetInputList', {})
    except Exception:
        return []

    out: list[str] = []
    seen: set[str] = set()
    inputs = resp.get('inputs')
    if not isinstance(inputs, list):
        return out

    audio_kind_tokens = {
        'wasapi',
        'coreaudio',
        'pulse',
        'alsa',
        'audio',
        'jack',
    }

    for raw in inputs:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get('inputKind') or raw.get('unversionedInputKind') or '').strip().lower()
        if kind and any(tok in kind for tok in audio_kind_tokens):
            continue
        name = str(raw.get('inputName') or raw.get('sourceName') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


ObsScreenshotProvider = Callable[[str, int, int], tuple[bytes, int, int]]


class ObsSourceRealCapture:
    """REAL capture directly from an OBS source by identity (no HWND/foreground/monitor).

    Uses OBS WebSocket v5 GetSourceScreenshot.
    """

    name = 'obs_source'

    def __init__(
        self,
        *,
        obs_source_name: str,
        expected_width: int,
        expected_height: int,
        rois: dict[str, Roi],
        minimap_roi_name: str,
        provider: Optional[ObsScreenshotProvider] = None,
    ) -> None:
        self.obs_source_name = str(obs_source_name or '').strip()
        self.expected_width = int(expected_width)
        self.expected_height = int(expected_height)
        self._rois = dict(rois)
        self._minimap_roi_name = str(minimap_roi_name or '').strip() or 'minimap'

        self.last_luma_std: float = 0.0
        self.last_all_zero: bool = True
        self.last_frame_resolution: tuple[int, int] = (0, 0)

        self._std_min = float(_env_float('FRBOT_OBS_LUMA_STD_MIN', 5.0))

        self._provider = provider
        self._ws_client: _ObsWsV5Client | None = None

        if not self.obs_source_name:
            raise PreflightFailed('obs_source_not_found')
        if self.expected_width <= 0 or self.expected_height <= 0:
            raise PreflightFailed('config_invalid_schema')

    def _default_provider(self) -> ObsScreenshotProvider:
        def _p(source_name: str, w: int, h: int) -> tuple[bytes, int, int]:
            host = _env_str('FRBOT_OBS_WS_HOST', '127.0.0.1')
            port = _env_int('FRBOT_OBS_WS_PORT', 4455)
            password = _env_str('FRBOT_OBS_WS_PASSWORD', '')
            timeout_s = _env_float('FRBOT_OBS_WS_TIMEOUT_S', 2.0)

            if self._ws_client is None:
                self._ws_client = _ObsWsV5Client(host=host, port=port, password=password, timeout_s=timeout_s)

            resp = self._ws_client.request(
                'GetSourceScreenshot',
                {
                    'sourceName': str(source_name),
                    'imageFormat': 'png',
                    'imageWidth': int(w),
                    'imageHeight': int(h),
                    'imageCompressionQuality': -1,
                },
            )
            raw = str(resp.get('imageData') or '')
            # Some servers return data URLs.
            if raw.startswith('data:image'):
                comma = raw.find(',')
                raw = raw[comma + 1 :] if comma >= 0 else ''
            png = base64.b64decode(raw) if raw else b''
            rgb, ww, hh = _to_rgb_bytes_from_png(png)
            return rgb, int(ww), int(hh)

        return _p

    def _provider_fn(self) -> ObsScreenshotProvider:
        return self._provider or self._default_provider()

    def _roi_dict(self, name: str) -> dict[str, int] | None:
        roi = self._rois.get(name)
        if roi is None:
            return None
        return {'x': int(roi.x), 'y': int(roi.y), 'width': int(roi.width), 'height': int(roi.height)}

    def _semantic_snapshot(self) -> dict[str, object]:
        required = ['minimap', 'battle_list', 'hp_mp']
        required_rois: dict[str, object] = {k: self._roi_dict(k) for k in required}
        mm_name = self._minimap_roi_name
        if mm_name not in required_rois:
            required_rois[mm_name] = self._roi_dict(mm_name)
        return {
            'minimap_roi_name': str(mm_name),
            'required_rois': required_rois,
        }

    def _validate_semantics(self, frame: Frame) -> None:
        # ROI presence/shape
        required = ['minimap', 'battle_list', 'hp_mp']
        for k in required:
            roi = self._rois.get(k)
            if roi is None or int(roi.width) <= 0 or int(roi.height) <= 0:
                exc = PreflightFailed('obs_capture_invalid_content')
                setattr(
                    exc,
                    'details',
                    {
                        'reason': 'obs_capture_invalid_content',
                        'obs_source_name': str(self.obs_source_name),
                        'frame_resolution': [int(frame.width), int(frame.height)],
                        'luma_std': float(self.last_luma_std),
                        'all_zero': bool(self.last_all_zero),
                        'error': 'missing_required_roi',
                        'missing_roi': str(k),
                        'semantic_snapshot': self._semantic_snapshot(),
                    },
                )
                raise exc

        # Minimap crop must be possible.
        mm = self._rois.get(self._minimap_roi_name)
        if mm is None:
            exc = PreflightFailed('obs_capture_invalid_content')
            setattr(
                exc,
                'details',
                {
                    'reason': 'obs_capture_invalid_content',
                    'obs_source_name': str(self.obs_source_name),
                    'frame_resolution': [int(frame.width), int(frame.height)],
                    'luma_std': float(self.last_luma_std),
                    'all_zero': bool(self.last_all_zero),
                    'error': 'minimap_roi_missing',
                    'minimap_roi_name': str(self._minimap_roi_name),
                    'semantic_snapshot': self._semantic_snapshot(),
                },
            )
            raise exc
        mm_rgb = _crop_rgb(frame.rgb, frame_w=frame.width, frame_h=frame.height, roi=mm)
        if not mm_rgb:
            exc = PreflightFailed('obs_capture_invalid_content')
            setattr(
                exc,
                'details',
                {
                    'reason': 'obs_capture_invalid_content',
                    'obs_source_name': str(self.obs_source_name),
                    'frame_resolution': [int(frame.width), int(frame.height)],
                    'luma_std': float(self.last_luma_std),
                    'all_zero': bool(self.last_all_zero),
                    'error': 'minimap_crop_failed',
                    'minimap_roi_name': str(self._minimap_roi_name),
                    'semantic_snapshot': self._semantic_snapshot(),
                },
            )
            raise exc

    def verify(self) -> VerificationResult:
        # Must be verifiable without HWND/foreground.
        try:
            _ = self.grab()
            return VerificationResult(ok=True)
        except PreflightFailed:
            raise
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        provider = self._provider_fn()

        rgb, w, h = provider(self.obs_source_name, int(self.expected_width), int(self.expected_height))
        self.last_frame_resolution = (int(w), int(h))

        if int(w) != int(self.expected_width) or int(h) != int(self.expected_height):
            exc = PreflightFailed('obs_capture_invalid_content')
            setattr(
                exc,
                'details',
                {
                    'reason': 'obs_capture_invalid_content',
                    'obs_source_name': str(self.obs_source_name),
                    'expected_resolution': [int(self.expected_width), int(self.expected_height)],
                    'frame_resolution': [int(w), int(h)],
                    'error': 'wrong_resolution',
                    'semantic_snapshot': self._semantic_snapshot(),
                },
            )
            raise exc

        std, all_zero = _sample_luma_std(rgb, width=int(w), height=int(h))
        self.last_luma_std = float(std)
        self.last_all_zero = bool(all_zero)

        if bool(all_zero) or float(std) < float(self._std_min):
            exc = PreflightFailed('obs_capture_invalid_content')
            setattr(
                exc,
                'details',
                {
                    'reason': 'obs_capture_invalid_content',
                    'obs_source_name': str(self.obs_source_name),
                    'frame_resolution': [int(w), int(h)],
                    'luma_std': float(std),
                    'all_zero': bool(all_zero),
                    'error': 'frame_black_or_low_variance',
                    'semantic_snapshot': self._semantic_snapshot(),
                },
            )
            if dump_enabled():
                failing = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(time.monotonic_ns()), digest_hex='', rgb=bytes(rgb))
                dump_pair(gate='capture', before=failing, after=None, reason='obs_capture_invalid_content')
            raise exc

        ts_ns = int(time.monotonic_ns())
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

        minimap_roi = self._rois.get(self._minimap_roi_name)
        minimap_rgb = _crop_rgb(rgb, frame_w=int(w), frame_h=int(h), roi=minimap_roi) if minimap_roi is not None else b''
        minimap_detected = bool(minimap_rgb)
        minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ''

        frame = Frame(
            width=int(w),
            height=int(h),
            monotonic_ts_ns=int(ts_ns),
            digest_hex=str(digest),
            rgb=bytes(rgb),
            minimap_detected=bool(minimap_detected),
            minimap_rgb=bytes(minimap_rgb),
            minimap_width=int(minimap_roi.width) if minimap_roi is not None else 0,
            minimap_height=int(minimap_roi.height) if minimap_roi is not None else 0,
            minimap_digest_hex=str(minimap_digest),
        )

        # Semantic ROI validation (must pass every grab).
        try:
            self._validate_semantics(frame)
        except PreflightFailed as exc:
            if str(exc) != 'obs_capture_invalid_content':
                raise
            d = getattr(exc, 'details', None)
            if not isinstance(d, dict):
                setattr(
                    exc,
                    'details',
                    {
                        'reason': 'obs_capture_invalid_content',
                        'obs_source_name': str(self.obs_source_name),
                        'frame_resolution': [int(w), int(h)],
                        'luma_std': float(std),
                        'all_zero': bool(all_zero),
                        'error': 'semantic_roi_invalid',
                        'semantic_snapshot': self._semantic_snapshot(),
                    },
                )
            if dump_enabled():
                dump_pair(gate='capture', before=frame, after=None, reason='obs_capture_invalid_content')
            raise

        return frame
