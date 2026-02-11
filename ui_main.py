from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt as _Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
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
    QFileDialog,
)

from logging_setup import setup_logger
from models import Script, Waypoint, WaypointType, now_iso
from storage import SchemaError, canonical_json, load_script, save_script


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
        border: 1px solid {_ACCENT};
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
    }}

    QLabel#header {{
        font-size: 18px;
        font-weight: 600;
        padding: 6px 0;
    }}

    QListWidget {{
        background: {_PANEL_BG};
        border: 1px solid {_ACCENT};
        border-radius: 8px;
        padding: 6px;
    }}

    QTableView {{
        background: {_PANEL_BG};
        border: 1px solid {_ACCENT};
        border-radius: 8px;
        gridline-color: #2b2b2b;
        selection-background-color: #202020;
        selection-color: {_TEXT};
        alternate-background-color: #111111;
    }}

    QHeaderView::section {{
        background: #121212;
        color: {_TEXT};
        border: 0px;
        border-bottom: 1px solid {_ACCENT};
        padding: 6px;
    }}

    QPushButton {{
        background: {_BTN_BG};
        border: 2px solid {_ACCENT};
        border-radius: 10px;
        padding: 8px 10px;
        color: {_TEXT};
    }}

    QPushButton:hover {{
        background: {_BTN_HOVER};
    }}

    QPushButton:pressed {{
        background: {_BTN_PRESSED};
    }}

    QPushButton#danger {{
        border: 2px solid {_ACCENT};
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
        border: 1px solid {_ACCENT};
        border-radius: 8px;
        padding: 4px 8px;
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
        self.setFixedSize(1280, 720)

        self._current_path: str | None = None
        self._script = Script(name="script1")
        self._last_saved_state = canonical_json(self._script)

        self._build_ui()
        self._wire_events()
        self._refresh_script_list()
        self._sync_right_controls_from_script()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

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
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)

        dir_group = QGroupBox("Direction")
        dir_layout = QVBoxLayout(dir_group)
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
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

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
            self.action_buttons[action_name] = btn
            r = i // 2
            c = i % 2
            grid.addWidget(btn, r, c)
        right_layout.addWidget(buttons_box, 1)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self.setStyleSheet(app_stylesheet())

        # Menu shortcuts (optional quality-of-life, no extra UX surfaces)
        act_save = QAction("Save", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self._on_save_clicked)
        self.addAction(act_save)

    def _wire_events(self) -> None:
        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_load.clicked.connect(self._on_load_clicked)
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        self.chk_enabled.stateChanged.connect(self._on_script_flags_changed)
        self.chk_run_to_target.stateChanged.connect(self._on_script_flags_changed)
        self.model.dataChanged.connect(lambda *_: self._mark_dirty())
        self.model.rowsInserted.connect(lambda *_: self._mark_dirty())
        self.model.rowsRemoved.connect(lambda *_: self._mark_dirty())

        for btn in self.action_buttons.values():
            btn.clicked.connect(self._on_action_button_clicked)

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
