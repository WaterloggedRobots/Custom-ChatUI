"""GPT-SoVITS engine (chat_server/sovits.py) against a fake api_v2.py."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from chat_server import sovits, tts
from chat_server.app import create_app


class FakeSovits:
    """Answers like GPT-SoVITS's api_v2.py: /control, /set_*_weights, POST /tts."""

    def __init__(self):
        self.calls = []
        self.running = True

    def handler(self, request: httpx.Request):
        if not self.running:
            raise httpx.ConnectError("connection refused")
        path = request.url.path
        self.calls.append((path, dict(request.url.params), json.loads(request.content) if request.content else None))
        if path == "/control":
            return httpx.Response(400, json={"message": "command is required"})
        if path in ("/set_gpt_weights", "/set_sovits_weights"):
            return httpx.Response(200, json={"message": "success"})
        if path == "/tts":
            body = json.loads(request.content)
            if body["text_lang"] not in ("ja", "zh", "en", "yue", "ko"):
                return httpx.Response(400, json={"message": "text_lang not supported"})
            return httpx.Response(200, content=b"RIFF....WAVEfmt ", headers={"content-type": "audio/wav"})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    api = FakeSovits()
    transport = httpx.MockTransport(api.handler)
    monkeypatch.setattr(sovits, "_client", lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout))
    monkeypatch.setattr(sovits, "_state", {"up": False, "checked": 0.0, "loaded": (None, None)})
    home, clips = tmp_path / "GPT-SoVITS", tmp_path / "clips"
    (home / "GPT_weights_v2Pro").mkdir(parents=True)
    (home / "SoVITS_weights_v2Pro").mkdir(parents=True)
    clips.mkdir()
    (home / "GPT_weights_v2Pro" / "Mio-e10.ckpt").write_bytes(b"x")
    (home / "GPT_weights_v2Pro" / "Mio-e15.ckpt").write_bytes(b"x")          # newest wins
    (home / "SoVITS_weights_v2Pro" / "Mio_e8_s200.pth").write_bytes(b"x")
    (home / "GPT_weights_v2Pro" / "Mioko-e5.ckpt").write_bytes(b"x")          # a different voice
    (clips / "Mio.wav").write_bytes(b"RIFF")
    (clips / "Mio.txt").write_text("こんにちは、ミオです。", encoding="utf-8")
    (clips / "Plain.wav").write_bytes(b"RIFF")                                # no training, no text
    monkeypatch.setattr(sovits.Config, "TTS_SOVITS_HOME", str(home))
    monkeypatch.setattr(sovits.Config, "TTS_SOVITS_DIR", str(clips))
    monkeypatch.setattr(sovits.Config, "TTS_SOVITS_LANGUAGES", "ja,en,zh-HK")
    import os, time
    os.utime(home / "GPT_weights_v2Pro" / "Mio-e10.ckpt", (time.time() - 100,) * 2)
    return api, home, clips


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_voices_are_listed_per_language(client, fake):
    api, home, clips = fake
    body = client.get("/v1/audio/voices", params={"engine": "gpt-sovits"}).json()
    engine = next(e for e in body["engines"] if e["id"] == "gpt-sovits")
    assert engine["available"] and engine["speed"]
    ids = [v["id"] for v in body["voices"]]
    assert ids == ["Mio|ja", "Mio|en", "Mio|zh-HK", "Plain|ja", "Plain|en", "Plain|zh-HK"]
    assert "trained" in body["voices"][0]["name"] and "not trained" in body["voices"][3]["name"]
    v = {x["name"]: x for x in sovits.voices_on_disk()}
    assert v["Mio"]["gpt"].name == "Mio-e15.ckpt" and v["Mio"]["language"] == "ja"
    assert v["Plain"]["gpt"] is None and v["Plain"]["language"] == "en"


def test_speaking_loads_the_trained_models_once(client, fake):
    api, home, clips = fake
    r = client.post("/v1/audio/speech", json={"input": "Hello there.", "model": "gpt-sovits",
                                              "voice": "Mio|zh-HK", "speed": 1.2})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"RIFF")
    paths = [c[0] for c in api.calls]
    assert paths.count("/set_gpt_weights") == 1 and paths.count("/set_sovits_weights") == 1
    gpt = next(c for c in api.calls if c[0] == "/set_gpt_weights")
    assert gpt[1]["weights_path"].endswith("Mio-e15.ckpt")
    tts_body = [c[2] for c in api.calls if c[0] == "/tts"][-1]
    assert tts_body["text_lang"] == "yue" and tts_body["prompt_lang"] == "ja"
    assert tts_body["prompt_text"] == "こんにちは、ミオです。" and tts_body["speed_factor"] == 1.2
    assert tts_body["ref_audio_path"].endswith("Mio.wav")
    client.post("/v1/audio/speech", json={"input": "Again.", "model": "gpt-sovits", "voice": "Mio|en"})
    assert [c[0] for c in api.calls].count("/set_gpt_weights") == 1          # already loaded


def test_untrained_clip_and_errors(client, fake):
    api, home, clips = fake
    r = client.post("/v1/audio/speech", json={"input": "Hi.", "model": "gpt-sovits", "voice": "Plain|en"})
    assert r.status_code == 200 and "/set_gpt_weights" not in [c[0] for c in api.calls]
    r = client.post("/v1/audio/speech", json={"input": "Hi.", "model": "gpt-sovits", "voice": "Nobody|en"})
    assert r.status_code == 404 and "Mio" in r.json()["error"]["message"]
    api.running = False
    sovits._state["checked"] = 0.0
    r = client.post("/v1/audio/speech", json={"input": "Hi.", "model": "gpt-sovits", "voice": "Mio|en"})
    assert r.status_code == 503 and "api_v2.py" in r.json()["error"]["message"]


def test_translation_uses_the_voice_language():
    import asyncio
    assert asyncio.run(tts.voice_language("gpt-sovits", "Mio|zh-HK")) == "zh-HK"
    assert sovits.guess_language("佢唔喺度") == "zh-HK"
    assert sovits.guess_language("你好") == "zh"
