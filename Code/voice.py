"""Voice: speech to text for the 🎤 button, and text to speech for the ▶ Play
buttons on the bot's replies. The server does the heavy lifting both ways.

The 🎤 button in the chat window (ChatMain) uses:

  Recorder      records the default microphone with Qt Multimedia (part of
                PySide6, nothing extra to install) and hands back a WAV file.
                It asks for 16 kHz mono 16-bit, which is what Whisper wants;
                when the microphone can't do that it records in the device's
                own format (often 48 kHz float on Windows) and the server
                converts it.
  Transcriber   uploads the WAV to the server's /v1/audio/transcriptions
                (Server/ollama/chat_server/speech.py) on a worker thread and
                emits the text.

The ▶ Play buttons use:

  Speaker       asks the server's /v1/audio/speech (chat_server/tts.py) to read
                a reply with the chat's engine, voice and speed, keeps the
                audio in CLIENT_DATA_DIR/Audio so replays are instant, and
                plays and pauses it with Qt Multimedia.
  list_voices() the engines and voices the server offers, for Chat Settings.

wav_bytes() has no Qt in it, so it can be tested on its own (tests/test_voice.py).
"""

import hashlib
import struct
import threading
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QTimer, QUrl, Signal

MAX_SECONDS = 300            # a recording stops by itself after this long
LEVEL_INTERVAL_MS = 100

# WAV format tags
PCM, IEEE_FLOAT = 1, 3


def _no_error(err) -> bool:
    """QAudio.Error and the newer QtAudio.Error don't compare equal, so look at the value."""
    return getattr(err, "value", err) == 0


def wav_bytes(pcm: bytes, rate: int, channels: int, bits: int, tag: int = PCM) -> bytes:
    """A WAV file around raw samples. (Python's `wave` module can't write float WAVs.)"""
    block = channels * bits // 8
    pcm = pcm[:len(pcm) // block * block]
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def peak_level(pcm: bytes, bits: int, tag: int) -> float:
    """Loudest sample of a chunk, 0..1, for the level shown while recording."""
    if not pcm:
        return 0.0
    if tag == IEEE_FLOAT:
        n = len(pcm) // 4
        values = struct.unpack(f"<{n}f", pcm[:n * 4])
        return min(1.0, max((abs(v) for v in values[::8]), default=0.0))
    if bits == 16:
        n = len(pcm) // 2
        values = struct.unpack(f"<{n}h", pcm[:n * 2])
        return max((abs(v) for v in values[::8]), default=0) / 32768
    if bits == 32:
        n = len(pcm) // 4
        values = struct.unpack(f"<{n}i", pcm[:n * 4])
        return max((abs(v) for v in values[::8]), default=0) / 2147483648
    return max((abs(b - 128) for b in pcm[::8]), default=0) / 128


class Recorder(QObject):
    level = Signal(float)        # 0..1, about ten times a second while recording
    elapsed = Signal(int)        # whole seconds recorded so far
    finished = Signal(bytes)     # the WAV file, after stop()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._io = None
        self._buffer = bytearray()
        self._format = None          # (rate, channels, bits, tag)
        self._peak = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(LEVEL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def recording(self) -> bool:
        return self._source is not None

    def start(self):
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
        except ImportError as e:
            self.failed.emit(f"Qt Multimedia is missing from this PySide6 install: {e}")
            return
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.failed.emit("No microphone found. Plug one in, or pick one in Windows sound settings.")
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(16000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        kinds = {QAudioFormat.Int16: (16, PCM), QAudioFormat.Int32: (32, PCM),
                 QAudioFormat.Float: (32, IEEE_FLOAT), QAudioFormat.UInt8: (8, PCM)}
        if fmt.sampleFormat() not in kinds:
            self.failed.emit(f"The microphone's sample format isn't supported ({fmt.sampleFormat()})")
            return
        bits, tag = kinds[fmt.sampleFormat()]
        self._format = (fmt.sampleRate(), fmt.channelCount(), bits, tag)
        self._buffer = bytearray()
        self._peak = 0.0
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._source is None or self._io is None or not _no_error(self._source.error()):
            self._cleanup()
            self.failed.emit("Can't open the microphone. Check that Windows allows apps to use it "
                             "(Settings > Privacy > Microphone).")
            return
        self._io.readyRead.connect(self._read)
        self._timer.start()

    def stop(self):
        """Stop and emit `finished` with the WAV (nothing if not recording)."""
        if not self.recording:
            return
        self._read()
        self._cleanup()
        rate, channels, bits, tag = self._format
        self.finished.emit(wav_bytes(bytes(self._buffer), rate, channels, bits, tag))

    def cancel(self):
        self._cleanup()
        self._buffer = bytearray()

    def seconds(self) -> float:
        if not self._format:
            return 0.0
        rate, channels, bits, _ = self._format
        return len(self._buffer) / (rate * channels * bits // 8)

    def _read(self):
        if self._io is None:
            return
        chunk = bytes(self._io.readAll())
        if chunk:
            self._buffer += chunk
            _, _, bits, tag = self._format
            self._peak = max(self._peak, peak_level(chunk, bits, tag))

    def _tick(self):
        # Errors are polled here: PySide6 6.12 can't deliver QAudioSource.stateChanged to Python.
        if self._source is not None and not _no_error(self._source.error()):
            err = self._source.error()
            self._cleanup()
            self.failed.emit(f"The microphone stopped ({err.name if hasattr(err, 'name') else err})")
            return
        self._read()
        self.level.emit(self._peak)
        self._peak = 0.0
        secs = self.seconds()
        self.elapsed.emit(int(secs))
        if secs >= MAX_SECONDS:
            self.stop()

    def _cleanup(self):
        self._timer.stop()
        if self._source is not None:
            src, self._source, self._io = self._source, None, None
            src.stop()
            src.deleteLater()


class Transcriber(QObject):
    """Sends a clip to the server's speech to text, one clip at a time."""
    done = Signal(str)          # the text ("" when nothing was understood)
    failed = Signal(str)

    def __init__(self, base_url, parent=None):
        super().__init__(parent)
        self._base_url = base_url        # a function, so a changed server address is picked up
        self._worker = None

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def transcribe(self, wav: bytes, language: str = ""):
        self._worker = threading.Thread(target=self._run, args=(wav, language), daemon=True)
        self._worker.start()

    def _run(self, wav, language):
        url = f"{self._base_url()}/v1/audio/transcriptions"
        try:
            # The first clip can take minutes on a fresh server: it downloads and loads Whisper.
            r = requests.post(url, files={"file": ("voice.wav", wav, "audio/wav")},
                              data={"language": language}, timeout=(5, 600))
        except requests.RequestException as e:
            self.failed.emit(f"Can't reach the server for speech to text: {e}")
            return
        if r.status_code == 404:
            self.failed.emit("This server has no speech to text yet; update it (git pull) and restart it.")
            return
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                msg = r.text[:300]
            self.failed.emit(f"Speech to text failed: {msg}")
            return
        self.done.emit(r.json().get("text", "").strip())


# ---------------------------------------------------------------------------
# Text to speech
# ---------------------------------------------------------------------------

AUDIO_CACHE_FILES = 300      # oldest clips are deleted beyond this many


def list_voices(base_url: str, engine: str = "") -> dict:
    """{"engines": [...], "voices": [{"id", "engine", "language", "name"}], ...}; raises RuntimeError."""
    try:
        r = requests.get(f"{base_url}/v1/audio/voices", params={"engine": engine} if engine else None, timeout=10)
    except requests.RequestException as e:
        raise RuntimeError(f"Can't reach the server: {e}") from e
    if r.status_code == 404:
        raise RuntimeError("This server has no text to speech yet; update it (git pull) and restart it")
    if r.status_code != 200:
        raise RuntimeError(f"The server couldn't list voices: {r.text[:200]}")
    return r.json()


def clip_name(text: str, engine: str, voice: str, speed: float) -> str:
    """Cache file name (without extension) for one reading of one text."""
    return hashlib.sha1(f"{engine}|{voice}|{speed:.2f}|{text}".encode("utf-8")).hexdigest()


class Speaker(QObject):
    """Plays one reply at a time. `key` is whatever the caller uses to tell
    replies apart (ChatMain uses the message's index)."""
    changed = Signal()           # the playing key or its state changed: redraw the buttons
    failed = Signal(str)
    _fetched = Signal(object, str)          # key, file (from the worker thread)
    _fetch_failed = Signal(object, str)

    def __init__(self, base_url, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._base_url = base_url              # a function, so a changed server address is picked up
        self.cache_dir = Path(cache_dir)
        self.key = None                        # the reply playing, paused or loading
        self._loading = False
        self._player = None
        self._output = None
        self._last_state = "stopped"
        self._wants_play = False
        self._poll = QTimer(self)              # PySide6 6.12 can't hand some Qt Multimedia signals to Python
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._check_player)
        self._fetched.connect(self._on_fetched)
        self._fetch_failed.connect(self._on_fetch_failed)

    def state_of(self, key) -> str:
        """"loading", "playing", "paused" or "stopped"."""
        if key != self.key or key is None:
            return "stopped"
        if self._loading:
            return "loading"
        return self._player_state()

    def toggle(self, key, text: str, engine: str, voice: str, speed: float):
        """Play `key`, or pause/resume it if it's the one already playing."""
        if key == self.key and self.key is not None:
            state = self.state_of(key)
            if state == "loading":
                return
            if state == "playing":
                self._wants_play = False
                self._player.pause()
            else:
                self._wants_play = True
                self._player.play()
            self._last_state = "paused" if state == "playing" else "playing"
            self._poll.start()
            self.changed.emit()
            return
        self.stop()
        self.key = key
        self._loading = True
        self.changed.emit()
        threading.Thread(target=self._fetch, args=(key, text, engine, voice, speed), daemon=True).start()

    def stop(self):
        self._wants_play = False
        if self._player is not None:
            self._player.stop()
        was = self.key
        self.key = None
        self._loading = False
        self._poll.stop()
        if was is not None:
            self.changed.emit()

    # -------------------------------------------------------------- fetching

    def cached(self, text, engine, voice, speed):
        name = clip_name(text, engine, voice, speed)
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / (name + ext)
            if path.is_file() and path.stat().st_size > 0:
                return path
        return None

    def _fetch(self, key, text, engine, voice, speed):
        path = self.cached(text, engine, voice, speed)
        if path is None:
            try:
                r = requests.post(f"{self._base_url()}/v1/audio/speech",
                                  json={"input": text, "model": engine, "voice": voice, "speed": speed},
                                  timeout=(5, 900))     # the first use of a local engine downloads its model
            except requests.RequestException as e:
                self._fetch_failed.emit(key, f"Can't reach the server for text to speech: {e}")
                return
            if r.status_code == 404 and "voice" not in r.text.lower():
                self._fetch_failed.emit(key, "This server has no text to speech yet; update it (git pull) and restart it.")
                return
            if r.status_code != 200:
                try:
                    msg = r.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    msg = r.text[:300]
                self._fetch_failed.emit(key, f"Text to speech failed: {msg}")
                return
            ext = ".wav" if "wav" in r.headers.get("content-type", "") else ".mp3"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / (clip_name(text, engine, voice, speed) + ext)
            path.write_bytes(r.content)
            self._prune()
        self._fetched.emit(key, str(path))

    def _prune(self):
        try:
            clips = sorted(self.cache_dir.glob("*.*"), key=lambda p: p.stat().st_mtime)
            for old in clips[:-AUDIO_CACHE_FILES]:
                old.unlink()
        except OSError:
            pass

    def _on_fetched(self, key, path):
        if key != self.key:            # another reply was picked meanwhile
            return
        self._loading = False
        if not self._ensure_player():
            self.stop()
            return
        self._player.setSource(QUrl.fromLocalFile(path))
        self._wants_play = True
        self._player.play()
        self._last_state = "loading"
        self._poll.start()
        self.changed.emit()

    def _on_fetch_failed(self, key, err):
        if key == self.key:
            self.stop()
        self.failed.emit(err)

    # -------------------------------------------------------------- playback

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError as e:
            self.failed.emit(f"Qt Multimedia is missing from this PySide6 install: {e}")
            return False
        self._output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._output)
        return True

    def _player_state(self) -> str:
        if self._player is None:
            return "stopped"
        from PySide6.QtMultimedia import QMediaPlayer
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            return "playing"
        if state == QMediaPlayer.PlaybackState.PausedState:
            return "paused"
        # Right after play() the player still says Stopped while it opens the file.
        status = self._player.mediaStatus()
        if status in (QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.BufferedMedia) \
                and self._wants_play:
            return "loading"
        return "stopped"

    def _check_player(self):
        if self._player is None or self.key is None:
            self._poll.stop()
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.error() != QMediaPlayer.Error.NoError:
            err = self._player.errorString()
            self.stop()
            self.failed.emit(f"Can't play the audio: {err}")
            return
        state = self._player_state()
        if state == "stopped":             # played to the end
            self.stop()
            return
        if state != self._last_state:
            self._last_state = state
            self.changed.emit()
