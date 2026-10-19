"""Voice input: record the microphone and turn it into text on the server.

The 🎤 button in the chat window (ChatMain) uses the two halves here:

  Recorder      records the default microphone with Qt Multimedia (part of
                PySide6, nothing extra to install) and hands back a WAV file.
                It asks for 16 kHz mono 16-bit, which is what Whisper wants;
                when the microphone can't do that it records in the device's
                own format (often 48 kHz float on Windows) and the server
                converts it.
  Transcriber   uploads the WAV to the server's /v1/audio/transcriptions
                (Server/ollama/chat_server/speech.py) on a worker thread and
                emits the text.

wav_bytes() has no Qt in it, so it can be tested on its own (tests/test_voice.py).
"""

import struct
import threading

import requests
from PySide6.QtCore import QObject, QTimer, Signal

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
