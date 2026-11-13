"""Every folder and network address the ChatUI client uses, in one place.

There are three places the client reads or writes:

  CLIENT_REPO_DIR   The client's own files from the git clone (Code/): UI layouts,
                    images and the default network settings. Read-only at runtime.
                    In the packaged ChatUI.exe this is PyInstaller's unpack folder.

  SAVE_DIR          Your chats (each with its own settings), sections, links, projects
                    and personalities: LLM/Save on the NAS, so every PC sees the
                    same chats (network.json's "nas"):
                    Windows: \\\\<nas ip>\\<share>\\LLM\\Save
                    Linux:   <mount>/LLM/Save, e.g. /mnt/nas/LLM/Save
                    While the NAS can't be reached they go to CLIENT_DATA_DIR/Save,
                    and the window says so.

  CLIENT_DATA_DIR   Your server address, the audio of replies read aloud (Audio/)
                    and the pictures drawn for them (Images/) on this PC:
                    Windows: %APPDATA%\\Waterlogged\\ChatUI   (AppData\\Roaming)
                    Linux:   ~/.local/share/Waterlogged/ChatUI
                    (the same folder Qt's AppDataLocation gave earlier versions)

  The LLM server    Reached over the network at SERVER_IP:SERVER_PORT. It runs
                    Server/ollama/ on the Ubuntu machine; see network.json.

The first time the NAS folder is used, what this PC saved locally before is
copied there (move_local_saves); the local copy stays as a backup.
"""

import json
import os
import shutil
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

LOCAL_SAVE_DIR = CLIENT_DATA_DIR / "Save"
USER_NETWORK_FILE = CLIENT_DATA_DIR / "network.json"   # your saved server address


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def nas_save_dir():
    """Where the NAS keeps the saves, from network.json's "nas"; None if no share is set."""
    nas = {**_read(DEFAULT_NETWORK_FILE).get("nas", {}), **_read(USER_NETWORK_FILE).get("nas", {})}
    parts = [p for p in str(nas.get("save_path") or "").replace("\\", "/").split("/") if p]
    if sys.platform == "win32":
        if not nas.get("ip") or not nas.get("share"):
            return None
        return Path("\\\\" + "\\".join([nas["ip"], nas["share"], *parts]))
    if not nas.get("mount"):
        return None
    return Path(nas["mount"]).joinpath(*parts)


def _pick_save_dir():
    nas = nas_save_dir()
    try:
        if nas and nas.parent.is_dir():
            return nas, True
    except OSError:
        pass
    return LOCAL_SAVE_DIR, False


NAS_SAVE_DIR = nas_save_dir()
SAVE_DIR, SAVE_ON_NAS = _pick_save_dir()
CHAT_DIR = SAVE_DIR / "Chat"                  # one <name>.json per chat, settings included
CHAT_INDEX_FILE = SAVE_DIR / ".temp.json"     # last opened chat, recent-chats order and sections
AUDIO_DIR = CLIENT_DATA_DIR / "Audio"         # replies read aloud by the server, kept for replays
IMAGE_DIR = CLIENT_DATA_DIR / "Images"        # pictures ComfyUI drew for the chats
LINKS_FILE = SAVE_DIR / "links.json"          # projects, personalities and what links to what (chat_links.py)
PROJECT_DIR = SAVE_DIR / "Projects"           # one folder per project, for its memory notes
PERSONALITY_DIR = SAVE_DIR / "Personalities"  # one folder per saved personality (personalities.py)


def chat_memory_dir(chat_name: str) -> Path:
    """The folder of a chat's memory notes: CHAT_DIR/<chat name>/memory."""
    return CHAT_DIR / chat_name / "memory"


def project_memory_dir(name: str) -> Path:
    return PROJECT_DIR / name / "memory"


def personality_memory_dir(name: str) -> Path:
    return PERSONALITY_DIR / name / "memory"


MOVED_MARK = ".copied-to-nas.txt"         # in LOCAL_SAVE_DIR once its files are on the NAS


def move_local_saves(local=None, nas=None) -> int:
    """Copy what this PC saved locally to the NAS folder, once; returns the files copied.
    Files the NAS already has are kept; the chat index and links.json are merged."""
    local, nas = Path(local or LOCAL_SAVE_DIR), Path(nas or SAVE_DIR)
    mark = local / MOVED_MARK
    if local == nas or not local.is_dir() or mark.exists():
        return 0
    copied = 0
    for src in local.rglob("*"):
        dst = nas / src.relative_to(local)
        if src.is_dir() or src == mark:
            continue
        if src.parent == local and src.name in (".temp.json", "links.json") and dst.exists():
            _merge_json(src, dst)
            continue
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    mark.write_text(f"Copied to {nas}; these local files are only a backup now.\n", encoding="utf-8")
    return copied


def _merge_json(src: Path, dst: Path):
    """Lists are joined (the NAS's first), tables keep the NAS's entry where both have one,
    and plain values come from this PC."""
    mine, theirs = _read(src), _read(dst)
    merged = {**theirs}
    for key, value in mine.items():
        old = theirs.get(key)
        if isinstance(value, list) and isinstance(old, list):
            merged[key] = [*old, *[v for v in value if v not in old]]
        elif isinstance(value, dict) and isinstance(old, dict):
            merged[key] = {**value, **old}
        else:
            merged[key] = value
    dst.write_text(json.dumps(merged, indent=4, ensure_ascii=False), encoding="utf-8")


def tidy_index():
    """Drop chats from .temp.json whose files are gone, like those an older version of the app
    listed. The first time that happens the index as it was is kept as .temp.old.json."""
    index = _read(CHAT_INDEX_FILE)
    chats = index.get("ChatList") if isinstance(index.get("ChatList"), list) else []
    kept = [c for c in chats if isinstance(c, str) and (CHAT_DIR / f"{c}.json").is_file()]
    if CHAT_INDEX_FILE.exists() and kept == chats:
        return
    backup = CHAT_INDEX_FILE.with_name(".temp.old.json")
    if CHAT_INDEX_FILE.exists() and not backup.exists():
        shutil.copy2(CHAT_INDEX_FILE, backup)
    index = {"LastOpened": "", "LastChat": "", **index, "ChatList": kept}
    CHAT_INDEX_FILE.write_text(json.dumps(index, indent=4), encoding="utf-8")


def ensure_data_dirs():
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    tidy_index()
    if SAVE_ON_NAS:
        try:
            move_local_saves()
        except OSError as e:
            print(f"Copying the local saves to the NAS failed: {e}")
        tidy_index()


# --------------------------------------------------------------------------
# Network addresses (LLM server, NAS)
# --------------------------------------------------------------------------
# Defaults live in Code/UI/network.json. "Server > Set IP" saves your choice to
# CLIENT_DATA_DIR/network.json, which wins over the defaults (the packaged app
# can't write to its own folder).

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


def load_tool_token() -> str:
    """The token the server's own tools (REA, Ubuntu files) ask for; "" if none is set."""
    return str((load_network().get("server_tools") or {}).get("token") or "").strip()


def save_tool_token(token: str):
    user = _read(USER_NETWORK_FILE)
    user["server_tools"] = {"token": token.strip()}
    USER_NETWORK_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_NETWORK_FILE.write_text(json.dumps(user, indent=4), encoding="utf-8")


def split_address(text: str, default_port: int):
    """'192.168.0.192:8100' or '192.168.0.192' -> (ip, port)."""
    text = text.strip().removeprefix("http://").rstrip("/")
    host, _, port = text.partition(":")
    return host, int(port) if port.isdigit() else default_port
