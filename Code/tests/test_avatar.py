"""Tests of the 3D model (avatar.py): stage directions in replies, the system
note, lip sync, and the chat window playing a reply on her. The 3D model and
the server are stood in for.

Run from Code/:  python -m pytest -q tests
"""

import os
import struct
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import avatar  # noqa: E402
import chat_settings  # noqa: E402
from tests.test_chat_settings import gui, MODELS  # noqa: E402,F401

CATALOG = {
    "posture": "Standing", "personality": "Any",
    "expressions": [{"name": "happy", "description": "smiling"}, {"name": "sad", "description": ""}],
    "specialExpressions": [{"name": "laugh", "description": "big laugh"}],
    "actions": [{"name": "wave", "description": "waves hello"}, {"name": "lie down", "description": ""}],
    "motionSets": [{"name": "greet", "description": "wave and smile"}],
}


# ---------------------------------------------------------------------------
# Stage directions
# ---------------------------------------------------------------------------

def test_parse_tag_reads_face_strength_move_and_preset():
    assert avatar.parse_tag("face: happy 0.7 | move: wave") == {"expression": "happy", "intensity": 0.7,
                                                                 "action": "wave"}
    assert avatar.parse_tag("face=Sad; intensity=3; move=lie_down") == {"expression": "sad", "intensity": 1.0,
                                                                         "action": "lie down"}
    assert avatar.parse_tag("preset: greet") == {"motionset": "greet"}
    assert avatar.parse_tag("see the wiki page") == {}


def test_strip_tags_keeps_where_each_act_was():
    text, acts = avatar.strip_tags("[[face: happy 0.6 | move: wave]] Hi there! [[face: sad 0.4]] I missed you.")
    assert text == "Hi there! I missed you."
    assert acts == [{"expression": "happy", "intensity": 0.6, "action": "wave", "at": 0},
                    {"expression": "sad", "intensity": 0.4, "at": len("Hi there!")}]
    assert avatar.strip_tags("A [[wiki link]] stays.") == ("A [[wiki link]] stays.", [])


def test_with_tags_puts_them_back_for_the_history():
    text, acts = avatar.strip_tags("[[face: happy 0.6]] Hi there! [[move: wave]] Bye.")
    again = avatar.with_tags(text, acts)
    assert again == "[[face: happy 0.6]] Hi there! [[move: wave]] Bye."
    assert avatar.strip_tags(again) == (text, acts)


def test_tag_filter_hides_tags_split_over_chunks():
    f = avatar.TagFilter()
    shown, acts = [], []
    for chunk in ["[", "[face: hap", "py 0.5]] Hel", "lo [", "[move: wave", "]] there [[x]]", " ["]:
        s, a = f.feed(chunk)
        shown.append(s)
        acts += a
    shown.append(f.flush())
    assert "".join(shown) == " Hello  there [[x]] ["
    assert acts == [{"expression": "happy", "intensity": 0.5}, {"action": "wave"}]


def test_system_note_lists_what_she_can_do_and_her_personality():
    note = avatar.system_note(CATALOG, "Tsundere", "Waves only when greeting.")
    assert "[[face:" in note
    assert "happy (smiling), sad" in note and "laugh (big laugh)" in note
    assert "wave (waves hello), lie down" in note and "greet (wave and smile)" in note
    assert "standing" in note and "tsundere" in note
    assert note.rstrip().endswith("Waves only when greeting.")
    assert "tsundere" not in avatar.system_note(CATALOG, "Any", "")


def test_check_names_drops_made_up_ones():
    assert avatar.check_names({"expression": "HAPPY", "intensity": 0.3, "action": "dance"}, CATALOG) == \
        {"expression": "happy", "intensity": 0.3}
    assert avatar.check_names({"motionset": "Greet"}, CATALOG) == {"motionset": "greet"}
    assert avatar.check_names({"action": "dance"}, None) == {"action": "dance"}


# ---------------------------------------------------------------------------
# Lip sync
# ---------------------------------------------------------------------------

def test_vowels_of_latin_kana_hangul():
    assert avatar.vowels_of("Hello you, my") == ["E", "O", "O", "I"]
    assert avatar.vowels_of("こんにちは") == ["O", "I", "I", "A"]
    assert avatar.vowels_of("ありがとー") == ["A", "I", "A", "O", "O"]
    assert avatar.vowels_of("안녕") == ["A", "O"]
    assert len(avatar.vowels_of("你好")) == 2


def _wav(path, seconds_on, seconds_off, rate=16000):
    loud = struct.pack("<h", 12000) + struct.pack("<h", -12000)
    pcm = loud * int(rate * seconds_on / 2) + b"\0\0" * int(rate * seconds_off)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_wav_envelope_and_frames(tmp_path):
    _wav(tmp_path / "a.wav", 0.5, 0.5)
    levels = avatar.wav_envelope(tmp_path / "a.wav")
    assert len(levels) == 20                                   # 1 s in 50 ms steps
    assert all(v > 0.9 for v in levels[:10]) and all(v == 0 for v in levels[10:])
    frames = avatar.lip_frames(levels, ["A", "O"])
    assert [f[0] for f in frames[:10]] == ["A"] * 5 + ["O"] * 5
    assert frames[15] == ("", 0.0)
    assert avatar.wav_envelope(tmp_path / "missing.wav") == []


def test_text_levels_close_at_spaces():
    levels = avatar.text_levels("ha ha", 10)
    assert len(levels) == 10 and max(levels) == 0.9 and 0.0 in levels


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_avatar_settings_normalize_and_turn_the_voice_on():
    s = chat_settings.normalize({"Avatar": True, "TTS": False, "Avatar Personality": "Kuudere",
                                 "Avatar Notes": "  calm  "})
    assert (s["Avatar"], s["TTS"], s["Avatar Personality"], s["Avatar Notes"]) == (True, True, "Kuudere", "calm")
    assert chat_settings.normalize({"Avatar Personality": "Yandere"})["Avatar Personality"] == "Any"


# ---------------------------------------------------------------------------
# The chat window with her
# ---------------------------------------------------------------------------

class FakeLink:
    def __init__(self):
        self.sent = []

    def act(self, act):
        self.sent.append(("act", act))

    def personality(self, name):
        self.sent.append(("personality", name))

    def mouth(self, viseme, amount):
        self.sent.append(("mouth", viseme, amount))

    def catalog(self, max_age=5.0):
        return CATALOG


@pytest.fixture
def stage(gui, monkeypatch):
    main, win, tmp = gui
    link = FakeLink()
    link.url = "http://127.0.0.1:7870"
    monkeypatch.setattr(main, "avatar_link", link)
    main.performer.link = link
    return main, win, link


def test_reply_is_acted_and_spoken(stage, monkeypatch):
    main, _, link = stage
    main.create_chat("Mio", {**chat_settings.DEFAULTS, "Model": "m1", "Avatar": True,
                             "Avatar Personality": "Tsundere", "TTS Auto Play": False})
    assert ("personality", "Tsundere") in link.sent
    messages = main.client.build_messages([{"role": "user", "content": "hi"}])
    assert any("[[face:" in m["content"] for m in messages if m["role"] == "system")

    played = []
    monkeypatch.setattr(main.speaker, "toggle", lambda *a: played.append(a))
    main.chat_markdown.append({"role": "user", "content": "hi", "ts": 1})
    main.response_start_pos = main.chat.textCursor().position()
    main._tag_filter, main._reacted = avatar.TagFilter(), False
    main.stream_phase = None
    for chunk in ["[[face: happy 0.8 | move: wa", "ve]] Hello! ", "[[face: sad 0.3]] Bye."]:
        main.on_token(chunk)
    assert link.sent[-1] == ("act", {"expression": "happy", "intensity": 0.8, "action": "wave"})
    assert "[[" not in main.chat.toPlainText()

    main.on_done({"content": "[[face: happy 0.8 | move: wave]] Hello! [[face: sad 0.3]] Bye."})
    msg = main.chat_markdown[-1]
    assert msg["content"] == "Hello! Bye."
    assert [a.get("expression") for a in msg["acts"]] == ["happy", "sad"]
    assert played and played[0][:2] == (1, "Hello! Bye.")       # spoken although auto play is off
    assert [a.get("expression") for _, a in main.performer._acts] == ["sad"]   # happy was played already

    later = main.client.build_messages(main.chat_markdown)
    assert later[-1]["content"].startswith("[[face: happy 0.8 | move: wave]] Hello!")


def test_performer_plays_acts_with_the_speech(stage, tmp_path, monkeypatch):
    main, _, link = stage
    perf = main.performer
    _wav(tmp_path / "s.wav", 0.5, 0.5)
    text, acts = avatar.strip_tags("[[face: happy]] One two. [[face: sad]] Three four.")
    perf.prepare(7, text, acts)
    position = {"ms": 0}
    monkeypatch.setattr(main.speaker, "state_of", lambda key: "playing" if key == 7 else "stopped")
    monkeypatch.setattr(main.speaker, "position", lambda: position["ms"])
    monkeypatch.setattr(main.speaker, "duration", lambda: 1000)
    perf.on_started(7, str(tmp_path / "s.wav"), text)
    perf._tick()
    assert ("act", {"expression": "happy"}) in link.sent and ("act", {"expression": "sad"}) not in link.sent
    assert link.sent[-1][0] == "mouth" and link.sent[-1][2] > 0.9
    position["ms"] = 800
    perf._tick()
    assert ("act", {"expression": "sad"}) in link.sent
    assert link.sent[-1] == ("mouth", "", 0.0)                 # silent part: mouth closed
    monkeypatch.setattr(main.speaker, "state_of", lambda key: "stopped")
    perf._tick()
    assert not perf._timer.isActive()


def test_settings_window_avatar_turns_the_voice_on(stage, monkeypatch):
    import voice
    main, win, link = stage
    monkeypatch.setattr(voice, "list_voices", lambda base_url, engine="": {"engines": [], "voices": []})
    win.open_for("Mio", {**chat_settings.DEFAULTS, "Model": "m1"}, is_new=False)
    assert win.ui.avatarBox.isHidden() and not win.ui.ttsCheck.isChecked()
    win.ui.avatarCheck.setChecked(True)
    assert not win.ui.avatarBox.isHidden()
    assert win.ui.ttsCheck.isChecked() and not win.ui.ttsCheck.isEnabled()
    assert "Connected" in win.ui.avatarStatusLabel.text()
    win.ui.avatarPersonalityCombo.setCurrentIndex(win.ui.avatarPersonalityCombo.findData("Kuudere"))
    win.ui.avatarNotesEdit.setPlainText("Never lies down.")
    s = win.settings()
    assert (s["Avatar"], s["TTS"], s["TTS Auto Play"], s["Avatar Personality"], s["Avatar Notes"]) == \
        (True, True, True, "Kuudere", "Never lies down.")
    win.ui.avatarCheck.setChecked(False)
    assert win.ui.ttsCheck.isEnabled()
