"""GPT-SoVITS voices: voices you train on this machine's GPU from your own audio.

GPT-SoVITS (github.com/RVC-Boss/GPT-SoVITS) is a separate program in its own
Python environment. You train a voice in its web UI (webui.py); training saves
two files per voice, named after the experiment:
  GPT_weights*/<name>-e15.ckpt         the GPT (text to speech tokens) model
  SoVITS_weights*/<name>_e8_s200.pth   the SoVITS (tokens to sound) model
Its API (api_v2.py, port 9880) then speaks with them; this module calls it.

Each voice also needs a reference clip: 3 to 10 seconds of that voice saying
something, with what it says written in a .txt of the same name:
  voices/gpt-sovits/<name>.wav  and  voices/gpt-sovits/<name>.txt
The clip sets the tone; the trained models make it sound like the person. A clip
with no trained models still works, copied by whatever model GPT-SoVITS has
loaded, but less closely. Optional <name>.json: {"language": "ja"} (the clip's
language, else guessed from the .txt), "gpt"/"sovits" (model paths, else the
newest files named after the voice).

Voice ids are "<name>|<language>", like Chatterbox's.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import HTTPException

from .config import Config

log = logging.getLogger(__name__)

CLIP_TYPES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
# Our language codes -> GPT-SoVITS's text_lang ("ja" etc. also read English words in the text)
TEXT_LANG = {"ja": "ja", "zh": "zh", "en": "en", "zh-HK": "yue", "ko": "ko"}
LANGUAGE_LABELS = {"ja": "Japanese", "zh": "Mandarin", "en": "English", "zh-HK": "Cantonese", "ko": "Korean"}
CHECK_EVERY = 10.0           # seconds between "is it running?" checks

_state = {"up": False, "checked": 0.0, "loaded": (None, None)}
_lock = asyncio.Lock()        # one voice at a time: switching models is global in GPT-SoVITS


def _client(timeout: float) -> httpx.AsyncClient:      # tests swap this for a fake GPT-SoVITS
    return httpx.AsyncClient(timeout=timeout)


def languages() -> list:
    return [c.strip() for c in Config.TTS_SOVITS_LANGUAGES.split(",") if c.strip() in TEXT_LANG]


async def check(force: bool = False) -> bool:
    """Is GPT-SoVITS's API running? Asked at most every CHECK_EVERY seconds."""
    now = time.monotonic()
    if force or now - _state["checked"] > CHECK_EVERY:
        try:
            async with _client(1.5) as client:
                await client.get(f"{Config.TTS_SOVITS_URL}/control")   # 400 without a command: it's up
            _state["up"] = True
        except httpx.HTTPError:
            _state["up"] = False
            _state["loaded"] = (None, None)      # it restarts with its default models
        _state["checked"] = now
    return _state["up"]


def up() -> bool:
    """The last check's answer (for code that can't wait)."""
    return _state["up"]


# ---------------------------------------------------------------- voices on disk

def _weights(kind: str, name: str) -> Optional[Path]:
    """The newest trained file for voice `name`: kind "GPT" (.ckpt) or "SoVITS" (.pth)."""
    home = Path(Config.TTS_SOVITS_HOME)
    suffix = ".ckpt" if kind == "GPT" else ".pth"
    pattern = re.compile(rf"{re.escape(name)}[-_]e\d+", re.I)
    found = [p for folder in home.glob(f"{kind}_weights*") if folder.is_dir()
             for p in folder.iterdir() if p.suffix == suffix and pattern.match(p.name)]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def guess_language(text: str) -> str:
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "zh-HK" if re.search(r"[嘅咗喺哋唔冇嚟啲]", text) else "zh"
    return "en"


def voices_on_disk() -> list:
    """[{name, clip, text, language, gpt, sovits}] for every reference clip."""
    folder = Path(Config.TTS_SOVITS_DIR)
    if not folder.is_dir():
        return []
    out = []
    for clip in sorted(p for p in folder.iterdir() if p.suffix.lower() in CLIP_TYPES):
        name = clip.stem
        txt = clip.with_suffix(".txt")
        text = txt.read_text(encoding="utf-8").strip() if txt.is_file() else ""
        meta = {}
        try:
            meta = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        gpt = Path(meta["gpt"]) if meta.get("gpt") else _weights("GPT", name)
        sovits = Path(meta["sovits"]) if meta.get("sovits") else _weights("SoVITS", name)
        language = meta.get("language") if meta.get("language") in TEXT_LANG else guess_language(text)
        out.append({"name": name, "clip": clip, "text": text, "language": language,
                    "gpt": gpt, "sovits": sovits})
    return out


def voice_list() -> list:
    """Entries for GET /v1/audio/voices."""
    out = []
    for v in voices_on_disk():
        trained = "trained" if v["gpt"] and v["sovits"] else "copied from the clip, not trained"
        for lang in languages():
            out.append({"id": f"{v['name']}|{lang}", "engine": "gpt-sovits", "language": lang, "gender": "",
                        "name": f"{v['name']} ({LANGUAGE_LABELS[lang]}, {trained})"})
    return out


def voice_language(voice: str) -> str:
    lang = (voice or "").partition("|")[2]
    return lang if lang in TEXT_LANG else ""


# ---------------------------------------------------------------- speaking

async def _get(client, path, **params):
    r = await client.get(f"{Config.TTS_SOVITS_URL}{path}", params=params)
    if r.status_code != 200:
        raise HTTPException(502, f"GPT-SoVITS {path} failed: {_message(r)}")


def _message(r) -> str:
    try:
        body = r.json()
        return " ".join(str(body.get(k)) for k in ("message", "Exception") if body.get(k)) or r.text[:300]
    except ValueError:
        return r.text[:300] or f"HTTP {r.status_code}"


async def speak(text: str, voice: str, speed: float) -> bytes:
    name, _, lang = (voice or "").partition("|")
    voices = voices_on_disk()
    if not voices:
        raise HTTPException(404, f"No GPT-SoVITS voices yet: put a reference clip and its .txt in "
                                 f"{Config.TTS_SOVITS_DIR} (see the server README)")
    v = next((x for x in voices if x["name"] == name), None)
    if v is None:
        raise HTTPException(404, f"The GPT-SoVITS voice {name!r} isn't in {Config.TTS_SOVITS_DIR} "
                                 f"(it has {', '.join(x['name'] for x in voices)}); reopen Chat Settings "
                                 "to reload the voice list")
    lang = lang if lang in TEXT_LANG else v["language"]
    body = {
        "text": text, "text_lang": TEXT_LANG[lang],
        "ref_audio_path": str(v["clip"].resolve()),
        "prompt_text": v["text"], "prompt_lang": TEXT_LANG[v["language"]],
        "text_split_method": "cut5", "batch_size": 1, "speed_factor": speed,
        "media_type": "wav", "streaming_mode": False,
    }
    async with _lock, _client(Config.TTS_SOVITS_TIMEOUT) as client:
        try:
            wanted = (v["gpt"], v["sovits"])
            if all(wanted) and _state["loaded"] != wanted:
                started = time.time()
                await _get(client, "/set_gpt_weights", weights_path=str(v["gpt"]))
                await _get(client, "/set_sovits_weights", weights_path=str(v["sovits"]))
                _state["loaded"] = wanted
                log.info("GPT-SoVITS switched to %s (%s, %s) in %.1fs", name, v["gpt"].name,
                         v["sovits"].name, time.time() - started)
            r = await client.post(f"{Config.TTS_SOVITS_URL}/tts", json=body)
        except httpx.TransportError as e:
            _state["up"] = False
            _state["loaded"] = (None, None)
            raise HTTPException(503, f"GPT-SoVITS isn't running at {Config.TTS_SOVITS_URL} ({e}); "
                                     "start its api_v2.py (see the server README)")
    if r.status_code != 200 or not r.content.startswith(b"RIFF"):
        raise HTTPException(502, f"GPT-SoVITS couldn't speak: {_message(r)}")
    return r.content
