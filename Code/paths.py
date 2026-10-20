"""Every folder and network address the ChatUI client uses, in one place.

There are three places the client reads or writes:

  CLIENT_REPO_DIR   The client's own files from the git clone (Code/): UI layouts,
                    images and the default network settings. Read-only at runtime.
                    In the packaged ChatUI.exe this is PyInstaller's unpack folder.

  CLIENT_DATA_DIR   Your chats (each with its own settings), your server address and
                    the audio of replies read aloud (Audio/) on this PC:
                    Windows: %APPDATA%\\Waterlogged\\ChatUI   (AppData\\Roaming)
                    Linux:   ~/.local/share/Waterlogged/ChatUI
                    (the same folder Qt's AppDataLocation gave earlier versions)

  The LLM server    Reached over the network at SERVER_IP:SERVER_PORT. It runs
                    Server/ollama/ on the Ubuntu machine; see network.json.

The NAS has an address entry in network.json, but nothing in the client
uses it yet.
"""

import json
import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Client git folder (app files, read-only)
# --------------------------------------------------------------------------
CLIENT_REPO_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DIR = CLIENT_REPO_DIR / "UI"
IMG_DIR = CLIENT_REPO_DIR / "Img"
DEFAULT_NETWORK_FILE = UI_DIR / "network.json"     # shipped defaults


def ui_file(name: str) -> str:
    """Path of a Qt Designer layout, e.g. ui_file("Chat Window.ui")."""
    return str(UI_DIR / name)


# --------------------------------------------------------------------------
# Client data folder (chats and your server address)
# --------------------------------------------------------------------------
if sys.platform == "win32":
    CLIENT_DATA_DIR = Path(os.environ["APPDATA"]) / "Waterlogged" / "ChatUI"
else:
    CLIENT_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Waterlogged" / "ChatUI"

SAVE_DIR = CLIENT_DATA_DIR / "Save"
CHAT_DIR = SAVE_DIR / "Chat"                  # one <name>.json per chat, settings included
CHAT_INDEX_FILE = SAVE_DIR / ".temp.json"     # last opened chat + recent-chats order
USER_NETWORK_FILE = CLIENT_DATA_DIR / "network.json"   # your saved server address
AUDIO_DIR = CLIENT_DATA_DIR / "Audio"         # replies read aloud by the server, kept for replays


def ensure_data_dirs():
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    if not CHAT_INDEX_FILE.exists():
        CHAT_INDEX_FILE.write_text(json.dumps({"LastOpened": "", "LastChat": "", "ChatList": []}, indent=4),
                                   encoding="utf-8")


# --------------------------------------------------------------------------
# Network addresses (LLM server, NAS)
# --------------------------------------------------------------------------
# Defaults live in Code/UI/network.json. "Server > Set IP" saves your choice to
# CLIENT_DATA_DIR/network.json, which wins over the defaults (the packaged app
# can't write to its own folder).

def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def default_network() -> dict:
    return _read(DEFAULT_NETWORK_FILE)


def load_network() -> dict:
    net = default_network()
    for key, value in _read(USER_NETWORK_FILE).items():
        if isinstance(value, dict):
            net[key] = {**net.get(key, {}), **value}
    return net


def save_server_address(ip: str, port: int):
    user = _read(USER_NETWORK_FILE)
    user["llm_server"] = {"ip": ip, "port": port}
    USER_NETWORK_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_NETWORK_FILE.write_text(json.dumps(user, indent=4), encoding="utf-8")


def split_address(text: str, default_port: int):
    """'192.168.0.192:8100' or '192.168.0.192' -> (ip, port)."""
    text = text.strip().removeprefix("http://").rstrip("/")
    host, _, port = text.partition(":")
    return host, int(port) if port.isdigit() else default_port
