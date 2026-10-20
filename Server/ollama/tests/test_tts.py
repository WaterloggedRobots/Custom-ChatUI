"""Text to speech: the markdown cleaner, the voice list and the speech route,
with stand-ins for Edge's online service and for a Piper voice.

Run from Server/ollama:  python -m pytest -q tests
"""

import io
import json
import os
import sys
import wave

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import tts  # noqa: E402
from chat_server.app import create_app  # noqa: E402


def test_clean_for_speech():
    md = """# Weather today

**Hong Kong** will be *sunny* [1] with a ~~small~~ chance of rain [2, 3].
- Check [the observatory](https://www.hko.gov.hk) 🌞
- Run `get_weather` or see https://example.com/x

```python
print("not read")
```

| City | Temp |
|------|------|
| HK | 28 |
<think>hidden</think>"""
    out = tts.clean_for_speech(md)
    assert "Weather today" in out and "#" not in out
    assert "Hong Kong will be sunny with a small chance of rain." in out
    assert "Check the observatory" in out and "http" not in out and "🌞" not in out
    assert "Run get_weather or see" in out                  # underscores inside words survive
    assert "print" not in out and "hidden" not in out
    assert "City, Temp\nHK, 28" in out


def test_edge_rate():
    assert tts.edge_rate(1.0) == "+0%"
    assert tts.edge_rate(1.25) == "+25%"
    assert tts.edge_rate(0.8) == "-20%"


# ---------------------------------------------------------------- stand-ins

class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, rate="+0%"):
        FakeCommunicate.calls.append((text, voice, rate))

    async def stream(self):
        yield {"type": "WordBoundary"}
        yield {"type": "audio", "data": b"ID3mp3-"}
        yield {"type": "audio", "data": b"bytes"}


class FakeEdge:
    Communicate = FakeCommunicate

    @staticmethod
    async def list_voices():
        return [{"ShortName": "zh-HK-HiuGaaiNeural", "Locale": "zh-HK", "Gender": "Female"},
                {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female"}]


class FakePiperVoice:
    loaded = []

    @classmethod
    def load(cls, path):
        cls.loaded.append(str(path))
        return cls()

    def synthesize_wav(self, text, wav, syn_config=None):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 100)
        self.last = (text, syn_config.length_scale)
        FakePiperVoice.last = self.last


class FakePiper:
    PiperVoice = FakePiperVoice

    class SynthesisConfig:
        def __init__(self, length_scale=None):
            self.length_scale = length_scale


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    FakeCommunicate.calls = []
    monkeypatch.setattr(tts, "edge_tts", FakeEdge)
    monkeypatch.setattr(tts, "piper", FakePiper)
    monkeypatch.setattr(tts, "_edge_voices", {"at": 0.0, "list": []})
    monkeypatch.setattr(tts, "_piper_loaded", {})
    monkeypatch.setattr(tts.Config, "TTS_PIPER_DIR", str(tmp_path))
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"onnx")
    (tmp_path / "en_US-lessac-medium.onnx.json").write_text(json.dumps({"language": {"code": "en_US"}}))
    return tmp_path


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_voice_list(client, fakes):
    body = client.get("/v1/audio/voices").json()
    assert {e["id"]: e["available"] for e in body["engines"]} == {"edge": True, "piper": True}
    ids = [(v["engine"], v["id"], v["language"]) for v in body["voices"]]
    assert ("edge", "en-US-JennyNeural", "en-US") in ids and ("edge", "zh-HK-HiuGaaiNeural", "zh-HK") in ids
    assert ("piper", "en_US-lessac-medium", "en-US") in ids
    assert client.get("/v1/audio/voices", params={"engine": "piper"}).json()["voices"][0]["engine"] == "piper"


def test_edge_speech(client, fakes):
    r = client.post("/v1/audio/speech", json={"input": "**Hi** [1]", "model": "edge",
                                              "voice": "zh-HK-HiuGaaiNeural", "speed": 1.2})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3mp3-bytes"
    assert FakeCommunicate.calls == [("Hi", "zh-HK-HiuGaaiNeural", "+20%")]


def test_piper_speech_and_speed_limits(client, fakes):
    r = client.post("/v1/audio/speech", json={"input": "Hello", "model": "piper", "speed": 5})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 22050
    assert FakePiperVoice.last == ("Hello", 0.5)          # speed capped at 2.0 -> length_scale 0.5


def test_old_tts_route(client, fakes):
    r = client.post("/tts", json={"text": "Old style", "voice": "en-US-JennyNeural", "rate": "-10%"})
    assert r.status_code == 200
    assert FakeCommunicate.calls[-1] == ("Old style", "en-US-JennyNeural", "-10%")


def test_errors(client, fakes, monkeypatch):
    assert client.post("/v1/audio/speech", json={"input": "```code only```", "model": "edge"}).status_code == 400
    assert client.post("/v1/audio/speech", json={"input": "hi", "model": "nope"}).status_code == 400
    r = client.post("/v1/audio/speech", json={"input": "hi", "model": "piper", "voice": "missing-voice"})
    assert r.status_code == 404
    monkeypatch.setattr(tts, "edge_tts", None)
    r = client.post("/v1/audio/speech", json={"input": "hi", "model": "edge"})
    assert r.status_code == 503 and "edge-tts" in r.json()["error"]["message"]


def test_download_rejects_odd_names(client, fakes):
    assert client.post("/v1/audio/voices/download", json={"voice": "../../etc/passwd"}).status_code == 400
