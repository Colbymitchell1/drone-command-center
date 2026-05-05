"""
app/services/mqtt_publisher.py

MQTTPublisher -- bridges VehicleState from a VehicleAdapter to MQTT topics.

Runs a local Mosquitto broker by default (localhost:1883).
All drone telemetry, health, and mission events are published here.
The UI, LLM layer, and future multi-vehicle coordinator all subscribe
to these topics instead of talking to the adapter directly.

Topic structure:
    cc/{mission_id}/vehicle/{vehicle_id}/state       -- VehicleState at publish_hz
    cc/{mission_id}/vehicle/{vehicle_id}/health      -- VehicleHealth on demand
    cc/{mission_id}/vehicle/{vehicle_id}/event       -- discrete events (arm, RTL, etc.)
    cc/{mission_id}/vehicle/{vehicle_id}/command/ack -- CommandResult responses
    cc/{mission_id}/log/event                        -- mission-level log events

Install broker (Ubuntu):
    sudo apt install mosquitto mosquitto-clients
    sudo systemctl enable --now mosquitto

Install client library:
    pip install paho-mqtt
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from app.models.domain import CommandResult, VehicleHealth, VehicleState
from integrations.vehicle_adapter import Subscription, VehicleAdapter

log = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    _MQTT_AVAILABLE = True
except ImportError:
    _MQTT_AVAILABLE = False
    log.warning("paho-mqtt not installed -- MQTTPublisher will run in dry-run mode")


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------


def _serialize(obj) -> str:
    """
    Serialize domain dataclasses to JSON.
    Handles datetime, Enum, and nested dataclasses automatically.
    """
    def _default(o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "value"):          # Enum
            return o.value
        return str(o)

    return json.dumps(
        dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj,
        default=_default,
    )


# ---------------------------------------------------------------------------
# MQTTPublisher
# ---------------------------------------------------------------------------


class MQTTPublisher:
    """
    Subscribes to a VehicleAdapter's state stream and publishes
    normalized JSON payloads to MQTT topics.

    Threading:
        paho-mqtt runs its own network thread.
        The adapter's state callback fires on the adapter's asyncio loop thread.
        publish() is thread-safe in paho -- no additional locking needed.

    Usage:
        publisher = MQTTPublisher(
            adapter=adapter,
            mission_id="abc-123",
            vehicle_id="drone-1",
        )
        publisher.start()
        # ... fly mission ...
        publisher.stop()
    """

    DEFAULT_BROKER_HOST = "localhost"
    DEFAULT_BROKER_PORT = 1883
    DEFAULT_QOS = 0          # fire-and-forget; use QoS 1 for critical events
    RETAIN_STATE = False      # don't retain telemetry -- stale state is dangerous

    def __init__(
        self,
        adapter: VehicleAdapter,
        mission_id: str,
        vehicle_id: str,
        broker_host: str = DEFAULT_BROKER_HOST,
        broker_port: int = DEFAULT_BROKER_PORT,
        publish_hz: float = 4.0,
    ):
        self._adapter = adapter
        self._mission_id = mission_id
        self._vehicle_id = vehicle_id
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._publish_hz = publish_hz

        self._client: Optional[object] = None   # paho Client
        self._subscription: Optional[Subscription] = None
        self._running = False
        self._dry_run = not _MQTT_AVAILABLE

    # ── Topic builders ───────────────────────────────────────────────────────

    def _topic(self, *parts: str) -> str:
        base = f"cc/{self._mission_id}/vehicle/{self._vehicle_id}"
        return "/".join([base, *parts])

    def _log_topic(self, *parts: str) -> str:
        return f"cc/{self._mission_id}/log/" + "/".join(parts)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Connect to the broker and begin publishing telemetry."""
        if self._running:
            return

        if not self._dry_run:
            self._client = mqtt.Client(
                client_id=f"dcc-{self._vehicle_id}",
                clean_session=True,
            )
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect

            try:
                self._client.connect(self._broker_host, self._broker_port, keepalive=60)
                self._client.loop_start()
            except Exception as e:
                log.error(f"MQTT connect failed: {e} -- switching to dry-run mode")
                self._dry_run = True

        self._running = True
        self._subscription = self._adapter.subscribe_state(
            callback=self._on_vehicle_state,
            hz=self._publish_hz,
        )
        log.info(
            f"MQTTPublisher started "
            f"({'dry-run' if self._dry_run else self._broker_host}:{self._broker_port})"
        )

    def stop(self) -> None:
        """Stop publishing and disconnect from the broker."""
        if not self._running:
            return
        self._running = False

        if self._subscription:
            self._subscription.cancel()
            self._subscription = None

        if self._client and not self._dry_run:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

        log.info("MQTTPublisher stopped")

    # ── Publish helpers ──────────────────────────────────────────────────────

    def _publish(self, topic: str, payload: str, qos: int = DEFAULT_QOS) -> None:
        if self._dry_run:
            log.debug(f"[dry-run] {topic}: {payload[:120]}")
            return
        if self._client:
            self._client.publish(topic, payload, qos=qos, retain=self.RETAIN_STATE)

    def publish_event(self, event_type: str, data: dict) -> None:
        """
        Publish a discrete vehicle event (arm, disarm, RTL, abort, etc.).
        Use QoS 1 so these are delivered at least once.
        """
        payload = json.dumps({
            "event": event_type,
            "vehicle_id": self._vehicle_id,
            "mission_id": self._mission_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
        self._publish(self._topic("event"), payload, qos=1)

    def publish_command_ack(self, command: str, result: CommandResult) -> None:
        """Publish a CommandResult acknowledgment after a command is issued."""
        payload = json.dumps({
            "command": command,
            "status": result.status.value,
            "message": result.message,
            "code": result.code,
            "recoverable": result.recoverable,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })
        self._publish(self._topic("command", "ack"), payload, qos=1)

    def publish_health(self, health: VehicleHealth) -> None:
        """Publish a VehicleHealth snapshot (call after preflight checks)."""
        payload = json.dumps({
            "vehicle_id": self._vehicle_id,
            "ready": health.ready,
            "blocking": health.blocking,
            "warnings": health.warnings,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })
        self._publish(self._topic("health"), payload, qos=1)

    def publish_log_event(self, event_type: str, data: dict) -> None:
        """Publish a mission-level log event (mission start, complete, abort)."""
        payload = json.dumps({
            "event": event_type,
            "mission_id": self._mission_id,
            "vehicle_id": self._vehicle_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
        self._publish(self._log_topic("event"), payload, qos=1)

    # ── Adapter state callback ───────────────────────────────────────────────

    def _on_vehicle_state(self, state: VehicleState) -> None:
        """Called by the adapter's publish loop at the configured rate."""
        if not self._running:
            return
        try:
            payload = _serialize(state)
            self._publish(self._topic("state"), payload)
        except Exception as e:
            log.warning(f"State serialize error: {e}")

    # ── paho callbacks ───────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            log.info(f"MQTT connected to {self._broker_host}:{self._broker_port}")
        else:
            log.error(f"MQTT connection refused (rc={rc})")

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            log.warning(f"MQTT unexpected disconnect (rc={rc}) -- paho will retry")
