from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))


def _read_json(path: Path) -> dict[str, Any]:
	data = json.loads(path.read_text(encoding='utf-8-sig'))
	return data if isinstance(data, dict) else {}


def _compute_roi_config_sha(*, rois_node: dict[str, Any], frame_node: dict[str, Any] | None) -> str:
	rois_for_sha: dict[str, dict[str, int]] = {}
	for name, roi_raw in rois_node.items():
		if not isinstance(name, str) or not isinstance(roi_raw, dict):
			continue
		try:
			x = int(roi_raw['x'])
			y = int(roi_raw['y'])
			w = int(roi_raw['width'])
			h = int(roi_raw['height'])
		except Exception:
			continue
		rois_for_sha[name] = {'x': x, 'y': y, 'width': w, 'height': h}

	canonical: dict[str, object] = {'rois': rois_for_sha}
	if isinstance(frame_node, dict):
		try:
			fw = int(frame_node['width'])
			fh = int(frame_node['height'])
			canonical['frame'] = {'width': fw, 'height': fh}
		except Exception:
			pass

	blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
	return hashlib.sha256(blob).hexdigest()


def _guard_config(*, config_path: Path) -> tuple[bool, str, dict[str, Any]]:
	data = _read_json(config_path)
	rois_node = data.get('rois')
	if not isinstance(rois_node, dict):
		return False, 'config_invalid_schema', {'expected': 'top_level.rois object'}

	frame_node = data.get('frame')
	computed = _compute_roi_config_sha(rois_node=rois_node, frame_node=frame_node if isinstance(frame_node, dict) else None)
	cert = data.get('certified_manifest')
	if not isinstance(cert, dict):
		return False, 'roi_config_not_certified', {'computed_sha': computed, 'missing': 'certified_manifest'}
	claimed = cert.get('roi_config_sha')
	if not isinstance(claimed, str) or claimed.strip() != computed:
		return False, 'roi_config_sha_mismatch', {'computed_sha': computed, 'claimed_sha': claimed}
	return True, 'ok', {'computed_sha': computed}


def _cmd_calibrate(args: argparse.Namespace) -> int:
	# Delegate to the existing calibrator tool to keep behavior consistent.
	cmd = [sys.executable, str(_REPO_ROOT / 'tools' / 'calibrate_obs_projector_rois.py')]
	cmd.extend(list(args.calibrate_args or []))
	return subprocess.call(cmd)


def _cmd_guard(args: argparse.Namespace) -> int:
	path = Path(str(args.config).strip())
	if not path.is_absolute():
		sys.stdout.write(json.dumps({'reason': 'arg_not_absolute', 'arg': '--config', 'value': str(path)}, indent=2, sort_keys=True))
		sys.stdout.write('\n')
		return 2
	ok, reason, details = _guard_config(config_path=path)
	if not ok:
		sys.stdout.write(json.dumps({'reason': reason, 'details': details, 'config_path': str(path)}, indent=2, sort_keys=True))
		sys.stdout.write('\n')
		return 2
	print('ROI CERTIFIED')
	return 0


def _cmd_certify(args: argparse.Namespace) -> int:
	path = Path(str(args.config).strip())
	if not path.is_absolute():
		sys.stdout.write(json.dumps({'reason': 'arg_not_absolute', 'arg': '--config', 'value': str(path)}, indent=2, sort_keys=True))
		sys.stdout.write('\n')
		return 2
	data = _read_json(path)
	rois_node = data.get('rois')
	if not isinstance(rois_node, dict):
		sys.stdout.write(json.dumps({'reason': 'config_invalid_schema', 'details': {'expected': 'top_level.rois object'}}, indent=2, sort_keys=True))
		sys.stdout.write('\n')
		return 2
	frame_node = data.get('frame')
	computed = _compute_roi_config_sha(rois_node=rois_node, frame_node=frame_node if isinstance(frame_node, dict) else None)

	from diagnostics.schema import SCHEMA_VERSION

	manifest = {
		'schema_version': str(SCHEMA_VERSION),
		'roi_config_sha': str(computed),
		'certified_ts': int(time.time()),
		'certified_by': (os.environ.get('USERNAME') or os.environ.get('USER') or '').strip(),
	}
	data['certified_manifest'] = manifest
	path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
	print(json.dumps({'result': 'certified', 'roi_config_sha': computed, 'config_path': str(path)}, indent=2, sort_keys=True))
	return 0


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(add_help=True)
	sub = parser.add_subparsers(dest='cmd', required=True)

	p_cal = sub.add_parser('calibrate', add_help=True)
	p_cal.add_argument('calibrate_args', nargs=argparse.REMAINDER)
	p_cal.set_defaults(func=_cmd_calibrate)

	p_guard = sub.add_parser('guard', add_help=True)
	p_guard.add_argument('--config', required=True, help='Absolute path to ROI config JSON')
	p_guard.set_defaults(func=_cmd_guard)

	p_cert = sub.add_parser('certify', add_help=True)
	p_cert.add_argument('--config', required=True, help='Absolute path to ROI config JSON')
	p_cert.set_defaults(func=_cmd_certify)

	args = parser.parse_args(argv)
	return int(args.func(args))


if __name__ == '__main__':
	raise SystemExit(main())
