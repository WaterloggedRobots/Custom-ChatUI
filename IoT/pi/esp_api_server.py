#!/usr/bin/env python3
"""
Production ESP API Server (Raspberry Pi)
With logging, error handling, and production configuration

  caller (Mercury tool) --HTTP--> this server --MQTT--> espnow_serial_bridge.py --serial--> antenna

Changes from the earlier version:
- MQTT now connects when the module is imported, so it also works under Gunicorn.
  Before, connect_mqtt() only ran in `if __name__ == "__main__"`, which Gunicorn never
  executes, so every command answered 503 "MQTT connection unavailable".
- Works with paho-mqtt 1.x and 2.x (2.x requires the callback API version).
- Each command gets a request_id and is published as {"request_id", "payload"}.
- `?wait=SECONDS` (max 15) waits for the device's first reply and returns it, so the
  caller can tell "the relay acknowledged" from "sent, but nobody answered".
- Optional API key: set ESP_API_TOKEN and send it as the X-API-Key header.

Run: gunicorn -w 1 -b 127.0.0.1:8000 esp_api_server:app   (one worker: MQTT state is per process)
"""

import os
import sys
import logging
import json
import time
import threading
import uuid
import atexit
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify
import paho.mqtt.client as mqtt

# ============================================================================
# Configuration
# ============================================================================

MQTT_BROKER = os.environ.get('MQTT_BROKER', 'localhost')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))
MQTT_COMMAND_TOPIC = "espnow/command/"
MQTT_RESPONSE_TOPIC = "espnow/response/"

API_HOST = os.environ.get('API_HOST', '127.0.0.1')  # Only localhost, Nginx will proxy
API_PORT = int(os.environ.get('API_PORT', 8000))
API_TOKEN = os.environ.get('ESP_API_TOKEN', '')       # empty = no key required
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
MAX_WAIT = 15.0

LOG_DIR = os.environ.get('ESP_API_LOG_DIR', '/var/log/esp-api')
LOG_FILE = f'{LOG_DIR}/esp-api.log'

# ============================================================================
# Setup Logging
# ============================================================================

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('esp-api')

# ============================================================================
# MQTT
# ============================================================================

app = Flask(__name__)

mqtt_client = None
mqtt_connected = False

# request_id -> {"event": Event, "reply": dict | None} for callers waiting on a reply
waiting = {}
waiting_lock = threading.Lock()


def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        client.subscribe(MQTT_RESPONSE_TOPIC + "#", qos=1)
        client.publish("espnow/api/status", "online", retain=True)
        logger.info("Connected to MQTT broker")
    else:
        mqtt_connected = False
        logger.error(f"MQTT connection failed with code {rc}")


def on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    logger.warning("Disconnected from MQTT broker (paho reconnects on its own)")


def on_mqtt_message(client, userdata, msg):
    """Replies from devices, via the serial bridge."""
    try:
        reply = json.loads(msg.payload)
    except ValueError:
        return
    # The device's own text arrives as a string; decode it when it is JSON.
    if isinstance(reply.get("payload"), str):
        try:
            reply["payload"] = json.loads(reply["payload"])
        except ValueError:
            pass
    with waiting_lock:
        slot = waiting.get(reply.get("request_id"))
        if slot and slot["reply"] is None:
            slot["reply"] = reply
            slot["event"].set()
    logger.info(f"Reply on {msg.topic}: {reply}")


def make_client(client_id):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id, clean_session=True)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id, clean_session=True)


def connect_mqtt():
    """Start the MQTT connection in the background; paho keeps retrying if the broker is down."""
    global mqtt_client
    mqtt_client = make_client(f"esp-api-{os.getpid()}")
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_disconnect = on_mqtt_disconnect
    mqtt_client.on_message = on_mqtt_message
    mqtt_client.will_set("espnow/api/status", "offline", retain=True)
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()


def publish_command(target, payload, request_id):
    if not mqtt_connected or not mqtt_client:
        logger.error("MQTT not connected")
        return False
    topic = f"{MQTT_COMMAND_TOPIC}{target}"
    try:
        info = mqtt_client.publish(topic, json.dumps({"request_id": request_id, "payload": payload}), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(f"Publish to {topic} failed: rc={info.rc}")
            return False
        logger.info(f"Published to {topic}: {request_id} {payload}")
        return True
    except Exception as e:
        logger.error(f"Failed to publish: {e}")
        return False


def send_and_maybe_wait(target, payload):
    """Publishes the command; with ?wait=N also waits up to N seconds for the first reply."""
    try:
        wait = min(float(request.args.get('wait', 0)), MAX_WAIT)
    except ValueError:
        return jsonify({"error": "wait must be a number of seconds"}), 400
    request_id = uuid.uuid4().hex[:12]
    slot = {"event": threading.Event(), "reply": None}
    if wait > 0:
        with waiting_lock:
            waiting[request_id] = slot
    try:
        if not publish_command(target, payload, request_id):
            return jsonify({"error": "MQTT connection unavailable", "status": "failed"}), 503
        body = {"status": "sent", "device": target, "command": payload,
                "request_id": request_id, "timestamp": time.time()}
        if wait <= 0:
            return jsonify(body), 202
        if not slot["event"].wait(wait):
            body["status"] = "no_reply"
            return jsonify(body), 504
        reply = slot["reply"]
        body["reply"] = reply
        if reply.get("type") == "error":
            body["status"] = "failed"
            return jsonify(body), 502
        body["status"] = "acknowledged"
        return jsonify(body), 200
    finally:
        with waiting_lock:
            waiting.pop(request_id, None)


# ============================================================================
# API Routes
# ============================================================================

@app.before_request
def check_request():
    logger.info(f"{request.method} {request.path} from {request.remote_addr}")
    if API_TOKEN and request.path != '/api/health' and request.method != 'OPTIONS':
        if request.headers.get('X-API-Key') != API_TOKEN:
            return jsonify({"error": "missing or wrong X-API-Key"}), 401


@app.after_request
def add_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "esp-api",
        "mqtt_connected": mqtt_connected,
        "timestamp": time.time()
    })


@app.route('/api/device/<device_mac>/command', methods=['POST'])
def send_command(device_mac):
    """
    Send a command to one device
    POST /api/device/AA:BB:CC:DD:EE:FF/command?wait=5
    Body: {"action": "on"}   (any JSON the device understands, at most 250 bytes)
    """
    parts = device_mac.split(':')
    if len(parts) != 6 or not all(len(p) == 2 and all(c in '0123456789abcdefABCDEF' for c in p) for p in parts):
        return jsonify({"error": "Invalid MAC format. Use AA:BB:CC:DD:EE:FF"}), 400
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    return send_and_maybe_wait(device_mac.upper(), request.get_json())


@app.route('/api/broadcast', methods=['POST'])
def broadcast_command():
    """Broadcast a command to all devices (replies are not collected)."""
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    request_id = uuid.uuid4().hex[:12]
    command = request.get_json()
    if not publish_command("broadcast", command, request_id):
        return jsonify({"error": "MQTT unavailable"}), 503
    return jsonify({"status": "broadcast_sent", "command": command,
                    "request_id": request_id, "timestamp": time.time()}), 202


@app.route('/api/device/<device_mac>/command', methods=['OPTIONS'])
def options_handler(device_mac):
    response = jsonify({})
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
    return response


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ============================================================================
# Startup and shutdown
# ============================================================================

def shutdown():
    logger.info("Shutting down...")
    if mqtt_client:
        mqtt_client.publish("espnow/api/status", "offline", retain=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    logger.info("Shutdown complete")


atexit.register(shutdown)
connect_mqtt()   # at import time, so Gunicorn workers connect too

if __name__ == "__main__":
    logger.info(f"Starting API server on {API_HOST}:{API_PORT} (use Gunicorn in production)")
    app.run(host=API_HOST, port=API_PORT, debug=DEBUG, use_reloader=False)
