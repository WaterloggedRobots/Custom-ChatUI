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
