# Cubixx MQTT Bridge

Connects one or more Cubixx relay switches to Home Assistant through MQTT.
Each relay appears as a switch entity, with live two-way state: pressing the
physical button or using the switch's own web page updates Home Assistant
immediately.

## Setup

1. Install and start the **Mosquitto broker** app and add the MQTT integration
   (Home Assistant usually offers this automatically).
2. Start this app and open **Cubixx** in the sidebar (or "Open Web UI").
3. Click **Add switch** and enter the switch's name, IP address and device ID.
   The WebSocket port (81) and relay count (6) are the defaults.
4. The relays appear under Settings → Devices & Services → MQTT.

The MQTT broker connection is configured automatically. To use a different
broker, set the host (and optionally port, username and password) in this
app's **Configuration** tab.

## Tips

- Give each switch a DHCP reservation in your router so its IP does not change.
- Use the same device ID as before if you are replacing an older setup. Entity
  IDs, areas and history are kept because they are tied to that ID.
- Deleting a switch in the UI also removes its entities from Home Assistant.

## MQTT topics

    cubixx/<id>/relay/<n>/state    ON | OFF   (n starts at 0)
    cubixx/<id>/relay/<n>/set      ON | OFF
    cubixx/<id>/status             online | offline
    cubixx/bridge/status           online | offline

Discovery messages are published under `homeassistant/switch/`.

## Data

Your list of switches is stored in the app's own data volume and is included
in Home Assistant backups.
