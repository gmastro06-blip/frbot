from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

from .errors import ContractViolation
from .capture import CaptureStatus
from .evidence import Roi
from .input import InputStatus


@dataclass(frozen=True, slots=True)
class Tile:
    x: int
    y: int
    z: int
    walkable: bool = True


@dataclass(slots=True)
class PositionState:
    x: int
    y: int
    z: int
    source: Literal["minimap", "minimap_track"]
    confidence: float

    def tile(self) -> Tile:
        return Tile(x=int(self.x), y=int(self.y), z=int(self.z), walkable=True)


@dataclass(slots=True)
class CavebotState:
    waypoints: tuple[Tile, ...] = ()
    current_index: int = 0
    last_move_tick: int = 0
    stuck_counter: int = 0
    stuck_waypoint: Optional[tuple[int, int, int]] = None
    stuck_started_ts_ms: int = 0

    # Gate Cavebot state (minimap + marker tracking).
    gate_waypoints: tuple["Waypoint", ...] = ()
    gate_waypoint_index: int = 0
    gate_attempts_used: int = 0
    gate_ticks_in_waypoint: int = 0
    gate_inputs_sent: int = 0
    gate_reach_streak: int = 0

    def current_waypoint(self) -> Optional[Tile]:
        if not self.waypoints:
            return None
        if self.current_index < 0 or self.current_index >= len(self.waypoints):
            return None
        return self.waypoints[self.current_index]

    def current_gate_waypoint(self) -> Optional["Waypoint"]:
        if not self.gate_waypoints:
            return None
        if self.gate_waypoint_index < 0 or self.gate_waypoint_index >= len(
            self.gate_waypoints
        ):
            return None
        return self.gate_waypoints[self.gate_waypoint_index]


@dataclass(frozen=True, slots=True)
class MinimapMarker:
    x_px: int
    y_px: int
    pixel_count: int


@dataclass(frozen=True, slots=True)
class Waypoint:
    """Cavebot waypoint expressed in minimap pixel space.

    Note: x/y are in minimap pixels (not world tiles). z is carried for future
    compatibility but is not used for pixel-space progress checks.
    """

    waypoint_id: str
    x: int
    y: int
    z: int
    radius_px: int
    max_ticks: int
    waypoint_type: str = "walk"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CavebotTelemetry:
    waypoint_id: str = ""
    # Marker RGB used for semantic minimap tracking. When None, runner falls back
    # to RuntimeConfig.player_marker_rgb.
    marker_rgb: tuple[int, int, int] | None = None
    world_x: int | None = None
    world_y: int | None = None
    world_z: int | None = None
    # Virtual marker position used when the real minimap keeps the marker centered
    # and movement must be inferred from minimap scroll.
    virtual_x_px: int | None = None
    virtual_y_px: int | None = None
    marker_before: Optional[MinimapMarker] = None
    marker_after: Optional[MinimapMarker] = None
    distance_before_px: float = 0.0
    distance_after_px: float = 0.0
    angle_deg: float = 0.0
    attempts_used: int = 0
    inputs_sent: int = 0
    wrong_direction_streak: int = 0
    last_n_distances: list[float] = field(default_factory=list)


@dataclass(slots=True)
class CavebotGateState:
    """Runtime state for the Cavebot REAL/MOCK gate runner."""

    telemetry: CavebotTelemetry = field(default_factory=CavebotTelemetry)


class RuntimeState(str, Enum):
    INIT = "INIT"
    PREFLIGHT = "PREFLIGHT"
    READY = "READY"
    RUNNING = "RUNNING"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Only supported modes: real/mock."""

    mode: str
    tick_hz: float = 20.0
    # ROI config used for minimap detection/cropping.
    config_path: str = ""

    # Waypoints (legacy Waypoints/file.json) path.
    bot_config_path: str = ""

    enable_cavebot: bool = True

    # Targeting.
    enable_targeting: bool = False

    # Healing.
    enable_healing: bool = False

    # Combat.
    enable_combat: bool = False

    # Healing evidence ROI names.
    hp_mp_roi: str = "hp_mp"
    hp_bar_roi: str = "hp_bar"
    mp_bar_roi: str = "mp_bar"
    hp_text_roi: str = "hp_text"
    mp_text_roi: str = "mp_text"
    heal_cooldown_roi: str = "heal_cooldown"
    heal_feedback_roi: str = "heal_feedback"

    # Healing thresholds (percent in [0,1]).
    heal_hp_threshold: float = 0.5
    heal_mp_min: float = 0.0
    heal_mp_cost: float = 0.0
    heal_hp_increase_min: float = 0.02
    heal_consistency_tol: float = 0.05

    # Healing input.
    heal_key: str = "F1"

    # Anti-loop guardrails (healing).
    max_attempts_per_heal: int = 2
    max_time_ms_per_heal: int = 2500

    # Battle List evidence ROI name.
    battle_list_roi: str = "battle_list"
    # Optional: target frame/target bar evidence ROI name.
    target_frame_roi: str = "target_frame"

    # Combat evidence ROI names.
    target_hp_bar_roi: str = "target_hp_bar"
    combat_cooldown_roi: str = "combat_cooldown"
    combat_feedback_roi: str = "combat_feedback"

    # Combat thresholds.
    combat_target_hp_decrease_min: float = 0.02

    # Combat input.
    attack_key: str = "SPACE"

    # Anti-loop guardrails (targeting).
    max_attempts_per_target: int = 2
    max_time_ms_per_target: int = 2500

    # Movement evidence ROI name.
    minimap_roi: str = "minimap"

    # Strong window binding (Windows).
    window_hwnd: int = 0
    window_title_substring: str = ""

    # Semantic minimap tracking.
    player_marker_rgb: str = "255,0,255"
    player_marker_tol: int = 30
    player_marker_min_pixels: int = 5
    player_marker_max_pixels: int = 0
    player_marker_min_fill_ratio: float = 0.15
    player_marker_max_aspect_ratio: float = 4.0
    # Conversion from minimap pixels to logical tiles.
    pixels_per_tile: float = 1.0

    # Anti-loop guardrails.
    max_attempts_per_waypoint: int = 3
    max_time_ms_per_waypoint: int = 5000

    # Cavebot gate guardrails (explicit env-config fields).
    cavebot_max_attempts_per_waypoint: int = 3
    cavebot_max_ticks_per_waypoint: int = 20
    cavebot_min_pixel_delta: int = 2

    # Looting gate config.
    looting_max_attempts_per_corpse: int = 3
    looting_max_ticks: int = 20
    looting_require_inventory_delta: bool = True
    looting_mode: Literal["premium", "free"] = "premium"
    quick_loot_key: str = "R"

    # Looting ROI names.
    inventory_text_roi: str = "inventory_text"
    loot_container_open_roi: str = "loot_container_open"
    loot_corpse_roi: str = "loot_corpse"
    loot_take_roi: str = "loot_take"

    # Deposit gate config.
    deposit_max_attempts: int = 3
    deposit_max_ticks: int = 20
    deposit_key: str = "D"

    # Deposit ROI names.
    depot_container_roi: str = "depot_container"

    # Trade gate config.
    trade_max_attempts: int = 3
    trade_max_ticks: int = 20
    trade_action: Literal["buy", "sell", "deposit"] = "buy"
    trade_expected_npc_id: int = 1

    # Trade ROI names.
    trade_inventory_roi: str = "trade_inventory"
    trade_npc_roi: str = "trade_npc"
    trade_action_roi: str = "trade_action"

    # Hunger/Auto-eat gate config.
    enable_hunger: bool = False
    hunger_roi: str = "hunger_status"
    eat_key: str = "F9"
    hungry_rgb: tuple[int, int, int] = (255, 170, 0)
    hunger_color_tol: int = 28
    hunger_match_ratio_min: float = 0.08
    eat_interval_ms: int = 1200

    # Auto-fish gate config.
    enable_fish: bool = False
    fish_roi: str = "fishing_indicator"
    fish_key: str = "F10"
    fish_bite_rgb: tuple[int, int, int] = (0, 255, 0)
    fish_color_tol: int = 30
    fish_match_ratio_min: float = 0.05
    fish_interval_ms: int = 2000

    # Auto-ring gate config.
    enable_ring: bool = False
    ring_slot_roi: str = "ring_slot"
    ring_equip_key: str = "F11"
    ring_switch_interval_ms: int = 5000

    # Auto-supply (potion refill/buy) config.
    enable_supply: bool = False
    supply_hp_threshold: float = 0.5
    supply_mp_threshold: float = 0.3
    health_potion_key: str = "F1"
    mana_potion_key: str = "F2"
    supply_drink_interval_ms: int = 1000

    def __post_init__(self) -> None:
        mode = self.mode.strip().lower()
        if mode not in {"real", "mock"}:
            raise ContractViolation(f"Unsupported mode: {self.mode!r}")
        if self.tick_hz <= 0 or self.tick_hz > 200:
            raise ContractViolation("tick_hz must be in (0, 200]")
        if self.max_attempts_per_waypoint <= 0 or self.max_attempts_per_waypoint > 20:
            raise ContractViolation("max_attempts_per_waypoint must be in [1, 20]")
        if (
            self.max_time_ms_per_waypoint <= 0
            or self.max_time_ms_per_waypoint > 120_000
        ):
            raise ContractViolation("max_time_ms_per_waypoint must be in (0, 120000]")

        if (
            self.cavebot_max_attempts_per_waypoint <= 0
            or self.cavebot_max_attempts_per_waypoint > 20
        ):
            raise ContractViolation(
                "cavebot_max_attempts_per_waypoint must be in [1, 20]"
            )
        if (
            self.cavebot_max_ticks_per_waypoint <= 0
            or self.cavebot_max_ticks_per_waypoint > 10_000
        ):
            raise ContractViolation(
                "cavebot_max_ticks_per_waypoint must be in [1, 10000]"
            )
        if self.cavebot_min_pixel_delta <= 0 or self.cavebot_min_pixel_delta > 50:
            raise ContractViolation("cavebot_min_pixel_delta must be in [1, 50]")

        if (
            self.looting_max_attempts_per_corpse <= 0
            or self.looting_max_attempts_per_corpse > 20
        ):
            raise ContractViolation(
                "looting_max_attempts_per_corpse must be in [1, 20]"
            )
        if self.looting_max_ticks <= 0 or self.looting_max_ticks > 10_000:
            raise ContractViolation("looting_max_ticks must be in [1, 10000]")
        if str(self.quick_loot_key).strip() == "":
            raise ContractViolation("quick_loot_key must be non-empty")

        if self.deposit_max_attempts <= 0 or self.deposit_max_attempts > 20:
            raise ContractViolation("deposit_max_attempts must be in [1, 20]")
        if self.deposit_max_ticks <= 0 or self.deposit_max_ticks > 10_000:
            raise ContractViolation("deposit_max_ticks must be in [1, 10000]")
        if str(self.deposit_key).strip() == "":
            raise ContractViolation("deposit_key must be non-empty")

        if self.trade_max_attempts <= 0 or self.trade_max_attempts > 20:
            raise ContractViolation("trade_max_attempts must be in [1, 20]")
        if self.trade_max_ticks <= 0 or self.trade_max_ticks > 10_000:
            raise ContractViolation("trade_max_ticks must be in [1, 10000]")
        if int(self.trade_expected_npc_id) <= 0:
            raise ContractViolation("trade_expected_npc_id must be > 0")

        if self.max_attempts_per_target <= 0 or self.max_attempts_per_target > 5:
            raise ContractViolation("max_attempts_per_target must be in [1, 5]")
        if self.max_time_ms_per_target <= 0 or self.max_time_ms_per_target > 60_000:
            raise ContractViolation("max_time_ms_per_target must be in (0, 60000]")

        if not (0.0 <= float(self.heal_hp_threshold) <= 1.0):
            raise ContractViolation("heal_hp_threshold must be in [0, 1]")
        if not (0.0 <= float(self.heal_mp_min) <= 1.0):
            raise ContractViolation("heal_mp_min must be in [0, 1]")
        if not (0.0 <= float(self.heal_mp_cost) <= 1.0):
            raise ContractViolation("heal_mp_cost must be in [0, 1]")
        if not (0.0 <= float(self.heal_hp_increase_min) <= 1.0):
            raise ContractViolation("heal_hp_increase_min must be in [0, 1]")
        if not (0.0 <= float(self.heal_consistency_tol) <= 1.0):
            raise ContractViolation("heal_consistency_tol must be in [0, 1]")

        if self.max_attempts_per_heal <= 0 or self.max_attempts_per_heal > 5:
            raise ContractViolation("max_attempts_per_heal must be in [1, 5]")
        if self.max_time_ms_per_heal <= 0 or self.max_time_ms_per_heal > 60_000:
            raise ContractViolation("max_time_ms_per_heal must be in (0, 60000]")

        if not str(self.heal_key).strip():
            raise ContractViolation("heal_key must be non-empty")

        if not (0.0 <= float(self.combat_target_hp_decrease_min) <= 1.0):
            raise ContractViolation("combat_target_hp_decrease_min must be in [0, 1]")
        if not str(self.attack_key).strip():
            raise ContractViolation("attack_key must be non-empty")


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return int(self.x) + int(self.width)

    @property
    def bottom(self) -> int:
        return int(self.y) + int(self.height)

    def contains_rect(self, other: "Rect") -> bool:
        return (
            int(other.x) >= int(self.x)
            and int(other.y) >= int(self.y)
            and int(other.right) <= int(self.right)
            and int(other.bottom) <= int(self.bottom)
        )


@dataclass(frozen=True, slots=True)
class BattleListEntry:
    name: str
    hp_bar_visible: bool
    is_attackable: bool
    screen_bbox: Rect
    row_index: int
    highlighted: bool = False


@dataclass(slots=True)
class TargetState:
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    target_position: Optional[tuple[int, int, int]] = None
    target_rect: Optional[Rect] = None
    source: Literal["battle_list"] = "battle_list"
    confidence: float = 0.0
    locked: bool = False


@dataclass(slots=True)
class TargetingState:
    target: TargetState = field(default_factory=TargetState)
    attempt_target_name: Optional[str] = None
    attempt_count: int = 0
    attempt_started_ts_ms: int = 0
    inputs_sent: int = 0


@dataclass(slots=True)
class RuntimeTelemetry:
    tick_count: int = 0
    last_frame_ts_ns: int = 0
    last_capture_age_ms: int = 0
    last_tick_valid: bool = False
    last_intent: str = ""

    # Additive: per-intent correlation snapshot used for audit-grade evidence.
    last_event_correlation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeStatus:
    state: RuntimeState = RuntimeState.INIT
    reason: str = ""


@dataclass(slots=True)
class RuntimeContext:
    config: RuntimeConfig
    status: RuntimeStatus

    telemetry: RuntimeTelemetry

    capture: Optional[CaptureStatus] = None
    input: Optional[InputStatus] = None

    # Cavebot-only typed state.
    position: PositionState = field(
        default_factory=lambda: PositionState(
            x=0, y=0, z=0, source="minimap", confidence=0.0
        )
    )
    cavebot: CavebotState = field(default_factory=CavebotState)
    cavebot_gate: CavebotGateState = field(default_factory=CavebotGateState)

    # Looting state.
    looting: "LootingState" = field(default_factory=lambda: LootingState())

    # Deposit state.
    deposit: "DepositState" = field(default_factory=lambda: DepositState())

    # Trade state.
    trade: "TradeState" = field(default_factory=lambda: TradeState())

    # Targeting state.
    targeting: TargetingState = field(default_factory=TargetingState)

    # Healing state.
    healing: "HealingState" = field(default_factory=lambda: HealingState())

    # Combat state.
    combat: "CombatState" = field(default_factory=lambda: CombatState())

    # Loaded ROI map (must include minimap in operational modes).
    rois: dict[str, Roi] = field(default_factory=dict)

    # Optional: configured capture frame dimensions (used to map ROI/frame coordinates
    # to window client coordinates for input when capture is OBS source identity).
    frame_width: Optional[int] = None
    frame_height: Optional[int] = None


@dataclass(slots=True)
class HealState:
    hp_percent: Optional[float] = None
    mp_percent: Optional[float] = None
    source: Literal["bar", "text", "bar+text"] = "bar"
    confidence: float = 0.0


@dataclass(slots=True)
class HealingState:
    last: HealState = field(default_factory=HealState)
    attempt_count: int = 0
    attempt_started_ts_ms: int = 0
    # Hunger integration: track eating state
    last_eat_ts_ms: int = 0
    eat_count: int = 0


@dataclass(slots=True)
class CombatState:
    attempt_count: int = 0
    attempt_started_ts_ms: int = 0
    intents_emitted: int = 0
    inputs_sent: int = 0
    last_target_hp: Optional[float] = None
    last_click_xy: Optional[tuple[int, int]] = None
    last_action_type: str = ""
    last_action_value: str = ""


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    slot_counts: dict[str, int]
    capacity_used: Optional[int] = None


@dataclass(frozen=True, slots=True)
class NpcIdentity:
    npc_id: int
    open: bool


@dataclass(frozen=True, slots=True)
class TradeTelemetry:
    npc: Optional[NpcIdentity] = None
    inventory_before: Optional[InventorySnapshot] = None
    inventory_after: Optional[InventorySnapshot] = None
    gold_before: Optional[int] = None
    gold_after: Optional[int] = None
    items_before: Optional[int] = None
    items_after: Optional[int] = None
    capacity_before: Optional[int] = None
    capacity_after: Optional[int] = None


@dataclass(slots=True)
class TradeState:
    attempts_used: int = 0
    inputs_sent: int = 0
    last_npc: Optional[NpcIdentity] = None
    last_inventory_before: Optional[InventorySnapshot] = None
    last_inventory_after: Optional[InventorySnapshot] = None
    last_telemetry: TradeTelemetry = field(default_factory=TradeTelemetry)


@dataclass(slots=True)
class LootingState:
    mode: Literal["premium", "free"] = "premium"
    attempts_used: int = 0
    items_looted: int = 0
    last_inventory: Optional[InventorySnapshot] = None
    container_open: bool = False
    # Targeting integration: require valid target before looting
    target_locked: bool = False
    target_name: Optional[str] = None
    # Targeting integration: require a target to be selected before looting
    target_required: bool = True


@dataclass(slots=True)
class DepositState:
    attempts_used: int = 0
    inputs_sent: int = 0
    last_inventory_before: Optional[InventorySnapshot] = None
    last_inventory_after: Optional[InventorySnapshot] = None
    last_depot_before: Optional["DepotSnapshot"] = None
    last_depot_after: Optional["DepotSnapshot"] = None


@dataclass(frozen=True, slots=True)
class DepotSnapshot:
    item_count: int
    open: bool
