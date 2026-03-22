import asyncio
import json

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

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19
    }).addTo(map);

    var drawnItems   = new L.FeatureGroup().addTo(map);
    var waypointLayer = L.layerGroup().addTo(map);
    var drawHandler  = null;
    var bridge       = null;

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

    // ── functions called from Python via runJavaScript ────────────────────────

    function startDraw() {
      if (drawHandler) drawHandler.disable();
      drawHandler = new L.Draw.Polygon(map, drawControl.options.draw.polygon);
      drawHandler.enable();
    }

    function setWaypoints(wps) {
      // wps is already a JS array passed directly from Python via runJavaScript —
      // do NOT call JSON.parse on it.
      waypointLayer.clearLayers();
      if (!wps || wps.length < 2) return;

      L.polyline(wps, { color: '#2196F3', weight: 1.5, opacity: 0.8 })
       .addTo(waypointLayer);

      wps.forEach(function(wp, i) {
        L.marker(wp, {
          icon: L.divIcon({
            html: '<div style="background:#2196F3;color:#fff;border-radius:50%;'
                + 'width:18px;height:18px;line-height:18px;text-align:center;'
                + 'font-size:9px;font-weight:bold;">' + (i + 1) + '</div>',
            iconSize: [18, 18],
            iconAnchor: [9, 9],
            className: ''
          })
        }).addTo(waypointLayer);
      });
    }

    function clearMap() {
      drawnItems.clearLayers();
      waypointLayer.clearLayers();
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
    """

    # Emitted from asyncio thread, delivered on Qt main thread via queued conn.
    _upload_result = Signal(bool, str)

    def __init__(self, connector: DroneConnector, parent=None):
        super().__init__(parent)
        self._connector = connector
        self._polygon: list = []
        self._offsets: list = []   # (north_m, east_m) from polygon centroid
        self._bridge = _MapBridge(self)

        self._build_ui()

        self._bridge.polygon_received.connect(self._on_polygon)
        self._upload_result.connect(self._on_upload_result)
        bus.vehicle_connected.connect(self._refresh_upload_btn)
        bus.vehicle_disconnected.connect(self._refresh_upload_btn)

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
        self._status_lbl = QLabel("Draw a search area polygon on the map. Mission will be placed at the drone's position on upload.")
        self._status_lbl.setStyleSheet(
            "color: #888; font-size: 11px; padding: 0 8px;"
        )

        root.addLayout(loc_row)
        root.addWidget(self._web, stretch=1)
        root.addWidget(ctrl_box)
        root.addWidget(self._status_lbl)

    # ── slots ─────────────────────────────────────────────────────────────────

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
        self._set_status("Draw a search area polygon on the map. Mission will be placed at the drone's position on upload.")

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
            await upload_mission(self._connector.drone, offsets)

        self._upload_btn.setEnabled(False)
        self._set_status("Fetching drone position and uploading mission…")
        future = asyncio.run_coroutine_threadsafe(_upload(), self._connector.loop)
        future.add_done_callback(self._on_upload_done)

    def _on_upload_done(self, future) -> None:
        """Called from asyncio thread — emit signal for Qt-thread delivery."""
        try:
            future.result()
            n = len(self._offsets)
            self._upload_result.emit(
                True, f"Uploaded {n} waypoints + geofence (anchored at drone position)."
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

    # ── helpers ───────────────────────────────────────────────────────────────

    def _generate_and_display(self, spacing_m: int) -> None:
        polygon_tuples = [tuple(p) for p in self._polygon]
        self._offsets = generate_lawnmower(polygon_tuples, float(spacing_m))
        # Preview on the map uses polygon centroid as a stand-in for drone position.
        # Actual upload will reanchor at the drone's real GPS position.
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

    def _set_status(self, msg: str, error: bool = False) -> None:
        color = "#f44336" if error else "#888"
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; padding: 0 8px;"
        )
        self._status_lbl.setText(msg)
