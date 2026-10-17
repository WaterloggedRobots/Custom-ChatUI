# IoT: Mercury power control over ESP-NOW

```
ChatUI server (Ubuntu, .192)            Orange Pi                                  ESP-NOW (channel 1)
tools/mercury.py --HTTP--> esp_api_server.py --MQTT--> espnow_serial_bridge.py --USB--> XIAO ESP32-C3 antenna ~~~> ESP-01S relay
                 <-------- reply (?wait=5) <-- espnow/response/<MAC> <----------- serial JSON <------------- ESP-NOW reply
```

| Part | File | Runs on |
|---|---|---|
| Mercury tool (model definition + HTTP call) | `Server/ollama/tools/mercury.py` | LLM server |
| ESP API (HTTP → MQTT, waits for replies) | `IoT/pi/esp_api_server.py` | Orange Pi, behind Nginx |
| MQTT ↔ serial bridge | `IoT/pi/espnow_serial_bridge.py` | Orange Pi |
| Antenna bridge (serial ↔ ESP-NOW) | `IoT/esp_antenna_bridge/esp_antenna_bridge.ino` | XIAO ESP32-C3 on the Orange Pi's USB |
| Relay node | `IoT/esp01s_relay/esp01s_relay.ino` | ESP-01S relay board across Mercury's power button |

## Messages

| Hop | Format |
|---|---|
| Mercury → Pi API | `POST /api/device/<MAC>/command?wait=5`, body `{"action": "on"}`, header `X-API-Key` if set |
| Pi API → MQTT | topic `espnow/command/<MAC>`, `{"request_id": "...", "payload": {"action": "on"}}` |
| Bridge → antenna (serial) | `{"request_id": "...", "target_mac": "<MAC>", "payload": {"action": "on"}}` + newline |
| Antenna → relay (ESP-NOW) | the payload as JSON text, at most 250 bytes |
| Relay → antenna | `{"ok":true,"action":"on","status":"started","ms":500}`, later `..."status":"done"` |
| Antenna → bridge (serial) | `{"request_id", "type": "response", "from_mac", "payload": "<relay text>"}` or `{"request_id", "type": "error", "message"}` |
| Bridge → MQTT | topic `espnow/response/<MAC>` (errors without a MAC: `espnow/response/unknown`) |
| Pi API → Mercury | `200 acknowledged` with the reply, `504 no_reply`, `502 failed`, `503` MQTT down, `401` bad key |

## Relay behaviour (ESP-01S)

The relay is a push button across the power switch:

| Action | Press |
|---|---|
| `on` | short press, `PRESS_MS` = 0.5 s |
| `off` | long press, `HOLD_MS` = 6 s (forces power off) |
| `restart` | long press, `RESTART_GAP_MS` = 3 s pause, short press |

Timings, `RELAY_PIN` (GPIO0) and `RELAY_ACTIVE_LOW` are at the top of the sketch; most
ESP-01S relay boards close on LOW. Commands that arrive while a press sequence runs are
ignored. For 10 s after boot it prints `[PAIR] MAC: ...` every half second: copy that into
`MERCURY_MAC`. After pairing, put the antenna's MAC in `ALLOWED_SENDER` so other ESP-NOW
senders can't press the button.

**The antenna doesn't need reflashing.** The Pi bridge and the relay work with the
antenna sketch that is already on the XIAO: the serial format is the same. The version in
`esp_antenna_bridge/` is optional. It fixes a read past the end of received data (the
relay now sends a closing NUL, so the old sketch is safe), stops the harmless "Failed to add
peer" log on every send after the first, and tags each reply with the request id of the
last command sent to that device instead of the last command overall, which only matters
once several devices are in use.

Arduino IDE boards: "Generic ESP8266 Module" (esp8266 core 3.x) for the relay, "XIAO_ESP32C3"
(esp32 core 3.3.x) for the antenna. Library: ArduinoJson (6 or 7).

## Orange Pi setup

See [pi/README.md](pi/README.md): Mosquitto, the venv, the API under Gunicorn and Nginx,
the bridge, systemd services, log rotation and tests.

## LLM server settings (`Server/ollama/.env`)

```
ESP_API_URL=http://<orange-pi-ip>    # Nginx on port 80
ESP_API_TOKEN=<same as the Pi>
MERCURY_MAC=<from the relay's [PAIR] output>
```

Test without the model: `.venv/bin/python tools/mercury.py on`
