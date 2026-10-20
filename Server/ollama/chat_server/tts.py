"""Text to speech for the chat's ▶ Play buttons.

The GUI sends a reply's text with the chat's engine, voice and speed (set per
chat in Chat Settings) and plays the audio that comes back. Two engines, each
switched off when its package is missing:

  edge    Microsoft Edge's online voices (edge-tts): 400+ natural voices in
          most languages, Cantonese (zh-HK) included. Needs internet. Default.
  piper   Piper, running on this machine (piper-tts): no internet needed, but
          each voice is a file to download once into TTS_PIPER_DIR (README),
          or with POST /v1/audio/voices/download.

Markdown, code blocks, links and citation marks are taken out of the text
first, so they aren't read aloud.
"""

import asyncio
import io
import json
import logging
import re
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .config import Config

log = logging.getLogger(__name__)
router = APIRouter()

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import piper
except Exception as e:          # onnxruntime can fail to import on odd systems
    piper = None
    log.info("piper not available: %s", e)

ENGINE_NAMES = {"edge": "Microsoft Edge voices (online)", "piper": "Piper (on the server, offline)"}
SPEED_RANGE = (0.5, 2.0)


def available() -> dict:
    return {"edge": edge_tts is not None, "piper": piper is not None}


def default_engine() -> Optional[str]:
    have = available()
    wanted = Config.TTS_ENGINE
    if have.get(wanted):
        return wanted
    return next((e for e, ok in have.items() if ok), None)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def clean_for_speech(text: str) -> str:
    """The words of a markdown reply, as they should be read aloud."""
    text = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S)
    text = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)                # code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)                              # inline code
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                     # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)                  # links -> their text
    text = re.sub(r"https?://\S+", " ", text)                             # bare URLs
    text = re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", text)                 # citations [1], [2, 3]
    text = re.sub(r"<[^>]+>", " ", text)                                  # html tags
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)             # headings
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)                      # quotes
    text = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", text, flags=re.M)        # list markers
    text = re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", "", text, flags=re.M)    # table rules
    text = re.sub(r"^[ \t]*\||\|[ \t]*$", "", text, flags=re.M)            # table edges
    text = re.sub(r"[ \t]*\|[ \t]*", ", ", text)                          # table cells
    text = re.sub(r"(?<!\w)(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", text)   # bold, italic, strike
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Edge voices
# ---------------------------------------------------------------------------

_edge_voices = {"at": 0.0, "list": []}


async def edge_voices() -> list:
    if edge_tts is None:
        return []
    if not _edge_voices["list"] or time.time() - _edge_voices["at"] > 24 * 3600:
        raw = await edge_tts.list_voices()
        _edge_voices["list"] = sorted((
            {"id": v["ShortName"], "engine": "edge", "language": v.get("Locale", ""),
             "gender": v.get("Gender", ""),
             "name": f"{v['ShortName'].split('-', 2)[-1].removesuffix('Neural')} ({v.get('Locale', '')}, {v.get('Gender', '')})"}
            for v in raw), key=lambda v: (v["language"], v["id"]))
        _edge_voices["at"] = time.time()
    return _edge_voices["list"]


def edge_rate(speed: float) -> str:
    return f"{round((speed - 1) * 100):+d}%"


async def edge_speak(text: str, voice: str, speed: float) -> bytes:
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice or Config.TTS_VOICE, rate=edge_rate(speed)).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    if not audio:
        raise HTTPException(502, "The Edge voice service returned no audio")
    return bytes(audio)


# ---------------------------------------------------------------------------
# Piper voices (files in TTS_PIPER_DIR)
# ---------------------------------------------------------------------------

def piper_dir() -> Path:
    return Path(Config.TTS_PIPER_DIR)


def piper_voices() -> list:
    if piper is None or not piper_dir().is_dir():
        return []
    voices = []
    for model in sorted(piper_dir().glob("*.onnx")):
        lang = ""
        try:
            info = json.loads(model.with_name(model.name + ".json").read_text(encoding="utf-8"))
            lang = (info.get("language") or {}).get("code", "").replace("_", "-")
        except (OSError, ValueError):
            pass
        voices.append({"id": model.stem, "engine": "piper", "language": lang or model.stem.split("-")[0].replace("_", "-"),
                       "gender": "", "name": f"{model.stem} ({lang or 'local'})"})
    return voices


_piper_loaded = {}
_piper_lock = threading.Lock()


def _piper_voice(name: str):
    with _piper_lock:
        if name not in _piper_loaded:
            path = piper_dir() / f"{name}.onnx"
            if not path.is_file():
                raise HTTPException(404, f"Piper voice {name} isn't on the server; download it first (see README)")
            _piper_loaded[name] = piper.PiperVoice.load(path)
        return _piper_loaded[name]


def piper_speak(text: str, voice: str, speed: float) -> bytes:
    if not voice:
        voices = piper_voices()
        if not voices:
            raise HTTPException(404, "No Piper voices on the server yet; download one first (see README)")
        voice = voices[0]["id"]
    model = _piper_voice(voice)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        model.synthesize_wav(text, wav, syn_config=piper.SynthesisConfig(length_scale=1.0 / speed))
    return buf.getvalue()


def piper_download(name: str):
    from piper.download_voices import download_voice
    piper_dir().mkdir(parents=True, exist_ok=True)
    download_voice(name, piper_dir())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class SpeechRequest(BaseModel):
    """OpenAI-style (`input`, `model`, `voice`, `speed`); the old /tts names
    (`text`, `engine`, `rate`) still work."""
    input: str = ""
    text: str = ""
    model: str = ""            # the engine: edge | piper ("" = TTS_ENGINE)
    engine: str = ""
    voice: str = ""
    speed: float = 1.0
    rate: str = ""             # old /tts: "+10%"


class DownloadRequest(BaseModel):
    voice: str


@router.get("/v1/audio/voices")
async def list_voices(engine: str = ""):
    """The engines and their voices, for the voice pickers in Chat Settings."""
    have = available()
    engines = [{"id": e, "name": ENGINE_NAMES[e], "available": ok} for e, ok in have.items()]
    voices = []
    errors = {}
    if engine in ("", "edge") and have["edge"]:
        try:
            voices += await edge_voices()
        except Exception as e:      # offline: the list comes from Microsoft
            errors["edge"] = f"can't get the Edge voice list: {e}"
    if engine in ("", "piper"):
        voices += piper_voices()
    return {"engines": engines, "default_engine": default_engine(),
            "default_voice": {"edge": Config.TTS_VOICE}, "voices": voices, "errors": errors}


@router.post("/v1/audio/speech")
@router.post("/tts")
async def text_to_speech(req: SpeechRequest):
    engine = (req.model or req.engine or default_engine() or "").lower()
    if engine not in ENGINE_NAMES:
        raise HTTPException(400, f"Unknown speech engine {engine!r}; use edge or piper")
    if not available()[engine]:
        raise HTTPException(503, f"{ENGINE_NAMES[engine]} isn't installed on the server; "
                                 f"pip install {'edge-tts' if engine == 'edge' else 'piper-tts'}")
    text = clean_for_speech(req.input or req.text)[:Config.TTS_MAX_CHARS]
    if not text:
        raise HTTPException(400, "There is nothing to read aloud in this text")
    speed = req.speed
    if req.rate:
        m = re.fullmatch(r"([+-]\d+)%", req.rate.strip())
        speed = 1 + int(m.group(1)) / 100 if m else speed
    lo, hi = SPEED_RANGE
    speed = min(max(speed, lo), hi)
    started = time.time()
    if engine == "edge":
        try:
            audio, media = await edge_speak(text, req.voice, speed), "audio/mpeg"
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"The Edge voice service failed (it needs internet): {e}")
    else:
        audio, media = await asyncio.to_thread(piper_speak, text, req.voice, speed), "audio/wav"
    log.info("TTS %s %s: %d chars -> %d KB in %.1fs", engine, req.voice or "(default)", len(text),
             len(audio) // 1024, time.time() - started)
    return Response(audio, media_type=media,
                    headers={"Content-Disposition": f"inline; filename=speech.{'mp3' if engine == 'edge' else 'wav'}"})


@router.post("/v1/audio/voices/download")
async def download_voice(req: DownloadRequest):
    """Fetch a Piper voice (e.g. en_US-lessac-medium) into TTS_PIPER_DIR."""
    if piper is None:
        raise HTTPException(503, "Piper isn't installed on the server; pip install piper-tts")
    if not re.fullmatch(r"[A-Za-z]{2,3}_[A-Za-z]{2}-[\w]+-(x_low|low|medium|high)", req.voice):
        raise HTTPException(400, "A Piper voice name looks like en_US-lessac-medium")
    try:
        await asyncio.to_thread(piper_download, req.voice)
    except Exception as e:
        raise HTTPException(502, f"Can't download {req.voice}: {e}")
    return {"status": "ok", "voices": piper_voices()}
