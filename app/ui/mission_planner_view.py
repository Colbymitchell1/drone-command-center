import asyncio
import json
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.events.event_bus import bus
from app.services.sim_controller import SIM_HOME_LAT, SIM_HOME_LON
from integrations.mavsdk.connector import DroneConnector
from mission.planning.lawnmower import generate_lawnmower, offsets_to_latlon, polygon_center


# ── Embedded map HTML ─────────────────────────────────────────────────────────

_MAP_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css"/>
  <style>
    * { margin: 0; padding: 0; }
    html, body { width: 100%; height: 100%; background: #1a1a1a; }
    #map { width: 100%; height: 100%; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script>
    var map = L.map('map').setView([$HOME_LAT, $HOME_LON], 14);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      maxZoom: 19
    }).addTo(map);

    // ── Layer groups — order determines z-order (later = on top) ─────────────
    var drawnItems    = new L.FeatureGroup().addTo(map);  // polygon
    var waypointLayer = L.layerGroup().addTo(map);        // planned path + markers
    var completedLayer = L.layerGroup().addTo(map);       // flown segments overlay
    var droneLayer    = L.layerGroup().addTo(map);        // live drone position

    var drawHandler = null;
    var bridge      = null;

    // Mission state
    var waypointMarkers = [];   // L.Marker per waypoint, indexed to match wps[]
    var currentWps      = [];   // last wps array passed to setWaypoints
    var droneMarker     = null; // single live-position marker

    // ── Draw control ──────────────────────────────────────────────────────────
    var drawControl = new L.Control.Draw({
      draw: {
        polygon:      { allowIntersection: false, showArea: true },
        polyline:     false,
        rectangle:    false,
        circle:       false,
        circlemarker: false,
        marker:       false
      },
      edit: { featureGroup: drawnItems }
    });
    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, function(e) {
      drawnItems.clearLayers();
      drawnItems.addLayer(e.layer);
      var verts = e.layer.getLatLngs()[0].map(function(ll) {
        return [ll.lat, ll.lng];
      });
      if (bridge) bridge.polygonDrawn(JSON.stringify(verts));
    });

    // ── Icon factories ────────────────────────────────────────────────────────

    // Numbered circle for regular waypoints
    function _wpIcon(label, color) {
      return L.divIcon({
        html: '<div style="background:' + color + ';color:#fff;border-radius:50%;'
            + 'width:18px;height:18px;line-height:18px;text-align:center;'
            + 'font-size:9px;font-weight:bold;">' + label + '</div>',
        iconSize: [18, 18],
        iconAnchor: [9, 9],
        className: ''
      });
    }

    // House icon for the start/home waypoint (index 0)
    function _homeIcon(color) {
      return L.divIcon({
        html: '<div style="background:' + color + ';color:#fff;border-radius:3px;'
            + 'width:22px;height:22px;line-height:22px;text-align:center;'
            + 'font-size:14px;box-shadow:0 0 3px rgba(0,0,0,0.5);">&#8962;</div>',
        iconSize: [22, 22],
        iconAnchor: [11, 11],
        className: ''
      });
    }

    // Small directional arrow placed at segment midpoints
    function _arrowIcon(bearing) {
      return L.divIcon({
        html: '<div style="transform:rotate(' + bearing + 'deg);'
            + 'color:#2196F3;font-size:10px;width:12px;height:12px;'
            + 'line-height:12px;text-align:center;opacity:0.85;">&#9650;</div>',
        iconSize: [12, 12],
        iconAnchor: [6, 6],
        className: ''
      });
    }

    // Drone marker: SVG arrow pointing north, rotated by heading via CSS transform
    function _droneIcon(heading) {
      return L.divIcon({
        html: '<div style="width:26px;height:26px;">'
            + '<svg style="transform:rotate(' + heading + 'deg);'
            + 'transform-origin:center center;" width="26" height="26" viewBox="0 0 26 26">'
            + '<polygon points="13,2 21,24 13,18 5,24" '
            + 'fill="#00BCD4" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>'
            + '</svg></div>',
        iconSize: [26, 26],
        iconAnchor: [13, 13],
        className: ''
      });
    }

    // ── Functions called from Python via runJavaScript ────────────────────────

    function startDraw() {
      if (drawHandler) drawHandler.disable();
      drawHandler = new L.Draw.Polygon(map, drawControl.options.draw.polygon);
      drawHandler.enable();
    }

    // Draw the planned flight path with direction arrows, a home marker at
    // index 0, and numbered circles for the remaining waypoints.
    // wps is a JS array of [lat, lon] pairs — do NOT JSON.parse it.
    function setWaypoints(wps) {
      waypointLayer.clearLayers();
      completedLayer.clearLayers();
      waypointMarkers = [];
      currentWps = wps ? wps.slice() : [];

      if (!wps || wps.length < 2) return;

      // Planned path line
      L.polyline(wps, { color: '#2196F3', weight: 2, opacity: 0.75 })
       .addTo(waypointLayer);

      // Direction arrow at the midpoint of each segment
      for (var i = 0; i < wps.length - 1; i++) {
        var midLat  = (wps[i][0] + wps[i+1][0]) / 2;
        var midLon  = (wps[i][1] + wps[i+1][1]) / 2;
        var dLat    = wps[i+1][0] - wps[i][0];
        var dLon    = wps[i+1][1] - wps[i][1];
        // atan2(dLon, dLat) gives bearing clockwise from north — matches CSS rotate
        var bearing = Math.atan2(dLon, dLat) * 180 / Math.PI;
        L.marker([midLat, midLon], {
          icon: _arrowIcon(bearing),
          interactive: false
        }).addTo(waypointLayer);
      }

      // Waypoint markers: home icon for index 0, numbered circles for the rest
      wps.forEach(function(wp, i) {
        var icon = (i === 0) ? _homeIcon('#4caf50') : _wpIcon(i, '#2196F3');
        var m = L.marker(wp, { icon: icon }).addTo(waypointLayer);
        waypointMarkers.push(m);
      });
    }

    // Highlight the current target waypoint in orange.
    // Called each time progress.current advances during a mission.
    function setActiveWaypoint(index) {
      if (waypointMarkers.length === 0 || index < 0 || index >= waypointMarkers.length) return;
      waypointMarkers[index].setIcon(
        index === 0 ? _homeIcon('#ff9800') : _wpIcon(index, '#ff9800')
      );
    }

    // Mark a waypoint as completed: grey the marker and draw a grey overlay
    // segment from that waypoint to the next, showing coverage progress.
    function markWaypointComplete(index) {
      if (index >= 0 && index < waypointMarkers.length) {
        waypointMarkers[index].setIcon(
          index === 0 ? _homeIcon('#616161') : _wpIcon(index, '#616161')
        );
      }
      // Overlay the completed segment (index → index+1) in grey on top of the blue path
      if (index >= 0 && index + 1 < currentWps.length) {
        L.polyline([currentWps[index], currentWps[index + 1]], {
          color: '#9e9e9e', weight: 3, opacity: 0.8
        }).addTo(completedLayer);
      }
    }

    // Reset waypoint markers to their original colours and clear completed overlays.
    // Called on mission complete, abort, or when a new mission starts.
    function clearMissionState() {
      completedLayer.clearLayers();
      waypointMarkers.forEach(function(m, i) {
        m.setIcon(i === 0 ? _homeIcon('#4caf50') : _wpIcon(i, '#2196F3'));
      });
    }

    // Update (or create) the live drone position marker.
    // heading is degrees clockwise from north — applied as a CSS rotation.
    function updateDroneMarker(lat, lon, heading) {
      var icon = _droneIcon(heading);
      if (!droneMarker) {
        droneMarker = L.marker([lat, lon], { icon: icon, zIndexOffset: 1000 });
        droneMarker.addTo(droneLayer);
      } else {
        droneMarker.setLatLng([lat, lon]);
        droneMarker.setIcon(icon);
      }
    }

    // Remove the drone marker (called on vehicle disconnect).
    function hideDroneMarker() {
      if (droneMarker) {
        droneLayer.removeLayer(droneMarker);
        droneMarker = null;
      }
    }

    // Clear polygon, planned path, and completed overlays.
    // Drone marker is intentionally preserved — it reflects live vehicle state.
    function clearMap() {
      drawnItems.clearLayers();
      waypointLayer.clearLayers();
      completedLayer.clearLayers();
      waypointMarkers = [];
      currentWps = [];
    }

    // Programmatically set a polygon (e.g. from AI assist).
    // Clears any previously drawn polygon and zooms the map to fit.
    function setPolygon(verts) {
      drawnItems.clearLayers();
      if (!verts || verts.length < 3) return;
      var poly = L.polygon(verts, { color: '#3388ff', weight: 2, fillOpacity: 0.08 });
      drawnItems.addLayer(poly);
      map.fitBounds(poly.getBounds());
    }

    function setCenter(lat, lon) {
      map.setView([lat, lon], 14);
    }

    // ── QWebChannel bridge setup ──────────────────────────────────────────────
    new QWebChannel(qt.webChannelTransport, function(channel) {
      bridge = channel.objects.bridge;
    });
  </script>
</body>
</html>"""

_MAP_HTML = (
    _MAP_HTML_TEMPLATE
    .replace("$HOME_LAT", str(SIM_HOME_LAT))
    .replace("$HOME_LON", str(SIM_HOME_LON))
)


# ── JS→Python bridge ──────────────────────────────────────────────────────────

class _MapBridge(QObject):
    """Registered with QWebChannel; JS calls its slots directly."""
    polygon_received = Signal(list)  # list of [lat, lon]

    @Slot(str)
    def polygonDrawn(self, vertices_json: str) -> None:
        self.polygon_received.emit(json.loads(vertices_json))


# ── MissionPlannerView ────────────────────────────────────────────────────────

class MissionPlannerView(QWidget):
    """
    Full-panel mission planner: Leaflet map with draw tools, lawnmower
    pattern generation, and MAVSDK geofence + mission upload.

    Map logic lives entirely in HTML/JS. Python owns geometry and upload.

    Live features:
    - Drone marker updated from bus.telemetry_updated at 4 Hz
    - Active waypoint highlighted via bus.waypoint_advanced
    - Completed path segments overlaid as drone progresses through mission
    """

    # Emitted from asyncio thread, delivered on Qt main thread via queued conn.
    _upload_result = Signal(bool, str)
    _ai_result = Signal(object)   # dict on success, str on error

    def __init__(self, connector: DroneConnector, ai_service=None, parent=None):
        super().__init__(parent)
        self._connector = connector
        self._ai_service = ai_service
        self._polygon: list = []
        self._offsets: list = []   # (north_m, east_m) from polygon centroid
        self._bridge = _MapBridge(self)
        self._last_drone_pos: Optional[tuple[float, float]] = None

        self._build_ui()

        self._bridge.polygon_received.connect(self._on_polygon)
        self._upload_result.connect(self._on_upload_result)
        self._ai_result.connect(self._on_ai_result)

        # Upload button enablement
        bus.vehicle_connected.connect(self._refresh_upload_btn)
        bus.vehicle_disconnected.connect(self._refresh_upload_btn)

        # Live drone marker
        bus.telemetry_updated.connect(self._on_telemetry_map)
        bus.vehicle_disconnected.connect(self._on_vehicle_disconnected_map)

        # Mission state on map
        bus.mission_waypoints_ready.connect(self._on_waypoints_ready)
        bus.mission_started.connect(self._on_mission_started_map)
        bus.mission_completed.connect(self._on_mission_ended_map)
        bus.mission_aborted.connect(lambda _: self._on_mission_ended_map())
        bus.waypoint_advanced.connect(self._on_waypoint_advanced)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(4)

        # Location bar
        loc_row = QHBoxLayout()
        loc_row.setContentsMargins(8, 0, 8, 0)
        self._loc_input = QLineEdit()
        self._loc_input.setPlaceholderText("lat, lon  —  e.g. 32.923, -117.259")
        self._loc_input.returnPressed.connect(self._on_go)
        go_btn = QPushButton("Go")
        go_btn.setFixedWidth(56)
        go_btn.clicked.connect(self._on_go)
        loc_row.addWidget(QLabel("Location:"))
        loc_row.addWidget(self._loc_input)
        loc_row.addWidget(go_btn)

        # Map
        self._web = QWebEngineView()
        channel = QWebChannel(self._web.page())
        channel.registerObject("bridge", self._bridge)
        self._web.page().setWebChannel(channel)
        self._web.setHtml(_MAP_HTML, QUrl("qrc:/"))

        # Controls bar
        ctrl_box = QGroupBox()
        ctrl_row = QHBoxLayout(ctrl_box)
        ctrl_row.setContentsMargins(8, 6, 8, 6)

        ctrl_row.addWidget(QLabel("Leg spacing:"))
        self._spacing_spin = QSpinBox()
        self._spacing_spin.setRange(5, 500)
        self._spacing_spin.setValue(20)
        self._spacing_spin.setSuffix(" m")
        self._spacing_spin.setFixedWidth(76)
        self._spacing_spin.valueChanged.connect(self._on_spacing_changed)
        ctrl_row.addWidget(self._spacing_spin)

        ctrl_row.addStretch()

        draw_btn = QPushButton("Draw Search Area")
        draw_btn.setFixedWidth(148)
        draw_btn.clicked.connect(self._on_draw)
        ctrl_row.addWidget(draw_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(72)
        self._clear_btn.clicked.connect(self._on_clear)
        ctrl_row.addWidget(self._clear_btn)

        self._upload_btn = QPushButton("Upload Mission")
        self._upload_btn.setFixedWidth(140)
        self._upload_btn.setEnabled(False)
        self._upload_btn.clicked.connect(self._on_upload)
        ctrl_row.addWidget(self._upload_btn)

        # Status bar
        self._status_lbl = QLabel(
            "Draw a search area polygon on the map. "
            "Mission will be placed at the drone's position on upload."
        )
        self._status_lbl.setStyleSheet("color: #888; font-size: 11px; padding: 0 8px;")

        # AI Mission Assist panel
        ai_box = QGroupBox("AI Mission Assist")
        ai_row = QHBoxLayout(ai_box)
        ai_row.setContentsMargins(8, 6, 8, 6)
        ai_row.setSpacing(8)

        self._ai_input = QLineEdit()
        self._ai_input.setPlaceholderText("Describe your search mission…")
        self._ai_input.returnPressed.connect(self._on_generate_mission)

        self._ai_btn = QPushButton("Generate Mission")
        self._ai_btn.setFixedWidth(148)
        self._ai_btn.clicked.connect(self._on_generate_mission)

        self._ai_status = QLabel()
        self._ai_status.setStyleSheet("color: #888; font-size: 11px;")

        ai_row.addWidget(QLabel("Mission:"))
        ai_row.addWidget(self._ai_input, stretch=1)
        ai_row.addWidget(self._ai_btn)
        ai_row.addWidget(self._ai_status)

        # Show unavailable state immediately if no backend is ready
        self._refresh_ai_panel()

        root.addLayout(loc_row)
        root.addWidget(ai_box)
        root.addWidget(self._web, stretch=1)
        root.addWidget(ctrl_box)
        root.addWidget(self._status_lbl)

    # ── planning slots ────────────────────────────────────────────────────────

    def _on_go(self) -> None:
        text = self._loc_input.text().strip()
        try:
            lat_s, lon_s = text.split(",", 1)
            lat, lon = float(lat_s.strip()), float(lon_s.strip())
            self._web.page().runJavaScript(f"setCenter({lat}, {lon});")
        except ValueError:
            self._set_status("Invalid coordinates — use:  lat, lon", error=True)

    def _on_draw(self) -> None:
        self._web.page().runJavaScript("startDraw();")

    def _on_clear(self) -> None:
        self._polygon = []
        self._offsets = []
        self._web.page().runJavaScript("clearMap();")
        self._refresh_upload_btn()
        self._set_status(
            "Draw a search area polygon on the map. "
            "Mission will be placed at the drone's position on upload."
        )

    def _on_polygon(self, vertices: list) -> None:
        self._polygon = vertices
        self._generate_and_display(self._spacing_spin.value())

    def _on_spacing_changed(self, value: int) -> None:
        if self._polygon:
            self._generate_and_display(value)

    def _on_upload(self) -> None:
        if not self._connector.drone or not self._connector.loop:
            self._set_status("No drone connected.", error=True)
            return

        from mission.planning.uploader import upload_geofence, upload_mission

        polygon = [tuple(p) for p in self._polygon]
        offsets = list(self._offsets)

        async def _upload():
            await upload_geofence(self._connector.drone, polygon)
            return await upload_mission(self._connector.drone, offsets)

        self._upload_btn.setEnabled(False)
        self._set_status("Fetching drone position and uploading mission…")
        future = asyncio.run_coroutine_threadsafe(_upload(), self._connector.loop)
        future.add_done_callback(self._on_upload_done)

    def _on_upload_done(self, future) -> None:
        """Called from asyncio thread — emit signal for Qt-thread delivery."""
        try:
            waypoints = future.result()   # List[LatLon] — GPS-anchored actual positions
            bus.mission_waypoints_ready.emit(waypoints)
            self._upload_result.emit(
                True,
                f"Uploaded {len(waypoints)} waypoints + geofence (anchored at drone position).",
            )
        except Exception as e:
            self._upload_result.emit(False, f"Upload failed: {e}")

    @Slot(bool, str)
    def _on_upload_result(self, success: bool, msg: str) -> None:
        self._set_status(msg, error=not success)
        if success:
            bus.mission_uploaded.emit()
        else:
            self._refresh_upload_btn()

    # ── live map slots ────────────────────────────────────────────────────────

    def _on_waypoints_ready(self, waypoints: list) -> None:
        """
        Redraw map markers at the actual GPS-anchored coordinates returned by
        upload_mission().  Replaces the centroid-preview positions so the map
        matches exactly what was uploaded to the drone.
        """
        self._web.page().runJavaScript(f"setWaypoints({json.dumps(waypoints)});")

    def _on_telemetry_map(self, data: dict) -> None:
        """Update drone position marker on every telemetry tick (4 Hz)."""
        lat     = data.get("lat")
        lon     = data.get("lon")
        heading = data.get("heading")
        if isinstance(lat, float) and isinstance(lon, float):
            self._last_drone_pos = (lat, lon)
            if isinstance(heading, float):
                self._web.page().runJavaScript(
                    f"updateDroneMarker({lat}, {lon}, {heading});"
                )

    def _on_vehicle_disconnected_map(self) -> None:
        self._web.page().runJavaScript("hideDroneMarker();")

    def _on_mission_started_map(self) -> None:
        """Reset any leftover mission state and highlight the first waypoint."""
        self._web.page().runJavaScript("clearMissionState();")
        self._web.page().runJavaScript("setActiveWaypoint(0);")

    def _on_mission_ended_map(self) -> None:
        """Restore all waypoint markers to their resting colours on finish/abort."""
        self._web.page().runJavaScript("clearMissionState();")

    def _on_waypoint_advanced(self, index: int) -> None:
        """
        progress.current advanced — 'index' is the next waypoint we're flying toward.
        Highlight it orange and shade the segment just completed.
        """
        self._web.page().runJavaScript(f"setActiveWaypoint({index});")
        if index > 0:
            # Mark the waypoint we just left as complete; draw the flown segment
            self._web.page().runJavaScript(f"markWaypointComplete({index - 1});")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _generate_and_display(self, spacing_m: int) -> None:
        polygon_tuples = [tuple(p) for p in self._polygon]
        self._offsets = generate_lawnmower(polygon_tuples, float(spacing_m))
        # Preview uses polygon centroid as a stand-in for drone position.
        # Actual upload will re-anchor at the drone's real GPS position.
        center = polygon_center(polygon_tuples)
        preview_wps = offsets_to_latlon(center, self._offsets)
        self._web.page().runJavaScript(
            f"setWaypoints({json.dumps(preview_wps)});"
        )
        n = len(self._offsets)
        self._set_status(
            f"{n} waypoints generated  ({spacing_m} m spacing).  "
            f"Pattern will be anchored at drone position on upload."
        )
        self._refresh_upload_btn()

    def _refresh_upload_btn(self) -> None:
        self._upload_btn.setEnabled(
            bool(self._offsets) and self._connector.drone is not None
        )

    # ── public accessors (used by MissionPanel for preflight) ─────────────────

    @property
    def offsets(self) -> list:
        """Current (north_m, east_m) offset list from the drawn polygon."""
        return list(self._offsets)

    @property
    def spacing_m(self) -> float:
        """Current leg spacing in metres."""
        return float(self._spacing_spin.value())

    # ── AI assist ─────────────────────────────────────────────────────────────

    def _refresh_ai_panel(self) -> None:
        """Show or hide the AI input row based on backend availability."""
        available = bool(self._ai_service and self._ai_service.available)
        self._ai_input.setVisible(available)
        self._ai_btn.setVisible(available)
        if not available:
            self._ai_status.setText("AI unavailable — draw mission manually")
        else:
            self._ai_status.setText("")

    def _on_generate_mission(self) -> None:
        # Re-check at call time in case background init just finished
        if not self._ai_service or not self._ai_service.available:
            self._refresh_ai_panel()
            return

        desc = self._ai_input.text().strip()
        if not desc:
            self._ai_status.setText("Enter a mission description first.")
            return

        if not self._connector.loop:
            self._ai_status.setText("No event loop — connect a drone first.")
            return

        self._ai_btn.setEnabled(False)
        self._ai_status.setText("Thinking…")

        pos = self._last_drone_pos
        future = asyncio.run_coroutine_threadsafe(
            self._ai_service.assistant.assist_mission(desc, pos),
            self._connector.loop,
        )
        future.add_done_callback(self._on_ai_future_done)

    def _on_ai_future_done(self, future) -> None:
        """Called from asyncio thread — forward to Qt thread via signal."""
        try:
            result = future.result()
        except Exception as e:
            result = str(e)
        self._ai_result.emit(result)

    @Slot(object)
    def _on_ai_result(self, result) -> None:
        self._ai_btn.setEnabled(True)

        if isinstance(result, str):
            # Error message
            self._ai_status.setText(f"Error: {result}")
            return

        try:
            polygon = result["polygon"]
            spacing_m = float(result["leg_spacing_m"])
        except (KeyError, TypeError, ValueError) as e:
            self._ai_status.setText(f"Invalid AI response: {e}")
            return

        # Populate the polygon and generate the lawnmower pattern
        self._polygon = polygon
        self._spacing_spin.setValue(int(max(5, min(500, spacing_m))))
        self._web.page().runJavaScript(f"setPolygon({json.dumps(polygon)});")
        self._generate_and_display(self._spacing_spin.value())
        self._ai_status.setText(
            f"Mission generated ({len(polygon)}-vertex polygon, {spacing_m:.0f} m legs)."
        )

    def _set_status(self, msg: str, error: bool = False) -> None:
        color = "#f44336" if error else "#888"
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; padding: 0 8px;"
        )
        self._status_lbl.setText(msg)
