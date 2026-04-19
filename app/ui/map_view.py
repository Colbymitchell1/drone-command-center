"""MapView — standalone QWebEngineView wrapper for the mission map.

Provides a clean Python API for all map operations.  The underlying
map implementation (CesiumJS) lives entirely inside the HTML; callers
use the methods below.

Polygon completion flow (no QWebChannel dependency):
    1. JS sets window.lastPolygon = [[lat,lon], ...] when the operator
       closes a drawn polygon (vertices are angular-sorted before storing).
    2. A Python QTimer polls window.lastPolygon every 500 ms while draw
       mode is active.  When a non-null value is returned Python emits
       polygon_received and resets window.lastPolygon to null.

Waypoint edit flow:
    1. JS sets window.lastWaypointEdit = [[lat,lon], ...] after a waypoint
       is moved or deleted via the right-click context menu.
    2. A Python QTimer polls window.lastWaypointEdit every 300 ms always.
       When a non-null value is found Python emits waypoint_edit_received
       and resets window.lastWaypointEdit to null.

JS functions exposed by the HTML:
    updateDroneMarker(lat, lon, heading)
    setPolygon(verts)          — array of [lat, lon]
    setWaypoints(waypoints)    — array of [lat, lon]
    setActiveWaypoint(index)
    markWaypointComplete(index)
    clearMissionState()
    clearMap()
    enableDrawMode()
    enableBoxDrawMode()
    hideDroneMarker()
    setCenter(lat, lon)
    toggleMapStyle()
"""

import json
import sys

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.services.sim_controller import SIM_HOME_LAT, SIM_HOME_LON

# ── Cesium Ion token ───────────────────────────────────────────────────────────

_CESIUM_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJqdGkiOiI4NTlkZjkxMS1hZmExLTQ0ZjUtYmQyMi1mYmRkZDgzOTFkMGIiLCJpZCI6NDA4MTg0LCJpYXQiOjE3NzQzMjI0MTh9"
    ".Rks3jLoRTq-5q-1OPSlPZl3oM6rXdML3Q9smL-9tdj4"
)

# ── JS console → Python terminal ───────────────────────────────────────────────

class _DebugPage(QWebEnginePage):
    """QWebEnginePage that pipes JavaScript console output to Python's stderr."""

    _LEVELS = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel:    "INFO ",
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: "WARN ",
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:   "ERROR",
    }

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        tag = self._LEVELS.get(level, "LOG  ")
        src = source_id.split("/")[-1] if source_id else "?"
        print(f"[MAP JS {tag}] {src}:{line_number}  {message}", file=sys.stderr, flush=True)


# ── Map HTML (CesiumJS dark tactical) ──────────────────────────────────────────

_MAP_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <link rel="stylesheet"
    href="https://cesium.com/downloads/cesiumjs/releases/1.116/Build/Cesium/Widgets/widgets.css"/>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 100%; height: 100%; background: #060b10; overflow: hidden; }
    #cesiumContainer { width: 100%; height: 100%; }

    /* ── Loading / error overlay ──────────────────────────────────── */
    #overlay {
      position: absolute; top: 0; left: 0; right: 0; bottom: 0;
      background: #060b10;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      font-family: "Segoe UI", system-ui, sans-serif;
      font-size: 13px;
      z-index: 999;
      gap: 14px;
    }
    #overlay-spinner {
      width: 32px; height: 32px;
      border: 3px solid #30363d;
      border-top-color: #00d4ff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    #overlay-msg  { color: #8b949e; }
    #overlay-err  { color: #da3633; max-width: 480px; text-align: center;
                    word-break: break-word; white-space: pre-wrap; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Strip Cesium chrome, keep navigation help button ─────────── */
    .cesium-viewer-toolbar           { display: none !important; }
    .cesium-viewer-animationContainer{ display: none !important; }
    .cesium-viewer-timelineContainer { display: none !important; }
    .cesium-viewer-fullscreenContainer{ display: none !important; }
    .cesium-credit-logoContainer     { display: none !important; }
    .cesium-credit-textContainer     { display: none !important; }
    .cesium-widget-credits           { display: none !important; }
    .cesium-viewer-bottom            { display: none !important; }
    /* Navigation help button (top-right ?) is intentionally visible */

    /* ── Controls hint ────────────────────────────────────────────── */
    #controls-hint {
      position: absolute; bottom: 12px; left: 12px;
      background: rgba(6, 11, 16, 0.82);
      border: 1px solid rgba(0, 212, 255, 0.18);
      border-radius: 6px;
      padding: 6px 10px;
      font-family: "Segoe UI", system-ui, sans-serif;
      font-size: 11px;
      color: #8b949e;
      display: flex; align-items: center; gap: 10px;
      z-index: 100;
      pointer-events: auto;
      user-select: none;
    }
    #controls-hint button {
      background: none; border: none; color: #484f58;
      font-size: 13px; cursor: pointer; padding: 0 2px; line-height: 1;
    }
    #controls-hint button:hover { color: #8b949e; }
  </style>
</head>
<body>
  <div id="cesiumContainer"></div>
  <div id="overlay">
    <div id="overlay-spinner"></div>
    <div id="overlay-msg">Loading tactical map…</div>
    <div id="overlay-err"></div>
  </div>
  <div id="controls-hint">
    🖱 Scroll: zoom &nbsp;·&nbsp; Drag: pan &nbsp;·&nbsp; Ctrl+Drag: rotate/tilt
    <button onclick="dismissHint()" title="Dismiss">✕</button>
  </div>

  <script src="https://cesium.com/downloads/cesiumjs/releases/1.116/Build/Cesium/Cesium.js"></script>
  <script>
    // ── Global error catcher ──────────────────────────────────────────────────
    window.onerror = function(msg, src, line, col, err) {
      var text = (err ? err.toString() : msg) + '\n(' + src + ':' + line + ')';
      console.error('Uncaught: ' + text);
      _showError(text);
      return false;
    };
    window.addEventListener('unhandledrejection', function(ev) {
      var text = 'Unhandled promise rejection: ' + (ev.reason || ev);
      console.error(text);
      _showError(text);
    });

    function _showError(text) {
      var el = document.getElementById('overlay-err');
      if (el) el.textContent = text;
      var sp = document.getElementById('overlay-spinner');
      if (sp) sp.style.display = 'none';
      var msg = document.getElementById('overlay-msg');
      if (msg) msg.style.display = 'none';
      var ov = document.getElementById('overlay');
      if (ov) ov.style.display = 'flex';
    }

    function _hideOverlay() {
      var ov = document.getElementById('overlay');
      if (ov) ov.style.display = 'none';
    }

    // ── Controls hint ─────────────────────────────────────────────────────────
    function _initHint() {
      if (localStorage.getItem('mapHintDismissed') === '1') {
        document.getElementById('controls-hint').style.display = 'none';
      }
    }
    function dismissHint() {
      localStorage.setItem('mapHintDismissed', '1');
      document.getElementById('controls-hint').style.display = 'none';
    }

    // ── Poll variables (read by Python QTimers) ───────────────────────────────
    window.lastPolygon     = null;  // set on draw close; polled by Python
    window.lastWaypointEdit = null; // set on wp move/delete; polled by Python

    // ── Global state ──────────────────────────────────────────────────────────
    var viewer          = null;
    var mapMode         = 'dark';

    // Draw mode (polygon)
    var drawingPoints    = [];
    var drawHandler      = null;
    var drawMarkers      = [];
    var drawPreviewLine  = null;  // open path: vertex 0 … vertex N
    var drawClosingLine  = null;  // closing segment: vertex N → vertex 0

    // Box AOI draw mode
    var boxHandler       = null;
    var boxFirstCorner   = null;  // {lat, lon} — anchor set on LEFT_DOWN
    var boxCurrentCorner = null;  // {lat, lon} — live corner, updated on MOUSE_MOVE
    var boxIsDragging    = false; // true between LEFT_DOWN and LEFT_UP
    var boxPreviewEntity = null;

    // Waypoints
    var polygonEntity    = null;
    var pathPolyline     = null;
    var waypointEntities = [];
    var waypointLatLons  = [];   // [[lat, lon], ...] parallel to waypointEntities

    // Drone
    var droneEntity      = null;

    // Selection & interaction
    var _selectedWpIndex    = -1;
    var _selectedWpRing     = null;
    var _draggingWpIndex    = -1;
    var _ctxMenuWpIndex     = -1;
    var _interactionHandler = null;

    // Lazy-created floating UI elements
    var _ctxMenuEl  = null;
    var _tooltipEl  = null;

    var _WP_ALT = 25;

    // ── Bootstrap ─────────────────────────────────────────────────────────────
    if (typeof Cesium === 'undefined') {
      _showError('Cesium library failed to load.\nCheck network access or CDN URL.');
    } else {
      console.log('[cesium] Cesium loaded, version: ' + Cesium.VERSION);
      Cesium.Ion.defaultAccessToken = 'CESIUM_TOKEN';
      _initViewer();
    }

    async function _initViewer() {
      console.log('[cesium] _initViewer start');
      try {
        var terrain;
        try {
          terrain = await Cesium.CesiumTerrainProvider.fromIonAssetId(1);
          console.log('[cesium] Terrain loaded');
        } catch (e) {
          console.warn('[cesium] Terrain failed, using ellipsoid: ' + e);
          terrain = new Cesium.EllipsoidTerrainProvider();
        }

        viewer = new Cesium.Viewer('cesiumContainer', {
          terrainProvider:                      terrain,
          animation:                            false,
          baseLayerPicker:                      false,
          fullscreenButton:                     false,
          geocoder:                             false,
          homeButton:                           false,
          infoBox:                              false,
          sceneModePicker:                      false,
          selectionIndicator:                   false,
          timeline:                             false,
          navigationHelpButton:                 true,
          navigationInstructionsInitiallyVisible: false,
          imageryProvider:                      false,
          skyBox:                               false,
          skyAtmosphere:                        new Cesium.SkyAtmosphere(),
        });

        viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#060b10');
        viewer.scene.globe.baseColor  = Cesium.Color.fromCssColorString('#0a1628');
        viewer.scene.globe.enableLighting       = false;
        viewer.scene.globe.showGroundAtmosphere = false;

        // ── Camera controls — Google Earth style ──────────────────────────
        var ctrl = viewer.scene.screenSpaceCameraController;
        ctrl.enableRotate = ctrl.enableTranslate = ctrl.enableZoom = true;
        ctrl.enableTilt   = ctrl.enableLook      = ctrl.enableInputs = true;
        ctrl.inertiaSpin  = 0.9;
        ctrl.translateEventTypes = Cesium.CameraEventType.LEFT_DRAG;
        var rotateTilt = [
          Cesium.CameraEventType.MIDDLE_DRAG,
          { eventType: Cesium.CameraEventType.LEFT_DRAG,
            modifier:  Cesium.KeyboardEventModifier.CTRL }
        ];
        ctrl.rotateEventTypes = rotateTilt;
        ctrl.tiltEventTypes   = rotateTilt;
        ctrl.zoomEventTypes   = [Cesium.CameraEventType.WHEEL, Cesium.CameraEventType.PINCH];

        viewer.cesiumWidget.canvas.setAttribute('tabindex', '0');
        viewer.cesiumWidget.canvas.addEventListener('click', function() {
          viewer.cesiumWidget.canvas.focus();
        });

        viewer.scene.useDepthPicking      = true;
        viewer.scene.pickTranslucentDepth = true;

        // ── Dark base layer ───────────────────────────────────────────────
        viewer.imageryLayers.addImageryProvider(
          new Cesium.UrlTemplateImageryProvider({
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
            subdomains: ['a', 'b', 'c', 'd'],
            maximumLevel: 19,
            credit: new Cesium.Credit(
              '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
              'contributors © <a href="https://carto.com/attributions">CARTO</a>'
            )
          })
        );

        // ── Default camera — top-down 1500 m, north up ────────────────────
        viewer.camera.setView({
          destination: Cesium.Cartesian3.fromDegrees(HOME_LON, HOME_LAT, 1500),
          orientation: { heading: 0.0, pitch: Cesium.Math.toRadians(-90), roll: 0.0 }
        });

        _startInteractionHandler();
        _hideOverlay();
        _initHint();
        console.log('[cesium] Init complete');

      } catch (err) {
        console.error('[cesium] Init failed: ' + err);
        _showError('Map init failed:\n' + err);
      }
    }

    // ── Angular sort — produces a non-crossing polygon regardless of click order
    // Projects vertices to centroid-relative coords and sorts by atan2 angle.
    function _sortPolygonVertices(pts) {
      var cx = 0, cy = 0;
      for (var i = 0; i < pts.length; i++) {
        cx += pts[i][1];  // lon → x
        cy += pts[i][0];  // lat → y
      }
      cx /= pts.length;
      cy /= pts.length;
      return pts.slice().sort(function(a, b) {
        return Math.atan2(a[0] - cy, a[1] - cx) - Math.atan2(b[0] - cy, b[1] - cx);
      });
    }

    // ── Drone marker ──────────────────────────────────────────────────────────

    function _droneCanvas(heading) {
      var c = document.createElement('canvas');
      c.width = 40; c.height = 40;
      var ctx = c.getContext('2d');
      ctx.translate(20, 20);
      ctx.rotate((heading || 0) * Math.PI / 180);
      ctx.beginPath();
      ctx.arc(0, 0, 16, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(0,212,255,0.25)';
      ctx.lineWidth = 8;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, -13); ctx.lineTo(9, 11); ctx.lineTo(0, 7); ctx.lineTo(-9, 11);
      ctx.closePath();
      ctx.fillStyle = '#00d4ff'; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5;
      ctx.fill(); ctx.stroke();
      return c.toDataURL();
    }

    function updateDroneMarker(lat, lon, heading) {
      if (!viewer) return;
      var pos = Cesium.Cartesian3.fromDegrees(lon, lat, 5);
      var img = _droneCanvas(heading);
      if (!droneEntity) {
        droneEntity = viewer.entities.add({
          position: pos,
          billboard: {
            image: img, width: 40, height: 40,
            heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
            verticalOrigin:  Cesium.VerticalOrigin.CENTER,
          }
        });
      } else {
        droneEntity.position        = pos;
        droneEntity.billboard.image = img;
      }
    }

    function hideDroneMarker() {
      if (!viewer || !droneEntity) return;
      viewer.entities.remove(droneEntity);
      droneEntity = null;
    }

    // ── Polygon ───────────────────────────────────────────────────────────────

    function setPolygon(verts) {
      if (!viewer) return;
      if (polygonEntity) { viewer.entities.remove(polygonEntity); polygonEntity = null; }
      if (!verts || verts.length < 3) return;
      polygonEntity = viewer.entities.add({
        polygon: {
          hierarchy: new Cesium.PolygonHierarchy(
            verts.map(function(v) { return Cesium.Cartesian3.fromDegrees(v[1], v[0]); })
          ),
          material:           Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.12),
          outline:            true,
          outlineColor:       Cesium.Color.fromCssColorString('#00d4ff'),
          outlineWidth:       2,
          height:             0,
          classificationType: Cesium.ClassificationType.TERRAIN
        }
      });
      viewer.flyTo(polygonEntity, { duration: 1.2 });
    }

    // ── Waypoints ─────────────────────────────────────────────────────────────

    function _wpColor(state) {
      if (state === 'active')   return Cesium.Color.fromCssColorString('#d29922');
      if (state === 'complete') return Cesium.Color.fromCssColorString('#484f58');
      return Cesium.Color.fromCssColorString('#00d4ff');
    }

    function setWaypoints(waypoints) {
      if (!viewer) return;
      _clearWaypointEntities();
      if (!waypoints || waypoints.length < 1) return;

      waypointLatLons = waypoints.map(function(wp) { return [wp[0], wp[1]]; });

      var positions = waypointLatLons.map(function(wp) {
        return Cesium.Cartesian3.fromDegrees(wp[1], wp[0], _WP_ALT);
      });

      pathPolyline = viewer.entities.add({
        polyline: {
          positions: positions,
          width: 2.5,
          material: new Cesium.PolylineGlowMaterialProperty({
            glowPower: 0.15,
            color: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.85)
          }),
          clampToGround: false
        }
      });

      waypoints.forEach(function(wp, i) {
        var pos = Cesium.Cartesian3.fromDegrees(wp[1], wp[0], _WP_ALT);
        var ent = viewer.entities.add({
          position: pos,
          point: {
            pixelSize: i === 0 ? 14 : 10,
            color: _wpColor('pending'),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1.5,
            heightReference: Cesium.HeightReference.NONE,
            disableDepthTestDistance: Number.POSITIVE_INFINITY
          },
          label: {
            text: i === 0 ? '\u2302' : String(i),
            font: '11px "Segoe UI", system-ui, sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cesium.Cartesian2(0, -22),
            heightReference: Cesium.HeightReference.NONE,
            showBackground: false
          }
        });
        waypointEntities.push(ent);
      });
    }

    function setActiveWaypoint(index) {
      if (!viewer || index < 0 || index >= waypointEntities.length) return;
      waypointEntities[index].point.color = _wpColor('active');
    }

    function markWaypointComplete(index) {
      if (!viewer || index < 0 || index >= waypointEntities.length) return;
      waypointEntities[index].point.color = _wpColor('complete');
    }

    function clearMissionState() {
      waypointEntities.forEach(function(ent) { ent.point.color = _wpColor('pending'); });
    }

    function _clearWaypointEntities() {
      _deselectWaypoint();
      _draggingWpIndex = -1;
      _ctxMenuWpIndex  = -1;
      if (pathPolyline) { viewer.entities.remove(pathPolyline); pathPolyline = null; }
      waypointEntities.forEach(function(e) { viewer.entities.remove(e); });
      waypointEntities = [];
      waypointLatLons  = [];
    }

    // Rebuild the pathPolyline positions from the current waypointLatLons.
    function _redrawWaypointPath() {
      if (!viewer) return;
      if (!pathPolyline || waypointLatLons.length < 2) {
        if (pathPolyline) { viewer.entities.remove(pathPolyline); pathPolyline = null; }
        return;
      }
      pathPolyline.polyline.positions = waypointLatLons.map(function(wp) {
        return Cesium.Cartesian3.fromDegrees(wp[1], wp[0], _WP_ALT);
      });
    }

    function clearMap() {
      if (!viewer) return;
      // Destroy live handlers FIRST so their callbacks cannot fire against
      // the entities we are about to remove/null — root cause of .polyline crash.
      if (drawHandler) { drawHandler.destroy(); drawHandler = null; }
      if (boxHandler)  { boxHandler.destroy();  boxHandler  = null; }
      _clearDrawState();   // nulls drawPreviewLine, drawClosingLine, drawMarkers
      _clearBoxState();    // removes boxPreviewEntity, clears drag state
      if (polygonEntity) { viewer.entities.remove(polygonEntity); polygonEntity = null; }
      _clearWaypointEntities();
      _hideWpContextMenu();
      // Restore camera controls in case box mode had disabled them.
      var ctrl = viewer.scene.screenSpaceCameraController;
      ctrl.enableTranslate = true;
      ctrl.enableRotate    = true;
      viewer.container.style.cursor = 'default';
      window.lastPolygon = null;
    }

    // ── Waypoint selection (left-click highlight + tooltip) ───────────────────

    function _ensureTooltip() {
      if (_tooltipEl) return;
      _tooltipEl = document.createElement('div');
      _tooltipEl.style.cssText = [
        'display:none', 'position:absolute',
        'background:rgba(6,11,16,0.88)',
        'border:1px solid rgba(0,212,255,0.2)',
        'border-radius:4px', 'padding:4px 8px',
        'font-family:"Segoe UI",system-ui,sans-serif',
        'font-size:10px', 'color:#8b949e',
        'pointer-events:none', 'z-index:150', 'white-space:nowrap'
      ].join(';');
      document.body.appendChild(_tooltipEl);
    }

    function _showTooltip(text, x, y) {
      _ensureTooltip();
      _tooltipEl.textContent = text;
      _tooltipEl.style.left    = (x + 12) + 'px';
      _tooltipEl.style.top     = (y - 28) + 'px';
      _tooltipEl.style.display = 'block';
    }

    function _hideTooltip() {
      if (_tooltipEl) _tooltipEl.style.display = 'none';
    }

    function _selectWaypoint(idx) {
      _deselectWaypoint();
      if (idx < 0 || idx >= waypointEntities.length) return;
      _selectedWpIndex = idx;
      var ent = waypointEntities[idx];
      var pos = ent.position.getValue(Cesium.JulianDate.now());
      if (!pos) return;

      // Glowing ring rendered behind the waypoint dot
      _selectedWpRing = viewer.entities.add({
        position: pos,
        point: {
          pixelSize: 28,
          color: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.18),
          outlineColor: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.75),
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          heightReference: Cesium.HeightReference.NONE
        }
      });

      var ll = waypointLatLons[idx];
      var screenPos = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, pos);
      if (screenPos) {
        _showTooltip(
          (idx === 0 ? 'Home' : 'WP\u00a0' + idx) +
          '  ' + ll[0].toFixed(6) + '\u00b0, ' + ll[1].toFixed(6) + '\u00b0',
          screenPos.x, screenPos.y
        );
      }
    }

    function _deselectWaypoint() {
      if (_selectedWpRing) {
        viewer.entities.remove(_selectedWpRing);
        _selectedWpRing = null;
      }
      _selectedWpIndex = -1;
      _hideTooltip();
    }

    // ── Waypoint right-click context menu ─────────────────────────────────────

    function _ensureContextMenu() {
      if (_ctxMenuEl) return;
      _ctxMenuEl = document.createElement('div');
      _ctxMenuEl.style.cssText = [
        'display:none', 'position:absolute',
        'background:rgba(6,11,16,0.95)',
        'border:1px solid rgba(0,212,255,0.22)',
        'border-radius:6px', 'padding:4px 0',
        'font-family:"Segoe UI",system-ui,sans-serif',
        'font-size:12px', 'color:#c9d1d9',
        'z-index:250', 'min-width:108px',
        'box-shadow:0 4px 14px rgba(0,0,0,0.55)'
      ].join(';');

      function _menuItem(label, color, fn) {
        var d = document.createElement('div');
        d.textContent = label;
        d.style.cssText = 'padding:6px 14px;cursor:pointer' + (color ? ';color:' + color : '');
        d.onmouseenter = function() { d.style.background = 'rgba(0,212,255,0.08)'; };
        d.onmouseleave = function() { d.style.background = ''; };
        d.onclick = fn;
        return d;
      }

      _ctxMenuEl.appendChild(_menuItem('⊹ Move',   null,      function() { wpMenuMove();   }));
      _ctxMenuEl.appendChild(_menuItem('✕ Delete', '#f85149', function() { wpMenuDelete(); }));
      document.body.appendChild(_ctxMenuEl);

      // Click anywhere outside the menu closes it
      document.addEventListener('click', function(e) {
        if (_ctxMenuEl && !_ctxMenuEl.contains(e.target)) {
          _hideWpContextMenu();
        }
      }, true);
    }

    function _showWpContextMenu(idx, x, y) {
      _ensureContextMenu();
      _ctxMenuWpIndex      = idx;
      _ctxMenuEl.style.left    = x + 'px';
      _ctxMenuEl.style.top     = y + 'px';
      _ctxMenuEl.style.display = 'block';
    }

    function _hideWpContextMenu() {
      if (_ctxMenuEl) _ctxMenuEl.style.display = 'none';
      _ctxMenuWpIndex = -1;
    }

    // "Move" context menu action
    function wpMenuMove() {
      var idx = _ctxMenuWpIndex;
      _hideWpContextMenu();
      if (idx < 0 || idx >= waypointEntities.length) return;
      _deselectWaypoint();
      _draggingWpIndex = idx;
      waypointEntities[idx].point.color = Cesium.Color.fromCssColorString('#f0c040');
      viewer.container.style.cursor = 'crosshair';
      // Prevent camera pan while dragging
      viewer.scene.screenSpaceCameraController.enableTranslate = false;
      console.log('[wp] move mode for waypoint #' + idx);
    }

    // Drop the dragged waypoint; waypointLatLons already has the final position
    // from the last MOUSE_MOVE update.
    function _finishWaypointDrag() {
      if (_draggingWpIndex < 0) return;
      var idx = _draggingWpIndex;
      _draggingWpIndex = -1;
      viewer.container.style.cursor = 'default';
      viewer.scene.screenSpaceCameraController.enableTranslate = true;
      waypointEntities[idx].point.color = _wpColor('pending');
      _redrawWaypointPath();
      window.lastWaypointEdit = waypointLatLons.slice();
      console.log('[wp] move complete for waypoint #' + idx);
    }

    // "Delete" context menu action
    function wpMenuDelete() {
      var idx = _ctxMenuWpIndex;
      _hideWpContextMenu();
      if (idx < 0 || idx >= waypointEntities.length) return;

      viewer.entities.remove(waypointEntities[idx]);
      waypointEntities.splice(idx, 1);
      waypointLatLons.splice(idx, 1);

      // Re-number labels on shifted waypoints
      for (var i = idx; i < waypointEntities.length; i++) {
        waypointEntities[i].label.text     = i === 0 ? '\u2302' : String(i);
        waypointEntities[i].point.pixelSize = i === 0 ? 14 : 10;
      }

      _deselectWaypoint();
      _redrawWaypointPath();
      window.lastWaypointEdit = waypointLatLons.slice();
      console.log('[wp] deleted waypoint #' + idx + ', remaining: ' + waypointEntities.length);
    }

    // ── Interaction handler (always-on: selection, right-click menu, drag) ────

    function _pickWaypointIndex(screenPos) {
      if (!viewer || waypointEntities.length === 0) return -1;
      var picked = viewer.scene.pick(screenPos);
      if (!picked || !picked.id) return -1;
      return waypointEntities.indexOf(picked.id);
    }

    function _startInteractionHandler() {
      _interactionHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

      // Right-click → context menu (ignored during draw mode)
      _interactionHandler.setInputAction(function(click) {
        if (drawHandler !== null) return;
        _hideWpContextMenu();
        var idx = _pickWaypointIndex(click.position);
        if (idx >= 0) _showWpContextMenu(idx, click.position.x, click.position.y);
      }, Cesium.ScreenSpaceEventType.RIGHT_CLICK);

      // Left-click → drop drag | select waypoint | deselect (ignored during draw)
      _interactionHandler.setInputAction(function(click) {
        if (drawHandler !== null) return;
        _hideWpContextMenu();
        if (_draggingWpIndex >= 0) {
          _finishWaypointDrag();
          return;
        }
        var idx = _pickWaypointIndex(click.position);
        if (idx >= 0) {
          _selectWaypoint(idx);
        } else {
          _deselectWaypoint();
        }
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      // Mouse move → update dragged waypoint position in real time
      _interactionHandler.setInputAction(function(movement) {
        if (_draggingWpIndex < 0) return;
        var ray = viewer.camera.getPickRay(movement.endPosition);
        var pos = viewer.scene.globe.pick(ray, viewer.scene) ||
                  viewer.camera.pickEllipsoid(movement.endPosition, viewer.scene.globe.ellipsoid);
        if (!Cesium.defined(pos)) return;
        var carto = Cesium.Cartographic.fromCartesian(pos);
        var lat   = Cesium.Math.toDegrees(carto.latitude);
        var lon   = Cesium.Math.toDegrees(carto.longitude);
        waypointLatLons[_draggingWpIndex] = [lat, lon];
        waypointEntities[_draggingWpIndex].position =
          Cesium.Cartesian3.fromDegrees(lon, lat, _WP_ALT);
        _redrawWaypointPath();
      }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    }

    // ── Draw mode ─────────────────────────────────────────────────────────────

    function _clearDrawState() {
      if (drawPreviewLine) { viewer.entities.remove(drawPreviewLine); drawPreviewLine = null; }
      if (drawClosingLine) { viewer.entities.remove(drawClosingLine); drawClosingLine = null; }
      drawMarkers.forEach(function(e) { viewer.entities.remove(e); });
      drawMarkers = []; drawingPoints = [];
    }

    // Create the two preview lines for draw mode.  Both use CallbackProperty so
    // Cesium re-evaluates positions every render frame — no per-click rebuild needed.
    function _createDrawPreviewLines() {
      console.log('[draw] creating preview lines');
      // Open path: all vertices in click order (grows as points are added)
      drawPreviewLine = viewer.entities.add({
        polyline: {
          positions: [],
          width: 2,
          material: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.65),
          clampToGround: false
        }
      });

      // Closing segment: last vertex → first vertex (shows how the polygon will close)
      drawClosingLine = viewer.entities.add({
        polyline: {
          positions: [],
          width: 1.5,
          material: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.30),
          clampToGround: false
        }
      });
    }

    function enableDrawMode() {
      if (!viewer) { console.warn('[draw] viewer not ready'); return; }
      _startDrawMode();
    }

    // ── Box AOI draw mode ─────────────────────────────────────────────────────

    function _clearBoxState() {
      if (boxPreviewEntity) {
        viewer.entities.remove(boxPreviewEntity);
        boxPreviewEntity = null;
      }
      boxFirstCorner   = null;
      boxCurrentCorner = null;
      boxIsDragging    = false;
    }

    function _boxRectPositions(c1, c2) {
      // Returns 5 Cartesian3 positions (closed loop) for a lat/lon-aligned rectangle.
      // c1, c2 are {lat, lon} objects (the two diagonal corners).
      var minLat = Math.min(c1.lat, c2.lat);
      var maxLat = Math.max(c1.lat, c2.lat);
      var minLon = Math.min(c1.lon, c2.lon);
      var maxLon = Math.max(c1.lon, c2.lon);
      return [
        Cesium.Cartesian3.fromDegrees(minLon, minLat, 2),
        Cesium.Cartesian3.fromDegrees(maxLon, minLat, 2),
        Cesium.Cartesian3.fromDegrees(maxLon, maxLat, 2),
        Cesium.Cartesian3.fromDegrees(minLon, maxLat, 2),
        Cesium.Cartesian3.fromDegrees(minLon, minLat, 2)  // close loop
      ];
    }

    function _boxRectLatLons(c1, c2) {
      // Returns [[lat,lon], ...] 4-vertex non-crossing rectangle (SW, SE, NE, NW).
      var minLat = Math.min(c1.lat, c2.lat);
      var maxLat = Math.max(c1.lat, c2.lat);
      var minLon = Math.min(c1.lon, c2.lon);
      var maxLon = Math.max(c1.lon, c2.lon);
      return [
        [minLat, minLon],
        [minLat, maxLon],
        [maxLat, maxLon],
        [maxLat, minLon]
      ];
    }

    function _pickLatLon(position) {
      var ray = viewer.camera.getPickRay(position);
      var cartesian = viewer.scene.globe.pick(ray, viewer.scene) ||
                      viewer.camera.pickEllipsoid(position, viewer.scene.globe.ellipsoid);
      if (!Cesium.defined(cartesian)) return null;
      var carto = Cesium.Cartographic.fromCartesian(cartesian);
      return {
        lat: Cesium.Math.toDegrees(carto.latitude),
        lon: Cesium.Math.toDegrees(carto.longitude)
      };
    }

    function enableBoxDrawMode() {
      if (!viewer) { console.warn('[box] viewer not ready'); return; }
      console.log('[box] entering box AOI draw mode');

      // Destroy any live polygon draw handler so its LEFT_CLICK can no longer
      // fire and access the now-null drawPreviewLine (root cause of the crash).
      if (drawHandler) { drawHandler.destroy(); drawHandler = null; }
      // Destroy any previously started box handler to prevent duplicate listeners.
      if (boxHandler) { boxHandler.destroy(); boxHandler = null; }

      _clearDrawState();
      _clearBoxState();
      window.lastPolygon = null;

      // Disable camera pan so left-click is free for box drawing
      var ctrl = viewer.scene.screenSpaceCameraController;
      ctrl.enableTranslate = false;
      ctrl.enableRotate    = false;

      viewer.container.style.cursor = 'crosshair';

      // Preview entity uses CallbackProperty so Cesium re-reads positions every
      // render frame — no geometry rebuilds on each MOUSE_MOVE event.
      boxPreviewEntity = viewer.entities.add({
        polyline: {
          positions: new Cesium.CallbackProperty(function() {
            if (!boxFirstCorner || !boxCurrentCorner) return [];
            return _boxRectPositions(boxFirstCorner, boxCurrentCorner);
          }, false),
          width: 2,
          material: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.65),
          clampToGround: false
        }
      });

      boxHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

      // LEFT_DOWN — anchor the first corner and begin drag
      boxHandler.setInputAction(function(event) {
        var pt = _pickLatLon(event.position);
        if (!pt) { console.warn('[box] LEFT_DOWN: could not resolve position'); return; }
        boxFirstCorner   = pt;
        boxCurrentCorner = pt;
        boxIsDragging    = true;
        console.log('[box] drag started: lat=' + pt.lat.toFixed(6) + ' lon=' + pt.lon.toFixed(6));
      }, Cesium.ScreenSpaceEventType.LEFT_DOWN);

      // MOUSE_MOVE — update live second corner only while dragging
      boxHandler.setInputAction(function(movement) {
        if (!boxIsDragging) return;
        var pt = _pickLatLon(movement.endPosition);
        if (!pt) return;
        boxCurrentCorner = pt;
      }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

      // LEFT_UP — finalize the rectangle and clean up unconditionally
      boxHandler.setInputAction(function(event) {
        if (!boxIsDragging) return;
        boxIsDragging = false;

        var pt = _pickLatLon(event.position);
        if (pt) { boxCurrentCorner = pt; }

        if (boxFirstCorner && boxCurrentCorner) {
          var verts = _boxRectLatLons(boxFirstCorner, boxCurrentCorner);
          var latSpan = Math.abs(verts[2][0] - verts[0][0]);
          var lonSpan = Math.abs(verts[1][1] - verts[0][1]);
          if (latSpan > 1e-6 && lonSpan > 1e-6) {
            window.lastPolygon = verts;
            // Render the permanent polygon immediately so there is no visual gap
            // between the drag preview disappearing and Python's poll completing.
            // Python's poll will call setPolygon again within ~1 s — that is safe
            // because setPolygon removes the existing entity before recreating it.
            setPolygon(verts);
            console.log('[box] rectangle finalized: ' + JSON.stringify(verts));
          } else {
            console.warn('[box] drag too small, discarding');
          }
        }

        if (boxHandler) { boxHandler.destroy(); boxHandler = null; }
        _clearBoxState();
        viewer.container.style.cursor = 'default';
        ctrl.enableTranslate = true;
        ctrl.enableRotate    = true;
      }, Cesium.ScreenSpaceEventType.LEFT_UP);
    }

    function _startDrawMode() {
      console.log('[draw] entering draw mode');
      if (drawHandler) { drawHandler.destroy(); drawHandler = null; }
      // Tear down any live box handler and restore camera controls it may have disabled.
      if (boxHandler)  { boxHandler.destroy();  boxHandler  = null; }
      _clearDrawState();
      _clearBoxState();
      var ctrl = viewer.scene.screenSpaceCameraController;
      ctrl.enableTranslate = true;
      ctrl.enableRotate    = true;
      window.lastPolygon = null;
      viewer.container.style.cursor = 'crosshair';

      // Create both preview lines once; CallbackProperty keeps them current every frame
      console.log('[draw] drawingPoints before preview create:', JSON.stringify(drawingPoints));
      _createDrawPreviewLines();

      drawHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

      drawHandler.setInputAction(function(click) {
        var ray = viewer.camera.getPickRay(click.position);
        var cartesian = viewer.scene.globe.pick(ray, viewer.scene) ||
                        viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid);
        if (!Cesium.defined(cartesian)) {
          console.warn('[draw] click: could not resolve position, skipping');
          return;
        }
        var carto = Cesium.Cartographic.fromCartesian(cartesian);
        var lon   = Cesium.Math.toDegrees(carto.longitude);
        var lat   = Cesium.Math.toDegrees(carto.latitude);
        drawingPoints = drawingPoints.concat([[lat, lon]]);
        console.log('[draw] vertex #' + drawingPoints.length +
          ': lat=' + lat.toFixed(6) + ' lon=' + lon.toFixed(6));
        drawPreviewLine.polyline.positions = new Cesium.ConstantProperty(
          drawingPoints.map(function(p) {
            return Cesium.Cartesian3.fromDegrees(p[1], p[0], 2);
          })
        );
        if (drawingPoints.length >= 2) {
          var first = drawingPoints[0];
          var last  = drawingPoints[drawingPoints.length - 1];
          drawClosingLine.polyline.positions = new Cesium.ConstantProperty([
            Cesium.Cartesian3.fromDegrees(first[1], first[0], 2),
            Cesium.Cartesian3.fromDegrees(last[1],  last[0],  2)
          ]);
        }
        drawMarkers.push(viewer.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 2),
          point: {
            pixelSize: 8,
            color: Cesium.Color.fromCssColorString('#00d4ff'),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1,
            disableDepthTestDistance: Number.POSITIVE_INFINITY
          }
        }));
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      drawHandler.setInputAction(function() {
        // Double-click fires an extra LEFT_CLICK first — discard that vertex
        if (drawingPoints.length > 0) {
          drawingPoints.pop();
          if (drawMarkers.length > 0) viewer.entities.remove(drawMarkers.pop());
        }
        if (drawHandler) { drawHandler.destroy(); drawHandler = null; }
        viewer.container.style.cursor = 'default';

        console.log('[draw] polygon closed with ' + drawingPoints.length + ' vertices');
        if (drawingPoints.length >= 3) {
          // Sort vertices into a non-crossing order before handing to Python.
          // Only assign window.lastPolygon after the sort succeeds and the
          // count is confirmed — prevents a partial read on the Python side.
          var sorted = _sortPolygonVertices(drawingPoints);
          if (sorted.length >= 3) {
            window.lastPolygon = sorted;
            console.log('[draw] window.lastPolygon set (' + sorted.length + ' vertices)');
          }
        } else {
          console.warn('[draw] need ≥3 vertices, got ' + drawingPoints.length);
        }
        _clearDrawState();
      }, Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
    }

    // ── Navigation ────────────────────────────────────────────────────────────

    function setCenter(lat, lon) {
      if (!viewer) return;
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lon, lat, 1500),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-40), roll: 0 },
        duration: 1.2
      });
    }

    // ── Map style toggle ──────────────────────────────────────────────────────

    function toggleMapStyle() {
      if (!viewer) return;
      viewer.imageryLayers.removeAll();
      if (mapMode === 'dark') {
        Cesium.IonImageryProvider.fromAssetId(2).then(function(provider) {
          viewer.imageryLayers.addImageryProvider(provider);
        }).catch(function(err) {
          console.error('[cesium] Satellite layer failed: ' + err);
          _addDarkLayer();
          mapMode = 'dark';
        });
        mapMode = 'satellite';
      } else {
        _addDarkLayer();
        mapMode = 'dark';
      }
    }

    function _addDarkLayer() {
      viewer.imageryLayers.addImageryProvider(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
          subdomains: ['a', 'b', 'c', 'd'],
          maximumLevel: 19,
          credit: new Cesium.Credit('© OpenStreetMap contributors © CARTO')
        })
      );
    }
  </script>
</body>
</html>
"""

_MAP_HTML = (
    _MAP_HTML_TEMPLATE
    .replace("CESIUM_TOKEN", _CESIUM_TOKEN)
    .replace("HOME_LAT", str(SIM_HOME_LAT))
    .replace("HOME_LON", str(SIM_HOME_LON))
)


# ── MapView ───────────────────────────────────────────────────────────────────

class MapView(QWidget):
    """
    Standalone CesiumJS map widget.

    Signals:
        polygon_received(list)       — [[lat, lon], ...] when operator draws polygon.
        waypoint_edit_received(list) — [[lat, lon], ...] when a waypoint is
                                       moved or deleted via the right-click menu.

    Poll timers:
        _poll_timer    — 500 ms; active only while draw mode is running.
        _wp_edit_timer — 300 ms; always running after build.
    """

    polygon_received       = Signal(list)  # [[lat, lon], ...]
    waypoint_edit_received = Signal(list)  # [[lat, lon], ...]

    _POLL_INTERVAL_MS = 1000
    _WP_EDIT_POLL_MS  = 300

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_polygon)

        self._wp_edit_timer = QTimer(self)
        self._wp_edit_timer.setInterval(self._WP_EDIT_POLL_MS)
        self._wp_edit_timer.timeout.connect(self._poll_wp_edit)
        self._last_wp_count: int | None = None  # for change-only logging

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        page = _DebugPage(self)
        self._web = QWebEngineView()
        self._web.setPage(page)
        self._web.setStyleSheet("background-color: #060b10;")

        self._web.setMouseTracking(True)
        self._web.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Forward right/middle-click to web view (not intercepted by Qt)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        s = self._web.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        self._web.setHtml(_MAP_HTML, QUrl("qrc:/"))

        # Waypoint edit polling starts immediately (no-op until JS is ready)
        self._wp_edit_timer.start()

        layout.addWidget(self._web)

    # ── Polygon draw polling ──────────────────────────────────────────────────

    def _poll_polygon(self) -> None:
        print("[MapView] _poll_polygon: firing", file=sys.stderr, flush=True)
        self._web.page().runJavaScript("JSON.stringify(window.lastPolygon)", 0, self._on_poll_result)

    def _on_poll_result(self, result) -> None:
        print(f"[MapView] _on_poll_result: result={result!r}", file=sys.stderr, flush=True)
        if not result or result == "null":
            return
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return
        if not isinstance(parsed, list) or len(parsed) < 3:
            return
        self._poll_timer.stop()
        self._web.page().runJavaScript("window.lastPolygon = null;")
        print(f"[MapView] polygon received: {len(parsed)} vertices", file=sys.stderr, flush=True)
        self.polygon_received.emit(parsed)

    # ── Waypoint edit polling ─────────────────────────────────────────────────

    def _poll_wp_edit(self) -> None:
        self._web.page().runJavaScript("JSON.stringify(window.lastWaypointEdit)", 0, self._on_wp_edit_result)

    def _on_wp_edit_result(self, result) -> None:
        if not result or result == "null":
            return
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return
        if parsed is None:
            return
        self._web.page().runJavaScript("window.lastWaypointEdit = null;")
        # Only log when the waypoint count actually changes to avoid spam
        if len(parsed) != self._last_wp_count:
            self._last_wp_count = len(parsed)
            print(f"[MapView] waypoint edit: {len(parsed)} waypoints", file=sys.stderr, flush=True)
        self.waypoint_edit_received.emit(parsed)

    # ── JS interface ──────────────────────────────────────────────────────────

    def _js(self, script: str) -> None:
        self._web.page().runJavaScript(script)

    def update_drone_marker(self, lat: float, lon: float, heading: float) -> None:
        self._js(f"updateDroneMarker({lat},{lon},{heading});")

    def set_polygon(self, verts: list) -> None:
        self._js(f"setPolygon({json.dumps(verts)});")

    def set_waypoints(self, waypoints: list) -> None:
        self._js(f"setWaypoints({json.dumps(waypoints)});")

    def set_active_waypoint(self, index: int) -> None:
        self._js(f"setActiveWaypoint({index});")

    def mark_waypoint_complete(self, index: int) -> None:
        self._js(f"markWaypointComplete({index});")

    def clear_mission_state(self) -> None:
        self._js("clearMissionState();")

    def clear_map(self) -> None:
        self._poll_timer.stop()
        self._js("clearMap();")

    def enable_draw_mode(self) -> None:
        self._js("enableDrawMode();")
        self._poll_timer.start()

    def enable_box_draw_mode(self) -> None:
        print("[MapView] enable_box_draw_mode: starting", file=sys.stderr, flush=True)
        self._js("enableBoxDrawMode();")
        self._poll_timer.start()
        print(f"[MapView] enable_box_draw_mode: poll timer active={self._poll_timer.isActive()}", file=sys.stderr, flush=True)

    def hide_drone_marker(self) -> None:
        self._js("hideDroneMarker();")

    def set_center(self, lat: float, lon: float) -> None:
        self._js(f"setCenter({lat},{lon});")

    def toggle_map_style(self) -> None:
        self._js("toggleMapStyle();")
