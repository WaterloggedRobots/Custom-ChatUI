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


class FakeKokoro:
    calls = []

    def __init__(self, model, voices):
        self.files = (model, voices)

    def create(self, text, voice, speed, lang):
        import numpy as np
        FakeKokoro.calls.append((text, voice, speed, lang))
        return np.zeros(2400, np.float32), 24000


class FakeKokoroModule:
    Kokoro = FakeKokoro


class FakeTensor:
    def __init__(self, n):
        import numpy as np
        self.a = np.full((1, n), 0.1, np.float32)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class FakeChatterbox:
    sr = 24000

    def __init__(self):
        self.calls = []

    def generate(self, text, **kw):
        self.calls.append((text, kw))
        return FakeTensor(1000)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    FakeCommunicate.calls = []
    monkeypatch.setattr(tts, "edge_tts", FakeEdge)
    monkeypatch.setattr(tts, "piper", FakePiper)
    monkeypatch.setattr(tts, "kokoro_onnx", FakeKokoroModule)
    monkeypatch.setattr(tts, "HAVE_CHATTERBOX", True)
    monkeypatch.setattr(tts, "kokoro", tts._Kokoro())
    monkeypatch.setattr(tts, "chatterbox", tts._Chatterbox())
    monkeypatch.setattr(tts.Config, "TTS_KOKORO_DIR", str(tmp_path / "kokoro"))
    monkeypatch.setattr(tts.Config, "TTS_CHATTERBOX_DIR", str(tmp_path / "clips"))
    monkeypatch.setattr(tts.Config, "TTS_CHATTERBOX_LANGUAGES", "en,zh")
    (tmp_path / "kokoro").mkdir()
    for name in (tts.Config.TTS_KOKORO_MODEL, tts.KOKORO_VOICES_FILE):
        (tmp_path / "kokoro" / name).write_bytes(b"x")
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "Grandpa.wav").write_bytes(b"RIFF")
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
    engines = {e["id"]: e for e in body["engines"]}
    assert list(engines) == ["edge", "kokoro", "chatterbox", "piper"]
    assert engines["edge"]["available"] and engines["piper"]["available"]
    assert engines["chatterbox"]["install"] == "pip install chatterbox-tts" and engines["chatterbox"]["speed"] is False
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


def test_kokoro_voices_and_speech(client, fakes):
    voices = client.get("/v1/audio/voices", params={"engine": "kokoro"}).json()["voices"]
    assert len(voices) == len(tts.KOKORO_VOICES)
    by_id = {v["id"]: v for v in voices}
    assert by_id["af_heart"]["language"] == "en-US" and "★" in by_id["af_heart"]["name"]
    assert by_id["zf_xiaoxiao"]["language"] == "zh-CN" and by_id["jm_kumo"]["gender"] == "Male"
    FakeKokoro.calls = []
    r = client.post("/v1/audio/speech", json={"input": "你好", "model": "kokoro", "voice": "zf_xiaoxiao", "speed": 1.1})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnframes()) == (24000, 2400)
    assert FakeKokoro.calls == [("你好", "zf_xiaoxiao", 1.1, "cmn")]
    client.post("/v1/audio/speech", json={"input": "Hi", "model": "kokoro", "voice": "nobody"})
    assert FakeKokoro.calls[-1][1] == "af_heart"          # unknown voice: the best English one


def test_kokoro_downloads_missing_files(fakes, monkeypatch):
    got = []
    monkeypatch.setattr(tts, "download", lambda url, path: (got.append(url), path.write_bytes(b"x")))
    for f in (fakes / "kokoro").iterdir():
        f.unlink()
    tts.kokoro.load()
    assert got == [tts.KOKORO_URL + tts.Config.TTS_KOKORO_MODEL, tts.KOKORO_URL + tts.KOKORO_VOICES_FILE]


def test_chatterbox_voices_and_speech(client, fakes):
    voices = client.get("/v1/audio/voices", params={"engine": "chatterbox"}).json()["voices"]
    assert [v["id"] for v in voices] == ["default|en", "Grandpa|en", "default|zh", "Grandpa|zh"]
    en, multi = FakeChatterbox(), FakeChatterbox()
    tts.chatterbox.models = {"en": en, "multi": multi}
    long = "First sentence here. " * 30
    r = client.post("/v1/audio/speech", json={"input": long, "model": "chatterbox", "voice": "Grandpa|en"})
    assert r.status_code == 200, r.text
    assert len(en.calls) > 1 and all(len(t) <= tts.CHATTERBOX_CHUNK for t, _ in en.calls)
    assert en.calls[0][1] == {"audio_prompt_path": str(fakes / "clips" / "Grandpa.wav")}
    r = client.post("/v1/audio/speech", json={"input": "你好。", "model": "chatterbox", "voice": "default|zh"})
    assert r.status_code == 200
    assert multi.calls == [("你好。", {"language_id": "zh"})]
    r = client.post("/v1/audio/speech", json={"input": "Hi", "model": "chatterbox", "voice": "Nobody|en"})
    assert r.status_code == 404


def test_split_sentences():
    parts = tts.split_sentences("One. Two! Three? " + "word " * 100, 60)
    assert parts[0] == "One. Two! Three?"
    assert all(len(p) <= 60 for p in parts)
    assert " ".join(parts).split() == ("One. Two! Three? " + "word " * 100).split()
    assert tts.split_sentences("你好。今天天气很好！", 7) == ["你好。", "今天天气很好！"]


@pytest.mark.skipif(not os.environ.get("KOKORO_TEST_DIR"), reason="set KOKORO_TEST_DIR to a folder with the Kokoro model")
def test_real_kokoro(monkeypatch):
    pytest.importorskip("kokoro_onnx")
    monkeypatch.setattr(tts.Config, "TTS_KOKORO_DIR", os.environ["KOKORO_TEST_DIR"])
    monkeypatch.setattr(tts, "kokoro", tts._Kokoro())
    data = tts.kokoro.speak("Hello there, this is a test.", "af_heart", 1.0)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 24000 and w.getnframes() > 24000
