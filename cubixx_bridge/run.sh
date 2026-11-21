#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

export DATA_DIR=/data
export PORT=8080

if bashio::config.has_value 'mqtt_host'; then
    # Manual override from the app's Configuration tab
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT=1883
    if bashio::config.has_value 'mqtt_port'; then
        MQTT_PORT="$(bashio::config 'mqtt_port')"
    fi
    export MQTT_USER=""
    export MQTT_PASS=""
    if bashio::config.has_value 'mqtt_username'; then
        MQTT_USER="$(bashio::config 'mqtt_username')"
    fi
    if bashio::config.has_value 'mqtt_password'; then
        MQTT_PASS="$(bashio::config 'mqtt_password')"
    fi
    export MQTT_MANAGED=1
    bashio::log.info "Using manually configured MQTT broker ${MQTT_HOST}:${MQTT_PORT}"
elif bashio::services.available "mqtt"; then
    # Credentials supplied automatically by the Home Assistant Supervisor
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASS="$(bashio::services mqtt 'password')"
    export MQTT_MANAGED=1
    bashio::log.info "Using the Home Assistant MQTT broker at ${MQTT_HOST}:${MQTT_PORT}"
else
    bashio::log.warning "No MQTT broker found. Install the Mosquitto broker app, or set a broker in this app's Configuration tab."
fi

bashio::log.info "Starting Cubixx MQTT Bridge..."
exec python3 /srv/app/main.py
