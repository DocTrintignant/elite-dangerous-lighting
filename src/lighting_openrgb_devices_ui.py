#!/usr/bin/env python3
"""Interactive lighting discovery and device selection UI."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from lighting_device_selection import (
    device_control_routes,
    known_device_ids,
    save_device_control_route_review,
    save_device_review,
    selected_device_ids,
    set_show_device_selector_at_startup,
    show_device_selector_at_startup,
)
from lighting_discovery_catalog import (
    SOURCE_CHROMA,
    SOURCE_GOVEE,
    SOURCE_OPENRGB,
    DiscoveryCandidate,
    LightingDiscoveryResult,
    discover_enabled_candidates,
)
from lighting_govee_config import (
    load_govee_configuration,
    with_discovered_verified_devices,
)
from lighting_integration_preferences import (
    SOURCE_CHROMA as PREF_SOURCE_CHROMA,
    SOURCE_GOVEE as PREF_SOURCE_GOVEE,
    SOURCE_OPENRGB as PREF_SOURCE_OPENRGB,
    IntegrationPreferences,
    load_discovery_order,
    load_integration_preferences,
    save_discovery_order,
    save_integration_preferences,
)
from lighting_ui_tokens import BODY_PT
from lighting_openrgb_targets import (
    discovery_target_bindings,
    fallback_target_label,
    is_openrgb_target,
)


_DEVICE_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


_SOURCE_COLOURS_DARK = {
    SOURCE_OPENRGB: "#7FB5FF",
    SOURCE_CHROMA: "#7EE787",
    SOURCE_GOVEE: "#D2A8FF",
}

_SOURCE_COLOURS_LIGHT = {
    SOURCE_OPENRGB: "#245C9E",
    SOURCE_CHROMA: "#267247",
    SOURCE_GOVEE: "#70469A",
}


def _source_colour(source_label: str) -> str:
    app = QApplication.instance()
    light = app is not None and app.property("edlAppearanceEffective") == "Light"
    colours = _SOURCE_COLOURS_LIGHT if light else _SOURCE_COLOURS_DARK
    return colours.get(source_label, "")


def _source_brush(source_label: str) -> QBrush:
    colour = _source_colour(source_label)
    return QBrush(QColor(colour)) if colour else QBrush()


def _source_widget(sources: tuple[str, ...], parent=None) -> QLabel:
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(
        " + ".join(
            (
                f'<span style="color:{_source_colour(source)};">{source}</span>'
                if _source_colour(source)
                else source
            )
            for source in sources
        )
    )
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    label.setContentsMargins(8, 0, 4, 0)
    label.setStyleSheet("background: transparent;")
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return label


def _backend_short_name(route: str) -> str:
    if route == SOURCE_CHROMA:
        return "Chroma"
    if route == SOURCE_OPENRGB:
        return "OpenRGB"
    if route == SOURCE_GOVEE:
        return "Enhanced Govee"
    return str(route)


def _control_route_choices(
    candidate: DiscoveryCandidate,
    enabled_sources: tuple[str, ...],
    configured: str | None = None,
) -> tuple[str, ...]:
    enabled = set(enabled_sources)
    supported = set(candidate.sources)

    # Chroma's REST catalogue exposes endpoint classes rather than physical
    # instances. A physical Razer keyboard/mouse is therefore a known Chroma
    # candidate even while Synapse/Chroma is temporarily unavailable.
    if (
        candidate.vendor.strip().casefold() == "razer"
        and candidate.device_type.strip().casefold() in {"keyboard", "mouse"}
    ):
        supported.add(SOURCE_CHROMA)

    # Preserve an already-confirmed owner as a selectable route while its
    # backend is temporarily unavailable. This is what lets the operator switch
    # explicitly to a healthy alternative without first restoring the old app.
    if configured:
        supported.add(configured)

    return tuple(
        source
        for source in (SOURCE_OPENRGB, SOURCE_CHROMA, SOURCE_GOVEE)
        if source in enabled and source in supported
    )


def _candidate_route_status(
    candidate: DiscoveryCandidate,
    owner: str | None,
    result: LightingDiscoveryResult,
) -> tuple[str, str]:
    """Return operator-facing (status, alternative) for one physical candidate."""
    if owner is None:
        if len(candidate.sources) > 1:
            return "Choose Control via", ""
        owner = candidate.sources[0] if candidate.sources else None

    if owner is None:
        return "No backend available", "—"

    enabled = set(result.enabled_sources)
    available = set(result.available_sources)

    # Compatibility for older/manual results without explicit health metadata.
    if not enabled:
        enabled = set(candidate.sources)
    if not available:
        available = set(candidate.sources)

    if owner not in enabled:
        status = "Disabled in EDL"
    elif owner not in available or owner not in candidate.sources:
        status = "Unavailable"
    else:
        status = "Available"

    alternatives = tuple(
        route
        for route in candidate.sources
        if route != owner and route in available
    )
    alternative = " + ".join(alternatives) if alternatives else "—"
    return status, alternative


def _recommended_control_route(
    candidate: DiscoveryCandidate,
    choices: tuple[str, ...],
) -> str | None:
    """Presentation-only initial recommendation for an ambiguous physical device."""
    if not choices:
        return None
    vendor = candidate.vendor.strip().casefold()
    if vendor == "razer" and SOURCE_CHROMA in choices:
        return SOURCE_CHROMA
    if candidate.govee_device is not None and SOURCE_GOVEE in choices:
        return SOURCE_GOVEE
    if SOURCE_OPENRGB in choices:
        return SOURCE_OPENRGB
    return choices[0]


def _configured_route_for_candidate(
    candidate: DiscoveryCandidate,
    configured: dict[str, str],
) -> str | None:
    for identity in (candidate.identity, *_candidate_aliases(candidate)):
        route = configured.get(identity)
        if route:
            return route
    return None


def _candidate_aliases(candidate: DiscoveryCandidate) -> tuple[str, ...]:
    aliases = set(candidate.aliases)
    physical = candidate.openrgb_device
    if physical is not None:
        aliases.update(physical.member_identities)
    return tuple(sorted(aliases))


class LightingDevicesDialog(QDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._device_items: list[QTreeWidgetItem] = []
        self._candidate_aliases: dict[str, tuple[str, ...]] = {}
        self._session_selected = set(selected_device_ids(settings))
        self._session_unselected: set[str] = set()
        self._session_control_routes = device_control_routes(settings)
        self._visible_overlap_routes: dict[str, str] = {}
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._last_result = LightingDiscoveryResult(())

        self.setWindowTitle("Choose lighting devices")
        self.setMinimumSize(760, 540)

        root = QVBoxLayout(self)

        intro = QLabel(
            "<b>Choose which integrations to scan, then select the devices EDL should use.</b>"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        prefs = load_integration_preferences(settings)
        self._discovery_order = list(load_discovery_order(settings, prefs))
        source_row = QHBoxLayout()

        self.openrgb_check = QCheckBox("OpenRGB")
        self.openrgb_check.setChecked(prefs.openrgb_enabled)
        self.openrgb_check.setStyleSheet(f"color:{_source_colour(SOURCE_OPENRGB)};")
        self.openrgb_check.setToolTip(
            "General compatibility backend for hardware OpenRGB exposes. "
            "The OpenRGB SDK server must be running; its main window may stay closed. "
            "Discovery is read-only and does not change lighting state or device ownership."
        )
        self.openrgb_check.setToolTipDuration(30000)
        source_row.addWidget(self.openrgb_check)

        self.chroma_check = QCheckBox("Razer Chroma")
        self.chroma_check.setChecked(prefs.chroma_enabled)
        self.chroma_check.setStyleSheet(f"color:{_source_colour(SOURCE_CHROMA)};")
        self.chroma_check.setToolTip(
            "Recommended route for Razer devices when Synapse/Chroma is available. "
            "It can also expose some Chroma-compatible third-party lighting, although "
            "those devices may offer fewer LEDs or zones than a native or OpenRGB route."
        )
        self.chroma_check.setToolTipDuration(30000)
        source_row.addWidget(self.chroma_check)

        self.govee_check = QCheckBox("Enhanced Govee H61C3 (native LAN)")
        self.govee_check.setChecked(prefs.govee_h61c3_enabled)
        self.govee_check.setStyleSheet(f"color:{_source_colour(SOURCE_GOVEE)};")
        self.govee_check.setToolTip(
            "EDL's native LAN route for the physically validated Govee H61C3. "
            "It provides finer segment control than generic compatibility routes; "
            "the light must be powered on, reachable on the network and have LAN Control enabled."
        )
        self.govee_check.setToolTipDuration(30000)
        source_row.addWidget(self.govee_check)

        source_row.addStretch(1)

        self.scan_button = QPushButton("Scan again")
        self.scan_button.clicked.connect(self._scan)
        source_row.addWidget(self.scan_button)
        root.addLayout(source_row)

        self.source_note = QLabel("")
        self.source_note.setWordWrap(True)
        root.addWidget(self.source_note)

        self.control_route_note = QLabel(
            "If a device is available through more than one integration, choose its "
            "<b>Control via</b> route."
        )
        self.control_route_note.setWordWrap(True)
        root.addWidget(self.control_route_note)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setProperty("edlState", "warning")
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(
            (
                "Device",
                "Type",
                "LED count",
                "Layout",
                "Source",
                "Control via",
                "Status",
                "Alternative detected",
            )
        )
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        header.sectionClicked.connect(self._header_clicked)

        for column in range(8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)

        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 75)
        self.tree.setColumnWidth(3, 125)
        self.tree.setColumnWidth(4, 290)
        self.tree.setColumnWidth(5, 195)
        self.tree.setColumnWidth(6, 160)
        self.tree.setColumnWidth(7, 165)

        # Open at a width that exposes all columns when the current screen
        # allows it. On smaller displays Qt still provides horizontal scrolling.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            table_width = sum(self.tree.columnWidth(index) for index in range(8))
            desired_width = table_width + 80
            width = min(desired_width, max(760, available.width() - 60))
            height = min(700, max(540, available.height() - 80))
            self.resize(width, height)

        self.tree.headerItem().setToolTip(0, "Click to sort A→Z or Z→A")
        self.tree.headerItem().setToolTip(1, "Click to sort A→Z or Z→A")
        self.tree.headerItem().setToolTip(2, "Click to sort 1→X or X→1")
        self.tree.headerItem().setToolTip(3, "Click to sort A→Z or Z→A")
        self.tree.headerItem().setToolTip(4, "Detected integration route(s)")
        self.tree.headerItem().setToolTip(
            5,
            "Renderer choice for this physical device. A dropdown appears only "
            "when more than one enabled integration can control it.",
        )
        self.tree.headerItem().setToolTip(
            6,
            "Health of the selected backend. Unavailable means the backend itself "
            "is not currently usable; disabled means it is unchecked in EDL.",
        )
        self.tree.headerItem().setToolTip(
            7,
            "Other currently detected backend(s) for the same physical device. "
            "EDL never switches to them automatically.",
        )
        root.addWidget(self.tree, 1)

        self.selection_count = QLabel("")
        root.addWidget(self.selection_count)

        startup_row = QHBoxLayout()
        startup_label = QLabel("<b>Startup:</b>")
        startup_row.addWidget(startup_label)

        self.show_devices_startup_check = QCheckBox(
            "Show this device-selection window at startup"
        )
        self.show_devices_startup_check.setChecked(
            show_device_selector_at_startup(settings)
        )
        startup_row.addWidget(self.show_devices_startup_check)

        self.show_compatibility_startup_check = QCheckBox(
            "Show compatibility message at startup"
        )
        self.show_compatibility_startup_check.setChecked(
            not compatibility_notice_hidden(settings)
        )
        startup_row.addWidget(self.show_compatibility_startup_check)
        startup_row.addStretch(1)
        root.addLayout(startup_row)

        actions = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(self._select_all)
        actions.addWidget(select_all)

        unselect_all = QPushButton("Unselect all")
        unselect_all.clicked.connect(self._unselect_all)
        actions.addWidget(unselect_all)

        actions.addStretch(1)

        continue_button = QPushButton("Continue")
        continue_button.setDefault(True)
        continue_button.clicked.connect(self._save_and_accept)
        actions.addWidget(continue_button)
        root.addLayout(actions)

        self.tree.itemChanged.connect(self._item_changed)
        self.openrgb_check.toggled.connect(
            lambda checked: self._integration_changed(PREF_SOURCE_OPENRGB, checked)
        )
        self.chroma_check.toggled.connect(
            lambda checked: self._integration_changed(PREF_SOURCE_CHROMA, checked)
        )
        self.govee_check.toggled.connect(
            lambda checked: self._integration_changed(PREF_SOURCE_GOVEE, checked)
        )

        # Let the dialog become visible before discovery begins. Some backends can
        # take noticeable time to answer; running discovery in the constructor kept
        # the startup chooser invisible until that work completed.
        QTimer.singleShot(0, self._scan)

    def _preferences(self) -> IntegrationPreferences:
        return IntegrationPreferences(
            openrgb_enabled=self.openrgb_check.isChecked(),
            chroma_enabled=self.chroma_check.isChecked(),
            govee_h61c3_enabled=self.govee_check.isChecked(),
        )

    def _persist_integrations(self) -> None:
        save_integration_preferences(self._settings, self._preferences())
        save_discovery_order(self._settings, self._discovery_order)

    def _integration_changed(self, source: str, checked: bool) -> None:
        self._capture_visible_selection()
        if checked:
            if source not in self._discovery_order:
                self._discovery_order.append(source)
        else:
            self._discovery_order = [
                value for value in self._discovery_order if value != source
            ]
        self._persist_integrations()
        self._scan()

    def _capture_visible_selection(self) -> None:
        for item in self._device_items:
            identity = item.data(0, _DEVICE_ID_ROLE)
            if not identity:
                continue
            identity = str(identity)
            if item.checkState(0) == Qt.CheckState.Checked:
                self._session_selected.add(identity)
                self._session_unselected.discard(identity)
            else:
                self._session_selected.discard(identity)
                self._session_unselected.add(identity)

    def _source_help(self) -> str:
        notes = []
        if self.openrgb_check.isChecked():
            notes.append(
                "OpenRGB is the default backend. Zero-LED zones remain visible as "
                "topology evidence and are not Rule targets."
            )
        if self.chroma_check.isChecked():
            notes.append("Chroma is recommended for Razer devices.")
        if self.govee_check.isChecked():
            notes.append("Enhanced Govee provides native H61C3 support.")
        return " ".join(notes)

    def _scan(self) -> None:
        self._capture_visible_selection()
        self._persist_integrations()

        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning…")
        self.scan_button.repaint()
        QApplication.processEvents()
        try:
            result = discover_enabled_candidates(
                self._preferences(),
                tuple(self._discovery_order),
            )
        finally:
            self.scan_button.setEnabled(True)
            self.scan_button.setText("Scan again")

        self._last_result = result
        self.source_note.setText(self._source_help())

        if result.errors:
            self.error_label.setText("<br>".join(result.errors))
            self.error_label.show()
        else:
            self.error_label.clear()
            self.error_label.hide()

        self._rebuild_tree(result)

    def _rebuild_tree(self, result: LightingDiscoveryResult) -> None:
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._device_items = []
            self._candidate_aliases = {}
            self._visible_overlap_routes = {}
            decorated_rows: list[
                tuple[
                    QTreeWidgetItem,
                    DiscoveryCandidate,
                    tuple[str, ...],
                    str | None,
                    str | None,
                    str | None,
                    str,
                    str,
                    bool,
                ]
            ] = []

            known = known_device_ids(self._settings)
            enabled_sources = tuple(
                source
                for source, enabled in (
                    (SOURCE_OPENRGB, self.openrgb_check.isChecked()),
                    (SOURCE_CHROMA, self.chroma_check.isChecked()),
                    (SOURCE_GOVEE, self.govee_check.isChecked()),
                )
                if enabled
            )

            for candidate in result.candidates:
                identity = candidate.identity
                aliases = _candidate_aliases(candidate)
                self._candidate_aliases[identity] = aliases

                is_new = identity not in known and not any(alias in known for alias in aliases)
                is_selected = (
                    identity in self._session_selected
                    or any(alias in self._session_selected for alias in aliases)
                ) and identity not in self._session_unselected

                display_name = f"{candidate.name}  NEW" if is_new else candidate.name
                led_text = "—" if candidate.led_count is None else str(candidate.led_count)
                configured = _configured_route_for_candidate(
                    candidate,
                    self._session_control_routes,
                )
                route_choices = _control_route_choices(
                    candidate,
                    enabled_sources,
                    configured,
                )
                recommended = _recommended_control_route(candidate, route_choices)
                selected_route = (
                    configured
                    if configured in route_choices
                    else recommended
                )
                display_owner = configured or selected_route
                route_sort = display_owner or (
                    route_choices[0] if route_choices else ""
                )
                status_text, alternative_text = _candidate_route_status(
                    candidate,
                    display_owner,
                    result,
                )

                item = QTreeWidgetItem(
                    (
                        display_name,
                        candidate.device_type,
                        led_text,
                        candidate.layout,
                        "",
                        "",
                        "",
                        "",
                    )
                )
                item.setData(0, _DEVICE_ID_ROLE, identity)
                item.setData(0, _SORT_ROLE, candidate.name.casefold())
                item.setData(1, _SORT_ROLE, candidate.device_type.casefold())
                item.setData(
                    2,
                    _SORT_ROLE,
                    -1 if candidate.led_count is None else int(candidate.led_count),
                )
                item.setData(3, _SORT_ROLE, candidate.layout.casefold())
                item.setData(4, _SORT_ROLE, candidate.source_label.casefold())
                item.setData(5, _SORT_ROLE, route_sort.casefold())
                item.setData(6, _SORT_ROLE, status_text.casefold())
                item.setData(7, _SORT_ROLE, alternative_text.casefold())
                item.setToolTip(0, candidate.name)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                item.setSizeHint(0, QSize(0, 34))
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if is_selected else Qt.CheckState.Unchecked,
                )

                self._device_items.append(item)
                self.tree.addTopLevelItem(item)
                self._add_candidate_details(item, candidate)
                openrgb_multizone = bool(
                    candidate.openrgb_device is not None
                    and any(
                        len(surface.zones) > 1
                        for surface in candidate.openrgb_device.surfaces
                    )
                )
                # Device details are inspection aids. Keep physical rows collapsed
                # by default so the operator first sees one row per device.
                expand_openrgb_details = False
                decorated_rows.append(
                    (
                        item,
                        candidate,
                        route_choices,
                        recommended,
                        configured,
                        selected_route,
                        status_text,
                        alternative_text,
                        expand_openrgb_details,
                    )
                )

            if not result.candidates:
                self.tree.addTopLevelItem(
                    QTreeWidgetItem(("No devices discovered", "", "", "", "", "", "", ""))
                )
            else:
                self._apply_device_sort(0, Qt.SortOrder.AscendingOrder)

                # Attach QWidget cell content only after sorting. QTreeWidget can
                # otherwise discard item widgets while top-level rows are removed
                # and reinserted for sorting.
                for (
                    item,
                    candidate,
                    route_choices,
                    recommended,
                    configured,
                    selected_route,
                    status_text,
                    alternative_text,
                    expand_openrgb_details,
                ) in decorated_rows:
                    if candidate.sources:
                        self.tree.setItemWidget(
                            item,
                            4,
                            _source_widget(candidate.sources, self.tree),
                        )

                    status_label = QLabel(status_text, self.tree)
                    status_label.setAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                    )
                    status_label.setContentsMargins(8, 0, 4, 0)
                    if status_text == "Available":
                        status_label.setProperty("edlState", "ok")
                    elif (
                        status_text == "Disabled in EDL"
                        or status_text == "Choose Control via"
                    ):
                        status_label.setProperty("edlState", "warning")
                    else:
                        status_label.setProperty("edlState", "attention")
                    status_label.setStyleSheet("background:transparent;")
                    self.tree.setItemWidget(item, 6, status_label)

                    alternative_label = QLabel(alternative_text, self.tree)
                    alternative_label.setAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                    )
                    alternative_label.setContentsMargins(8, 0, 4, 0)
                    alternative_label.setStyleSheet("background:transparent;")
                    self.tree.setItemWidget(item, 7, alternative_label)

                    if len(route_choices) == 1:
                        route = configured or route_choices[0]
                        label = QLabel(route, self.tree)
                        label.setAlignment(
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                        )
                        label.setContentsMargins(8, 0, 4, 0)
                        label.setStyleSheet(
                            f"color:{_source_colour(route)}; background:transparent;"
                        )
                        label.setToolTip(
                            "Only one enabled route is configured for this device."
                        )
                        self.tree.setItemWidget(item, 5, label)
                    elif len(route_choices) > 1:
                        combo = QComboBox(self.tree)
                        combo.setFixedHeight(30)
                        combo.setMaximumWidth(195)
                        combo.setContentsMargins(0, 0, 0, 0)
                        for route in route_choices:
                            # Recommendation affects only the initial selection.
                            # The adjacent Status column is reserved for runtime
                            # health/state, so the combo displays backend names only.
                            combo.addItem(route, route)
                            combo.setItemData(
                                combo.count() - 1,
                                _source_brush(route),
                                Qt.ItemDataRole.ForegroundRole,
                            )

                        index = combo.findData(selected_route)
                        combo.setCurrentIndex(index if index >= 0 else 0)

                        def _sync_combo(
                            _index: int,
                            *,
                            widget: QComboBox = combo,
                            device_id: str = candidate.identity,
                            physical_candidate: DiscoveryCandidate = candidate,
                            current_result: LightingDiscoveryResult = result,
                            status_widget: QLabel = status_label,
                            alternative_widget: QLabel = alternative_label,
                            row_item: QTreeWidgetItem = item,
                        ) -> None:
                            route = str(widget.currentData() or "")
                            if route:
                                self._visible_overlap_routes[device_id] = route
                                self._session_control_routes[device_id] = route

                            colour = _source_colour(route)
                            widget.setStyleSheet(
                                (
                                    f"color:{colour}; padding-left:6px;"
                                    if colour
                                    else "padding-left:6px;"
                                )
                            )

                            status, alternative = _candidate_route_status(
                                physical_candidate,
                                route or None,
                                current_result,
                            )
                            status_widget.setText(status)
                            alternative_widget.setText(alternative)
                            row_item.setData(5, _SORT_ROLE, route.casefold())
                            row_item.setData(6, _SORT_ROLE, status.casefold())
                            row_item.setData(7, _SORT_ROLE, alternative.casefold())

                            if status == "Available":
                                status_widget.setProperty("edlState", "ok")
                            elif (
                                status == "Disabled in EDL"
                                or status == "Choose Control via"
                            ):
                                status_widget.setProperty("edlState", "warning")
                            else:
                                status_widget.setProperty("edlState", "attention")
                            status_widget.setStyleSheet("background:transparent;")
                            style = status_widget.style()
                            style.unpolish(status_widget)
                            style.polish(status_widget)

                        combo.currentIndexChanged.connect(_sync_combo)
                        _sync_combo(combo.currentIndex())
                        combo.setToolTip(
                            "Choose which enabled backend owns this physical device. "
                            "Continue confirms the choice. EDL never switches owners automatically."
                        )
                        self.tree.setItemWidget(item, 5, combo)
                    elif configured is not None:
                        label = QLabel(configured, self.tree)
                        label.setAlignment(
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                        )
                        label.setContentsMargins(8, 0, 4, 0)
                        label.setStyleSheet(
                            f"color:{_source_colour(configured)}; background:transparent;"
                        )
                        label.setToolTip(
                            f"{configured} remains the confirmed owner. "
                            "Its current health is shown in Status."
                        )
                        self.tree.setItemWidget(item, 5, label)

                    # Sorting removes/reinserts top-level QTreeWidgetItems, so
                    # apply the presentation expansion only after the existing
                    # sort/decorate phase has stabilized the row.
                    item.setExpanded(expand_openrgb_details)
        finally:
            self.tree.blockSignals(False)

        self._update_selection_count()

    def _add_candidate_details(
        self,
        parent: QTreeWidgetItem,
        candidate: DiscoveryCandidate,
    ) -> None:
        physical = candidate.openrgb_device
        if physical is not None:
            for surface_index, surface in enumerate(physical.surfaces):
                if len(physical.surfaces) > 1:
                    surface_item = QTreeWidgetItem(
                        (
                            f"OpenRGB surface {surface_index + 1}",
                            surface.device_type,
                            str(surface.led_count),
                            surface.description or "OpenRGB",
                            SOURCE_OPENRGB,
                        )
                    )
                    surface_item.setForeground(4, _source_brush(SOURCE_OPENRGB))
                    parent.addChild(surface_item)
                    zone_parent = surface_item
                else:
                    zone_parent = parent

                for zone in surface.zones:
                    zero_led = int(zone.led_count) <= 0
                    if zero_led:
                        layout = "Topology only · not a Rule target"
                    else:
                        layout = zone.zone_type
                        if zone.matrix_width and zone.matrix_height:
                            layout = f"{zone.zone_type} {zone.matrix_width}×{zone.matrix_height}"
                        if zone.segments:
                            layout += f" · {len(zone.segments)} segments"
                    zone_item = QTreeWidgetItem(
                        (
                            zone.name,
                            zone.zone_type,
                            str(zone.led_count),
                            layout,
                            SOURCE_OPENRGB,
                        )
                    )
                    if zero_led:
                        zone_item.setToolTip(
                            0,
                            "OpenRGB reports this zone with 0 LEDs. EDL preserves it as "
                            "topology evidence but does not expose it as a Rule target.",
                        )
                        zone_item.setToolTip(3, zone_item.toolTip(0))
                    zone_item.setForeground(4, _source_brush(SOURCE_OPENRGB))
                    zone_item.setFlags(
                        (zone_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        & ~Qt.ItemFlag.ItemIsSelectable
                    )
                    zone_item.setCheckState(0, parent.checkState(0))
                    zone_item.setDisabled(True)
                    zone_item.setToolTip(
                        0,
                        (
                            zone_item.toolTip(0)
                            or "This row belongs to the physical device above. Select or clear the physical device to include or exclude all of its zones."
                        ),
                    )
                    zone_parent.addChild(zone_item)

        if candidate.chroma_target:
            detail = QTreeWidgetItem(
                (
                    f"Chroma surface: {candidate.chroma_target}",
                    "SDK surface",
                    "—",
                    candidate.layout if SOURCE_OPENRGB not in candidate.sources else "Available route",
                    SOURCE_CHROMA,
                )
            )
            detail.setForeground(4, _source_brush(SOURCE_CHROMA))
            detail.setFlags(
                (detail.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsSelectable
            )
            detail.setCheckState(0, parent.checkState(0))
            detail.setDisabled(True)
            detail.setToolTip(
                0,
                "This surface belongs to the physical device above. Select or clear the physical device to include or exclude it.",
            )
            parent.addChild(detail)

        if candidate.govee_device is not None:
            detail = QTreeWidgetItem(
                (
                    f"{candidate.govee_device.sku} · {candidate.govee_device.ip}",
                    "Native LAN",
                    "—",
                    candidate.layout,
                    SOURCE_GOVEE,
                )
            )
            detail.setForeground(4, _source_brush(SOURCE_GOVEE))
            detail.setFlags(
                (detail.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsSelectable
            )
            detail.setCheckState(0, parent.checkState(0))
            detail.setDisabled(True)
            detail.setToolTip(
                0,
                "This surface belongs to the physical device above. Select or clear the physical device to include or exclude it.",
            )
            parent.addChild(detail)

    def _set_child_check_state(self, item: QTreeWidgetItem) -> None:
        state = item.checkState(0)
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            for nested in range(child.childCount()):
                child.child(nested).setCheckState(0, state)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        identity = item.data(0, _DEVICE_ID_ROLE)
        if not identity:
            return
        identity = str(identity)
        self.tree.blockSignals(True)
        try:
            self._set_child_check_state(item)
        finally:
            self.tree.blockSignals(False)
        if item.checkState(0) == Qt.CheckState.Checked:
            self._session_selected.add(identity)
            self._session_unselected.discard(identity)
        else:
            self._session_selected.discard(identity)
            self._session_unselected.add(identity)
        self._update_selection_count()

    def _update_selection_count(self) -> None:
        selected = sum(
            1
            for item in self._device_items
            if item.checkState(0) == Qt.CheckState.Checked
        )
        total = len(self._device_items)
        self.selection_count.setText(f"{selected} of {total} selected")

    def _header_clicked(self, column: int) -> None:
        if column == self._sort_column:
            order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            order = Qt.SortOrder.AscendingOrder
        self._apply_device_sort(column, order)

    def _apply_device_sort(self, column: int, order: Qt.SortOrder) -> None:
        if not self._device_items:
            return

        items = [
            self.tree.takeTopLevelItem(0)
            for _ in range(self.tree.topLevelItemCount())
        ]
        reverse = order == Qt.SortOrder.DescendingOrder
        items.sort(
            key=lambda item: item.data(column, _SORT_ROLE),
            reverse=reverse,
        )
        for item in items:
            self.tree.addTopLevelItem(item)

        self._sort_column = column
        self._sort_order = order
        self.tree.header().setSortIndicator(column, order)

    def _select_all(self) -> None:
        for item in self._device_items:
            item.setCheckState(0, Qt.CheckState.Checked)
        self._update_selection_count()

    def _unselect_all(self) -> None:
        for item in self._device_items:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        self._update_selection_count()

    def _save_and_accept(self) -> None:
        self._capture_visible_selection()
        self._persist_integrations()
        set_show_device_selector_at_startup(
            self._settings,
            self.show_devices_startup_check.isChecked(),
        )
        set_compatibility_notice_visible(
            self._settings,
            self.show_compatibility_startup_check.isChecked(),
        )

        selected_visible = {
            str(item.data(0, _DEVICE_ID_ROLE))
            for item in self._device_items
            if item.data(0, _DEVICE_ID_ROLE)
            and item.checkState(0) == Qt.CheckState.Checked
        }

        save_device_review(
            self._settings,
            self._candidate_aliases,
            selected_visible,
            self._session_unselected,
        )
        save_device_control_route_review(
            self._settings,
            self._candidate_aliases,
            self._visible_overlap_routes,
        )
        self.accept()


def _header_layout(window):
    central = window.centralWidget()
    outer = central.layout() if central is not None else None
    if outer is None or not outer.count():
        return None
    return outer.itemAt(0).layout()


def apply_lighting_devices_ui(app_module: Any) -> None:
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_lighting_devices_ui_applied", False):
        return

    previous_init = window_class.__init__

    def refresh_lighting_devices(
        self,
        *,
        show_dialog: bool,
        startup: bool = False,
    ) -> LightingDiscoveryResult | None:
        if show_dialog:
            dialog = LightingDevicesDialog(self._settings, self)
            dialog.exec()
            result = dialog._last_result
        else:
            preferences = load_integration_preferences(self._settings)
            result = discover_enabled_candidates(
                preferences,
                load_discovery_order(self._settings, preferences),
            )

        self._last_lighting_discovery = result
        self._last_openrgb_inventory = result.openrgb_inventory

        # Verified native models bootstrap themselves from live discovery. The
        # persisted file is only an optional cache/user overlay; deleting it must
        # never make a supported H61C3 unusable.
        discovered_govee = tuple(
            candidate.govee_device
            for candidate in result.candidates
            if candidate.govee_device is not None
        )
        try:
            saved_govee = load_govee_configuration()
            runtime_govee = with_discovered_verified_devices(
                saved_govee,
                discovered_govee,
            )
            if hasattr(self, "_govee_configuration"):
                self._govee_configuration = runtime_govee
                self._govee_configuration_error = None
        except Exception as exc:
            if hasattr(self, "_govee_configuration_error"):
                self._govee_configuration_error = str(exc)

        if result.errors:
            self.statusBar().showMessage(
                "Lighting discovery completed with warnings.",
                7000,
            )
        else:
            count = len(result.candidates)
            self.statusBar().showMessage(
                f"Lighting discovery found {count} device{'s' if count != 1 else ''}.",
                5000,
            )
        return result

    def show_lighting_devices(self) -> None:
        self.refresh_lighting_devices(show_dialog=True, startup=False)

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)

        button = QPushButton("Lighting devices", self)
        button.setToolTip(
            "Choose discovery layers, rescan and select the devices EDL may use."
        )
        button.setToolTipDuration(30000)
        button.clicked.connect(self.show_lighting_devices)
        self.lighting_devices_button = button

        header = _header_layout(self)
        if header is None:
            button.hide()
        else:
            header.addWidget(button)

    window_class.__init__ = init
    window_class.refresh_lighting_devices = refresh_lighting_devices
    window_class.show_lighting_devices = show_lighting_devices
    window_class._edl_lighting_devices_ui_applied = True


# Rule-target presentation shares the same discovery/selection UI owner.
def apply_openrgb_rule_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_openrgb_rule_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_refresh_devices = window_class.refresh_lighting_devices
    previous_load_draft = window_class._load_draft
    previous_runnable_targets = window_class._runnable_targets
    previous_update_profile_buttons = window_class._update_profile_buttons

    def sync_openrgb_rule_targets(self) -> None:
        selector = getattr(self, "target_combo", None)
        if selector is None or not hasattr(selector, "_ensure_target"):
            return

        discovery = getattr(self, "_last_lighting_discovery", None)
        if not isinstance(discovery, LightingDiscoveryResult):
            discovery = LightingDiscoveryResult(())

        selected = frozenset(selected_device_ids(self._settings))
        bindings = discovery_target_bindings(
            discovery,
            selected,
            selected_only=False,
            control_routes=device_control_routes(self._settings),
        )

        for target, binding in bindings.items():
            selector._ensure_target(target)
            box = selector._boxes[target]
            box.setText(binding.label)
            box.setToolTip(
                "OpenRGB target. Available for new rules only while its physical device is "
                "discovered, selected, and currently owned through OpenRGB in Lighting devices."
            )

        # A saved unavailable target may have been materialized by set_targets()
        # before discovery could resolve it. Give it a readable label without
        # changing the persisted target ID.
        for target, box in tuple(selector._boxes.items()):
            if not is_openrgb_target(target) or target in bindings:
                continue
            text = box.text().strip()
            if text == target or not text:
                box.setText(fallback_target_label(target))

        refresh = getattr(selector, "_refresh_button", None)
        if callable(refresh):
            refresh()

    def refresh_lighting_devices(self, *args, **kwargs):
        result = previous_refresh_devices(self, *args, **kwargs)
        self._edl_sync_openrgb_rule_targets()
        return result

    def load_draft(self, rule, *args, **kwargs) -> None:
        previous_load_draft(self, rule, *args, **kwargs)
        self._edl_sync_openrgb_rule_targets()

    def runnable_targets(self) -> set[str]:
        result = set(previous_runnable_targets(self))
        profile = getattr(self, "_profile", None)
        if profile is None:
            return result
        result.update(
            output.target
            for rule in profile.rules
            if rule.enabled
            for output in rule.outputs
            if output.enabled and is_openrgb_target(output.target)
        )
        return result

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        self._edl_sync_openrgb_rule_targets()

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        QTimer.singleShot(0, self._edl_sync_openrgb_rule_targets)

    window_class.__init__ = init
    window_class.refresh_lighting_devices = refresh_lighting_devices
    window_class._load_draft = load_draft
    window_class._runnable_targets = runnable_targets
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_sync_openrgb_rule_targets = sync_openrgb_rule_targets
    window_class._edl_openrgb_rule_ui_applied = True


# Integration preferences share the same lighting-device presentation owner.
class LightingIntegrationsDialog(QDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Lighting integrations")
        self.setMinimumWidth(680)

        root = QVBoxLayout(self)

        about = QLabel(
            "<b>About lighting compatibility</b><br><br>" + COMPATIBILITY_DETAILS_HTML
        )
        about.setWordWrap(True)
        root.addWidget(about)

        current = load_integration_preferences(settings)

        self.openrgb_check = QCheckBox("OpenRGB — recommended baseline")
        self.openrgb_check.setChecked(current.openrgb_enabled)
        self.openrgb_check.setToolTip(
            "Use OpenRGB as EDL's general compatibility layer for hardware it exposes."
        )
        root.addWidget(self.openrgb_check)

        self.chroma_check = QCheckBox("Razer Chroma — optional")
        self.chroma_check.setChecked(current.chroma_enabled)
        self.chroma_check.setToolTip(
            "Recommended for Razer devices. Also useful as a fallback for some "
            "Chroma-compatible third-party devices that OpenRGB does not expose."
        )
        root.addWidget(self.chroma_check)

        self.govee_check = QCheckBox("Enhanced Govee H61C3 — optional")
        self.govee_check.setChecked(current.govee_h61c3_enabled)
        self.govee_check.setToolTip(
            "Enable EDL's enhanced native path for the physically validated Govee H61C3."
        )
        root.addWidget(self.govee_check)

        startup = QLabel("<b>Startup</b>")
        root.addWidget(startup)

        self.show_notice_check = QCheckBox("Show compatibility message at startup")
        self.show_notice_check.setChecked(not compatibility_notice_hidden(settings))
        root.addWidget(self.show_notice_check)

        self.show_devices_check = QCheckBox("Show device selection after discovery at startup")
        self.show_devices_check.setChecked(show_device_selector_at_startup(settings))
        root.addWidget(self.show_devices_check)

        scan_now = QPushButton("Save choices and scan for lighting devices now")
        scan_now.clicked.connect(self._save_and_scan)
        root.addWidget(scan_now)

        note = QLabel(
            "<b>Your choices are saved under Lighting integrations and can be changed "
            "at any time.</b><br><br>"
            "Discovery reads enabled integrations only. At this stage OpenRGB discovery "
            "is read-only and does not change colors, modes, zone sizes or ownership."
        )
        note.setWordWrap(True)
        note.setProperty("edlTextRole", "secondary")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _persist(self) -> None:
        save_integration_preferences(
            self._settings,
            IntegrationPreferences(
                openrgb_enabled=self.openrgb_check.isChecked(),
                chroma_enabled=self.chroma_check.isChecked(),
                govee_h61c3_enabled=self.govee_check.isChecked(),
            ),
        )
        set_compatibility_notice_visible(
            self._settings,
            self.show_notice_check.isChecked(),
        )
        set_show_device_selector_at_startup(
            self._settings,
            self.show_devices_check.isChecked(),
        )

    def _save(self) -> None:
        self._persist()
        self.accept()

    def _save_and_scan(self) -> None:
        self._persist()
        parent = self.parent()
        refresh = getattr(parent, "refresh_lighting_devices", None)
        if callable(refresh):
            refresh(show_dialog=True, startup=False)


def apply_integration_preferences_ui(app_module: Any) -> None:
    """Add the persisted integration-selection surface to the finished UI."""
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_integration_preferences_ui_applied", False):
        return

    previous_init = window_class.__init__

    def show_lighting_integrations(self) -> None:
        dialog = LightingIntegrationsDialog(self._settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = load_integration_preferences(self._settings)
            enabled = []
            if selected.openrgb_enabled:
                enabled.append("OpenRGB")
            if selected.chroma_enabled:
                enabled.append("Chroma")
            if selected.govee_h61c3_enabled:
                enabled.append("Enhanced Govee H61C3")
            summary = ", ".join(enabled) if enabled else "none"
            self.statusBar().showMessage(
                f"Lighting integration preferences saved: {summary}.",
                5000,
            )

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        button = QPushButton("Lighting integrations", self)
        button.setToolTip(
            "Choose enabled lighting integrations, startup behavior and rescan devices."
        )
        button.setToolTipDuration(30000)
        button.clicked.connect(self.show_lighting_integrations)
        self.lighting_integrations_button = button

        header = _header_layout(self)
        if header is None:
            button.hide()
        else:
            header.addWidget(button)

    window_class.__init__ = init
    window_class.show_lighting_integrations = show_lighting_integrations
    window_class._edl_integration_preferences_ui_applied = True


# Co-located startup compatibility and device-review flow.
HIDE_COMPATIBILITY_NOTICE_KEY = "ui/hide_lighting_compatibility_notice"

COMPATIBILITY_NOTICE_HTML = """
<p>EDL can control lighting through:</p>

<p>
<b>OpenRGB</b> — default<br>
<b>Razer Chroma</b> — recommended for Razer devices<br>
<b>Enhanced Govee H61C3</b> — optional native support
</p>

<p>Make sure the integrations you want to use are available. You can change them later under <b>Setup → Lighting integrations</b>.</p>

<p><b>COVAS:NEXT</b><br>
EDL can connect to COVAS:NEXT using a local connection on this PC. Windows may ask for permission when this connection starts. It is only accessible from your computer and is not exposed to your local network or the Internet.</p>
""".strip()


COMPATIBILITY_DETAILS_HTML = """
<p>EDL can control supported lighting entirely through <b>OpenRGB</b>.
Optional integrations can provide better support for specific hardware.</p>

<ul>
<li><b>Razer devices:</b> Chroma is recommended when available. If you do not use
Chroma/Synapse, EDL can still use Razer devices exposed by OpenRGB.</li>

<li><b>Enhanced Govee:</b> EDL currently provides enhanced native support for the
<b>Govee H61C3</b>. This route provides finer segment control than generic integrations.</li>

<li><b>Chroma-compatible third-party devices:</b> <b>Check OpenRGB first.</b>
If the device appears there and provides the control you need, OpenRGB is normally
preferred. If OpenRGB does not detect the device, Chroma may provide an alternative
route for Chroma-compatible hardware. Third-party Chroma integrations may expose
fewer LEDs or zones than a native or OpenRGB connection.</li>
</ul>

<p><b>Availability required for discovery:</b></p>
<ul>
<li><b>OpenRGB:</b> the OpenRGB service/SDK server must be running. The OpenRGB
window itself may remain closed when the service is providing the SDK server.</li>
<li><b>Razer Chroma:</b> Synapse/Chroma must be available when that integration is enabled.</li>
<li><b>Enhanced Govee H61C3:</b> the light must be powered on and reachable on the network.</li>
</ul>

<p>Your integration choices are saved under <b>Lighting integrations</b> and can
be changed at any time.</p>
""".strip()


def compatibility_notice_hidden(settings) -> bool:
    value = settings.value(HIDE_COMPATIBILITY_NOTICE_KEY, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def set_compatibility_notice_visible(settings, visible: bool) -> None:
    settings.setValue(HIDE_COMPATIBILITY_NOTICE_KEY, not bool(visible))
    settings.sync()


def remember_hide_compatibility_notice(settings) -> None:
    set_compatibility_notice_visible(settings, False)


def show_compatibility_notice(parent, settings) -> bool:
    """Show the notice when enabled. Return True when the notice was displayed."""
    if compatibility_notice_hidden(settings):
        return False

    box = QMessageBox(parent)
    box.setWindowTitle("Before you start")
    box.setIcon(QMessageBox.Icon.Information)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(COMPATIBILITY_NOTICE_HTML)
    popup_pt = BODY_PT + 1.0
    box.setStyleSheet(
        f"QMessageBox QLabel {{ font-size: {popup_pt}pt; }} "
        f"QMessageBox QCheckBox {{ font-size: {popup_pt}pt; }} "
        f"QMessageBox QPushButton {{ font-size: {popup_pt}pt; }}"
    )

    dont_show = QCheckBox("Don't show this message again", box)
    box.setCheckBox(dont_show)
    continue_button = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(continue_button)
    box.exec()

    if dont_show.isChecked():
        remember_hide_compatibility_notice(settings)
    return True


def startup_flow_disabled() -> bool:
    """Test/host escape hatch for constructing the UI without startup side effects."""
    value = os.environ.get("EDL_DISABLE_STARTUP_FLOW", "")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def apply_startup_compatibility_notice(app_module: Any) -> None:
    """Install the bounded startup setup/review flow on the product window.

    The canonical launcher schedules this method only after the real window has
    been shown. Keeping startup orchestration out of the accumulated __init__
    wrapper chain makes the two startup dialogs deterministic and leaves direct
    MainWindow construction (for tests/tools) side-effect free.
    """
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_startup_compatibility_notice_applied", False):
        return

    def run_startup_flow(self) -> None:
        if startup_flow_disabled():
            return
        if getattr(self, "_edl_startup_flow_started", False):
            return
        self._edl_startup_flow_started = True

        show_compatibility_notice(self, self._settings)

        # Start the local COVAS listener only after the first-start message has
        # had the chance to explain why Windows may ask for permission.
        start_covas_bridge = getattr(self, "_start_covas_bridge", None)
        if callable(start_covas_bridge):
            start_covas_bridge()

        # The compatibility QMessageBox and the device chooser are two separate
        # startup modals. On Windows/Qt, opening the second dialog immediately from
        # the same callback that just closed the first can return focus straight to
        # the main window instead. Let the first modal fully unwind, then continue
        # startup on a fresh event-loop turn.
        show_selector = show_device_selector_at_startup(self._settings)

        def continue_startup() -> None:
            self.refresh_lighting_devices(
                show_dialog=show_selector,
                startup=True,
            )

        QTimer.singleShot(100, continue_startup)

    window_class._edl_run_startup_flow = run_startup_flow
    window_class._edl_startup_compatibility_notice_applied = True
