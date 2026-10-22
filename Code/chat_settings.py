"""Per-chat settings: the character, model and options each chat keeps for itself.

They live in the chat file under "Settings" (see ChatMain.save_chat) and can be
exported to, or imported from, a small .json of their own to reuse them in
another chat or share them.

Every read goes through normalize(): an entry that is missing, of the wrong
type or out of range takes its default from DEFAULTS, so an old, partial or
hand-edited file never breaks a chat. To add a setting later, add it to
DEFAULTS and give it a check in normalize().

No Qt here, so it can be tested on its own (tests/test_chat_settings.py).
"""

import json
import re
from pathlib import Path

WEB_MODES = ("auto", "on", "off")
TEMPERATURE_RANGE = (0.0, 2.0)
TTS_ENGINES = ("edge", "kokoro", "chatterbox", "piper")   # the server's; it may add more
TTS_SPEED_RANGE = (0.5, 2.0)
IMAGE_MODES = ("auto", "on")           # auto: the model decides when to draw; on: a picture with every reply
TOOL_KEYS = {"files": "Tool Files", "git": "Tool Git", "run": "Tool Run"}   # local_tools group -> setting
EXPORT_TAG = "ChatUI Settings"        # marks an exported file; the value is the format version
EXPORT_VERSION = 1

DEFAULTS = {
    "Character": "Bot",               # name shown on the bot's replies
    "Pretext": "",                    # the character description, sent first as the system message
    "Model": "",                      # "" = whatever the chat picks first from the server's list
    "Temperature": 0.7,
    "Think": True,
    "Web Search": "auto",             # one of WEB_MODES
    # Tools on this PC (local_tools/), one switch per group. Chats saved before 0.12 had one "Tools" switch.
    "Tool Files": True,               # browse, read and edit files and folders
    "Tool Git": True,                 # git in the user's repositories
    "Tool Run": True,                 # run scripts, programs and terminal commands
    # Text to speech, done by the server (Server/ollama/chat_server/tts.py)
    "TTS": False,                     # off: no Play buttons and no voice settings shown
    "TTS Engine": "edge",             # an engine id from the server, e.g. one of TTS_ENGINES
    "TTS Voice": "",                  # "" = the server's default voice for the engine
    "TTS Speed": 1.0,
    "TTS Auto Play": True,            # read each new reply aloud when it arrives
    "TTS Translate": False,           # speak in the voice's language: the chat model translates the reply first
    # Pictures, drawn by ComfyUI on the server (Server/ollama/chat_server/comfy.py)
    "Image Workflow": "",             # a workflow id from the server's workflows/ folder; "" = no pictures
    "Image Mode": "auto",             # one of IMAGE_MODES
}


def tool_groups(settings: dict) -> set:
    """The local_tools groups these settings turn on."""
    return {group for group, key in TOOL_KEYS.items() if settings.get(key)}


def defaults() -> dict:
    return dict(DEFAULTS)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def normalize(data) -> dict:
    """A full, valid settings dict from whatever `data` holds."""
    s = defaults()
    if not isinstance(data, dict):
        return s
    for key in ("Character", "Pretext", "Model", "TTS Voice"):
        if isinstance(data.get(key), str):
            s[key] = data[key]
    if not s["Character"].strip():
        s["Character"] = DEFAULTS["Character"]
    temp = _number(data.get("Temperature"))
    if temp is not None:
        lo, hi = TEMPERATURE_RANGE
        s["Temperature"] = round(min(max(float(temp), lo), hi), 2)
    speed = _number(data.get("TTS Speed"))
    if speed is not None:
        lo, hi = TTS_SPEED_RANGE
        s["TTS Speed"] = round(min(max(float(speed), lo), hi), 2)
    engine = data.get("TTS Engine")
    if isinstance(engine, str) and re.fullmatch(r"[a-z0-9_-]{1,32}", engine):
        s["TTS Engine"] = data["TTS Engine"]
    if isinstance(data.get("Tools"), bool):          # the old single switch
        for key in TOOL_KEYS.values():
            s[key] = data["Tools"]
    for key in ("Think", *TOOL_KEYS.values(), "TTS", "TTS Auto Play", "TTS Translate"):
        if isinstance(data.get(key), bool):
            s[key] = data[key]
    if data.get("Web Search") in WEB_MODES:
        s["Web Search"] = data["Web Search"]
    workflow = data.get("Image Workflow")
    if isinstance(workflow, str) and len(workflow) <= 120:
        s["Image Workflow"] = workflow.strip()
    if data.get("Image Mode") in IMAGE_MODES:
        s["Image Mode"] = data["Image Mode"]
    return s


# ---------------------------------------------------------------------------
# Chat files
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def from_chat(chat: dict) -> dict:
    """The settings of a loaded chat file.

    Chats saved before 0.9 kept these at the top level and the character in a
    separate bot folder ("Bot Path"/Bot Description.json); those are read once
    here and saved into the chat itself from then on."""
    if isinstance(chat.get("Settings"), dict):
        return normalize(chat["Settings"])
    old = {k: chat[k] for k in ("Model", "Temperature", "Web Search", "Think", "Tools") if k in chat}
    bot_path = chat.get("Bot Path")
    bot = _read_json(Path(bot_path) / "Bot Description.json") if isinstance(bot_path, str) and bot_path else None
    if isinstance(bot, dict):
        old["Character"] = bot.get("Name")
        old["Pretext"] = bot.get("Description")
    return normalize(old)


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

def export_data(settings: dict) -> dict:
    return {EXPORT_TAG: EXPORT_VERSION, **normalize(settings)}


def export_file(settings: dict, path):
    Path(path).write_text(json.dumps(export_data(settings), indent=4, ensure_ascii=False), encoding="utf-8")


def import_data(data) -> dict:
    """Settings from an exported file, a whole chat file, or an old Bot Description.json."""
    if not isinstance(data, dict):
        raise ValueError("the file doesn't hold a settings object")
    if isinstance(data.get("Settings"), dict):          # a chat file
        return normalize(data["Settings"])
    if "Description" in data and "Pretext" not in data:  # an old bot file
        data = {**data, "Character": data.get("Name"), "Pretext": data.get("Description")}
    return normalize(data)


def import_file(path) -> dict:
    """Raises ValueError with a readable reason if the file can't be used."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"can't read the file: {e}") from e
    except ValueError as e:
        raise ValueError(f"the file isn't valid JSON: {e}") from e
    return import_data(data)
