"""Mercury power control: presses Mercury's power button through the ESP-01S relay.

  this script --HTTP--> Pi ESP API (esp_api_server.py) --MQTT--> serial bridge
              --USB--> ESP-NOW antenna --ESP-NOW--> ESP-01S relay on Mercury's power switch

Actions: "on" (short press), "off" (long press, forces power off), "restart" (long press,
pause, short press). The press timings live in the relay sketch (IoT/esp01s_relay).

`TOOL` is the one definition of this tool for the model (Ollama/OpenAI tool format), so
the name and argument stay the same everywhere. `run(action)` does the work and returns a
dict the model can read back.

Settings (environment or Server/ollama/.env):
  ESP_API_URL     Pi API address, e.g. http://192.168.0.50 (behind Nginx) or http://192.168.0.50:8000
  ESP_API_TOKEN   the Pi's X-API-Key, if it has one
  MERCURY_MAC     the relay's MAC, printed on its serial port for 10 s after boot

Command line:  python tools/mercury.py on|off|restart
"""

import os
import sys

import httpx

ACTIONS = ("on", "off", "restart")
WAIT_S = 5          # how long the Pi waits for the relay's acknowledgement

TOOL = {
    "type": "function",
    "function": {
        "name": "mercury_power",
        "description": (
            "Turn the Mercury machine on, off, or restart it, by pressing its power button "
            "through a relay. Use only when the user clearly asks to change Mercury's power."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "on = start / power on; off = shut down / power off; "
                                   "restart = reboot / reset (never 'on' for a reboot).",
                }
            },
            "required": ["action"],
        },
    },
}


def run(action: str, timeout: float = WAIT_S + 5) -> dict:
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        return {"ok": False, "error": f"action must be one of {', '.join(ACTIONS)}"}
    base = os.environ.get("ESP_API_URL", "").rstrip("/")
    mac = os.environ.get("MERCURY_MAC", "").strip().upper()
    if not base or not mac:
        return {"ok": False, "error": "set ESP_API_URL and MERCURY_MAC in Server/ollama/.env"}
    headers = {"X-API-Key": os.environ["ESP_API_TOKEN"]} if os.environ.get("ESP_API_TOKEN") else {}
    try:
        r = httpx.post(f"{base}/api/device/{mac}/command", params={"wait": WAIT_S},
                       json={"action": action}, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"can't reach the ESP API at {base}: {e}"}
    try:
        body = r.json()
    except ValueError:
        body = {"error": r.text[:300]}
    status = body.get("status")
    if r.status_code == 200 and status == "acknowledged":
        reply = (body.get("reply") or {}).get("payload") or {}
        if isinstance(reply, dict) and reply.get("ok") is False:
            return {"ok": False, "error": f"the relay refused: {reply.get('error')}"}
        secs = reply.get("ms", 0) / 1000 if isinstance(reply, dict) else 0
        return {"ok": True, "action": action, "result": "the relay acknowledged and is pressing the button"
                + (f" ({secs:g} s)" if secs else "")}
    if r.status_code == 202:   # an older Pi API without ?wait support
        return {"ok": True, "action": action, "result": "sent; this Pi API can't confirm delivery"}
    if status == "no_reply":
        return {"ok": False, "error": "sent, but the relay didn't answer (out of range, unpowered, or wrong MAC)"}
    return {"ok": False, "error": body.get("error") or (body.get("reply") or {}).get("message")
            or f"ESP API answered {r.status_code}"}


def _load_env_file():
    """For the command line: read Server/ollama/.env (run.sh does this for the server)."""
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.isfile(env):
        for line in open(env, encoding="utf-8"):
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    _load_env_file()
    if len(sys.argv) != 2:
        sys.exit("usage: python tools/mercury.py on|off|restart")
    result = run(sys.argv[1])
    print(result)
    sys.exit(0 if result.get("ok") else 1)
