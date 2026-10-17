# Orange Pi: ESP API server setup

The Orange Pi is the gateway between the LLM server and the ESP-NOW devices:

```
LLM server (Mercury tool)                 Orange Pi                                        USB             ESP-NOW
tools/mercury.py --HTTP :80--> Nginx --> Gunicorn: esp_api_server.py --MQTT--> espnow_serial_bridge.py --> XIAO ESP32-C3 ~~~> ESP-01S relay
```

| File in this folder | What it is |
|---|---|
| `esp_api_server.py` | Flask app: HTTP → MQTT, optionally waits for the device's reply |
| `espnow_serial_bridge.py` | MQTT ↔ USB serial to the antenna |
| `gunicorn_config.py` | Gunicorn settings (one worker, threads) |
| `deploy/esp-api.service` | systemd unit for the API |
| `deploy/espnow-bridge.service` | systemd unit for the bridge |
| `deploy/nginx-esp-api` | Nginx site, port 80 → 127.0.0.1:8000 |
| `deploy/logrotate-esp-api` | log rotation for Gunicorn's logs |
| `deploy/esp-api.env.example` | where the API key lives |

The steps assume the user `waterlogged` and a Debian/Ubuntu-based Orange Pi image
(Armbian, Orange Pi OS, Ubuntu). If your user differs, change it in the two `.service`
files and in `deploy/logrotate-esp-api`.

## 1. Packages

```bash
sudo apt update
sudo apt install -y git python3-venv python3-full mosquitto mosquitto-clients nginx
sudo systemctl enable --now mosquitto
```

Mosquitto 2.x accepts connections only from the Pi itself by default, which is all this
needs: the API and the bridge both run on the Pi.

## 2. Code and Python environment

```bash
cd ~
git clone -b iot https://github.com/WaterloggedRobots/Custom-ChatUI.git
python3 -m venv ~/esp-gateway-env
~/esp-gateway-env/bin/pip install flask gunicorn paho-mqtt pyserial
```

Later updates are `cd ~/Custom-ChatUI && git pull`, then
`sudo systemctl restart esp-api espnow-bridge`.

Notes:
- Flask must be installed: the API is a Flask app. eventlet/gevent aren't needed; the
  `gthread` worker in `gunicorn_config.py` handles concurrent requests.
- paho-mqtt 1.x and 2.x both work.

## 3. Serial port access (bridge)

Plug the XIAO ESP32-C3 antenna into the Pi's USB and find its port:

```bash
ls /dev/ttyACM* /dev/ttyUSB*      # the XIAO is usually /dev/ttyACM0
sudo usermod -aG dialout waterlogged   # log out and in again afterwards
```

If the port isn't `/dev/ttyACM0`, change `ESPNOW_SERIAL` in `deploy/espnow-bridge.service`.

## 4. Try it by hand first

```bash
cd ~/Custom-ChatUI/IoT/pi
# terminal 1: the bridge
~/esp-gateway-env/bin/python espnow_serial_bridge.py
# terminal 2: the API, logging into this folder instead of /var/log
ESP_API_LOG_DIR=/tmp/esp-api ~/esp-gateway-env/bin/python esp_api_server.py
# terminal 3
curl http://127.0.0.1:8000/api/health
```

`mqtt_connected` should be `true`. Stop both with Ctrl+C.

## 5. API key

Anyone on your LAN can reach port 80, so set a key:

```bash
sudo cp deploy/esp-api.env.example /etc/esp-api.env
sudo nano /etc/esp-api.env          # set ESP_API_TOKEN to a long random string
sudo chmod 600 /etc/esp-api.env
```

Put the same value in `ESP_API_TOKEN` in `Server/ollama/.env` on the LLM server.
Callers send it as the `X-API-Key` header. `/api/health` never needs it.

## 6. Services

```bash
cd ~/Custom-ChatUI/IoT/pi

# log directory (Gunicorn and the app write here)
sudo mkdir -p /var/log/esp-api
sudo chown waterlogged:waterlogged /var/log/esp-api

sudo cp deploy/esp-api.service deploy/espnow-bridge.service /etc/systemd/system/
sudo cp deploy/logrotate-esp-api /etc/logrotate.d/esp-api
sudo systemctl daemon-reload
sudo systemctl enable --now espnow-bridge esp-api

systemctl status esp-api espnow-bridge
journalctl -u esp-api -u espnow-bridge -f
```

Log files: `/var/log/esp-api/esp-api.log` (the app, which rotates it itself) and
`gunicorn-access.log` / `gunicorn-error.log` (rotated daily by logrotate).

## 7. Nginx

```bash
sudo cp deploy/nginx-esp-api /etc/nginx/sites-available/esp-api
sudo ln -sf /etc/nginx/sites-available/esp-api /etc/nginx/sites-enabled/esp-api
sudo rm -f /etc/nginx/sites-enabled/default    # it also claims port 80
sudo nginx -t && sudo systemctl reload nginx
```

## 8. Test

On the Pi:

```bash
curl http://localhost/health
# {"mqtt_connected":true,"service":"esp-api","status":"healthy","timestamp":...}

# watch everything that crosses MQTT (in another terminal)
mosquitto_sub -v -t 'espnow/#'

# press the relay and wait up to 5 s for its answer
curl -X POST 'http://localhost/api/device/AA:BB:CC:DD:EE:FF/command?wait=5' \
  -H 'Content-Type: application/json' -H 'X-API-Key: <your key>' \
  -d '{"action":"on"}'
```

Use the relay's MAC (the ESP-01S prints `[PAIR] MAC: ...` for 10 s after boot). From the
LLM server, replace `localhost` with the Pi's address (`http://<orange-pi-ip>`), or run
`.venv/bin/python tools/mercury.py on` in `Server/ollama`.

## API

| Request | Answer |
|---|---|
| `GET /api/health` (or `/health` via Nginx) | `{"status":"healthy","mqtt_connected":true,...}` |
| `POST /api/device/<MAC>/command` body `{"action":"on"}` | `202` `"status":"sent"` (no waiting) |
| same with `?wait=5` (max 15 s) | `200` `"acknowledged"` with the device's `reply`, `504` `"no_reply"`, `502` `"failed"` (antenna couldn't send) |
| `POST /api/broadcast` body `{...}` | `202` `"broadcast_sent"`, replies aren't collected |
| any of the above when MQTT is down | `503` |
| missing or wrong `X-API-Key` (when a key is set) | `401` |

The relay understands `{"action": "on" | "off" | "restart"}`. The body can be any JSON the
device understands, up to 250 bytes (the ESP-NOW limit). MQTT topics and the serial format
are in [../README.md](../README.md).

## Corrections to the earlier setup notes

These are the things that would have broken or misbehaved in the old notes; the files in
this folder already have them right.

- **The startup patch isn't needed, and would log a false error.** `esp_api_server.py` now
  connects to MQTT when Gunicorn imports it. `connect_mqtt()` connects in the background
  and returns nothing, so `if not connect_mqtt()` would always log "Failed to connect".
- **Flask was never installed.** The venv only got pyserial, paho-mqtt and gunicorn.
- **Gunicorn must run one worker.** `cpu_count() * 2 + 1` workers each open their own MQTT
  connection, so a device reply often lands in a different worker from the request waiting
  for it and the request times out. The config uses 1 worker with 8 threads.
- **eventlet isn't needed,** and its monkey-patching doesn't mix well with paho's network thread.
- **`Connection "upgrade"` in Nginx** was sent on every request, which defeats the upstream
  keepalive. Nothing here uses WebSockets, so it's now `Connection ""`.
- **The default Nginx site** also listens on port 80 and has to be removed.
- **logrotate** rotated `esp-api.log`, which the app already rotates itself, and ran
  `systemctl reload` (a full worker restart that drops the MQTT connection and any waiting
  request). It now rotates only Gunicorn's logs, tells Gunicorn to reopen them with USR1,
  and has the `su` line logrotate needs for a directory not owned by root.
- **systemd unit:** `NotifyAccess=main` was missing for `Type=notify`; `PATH` was only the
  venv, so plain system tools weren't found; `WorkingDirectory` and `--chdir` pointed at
  different folders; the `After=`/`Wants=` lines were split in two; the key is now in a
  root-only file instead of the unit.
- **Gunicorn config:** `user`/`group`/`daemon`/`pidfile` belong to systemd and are gone.
- **The bridge** (`esp_gateway_bridge.py` in the notes, now `espnow_serial_bridge.py`) had
  no service, so nothing carried commands to the antenna after a reboot.
- **Test command:** the body `{"relay":1,"state":1}` isn't understood by the relay sketch;
  it's `{"action":"on"}`. Without `?wait=` the answer is `202 sent`, not a confirmation.
- **Mosquitto** has to be installed before the API starts; it's now step 1.
