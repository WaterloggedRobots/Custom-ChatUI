#!/usr/bin/env python3
"""MQTT <-> serial bridge between the ESP API server and the ESP-NOW antenna (XIAO ESP32-C3).

  esp_api_server.py --MQTT--> this bridge --USB serial--> antenna --ESP-NOW--> device

MQTT in   espnow/command/<MAC or "broadcast">   {"request_id": "...", "payload": {...}}
          (a body without "payload" is sent whole as the payload, as older callers did)
Serial    {"request_id", "target_mac", "payload"}            one JSON line per command
Serial in {"request_id", "type": "response"|"error", ...}    one JSON line per reply
MQTT out  espnow/response/<MAC>   (errors without a MAC go to espnow/response/unknown)

Lines from the antenna that aren't JSON are its logs and are only logged here.

Settings (environment): MQTT_BROKER (localhost), MQTT_PORT (1883),
ESPNOW_SERIAL (/dev/ttyACM0), ESPNOW_BAUD (115200).
Needs: pip install pyserial paho-mqtt
"""

import json
import logging
import os
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import serial

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
SERIAL_PORT = os.environ.get("ESPNOW_SERIAL", "/dev/ttyACM0")
BAUD = int(os.environ.get("ESPNOW_BAUD", 115200))
COMMAND_TOPIC = "espnow/command/"
RESPONSE_TOPIC = "espnow/response/"
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("espnow-bridge")

write_lock = threading.Lock()
port = None


def make_client(client_id):
    """paho-mqtt 2.x needs the callback API version; 1.x doesn't know the argument."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def open_serial():
    while True:
        try:
            s = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
            log.info("antenna connected on %s", SERIAL_PORT)
            return s
        except serial.SerialException as e:
            log.warning("antenna not available on %s (%s); retrying in 3 s", SERIAL_PORT, e)
            time.sleep(3)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(COMMAND_TOPIC + "#", qos=1)
        client.publish("espnow/bridge/status", "online", retain=True)
        log.info("MQTT connected; listening on %s#", COMMAND_TOPIC)
    else:
        log.error("MQTT connection failed: rc=%s", rc)


def on_command(client, userdata, msg):
    target = msg.topic[len(COMMAND_TOPIC):]
    mac = BROADCAST_MAC if target.lower() == "broadcast" else target.upper()
    try:
        body = json.loads(msg.payload or b"{}")
    except ValueError:
        log.error("ignored a non-JSON command on %s", msg.topic)
        return
    if isinstance(body, dict) and "payload" in body:
        request_id, payload = str(body.get("request_id") or uuid.uuid4().hex[:12]), body["payload"]
    else:
        request_id, payload = uuid.uuid4().hex[:12], body
    line = json.dumps({"request_id": request_id, "target_mac": mac, "payload": payload}, separators=(",", ":"))
    if len(json.dumps(payload, separators=(",", ":"))) > 250:
        log.error("payload for %s is over ESP-NOW's 250 bytes; not sent", mac)
        return
    with write_lock:
        port.write((line + "\n").encode())
    log.info("-> %s %s", mac, line)


def read_antenna(client):
    global port
    while True:
        try:
            raw = port.readline()
        except serial.SerialException as e:
            log.error("antenna disconnected (%s)", e)
            port.close()
            port = open_serial()
            continue
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            continue
        if not text.startswith("{"):
            log.debug("antenna: %s", text)
            continue
        try:
            reply = json.loads(text)
        except ValueError:
            log.debug("antenna (bad JSON): %s", text)
            continue
        mac = reply.get("from_mac") or "unknown"
        client.publish(RESPONSE_TOPIC + mac, json.dumps(reply), qos=1)
        log.info("<- %s %s", mac, text)


def main():
    global port
    port = open_serial()
    client = make_client(f"espnow-bridge-{os.getpid()}")
    client.on_connect = on_connect
    client.on_message = on_command
    client.will_set("espnow/bridge/status", "offline", retain=True)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    read_antenna(client)


if __name__ == "__main__":
    main()
