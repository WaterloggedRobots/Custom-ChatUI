"""Speech to text for the chat's mic button.

The GUI records the microphone and uploads the clip as a WAV file; this turns
it into text. Two engines, picked by STT_ENGINE:

  whisper   faster-whisper running on this machine (no internet needed, and
            the default when it is installed). The model is loaded on the
            first request, or at start-up with STT_PRELOAD=1, and then kept.
  google    the free Google web recogniser from SpeechRecognition (the old
            /stt behaviour); needs internet.

`auto` takes whisper if it imports, else google; `off` turns the routes off.

WAV clips (16/32-bit PCM or 32-bit float, any rate or channel count) are
decoded here with numpy; anything else (mp3, webm, ...) is left to
faster-whisper's own decoder.
"""

import asyncio
import io
import logging
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .config import Config

log = logging.getLogger(__name__)
router = APIRouter()

SAMPLE_RATE = 16000                  # what Whisper works at

try:
    import numpy as np
except ImportError:
    np = None

try:
    import faster_whisper
except Exception as e:               # also catches a broken CUDA/ctranslate2 install
    faster_whisper = None
    log.info("faster-whisper not available: %s", e)

try:
    import speech_recognition as sr
except ImportError:
    sr = None


def pick_engine(wanted: str = None) -> Optional[str]:
    wanted = (wanted or Config.STT_ENGINE).lower()
    have = {"whisper": faster_whisper is not None and np is not None, "google": sr is not None}
    if wanted == "off":
        return None
    if wanted in have:
        return wanted if have[wanted] else None
    return "whisper" if have["whisper"] else "google" if have["google"] else None


ENGINE = pick_engine()


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

class AudioError(ValueError):
    pass


def read_wav(data: bytes):
    """(samples float32 in -1..1, mono, SAMPLE_RATE) from a WAV file.

    Raises AudioError if `data` isn't a WAV this can read. Python's own `wave`
    module can't read float WAVs, which is what Windows microphones often give."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("not a WAV file")
    fmt = None
    pcm = None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:            # WAVE_FORMAT_EXTENSIBLE: real tag in the sub-format
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise AudioError("the WAV file has no fmt or data chunk")
    tag, channels, rate, bits = fmt
    if channels < 1 or rate < 1:
        raise AudioError("the WAV header is invalid")
    if tag == 1 and bits == 16:
        x = np.frombuffer(pcm[:len(pcm) // 2 * 2], "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(pcm[:len(pcm) // 4 * 4], "<i4").astype(np.float32) / 2147483648.0
    elif tag == 1 and bits == 8:
        x = (np.frombuffer(pcm, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 3 and bits == 32:
        x = np.frombuffer(pcm[:len(pcm) // 4 * 4], "<f4").astype(np.float32)
    else:
        raise AudioError(f"unsupported WAV format (tag {tag}, {bits}-bit)")
    x = x[:len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    return resample(np.clip(x, -1.0, 1.0), rate)


def resample(x, rate: int):
    if rate == SAMPLE_RATE or len(x) == 0:
        return x.astype(np.float32)
    n = int(round(len(x) * SAMPLE_RATE / rate))
    return np.interp(np.arange(n) * (rate / SAMPLE_RATE), np.arange(len(x)), x).astype(np.float32)


def to_wav16(x) -> bytes:
    """Mono 16 kHz 16-bit WAV, for the Google engine."""
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def whisper_language(language: str) -> Optional[str]:
    """'en-US' -> 'en'; '' or 'auto' -> None (Whisper detects it)."""
    language = (language or "").strip().lower()
    if language in ("", "auto"):
        return None
    return language.replace("_", "-").split("-")[0]


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class Whisper:
    """One faster-whisper model, loaded once and shared by every request."""

    def __init__(self):
        self.model = None
        self.name = ""
        self.device = ""
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self.model is None:
                self.model, self.name, self.device = self._load()
            return self.model

    @staticmethod
    def _load():
        wanted = Config.STT_DEVICE.lower()
        devices = ["cuda", "cpu"] if wanted == "auto" else [wanted]
        errors = []
        for device in devices:
            name = Config.STT_MODEL or ("large-v3-turbo" if device == "cuda" else "small")
            compute = Config.STT_COMPUTE_TYPE or ("float16" if device == "cuda" else "int8")
            if device == "cuda" and wanted == "auto" and not _cuda_works(name, compute):
                errors.append("cuda: not usable (see the log)")
                continue
            started = time.time()
            try:
                model = faster_whisper.WhisperModel(name, device=device, compute_type=compute,
                                                    download_root=Config.STT_MODEL_DIR or None)
            except Exception as e:      # no GPU, missing CUDA libraries, unknown model, ...
                errors.append(f"{device}: {e}")
                log.warning("Whisper %s on %s failed: %s", name, device, e)
                continue
            log.info("Whisper %s loaded on %s (%s) in %.1fs", name, device, compute, time.time() - started)
            return model, name, device
        raise RuntimeError("could not load the Whisper model; " + "; ".join(errors))

    def transcribe(self, audio, language: Optional[str], prompt: str = ""):
        model = self.load()
        supported = getattr(model, "supported_languages", None)
        if language and supported and language not in supported:
            # Cantonese ("yue") is only in large-v3 and its turbo; the smaller models know it as Chinese.
            language = "zh" if language == "yue" and "zh" in supported else None
        with self._lock:                # one clip at a time keeps GPU memory predictable
            segments, info = model.transcribe(
                audio, language=language, beam_size=5, vad_filter=True,
                initial_prompt=prompt or None, condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language, round(info.duration, 2)


whisper = Whisper()

_CUDA_PROBE = """
import sys, numpy as np, faster_whisper
m = faster_whisper.WhisperModel(sys.argv[1], device="cuda", compute_type=sys.argv[2], download_root=sys.argv[3] or None)
segments, _ = m.transcribe(np.zeros(16000, np.float32))
list(segments)
"""


def _cuda_works(name: str, compute: str) -> bool:
    """Try Whisper on the GPU in a child process first. A missing cuDNN library
    doesn't raise in faster-whisper; it aborts the whole process on the first
    clip, which would take the chat server down with it."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            log.info("Whisper: no CUDA GPU found; using the CPU")
            return False
    except Exception:
        return False
    log.info("Whisper: checking %s on the GPU (downloads the model the first time)...", name)
    try:
        r = subprocess.run([sys.executable, "-c", _CUDA_PROBE, name, compute, Config.STT_MODEL_DIR],
                           capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        log.warning("Whisper GPU check timed out; using the CPU")
        return False
    if r.returncode != 0:
        log.warning("Whisper can't run on the GPU (exit %s); using the CPU. %s", r.returncode,
                    (r.stderr or r.stdout).strip()[-800:])
        return False
    return True


def _google(audio, language: str):
    r = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(to_wav16(audio))) as source:
        clip = r.record(source)
    try:
        return r.recognize_google(clip, language=language or "en-US")
    except sr.UnknownValueError:
        return ""


def transcribe(data: bytes, language: str = "", prompt: str = "", engine: str = None) -> dict:
    """Text of an audio clip: {"text", "language", "duration", "engine"}. Blocking."""
    engine = engine or ENGINE
    if engine is None:
        raise HTTPException(503, "Speech to text is off or not installed on the server; "
                                 "pip install faster-whisper (see Server/ollama/README.md)")
    try:
        audio = read_wav(data) if np is not None else None
    except AudioError as e:
        if engine != "whisper":
            raise HTTPException(400, f"Can't read the audio: {e}")
        audio = io.BytesIO(data)        # mp3, webm, ...: faster-whisper decodes it itself
    if engine == "whisper":
        try:
            text, lang, duration = whisper.transcribe(audio, whisper_language(language), prompt)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
        except Exception as e:
            raise HTTPException(400, f"Can't transcribe the audio: {e}")
        return {"text": text, "language": lang, "duration": duration, "engine": f"whisper {whisper.name}"}
    if audio is None:
        raise HTTPException(503, "The google engine needs numpy on the server")
    try:
        text = _google(audio, language)
    except sr.RequestError as e:
        raise HTTPException(503, f"Google speech service error: {e}")
    return {"text": text, "language": language or "en-US",
            "duration": round(len(audio) / SAMPLE_RATE, 2), "engine": "google"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/v1/audio/stt")
async def stt_status():
    """Whether speech to text works, and with what. The GUI asks this before recording."""
    return {"available": ENGINE is not None, "engine": ENGINE,
            "model": whisper.name or Config.STT_MODEL or "auto", "loaded": whisper.model is not None,
            "device": whisper.device or Config.STT_DEVICE, "max_bytes": Config.STT_MAX_BYTES}


@router.post("/v1/audio/transcriptions")       # OpenAI-style
@router.post("/stt")                           # the old route
async def speech_to_text(file: UploadFile = File(...), language: str = Form(""), prompt: str = Form(""),
                         response_format: str = Form("json"), model: str = Form("")):
    """`model` is accepted for OpenAI compatibility and ignored; STT_MODEL decides."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "The audio file is empty")
    if len(data) > Config.STT_MAX_BYTES:
        raise HTTPException(413, f"The audio is over {Config.STT_MAX_BYTES // (1024 * 1024)} MB")
    result = await asyncio.to_thread(transcribe, data, language, prompt)
    log.info("STT %s: %.1fs of audio -> %d chars", result["engine"], result["duration"], len(result["text"]))
    if response_format == "text":
        return PlainTextResponse(result["text"])
    return result


def preload():
    """Load the Whisper model now instead of on the first clip (STT_PRELOAD=1)."""
    if ENGINE == "whisper":
        try:
            whisper.load()
        except Exception as e:
            log.warning("Whisper preload failed: %s", e)
