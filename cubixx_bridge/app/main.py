#!/usr/bin/env python3
"""Cubixx MQTT bridge.

Connects to one or more Cubixx relay switches over their WebSocket (port 81),
mirrors relay state to an MQTT broker, accepts commands from MQTT, and
publishes Home Assistant MQTT Discovery messages. Includes a small web UI
for managing devices and broker settings.
"""
import asyncio
import json
import logging
import os
import re
import signal
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import websockets
from aiohttp import web

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
STATIC_DIR = Path(__file__).parent / "static"
HTTP_PORT = int(os.getenv("PORT", "8080"))
CLEANUP_RELAYS = 16  # how many entity slots to clear when a device is removed
# When set (by the Home Assistant app), broker host/port/credentials come from the
# environment on every start and cannot be changed in the UI.
MQTT_MANAGED = os.getenv("MQTT_MANAGED") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cubixx")

ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def default_mqtt():
    return {
        "host": os.getenv("MQTT_HOST", ""),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USER", ""),
        "password": os.getenv("MQTT_PASS", ""),
        "discovery_prefix": os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        "base_topic": os.getenv("MQTT_BASE_TOPIC", "cubixx"),
    }


class Config:
    def __init__(self):
        self.mqtt = default_mqtt()
        self.devices = []
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                self.mqtt.update(data.get("mqtt", {}))
                self.devices = data.get("devices", [])
            except Exception:
                log.exception("Could not read %s", CONFIG_PATH)
        if MQTT_MANAGED:
            env = default_mqtt()
            for key in ("host", "port", "username", "password"):
                self.mqtt[key] = env[key]

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mqtt": self.mqtt, "devices": self.devices}, indent=2))
        tmp.replace(CONFIG_PATH)


class MqttLink:
    """Owns the paho client. Callbacks run in paho's thread."""

    def __init__(self, manager):
        self.m = manager
        self.client = None
        self.connected = False
        self.error = None

    def start(self):
        self.stop()
        cfg = self.m.cfg.mqtt
        if not cfg.get("host"):
            self.error = "Broker host not configured"
            return
        self.error = "Connecting..."
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cubixx-bridge")
        if cfg.get("username"):
            c.username_pw_set(cfg["username"], cfg.get("password") or None)
        c.will_set(self.m.bridge_topic(), "offline", qos=1, retain=True)
        c.reconnect_delay_set(1, 30)
        c.on_connect = self._on_connect
        c.on_connect_fail = self._on_connect_fail
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c
        c.connect_async(cfg["host"], int(cfg["port"]))
        c.loop_start()

    def stop(self):
        c, self.client = self.client, None
        if c is None:
            return
        try:
            if self.connected:
                c.publish(self.m.bridge_topic(), "offline", qos=1, retain=True).wait_for_publish(2)
            c.disconnect()
            c.loop_stop()
        except Exception:
            log.debug("Error while stopping MQTT", exc_info=True)
        self.connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.connected = False
            self.error = f"Broker refused connection: {reason_code}"
            log.warning(self.error)
            return
        self.connected = True
        self.error = None
        log.info("MQTT connected to %s", self.m.cfg.mqtt["host"])
        client.subscribe(f"{self.m.base}/+/relay/+/set", qos=1)
        self.m.loop.call_soon_threadsafe(self.m.republish_all)

    def _on_connect_fail(self, client, userdata):
        self.connected = False
        self.error = "Could not reach broker"

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        self.error = f"Disconnected: {reason_code}"
        log.warning(self.error)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore")
        asyncio.run_coroutine_threadsafe(self.m.handle_command(msg.topic, payload), self.m.loop)


class Device:
    def __init__(self, manager, cfg):
        self.m = manager
        self.cfg = cfg  # same dict object that lives in Config.devices
        self.ws = None
        self.connected = False
        self.error = None
        self.last_message = None
        self.relays = {}  # index -> {"name": str, "state": bool}
        self.task = None

    @property
    def id(self):
        return self.cfg["id"]

    def start(self):
        self.task = asyncio.create_task(self._run(), name=f"device-{self.id}")

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.ws = None
        self.connected = False
        self.m.publish_availability(self)

    async def send(self, payload):
        if self.ws is None:
            raise ConnectionError("Device is not connected")
        await self.ws.send(json.dumps(payload))

    async def _keepalive(self, ws):
        try:
            while True:
                await asyncio.sleep(10)
                await ws.send('{"command":"ping"}')
        except Exception:
            pass

    async def _run(self):
        while True:
            uri = f"ws://{self.cfg['ip']}:{self.cfg['port']}/"
            try:
                async with websockets.connect(uri, ping_interval=None, open_timeout=10) as ws:
                    self.ws = ws
                    self.connected = True
                    self.error = None
                    self.last_message = time.time()
                    log.info("[%s] connected to %s", self.id, uri)
                    self.m.publish_availability(self)
                    await ws.send('{"command":"request_state"}')
                    pinger = asyncio.create_task(self._keepalive(ws))
                    try:
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=35)
                            self.last_message = time.time()
                            try:
                                msg = json.loads(raw)
                            except ValueError:
                                continue
                            self._handle(msg)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}".rstrip(": ") or "Connection error"
                log.warning("[%s] %s", self.id, self.error)
            self.ws = None
            self.connected = False
            self.m.publish_availability(self)
            await asyncio.sleep(5)

    def _handle(self, msg):
        ev = msg.get("event")
        if ev == "full_state":
            self.relays = {
                int(r["index"]): {
                    "name": r.get("name") or f"Relay {int(r['index']) + 1}",
                    "state": bool(r.get("state")),
                }
                for r in msg.get("relays", [])
            }
            self.m.publish_discovery(self)
            for i in self.relays:
                self.m.publish_state(self, i)
        elif ev == "relay_update":
            r = msg.get("relay", {})
            i = int(r.get("index", -1))
            if i >= 0:
                entry = self.relays.setdefault(i, {"name": f"Relay {i + 1}", "state": False})
                entry["state"] = bool(r.get("state"))
                self.m.publish_state(self, i)

    def status(self):
        age = None if self.last_message is None else int(time.time() - self.last_message)
        return {
            "id": self.id,
            "name": self.cfg.get("name") or f"Cubixx {self.id}",
            "ip": self.cfg["ip"],
            "port": self.cfg["port"],
            "connected": self.connected,
            "error": self.error,
            "last_message_age": age,
            "relays": [
                {"index": i, "name": r["name"], "state": r["state"]}
                for i, r in sorted(self.relays.items())
            ],
        }


class Manager:
    def __init__(self):
        self.cfg = Config()
        self.devices = {}
        self.mqtt = MqttLink(self)
        self.loop = None

    # ---- topics -------------------------------------------------------
    @property
    def base(self):
        return (self.cfg.mqtt.get("base_topic") or "cubixx").strip("/")

    def bridge_topic(self):
        return f"{self.base}/bridge/status"

    def dev_topic(self, dev_id, *parts):
        return "/".join([self.base, dev_id, *parts])

    def _disc_topic(self, dev_id, index):
        prefix = (self.cfg.mqtt.get("discovery_prefix") or "homeassistant").strip("/")
        return f"{prefix}/switch/cubixx_{dev_id}_{index + 1}/config"

    # ---- lifecycle ----------------------------------------------------
    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.mqtt.start()
        for cfg in self.cfg.devices:
            dev = Device(self, cfg)
            self.devices[dev.id] = dev
            dev.start()

    async def shutdown(self):
        for dev in list(self.devices.values()):
            await dev.stop()
        self.mqtt.stop()

    # ---- publishing ---------------------------------------------------
    def publish(self, topic, payload, retain=True):
        c = self.mqtt.client
        if c is not None and self.mqtt.connected:
            c.publish(topic, payload, qos=1, retain=retain)

    def publish_availability(self, dev):
        self.publish(self.dev_topic(dev.id, "status"), "online" if dev.connected else "offline")

    def publish_state(self, dev, index):
        state = dev.relays[index]["state"]
        self.publish(self.dev_topic(dev.id, "relay", str(index), "state"), "ON" if state else "OFF")

    def publish_discovery(self, dev):
        count = len(dev.relays) or int(dev.cfg.get("relays", 6))
        device = {
            "identifiers": [f"cubixx_{dev.id}"],
            "name": dev.cfg.get("name") or f"Cubixx {dev.id}",
            "manufacturer": "Cubixx",
            "model": f"{count} Gang Relay Switch",
            "configuration_url": f"http://{dev.cfg['ip']}",
        }
        for i in range(count):
            cfg = {
                "name": dev.relays.get(i, {}).get("name") or f"Relay {i + 1}",
                "unique_id": f"cubixx_{dev.id}_relay_{i + 1}",
                "has_entity_name": True,
                "command_topic": self.dev_topic(dev.id, "relay", str(i), "set"),
                "state_topic": self.dev_topic(dev.id, "relay", str(i), "state"),
                "availability": [
                    {"topic": self.bridge_topic()},
                    {"topic": self.dev_topic(dev.id, "status")},
                ],
                "availability_mode": "all",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": device,
            }
            self.publish(self._disc_topic(dev.id, i), json.dumps(cfg))

    def remove_discovery(self, dev):
        for i in range(CLEANUP_RELAYS):
            self.publish(self._disc_topic(dev.id, i), "")
            self.publish(self.dev_topic(dev.id, "relay", str(i), "state"), "")
        self.publish(self.dev_topic(dev.id, "status"), "")

    def republish_all(self):
        self.publish(self.bridge_topic(), "online")
        for dev in self.devices.values():
            self.publish_availability(dev)
            self.publish_discovery(dev)
            for i in dev.relays:
                self.publish_state(dev, i)

    # ---- commands from MQTT ------------------------------------------
    async def handle_command(self, topic, payload):
        prefix = self.base + "/"
        if not topic.startswith(prefix):
            return
        parts = topic[len(prefix):].split("/")
        if len(parts) != 4 or parts[1] != "relay" or parts[3] != "set":
            return
        dev = self.devices.get(parts[0])
        if dev is None:
            return
        try:
            index = int(parts[2])
            await dev.send({
                "command": "set_relay",
                "index": index,
                "state": payload.strip().upper() in ("ON", "1", "TRUE"),
            })
        except Exception as e:
            log.warning("[%s] command failed: %s", dev.id, e)

    # ---- management API ----------------------------------------------
    def validate_device(self, data, existing_id=None):
        name = str(data.get("name") or "").strip()
        ip = str(data.get("ip") or "").strip()
        if not ip:
            raise ValueError("IP address or hostname is required")
        try:
            port = int(data.get("port") or 81)
            relays = int(data.get("relays") or 6)
        except (TypeError, ValueError):
            raise ValueError("Port and relay count must be numbers")
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if not 1 <= relays <= CLEANUP_RELAYS:
            raise ValueError(f"Relay count must be between 1 and {CLEANUP_RELAYS}")
        if existing_id:
            dev_id = existing_id
        else:
            dev_id = str(data.get("id") or "").strip().lower()
            if not dev_id:
                dev_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
            if not ID_RE.fullmatch(dev_id):
                raise ValueError("ID must be 1-32 characters: lowercase letters, digits, - or _")
        return {"id": dev_id, "name": name, "ip": ip, "port": port, "relays": relays}

    def get_device(self, dev_id):
        dev = self.devices.get(dev_id)
        if dev is None:
            raise LookupError("Device not found")
        return dev

    async def add_device(self, data):
        cfg = self.validate_device(data)
        if cfg["id"] in self.devices:
            raise ValueError("A device with this ID already exists")
        self.cfg.devices.append(cfg)
        self.cfg.save()
        dev = Device(self, cfg)
        self.devices[dev.id] = dev
        dev.start()
        self.publish_discovery(dev)

    async def update_device(self, dev_id, data):
        dev = self.get_device(dev_id)
        new = self.validate_device(data, existing_id=dev_id)
        reconnect = (new["ip"], new["port"]) != (dev.cfg["ip"], dev.cfg["port"])
        dev.cfg.update(new)
        self.cfg.save()
        if reconnect:
            await dev.stop()
            dev.relays = {}
            dev.start()
        self.publish_discovery(dev)

    async def remove_device(self, dev_id):
        dev = self.get_device(dev_id)
        await dev.stop()
        self.remove_discovery(dev)
        del self.devices[dev_id]
        self.cfg.devices.remove(dev.cfg)
        self.cfg.save()

    def update_mqtt(self, data):
        cfg = self.cfg.mqtt
        if not MQTT_MANAGED:
            try:
                port = int(data.get("port") or 1883)
            except (TypeError, ValueError):
                raise ValueError("Port must be a number")
            cfg.update({
                "host": str(data.get("host") or "").strip(),
                "port": port,
                "username": str(data.get("username") or "").strip(),
            })
            if data.get("password"):  # blank means "keep the current password"
                cfg["password"] = str(data["password"])
            if data.get("clear_password"):
                cfg["password"] = ""
        cfg.update({
            "discovery_prefix": str(data.get("discovery_prefix") or "homeassistant").strip("/ "),
            "base_topic": str(data.get("base_topic") or "cubixx").strip("/ "),
        })
        self.cfg.save()
        self.mqtt.start()

    def snapshot(self):
        m = dict(self.cfg.mqtt)
        m["password_set"] = bool(m.pop("password", ""))
        m["managed"] = MQTT_MANAGED
        m["connected"] = self.mqtt.connected
        m["error"] = self.mqtt.error
        return {"mqtt": m, "devices": [d.status() for d in self.devices.values()]}


# ---- HTTP ---------------------------------------------------------------
def api(handler):
    async def wrapper(request):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except LookupError as e:
            return web.json_response({"error": str(e.args[0])}, status=404)
        except ConnectionError as e:
            return web.json_response({"error": str(e)}, status=409)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            log.exception("Unhandled error")
            return web.json_response({"error": "Internal error"}, status=500)
    return wrapper


def build_app(manager):
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(request):
        return web.FileResponse(STATIC_DIR / "index.html")

    @routes.get("/api/state")
    @api
    async def state(request):
        return web.json_response(manager.snapshot())

    @routes.put("/api/mqtt")
    @api
    async def put_mqtt(request):
        manager.update_mqtt(await request.json())
        return web.json_response({"ok": True})

    @routes.post("/api/devices")
    @api
    async def add_device(request):
        await manager.add_device(await request.json())
        return web.json_response({"ok": True})

    @routes.put("/api/devices/{id}")
    @api
    async def edit_device(request):
        await manager.update_device(request.match_info["id"], await request.json())
        return web.json_response({"ok": True})

    @routes.delete("/api/devices/{id}")
    @api
    async def delete_device(request):
        await manager.remove_device(request.match_info["id"])
        return web.json_response({"ok": True})

    @routes.post("/api/devices/{id}/relay/{n}")
    @api
    async def set_relay(request):
        dev = manager.get_device(request.match_info["id"])
        body = await request.json()
        await dev.send({
            "command": "set_relay",
            "index": int(request.match_info["n"]),
            "state": bool(body.get("state")),
        })
        return web.json_response({"ok": True})

    app = web.Application()
    app.add_routes(routes)
    return app


async def main():
    manager = Manager()
    await manager.start()
    runner = web.AppRunner(build_app(manager))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()
    log.info("Web UI listening on port %d", HTTP_PORT)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Shutting down")
    await manager.shutdown()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
