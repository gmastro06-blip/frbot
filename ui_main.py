from __future__ import annotations

import json
import os
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
from models import Script, Waypoint, WaypointType, now_iso
from storage import SchemaError, canonical_json, load_script, save_script
from diagnostics.fatal import write_fatal
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.minimap_semantics import marker_config_from_env
from runtime.preflight import preflight as runtime_preflight
from runtime.route_recorder import MinimapRouteSampler, RouteRecordingSession, WaypointRecorder
from adapters.windows import win32 as w32


# PySide6 exposes many Qt enums/flags dynamically. Some type-checker versions
# (and certain bundled stubs) can report false-positive attribute errors.
Qt = cast(Any, _Qt)
_QAbstractItemView = cast(Any, QAbstractItemView)
_QMessageBox = cast(Any, QMessageBox)


_LOG = setup_logger()


_DARK_BG = "#0f0f0f"
_PANEL_BG = "#141414"
_TEXT = "#d0d0d0"
_MUTED = "#9a9a9a"
_ACCENT = "#c43b3b"
_BORDER = "#2b2b2b"
_BTN_BG = "#1a1a1a"
_BTN_HOVER = "#232323"
_BTN_PRESSED = "#2a2a2a"


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
        self._route_recording_active: bool = False
        self._waypoint_recorder: WaypointRecorder | None = None
        self._waypoint_export_path: str | None = None
        self._cavebot_running: bool = False

        self._build_ui()
        self._wire_events()
        self._refresh_script_list()
        self._sync_right_controls_from_script()
        self._sync_aux_controls_from_metadata()
        self._apply_runtime_env_from_ui()

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
        self.btn_screen_settings = QPushButton("Configuración")

        nav_layout.addWidget(self.btn_screen_home)
        nav_layout.addWidget(self.btn_screen_healing)
        nav_layout.addWidget(self.btn_screen_cavebot)
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
        self.lbl_route_counts.setStyleSheet(f"color: {_MUTED};")
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
        self.btn_route_open_folder.setEnabled(False)

        recorder_layout.addWidget(self.lbl_route_status)
        recorder_layout.addWidget(self.lbl_route_counts)
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
        self.btn_cavebot_stop.setEnabled(False)
        cavebot_status_layout.addWidget(self.btn_cavebot_start)
        cavebot_status_layout.addWidget(self.btn_cavebot_stop)
        cavebot_status_layout.addWidget(self.lbl_cavebot_status, 1)

        cavebot_shell = QVBoxLayout()
        cavebot_shell.setContentsMargins(0, 0, 0, 0)
        cavebot_shell.setSpacing(8)
        cavebot_shell.addWidget(cavebot_status_box)
        cavebot_shell.addWidget(splitter, 1)
        cavebot_layout.addLayout(cavebot_shell)
        self.screens.addWidget(cavebot_screen)

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
        self.chk_try_focus = QCheckBox("Intentar enfocar ventana")
        self.chk_try_focus.setChecked(True)

        settings_form.addRow("Config path", self.input_config_path)
        settings_form.addRow("Tick Hz", self.spin_tick_hz)
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
        self.btn_screen_waypoints.clicked.connect(lambda: self.screens.setCurrentIndex(2))
        self.btn_screen_settings.clicked.connect(lambda: self.screens.setCurrentIndex(3))
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
        self.chk_try_focus.stateChanged.connect(self._on_aux_controls_changed)
        self.btn_import_env.clicked.connect(self._on_import_env_clicked)
        self.btn_export_env.clicked.connect(self._on_export_env_clicked)

        self.btn_route_start.clicked.connect(self._on_route_start_clicked)
        self.btn_route_stop.clicked.connect(self._on_route_stop_clicked)
        self.btn_route_apply.clicked.connect(self._on_route_apply_clicked)
        self.btn_route_reset.clicked.connect(self._on_route_reset_clicked)
        self.btn_route_export.clicked.connect(self._on_route_export_clicked)
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

        for btn in self.action_buttons.values():
            btn.clicked.connect(self._on_action_button_clicked)

    def _on_cavebot_start_clicked(self) -> None:
        self._cavebot_running = True
        self.btn_cavebot_start.setEnabled(False)
        self.btn_cavebot_stop.setEnabled(True)
        self.lbl_cavebot_status.setText("Estado: running")

    def _on_cavebot_stop_clicked(self) -> None:
        self._cavebot_running = False
        self.btn_cavebot_start.setEnabled(True)
        self.btn_cavebot_stop.setEnabled(False)
        self.lbl_cavebot_status.setText("Estado: detenido")

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
        self.chk_try_focus.blockSignals(True)
        try:
            self.chk_healing_enabled.setChecked(bool(healing_md.get("enabled", True)))
            self.input_heal_key.setText(str(healing_md.get("heal_key", "F1") or "F1"))
            hp_pct = int(healing_md.get("hp_threshold_percent", 50) or 50)
            self.spin_heal_hp_threshold.setValue(max(1, min(100, int(hp_pct))))

            self.input_config_path.setText(str(general_md.get("config_path", "config/rois_prod_full.json") or "config/rois_prod_full.json"))
            tick_hz = int(general_md.get("tick_hz", 20) or 20)
            self.spin_tick_hz.setValue(max(1, min(120, int(tick_hz))))
            self.chk_try_focus.setChecked(bool(general_md.get("try_focus", True)))
        finally:
            self.chk_healing_enabled.blockSignals(False)
            self.input_heal_key.blockSignals(False)
            self.spin_heal_hp_threshold.blockSignals(False)
            self.input_config_path.blockSignals(False)
            self.spin_tick_hz.blockSignals(False)
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
            "FRBOT_TRY_FOCUS": "1" if bool(settings["try_focus"]) else "0",
        }

    def _on_aux_controls_changed(self, *_: Any) -> None:
        try:
            self._write_aux_controls_to_metadata()
            self._apply_runtime_env_from_ui()
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

            if "FRBOT_TRY_FOCUS" in env_values:
                self.chk_try_focus.setChecked(_env_bool(env_values.get("FRBOT_TRY_FOCUS", "1"), True))
        finally:
            self.chk_healing_enabled.blockSignals(False)
            self.input_heal_key.blockSignals(False)
            self.spin_heal_hp_threshold.blockSignals(False)
            self.input_config_path.blockSignals(False)
            self.spin_tick_hz.blockSignals(False)
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
        recorder = self._waypoint_recorder
        if recorder is None:
            self.lbl_route_counts.setText("Steps: 0 | Move: 0 | Rope: 0 | Shovel: 0 | Pick: 0 | Up: 0 | Down: 0")
            return

        steps = list(recorder.steps)
        total = int(len(steps))
        move = 0
        rope = 0
        shovel = 0
        pick = 0
        up = 0
        down = 0
        for step in steps:
            t = str(getattr(step, 'action_kind', '') or '').strip().lower()
            if t == 'move':
                move += 1
            elif t == 'rope':
                rope += 1
            elif t == 'shovel':
                shovel += 1
            elif t == 'pick':
                pick += 1
            elif t == 'stairs_up':
                up += 1
            elif t == 'stairs_down':
                down += 1

        self.lbl_route_counts.setText(
            f"Steps: {total} | Move: {move} | Rope: {rope} | Shovel: {shovel} | Pick: {pick} | Up: {up} | Down: {down}"
        )

    def _set_route_controls_enabled(self, *, recording: bool) -> None:
        self.btn_route_start.setEnabled(not bool(recording))
        self.btn_route_stop.setEnabled(self._waypoint_recorder is not None)
        self.btn_route_apply.setEnabled(self._waypoint_recorder is not None and not bool(recording))
        self.btn_route_mark_rope.setEnabled(bool(recording))
        self.btn_route_mark_shovel.setEnabled(bool(recording))
        self.btn_route_mark_pick.setEnabled(bool(recording))
        self.btn_route_mark_up.setEnabled(bool(recording))
        self.btn_route_mark_down.setEnabled(bool(recording))
        self.btn_route_export.setEnabled(self._waypoint_recorder is not None)
        self.btn_route_open_folder.setEnabled(self._waypoint_recorder is not None)

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
            mode=str(os.environ.get("FRBOT_MODE", "real") or "real").strip().lower(),
            tick_hz=float(self.spin_tick_hz.value()),
            config_path=str(self.input_config_path.text() or "").strip(),
            enable_cavebot=False,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=False,
            minimap_roi=str(os.environ.get("FRBOT_MINIMAP_ROI", "minimap") or "minimap"),
            player_marker_rgb=str(os.environ.get("FRBOT_PLAYER_MARKER_RGB", "255,0,255") or "255,0,255"),
            player_marker_tol=int(str(os.environ.get("FRBOT_PLAYER_MARKER_TOL", "30") or "30")),
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
            ctx = self._new_route_runtime_context()
            try:
                capture, _input, binding = runtime_preflight(ctx)
            except Exception as first_exc:
                reason = str(first_exc or "").strip().lower()
                if "obs_source_not_found" not in reason:
                    if "window_not_foreground" not in reason:
                        raise
                    prev_profile = os.environ.get("FRBOT_PROFILE")
                    try:
                        os.environ["FRBOT_PROFILE"] = "ui_local"
                        os.environ["FRBOT_TRY_FOCUS"] = "1"
                        os.environ["FRBOT_FOREGROUND_RETRIES"] = "12"
                        os.environ["FRBOT_FOREGROUND_DELAY_MS"] = "120"
                        ctx = self._new_route_runtime_context()
                        capture, _input, binding = runtime_preflight(ctx)
                        self.lbl_route_status.setText("Recorder: fallback local (sin guard foreground prod)")
                    finally:
                        if prev_profile is None:
                            os.environ.pop("FRBOT_PROFILE", None)
                        else:
                            os.environ["FRBOT_PROFILE"] = str(prev_profile)
                else:
                    os.environ["FRBOT_CAPTURE_SOURCE"] = "client"
                    ctx = self._new_route_runtime_context()
                    try:
                        capture, _input, binding = runtime_preflight(ctx)
                        self.lbl_route_status.setText("Recorder: fallback a captura cliente")
                    except Exception as second_exc:
                        reason2 = str(second_exc or "").strip().lower()
                        if "window_not_foreground" not in reason2:
                            raise
                        prev_profile = os.environ.get("FRBOT_PROFILE")
                        try:
                            os.environ["FRBOT_PROFILE"] = "ui_local"
                            os.environ["FRBOT_TRY_FOCUS"] = "1"
                            os.environ["FRBOT_FOREGROUND_RETRIES"] = "12"
                            os.environ["FRBOT_FOREGROUND_DELAY_MS"] = "120"
                            ctx = self._new_route_runtime_context()
                            capture, _input, binding = runtime_preflight(ctx)
                            self.lbl_route_status.setText("Recorder: fallback local (sin guard foreground prod)")
                        finally:
                            if prev_profile is None:
                                os.environ.pop("FRBOT_PROFILE", None)
                            else:
                                os.environ["FRBOT_PROFILE"] = str(prev_profile)

            marker_cfg = marker_config_from_env(
                str(ctx.config.player_marker_rgb),
                str(ctx.config.player_marker_tol),
                str(ctx.config.player_marker_min_pixels),
                str(ctx.config.player_marker_max_pixels),
                str(ctx.config.player_marker_min_fill_ratio),
                str(ctx.config.player_marker_max_aspect_ratio),
            )

            self._route_capture = capture
            self._route_binding = binding
            self._route_sampler = MinimapRouteSampler(
                marker_cfg=marker_cfg,
                pixels_per_tile=float(ctx.config.pixels_per_tile),
                z=int(self._route_z()),
            )
            self._waypoint_recorder = WaypointRecorder(
                capture=capture,
                input_adapter=_input,
                binding=binding,
                marker_cfg=marker_cfg,
                out_dir=Path.cwd() / "diagnostics" / "waypoints",
                max_steps=2000,
            )
            self._waypoint_recorder.start(
                {
                    "script_name": f"waypoints_{int(time.time())}",
                    "window_title": str(ctx.config.window_title_substring),
                    "window_hwnd": int(ctx.config.window_hwnd),
                    "capture_source": str(os.environ.get("FRBOT_CAPTURE_SOURCE", "") or ""),
                }
            )
            self._route_recording_active = True
            self._set_route_controls_enabled(recording=True)
            self.lbl_route_status.setText("Recorder: grabando...")
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed to start route recorder: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "start"})
            self._show_error("Route Recorder", f"No se pudo iniciar la grabación.\n\n{exc}")

    def _on_route_stop_clicked(self) -> None:
        try:
            if self._waypoint_recorder is None:
                self.lbl_route_status.setText("Recorder: ya está detenido")
                self._set_route_controls_enabled(recording=False)
                return

            if bool(self._route_recording_active):
                self._waypoint_recorder.pause()
                self._route_recording_active = False
                self.btn_route_stop.setText("Reanudar")
                self.lbl_route_status.setText(f"Recorder: pausado ({len(self._waypoint_recorder.steps)} steps)")
            else:
                self._waypoint_recorder.resume()
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
            if self._waypoint_recorder is None:
                return
            if self._route_recording_active:
                self._waypoint_recorder.pause()
                self._route_recording_active = False

            steps = list(self._waypoint_recorder.steps)
            waypoints: list[Waypoint] = []
            for step in steps:
                options = {
                    "action_kind": str(step.action_kind),
                    "key_or_click": str(step.key_or_click),
                    "before_ppm": str(step.before_ppm),
                    "after_ppm": str(step.after_ppm),
                    "metrics": dict(step.metrics),
                }
                wp_type = WaypointType.WALK.value if str(step.action_kind) == "move" else WaypointType.USE_RIGHT_CLICK.value
                if str(step.action_kind) == "rope":
                    wp_type = WaypointType.ROPE.value
                elif str(step.action_kind) in {"stairs_up", "stairs_down"}:
                    wp_type = WaypointType.USE_LADDER.value

                waypoints.append(
                    Waypoint(
                        type=str(wp_type),
                        x=int(step.step_index),
                        y=0,
                        z=int(self._route_z()),
                        options=options,
                        enabled=True,
                        created_at=str(step.ts),
                    )
                )

            script = Script(
                name=f"recorded_route_{int(time.time())}",
                enabled=True,
                run_to_target=False,
                waypoints=waypoints,
                metadata={"waypoint_recorder_jsonl": str(self._waypoint_recorder.jsonl_path)},
            )
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
            autosaved_path: str | None = None
            if self._waypoint_recorder is not None and len(self._waypoint_recorder.steps) > 0:
                try:
                    out_path = self._waypoint_recorder.stop(save=True)
                    autosaved_path = str(out_path) if out_path is not None else None
                    _LOG.info("Autosaved recorded route before reset: %s", autosaved_path)
                except Exception as exc:
                    _LOG.exception("Failed to autosave route before reset: %s", exc)

            self._route_recording_active = False
            self._route_capture = None
            self._route_binding = None
            self._route_sampler = None
            self._route_session = None
            self._waypoint_recorder = None
            self.route_steps_list.clear()
            self.btn_route_stop.setText("Pausar")
            if autosaved_path:
                self.lbl_route_status.setText(f"Recorder: sesión reiniciada (autosave: {Path(autosaved_path).name})")
                _QMessageBox.information(
                    self,
                    "Route Recorder",
                    f"Sesión reiniciada.\nRuta auto-guardada en:\n{autosaved_path}",
                )
            else:
                self.lbl_route_status.setText("Recorder: sesión reiniciada")
            self._update_route_counters()
            self._set_route_controls_enabled(recording=False)
        except Exception as exc:
            _LOG.exception("Failed to reset route recorder session: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "reset"})
            self._show_error("Route Recorder", f"No se pudo reiniciar la sesión.\n\n{exc}")

    def _on_route_mark_action(self, action: str) -> None:
        try:
            if self._waypoint_recorder is None or not bool(self._route_recording_active):
                return
            key_map = {
                "rope": str(os.environ.get("FRBOT_ROPE_KEY", "F8") or "F8"),
                "shovel": str(os.environ.get("FRBOT_SHOVEL_KEY", "F9") or "F9"),
                "pick": str(os.environ.get("FRBOT_PICK_KEY", "F10") or "F10"),
                "stairs_up": str(os.environ.get("FRBOT_STAIRS_UP_KEY", "PageUp") or "PageUp"),
                "stairs_down": str(os.environ.get("FRBOT_STAIRS_DOWN_KEY", "PageDown") or "PageDown"),
            }
            a = str(action)
            key = str(key_map.get(a, ""))
            step = self._waypoint_recorder.record_action(a, key)
            self.route_steps_list.addItem(f"#{step.step_index} {step.action_kind} [{step.key_or_click}]")
            self.lbl_route_status.setText(f"Recorder: acción {action} marcada")
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed to mark route action: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "action", "action": str(action)})
            self._show_error("Route Recorder", f"No se pudo marcar acción {action}.\n\n{exc}")

    def _on_route_tick(self) -> None:
        return

    def _on_route_move_key(self, key: str) -> None:
        try:
            if self._waypoint_recorder is None or not bool(self._route_recording_active):
                return
            step = self._waypoint_recorder.record_move(str(key))
            self.route_steps_list.addItem(f"#{step.step_index} move [{step.key_or_click}]")
            self.lbl_route_status.setText(f"Recorder: move {key}")
            self._update_route_counters()
        except Exception as exc:
            _LOG.exception("Failed to record move key: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "move", "key": str(key)})
            self._show_error("Route Recorder", f"No se pudo grabar movimiento {key}.\n\n{exc}")

    def _on_route_export_clicked(self) -> None:
        try:
            if self._waypoint_recorder is None:
                return
            out_path = self._waypoint_recorder.stop(save=True)
            self._waypoint_export_path = str(out_path) if out_path is not None else None
            self._route_recording_active = False
            self.btn_route_stop.setText("Pausar")
            self._set_route_controls_enabled(recording=False)
            if self._waypoint_export_path:
                self.lbl_route_status.setText(f"Recorder: exportado {Path(self._waypoint_export_path).name}")
        except Exception as exc:
            _LOG.exception("Failed to export route recorder session: %s", exc)
            write_fatal("waypoint_record_failed", exc, details={"reason": str(exc), "phase": "export"})
            self._show_error("Route Recorder", f"No se pudo exportar sesión.\n\n{exc}")

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
                path, _ = QFileDialog.getSaveFileName(self, "Save Script", str(Path.cwd() / "script.json"), "JSON (*.json)")
                if not path:
                    return

            save_script(path, self._script)
            self._current_path = path
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
            path, _ = QFileDialog.getOpenFileName(self, "Load Script", str(Path.cwd()), "JSON (*.json)")
            if not path:
                return

            script = load_script(path)
            self._script = script
            self.model.set_script(self._script)
            self._current_path = path
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
