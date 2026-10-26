"""Tests of voice input (voice.py and the mic button in ChatMain) and of the
settings export dialog. The microphone and the server are stood in for.

Run from Code/:  python -m pytest -q tests
"""

import io
import json
import os
import struct
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import voice  # noqa: E402
from tests.test_chat_settings import MODELS  # noqa: E402,F401


def test_wav_bytes_pcm16_reads_back_with_wave():
    pcm = struct.pack("<4h", 0, 1000, -1000, 32767)
    with wave.open(io.BytesIO(voice.wav_bytes(pcm, 16000, 1, 16)), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (16000, 1, 2, 4)
        assert w.readframes(4) == pcm


def test_wav_bytes_float_header_and_partial_frames_dropped():
    pcm = struct.pack("<5f", 0.1, -0.1, 0.5, -0.5, 0.25)        # 2.5 stereo frames
    data = voice.wav_bytes(pcm, 48000, 2, 32, voice.IEEE_FLOAT)
    tag, channels, rate, _, block, bits = struct.unpack("<HHIIHH", data[20:36])
    assert (tag, channels, rate, block, bits) == (3, 2, 48000, 8, 32)
    assert struct.unpack("<I", data[40:44])[0] == 16
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8


def test_peak_level():
    assert voice.peak_level(b"", 16, voice.PCM) == 0.0
    assert voice.peak_level(struct.pack("<h", -16384), 16, voice.PCM) == 0.5
    assert voice.peak_level(struct.pack("<f", 0.75), 32, voice.IEEE_FLOAT) == 0.75
    assert voice.peak_level(bytes([128, 192]), 8, voice.PCM) == 0.0       # only every 8th sample is looked at


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.mark.parametrize("status,body,signal,expected", [
    (200, {"text": " Hello there. "}, "done", "Hello there."),
    (503, {"error": {"message": "Speech to text is off"}}, "failed", "Speech to text failed: Speech to text is off"),
    (404, {"detail": "Not Found"}, "failed", "This server has no speech to text yet"),
])
def test_transcriber(monkeypatch, status, body, signal, expected):
    sent = {}

    def post(url, files, data, timeout):
        sent.update(url=url, name=files["file"][0], language=data["language"])
        return FakeResponse(status, body)

    monkeypatch.setattr(voice.requests, "post", post)
    t = voice.Transcriber(lambda: "http://server:8100")
    got = {}
    t.done.connect(lambda text: got.setdefault("done", text))
    t.failed.connect(lambda err: got.setdefault("failed", err))
    t._run(b"RIFF", "en")
    assert sent == {"url": "http://server:8100/v1/audio/transcriptions", "name": "voice.wav", "language": "en"}
    assert got[signal].startswith(expected)


# ---------------------------------------------------------------------------
# GUI, offscreen
# ---------------------------------------------------------------------------

@pytest.fixture
def gui(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    import paths
    import llmClient

    QCoreApplication.setOrganizationName("WaterloggedTest")
    QCoreApplication.setApplicationName("ChatUITest")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "qsettings"))
    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / "Chat")
    monkeypatch.setattr(paths, "CHAT_INDEX_FILE", tmp_path / ".temp.json")
    monkeypatch.setattr(llmClient.LLMClient, "list_models", lambda self: MODELS)
    paths.ensure_data_dirs()
    app = QApplication.instance() or QApplication([])
    from Widgets import chatMain, chatSettings
    main = chatMain.ChatMain()
    win = chatSettings.ChatSettings(main)
    yield main, win, tmp_path
    win.close()
    main.close()
    app.processEvents()


def test_mic_button_sits_between_input_and_send(gui):
    main, _, _ = gui
    row = main.ui.horizontalLayout
    assert row.indexOf(main.mic_button) == row.indexOf(main.ui.pushButton_5) - 1
    assert row.indexOf(main.mic_button) == row.indexOf(main.ui.plainTextEdit) + 1


def _speak(main, monkeypatch, text, seconds=2.0):
    """As if a clip was recorded and the server wrote down `text`."""
    clips = []
    monkeypatch.setattr(main.recorder, "seconds", lambda: seconds)
    monkeypatch.setattr(main.transcriber, "transcribe", lambda wav, lang: clips.append((wav, lang)))
    main.recorder.finished.emit(b"RIFF....")
    if clips:
        assert not main.mic_button.isEnabled()
        main.transcriber.done.emit(text)
    return clips


def test_spoken_text_goes_into_the_input(gui, monkeypatch):
    main, _, _ = gui
    main.input.setPlainText("Note:")
    main.input.moveCursor(main.input.textCursor().MoveOperation.End)
    clips = _speak(main, monkeypatch, "remind me at five")
    assert clips == [(b"RIFF....", "")]
    assert main.input.toPlainText() == "Note: remind me at five"
    assert main.mic_button.isEnabled() and main.mic_button.text() == "🎤"
    assert main.chat_markdown == []                       # not sent: auto-send is off by default


def test_auto_send_and_language(gui, monkeypatch):
    from PySide6.QtCore import QSettings
    main, _, _ = gui
    main.create_chat("Voice", {**__import__("chat_settings").DEFAULTS, "Model": "m1"})
    QSettings().setValue("voice/autoSend", True)
    QSettings().setValue("voice/language", "yue")
    sent = []
    monkeypatch.setattr(main, "sendMessage", lambda: sent.append(main.input.toPlainText()))
    clips = _speak(main, monkeypatch, "你好")
    assert clips[0][1] == "yue"
    assert sent == ["你好"]


def test_too_short_clip_is_dropped(gui, monkeypatch):
    main, _, _ = gui
    assert _speak(main, monkeypatch, "x", seconds=0.1) == []
    assert main.input.toPlainText() == ""


def test_failure_is_shown_and_button_comes_back(gui):
    main, _, _ = gui
    main.mic_button.setEnabled(False)
    main.transcriber.failed.emit("Can't reach the server for speech to text")
    assert main.mic_button.isEnabled()
    assert "Can't reach the server" in main.status_label.text()


# ---------------------------------------------------------------------------
# Export dialog
# ---------------------------------------------------------------------------

def test_export_defaults_to_chat_name_and_remembers_the_folder(gui, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from Widgets import chatSettings
    main, win, _ = gui
    asked = []
    out = tmp_path / "exports"
    out.mkdir()
    answers = iter([str(out / "Renamed by me"), str(out / "second.json")])

    def save_dialog(parent, title, start, filters):
        asked.append(start)
        return next(answers), filters

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    win.open_for("Trip: Tokyo?", {**__import__("chat_settings").DEFAULTS, "Character": "Guide"}, is_new=False)

    win.export_settings()
    assert asked[0].endswith("Trip Tokyo.json")          # the chat's name, made safe for Windows
    saved = json.loads((out / "Renamed by me.json").read_text(encoding="utf-8"))   # .json added
    assert saved["Character"] == "Guide"

    win.export_settings()
    assert asked[1] == str(out / "Trip Tokyo.json")       # opens where the last export went
    assert (out / "second.json").exists()
    assert chatSettings._last_folder() == out


# ---------------------------------------------------------------------------
# Text to speech
# ---------------------------------------------------------------------------

VOICES = {"engines": [{"id": "edge", "name": "Microsoft Edge voices (online)", "available": True, "speed": True},
                      {"id": "chatterbox", "name": "Chatterbox", "available": True, "speed": False},
                      {"id": "piper", "name": "Piper (on the server, offline)", "available": False,
                       "install": "pip install piper-tts", "speed": True}],
          "voices": [{"id": "en-US-JennyNeural", "engine": "edge", "language": "en-US", "name": "Jenny (en-US, Female)"},
                     {"id": "en-US-GuyNeural", "engine": "edge", "language": "en-US", "name": "Guy (en-US, Male)"},
                     {"id": "zh-HK-HiuGaaiNeural", "engine": "edge", "language": "zh-HK", "name": "HiuGaai (zh-HK, Female)"}],
          "errors": {}}


def test_tts_settings_normalize():
    import chat_settings
    assert chat_settings.normalize({"TTS Engine": "kokoro"})["TTS Engine"] == "kokoro"
    s = chat_settings.normalize({"TTS": True, "TTS Engine": "Robot Voice!", "TTS Speed": 9, "TTS Voice": 3})
    assert s["TTS"] is True and s["TTS Engine"] == "edge" and s["TTS Speed"] == 2.0 and s["TTS Voice"] == ""
    assert chat_settings.defaults()["TTS"] is False


def test_speaker_fetches_once_then_uses_the_cache(monkeypatch, tmp_path):
    posts = []

    class Resp:
        status_code = 200
        headers = {"content-type": "audio/mpeg"}
        content = b"ID3audio"
        text = ""

    def post(url, json, timeout):
        posts.append((url, json))
        return Resp()

    monkeypatch.setattr(voice.requests, "post", post)
    sp = voice.Speaker(lambda: "http://server:8100", tmp_path)
    got = []
    sp._fetched.disconnect()
    sp._fetched.connect(lambda key, path: got.append((key, path)))
    sp._fetch(3, "Hello", "edge", "en-US-GuyNeural", 1.0)
    sp._fetch(3, "Hello", "edge", "en-US-GuyNeural", 1.0)
    assert posts == [("http://server:8100/v1/audio/speech",
                      {"input": "Hello", "model": "edge", "voice": "en-US-GuyNeural", "speed": 1.0})]
    assert got[0] == got[1] and got[0][1].endswith(".mp3")
    assert open(got[0][1], "rb").read() == b"ID3audio"
    sp._fetch(3, "Hello", "edge", "en-US-GuyNeural", 1.2)        # another speed is another clip
    assert len(posts) == 2


def test_speaker_failure_is_reported(monkeypatch, tmp_path):
    class Resp:
        status_code = 502
        headers = {}
        text = '{"error": {"message": "no internet"}}'

        def json(self):
            return json.loads(self.text)

    monkeypatch.setattr(voice.requests, "post", lambda *a, **k: Resp())
    sp = voice.Speaker(lambda: "http://s", tmp_path)
    errors = []
    sp.failed.connect(errors.append)
    sp.toggle(0, "Hi", "edge", "", 1.0)
    sp._worker_join = None
    import time
    from PySide6.QtWidgets import QApplication
    for _ in range(50):
        QApplication.processEvents()
        if errors:
            break
        time.sleep(0.02)
    assert errors == ["Text to speech failed: no internet"]
    assert sp.key is None and sp.state_of(0) == "stopped"


def test_play_buttons_follow_the_tts_setting(gui, monkeypatch):
    import chat_settings
    main, _, _ = gui
    main.create_chat("Talk", {**chat_settings.DEFAULTS, "Model": "m1"})
    main.chat_markdown += [{"role": "user", "content": "hi", "ts": 1},
                           {"role": "assistant", "content": "Hello **there**", "ts": 2}]
    main._rebuild_chat_ui()
    assert "speak:1" not in main.chat.toHtml()                 # TTS off: no Play button

    main.apply_settings("Talk", {**main.settings, "TTS": True, "TTS Voice": "zh-HK-HiuGaaiNeural", "TTS Speed": 1.5})
    assert "speak:1" in main.chat.toHtml() and "▶ Play" in main.chat.toPlainText()
    calls = []
    monkeypatch.setattr(main.speaker, "toggle", lambda *a: calls.append(a))
    from PySide6.QtCore import QUrl
    main.on_anchor(QUrl("speak:1"))
    assert calls == [(1, "Hello **there**", "edge", "zh-HK-HiuGaaiNeural", 1.5, None)]

    monkeypatch.setattr(main.speaker, "state_of", lambda key: "playing" if key == 1 else "stopped")
    main.on_speaker_changed()
    assert "⏸ Pause" in main.chat.toPlainText()


def test_new_reply_plays_by_itself(gui, monkeypatch):
    import chat_settings
    main, _, _ = gui
    main.create_chat("Talk", {**chat_settings.DEFAULTS, "Model": "m1", "TTS": True})
    spoken = []
    monkeypatch.setattr(main, "speak", spoken.append)
    main.chat_markdown.append({"role": "user", "content": "hi", "ts": 1})
    main._start_response = lambda: None
    main.response_start_pos = main.chat.textCursor().position()
    main.on_done({"content": "Hello!"})
    assert spoken == [1]


def test_settings_window_voice_group(gui, monkeypatch):
    import chat_settings
    main, win, _ = gui
    monkeypatch.setattr(voice, "list_voices", lambda base_url, engine="": VOICES)
    win.open_for("Talk", {**chat_settings.DEFAULTS, "Model": "m1"}, is_new=False)
    assert win.ui.ttsBox.isHidden()                            # TTS off: no voice settings
    win.ui.ttsCheck.setChecked(True)
    assert not win.ui.ttsBox.isHidden()
    assert win.ui.ttsEngineCombo.count() == 3
    lang = win.ui.ttsLanguageCombo
    lang.setCurrentIndex(lang.findData("zh-HK"))
    lang.activated.emit(lang.currentIndex())
    assert win.ui.ttsVoiceCombo.currentData() == "zh-HK-HiuGaaiNeural"
    win.ui.ttsSpeedSpin.setValue(1.3)
    s = win.settings()
    assert (s["TTS"], s["TTS Engine"], s["TTS Voice"], s["TTS Speed"]) == (True, "edge", "zh-HK-HiuGaaiNeural", 1.3)

    win.open_for("Talk", s, is_new=False)                      # reopening shows the saved voice
    assert win.ui.ttsLanguageCombo.currentData() == "zh-HK"
    assert win.ui.ttsVoiceCombo.currentData() == "zh-HK-HiuGaaiNeural"


def test_settings_window_keeps_the_voice_when_the_server_is_down(gui, monkeypatch):
    import chat_settings
    main, win, _ = gui

    def down(base_url, engine=""):
        raise RuntimeError("Can't reach the server")

    monkeypatch.setattr(voice, "list_voices", down)
    win.open_for("Talk", {**chat_settings.DEFAULTS, "TTS": True, "TTS Voice": "en-US-GuyNeural"}, is_new=False)
    assert "Can't reach the server" in win.ui.ttsStatusLabel.text()
    assert win.settings()["TTS Voice"] == "en-US-GuyNeural"


def test_voice_sample():
    from Widgets.chatSettings import voice_sample
    assert voice_sample("zh-HK", "Ada").startswith("你好，我係Ada")
    assert voice_sample("zh-CN", "Ada").startswith("你好，我是Ada")
    assert voice_sample("en-GB", "Ada") == "Hello, I'm Ada. This is how I sound."


def test_settings_window_engine_hints(gui, monkeypatch):
    import chat_settings
    main, win, _ = gui
    voices = {**VOICES, "voices": VOICES["voices"] + [
        {"id": "default|en", "engine": "chatterbox", "language": "en", "name": "Default voice (en)"}]}
    monkeypatch.setattr(voice, "list_voices", lambda base_url, engine="": voices)
    win.open_for("Talk", {**chat_settings.DEFAULTS, "TTS": True, "TTS Engine": "piper"}, is_new=False)
    assert "pip install piper-tts" in win.ui.ttsStatusLabel.text()
    combo = win.ui.ttsEngineCombo
    combo.setCurrentIndex(combo.findData("chatterbox"))
    combo.activated.emit(combo.currentIndex())
    assert not win.ui.ttsSpeedSpin.isEnabled()                 # Chatterbox has no speed setting
    assert win.ui.ttsVoiceCombo.currentData() == "default|en"
    assert win.ui.ttsStatusLabel.text() == ""


def test_speaker_translation_is_sent_and_remembered(monkeypatch, tmp_path):
    import urllib.parse
    posts = []

    class Resp:
        status_code = 200
        headers = {"content-type": "audio/wav", "x-spoken-language": "ja-JP",
                   "x-spoken-text": urllib.parse.quote("こんにちは")}
        content = b"RIFFwav"
        text = ""

    def post(url, json, timeout):
        posts.append(json)
        return Resp()

    monkeypatch.setattr(voice.requests, "post", post)
    sp = voice.Speaker(lambda: "http://s", tmp_path)
    spoken = []
    sp.spoken.connect(lambda *a: spoken.append(a))
    sp._fetched.disconnect()
    sp._fetch(2, "Hello", "kokoro", "jf_alpha", 1.0, "gemma4:31b")
    sp._fetch(2, "Hello", "kokoro", "jf_alpha", 1.0, "gemma4:31b")     # from the cache
    assert posts == [{"input": "Hello", "model": "kokoro", "voice": "jf_alpha", "speed": 1.0,
                      "translate": True, "translate_model": "gemma4:31b"}]
    assert spoken == [(2, "ja-JP", "こんにちは")] * 2
    sp._fetch(2, "Hello", "kokoro", "jf_alpha", 1.0)                    # untranslated is another clip
    assert len(posts) == 2 and "translate" not in posts[1]


def test_translated_reply_shows_what_was_spoken(gui, monkeypatch):
    import chat_settings
    from PySide6.QtCore import QUrl
    main, _, _ = gui
    main.create_chat("Talk", {**chat_settings.DEFAULTS, "Model": "m1", "TTS": True, "TTS Translate": True,
                              "TTS Engine": "kokoro", "TTS Voice": "jf_alpha"})
    main.chat_markdown += [{"role": "user", "content": "hi", "ts": 1},
                           {"role": "assistant", "content": "Hello!", "ts": 2}]
    calls = []
    monkeypatch.setattr(main.speaker, "toggle", lambda *a: calls.append(a))
    main.speak(1)
    assert calls == [(1, "Hello!", "kokoro", "jf_alpha", 1.0, "m1")]   # the chat's model translates

    main.speaker.spoken.emit(1, "ja-JP", "こんにちは！")
    assert main.chat_markdown[1]["spoken"] == {"language": "ja-JP", "text": "こんにちは！"}
    assert json.loads(open(main.chat_path, encoding="utf-8").read())["Chat"][1]["spoken"]["text"] == "こんにちは！"
    assert "Spoken in ja-JP" in main.chat.toPlainText() and "こんにちは" not in main.chat.toPlainText()
    main.on_anchor(QUrl("spoken:1"))
    assert "こんにちは！" in main.chat.toPlainText()

    main.apply_settings("Talk", {**main.settings, "TTS Translate": False})
    assert "Spoken in" not in main.chat.toPlainText()
    calls.clear()
    main.speak(1)
    assert calls[0][-1] is None                                        # no translation
