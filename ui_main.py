from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt as _Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QFileDialog,
    QLineEdit,
    QStackedWidget,
)

from logging_setup import setup_logger
from diagnostics.jsonlog import log as log_json
from models import Script, Waypoint, WaypointType, now_iso
from storage import SchemaError, canonical_json, load_script, save_script
from diagnostics.fatal import write_fatal
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from contracts.errors import PreflightFailed
from runtime.minimap_semantics import MarkerConfig, detect_player_marker as detect_minimap_marker, marker_config_from_env
from runtime.minimap_localization import localize_minimap
from runtime.route_recorder import MinimapRouteSampler, RouteRecordingSession, WaypointRecorder
from runtime.route_preflight import run_capture_only as route_capture_only_preflight
from runtime.cavebot_script_loader import ScriptLoaderParams, script_to_runtime_waypoints
from runtime.tibia_map_data import TibiaMapDataset, load_tibia_map_dataset
from runtime.env_bootstrap import load_repo_env
from adapters.capture.obs_source_real import list_obs_input_names
from adapters.windows import win32 as w32


# PySide6 exposes many Qt enums/flags dynamically. Some type-checker versions
# (and certain bundled stubs) can report false-positive attribute errors.
Qt = cast(Any, _Qt)
_QAbstractItemView = cast(Any, QAbstractItemView)
_QMessageBox = cast(Any, QMessageBox)


_LOG = setup_logger()


load_repo_env()


_DARK_BG = "#0f0f0f"
_PANEL_BG = "#141414"
_TEXT = "#d0d0d0"
_MUTED = "#9a9a9a"
_ACCENT = "#c43b3b"
_BORDER = "#2b2b2b"
_BTN_BG = "#1a1a1a"
_BTN_HOVER = "#232323"
_BTN_PRESSED = "#2a2a2a"
_DEFAULT_PLAYER_MARKER_RGB = "255,0,255"
_DEFAULT_PLAYER_MARKER_TOL = "30"


_REQUIRED_REAL_ROIS: set[str] = {"minimap", "battle_list", "hp_mp", "target_frame"}
_ALLOWED_PROD_EMERGENCY_EXTRA_ROIS: set[str] = {
    "target_hp_bar",
    "combat_cooldown",
    "combat_feedback",
    "inventory_text",
    "chat_loot_area",
    "loot_corpse",
    "depot_container",
    "trade_inventory",
    "trade_npc",
    "trade_action",
}
_ALLOWED_PROD_FULL_EXTRA_ROIS: set[str] = {
    "target_hp_bar",
    "combat_cooldown",
    "combat_feedback",
    "hp_bar",
    "mp_bar",
    "hp_text",
    "mp_text",
    "heal_cooldown",
    "heal_feedback",
    "inventory_text",
    "chat_loot_area",
    "loot_corpse",
    "loot_container_open",
    "loot_take",
    "depot_container",
    "trade_inventory",
    "trade_npc",
    "trade_action",
}


def app_stylesheet() -> str:
    return f"""
    QMainWindow {{
        background: {_DARK_BG};
        color: {_TEXT};
    }}

    QWidget {{
        background: {_DARK_BG};
        color: {_TEXT};
        font-size: 12px;
    }}

    QGroupBox {{
        border: 1px solid {_BORDER};
        border-radius: 8px;
        margin-top: 8px;
        padding: 10px;
        background: {_PANEL_BG};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {_TEXT};
        font-size: 11px;
    }}

    QLabel#header {{
        font-size: 18px;
        font-weight: 600;
        padding: 6px 0;
    }}

    QListWidget {{
        background: {_PANEL_BG};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 6px;
    }}

    QTableView {{
        background: {_PANEL_BG};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        gridline-color: #2b2b2b;
        selection-background-color: #202020;
        selection-color: {_TEXT};
        alternate-background-color: #111111;
    }}

    QTableView::item {{
        padding: 6px;
    }}

    QHeaderView::section {{
        background: #121212;
        color: {_TEXT};
        border: 0px;
        border-bottom: 1px solid {_BORDER};
        padding: 6px;
    }}

    QPushButton {{
        background: {_BTN_BG};
        border: 1px solid {_BORDER};
        border-radius: 10px;
        padding: 8px 10px;
        min-height: 30px;
        color: {_TEXT};
    }}

    QPushButton:hover {{
        background: {_BTN_HOVER};
        border: 1px solid {_ACCENT};
    }}

    QPushButton:pressed {{
        background: {_BTN_PRESSED};
    }}

    QPushButton#danger {{
        border: 1px solid {_ACCENT};
        color: {_TEXT};
    }}

    QCheckBox {{
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
    }}

    QSpinBox {{
        background: {_PANEL_BG};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 4px 8px;
    }}

    QLineEdit {{
        background: {_PANEL_BG};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 4px 8px;
    }}

    QScrollArea {{
        border: 0px;
        background: {_DARK_BG};
    }}

    QRadioButton {{
        spacing: 8px;
    }}

    QFrame#separator {{
        background: #1f1f1f;
        min-height: 1px;
        max-height: 1px;
    }}
    """


class WaypointsTableModel(QAbstractTableModel):
    COLS = ["Type", "X", "Y", "Z", "Options", "Enabled"]

    def __init__(self, script: Script, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._script = script

    def script(self) -> Script:
        return self._script

    def set_script(self, script: Script) -> None:
        self.beginResetModel()
        self._script = script
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._script.waypoints)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self.COLS)

    def headerData(self, section: int, orientation: _Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLS):
            return self.COLS[section]
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Any:  # type: ignore[override]
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = index.column()
        if col in (0, 1, 2, 3, 4):
            flags |= Qt.ItemFlag.ItemIsEditable
        if col == 5:
            flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._script.waypoints)):
            return None

        wp = self._script.waypoints[row]

        if col == 5 and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if wp.enabled else Qt.CheckState.Unchecked

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return str(wp.type)
            if col == 1:
                return int(wp.x)
            if col == 2:
                return int(wp.y)
            if col == 3:
                return int(wp.z)
            if col == 4:
                try:
                    return json.dumps(dict(wp.options or {}), ensure_ascii=False, sort_keys=True)
                except Exception:
                    return "{}"
            if col == 5:
                return bool(wp.enabled)

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:  # type: ignore[override]
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._script.waypoints)):
            return False

        wp = self._script.waypoints[row]

        try:
            if col == 5 and role in (Qt.ItemDataRole.EditRole, Qt.ItemDataRole.CheckStateRole):
                enabled = (
                    bool(value == Qt.CheckState.Checked)
                    if role == Qt.ItemDataRole.CheckStateRole
                    else bool(value)
                )
                wp.enabled = enabled
            elif role != Qt.ItemDataRole.EditRole:
                return False
            elif col == 0:
                t = str(value).strip()
                if t not in set(WaypointType.values()):
                    return False
                wp.type = t
            elif col == 1:
                wp.x = int(value)
            elif col == 2:
                wp.y = int(value)
            elif col == 3:
                wp.z = int(value)
            elif col == 4:
                s = str(value).strip()
                if s == "":
                    wp.options = {}
                else:
                    obj = json.loads(s)
                    if not isinstance(obj, dict):
                        return False
                    wp.options = dict(obj)
            else:
                return False

            self.dataChanged.emit(
                index,
                index,
                [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.EditRole,
                    Qt.ItemDataRole.CheckStateRole,
                ],
            )
            return True
        except Exception:
            return False

    def insert_waypoint(self, wp: Waypoint) -> None:
        row = len(self._script.waypoints)
        self.beginInsertRows(QModelIndex(), row, row)
        self._script.waypoints.append(wp)
        self.endInsertRows()

    def delete_rows(self, rows: list[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._script.waypoints):
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._script.waypoints[row]
                self.endRemoveRows()


class MainWindow(QMainWindow):
    actionRequested = Signal(str, object)  # (action_name, Waypoint)
    scriptSaved = Signal(str)  # path
    scriptLoaded = Signal(str, object)  # (path, Script)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Waypoint/Script Editor")
        self.resize(1360, 820)
        self.setMinimumSize(1100, 700)

        self._current_path: str | None = None
        self._script = Script(name="script1")
        self._last_saved_state = canonical_json(self._script)

        self._route_timer = QTimer(self)
        self._route_timer.setInterval(120)
        self._route_capture: Any = None
        self._route_binding: Any = None
        self._route_sampler: MinimapRouteSampler | None = None
        self._route_session: RouteRecordingSession | None = None
        self._route_marker_cfg: MarkerConfig | None = None
        self._route_tibia_dataset: TibiaMapDataset | None = None
        self._route_world_by_rel: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        self._route_last_world: tuple[int, int, int] | None = None
        self._route_world_lock_on: bool = False
        self._route_world_lock_last_score: float | None = None
        self._route_last_minimap_digest: str = ""
        self._route_floor_change_streak: int = 0
        self._route_last_tile: tuple[int, int, int] | None = None
        self._route_recording_active: bool = False
        self._waypoint_recorder: WaypointRecorder | None = None
        self._waypoint_export_path: str | None = None
        self._cavebot_running: bool = False
        self._cavebot_timer = QTimer(self)
        self._cavebot_proc: subprocess.Popen[Any] | None = None
        self._cavebot_last_exit: int | None = None
        self._cavebot_prev_waypoints_env: str | None = None
        self._cavebot_prev_waypoints_file_env: str | None = None
        self._cavebot_prev_auto_route_env: str | None = None
        self._cavebot_prev_waypoint_space_env: str | None = None
        self._last_loaded_script_path: str | None = None

        self._build_ui()
        self._wire_events()
        self._refresh_script_list()
        self._sync_right_controls_from_script()
        self._sync_aux_controls_from_metadata()
        self._apply_runtime_env_from_ui()
        self._restore_last_loaded_script()

    def _ui_state_path(self) -> Path:
        return Path.cwd() / "diagnostics" / "ui_state.json"

    def _read_ui_state(self) -> dict[str, Any]:
        path = self._ui_state_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_ui_state(self) -> None:
        try:
            path = self._ui_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_script_path": str(self._last_loaded_script_path or "").strip(),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _waypoints_dir(self) -> Path:
        p = Path.cwd() / "Waypoints"
        if p.exists() and p.is_dir():
            return p
        return Path.cwd()

    def _default_load_dir(self) -> Path:
        if self._current_path:
            cur = Path(str(self._current_path))
            if cur.exists() and cur.is_file():
                return cur.parent

        st = self._read_ui_state()
        raw = str(st.get("last_script_path") or "").strip()
        if raw:
            p = Path(raw)
            if p.exists() and p.is_file():
                return p.parent

        return self._waypoints_dir()

    def _set_current_script_path(self, path: str | None) -> None:
        val = str(path or "").strip()
        if not val:
            self._current_path = None
            return
        self._current_path = val
        self._last_loaded_script_path = val
        self._write_ui_state()

    def _restore_last_loaded_script(self) -> None:
        st = self._read_ui_state()
        raw = str(st.get("last_script_path") or "").strip()
        if not raw:
            return
        p = Path(raw)
        if not p.exists() or not p.is_file():
            self._last_loaded_script_path = None
            self._write_ui_state()
            return
        try:
            script = load_script(str(p))
        except Exception as exc:
            _LOG.warning("Failed to restore last loaded script %s: %s", p, exc)
            self._last_loaded_script_path = None
            self._write_ui_state()
            return

        self._script = script
        self.model.set_script(self._script)
        self._set_current_script_path(str(p))
        self._last_saved_state = canonical_json(self._script)
        self._refresh_script_list()
        self._sync_right_controls_from_script()
        self._sync_aux_controls_from_metadata()
        self._apply_runtime_env_from_ui()
        self._update_title()
        _LOG.info("Restored last loaded script from %s", p)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        nav = QGroupBox("Pantallas")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(10, 10, 10, 10)
        nav_layout.setSpacing(10)

        self.btn_screen_home = QPushButton("Home")
        self.btn_screen_waypoints = QPushButton("Waypoints")
        self.btn_screen_healing = QPushButton("Configurar Healing")
        self.btn_screen_cavebot = QPushButton("Configurar Cavebot")
        self.btn_screen_autos = QPushButton("Autos")
        self.btn_screen_settings = QPushButton("Configuración")

        nav_layout.addWidget(self.btn_screen_home)
        nav_layout.addWidget(self.btn_screen_healing)
        nav_layout.addWidget(self.btn_screen_cavebot)
        nav_layout.addWidget(self.btn_screen_autos)
        nav_layout.addWidget(self.btn_screen_waypoints)
        nav_layout.addWidget(self.btn_screen_settings)
        outer.addWidget(nav)

        self.screens = QStackedWidget()
        outer.addWidget(self.screens, 1)

        home_screen = QWidget()
        home_layout = QVBoxLayout(home_screen)
        home_layout.setContentsMargins(24, 24, 24, 24)
        home_layout.setSpacing(12)
        home_header = QLabel("Panel Principal")
        home_header.setObjectName("header")
        self.btn_home_cavebot = QPushButton("Cavebot")
        self.btn_home_healing = QPushButton("Healing")
        self.btn_home_waypoints = QPushButton("Waypoints")
        self.btn_home_exit = QPushButton("Salir")
        home_layout.addWidget(home_header)
        home_layout.addWidget(self.btn_home_cavebot)
        home_layout.addWidget(self.btn_home_healing)
        home_layout.addWidget(self.btn_home_waypoints)
        home_layout.addWidget(self.btn_home_exit)
        home_layout.addStretch(1)
        self.screens.addWidget(home_screen)

        # Healing screen
        healing_screen = QWidget()
        healing_layout = QVBoxLayout(healing_screen)
        healing_layout.setContentsMargins(10, 10, 10, 10)
        healing_layout.setSpacing(10)

        healing_header = QLabel("Healing")
        healing_header.setObjectName("header")
        healing_layout.addWidget(healing_header)

        healing_box = QGroupBox("Parámetros de Healing")
        healing_form = QFormLayout(healing_box)
        healing_form.setContentsMargins(10, 10, 10, 10)

        self.chk_healing_enabled = QCheckBox("Habilitar healing")
        self.chk_healing_enabled.setChecked(True)
        self.input_heal_key = QLineEdit("F1")
        self.spin_heal_hp_threshold = QSpinBox()
        self.spin_heal_hp_threshold.setRange(1, 100)
        self.spin_heal_hp_threshold.setValue(50)

        healing_form.addRow("Estado", self.chk_healing_enabled)
        healing_form.addRow("Hotkey heal", self.input_heal_key)
        healing_form.addRow("HP % umbral", self.spin_heal_hp_threshold)
        healing_layout.addWidget(healing_box)
        healing_layout.addStretch(1)

        self.btn_healing_back = QPushButton("Volver")
        healing_layout.addWidget(self.btn_healing_back)
        self.screens.addWidget(healing_screen)

        # Cavebot screen (existing editor)
        cavebot_screen = QWidget()
        cavebot_layout = QHBoxLayout(cavebot_screen)
        cavebot_layout.setContentsMargins(0, 0, 0, 0)
        cavebot_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        self.scripts_list = QListWidget()
        left_layout.addWidget(self.scripts_list, 1)

        self.btn_save = QPushButton("Save Script")
        self.btn_load = QPushButton("Load Script")
        left_layout.addWidget(self.btn_save)
        left_layout.addWidget(self.btn_load)

        # Center
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 10, 10, 10)
        center_layout.setSpacing(10)

        header = QLabel("Waypoints")
        header.setObjectName("header")
        center_layout.addWidget(header)

        coords = QGroupBox("Coordinates")
        coords_layout = QFormLayout(coords)
        coords_layout.setContentsMargins(10, 10, 10, 10)

        self.spin_x = QSpinBox()
        self.spin_y = QSpinBox()
        self.spin_z = QSpinBox()
        self.spin_x.setRange(0, 65535)
        self.spin_y.setRange(0, 65535)
        self.spin_z.setRange(0, 15)
        coords_layout.addRow("X", self.spin_x)
        coords_layout.addRow("Y", self.spin_y)
        coords_layout.addRow("Z", self.spin_z)
        center_layout.addWidget(coords)

        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(_QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(_QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            _QAbstractItemView.EditTrigger.DoubleClicked
            | _QAbstractItemView.EditTrigger.SelectedClicked
            | _QAbstractItemView.EditTrigger.EditKeyPressed
        )

        self.model = WaypointsTableModel(self._script)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 220)
        self.table.setColumnWidth(5, 80)

        center_layout.addWidget(self.table, 1)

        self.btn_delete = QPushButton("Delete Waypoints")
        self.btn_delete.setObjectName("danger")
        center_layout.addWidget(self.btn_delete)

        # Right
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)

        dir_group = QGroupBox("Direction")
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setContentsMargins(8, 8, 8, 8)
        dir_layout.setSpacing(4)
        self.rb_north = QRadioButton("North")
        self.rb_west = QRadioButton("West")
        self.rb_center = QRadioButton("Center")
        self.rb_east = QRadioButton("East")
        self.rb_south = QRadioButton("South")
        self.rb_center.setChecked(True)
        for rb in (self.rb_north, self.rb_west, self.rb_center, self.rb_east, self.rb_south):
            dir_layout.addWidget(rb)
        right_layout.addWidget(dir_group)

        self.chk_enabled = QCheckBox("Enabled")
        self.chk_run_to_target = QCheckBox("Run to target")
        right_layout.addWidget(self.chk_enabled)
        right_layout.addWidget(self.chk_run_to_target)

        buttons_box = QGroupBox("Actions")
        grid = QGridLayout(buttons_box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.action_buttons: dict[str, QPushButton] = {}
        actions = [
            ("walk", "Walk"),
            ("walk_ignore", "Walk Ignore"),
            ("single_move", "Single Move"),
            ("move_up", "Move Up"),
            ("move_down", "Move Down"),
            ("use_right_click", "Use Right Click"),
            ("open_door", "Open Door"),
            ("use_ladder", "Use Ladder"),
            ("rope", "Rope"),
            ("refill", "Refill"),
            ("travel", "Travel"),
            ("deposit", "Deposit"),
            ("trade", "Trade"),
        ]
        for i, (action_name, label) in enumerate(actions):
            btn = QPushButton(label)
            btn.setObjectName("danger")
            btn.setProperty("action_name", action_name)
            btn.setMinimumHeight(34)
            self.action_buttons[action_name] = btn
            r = i // 2
            c = i % 2
            grid.addWidget(btn, r, c)
        right_layout.addWidget(buttons_box, 1)

        recorder_box = QGroupBox("Route Recorder")
        recorder_layout = QVBoxLayout(recorder_box)
        recorder_layout.setContentsMargins(8, 8, 8, 8)
        recorder_layout.setSpacing(6)

        self.lbl_route_status = QLabel("Recorder: detenido")
        self.lbl_route_counts = QLabel("Steps: 0 | Move: 0 | Rope: 0 | Shovel: 0 | Pick: 0 | Up: 0 | Down: 0")
        self.lbl_route_world_lock = QLabel("✗ World lock: OFF")
        self.lbl_route_counts.setStyleSheet(f"color: {_MUTED};")
        self.lbl_route_world_lock.setStyleSheet(f"color: {_ACCENT};")
        self._update_route_world_lock_tooltip()
        self.btn_route_start = QPushButton("Iniciar grabación")
        self.btn_route_stop = QPushButton("Pausar")
        self.btn_route_apply = QPushButton("Aplicar ruta al editor")
        self.btn_route_reset = QPushButton("Reset sesión")
        self.btn_route_mark_rope = QPushButton("Marcar Rope")
        self.btn_route_mark_shovel = QPushButton("Marcar Shovel")
        self.btn_route_mark_pick = QPushButton("Marcar Pick")
        self.btn_route_mark_up = QPushButton("Marcar Subir")
        self.btn_route_mark_down = QPushButton("Marcar Bajar")
        self.btn_route_export = QPushButton("Exportar sesión")
        self.btn_route_save_recording = QPushButton("Guardar grabación")
        self.btn_route_open_folder = QPushButton("Abrir carpeta")
        self.route_steps_list = QListWidget()
        self.lbl_route_hotkeys = QLabel("Hotkeys: F5=Start, F6=Pausa/Resume, F7=Rope, F8=Shovel, F9=Pick, F10=Up, F11=Down, WASD/Flechas=Move")
        self.lbl_route_hotkeys.setStyleSheet(f"color: {_MUTED};")
        self.lbl_route_hotkeys.setWordWrap(True)

        for btn in (
            self.btn_route_start,
            self.btn_route_stop,
            self.btn_route_apply,
            self.btn_route_reset,
            self.btn_route_mark_rope,
            self.btn_route_mark_shovel,
            self.btn_route_mark_pick,
            self.btn_route_mark_up,
            self.btn_route_mark_down,
            self.btn_route_export,
            self.btn_route_save_recording,
            self.btn_route_open_folder,
        ):
            btn.setMinimumHeight(34)

        self.btn_route_stop.setEnabled(False)
        self.btn_route_apply.setEnabled(False)
        self.btn_route_mark_rope.setEnabled(False)
        self.btn_route_mark_shovel.setEnabled(False)
        self.btn_route_mark_pick.setEnabled(False)
        self.btn_route_mark_up.setEnabled(False)
        self.btn_route_mark_down.setEnabled(False)
        self.btn_route_export.setEnabled(False)
        self.btn_route_save_recording.setEnabled(False)
        self.btn_route_open_folder.setEnabled(False)

        recorder_layout.addWidget(self.lbl_route_status)
        recorder_layout.addWidget(self.lbl_route_counts)
        recorder_layout.addWidget(self.lbl_route_world_lock)
        recorder_layout.addWidget(self.btn_route_start)
        recorder_layout.addWidget(self.btn_route_stop)
        recorder_layout.addWidget(self.btn_route_apply)
        recorder_layout.addWidget(self.btn_route_reset)
        recorder_layout.addWidget(self.btn_route_mark_rope)
        recorder_layout.addWidget(self.btn_route_mark_shovel)
        recorder_layout.addWidget(self.btn_route_mark_pick)
        recorder_layout.addWidget(self.btn_route_mark_up)
        recorder_layout.addWidget(self.btn_route_mark_down)
        recorder_layout.addWidget(self.btn_route_export)
        recorder_layout.addWidget(self.btn_route_save_recording)
        recorder_layout.addWidget(self.btn_route_open_folder)
        recorder_layout.addWidget(self.route_steps_list, 1)
        recorder_layout.addWidget(self.lbl_route_hotkeys)
        right_layout.addWidget(recorder_box)

        self.shortcut_route_start = QShortcut(QKeySequence("F5"), self)
        self.shortcut_route_stop = QShortcut(QKeySequence("F6"), self)
        self.shortcut_mark_rope = QShortcut(QKeySequence("F7"), self)
        self.shortcut_mark_shovel = QShortcut(QKeySequence("F8"), self)
        self.shortcut_mark_pick = QShortcut(QKeySequence("F9"), self)
        self.shortcut_mark_up = QShortcut(QKeySequence("F10"), self)
        self.shortcut_mark_down = QShortcut(QKeySequence("F11"), self)
        self.shortcut_move_w = QShortcut(QKeySequence("W"), self)
        self.shortcut_move_a = QShortcut(QKeySequence("A"), self)
        self.shortcut_move_s = QShortcut(QKeySequence("S"), self)
        self.shortcut_move_d = QShortcut(QKeySequence("D"), self)
        self.shortcut_move_up = QShortcut(QKeySequence("Up"), self)
        self.shortcut_move_left = QShortcut(QKeySequence("Left"), self)
        self.shortcut_move_down = QShortcut(QKeySequence("Down"), self)
        self.shortcut_move_right = QShortcut(QKeySequence("Right"), self)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([260, 650, 420])

        cavebot_status_box = QGroupBox("Estado Cavebot")
        cavebot_status_layout = QHBoxLayout(cavebot_status_box)
        self.btn_cavebot_start = QPushButton("Start")
        self.btn_cavebot_stop = QPushButton("Stop")
        self.lbl_cavebot_status = QLabel("Estado: detenido")
        self.lbl_cavebot_runtime = QLabel("Tick: 0 | WP: -")
        self.btn_cavebot_stop.setEnabled(False)
        cavebot_status_layout.addWidget(self.btn_cavebot_start)
        cavebot_status_layout.addWidget(self.btn_cavebot_stop)
        cavebot_status_layout.addWidget(self.lbl_cavebot_status, 1)
        cavebot_status_layout.addWidget(self.lbl_cavebot_runtime)

        cavebot_shell = QVBoxLayout()
        cavebot_shell.setContentsMargins(0, 0, 0, 0)
        cavebot_shell.setSpacing(8)
        cavebot_shell.addWidget(cavebot_status_box)
        cavebot_shell.addWidget(splitter, 1)
        cavebot_layout.addLayout(cavebot_shell)
        self.screens.addWidget(cavebot_screen)

        # Autos screen (Fish, Ring, Hunger)
        autos_screen = QWidget()
        autos_layout = QVBoxLayout(autos_screen)
        autos_layout.setContentsMargins(10, 10, 10, 10)
        autos_layout.setSpacing(10)

        autos_header = QLabel("Autos")
        autos_header.setObjectName("header")
        autos_layout.addWidget(autos_header)

        # Auto Fish
        fish_box = QGroupBox("Auto Fish")
        fish_form = QFormLayout(fish_box)
        fish_form.setContentsMargins(10, 10, 10, 10)

        self.chk_fish_enabled = QCheckBox("Habilitar auto fish")
        self.input_fish_key = QLineEdit("F10")
        self.spin_fish_interval = QSpinBox()
        self.spin_fish_interval.setRange(1000, 30000)
        self.spin_fish_interval.setValue(2000)
        self.spin_fish_interval.setSuffix(" ms")

        fish_form.addRow("Estado", self.chk_fish_enabled)
        fish_form.addRow("Tecla fish", self.input_fish_key)
        fish_form.addRow("Intervalo", self.spin_fish_interval)
        autos_layout.addWidget(fish_box)

        # Auto Ring
        ring_box = QGroupBox("Auto Ring")
        ring_form = QFormLayout(ring_box)
        ring_form.setContentsMargins(10, 10, 10, 10)

        self.chk_ring_enabled = QCheckBox("Habilitar auto ring")
        self.input_ring_key = QLineEdit("F11")
        self.spin_ring_interval = QSpinBox()
        self.spin_ring_interval.setRange(1000, 60000)
        self.spin_ring_interval.setValue(5000)
        self.spin_ring_interval.setSuffix(" ms")
        self.combo_ring_type = QComboBox()
        self.combo_ring_type.addItems([
            "power_ring", "might_ring", "time_ring",
            "stealth_ring", "energy_ring", "ring_of_healing"
        ])

        ring_form.addRow("Estado", self.chk_ring_enabled)
        ring_form.addRow("Tecla equipar", self.input_ring_key)
        ring_form.addRow("Intervalo", self.spin_ring_interval)
        ring_form.addRow("Ring", self.combo_ring_type)
        autos_layout.addWidget(ring_box)

        # Auto Eat (Hunger)
        hunger_box = QGroupBox("Auto Eat (Hunger)")
        hunger_form = QFormLayout(hunger_box)
        hunger_form.setContentsMargins(10, 10, 10, 10)

        self.chk_hunger_enabled = QCheckBox("Habilitar auto eat")
        self.input_eat_key = QLineEdit("F9")
        self.spin_eat_interval = QSpinBox()
        self.spin_eat_interval.setRange(500, 10000)
        self.spin_eat_interval.setValue(1200)
        self.spin_eat_interval.setSuffix(" ms")

        hunger_form.addRow("Estado", self.chk_hunger_enabled)
        hunger_form.addRow("Tecla eat", self.input_eat_key)
        hunger_form.addRow("Intervalo", self.spin_eat_interval)
        autos_layout.addWidget(hunger_box)

        # Auto Supply (Potions)
        supply_box = QGroupBox("Auto Potion (Refill)")
        supply_form = QFormLayout(supply_box)
        supply_form.setContentsMargins(10, 10, 10, 10)

        self.chk_supply_enabled = QCheckBox("Habilitar auto potion")
        self.spin_hp_threshold = QSpinBox()
        self.spin_hp_threshold.setRange(1, 100)
        self.spin_hp_threshold.setValue(50)
        self.spin_hp_threshold.setSuffix(" %")
        self.spin_mp_threshold = QSpinBox()
        self.spin_mp_threshold.setRange(1, 100)
        self.spin_mp_threshold.setValue(30)
        self.spin_mp_threshold.setSuffix(" %")
        self.input_health_key = QLineEdit("F1")
        self.input_mana_key = QLineEdit("F2")
        self.spin_drink_interval = QSpinBox()
        self.spin_drink_interval.setRange(500, 10000)
        self.spin_drink_interval.setValue(1000)
        self.spin_drink_interval.setSuffix(" ms")

        supply_form.addRow("Estado", self.chk_supply_enabled)
        supply_form.addRow("HP umbral", self.spin_hp_threshold)
        supply_form.addRow("MP umbral", self.spin_mp_threshold)
        supply_form.addRow("Tecla HP", self.input_health_key)
        supply_form.addRow("Tecla MP", self.input_mana_key)
        supply_form.addRow("Intervalo", self.spin_drink_interval)
        autos_layout.addWidget(supply_box)

        autos_layout.addStretch(1)

        self.btn_autos_back = QPushButton("Volver")
        autos_layout.addWidget(self.btn_autos_back)
        self.screens.addWidget(autos_screen)

        self.btn_autos_back.clicked.connect(lambda: self.screens.setCurrentIndex(0))

        # Generic settings screen
        settings_screen = QWidget()
        settings_layout = QVBoxLayout(settings_screen)
        settings_layout.setContentsMargins(10, 10, 10, 10)
        settings_layout.setSpacing(10)

        settings_header = QLabel("Configuración General")
        settings_header.setObjectName("header")
        settings_layout.addWidget(settings_header)

        settings_box = QGroupBox("Parámetros")
        settings_form = QFormLayout(settings_box)
        settings_form.setContentsMargins(10, 10, 10, 10)

        self.input_config_path = QLineEdit("config/rois_prod_full.json")
        self.spin_tick_hz = QSpinBox()
        self.spin_tick_hz.setRange(1, 120)
        self.spin_tick_hz.setValue(20)
        self.spin_route_localize_min_score = QDoubleSpinBox()
        self.spin_route_localize_min_score.setRange(0.0, 1.0)
        self.spin_route_localize_min_score.setDecimals(2)
        self.spin_route_localize_min_score.setSingleStep(0.01)
        self.spin_route_localize_min_score.setValue(self._route_localize_min_score())
        self.chk_try_focus = QCheckBox("Intentar enfocar ventana")
        self.chk_try_focus.setChecked(True)

        settings_form.addRow("Config path", self.input_config_path)
        settings_form.addRow("Tick Hz", self.spin_tick_hz)
        settings_form.addRow("World lock min score", self.spin_route_localize_min_score)
        settings_form.addRow("Focus", self.chk_try_focus)
        settings_layout.addWidget(settings_box)

        self.btn_import_env = QPushButton("Importar .env")
        self.btn_export_env = QPushButton("Exportar .env")
        settings_layout.addWidget(self.btn_import_env)
        settings_layout.addWidget(self.btn_export_env)
        self.btn_settings_back = QPushButton("Volver")
        settings_layout.addWidget(self.btn_settings_back)
        settings_layout.addStretch(1)

        self.screens.addWidget(settings_screen)

        self.setStyleSheet(app_stylesheet())

        # Default screen: home
        self.screens.setCurrentIndex(0)

        # Menu shortcuts (optional quality-of-life, no extra UX surfaces)
        act_save = QAction("Save", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self._on_save_clicked)
        self.addAction(act_save)

    def _wire_events(self) -> None:
        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_load.clicked.connect(self._on_load_clicked)
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        self.btn_screen_home.clicked.connect(lambda: self.screens.setCurrentIndex(0))
        self.btn_screen_healing.clicked.connect(lambda: self.screens.setCurrentIndex(1))
        self.btn_screen_cavebot.clicked.connect(lambda: self.screens.setCurrentIndex(2))
        self.btn_screen_autos.clicked.connect(lambda: self.screens.setCurrentIndex(3))
        self.btn_screen_waypoints.clicked.connect(lambda: self.screens.setCurrentIndex(4))
        self.btn_screen_settings.clicked.connect(lambda: self.screens.setCurrentIndex(5))
        self.btn_home_cavebot.clicked.connect(lambda: self.screens.setCurrentIndex(2))
        self.btn_home_healing.clicked.connect(lambda: self.screens.setCurrentIndex(1))
        self.btn_home_waypoints.clicked.connect(lambda: self.screens.setCurrentIndex(2))
        self.btn_home_exit.clicked.connect(self.close)
        self.btn_healing_back.clicked.connect(lambda: self.screens.setCurrentIndex(0))
        self.btn_settings_back.clicked.connect(lambda: self.screens.setCurrentIndex(0))
        self.btn_cavebot_start.clicked.connect(self._on_cavebot_start_clicked)
        self.btn_cavebot_stop.clicked.connect(self._on_cavebot_stop_clicked)
        self.chk_enabled.stateChanged.connect(self._on_script_flags_changed)
        self.chk_run_to_target.stateChanged.connect(self._on_script_flags_changed)
        self.model.dataChanged.connect(lambda *_: self._mark_dirty())
        self.model.rowsInserted.connect(lambda *_: self._mark_dirty())
        self.model.rowsRemoved.connect(lambda *_: self._mark_dirty())

        self.chk_healing_enabled.stateChanged.connect(self._on_aux_controls_changed)
        self.input_heal_key.textChanged.connect(self._on_aux_controls_changed)
        self.spin_heal_hp_threshold.valueChanged.connect(self._on_aux_controls_changed)
        self.input_config_path.textChanged.connect(self._on_aux_controls_changed)
        self.spin_tick_hz.valueChanged.connect(self._on_aux_controls_changed)
        self.spin_route_localize_min_score.valueChanged.connect(self._on_aux_controls_changed)
        self.chk_try_focus.stateChanged.connect(self._on_aux_controls_changed)
        self.btn_import_env.clicked.connect(self._on_import_env_clicked)
        self.btn_export_env.clicked.connect(self._on_export_env_clicked)

        self.btn_route_start.clicked.connect(self._on_route_start_clicked)
        self.btn_route_stop.clicked.connect(self._on_route_stop_clicked)
        self.btn_route_apply.clicked.connect(self._on_route_apply_clicked)
        self.btn_route_reset.clicked.connect(self._on_route_reset_clicked)
        self.btn_route_export.clicked.connect(self._on_route_export_clicked)
        self.btn_route_save_recording.clicked.connect(self._on_route_save_recording_clicked)
        self.btn_route_open_folder.clicked.connect(self._on_route_open_folder_clicked)
        self.shortcut_route_start.activated.connect(self._on_route_start_clicked)
        self.shortcut_route_stop.activated.connect(self._on_route_stop_clicked)
        self.btn_route_mark_rope.clicked.connect(lambda: self._on_route_mark_action("rope"))
        self.btn_route_mark_shovel.clicked.connect(lambda: self._on_route_mark_action("shovel"))
        self.btn_route_mark_pick.clicked.connect(lambda: self._on_route_mark_action("pick"))
        self.btn_route_mark_up.clicked.connect(lambda: self._on_route_mark_action("stairs_up"))
        self.btn_route_mark_down.clicked.connect(lambda: self._on_route_mark_action("stairs_down"))
        self.shortcut_mark_rope.activated.connect(lambda: self._on_route_mark_action("rope"))
        self.shortcut_mark_shovel.activated.connect(lambda: self._on_route_mark_action("shovel"))
        self.shortcut_mark_pick.activated.connect(lambda: self._on_route_mark_action("pick"))
        self.shortcut_mark_up.activated.connect(lambda: self._on_route_mark_action("stairs_up"))
        self.shortcut_mark_down.activated.connect(lambda: self._on_route_mark_action("stairs_down"))
        self.shortcut_move_w.activated.connect(lambda: self._on_route_move_key("w"))
        self.shortcut_move_a.activated.connect(lambda: self._on_route_move_key("a"))
        self.shortcut_move_s.activated.connect(lambda: self._on_route_move_key("s"))
        self.shortcut_move_d.activated.connect(lambda: self._on_route_move_key("d"))
        self.shortcut_move_up.activated.connect(lambda: self._on_route_move_key("up"))
        self.shortcut_move_left.activated.connect(lambda: self._on_route_move_key("left"))
        self.shortcut_move_down.activated.connect(lambda: self._on_route_move_key("down"))
        self.shortcut_move_right.activated.connect(lambda: self._on_route_move_key("right"))
        self._route_timer.timeout.connect(self._on_route_tick)
        self._cavebot_timer.timeout.connect(self._on_cavebot_tick)

        for btn in self.action_buttons.values():
            btn.clicked.connect(self._on_action_button_clicked)

    def _new_cavebot_runtime_context(self) -> RuntimeContext:
        base_ctx = self._new_route_runtime_context()
        cfg = replace(
            base_ctx.config,
            enable_cavebot=True,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=False,
        )
        return RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

    def _waypoints_for_cavebot_from_script(self, script: Script) -> list[dict[str, object]]:
        default_radius_px = 2
        default_max_ticks = 30
        anchor_x = 64
        anchor_y = 38
        anchor_z = 7
        try:
            default_radius_px = max(0, int(str(os.environ.get("FRBOT_CAVEBOT_RADIUS_PX", "2") or "2"), 10))
        except Exception:
            default_radius_px = 2
        try:
            default_max_ticks = max(1, int(str(os.environ.get("FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT", "30") or "30"), 10))
        except Exception:
            default_max_ticks = 30

        explicit_anchor_x = os.environ.get("FRBOT_CAVEBOT_ANCHOR_X")
        explicit_anchor_y = os.environ.get("FRBOT_CAVEBOT_ANCHOR_Y")
        explicit_anchor_z = os.environ.get("FRBOT_CAVEBOT_ANCHOR_Z")

        try:
            if explicit_anchor_x is not None:
                anchor_x = int(str(explicit_anchor_x or "").strip(), 10)
        except Exception:
            anchor_x = 64
        try:
            if explicit_anchor_y is not None:
                anchor_y = int(str(explicit_anchor_y or "").strip(), 10)
        except Exception:
            anchor_y = 38
        try:
            if explicit_anchor_z is not None:
                anchor_z = int(str(explicit_anchor_z or "").strip(), 10)
        except Exception:
            anchor_z = 7

        if explicit_anchor_x is None or explicit_anchor_y is None:
            try:
                profile = str(os.environ.get("FRBOT_PROFILE", "prod_emergency") or "prod_emergency").strip().lower()
                requested_cfg = str(self.input_config_path.text() or os.environ.get("FRBOT_CONFIG_PATH", "") or "").strip()
                resolved_cfg = Path(self._resolve_valid_roi_config_path(requested_cfg, profile=profile))
                data = json.loads(resolved_cfg.read_text(encoding="utf-8", errors="replace") or "{}")
                rois = data.get("rois") if isinstance(data, dict) else None
                minimap = rois.get("minimap") if isinstance(rois, dict) else None
                if isinstance(minimap, dict):
                    w = int(str(minimap.get("width", "0") or "0"), 10)
                    h = int(str(minimap.get("height", "0") or "0"), 10)
                    if explicit_anchor_x is None and w > 0:
                        anchor_x = max(0, int(w // 2))
                    if explicit_anchor_y is None and h > 0:
                        anchor_y = max(0, int(h // 2))
            except Exception:
                pass

        params = ScriptLoaderParams(
            anchor_x=int(anchor_x),
            anchor_y=int(anchor_y),
            anchor_z=int(anchor_z),
            default_radius_px=int(default_radius_px),
            default_max_ticks=int(default_max_ticks),
        )
        runtime_waypoints = script_to_runtime_waypoints(script, params)

        route: list[dict[str, object]] = []
        for idx, rwp in enumerate(runtime_waypoints):
            route.append(
                {
                    "waypoint_id": f"ui_wp_{int(idx)}",
                    "x": int(rwp.x),
                    "y": int(rwp.y),
                    "z": int(rwp.z),
                    "radius_px": int(rwp.radius_px),
                    "max_ticks": int(rwp.max_ticks),
                    "waypoint_type": str(rwp.waypoint_type),
                    "options": dict(rwp.options),
                }
            )
        return route

    def _script_waypoints_for_cavebot(self) -> list[dict[str, object]]:
        return self._waypoints_for_cavebot_from_script(self._script)

    def _apply_cavebot_route_env(self) -> None:
        self._cavebot_prev_waypoints_env = os.environ.get("FRBOT_CAVEBOT_WAYPOINTS")
        self._cavebot_prev_waypoints_file_env = os.environ.get("FRBOT_CAVEBOT_WAYPOINTS_FILE")
        self._cavebot_prev_auto_route_env = os.environ.get("FRBOT_CAVEBOT_AUTO_ROUTE")
        self._cavebot_prev_waypoint_space_env = os.environ.get("FRBOT_CAVEBOT_WAYPOINT_SPACE")

        route = self._script_waypoints_for_cavebot()
        if route:
            def _coord_space_for_item(item: dict[str, object]) -> str:
                options = item.get("options")
                if not isinstance(options, dict):
                    return ""
                return str(options.get("coord_space", "") or "").strip().lower()

            has_world = any(
                _coord_space_for_item(item) == "world"
                for item in route
            )
            if has_world:
                os.environ["FRBOT_CAVEBOT_WAYPOINT_SPACE"] = "world"
            else:
                os.environ.pop("FRBOT_CAVEBOT_WAYPOINT_SPACE", None)

            payload = json.dumps(route, ensure_ascii=False, separators=(",", ":"))
            # Windows CreateProcess/env block can fail for very large vars.
            # Keep margin below 32767 to avoid runtime startup errors.
            if len(payload) >= 30000:
                out_dir = Path.cwd() / "diagnostics"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / "cavebot_ui_waypoints.json"
                out_path.write_text(payload, encoding="utf-8")
                os.environ["FRBOT_CAVEBOT_WAYPOINTS_FILE"] = str(out_path)
                os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS", None)
            else:
                os.environ["FRBOT_CAVEBOT_WAYPOINTS"] = payload
                os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS_FILE", None)
            os.environ["FRBOT_CAVEBOT_AUTO_ROUTE"] = "0"
            return

        os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS", None)
        os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS_FILE", None)
        os.environ["FRBOT_CAVEBOT_AUTO_ROUTE"] = "1"

    def _force_obs_projector_capture_env(self) -> None:
        os.environ["FRBOT_CAPTURE_SOURCE"] = "obs"
        proj_title = str(os.environ.get("FRBOT_OBS_PROJECTOR_TITLE", "") or "").strip()
        if proj_title:
            return
        obs_source = str(os.environ.get("FRBOT_OBS_SOURCE_NAME", "Tibia_Fuente") or "Tibia_Fuente").strip() or "Tibia_Fuente"
        os.environ["FRBOT_OBS_PROJECTOR_TITLE"] = f"Proyector en ventana (Fuente) - {obs_source}"

    def _minimize_for_capture(self) -> None:
        try:
            self.showMinimized()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:
            return

    def _restore_cavebot_route_env(self) -> None:
        if self._cavebot_prev_waypoints_env is None:
            os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS", None)
        else:
            os.environ["FRBOT_CAVEBOT_WAYPOINTS"] = str(self._cavebot_prev_waypoints_env)

        if self._cavebot_prev_waypoints_file_env is None:
            os.environ.pop("FRBOT_CAVEBOT_WAYPOINTS_FILE", None)
        else:
            os.environ["FRBOT_CAVEBOT_WAYPOINTS_FILE"] = str(self._cavebot_prev_waypoints_file_env)

        if self._cavebot_prev_auto_route_env is None:
            os.environ.pop("FRBOT_CAVEBOT_AUTO_ROUTE", None)
        else:
            os.environ["FRBOT_CAVEBOT_AUTO_ROUTE"] = str(self._cavebot_prev_auto_route_env)

        if self._cavebot_prev_waypoint_space_env is None:
            os.environ.pop("FRBOT_CAVEBOT_WAYPOINT_SPACE", None)
        else:
            os.environ["FRBOT_CAVEBOT_WAYPOINT_SPACE"] = str(self._cavebot_prev_waypoint_space_env)

    def _route_try_localize_world(
        self,
        *,
        frame: Any,
        tile: tuple[int, int, int],
    ) -> tuple[int, int, int] | None:
        try:
            if self._route_marker_cfg is None:
                self._set_route_world_lock_status(False)
                return None
            raw_dir = str(os.environ.get("FRBOT_TIBIA_MAP_DATA_DIR", "") or "").strip()
            if not raw_dir:
                self._set_route_world_lock_status(False)
                return None
            if self._route_tibia_dataset is None:
                self._route_tibia_dataset = load_tibia_map_dataset()

            det = detect_minimap_marker(frame, self._route_marker_cfg)
            if det is None:
                self._set_route_world_lock_status(False)
                return None

            min_score = self._route_localize_min_score()

            prev_world = self._route_last_world
            if prev_world is not None and int(prev_world[2]) != int(tile[2]):
                prev_world = None

            loc = localize_minimap(
                minimap_rgb=bytes(getattr(frame, "minimap_rgb", b"")),
                minimap_width=int(getattr(frame, "minimap_width", 0)),
                minimap_height=int(getattr(frame, "minimap_height", 0)),
                floor_z=int(tile[2]),
                dataset=self._route_tibia_dataset,
                marker_px=(int(round(float(det.pos.px))), int(round(float(det.pos.py)))),
                marker_rgb=(
                    int(self._route_marker_cfg.rgb[0]),
                    int(self._route_marker_cfg.rgb[1]),
                    int(self._route_marker_cfg.rgb[2]),
                ),
                marker_tol=int(self._route_marker_cfg.tol),
                prev_player_world=(int(prev_world[0]), int(prev_world[1])) if prev_world is not None else None,
            )

            if bool(loc.ambiguous):
                self._set_route_world_lock_status(False, score=float(loc.score))
                return None
            if float(loc.score) < float(min_score):
                self._set_route_world_lock_status(False, score=float(loc.score))
                return None

            out = (int(loc.player_x), int(loc.player_y), int(loc.player_z))
            self._route_last_world = out
            self._set_route_world_lock_status(True, score=float(loc.score))
            return out
        except Exception:
            self._set_route_world_lock_status(False)
            return None

    def _annotate_script_with_world_coords(self, script: Script) -> Script:
        mapping = dict(self._route_world_by_rel)
        if not mapping:
            return script

        used = 0
        for wp in script.waypoints:
            rel = (int(getattr(wp, "x", 0)), int(getattr(wp, "y", 0)), int(getattr(wp, "z", 0)))
            world = mapping.get(rel)
            if world is None:
                continue
            opts = dict(getattr(wp, "options", {}) or {})
            opts["coord_space"] = "world"
            opts["world_x"] = int(world[0])
            opts["world_y"] = int(world[1])
            opts["world_z"] = int(world[2])
            wp.options = opts
            used += 1

        md = dict(getattr(script, "metadata", {}) or {})
        rec = dict(md.get("recorder", {}) or {})
        rec["coord_space"] = "world"
        rec["world_waypoints"] = int(used)
        rec["world_waypoints_total"] = int(len(script.waypoints))
        md["recorder"] = rec
        script.metadata = md
        return script

    def _build_route_script_for_output(self) -> Script:
        if self._route_session is None:
            raise RuntimeError("route_session_missing")
        script = self._route_session.build_script()
        return self._annotate_script_with_world_coords(script)

    def _terminate_cavebot_process(self, proc: object) -> None:
        try:
            if getattr(proc, "poll")() is not None:
                return
        except Exception:
            return

        pid = 0
        try:
            pid = int(getattr(proc, "pid", 0) or 0)
        except Exception:
            pid = 0

        if sys.platform == "win32" and int(pid) > 0:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass

        try:
            if getattr(proc, "poll")() is None:
                getattr(proc, "terminate")()
                try:
                    getattr(proc, "wait")(timeout=3)
                except Exception:
                    getattr(proc, "kill")()
        except Exception:
            pass

    def _stop_cavebot_runtime(self, *, status_text: str) -> None:
        self._cavebot_timer.stop()
        proc = self._cavebot_proc
        if proc is not None:
            self._terminate_cavebot_process(proc)
        self._cavebot_running = False
        self._cavebot_proc = None
        self._restore_cavebot_route_env()

        self.btn_cavebot_start.setEnabled(True)
        self.btn_cavebot_stop.setEnabled(False)
        self.lbl_cavebot_status.setText(f"Estado: {status_text}")
        self.lbl_cavebot_runtime.setText("Tick: 0 | WP: -")

    def _read_last_fatal_reason(self) -> str:
        try:
            p = Path.cwd() / "diagnostics" / "fatal.log"
            if not p.exists():
                return "unknown"
            payload = json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
            reason = str(payload.get("reason") or "unknown").strip() or "unknown"
            return reason
        except Exception:
            return "unknown"

    def _candidate_cavebot_trace_paths(self) -> list[Path]:
        candidates: list[Path] = []
        raw_frames_dir = str(os.environ.get("FRBOT_REAL_FRAMES_DIR", "") or "").strip()
        if raw_frames_dir:
            candidates.append(Path(raw_frames_dir) / "cavebot_trace.jsonl")
        cwd = Path.cwd()
        candidates.extend(
            [
                cwd / "diagnostics" / "frames" / "cavebot_trace.jsonl",
                cwd / "diagnostics" / "frames_emergency" / "cavebot_trace.jsonl",
                cwd / "diagnostics" / "frames_full" / "cavebot_trace.jsonl",
            ]
        )

        existing: list[Path] = []
        seen: set[str] = set()
        for p in candidates:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            if p.exists() and p.is_file():
                existing.append(p)

        existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return existing

    def _read_latest_cavebot_trace_event(self) -> dict[str, Any] | None:
        for path in self._candidate_cavebot_trace_paths():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for raw in reversed(lines):
                row = str(raw or "").strip()
                if not row:
                    continue
                try:
                    payload = json.loads(row)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("event", "") or "").strip() == "":
                    continue
                return payload
        return None

    def _format_cavebot_runtime_from_trace(self, payload: dict[str, Any]) -> str:
        tick = payload.get("tick_index", "?")
        wp = "-"
        waypoint = payload.get("waypoint")
        if isinstance(waypoint, dict):
            wp = str(waypoint.get("waypoint_id", "-") or "-")
        blocked = str(payload.get("blocked_reason", "none") or "none")
        roi_sanity = str(payload.get("roi_sanity_reason", "") or "").strip()
        pnf = bool(payload.get("pnf", False))
        keys_raw = payload.get("last_keys_sent")
        keys = "-"
        if isinstance(keys_raw, list):
            keys = ",".join([str(k) for k in keys_raw if str(k or "").strip()]) or "-"
        elif str(payload.get("key", "") or "").strip():
            keys = str(payload.get("key", "") or "").strip()

        try:
            dist_after = float(payload.get("distance_after_px", 0.0) or 0.0)
        except Exception:
            dist_after = 0.0
        try:
            angle = float(payload.get("angle_deg", 0.0) or 0.0)
        except Exception:
            angle = 0.0

        roi_info = ""
        if blocked == "roi_invalid" and roi_sanity:
            roi_info = f" | roi:{roi_sanity}"

        return (
            f"Tick: {tick} | WP: {wp} | block:{blocked}{roi_info} | pnf:{1 if pnf else 0} "
            f"| key:{keys} | d:{dist_after:.1f} | a:{angle:.0f}"
        )

    def _build_cavebot_process_env(self) -> dict[str, str]:
        env = dict(os.environ)
        profile = str(env.get("FRBOT_PROFILE", "prod_emergency") or "prod_emergency").strip().lower()
        env["FRBOT_PROFILE"] = "prod_full" if profile == "prod_full" else "prod_emergency"
        env["FRBOT_MODE"] = "cavebot_full" if env["FRBOT_PROFILE"] == "prod_full" else "cavebot"
        env["FRBOT_CAPTURE_SOURCE"] = "obs_source"
        obs_src = str(env.get("FRBOT_OBS_SOURCE_NAME", "Tibia_Fuente") or "Tibia_Fuente").strip() or "Tibia_Fuente"
        env["FRBOT_OBS_SOURCE_NAME"] = obs_src
        default_cfg = "config/rois_prod_full.json" if env["FRBOT_PROFILE"] == "prod_full" else "rois_prod_emergency.json"
        requested_cfg = str(self.input_config_path.text() or env.get("FRBOT_CONFIG_PATH", default_cfg) or default_cfg).strip() or default_cfg
        resolved_cfg = self._resolve_valid_roi_config_path(requested_cfg, profile=env["FRBOT_PROFILE"])
        env["FRBOT_CONFIG_PATH"] = str(resolved_cfg)
        env["FRBOT_TRY_FOCUS"] = "1" if bool(self.chk_try_focus.isChecked()) else str(env.get("FRBOT_TRY_FOCUS", "1") or "1")
        env.setdefault("FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG", "130")
        env.setdefault("FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK", "6")
        env.setdefault("FRBOT_CAVEBOT_MARKER_AREA_RATIO_MAX", "0.80")
        env.setdefault("FRBOT_CAVEBOT_STUCK_WINDOW", "12")

        marker_rgb = str(env.get("FRBOT_PLAYER_MARKER_RGB", "") or "").strip()
        marker_tol = str(env.get("FRBOT_PLAYER_MARKER_TOL", "") or "").strip()
        if marker_rgb in {"", "0,200,0"}:
            env["FRBOT_PLAYER_MARKER_RGB"] = "255,0,255"
        if marker_tol in {"", "45"}:
            env["FRBOT_PLAYER_MARKER_TOL"] = "30"

        # Guard against invalid HWND inherited from shell profile/history.
        raw_hwnd = str(env.get("FRBOT_WINDOW_HWND", "") or "").strip()
        raw_title = str(env.get("FRBOT_WINDOW_TITLE", "") or "").strip()

        def _is_hwnd_placeholder(s: str) -> bool:
            ss = str(s or "").strip().lower()
            if not ss:
                return False
            if ss.startswith("0x") and len(ss) > 2 and set(ss[2:]) == {"x"}:
                return True
            return ss in {"0xyourhwnd", "yourhwnd", "0x<yourhwnd>"}

        hwnd_ok = False
        if raw_hwnd and not _is_hwnd_placeholder(raw_hwnd):
            try:
                hwnd_ok = int(raw_hwnd, 0) >= 0
            except Exception:
                hwnd_ok = False

        if not hwnd_ok:
            env.pop("FRBOT_WINDOW_HWND", None)

        if raw_title:
            env["FRBOT_WINDOW_TITLE"] = raw_title
        elif not hwnd_ok:
            # Startup guards require one selector; default to Tibia title substring.
            env["FRBOT_WINDOW_TITLE"] = "Tibia"

        return {str(k): str(v) for k, v in env.items()}

    def _roi_config_schema_ok(self, path: Path, *, profile: str) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        rois = data.get("rois")
        if not isinstance(rois, dict):
            return False
        if not _REQUIRED_REAL_ROIS.issubset(set(rois.keys())):
            return False
        for name, roi in rois.items():
            if not isinstance(name, str) or not isinstance(roi, dict):
                return False
            vx = roi.get("x")
            vy = roi.get("y")
            vw = roi.get("width")
            vh = roi.get("height")
            if vx is None or vy is None or vw is None or vh is None:
                return False
            try:
                x = int(str(vx))
                y = int(str(vy))
                w = int(str(vw))
                h = int(str(vh))
            except Exception:
                return False
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                return False

        keys = set(rois.keys())
        extra = keys - set(_REQUIRED_REAL_ROIS)
        if profile == "prod_full":
            if not extra.issubset(set(_ALLOWED_PROD_FULL_EXTRA_ROIS)):
                return False
        else:
            if not extra.issubset(set(_ALLOWED_PROD_EMERGENCY_EXTRA_ROIS)):
                return False
        return True

    def _resolve_valid_roi_config_path(self, requested: str, *, profile: str) -> str:
        profile_key = "prod_full" if str(profile or "").strip().lower() == "prod_full" else "prod_emergency"
        if profile_key == "prod_full":
            defaults = ["config/rois_prod_full.json", "rois_prod_full.json", "rois_prod_emergency.json"]
        else:
            defaults = ["rois_prod_emergency.json", "config/rois_prod_full.json", "rois_prod_full.json"]
        candidates = [str(requested or "").strip(), *defaults]
        seen: set[str] = set()
        for raw in candidates:
            p_raw = str(raw or "").strip()
            if not p_raw or p_raw in seen:
                continue
            seen.add(p_raw)
            p = Path(p_raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists() or not p.is_file():
                continue
            if self._roi_config_schema_ok(p, profile=profile_key):
                return str(p)
        return str(requested or ("config/rois_prod_full.json" if profile_key == "prod_full" else "rois_prod_emergency.json"))

    def _on_cavebot_start_clicked(self) -> None:
        if bool(self._cavebot_running):
            return
        try:
            log_json(_LOG, event="ui_action", gate="ui", action="cavebot_start", phase="begin")
            self._minimize_for_capture()
            self._apply_runtime_env_from_ui()
            self._force_obs_projector_capture_env()
            self._apply_cavebot_route_env()

            env = self._build_cavebot_process_env()
            python_exec = str(sys.executable or "").strip() or "python"
            self._cavebot_proc = subprocess.Popen(
                [python_exec, "main.py"],
                cwd=str(Path.cwd()),
                env=env,
            )
            self._cavebot_running = True

            self._cavebot_timer.setInterval(350)
            self._cavebot_timer.start()

            self.btn_cavebot_start.setEnabled(False)
            self.btn_cavebot_stop.setEnabled(True)
            self.lbl_cavebot_status.setText("Estado: running")
            self.lbl_cavebot_runtime.setText("Tick: process | WP: external")
            log_json(_LOG, event="ui_action", gate="ui", action="cavebot_start", phase="success")
        except Exception as exc:
            _LOG.exception("Failed to start cavebot: %s", exc)
            log_json(_LOG, event="ui_action", gate="ui", action="cavebot_start", phase="failed", reason=str(exc))
            write_fatal("cavebot_ui_start_failed", exc, details={"reason": str(exc)})
            self._stop_cavebot_runtime(status_text=f"error ({str(exc)})")
            self._show_error("Cavebot", f"No se pudo iniciar Cavebot.\n\n{exc}")

    def _on_cavebot_stop_clicked(self) -> None:
        self._stop_cavebot_runtime(status_text="detenido")

    def _on_cavebot_tick(self) -> None:
        if not bool(self._cavebot_running):
            return
        proc = self._cavebot_proc
        if proc is None:
            self._stop_cavebot_runtime(status_text="error (proceso no inicializado)")
            return
        try:
            code = proc.poll()
            if code is None:
                self.lbl_cavebot_status.setText("Estado: running")
                trace_payload = self._read_latest_cavebot_trace_event()
                if trace_payload is not None:
                    self.lbl_cavebot_runtime.setText(self._format_cavebot_runtime_from_trace(trace_payload))
                return

            self._cavebot_last_exit = int(code)
            if int(code) == 0:
                self._stop_cavebot_runtime(status_text="detenido (ok)")
                return

            fatal_reason = self._read_last_fatal_reason()
            self._stop_cavebot_runtime(status_text=f"error ({fatal_reason})")
        except Exception as exc:
            _LOG.exception("Cavebot process polling failed: %s", exc)
            write_fatal("cavebot_ui_tick_failed", exc, details={"reason": str(exc)})
            self._stop_cavebot_runtime(status_text=f"error ({str(exc)})")

    def _collect_healing_config(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.chk_healing_enabled.isChecked()),
            "heal_key": str(self.input_heal_key.text() or "F1").strip() or "F1",
            "hp_threshold_percent": int(self.spin_heal_hp_threshold.value()),
        }

    def _collect_general_config(self) -> dict[str, Any]:
        return {
            "config_path": str(self.input_config_path.text() or "config/rois_prod_full.json").strip() or "config/rois_prod_full.json",
            "tick_hz": int(self.spin_tick_hz.value()),
            "route_localize_min_score": max(0.0, min(1.0, float(self.spin_route_localize_min_score.value()))),
            "try_focus": bool(self.chk_try_focus.isChecked()),
        }

    def _sync_aux_controls_from_metadata(self) -> None:
        md = dict(self._script.metadata or {})
        healing_md = dict(md.get("healing", {}) or {})
        general_md = dict(md.get("settings", {}) or {})

        self.chk_healing_enabled.blockSignals(True)
        self.input_heal_key.blockSignals(True)
        self.spin_heal_hp_threshold.blockSignals(True)
        self.input_config_path.blockSignals(True)
        self.spin_tick_hz.blockSignals(True)
        self.spin_route_localize_min_score.blockSignals(True)
        self.chk_try_focus.blockSignals(True)
        try:
            self.chk_healing_enabled.setChecked(bool(healing_md.get("enabled", True)))
            self.input_heal_key.setText(str(healing_md.get("heal_key", "F1") or "F1"))
            hp_pct = int(healing_md.get("hp_threshold_percent", 50) or 50)
            self.spin_heal_hp_threshold.setValue(max(1, min(100, int(hp_pct))))

            self.input_config_path.setText(str(general_md.get("config_path", "config/rois_prod_full.json") or "config/rois_prod_full.json"))
            tick_hz = int(general_md.get("tick_hz", 20) or 20)
            self.spin_tick_hz.setValue(max(1, min(120, int(tick_hz))))
            try:
                localize_min_score = float(general_md.get("route_localize_min_score", self._route_localize_min_score()) or self._route_localize_min_score())
            except Exception:
                localize_min_score = self._route_localize_min_score()
            self.spin_route_localize_min_score.setValue(max(0.0, min(1.0, float(localize_min_score))))
            self.chk_try_focus.setChecked(bool(general_md.get("try_focus", True)))
        finally:
            self.chk_healing_enabled.blockSignals(False)
            self.input_heal_key.blockSignals(False)
            self.spin_heal_hp_threshold.blockSignals(False)
            self.input_config_path.blockSignals(False)
            self.spin_tick_hz.blockSignals(False)
            self.spin_route_localize_min_score.blockSignals(False)
            self.chk_try_focus.blockSignals(False)

    def _write_aux_controls_to_metadata(self) -> None:
        md = dict(self._script.metadata or {})
        md["healing"] = self._collect_healing_config()
        md["settings"] = self._collect_general_config()
        self._script.metadata = md

    def _apply_runtime_env_from_ui(self) -> None:
        env_values = self._runtime_env_values_from_ui()
        for k, v in env_values.items():
            os.environ[str(k)] = str(v)

    def _runtime_env_values_from_ui(self) -> dict[str, str]:
        healing = self._collect_healing_config()
        settings = self._collect_general_config()
        hp_ratio = max(0.01, min(1.0, float(int(healing["hp_threshold_percent"])) / 100.0))

        return {
            "FRBOT_ENABLE_HEALING": "1" if bool(healing["enabled"]) else "0",
            "FRBOT_HEAL_KEY": str(healing["heal_key"]),
            "FRBOT_HEAL_HP_THRESHOLD": f"{hp_ratio:.2f}",
            "FRBOT_CONFIG_PATH": str(settings["config_path"]),
            "FRBOT_TICK_HZ": str(int(settings["tick_hz"])),
            "FRBOT_ROUTE_LOCALIZE_MIN_SCORE": f"{float(settings['route_localize_min_score']):.2f}",
            "FRBOT_TRY_FOCUS": "1" if bool(settings["try_focus"]) else "0",
            "FRBOT_PLAYER_MARKER_RGB": str(os.environ.get("FRBOT_PLAYER_MARKER_RGB", _DEFAULT_PLAYER_MARKER_RGB) or _DEFAULT_PLAYER_MARKER_RGB),
            "FRBOT_PLAYER_MARKER_TOL": str(os.environ.get("FRBOT_PLAYER_MARKER_TOL", _DEFAULT_PLAYER_MARKER_TOL) or _DEFAULT_PLAYER_MARKER_TOL),
        }

    def _on_aux_controls_changed(self, *_: Any) -> None:
        try:
            self._write_aux_controls_to_metadata()
            self._apply_runtime_env_from_ui()
            self._update_route_world_lock_tooltip()
            self._mark_dirty()
        except Exception as exc:
            _LOG.exception("Failed to sync auxiliary UI controls: %s", exc)
            self._show_error("Error", f"Failed to apply configuration changes.\n\n{exc}")

    def _on_export_env_clicked(self) -> None:
        try:
            self._write_aux_controls_to_metadata()
            env_values = self._runtime_env_values_from_ui()
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Exportar archivo .env",
                str(Path.cwd() / ".env"),
                "Env Files (*.env);;All Files (*)",
            )
            if not path:
                return

            lines = [f"{k}={v}" for k, v in env_values.items()]
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            _LOG.info("Exported .env to %s", path)
            _QMessageBox.information(
                self,
                "Exportación completada",
                f"Archivo exportado: {path}\nVariables exportadas: {len(env_values)}",
            )
        except Exception as exc:
            _LOG.exception("Failed to export .env: %s", exc)
            self._show_error("Error", f"Failed to export .env.\n\n{exc}")

    def _parse_env_file(self, path: str | Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = str(raw_line).strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = str(k).strip()
            val = str(v).strip()
            if key:
                out[key] = val
        return out

    def _apply_env_values_to_ui(self, env_values: dict[str, str]) -> None:
        def _env_bool(v: str, default: bool) -> bool:
            s = str(v or "").strip().lower()
            if s == "":
                return bool(default)
            return s not in {"0", "false", "no", "off"}

        def _env_int(v: str, default: int, low: int, high: int) -> int:
            try:
                n = int(str(v).strip())
            except Exception:
                n = int(default)
            return max(int(low), min(int(high), int(n)))

        self.chk_healing_enabled.blockSignals(True)
        self.input_heal_key.blockSignals(True)
        self.spin_heal_hp_threshold.blockSignals(True)
        self.input_config_path.blockSignals(True)
        self.spin_tick_hz.blockSignals(True)
        self.spin_route_localize_min_score.blockSignals(True)
        self.chk_try_focus.blockSignals(True)
        try:
            if "FRBOT_ENABLE_HEALING" in env_values:
                self.chk_healing_enabled.setChecked(_env_bool(env_values.get("FRBOT_ENABLE_HEALING", "1"), True))

            if "FRBOT_HEAL_KEY" in env_values:
                key = str(env_values.get("FRBOT_HEAL_KEY", "F1") or "F1").strip() or "F1"
                self.input_heal_key.setText(key)

            if "FRBOT_HEAL_HP_THRESHOLD" in env_values:
                try:
                    ratio = float(str(env_values.get("FRBOT_HEAL_HP_THRESHOLD", "0.50")).strip())
                except Exception:
                    ratio = 0.50
                pct = max(1, min(100, int(round(float(ratio) * 100.0))))
                self.spin_heal_hp_threshold.setValue(int(pct))

            if "FRBOT_CONFIG_PATH" in env_values:
                p = str(env_values.get("FRBOT_CONFIG_PATH", "config/rois_prod_full.json") or "config/rois_prod_full.json").strip()
                self.input_config_path.setText(p or "config/rois_prod_full.json")

            if "FRBOT_TICK_HZ" in env_values:
                self.spin_tick_hz.setValue(_env_int(env_values.get("FRBOT_TICK_HZ", "20"), 20, 1, 120))

            if "FRBOT_ROUTE_LOCALIZE_MIN_SCORE" in env_values:
                try:
                    localize_min_score = float(str(env_values.get("FRBOT_ROUTE_LOCALIZE_MIN_SCORE", "0.55")).strip())
                except Exception:
                    localize_min_score = 0.55
                self.spin_route_localize_min_score.setValue(max(0.0, min(1.0, float(localize_min_score))))

            if "FRBOT_TRY_FOCUS" in env_values:
                self.chk_try_focus.setChecked(_env_bool(env_values.get("FRBOT_TRY_FOCUS", "1"), True))
        finally:
            self.chk_healing_enabled.blockSignals(False)
            self.input_heal_key.blockSignals(False)
            self.spin_heal_hp_threshold.blockSignals(False)
            self.input_config_path.blockSignals(False)
            self.spin_tick_hz.blockSignals(False)
            self.spin_route_localize_min_score.blockSignals(False)
            self.chk_try_focus.blockSignals(False)

    def _on_import_env_clicked(self) -> None:
        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Importar archivo .env",
                str(Path.cwd()),
                "Env Files (*.env);;All Files (*)",
            )
            if not path:
                return

            env_values = self._parse_env_file(path)
            self._apply_env_values_to_ui(env_values)
            self._write_aux_controls_to_metadata()
            self._apply_runtime_env_from_ui()
            self._mark_dirty()
            _LOG.info("Imported .env from %s", path)

            supported_keys = [
                "FRBOT_ENABLE_HEALING",
                "FRBOT_HEAL_KEY",
                "FRBOT_HEAL_HP_THRESHOLD",
                "FRBOT_CONFIG_PATH",
                "FRBOT_TICK_HZ",
                "FRBOT_ROUTE_LOCALIZE_MIN_SCORE",
                "FRBOT_TRY_FOCUS",
            ]
            applied = [k for k in supported_keys if k in env_values]
            applied_txt = ", ".join(applied) if applied else "ninguna"
            _QMessageBox.information(
                self,
                "Importación completada",
                f"Archivo importado: {path}\nVariables aplicadas: {len(applied)}\n{applied_txt}",
            )
        except Exception as exc:
            _LOG.exception("Failed to import .env: %s", exc)
            self._show_error("Error", f"Failed to import .env.\n\n{exc}")

    def _route_z(self) -> int:
        try:
            return int(self.spin_z.value())
        except Exception:
            return 7

    def _update_route_counters(self) -> None:
        session = self._route_session
        if session is None:
            self.lbl_route_counts.setText("Steps: 0 | Move: 0 | Rope: 0 | Shovel: 0 | Pick: 0 | Up: 0 | Down: 0")
            return

        waypoints = list(session.waypoints)
        total = int(len(waypoints))
        move = 0
        rope = 0
        shovel = 0
        pick = 0
        up = 0
        down = 0
        for wp in waypoints:
            wp_type = str(getattr(wp, 'type', '') or '').strip().lower()
            action_kind = str((getattr(wp, 'options', {}) or {}).get('action_kind', '')).strip().lower()
            if wp_type == WaypointType.WALK.value:
                move += 1
            elif wp_type == WaypointType.ROPE.value:
                rope += 1
            elif wp_type == WaypointType.USE_RIGHT_CLICK.value:
                if action_kind == 'pick':
                    pick += 1
                else:
                    shovel += 1
            elif wp_type == WaypointType.USE_LADDER.value:
                if action_kind == 'stairs_down':
                    down += 1
                else:
                    up += 1

        self.lbl_route_counts.setText(
            f"Steps: {total} | Move: {move} | Rope: {rope} | Shovel: {shovel} | Pick: {pick} | Up: {up} | Down: {down}"
        )

    def _set_route_controls_enabled(self, *, recording: bool) -> None:
        self.btn_route_start.setEnabled(not bool(recording))
        self.btn_route_stop.setEnabled(self._route_session is not None)
        self.btn_route_apply.setEnabled(self._route_session is not None and not bool(recording))
        self.btn_route_mark_rope.setEnabled(bool(recording))
        self.btn_route_mark_shovel.setEnabled(bool(recording))
        self.btn_route_mark_pick.setEnabled(bool(recording))
        self.btn_route_mark_up.setEnabled(bool(recording))
        self.btn_route_mark_down.setEnabled(bool(recording))
        self.btn_route_export.setEnabled(self._route_session is not None)
        self.btn_route_save_recording.setEnabled(self._route_session is not None)
        self.btn_route_open_folder.setEnabled(self._route_session is not None)

    def _route_localize_min_score(self) -> float:
        ui_spin = getattr(self, "spin_route_localize_min_score", None)
        if ui_spin is not None:
            try:
                return max(0.0, min(1.0, float(ui_spin.value())))
            except Exception:
                pass
        raw = str(os.environ.get("FRBOT_ROUTE_LOCALIZE_MIN_SCORE", "0.55") or "0.55").strip()
        try:
            val = float(raw)
        except Exception:
            val = 0.55
        return max(0.0, min(1.0, float(val)))

    def _update_route_world_lock_tooltip(self) -> None:
        min_score = self._route_localize_min_score()
        self.lbl_route_world_lock.setToolTip(
            f"Localización absoluta minimap→mundo. Umbral ON/OFF: score >= {float(min_score):.2f} (FRBOT_ROUTE_LOCALIZE_MIN_SCORE)."
        )

    def _set_route_world_lock_status(self, enabled: bool, *, score: float | None = None) -> None:
        self._route_world_lock_on = bool(enabled)
        self._update_route_world_lock_tooltip()
        if score is not None:
            self._route_world_lock_last_score = float(score)
        if bool(enabled):
            if score is None:
                self.lbl_route_world_lock.setText("✓ World lock: ON")
            else:
                self.lbl_route_world_lock.setText(f"✓ World lock: ON ({float(score):.2f})")
            self.lbl_route_world_lock.setStyleSheet(f"color: {_TEXT};")
            return
        shown_score = score
        if shown_score is None:
            shown_score = self._route_world_lock_last_score
        if shown_score is None:
            self.lbl_route_world_lock.setText("✗ World lock: OFF")
        else:
            self.lbl_route_world_lock.setText(f"✗ World lock: OFF ({float(shown_score):.2f})")
        self.lbl_route_world_lock.setStyleSheet(f"color: {_ACCENT};")

    def _new_route_runtime_context(self) -> RuntimeContext:
        window_hwnd = 0
        try:
            window_hwnd = int(str(os.environ.get("FRBOT_WINDOW_HWND", "0") or "0"), 0)
        except Exception:
            window_hwnd = 0

        window_title = str(os.environ.get("FRBOT_WINDOW_TITLE", "") or "").strip()

        if not window_title and window_hwnd <= 0:
            window_title = "Tibia"

        if window_hwnd <= 0 and window_title:
            try:
                found = w32.find_window_by_title_substring(window_title)
                if found is not None:
                    window_hwnd = int(found.hwnd)
            except Exception:
                pass

        if not window_title and window_hwnd > 0:
            try:
                window_title = str(w32.get_window_text(int(window_hwnd)) or "").strip()
            except Exception:
                window_title = ""

        cfg = RuntimeConfig(
            mode="real",  # El recorder siempre usa modo real; FRBOT_MODE del entorno no aplica aquí.
            tick_hz=float(self.spin_tick_hz.value()),
            config_path=str(self.input_config_path.text() or "").strip(),
            enable_cavebot=False,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=False,
            minimap_roi=str(os.environ.get("FRBOT_MINIMAP_ROI", "minimap") or "minimap"),
            player_marker_rgb=str(os.environ.get("FRBOT_PLAYER_MARKER_RGB", _DEFAULT_PLAYER_MARKER_RGB) or _DEFAULT_PLAYER_MARKER_RGB),
            player_marker_tol=int(str(os.environ.get("FRBOT_PLAYER_MARKER_TOL", _DEFAULT_PLAYER_MARKER_TOL) or _DEFAULT_PLAYER_MARKER_TOL)),
            player_marker_min_pixels=int(str(os.environ.get("FRBOT_PLAYER_MARKER_MIN_PIXELS", "5") or "5")),
            player_marker_max_pixels=int(str(os.environ.get("FRBOT_PLAYER_MARKER_MAX_PIXELS", "0") or "0")),
            window_hwnd=int(window_hwnd),
            window_title_substring=str(window_title),
        )

        return RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

    def _on_route_start_clicked(self) -> None:
        if bool(self._route_recording_active):
            self.lbl_route_status.setText("Recorder: ya está grabando")
            return
        try:
            log_json(_LOG, event="ui_action", gate="ui", action="route_record_start", phase="begin")
            self._force_obs_projector_capture_env()

            # Validación proactiva de config_path antes de intentar el preflight.
            # Si está vacío o apunta a un archivo inexistente, intentamos fallback
            # automático; si tampoco existe, mostramos error accionable y abortamos.
            _raw_cfg = str(self.input_config_path.text() or "").strip()
            if not _raw_cfg or not Path(_raw_cfg).exists():
                _fallback_cfg = self._resolve_valid_roi_config_path(_raw_cfg, profile="prod_full")
                if Path(_fallback_cfg).exists():
                    self.input_config_path.setText(_fallback_cfg)
                else:
                    _desc = f"'{_raw_cfg}'" if _raw_cfg else "(vacío)"
                    self._show_error(
                        "Route Recorder — config_path inválido",
                        f"config_path {_desc} no existe.\n\n"
                        "Opciones:\n"
                        "  • Selecciona un archivo JSON de ROIs válido (p.ej. config/rois_prod_full.json).\n"
                        "  • Usa el botón de configuración para elegir la ruta.",
                    )
                    return

            ctx, capture = self._route_capture_with_obs_fallback()

            marker_cfg = marker_config_from_env(
                str(ctx.config.player_marker_rgb),
                str(ctx.config.player_marker_tol),
                str(ctx.config.player_marker_min_pixels),
                str(ctx.config.player_marker_max_pixels),
                str(ctx.config.player_marker_min_fill_ratio),
                str(ctx.config.player_marker_max_aspect_ratio),
            )

            self._route_capture = capture
            self._route_binding = None
            self._route_sampler = MinimapRouteSampler(
                marker_cfg=marker_cfg,
                pixels_per_tile=float(ctx.config.pixels_per_tile),
                z=int(self._route_z()),
            )
            self._route_marker_cfg = marker_cfg
            self._route_tibia_dataset = None
            self._route_world_by_rel = {}
            self._route_last_world = None
            self._route_world_lock_last_score = None
            self._set_route_world_lock_status(False)
            self._route_session = RouteRecordingSession(
                script_name=f"recorded_route_{int(time.time())}",
                default_z=int(self._route_z()),
                simplify_straight_every=3,
            )
            self._route_last_minimap_digest = ""
            self._route_floor_change_streak = 0
            self._route_last_tile = None
            self.route_steps_list.clear()
            self._route_recording_active = True
            self._route_timer.start()
            self._set_route_controls_enabled(recording=True)
            self.lbl_route_status.setText("Recorder: grabando...")
            self._update_route_counters()
            log_json(_LOG, event="ui_action", gate="ui", action="route_record_start", phase="success")
        except Exception as exc:
            _LOG.exception("Failed to start route recorder: %s", exc)
            log_json(_LOG, event="ui_action", gate="ui", action="route_record_start", phase="failed", reason=str(exc))
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "start"})
            self._show_error("Route Recorder", f"No se pudo iniciar la grabación.\n\n{exc}")

    def _route_capture_with_obs_fallback(self) -> tuple[RuntimeContext, Any]:
        ctx = self._new_route_runtime_context()
        try:
            return ctx, route_capture_only_preflight(ctx)
        except PreflightFailed as exc:
            reason = str(exc)
            if reason not in {
                "obs_source_not_found",
                "minimap_player_not_found",
                "minimap_not_detected",
                "capture_black_or_unavailable",
                "obs_capture_invalid_content",
            }:
                raise

        for cand in self._pick_obs_source_candidates():
            os.environ["FRBOT_OBS_SOURCE_NAME"] = str(cand)
            self.lbl_route_status.setText(f"Recorder: reintentando con fuente OBS '{cand}'...")
            try:
                ctx = self._new_route_runtime_context()
                return ctx, route_capture_only_preflight(ctx)
            except PreflightFailed:
                continue

        ctx = self._new_route_runtime_context()
        return ctx, route_capture_only_preflight(ctx)

    def _pick_obs_source_candidates(self) -> list[str]:
        current = str(os.environ.get("FRBOT_OBS_SOURCE_NAME", "") or "").strip().lower()
        candidates = list_obs_input_names()
        if not candidates:
            return []

        def _score(name: str) -> int:
            n = str(name or "").strip().lower()
            score = 0
            if current and current in n:
                score += 5
            if "tibia" in n:
                score += 10
            if "game" in n:
                score += 4
            if "window" in n:
                score += 3
            if "display" in n or "monitor" in n:
                score += 2
            if "captur" in n or "fuente" in n:
                score += 1
            return score

        ordered = sorted(candidates, key=_score, reverse=True)
        out: list[str] = []
        seen: set[str] = set()
        for name in ordered:
            s = str(name or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def _on_route_stop_clicked(self) -> None:
        try:
            if self._route_session is None:
                self.lbl_route_status.setText("Recorder: ya está detenido")
                self._set_route_controls_enabled(recording=False)
                return

            if bool(self._route_recording_active):
                self._route_timer.stop()
                self._route_recording_active = False
                self.btn_route_stop.setText("Reanudar")
                self.lbl_route_status.setText(f"Recorder: pausado ({len(self._route_session.waypoints)} steps)")
            else:
                self._route_timer.start()
                self._route_recording_active = True
                self.btn_route_stop.setText("Pausar")
                self.lbl_route_status.setText("Recorder: grabando...")

            self._update_route_counters()
            self._set_route_controls_enabled(recording=self._route_recording_active)
        except Exception as exc:
            _LOG.exception("Failed to pause/resume route recorder: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "pause_resume"})
            self._show_error("Route Recorder", f"No se pudo pausar/reanudar grabación.\n\n{exc}")

    def _on_route_apply_clicked(self) -> None:
        try:
            if self._route_session is None:
                return
            if self._route_recording_active:
                self._route_timer.stop()
                self._route_recording_active = False

            script = self._build_route_script_for_output()
            self._script = script
            self.model.set_script(self._script)
            self._refresh_script_list()
            self._sync_right_controls_from_script()
            self._sync_aux_controls_from_metadata()
            self._mark_dirty()
            self.lbl_route_status.setText(f"Recorder: ruta aplicada ({len(self._script.waypoints)} waypoints)")
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed to apply recorded route: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "apply"})
            self._show_error("Route Recorder", f"No se pudo aplicar la ruta grabada.\n\n{exc}")

    def _on_route_reset_clicked(self) -> None:
        try:
            self._route_timer.stop()
            self._route_recording_active = False
            self._route_capture = None
            self._route_binding = None
            self._route_sampler = None
            self._route_session = None
            self._route_marker_cfg = None
            self._route_tibia_dataset = None
            self._route_world_by_rel = {}
            self._route_last_world = None
            self._route_world_lock_last_score = None
            self._set_route_world_lock_status(False)
            self._waypoint_recorder = None
            self._route_last_minimap_digest = ""
            self._route_floor_change_streak = 0
            self._route_last_tile = None
            self.route_steps_list.clear()
            self.btn_route_stop.setText("Pausar")
            self.lbl_route_status.setText("Recorder: sesión reiniciada")
            self._update_route_counters()
            self._set_route_controls_enabled(recording=False)
        except Exception as exc:
            _LOG.exception("Failed to reset route recorder session: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "reset"})
            self._show_error("Route Recorder", f"No se pudo reiniciar la sesión.\n\n{exc}")

    def _on_route_mark_action(self, action: str) -> None:
        try:
            if self._route_session is None or not bool(self._route_recording_active):
                return
            a = str(action)
            # If recorder has no last sampled tile, attempt to sample immediately
            try:
                if getattr(self._route_session, "_last_tile", None) is None:
                    if self._route_capture is not None and self._route_sampler is not None:
                        try:
                            frame = self._route_capture.grab()
                            tile = self._route_sampler.sample_tile(frame)
                            if tile is not None:
                                self._route_session.record_tile(int(tile[0]), int(tile[1]), int(tile[2]))
                        except Exception:
                            # sampling failed; proceed to mark_action which will raise a clear error
                            pass
            except Exception:
                pass

            _ = self._route_session.mark_action(a)
            self.route_steps_list.addItem(f"#{len(self._route_session.waypoints) - 1} {a}")
            self.lbl_route_status.setText(f"Recorder: acción {action} marcada")
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed to mark route action: %s", exc)
            # Provide explicit reason to user and diagnostics instead of bubbling raw ValueError
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "action", "action": str(action)})
            self._show_error("Route Recorder", f"No se pudo marcar acción {action}.\n\nMotivo: {str(exc)}\n\nAsegúrate de que el recorder haya detectado tu posición en el minimapa antes de marcar la acción.")

    def _on_route_tick(self) -> None:
        try:
            if not bool(self._route_recording_active):
                return
            if self._route_capture is None or self._route_sampler is None or self._route_session is None:
                return

            frame = self._route_capture.grab()
            tile = self._route_sampler.sample_tile(frame)
            if tile is None:
                return

            added = self._route_session.record_tile(int(tile[0]), int(tile[1]), int(tile[2]))
            if added:
                world = self._route_try_localize_world(frame=frame, tile=tile)
                if world is not None:
                    self._route_world_by_rel[(int(tile[0]), int(tile[1]), int(tile[2]))] = (
                        int(world[0]),
                        int(world[1]),
                        int(world[2]),
                    )
                self.route_steps_list.addItem(f"#{len(self._route_session.waypoints) - 1} move ({int(tile[0])},{int(tile[1])},{int(tile[2])})")

            prev_digest = str(self._route_last_minimap_digest or "")
            cur_digest = str(frame.minimap_digest_hex or "")
            if prev_digest and cur_digest and prev_digest != cur_digest and self._route_last_tile == tile:
                self._route_floor_change_streak += 1
            else:
                self._route_floor_change_streak = 0

            if int(self._route_floor_change_streak) >= 2:
                _ = self._route_session.mark_action("stairs_up")
                self.route_steps_list.addItem(f"#{len(self._route_session.waypoints) - 1} stairs_up (auto)")
                self._route_floor_change_streak = 0

            self._route_last_minimap_digest = cur_digest
            self._route_last_tile = tile
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed in route recorder tick: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "tick"})
            self._show_error("Route Recorder", f"No se pudo capturar waypoint.\n\n{exc}")
            self._route_timer.stop()
            self._route_recording_active = False
            self._set_route_controls_enabled(recording=False)

    def _on_route_move_key(self, key: str) -> None:
        return

    def _on_route_export_clicked(self) -> None:
        try:
            if self._route_session is None:
                return
            script = self._build_route_script_for_output()
            out_dir = Path.cwd() / "diagnostics" / "waypoints"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{script.name}.json"
            save_script(str(out_path), script)
            self._waypoint_export_path = str(out_path)
            self._route_timer.stop()
            self._route_recording_active = False
            self.btn_route_stop.setText("Pausar")
            self._set_route_controls_enabled(recording=False)
            if self._waypoint_export_path:
                self.lbl_route_status.setText(f"Recorder: exportado {Path(self._waypoint_export_path).name}")
        except Exception as exc:
            _LOG.exception("Failed to export route recorder session: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "export"})
            self._show_error("Route Recorder", f"No se pudo exportar sesión.\n\n{exc}")

    def _on_route_save_recording_clicked(self) -> None:
        try:
            if self._route_session is None:
                self.lbl_route_status.setText("Recorder: no hay sesión para guardar")
                return

            if self._route_recording_active:
                self._route_timer.stop()
                self._route_recording_active = False
                self.btn_route_stop.setText("Pausar")

            script = self._build_route_script_for_output()
            out_dir = Path.cwd() / "Waypoints"
            out_dir.mkdir(parents=True, exist_ok=True)
            default_path = out_dir / f"{script.name}.json"
            path, _ = QFileDialog.getSaveFileName(self, "Guardar grabación", str(default_path), "JSON (*.json)")
            if not path:
                self._set_route_controls_enabled(recording=False)
                return

            save_script(path, script)
            self._waypoint_export_path = str(path)
            self.lbl_route_status.setText(f"Recorder: guardado {Path(path).name}")
            self._set_route_controls_enabled(recording=False)
        except Exception as exc:
            _LOG.exception("Failed to save waypoint recording: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "save_recording"})
            self._show_error("Route Recorder", f"No se pudo guardar la grabación.\n\n{exc}")

    def _on_route_open_folder_clicked(self) -> None:
        try:
            base = Path.cwd() / "diagnostics" / "waypoints"
            base.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(base.resolve())))
        except Exception as exc:
            _LOG.exception("Failed to open waypoints folder: %s", exc)
            self._show_error("Route Recorder", f"No se pudo abrir carpeta.\n\n{exc}")

    def _refresh_script_list(self) -> None:
        self.scripts_list.clear()
        self.scripts_list.addItem(self._script.name)
        self.scripts_list.setCurrentRow(0)

    def _sync_right_controls_from_script(self) -> None:
        self.chk_enabled.blockSignals(True)
        self.chk_run_to_target.blockSignals(True)
        try:
            self.chk_enabled.setChecked(bool(self._script.enabled))
            self.chk_run_to_target.setChecked(bool(self._script.run_to_target))
        finally:
            self.chk_enabled.blockSignals(False)
            self.chk_run_to_target.blockSignals(False)

    def _selected_direction(self) -> str:
        if self.rb_north.isChecked():
            return "north"
        if self.rb_west.isChecked():
            return "west"
        if self.rb_east.isChecked():
            return "east"
        if self.rb_south.isChecked():
            return "south"
        return "center"

    def _on_script_flags_changed(self) -> None:
        try:
            self._script.enabled = bool(self.chk_enabled.isChecked())
            self._script.run_to_target = bool(self.chk_run_to_target.isChecked())
            _LOG.info("Script flags changed: enabled=%s run_to_target=%s", self._script.enabled, self._script.run_to_target)
            self._mark_dirty()
        except Exception as exc:
            _LOG.exception("Failed to change script flags: %s", exc)
            self._show_error("Error", f"Failed to change script flags.\n\n{exc}")

    def _on_action_button_clicked(self) -> None:
        btn = self.sender()
        action_name = ""
        try:
            action_name = str(btn.property("action_name"))
            x = int(self.spin_x.value())
            y = int(self.spin_y.value())
            z = int(self.spin_z.value())

            options: dict[str, Any] = {}
            wp_type = action_name

            if action_name == WaypointType.SINGLE_MOVE.value:
                options = {"direction": self._selected_direction()}
            elif action_name == WaypointType.WALK_IGNORE.value:
                options = {"ignore": True}

            wp = Waypoint(type=wp_type, x=x, y=y, z=z, options=options, enabled=True, created_at=now_iso())
            self.model.insert_waypoint(wp)

            _LOG.info("Action requested: %s (%s,%s,%s) options=%s", action_name, x, y, z, options)
            self.actionRequested.emit(action_name, wp)
        except Exception as exc:
            _LOG.exception("Action button failed: %s", exc)
            self._show_error("Error", f"Failed to add waypoint for action '{action_name}'.\n\n{exc}")

    def _on_delete_clicked(self) -> None:
        try:
            selection = self.table.selectionModel().selectedRows()
            rows = [idx.row() for idx in selection]
            if not rows:
                return
            self.model.delete_rows(rows)
            _LOG.info("Deleted waypoints rows=%s", rows)
        except Exception as exc:
            _LOG.exception("Delete waypoints failed: %s", exc)
            self._show_error("Error", f"Failed to delete selected waypoints.\n\n{exc}")

    def _on_save_clicked(self) -> None:
        try:
            self._write_aux_controls_to_metadata()
            path = self._current_path
            if not path:
                path, _ = QFileDialog.getSaveFileName(self, "Save Script", str(self._waypoints_dir() / "script.json"), "JSON (*.json)")
                if not path:
                    return

            save_script(path, self._script)
            self._set_current_script_path(path)
            self._last_saved_state = canonical_json(self._script)
            self._update_title()
            _LOG.info("Saved script to %s", path)
            self.scriptSaved.emit(path)
        except SchemaError as exc:
            _LOG.exception("Save failed (schema): %s", exc)
            self._show_error("Save failed", str(exc))
        except Exception as exc:
            _LOG.exception("Save failed: %s", exc)
            self._show_error("Save failed", f"{exc}")

    def _on_load_clicked(self) -> None:
        try:
            path, _ = QFileDialog.getOpenFileName(self, "Load Script", str(self._default_load_dir()), "JSON (*.json)")
            if not path:
                return

            script = load_script(path)
            self._script = script
            self.model.set_script(self._script)
            self._set_current_script_path(path)
            self._last_saved_state = canonical_json(self._script)
            self._refresh_script_list()
            self._sync_right_controls_from_script()
            self._sync_aux_controls_from_metadata()
            self._apply_runtime_env_from_ui()
            self._update_title()

            _LOG.info("Loaded script from %s", path)
            self.scriptLoaded.emit(path, script)
        except SchemaError as exc:
            _LOG.exception("Load failed (schema): %s", exc)
            self._show_error("Load failed", str(exc))
        except Exception as exc:
            _LOG.exception("Load failed: %s", exc)
            self._show_error("Load failed", f"{exc}")

    def _mark_dirty(self) -> None:
        self._update_title()

    def _is_dirty(self) -> bool:
        try:
            return canonical_json(self._script) != self._last_saved_state
        except Exception:
            return True

    def _update_title(self) -> None:
        dirty = self._is_dirty()
        name = self._script.name
        p = self._current_path
        suffix = " *" if dirty else ""
        where = f" - {Path(p).name}" if p else ""
        self.setWindowTitle(f"Waypoint/Script Editor{where} ({name}){suffix}")

    def _show_error(self, title: str, message: str) -> None:
        try:
            write_fatal("ui_error", None, details={"title": str(title), "message": str(message)})
        except Exception:
            pass
        _QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        try:
            if bool(self._cavebot_running):
                self._stop_cavebot_runtime(status_text="detenido")

            if not self._is_dirty():
                event.accept()
                return

            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText("You have unsaved changes. Save before closing?")
            btn_save = box.addButton("Save", _QMessageBox.ButtonRole.AcceptRole)
            btn_discard = box.addButton("Discard", _QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton("Cancel", _QMessageBox.ButtonRole.RejectRole)
            box.setIcon(_QMessageBox.Icon.Warning)
            box.exec()

            clicked = box.clickedButton()
            if clicked == btn_cancel:
                event.ignore()
                return
            if clicked == btn_discard:
                event.accept()
                return
            if clicked == btn_save:
                self._on_save_clicked()
                if self._is_dirty():
                    event.ignore()
                else:
                    event.accept()
                return

            event.ignore()
        except Exception as exc:
            _LOG.exception("closeEvent failed: %s", exc)
            try:
                _QMessageBox.critical(self, "Error", f"Unexpected error during close.\n\n{exc}")
            except Exception:
                pass
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
