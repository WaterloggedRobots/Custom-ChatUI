"""Speech to text: WAV decoding, the engine choice and the routes, with a fake
Whisper model (the real one is a download; see README for a manual check).

Run from Server/ollama:  python -m pytest -q tests
"""

import io
import os
import struct
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import speech  # noqa: E402
from chat_server.app import create_app  # noqa: E402


def wav(samples, rate=16000, channels=1, kind="int16", extensible=False):
    """A WAV file like the GUI writes (Code/voice.py)."""
    x = np.asarray(samples, dtype=np.float32)
    if kind == "int16":
        tag, bits, pcm = 1, 16, (x * 32767).astype("<i2").tobytes()
    elif kind == "int32":
        tag, bits, pcm = 1, 32, (x * 2147483647).astype("<i4").tobytes()
    else:
        tag, bits, pcm = 3, 32, x.astype("<f4").tobytes()
    block = channels * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * block, block, bits, 22, bits, 0)
        fmt += struct.pack("<H", tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    else:
        fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"LIST" + struct.pack("<I", 3) + b"abc\x00"
    chunks += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def tone(seconds=1.0, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    return 0.5 * np.sin(2 * np.pi * 440 * t)


# ---------------------------------------------------------------- decoding

@pytest.mark.parametrize("kind", ["int16", "int32", "float"])
def test_read_wav_formats(kind):
    x = speech.read_wav(wav(tone(), kind=kind))
    assert x.dtype == np.float32 and len(x) == 16000
    assert abs(float(np.max(x)) - 0.5) < 0.01


def test_read_wav_stereo_48k_extensible_is_mixed_and_resampled():
    mono = tone(2.0, 48000)
    stereo = np.stack([mono, mono], axis=1).reshape(-1)
    x = speech.read_wav(wav(stereo, rate=48000, channels=2, kind="float", extensible=True))
    assert len(x) == 32000
    assert abs(float(np.max(x)) - 0.5) < 0.01


def test_read_wav_rejects_other_files():
    with pytest.raises(speech.AudioError):
        speech.read_wav(b"ID3\x03not a wav at all")
    with pytest.raises(speech.AudioError):
        speech.read_wav(b"RIFF\x04\x00\x00\x00WAVE")


def test_to_wav16_round_trip():
    x = tone()
    assert np.allclose(speech.read_wav(speech.to_wav16(x)), x, atol=1e-3)


def test_whisper_language():
    assert speech.whisper_language("en-US") == "en"
    assert speech.whisper_language("zh_HK") == "zh"
    assert speech.whisper_language("auto") is None
    assert speech.whisper_language("") is None


# ----------------------------------------------------------------- engines

class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeInfo:
    language = "en"
    duration = 1.0


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        return iter([FakeSegment(" Hello there,"), FakeSegment(" how are you? ")]), FakeInfo()


@pytest.fixture
def fake_whisper(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(speech, "ENGINE", "whisper")
    monkeypatch.setattr(speech.whisper, "model", model)
    monkeypatch.setattr(speech.whisper, "name", "fake")
    return model


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_pick_engine(monkeypatch):
    monkeypatch.setattr(speech, "faster_whisper", object())
    monkeypatch.setattr(speech, "sr", None)
    assert speech.pick_engine("auto") == "whisper"
    assert speech.pick_engine("google") is None
    assert speech.pick_engine("off") is None
    monkeypatch.setattr(speech, "faster_whisper", None)
    monkeypatch.setattr(speech, "sr", object())
    assert speech.pick_engine("auto") == "google"
    assert speech.pick_engine("whisper") is None


def test_transcription_route(client, fake_whisper):
    r = client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", wav(tone(), kind="float"), "audio/wav")},
                    data={"language": "en-US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Hello there, how are you?"
    assert body["engine"] == "whisper fake"
    audio, kw = fake_whisper.calls[0]
    assert isinstance(audio, np.ndarray) and len(audio) == 16000
    assert kw["language"] == "en" and kw["vad_filter"] is True


def test_old_route_and_text_format(client, fake_whisper):
    r = client.post("/stt", files={"file": ("clip.wav", wav(tone()), "audio/wav")},
                    data={"response_format": "text"})
    assert r.status_code == 200
    assert r.text == "Hello there, how are you?"
    assert fake_whisper.calls[0][1]["language"] is None      # no language: Whisper detects it


def test_non_wav_goes_to_whisper_decoder(client, fake_whisper):
    r = client.post("/v1/audio/transcriptions", files={"file": ("clip.mp3", b"ID3fake-mp3", "audio/mpeg")})
    assert r.status_code == 200
    assert isinstance(fake_whisper.calls[0][0], io.BytesIO)


def test_empty_upload(client, fake_whisper):
    r = client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_off(client, monkeypatch):
    monkeypatch.setattr(speech, "ENGINE", None)
    assert client.get("/v1/audio/stt").json()["available"] is False
    r = client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", wav(tone()), "audio/wav")})
    assert r.status_code == 503
    assert "faster-whisper" in r.json()["error"]["message"]


def test_status(client, fake_whisper):
    body = client.get("/v1/audio/stt").json()
    assert body["available"] is True and body["engine"] == "whisper" and body["loaded"] is True


def test_model_load_falls_back_to_cpu(monkeypatch):
    tried = []

    class Model:
        def __init__(self, name, device, compute_type, download_root):
            tried.append((name, device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA driver not found")

    monkeypatch.setattr(speech, "faster_whisper", type("fw", (), {"WhisperModel": Model}))
    monkeypatch.setattr(speech, "_cuda_works", lambda name, compute: True)
    monkeypatch.setattr(speech.Config, "STT_DEVICE", "auto")
    monkeypatch.setattr(speech.Config, "STT_MODEL", "")
    monkeypatch.setattr(speech.Config, "STT_COMPUTE_TYPE", "")
    w = speech.Whisper()
    w.load()
    assert tried == [("large-v3-turbo", "cuda", "float16"), ("small", "cpu", "int8")]
    assert (w.name, w.device) == ("small", "cpu")


def test_unusable_gpu_is_skipped_without_loading(monkeypatch):
    tried = []

    class Model:
        def __init__(self, name, device, compute_type, download_root):
            tried.append(device)

    monkeypatch.setattr(speech, "faster_whisper", type("fw", (), {"WhisperModel": Model}))
    monkeypatch.setattr(speech, "_cuda_works", lambda name, compute: False)
    monkeypatch.setattr(speech.Config, "STT_DEVICE", "auto")
    speech.Whisper().load()
    assert tried == ["cpu"]


def test_cuda_probe_without_gpu():
    assert speech._cuda_works("tiny", "float16") is False      # this test machine has no GPU


def test_unsupported_language_falls_back(client, fake_whisper):
    fake_whisper.supported_languages = ["en", "zh"]
    for lang, expected in (("yue", "zh"), ("xx", None), ("en", "en")):
        client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", wav(tone()), "audio/wav")},
                    data={"language": lang})
        assert fake_whisper.calls[-1][1]["language"] == expected
