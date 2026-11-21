# Cubixx MQTT Bridge

[![Add repository to my Home Assistant][repo-badge]][repo-url]

Connects **Cubixx relay switches** (such as the 6-gang relay) to **MQTT** and **Home Assistant**.
Cubixx firmware has no MQTT settings of its own, so this bridge talks to each switch over its
local WebSocket, mirrors the relays to your MQTT broker, and publishes Home Assistant MQTT
Discovery messages so every relay shows up as a switch entity.

- Live two-way state: pressing the physical button or using the switch's web page updates Home Assistant immediately
- Any number of switches, managed from a small web UI (add, edit, delete, see connection status)
- Home Assistant discovery: entities, device, availability. Deleting a switch removes its entities
- Runs as a **Home Assistant app (add-on)** or as a **standalone Docker container**
- Local only: no cloud, no login

## Choose how to run it

| | Home Assistant app | Docker |
|---|---|---|
| Best for | Home Assistant OS / Supervised | Any Docker host |
| MQTT broker setup | Automatic (uses the Mosquitto app) | Enter it once in the web UI |
| Web UI | In the Home Assistant sidebar | `http://<host>:8080` |
| Backups | Included in Home Assistant backups | Back up the `data` folder |

Run **one or the other**, not both at the same time, or they will fight over the same MQTT topics.

## Option A: Home Assistant app

Requires Home Assistant OS or Supervised, plus the **Mosquitto broker** app with the MQTT integration.

1. Click the badge at the top of this page, or in Home Assistant go to
   **Settings → Apps → App store → ⋮ → Repositories** and add:

   ```
   https://github.com/danielbn21/cubixx-mqtt-bridge
   ```

2. Find **Cubixx MQTT Bridge** in the store (you may need to reload the page), then **Install** and **Start**.
   The first install builds the image on your device, which takes a minute or two.
3. Enable **Show in sidebar**, open **Cubixx**, and click **Add switch**.

The MQTT connection is configured automatically. To use a different broker, set it in the app's
**Configuration** tab.

Updates appear in Home Assistant when the `version` in `cubixx_bridge/config.yaml` changes.

## Option B: Docker

```bash
git clone https://github.com/danielbn21/cubixx-mqtt-bridge.git
cd cubixx-mqtt-bridge
docker compose up -d --build
```

Open `http://<docker-host>:8080`, enter your broker under **Broker settings**
(host, port, username, password), then click **Add switch**. Settings are stored in `./data/config.json`.

You can pre-fill the broker with environment variables instead:

| Variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | | Broker host or IP |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` / `MQTT_PASS` | | Broker credentials |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant discovery prefix |
| `MQTT_BASE_TOPIC` | `cubixx` | Base for the bridge's own topics |
| `PORT` | `8080` | Web UI port |
| `DATA_DIR` | `/data` | Where `config.json` is stored |

The web UI has no login. Keep it on your local network and do not expose it to the internet.

## Adding a switch

In the UI click **Add switch** and enter:

- **Name**: shown in Home Assistant
- **IP address**: of the Cubixx switch (give it a DHCP reservation so it does not change)
- **Device ID**: used in MQTT topics, for example the ID shown on the switch's Controls page. It cannot be changed later
- **WebSocket port** (default `81`) and **number of relays** (default `6`)

Reuse the same device ID if you are replacing an older setup, and the entities keep their IDs, areas and history.

## MQTT topics

The base topic defaults to `cubixx`. Relay numbers start at 0.

```
cubixx/<id>/relay/<n>/state    ON | OFF
cubixx/<id>/relay/<n>/set      ON | OFF
cubixx/<id>/status             online | offline
cubixx/bridge/status           online | offline
```

Discovery messages are published to `homeassistant/switch/cubixx_<id>_<n>/config`.

## How it works

The Cubixx web page uses a WebSocket on port 81 (`ws://<ip>:81/`). The bridge speaks the same protocol:

```
-> {"command":"request_state"}
<- {"event":"full_state","relays":[{"index":0,"state":true,"name":"..."}, ...]}
-> {"command":"set_relay","index":0,"state":true}
<- {"event":"relay_update","relay":{"index":0,"state":true}}
-> {"command":"ping"}            <- {"event":"pong"}
```

This was worked out from the switch's own web page (firmware 4.22.22). Other firmware versions
or Cubixx models may differ.

## Troubleshooting

- **Switch shows Offline in the UI**: check the IP and port, and that the Docker host or Home Assistant can reach the switch. The error is shown on the switch's card.
- **Broker shows Disconnected**: check host, port and credentials. In Home Assistant, make sure the Mosquitto broker app is running.
- **Entities do not appear in Home Assistant**: confirm the MQTT integration has discovery enabled and the discovery prefix matches (`homeassistant` by default).
- **Entities show unavailable**: the bridge or that switch is offline. Availability follows the connection to the switch.

## Development

Both the Docker image and the Home Assistant app use the same code in `cubixx_bridge/app`.

```bash
pip install -r cubixx_bridge/requirements.txt
DATA_DIR=./data PORT=8080 python cubixx_bridge/app/main.py
```

```
.
├── repository.yaml          # makes this a Home Assistant app repository
├── Dockerfile               # standalone Docker image
├── docker-compose.yml
└── cubixx_bridge/           # the Home Assistant app
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    ├── DOCS.md
    ├── CHANGELOG.md
    ├── translations/
    ├── requirements.txt
    └── app/                 # shared bridge code and web UI
        ├── main.py
        └── static/index.html
```

When you change the code, bump `version` in `cubixx_bridge/config.yaml` and add a line to
`cubixx_bridge/CHANGELOG.md` so Home Assistant offers the update.

[repo-badge]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[repo-url]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdanielbn21%2Fcubixx-mqtt-bridge
