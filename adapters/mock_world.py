from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

from contracts.capture import Frame
from contracts.evidence import Roi


def _roi_bounds_ok(width: int, height: int, roi: Roi) -> bool:
	return roi.x >= 0 and roi.y >= 0 and (roi.x + roi.width) <= width and (roi.y + roi.height) <= height


@dataclass(slots=True)
class MockWorld:
	"""Deterministic in-memory "screen" that reacts to key presses."""

	width: int
	height: int
	rois: Dict[str, Roi]
	key_effects: Dict[str, str]
	key_kinds: Dict[str, str]
	rgb: bytearray
	minimap_noise: bool = False

	# Healing mock state.
	hp_current: int = 100
	hp_max: int = 100
	mp_current: int = 100
	mp_max: int = 100
	heal_amount: int = 30
	heal_behavior: str = 'normal'

	# Deterministic healing test controls.
	# - evidence=none: cast produces NO evidence (no hp increase, no cooldown, no feedback)
	# - cooldown=permanent: cooldown marker always visible (cast must be suppressed by runtime)
	mock_heal_evidence: str = 'ok'
	mock_heal_cooldown: str = 'clear'
	heal_cooldown_frames_left: int = 0

	# Healing markers (semantic evidence).
	heal_cooldown_marker_rgb: tuple[int, int, int] = (255, 255, 0)
	heal_feedback_marker_rgb: tuple[int, int, int] = (0, 255, 0)

	# Targeting mock UI (Battle List + target frame).
	battle_list_row_height: int = 16
	battle_list_rows: tuple['MockBattleListRow', ...] = ()
	battle_list_selected_row: Optional[int] = None
	click_behavior: str = 'normal'

	target_frame_visible: bool = False
	target_frame_name: str = ''

	# Combat mock state.
	target_hp_current: int = 100
	target_hp_max: int = 100
	target_damage_amount: int = 10

	# Deterministic combat test controls.
	# - damage: decreases target HP
	# - feedback: shows damage feedback marker after attack
	# - cooldown: shows cooldown marker after attack
	# - permanent_cooldown: cooldown marker always visible (attack must be suppressed by runtime)
	mock_combat_damage: bool = False
	mock_combat_feedback: bool = False
	mock_combat_cooldown: bool = False
	mock_combat_permanent_cooldown: bool = False
	combat_cooldown_frames_left: int = 0
	combat_feedback_frames_left: int = 0

	combat_cooldown_marker_rgb: tuple[int, int, int] = (255, 255, 0)
	combat_feedback_marker_rgb: tuple[int, int, int] = (0, 255, 255)

	# Deterministic "position" used by cavebot in mock mode.
	pos_x: int = 0
	pos_y: int = 0
	pos_z: int = 7

	# Deterministic cavebot controls.
	# - marker_static: movement inputs do not move the marker
	# - marker_wrong_direction: movement inputs move in the opposite direction
	# - noise_only: minimap changes, but marker is not rendered (marker missing)
	# - progress_ok: normal movement behavior
	mock_cavebot_marker_static: bool = False
	mock_cavebot_marker_wrong_direction: bool = False
	mock_cavebot_noise_only: bool = False
	mock_cavebot_progress_ok: bool = False
	# - dual_marker: renders an additional static marker blob to validate stabilization
	# - minimap_force_black: minimap ROI stays uniform/black while full frame changes
	mock_cavebot_dual_marker: bool = False
	mock_cavebot_minimap_force_black: bool = False

	# Looting mock state.
	inventory_gold_count: int = 0
	inventory_capacity_used: int = 0
	loot_container_open: bool = False

	# Deposit mock state.
	depot_item_count: int = 0
	depot_open: bool = True

	# Deterministic deposit test controls.
	# - success: inventory decreases and depot increases by same amount
	# - no_delta: neither inventory nor depot changes
	# - partial: inventory decreases more than depot increases
	# - depot_closed: depot is not open
	# - inventory_unreadable: inventory ROI magic is invalid
	mock_deposit_success: bool = False
	mock_deposit_no_delta: bool = False
	mock_deposit_partial: bool = False
	mock_deposit_inventory_unreadable: bool = False

	# Deterministic looting test controls.
	# - inventory_delta: looting produces a semantic inventory delta (gold or capacity_used increases)
	# - container_opens: clicking the corpse opens the container
	# - inventory_read_fail: inventory ROI is not readable (bad magic)
	# - premium: quick-loot key is accepted
	mock_loot_inventory_delta: bool = False
	mock_loot_container_opens: bool = False
	mock_loot_inventory_read_fail: bool = False
	mock_loot_premium: bool = True

	# Trade mock state.
	trade_npc_present: bool = True
	trade_npc_id: int = 1
	trade_npc_open: bool = True
	trade_action: str = 'buy'
	trade_gold: int = 0
	trade_item_count: int = 0
	trade_capacity_used: int = 0

	# Deterministic trade test controls.
	# - buy_ok: buy action produces gold- and item+
	# - sell_ok: sell action produces gold+ and item-
	# - no_delta: no changes at all
	# - gold_only: gold changes but items do not
	# - item_only: items change but gold does not
	mock_trade_buy_ok: bool = False
	mock_trade_sell_ok: bool = False
	mock_trade_no_delta: bool = False
	mock_trade_gold_only: bool = False
	mock_trade_item_only: bool = False

	_noise_bit: int = 0

	@classmethod
	def create(
		cls,
		rois: Mapping[str, Roi],
		key_effects: Optional[Mapping[str, str]] = None,
		key_kinds: Optional[Mapping[str, str]] = None,
		minimap_noise: bool = False,
		battle_list_rows: Optional[Sequence['MockBattleListRow']] = None,
		battle_list_selected_row: Optional[int] = None,
		battle_list_row_height: int = 16,
		click_behavior: str = 'normal',
		hp_current: int = 100,
		hp_max: int = 100,
		mp_current: int = 100,
		mp_max: int = 100,
		heal_amount: int = 30,
		heal_behavior: str = 'normal',
		mock_heal_evidence: str = 'ok',
		mock_heal_cooldown: str = 'clear',
		target_hp_current: int = 100,
		target_hp_max: int = 100,
		mock_combat_damage: bool = False,
		mock_combat_feedback: bool = False,
		mock_combat_cooldown: bool = False,
		mock_combat_permanent_cooldown: bool = False,
		mock_cavebot_marker_static: bool = False,
		mock_cavebot_marker_wrong_direction: bool = False,
		mock_cavebot_noise_only: bool = False,
		mock_cavebot_progress_ok: bool = False,
		mock_cavebot_dual_marker: bool = False,
		mock_cavebot_minimap_force_black: bool = False,
		inventory_gold_count: int = 0,
		inventory_capacity_used: int = 0,
		depot_item_count: int = 0,
		depot_open: bool = True,
		mock_deposit_success: bool = False,
		mock_deposit_no_delta: bool = False,
		mock_deposit_partial: bool = False,
		mock_deposit_inventory_unreadable: bool = False,
		mock_loot_inventory_delta: bool = False,
		mock_loot_container_opens: bool = False,
		mock_loot_inventory_read_fail: bool = False,
		mock_loot_premium: bool = True,
		trade_npc_present: bool = True,
		trade_npc_id: int = 1,
		trade_npc_open: bool = True,
		trade_action: str = 'buy',
		trade_gold: int = 0,
		trade_item_count: int = 0,
		trade_capacity_used: int = 0,
		mock_trade_buy_ok: bool = False,
		mock_trade_sell_ok: bool = False,
		mock_trade_no_delta: bool = False,
		mock_trade_gold_only: bool = False,
		mock_trade_item_only: bool = False,
	) -> 'MockWorld':
		# Choose the smallest frame that contains all ROIs.
		max_x = 1
		max_y = 1
		for roi in rois.values():
			max_x = max(max_x, roi.x + roi.width)
			max_y = max(max_y, roi.y + roi.height)
		width = max(1, max_x)
		height = max(1, max_y)
		rgb = bytearray(width * height * 3)

		world = cls(
			width=width,
			height=height,
			rois=dict(rois),
			key_effects=dict(key_effects or {}),
			key_kinds=dict(key_kinds or {}),
			rgb=rgb,
			minimap_noise=bool(minimap_noise),
			battle_list_row_height=int(battle_list_row_height),
			battle_list_rows=tuple(battle_list_rows or ()),
			battle_list_selected_row=(None if battle_list_selected_row is None else int(battle_list_selected_row)),
			click_behavior=str(click_behavior or 'normal'),
			hp_current=int(hp_current),
			hp_max=max(1, int(hp_max)),
			mp_current=int(mp_current),
			mp_max=max(1, int(mp_max)),
			heal_amount=int(heal_amount),
			heal_behavior=str(heal_behavior or 'normal'),
			mock_heal_evidence=str(mock_heal_evidence or 'ok'),
			mock_heal_cooldown=str(mock_heal_cooldown or 'clear'),
			target_hp_current=int(target_hp_current),
			target_hp_max=max(1, int(target_hp_max)),
			mock_combat_damage=bool(mock_combat_damage),
			mock_combat_feedback=bool(mock_combat_feedback),
			mock_combat_cooldown=bool(mock_combat_cooldown),
			mock_combat_permanent_cooldown=bool(mock_combat_permanent_cooldown),
			mock_cavebot_marker_static=bool(mock_cavebot_marker_static),
			mock_cavebot_marker_wrong_direction=bool(mock_cavebot_marker_wrong_direction),
			mock_cavebot_noise_only=bool(mock_cavebot_noise_only),
			mock_cavebot_progress_ok=bool(mock_cavebot_progress_ok),
			mock_cavebot_dual_marker=bool(mock_cavebot_dual_marker),
			mock_cavebot_minimap_force_black=bool(mock_cavebot_minimap_force_black),
			inventory_gold_count=int(inventory_gold_count),
			inventory_capacity_used=int(inventory_capacity_used),
			depot_item_count=int(depot_item_count),
			depot_open=bool(depot_open),
			mock_deposit_success=bool(mock_deposit_success),
			mock_deposit_no_delta=bool(mock_deposit_no_delta),
			mock_deposit_partial=bool(mock_deposit_partial),
			mock_deposit_inventory_unreadable=bool(mock_deposit_inventory_unreadable),
			mock_loot_inventory_delta=bool(mock_loot_inventory_delta),
			mock_loot_container_opens=bool(mock_loot_container_opens),
			mock_loot_inventory_read_fail=bool(mock_loot_inventory_read_fail),
			mock_loot_premium=bool(mock_loot_premium),
			trade_npc_present=bool(trade_npc_present),
			trade_npc_id=max(1, int(trade_npc_id)),
			trade_npc_open=bool(trade_npc_open),
			trade_action=str(trade_action or 'buy'),
			trade_gold=int(trade_gold),
			trade_item_count=int(trade_item_count),
			trade_capacity_used=int(trade_capacity_used),
			mock_trade_buy_ok=bool(mock_trade_buy_ok),
			mock_trade_sell_ok=bool(mock_trade_sell_ok),
			mock_trade_no_delta=bool(mock_trade_no_delta),
			mock_trade_gold_only=bool(mock_trade_gold_only),
			mock_trade_item_only=bool(mock_trade_item_only),
		)

		# Dual-marker cavebot tests require a stable marker area across moves.
		# Keep the moving marker away from minimap edges to avoid clipping.
		if bool(world.mock_cavebot_dual_marker):
			world.pos_x = 10
			world.pos_y = 10
			world._encode_position()

		# If a battle list row is pre-selected, expose a consistent locked target frame.
		if battle_list_selected_row is not None:
			idx = int(battle_list_selected_row)
			rows = tuple(battle_list_rows or ())
			if 0 <= idx < len(rows):
				world.target_frame_visible = True
				world.target_frame_name = rows[idx].name

		return world

	def _draw_deposit_ui(self) -> None:
		roi = self.rois.get('depot_container')
		if roi is None:
			return
		view = self._roi_bytes_view(roi)
		if view is None or len(view) < 6:
			return
		view[0:2] = int(0xD00D).to_bytes(2, 'little', signed=False)
		view[2:4] = int(max(0, self.depot_item_count)).to_bytes(2, 'little', signed=False)
		view[4:6] = int(1 if self.depot_open else 0).to_bytes(2, 'little', signed=False)

	def _draw_looting_ui(self) -> None:
		# Container open state ROI.
		if 'loot_container_open' in self.rois:
			self._set_bool_roi('loot_container_open', bool(self.loot_container_open))

		# Encode inventory snapshot ROI.
		roi = self.rois.get('inventory_text')
		if roi is None:
			return
		view = self._roi_bytes_view(roi)
		if view is None or len(view) < 6:
			return
		if bool(self.mock_loot_inventory_read_fail) or bool(self.mock_deposit_inventory_unreadable):
			view[0:6] = b'\x00\x00\x00\x00\x00\x00'
			return
		view[0:2] = int(0xBEEF).to_bytes(2, 'little', signed=False)
		view[2:4] = int(max(0, self.inventory_gold_count)).to_bytes(2, 'little', signed=False)
		view[4:6] = int(max(0, self.inventory_capacity_used)).to_bytes(2, 'little', signed=False)

	def _draw_trade_ui(self) -> None:
		# NPC identity ROI.
		npc_roi = self.rois.get('trade_npc')
		if npc_roi is not None:
			view = self._roi_bytes_view(npc_roi)
			if view is not None and len(view) >= 6:
				if bool(self.trade_npc_present):
					view[0:2] = int(0xFACE).to_bytes(2, 'little', signed=False)
					view[2:4] = int(max(1, self.trade_npc_id)).to_bytes(2, 'little', signed=False)
					view[4:6] = int(1 if self.trade_npc_open else 0).to_bytes(2, 'little', signed=False)
				else:
					view[0:6] = b'\x00\x00\x00\x00\x00\x00'

		# Trade inventory ROI.
		inv_roi = self.rois.get('trade_inventory')
		if inv_roi is None:
			return
		view2 = self._roi_bytes_view(inv_roi)
		if view2 is None or len(view2) < 8:
			return
		view2[0:2] = int(0xB00B).to_bytes(2, 'little', signed=False)
		view2[2:4] = int(max(0, self.trade_gold)).to_bytes(2, 'little', signed=False)
		view2[4:6] = int(max(0, self.trade_item_count)).to_bytes(2, 'little', signed=False)
		view2[6:8] = int(max(0, self.trade_capacity_used)).to_bytes(2, 'little', signed=False)

	def _draw_combat_ui(self) -> None:
		# Target HP bar ROI.
		target_hp_roi = self.rois.get('target_hp_bar')
		if target_hp_roi is not None and _roi_bounds_ok(self.width, self.height, target_hp_roi):
			pct = float(self.target_hp_current) / float(max(1, self.target_hp_max))
			self._draw_bar_roi('target_hp_bar', percent=pct, fill_rgb=(255, 0, 0))

		# Combat cooldown marker ROI.
		cd_roi = self.rois.get('combat_cooldown')
		if cd_roi is not None and _roi_bounds_ok(self.width, self.height, cd_roi):
			if bool(self.mock_combat_permanent_cooldown):
				self.fill_roi('combat_cooldown', *self.combat_cooldown_marker_rgb)
			elif int(self.combat_cooldown_frames_left) > 0:
				self.fill_roi('combat_cooldown', *self.combat_cooldown_marker_rgb)
			else:
				self.fill_roi('combat_cooldown', 0, 0, 0)

		# Combat damage feedback marker ROI.
		fb_roi = self.rois.get('combat_feedback')
		if fb_roi is not None and _roi_bounds_ok(self.width, self.height, fb_roi):
			# Feedback is only shown after an attack.
			if int(self.combat_feedback_frames_left) > 0:
				self.fill_roi('combat_feedback', *self.combat_feedback_marker_rgb)
			else:
				self.fill_roi('combat_feedback', 0, 0, 0)

	def _apply_attack(self) -> None:
		# Cooldown marker becomes visible after an attack when enabled.
		if bool(self.mock_combat_cooldown):
			self.combat_cooldown_frames_left = 2

		# Damage evidence.
		if bool(self.mock_combat_damage):
			self.target_hp_current = max(0, int(self.target_hp_current) - int(self.target_damage_amount))

		# Feedback evidence.
		if bool(self.mock_combat_feedback):
			self.combat_feedback_frames_left = 2

	def _encode_u16_pair_roi(self, roi_name: str, a: int, b: int) -> None:
		roi = self.rois.get(roi_name)
		if roi is None:
			return
		view = self._roi_bytes_view(roi)
		if view is None or len(view) < 4:
			return
		view[0:2] = int(max(0, a)).to_bytes(2, 'little', signed=False)
		view[2:4] = int(max(1, b)).to_bytes(2, 'little', signed=False)

	def _draw_bar_roi(self, roi_name: str, *, percent: float, fill_rgb: tuple[int, int, int]) -> None:
		roi = self.rois.get(roi_name)
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return
		p = float(percent)
		if p < 0.0:
			p = 0.0
		if p > 1.0:
			p = 1.0
		fill_w = int(round(p * float(roi.width)))
		row_stride = self.width * 3
		fr, fg, fb = (int(fill_rgb[0]), int(fill_rgb[1]), int(fill_rgb[2]))
		for y in range(roi.y, roi.y + roi.height):
			for x in range(roi.x, roi.x + roi.width):
				idx = (y * row_stride) + (x * 3)
				if (x - roi.x) < fill_w:
					self.rgb[idx] = fr
					self.rgb[idx + 1] = fg
					self.rgb[idx + 2] = fb
				else:
					self.rgb[idx] = 0
					self.rgb[idx + 1] = 0
					self.rgb[idx + 2] = 0

	def _draw_healing_ui(self) -> None:
		# HP/MP bar ROIs.
		hp_pct = float(self.hp_current) / float(max(1, self.hp_max))
		mp_pct = float(self.mp_current) / float(max(1, self.mp_max))
		self._draw_bar_roi('hp_bar', percent=hp_pct, fill_rgb=(255, 0, 0))
		self._draw_bar_roi('mp_bar', percent=mp_pct, fill_rgb=(0, 0, 255))

		# Numeric ROIs.
		self._encode_u16_pair_roi('hp_text', self.hp_current, self.hp_max)
		self._encode_u16_pair_roi('mp_text', self.mp_current, self.mp_max)

		# Combined HP/MP ROI (prod-emergency contract): 8 bytes -> hp_cur,hp_max,mp_cur,mp_max.
		roi = self.rois.get('hp_mp')
		if roi is not None:
			view = self._roi_bytes_view(roi)
			if view is not None and len(view) >= 8:
				view[0:2] = int(max(0, self.hp_current)).to_bytes(2, 'little', signed=False)
				view[2:4] = int(max(1, self.hp_max)).to_bytes(2, 'little', signed=False)
				view[4:6] = int(max(0, self.mp_current)).to_bytes(2, 'little', signed=False)
				view[6:8] = int(max(1, self.mp_max)).to_bytes(2, 'little', signed=False)

		# Cooldown overlay marker ROI.
		cooldown_roi = self.rois.get('heal_cooldown')
		if cooldown_roi is not None and _roi_bounds_ok(self.width, self.height, cooldown_roi):
			cd_mode = (self.mock_heal_cooldown or 'clear').strip().lower()
			if cd_mode == 'permanent':
				self.fill_roi('heal_cooldown', *self.heal_cooldown_marker_rgb)
			elif self.heal_cooldown_frames_left > 0:
				self.fill_roi('heal_cooldown', *self.heal_cooldown_marker_rgb)
			else:
				self.fill_roi('heal_cooldown', 0, 0, 0)

		# Feedback marker ROI (explicit spell feedback).
		feedback_roi = self.rois.get('heal_feedback')
		if feedback_roi is not None and _roi_bounds_ok(self.width, self.height, feedback_roi):
			ev_mode = (self.mock_heal_evidence or 'ok').strip().lower()
			if ev_mode == 'none':
				self.fill_roi('heal_feedback', 0, 0, 0)
			elif self.heal_behavior.strip().lower() == 'feedback_only':
				self.fill_roi('heal_feedback', *self.heal_feedback_marker_rgb)
			else:
				self.fill_roi('heal_feedback', 0, 0, 0)

	def _apply_heal(self) -> None:
		behavior = (self.heal_behavior or 'normal').strip().lower()
		ev_mode = (self.mock_heal_evidence or 'ok').strip().lower()

		# evidence=none => cast produces NO observable evidence.
		if ev_mode == 'none':
			return

		# Cooldown becomes visible in normal flows (unless permanent mode overrides anyway).
		self.heal_cooldown_frames_left = 2

		if behavior == 'no_effect':
			return
		if behavior == 'cooldown_only':
			return
		if behavior == 'feedback_only':
			return

		# Normal: increase HP and consume some MP.
		self.hp_current = min(int(self.hp_max), int(self.hp_current) + int(self.heal_amount))
		self.mp_current = max(0, int(self.mp_current) - 5)

	def _draw_target_frame(self) -> None:
		roi = self.rois.get('target_frame')
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return

		if not self.target_frame_visible:
			self.fill_roi('target_frame', 0, 0, 0)
			return

		# Background.
		self.fill_roi('target_frame', 20, 20, 20)

		# Encode target name using the same mock OCR scheme.
		row_stride = self.width * 3
		x0 = roi.x + 2
		y0 = roi.y + 2
		name_bytes = (self.target_frame_name or '').encode('ascii', errors='ignore')[:12]
		for i in range(12):
			b = name_bytes[i] if i < len(name_bytes) else 0
			idx = (y0 * row_stride) + ((x0 + i) * 3)
			if 0 <= idx + 2 < len(self.rgb):
				self.rgb[idx] = int(b)
				self.rgb[idx + 1] = 0
				self.rgb[idx + 2] = 0

		# HP bar present: small red segment at top-right.
		bar_y = roi.y + 1
		for x in range(max(roi.x, roi.x + roi.width - 20), roi.x + roi.width - 2):
			idx = (bar_y * row_stride) + (x * 3)
			if 0 <= idx + 2 < len(self.rgb):
				self.rgb[idx] = 255
				self.rgb[idx + 1] = 0
				self.rgb[idx + 2] = 0

	def _draw_battle_list(self) -> None:
		roi = self.rois.get('battle_list')
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return

		# Clear container.
		self.fill_roi('battle_list', 0, 0, 0)
		if not self.battle_list_rows:
			return

		row_h = max(1, int(self.battle_list_row_height))
		row_stride = self.width * 3
		max_rows = max(0, roi.height // row_h)
		rows = self.battle_list_rows[:max_rows]
		for row_index, row in enumerate(rows):
			row_y = roi.y + row_index * row_h
			# Highlight background.
			if self.battle_list_selected_row == row_index:
				for y in range(row_y, min(roi.y + roi.height, row_y + row_h)):
					for x in range(roi.x, roi.x + roi.width):
						idx = (y * row_stride) + (x * 3)
						self.rgb[idx] = 0
						self.rgb[idx + 1] = 0
						self.rgb[idx + 2] = 255

			# Attackable marker.
			if row.is_attackable:
				idx = ((row_y + 1) * row_stride) + ((roi.x + 1) * 3)
				if 0 <= idx + 2 < len(self.rgb):
					self.rgb[idx] = 0
					self.rgb[idx + 1] = 255
					self.rgb[idx + 2] = 0

			# Encode name.
			name_bytes = (row.name or '').encode('ascii', errors='ignore')[:12]
			for i in range(12):
				b = name_bytes[i] if i < len(name_bytes) else 0
				idx = ((row_y + 2) * row_stride) + ((roi.x + 2 + i) * 3)
				if 0 <= idx + 2 < len(self.rgb):
					self.rgb[idx] = int(b)
					self.rgb[idx + 1] = 0
					self.rgb[idx + 2] = 0

			# HP bar marker.
			if row.hp_bar_visible:
				bar_y = row_y + 1
				for x in range(max(roi.x, roi.x + roi.width - 20), roi.x + roi.width - 2):
					idx = (bar_y * row_stride) + (x * 3)
					if 0 <= idx + 2 < len(self.rgb):
						self.rgb[idx] = 255
						self.rgb[idx + 1] = 0
						self.rgb[idx + 2] = 0

	def on_click(self, x: int, y: int) -> None:
		# Trade click interactions.
		action_roi = self.rois.get('trade_action')
		if action_roi is not None and _roi_bounds_ok(self.width, self.height, action_roi):
			if x >= action_roi.x and y >= action_roi.y and x < (action_roi.x + action_roi.width) and y < (action_roi.y + action_roi.height):
				if bool(self.mock_trade_no_delta):
					self._draw_trade_ui()
					return

				action = (self.trade_action or 'buy').strip().lower()
				if action == 'buy':
					if bool(self.mock_trade_item_only):
						self.trade_item_count += 1
					elif bool(self.mock_trade_gold_only):
						self.trade_gold = max(0, int(self.trade_gold) - 1)
					elif bool(self.mock_trade_buy_ok):
						self.trade_gold = max(0, int(self.trade_gold) - 1)
						self.trade_item_count += 1
					self._draw_trade_ui()
					return
				if action == 'sell':
					if bool(self.mock_trade_item_only):
						self.trade_item_count = max(0, int(self.trade_item_count) - 1)
					elif bool(self.mock_trade_gold_only):
						self.trade_gold += 1
					elif bool(self.mock_trade_sell_ok):
						self.trade_gold += 1
						self.trade_item_count = max(0, int(self.trade_item_count) - 1)
					self._draw_trade_ui()
					return
				if action == 'deposit':
					# Deposit: gold decreases OR capacity_used decreases.
					if bool(self.mock_trade_item_only):
						self.trade_capacity_used = max(0, int(self.trade_capacity_used) - 1)
					else:
						self.trade_gold = max(0, int(self.trade_gold) - 1)
					self._draw_trade_ui()
					return

				self._draw_trade_ui()
				return

		# Looting click interactions.
		corpse_roi = self.rois.get('loot_corpse')
		if corpse_roi is not None and _roi_bounds_ok(self.width, self.height, corpse_roi):
			if x >= corpse_roi.x and y >= corpse_roi.y and x < (corpse_roi.x + corpse_roi.width) and y < (corpse_roi.y + corpse_roi.height):
				if bool(self.mock_loot_container_opens):
					self.loot_container_open = True
					self._draw_looting_ui()
				return

		take_roi = self.rois.get('loot_take')
		if take_roi is not None and _roi_bounds_ok(self.width, self.height, take_roi):
			if x >= take_roi.x and y >= take_roi.y and x < (take_roi.x + take_roi.width) and y < (take_roi.y + take_roi.height):
				# Only succeed if container is open.
				if bool(self.loot_container_open) and bool(self.mock_loot_inventory_delta):
					self.inventory_gold_count += 1
					self.inventory_capacity_used += 1
					self._draw_looting_ui()
				return

		roi = self.rois.get('battle_list')
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return
		if x < roi.x or y < roi.y or x >= (roi.x + roi.width) or y >= (roi.y + roi.height):
			return

		row_h = max(1, int(self.battle_list_row_height))
		row_index = (int(y) - int(roi.y)) // row_h
		if row_index < 0 or row_index >= len(self.battle_list_rows):
			return

		behavior = (self.click_behavior or 'normal').strip().lower()
		if behavior == 'no_highlight':
			# Click is ignored.
			return
		if behavior == 'wrong_row':
			# Highlight a different row (deterministic).
			row_index = 0 if row_index != 0 else min(len(self.battle_list_rows) - 1, 1)
			self.battle_list_selected_row = int(row_index)
			self.target_frame_visible = True
			self.target_frame_name = self.battle_list_rows[int(row_index)].name
			return
		if behavior == 'wrong_target_name':
			self.battle_list_selected_row = int(row_index)
			self.target_frame_visible = True
			self.target_frame_name = 'WRONG'
			return

		# Normal selection.
		self.battle_list_selected_row = int(row_index)
		self.target_frame_visible = True
		self.target_frame_name = self.battle_list_rows[int(row_index)].name

	def _draw_minimap(self) -> None:
		minimap = self.rois.get('minimap')
		if minimap is None:
			return
		if not _roi_bounds_ok(self.width, self.height, minimap):
			return

		# Clear minimap region.
		self.fill_roi('minimap', 0, 0, 0)

		# Force-black minimap ROI while keeping full-frame variance (anti-noise guard test).
		if bool(self.mock_cavebot_minimap_force_black):
			self._noise_bit ^= 1
			# Toggle a full row outside minimap for full-frame luma variance.
			row_stride = self.width * 3
			val = 255 if self._noise_bit else 0
			end = min(len(self.rgb), row_stride)
			for i in range(0, end, 3):
				self.rgb[i] = val
				self.rgb[i + 1] = val
				self.rgb[i + 2] = val
			return

		# Optional visual noise (must NOT count as progress).
		if self.minimap_noise:
			self._noise_bit ^= 1
			row_stride = self.width * 3
			idx = (minimap.y * row_stride) + (minimap.x * 3)
			if 0 <= idx < len(self.rgb):
				self.rgb[idx] = 255 if self._noise_bit else 0

		# In noise-only mode, do not render the marker at all (marker missing).
		if bool(self.mock_cavebot_noise_only):
			return

		# Draw player marker (magenta) at a deterministic position based on (x,y).
		mw = max(1, int(minimap.width))
		mh = max(1, int(minimap.height))
		mx = int(self.pos_x) % mw
		my = int(self.pos_y) % mh
		row_stride = self.width * 3
		for dy in (-2, -1, 0, 1, 2):
			for dx in (-2, -1, 0, 1, 2):
				x = mx + dx
				y = my + dy
				if x < 0 or y < 0 or x >= mw or y >= mh:
					continue
				idx0 = ((minimap.y + y) * row_stride) + ((minimap.x + x) * 3)
				if 0 <= idx0 + 2 < len(self.rgb):
					self.rgb[idx0] = 255
					self.rgb[idx0 + 1] = 0
					self.rgb[idx0 + 2] = 255

		# Optional second static marker blob (same color) for stabilization tests.
		if bool(self.mock_cavebot_dual_marker):
			sx = max(0, min(mw - 1, mw - 8))
			sy = max(0, min(mh - 1, mh - 8))
			for dy in (-2, -1, 0, 1, 2):
				for dx in (-2, -1, 0, 1, 2):
					x = sx + dx
					y = sy + dy
					if x < 0 or y < 0 or x >= mw or y >= mh:
						continue
					idx0 = ((minimap.y + y) * row_stride) + ((minimap.x + x) * 3)
					if 0 <= idx0 + 2 < len(self.rgb):
						self.rgb[idx0] = 255
						self.rgb[idx0 + 1] = 0
						self.rgb[idx0 + 2] = 255

	def frame(self) -> Frame:
		# Targeting UI overlays.
		self._draw_battle_list()
		self._draw_target_frame()
		# Healing UI overlays.
		self._draw_healing_ui()
		# Combat UI overlays.
		self._draw_combat_ui()
		# Looting UI overlays.
		self._draw_looting_ui()
		# Deposit UI overlays.
		self._draw_deposit_ui()
		# Trade UI overlays.
		self._draw_trade_ui()

		ts = time.monotonic_ns()
		rgb_bytes = bytes(self.rgb)
		digest = hashlib.sha256(rgb_bytes).hexdigest()

		minimap_roi = self.rois.get('minimap')
		minimap_rgb = b''
		minimap_digest = ''
		minimap_detected = False
		minimap_w = 0
		minimap_h = 0
		if minimap_roi is not None and _roi_bounds_ok(self.width, self.height, minimap_roi):
			minimap_w = int(minimap_roi.width)
			minimap_h = int(minimap_roi.height)
			row_stride = self.width * 3
			minimap_buf = bytearray(minimap_roi.width * minimap_roi.height * 3)
			out_row_stride = minimap_roi.width * 3
			for row in range(minimap_roi.height):
				src_start = ((minimap_roi.y + row) * row_stride) + (minimap_roi.x * 3)
				src_end = src_start + out_row_stride
				dst_start = row * out_row_stride
				minimap_buf[dst_start : dst_start + out_row_stride] = rgb_bytes[src_start:src_end]
			minimap_rgb = bytes(minimap_buf)
			minimap_detected = bool(minimap_rgb)
			minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ''

		frame_out = Frame(
			width=self.width,
			height=self.height,
			monotonic_ts_ns=ts,
			digest_hex=digest,
			rgb=rgb_bytes,
			minimap_detected=minimap_detected,
			minimap_rgb=minimap_rgb,
			minimap_width=minimap_w,
			minimap_height=minimap_h,
			minimap_digest_hex=minimap_digest,
		)

		# Decay cooldown deterministically after each produced frame.
		if self.heal_cooldown_frames_left > 0:
			self.heal_cooldown_frames_left -= 1

		if self.combat_feedback_frames_left > 0:
			self.combat_feedback_frames_left -= 1
		if self.combat_cooldown_frames_left > 0:
			self.combat_cooldown_frames_left -= 1

		return frame_out

	def _toggle_roi_byte(self, roi_name: str) -> None:
		roi = self.rois.get(roi_name)
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return

		# Toggle the ROI between "off" (black) and "on" (white).
		row_stride = self.width * 3
		idx0 = (roi.y * row_stride) + (roi.x * 3)
		is_on = self.rgb[idx0] != 0
		if is_on:
			self.fill_roi(roi_name, 0, 0, 0)
		else:
			self.fill_roi(roi_name, 255, 255, 255)

	def _encode_position(self) -> None:
		# Encode position as 3x uint16 (x,y,z) in little-endian inside ROI 'position'.
		roi = self.rois.get('position')
		if roi is None:
			return
		rgb = self._roi_bytes_view(roi)
		if rgb is None or len(rgb) < 6:
			return
		rgb[0:2] = int(self.pos_x).to_bytes(2, 'little', signed=False)
		rgb[2:4] = int(self.pos_y).to_bytes(2, 'little', signed=False)
		rgb[4:6] = int(self.pos_z).to_bytes(2, 'little', signed=False)

	def _roi_bytes_view(self, roi: Roi) -> Optional[memoryview]:
		if not _roi_bounds_ok(self.width, self.height, roi):
			return None
		# This helper only supports 1-row ROIs to keep it simple and deterministic.
		if roi.height != 1:
			return None
		row_stride = self.width * 3
		start = roi.y * row_stride + roi.x * 3
		end = start + roi.width * 3
		if start < 0 or end > len(self.rgb):
			return None
		return memoryview(self.rgb)[start:end]

	def _bump_hp(self, amount: int = 20) -> None:
		roi = self.rois.get('hp_bar')
		if roi is None:
			return
		# Increase red channel as a proxy for HP.
		row_stride = self.width * 3
		for y in range(roi.y, roi.y + roi.height):
			for x in range(roi.x, roi.x + roi.width):
				idx = (y * row_stride) + (x * 3)
				self.rgb[idx] = min(255, int(self.rgb[idx]) + int(amount))

	def _set_bool_roi(self, roi_name: str, value: bool) -> None:
		self.fill_roi(roi_name, 255, 255, 255) if value else self.fill_roi(roi_name, 0, 0, 0)

	def _move(self, dx: int, dy: int) -> None:
		if bool(self.mock_cavebot_noise_only):
			# Deterministic noise-only: marker never moves (and is not rendered).
			self._draw_minimap()
			return
		if bool(self.mock_cavebot_marker_static):
			# Marker is visible but does not move.
			self._draw_minimap()
			return
		if bool(self.mock_cavebot_marker_wrong_direction):
			dx = -int(dx)
			dy = -int(dy)

		# In progress_ok mode, make the step large enough to satisfy typical
		# gate thresholds (min_pixel_delta) deterministically.
		if bool(self.mock_cavebot_progress_ok):
			dx = int(dx) * 2
			dy = int(dy) * 2

		# Default (including progress_ok): apply movement.
		self.pos_x += int(dx)
		self.pos_y += int(dy)
		self._encode_position()
		self._draw_minimap()

	def fill_roi(self, roi_name: str, r: int, g: int, b: int) -> None:
		roi = self.rois.get(roi_name)
		if roi is None:
			return
		if not _roi_bounds_ok(self.width, self.height, roi):
			return

		row_stride = self.width * 3
		rr = int(max(0, min(255, r)))
		gg = int(max(0, min(255, g)))
		bb = int(max(0, min(255, b)))
		for y in range(roi.y, roi.y + roi.height):
			for x in range(roi.x, roi.x + roi.width):
				idx = (y * row_stride) + (x * 3)
				self.rgb[idx] = rr
				self.rgb[idx + 1] = gg
				self.rgb[idx + 2] = bb

	def on_key(self, key: str) -> None:
		raw = key.strip()
		kind = self.key_kinds.get(raw, 'toggle')
		if kind == 'noop':
			self.on_noop()
			return
		if kind == 'move_up':
			self._move(0, -1)
			return
		if kind == 'move_down':
			self._move(0, 1)
			return
		if kind == 'move_left':
			self._move(-1, 0)
			return
		if kind == 'move_right':
			self._move(1, 0)
			return
		if kind == 'heal':
			self._apply_heal()
			self._draw_healing_ui()
			return
		if kind == 'attack':
			self._apply_attack()
			self._draw_combat_ui()
			self._draw_target_frame()
			return
		if kind == 'target_on':
			self._set_bool_roi('target_indicator', True)
			return
		if kind == 'loot':
			# Premium quick-loot key.
			if not bool(self.mock_loot_premium):
				return
			if bool(self.mock_loot_inventory_delta):
				self.inventory_gold_count += 1
				self.inventory_capacity_used += 1
			self._draw_looting_ui()
			return
		if kind == 'deposit':
			# Deposit requires depot open.
			if not bool(self.depot_open):
				self._draw_deposit_ui()
				return
			if bool(self.mock_deposit_no_delta):
				self._draw_deposit_ui()
				self._draw_looting_ui()
				return
			if bool(self.mock_deposit_success):
				# Move 1 unit.
				if self.inventory_gold_count > 0:
					self.inventory_gold_count -= 1
				if self.inventory_capacity_used > 0:
					self.inventory_capacity_used -= 1
				self.depot_item_count += 1
				self._draw_deposit_ui()
				self._draw_looting_ui()
				return
			if bool(self.mock_deposit_partial):
				# Inventory decreases by 2, depot increases by 1.
				if self.inventory_gold_count > 0:
					self.inventory_gold_count = max(0, int(self.inventory_gold_count) - 2)
				if self.inventory_capacity_used > 0:
					self.inventory_capacity_used = max(0, int(self.inventory_capacity_used) - 2)
				self.depot_item_count += 1
				self._draw_deposit_ui()
				self._draw_looting_ui()
				return
			# Default: no evidence.
			self._draw_deposit_ui()
			self._draw_looting_ui()
			return
		if kind == 'trade_on':
			self._set_bool_roi('trade', True)
			return
		if kind == 'depot_on':
			self._set_bool_roi('depot', True)
			return

		roi_name_opt = self.key_effects.get(raw)
		if roi_name_opt:
			self._toggle_roi_byte(roi_name_opt)

	def on_noop(self) -> None:
		# Noop can still change pixels via minimap noise, but must not move the marker.
		self._draw_minimap()
		self._draw_battle_list()
		self._draw_target_frame()
		self._draw_healing_ui()
		self._draw_looting_ui()
		self._draw_deposit_ui()
		self._draw_trade_ui()


@dataclass(frozen=True, slots=True)
class MockBattleListRow:
	name: str
	hp_bar_visible: bool
	is_attackable: bool
