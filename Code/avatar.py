"""The 3D model: she acts out and speaks each reply when a chat has "3D model" on.

The model is the ChatUIAvatar app (Unity; its README is next to the project
on the NAS). It runs on this PC and listens on http://localhost:7870
(`avatar` in Code/UI/network.json):

  GET  /api/catalog      her faces, moves, presets, posture and personality
  POST /api/act          {"expression", "intensity", "action"}; or /api/motionset {"name"}
  POST /api/personality  {"personality": "Any" | "Tsundere" | "Kuudere"}
  POST /api/lipsync      {"visemes": [{"viseme": "A", "amount": 0.8}]}; the mouth
                         closes by itself half a second after the last one

How a reply becomes a performance:

  1. system_note() tells the model what she can do (from the catalog), her
     personality and the user's acting notes, and asks for stage directions
     in the reply: [[face: happy 0.7 | move: wave]] at the start, and again
     before a sentence where her feeling changes.
  2. TagFilter hides the tags while the reply streams in, and the first one
     is played straight away, so she reacts as the answer appears.
  3. strip_tags() takes the tags out of the finished reply; the message keeps
     the text without them, plus "acts" with where each one was.
  4. The reply is read aloud (voice.Speaker), and Performer plays the later
     acts when the speech gets to them and moves her mouth: loudness from the
     audio (WAV) or from the text (MP3), mouth shapes from the vowels.

Everything above Performer has no Qt in it (tests/test_avatar.py).
"""

import array
import collections
import re
import threading
import time
import unicodedata
import wave

import requests

import paths

DEFAULT_PORT = 7870
STEP_MS = 50                     # lip sync frame: 20 mouth updates a second
MAX_TAG = 200                    # "[[" with no "]]" within this many characters is just text
MAX_ACTS = 6
VISEMES = ("A", "I", "U", "E", "O")

PERSONALITY_TONES = {
    "Any": "",
    "Tsundere": ("You are tsundere: proud, prickly and easily flustered. You deny caring and get huffy "
                 "when teased or thanked, but your warmth keeps showing through anyway."),
    "Kuudere": ("You are kuudere: calm, cool and matter-of-fact, with few words and little visible emotion, "
                "but quietly caring; the rare soft moment means a lot."),
}


def avatar_url() -> str:
    net = paths.load_network().get("avatar", {})
    host = str(net.get("ip") or "localhost")
    port = int(net.get("port") or DEFAULT_PORT)
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Stage directions in the reply
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"[ \t]*\[\[(.{0,%d}?)\]\]" % MAX_TAG, re.S)
KEYS = {"face": "expression", "expression": "expression", "move": "action", "action": "action",
        "body": "action", "preset": "motionset", "motionset": "motionset", "intensity": "intensity"}


def _number(text):
    try:
        return min(max(float(text), 0.0), 1.0)
    except ValueError:
        return None


def parse_tag(inside: str) -> dict:
    """"face: happy 0.7 | move: wave" -> {"expression": "happy", "intensity": 0.7, "action": "wave"}.
    Parts are split by | or ; and each is "key: value" (or key=value)."""
    act = {}
    for part in re.split(r"[|;]", inside):
        key, sep, value = part.partition(":")
        if not sep:
            key, sep, value = part.partition("=")
        key, value = key.strip().lower(), value.strip().strip("\"'").strip()
        if not sep or key not in KEYS or not value:
            continue
        field = KEYS[key]
        if field == "intensity":
            if _number(value) is not None:
                act["intensity"] = _number(value)
            continue
        if field == "expression":
            words = value.split()
            if len(words) > 1 and _number(words[-1]) is not None:
                act["intensity"] = _number(words[-1])
                value = " ".join(words[:-1])
        act[field] = value.replace("_", " ").lower()
    return act


def strip_tags(text: str):
    """(text without the tags, [act + {"at": where it was in that text}])."""
    acts, out, last = [], [], 0
    for m in TAG_RE.finditer(text):
        act = parse_tag(m.group(1))
        if not act:                       # [[something else]]: leave it in the text
            continue
        out.append(text[last:m.start()])
        last = m.end()
        if len(acts) < MAX_ACTS:
            acts.append({**act, "at": len("".join(out).lstrip())})
    if not last:
        return text, []
    out.append(text[last:])
    return "".join(out).strip(), acts


def with_tags(text: str, acts) -> str:
    """The reply as the model wrote it, so earlier replies show it how to keep doing it."""
    for act in sorted(acts or [], key=lambda a: a.get("at", 0), reverse=True):
        at = min(max(int(act.get("at", 0)), 0), len(text))
        if text[at:at + 1] == " ":            # "Hi! Bye" -> "Hi! [[...]] Bye"
            at += 1
        space = " " if at and not text[at - 1].isspace() else ""
        text = text[:at] + space + tag_text(act) + text[at:]
    return text


def tag_text(act: dict) -> str:
    parts = []
    if act.get("motionset"):
        parts.append(f"preset: {act['motionset']}")
    if act.get("expression"):
        intensity = act.get("intensity")
        parts.append(f"face: {act['expression']}" + (f" {intensity:g}" if intensity is not None else ""))
    if act.get("action"):
        parts.append(f"move: {act['action']}")
    return "[[" + " | ".join(parts) + "]] "


class TagFilter:
    """Hides the tags from a reply arriving piece by piece. feed() returns
    (text to show, acts that just finished)."""

    def __init__(self):
        self._pending = ""

    def feed(self, chunk: str):
        s, shown, acts = self._pending + chunk, [], []
        self._pending = ""
        while s:
            i = s.find("[[")
            if i < 0:
                if s.endswith("["):           # could be the start of a tag
                    shown.append(s[:-1])
                    self._pending = "["
                else:
                    shown.append(s)
                break
            shown.append(s[:i])
            j = s.find("]]", i + 2)
            if j < 0:
                if len(s) - i > MAX_TAG:      # never closed: it was text after all
                    shown.append(s[i:])
                else:
                    self._pending = s[i:]
                break
            act = parse_tag(s[i + 2:j])
            if act:
                acts.append(act)
            else:                             # [[something else]]: show it
                shown.append(s[i:j + 2])
            s = s[j + 2:]
        return "".join(shown), acts

    def flush(self) -> str:
        rest, self._pending = self._pending, ""
        return rest


# ---------------------------------------------------------------------------
# What to tell the model
# ---------------------------------------------------------------------------

def _names(items) -> str:
    out = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else str(item)
        if not name:
            continue
        desc = (item.get("description") or "").strip() if isinstance(item, dict) else ""
        out.append(f"{name} ({desc})" if desc else name)
    return ", ".join(out)


def system_note(catalog: dict, personality: str = "Any", notes: str = "") -> str:
    """The system message that makes the model act as the 3D character."""
    lines = [
        "You are also the 3D character on the user's screen. Every reply is spoken aloud in your voice "
        "while you act it out with your face and body.",
        "",
        "Stage directions: start every reply with a tag like [[face: happy 0.6 | move: wave]] choosing the "
        "face and move that fit what you are about to say. When your feeling changes later in the reply, put "
        "another tag right before that sentence (at most 3 tags in a reply). The number after the face is how "
        "strong it is, from 0.2 (a hint) to 1 (as strong as it gets). Leave out \"move\" to keep doing what you "
        "are doing, and only move when it adds something. Use only the names listed below. The tags are hidden "
        "and never spoken, so never mention them, and don't describe your actions in words or *asterisks*.",
        "",
        "Because you are heard, not read: talk the way you speak, in short paragraphs, without tables, "
        "headings or long lists unless the user asks for them.",
        "",
    ]
    if catalog.get("expressions"):
        lines.append(f"Faces: {_names(catalog['expressions'])}.")
    if catalog.get("specialExpressions"):
        lines.append(f"Special faces, for the moments that call for them: {_names(catalog['specialExpressions'])}.")
    if catalog.get("actions"):
        lines.append(f"Moves: {_names(catalog['actions'])}.")
    if catalog.get("motionSets"):
        lines.append("Presets, a face and a move together, written [[preset: name]]: "
                     f"{_names(catalog['motionSets'])}.")
    if catalog.get("posture"):
        lines.append(f"Right now you are {str(catalog['posture']).lower()}.")
    tone = PERSONALITY_TONES.get(personality, "")
    if tone:
        lines += ["", tone + " Let it show in your words, your faces and how you move."]
    if notes.strip():
        lines += ["", "How you act (from the user, follow it):", notes.strip()]
    return "\n".join(lines)


def check_names(act: dict, catalog: dict) -> dict:
    """The act with names the model made up left out (matched ignoring case)."""
    if not catalog:
        return act

    def known(field, *lists):
        names = {str(i.get("name", "")).lower(): i.get("name") for key in lists for i in catalog.get(key) or []}
        return names.get(str(act.get(field, "")).lower())

    out = {}
    face = known("expression", "expressions", "specialExpressions")
    if face:
        out["expression"] = face
        if act.get("intensity") is not None:
            out["intensity"] = act["intensity"]
    move = known("action", "actions")
    if move:
        out["action"] = move
    preset = known("motionset", "motionSets")
    if preset:
        out["motionset"] = preset
    return out


# ---------------------------------------------------------------------------
# Lip sync
# ---------------------------------------------------------------------------

def _hangul_vowel(ch):
    index = ((ord(ch) - 0xAC00) // 28) % 21
    # ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ
    return "AEAEOEOEOAEEOUOEIUUII"[index]


def vowels_of(text: str) -> list:
    """The mouth shapes (A I U E O) for the vowels of `text`, roughly in order.
    Latin letters and kana by their vowel, Hangul by its vowel, Chinese
    characters (no reading here) by a fixed spread over the five."""
    out, prev_vowel = [], False
    for i, ch in enumerate(text):
        low = ch.lower()
        vowel = None
        if low == "y" and text[i + 1:i + 2].lower() in tuple("aeiou"):
            pass                               # "you", "yes": y is a consonant there
        elif low in "aeiouy":
            vowel = {"y": "I"}.get(low, low.upper())
            if prev_vowel:                     # "ea", "ou": one syllable
                continue
        elif "가" <= ch <= "힣":
            vowel = _hangul_vowel(ch)
        elif "぀" <= ch <= "ヿ":
            if ch in "ーゝゞヽヾ" and out:
                vowel = out[-1]
            else:
                name = unicodedata.name(ch, "")
                if name.endswith(" N") or "SMALL TSU" in name or "MARK" in name:
                    vowel = None
                elif name[-1:] in VISEMES:
                    vowel = name[-1]
        elif "一" <= ch <= "鿿":
            vowel = "AOEIU"[ord(ch) % 5]
        if vowel:
            out.append(vowel)
        prev_vowel = vowel is not None and low in "aeiouy"
    return out


def wav_envelope(path, step_ms: int = STEP_MS) -> list:
    """Loudness of a WAV file, 0..1, one value per `step_ms`; [] if it can't be read."""
    try:
        with wave.open(str(path), "rb") as w:
            width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return []
    codes = {1: "B", 2: "h", 4: "i"}
    if width not in codes or not rate:
        return []
    samples = array.array(codes[width])
    samples.frombytes(raw[:len(raw) // width * width])
    if width == 1:                               # 8-bit WAV is unsigned, silence at 128
        samples = array.array("h", (s - 128 for s in samples))
    step = max(1, rate * step_ms // 1000) * channels
    levels = []
    for start in range(0, len(samples), step):
        chunk = samples[start:start + step:4 * channels] or samples[start:start + 1]
        levels.append((sum(s * s for s in chunk) / len(chunk)) ** 0.5)
    if not levels:
        return []
    loud = sorted(levels)[int(len(levels) * 0.95)] or 1.0
    return [0.0 if v / loud < 0.08 else min(1.0, v / loud) for v in levels]


def text_levels(text: str, frames: int) -> list:
    """A made-up loudness for audio we can't read (MP3): open on vowels,
    nearly closed on consonants, shut at spaces and punctuation."""
    shape = []
    for ch in text:
        if ch.lower() in "aeiouy" or "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" \
                or "가" <= ch <= "힣":
            shape += [0.5, 0.9, 0.6]
        elif ch.isalnum():
            shape.append(0.25)
        elif ch.isspace():
            shape.append(0.0)
        else:
            shape += [0.0, 0.0]
    if not shape or frames <= 0:
        return [0.0] * max(frames, 0)
    return [shape[int(i * len(shape) / frames)] for i in range(frames)]


def lip_frames(levels: list, vowels: list) -> list:
    """[(viseme, amount)] per frame: how open from `levels`, which shape from
    `vowels`, spread evenly over the frames where she makes a sound."""
    voiced = [i for i, v in enumerate(levels) if v > 0]
    shapes = vowels or ["A", "O", "E"]
    frames = [("", 0.0)] * len(levels)
    for k, i in enumerate(voiced):
        frames[i] = (shapes[k * len(shapes) // len(voiced)], round(levels[i], 2))
    return frames


# ---------------------------------------------------------------------------
# Talking to the 3D model, and playing a reply on it (Qt from here on)
# ---------------------------------------------------------------------------

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402


class AvatarLink(QObject):
    """Sends commands to the 3D model in the order given, on one worker thread,
    so the window never waits for it. Only the newest mouth shape is kept."""
    failed = Signal(str)
    COMPLAIN_EVERY = 15.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.url = avatar_url()
        self._jobs = collections.deque()
        self._mouth = None
        self._wake = threading.Condition()
        self._session = requests.Session()
        self._catalog = (0.0, None)
        self._complained = 0.0
        threading.Thread(target=self._work, daemon=True).start()

    def catalog(self, max_age: float = 5.0):
        """What she can do, or None if the model isn't running. Blocks for up to
        two seconds, so call it off the GUI thread (llmClient's worker does)."""
        at, data = self._catalog
        if data is not None and time.monotonic() - at < max_age:
            return data
        try:
            r = requests.get(f"{self.url}/api/catalog", timeout=2)
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            data = None
        self._catalog = (time.monotonic(), data)
        if data is None:
            self._complain()
        return data

    def act(self, act: dict):
        act = check_names(act, self._catalog[1])
        if act.get("motionset"):
            self._send("/api/motionset", {"name": act["motionset"]})
        body = {k: act[k] for k in ("expression", "intensity", "action") if k in act}
        if body.get("expression") or body.get("action"):
            self._send("/api/act", body)

    def personality(self, name: str):
        self._send("/api/personality", {"personality": name})

    def mouth(self, viseme: str, amount: float):
        with self._wake:
            self._mouth = {"visemes": [{"viseme": viseme, "amount": amount}] if viseme and amount > 0 else []}
            self._wake.notify()

    def _send(self, path, body):
        with self._wake:
            self._jobs.append((path, body))
            self._wake.notify()

    def _work(self):
        while True:
            with self._wake:
                while not self._jobs and self._mouth is None:
                    self._wake.wait()
                if self._jobs:
                    path, body = self._jobs.popleft()
                else:
                    path, body, self._mouth = "/api/lipsync", self._mouth, None
            try:
                self._session.post(self.url + path, json=body, timeout=2)
            except requests.RequestException:
                self._complain()

    def _complain(self):
        now = time.monotonic()
        if now - self._complained > self.COMPLAIN_EVERY:
            self._complained = now
            self.failed.emit(f"The 3D model isn't answering at {self.url}. Start ChatUIAvatar on this PC "
                             "(or press Play in Unity).")


class Performer(QObject):
    """Plays a spoken reply on the 3D model: its acts as the speech reaches
    them, and the mouth in time with the audio."""

    def __init__(self, speaker, link: AvatarLink, parent=None):
        super().__init__(parent)
        self.speaker = speaker
        self.link = link
        self.key = None
        self._acts = []                # [(fraction of the reply, act)] not played yet
        self._text_len = 1
        self._path = ""
        self._spoken = ""
        self._frames = None
        self._mouth_open = False
        self._timer = QTimer(self)
        self._timer.setInterval(STEP_MS)
        self._timer.timeout.connect(self._tick)
        speaker.started.connect(self.on_started)
        speaker.failed.connect(lambda _err: self._give_up())

    def react(self, act: dict):
        """Play an act now (the first tag of a reply that is still streaming in)."""
        self.link.act(act)

    def prepare(self, key, text: str, acts, skip_first: bool = False):
        """The reply `key` is about to be spoken: its acts wait for the speech."""
        acts = list(acts or [])
        if skip_first and acts and acts[0].get("at", 0) == 0:
            acts = acts[1:]
        self.key = key
        self._text_len = max(1, len(text))
        self._acts = [(min(1.0, a.get("at", 0) / self._text_len), {k: v for k, v in a.items() if k != "at"})
                      for a in acts]
        self._frames = None
        self._path = ""

    def on_started(self, key, path, spoken_text):
        if key != self.key:
            return
        self._path, self._spoken = path, spoken_text
        self._frames = None
        if path.lower().endswith(".wav"):
            levels = wav_envelope(path)
            if levels:
                self._frames = lip_frames(levels, vowels_of(spoken_text))
        self._timer.start()

    def _tick(self):
        state = self.speaker.state_of(self.key)
        if state in ("stopped", "paused"):
            self._close_mouth()
            if state == "stopped":
                self._timer.stop()
            return
        if state != "playing":
            return
        pos, length = self.speaker.position(), self.speaker.duration()
        if self._frames is None and length > 0:          # MP3: shape the mouth from the text
            n = length // STEP_MS
            self._frames = lip_frames(text_levels(self._spoken, n), vowels_of(self._spoken))
        if length > 0:
            progress = pos / length
            while self._acts and self._acts[0][0] <= progress:
                self.link.act(self._acts.pop(0)[1])
        if self._frames:
            i = min(pos // STEP_MS, len(self._frames) - 1)
            viseme, amount = self._frames[i]
            self.link.mouth(viseme, amount)
            self._mouth_open = amount > 0

    def _close_mouth(self):
        if self._mouth_open:
            self.link.mouth("", 0)
            self._mouth_open = False

    def _give_up(self):
        """The speech failed: show the reply's last feeling at least."""
        if self._acts:
            self.link.act(self._acts[-1][1])
            self._acts = []
        self._close_mouth()
