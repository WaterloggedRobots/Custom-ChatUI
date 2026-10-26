"""Text to speech for the chat's ▶ Play buttons.

The GUI sends a reply's text with the chat's engine, voice and speed (set per
chat in Chat Settings) and plays the audio that comes back. Each engine
switches itself off when its package is missing:

  edge        Microsoft Edge's online voices (edge-tts): 400+ natural voices in
              most languages, Cantonese (zh-HK) included. Needs internet. Default.
  kokoro      Kokoro-82M on this machine (kokoro-onnx): natural voices, offline,
              fast even on the CPU. Its model (about 340 MB) downloads by
              itself once. English, Mandarin, Japanese, Spanish, French, ...
  chatterbox  Chatterbox on this machine (chatterbox-tts): the most human
              sounding, and it can copy any voice from a short clip you put in
              TTS_CHATTERBOX_DIR. Wants an NVIDIA GPU; slow on the CPU.
  piper       Piper on this machine (piper-tts): light and offline, plainer
              voices; each is a file to download once into TTS_PIPER_DIR
              (README), or with POST /v1/audio/voices/download.

Markdown, code blocks, links and citation marks are taken out of the text
first, so they aren't read aloud. With `translate` on, the chat model (through
Ollama) first translates the text into the voice's language, so an English
reply can be spoken by a Japanese voice in Japanese; the translation comes back
in the X-Spoken-Text header.
"""

import asyncio
import importlib.util
import io
import json
import logging
import re
import sys
import threading
import time
import urllib.parse
import wave
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .config import Config

log = logging.getLogger(__name__)
router = APIRouter()

IMPORT_ERRORS = {}              # engine -> why its package didn't import, shown with the 503

try:
    import edge_tts
except Exception as e:
    edge_tts = None
    IMPORT_ERRORS["edge"] = e

try:
    import piper
except Exception as e:          # onnxruntime can fail to import on odd systems
    piper = None
    IMPORT_ERRORS["piper"] = e

try:
    import kokoro_onnx
except Exception as e:
    kokoro_onnx = None
    IMPORT_ERRORS["kokoro"] = e

for _engine, _err in IMPORT_ERRORS.items():
    if not isinstance(_err, ModuleNotFoundError) or _err.name not in ("edge_tts", "piper", "kokoro_onnx"):
        # Installed, but it broke on import (a missing dependency, say): say why at start-up.
        log.warning("%s is installed but can't be imported: %s: %s", _engine, type(_err).__name__, _err)

# Chatterbox pulls in torch, which takes seconds to import: only look for it here.
HAVE_CHATTERBOX = importlib.util.find_spec("chatterbox") is not None

ENGINE_NAMES = {"edge": "Microsoft Edge voices (online)",
                "kokoro": "Kokoro (on the server, offline, natural)",
                "chatterbox": "Chatterbox (on the server, most human, clones voices; GPU)",
                "piper": "Piper (on the server, offline, light)"}
ENGINE_INSTALL = {"edge": "pip install edge-tts", "kokoro": "pip install kokoro-onnx",
                  "chatterbox": "pip install chatterbox-tts", "piper": "pip install piper-tts"}
ENGINE_SPEED = {"edge": True, "kokoro": True, "chatterbox": False, "piper": True}
SPEED_RANGE = (0.5, 2.0)


def available() -> dict:
    return {"edge": edge_tts is not None, "kokoro": kokoro_onnx is not None,
            "chatterbox": HAVE_CHATTERBOX, "piper": piper is not None}


def not_available(engine: str) -> str:
    """Why `engine` can't speak, and what to do about it."""
    err = IMPORT_ERRORS.get(engine)
    pip = f"{sys.executable} -m pip install {ENGINE_INSTALL[engine].split()[-1]}"
    if err is None or (isinstance(err, ModuleNotFoundError)
                       and err.name in ("edge_tts", "piper", "kokoro_onnx", "chatterbox")):
        return (f"{ENGINE_NAMES[engine]} isn't installed in the server's Python. On the server run "
                f"{pip}, then restart the server.")
    return (f"{ENGINE_NAMES[engine]} is installed but fails to load: {type(err).__name__}: {err}. "
            f"Reinstalling it may help ({pip} --force-reinstall), then restart the server.")


def to_wav(samples, rate: int) -> bytes:
    """16-bit mono WAV from float samples in -1..1 (a numpy array)."""
    import numpy as np
    pcm = (np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def split_sentences(text: str, limit: int) -> list:
    """Pieces of at most about `limit` characters, cut at sentence ends where possible."""
    parts = re.split(r"(?<=[.!?;])\s+|(?<=[。！？；])\s*|\n+", text)
    chunks, current = [], ""
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if len(part) > limit and current:
            chunks.append(current)
            current = ""
        while len(part) > limit:                       # one very long sentence: cut at a comma or space
            cut = max(part.rfind(",", 0, limit), part.rfind("，", 0, limit), part.rfind(" ", 0, limit))
            cut = cut + 1 if cut > limit // 3 else limit
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        if current and len(current) + 1 + len(part) > limit:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks


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
# Kokoro (model files in TTS_KOKORO_DIR, downloaded once by itself)
# ---------------------------------------------------------------------------

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
# First letter of a voice: its language, as (shown, espeak code for kokoro-onnx).
KOKORO_LANGUAGES = {"a": ("en-US", "en-us"), "b": ("en-GB", "en-gb"), "e": ("es-ES", "es"), "f": ("fr-FR", "fr-fr"),
                    "h": ("hi-IN", "hi"), "i": ("it-IT", "it"), "j": ("ja-JP", "ja"), "p": ("pt-BR", "pt-br"),
                    "z": ("zh-CN", "cmn")}
KOKORO_VOICES = (
    "af_heart af_bella af_nicole af_aoede af_kore af_sarah af_nova af_sky af_alloy af_jessica af_river "
    "am_michael am_fenrir am_puck am_echo am_eric am_liam am_onyx am_adam am_santa "
    "bf_emma bf_isabella bf_alice bf_lily bm_george bm_fable bm_lewis bm_daniel "
    "ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola "
    "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa "
    "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang").split()
KOKORO_BEST = {"af_heart", "af_bella", "bf_emma", "ff_siwis", "jf_alpha", "zf_xiaoxiao"}   # the model card's top grades


def kokoro_voices() -> list:
    if kokoro_onnx is None:
        return []
    voices = []
    for v in KOKORO_VOICES:
        lang = KOKORO_LANGUAGES[v[0]][0]
        gender = "Female" if v[1] == "f" else "Male"
        star = " ★" if v in KOKORO_BEST else ""
        voices.append({"id": v, "engine": "kokoro", "language": lang, "gender": gender,
                       "name": f"{v[3:].title()}{star} ({lang}, {gender})"})
    return sorted(voices, key=lambda v: (v["language"], v["id"] not in KOKORO_BEST, v["id"]))


class _Kokoro:
    def __init__(self):
        self.model = None
        self.lock = threading.Lock()

    def files(self):
        folder = Path(Config.TTS_KOKORO_DIR)
        return folder / Config.TTS_KOKORO_MODEL, folder / KOKORO_VOICES_FILE

    def load(self):
        with self.lock:
            if self.model is None:
                model, voices = self.files()
                for path in (model, voices):
                    if not path.is_file():
                        download(KOKORO_URL + path.name, path)
                self.model = kokoro_onnx.Kokoro(str(model), str(voices))
                log.info("Kokoro loaded from %s", model)
            return self.model

    def speak(self, text: str, voice: str, speed: float) -> bytes:
        voice = voice if voice in KOKORO_VOICES else "af_heart"
        model = self.load()
        with self.lock:
            samples, rate = model.create(text, voice=voice, speed=speed, lang=KOKORO_LANGUAGES[voice[0]][1])
        return to_wav(samples, rate)


kokoro = _Kokoro()


def download(url: str, path: Path):
    """Fetch `url` to `path` (through a .part file, so a broken download is retried next time)."""
    import httpx
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    log.info("Downloading %s ...", url)
    started = time.time()
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    part.replace(path)
    log.info("Downloaded %s (%d MB) in %.0fs", path.name, path.stat().st_size >> 20, time.time() - started)


# ---------------------------------------------------------------------------
# Chatterbox (voice clips to copy in TTS_CHATTERBOX_DIR)
# ---------------------------------------------------------------------------

CHATTERBOX_CLIP_TYPES = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
CHATTERBOX_CHUNK = 280          # characters per generate() call; longer pieces drift or get cut off


def chatterbox_languages() -> list:
    return [x.strip() for x in Config.TTS_CHATTERBOX_LANGUAGES.split(",") if x.strip()]


def chatterbox_clips() -> list:
    folder = Path(Config.TTS_CHATTERBOX_DIR)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in CHATTERBOX_CLIP_TYPES)


def chatterbox_voices() -> list:
    """One entry per voice and language: "default|en", "<clip name>|zh", ..."""
    if not HAVE_CHATTERBOX:
        return []
    voices = []
    for lang in chatterbox_languages():
        voices.append({"id": f"default|{lang}", "engine": "chatterbox", "language": lang, "gender": "",
                       "name": f"Default voice ({lang})"})
        for clip in chatterbox_clips():
            voices.append({"id": f"{clip.stem}|{lang}", "engine": "chatterbox", "language": lang, "gender": "",
                           "name": f"{clip.stem} ({lang}, copied from {clip.name})"})
    return voices


class _Chatterbox:
    """The English model, and the multilingual one for other languages, each loaded on first use."""

    def __init__(self):
        self.models = {}
        self.lock = threading.Lock()

    def device(self) -> str:
        if Config.TTS_CHATTERBOX_DEVICE != "auto":
            return Config.TTS_CHATTERBOX_DEVICE
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"

    def model(self, multilingual: bool):
        key = "multi" if multilingual else "en"
        if key not in self.models:
            device = self.device()
            started = time.time()
            if multilingual:
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS as Model
            else:
                from chatterbox.tts import ChatterboxTTS as Model
            self.models[key] = Model.from_pretrained(device=device)
            log.info("Chatterbox %s loaded on %s in %.0fs", key, device, time.time() - started)
        return self.models[key]

    def speak(self, text: str, voice: str) -> bytes:
        import numpy as np
        name, _, lang = (voice or "default|en").partition("|")
        lang = lang or "en"
        clip = None
        if name and name != "default":
            clip = next((c for c in chatterbox_clips() if c.stem == name), None)
            if clip is None:
                raise HTTPException(404, f"The Chatterbox voice clip {name!r} isn't in {Config.TTS_CHATTERBOX_DIR}")
        pieces = []
        with self.lock:
            model = self.model(multilingual=lang != "en")
            for chunk in split_sentences(text, CHATTERBOX_CHUNK):
                kwargs = {"audio_prompt_path": str(clip)} if clip else {}
                if lang != "en":
                    kwargs["language_id"] = lang
                wav = model.generate(chunk, **kwargs)
                pieces.append(wav.detach().cpu().numpy().reshape(-1))
                pieces.append(np.zeros(int(model.sr * 0.2), dtype=np.float32))     # a breath between sentences
            rate = model.sr
        return to_wav(np.concatenate(pieces) if pieces else np.zeros(1, np.float32), rate)


chatterbox = _Chatterbox()


def preload():
    """Load the engines named in TTS_PRELOAD in the background, so the first ▶ Play
    doesn't wait for a download."""
    wanted = {x.strip() for x in Config.TTS_PRELOAD.split(",") if x.strip()}
    if "kokoro" in wanted and kokoro_onnx is not None:
        try:
            kokoro.load()
        except Exception as e:
            log.warning("Kokoro preload failed: %s", e)
    if "chatterbox" in wanted and HAVE_CHATTERBOX:
        try:
            chatterbox.model(multilingual=False)
        except Exception as e:
            log.warning("Chatterbox preload failed: %s", e)


# ---------------------------------------------------------------------------
# Translation into the voice's language
# ---------------------------------------------------------------------------

# What the translator is told to write for each language code. Cantonese gets
# written Cantonese, since that is what zh-HK voices read naturally.
LANGUAGE_NAMES = {
    "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "pt-BR": "Brazilian Portuguese", "ru": "Russian", "hi": "Hindi",
    "ar": "Arabic", "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "vi": "Vietnamese", "th": "Thai",
    "id": "Indonesian", "ms": "Malay", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
    "el": "Greek", "he": "Hebrew", "sw": "Swahili", "uk": "Ukrainian",
    "zh": "Mandarin Chinese in simplified characters",
    "zh-CN": "Mandarin Chinese in simplified characters",
    "zh-TW": "Mandarin Chinese in traditional characters (Taiwan)",
    "zh-HK": "spoken Cantonese written in traditional characters (Hong Kong colloquial, e.g. 係, 嘅, 唔)",
    "yue": "spoken Cantonese written in traditional characters (Hong Kong colloquial, e.g. 係, 嘅, 唔)",
}

TRANSLATE_PROMPT = """You translate text for a text-to-speech voice.
Translate the user's text into {language}. Keep the meaning, tone and names; write it the way a
native speaker would say it out loud. If it is already in {language}, return it unchanged.
Reply with the translation only: no notes, no quotes, no romanisation."""


def language_name(code: str) -> str:
    code = (code or "").replace("_", "-")
    return LANGUAGE_NAMES.get(code) or LANGUAGE_NAMES.get(code.split("-")[0]) or code


async def voice_language(engine: str, voice: str) -> str:
    """The language a voice speaks, e.g. "ja-JP"; "" if unknown."""
    if engine == "edge":
        voice = voice or Config.TTS_VOICE
        m = re.match(r"([a-z]{2,3}-[A-Z]{2})", voice)
        return m.group(1) if m else ""
    if engine == "kokoro":
        voice = voice if voice in KOKORO_VOICES else "af_heart"
        return KOKORO_LANGUAGES[voice[0]][0]
    if engine == "chatterbox":
        return (voice or "default|en").partition("|")[2] or "en"
    if engine == "piper":
        for v in piper_voices():
            if v["id"] == voice or not voice:
                return v["language"]
    return ""


async def translate(ollama, text: str, language: str, model: str) -> str:
    """`text` in `language`, written by the chat model."""
    reply = await ollama.chat({
        "model": model,
        "messages": [{"role": "system", "content": TRANSLATE_PROMPT.format(language=language_name(language))},
                     {"role": "user", "content": text}],
        "think": False,
        "keep_alive": Config.KEEP_ALIVE,
        # Same num_ctx as the chat, so Ollama doesn't reload the model for this.
        "options": {"temperature": 0.2, "num_ctx": Config.NUM_CTX, "num_predict": max(256, len(text) * 3)},
    })
    out = (reply.get("message") or {}).get("content") or ""
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"「」').strip()
    return clean_for_speech(out)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class SpeechRequest(BaseModel):
    """OpenAI-style (`input`, `model`, `voice`, `speed`); the old /tts names
    (`text`, `engine`, `rate`) still work."""
    input: str = ""
    text: str = ""
    model: str = ""            # the engine: edge | kokoro | chatterbox | piper ("" = TTS_ENGINE)
    engine: str = ""
    voice: str = ""
    speed: float = 1.0
    rate: str = ""             # old /tts: "+10%"
    translate: bool = False    # first translate the text into the voice's language
    translate_model: str = ""  # the chat's model, which translates (TTS_TRANSLATE_MODEL overrides it)


class DownloadRequest(BaseModel):
    voice: str


@router.get("/v1/audio/voices")
async def list_voices(engine: str = ""):
    """The engines and their voices, for the voice pickers in Chat Settings."""
    have = available()
    engines = [{"id": e, "name": ENGINE_NAMES[e], "available": ok, "install": ENGINE_INSTALL[e],
                "speed": ENGINE_SPEED[e]} for e, ok in have.items()]
    voices = []
    errors = {}
    if engine in ("", "edge") and have["edge"]:
        try:
            voices += await edge_voices()
        except Exception as e:      # offline: the list comes from Microsoft
            errors["edge"] = f"can't get the Edge voice list: {e}"
    if engine in ("", "kokoro"):
        voices += kokoro_voices()
    if engine in ("", "chatterbox"):
        voices += chatterbox_voices()
    if engine in ("", "piper"):
        voices += piper_voices()
    return {"engines": engines, "default_engine": default_engine(),
            "default_voice": {"edge": Config.TTS_VOICE}, "voices": voices, "errors": errors}


@router.post("/v1/audio/speech")
@router.post("/tts")
async def text_to_speech(req: SpeechRequest, request: Request):
    engine = (req.model or req.engine or default_engine() or "").lower()
    if engine not in ENGINE_NAMES:
        raise HTTPException(400, f"Unknown speech engine {engine!r}; use one of {', '.join(ENGINE_NAMES)}")
    if not available()[engine]:
        raise HTTPException(503, not_available(engine))
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
    headers = {"Content-Disposition": f"inline; filename=speech.{'mp3' if engine == 'edge' else 'wav'}"}
    if req.translate:
        language = await voice_language(engine, req.voice)
        if language:
            model = Config.TTS_TRANSLATE_MODEL or req.translate_model or Config.DEFAULT_MODEL
            text = (await translate(request.app.state.ollama, text, language, model))[:Config.TTS_MAX_CHARS]
            if not text:
                raise HTTPException(502, f"{model} returned no translation")
            log.info("TTS translated into %s with %s in %.1fs", language, model, time.time() - started)
            headers["X-Spoken-Language"] = language
            headers["X-Spoken-Text"] = urllib.parse.quote(text[:3000])
    if engine == "edge":
        try:
            audio, media = await edge_speak(text, req.voice, speed), "audio/mpeg"
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"The Edge voice service failed (it needs internet): {e}")
    else:
        work = {"kokoro": lambda: kokoro.speak(text, req.voice, speed),
                "chatterbox": lambda: chatterbox.speak(text, req.voice),
                "piper": lambda: piper_speak(text, req.voice, speed)}[engine]
        try:
            audio, media = await asyncio.to_thread(work), "audio/wav"
        except HTTPException:
            raise
        except Exception as e:
            log.exception("TTS %s failed", engine)
            raise HTTPException(500, f"{ENGINE_NAMES[engine]} failed: {e}")
    log.info("TTS %s %s: %d chars -> %d KB in %.1fs", engine, req.voice or "(default)", len(text),
             len(audio) // 1024, time.time() - started)
    return Response(audio, media_type=media, headers=headers)


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
